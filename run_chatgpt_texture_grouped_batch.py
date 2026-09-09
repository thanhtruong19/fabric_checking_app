"""Generate raw-scan textures in small, isolated ChatGPT conversations.

The first turn in each conversation includes the complete master prompt and an
image. Later turns attach only the next image and a short continuation request.
A fresh conversation is opened after a configurable number of successful turns.
"""

import argparse
import hashlib
import json
import random
import time
import traceback
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import run_batch as shared
import run_chatgpt_texture_batch as legacy
from seamless_packaging import package_and_report


SETTINGS = shared.config.get("chatgpt_texture_grouped", {})
BASE_SETTINGS = shared.config.get("chatgpt_texture", {})
CHATGPT_SETTINGS = shared.config.get("chatgpt", {})

RAW_DIR = shared.resolve_path(
    SETTINGS.get("raw_dir", BASE_SETTINGS.get("raw_dir", "textures_raw"))
)
TEXTURES_DIR = shared.resolve_path(
    SETTINGS.get("textures_dir", shared.PATHS.get("textures_dir", "textures"))
)
MASTER_PROMPT_FILE = shared.resolve_path(
    SETTINGS.get(
        "master_prompt_file",
        "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md",
    )
)
DEFAULT_PROMPT_MODE = str(SETTINGS.get("prompt_mode", "attachment")).strip().lower()
DEFAULT_PROMPT_TEXT = str(SETTINGS.get("prompt_text", "")).strip()
STATUS_FILE = shared.resolve_path(
    SETTINGS.get("status_file", "status_chatgpt_texture_grouped.json")
)


def output_path_for(sku, folder=None):
    if folder and str(folder) != ".":
        return TEXTURES_DIR / folder / f"texture_{sku}.png"
    return TEXTURES_DIR / f"texture_{sku}.png"
LOG_DIR = shared.LOGS_DIR / "chatgpt_texture_grouped"
DOWNLOAD_DIR = LOG_DIR / "downloads"
INVALID_DIR = LOG_DIR / "invalid"
TIMEOUT_MS = int(SETTINGS.get("timeout_ms", BASE_SETTINGS.get("timeout_ms", 300000)))
UPLOAD_TIMEOUT_MS = int(
    SETTINGS.get("upload_timeout_ms", BASE_SETTINGS.get("upload_timeout_ms", 90000))
)
DEFAULT_IMAGES_PER_CHAT = int(SETTINGS.get("images_per_chat", 10))
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
PACING = SETTINGS.get("pacing", {})
SUPPORTED_EXTENSIONS = legacy.SUPPORTED_EXTENSIONS

# Reuse the proven download/normalization routine while keeping this flow's
# temporary and rejected files isolated from the legacy one-chat-per-SKU flow.
legacy.DOWNLOAD_DIR = DOWNLOAD_DIR
legacy.INVALID_DIR = INVALID_DIR

FIRST_TURN_SUFFIX = (
    "\n\nHãy dùng ảnh vừa tải lên làm nguồn bắt buộc và tạo ngay chính xác "
    "01 ảnh MASTER TEXTURE seamless theo toàn bộ yêu cầu trên. Chỉ trả về ảnh "
    "kết quả, không giải thích."
)
CONTINUATION_PROMPT = (
    "Tiếp tục tạo texture cho ảnh mới vừa tải lên. Chỉ dùng ảnh mới này làm "
    "nguồn authoritative, áp dụng nguyên vẹn các quy tắc MASTER TEXTURE ở đầu "
    "cuộc trò chuyện và tạo chính xác 01 ảnh seamless 1024×1024 dạng JPG. Không dùng lại "
    "ảnh nguồn hay ảnh kết quả trước đó. Chỉ trả về ảnh, không giải thích."
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
    value = PACING.get(stage, DEFAULT_PACING[stage])
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(
            f"chatgpt_texture_grouped.pacing.{stage} must be [minimum, maximum]."
        )
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
        raise ValueError("chatgpt_texture_grouped.browser_recovery_attempts must be at least 1.")
    for stage in DEFAULT_PACING:
        pacing_range(stage)


def browser_connection_lost(page, exc):
    """Identify a closed tab/browser without depending on Playwright internals."""
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
    """Restore the automation Chrome/CDP connection and return a fresh page."""
    if not shared.cdp_is_ready():
        print("  [Recovery] Chrome automation is closed; starting it again...")
        shared.launch_automation_chrome()
    else:
        print("  [Recovery] Chrome is still running; reconnecting via CDP...")
    browser = legacy.connect_to_automation_chrome(playwright)
    context = browser.contexts[0]
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    page = legacy.get_automation_chatgpt_page(context)
    page.bring_to_front()
    print("  [Recovery] Browser connection restored with the reusable ChatGPT tab.")
    return browser, context, page


def discover_raw_files(parser, selected_sku=None):
    if not RAW_DIR.exists():
        print(f"Raw texture directory does not exist: {RAW_DIR}")
        return []
    files = []
    for path in sorted(RAW_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    keyed = {}
    duplicates = {}
    for path in files:
        sku = path.stem
        if selected_sku and sku != selected_sku:
            continue
        try:
            rel = path.parent.relative_to(RAW_DIR)
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
        parser.error(f"Multiple raw files map to the same texture name: {details}")
    return sorted(keyed.items())


def is_texture_ready(sku, folder=None):
    candidates = []
    if folder and str(folder) != ".":
        candidates.append(TEXTURES_DIR / folder / f"texture_{sku}.png")
    candidates.append(TEXTURES_DIR / f"texture_{sku}.png")

    out_root = shared.resolve_path(
        shared.config.get("seamless_package", {}).get(
            "output_dir", shared.config.get("chatgpt", {}).get("output_dir", "output/chatgpt")
        )
    )
    if folder and str(folder) != ".":
        candidates.append(out_root / folder / sku / "seamless_texture.png")
    candidates.append(out_root / sku / "seamless_texture.png")

    for candidate in candidates:
        if candidate.is_file():
            try:
                legacy.inspect_output(candidate)
                return True
            except Exception:
                pass

    if out_root.is_dir():
        for match in out_root.glob(f"*/{sku}/seamless_texture.png"):
            if match.is_file():
                try:
                    legacy.inspect_output(match)
                    return True
                except Exception:
                    pass
    return False


def select_files(args, parser, status_data):
    discovered = discover_raw_files(parser, args.sku)
    if args.sku and not discovered:
        parser.error(f"Raw texture SKU was not found: {args.sku}")

    if args.sku_file:
        with open(args.sku_file, "r", encoding="utf-8") as handle:
            values = json.load(handle)
        if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
            parser.error("--sku-file must contain a JSON array of non-empty SKU names")
        selected_skus = {value.casefold() for value in values}
        discovered = [item for item in discovered if item[0].casefold() in selected_skus]
    selected = []
    skipped_ready = 0
    skipped_exhausted = 0
    for sku, (path, folder) in discovered:
        item = status_data.get(sku, {})
        if not args.force and is_texture_ready(sku, folder):
            skipped_ready += 1
            continue
        if not args.force and int(item.get("retries", 0)) >= MAX_RETRIES:
            skipped_exhausted += 1
            continue
        selected.append((sku, path, folder))

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        selected = selected[: args.limit]
    print(
        f"Found {len(selected)} eligible raw texture(s) "
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
    create_image = page.get_by_text("Create image", exact=True).last
    create_image.wait_for(state="visible", timeout=15000)
    create_image.click()
    page.get_by_text("Create image", exact=True).last.wait_for(
        state="visible", timeout=15000
    )
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
    print(f"  Raw texture attached: {source_path.name}")
    paced_sleep("after_upload", "after attaching the new raw texture")


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
            legacy.dismiss_image_comparison(page)
            candidate = legacy.first_new_generated_image(page, previous_sources)
            if candidate is not None:
                return candidate

        elapsed = int(TIMEOUT_MS / 1000 - max(0, deadline - time.monotonic()))
        if elapsed - last_progress >= 30:
            print(
                f"  [{turn_number}/{images_per_chat}] Still generating "
                f"({elapsed}s elapsed)..."
            )
            last_progress = elapsed
        page.wait_for_timeout(1000)
    raise PlaywrightTimeoutError(
        f"No new ChatGPT generated image appeared within {TIMEOUT_MS} ms."
    )


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
    folder=None,
    force=False,
    prompt_attachment_file=None,
    master_doc_name=None,
):
    print("\n=========================================")
    print(f"Processing grouped texture SKU: {sku}")
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
            f"before sending texture {turn_number}/{images_per_chat} in this chat",
        )
        print(
            f"  [{turn_number}/{images_per_chat}] Sending {prompt_kind} prompt "
            "and waiting for the texture..."
        )
        send_button.click()
        try:
            page.wait_for_url("https://chatgpt.com/c/**", timeout=30000)
        except PlaywrightTimeoutError:
            pass

        generated_image = wait_for_generated_image(
            page, previous_sources, turn_number, images_per_chat
        )
        paced_sleep("before_download", "before downloading the generated texture")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        output_info = legacy.download_and_validate(
            page, generated_image, sku, target_path
        )
        print(
            f"  Saved {output_info['width']}x{output_info['height']} PNG to {target_path}"
        )
        package_result = package_and_report(sku, target_path, folder=folder, force=force)
        try:
            from run_algorithm_seamless_batch import refine_chatgpt_texture_seamless
            refine_chatgpt_texture_seamless(
                sku,
                folder=folder,
                project_dir=shared.PROJECT_DIR,
                final_path=package_result.get("path"),
                texture_path=target_path,
                refresh_raw_backup=force,
                verbose=True,
            )
        except Exception as ref_exc:
            print(f"  [Seamless Refiner] Cảnh báo hậu kỳ: {ref_exc}")
        paced_sleep("after_download", "after downloading the generated texture")

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
        print(f"ChatGPT grouped texture SKU {sku} completed successfully!")
        return True
    except Exception as exc:
        message = str(exc)
        print(f"  [ERROR] {message}")
        if browser_connection_lost(page, exc):
            if not force and target_path.exists():
                try:
                    output_info = legacy.inspect_output(target_path)
                    package_and_report(sku, target_path, folder=folder, force=False)
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
                        f"  [Recovery] Output for {sku} was already saved and valid; "
                        "marked as completed."
                    )
                    return True
                except Exception:
                    pass
            print(
                f"  [Recovery] Browser connection was lost while processing {sku}; "
                "the SKU remains pending and will be retried."
            )
            item.update({"status": "pending", "error_type": "browser_closed"})
            status_data[sku] = item
            save_status(status_data)
            return "RECONNECT"
        traceback.print_exc()
        try:
            screenshot = LOG_DIR / f"error_{sku}.png"
            page.screenshot(path=str(screenshot), full_page=False)
            print(f"  Saved error screenshot: {screenshot}")
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

        item["retries"] = int(item.get("retries", 0)) + 1
        item["status"] = "error" if item["retries"] >= MAX_RETRIES else "pending"
        item["error_type"] = (
            "timeout" if isinstance(exc, PlaywrightTimeoutError) else "unknown"
        )
        status_data[sku] = item
        save_status(status_data)
        return False


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate textures in ChatGPT, reusing one conversation for a small "
            "group and sending the full master prompt or attachment only on the first turn."
        )
    )
    parser.add_argument("--sku", help="Process one exact raw-texture SKU")
    parser.add_argument("--sku-file", help="JSON file containing exact SKU names to process")
    parser.add_argument("--limit", type=int, help="Process at most this many SKUs")
    parser.add_argument(
        "--images-per-chat",
        type=int,
        default=DEFAULT_IMAGES_PER_CHAT,
        help="Open a fresh conversation after this many successful images (default: 10)",
    )
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
        "--dry-run", action="store_true", help="Show selected SKUs without opening Chrome"
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate and replace selected outputs"
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Verify Chrome, ChatGPT login, and the new-chat composer",
    )
    args = parser.parse_args()

    prompt_mode = args.prompt_mode
    prompt_file = shared.resolve_path(args.prompt_file or MASTER_PROMPT_FILE)
    validate_settings(args.images_per_chat, prompt_mode=prompt_mode, prompt_file=prompt_file)

    for directory in (LOG_DIR, DOWNLOAD_DIR, INVALID_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    legacy.TEXTURES_DIR.mkdir(parents=True, exist_ok=True)

    prompt_attachment = None
    master_doc_name = prompt_file.name
    if prompt_mode == "attachment":
        prompt_attachment = prompt_file
        first_prompt = (
            f"Hãy áp dụng toàn bộ hướng dẫn và quy tắc trong tài liệu đính kèm ({prompt_file.name}) "
            "cho ảnh vừa tải lên để tạo ngay chính xác 01 ảnh MASTER TEXTURE seamless 1024×1024 dạng JPG. "
            "Chỉ trả về ảnh kết quả, không giải thích."
        )
        master_sha256 = sha256_file(prompt_file)
    else:
        manual_text = (args.prompt_text or DEFAULT_PROMPT_TEXT).strip()
        if not manual_text and prompt_file.exists():
            manual_text = prompt_file.read_text(encoding="utf-8").strip()
        if not manual_text:
            raise ValueError("Chưa có nội dung prompt thủ công và không tìm thấy file prompt mẫu.")
        first_prompt = manual_text + FIRST_TURN_SUFFIX
        master_sha256 = hashlib.sha256(manual_text.encode("utf-8")).hexdigest()
        master_doc_name = f"manual_text_{master_sha256[:8]}"

    status_data = load_status()
    selected = [] if args.check_browser else select_files(args, parser, status_data)

    print("ChatGPT grouped texture flow")
    print(f"Prompt mode: {prompt_mode} ({'file: ' + str(prompt_file) if prompt_mode == 'attachment' else 'manual text'})")
    print(f"Conversation size: {args.images_per_chat} successful image(s)")
    if args.dry_run:
        print("Selected raw textures:")
        for index, (sku, source_path, folder) in enumerate(selected, start=1):
            turn = ((index - 1) % args.images_per_chat) + 1
            prompt_kind = f"full master ({prompt_mode})" if turn == 1 else "continuation"
            target_path = output_path_for(sku, folder)
            print(
                f"  {sku}: {source_path.name} -> {target_path} "
                f"(turn {turn}, {prompt_kind})"
            )
        return
    if not args.check_browser and not selected:
        print("Nothing eligible to process.")
        return

    if not shared.cdp_is_ready():
        shared.launch_automation_chrome()
        if not args.check_browser:
            print("Sign in to ChatGPT in the automation Chrome, then run again.")
            return

    print(f"Connecting to signed-in Chrome via CDP ({shared.CDP_URL})...")
    with sync_playwright() as playwright:
        try:
            browser = legacy.connect_to_automation_chrome(playwright)
        except Exception as exc:
            print(f"FATAL: {exc}")
            return
        context = browser.contexts[0]
        context.set_default_timeout(30000)
        context.set_default_navigation_timeout(60000)
        page = legacy.get_automation_chatgpt_page(context)
        page.bring_to_front()
        try:
            if args.check_browser:
                open_fresh_chat(page)
                print(f"Browser and ChatGPT check passed; {len(context.pages)} open tab(s).")
                return

            turn_in_chat = 0
            conversation_number = 0
            consecutive_failures = 0
            browser_recoveries = 0
            skip_next_chat_delay = False
            selected_index = 0
            while selected_index < len(selected):
                sku, source_path, folder = selected[selected_index]
                if turn_in_chat == 0:
                    if conversation_number > 0 and not skip_next_chat_delay:
                        paced_sleep("between_chats", "before opening the next conversation")
                    skip_next_chat_delay = False
                    conversation_number += 1
                    print(
                        f"\nOpening ChatGPT conversation {conversation_number}; "
                        f"up to {args.images_per_chat} textures will use this chat."
                    )
                    try:
                        open_fresh_chat(page)
                    except Exception as exc:
                        if not browser_connection_lost(page, exc):
                            raise
                        browser_recoveries += 1
                        if browser_recoveries > MAX_BROWSER_RECOVERIES:
                            raise RuntimeError(
                                "Could not keep Chrome connected after "
                                f"{MAX_BROWSER_RECOVERIES} recovery attempts."
                            ) from exc
                        print(
                            "  [Recovery] Browser closed while opening a chat; "
                            "reconnecting immediately."
                        )
                        browser, context, page = connect_recovery_page(playwright)
                        skip_next_chat_delay = True
                        continue

                turn_number = turn_in_chat + 1
                prompt_kind = f"full_master_{prompt_mode}" if turn_number == 1 else "continuation"
                prompt = first_prompt if turn_number == 1 else CONTINUATION_PROMPT
                target_path = output_path_for(sku, folder)
                result = process_turn(
                    page,
                    sku,
                    source_path,
                    target_path,
                    prompt,
                    prompt_kind,
                    turn_number,
                    args.images_per_chat,
                    status_data,
                    master_sha256,
                    folder=folder,
                    force=args.force,
                    prompt_attachment_file=prompt_attachment if turn_number == 1 else None,
                    master_doc_name=master_doc_name,
                )
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
                    break
                if result == "RECONNECT":
                    turn_in_chat = 0
                    browser_recoveries += 1
                    if browser_recoveries > MAX_BROWSER_RECOVERIES:
                        raise RuntimeError(
                            "Could not keep Chrome connected after "
                            f"{MAX_BROWSER_RECOVERIES} recovery attempts. "
                            f"SKU {sku} is still pending."
                        )
                    browser, context, page = connect_recovery_page(playwright)
                    skip_next_chat_delay = True
                    continue
                if result is not True:
                    consecutive_failures += 1
                    turn_in_chat = 0
                    selected_index += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        print(
                            f"Batch stopped after {consecutive_failures} consecutive failures."
                        )
                        break
                    continue

                consecutive_failures = 0
                browser_recoveries = 0
                selected_index += 1
                turn_in_chat += 1
                if turn_in_chat >= args.images_per_chat:
                    turn_in_chat = 0
                else:
                    paced_sleep("between_images", "before attaching the next raw texture")
        finally:
            # Keep this tab open for the swatch step and subsequent SKU workers.
            pass


if __name__ == "__main__":
    with shared.single_instance_lock() as lock_acquired:
        if not lock_acquired:
            print(
                "ERROR: Another Flow/ChatGPT batch is controlling Chrome port "
                f"{shared.CDP_PORT}. Stop it before starting this runner."
            )
            raise SystemExit(2)
        main()
