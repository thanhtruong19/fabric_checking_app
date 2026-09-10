import argparse
from contextlib import contextmanager
import json
import msvcrt
import os
import random
import time
import re
import traceback
import subprocess
import urllib.request
from urllib.parse import urljoin
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from veo3_runtime import get_project_dir

# ==========================================
# CONFIGURATION
# ==========================================
PROJECT_DIR = get_project_dir(__file__)
CONFIG_FILE = PROJECT_DIR / "config.json"
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

PATHS = config["paths"]
DELAYS = config["delays"]
RETRY = config["retry"]
BROWSER = config["browser"]
DOWNLOAD_RESOLUTION = config.get("download", {}).get("resolution", "2K").upper()
GENERATION_ASPECT_RATIO = config.get("generation", {}).get("aspect_ratio", "4:3")

ASPECT_RATIO_API_VALUES = {
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
    "3:4": "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR",
    "4:3": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT_NINE_SIXTEEN",
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE_SIXTEEN_NINE",
}
ASPECT_RATIO_NUMERIC_VALUES = {
    ratio: int(ratio.split(":")[0]) / int(ratio.split(":")[1])
    for ratio in ASPECT_RATIO_API_VALUES
}

if DOWNLOAD_RESOLUTION not in {"1K", "2K", "4K"}:
    raise ValueError("download.resolution must be one of: 1K, 2K, 4K")
if GENERATION_ASPECT_RATIO not in ASPECT_RATIO_API_VALUES:
    raise ValueError(
        "generation.aspect_ratio must be one of: "
        + ", ".join(ASPECT_RATIO_API_VALUES)
    )


def resolve_path(path_value):
    """Resolve config paths consistently, regardless of the current directory."""
    expanded = Path(os.path.expandvars(path_value))
    return expanded if expanded.is_absolute() else PROJECT_DIR / expanded

TEXTURES_DIR = resolve_path(PATHS["textures_dir"])
OUTPUT_DIR = resolve_path(PATHS["output_dir"])
LOGS_DIR = PROJECT_DIR / "logs"

STATUS_FILE = resolve_path(PATHS["status_file"])
REQUESTS_LOG = LOGS_DIR / "requests.json"
ERRORS_LOG = LOGS_DIR / "errors.log"
CHROME_USER_DATA_DIR = resolve_path(BROWSER["user_data_dir"])
CDP_PORT = int(BROWSER.get("cdp_port", 9333))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
FLOW_HOME_URL = "https://labs.google/fx/vi/tools/flow"
INSTANCE_LOCK_FILE = PROJECT_DIR / ".run_batch.lock"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


@contextmanager
def single_instance_lock():
    """Prevent multiple batch processes from controlling the same Chrome session."""
    lock_handle = open(INSTANCE_LOCK_FILE, "a+b")
    lock_handle.seek(0, os.SEEK_END)
    if lock_handle.tell() == 0:
        lock_handle.write(b"\0")
        lock_handle.flush()

    acquired = False
    try:
        lock_handle.seek(0)
        try:
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            acquired = True
        except OSError:
            yield False
            return
        yield True
    finally:
        if acquired:
            lock_handle.seek(0)
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        lock_handle.close()

# Load Prompts
with open(resolve_path(PATHS["base_prompt"]), "r", encoding="utf-8") as f:
    BASE_PROMPT = f.read()
with open(resolve_path(PATHS["fold_contexts"]), "r", encoding="utf-8") as f:
    FOLD_CONTEXTS = json.load(f)

AVAILABLE_FOLD_CONTEXT_IDS = {
    context_id
    for context_id, context_data in FOLD_CONTEXTS.items()
    if isinstance(context_data, dict)
    and isinstance(context_data.get("prompt"), str)
    and context_data["prompt"].strip()
}
CONFIGURED_FOLD_CONTEXT_IDS = {
    context_id
    for group in config["context_weights"].values()
    for context_id in group["contexts"]
}
MISSING_FOLD_CONTEXT_IDS = sorted(
    CONFIGURED_FOLD_CONTEXT_IDS - AVAILABLE_FOLD_CONTEXT_IDS
)

# Initialize Status
if os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        status_data = json.load(f)
else:
    status_data = {}

def save_status():
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=4)

def log_error(sku, step, error_msg):
    with open(ERRORS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] SKU: {sku} | Step: {step} | Error: {error_msg}\n")

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_random_delay(delay_range):
    return random.uniform(delay_range[0], delay_range[1])

def safe_sleep(delay_range):
    delay = get_random_delay(delay_range)
    print(f"  [Sleep] Waiting {delay:.1f} seconds...")
    time.sleep(delay)

def select_contexts_for_sku(sku):
    """Deterministically select 2 unique fold contexts based on SKU seed and weights."""
    # Use the digits in SKU as seed
    seed_val = int(re.sub(r'\D', '', sku)) if re.sub(r'\D', '', sku) else hash(sku)
    rng = random.Random(seed_val)
    
    # Flatten weighted lists
    weighted_pool = []
    for group in config["context_weights"].values():
        weight = group["weight"]
        for ctx_id in group["contexts"]:
            if ctx_id not in AVAILABLE_FOLD_CONTEXT_IDS:
                continue
            # Add context id multiple times according to its group weight
            weighted_pool.extend([ctx_id] * weight)

    if len(set(weighted_pool)) < 2:
        raise ValueError(
            "At least two configured fold contexts with non-empty prompts are required."
        )
            
    ctx_1 = rng.choice(weighted_pool)
    
    # Remove all instances of ctx_1 to ensure uniqueness
    pool_for_2 = [c for c in weighted_pool if c != ctx_1]
    ctx_2 = rng.choice(pool_for_2) if pool_for_2 else ctx_1
    
    return ctx_1, ctx_2


def find_chrome_executable():
    configured_path = BROWSER.get("chrome_path")
    candidates = []
    if configured_path:
        candidates.append(Path(os.path.expandvars(configured_path)))
    candidates.extend([
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Google Chrome was not found. Set browser.chrome_path in config.json.")


def cdp_is_ready():
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def launch_automation_chrome():
    """Launch an isolated Chrome profile without touching the user's normal Chrome."""
    default_data_dir = Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
    if os.path.normcase(str(CHROME_USER_DATA_DIR.resolve())) == os.path.normcase(str(default_data_dir.resolve())):
        raise ValueError(
            "browser.user_data_dir must be a non-default directory because Chrome 136+ "
            "disables remote debugging for the normal Chrome profile."
        )

    chrome_path = find_chrome_executable()
    CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Starting isolated Chrome on port {CDP_PORT}...")
    print(f"  Automation profile: {CHROME_USER_DATA_DIR}")
    subprocess.Popen([
        str(chrome_path),
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CHROME_USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
    ])

    timeout_seconds = int(BROWSER.get("startup_timeout_seconds", 30))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if cdp_is_ready():
            return
        time.sleep(1)
    raise TimeoutError(
        f"Chrome started but CDP port {CDP_PORT} was not ready after {timeout_seconds} seconds."
    )


def restart_automation_chrome():
    """Restart only the Chrome browser process listening on the configured CDP port."""
    print(f"Restarting stale automation Chrome on port {CDP_PORT}...")
    script = (
        f"$ids=Get-NetTCPConnection -State Listen -LocalPort {CDP_PORT} "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique;"
        "foreach($id in $ids){Stop-Process -Id $id -Force -ErrorAction Stop}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        creationflags=0x08000000,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "Could not stop the stale automation Chrome process."
        )
    deadline = time.monotonic() + 10
    while cdp_is_ready() and time.monotonic() < deadline:
        time.sleep(0.25)
    launch_automation_chrome()


def ensure_flow_project(page):
    """Open an existing Flow project, or create one when the account has none."""
    if "/flow/project/" in page.url and "/edit/" not in page.url:
        page.locator('input[type="file"][accept*="image"]').first.wait_for(
            state="attached", timeout=30000
        )
        return

    if "/flow/project/" in page.url and "/edit/" in page.url:
        # The previous SKU finishes inside its editor. Uploading from that screen
        # edits the old media instead of creating a new project tile.
        project_url = page.url.split("/edit/", 1)[0]
        page.goto(
            project_url,
            wait_until="domcontentloaded",
            timeout=RETRY["timeout_ms"],
        )
        page.locator('input[type="file"][accept*="image"]').first.wait_for(
            state="attached", timeout=30000
        )
        print(f"  Returned to Flow project: {page.url}")
        return

    page.goto(FLOW_HOME_URL, wait_until="domcontentloaded", timeout=RETRY["timeout_ms"])
    stop_reason = check_safe_stop(page)
    if stop_reason:
        raise Exception(f"SAFE_STOP_REQUIRED: {stop_reason}")

    project_links = page.locator("a[href*='/flow/project/']")
    try:
        project_links.first.wait_for(state="attached", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    if project_links.count() > 0:
        project_href = project_links.first.get_attribute("href")
        page.goto(
            urljoin(page.url, project_href),
            wait_until="domcontentloaded",
            timeout=RETRY["timeout_ms"],
        )
    else:
        new_project_button = page.locator(
            'button:has-text("Dự án mới"), button:has-text("New project")'
        ).last
        new_project_button.click()
        page.wait_for_url("**/flow/project/**", timeout=RETRY["timeout_ms"])

    page.locator('input[type="file"][accept*="image"]').first.wait_for(
        state="attached", timeout=30000
    )
    print(f"  Using Flow project: {page.url}")


def upload_texture_and_open_editor(page, file_path):
    """Upload a texture, wait for its media tile, and open it as the edit reference."""
    ensure_flow_project(page)
    existing_editor_links = set(
        page.locator("a[href*='/flow/project/'][href*='/edit/']").evaluate_all(
            "elements => elements.map(element => element.getAttribute('href'))"
        )
    )

    file_input = page.locator('input[type="file"][accept*="image"]').first
    file_input.wait_for(state="attached", timeout=30000)
    with page.expect_response(
        lambda response: "/flow/uploadImage" in response.url,
        timeout=RETRY["timeout_ms"],
    ) as upload_info:
        file_input.set_input_files(str(file_path))

    upload_response = upload_info.value
    if not upload_response.ok:
        raise RuntimeError(f"Texture upload failed with HTTP {upload_response.status}.")

    editor_link = None
    deadline = time.monotonic() + (RETRY["timeout_ms"] / 1000)
    while time.monotonic() < deadline:
        editor_links = page.locator("a[href*='/flow/project/'][href*='/edit/']")
        for index in range(editor_links.count()):
            candidate = editor_links.nth(index)
            href = candidate.get_attribute("href")
            if href not in existing_editor_links:
                editor_link = candidate
                break
        if editor_link is not None:
            break
        page.wait_for_timeout(1000)

    if editor_link is None:
        raise PlaywrightTimeoutError(
            "Upload completed, but the new media tile did not appear in the project."
        )

    # Flow initializes the edit route through its SPA click handler. A direct goto
    # to the same href is redirected back to the project overview.
    editor_link.scroll_into_view_if_needed()
    editor_link.click()
    page.wait_for_url("**/flow/project/**/edit/**", timeout=30000)
    if "/edit/" not in page.url:
        raise RuntimeError("The uploaded texture tile did not open in Flow's editor.")
    page.locator('[contenteditable="true"][role="textbox"]').last.wait_for(
        state="visible", timeout=30000
    )
    print(f"  Texture uploaded and opened: {page.url}")
    return page.url


def content_image_sources(page):
    """Return non-avatar, non-placeholder image sources currently rendered by Flow."""
    return set(
        page.locator("img").evaluate_all(
            """elements => elements
                .filter(element => {
                    const alt = (element.alt || '').toLowerCase();
                    const src = element.src || '';
                    return !alt.includes('hồ sơ') &&
                           !alt.includes('profile') &&
                           !src.includes('flower-placeholder');
                })
                .map(element => element.src)"""
        )
    )

# ==========================================
# PLAYWRIGHT AUTOMATION
# ==========================================
def check_safe_stop(page):
    """Check if we hit a login page, captcha, or quota error."""
    url = page.url
    if "accounts.google.com" in url or "signin" in url:
        return "Login expired. Redirected to Google Login."
    
    # Check for visible captcha or quota errors (update text as per actual UI)
    if page.locator("text=reCAPTCHA").is_visible():
        return "Google reCAPTCHA challenge detected."
    if page.locator("text=quota exceeded").is_visible(timeout=1000):
        return "Quota exhausted detected."
    return None

def process_sku(page, sku, file_path):
    print(f"\n=========================================")
    print(f"Processing SKU: {sku}")
    
    sku_status = status_data.get(sku, {"status": "pending", "retries": 0, "error_type": None})
    if sku_status["status"] == "done":
        print(f"SKU {sku} already done. Skipping.")
        return True
        
    if sku_status["retries"] >= RETRY["max_retries"]:
        print(f"SKU {sku} reached max retries. Skipping.")
        return False
        
    sku_status["status"] = "processing"
    status_data[sku] = sku_status
    save_status()
    
    sku_out_dir = OUTPUT_DIR / sku
    os.makedirs(sku_out_dir, exist_ok=True)
    
    try:
        ctx1_id, ctx2_id = select_contexts_for_sku(sku)
        print(f"  Selected Contexts: {ctx1_id}, {ctx2_id}")

        prompt1 = BASE_PROMPT.replace(
            "{{FOLD_CONTEXT}}", FOLD_CONTEXTS[ctx1_id]["prompt"]
        )
        prompt2 = BASE_PROMPT.replace(
            "{{FOLD_CONTEXT}}", FOLD_CONTEXTS[ctx2_id]["prompt"]
        )

        # Make sure we are inside a project; the Flow home page has no upload field.
        ensure_flow_project(page)
        
        # Check Safe Stop
        stop_reason = check_safe_stop(page)
        if stop_reason:
            raise Exception(f"SAFE_STOP_REQUIRED: {stop_reason}")
            
        image_1_path = sku_out_dir / "image_1.png"
        image_2_path = sku_out_dir / "image_2.png"
        original_editor_url = None
        if not image_1_path.exists() or not image_2_path.exists():
            print("  Uploading texture...")
            original_editor_url = upload_texture_and_open_editor(page, file_path)
            safe_sleep(DELAYS["upload_wait"])
        
        # Helper to generate and download
        def generate_and_download(img_num, prompt_text):
            print(f"  [{img_num}/2] Generating...")
            prompt_editor = page.locator('[contenteditable="true"][role="textbox"]').last
            prompt_editor.wait_for(state="visible", timeout=30000)
            prompt_editor.fill(prompt_text)

            download_selector = (
                'button:has-text("Download"), button:has-text("Tải xuống"), '
                'button[aria-label*="Download"], button[aria-label*="Tải xuống"], '
                'a:has-text("Download")'
            )
            download_buttons = page.locator(download_selector)
            previous_download_count = download_buttons.count()
            previous_image_sources = content_image_sources(page)
            
            # Use the submit button in the prompt composer, not the sidebar's "Tạo" button.
            composer = prompt_editor.locator("xpath=../..")
            btn = composer.locator(
                "xpath=.//button[.//i[normalize-space()='arrow_forward']]"
            ).first
            btn.wait_for(state="visible", timeout=10000)
            if not btn.is_enabled():
                raise RuntimeError("Flow's Generate button is still disabled after entering the prompt.")
            btn.click()
            
            # Wait for a new rendered image before using the current Download button.
            print(f"  [{img_num}/2] Waiting for generation to complete...")
            deadline = time.monotonic() + (RETRY["timeout_ms"] / 1000)
            download_btn = None
            while time.monotonic() < deadline:
                stop_reason = check_safe_stop(page)
                if stop_reason:
                    raise Exception(f"SAFE_STOP_REQUIRED: {stop_reason}")

                current_count = download_buttons.count()
                current_image_sources = content_image_sources(page)
                result_changed = (
                    current_count > previous_download_count
                    or bool(current_image_sources - previous_image_sources)
                )
                if result_changed and current_count > 0:
                    candidate = download_buttons.last
                    if candidate.is_visible() and candidate.is_enabled():
                        download_btn = candidate
                        break
                page.wait_for_timeout(1000)

            if download_btn is None:
                raise PlaywrightTimeoutError(
                    f"No new Download button appeared within {RETRY['timeout_ms']} ms."
                )
            
            print(f"  [{img_num}/2] Downloading...")
            download_btn.click()
            # Flow has used both ARIA menu items and plain divs for this popover.
            # The short exact label is stable across languages and both DOM variants.
            resolution_item = page.get_by_text(
                DOWNLOAD_RESOLUTION, exact=True
            ).last
            resolution_item.wait_for(state="visible", timeout=10000)
            print(f"  [{img_num}/2] Requesting Flow {DOWNLOAD_RESOLUTION} resolution...")
            with page.expect_download(timeout=RETRY["timeout_ms"]) as download_info:
                resolution_item.click()
            
            download = download_info.value
            save_path = sku_out_dir / f"image_{img_num}.png"
            downloaded_path = download.path()
            with Image.open(downloaded_path) as downloaded_image:
                downloaded_size = downloaded_image.size
                minimum_long_edge = {"1K": 900, "2K": 1800, "4K": 3600}[
                    DOWNLOAD_RESOLUTION
                ]
                if max(downloaded_size) < minimum_long_edge:
                    raise RuntimeError(
                        f"Flow returned {downloaded_size[0]}x{downloaded_size[1]} while "
                        f"{DOWNLOAD_RESOLUTION} was requested."
                    )
                actual_ratio = downloaded_size[0] / downloaded_size[1]
                expected_ratio = ASPECT_RATIO_NUMERIC_VALUES[
                    GENERATION_ASPECT_RATIO
                ]
                relative_ratio_error = abs(actual_ratio - expected_ratio) / expected_ratio
                if relative_ratio_error > 0.03:
                    raise RuntimeError(
                        f"Flow returned aspect ratio {downloaded_size[0]}:"
                        f"{downloaded_size[1]} while {GENERATION_ASPECT_RATIO} "
                        "was requested."
                    )
                has_alpha = downloaded_image.mode in ("RGBA", "LA") or (
                    downloaded_image.mode == "P" and "transparency" in downloaded_image.info
                )
                converted_image = downloaded_image.convert("RGBA" if has_alpha else "RGB")
                converted_image.save(save_path, format="PNG")
            print(
                f"  [{img_num}/2] Saved {downloaded_size[0]}x{downloaded_size[1]} "
                f"to {save_path}"
            )
            return True

        # Generate Image 1
        if not image_1_path.exists():
            generate_and_download(1, prompt1)
        else:
            print("  Image 1 already exists. Skipping generation 1.")
            
        safe_sleep(DELAYS["after_generate"])
        
        # Generate Image 2
        if not image_2_path.exists():
            if original_editor_url is None:
                raise RuntimeError("The original texture editor URL is unavailable.")
            if page.url != original_editor_url:
                page.goto(
                    original_editor_url,
                    wait_until="domcontentloaded",
                    timeout=RETRY["timeout_ms"],
                )
                page.locator('[contenteditable="true"][role="textbox"]').last.wait_for(
                    state="visible", timeout=30000
                )
            generate_and_download(2, prompt2)
        else:
            print("  Image 2 already exists. Skipping generation 2.")

        # Save Metadata
        metadata = {
            "sku": sku,
            "generation": {
                "aspect_ratio": GENERATION_ASPECT_RATIO,
                "download_resolution": DOWNLOAD_RESOLUTION,
            },
            "image1": {
                "fold_context": ctx1_id,
                "prompt_version": "v3"
            },
            "image2": {
                "fold_context": ctx2_id,
                "prompt_version": "v3"
            }
        }
        with open(sku_out_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)
            
        sku_status["status"] = "done"
        status_data[sku] = sku_status
        save_status()
        
        print(f"SKU {sku} completed successfully!")
        safe_sleep(DELAYS["after_sku"])
        return True

    except Exception as e:
        err_msg = str(e)
        print(f"  [ERROR] {err_msg}")
        try:
            screenshot_path = LOGS_DIR / f"error_{sku}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"  Saved error screenshot: {screenshot_path}")
        except Exception:
            pass
        traceback.print_exc()
        
        # Safe Stop Check
        if "SAFE_STOP_REQUIRED" in err_msg:
            print("!!! CRITICAL STOP !!!")
            sku_status["error_type"] = "SAFE_STOP"
            status_data[sku] = sku_status
            save_status()
            log_error(sku, "CRITICAL", err_msg)
            return "STOP"
            
        sku_status["retries"] += 1
        sku_status["error_type"] = "timeout" if "Timeout" in err_msg else "unknown"
        sku_status["status"] = "error" if sku_status["retries"] >= RETRY["max_retries"] else "pending"
        status_data[sku] = sku_status
        save_status()
        log_error(sku, "Process", err_msg)
        return False

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Generate Flow images from texture files.")
    parser.add_argument("--sku", help="Process only one exact SKU, for example: 101308")
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many unfinished texture files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which SKUs would run without opening Chrome",
    )
    args = parser.parse_args()

    print("Starting Batch Processor...")
    if MISSING_FOLD_CONTEXT_IDS:
        print(
            "WARNING: Ignoring fold contexts that have no prompt definition: "
            + ", ".join(MISSING_FOLD_CONTEXT_IDS)
        )
    
    # Load SKUs
    files = sorted(TEXTURES_DIR.glob("texture_*.*"))
    if args.sku:
        files = [
            file_path
            for file_path in files
            if re.fullmatch(rf"texture_{re.escape(args.sku)}\.[^.]+", file_path.name)
        ]
    if not files:
        print(f"No matching texture files found in {TEXTURES_DIR}")
        return

    skipped_done = 0
    skipped_exhausted = 0
    eligible_files = []
    for file_path in files:
        match = re.search(r"texture_(.+)\.", file_path.name)
        if not match:
            continue
        sku = match.group(1)
        sku_status = status_data.get(sku, {})
        if sku_status.get("status") == "done":
            skipped_done += 1
            continue
        if sku_status.get("retries", 0) >= RETRY["max_retries"]:
            skipped_exhausted += 1
            continue
        eligible_files.append(file_path)

    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        eligible_files = eligible_files[:args.limit]
    files = eligible_files
    print(
        f"Found {len(files)} unfinished textures to process "
        f"(skipped {skipped_done} done, {skipped_exhausted} at max retries)."
    )

    if not files:
        print("Nothing eligible to process.")
        return

    if args.dry_run:
        print("Selected SKUs:")
        for file_path in files:
            match = re.search(r"texture_(.+)\.", file_path.name)
            if match:
                print(f"  {match.group(1)}")
        return

    if not cdp_is_ready():
        try:
            launch_automation_chrome()
        except Exception as exc:
            print(f"FATAL: Could not start automation Chrome: {exc}")
            print("Run start_chrome.bat, sign in to Google Flow once, then run run.bat.")
            return
    else:
        print(f"Reusing automation Chrome on port {CDP_PORT}.")

    with sync_playwright() as p:
        print(f"Connecting Playwright to Chrome via CDP ({CDP_URL})...")
        max_attempts = 5
        browser = None
        for attempt in range(1, max_attempts + 1):
            try:
                browser = p.chromium.connect_over_cdp(CDP_URL)
                print(f"  Connected successfully on attempt {attempt}!")
                break
            except Exception as e:
                print(f"  Attempt {attempt}/{max_attempts} failed: {e}")
                if attempt < max_attempts:
                    time.sleep(3)
        
        if not browser:
            print(f"FATAL: Could not connect to Chrome on port {CDP_PORT}.")
            print("Close only the VEO3 automation Chrome, then run start_chrome.bat again.")
            return

        if not browser.contexts:
            print("FATAL: Chrome connected but did not expose a browser context.")
            return

        context = browser.contexts[0]
        page = context.new_page()
        page.bring_to_front()

        def force_generation_aspect_ratio(route, request):
            """Force Flow's private generation request to use the configured ratio."""
            try:
                payload = json.loads(request.post_data or "{}")
                api_ratio = ASPECT_RATIO_API_VALUES[GENERATION_ASPECT_RATIO]
                generation_requests = payload.get("requests", [])
                if not generation_requests:
                    raise ValueError("generation request contains no image requests")
                for generation_request in generation_requests:
                    generation_request["imageAspectRatio"] = api_ratio
                route.continue_(
                    post_data=json.dumps(payload, separators=(",", ":"))
                )
                print(f"  Forced generation aspect ratio: {GENERATION_ASPECT_RATIO}")
            except Exception as exc:
                print(f"  [WARNING] Could not force aspect ratio: {exc}")
                route.continue_()

        page.route(
            "**/flowMedia:batchGenerateImages",
            force_generation_aspect_ratio,
        )
            
        # Network Logger Setup
        def log_request(req):
            # Basic filter to avoid logging everything
            if "flowMedia:batchGenerate" in req.url or "uploadImage" in req.url:
                try:
                    log_entry = {
                        "time": time.strftime('%Y-%m-%d %H:%M:%S'),
                        "method": req.method,
                        "url": req.url,
                        "hasPostData": req.post_data is not None
                    }
                    with open(REQUESTS_LOG, "a", encoding="utf-8") as lf:
                        lf.write(json.dumps(log_entry) + "\n")
                except:
                    pass
                    
        page.on("request", log_request)
        
        consecutive_failures = 0
        for fpath in files:
            # Extract SKU from texture_SKU.ext
            match = re.search(r'texture_(.+)\.', fpath.name)
            if match:
                sku = match.group(1)
                res = process_sku(page, sku, fpath)
                if res == "STOP":
                    print("Script paused securely due to critical error. Please resolve it and restart.")
                    break
                if res is True:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= RETRY.get("max_consecutive_failures", 3):
                        print(
                            "Batch stopped after "
                            f"{consecutive_failures} consecutive failures to avoid repeating "
                            "the same UI error for every SKU."
                        )
                        break

if __name__ == "__main__":
    with single_instance_lock() as lock_acquired:
        if not lock_acquired:
            print(
                "ERROR: Another run_batch.py instance is already running. "
                "Use the existing console instead of starting another batch."
            )
            raise SystemExit(2)
        main()
