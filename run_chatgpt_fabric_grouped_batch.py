"""Generate realistic fabric swatch photography in small, isolated ChatGPT conversations.

The first turn in each conversation includes the complete master prompt
(prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md) and an input image.
Later turns attach only the next image and a short continuation request.
The output image is normalized to 4:3 landscape (2048x1536) and saved to
output/chatgpt/<SKU>/image_1.png along with metadata.json.
"""

import argparse
import hashlib
import json
import os
import random
import re
import time
import traceback
from pathlib import Path

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import run_batch as shared
import run_chatgpt_texture_batch as legacy


SETTINGS = shared.config.get("chatgpt_fabric_grouped", {})
BASE_SETTINGS = shared.config.get("chatgpt_texture_grouped", {})
CHATGPT_SETTINGS = shared.config.get("chatgpt", {})

RAW_DIR = shared.resolve_path(
    SETTINGS.get("raw_dir", "textures_cropped")
)
FALLBACK_RAW_DIR = shared.resolve_path(
    shared.config.get("chatgpt_texture", {}).get("raw_dir", "textures_raw")
)
MASTER_PROMPT_FILE = shared.resolve_path(
    SETTINGS.get(
        "master_prompt_file",
        "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md",
    )
)
DEFAULT_PROMPT_MODE = str(SETTINGS.get("prompt_mode", "attachment")).strip().lower()
DEFAULT_PROMPT_TEXT = str(SETTINGS.get("prompt_text", "")).strip()
STATUS_FILE = shared.resolve_path(
    SETTINGS.get("status_file", "status_chatgpt_fabric_grouped.json")
)
OUTPUT_DIR = shared.resolve_path(
    SETTINGS.get("output_dir", "output/chatgpt")
)
OUTPUT_FILENAME = str(SETTINGS.get("filename", "image_1.png")).strip()
TARGET_WIDTH = int(SETTINGS.get("target_width", 2048))
TARGET_HEIGHT = int(SETTINGS.get("target_height", 1536))

LOG_DIR = shared.LOGS_DIR / "chatgpt_fabric_grouped"
DOWNLOAD_DIR = LOG_DIR / "downloads"
INVALID_DIR = LOG_DIR / "invalid"
TIMEOUT_MS = int(SETTINGS.get("timeout_ms", BASE_SETTINGS.get("timeout_ms", 300000)))
UPLOAD_TIMEOUT_MS = int(
    SETTINGS.get("upload_timeout_ms", BASE_SETTINGS.get("upload_timeout_ms", 90000))
)
DEFAULT_IMAGES_PER_CHAT = int(SETTINGS.get("images_per_chat", 5))
MAX_RETRIES = int(shared.RETRY.get("max_retries", 3))
MAX_CONSECUTIVE_FAILURES = int(
    shared.RETRY.get("max_consecutive_failures", 3)
)
MAX_BROWSER_RECOVERIES = int(SETTINGS.get("browser_recovery_attempts", 3))

DEFAULT_PACING = {
    "after_page_load": [2, 4],
    "after_mode_select": [1, 2],
    "after_upload": [2, 4],
    "before_send": [2, 5],
    "before_download": [1, 2],
    "after_download": [1, 2],
    "between_images": [2, 5],
    "between_chats": [90, 180],
}
PACING = SETTINGS.get("pacing", BASE_SETTINGS.get("pacing", {}))
SUPPORTED_EXTENSIONS = legacy.SUPPORTED_EXTENSIONS

LOG_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
INVALID_DIR.mkdir(parents=True, exist_ok=True)

FIRST_TURN_SUFFIX = (
    "\n\nHãy dùng ảnh vừa tải lên làm authoritative material reference và tạo ngay "
    "chính xác 01 ảnh FABRIC REALISTIC SWATCH theo toàn bộ yêu cầu trên "
    f"(tỷ lệ 4:3 landscape, {TARGET_WIDTH}×{TARGET_HEIGHT} px). Chỉ trả về ảnh kết quả, không giải thích."
)
CONTINUATION_PROMPT = (
    "Tiếp tục tạo ảnh mẫu vải cho ảnh mới vừa tải lên. Chỉ dùng ảnh mới này làm "
    "authoritative reference, áp dụng nguyên vẹn các quy tắc FABRIC REALISTIC SWATCH "
    f"ở đầu cuộc trò chuyện và tạo chính xác 01 ảnh 4:3 landscape {TARGET_WIDTH}×{TARGET_HEIGHT}. "
    "Không dùng lại ảnh nguồn hay ảnh kết quả trước đó. Chỉ trả về ảnh, không giải thích."
)


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_status():
    if not STATUS_FILE.exists():
        return {}
    with open(STATUS_FILE, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Status file must contain a JSON object: {STATUS_FILE}")
    return value


def save_status(status_data):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_FILE.with_suffix(STATUS_FILE.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(status_data, handle, indent=4, ensure_ascii=False)
    temporary.replace(STATUS_FILE)


def pacing_range(stage):
    value = PACING.get(stage, DEFAULT_PACING.get(stage, [2, 4]))
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"pacing.{stage} must be [minimum, maximum].")
    minimum, maximum = map(float, value)
    if minimum < 0 or maximum < minimum:
        raise ValueError(f"Invalid pacing range for {stage}: {value}")
    return minimum, maximum


def paced_sleep(stage, label):
    minimum, maximum = pacing_range(stage)
    delay = random.uniform(minimum, maximum)
    print(f"  [Pacing] {label}: {delay:.1f}s")
    time.sleep(delay)


def validate_settings(images_per_chat, prompt_mode="attachment", prompt_file=None):
    if prompt_mode == "attachment":
        target_file = prompt_file or MASTER_PROMPT_FILE
        if not target_file.exists():
            raise ValueError(f"Master prompt file does not exist: {target_file}")
    if images_per_chat < 1:
        raise ValueError("images_per_chat must be at least 1.")
    if MAX_BROWSER_RECOVERIES < 1:
        raise ValueError("browser_recovery_attempts must be at least 1.")


def browser_connection_lost(page, exc):
    try:
        if page.is_closed():
            return True
    except Exception:
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "target closed",
            "browser has been closed",
            "page has been closed",
            "browser disconnected",
            "connection closed",
        )
    )


def connect_recovery_page(playwright):
    if not shared.cdp_is_ready():
        print("  [Recovery] Chrome automation is closed; starting it again...")
        shared.launch_automation_chrome()
    else:
        print("  [Recovery] Chrome is still running; reconnecting via CDP...")
    browser = legacy.connect_to_automation_chrome(playwright)
    context = browser.contexts[0]
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    page = context.new_page()
    page.bring_to_front()
    print("  [Recovery] Browser connection restored with a fresh tab.")
    return browser, context, page


def output_path_for(sku, folder=None):
    if folder and str(folder) != ".":
        return OUTPUT_DIR / folder / sku / OUTPUT_FILENAME
    return OUTPUT_DIR / sku / OUTPUT_FILENAME


def inspect_output(target_path):
    with Image.open(target_path) as img:
        img.load()
        w, h = img.size
        fmt = img.format or "PNG"
    if fmt != "PNG":
        raise ValueError(f"File format is {fmt}, expected PNG.")
    if (w, h) != (TARGET_WIDTH, TARGET_HEIGHT):
        raise ValueError(f"Image size is {w}x{h}, expected {TARGET_WIDTH}x{TARGET_HEIGHT}.")
    return {"width": w, "height": h, "format": fmt}


def normalize_image_to_fabric(downloaded_path, target_path):
    with Image.open(downloaded_path) as image:
        image.load()
        src_w, src_h = image.size
        src_fmt = image.format or "PNG"
        if (src_w, src_h) != (TARGET_WIDTH, TARGET_HEIGHT):
            resized = image.resize(
                (TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS
            )
        else:
            resized = image.copy()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if resized.mode in ("RGBA", "P"):
        resized = resized.convert("RGB")
    resized.save(target_path, format="PNG")

    return {
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "format": "PNG",
        "source_width": src_w,
        "source_height": src_h,
        "source_download_format": src_fmt,
        "uniformly_resized": (src_w, src_h) != (TARGET_WIDTH, TARGET_HEIGHT),
    }


def write_fabric_metadata(sku, output_path, status_entry):
    meta_file = output_path.parent / "metadata.json"
    meta = {}
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    meta["sku"] = sku
    meta["fabric_swatch"] = {
        "file": output_path.name,
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "aspect_ratio": "4:3",
        "master_document": MASTER_PROMPT_FILE.name,
        "completed_at": status_entry.get("completed_at"),
    }
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def discover_input_files(parser, selected_sku=None):
    active_dir = RAW_DIR if RAW_DIR.exists() else FALLBACK_RAW_DIR
    if not active_dir.exists():
        print(f"Source directory does not exist: {active_dir}")
        return []
    files = []
    for path in sorted(active_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    keyed = {}
    duplicates = {}
    for path in files:
        sku = path.stem
        if selected_sku and sku != selected_sku:
            continue
        try:
            rel = path.parent.relative_to(active_dir)
            folder = str(rel) if str(rel) != "." else None
        except Exception:
            folder = None
        if sku in keyed:
            duplicates.setdefault(sku, [keyed[sku]]).append((path, folder))
        else:
            keyed[sku] = (path, folder)
    if duplicates:
        details = ", ".join(
            f"{sku}: {', '.join(p.name for p, _ in paths)}"
            for sku, paths in duplicates.items()
        )
        parser.error(f"Multiple source files map to the same SKU: {details}")
    return sorted(keyed.items())


def select_files(args, parser, status_data):
    discovered = discover_input_files(parser, args.sku)
    if args.sku and not discovered:
        parser.error(f"Source SKU was not found: {args.sku}")

    selected = []
    skipped_ready = 0
    skipped_exhausted = 0
    for sku, (path, folder) in discovered:
        target = output_path_for(sku, folder)
        item = status_data.get(sku, {})
        if target.exists() and not args.force:
            try:
                inspect_output(target)
                skipped_ready += 1
                continue
            except Exception as exc:
                print(f"  Existing output for {sku} is invalid and will be replaced: {exc}")
        if not args.force and int(item.get("retries", 0)) >= MAX_RETRIES:
            skipped_exhausted += 1
            continue
        selected.append((sku, path, folder))

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        selected = selected[: args.limit]
    print(
        f"Found {len(selected)} eligible fabric SKU(s) "
        f"(skipped {skipped_ready} ready, {skipped_exhausted} at max retries)."
    )
    return selected


def open_fresh_chat(page):
    url = str(CHATGPT_SETTINGS.get("home_url", "https://chatgpt.com/"))
    max_nav_attempts = 4
    for attempt in range(1, max_nav_attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.locator("#prompt-textarea").wait_for(state="visible", timeout=60000)
            break
        except Exception as exc:
            if attempt >= max_nav_attempts:
                raise
            print(f"  [Warning] Could not load ChatGPT page (attempt {attempt}/{max_nav_attempts}): {exc}. Retrying in 3s...")
            time.sleep(3)

    paced_sleep("after_page_load", "after opening a fresh ChatGPT conversation")
    stop_reason = legacy.check_safe_stop(page)
    if stop_reason:
        raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")


def activate_create_image_mode(page):
    plus_button = page.locator('[data-testid="composer-plus-btn"]')
    plus_button.wait_for(state="visible", timeout=15000)
    plus_button.click()
    create_image = page.get_by_text(
        re.compile(r"^(Create image|Tạo hình ảnh|Tạo ảnh)$", re.IGNORECASE)
    ).last
    create_image.wait_for(state="visible", timeout=15000)
    create_image.click()
    paced_sleep("after_mode_select", "after selecting Create image")


def upload_prompt_document(page, prompt_file_path):
    """Attach prompt document (.md, .txt, etc.) to the ChatGPT conversation."""
    file_input = page.locator('input[type="file"]:not([disabled])').first
    file_input.wait_for(state="attached", timeout=15000)
    with page.expect_response(
        lambda response: (
            "/backend-api/files/process_upload_stream" in response.url
            or "/backend-api/files" in response.url
        )
        and response.request.method == "POST",
        timeout=UPLOAD_TIMEOUT_MS,
    ) as response_info:
        file_input.set_input_files(str(prompt_file_path))
    upload_response = response_info.value
    if not upload_response.ok:
        raise RuntimeError(f"ChatGPT prompt attachment upload failed with HTTP {upload_response.status}.")
    print(f"  Prompt document attached: {prompt_file_path.name}")
    paced_sleep("after_upload", f"after attaching prompt document {prompt_file_path.name}")


def upload_source(page, source_path):
    previous_sources = {
        source
        for source in page.locator("main img").evaluate_all(
            "images => images.map(image => image.currentSrc || image.src)"
        )
        if source
    }
    image_input = page.locator(
        'input[type="file"][accept*="image"]:not([disabled])'
    ).first
    image_input.wait_for(state="attached", timeout=15000)
    with page.expect_response(
        lambda response: (
            "/backend-api/files/process_upload_stream" in response.url
            and response.request.method == "POST"
        ),
        timeout=UPLOAD_TIMEOUT_MS,
    ) as response_info:
        image_input.set_input_files(str(source_path))
    upload_response = response_info.value
    if not upload_response.ok:
        raise RuntimeError(f"ChatGPT upload failed with HTTP {upload_response.status}.")
    page.wait_for_function(
        """previous => Array.from(document.querySelectorAll('main img')).some(image => {
            const source = image.currentSrc || image.src;
            return source && !previous.includes(source) &&
                (source.includes('/backend-api/estuary/content') || source.startsWith('blob:'));
        })""",
        arg=list(previous_sources),
        timeout=UPLOAD_TIMEOUT_MS,
    )
    print(f"  Fabric source attached: {source_path.name}")
    paced_sleep("after_upload", "after attaching fabric source")


def wait_for_generated_image(page, previous_sources, turn_number, images_per_chat):
    deadline = time.monotonic() + (TIMEOUT_MS / 1000)
    last_progress = 0
    while time.monotonic() < deadline:
        stop_reason = legacy.check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

        stop_buttons = page.locator(
            '[data-testid="stop-button"], button[aria-label="Stop generating"], button[aria-label="Dừng tạo"]'
        )
        is_still_generating = any(
            stop_buttons.nth(i).is_visible() for i in range(stop_buttons.count())
        )

        if not is_still_generating:
            images = page.locator('main img[alt^="Generated image:"]')
            for index in range(images.count() - 1, -1, -1):
                candidate = images.nth(index)
                source = candidate.get_attribute("src")
                if source and source not in previous_sources and candidate.is_visible():
                    return candidate

        elapsed = int(TIMEOUT_MS / 1000 - max(0, deadline - time.monotonic()))
        if elapsed - last_progress >= 30:
            print(
                f"  [{turn_number}/{images_per_chat}] Still generating fabric swatch "
                f"({elapsed}s elapsed)..."
            )
            last_progress = elapsed
        page.wait_for_timeout(1000)
    raise PlaywrightTimeoutError(
        f"No new ChatGPT generated fabric image appeared within {TIMEOUT_MS} ms."
    )


def download_and_validate_fabric(page, generated_image, sku, target_path):
    generated_image.click()
    page.locator('div[role="dialog"]').wait_for(state="visible", timeout=15000)
    close_button = page.get_by_role(
        "button", name=re.compile(r"^(Close fullscreen view|Close|Đóng.*)$")
    ).last
    close_button.wait_for(state="visible", timeout=15000)
    save_button = page.get_by_role(
        "button", name=re.compile(r"^(Save|Download|Tải xuống)$")
    ).last
    save_button.wait_for(state="visible", timeout=15000)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    downloaded_path = DOWNLOAD_DIR / f"{sku}_{stamp}_{os.getpid()}.download"
    staged_png = DOWNLOAD_DIR / f".{sku}_{stamp}_{os.getpid()}.png"

    try:
        with page.expect_download(timeout=TIMEOUT_MS) as download_info:
            save_button.click()
        download_info.value.save_as(str(downloaded_path))
    finally:
        try:
            if close_button.is_visible():
                close_button.click()
        except Exception:
            pass

    try:
        output_info = normalize_image_to_fabric(downloaded_path, staged_png)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_png, target_path)
    finally:
        if downloaded_path.exists():
            downloaded_path.unlink()
        if staged_png.exists():
            staged_png.unlink()

    return output_info


def process_turn(
    page,
    sku,
    source_path,
    target_path,
    prompt,
    prompt_kind,
    turn_number,
    images_per_chat,
    status_data,
    master_sha256,
    force=False,
    prompt_attachment_file=None,
    master_doc_name=None,
):
    print("\n=========================================")
    print(f"Processing grouped fabric swatch SKU: {sku}")
    item = status_data.get(
        sku, {"status": "pending", "retries": 0, "error_type": None}
    )
    if force:
        item["retries"] = 0
    item.update(
        {
            "status": "processing",
            "error_type": None,
            "source_file": source_path.name,
            "source_sha256": sha256_file(source_path),
            "master_document": master_doc_name or (prompt_attachment_file.name if prompt_attachment_file else MASTER_PROMPT_FILE.name),
            "master_sha256": master_sha256,
            "prompt_kind": prompt_kind,
            "chat_turn": turn_number,
            "started_at": now_text(),
        }
    )
    status_data[sku] = item
    save_status(status_data)

    try:
        activate_create_image_mode(page)
        stop_reason = legacy.check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

        previous_sources = legacy.generated_sources(page)
        if turn_number == 1 and prompt_attachment_file and prompt_attachment_file.exists():
            upload_prompt_document(page, prompt_attachment_file)
        upload_source(page, source_path)
        composer = page.locator("#prompt-textarea")
        composer.fill(prompt)
        send_button = page.locator('[data-testid="send-button"]')
        deadline = time.monotonic() + 30
        while not send_button.is_enabled() and time.monotonic() < deadline:
            stop_reason = legacy.check_safe_stop(page)
            if stop_reason:
                raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")
            page.wait_for_timeout(500)
        if not send_button.is_enabled():
            stop_reason = legacy.check_safe_stop(page)
            if stop_reason:
                raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")
            raise RuntimeError(
                "ChatGPT Send button stayed disabled after image and prompt entry."
            )

        paced_sleep(
            "before_send",
            f"before sending fabric {turn_number}/{images_per_chat} in this chat",
        )
        print(
            f"  [{turn_number}/{images_per_chat}] Sending {prompt_kind} prompt "
            "and waiting for fabric swatch..."
        )
        send_button.click()
        try:
            page.wait_for_url("https://chatgpt.com/c/**", timeout=30000)
        except PlaywrightTimeoutError:
            pass

        generated_image = wait_for_generated_image(
            page, previous_sources, turn_number, images_per_chat
        )
        paced_sleep("before_download", "before downloading the generated fabric swatch")
        output_info = download_and_validate_fabric(
            page, generated_image, sku, target_path
        )
        print(
            f"  Saved {output_info['width']}x{output_info['height']} PNG to {target_path}"
        )
        write_fabric_metadata(sku, target_path, item)
        paced_sleep("after_download", "after downloading the generated fabric swatch")

        conversation_url = (
            legacy.normalized_url(page.url) if legacy.is_conversation_url(page.url) else None
        )
        item.update(
            {
                "status": "done",
                "error_type": None,
                "output": output_info,
                "conversation_url": conversation_url,
                "completed_at": now_text(),
            }
        )
        status_data[sku] = item
        save_status(status_data)
        print(f"ChatGPT grouped fabric SKU {sku} completed successfully!")
        return True
    except Exception as exc:
        message = str(exc)
        print(f"  [ERROR] {message}")
        if browser_connection_lost(page, exc):
            if not force and target_path.exists():
                try:
                    output_info = inspect_output(target_path)
                    write_fabric_metadata(sku, target_path, item)
                    item.update(
                        {
                            "status": "done",
                            "error_type": None,
                            "output": output_info,
                            "completed_at": now_text(),
                            "completion_note": "output_recovered_after_browser_closed",
                        }
                    )
                    status_data[sku] = item
                    save_status(status_data)
                    print(
                        f"  [Recovery] Fabric output for {sku} was already saved and valid; "
                        "marked as completed."
                    )
                    return True
                except Exception:
                    pass
        if "SAFE_STOP_REQUIRED" in message:
            if "CHATGPT_QUOTA_EXHAUSTED" in message:
                _, reset_info, quota_msg = legacy.extract_chatgpt_quota_info(message)
                clean_msg = quota_msg or message.replace("SAFE_STOP_REQUIRED: CHATGPT_QUOTA_EXHAUSTED:", "").strip()
                item.update({
                    "status": "pending",
                    "error_type": "quota_limit",
                    "quota_reset_info": reset_info,
                    "quota_message": clean_msg,
                })
                status_data[sku] = item
                save_status(status_data)
                print("\n" + "=" * 72)
                print(f"[CẢNH BÁO QUOTA CHATGPT] {clean_msg}")
                print("Tiến trình tự động dừng để bảo toàn checkpoint và không làm mất lượt retry của SKU.")
                print("=" * 72 + "\n")
                return "STOP_QUOTA"
            else:
                item.update({"status": "pending", "error_type": "SAFE_STOP"})
                status_data[sku] = item
                save_status(status_data)
                return "STOP"

        item["status"] = "failed"
        item["error_type"] = message
        item["retries"] = int(item.get("retries", 0)) + 1
        status_data[sku] = item
        save_status(status_data)
        return False


def run_batch(args, parser):
    images_per_chat = (
        int(args.images_per_chat)
        if args.images_per_chat is not None
        else DEFAULT_IMAGES_PER_CHAT
    )
    prompt_mode = args.prompt_mode
    prompt_file = shared.resolve_path(args.prompt_file or MASTER_PROMPT_FILE)
    validate_settings(images_per_chat, prompt_mode=prompt_mode, prompt_file=prompt_file)

    prompt_attachment = None
    master_doc_name = prompt_file.name
    if prompt_mode == "attachment":
        prompt_attachment = prompt_file
        first_turn_prompt = (
            f"Hãy dùng ảnh vừa tải lên làm authoritative material reference và áp dụng toàn bộ hướng dẫn trong tài liệu đính kèm ({prompt_file.name}) "
            f"để tạo ngay chính xác 01 ảnh FABRIC REALISTIC SWATCH (tỷ lệ 4:3 landscape, {TARGET_WIDTH}×{TARGET_HEIGHT} px). "
            "Chỉ trả về ảnh kết quả, không giải thích."
        )
        master_sha256 = sha256_file(prompt_file)
    else:
        manual_text = (args.prompt_text or DEFAULT_PROMPT_TEXT).strip()
        if not manual_text and prompt_file.exists():
            manual_text = prompt_file.read_text(encoding="utf-8").strip()
        if not manual_text:
            raise ValueError("Chưa có nội dung prompt thủ công và không tìm thấy file prompt mẫu.")
        first_turn_prompt = manual_text + FIRST_TURN_SUFFIX
        master_sha256 = hashlib.sha256(manual_text.encode("utf-8")).hexdigest()
        master_doc_name = f"manual_text_{master_sha256[:8]}"

    status_data = load_status()
    selected_files = select_files(args, parser, status_data)
    if not selected_files:
        print("No eligible fabric SKUs to process.")
        return

    print("ChatGPT grouped fabric swatch flow")
    print(f"Prompt mode: {prompt_mode} ({'file: ' + str(prompt_file) if prompt_mode == 'attachment' else 'manual text'})")
    print(f"Conversation size: {images_per_chat} successful image(s)")
    if args.dry_run:
        print("\nDry run mode. Eligible fabric SKUs:")
        for turn_idx, (sku, path, folder) in enumerate(selected_files, start=1):
            p_kind = f"full master ({prompt_mode})" if ((turn_idx - 1) % images_per_chat) == 0 else "continuation"
            print(f"  - {sku}: {path.name} -> {output_path_for(sku, folder)} ({p_kind})")
        return

    with shared.single_instance_lock() as lock_acquired:
        if not lock_acquired:
            print("Another batch process is currently running. Exiting.")
            return

        with sync_playwright() as playwright:
            if not shared.cdp_is_ready():
                print("Launching Chrome with automation profile...")
                shared.launch_automation_chrome()
            browser = legacy.connect_to_automation_chrome(playwright)
            context = browser.contexts[0]
            context.set_default_timeout(30000)
            context.set_default_navigation_timeout(60000)
            page = context.new_page()
            page.bring_to_front()

            queue = list(selected_files)
            consecutive_failures = 0
            browser_recoveries = 0

            while queue:
                current_batch = queue[:images_per_chat]
                queue = queue[images_per_chat:]
                chat_opened = False

                for turn_index, (sku, source_path, folder) in enumerate(current_batch, start=1):
                    target_path = output_path_for(sku, folder)
                    prompt = first_turn_prompt if turn_index == 1 else CONTINUATION_PROMPT
                    prompt_kind = f"full_master_{prompt_mode}" if turn_index == 1 else "continuation"

                    while True:
                        if not chat_opened:
                            try:
                                open_fresh_chat(page)
                                chat_opened = True
                            except Exception as exc:
                                if browser_connection_lost(page, exc):
                                    browser_recoveries += 1
                                    if browser_recoveries > MAX_BROWSER_RECOVERIES:
                                        raise RuntimeError(
                                            "Exceeded maximum consecutive browser recovery attempts."
                                        ) from exc
                                    try:
                                        browser.close()
                                    except Exception:
                                        pass
                                    browser, context, page = connect_recovery_page(playwright)
                                    continue
                                raise

                        result = process_turn(
                            page,
                            sku,
                            source_path,
                            target_path,
                            prompt,
                            prompt_kind,
                            turn_index,
                            len(current_batch),
                            status_data,
                            master_sha256,
                            force=args.force,
                            prompt_attachment_file=prompt_attachment if turn_index == 1 else None,
                            master_doc_name=master_doc_name,
                        )

                        if result == "RECONNECT":
                            browser_recoveries += 1
                            if browser_recoveries > MAX_BROWSER_RECOVERIES:
                                raise RuntimeError(
                                    "Exceeded maximum consecutive browser recovery attempts."
                                )
                            try:
                                browser.close()
                            except Exception:
                                pass
                            browser, context, page = connect_recovery_page(playwright)
                            chat_opened = False
                            turn_index = 1
                            prompt = first_turn_prompt
                            prompt_kind = f"full_master_{prompt_mode}"
                            continue
                        break

                    browser_recoveries = 0
                    if result in {"STOP", "STOP_QUOTA"}:
                        if result == "STOP_QUOTA":
                            print(
                                "\n[DỪNG BATCH] Đã dừng toàn bộ tiến trình do hết quota ChatGPT. "
                                "Hãy đợi đến thời gian reset hạn mức rồi chạy lại.\n"
                            )
                            raise SystemExit(42)
                        else:
                            print(
                                "Batch stopped safely. Resolve login, verification, or quota "
                                "in Chrome, then run again."
                            )
                            raise SystemExit(41)
                        return

                    if result is True:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            print(
                                f"Reached maximum consecutive failures ({MAX_CONSECUTIVE_FAILURES}). Stopping batch."
                            )
                            return

                    if turn_index < len(current_batch):
                        paced_sleep(
                            "between_images",
                            "between fabric swatch turns in the same chat",
                        )

                if queue:
                    paced_sleep(
                        "between_chats",
                        "between fabric swatch grouped chat sessions",
                    )


def check_browser():
    with sync_playwright() as playwright:
        if not shared.cdp_is_ready():
            print("Chrome is not running. Launching automation Chrome...")
            shared.launch_automation_chrome()
        browser = legacy.connect_to_automation_chrome(playwright)
        context = browser.contexts[0]
        page = context.new_page()
        page.bring_to_front()
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        page.locator("#prompt-textarea").wait_for(state="visible", timeout=60000)
        print("ChatGPT Web is accessible and ready for fabric swatch automation!")


def main():
    parser = argparse.ArgumentParser(
        description="Batch generate fabric swatches using 02_FABRIC_SWATCH_MASTER.md."
    )
    parser.add_argument("--sku", help="Process only a specific SKU.")
    parser.add_argument("--limit", type=int, help="Limit number of SKUs to process.")
    parser.add_argument("--images-per-chat", type=int, help="Number of images per chat.")
    parser.add_argument(
        "--prompt-mode",
        choices=["attachment", "manual"],
        default=DEFAULT_PROMPT_MODE if DEFAULT_PROMPT_MODE in {"attachment", "manual"} else "attachment",
        help="Prompt delivery mode: 'attachment' (attach .md file) or 'manual' (type/paste text prompt)",
    )
    parser.add_argument(
        "--prompt-file",
        help="Path to master prompt file for attachment or manual text source",
    )
    parser.add_argument(
        "--prompt-text",
        help="Custom manual prompt text (used when --prompt-mode=manual)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-generate and overwrite existing fabric swatches.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show eligible fabric SKUs without running.",
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Check Chrome and ChatGPT connection.",
    )
    args = parser.parse_args()

    if args.check_browser:
        check_browser()
        return

    run_batch(args, parser)


if __name__ == "__main__":
    main()
