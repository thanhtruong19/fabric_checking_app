import argparse
import json
import mimetypes
import re
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = PROJECT_DIR / "dashboard"
CONFIG_PATH = PROJECT_DIR / "config.json"
INSTANCE_LOCK_PATH = PROJECT_DIR / ".run_batch.lock"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
TEXTURE_PATTERN = re.compile(r"^texture_(.+)\.[^.]+$", re.IGNORECASE)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {} if default is None else default


CONFIG = load_json(CONFIG_PATH)
PATHS = CONFIG.get("paths", {})
MAX_RETRIES = int(CONFIG.get("retry", {}).get("max_retries", 3))


def resolve_project_path(value, fallback):
    path = Path(value or fallback)
    return path if path.is_absolute() else PROJECT_DIR / path


RAW_DIR = resolve_project_path(
    CONFIG.get("chatgpt_texture", {}).get("raw_dir"), "textures_raw"
)
TEXTURES_DIR = resolve_project_path(PATHS.get("textures_dir"), "textures")
OUTPUT_DIR = resolve_project_path(PATHS.get("output_dir"), "output")
CHATGPT_OUTPUT_DIR = resolve_project_path(
    CONFIG.get("chatgpt", {}).get("output_dir"), "output/chatgpt"
)

STATUS_PATHS = {
    "raw-texture": {
        "chatgpt": resolve_project_path(
            CONFIG.get("chatgpt_texture", {}).get("status_file"),
            "status_chatgpt_texture.json",
        ),
        "flow": resolve_project_path(
            CONFIG.get("flow_texture", {}).get("status_file"),
            "status_flow_texture.json",
        ),
    },
    "texture-fabrics": {
        "chatgpt": resolve_project_path(
            CONFIG.get("chatgpt", {}).get("status_file"), "status_chatgpt.json"
        ),
        "flow": resolve_project_path(PATHS.get("status_file"), "status.json"),
    },
}

PROVIDER_LABELS = {"chatgpt": "ChatGPT", "flow": "Google Flow"}


def file_mtime_text(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None


def parse_time(value):
    if not value:
        return 0.0
    for template in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, template).timestamp()
        except (TypeError, ValueError):
            continue
    return 0.0


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def image_files(directory):
    if not directory.exists():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: natural_key(path.name),
    )


def texture_files_by_sku():
    result = {}
    for path in image_files(TEXTURES_DIR):
        match = TEXTURE_PATTERN.fullmatch(path.name)
        if match:
            result.setdefault(match.group(1), path)
    return result


def raw_files_by_sku():
    return {path.stem: path for path in image_files(RAW_DIR)}


def status_state(record, output_ready=False):
    raw_state = str(record.get("status", "pending")).lower()
    error_type = str(record.get("error_type") or "")
    retries = int(record.get("retries", 0) or 0)
    if raw_state in {"processing", "cleanup_pending"}:
        return "processing"
    if output_ready or raw_state == "done":
        return "done"
    if error_type.upper() == "SAFE_STOP":
        return "blocked"
    if raw_state == "error" or retries >= MAX_RETRIES:
        return "error"
    if retries or error_type:
        return "retrying"
    return "pending"


def provider_record(provider, record, output_files):
    output_ready = len(output_files) >= 2 if output_files is not None else False
    state = status_state(record, output_ready)
    timestamps = [
        record.get("completed_at"),
        record.get("chat_deleted_at"),
        record.get("started_at"),
    ]
    updated_at = max(timestamps, key=parse_time, default=None)
    return {
        "key": provider,
        "label": PROVIDER_LABELS[provider],
        "state": state,
        "retries": int(record.get("retries", 0) or 0),
        "errorType": record.get("error_type"),
        "startedAt": record.get("started_at"),
        "completedAt": record.get("completed_at"),
        "updatedAt": updated_at,
        "outputCount": len(output_files or []),
    }


def overall_state(providers, ready):
    states = {provider["state"] for provider in providers}
    if "processing" in states:
        return "processing"
    if ready or "done" in states:
        return "done"
    if "blocked" in states:
        return "blocked"
    if "retrying" in states:
        return "retrying"
    if "error" in states:
        return "error"
    return "pending"


def newest_value(providers, key):
    values = [provider.get(key) for provider in providers if provider.get(key)]
    return max(values, key=parse_time) if values else None


def media_url(kind, *parts):
    encoded = "/".join(urllib.parse.quote(str(part), safe="") for part in parts)
    return f"/media/{kind}/{encoded}"


def raw_texture_payload():
    raw_files = raw_files_by_sku()
    textures = texture_files_by_sku()
    status_sets = {
        provider: load_json(path) for provider, path in STATUS_PATHS["raw-texture"].items()
    }
    skus = set(raw_files)
    for records in status_sets.values():
        skus.update(records)

    rows = []
    for sku in skus:
        texture = textures.get(sku)
        providers = []
        for provider, records in status_sets.items():
            record = records.get(sku, {})
            if record:
                providers.append(provider_record(provider, record, None))

        ready = texture is not None
        state = overall_state(providers, ready)
        source = raw_files.get(sku)
        retries = max((item["retries"] for item in providers), default=0)
        errors = [item["errorType"] for item in providers if item.get("errorType")]
        rows.append(
            {
                "sku": sku,
                "state": state,
                "ready": ready,
                "sourceName": source.name if source else None,
                "sourceUrl": media_url("raw", source.name) if source else None,
                "outputName": texture.name if texture else None,
                "outputUrl": media_url("texture", texture.name) if texture else None,
                "providers": providers,
                "retries": retries,
                "errorType": ", ".join(dict.fromkeys(errors)) or None,
                "startedAt": newest_value(providers, "startedAt"),
                "completedAt": newest_value(providers, "completedAt"),
                "updatedAt": newest_value(providers, "updatedAt")
                or (file_mtime_text(texture) if texture else None),
            }
        )
    return build_payload("raw-texture", rows, STATUS_PATHS["raw-texture"])


def fabric_outputs(provider, sku):
    if provider == "chatgpt":
        files = [CHATGPT_OUTPUT_DIR / sku / "image_1.png", CHATGPT_OUTPUT_DIR / sku / "image_2.png"]
        existing = [path for path in files if path.is_file()]
        if not existing and CHATGPT_OUTPUT_DIR.is_dir():
            for p in CHATGPT_OUTPUT_DIR.glob(f"*/{sku}/image_1.png"):
                if p.is_file():
                    existing.append(p)
                    break
        return existing
    root = OUTPUT_DIR / sku
    files = [root / "image_1.png", root / "image_2.png"]
    return [path for path in files if path.is_file()]


def texture_fabrics_payload():
    textures = texture_files_by_sku()
    status_sets = {
        provider: load_json(path)
        for provider, path in STATUS_PATHS["texture-fabrics"].items()
    }
    skus = set(textures)
    for records in status_sets.values():
        skus.update(records)

    rows = []
    for sku in skus:
        providers = []
        preview_urls = []
        ready = False
        for provider, records in status_sets.items():
            outputs = fabric_outputs(provider, sku)
            provider_ready = (len(outputs) >= 1 if provider == "chatgpt" else len(outputs) >= 2)
            ready = ready or provider_ready
            record = records.get(sku, {})
            if record or outputs:
                providers.append(provider_record(provider, record, outputs))
                preview_urls.extend(
                    media_url("fabric", provider, sku, path.name) for path in outputs
                )

        state = overall_state(providers, ready)
        source = textures.get(sku)
        retries = max((item["retries"] for item in providers), default=0)
        errors = [item["errorType"] for item in providers if item.get("errorType")]
        rows.append(
            {
                "sku": sku,
                "state": state,
                "ready": ready,
                "sourceName": source.name if source else None,
                "sourceUrl": media_url("texture", source.name) if source else None,
                "outputName": f"{len(preview_urls)} ảnh fabric" if preview_urls else None,
                "outputUrl": preview_urls[0] if preview_urls else None,
                "previewUrls": preview_urls,
                "providers": providers,
                "retries": retries,
                "errorType": ", ".join(dict.fromkeys(errors)) or None,
                "startedAt": newest_value(providers, "startedAt"),
                "completedAt": newest_value(providers, "completedAt"),
                "updatedAt": newest_value(providers, "updatedAt"),
            }
        )
    return build_payload("texture-fabrics", rows, STATUS_PATHS["texture-fabrics"])


def runner_active():
    if not INSTANCE_LOCK_PATH.exists():
        return False
    try:
        import msvcrt

        with open(INSTANCE_LOCK_PATH, "r+b") as handle:
            if handle.seek(0, 2) == 0:
                return False
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        return False
    return False


def build_payload(pipeline, rows, status_paths):
    priority = {
        "processing": 0,
        "blocked": 1,
        "retrying": 2,
        "error": 3,
        "pending": 4,
        "done": 5,
    }
    rows.sort(key=lambda item: (priority.get(item["state"], 9), natural_key(item["sku"])))
    counts = {state: 0 for state in priority}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    counts["attention"] = counts["blocked"] + counts["retrying"] + counts["error"]
    counts["ready"] = sum(1 for row in rows if row["ready"])
    counts["remaining"] = len(rows) - counts["ready"]
    updates = [file_mtime_text(path) for path in status_paths.values()]
    updates = [value for value in updates if value]
    return {
        "pipeline": pipeline,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "lastStatusUpdate": max(updates, key=parse_time) if updates else None,
        "runnerActive": runner_active(),
        "counts": {"total": len(rows), **counts},
        "items": rows,
    }


MEDIA_ROOTS = {
    "raw": RAW_DIR,
    "texture": TEXTURES_DIR,
    "fabric-flow": OUTPUT_DIR,
    "fabric-chatgpt": CHATGPT_OUTPUT_DIR,
}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "VEO3Status/1.0"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status/raw-texture":
            return self.send_json(raw_texture_payload())
        if path == "/api/status/texture-fabrics":
            return self.send_json(texture_fabrics_payload())
        if path.startswith("/media/"):
            return self.send_media(path)
        return self.send_static(path)

    def send_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, request_path):
        if request_path in {"", "/"}:
            request_path = "/index.html"
        relative = urllib.parse.unquote(request_path.lstrip("/"))
        candidate = (DASHBOARD_DIR / relative).resolve()
        if not self.is_within(candidate, DASHBOARD_DIR) or not candidate.is_file():
            return self.send_error(404, "Dashboard file not found")
        return self.send_file(candidate, cache_control="no-cache")

    def send_media(self, request_path):
        parts = [urllib.parse.unquote(part) for part in request_path.split("/") if part]
        if len(parts) < 3:
            return self.send_error(404, "Media file not found")
        kind = parts[1]
        if kind in {"raw", "texture"}:
            root = MEDIA_ROOTS[kind]
            relative_parts = parts[2:]
        elif kind == "fabric" and len(parts) >= 5 and parts[2] in {"flow", "chatgpt"}:
            root = MEDIA_ROOTS[f"fabric-{parts[2]}"]
            relative_parts = parts[3:]
        else:
            return self.send_error(404, "Media route not found")
        candidate = root.joinpath(*relative_parts).resolve()
        if not candidate.is_file() and kind == "fabric" and len(relative_parts) >= 2:
            sku, fname = relative_parts[0], relative_parts[1]
            matches = list(root.glob(f"*/{sku}/{fname}"))
            if matches:
                candidate = matches[0].resolve()
        if not self.is_within(candidate, root) or not candidate.is_file():
            return self.send_error(404, "Media file not found")
        return self.send_file(candidate, cache_control="private, max-age=30")

    @staticmethod
    def is_within(candidate, root):
        try:
            candidate.relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def send_file(self, path, cache_control):
        try:
            body = path.read_bytes()
        except OSError:
            return self.send_error(404, "File not found")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, template, *args):
        if self.path.startswith("/api/"):
            return
        super().log_message(template, *args)


def main():
    parser = argparse.ArgumentParser(description="Serve the local VEO3 status dashboards.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    host, port = server.server_address[:2]
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{browser_host}:{port}/"
    print(f"VEO3 status dashboard: {url}")
    print("Press Ctrl+C to stop the dashboard server.")
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
