import argparse
import hashlib
import json
import os
import random
import time
import traceback

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import run_batch as shared
from seamless_packaging import package_and_report


SETTINGS = shared.config.get("flow_texture", {})
RAW_DIR = shared.resolve_path(SETTINGS.get("raw_dir", "textures_raw"))
PROMPT_FILE = shared.resolve_path(
    SETTINGS.get("prompt_file", "prompts/scanned_to_texture_prompt.md")
)
STATUS_FILE = shared.resolve_path(
    SETTINGS.get("status_file", "status_flow_texture.json")
)
TEXTURES_DIR = shared.TEXTURES_DIR
DOWNLOAD_RESOLUTION = str(SETTINGS.get("download_resolution", "2K")).upper()
TARGET_WIDTH = int(SETTINGS.get("target_width", 2048))
TARGET_HEIGHT = int(SETTINGS.get("target_height", 2048))
ALLOW_UNIFORM_RESIZE = bool(SETTINGS.get("allow_uniform_resize", True))
MINIMUM_SOURCE_WIDTH = int(SETTINGS.get("minimum_source_width", 1024))
MINIMUM_SOURCE_HEIGHT = int(SETTINGS.get("minimum_source_height", 1024))
ASPECT_RATIO_TOLERANCE = float(SETTINGS.get("aspect_ratio_tolerance", 0.01))
TIMEOUT_MS = int(SETTINGS.get("timeout_ms", 300000))
CDP_CONNECT_TIMEOUT_MS = int(SETTINGS.get("cdp_connect_timeout_ms", 20000))
CDP_CONNECT_ATTEMPTS = int(SETTINGS.get("cdp_connect_attempts", 4))
CDP_CONNECT_RETRY_SECONDS = float(SETTINGS.get("cdp_connect_retry_seconds", 3))
PACING = SETTINGS.get("pacing", {})
MAX_RETRIES = int(shared.RETRY.get("max_retries", 3))
MAX_CONSECUTIVE_FAILURES = int(
    shared.RETRY.get("max_consecutive_failures", 3)
)
LOG_DIR = shared.LOGS_DIR / "flow_texture"
DOWNLOAD_DIR = LOG_DIR / "downloads"
INVALID_DIR = LOG_DIR / "invalid"
REQUESTS_LOG = LOG_DIR / "requests.jsonl"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_PACING = {
    "after_upload": [3, 6],
    "before_send": [1.5, 4],
    "before_download": [1, 3],
    "after_download": [1, 3],
    "after_sku": [8, 15],
}

for directory in (TEXTURES_DIR, LOG_DIR, DOWNLOAD_DIR, INVALID_DIR):
    directory.mkdir(parents=True, exist_ok=True)

if DOWNLOAD_RESOLUTION not in {"1K", "2K", "4K"}:
    raise ValueError("flow_texture.download_resolution must be 1K, 2K, or 4K.")
if TARGET_WIDTH < 1 or TARGET_HEIGHT < 1:
    raise ValueError("flow_texture target dimensions must be positive integers.")
if MINIMUM_SOURCE_WIDTH < 1 or MINIMUM_SOURCE_HEIGHT < 1:
    raise ValueError("flow_texture minimum source dimensions must be positive.")
if not 0 <= ASPECT_RATIO_TOLERANCE <= 0.1:
    raise ValueError("flow_texture.aspect_ratio_tolerance must be between 0 and 0.1.")

with open(PROMPT_FILE, "r", encoding="utf-8") as handle:
    BASE_PROMPT = handle.read().strip()

FINAL_REQUIREMENT = (
    "\n\nYÊU CẦU ĐẦU RA CUỐI CÙNG\n"
    f"Tạo chính xác một ảnh texture vuông tỷ lệ 1:1, kích thước đích "
    f"{TARGET_WIDTH}×{TARGET_HEIGHT}. Ảnh được trả về phải là PNG master seamless, "
    "không phải preview tiled, contact sheet hoặc ảnh minh họa trong một khung khác. "
    "Chỉ dùng ảnh scan vừa tải lên làm ảnh nguồn. Không trả về văn bản giải thích."
)
PROMPT = BASE_PROMPT + FINAL_REQUIREMENT
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


def load_status():
    if not STATUS_FILE.exists():
        return {}
    with open(STATUS_FILE, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Status file must contain a JSON object: {STATUS_FILE}")
    return data


status_data = load_status()


def save_status():
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = STATUS_FILE.with_suffix(STATUS_FILE.suffix + ".tmp")
    with open(temp_file, "w", encoding="utf-8") as handle:
        json.dump(status_data, handle, indent=4, ensure_ascii=False)
    os.replace(temp_file, STATUS_FILE)


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paced_sleep(stage, label):
    delay_range = PACING.get(stage, DEFAULT_PACING[stage])
    if not isinstance(delay_range, list) or len(delay_range) != 2:
        raise ValueError(
            f"flow_texture.pacing.{stage} must contain [minimum, maximum]."
        )
    minimum, maximum = map(float, delay_range)
    if minimum < 0 or maximum < minimum:
        raise ValueError(f"Invalid Flow texture pacing for {stage}: {delay_range}")
    delay = random.uniform(minimum, maximum)
    print(f"  [Pacing] {label}: {delay:.1f}s")
    time.sleep(delay)


def log_request(request):
    if not any(
        marker in request.url
        for marker in ("flowMedia:batchGenerate", "/flow/uploadImage")
    ):
        return
    entry = {
        "time": now_text(),
        "method": request.method,
        "url": request.url.split("?", 1)[0],
        "has_post_data": request.post_data is not None,
    }
    try:
        with open(REQUESTS_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def output_path_for(sku):
    return TEXTURES_DIR / f"texture_{sku}.png"


def inspect_output(path):
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
        image.load()
    if image_format != "PNG":
        raise RuntimeError(f"{path} contains {image_format}, not a real PNG file.")
    if (width, height) != (TARGET_WIDTH, TARGET_HEIGHT):
        raise RuntimeError(
            f"{path} is {width}x{height}; expected {TARGET_WIDTH}x{TARGET_HEIGHT}."
        )
    return {"width": width, "height": height, "format": image_format}


def normalize_image_to_master(source_path, staged_png):
    with Image.open(source_path) as image:
        source_width, source_height = image.size
        source_format = image.format or "UNKNOWN"
        image.load()

        target_ratio = TARGET_WIDTH / TARGET_HEIGHT
        source_ratio = source_width / source_height
        ratio_error = abs(source_ratio - target_ratio) / target_ratio
        is_exact_size = (source_width, source_height) == (
            TARGET_WIDTH,
            TARGET_HEIGHT,
        )
        if not is_exact_size:
            if not ALLOW_UNIFORM_RESIZE:
                raise RuntimeError(
                    f"Flow returned {source_width}x{source_height}; expected "
                    f"{TARGET_WIDTH}x{TARGET_HEIGHT} and uniform resize is disabled."
                )
            if ratio_error > ASPECT_RATIO_TOLERANCE:
                raise RuntimeError(
                    f"Flow returned aspect ratio {source_width}:{source_height}; "
                    f"expected {TARGET_WIDTH}:{TARGET_HEIGHT}. Refusing to stretch it."
                )
            if (
                source_width < MINIMUM_SOURCE_WIDTH
                or source_height < MINIMUM_SOURCE_HEIGHT
            ):
                raise RuntimeError(
                    f"Flow returned only {source_width}x{source_height}; minimum accepted "
                    f"source is {MINIMUM_SOURCE_WIDTH}x{MINIMUM_SOURCE_HEIGHT}."
                )

        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        converted = image.convert("RGBA" if has_alpha else "RGB")
        if not is_exact_size:
            converted = converted.resize(
                (TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS
            )
        converted.save(staged_png, format="PNG")

    inspect_output(staged_png)
    return {
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "format": "PNG",
        "source_width": source_width,
        "source_height": source_height,
        "source_download_format": source_format,
        "uniformly_resized": not is_exact_size,
    }


def wait_for_result(page, previous_download_count, previous_image_sources):
    download_selector = (
        'button:has-text("Download"), button:has-text("Tải xuống"), '
        'button[aria-label*="Download"], button[aria-label*="Tải xuống"], '
        'a:has-text("Download")'
    )
    download_buttons = page.locator(download_selector)
    deadline = time.monotonic() + (TIMEOUT_MS / 1000)
    last_progress = 0
    while time.monotonic() < deadline:
        stop_reason = shared.check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

        current_count = download_buttons.count()
        current_image_sources = shared.content_image_sources(page)
        result_changed = (
            current_count > previous_download_count
            or bool(current_image_sources - previous_image_sources)
        )
        if result_changed and current_count > 0:
            candidate = download_buttons.last
            if candidate.is_visible() and candidate.is_enabled():
                return candidate

        elapsed = int(TIMEOUT_MS / 1000 - max(0, deadline - time.monotonic()))
        if elapsed - last_progress >= 30:
            print(f"  Still generating in Flow ({elapsed}s elapsed)...")
            last_progress = elapsed
        page.wait_for_timeout(1000)

    raise PlaywrightTimeoutError(
        f"No new Flow download button appeared within {TIMEOUT_MS} ms."
    )


def generate_and_download(page, sku, target_path):
    prompt_editor = page.locator('[contenteditable="true"][role="textbox"]').last
    prompt_editor.wait_for(state="visible", timeout=30000)
    prompt_editor.fill(PROMPT)

    download_selector = (
        'button:has-text("Download"), button:has-text("Tải xuống"), '
        'button[aria-label*="Download"], button[aria-label*="Tải xuống"], '
        'a:has-text("Download")'
    )
    download_buttons = page.locator(download_selector)
    previous_download_count = download_buttons.count()
    previous_image_sources = shared.content_image_sources(page)

    composer = prompt_editor.locator("xpath=../..")
    generate_button = composer.locator(
        "xpath=.//button[.//i[normalize-space()='arrow_forward']]"
    ).first
    generate_button.wait_for(state="visible", timeout=10000)
    if not generate_button.is_enabled():
        raise RuntimeError(
            "Flow's Generate button is still disabled after entering the prompt."
        )

    paced_sleep("before_send", "before sending the seamless-texture prompt")
    print("  Sending prompt and waiting for the Flow texture...")
    generate_button.click()
    download_button = wait_for_result(
        page, previous_download_count, previous_image_sources
    )

    paced_sleep("before_download", "before downloading the Flow texture")
    download_button.click()
    resolution_item = page.get_by_text(DOWNLOAD_RESOLUTION, exact=True).last
    resolution_item.wait_for(state="visible", timeout=10000)
    print(f"  Requesting Flow {DOWNLOAD_RESOLUTION} download...")
    with page.expect_download(timeout=TIMEOUT_MS) as download_info:
        resolution_item.click()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    downloaded_path = DOWNLOAD_DIR / f"{sku}_{stamp}_{os.getpid()}.download"
    staged_png = DOWNLOAD_DIR / f".{sku}_{stamp}_{os.getpid()}.png"
    download_info.value.save_as(str(downloaded_path))
    try:
        try:
            output_info = normalize_image_to_master(downloaded_path, staged_png)
        except Exception as image_error:
            invalid_path = INVALID_DIR / f"{sku}_{stamp}_{os.getpid()}.bin"
            os.replace(downloaded_path, invalid_path)
            raise RuntimeError(
                f"Downloaded Flow image was rejected. Original kept at "
                f"{invalid_path}: {image_error}"
            ) from image_error
        os.replace(staged_png, target_path)
    finally:
        if downloaded_path.exists():
            downloaded_path.unlink()
        if staged_png.exists():
            staged_png.unlink()

    paced_sleep("after_download", "after downloading the Flow texture")
    return output_info


def recover_previous_download(sku, target_path):
    candidates = sorted(
        INVALID_DIR.glob(f"{sku}_*"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for candidate in candidates:
        staged_png = DOWNLOAD_DIR / f".{sku}_recovery_{os.getpid()}.png"
        try:
            output_info = normalize_image_to_master(candidate, staged_png)
            os.replace(staged_png, target_path)
            output_info["recovered_from"] = str(candidate)
            print(
                f"  Recovered previous Flow {output_info['source_width']}x"
                f"{output_info['source_height']} download as {target_path.name}."
            )
            return output_info
        except Exception:
            if staged_png.exists():
                staged_png.unlink()
    return None


def process_sku(page, sku, raw_path, force=False):
    print("\n=========================================")
    print(f"Processing Flow raw texture SKU: {sku}")
    target_path = output_path_for(sku)
    item = status_data.get(
        sku, {"status": "pending", "retries": 0, "error_type": None}
    )

    if not force and target_path.exists():
        output_info = inspect_output(target_path)
        package_and_report(sku, target_path)
        item.update(
            {
                "status": "done",
                "error_type": None,
                "output": output_info,
                "completed_at": item.get("completed_at", now_text()),
            }
        )
        status_data[sku] = item
        save_status()
        print(f"  Valid output already exists: {target_path}")
        return True

    if force:
        item["retries"] = 0
    elif int(item.get("retries", 0)) >= MAX_RETRIES:
        print(f"SKU {sku} reached max Flow texture retries. Skipping.")
        return False

    item.update(
        {
            "status": "processing",
            "error_type": None,
            "provider": "google_flow_web",
            "source_file": raw_path.name,
            "source_sha256": sha256_file(raw_path),
            "prompt_sha256": PROMPT_SHA256,
            "download_resolution": DOWNLOAD_RESOLUTION,
            "started_at": now_text(),
        }
    )
    status_data[sku] = item
    save_status()

    try:
        stop_reason = shared.check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

        print("  Uploading raw scan to Google Flow...")
        editor_url = shared.upload_texture_and_open_editor(page, raw_path)
        item["editor_url"] = editor_url
        status_data[sku] = item
        save_status()
        paced_sleep("after_upload", "after uploading the raw scan")

        output_info = generate_and_download(page, sku, target_path)
        print(
            f"  Saved {output_info['width']}x{output_info['height']} PNG to {target_path}"
        )
        package_and_report(sku, target_path, force=force)
        item.update(
            {
                "status": "done",
                "error_type": None,
                "output": output_info,
                "completed_at": now_text(),
            }
        )
        status_data[sku] = item
        save_status()
        print(f"Flow texture SKU {sku} completed successfully!")
        paced_sleep("after_sku", "before moving to the next raw texture")
        return True
    except Exception as exc:
        message = str(exc)
        print(f"  [ERROR] {message}")
        traceback.print_exc()
        try:
            screenshot = LOG_DIR / f"error_{sku}.png"
            page.screenshot(path=str(screenshot), full_page=True)
            print(f"  Saved error screenshot: {screenshot}")
        except Exception:
            pass

        if "SAFE_STOP_REQUIRED" in message:
            item.update({"status": "pending", "error_type": "SAFE_STOP"})
            status_data[sku] = item
            save_status()
            return "STOP"

        item["retries"] = int(item.get("retries", 0)) + 1
        item["status"] = "error" if item["retries"] >= MAX_RETRIES else "pending"
        item["error_type"] = (
            "timeout" if isinstance(exc, PlaywrightTimeoutError) else "unknown"
        )
        status_data[sku] = item
        save_status()
        return False


def discover_raw_files(parser, selected_sku=None):
    target_dir = RAW_DIR
    if not target_dir.exists() or not any(target_dir.iterdir()):
        fallback = shared.resolve_path(str(target_dir).replace("textures_cropped", "textures_raw"))
        if fallback.exists() and any(fallback.iterdir()):
            target_dir = fallback

    if not target_dir.exists():
        print(f"Raw texture directory does not exist: {target_dir}")
        return []
    files = sorted(
        path
        for path in target_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if selected_sku:
        files = [path for path in files if path.stem == selected_sku]

    grouped = {}
    for path in files:
        grouped.setdefault(path.stem, []).append(path)
    collisions = {sku: paths for sku, paths in grouped.items() if len(paths) > 1}
    if collisions:
        details = "; ".join(
            f"{sku}: {', '.join(path.name for path in paths)}"
            for sku, paths in collisions.items()
        )
        parser.error(f"Multiple raw files map to the same texture name: {details}")
    return files


def select_files(args, parser):
    files = discover_raw_files(parser, args.sku)
    if not files:
        print(f"No matching raw texture files found in {RAW_DIR}")
        return []

    eligible = []
    skipped_existing = 0
    skipped_exhausted = 0
    invalid_existing = []
    for path in files:
        sku = path.stem
        target_path = output_path_for(sku)
        item = status_data.get(sku, {})
        if args.force:
            eligible.append(path)
            continue
        if target_path.exists():
            try:
                inspect_output(target_path)
            except Exception as exc:
                invalid_existing.append(f"{target_path.name}: {exc}")
            else:
                skipped_existing += 1
            continue
        if int(item.get("retries", 0)) >= MAX_RETRIES:
            skipped_exhausted += 1
            continue
        eligible.append(path)

    if invalid_existing:
        print("WARNING: Existing outputs are invalid and need --force to be replaced:")
        for message in invalid_existing:
            print(f"  {message}")

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        eligible = eligible[: args.limit]

    print(
        f"Found {len(eligible)} raw textures for Flow "
        f"(skipped {skipped_existing} valid outputs, "
        f"{skipped_exhausted} at max retries, "
        f"{len(invalid_existing)} invalid existing outputs)."
    )
    return eligible


def recover_selected_files(files, force=False):
    if force:
        return files
    remaining = []
    for raw_path in files:
        sku = raw_path.stem
        target_path = output_path_for(sku)
        if target_path.exists():
            remaining.append(raw_path)
            continue
        output_info = recover_previous_download(sku, target_path)
        if output_info is None:
            remaining.append(raw_path)
            continue
        item = status_data.get(
            sku, {"status": "pending", "retries": 0, "error_type": None}
        )
        item.update(
            {
                "status": "done",
                "error_type": None,
                "provider": "google_flow_web",
                "source_file": raw_path.name,
                "source_sha256": sha256_file(raw_path),
                "prompt_sha256": PROMPT_SHA256,
                "download_resolution": DOWNLOAD_RESOLUTION,
                "output": output_info,
                "completed_at": now_text(),
            }
        )
        status_data[sku] = item
        save_status()
    return remaining


def connect_to_automation_chrome(playwright):
    if CDP_CONNECT_TIMEOUT_MS < 1000:
        raise ValueError("flow_texture.cdp_connect_timeout_ms must be at least 1000.")
    if CDP_CONNECT_ATTEMPTS < 1:
        raise ValueError("flow_texture.cdp_connect_attempts must be at least 1.")

    last_error = None
    for attempt in range(1, CDP_CONNECT_ATTEMPTS + 1):
        if not shared.cdp_is_ready():
            last_error = RuntimeError(
                f"Chrome CDP endpoint {shared.CDP_URL} is not responding."
            )
        else:
            try:
                print(
                    f"  CDP connection attempt {attempt}/{CDP_CONNECT_ATTEMPTS} "
                    f"(timeout {CDP_CONNECT_TIMEOUT_MS}ms)..."
                )
                browser = playwright.chromium.connect_over_cdp(
                    shared.CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS
                )
                if not browser.contexts:
                    raise RuntimeError(
                        "Chrome connected but exposed no browser context."
                    )
                print(f"  Connected successfully on attempt {attempt}.")
                return browser
            except Exception as exc:
                last_error = exc
                print(f"  Attempt {attempt} failed: {exc}")
        if attempt < CDP_CONNECT_ATTEMPTS:
            print(f"  Retrying in {CDP_CONNECT_RETRY_SECONDS:.1f}s...")
            time.sleep(CDP_CONNECT_RETRY_SECONDS)

    raise RuntimeError(
        "Could not connect Playwright to the automation Chrome after "
        f"{CDP_CONNECT_ATTEMPTS} attempts. Close only that Chrome window, run "
        f"start_chrome.bat, sign in to Google Flow, then retry. Last error: {last_error}"
    )


def force_square_aspect_ratio(route, request):
    try:
        payload = json.loads(request.post_data or "{}")
        generation_requests = payload.get("requests", [])
        if not generation_requests:
            raise ValueError("generation request contains no image requests")
        for generation_request in generation_requests:
            generation_request["imageAspectRatio"] = shared.ASPECT_RATIO_API_VALUES["1:1"]
        route.continue_(post_data=json.dumps(payload, separators=(",", ":")))
        print("  Forced Flow generation aspect ratio: 1:1")
    except Exception as exc:
        print(f"  [WARNING] Could not force Flow square aspect ratio: {exc}")
        route.continue_()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert raw fabric scans into seamless 2048x2048 PNG textures with "
            "Google Flow."
        )
    )
    parser.add_argument("--sku", help="Process one exact raw filename stem, e.g. 101337")
    parser.add_argument(
        "--limit", type=int, help="Process at most this many unfinished raw textures"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show selected SKUs without opening Chrome"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate selected textures in Flow and replace valid existing outputs",
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Test the CDP connection without uploading or generating images",
    )
    parser.add_argument(
        "--prompt-file",
        help="Path to an alternative markdown/text prompt document",
    )
    parser.add_argument(
        "--prompt-text",
        help="Direct manual prompt text to use instead of reading from file",
    )
    args = parser.parse_args()

    global PROMPT, PROMPT_SHA256
    if args.prompt_text and args.prompt_text.strip():
        PROMPT = args.prompt_text.strip() + FINAL_REQUIREMENT
        PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    elif args.prompt_file and args.prompt_file.strip():
        p_file = shared.resolve_path(args.prompt_file.strip())
        if p_file.exists():
            with open(p_file, "r", encoding="utf-8") as h:
                PROMPT = h.read().strip() + FINAL_REQUIREMENT
                PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()

    print("Starting Google Flow Raw-to-Texture Batch Processor...")
    files = []
    if not args.check_browser:
        files = select_files(args, parser)
        if not files:
            print("Nothing eligible to process.")
            return

    if args.dry_run:
        print("Selected raw texture SKUs:")
        for path in files:
            print(f"  {path.stem} -> texture_{path.stem}.png")
        return

    files = recover_selected_files(files, force=args.force)
    if not files and not args.check_browser:
        print("All selected SKUs were recovered from previous valid Flow downloads.")
        return

    if not shared.cdp_is_ready():
        try:
            shared.launch_automation_chrome()
        except Exception as exc:
            print(f"FATAL: Could not start automation Chrome: {exc}")
            return
    else:
        print(f"Reusing automation Chrome on port {shared.CDP_PORT}.")

    print(f"Connecting to Chrome via CDP ({shared.CDP_URL})...")
    with sync_playwright() as playwright:
        try:
            browser = connect_to_automation_chrome(playwright)
        except Exception as exc:
            print(f"FATAL: {exc}")
            return
        context = browser.contexts[0]
        if args.check_browser:
            print(
                f"Browser check passed: {len(browser.contexts)} context(s), "
                f"{len(context.pages)} open tab(s)."
            )
            return

        context.set_default_timeout(30000)
        context.set_default_navigation_timeout(60000)
        page = context.new_page()
        page.route("**/flowMedia:batchGenerateImages", force_square_aspect_ratio)
        page.on("request", log_request)
        page.bring_to_front()
        try:
            consecutive_failures = 0
            for raw_path in files:
                result = process_sku(page, raw_path.stem, raw_path, force=args.force)
                if result == "STOP":
                    print(
                        "Batch stopped safely. Resolve Google login, CAPTCHA, or quota "
                        "in the visible Chrome window, then run again."
                    )
                    break
                if result is True:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        print(
                            f"Batch stopped after {consecutive_failures} consecutive failures."
                        )
                        break
        finally:
            page.close()


if __name__ == "__main__":
    with shared.single_instance_lock() as lock_acquired:
        if not lock_acquired:
            print(
                "ERROR: Another Flow/ChatGPT batch is controlling Chrome port "
                f"{shared.CDP_PORT}. Stop it before starting this runner."
            )
            raise SystemExit(2)
        main()
