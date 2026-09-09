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
from seamless_packaging import package_and_report


SETTINGS = shared.config.get("chatgpt_texture", {})
CHATGPT = shared.config.get("chatgpt", {})
HOME_URL = CHATGPT.get("home_url", "https://chatgpt.com/")
RAW_DIR = shared.resolve_path(SETTINGS.get("raw_dir", "textures_raw"))
PROMPT_FILE = shared.resolve_path(
    SETTINGS.get("prompt_file", "prompts/scanned_to_texture_prompt.md")
)
STATUS_FILE = shared.resolve_path(
    SETTINGS.get("status_file", "status_chatgpt_texture.json")
)
TEXTURES_DIR = shared.TEXTURES_DIR
TARGET_WIDTH = int(SETTINGS.get("target_width", 2048))
TARGET_HEIGHT = int(SETTINGS.get("target_height", 2048))
ALLOW_UNIFORM_RESIZE = bool(SETTINGS.get("allow_uniform_resize", True))
MINIMUM_SOURCE_WIDTH = int(SETTINGS.get("minimum_source_width", 1024))
MINIMUM_SOURCE_HEIGHT = int(SETTINGS.get("minimum_source_height", 1024))
ASPECT_RATIO_TOLERANCE = float(SETTINGS.get("aspect_ratio_tolerance", 0.01))
TIMEOUT_MS = int(SETTINGS.get("timeout_ms", 300000))
UPLOAD_TIMEOUT_MS = int(SETTINGS.get("upload_timeout_ms", 90000))
DOWNLOAD_TIMEOUT_MS = int(SETTINGS.get("download_timeout_ms", 90000))
DELETE_CHAT_AFTER_DONE = bool(SETTINGS.get("delete_chat_after_done", True))
PACING = SETTINGS.get("pacing", {})
CDP_CONNECT_TIMEOUT_MS = int(CHATGPT.get("cdp_connect_timeout_ms", 20000))
CDP_CONNECT_ATTEMPTS = int(CHATGPT.get("cdp_connect_attempts", 4))
CDP_CONNECT_RETRY_SECONDS = float(CHATGPT.get("cdp_connect_retry_seconds", 3))
MAX_RETRIES = int(shared.RETRY.get("max_retries", 3))
MAX_CONSECUTIVE_FAILURES = int(
    shared.RETRY.get("max_consecutive_failures", 3)
)
LOG_DIR = shared.LOGS_DIR / "chatgpt_texture"
DOWNLOAD_DIR = LOG_DIR / "downloads"
INVALID_DIR = LOG_DIR / "invalid"
REQUESTS_LOG = LOG_DIR / "requests.jsonl"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_PACING = {
    "after_page_load": [3, 6],
    "after_mode_select": [2, 4],
    "after_upload": [5, 10],
    "before_send": [8, 15],
    "before_download": [2, 5],
    "after_download": [3, 6],
    "before_delete": [3, 7],
    "after_sku": [60, 120],
}

for directory in (TEXTURES_DIR, LOG_DIR, DOWNLOAD_DIR, INVALID_DIR):
    directory.mkdir(parents=True, exist_ok=True)

if TARGET_WIDTH < 1 or TARGET_HEIGHT < 1:
    raise ValueError("chatgpt_texture target dimensions must be positive integers.")
if MINIMUM_SOURCE_WIDTH < 1 or MINIMUM_SOURCE_HEIGHT < 1:
    raise ValueError("chatgpt_texture minimum source dimensions must be positive.")
if not 0 <= ASPECT_RATIO_TOLERANCE <= 0.1:
    raise ValueError(
        "chatgpt_texture.aspect_ratio_tolerance must be between 0 and 0.1."
    )
if not DELETE_CHAT_AFTER_DONE:
    raise ValueError(
        "chatgpt_texture.delete_chat_after_done must remain true so every automation "
        "chat is removed before the next chat is created."
    )

with open(PROMPT_FILE, "r", encoding="utf-8") as handle:
    BASE_PROMPT = handle.read().strip()

FINAL_REQUIREMENT = (
    "\n\nYÊU CẦU ĐẦU RA CUỐI CÙNG\n"
    f"Tạo chính xác một ảnh texture vuông {TARGET_WIDTH}×{TARGET_HEIGHT}. "
    "Ảnh được trả về phải là PNG master seamless, không phải preview tiled, "
    "contact sheet hoặc ảnh minh họa trong một khung khác. "
    "Chỉ dùng ảnh scan vừa tải lên trong tin nhắn này làm ảnh nguồn. "
    "Không trả về văn bản giải thích."
)
PROMPT = BASE_PROMPT + FINAL_REQUIREMENT


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


PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


def paced_sleep(stage, label):
    delay_range = PACING.get(stage, DEFAULT_PACING[stage])
    if not isinstance(delay_range, list) or len(delay_range) != 2:
        raise ValueError(
            f"chatgpt_texture.pacing.{stage} must contain [minimum, maximum]."
        )
    minimum, maximum = map(float, delay_range)
    if minimum < 0 or maximum < minimum:
        raise ValueError(f"Invalid pacing range for {stage}: {delay_range}")
    delay = random.uniform(minimum, maximum)
    print(f"  [Pacing] {label}: {delay:.1f}s")
    time.sleep(delay)


def log_response(response):
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
        "time": now_text(),
        "status": response.status,
        "method": response.request.method,
        "url": response.url.split("?", 1)[0],
    }
    try:
        with open(REQUESTS_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def extract_chatgpt_quota_info(text):
    """
    Detect if ChatGPT image creation quota is exhausted and extract wait / reset time if present.
    Returns (is_quota: bool, reset_info: str | None, message: str | None)
    """
    if not text:
        return False, None, None

    lower_text = text.lower()
    quota_markers = (
        "you're out of images",
        "you are out of images",
        "you're out of image creations",
        "you are out of image creations",
        "you've reached your image creation limit",
        "you've reached your image generation limit",
        "you've reached your limit",
        "you have reached your limit",
        "reached the current limit for image",
        "reached your limit for creating images",
        "image generation limit",
        "image creations limit",
        "hạn mức tạo ảnh đã hết",
        "hạn mức sẽ được đặt lại sau",
        "không thể tạo ảnh vì hạn mức",
        "bạn đã dùng hết số lượt tạo ảnh",
        "bạn đã dùng hết hạn mức",
        "bạn đã đạt giới hạn",
        "bạn đã đạt đến hạn mức",
        "đã hết lượt tạo ảnh",
        "upgrade your plan to continue, or wait for more",
    )
    is_quota = any(marker in lower_text for marker in quota_markers)
    if not is_quota:
        return False, None, None

    parts = []

    # 1. Match Vietnamese duration e.g. "sau khoảng 6 giờ 44 phút" / "sau 45 phút"
    vn_dur_match = re.search(
        r'(?:đặt lại sau khoảng|đặt lại sau|thử lại sau khoảng|thử lại sau|sau khoảng|sau)\s*([0-9]+\s*(?:giờ|tiếng|phút|giây|h|m|s)(?:\s*(?:và\s*)?[0-9]+\s*(?:phút|giây|m|s))?)',
        text,
        re.IGNORECASE,
    )
    if vn_dur_match:
        dur_str = vn_dur_match.group(1).strip()
        parts.append(f"sau khoảng {dur_str}")

    # 2. Match English duration e.g. "in 3 hours and 15 minutes" / "in 45 minutes"
    if not vn_dur_match:
        en_dur_match = re.search(
            r'(?:try again in|resets? in|wait for|in)\s+([0-9]+\s*(?:hours?|hrs?|minutes?|mins?|secs?)(?:\s*(?:and\s*)?[0-9]+\s*(?:minutes?|mins?|secs?))?)',
            text,
            re.IGNORECASE,
        )
        if en_dur_match:
            dur_str = en_dur_match.group(1).strip()
            parts.append(f"sau {dur_str}")

    # 3. Match Clock time e.g. "wait for more at 4:17 PM" / "after 12:30 PM" / "resets at 16:30" / "lúc 16:30"
    time_match = re.search(
        r'(?:wait for more at|try again at|resets? at|continue after|after|vào lúc|lúc)\s+([0-9]{1,2}:[0-9]{2}(?:\s*[AP]M)?)',
        text,
        re.IGNORECASE,
    )
    if time_match:
        clock_str = time_match.group(1).strip()
        parts.append(f"lúc {clock_str}")

    if parts:
        reset_info = " (hoặc ".join(parts) + (")" if len(parts) > 1 else "")
        message = f"Hết hạn mức tạo ảnh ChatGPT! Thời gian chờ / đặt lại: {reset_info}."
    else:
        reset_info = None
        message = "Hết hạn mức tạo ảnh ChatGPT! Hãy đợi hệ thống đặt lại hạn mức."

    return True, reset_info, message


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

    is_quota, reset_info, quota_message = extract_chatgpt_quota_info(body)
    if is_quota:
        return f"CHATGPT_QUOTA_EXHAUSTED: {quota_message}"

    return None


def is_conversation_url(url):
    return re.fullmatch(
        r"https://chatgpt\.com/c/[0-9a-f-]+(?:[/?#].*)?", url
    ) is not None


def normalized_url(url):
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def output_path_for(sku):
    return TEXTURES_DIR / f"texture_{sku}.png"


def inspect_output(path):
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
    if image_format != "PNG":
        raise RuntimeError(f"{path} contains {image_format}, not a real PNG file.")
    is_valid_size = (width, height) == (TARGET_WIDTH, TARGET_HEIGHT) or (
        width >= 512 and height >= 512 and abs(width / height - 1.0) <= ASPECT_RATIO_TOLERANCE + 0.05
    )
    if not is_valid_size:
        raise RuntimeError(
            f"{path} is {width}x{height}; expected square {TARGET_WIDTH}x{TARGET_HEIGHT}."
        )
    return {"width": width, "height": height, "format": image_format}


def open_new_image_chat(page):
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.locator("#prompt-textarea").wait_for(state="visible", timeout=60000)
    paced_sleep("after_page_load", "after opening a new chat")
    stop_reason = check_safe_stop(page)
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


def upload_scan(page, file_path):
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
    print(f"  Raw scan attached: {file_path.name}")
    paced_sleep("after_upload", "after attaching the raw scan")


def generated_sources(page):
    sources = set()
    images = page.locator('main img[alt^="Generated image:"]')
    for index in range(images.count()):
        source = images.nth(index).get_attribute("src")
        if source:
            sources.add(source)
    return sources


def dismiss_image_comparison(page):
    """Dismiss ChatGPT's optional two-image preference prompt."""
    try:
        skip_controls = page.get_by_text(
            re.compile(r"^(Skip|Bỏ qua)$", re.IGNORECASE), exact=True
        )
        for index in range(skip_controls.count() - 1, -1, -1):
            control = skip_controls.nth(index)
            if control.is_visible():
                control.click()
                page.wait_for_timeout(500)
                print("  ChatGPT returned an image comparison; skipped it and selected image 1.")
                return True
    except Exception:
        # This prompt is optional and may disappear while the DOM is being read.
        pass
    return False


def first_new_generated_image(page, previous_sources):
    """Return image 1 when ChatGPT produces one or more new images."""
    images = page.locator('main img[alt^="Generated image:"]')
    for index in range(images.count()):
        candidate = images.nth(index)
        source = candidate.get_attribute("src")
        if source and source not in previous_sources and candidate.is_visible():
            return candidate
    return None


def record_conversation(page, sku, sku_status):
    if not is_conversation_url(page.url):
        return None
    conversation_url = normalized_url(page.url)
    sku_status.update(
        {
            "conversation_url": conversation_url,
            "chat_cleanup_pending": True,
        }
    )
    status_data[sku] = sku_status
    save_status()
    return conversation_url


def wait_for_generated_image(page, previous_sources):
    deadline = time.monotonic() + (TIMEOUT_MS / 1000)
    last_progress = 0
    while time.monotonic() < deadline:
        stop_reason = check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

        dismiss_image_comparison(page)
        candidate = first_new_generated_image(page, previous_sources)
        if candidate is not None:
            return candidate

        elapsed = int(TIMEOUT_MS / 1000 - max(0, deadline - time.monotonic()))
        if elapsed - last_progress >= 30:
            print(f"  Still generating ({elapsed}s elapsed)...")
            last_progress = elapsed
        page.wait_for_timeout(1000)

    raise PlaywrightTimeoutError(
        f"No new ChatGPT generated image appeared within {TIMEOUT_MS} ms."
    )


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
                    f"ChatGPT returned {source_width}x{source_height}; expected "
                    f"{TARGET_WIDTH}x{TARGET_HEIGHT} and uniform resize is disabled."
                )
            if ratio_error > ASPECT_RATIO_TOLERANCE:
                raise RuntimeError(
                    f"ChatGPT returned aspect ratio {source_width}:{source_height}; "
                    f"expected {TARGET_WIDTH}:{TARGET_HEIGHT}. Refusing to stretch it."
                )
            if (
                source_width < MINIMUM_SOURCE_WIDTH
                or source_height < MINIMUM_SOURCE_HEIGHT
            ):
                raise RuntimeError(
                    f"ChatGPT returned only {source_width}x{source_height}; minimum "
                    f"accepted source is {MINIMUM_SOURCE_WIDTH}x{MINIMUM_SOURCE_HEIGHT}."
                )

        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        converted = image.convert("RGBA" if has_alpha else "RGB")
        if not is_exact_size:
            converted = converted.resize(
                (TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS
            )
        converted.save(staged_png, format="PNG", optimize=True, compress_level=9)

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


def get_automation_chatgpt_page(context):
    """Reuse a ChatGPT tab in the dedicated automation browser across workers."""
    from urllib.parse import urlparse
    available = [page for page in context.pages if not page.is_closed()]
    for page in available:
        if urlparse(page.url).hostname in {"chatgpt.com", "chat.openai.com"}:
            return page
    for page in available:
        if page.url in {"about:blank", "chrome://newtab/"}:
            return page
    return context.new_page()


def download_and_validate(page, generated_image, sku, target_path):
    generated_image.scroll_into_view_if_needed()
    generated_image.click()

    close_button = page.get_by_role(
        "button", name=re.compile(r"^(Close fullscreen view|Close|Đóng.*)$")
    ).last
    close_button.wait_for(state="visible", timeout=15000)
    save_button = page.get_by_role(
        "button",
        name=re.compile(
            r"^(Save|Download|Download image|Tải xuống|Tải hình ảnh)$",
            re.IGNORECASE,
        ),
    ).last
    save_button.wait_for(state="visible", timeout=15000)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    downloaded_path = DOWNLOAD_DIR / f"{sku}_{stamp}_{os.getpid()}.download"
    # Atomic replacement requires the staged PNG and output to share a drive.
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staged_png = target_path.parent / f".{sku}_{stamp}_{os.getpid()}.png"
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            save_button.click()
        download_info.value.save_as(str(downloaded_path))
    finally:
        try:
            if close_button.is_visible():
                close_button.click()
        except Exception:
            pass

    try:
        try:
            output_info = normalize_image_to_master(downloaded_path, staged_png)
        except Exception as image_error:
            invalid_path = INVALID_DIR / f"{sku}_{stamp}_{os.getpid()}.bin"
            os.replace(downloaded_path, invalid_path)
            raise RuntimeError(
                f"Downloaded ChatGPT image was rejected. Original kept at "
                f"{invalid_path}: {image_error}"
            ) from image_error
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_png, target_path)
    finally:
        if downloaded_path.exists():
            downloaded_path.unlink()
        if staged_png.exists():
            staged_png.unlink()

    return output_info


def recover_previous_download(sku, target_path):
    candidates = sorted(
        INVALID_DIR.glob(f"{sku}_*"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for candidate in candidates:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staged_png = target_path.parent / f".{sku}_recovery_{os.getpid()}.png"
        try:
            output_info = normalize_image_to_master(candidate, staged_png)
            os.replace(staged_png, target_path)
            output_info["recovered_from"] = str(candidate)
            print(
                f"  Recovered previous {output_info['source_width']}x"
                f"{output_info['source_height']} download as {target_path.name}."
            )
            return output_info
        except Exception:
            if staged_png.exists():
                staged_png.unlink()
    return None


def delete_automation_chat(page, sku, conversation_url):
    if not is_conversation_url(conversation_url):
        raise RuntimeError(
            f"Refusing to delete chat for {sku}: invalid conversation URL."
        )

    expected = normalized_url(conversation_url)
    if normalized_url(page.url) != expected:
        page.goto(expected, wait_until="domcontentloaded", timeout=60000)
        stop_reason = check_safe_stop(page)
        if stop_reason:
            raise RuntimeError(f"SAFE_STOP_REQUIRED: {stop_reason}")

    if normalized_url(page.url) != expected:
        print(f"  Chat for {sku} is already unavailable; treating it as deleted.")
        return True

    options_button = page.locator('[data-testid="conversation-options-button"]')
    options_button.wait_for(state="visible", timeout=30000)
    paced_sleep("before_delete", f"before deleting the completed chat for {sku}")
    options_button.click()
    delete_item = page.locator('[data-testid="delete-chat-menu-item"]')
    delete_item.wait_for(state="visible", timeout=15000)
    delete_item.click()
    confirm = page.locator('[data-testid="delete-conversation-confirm-button"]')
    confirm.wait_for(state="visible", timeout=15000)
    confirm.click()
    page.wait_for_function(
        "expected => location.href.split('?')[0].split('#')[0].replace(/\\/$/, '') !== expected",
        arg=expected,
        timeout=30000,
    )
    print(f"  Deleted ChatGPT automation chat for {sku}.")
    return True


def pending_cleanup_items():
    pending = []
    for sku, item in status_data.items():
        if not isinstance(item, dict):
            continue
        conversation_url = item.get("conversation_url")
        if conversation_url and not item.get("chat_deleted", False):
            pending.append((sku, conversation_url))
    return pending


def cleanup_pending_chats(page):
    pending = pending_cleanup_items()
    for sku, conversation_url in pending:
        print(f"Cleaning pending ChatGPT chat before creating a new one: {sku}")
        item = status_data[sku]
        delete_automation_chat(page, sku, conversation_url)
        item.update(
            {
                "chat_deleted": True,
                "chat_cleanup_pending": False,
                "chat_deleted_at": now_text(),
                "error_type": None,
            }
        )
        item.pop("conversation_url", None)
        target_path = output_path_for(sku)
        try:
            output_info = inspect_output(target_path)
        except Exception:
            output_info = None
        if output_info is not None:
            item.update(
                {
                    "status": "done",
                    "output": output_info,
                    "completed_at": item.get("completed_at", now_text()),
                }
            )
        elif int(item.get("retries", 0)) >= MAX_RETRIES:
            item["status"] = "error"
        else:
            item["status"] = "pending"
        status_data[sku] = item
        save_status()


def process_sku(page, sku, raw_path, force=False):
    print("\n=========================================")
    print(f"Processing raw texture SKU: {sku}")
    target_path = output_path_for(sku)
    sku_status = status_data.get(
        sku, {"status": "pending", "retries": 0, "error_type": None}
    )

    if not force and target_path.exists():
        output_info = inspect_output(target_path)
        package_and_report(sku, target_path)
        sku_status.update(
            {
                "status": "done",
                "error_type": None,
                "output": output_info,
                "completed_at": sku_status.get("completed_at", now_text()),
            }
        )
        status_data[sku] = sku_status
        save_status()
        print(f"  Valid output already exists: {target_path}")
        return True

    if force:
        sku_status["retries"] = 0
    elif int(sku_status.get("retries", 0)) >= MAX_RETRIES:
        print(f"SKU {sku} reached max retries. Skipping.")
        return False

    sku_status.update(
        {
            "status": "processing",
            "error_type": None,
            "source_file": raw_path.name,
            "source_sha256": sha256_file(raw_path),
            "prompt_sha256": PROMPT_SHA256,
            "started_at": now_text(),
            "chat_deleted": False,
        }
    )
    status_data[sku] = sku_status
    save_status()

    try:
        print("  Opening a fresh ChatGPT image chat...")
        open_new_image_chat(page)
        activate_create_image_mode(page)
        previous_sources = generated_sources(page)
        upload_scan(page, raw_path)

        composer = page.locator("#prompt-textarea")
        composer.fill(PROMPT)
        send_button = page.locator('[data-testid="send-button"]')
        deadline = time.monotonic() + 30
        while not send_button.is_enabled() and time.monotonic() < deadline:
            page.wait_for_timeout(500)
        if not send_button.is_enabled():
            raise RuntimeError(
                "ChatGPT Send button stayed disabled after upload and prompt entry."
            )

        paced_sleep("before_send", "before sending the seamless-texture prompt")
        print("  Sending prompt and waiting for the generated texture...")
        send_button.click()
        try:
            page.wait_for_url("https://chatgpt.com/c/**", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        record_conversation(page, sku, sku_status)

        generated_image = wait_for_generated_image(page, previous_sources)
        record_conversation(page, sku, sku_status)
        paced_sleep("before_download", "before downloading the generated texture")
        print("  Downloading and validating the original image...")
        output_info = download_and_validate(page, generated_image, sku, target_path)
        print(
            f"  Saved {output_info['width']}x{output_info['height']} PNG to {target_path}"
        )
        package_and_report(sku, target_path, force=force)
        paced_sleep("after_download", "after downloading the generated texture")

        conversation_url = sku_status.get("conversation_url")
        if not conversation_url:
            conversation_url = record_conversation(page, sku, sku_status)
        if not conversation_url:
            raise RuntimeError(
                "Generated texture was saved, but the automation chat URL is missing; "
                "refusing to create another chat until cleanup can be verified."
            )

        sku_status.update(
            {
                "status": "cleanup_pending",
                "output": output_info,
                "completed_at": now_text(),
                "chat_cleanup_pending": True,
            }
        )
        status_data[sku] = sku_status
        save_status()

        try:
            delete_automation_chat(page, sku, conversation_url)
        except Exception as cleanup_error:
            sku_status.update(
                {
                    "status": "cleanup_pending",
                    "error_type": "chat_cleanup",
                    "chat_cleanup_pending": True,
                }
            )
            status_data[sku] = sku_status
            save_status()
            print(f"  [ERROR] Chat cleanup failed: {cleanup_error}")
            return "STOP"

        sku_status.update(
            {
                "status": "done",
                "error_type": None,
                "chat_deleted": True,
                "chat_cleanup_pending": False,
                "chat_deleted_at": now_text(),
            }
        )
        sku_status.pop("conversation_url", None)
        status_data[sku] = sku_status
        save_status()
        print(f"ChatGPT texture SKU {sku} completed successfully!")
        paced_sleep("after_sku", "before moving to the next raw texture")
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

        record_conversation(page, sku, sku_status)
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


def discover_raw_files(parser, selected_sku=None):
    if not RAW_DIR.exists():
        print(f"Raw texture directory does not exist: {RAW_DIR}")
        return []
    files = sorted(
        path
        for path in RAW_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if selected_sku:
        files = [path for path in files if path.stem == selected_sku]

    duplicates = {}
    for path in files:
        duplicates.setdefault(path.stem, []).append(path)
    collisions = {sku: paths for sku, paths in duplicates.items() if len(paths) > 1}
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
        item_status = status_data.get(sku, {})
        target_path = output_path_for(sku)
        has_pending_chat = bool(
            item_status.get("conversation_url")
            and not item_status.get("chat_deleted", False)
        )
        if args.force:
            eligible.append(path)
            continue
        if target_path.exists():
            try:
                inspect_output(target_path)
            except Exception as exc:
                invalid_existing.append(f"{target_path.name}: {exc}")
                continue
            if has_pending_chat:
                eligible.append(path)
            else:
                skipped_existing += 1
            continue
        if int(item_status.get("retries", 0)) >= MAX_RETRIES:
            skipped_exhausted += 1
            continue
        eligible.append(path)

    if invalid_existing:
        print("WARNING: Existing outputs are invalid and will not be overwritten without --force:")
        for message in invalid_existing:
            print(f"  {message}")

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        eligible = eligible[: args.limit]

    print(
        f"Found {len(eligible)} raw textures to process "
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
                "source_file": raw_path.name,
                "source_sha256": sha256_file(raw_path),
                "prompt_sha256": PROMPT_SHA256,
                "output": output_info,
                "completed_at": now_text(),
                "chat_deleted": True,
                "chat_cleanup_pending": False,
            }
        )
        item.pop("conversation_url", None)
        status_data[sku] = item
        save_status()
    return remaining


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
        "Could not complete the Playwright CDP handshake after "
        f"{CDP_CONNECT_ATTEMPTS} attempts. Close only the automation Chrome window, "
        "run start_chrome.bat, verify ChatGPT is signed in, then retry. "
        f"Last error: {last_error}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert raw fabric scans into seamless 2048x2048 PNG textures with "
            "the signed-in ChatGPT web UI."
        )
    )
    parser.add_argument("--sku", help="Process one exact raw filename stem, e.g. 101301")
    parser.add_argument(
        "--limit", type=int, help="Process at most this many unfinished raw textures"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show selected SKUs without opening Chrome"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate selected textures and atomically replace valid existing outputs",
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Test the CDP connection without creating or deleting chats",
    )
    args = parser.parse_args()

    print("Starting ChatGPT Raw-to-Texture Batch Processor...")
    files = []
    cleanup_count = len(pending_cleanup_items())
    if not args.check_browser:
        files = select_files(args, parser)
        if not files and cleanup_count == 0:
            print("Nothing eligible to process.")
            return

    if args.dry_run:
        if cleanup_count:
            print(f"Pending automation chats to delete first: {cleanup_count}")
        print("Selected raw texture SKUs:")
        for path in files:
            print(f"  {path.stem} -> texture_{path.stem}.png")
        return

    files = recover_selected_files(files, force=args.force)
    if not files and cleanup_count == 0:
        print("All selected SKUs were recovered from previous valid downloads.")
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
        page = get_automation_chatgpt_page(context)
        page.on("response", log_response)
        page.bring_to_front()
        try:
            try:
                cleanup_pending_chats(page)
            except Exception as exc:
                print(f"FATAL: Could not delete a pending automation chat: {exc}")
                return

            consecutive_failures = 0
            for raw_path in files:
                try:
                    cleanup_pending_chats(page)
                except Exception as exc:
                    print(f"FATAL: Could not delete a pending automation chat: {exc}")
                    break

                result = process_sku(page, raw_path.stem, raw_path, force=args.force)
                if result == "STOP":
                    print(
                        "Batch stopped safely. Resolve login, verification, quota, or "
                        "chat cleanup in the visible Chrome window, then run again."
                    )
                    break
                if result is True:
                    consecutive_failures = 0
                    continue

                try:
                    cleanup_pending_chats(page)
                except Exception as exc:
                    print(f"FATAL: Could not delete the failed automation chat: {exc}")
                    break
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"Batch stopped after {consecutive_failures} consecutive failures."
                    )
                    break
        finally:
            page.remove_listener("response", log_response)


if __name__ == "__main__":
    with shared.single_instance_lock() as lock_acquired:
        if not lock_acquired:
            print(
                "ERROR: Another Flow/ChatGPT batch is controlling Chrome port "
                f"{shared.CDP_PORT}. Stop it before starting this runner."
            )
            raise SystemExit(2)
        main()
