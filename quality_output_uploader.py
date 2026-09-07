"""Attach a local fabric image to an already-open Chrome tab."""

import json
import queue
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def validate_target_url(value):
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Link trang web mục tiêu phải bắt đầu bằng http:// hoặc https://.")
    return url


def cdp_is_ready(cdp_url):
    try:
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=0.35) as response:
            return response.status == 200
    except Exception:
        return False


def _same_target(open_url, target_url):
    opened = urlparse(open_url)
    target = urlparse(target_url)
    opened_path = opened.path.rstrip("/") or "/"
    target_path = target.path.rstrip("/") or "/"
    return (
        opened.scheme.lower() == target.scheme.lower()
        and opened.netloc.lower() == target.netloc.lower()
        and (opened_path == target_path or target_path == "/")
    )


def _find_open_page(context, target_url):
    pages = [page for page in context.pages if not page.is_closed()]
    exact = next((page for page in pages if page.url == target_url), None)
    return exact or next(
        (page for page in reversed(pages) if _same_target(page.url, target_url)), None
    )


def _attach_image(context, target_url, image):
    page = _find_open_page(context, target_url)
    if page is None:
        raise RuntimeError(
            "Không tìm thấy trang web mục tiêu đang mở trong Chrome automation. "
            "Hãy mở sẵn đúng trang web trong Chrome rồi bấm lại vào ảnh."
        )

    # Inspect all enabled inputs at once. The old two-stage lookup waited 15
    # seconds before falling back when accept did not contain the word image.
    file_inputs = page.locator('input[type="file"]:not([disabled])')
    file_input = None
    for index in range(file_inputs.count()):
        candidate = file_inputs.nth(index)
        accepts = (candidate.get_attribute("accept") or "").lower()
        if not accepts or "image" in accepts or ".png" in accepts or ".jpg" in accepts:
            file_input = candidate
            break
    if file_input is None:
        raise RuntimeError("Trang web đang mở không có ô Choose File nhận ảnh.")

    file_input.set_input_files(str(image), timeout=3_000)
    uploaded_name = file_input.evaluate(
        "input => input.files && input.files[0] ? input.files[0].name : ''"
    )
    if uploaded_name != image.name:
        raise RuntimeError("Trang web chưa nhận đúng file ảnh đã chọn.")
    page.bring_to_front()
    return {"ok": True, "file_name": image.name, "target_url": page.url}


class _UploadWorker:
    """Keep Playwright and its CDP connection alive between image clicks."""

    def __init__(self, cdp_url):
        self.cdp_url = cdp_url
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, target_url, image):
        completed = threading.Event()
        job = {"target_url": target_url, "image": image, "completed": completed}
        self.jobs.put(job)
        completed.wait()
        if "error" in job:
            raise job["error"]
        return job["result"]

    def _run(self):
        playwright = None
        browser = None
        while True:
            job = self.jobs.get()
            try:
                if not cdp_is_ready(self.cdp_url):
                    raise RuntimeError(
                        "Không kết nối được Chrome automation. Hãy chạy start_chrome.bat "
                        "và mở sẵn trang web mục tiêu trong cửa sổ Chrome đó."
                    )
                if browser is None or not browser.is_connected():
                    if playwright is None:
                        from playwright.sync_api import sync_playwright

                        playwright = sync_playwright().start()
                    browser = playwright.chromium.connect_over_cdp(
                        self.cdp_url, timeout=3_000
                    )
                if not browser.contexts:
                    raise RuntimeError("Chrome automation không cung cấp browser context.")
                job["result"] = _attach_image(
                    browser.contexts[0], job["target_url"], job["image"]
                )
            except Exception as exc:
                browser = None
                job["error"] = RuntimeError(
                    f"Không thể đưa ảnh vào ô Choose File của trang web: {exc}"
                )
            finally:
                job["completed"].set()
                self.jobs.task_done()


_workers = {}
_workers_lock = threading.Lock()


def _worker_for(cdp_url):
    with _workers_lock:
        worker = _workers.get(cdp_url)
        if worker is None or not worker.thread.is_alive():
            worker = _UploadWorker(cdp_url)
            _workers[cdp_url] = worker
        return worker


def upload_image_to_target(project_dir, target_url, image_path):
    target_url = validate_target_url(target_url)
    image = Path(image_path).expanduser().resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Không tìm thấy file ảnh: {image}")

    with (Path(project_dir) / "config.json").open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    port = int(config.get("browser", {}).get("cdp_port", 9333))
    return _worker_for(f"http://127.0.0.1:{port}").submit(target_url, image)
