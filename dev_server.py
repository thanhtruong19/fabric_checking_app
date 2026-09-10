"""Dependency-free development supervisor with backend restart and UI reload."""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP = ROOT / "veo3_auto_app.py"
PORT = 8765
POLL_SECONDS = 0.35
BACKEND_SUFFIXES = {".py"}
IGNORED_DIRS = {".git", "__pycache__", "build", "dist", "logs", "output"}


def backend_snapshot():
    """Capture source metadata without watching generated/runtime directories."""
    result = {}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in BACKEND_SUFFIXES:
            continue
        if path.name == Path(__file__).name or any(part in IGNORED_DIRS for part in path.parts):
            continue
        try:
            stat = path.stat()
            result[str(path)] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass
    return result


def stop_backend(process):
    if process.poll() is not None:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{PORT}/api/shutdown",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            ),
            timeout=1,
        ).read()
        process.wait(timeout=4)
        return
    except Exception:
        pass
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def start_backend(open_browser):
    environment = os.environ.copy()
    environment["VEO3_DEV_MODE"] = "1"
    environment["VEO3_DEV_SERVER_ID"] = str(time.time_ns())
    command = [sys.executable, str(APP), "--port", str(PORT)]
    if not open_browser:
        command.append("--no-browser")
    return subprocess.Popen(command, cwd=ROOT, env=environment)


def main():
    print("[DEV] Hot reload đang chạy tại http://127.0.0.1:8765/")
    print("[DEV] Sửa app.html, styles.css hoặc app.js: trình duyệt tự reload.")
    print("[DEV] Sửa file Python: backend tự restart, trình duyệt tự kết nối lại.")
    snapshot = backend_snapshot()
    process = start_backend(open_browser=True)
    try:
        while True:
            time.sleep(POLL_SECONDS)
            current = backend_snapshot()
            if current != snapshot:
                snapshot = current
                print("[DEV] Phát hiện backend thay đổi; đang restart...")
                stop_backend(process)
                process = start_backend(open_browser=False)
            elif process.poll() is not None:
                if process.returncode == 0:
                    print("[DEV] Ứng dụng đã được đóng.")
                    return
                print(f"[DEV] Backend đã dừng (code {process.returncode}); thử chạy lại...")
                time.sleep(1)
                process = start_backend(open_browser=False)
    except KeyboardInterrupt:
        print("\n[DEV] Đang dừng...")
    finally:
        stop_backend(process)


if __name__ == "__main__":
    main()
