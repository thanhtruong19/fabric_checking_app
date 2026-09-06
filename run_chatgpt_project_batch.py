import argparse
import hashlib
import json
import os
import random
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import run_batch as shared
from seamless_packaging import package_and_report


SETTINGS = shared.config.get("chatgpt_project", {})
BASE_CHATGPT = shared.config.get("chatgpt", {})
FLOW_SETTINGS = SETTINGS.get("flows", {})
RAW_DIR = shared.resolve_path(
    shared.config.get("chatgpt_texture", {}).get("raw_dir", "textures_raw")
)
TEXTURES_DIR = shared.TEXTURES_DIR
PROJECT_URL_CONFIG = str(SETTINGS.get("project_url", "")).strip()
TEXTURE_MASTER = shared.resolve_path(
    SETTINGS.get(
        "texture_master_file",
        "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md",
    )
)
FABRIC_MASTER = shared.resolve_path(
    SETTINGS.get(
        "fabric_master_file",
        "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md",
    )
)
TIMEOUT_MS = int(SETTINGS.get("timeout_ms", 300000))
UPLOAD_TIMEOUT_MS = int(SETTINGS.get("upload_timeout_ms", 90000))
DOWNLOAD_TIMEOUT_MS = int(SETTINGS.get("download_timeout_ms", 90000))
CDP_CONNECT_TIMEOUT_MS = int(
    SETTINGS.get(
        "cdp_connect_timeout_ms",
        BASE_CHATGPT.get("cdp_connect_timeout_ms", 20000),
    )
)
CDP_CONNECT_ATTEMPTS = int(
    SETTINGS.get(
        "cdp_connect_attempts",
        BASE_CHATGPT.get("cdp_connect_attempts", 4),
    )
)
CDP_CONNECT_RETRY_SECONDS = float(
    SETTINGS.get(
        "cdp_connect_retry_seconds",
        BASE_CHATGPT.get("cdp_connect_retry_seconds", 3),
    )
)
DELETE_CHAT_AFTER_DONE = bool(SETTINGS.get("delete_chat_after_done", True))
MINIMUM_SOURCE_WIDTH = int(SETTINGS.get("minimum_source_width", 1024))
MINIMUM_SOURCE_HEIGHT = int(SETTINGS.get("minimum_source_height", 1024))
ASPECT_RATIO_TOLERANCE = float(SETTINGS.get("aspect_ratio_tolerance", 0.01))
MAX_RETRIES = int(shared.RETRY.get("max_retries", 3))
MAX_CONSECUTIVE_FAILURES = int(
    shared.RETRY.get("max_consecutive_failures", 3)
)
PACING = SETTINGS.get("pacing", {})
LOG_ROOT = shared.LOGS_DIR / "chatgpt_project"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_PACING = {
    "after_page_load": [3, 6],
    "after_mode_select": [2, 4],
    "after_upload": [5, 10],
    "before_send": [5, 10],
    "before_download": [2, 5],
    "after_download": [3, 6],
    "between_images": [45, 75],
    "before_delete": [3, 7],
    "after_sku": [45, 90],
}


@dataclass(frozen=True)
class FlowSpec:
    key: str
    title: str
    input_kind: str
    image_count: int
    status_file: Path
    output_dir: Path | None
    target_width: int
    target_height: int
    master_file: Path


def flow_spec(flow_key):
    item = FLOW_SETTINGS.get(flow_key, {})
    if flow_key == "texture":
        return FlowSpec(
            key=flow_key,
            title="Project Flow 1 - raw scan to seamless texture",
            input_kind="raw",
            image_count=1,
            status_file=shared.resolve_path(
                item.get("status_file", "status_chatgpt_project_texture.json")
            ),
            output_dir=None,
            target_width=int(item.get("target_width", 2048)),
            target_height=int(item.get("target_height", 2048)),
            master_file=TEXTURE_MASTER,
        )
    if flow_key in {"fabric", "fabric-2"}:
        default_status = (
            "status_chatgpt_project_fabric.json"
            if flow_key == "fabric"
            else "status_chatgpt_project_fabric_2.json"
        )
        default_output = (
            "output/chatgpt_project_fabric"
            if flow_key == "fabric"
            else "output/chatgpt_project_fabric_2"
        )
        return FlowSpec(
            key=flow_key,
            title=(
                "Project Flow 2 - texture to one fabric image"
                if flow_key == "fabric"
                else "Project Flow 3 - texture to two fabric images"
            ),
            input_kind="texture",
            image_count=1 if flow_key == "fabric" else 2,
            status_file=shared.resolve_path(item.get("status_file", default_status)),
            output_dir=shared.resolve_path(item.get("output_dir", default_output)),
            target_width=int(item.get("target_width", 2048)),
            target_height=int(item.get("target_height", 1536)),
            master_file=FABRIC_MASTER,
        )
    raise ValueError(f"Unsupported project flow: {flow_key}")


def validate_settings(spec):
    if not spec.master_file.exists():
        raise ValueError(f"Master document does not exist: {spec.master_file}")
    if spec.target_width < 1 or spec.target_height < 1:
        raise ValueError(f"Invalid target dimensions for {spec.key}.")
    if MINIMUM_SOURCE_WIDTH < 1 or MINIMUM_SOURCE_HEIGHT < 1:
        raise ValueError("chatgpt_project minimum source dimensions must be positive.")
    if not 0 <= ASPECT_RATIO_TOLERANCE <= 0.1:
        raise ValueError(
            "chatgpt_project.aspect_ratio_tolerance must be between 0 and 0.1."
        )
    if not DELETE_CHAT_AFTER_DONE:
        raise ValueError(
            "chatgpt_project.delete_chat_after_done must remain true so an "
            "automation chat cannot leak context into the next SKU."
        )


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_url(url):
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def project_id_from_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc.lower() != "chatgpt.com":
        return None
    match = re.search(r"/(g-p-[^/]+)(?:/|$)", parsed.path)
    return match.group(1) if match else None


def validate_project_url(url):
    value = normalized_url(str(url).strip())
    project_id = project_id_from_url(value)
    if not project_id or "/project" not in urlparse(value).path:
        raise ValueError(
            "chatgpt_project.project_url must be the ChatGPT Project page URL, "
            "for example https://chatgpt.com/g/g-p-.../project"
        )
    return value, project_id


def is_conversation_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "chatgpt.com"
        and re.search(r"/c/[0-9a-f-]{8,}(?:/|$)", parsed.path, re.IGNORECASE)
        is not None
    )


def conversation_belongs_to_project(url, project_id):
    return is_conversation_url(url) and project_id_from_url(url) == project_id


def paced_sleep(stage, label):
    delay_range = PACING.get(stage, DEFAULT_PACING[stage])
    if not isinstance(delay_range, list) or len(delay_range) != 2:
        raise ValueError(
            f"chatgpt_project.pacing.{stage} must contain [minimum, maximum]."
        )
    minimum, maximum = map(float, delay_range)
    if minimum < 0 or maximum < minimum:
        raise ValueError(f"Invalid project pacing range for {stage}: {delay_range}")
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
    if any(
        marker in lower_body
        for marker in (
            "verify you are human",
            "security check",
            "captcha",
            "challenge-platform",
        )
    ):
        return "ChatGPT human-verification challenge detected."
    
    from run_chatgpt_texture_batch import extract_chatgpt_quota_info
    is_quota, reset_info, quota_message = extract_chatgpt_quota_info(body)
    if is_quota:
        return f"CHATGPT_QUOTA_EXHAUSTED: {quota_message}"
    return None


def load_status(status_file):
    if not status_file.exists():
        return {}
    with open(status_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Status file must contain a JSON object: {status_file}")
    return data


def save_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(path.suffix + ".tmp")
    with open(temp_file, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=4, ensure_ascii=False)
    os.replace(temp_file, path)


def output_paths_for(spec, sku):
    if spec.input_kind == "raw":
        return [TEXTURES_DIR / f"texture_{sku}.png"]
    sku_dir = spec.output_dir / sku
    return [sku_dir / f"image_{number}.png" for number in range(1, spec.image_count + 1)]


def metadata_path_for(spec, sku):
    if spec.output_dir is None:
        return None
    return spec.output_dir / sku / "metadata.json"


def inspect_output(path, spec):
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
        image.load()
    if image_format != "PNG":
        raise RuntimeError(f"{path} contains {image_format}, not a real PNG file.")
    if (width, height) != (spec.target_width, spec.target_height):
        raise RuntimeError(
            f"{path} is {width}x{height}; expected "
            f"{spec.target_width}x{spec.target_height}."
        )
    return {"width": width, "height": height, "format": image_format}


def discover_files(spec, selected_sku, parser):
    if spec.input_kind == "raw":
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
        keyed = [(path.stem, path) for path in files]
    else:
        files = sorted(
            path
            for path in TEXTURES_DIR.glob("texture_*.*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        keyed = []
        for path in files:
            match = re.fullmatch(r"texture_(.+)\.[^.]+", path.name)
            if not match:
                continue
            sku = match.group(1)
            if selected_sku and sku != selected_sku:
                continue
            keyed.append((sku, path))

    duplicates = {}
    for sku, path in keyed:
        duplicates.setdefault(sku, []).append(path)
    collisions = {sku: paths for sku, paths in duplicates.items() if len(paths) > 1}
    if collisions:
        details = "; ".join(
            f"{sku}: {', '.join(path.name for path in paths)}"
            for sku, paths in collisions.items()
        )
        parser.error(f"Multiple input files map to the same SKU: {details}")
    return keyed


def select_files(spec, status_data, args, parser):
    keyed = discover_files(spec, args.sku, parser)
    if not keyed:
        source_dir = RAW_DIR if spec.input_kind == "raw" else TEXTURES_DIR
        print(f"No matching input files found in {source_dir}")
        return []

    eligible = []
    skipped_ready = 0
    skipped_exhausted = 0
    invalid_existing = []
    for sku, path in keyed:
        item = status_data.get(sku, {})
        targets = output_paths_for(spec, sku)
        if args.force:
            eligible.append((sku, path))
            continue

        valid_count = 0
        invalid_messages = []
        for target in targets:
            if not target.exists():
                continue
            try:
                inspect_output(target, spec)
                valid_count += 1
            except Exception as exc:
                invalid_messages.append(f"{target}: {exc}")

        if invalid_messages:
            invalid_existing.extend(invalid_messages)
            continue
        if valid_count == len(targets):
            skipped_ready += 1
            continue
        if int(item.get("retries", 0)) >= MAX_RETRIES:
            skipped_exhausted += 1
            continue
        eligible.append((sku, path))

    if invalid_existing:
        print("WARNING: Invalid outputs will not be overwritten without --force:")
        for message in invalid_existing:
            print(f"  {message}")

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        eligible = eligible[: args.limit]

    print(
        f"Found {len(eligible)} eligible SKU(s) for {spec.key} "
        f"(skipped {skipped_ready} ready, {skipped_exhausted} at max retries, "
        f"{len(invalid_existing)} invalid existing outputs)."
    )
    return eligible


def open_project_chat(page, project_url, project_id):
    page.goto(project_url, wait_until="domcontentloaded", timeout=60000)
    page.locator("#prompt-textarea").wait_for(state="visible", timeout=60000)
    if project_id_from_url(page.url) != project_id:
        raise RuntimeError(
            f"ChatGPT opened outside the configured Project: {page.url}"
        )
    if is_conversation_url(page.url):
        raise RuntimeError(
            "The configured project_url opened an existing conversation instead of "
            "the Project's new-chat composer. Copy the Project root URL ending in /project."
        )
    paced_sleep("after_page_load", "after opening the Project chat composer")
    stop_reason = check_safe_stop(page)
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


def upload_source_image(page, file_path):
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
    print(f"  Source image attached: {file_path.name}")
    paced_sleep("after_upload", "after attaching the source image")


def generated_sources(page):
    sources = set()
    images = page.locator('main img[alt^="Generated image:"]')
    for index in range(images.count()):
        source = images.nth(index).get_attribute("src")
        if source:
            sources.add(source)
    return sources


def wait_for_generated_image(page, previous_sources, image_number, image_count):
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
            print(
                f"  [{image_number}/{image_count}] Still generating "
                f"({elapsed}s elapsed)..."
            )
            last_progress = elapsed
        page.wait_for_timeout(1000)

    raise PlaywrightTimeoutError(
        f"No new ChatGPT generated image appeared within {TIMEOUT_MS} ms."
    )


def normalize_image(source_path, staged_png, spec):
    with Image.open(source_path) as image:
        source_width, source_height = image.size
        source_format = image.format or "UNKNOWN"
        image.load()

        target_ratio = spec.target_width / spec.target_height
        source_ratio = source_width / source_height
        ratio_error = abs(source_ratio - target_ratio) / target_ratio
        exact_size = (source_width, source_height) == (
            spec.target_width,
            spec.target_height,
        )
        if not exact_size:
            if ratio_error > ASPECT_RATIO_TOLERANCE:
                raise RuntimeError(
                    f"ChatGPT returned aspect ratio {source_width}:{source_height}; "
                    f"expected {spec.target_width}:{spec.target_height}. "
                    "Refusing to crop or stretch it."
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
        if not exact_size:
            converted = converted.resize(
                (spec.target_width, spec.target_height), Image.Resampling.LANCZOS
            )
        converted.save(staged_png, format="PNG")

    inspect_output(staged_png, spec)
    return {
        "width": spec.target_width,
        "height": spec.target_height,
        "format": "PNG",
        "source_width": source_width,
        "source_height": source_height,
        "source_download_format": source_format,
        "uniformly_resized": not exact_size,
    }


def download_and_validate(
    page, generated_image, spec, sku, image_number, target_path, download_dir, invalid_dir
):
    generated_image.scroll_into_view_if_needed()
    generated_image.click()
    close_button = page.get_by_role(
        "button", name=re.compile(r"^(Close fullscreen view|Close|Đóng.*)$")
    ).last
    close_button.wait_for(state="visible", timeout=15000)
    save_button = page.get_by_role(
        "button", name=re.compile(r"^(Save|Download|Tải xuống)$")
    ).last
    save_button.wait_for(state="visible", timeout=15000)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    base_name = f"{sku}_image_{image_number}_{stamp}_{os.getpid()}"
    downloaded_path = download_dir / f"{base_name}.download"
    staged_png = download_dir / f".{base_name}.png"
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

    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        try:
            output_info = normalize_image(downloaded_path, staged_png, spec)
        except Exception as image_error:
            invalid_path = invalid_dir / f"{base_name}.bin"
            os.replace(downloaded_path, invalid_path)
            raise RuntimeError(
                f"Downloaded ChatGPT image was rejected. Original kept at "
                f"{invalid_path}: {image_error}"
            ) from image_error
        os.replace(staged_png, target_path)
    finally:
        if downloaded_path.exists():
            downloaded_path.unlink()
        if staged_png.exists():
            staged_png.unlink()
    return output_info


def trigger_for(spec, image_number):
    if spec.input_kind == "raw":
        return (
            "TASK=TEXTURE\n\n"
            f"Apply only the Project source {spec.master_file.name} to the newly "
            "uploaded source image. Ignore 02_FABRIC_SWATCH_MASTER.md completely. "
            "Generate exactly one final seamless master texture, not a tiled preview "
            "and not an explanation."
        )
    variant_line = (
        "This is the only requested fabric image."
        if spec.image_count == 1
        else (
            f"This is independent generation {image_number} of {spec.image_count}. "
            "Use only the source image newly uploaded with this message and do not use "
            "any previously generated image as a material reference."
        )
    )
    return (
        "TASK=FABRIC_SWATCH\n\n"
        f"Apply only the Project source {spec.master_file.name} to the newly uploaded "
        "texture image. Ignore 01_TEXTURE_SEAMLESS_MASTER.md completely. "
        f"{variant_line} Generate exactly one final 4:3 landscape fabric image and "
        "do not provide an explanation."
    )


def send_generation(page, spec, source_path, image_number, target_path, download_dir, invalid_dir):
    activate_create_image_mode(page)
    previous_sources = generated_sources(page)
    upload_source_image(page, source_path)
    composer = page.locator("#prompt-textarea")
    composer.fill(trigger_for(spec, image_number))
    send_button = page.locator('[data-testid="send-button"]')
    deadline = time.monotonic() + 30
    while not send_button.is_enabled() and time.monotonic() < deadline:
        page.wait_for_timeout(500)
    if not send_button.is_enabled():
        raise RuntimeError(
            "ChatGPT Send button stayed disabled after upload and trigger entry."
        )

    paced_sleep(
        "before_send", f"before sending generation {image_number}/{spec.image_count}"
    )
    print(
        f"  [{image_number}/{spec.image_count}] Sending Project trigger and waiting..."
    )
    send_button.click()
    try:
        page.wait_for_function(
            "() => /\\/c\\/[0-9a-f-]{8,}/i.test(location.pathname)",
            timeout=30000,
        )
    except PlaywrightTimeoutError:
        pass
    generated_image = wait_for_generated_image(
        page, previous_sources, image_number, spec.image_count
    )
    paced_sleep(
        "before_download", f"before downloading image {image_number}/{spec.image_count}"
    )
    output_info = download_and_validate(
        page,
        generated_image,
        spec,
        source_path.stem,
        image_number,
        target_path,
        download_dir,
        invalid_dir,
    )
    print(
        f"  [{image_number}/{spec.image_count}] Saved "
        f"{output_info['width']}x{output_info['height']} PNG to {target_path}"
    )
    paced_sleep(
        "after_download", f"after downloading image {image_number}/{spec.image_count}"
    )
    return output_info


def delete_automation_chat(page, sku, conversation_url, project_id):
    if not conversation_belongs_to_project(conversation_url, project_id):
        raise RuntimeError(
            f"Refusing to delete chat for {sku}: it is not a conversation in the "
            "configured ChatGPT Project."
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
    paced_sleep("before_delete", f"before deleting the completed Project chat for {sku}")
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
    print(f"  Deleted ChatGPT Project automation chat for {sku}.")
    return True


class ProjectFlowRunner:
    def __init__(self, spec, project_url, project_id):
        self.spec = spec
        self.project_url = project_url
        self.project_id = project_id
        self.status_data = load_status(spec.status_file)
        self.master_sha256 = sha256_file(spec.master_file)
        self.log_dir = LOG_ROOT / spec.key
        self.download_dir = self.log_dir / "downloads"
        self.invalid_dir = self.log_dir / "invalid"
        self.requests_log = self.log_dir / "requests.jsonl"
        for directory in (self.log_dir, self.download_dir, self.invalid_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if spec.output_dir is not None:
            spec.output_dir.mkdir(parents=True, exist_ok=True)
        TEXTURES_DIR.mkdir(parents=True, exist_ok=True)

    def save_status(self):
        save_json_atomic(self.spec.status_file, self.status_data)

    def log_response(self, response):
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
            with open(self.requests_log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def record_conversation(self, page, sku, item):
        if not conversation_belongs_to_project(page.url, self.project_id):
            return None
        conversation_url = normalized_url(page.url)
        item.update(
            {
                "conversation_url": conversation_url,
                "chat_cleanup_pending": True,
            }
        )
        self.status_data[sku] = item
        self.save_status()
        return conversation_url

    def pending_cleanup_items(self):
        pending = []
        for sku, item in self.status_data.items():
            if not isinstance(item, dict):
                continue
            conversation_url = item.get("conversation_url")
            if conversation_url and not item.get("chat_deleted", False):
                pending.append((sku, conversation_url))
        return pending

    def cleanup_pending_chats(self, page):
        for sku, conversation_url in self.pending_cleanup_items():
            print(f"Cleaning pending Project chat before creating a new one: {sku}")
            item = self.status_data[sku]
            delete_automation_chat(page, sku, conversation_url, self.project_id)
            item.update(
                {
                    "chat_deleted": True,
                    "chat_cleanup_pending": False,
                    "chat_deleted_at": now_text(),
                    "error_type": None,
                }
            )
            item.pop("conversation_url", None)
            targets = output_paths_for(self.spec, sku)
            outputs = []
            for target in targets:
                try:
                    outputs.append(inspect_output(target, self.spec))
                except Exception:
                    break
            if len(outputs) == len(targets):
                item.update(
                    {
                        "status": "done",
                        "outputs": outputs,
                        "completed_at": item.get("completed_at", now_text()),
                    }
                )
            elif int(item.get("retries", 0)) >= MAX_RETRIES:
                item["status"] = "error"
            else:
                item["status"] = "pending"
            self.status_data[sku] = item
            self.save_status()

    def process_sku(self, page, sku, source_path, force=False):
        print("\n=========================================")
        print(f"Processing {self.spec.key} SKU: {sku}")
        targets = output_paths_for(self.spec, sku)
        item = self.status_data.get(
            sku, {"status": "pending", "retries": 0, "error_type": None}
        )
        if force:
            item["retries"] = 0
        elif int(item.get("retries", 0)) >= MAX_RETRIES:
            print(f"SKU {sku} reached max retries. Skipping.")
            return False

        item.update(
            {
                "status": "processing",
                "error_type": None,
                "flow": self.spec.key,
                "source_file": source_path.name,
                "source_sha256": sha256_file(source_path),
                "project_id": self.project_id,
                "master_document": self.spec.master_file.name,
                "local_master_sha256": self.master_sha256,
                "target_width": self.spec.target_width,
                "target_height": self.spec.target_height,
                "started_at": now_text(),
                "chat_deleted": False,
            }
        )
        self.status_data[sku] = item
        self.save_status()

        try:
            print("  Opening a fresh chat in the configured ChatGPT Project...")
            open_project_chat(page, self.project_url, self.project_id)
            results = {}
            for image_number, target in enumerate(targets, start=1):
                if target.exists() and not force:
                    results[str(image_number)] = inspect_output(target, self.spec)
                    print(
                        f"  [{image_number}/{self.spec.image_count}] Valid output exists; "
                        "skipping generation."
                    )
                    continue

                results[str(image_number)] = send_generation(
                    page,
                    self.spec,
                    source_path,
                    image_number,
                    target,
                    self.download_dir,
                    self.invalid_dir,
                )
                self.record_conversation(page, sku, item)
                if image_number < self.spec.image_count:
                    paced_sleep("between_images", "before generating the next fabric image")

            if self.spec.input_kind == "raw":
                package_and_report(sku, targets[0], force=force)

            conversation_url = item.get("conversation_url")
            if not conversation_url:
                conversation_url = self.record_conversation(page, sku, item)
            if not conversation_url:
                raise RuntimeError(
                    "Outputs were saved, but the Project conversation URL is missing; "
                    "refusing to continue until cleanup can be verified."
                )

            metadata = {
                "sku": sku,
                "provider": "chatgpt_web_project",
                "flow": self.spec.key,
                "project_id": self.project_id,
                "master_document": self.spec.master_file.name,
                "local_master_sha256": self.master_sha256,
                "source_file": source_path.name,
                "source_sha256": item["source_sha256"],
                "generation": {
                    "images": self.spec.image_count,
                    "target_width": self.spec.target_width,
                    "target_height": self.spec.target_height,
                },
                "outputs": results,
                "completed_at": now_text(),
            }
            metadata_path = metadata_path_for(self.spec, sku)
            if metadata_path is not None:
                save_json_atomic(metadata_path, metadata)

            item.update(
                {
                    "status": "cleanup_pending",
                    "outputs": results,
                    "completed_at": metadata["completed_at"],
                    "chat_cleanup_pending": True,
                }
            )
            self.status_data[sku] = item
            self.save_status()

            try:
                delete_automation_chat(
                    page, sku, conversation_url, self.project_id
                )
            except Exception as cleanup_error:
                item.update(
                    {
                        "status": "cleanup_pending",
                        "error_type": "chat_cleanup",
                        "chat_cleanup_pending": True,
                    }
                )
                self.status_data[sku] = item
                self.save_status()
                print(f"  [ERROR] Project chat cleanup failed: {cleanup_error}")
                return "STOP"

            item.update(
                {
                    "status": "done",
                    "error_type": None,
                    "chat_deleted": True,
                    "chat_cleanup_pending": False,
                    "chat_deleted_at": now_text(),
                }
            )
            item.pop("conversation_url", None)
            self.status_data[sku] = item
            self.save_status()
            print(f"ChatGPT Project {self.spec.key} SKU {sku} completed successfully!")
            paced_sleep("after_sku", "before moving to the next SKU")
            return True
        except Exception as exc:
            message = str(exc)
            print(f"  [ERROR] {message}")
            traceback.print_exc()
            try:
                screenshot = self.log_dir / f"error_{sku}.png"
                page.screenshot(path=str(screenshot), full_page=False)
                print(f"  Saved error screenshot: {screenshot}")
            except Exception:
                pass

            self.record_conversation(page, sku, item)
            if "SAFE_STOP_REQUIRED" in message:
                item.update({"status": "pending", "error_type": "SAFE_STOP"})
                self.status_data[sku] = item
                self.save_status()
                return "STOP"

            item["retries"] = int(item.get("retries", 0)) + 1
            item["status"] = (
                "error" if item["retries"] >= MAX_RETRIES else "pending"
            )
            item["error_type"] = (
                "timeout" if isinstance(exc, PlaywrightTimeoutError) else "unknown"
            )
            self.status_data[sku] = item
            self.save_status()
            return False


def connect_to_automation_chrome(playwright):
    if CDP_CONNECT_TIMEOUT_MS < 1000:
        raise ValueError("chatgpt_project.cdp_connect_timeout_ms must be at least 1000.")
    if CDP_CONNECT_ATTEMPTS < 1:
        raise ValueError("chatgpt_project.cdp_connect_attempts must be at least 1.")

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
        description="Run one of the three isolated ChatGPT Project image flows."
    )
    parser.add_argument(
        "--flow",
        required=True,
        choices=("texture", "fabric", "fabric-2"),
        help="texture=raw to texture, fabric=one image, fabric-2=two images",
    )
    parser.add_argument("--sku", help="Process one exact SKU")
    parser.add_argument("--limit", type=int, help="Process at most this many SKUs")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show selected SKUs without opening Chrome"
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate and replace selected outputs"
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Verify Chrome, login, Project URL, and the Project chat composer",
    )
    parser.add_argument(
        "--project-url",
        help="Override chatgpt_project.project_url from config.json",
    )
    args = parser.parse_args()

    spec = flow_spec(args.flow)
    validate_settings(spec)
    print(spec.title)
    print(f"Master document: {spec.master_file.name}")
    print(
        f"Output requirement: {spec.image_count} image(s), "
        f"{spec.target_width}x{spec.target_height} PNG"
    )

    project_url_value = str(args.project_url or PROJECT_URL_CONFIG).strip()
    project_url = None
    project_id = None
    if not args.dry_run:
        project_url, project_id = validate_project_url(project_url_value)

    status_data = load_status(spec.status_file)
    selected = []
    cleanup_count = sum(
        1
        for item in status_data.values()
        if isinstance(item, dict)
        and item.get("conversation_url")
        and not item.get("chat_deleted", False)
    )
    if not args.check_browser:
        selected = select_files(spec, status_data, args, parser)
        if not selected and cleanup_count == 0:
            print("Nothing eligible to process.")
            return

    if args.dry_run:
        if not project_url_value:
            print(
                "NOTE: config.json still needs chatgpt_project.project_url before a real run."
            )
        print(f"Selected SKUs for {spec.key}:")
        for sku, source_path in selected:
            targets = ", ".join(str(path) for path in output_paths_for(spec, sku))
            print(f"  {sku}: {source_path} -> {targets}")
        return

    if not shared.cdp_is_ready():
        shared.launch_automation_chrome()
        if not args.check_browser:
            print("Sign in to ChatGPT in the automation Chrome, then run this command again.")
            return

    print(f"Connecting to signed-in Chrome via CDP ({shared.CDP_URL})...")
    with sync_playwright() as playwright:
        try:
            browser = connect_to_automation_chrome(playwright)
        except Exception as exc:
            print(f"FATAL: {exc}")
            return
        context = browser.contexts[0]
        context.set_default_timeout(30000)
        context.set_default_navigation_timeout(60000)
        page = context.new_page()
        page.bring_to_front()
        try:
            if args.check_browser:
                open_project_chat(page, project_url, project_id)
                print(
                    f"Browser and Project check passed: {project_id}; "
                    f"{len(context.pages)} open tab(s)."
                )
                return

            runner = ProjectFlowRunner(spec, project_url, project_id)
            page.on("response", runner.log_response)
            try:
                runner.cleanup_pending_chats(page)
            except Exception as exc:
                print(f"FATAL: Could not delete a pending Project chat: {exc}")
                return

            consecutive_failures = 0
            for sku, source_path in selected:
                try:
                    runner.cleanup_pending_chats(page)
                except Exception as exc:
                    print(f"FATAL: Could not delete a pending Project chat: {exc}")
                    break

                result = runner.process_sku(
                    page, sku, source_path, force=args.force
                )
                if result == "STOP":
                    print(
                        "Batch stopped safely. Resolve login, verification, quota, or "
                        "Project chat cleanup in Chrome, then run again."
                    )
                    break
                if result is True:
                    consecutive_failures = 0
                    continue

                try:
                    runner.cleanup_pending_chats(page)
                except Exception as exc:
                    print(f"FATAL: Could not delete the failed Project chat: {exc}")
                    break
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
