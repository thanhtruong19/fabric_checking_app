import argparse
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


CHATGPT = shared.config.get("chatgpt", {})
HOME_URL = CHATGPT.get("home_url", "https://chatgpt.com/")
STATUS_FILE = shared.resolve_path(CHATGPT.get("status_file", "status_chatgpt.json"))
OUTPUT_DIR = shared.resolve_path(CHATGPT.get("output_dir", "output/chatgpt"))
TIMEOUT_MS = int(CHATGPT.get("timeout_ms", 300000))
UPLOAD_TIMEOUT_MS = int(CHATGPT.get("upload_timeout_ms", 90000))
DOWNLOAD_TIMEOUT_MS = int(CHATGPT.get("download_timeout_ms", 90000))
CDP_CONNECT_TIMEOUT_MS = int(CHATGPT.get("cdp_connect_timeout_ms", 20000))
CDP_CONNECT_ATTEMPTS = int(CHATGPT.get("cdp_connect_attempts", 4))
CDP_CONNECT_RETRY_SECONDS = float(CHATGPT.get("cdp_connect_retry_seconds", 3))
DELETE_CHAT_AFTER_DONE = bool(CHATGPT.get("delete_chat_after_done", True))
PACING = CHATGPT.get("pacing", {})
ASPECT_RATIO = shared.GENERATION_ASPECT_RATIO
MAX_RETRIES = int(shared.RETRY.get("max_retries", 3))
MAX_CONSECUTIVE_FAILURES = int(shared.RETRY.get("max_consecutive_failures", 3))
LOG_DIR = shared.LOGS_DIR / "chatgpt"
REQUESTS_LOG = LOG_DIR / "requests.jsonl"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_status():
    if not STATUS_FILE.exists():
        return {}
    with open(STATUS_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


status_data = load_status()


DEFAULT_PACING = {
    "after_page_load": [3, 6],
    "after_mode_select": [2, 4],
    "after_upload": [5, 10],
    "before_send": [8, 15],
    "before_download": [2, 5],
    "after_download": [3, 6],
    "between_images": [45, 75],
    "before_delete": [3, 7],
    "after_sku": [60, 120],
}


def save_status():
    temp_file = STATUS_FILE.with_suffix(STATUS_FILE.suffix + ".tmp")
    with open(temp_file, "w", encoding="utf-8") as handle:
        json.dump(status_data, handle, indent=4, ensure_ascii=False)
    os.replace(temp_file, STATUS_FILE)


def log_request(response):
    if not any(
        marker in response.url
        for marker in (
            "/backend-api/files",
            "/backend-api/f/conversation",
            "/backend-api/estuary/content",
        )
    ):
        return
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": response.status,
        "method": response.request.method,
        "url": response.url.split("?", 1)[0],
    }
    try:
        with open(REQUESTS_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def paced_sleep(stage, label):
    delay_range = PACING.get(stage, DEFAULT_PACING[stage])
    if not isinstance(delay_range, list) or len(delay_range) != 2:
        raise ValueError(f"chatgpt.pacing.{stage} must contain [minimum, maximum].")
    minimum, maximum = map(float, delay_range)
    if minimum < 0 or maximum < minimum:
        raise ValueError(f"Invalid ChatGPT pacing range for {stage}: {delay_range}")
    delay = random.uniform(minimum, maximum)
    print(f"  [Pacing] {label}: {delay:.1f}s")
    time.sleep(delay)


def check_safe_stop(page):
    url = page.url.lower()
    if any(marker in url for marker in ("auth.openai.com", "/auth/login", "/auth/error")):
        return "ChatGPT login has expired."

    body = ""
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        pass

    lower_body = body.lower()
    captcha_markers = (
        "verify you are human",
        "security check",
        "captcha",
        "challenge-platform",
    )
    if any(marker in lower_body for marker in captcha_markers):
        return "ChatGPT human-verification challenge detected."

    from run_chatgpt_texture_batch import extract_chatgpt_quota_info
    is_quota, reset_info, quota_message = extract_chatgpt_quota_info(body)
    if is_quota:
        return f"CHATGPT_QUOTA_EXHAUSTED: {quota_message}"
    return None


def prepare_prompt(context_id):
    prompt = shared.BASE_PROMPT.replace(
        "{{FOLD_CONTEXT}}", shared.FOLD_CONTEXTS[context_id]["prompt"]
    )
    prompt = prompt.replace(
        "Preserve the aspect ratio of Image 1.",
        (
            f"The final image must use a {ASPECT_RATIO} landscape aspect ratio. "
            "Do not preserve the square aspect ratio of Image 1 and do not create a square output."
        ),
    )
    prompt += (
        "\n\nFINAL OUTPUT REQUIREMENT\n"
        f"Generate exactly one finished image in {ASPECT_RATIO} landscape format. "
        "Use only the newly uploaded texture in this message as Image 1. "
        "Ignore every image generated earlier in this conversation. "
        "Return an image, not an explanation."
    )
    return prompt


def open_new_image_chat(page):
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.locator("#prompt-textarea").wait_for(state="visible", timeout=60000)
    paced_sleep("after_page_load", "after opening a new chat")
    stop_reason = check_safe_stop(page)
    if stop_reason:
        raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")


def activate_create_image_mode(page):
    plus_button = page.locator('[data-testid="composer-plus-btn"]')
    plus_button.click()
    create_image = page.get_by_text("Create image", exact=True).last
    create_image.wait_for(state="visible", timeout=15000)
    create_image.click()
    page.get_by_text("Create image", exact=True).last.wait_for(
        state="visible", timeout=15000
    )
    paced_sleep("after_mode_select", "after selecting Create image")


def upload_texture(page, file_path):
    previous_sources = {
        source
        for source in page.locator("main img").evaluate_all(
            "images => images.map(image => image.currentSrc || image.src)"
        )
        if source
    }
    # ChatGPT currently keeps several hidden file inputs. Which one is active
    # changes between the normal composer and the Create image action modal.
    # Selecting the first enabled image input handles both DOM variants.
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
        image_input.set_input_files(str(file_path))
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
    print(f"  Texture attached: {file_path.name}")
    paced_sleep("after_upload", "after attaching the texture")


def generated_sources(page):
    sources = set()
    images = page.locator('main img[alt^="Generated image:"]')
    for index in range(images.count()):
        source = images.nth(index).get_attribute("src")
        if source:
            sources.add(source)
    return sources


def wait_for_generated_image(page, previous_sources, image_number):
    deadline = time.monotonic() + (TIMEOUT_MS / 1000)
    last_progress = 0
    while time.monotonic() < deadline:
        stop_reason = check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

        images = page.locator('main img[alt^="Generated image:"]')
        for index in range(images.count() - 1, -1, -1):
            candidate = images.nth(index)
            source = candidate.get_attribute("src")
            if source and source not in previous_sources and candidate.is_visible():
                return candidate

        elapsed = int(TIMEOUT_MS / 1000 - max(0, deadline - time.monotonic()))
        if elapsed - last_progress >= 30:
            print(f"  [{image_number}/2] Still generating ({elapsed}s elapsed)...")
            last_progress = elapsed
        page.wait_for_timeout(1000)

    raise PlaywrightTimeoutError(
        f"No new ChatGPT generated image appeared within {TIMEOUT_MS} ms."
    )


def download_generated_image(page, generated_image, save_path):
    generated_image.scroll_into_view_if_needed()
    generated_image.click()

    close_button = page.get_by_role("button", name="Close fullscreen view")
    close_button.wait_for(state="visible", timeout=15000)
    save_button = page.get_by_role("button", name=re.compile(r"^(Save|Download|Tải xuống)$"))
    save_button.wait_for(state="visible", timeout=15000)

    partial_path = save_path.with_name(f".{save_path.stem}.download{save_path.suffix}")
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            save_button.click()
        download = download_info.value
        download.save_as(str(partial_path))
    finally:
        if close_button.is_visible():
            close_button.click()

    with Image.open(partial_path) as image:
        width, height = image.size
        image_format = image.format
        image.load()

    expected = shared.ASPECT_RATIO_NUMERIC_VALUES[ASPECT_RATIO]
    actual = width / height
    ratio_error = abs(actual - expected) / expected
    if ratio_error > 0.04:
        invalid_path = save_path.with_name(
            f"{save_path.stem}_invalid_{width}x{height}{save_path.suffix}"
        )
        os.replace(partial_path, invalid_path)
        raise RuntimeError(
            f"ChatGPT returned {width}x{height} ({actual:.3f}) while {ASPECT_RATIO} "
            f"({expected:.3f}) was requested. The original file was kept at {invalid_path}."
        )
    os.replace(partial_path, save_path)
    return width, height, image_format


def inspect_existing_output(path):
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
        image.load()
    expected = shared.ASPECT_RATIO_NUMERIC_VALUES[ASPECT_RATIO]
    actual = width / height
    if abs(actual - expected) / expected > 0.04:
        raise RuntimeError(
            f"Existing ChatGPT output {path} is {width}x{height}, not {ASPECT_RATIO}."
        )
    return {"width": width, "height": height, "format": image_format}


def generate_one(page, file_path, prompt, image_number, save_path):
    activate_create_image_mode(page)
    previous_sources = generated_sources(page)
    upload_texture(page, file_path)

    composer = page.locator("#prompt-textarea")
    composer.fill(prompt)
    send_button = page.locator('[data-testid="send-button"]')
    deadline = time.monotonic() + 30
    while not send_button.is_enabled() and time.monotonic() < deadline:
        page.wait_for_timeout(500)
    if not send_button.is_enabled():
        raise RuntimeError("ChatGPT Send button stayed disabled after upload and prompt entry.")

    paced_sleep("before_send", f"before sending prompt {image_number}/2")
    print(f"  [{image_number}/2] Sending prompt and waiting for the generated image...")
    send_button.click()
    generated_image = wait_for_generated_image(page, previous_sources, image_number)

    paced_sleep("before_download", f"before downloading image {image_number}/2")
    print(f"  [{image_number}/2] Downloading the original image from ChatGPT...")
    width, height, image_format = download_generated_image(
        page, generated_image, save_path
    )
    print(f"  [{image_number}/2] Saved {width}x{height} {image_format} to {save_path}")
    paced_sleep("after_download", f"after downloading image {image_number}/2")
    return {"width": width, "height": height, "format": image_format}


def is_conversation_url(url):
    return re.fullmatch(r"https://chatgpt\.com/c/[0-9a-f-]+(?:[/?#].*)?", url) is not None


def delete_current_automation_chat(page, conversation_url, sku_dir):
    if not DELETE_CHAT_AFTER_DONE:
        return False
    if not is_conversation_url(conversation_url):
        raise RuntimeError(
            "Refusing to delete a chat because its automation conversation URL is missing."
        )
    required_outputs = [
        sku_dir / "image_1.png",
        sku_dir / "image_2.png",
        sku_dir / "metadata.json",
    ]
    if not all(path.exists() for path in required_outputs):
        raise RuntimeError("Refusing to delete the chat before all SKU outputs are saved.")

    expected = conversation_url.split("?", 1)[0].rstrip("/")
    current = page.url.split("?", 1)[0].rstrip("/")
    if current != expected:
        page.goto(conversation_url, wait_until="domcontentloaded", timeout=60000)
        page.locator('[data-testid="conversation-options-button"]').wait_for(
            state="visible", timeout=30000
        )
        current = page.url.split("?", 1)[0].rstrip("/")
    if current != expected:
        raise RuntimeError(
            f"Refusing to delete unexpected chat URL {page.url}; expected {conversation_url}."
        )

    paced_sleep("before_delete", "before deleting the completed automation chat")
    page.locator('[data-testid="conversation-options-button"]').click()
    delete_item = page.locator('[data-testid="delete-chat-menu-item"]')
    delete_item.wait_for(state="visible", timeout=15000)
    delete_item.click()
    confirm = page.locator('[data-testid="delete-conversation-confirm-button"]')
    confirm.wait_for(state="visible", timeout=15000)
    confirm.click()
    page.wait_for_function(
        "expected => location.href.split('?')[0].replace(/\\/$/, '') !== expected",
        arg=expected,
        timeout=30000,
    )
    print("  Deleted the completed automation chat from ChatGPT.")
    return True


def process_sku(page, sku, file_path):
    print("\n=========================================")
    print(f"Processing ChatGPT SKU: {sku}")
    sku_status = status_data.get(
        sku, {"status": "pending", "retries": 0, "error_type": None}
    )
    if sku_status.get("status") == "done":
        print(f"SKU {sku} already done in ChatGPT. Skipping.")
        return True
    if sku_status.get("retries", 0) >= MAX_RETRIES:
        print(f"SKU {sku} reached max ChatGPT retries. Skipping.")
        return False

    sku_status["status"] = "processing"
    status_data[sku] = sku_status
    save_status()

    sku_dir = OUTPUT_DIR / sku
    sku_dir.mkdir(parents=True, exist_ok=True)
    contexts = shared.select_contexts_for_sku(sku)
    print(f"  Selected Contexts: {contexts[0]}, {contexts[1]}")

    results = {}
    try:
        missing_outputs = [
            image_number
            for image_number in (1, 2)
            if not (sku_dir / f"image_{image_number}.png").exists()
        ]
        conversation_url = sku_status.get("conversation_url")
        if missing_outputs:
            if conversation_url and is_conversation_url(conversation_url):
                print(f"  Reopening the existing automation chat: {conversation_url}")
                page.goto(
                    conversation_url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.locator("#prompt-textarea").wait_for(
                    state="visible", timeout=60000
                )
                paced_sleep("after_page_load", "after reopening the SKU chat")
            else:
                print("  Opening one fresh ChatGPT chat for this SKU...")
                open_new_image_chat(page)

        for image_number, context_id in enumerate(contexts, start=1):
            save_path = sku_dir / f"image_{image_number}.png"
            if save_path.exists():
                results[str(image_number)] = inspect_existing_output(save_path)
                print(f"  [{image_number}/2] Existing output found. Skipping generation.")
                continue

            results[str(image_number)] = generate_one(
                page,
                file_path,
                prepare_prompt(context_id),
                image_number,
                save_path,
            )
            if is_conversation_url(page.url):
                conversation_url = page.url
                sku_status["conversation_url"] = conversation_url
                status_data[sku] = sku_status
                save_status()
            if image_number == 1:
                paced_sleep("between_images", "before generating the second image")

        metadata = {
            "sku": sku,
            "provider": "chatgpt_web",
            "generation": {"aspect_ratio": ASPECT_RATIO, "images": 2},
            "image1": {"fold_context": contexts[0], **results["1"]},
            "image2": {"fold_context": contexts[1], **results["2"]},
        }
        with open(sku_dir / "metadata.json", "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=4, ensure_ascii=False)

        if conversation_url is None:
            conversation_url = sku_status.get("conversation_url")
        chat_deleted = delete_current_automation_chat(
            page, conversation_url, sku_dir
        )
        sku_status.update(
            {
                "status": "done",
                "error_type": None,
                "chat_deleted": chat_deleted,
            }
        )
        if chat_deleted:
            sku_status.pop("conversation_url", None)
        status_data[sku] = sku_status
        save_status()
        print(f"ChatGPT SKU {sku} completed successfully!")
        paced_sleep("after_sku", "before moving to the next SKU")
        return True
    except Exception as exc:
        message = str(exc)
        print(f"  [ERROR] {message}")
        traceback.print_exc()
        try:
            screenshot = LOG_DIR / f"error_{sku}.png"
            page.screenshot(path=str(screenshot), full_page=False)
            print(f"  Saved error screenshot: {screenshot}")
        except Exception:
            pass

        if is_conversation_url(page.url):
            sku_status["conversation_url"] = page.url

        if "SAFE_STOP_REQUIRED" in message:
            sku_status.update({"status": "pending", "error_type": "SAFE_STOP"})
            status_data[sku] = sku_status
            save_status()
            return "STOP"

        sku_status["retries"] = int(sku_status.get("retries", 0)) + 1
        sku_status["status"] = (
            "error" if sku_status["retries"] >= MAX_RETRIES else "pending"
        )
        sku_status["error_type"] = (
            "timeout" if isinstance(exc, PlaywrightTimeoutError) else "unknown"
        )
        status_data[sku] = sku_status
        save_status()
        return False


def select_files(args, parser):
    files = sorted(shared.TEXTURES_DIR.glob("texture_*.*"))
    if args.sku:
        files = [
            path
            for path in files
            if re.fullmatch(rf"texture_{re.escape(args.sku)}\.[^.]+", path.name)
        ]
    if not files:
        print(f"No matching texture files found in {shared.TEXTURES_DIR}")
        return []

    eligible = []
    skipped_done = 0
    skipped_exhausted = 0
    for path in files:
        match = re.fullmatch(r"texture_(.+)\.[^.]+", path.name)
        if not match:
            continue
        item_status = status_data.get(match.group(1), {})
        if item_status.get("status") == "done":
            skipped_done += 1
        elif int(item_status.get("retries", 0)) >= MAX_RETRIES:
            skipped_exhausted += 1
        else:
            eligible.append(path)

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        eligible = eligible[: args.limit]

    print(
        f"Found {len(eligible)} unfinished ChatGPT textures to process "
        f"(skipped {skipped_done} done, {skipped_exhausted} at max retries)."
    )
    return eligible


def connect_to_automation_chrome(playwright):
    if CDP_CONNECT_TIMEOUT_MS < 1000:
        raise ValueError("chatgpt.cdp_connect_timeout_ms must be at least 1000.")
    if CDP_CONNECT_ATTEMPTS < 1:
        raise ValueError("chatgpt.cdp_connect_attempts must be at least 1.")

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
                    shared.CDP_URL,
                    timeout=CDP_CONNECT_TIMEOUT_MS,
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
        "Could not complete the Playwright CDP handshake after "
        f"{CDP_CONNECT_ATTEMPTS} attempts. Close only the VEO3 automation Chrome "
        "window, run start_chrome.bat, verify ChatGPT is still signed in, then retry. "
        f"Last error: {last_error}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate images with the signed-in ChatGPT web UI."
    )
    parser.add_argument("--sku", help="Process one exact SKU, for example: 101308")
    parser.add_argument(
        "--limit", type=int, help="Process at most this many unfinished ChatGPT SKUs"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show selected SKUs without opening Chrome"
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Test the CDP connection without creating chats or processing SKUs",
    )
    args = parser.parse_args()

    print("Starting ChatGPT Browser Batch Processor...")
    if shared.MISSING_FOLD_CONTEXT_IDS:
        print(
            "WARNING: Ignoring undefined fold contexts: "
            + ", ".join(shared.MISSING_FOLD_CONTEXT_IDS)
        )
    files = []
    if not args.check_browser:
        files = select_files(args, parser)
        if not files:
            print("Nothing eligible to process.")
            return

    if args.dry_run:
        print("Selected SKUs:")
        for path in files:
            print(f"  {re.fullmatch(r'texture_(.+)\.[^.]+', path.name).group(1)}")
        return

    if not shared.cdp_is_ready():
        shared.launch_automation_chrome()
        if not args.check_browser:
            print("Sign in to ChatGPT in the automation Chrome, then run this command again.")
            return

    print(f"Connecting to the signed-in Chrome via CDP ({shared.CDP_URL})...")
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
        page.on("response", log_request)
        page.bring_to_front()
        try:
            consecutive_failures = 0
            for file_path in files:
                sku = re.fullmatch(r"texture_(.+)\.[^.]+", file_path.name).group(1)
                result = process_sku(page, sku, file_path)
                if result == "STOP":
                    print(
                        "Batch stopped safely. Resolve login, verification, or quota "
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
