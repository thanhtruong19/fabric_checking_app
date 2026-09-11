import datetime
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_TITLE = "VEO3 Auto Pipeline"
PROJECT_DIR_ENV = "VEO3_PROJECT_DIR"
EMBEDDED_WORKER_ENV = "VEO3_EMBEDDED_WORKER"
EMBEDDED_WORKER_FLAG = "--embedded-worker"
EMBEDDED_GDOWN_FLAG = "--embedded-gdown"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
MAX_LOG_CHARS = 200_000
SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
QUALITY_QC_FILENAMES = {
    "qc_offset50.png",
    "qc_tile_3x3.png",
    "qc_tile_15x15.png",
}

@dataclass(frozen=True)
class FlowStep:
    key: str
    label: str
    script: str


FLOW_STEPS = (
    FlowStep("import", "Import Google Drive", "import_google_drive.py"),
    FlowStep("crop", "Crop cố định", "crop_textures.py"),
    FlowStep(
        "seamless",
        "Tạo seamless (01_TEXTURE)",
        "run_chatgpt_texture_grouped_batch.py",
    ),
    FlowStep(
        "fabric",
        "Tạo swatch vải (02_FABRIC)",
        "run_chatgpt_fabric_grouped_batch.py",
    ),
    FlowStep("package", "Đóng gói theo SKU", "package_seamless_textures.py"),
)


DEV_MODE_ENV = "VEO3_DEV_MODE"
DEV_SERVER_ID_ENV = "VEO3_DEV_SERVER_ID"


def frontend_dir():
    """Return the editable UI directory or its bundled copy in an EXE."""
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return bundle_root / "runtime_assets" / "dashboard"
    return Path(__file__).resolve().parent / "dashboard"


def frontend_path():
    return frontend_dir() / "app.html"


def load_index_html():
    """Read on every request so frontend edits need no backend restart."""
    return frontend_path().read_text(encoding="utf-8")


def dev_reload_version():
    """Change when either this backend process or an editable UI file changes."""
    server_id = os.environ.get(DEV_SERVER_ID_ENV, "production")
    if not os.environ.get(DEV_MODE_ENV):
        return server_id
    ui_dir = frontend_dir()
    newest_ui_change = max(
        (path.stat().st_mtime_ns for path in ui_dir.rglob("*") if path.is_file()),
        default=0,
    )
    return f"{server_id}:{newest_ui_change}"


def inject_dev_reload(html_text):
    if not os.environ.get(DEV_MODE_ENV):
        return html_text
    script = r"""
<script id="veo3-dev-reload">
(() => {
  let version = null;
  let backendWasUnavailable = false;
  async function checkForChanges() {
    try {
      const response = await fetch('/__dev__/version', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const current = (await response.json()).version;
      if (backendWasUnavailable || (version !== null && current !== version)) {
        location.reload();
        return;
      }
      version = current;
      backendWasUnavailable = false;
    } catch (_) {
      backendWasUnavailable = true;
    }
    setTimeout(checkForChanges, 500);
  }
  checkForChanges();
})();
</script>
"""
    return html_text.replace("</body>", script + "</body>")


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def bundled_resource_dir():
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "runtime_assets"


def copy_missing_tree(source, destination):
    """Copy bundled defaults without overwriting files edited by the user."""
    if not safe_is_dir(source):
        return
    for source_path in source.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        if destination_path.exists():
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)


def ensure_runtime_layout(project_dir):
    """Create the writable first-run files needed by a standalone EXE."""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    if not is_frozen():
        return

    resources = bundled_resource_dir()
    config_path = project_dir / "config.json"
    default_config = resources / "config.default.json"
    if not config_path.exists():
        if not default_config.is_file():
            raise FileNotFoundError(
                "Bản EXE thiếu cấu hình mặc định runtime_assets/config.default.json."
            )
        shutil.copy2(default_config, config_path)

    copy_missing_tree(resources / "prompts", project_dir / "prompts")
    for name in (
        "logs",
        "output",
        "textures",
        "textures_raw",
        "textures_cropped",
    ):
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def valid_project_dir(path):
    path = Path(path)
    if is_frozen():
        return path.is_dir() and (path / "config.json").is_file()
    required = (
        "config.json",
        "import_google_drive.py",
        "crop_textures.py",
        "run_chatgpt_texture_grouped_batch.py",
        "run_chatgpt_fabric_grouped_batch.py",
        "package_seamless_textures.py",
    )
    try:
        return path.is_dir() and all((path / name).is_file() for name in required)
    except OSError:
        return False


def safe_is_file(path):
    try:
        return Path(path).is_file()
    except OSError:
        return False


def safe_is_dir(path):
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def resolve_project_path(project_dir, value):
    """Resolve a configurable path without requiring it to exist."""
    expanded = Path(os.path.expandvars(str(value).strip()))
    return expanded if expanded.is_absolute() else Path(project_dir) / expanded


def source_settings(project_dir, config):
    """Return the source mode and folders used by the desktop UI."""
    drive = config.get("google_drive", {})
    crop = config.get("crop", {})
    app_ui = config.get("app_ui", {})
    mode = str(app_ui.get("source_mode", "drive")).strip().lower()
    if mode not in {"drive", "local"}:
        mode = "drive"

    drive_dir = resolve_project_path(
        project_dir, drive.get("destination_dir", "textures_raw")
    )
    local_value = str(app_ui.get("local_source_dir", "")).strip()
    local_dir = resolve_project_path(project_dir, local_value) if local_value else None
    active_dir = local_dir if mode == "local" and local_dir else drive_dir
    crop_dir = resolve_project_path(
        project_dir, crop.get("output_dir", "textures_cropped")
    )
    return mode, local_dir, active_dir, crop_dir


def parse_iso_or_custom_time(timestr):
    if not timestr:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(timestr, fmt)
        except ValueError:
            pass
    return None


def format_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    rem = seconds % 60
    if minutes < 60:
        return f"{minutes}m {rem:02d}s" if rem else f"{minutes}m"
    hours = minutes // 60
    rem_min = minutes % 60
    return f"{hours}h {rem_min:02d}m {rem:02d}s"


def format_file_size(bytes_count):
    if bytes_count < 1024:
        return f"{bytes_count} B"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count / (1024 * 1024):.2f} MB"


def compute_historical_timing_stats(project_dir):
    status_files = (
        Path(project_dir) / "status_chatgpt_texture_grouped.json",
        Path(project_dir) / "status_chatgpt_fabric_grouped.json",
    )
    durations = []
    for sfile in status_files:
        if safe_is_file(sfile):
            try:
                data = load_json(sfile)
                for item in data.values():
                    if isinstance(item, dict) and item.get("status") == "done":
                        start = parse_iso_or_custom_time(item.get("started_at"))
                        end = parse_iso_or_custom_time(item.get("completed_at"))
                        if start and end and end >= start:
                            sec = (end - start).total_seconds()
                            if 10 <= sec <= 600:
                                durations.append(sec)
            except Exception:
                pass

    if durations:
        avg_sec = sum(durations) / len(durations)
        return {
            "avg_duration_seconds": round(avg_sec, 1),
            "completed_count": len(durations),
            "last_duration_seconds": round(durations[-1], 1) if durations else None,
        }
    return {"avg_duration_seconds": 80.0, "completed_count": 0}


def fetch_drive_folder_title(url, timeout=6):
    """Fetch public Google Drive folder title using urllib."""
    try:
        req = urllib.request.Request(
            str(url or "").strip(),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            m = re.search(r"<title>(.*?)\s*-\s*Google Drive</title>", content, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            m_og = re.search(
                r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\'](.*?)[\"\']',
                content,
                re.IGNORECASE,
            )
            if m_og:
                return m_og.group(1).strip()
    except Exception:
        pass
    return None


def extract_base_folder_code(title):
    """Extract primary code from folder title (e.g. '1013 (Làm trước)' -> '1013')."""
    if not title:
        return ""
    cleaned = str(title).strip()
    parts = re.split(r"[\(\[\s_\-]", cleaned)
    candidate = parts[0].strip() if parts else cleaned
    return candidate or cleaned


def audit_single_drive_url(project_dir, url, target_child="all", timeout=60):
    """Audit one Google Drive folder URL against the project's output child directories."""
    url = str(url or "").strip()
    if not validate_drive_url(url):
        return {
            "url": url,
            "title": "URL không hợp lệ",
            "base_code": "",
            "error": "URL Google Drive không hợp lệ. Phải có dạng https://drive.google.com/drive/folders/...",
            "checks": [],
        }

    title = fetch_drive_folder_title(url, timeout=6)

    import import_google_drive
    try:
        entries = import_google_drive.list_public_folder(url, timeout)
    except Exception as exc:
        return {
            "url": url,
            "title": title or "Thư mục Drive",
            "base_code": extract_base_folder_code(title or ""),
            "error": f"Không thể lấy danh sách ảnh từ Drive: {exc}",
            "checks": [],
        }

    if not title and entries:
        first_parts = [entry["path"].parts[0] for entry in entries if entry["path"].parts]
        has_nested = any(len(entry["path"].parts) > 1 for entry in entries)
        common_root = (
            first_parts[0]
            if has_nested and first_parts and all(p == first_parts[0] for p in first_parts)
            else None
        )
        if common_root:
            title = common_root

    base_code = extract_base_folder_code(title or "")

    images, _, _ = import_google_drive.select_images(
        entries, SUPPORTED_IMAGE_EXTENSIONS, recursive=False
    )
    drive_skus = [Path(e["name"]).stem for e in images]

    out_base = resolve_project_path(project_dir, "output")
    available_children = []
    if safe_is_dir(out_base):
        for sub in sorted(out_base.iterdir()):
            if sub.is_dir():
                available_children.append(sub.name)

    if not available_children:
        available_children = ["chatgpt"]

    if target_child and target_child != "all":
        target_children = [c for c in available_children if c.casefold() == target_child.casefold()]
        if not target_children:
            target_children = [target_child]
    else:
        target_children = available_children

    output_checks = []
    output_name = "seamless_texture.png"
    fabric_name = "image_1.png"

    for child in target_children:
        child_dir = out_base / child
        folder_found = None
        matched_name = None

        if safe_is_dir(child_dir):
            existing_subdirs = {d.name.casefold(): d for d in child_dir.iterdir() if d.is_dir()}
            candidates = [n for n in [title, base_code] if n]
            for cand in candidates:
                if cand.casefold() in existing_subdirs:
                    folder_found = existing_subdirs[cand.casefold()]
                    matched_name = folder_found.name
                    break
            if not folder_found and base_code:
                for sname, sdir in existing_subdirs.items():
                    if sname.startswith(base_code.casefold()):
                        folder_found = sdir
                        matched_name = sdir.name
                        break

        sku_results = []
        created_count = 0
        missing_count = 0

        if folder_found and safe_is_dir(folder_found):
            for sku in drive_skus:
                sku_sub = folder_found / sku
                has_seamless = safe_is_file(sku_sub / output_name)
                has_fabric = safe_is_file(sku_sub / fabric_name)
                if has_seamless or has_fabric:
                    created_count += 1
                    status = "done"
                elif safe_is_dir(sku_sub):
                    status = "partial"
                    missing_count += 1
                else:
                    status = "missing"
                    missing_count += 1

                sku_results.append({
                    "sku": sku,
                    "status": status,
                    "has_seamless": has_seamless,
                    "has_fabric": has_fabric,
                })
        else:
            missing_count = len(drive_skus)
            for sku in drive_skus:
                sku_results.append({
                    "sku": sku,
                    "status": "missing",
                    "has_seamless": False,
                    "has_fabric": False,
                })

        total = len(drive_skus)
        percent = round(created_count * 100 / total, 1) if total > 0 else 0.0

        output_checks.append({
            "engine": child,
            "folder_exists": bool(folder_found),
            "matched_folder_name": matched_name or base_code or title or "chưa có",
            "folder_path": f"output/{child}/{matched_name or base_code or title}",
            "total": total,
            "created_count": created_count,
            "missing_count": missing_count,
            "percent": percent,
            "skus": sku_results,
        })

    return {
        "url": url,
        "title": title or base_code or "Drive Folder",
        "base_code": base_code,
        "drive_total": len(drive_skus),
        "drive_skus": drive_skus,
        "checks": output_checks,
    }


def compute_drive_folders_stats(project_dir, config, current_running_folder=None, include_unlinked=True):
    """Scan and compute real-time image progress for each Drive / base_sku folder."""
    try:
        organize_existing_folder_outputs(project_dir)
    except Exception:
        pass

    drive = config.get("google_drive", {})
    configured_urls = deduplicate_drive_items(drive.get("urls", []))
    if not configured_urls and drive.get("share_url"):
        configured_urls = [{"url": drive.get("share_url"), "folder": ""}]

    raw_root = resolve_project_path(project_dir, "textures_raw")
    crop_root = resolve_project_path(project_dir, "textures_cropped")
    tex_root = resolve_project_path(project_dir, "textures")
    out_root = resolve_project_path(project_dir, "output/chatgpt")

    sync_path = resolve_project_path(
        project_dir, drive.get("sync_status_file", "status_drive_sync.json")
    )
    sync_data = load_json(sync_path) if safe_is_file(sync_path) else {}
    sync_folders = sync_data.get("folders", {}) if isinstance(sync_data, dict) else {}

    # Collect known folder names from config
    folders_map = {}
    folder_modified_at = {}
    for item in configured_urls:
        f_name = str(item.get("folder", "")).strip()
        if f_name:
            folders_map[f_name] = str(item.get("url", "")).strip()
            folder_modified_at[f_name] = str(item.get("modified_at", "")).strip()

    # Also collect folder names from the sync snapshot if not already mapped.
    # The configured URL list is authoritative: a historical drive_url must not
    # restore a link after the user explicitly unlinks a folder.
    for f_name, f_info in sync_folders.items():
        if f_name and f_name not in folders_map and isinstance(f_info, dict):
            folders_map[f_name] = ""

    # Also discover all existing local fabric folders on disk
    if include_unlinked:
        for root_dir in (out_root, raw_root, crop_root, tex_root):
            if safe_is_dir(root_dir):
                for sub in sorted(root_dir.iterdir()):
                    if sub.is_dir() and sub.name not in folders_map:
                        folders_map[sub.name] = ""

    # Pre-map all created SKUs across output/chatgpt (supports both <base_sku>/<sku>/ and flat <sku>/)
    output_name = "seamless_texture.png"
    done_sku_keys = set()
    sku_to_out_folder = {}
    if safe_is_dir(out_root):
        for f in out_root.iterdir():
            if f.is_dir():
                if safe_is_file(f / output_name) and safe_is_file(f / "image_1.png"):
                    sku_to_out_folder[f.name] = ""
                    done_sku_keys.add(("", f.name))
                for sub in f.iterdir():
                    if sub.is_dir():
                        sname = sub.name
                        sku_to_out_folder[sname] = f.name
                        has_seamless = safe_is_file(sub / output_name)
                        has_fabric = safe_is_file(sub / "image_1.png")
                        if has_seamless and has_fabric:
                            done_sku_keys.add((f.name, sname))

    stats_list = []
    total_all_skus = 0
    total_all_created = 0
    total_all_pending = 0

    for folder_name, url in folders_map.items():
        if not folder_name:
            continue
        folder_display = folder_name
        raw_dir = raw_root / folder_name
        crop_dir = crop_root / folder_name
        tex_dir = tex_root / folder_name
        folder_out = out_root / folder_name

        skus = set()
        raw_count = 0
        cropped_count = 0
        seamless_count = 0
        fabric_count = 0

        # 1. Output folder subdirs
        if safe_is_dir(folder_out):
            for sub in folder_out.iterdir():
                if sub.is_dir():
                    sku_name = sub.name
                    skus.add(sku_name)
                    if safe_is_file(sub / output_name):
                        seamless_count += 1
                    if safe_is_file(sub / "image_1.png"):
                        fabric_count += 1

        # 2. Raw folder + top-level raw matching prefix
        if safe_is_dir(raw_dir):
            for path in raw_dir.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    skus.add(path.stem)
                    raw_count += 1
        if safe_is_dir(raw_root):
            for path in raw_root.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    if sku_to_out_folder.get(path.stem) == folder_name or path.stem.upper().startswith(folder_name.upper()):
                        skus.add(path.stem)
                        raw_count += 1

        # 3. Crop folder + top-level crop matching prefix
        if safe_is_dir(crop_dir):
            for path in crop_dir.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    skus.add(path.stem)
                    cropped_count += 1
        if safe_is_dir(crop_root):
            for path in crop_root.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    if sku_to_out_folder.get(path.stem) == folder_name or path.stem.upper().startswith(folder_name.upper()):
                        skus.add(path.stem)
                        cropped_count += 1

        # 4. Textures folder
        if safe_is_dir(tex_dir):
            for path in tex_dir.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and path.name.startswith("texture_"):
                    if seamless_count == 0:
                        seamless_count += 1
                    stem = path.stem[8:] if path.stem.startswith("texture_") else path.stem
                    skus.add(stem)

        done_skus = set()
        for s in skus:
            if (folder_name, s) in done_sku_keys:
                done_skus.add(s)

        total_skus = len(skus)
        created_count = len(done_skus)
        pending_count = max(0, total_skus - created_count)
        percent = round(created_count * 100 / total_skus) if total_skus > 0 else 0

        is_active = (current_running_folder == folder_name)

        sync_info = sync_folders.get(folder_name, {})
        drive_total_images = sync_info.get("drive_total_images")
        last_sync_at = sync_info.get("last_sync_at")
        last_imported_count = sync_info.get("last_imported_count")

        modified_candidates = []
        configured_modified = folder_modified_at.get(folder_name, "")
        if configured_modified:
            try:
                modified_candidates.append(datetime.datetime.fromisoformat(configured_modified.replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError):
                pass
        if last_sync_at:
            try:
                modified_candidates.append(datetime.datetime.fromisoformat(str(last_sync_at)).timestamp())
            except (TypeError, ValueError):
                pass
        for directory in (raw_dir, crop_dir, tex_dir, folder_out):
            if safe_is_dir(directory):
                try:
                    modified_candidates.append(directory.stat().st_mtime)
                except OSError:
                    pass
        modified_ts = max(modified_candidates, default=0.0)

        has_drive_url = bool(url)
        status_label = (
            "completed"
            if total_skus > 0 and created_count >= total_skus
            else ("in_progress" if created_count > 0 else ("not_started" if total_skus > 0 else "empty"))
        )

        stats_list.append({
            "folder": folder_name,
            "folder_display": folder_display,
            "url": url,
            "has_drive_url": has_drive_url,
            "status": status_label,
            "total": total_skus,
            "drive_total": drive_total_images,
            "last_sync_at": last_sync_at,
            "last_imported_count": last_imported_count,
            "modified_at": configured_modified,
            "modified_ts": modified_ts,
            "raw_count": raw_count,
            "cropped_count": cropped_count,
            "seamless_count": seamless_count,
            "fabric_count": fabric_count,
            "created_count": created_count,
            "pending_count": pending_count,
            "percent": percent,
            "is_active": is_active,
            "output_path": str(folder_out),
        })

        total_all_skus += total_skus
        total_all_created += created_count
        total_all_pending += pending_count

    stats_list.sort(key=lambda x: (0 if x["url"] else 1, x["folder_display"].casefold()))

    return {
        "folders": stats_list,
        "total_folders": len(stats_list),
        "linked_folders": sum(1 for s in stats_list if s.get("has_drive_url")),
        "unlinked_folders": sum(1 for s in stats_list if not s.get("has_drive_url")),
        "total_skus": total_all_skus,
        "total_created": total_all_created,
        "total_pending": total_all_pending,
        "overall_percent": round(total_all_created * 100 / total_all_skus) if total_all_skus > 0 else 0,
        "recent_sync_history": (
            sync_data.get("history", [])[-10:]
            if isinstance(sync_data, dict) and isinstance(sync_data.get("history"), list)
            else []
        ),
    }


def organize_existing_folder_outputs(project_dir):
    """Ensure any intermediate textures in textures/ are organized into their base_sku folder."""
    tex_base = resolve_project_path(project_dir, "textures")
    out_base = resolve_project_path(project_dir, "output/chatgpt")

    if not safe_is_dir(out_base) or not safe_is_dir(tex_base):
        return

    sku_to_folder = {}
    for f in out_base.iterdir():
        if f.is_dir():
            for sub in f.iterdir():
                if sub.is_dir():
                    sku_to_folder[sub.name] = f.name

    for sku, folder in sku_to_folder.items():
        root_tex = tex_base / f"texture_{sku}.png"
        folder_tex = tex_base / folder / f"texture_{sku}.png"
        if safe_is_file(root_tex):
            (tex_base / folder).mkdir(parents=True, exist_ok=True)
            if not safe_is_file(folder_tex):
                try:
                    shutil.copy2(root_tex, folder_tex)
                except Exception:
                    pass


def get_image_file(project_dir, sku, kind, folder=None):
    if not re.match(r"^[A-Za-z0-9_.\-]+$", sku):
        return None
    config = load_json(Path(project_dir) / "config.json")
    package = config.get("seamless_package", {})
    output_name = str(package.get("filename", "seamless_texture.png")).strip()

    out_base = resolve_project_path(project_dir, "output/chatgpt")
    crop_base = resolve_project_path(project_dir, "textures_cropped")
    raw_base = resolve_project_path(project_dir, "textures_raw")
    tex_base = resolve_project_path(project_dir, "textures")

    if kind == "output":
        if folder:
            target = out_base / folder / sku / output_name
            if safe_is_file(target):
                return target
            target_alt = out_base / folder / sku / "image_1.png"
            if safe_is_file(target_alt):
                return target_alt
        target = out_base / sku / output_name
        if safe_is_file(target):
            return target
        if safe_is_dir(out_base):
            for candidate in out_base.glob(f"*/{sku}/{output_name}"):
                if safe_is_file(candidate):
                    return candidate
            for candidate in out_base.glob(f"*/{sku}/image_1.png"):
                if safe_is_file(candidate):
                    return candidate
        return None

    elif kind == "final_seamless":
        if folder:
            target = out_base / folder / sku / output_name
            return target if safe_is_file(target) else None
        target = out_base / sku / output_name
        if safe_is_file(target):
            return target
        if safe_is_dir(out_base):
            matches = [candidate for candidate in out_base.glob(f"*/{sku}/{output_name}") if safe_is_file(candidate)]
            return matches[0] if len(matches) == 1 else None
        return None

    elif kind in {"seamless", "texture"}:
        if folder:
            target = tex_base / folder / f"texture_{sku}.png"
            if safe_is_file(target):
                return target
            target_out = out_base / folder / sku / output_name
            if safe_is_file(target_out):
                return target_out
        target = tex_base / f"texture_{sku}.png"
        if safe_is_file(target):
            return target
        if safe_is_dir(tex_base):
            for candidate in tex_base.glob(f"*/texture_{sku}.png"):
                if safe_is_file(candidate):
                    return candidate
        if safe_is_dir(out_base):
            for candidate in out_base.glob(f"*/{sku}/{output_name}"):
                if safe_is_file(candidate):
                    return candidate
        return None

    elif kind == "fabric":
        if folder:
            target = out_base / folder / sku / "image_1.png"
            if safe_is_file(target):
                return target
        target = out_base / sku / "image_1.png"
        if safe_is_file(target):
            return target
        if safe_is_dir(out_base):
            for candidate in out_base.glob(f"*/{sku}/image_1.png"):
                if safe_is_file(candidate):
                    return candidate
        alt_target = resolve_project_path(project_dir, "output/chatgpt_project_fabric") / sku / "image_1.png"
        if safe_is_file(alt_target):
            return alt_target
        return None

    elif kind == "cropped":
        if folder:
            target = crop_base / folder / f"{sku}.png"
            if safe_is_file(target):
                return target
        target = crop_base / f"{sku}.png"
        if safe_is_file(target):
            return target
        if safe_is_dir(crop_base):
            for candidate in crop_base.glob(f"*/{sku}.png"):
                if safe_is_file(candidate):
                    return candidate
        return None

    elif kind in {"raw", "source"}:
        if folder:
            f_raw = raw_base / folder
            if safe_is_dir(f_raw):
                for ext in SUPPORTED_IMAGE_EXTENSIONS:
                    candidate = f_raw / f"{sku}{ext}"
                    if safe_is_file(candidate):
                        return candidate
        if safe_is_dir(raw_base):
            for ext in SUPPORTED_IMAGE_EXTENSIONS:
                candidate = raw_base / f"{sku}{ext}"
                if safe_is_file(candidate):
                    return candidate
            for ext in SUPPORTED_IMAGE_EXTENSIONS:
                for candidate in raw_base.glob(f"*/{sku}{ext}"):
                    if safe_is_file(candidate):
                        return candidate
        target = crop_base / f"{sku}.png"
        if safe_is_file(target):
            return target
        return None

    return None


def get_sku_details(project_dir, sku, folder=None):
    if not re.match(r"^[A-Za-z0-9_.\-]+$", sku):
        raise ValueError("Mã SKU không hợp lệ.")

    config = load_json(Path(project_dir) / "config.json")
    output_name = str(config.get("seamless_package", {}).get("filename", "seamless_texture.png")).strip()
    out_base = resolve_project_path(project_dir, "output/chatgpt")
    detected_output_folder = str(folder or "").strip()
    if detected_output_folder:
        sku_output_dir = out_base / detected_output_folder / sku
    else:
        direct_dir = out_base / sku
        candidates = (
            sorted(path for path in out_base.glob(f"*/{sku}") if path.is_dir())
            if safe_is_dir(out_base)
            else []
        )
        if safe_is_dir(direct_dir):
            sku_output_dir = direct_dir
        elif len(candidates) == 1:
            sku_output_dir = candidates[0]
            detected_output_folder = candidates[0].parent.name
        else:
            sku_output_dir = direct_dir

    # Modal status must be based only on the two canonical final files.
    output_file = sku_output_dir / output_name
    output_file = output_file if safe_is_file(output_file) else None
    seamless_file = output_file
    fabric_file = sku_output_dir / "image_1.png"
    fabric_file = fabric_file if safe_is_file(fabric_file) else None
    cropped_file = get_image_file(project_dir, sku, "cropped", folder=folder)
    raw_file = get_image_file(project_dir, sku, "raw", folder=folder)

    # Detect folder if not explicitly provided
    detected_folder = folder or detected_output_folder
    if not detected_folder and output_file:
        try:
            parts = output_file.parts
            idx = parts.index("chatgpt") if "chatgpt" in parts else -1
            if idx != -1 and len(parts) >= idx + 3 and parts[idx + 2] == sku:
                detected_folder = parts[idx + 1]
        except Exception:
            pass
    if not detected_folder and cropped_file:
        try:
            parts = cropped_file.parts
            idx = parts.index("textures_cropped") if "textures_cropped" in parts else -1
            if idx != -1 and len(parts) >= idx + 2 and parts[idx + 1] != f"{sku}.png":
                detected_folder = parts[idx + 1]
        except Exception:
            pass

    status_file = Path(project_dir) / "status_chatgpt_texture_grouped.json"
    status_entry = None
    if safe_is_file(status_file):
        try:
            status_data = load_json(status_file)
            status_entry = status_data.get(sku)
        except Exception:
            pass

    fabric_status_file = Path(project_dir) / "status_chatgpt_fabric_grouped.json"
    fabric_status_entry = None
    if safe_is_file(fabric_status_file):
        try:
            f_data = load_json(fabric_status_file)
            fabric_status_entry = f_data.get(sku)
        except Exception:
            pass

    def file_metadata(path):
        if not path or not safe_is_file(path):
            return None
        stat = path.stat()
        meta = {
            "path": str(path.resolve()),
            "size_bytes": stat.st_size,
            "size_formatted": format_file_size(stat.st_size),
            "modified_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
            ),
            "width": None,
            "height": None,
        }
        try:
            from PIL import Image

            with Image.open(path) as img:
                meta["width"], meta["height"] = img.size
        except Exception:
            pass
        return meta

    output_meta = file_metadata(output_file)
    seamless_meta = file_metadata(seamless_file)
    fabric_meta = file_metadata(fabric_file)
    cropped_meta = file_metadata(cropped_file)
    raw_meta = file_metadata(raw_file)

    duration_text = None
    if status_entry:
        start = parse_iso_or_custom_time(status_entry.get("started_at"))
        end = parse_iso_or_custom_time(status_entry.get("completed_at"))
        if start and end and end >= start:
            sec = int((end - start).total_seconds())
            duration_text = format_duration(sec)

    fabric_duration_text = None
    if fabric_status_entry:
        start = parse_iso_or_custom_time(fabric_status_entry.get("started_at"))
        end = parse_iso_or_custom_time(fabric_status_entry.get("completed_at"))
        if start and end and end >= start:
            sec = int((end - start).total_seconds())
            fabric_duration_text = format_duration(sec)

    is_created = bool(output_meta is not None and fabric_meta is not None)
    status_label = "done" if is_created else (status_entry.get("status") if status_entry else "pending")
    quota_info = None
    if not is_created:
        if status_entry and status_entry.get("error_type") == "quota_limit":
            status_label = "quota_limit"
            quota_info = status_entry.get("quota_message") or status_entry.get("quota_reset_info")
        elif fabric_status_entry and fabric_status_entry.get("error_type") == "quota_limit":
            status_label = "quota_limit"
            quota_info = fabric_status_entry.get("quota_message") or fabric_status_entry.get("quota_reset_info")

    return {
        "sku": sku,
        "folder": detected_folder,
        "is_created": is_created,
        "status": status_label,
        "quota_info": quota_info,
        "duration_text": duration_text,
        "fabric_duration_text": fabric_duration_text,
        "output": output_meta,
        "seamless": seamless_meta,
        "fabric": fabric_meta,
        "cropped": cropped_meta,
        "raw": raw_meta,
        "status_record": status_entry,
        "fabric_status_record": fabric_status_entry,
    }


def fabric_progress(project_dir, config, folder_filter=None):
    """Return folder-aware SKU progress; complete means both seamless and swatch exist."""
    mode, local_dir, _, _ = source_settings(project_dir, config)
    output_name = str(config.get("seamless_package", {}).get("filename", "seamless_texture.png")).strip()
    raw_root = resolve_project_path(project_dir, "textures_raw")
    crop_root = resolve_project_path(project_dir, "textures_cropped")
    tex_root = resolve_project_path(project_dir, "textures")
    # Never reuse the mutable, folder-scoped output_dir written by apply_folder_config().
    out_root = resolve_project_path(project_dir, "output/chatgpt")
    is_all = not folder_filter or folder_filter in {"all", "Mặc định", ""}
    selected_folder = "" if is_all else str(folder_filter).strip().replace("/", "\\")
    sku_keys = set()

    def add_source_file(path, root, texture_prefix=False):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            return
        sku = path.stem
        if texture_prefix and sku.startswith("texture_"):
            sku = sku[8:]
        try:
            relative_parent = path.parent.relative_to(root)
            folder = "" if str(relative_parent) == "." else relative_parent.parts[0]
        except ValueError:
            folder = ""
        if not is_all and folder.casefold() != selected_folder.casefold():
            return
        sku_keys.add((folder, sku))

    if mode == "local" and local_dir:
        if safe_is_dir(local_dir):
            for path in local_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    sku_keys.add(("", path.stem))
        raw_dir = local_dir
        out_dir = out_root
    else:
        for root, texture_prefix in ((raw_root, False), (crop_root, False), (tex_root, True)):
            if safe_is_dir(root):
                for path in root.rglob("*"):
                    add_source_file(path, root, texture_prefix=texture_prefix)
        raw_dir = raw_root if is_all else raw_root / selected_folder
        out_dir = out_root if is_all else out_root / selected_folder

    if safe_is_dir(out_root):
        for first in out_root.iterdir():
            if not first.is_dir():
                continue
            if safe_is_file(first / output_name) or safe_is_file(first / "image_1.png"):
                if is_all:
                    sku_keys.add(("", first.name))
            for sku_dir in first.iterdir():
                if not sku_dir.is_dir():
                    continue
                if is_all or first.name.casefold() == selected_folder.casefold():
                    sku_keys.add((first.name, sku_dir.name))

    created = []
    pending = []
    for folder, sku in sorted(sku_keys, key=lambda value: (value[0].casefold(), value[1].casefold())):
        sku_dir = out_root / folder / sku if folder else out_root / sku
        has_seamless = safe_is_file(sku_dir / output_name)
        has_fabric = safe_is_file(sku_dir / "image_1.png")
        item = {
            "sku": sku,
            "folder": folder,
            "has_seamless": has_seamless,
            "has_fabric": has_fabric,
        }
        if has_seamless and has_fabric:
            created.append(item)
        else:
            pending.append(item)

    total = len(created) + len(pending)
    percent = round(len(created) * 100 / total) if total else 0
    return {
        "source_mode": mode,
        "local_source_dir": str(local_dir) if local_dir else "",
        "source_dir": str(raw_dir),
        "output_dir": str(out_dir),
        "folder_filter": folder_filter or "all",
        "source_exists": safe_is_dir(raw_dir),
        "total": total,
        "created_count": len(created),
        "pending_count": len(pending),
        "percent": percent,
        "created": created,
        "pending": pending,
    }


def choose_local_folder(initial_dir=None):
    """Open the native Windows common dialog in folder-selection mode."""
    initial = str(Path(initial_dir).resolve()) if initial_dir and safe_is_dir(initial_dir) else ""
    picker_script = (
        bundled_resource_dir() / "windows_folder_picker.ps1"
        if is_frozen()
        else Path(__file__).resolve().parent / "tools" / "windows" / "windows_folder_picker.ps1"
    )
    if not picker_script.is_file():
        raise FileNotFoundError(f"Thiếu file chọn thư mục: {picker_script}")
    completed = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
            "-File", str(picker_script), "-InitialDirectory", initial,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Không mở được cửa sổ chọn folder.")
    selected = completed.stdout.strip()
    return str(Path(selected).resolve()) if selected else ""


def quality_path_sort_key(path):
    """Keep each fabric code together and sort numeric suffixes naturally."""
    return tuple(
        tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
              for part in re.split(r"(\d+)", component))
        for component in Path(path).parts
    )


def list_quality_images(folder):
    """Return supported images below a user-selected quality-test folder."""
    root = Path(folder).resolve()
    if not safe_is_dir(root):
        raise ValueError("Folder vải không tồn tại hoặc không thể đọc.")
    images = []
    for path in sorted(root.rglob("*"), key=lambda item: quality_path_sort_key(item.relative_to(root))):
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            and path.name.casefold() not in QUALITY_QC_FILENAMES
        ):
            images.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "relative_path": str(path.relative_to(root)).replace("\\", "/"),
                }
            )
    return images


def list_quality_folder_groups(folder):
    """List immediate child folders and the images belonging to each child."""
    root = Path(folder).resolve()
    if not safe_is_dir(root):
        raise ValueError("Folder vải không tồn tại hoặc không thể đọc.")
    children = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda item: quality_path_sort_key(item.name),
    )
    groups = []
    for child in children:
        images = list_quality_images(child)
        groups.append({"name": child.name, "path": str(child.resolve()), "images": images})
    if children:
        images = [
            {"name": path.name, "path": str(path.resolve()), "relative_path": path.name}
            for path in sorted(root.iterdir(), key=lambda item: quality_path_sort_key(item.name))
            if (
                path.is_file()
                and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                and path.name.casefold() not in QUALITY_QC_FILENAMES
            )
        ]
        if images:
            groups.append({"name": root.name + " (ảnh trực tiếp)", "path": str(root), "images": images})
    else:
        images = list_quality_images(root)
        if images:
            groups.append({"name": root.name, "path": str(root), "images": images})
    return groups


def choose_prompt_file(initial_path=None):
    """Open the native file picker for selecting a markdown/text prompt document."""
    initial_dir = ""
    if initial_path:
        path = Path(initial_path)
        if safe_is_file(path):
            initial_dir = str(path.resolve().parent)
        elif safe_is_dir(path):
            initial_dir = str(path.resolve())
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.ShowInTaskbar=$false;"
        "$owner.TopMost=$true;"
        "$owner.StartPosition='CenterScreen';"
        "$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.Opacity=0;"
        "$owner.Show();$owner.Activate();"
        "$dialog=New-Object System.Windows.Forms.OpenFileDialog;"
        "$dialog.Title='Chọn file Master Prompt';"
        "$dialog.Filter='Markdown and Text (*.md;*.txt;*.markdown)|*.md;*.txt;*.markdown|All files (*.*)|*.*';"
        "if($args[0]){$dialog.InitialDirectory=$args[0]};"
        "try{if($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;Write-Output $dialog.FileName}}"
        "finally{$owner.Close();$owner.Dispose()}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script, initial_dir],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Không mở được cửa sổ chọn file prompt.")
    selected = completed.stdout.strip()
    return str(Path(selected).resolve()) if selected else ""


def get_default_prompt_text(project_dir, flow_key):
    """Read default master prompt content from template files."""
    flow = str(flow_key).strip().lower()
    if flow in {"seamless", "texture"}:
        rel = "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md"
    elif flow in {"fabric", "swatch"}:
        rel = "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md"
    elif flow in {"flow", "google_flow", "flow_texture"}:
        rel = "prompts/scanned_to_texture_prompt.md"
    else:
        raise ValueError(f"Không hỗ trợ luồng: {flow_key}")
    target = resolve_project_path(project_dir, rel)
    if not safe_is_file(target):
        raise ValueError(f"File prompt mẫu không tồn tại: {target}")
    return target.read_text(encoding="utf-8").strip()


def locate_project_dir():
    if is_frozen():
        executable_dir = Path(sys.executable).resolve().parent
        if (executable_dir / "config.json").is_file():
            return executable_dir
        for parent in (executable_dir, *executable_dir.parents):
            if (
                (parent / "config.json").is_file()
                and (parent / "veo3_auto_app.py").is_file()
            ):
                return parent
        return executable_dir
    else:
        candidates = (Path(__file__).resolve().parent, Path.cwd())
    for candidate in candidates:
        if valid_project_dir(candidate):
            return candidate.resolve()
    return Path(candidates[0]).resolve()


def locate_python(project_dir):
    if is_frozen():
        return Path(sys.executable).resolve()
    if not is_frozen() and Path(sys.executable).is_file():
        return Path(sys.executable).resolve()
    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "Programs" / "Python"
        if safe_is_dir(root):
            candidates.extend(
                sorted(
                    root.glob("Python3*/python.exe"),
                    key=lambda path: path.parent.name,
                    reverse=True,
                )
            )
    project_dir = Path(project_dir)
    candidates.extend(
        (
            project_dir / ".venv" / "Scripts" / "python.exe",
            project_dir / "venv" / "Scripts" / "python.exe",
        )
    )
    path_python = shutil.which("python.exe") or shutil.which("python")
    if path_python:
        candidates.append(Path(path_python))
    return next((Path(path).resolve() for path in candidates if safe_is_file(path)), None)


def build_step_command(python_exe, project_dir, step, arguments):
    if is_frozen():
        return [
            str(Path(sys.executable).resolve()),
            EMBEDDED_WORKER_FLAG,
            step.script,
            *arguments,
        ]
    return [
        str(python_exe),
        "-u",
        str(Path(project_dir) / step.script),
        *arguments,
    ]


def restore_worker_streams():
    """Reconnect stdout/stderr when a windowed EXE is launched with pipes."""
    for attribute, descriptor in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, attribute) is not None:
            continue
        try:
            stream = open(
                os.dup(descriptor),
                "w",
                encoding="utf-8",
                errors="replace",
                buffering=1,
                closefd=True,
            )
        except OSError:
            stream = open(os.devnull, "w", encoding="utf-8")
        setattr(sys, attribute, stream)


def run_embedded_worker(script_name, arguments):
    """Run one bundled pipeline module inside a child copy of this EXE."""
    restore_worker_streams()
    os.environ[EMBEDDED_WORKER_ENV] = "1"
    project_dir = Path(
        os.environ.get(PROJECT_DIR_ENV, "") or locate_project_dir()
    ).resolve()
    os.environ[PROJECT_DIR_ENV] = str(project_dir)
    ensure_runtime_layout(project_dir)
    sys.argv = [script_name, *arguments]
    script_name = Path(script_name).name

    if script_name == "import_google_drive.py":
        import import_google_drive as worker_module
    elif script_name == "crop_textures.py":
        import crop_textures as worker_module
    elif script_name == "run_chatgpt_texture_grouped_batch.py":
        import run_chatgpt_texture_grouped_batch as worker_module
    elif script_name == "run_chatgpt_fabric_grouped_batch.py":
        import run_chatgpt_fabric_grouped_batch as worker_module
    elif script_name == "run_flow_texture_batch.py":
        import run_flow_texture_batch as worker_module
    elif script_name == "package_seamless_textures.py":
        import package_seamless_textures as worker_module
    elif script_name == "run_algorithm_seamless_batch.py":
        import run_algorithm_seamless_batch as worker_module
    else:
        raise ValueError(f"Unsupported embedded worker: {script_name}")
    result = worker_module.main()
    return int(result) if isinstance(result, int) else 0


def run_embedded_gdown(arguments):
    restore_worker_streams()
    from gdown.__main__ import main as gdown_main

    sys.argv = ["gdown", *arguments]
    result = gdown_main()
    return int(result) if isinstance(result, int) else 0


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"File phải chứa JSON object: {path}")
    return value


def save_json_atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def validate_drive_url(value):
    parsed = urlparse(str(value).strip())
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "drive.google.com"
        and "/folders/" in parsed.path
    )


def drive_folder_id(value):
    """Return the stable Drive folder ID, ignoring query-string variants."""
    parsed = urlparse(str(value or "").strip())
    match = re.search(r"/folders/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def deduplicate_drive_items(items):
    """Keep the first configured item for each Drive folder ID."""
    unique = []
    seen_ids = set()
    for item in items if isinstance(items, list) else []:
        url = str(item.get("url", "")).strip() if isinstance(item, dict) else ""
        folder_id = drive_folder_id(url)
        if not url or not folder_id or folder_id in seen_ids:
            continue
        seen_ids.add(folder_id)
        unique.append(item)
    return unique


def find_chrome():
    candidates = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    )
    return next((path for path in candidates if safe_is_file(path)), None)


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


def send_telegram_message(bot_token, chat_id, text, parse_mode="HTML"):
    """Send message via Telegram Bot API using Python standard library urllib."""
    if not bot_token or not chat_id:
        return False, "Thiếu Bot Token hoặc Chat ID."
    
    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = {
        "chat_id": str(chat_id).strip(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            res = json.loads(body)
            if res.get("ok"):
                return True, None
            return False, res.get("description", "Lỗi không xác định từ Telegram")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
            return False, err_json.get("description", str(exc))
        except Exception:
            return False, f"HTTP Error {exc.code}: {exc.reason}"
    except Exception as exc:
        return False, str(exc)


class PipelineController:
    def __init__(self):
        self.project_dir = locate_project_dir()
        ensure_runtime_layout(self.project_dir)
        self.python_exe = locate_python(self.project_dir)
        self.lock = threading.RLock()
        self.log_text = ""
        self.status = "Sẵn sàng"
        self.quota_alert = None
        self.worker = None
        self.process = None
        self.stop_escalation_thread = None
        self.stop_requested = threading.Event()
        self.skipped_pipeline_folders = set()
        self.server = None
        self.last_client_at = time.monotonic()
        self.active_running_folder = None
        self.current_folder_filter = "all"
        self.quality_folders = set()

        # Session Metrics
        self.session_running = False
        self.session_started_monotonic = None
        self.session_ended_monotonic = None
        self.session_started_at_str = None
        self.session_ended_at_str = None
        self.session_start_created_count = 0

        self.telegram_polling_active = False
        self.telegram_poller_thread = None
        self.telegram_poller_stop = threading.Event()
        self.telegram_offset = 0
        self.telegram_progress_stop = threading.Event()
        self.telegram_progress_thread = None
        self.telegram_noncritical_errors = []
        self.telegram_queue_folders = []
        self.telegram_completed_folders = set()
        self.active_running_step = None
        self.active_running_sku = None
        self.start_telegram_poller()

    @property
    def config_path(self):
        return self.project_dir / "config.json"

    def append_log(self, text):
        with self.lock:
            self.log_text = (self.log_text + str(text))[-MAX_LOG_CHARS:]
            if "CẢNH BÁO QUOTA CHATGPT" in text or "CHATGPT_QUOTA_EXHAUSTED" in text:
                is_quota, reset_info, quota_msg = extract_chatgpt_quota_info(text)
                if is_quota:
                    self.quota_alert = {
                        "message": quota_msg,
                        "reset_info": reset_info,
                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    self.status = f"⚠️ Hết quota ChatGPT ({reset_info or 'Chờ reset'})"

    def set_status(self, value):
        with self.lock:
            self.status = str(value)

    def is_running(self):
        return bool(self.worker and self.worker.is_alive())

    def select_quality_folder(self):
        selected = choose_local_folder()
        if not selected:
            return {"path": "", "folders": []}
        root = Path(selected).resolve()
        folders = list_quality_folder_groups(root)
        with self.lock:
            self.quality_folders.add(root)
            failures = self.read_quality_failures()["images"]
            for group in folders:
                for image in group["images"]:
                    image["failed"] = self.quality_failure_key(Path(image["path"])) in failures
        return {"path": str(root), "folders": folders}

    @staticmethod
    def quality_data_relative(path_value):
        """Return a portable path beginning at output/ when one is present."""
        parts = str(path_value or "").replace("\\", "/").split("/")
        for index, part in enumerate(parts):
            if part.casefold() == "output":
                return "/".join(parts[index:])
        return ""

    def quality_failure_key(self, image, selected_root=None):
        """Build a machine-independent identity for one quality image."""
        image = Path(image).resolve()
        data_relative = self.quality_data_relative(image)
        if data_relative:
            return "data:" + data_relative.casefold()
        try:
            relative = image.relative_to(self.project_dir.resolve()).as_posix()
            return "project:" + relative.casefold()
        except ValueError:
            pass
        root = selected_root
        if root is None:
            roots = tuple(self.quality_folders)
            root = max(
                (candidate for candidate in roots if candidate == image or candidate in image.parents),
                key=lambda candidate: len(candidate.parts),
            )
        return "selected:" + image.relative_to(Path(root).resolve()).as_posix().casefold()

    def normalize_quality_failures(self, data):
        """Convert legacy absolute-path records into the portable version-2 schema."""
        if not isinstance(data, dict) or data.get("version") not in {1, 2} or not isinstance(data.get("images"), dict):
            raise ValueError("failed_image_ids.json không đúng định dạng.")
        normalized = {"version": 2, "images": {}}
        for old_key, record in data["images"].items():
            if not isinstance(record, dict):
                continue
            if data.get("version") == 2 and ":" in str(old_key):
                key = str(old_key).casefold()
                relative = str(record.get("relative_path", "")).replace("\\", "/")
                path_base = str(record.get("path_base", key.split(":", 1)[0]))
            else:
                data_relative = self.quality_data_relative(record.get("image_path", ""))
                if data_relative:
                    key, relative, path_base = "data:" + data_relative.casefold(), data_relative, "data"
                else:
                    relative = str(record.get("relative_path", "")).replace("\\", "/")
                    key, path_base = "selected:" + relative.casefold(), "selected"
            if not relative:
                # Keep an opaque legacy marker without retaining its machine path.
                relative = str(record.get("file_name", old_key)).replace("\\", "/").split("/")[-1]
                key, path_base = "legacy:" + relative.casefold(), "legacy"
            normalized["images"][key] = {
                "path_base": path_base,
                "relative_path": relative,
                "file_name": str(record.get("file_name", Path(relative).name)),
                "marked_at": str(record.get("marked_at", "")),
            }
        return normalized

    def read_quality_failures(self):
        path = self.project_dir / "failed_image_logs" / "failed_image_ids.json"
        if not path.exists():
            path = self.project_dir / "failed_image_ids.json"
        if not path.exists():
            return {"version": 2, "images": {}}
        # Do not overwrite unreadable or incompatible existing records.
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        return self.normalize_quality_failures(data)

    def set_quality_image_failure(self, payload):
        if not isinstance(payload.get("failed"), bool):
            raise ValueError("Trạng thái fail phải là true hoặc false.")
        image = self.resolve_quality_image(payload.get("image_path"))
        failed = payload["failed"]
        with self.lock:
            data = self.read_quality_failures()
            root = max((root for root in self.quality_folders if root in image.parents), key=lambda root: len(root.parts))
            key = self.quality_failure_key(image, root)
            if failed:
                path_base = key.split(":", 1)[0]
                if path_base == "data":
                    relative_path = self.quality_data_relative(image)
                elif path_base == "project":
                    relative_path = image.relative_to(self.project_dir.resolve()).as_posix()
                else:
                    relative_path = image.relative_to(root.resolve()).as_posix()
                data["images"][key] = {
                    "path_base": path_base,
                    "relative_path": relative_path,
                    "file_name": image.name,
                    "marked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            else:
                data["images"].pop(key, None)
            log_dir = self.project_dir / "failed_image_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            save_json_atomic(log_dir / "failed_image_ids.json", data)
        return {"image_path": str(image), "failed": failed}

    def prepare_quality_rerun(self, payload):
        folder = Path(str(payload.get("folder", ""))).resolve()
        paths = payload.get("image_paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError("Hãy chọn ít nhất một ảnh fail.")
        failures = self.read_quality_failures()["images"]
        jobs = {}
        for value in paths:
            image = self.resolve_quality_image(value)
            if folder not in image.parents or self.quality_failure_key(image) not in failures:
                raise ValueError("Chỉ được chọn ảnh fail trong folder đang xem.")
            data_root = next((parent for parent in image.parents if (parent / "textures_raw").is_dir() and (parent / "output") in image.parents), None)
            if data_root is None:
                raise ValueError(f"Không tìm thấy textures_raw tương ứng với {image}. Cần bộ thư mục nguồn và output cùng gốc.")
            relative = image.relative_to(data_root / "output")
            if len(relative.parts) != 4:
                raise ValueError(f"Không xác định được nhóm/SKU từ {image}; cần output/<engine>/<nhóm>/<SKU>/<ảnh>.")
            engine, group, sku, _ = relative.parts
            raw_dir = data_root / "textures_raw" / group
            if not any(path.is_file() and path.stem == sku and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS for path in raw_dir.glob("*")):
                raise ValueError(f"Không có ảnh nguồn cho SKU {sku} trong {raw_dir}.")
            key = str(image.parent)
            jobs[key] = {"folder": group, "sku": sku, "data_root": data_root,
                         "output_dir": self.project_dir.resolve() / "output" / "chatgpt" / group}
        return list(jobs.values())

    def rerun_quality_images(self, payload):
        with self.lock:
            if self.is_running():
                raise ValueError("Một tiến trình đang chạy. Hãy chờ hoàn tất trước khi tạo lại ảnh.")
            jobs = self.prepare_quality_rerun(payload)
            first_job = jobs[0]
            if any(
                job["folder"] != first_job["folder"] or job["data_root"] != first_job["data_root"]
                for job in jobs[1:]
            ):
                raise ValueError("Các ảnh tạo lại phải thuộc cùng một nhóm dữ liệu để dùng chung một chat.")
            run_payload = {"engine": "chatgpt", "source_mode": "local", "force": True,
                           "local_source_dir": str(first_job["data_root"] / "textures_raw" / first_job["folder"]),
                           "images_per_chat": len(jobs), "limit": str(len(jobs)), "auto_retry_enabled": False,
                           "flows": {"import": False, "crop": True, "seamless": True, "fabric": False, "package": True}}
            self.validate_run(run_payload)
            config = load_json(self.config_path)
            run_dir = self.project_dir / "failed_image_logs" / datetime.datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
            runtime = run_dir / "0"
            runtime.mkdir(parents=True)
            if (self.project_dir / "prompts").is_dir():
                shutil.copytree(self.project_dir / "prompts", runtime / "prompts")
            settings = json.loads(json.dumps(config))
            for section in settings.values():
                if isinstance(section, dict):
                    for name in ("master_prompt_file", "prompt_file", "user_data_dir"):
                        if section.get(name):
                            section[name] = str(resolve_project_path(self.project_dir, os.path.expandvars(section[name])))
            root, group = first_job["data_root"], first_job["folder"]
            raw, cropped = str(root / "textures_raw" / group), str(root / "textures_cropped" / group)
            textures, output = str(root / "textures" / group), str(first_job["output_dir"])
            for section, values in {
                "crop": {"source_dir": raw, "output_dir": cropped},
                "chatgpt_texture_grouped": {"raw_dir": cropped, "textures_dir": textures},
                "chatgpt_texture": {"raw_dir": cropped},
                "paths": {"textures_dir": textures, "output_dir": output},
                "chatgpt": {"output_dir": output},
                "seamless_package": {"output_dir": output},
            }.items():
                settings.setdefault(section, {}).update(values)
            save_json_atomic(runtime / "config.json", settings)
            sku_file = runtime / "selected_skus.json"
            save_json_atomic(sku_file, [job["sku"] for job in jobs])
            run_payload["sku_file"] = str(sku_file)
            queue = [{"folder": group, "runtime_dir": str(runtime)}]
            self.stop_requested.clear()
            self.quota_alert = None
            self.log_text = ""
            self.append_log(f"[Quality] Tạo lại {len(queue)} SKU từ các ảnh fail đã chọn.\n")
            for job in jobs:
                self.append_log(f"[Quality] Output {job['sku']}: {job['output_dir'] / job['sku']}\n")
            self.worker = threading.Thread(
                target=self.run_quality_queue,
                args=(queue, run_payload, run_dir),
                daemon=True,
            )
            self.worker.start()
        return {"ok": True, "sku_count": len(jobs)}

    def run_quality_queue(self, queue, payload, run_dir):
        """Run failed-image jobs and always remove their isolated runtime data."""
        try:
            self.run_queue(queue, payload)
        finally:
            runtime = Path(run_dir).resolve()
            log_root = (self.project_dir / "failed_image_logs").resolve()
            is_quality_runtime = (
                runtime.parent == log_root
                and re.fullmatch(r"run_\d{8}_\d{6}_\d{6}", runtime.name) is not None
            )
            if not is_quality_runtime:
                self.append_log(
                    f"[WARNING] Không dọn runtime Quality ngoài phạm vi an toàn: {runtime}\n"
                )
            else:
                try:
                    shutil.rmtree(runtime)
                    self.append_log(f"[Quality] Đã dọn dữ liệu chạy tạm: {runtime.name}\n")
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.append_log(
                        f"[WARNING] Không thể dọn dữ liệu chạy tạm {runtime.name}: {exc}\n"
                    )

    def resolve_quality_image(self, image_path):
        candidate = Path(str(image_path or "")).resolve()
        with self.lock:
            roots = tuple(self.quality_folders)
        if not any(candidate == root or root in candidate.parents for root in roots):
            raise ValueError("Ảnh không thuộc folder vải đã chọn trong phiên hiện tại.")
        if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError("File ảnh không tồn tại hoặc không được hỗ trợ.")
        return candidate

    def upload_quality_image(self, payload):
        target_url = str(payload.get("target_url", "")).strip()
        image = self.resolve_quality_image(payload.get("image_path"))
        from quality_output_uploader import upload_image_to_target

        self.append_log(f"[Quality] Đang gửi {image.name} tới {target_url}\n")
        result = upload_image_to_target(self.project_dir, target_url, image)
        self.append_log(f"[Quality] Đã đưa {image.name} vào Choose File: {result['target_url']}\n")
        return result

    def state(self, folder_filter=None):
        self.last_client_at = time.monotonic()
        drive_url = ""
        drive_urls = []
        images_per_chat = 10
        texture_prompt_mode = "attachment"
        texture_prompt_file = "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md"
        texture_prompt_text = ""
        fabric_prompt_mode = "attachment"
        fabric_prompt_file = "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md"
        fabric_prompt_text = ""
        flow_prompt_mode = "attachment"
        flow_prompt_file = "prompts/scanned_to_texture_prompt.md"
        flow_prompt_text = ""
        try:
            config = load_json(self.config_path)
            drive_url = str(config.get("google_drive", {}).get("share_url", ""))
            drive_urls = deduplicate_drive_items(config.get("google_drive", {}).get("urls", []))
            images_per_chat = int(
                config.get("chatgpt_texture_grouped", {}).get("images_per_chat", 10)
            )
            app_ui = config.get("app_ui", {})
            tex_cfg = config.get("chatgpt_texture_grouped", {})
            fab_cfg = config.get("chatgpt_fabric_grouped", {})
            flow_cfg = config.get("flow_texture", {})
            texture_prompt_mode = str(tex_cfg.get("prompt_mode", "attachment")).lower()
            texture_prompt_file = str(tex_cfg.get("master_prompt_file", "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md"))
            texture_prompt_text = str(tex_cfg.get("prompt_text", ""))
            fabric_prompt_mode = str(fab_cfg.get("prompt_mode", "attachment")).lower()
            fabric_prompt_file = str(fab_cfg.get("master_prompt_file", "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md"))
            fabric_prompt_text = str(fab_cfg.get("prompt_text", ""))
            flow_prompt_mode = str(flow_cfg.get("prompt_mode", "attachment")).lower()
            flow_prompt_file = str(flow_cfg.get("prompt_file", "prompts/scanned_to_texture_prompt.md"))
            flow_prompt_text = str(flow_cfg.get("prompt_text", ""))
        except Exception as exc:
            self.append_log(f"[ERROR] Không đọc được config.json: {exc}\n")
            app_ui = {}

        if folder_filter is not None:
            self.current_folder_filter = folder_filter

        progress = fabric_progress(self.project_dir, config, folder_filter=self.current_folder_filter)
        folders_stats = compute_drive_folders_stats(self.project_dir, config, self.active_running_folder)

        # Timing calculations
        historical_stats = compute_historical_timing_stats(self.project_dir)
        historical_avg = historical_stats.get("avg_duration_seconds", 80.0)

        session_duration = 0.0
        if self.session_started_monotonic is not None:
            if self.session_running:
                session_duration = time.monotonic() - self.session_started_monotonic
            elif self.session_ended_monotonic is not None:
                session_duration = (
                    self.session_ended_monotonic - self.session_started_monotonic
                )

        current_created = progress.get("created_count", 0)
        session_created_count = max(
            0, current_created - self.session_start_created_count
        )
        session_avg = (
            (session_duration / session_created_count)
            if session_created_count > 0
            else 0.0
        )

        effective_avg = (
            session_avg
            if (session_created_count >= 2 and session_avg >= 15)
            else historical_avg
        )
        pending_count = progress.get("pending_count", 0)
        eta_seconds = (
            round(pending_count * effective_avg) if pending_count > 0 else 0
        )

        timing_stats = {
            "session_running": self.session_running,
            "session_duration_seconds": round(session_duration, 1),
            "session_started_at": self.session_started_at_str,
            "session_created_count": session_created_count,
            "session_avg_seconds": round(session_avg, 1),
            "historical_avg_seconds": round(historical_avg, 1),
            "effective_avg_seconds": round(effective_avg, 1),
            "eta_seconds": eta_seconds,
        }

        quota_alert = self.quota_alert
        if not quota_alert:
            for sfile in (
                self.project_dir / "status_chatgpt_texture_grouped.json",
                self.project_dir / "status_chatgpt_fabric_grouped.json",
            ):
                if safe_is_file(sfile):
                    try:
                        sdata = load_json(sfile)
                        for v in sdata.values():
                            if isinstance(v, dict) and v.get("error_type") == "quota_limit":
                                q_msg = v.get("quota_message") or "Đã hết hạn mức tạo ảnh ChatGPT."
                                q_reset = v.get("quota_reset_info")
                                quota_alert = {
                                    "message": q_msg,
                                    "reset_info": q_reset,
                                }
                                break
                        if quota_alert:
                            break
                    except Exception:
                        pass

        tele_cfg = config.get("telegram", {})
        telegram_state = {
            "enabled": bool(tele_cfg.get("enabled", False)),
            "bot_token": str(tele_cfg.get("bot_token", "")),
            "chat_id": str(tele_cfg.get("chat_id", "")),
            "notify_on_complete": bool(tele_cfg.get("notify_on_complete", True)),
            "notify_on_start": bool(tele_cfg.get("notify_on_start", True)),
            "notify_periodic_progress": bool(tele_cfg.get("notify_periodic_progress", True)),
            "notify_on_failure": bool(tele_cfg.get("notify_on_failure", True)),
        }
        hidden_drive_folders = config.get("google_drive", {}).get("hidden_folders", [])
        if not isinstance(hidden_drive_folders, list):
            hidden_drive_folders = []

        out_root = resolve_project_path(self.project_dir, "output")
        output_children = [d.name for d in out_root.iterdir() if d.is_dir()] if safe_is_dir(out_root) else ["chatgpt"]

        with self.lock:
            return {
                "project_dir": str(self.project_dir),
                "python_exe": str(self.python_exe) if self.python_exe else None,
                "drive_url": drive_url,
                "drive_urls": drive_urls,
                "hidden_drive_folders": [
                    folder for folder in hidden_drive_folders if isinstance(folder, str)
                ],
                "drive_folders_stats": folders_stats,
                "drive_sync_recent": folders_stats.get("recent_sync_history", []),
                "output_subdirectories": output_children,
                "active_running_folder": self.active_running_folder,
                "active_pipeline_folders": [
                    folder for folder in self.telegram_queue_folders
                    if folder not in self.telegram_completed_folders
                    and folder.casefold() not in self.skipped_pipeline_folders
                ],
                "images_per_chat": images_per_chat,
                "auto_retry_enabled": bool(app_ui.get("auto_retry_enabled", True)),
                "auto_retry_delay_seconds": int(app_ui.get("auto_retry_delay_seconds", 120)),
                "auto_retry_max_attempts": int(app_ui.get("auto_retry_max_attempts", 10)),
                "texture_prompt_mode": texture_prompt_mode,
                "texture_prompt_file": texture_prompt_file,
                "texture_prompt_text": texture_prompt_text,
                "fabric_prompt_mode": fabric_prompt_mode,
                "fabric_prompt_file": fabric_prompt_file,
                "fabric_prompt_text": fabric_prompt_text,
                "flow_prompt_mode": flow_prompt_mode,
                "flow_prompt_file": flow_prompt_file,
                "flow_prompt_text": flow_prompt_text,
                "source_mode": progress["source_mode"],
                "local_source_dir": progress["local_source_dir"],
                "fabric_progress": progress,
                "timing_stats": timing_stats,
                "quota_alert": quota_alert,
                "telegram": telegram_state,
                "status": self.status,
                "running": self.is_running(),
                "log": self.log_text,
            }

    def save_settings(self, payload):
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        if source_mode not in {"drive", "local"}:
            raise ValueError("Nguồn ảnh phải là Google Drive hoặc thư mục local.")
        
        drive_urls = payload.get("drive_urls", [])
        if not isinstance(drive_urls, list):
            drive_urls = []

        existing_config = load_json(self.config_path)
        existing_modified = {
            drive_folder_id(item.get("url", "")): str(item.get("modified_at", "")).strip()
            for item in existing_config.get("google_drive", {}).get("urls", [])
            if isinstance(item, dict) and drive_folder_id(item.get("url", ""))
        }
        
        valid_urls = []
        for item in drive_urls:
            url = str(item.get("url", "")).strip()
            if url:
                if not validate_drive_url(url):
                    raise ValueError("Tất cả link Drive phải có dạng https://drive.google.com/drive/folders/...")
                folder_name = str(item.get("folder", "")).strip().replace("/", "\\")
                modified_at = str(item.get("modified_at", "")).strip()
                if not modified_at:
                    modified_at = existing_modified.get(drive_folder_id(url), "")
                valid_item = {"url": url, "folder": folder_name}
                if modified_at:
                    valid_item["modified_at"] = modified_at
                valid_urls.append(valid_item)
        duplicate_count = len(valid_urls) - len(deduplicate_drive_items(valid_urls))
        valid_urls = deduplicate_drive_items(valid_urls)
        if duplicate_count:
            self.append_log(
                f"[Drive] Đã bỏ qua {duplicate_count} link trùng Folder ID; mỗi thư mục chỉ được lưu một lần.\n"
            )
        
        drive_url = valid_urls[0]["url"] if valid_urls else ""
        local_value = str(payload.get("local_source_dir", "")).strip()
        local_dir = None
        if source_mode == "local":
            if not local_value:
                raise ValueError("Hãy chọn thư mục ảnh vải local.")
            local_dir = resolve_project_path(self.project_dir, local_value).resolve()
            if not safe_is_dir(local_dir):
                raise ValueError(f"Thư mục local không tồn tại: {local_dir}")
        images_per_chat = int(payload.get("images_per_chat", 10))
        if not 1 <= images_per_chat <= 20:
            raise ValueError("Ảnh mỗi chat phải nằm trong khoảng 1..20.")

        auto_retry_enabled = bool(payload.get("auto_retry_enabled", True))
        auto_retry_delay = int(payload.get("auto_retry_delay_seconds", 120))
        auto_retry_max = int(payload.get("auto_retry_max_attempts", 10))
        if auto_retry_delay < 5:
            auto_retry_delay = 5
        if auto_retry_max < 0:
            auto_retry_max = 0

        config = load_json(self.config_path)
        drive = config.setdefault("google_drive", {})
        drive["share_url"] = drive_url
        drive["urls"] = valid_urls
        drive["enabled"] = bool(drive_url)
        app_ui = config.setdefault("app_ui", {})
        app_ui["source_mode"] = source_mode
        app_ui["auto_retry_enabled"] = auto_retry_enabled
        app_ui["auto_retry_delay_seconds"] = auto_retry_delay
        app_ui["auto_retry_max_attempts"] = auto_retry_max
        if local_dir:
            app_ui["local_source_dir"] = str(local_dir)
        elif local_value:
            app_ui["local_source_dir"] = local_value

        crop = config.setdefault("crop", {})
        crop["source_dir"] = (
            str(local_dir)
            if source_mode == "local"
            else str(drive.get("destination_dir", "textures_raw"))
        )

        tex_cfg = config.setdefault("chatgpt_texture_grouped", {})
        tex_cfg["images_per_chat"] = images_per_chat
        tex_mode = str(payload.get("texture_prompt_mode", "attachment")).strip().lower()
        if tex_mode in {"attachment", "manual"}:
            tex_cfg["prompt_mode"] = tex_mode
        if "texture_prompt_file" in payload:
            tex_cfg["master_prompt_file"] = str(payload["texture_prompt_file"]).strip()
        if "texture_prompt_text" in payload:
            tex_cfg["prompt_text"] = str(payload["texture_prompt_text"])

        fab_cfg = config.setdefault("chatgpt_fabric_grouped", {})
        fab_cfg["images_per_chat"] = images_per_chat
        fab_cfg["input_mode"] = "seamless"
        fab_cfg["raw_dir"] = str(
            config.get("paths", {}).get("output_dir", config.get("chatgpt", {}).get("output_dir", "output/chatgpt"))
        )
        fab_mode = str(payload.get("fabric_prompt_mode", "attachment")).strip().lower()
        if fab_mode in {"attachment", "manual"}:
            fab_cfg["prompt_mode"] = fab_mode
        if "fabric_prompt_file" in payload:
            fab_cfg["master_prompt_file"] = str(payload["fabric_prompt_file"]).strip()
        if "fabric_prompt_text" in payload:
            fab_cfg["prompt_text"] = str(payload["fabric_prompt_text"])

        flow_cfg = config.setdefault("flow_texture", {})
        flow_mode = str(payload.get("flow_prompt_mode", "attachment")).strip().lower()
        if flow_mode in {"attachment", "manual"}:
            flow_cfg["prompt_mode"] = flow_mode
        if "flow_prompt_file" in payload:
            flow_cfg["prompt_file"] = str(payload["flow_prompt_file"]).strip()
        if "flow_prompt_text" in payload:
            flow_cfg["prompt_text"] = str(payload["flow_prompt_text"])

        if "telegram" in payload and isinstance(payload["telegram"], dict):
            t_in = payload["telegram"]
            tele_cfg = config.setdefault("telegram", {})
            if "enabled" in t_in:
                tele_cfg["enabled"] = bool(t_in["enabled"])
            if "bot_token" in t_in:
                tele_cfg["bot_token"] = str(t_in["bot_token"]).strip()
            if "chat_id" in t_in:
                tele_cfg["chat_id"] = str(t_in["chat_id"]).strip()
            if "notify_on_complete" in t_in:
                tele_cfg["notify_on_complete"] = bool(t_in["notify_on_complete"])
            if "notify_on_start" in t_in:
                tele_cfg["notify_on_start"] = bool(t_in["notify_on_start"])
            if "notify_periodic_progress" in t_in:
                tele_cfg["notify_periodic_progress"] = bool(t_in["notify_periodic_progress"])
            if "notify_on_failure" in t_in:
                tele_cfg["notify_on_failure"] = bool(t_in["notify_on_failure"])
            for legacy_key in (
                "notify_on_quota", "notify_on_safe_stop", "notify_on_folder_complete",
                "notify_on_noncritical_error", "include_folder_lists",
            ):
                tele_cfg.pop(legacy_key, None)

        save_json_atomic(self.config_path, config)
        self.start_telegram_poller()
        source_label = str(local_dir) if local_dir else f"Google Drive ({len(valid_urls)} link)"
        self.append_log(f"Đã lưu cấu hình nguồn ảnh & prompt: {source_label}.\n")

    def validate_run(self, payload):
        if not valid_project_dir(self.project_dir):
            raise ValueError("Không tìm thấy config.json trong thư mục dữ liệu.")
        self.python_exe = locate_python(self.project_dir)
        if not self.python_exe:
            raise ValueError("Không thể khởi tạo bộ chạy pipeline.")

        engine = str(payload.get("engine", "chatgpt")).strip().lower()
        limit = str(payload.get("limit", "")).strip()
        if limit and (not limit.isdigit() or int(limit) < 1):
            raise ValueError("Giới hạn phải là số nguyên từ 1 trở lên.")

        if engine in {"flow", "google_flow", "algo", "algorithm", "cv_algorithm"}:
            return

        flows = payload.get("flows", {})
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        if source_mode not in {"drive", "local"}:
            raise ValueError("Nguồn ảnh phải là Google Drive hoặc thư mục local.")
        selected_steps = [
            step
            for step in FLOW_STEPS
            if bool(flows.get(step.key))
            and not (source_mode == "local" and step.key == "import")
        ]
        if not selected_steps:
            raise ValueError("Hãy chọn ít nhất một luồng.")
        if source_mode == "local":
            local_dir = resolve_project_path(
                self.project_dir, payload.get("local_source_dir", "")
            )
            if not safe_is_dir(local_dir):
                raise ValueError("Hãy chọn một thư mục ảnh vải local đang tồn tại.")
        if source_mode == "drive" and flows.get("import"):
            drive_urls = payload.get("pipeline_drive_urls", payload.get("drive_urls", []))
            missing_link_folders = [
                str(item.get("folder", "")).strip() or "Mặc định"
                for item in drive_urls
                if isinstance(item, dict) and not str(item.get("url", "")).strip()
            ]
            if missing_link_folders:
                raise ValueError(
                    "Các thư mục đã chọn chưa có link Google Drive: "
                    + ", ".join(missing_link_folders)
                )
            valid_urls = [i for i in drive_urls if str(i.get("url", "")).strip()]
            if not valid_urls:
                raise ValueError("Hãy nhập ít nhất một link thư mục Google Drive public hợp lệ.")
            for item in valid_urls:
                if not validate_drive_url(str(item.get("url", "")).strip()):
                    raise ValueError("Tất cả link Drive phải có dạng https://drive.google.com/drive/folders/...")

    def build_steps(self, payload, folder=None):
        common = []
        sku = str(payload.get("sku", "")).strip()
        sku_file = str(payload.get("sku_file", "")).strip()
        limit = str(payload.get("limit", "")).strip()
        if sku:
            common.extend(("--sku", sku))
        if sku_file:
            common.extend(("--sku-file", sku_file))
        if limit:
            common.extend(("--limit", limit))
        if bool(payload.get("dry_run")):
            common.append("--dry-run")
        force = bool(payload.get("force"))
        engine = str(payload.get("engine", "chatgpt")).strip().lower()

        if engine in {"algo", "algorithm", "cv_algorithm", "fix_seams"}:
            steps = []
            algo_step = FlowStep("fix_seams", "Vá lỗi Seamless (Efros-Freeman)", "run_algorithm_seamless_batch.py")
            arguments = ["--source-mode", "chatgpt", *common]
            if folder and str(folder).strip():
                arguments.extend(("--folder", str(folder).strip()))
            if force:
                arguments.append("--force")
            steps.append((algo_step, arguments))

            package_args = list(common)
            if folder and str(folder).strip():
                package_args.extend(("--folder", str(folder).strip()))
            if force:
                package_args.append("--force")
            steps.append((FlowStep("package", "Đóng gói SKU", "package_seamless_textures.py"), package_args))

            return steps

        if engine in {"flow", "google_flow"}:
            steps = []
            source_mode = str(payload.get("source_mode", "drive")).strip().lower()
            
            # 1. Import Google Drive if in Drive mode
            if source_mode == "drive":
                import_args = list(common)
                if folder and str(folder).strip():
                    import_args.extend(("--folder", str(folder).strip()))
                steps.append((FlowStep("import", "Import Drive", "import_google_drive.py"), import_args))

            # 2. Crop raw scans into cropped textures
            crop_args = list(common)
            if force:
                crop_args.append("--force")
            steps.append((FlowStep("crop", "Crop cố định", "crop_textures.py"), crop_args))

            # 3. Google Flow texture generation
            flow_step = FlowStep("flow_texture", "Tạo texture Google Flow", "run_flow_texture_batch.py")
            arguments = list(common)
            flow_mode = str(payload.get("flow_prompt_mode", "")).strip().lower()
            flow_file = str(payload.get("flow_prompt_file", "")).strip()
            flow_text = str(payload.get("flow_prompt_text", "")).strip()
            if flow_file:
                arguments.extend(("--prompt-file", flow_file))
            if flow_mode == "manual" and flow_text:
                arguments.extend(("--prompt-text", flow_text))
            if force:
                arguments.append("--force")
            steps.append((flow_step, arguments))

            # 4. Package seamless textures
            package_args = list(common)
            if folder and str(folder).strip():
                package_args.extend(("--folder", str(folder).strip()))
            if force:
                package_args.append("--force")
            steps.append((FlowStep("package", "Đóng gói SKU", "package_seamless_textures.py"), package_args))

            return steps

        flows = payload.get("flows", {})
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        steps = []
        for step in FLOW_STEPS:
            if not bool(flows.get(step.key)):
                continue
            if source_mode == "local" and step.key == "import":
                continue
            arguments = list(common)
            if step.key == "import" and folder and str(folder).strip():
                arguments.extend(("--folder", str(folder).strip()))
            if step.key in {"seamless", "fabric"}:
                arguments.extend(
                    ("--images-per-chat", str(int(payload.get("images_per_chat", 10))))
                )
            if step.key == "seamless":
                seamless_engine = str(payload.get("seamless_engine", "")).strip().lower()
                if bool(payload.get("seamless_missing_only")):
                    seamless_engine = "chatgpt"
                if seamless_engine in {"algo", "algorithm", "cv"}:
                    algo_step = FlowStep("algo_seamless", "Tạo seamless Thuật toán", "run_algorithm_seamless_batch.py")
                    algo_args = list(common)
                    if folder and str(folder).strip():
                        algo_args.extend(("--folder", str(folder).strip()))
                    if force:
                        algo_args.append("--force")
                    steps.append((algo_step, algo_args))
                    continue
                tex_mode = str(payload.get("texture_prompt_mode", "")).strip().lower()
                tex_file = str(payload.get("texture_prompt_file", "")).strip()
                tex_text = str(payload.get("texture_prompt_text", "")).strip()
                if tex_mode in {"attachment", "manual"}:
                    arguments.extend(("--prompt-mode", tex_mode))
                if tex_file:
                    arguments.extend(("--prompt-file", tex_file))
                if tex_mode == "manual" and tex_text:
                    arguments.extend(("--prompt-text", tex_text))
            if step.key == "fabric":
                fab_mode = str(payload.get("fabric_prompt_mode", "")).strip().lower()
                fab_file = str(payload.get("fabric_prompt_file", "")).strip()
                fab_text = str(payload.get("fabric_prompt_text", "")).strip()
                if fab_mode in {"attachment", "manual"}:
                    arguments.extend(("--prompt-mode", fab_mode))
                if fab_file:
                    arguments.extend(("--prompt-file", fab_file))
                if fab_mode == "manual" and fab_text:
                    arguments.extend(("--prompt-text", fab_text))
            if step.key == "package" and folder and str(folder).strip():
                arguments.extend(("--folder", str(folder).strip()))
            if (
                force
                and step.key in {"crop", "seamless", "fabric", "package"}
                and not (step.key == "seamless" and bool(payload.get("seamless_missing_only")))
            ):
                arguments.append("--force")
            steps.append((step, arguments))
        return steps

    def apply_folder_config(self, url, folder):
        config = load_json(self.config_path)
        folder = folder.strip().replace("/", "\\") if folder else ""
        if folder:
            raw_dir = f"textures_raw\\{folder}"
            crop_dir = f"textures_cropped\\{folder}"
            texture_dir = f"textures\\{folder}"
            out_dir = f"output\\chatgpt\\{folder}"
        else:
            raw_dir = "textures_raw"
            crop_dir = "textures_cropped"
            texture_dir = "textures"
            out_dir = "output\\chatgpt"

        # 1. Google Drive
        config.setdefault("google_drive", {})
        config["google_drive"]["share_url"] = url
        config["google_drive"]["destination_dir"] = raw_dir
        config["google_drive"]["enabled"] = bool(url)

        # 2. Crop
        config.setdefault("crop", {})
        config["crop"]["source_dir"] = raw_dir
        config["crop"]["output_dir"] = crop_dir

        # 3. ChatGPT texture grouped
        config.setdefault("chatgpt_texture_grouped", {})
        config["chatgpt_texture_grouped"]["raw_dir"] = crop_dir

        # 4. ChatGPT fabric grouped
        config.setdefault("chatgpt_fabric_grouped", {})
        config["chatgpt_fabric_grouped"]["raw_dir"] = out_dir
        config["chatgpt_fabric_grouped"]["input_mode"] = "seamless"
        config["chatgpt_fabric_grouped"]["output_dir"] = out_dir

        # 5. Google Flow texture
        config.setdefault("flow_texture", {})
        config["flow_texture"]["raw_dir"] = crop_dir
        if folder:
            config["flow_texture"]["status_file"] = f"status_flow_texture_{folder}.json"
        else:
            config["flow_texture"]["status_file"] = "status_flow_texture.json"

        # 6. Seamless package
        config.setdefault("seamless_package", {})
        config["seamless_package"]["output_dir"] = out_dir

        # 6. Legacy / paths / general chatgpt
        config.setdefault("chatgpt", {})
        config["chatgpt"]["output_dir"] = out_dir

        config.setdefault("paths", {})
        config["paths"]["textures_dir"] = texture_dir
        config["paths"]["output_dir"] = out_dir

        save_json_atomic(self.config_path, config)

    def start_pipeline(self, payload, persist_settings=True):
        if self.is_running():
            raise RuntimeError("Một tiến trình đang chạy.")
        self.validate_run(payload)
        if persist_settings:
            self.save_settings(payload)
        self.stop_requested.clear()
        self.skipped_pipeline_folders = set()
        self.append_log("\n" + "=" * 72 + "\nBắt đầu pipeline\n")
        
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        if source_mode == "drive":
            has_pipeline_queue = isinstance(payload.get("pipeline_drive_urls"), list)
            queue = payload.get("pipeline_drive_urls") if has_pipeline_queue else payload.get("drive_urls", [])
            queue_items = [i for i in queue if isinstance(i, dict)]
            if has_pipeline_queue:
                valid_queue = queue_items
            else:
                valid_queue = deduplicate_drive_items(
                    [i for i in queue_items if str(i.get("url", "")).strip()]
                )
            if not valid_queue:
                if has_pipeline_queue:
                    raise ValueError("Không có thư mục chưa hoàn thành nào để chạy pipeline.")
                url = str(payload.get("drive_url", "")).strip()
                folder = str(payload.get("folder", "")).strip()
                valid_queue = [{"url": url, "folder": folder}]
        else:
            valid_queue = [{"url": "", "folder": ""}]
            
        self.worker = threading.Thread(
            target=self.run_queue, args=(valid_queue, payload), daemon=True
        )
        self.worker.start()

    def run_queue(self, queue, payload):
        with self.lock:
            self.session_running = True
            self.session_started_monotonic = time.monotonic()
            self.session_ended_monotonic = None
            self.session_started_at_str = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                config = load_json(self.config_path)
                fp = fabric_progress(self.project_dir, config)
                self.session_start_created_count = fp.get("created_count", 0)
            except Exception:
                self.session_start_created_count = 0

        self.telegram_progress_stop.clear()
        self.telegram_noncritical_errors = []
        queue_folders = [str(item.get("folder", "")).strip() or "Mặc định" for item in queue]
        self.telegram_queue_folders = list(dict.fromkeys(queue_folders))
        self.telegram_completed_folders = set()
        self.notify_telegram(
            "pipeline_start",
            folders=queue_folders,
            current_folder=queue_folders[0] if queue_folders else "Mặc định",
            started_at=time.strftime("%H:%M:%S"),
        )
        self.start_telegram_progress_reporter()

        config = load_json(self.config_path)
        app_ui = config.get("app_ui", {})
        auto_retry_enabled = bool(payload.get("auto_retry_enabled", app_ui.get("auto_retry_enabled", True)))
        auto_retry_delay = int(payload.get("auto_retry_delay_seconds", app_ui.get("auto_retry_delay_seconds", 120)))
        auto_retry_max = int(payload.get("auto_retry_max_attempts", app_ui.get("auto_retry_max_attempts", 10)))
        if auto_retry_delay < 1:
            auto_retry_delay = 1
        if auto_retry_max < 0:
            auto_retry_max = 0

        retry_attempt = 0
        failed = False
        stopped = False
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()

        while True:
            failed = False
            stopped = False

            for i, item in enumerate(queue):
                if self.stop_requested.is_set():
                    stopped = True
                    break
                    
                url = str(item.get("url", "")).strip()
                folder = str(item.get("folder", "")).strip()
                folder_key = (folder or "Mặc định").casefold()
                with self.lock:
                    if folder_key in self.skipped_pipeline_folders:
                        self.append_log(f"\n[SKIP] Bỏ qua thư mục [{folder or 'Mặc định'}] theo yêu cầu người dùng.\n")
                        continue
                self.active_running_folder = folder
                
                if source_mode == "drive":
                    self.apply_folder_config(url, folder)
                    folder_title = folder if folder else "Mặc định (Root)"
                    self.append_log(f"\n--- Đang xử lý thư mục Drive {i+1}/{len(queue)}: [{folder_title}] ---\n")
                    
                step_payload = {**payload, "sku": item["sku"]} if "sku" in item else payload
                steps = self.build_steps(step_payload, folder=folder)
                
                for index, (step, arguments) in enumerate(steps, start=1):
                    if self.stop_requested.is_set():
                        stopped = True
                        break
                    with self.lock:
                        if folder_key in self.skipped_pipeline_folders:
                            break
                    folder_tag = f" [{folder}]" if folder else ""
                    self.active_running_step = step.label
                    self.active_running_sku = step_payload.get("sku")
                    self.set_status(f"{step.label}{folder_tag} ({index}/{len(steps)})")
                    self.append_log(f"\n>>> {step.label}{folder_tag}\n")
                    command = build_step_command(
                        self.python_exe, self.project_dir, step, arguments
                    )
                    self.append_log("    " + subprocess.list2cmdline(command) + "\n")
                    environment = os.environ.copy()
                    environment["PYTHONIOENCODING"] = "utf-8"
                    environment["PYTHONUTF8"] = "1"
                    environment[PROJECT_DIR_ENV] = item.get("runtime_dir", str(self.project_dir.resolve()))
                    try:
                        self.process = subprocess.Popen(
                            command,
                            cwd=self.project_dir,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            env=environment,
                            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                        )
                        with self.lock:
                            skip_after_start = folder_key in self.skipped_pipeline_folders
                        if skip_after_start and self.process.poll() is None:
                            self.process.terminate()
                        if self.process.stdout:
                            for line in self.process.stdout:
                                self.append_log(line)
                        return_code = self.process.wait()
                    except Exception as exc:
                        self.append_log(f"[ERROR] Không chạy được bước: {exc}\n")
                        self.record_telegram_noncritical_error(folder, step.label, str(exc), step_payload.get("sku"))
                        failed = True
                        break
                    finally:
                        if self.process and self.process.stdout:
                            self.process.stdout.close()
                        self.process = None
                    with self.lock:
                        folder_skipped = folder_key in self.skipped_pipeline_folders
                    if folder_skipped:
                        self.append_log(
                            f"[SKIP] Đã dừng xử lý [{folder or 'Mặc định'}]; chuyển sang thư mục tiếp theo.\n"
                        )
                        break
                    if self.stop_requested.is_set():
                        stopped = True
                        break

                    is_quota_stop = (
                        return_code == 42
                        or bool(self.quota_alert)
                        or "CẢNH BÁO QUOTA CHATGPT" in self.log_text
                        or "CHATGPT_QUOTA_EXHAUSTED" in self.log_text
                    )
                    if is_quota_stop:
                        self.append_log(
                            "\n[DỪNG PIPELINE] Đã phát hiện hết quota ChatGPT. "
                            "Dừng toàn bộ pipeline ngay lập tức (không chạy tiếp các bước khác, không tự động thử lại).\n"
                        )
                        reset_info = self.quota_alert.get("reset_info") if self.quota_alert else None
                        self.notify_telegram("quota", reset_info=reset_info)
                        stopped = True
                        failed = False
                        break

                    is_safe_stop = (
                        return_code == 41
                        or any(
                            m in self.log_text
                            for m in (
                                "ChatGPT login has expired",
                                "ChatGPT human-verification challenge detected",
                            )
                        )
                    )
                    if is_safe_stop:
                        self.append_log(
                            "\n[DỪNG PIPELINE] Đã dừng pipeline do yêu cầu can thiệp trình duyệt (login / captcha).\n"
                        )
                        self.notify_telegram("safe_stop", reason="Yêu cầu can thiệp đăng nhập hoặc xác minh Captcha trên Chrome")
                        stopped = True
                        failed = False
                        break

                    if return_code != 0:
                        self.append_log(f"[ERROR] Bước kết thúc với mã lỗi {return_code}.\n")
                        self.record_telegram_noncritical_error(
                            folder, step.label, f"Bước kết thúc với mã lỗi {return_code}", step_payload.get("sku")
                        )
                        failed = True
                        break
                    self.clear_telegram_errors(folder, step.label, step_payload.get("sku"))
                    self.append_log(f"<<< Hoàn tất {step.label}{folder_tag}\n")
                    
                with self.lock:
                    folder_skipped = folder_key in self.skipped_pipeline_folders
                if folder_skipped:
                    self.active_running_step = None
                    self.active_running_sku = None
                    continue
                if failed or stopped:
                    break
                completion_issue = self.pipeline_folder_completion_issue(folder, payload)
                if completion_issue:
                    self.append_log(f"[ERROR] {completion_issue}\n")
                    self.record_telegram_noncritical_error(
                        folder, "Xác minh kết quả", completion_issue, step_payload.get("sku")
                    )
                    failed = True
                    break
                self.telegram_completed_folders.add(folder or "Mặc định")

            if stopped or self.stop_requested.is_set():
                break

            current_config = load_json(self.config_path)
            fp = fabric_progress(self.project_dir, current_config)
            pending_count = fp.get("pending_count", 0)

            # Check for safe stop markers (captcha, login, quota) in recent logs
            has_fatal_safe_stop = (
                bool(self.quota_alert)
                or "CẢNH BÁO QUOTA CHATGPT" in self.log_text
                or "CHATGPT_QUOTA_EXHAUSTED" in self.log_text
                or any(
                    m in self.log_text
                    for m in (
                        "ChatGPT login has expired",
                        "ChatGPT human-verification challenge detected",
                    )
                )
            )

            if failed and auto_retry_enabled and not has_fatal_safe_stop:
                retry_attempt += 1
                if auto_retry_max == 0 or retry_attempt <= auto_retry_max:
                    max_label = str(auto_retry_max) if auto_retry_max > 0 else "∞"
                    self.set_telegram_error_state("retrying", retry_attempt, auto_retry_max)
                    pending_msg = f" (còn {pending_count} SKU chưa tạo)" if pending_count > 0 else ""
                    self.append_log(
                        f"\n[AUTO-RETRY] Phát hiện gián đoạn{pending_msg}. "
                        f"Tự động chờ {auto_retry_delay}s trước khi khởi động lại pipeline (Lần {retry_attempt}/{max_label})...\n"
                    )
                    interrupted = False
                    for sec in range(auto_retry_delay, 0, -1):
                        if self.stop_requested.is_set():
                            interrupted = True
                            break
                        self.set_status(f"⏳ Tự động thử lại sau {sec}s (Lần {retry_attempt}/{max_label})")
                        time.sleep(1)

                    if interrupted or self.stop_requested.is_set():
                        stopped = True
                        break

                    self.append_log(f"\n[AUTO-RETRY] Khởi động lại pipeline ngay bây giờ (Lần {retry_attempt}/{max_label})...\n")
                    continue
                else:
                    self.set_telegram_error_state("failed", auto_retry_max, auto_retry_max)
                    self.append_log(
                        f"\n[AUTO-RETRY] Đã đạt giới hạn số lần thử lại tối đa ({auto_retry_max} lần). Dừng pipeline.\n"
                    )
                    break
            else:
                if failed:
                    self.set_telegram_error_state("failed", 0, 0)
                break

        last_running_folder = self.active_running_folder
        self.telegram_progress_stop.set()
        self.active_running_folder = None
        self.active_running_step = None
        self.active_running_sku = None
        if source_mode == "drive" and queue:
            self.apply_folder_config(queue[0].get("url", ""), queue[0].get("folder", ""))

        with self.lock:
            self.session_running = False
            self.session_ended_monotonic = time.monotonic()
            self.session_ended_at_str = time.strftime("%Y-%m-%d %H:%M:%S")
            if stopped or self.stop_requested.is_set():
                self.status = "Đã dừng"
            elif failed:
                self.status = "Lỗi"
            else:
                self.status = "Hoàn thành"
        if stopped or self.stop_requested.is_set():
            self.append_log("\n[WARNING] Pipeline đã bị dừng bởi người dùng.\n")
        elif failed:
            self.append_log("\n[ERROR] Pipeline dừng do lỗi.\n")
            self.notify_telegram("pipeline_failed", folder=last_running_folder)
        else:
            self.append_log("\n" + "=" * 72 + "\nPipeline hoàn tất.\n")
            duration_sec = (
                self.session_ended_monotonic - self.session_started_monotonic
                if (self.session_ended_monotonic and self.session_started_monotonic)
                else 0
            )
            self.notify_telegram("pipeline_complete", duration_sec=duration_sec)

    def pipeline_folder_completion_issue(self, folder, payload):
        """Return a postcondition error when a full-folder run leaves required outputs missing."""
        if not folder or payload.get("sku") or str(payload.get("limit", "")).strip():
            return ""
        flows = payload.get("flows", {})
        required_field = None
        required_label = None
        if flows.get("fabric"):
            required_field, required_label = "created_count", "swatch hoàn chỉnh"
        elif flows.get("seamless"):
            required_field, required_label = "seamless_count", "seamless"
        elif flows.get("crop"):
            required_field, required_label = "cropped_count", "ảnh crop"
        elif flows.get("import"):
            required_field, required_label = "raw_count", "ảnh Drive"
        if not required_field:
            return ""
        config = load_json(self.config_path)
        stats = compute_drive_folders_stats(self.project_dir, config, folder, include_unlinked=True)
        item = next(
            (value for value in stats.get("folders", []) if value.get("folder_display") == folder),
            None,
        )
        if not item:
            return f"Không tìm thấy dữ liệu kiểm chứng cho folder {folder}."
        expected = int(item.get("drive_total") or item.get("total") or 0)
        completed = int(item.get(required_field, 0))
        if expected > 0 and completed < expected:
            return (
                f"Folder {folder} còn thiếu {expected - completed}/{expected} {required_label}; "
                "pipeline chưa được phép báo hoàn thành."
            )
        return ""

    def record_telegram_noncritical_error(self, folder, stage, message, sku=None):
        """Keep concise structured errors for folder and periodic Telegram summaries."""
        entry = {
            "folder": str(folder or "Mặc định"),
            "sku": str(sku or "Không xác định"),
            "stage": str(stage or "Không xác định"),
            "message": str(message or "Lỗi không xác định")[:300],
            "state": "detected",
            "retry_attempt": 0,
            "retry_max": 0,
        }
        key = (entry["folder"], entry["sku"], entry["stage"], entry["message"])
        with self.lock:
            existing = {
                (item["folder"], item["sku"], item["stage"], item["message"])
                for item in self.telegram_noncritical_errors
            }
            if key not in existing:
                self.telegram_noncritical_errors.append(entry)

    def set_telegram_error_state(self, state, retry_attempt, retry_max):
        with self.lock:
            for item in self.telegram_noncritical_errors:
                item["state"] = str(state)
                item["retry_attempt"] = int(retry_attempt or 0)
                item["retry_max"] = int(retry_max or 0)

    def clear_telegram_errors(self, folder, stage, sku=None):
        """Remove transient errors once the same pipeline step succeeds on retry."""
        folder = str(folder or "Mặc định")
        stage = str(stage or "Không xác định")
        sku = str(sku or "Không xác định")
        with self.lock:
            self.telegram_noncritical_errors = [
                item for item in self.telegram_noncritical_errors
                if not (item["folder"] == folder and item["stage"] == stage and item["sku"] == sku)
            ]

    def start_telegram_progress_reporter(self):
        config = load_json(self.config_path)
        tele_cfg = config.get("telegram", {})
        if not (
            tele_cfg.get("enabled")
            and tele_cfg.get("notify_periodic_progress", True)
            and tele_cfg.get("bot_token")
            and tele_cfg.get("chat_id")
        ):
            return

        def report_loop():
            while not self.telegram_progress_stop.wait(30 * 60):
                if not self.session_running:
                    break
                self.notify_telegram("periodic_progress")

        self.telegram_progress_thread = threading.Thread(target=report_loop, daemon=True)
        self.telegram_progress_thread.start()

    def telegram_progress_summary(self, config, include_lists=True):
        stats = compute_drive_folders_stats(self.project_dir, config, self.active_running_folder)
        folders = stats.get("folders", [])
        session_folders = list(getattr(self, "telegram_queue_folders", []) or [])
        completed_set = set(getattr(self, "telegram_completed_folders", set()) or set())
        active_display = str(self.active_running_folder or ("Mặc định" if self.session_running else ""))
        if session_folders:
            session_names = set(session_folders)
            scoped_folders = [item for item in folders if item.get("folder_display") in session_names]
            completed = [name for name in session_folders if name in completed_set]
            waiting = [name for name in session_folders if name not in completed_set and name != active_display]
            folder_total = len(session_folders)
        else:
            scoped_folders = folders
            completed = [item["folder_display"] for item in folders if item.get("status") == "completed"]
            waiting = [
                item["folder_display"] for item in folders
                if item.get("status") != "completed" and not item.get("is_active")
            ]
            folder_total = stats.get("total_folders", 0)
        total_skus = sum(int(item.get("total", 0)) for item in scoped_folders)
        created_skus = sum(int(item.get("created_count", 0)) for item in scoped_folders)
        sku_percent = round(created_skus * 100 / total_skus) if total_skus else 0
        folder_percent = round(len(completed) * 100 / folder_total) if folder_total else 0
        lines = [
            f"📁 <b>Folder:</b> {len(completed)}/{folder_total} ({folder_percent}%)",
            f"📦 <b>SKU:</b> {created_skus}/{total_skus} ({sku_percent}%)",
        ]
        active = next((item for item in scoped_folders if item.get("is_active")), None)
        if active:
            lines.append(
                f"⚡ <b>Đang xử lý:</b> <code>{html.escape(active['folder_display'])}</code> — "
                f"{active.get('created_count', 0)}/{active.get('total', 0)} SKU ({active.get('percent', 0)}%)"
            )
        elif active_display:
            lines.append(f"⚡ <b>Đang xử lý:</b> <code>{html.escape(active_display)}</code>")
        if self.active_running_step:
            step_line = f"⚙️ <b>Công đoạn:</b> {html.escape(str(self.active_running_step))}"
            if self.active_running_sku:
                step_line += f" — SKU <code>{html.escape(str(self.active_running_sku))}</code>"
            lines.append(step_line)
        if include_lists:
            lines.append("✅ <b>Đã hoàn thành:</b> " + (", ".join(map(html.escape, completed)) or "Chưa có"))
            lines.append("⏳ <b>Đang chờ:</b> " + (", ".join(map(html.escape, waiting)) or "Không có"))
        return "\n".join(lines)

    def telegram_error_summary(self, folder=None):
        with self.lock:
            errors = [
                item for item in self.telegram_noncritical_errors
                if not folder or item["folder"] == folder
            ]
        if not errors:
            return ""
        shown = errors[-5:]
        lines = [f"⚠️ <b>Lỗi hiện tại:</b> {len(errors)}"]
        for item in shown:
            state = item.get("state", "detected")
            if state == "retrying":
                max_text = str(item.get("retry_max") or "∞")
                state_text = f"đang retry {item.get('retry_attempt', 0)}/{max_text}"
            elif state == "failed":
                state_text = "đã hết retry"
            else:
                state_text = "vừa phát hiện"
            lines.append(
                f"• <code>{html.escape(item['folder'])}</code> / {html.escape(item['sku'])} / "
                f"{html.escape(item['stage'])}: {html.escape(item['message'])} ({state_text})"
            )
        if len(errors) > len(shown):
            lines.append(f"• … và {len(errors) - len(shown)} lỗi khác")
        return "\n".join(lines)

    def notify_telegram(self, event_type, **kwargs):
        try:
            config = load_json(self.config_path)
            tele_cfg = config.get("telegram", {})
            if not bool(tele_cfg.get("enabled")):
                return
            bot_token = str(tele_cfg.get("bot_token", "")).strip()
            chat_id = str(tele_cfg.get("chat_id", "")).strip()
            if not bot_token or not chat_id:
                return

            msg = None
            include_lists = True
            if event_type == "pipeline_start" and bool(tele_cfg.get("notify_on_start", True)):
                folders = kwargs.get("folders", [])
                current_folder = kwargs.get("current_folder") or (folders[0] if folders else "Mặc định")
                started_at = kwargs.get("started_at") or time.strftime("%H:%M:%S")
                msg = (
                    "🚀 <b>[VEO3 AUTO] BẮT ĐẦU PIPELINE</b>\n\n"
                    f"📋 <b>Các folder trong hàng đợi:</b> {html.escape(', '.join(folders) or 'Mặc định')}\n"
                    f"⚡ <b>Folder hiện tại đang được xử lý:</b> "
                    f"<code>{html.escape(str(current_folder))}</code>\n"
                    f"🕒 <b>Thời điểm bắt đầu:</b> {html.escape(str(started_at))}"
                )
            elif event_type == "periodic_progress" and bool(tele_cfg.get("notify_periodic_progress", True)):
                summary = self.telegram_progress_summary(config, include_lists)
                errors = self.telegram_error_summary()
                msg = "📊 <b>[VEO3 AUTO] TIẾN ĐỘ ĐỊNH KỲ 30 PHÚT</b>\n\n" + summary
                if errors:
                    msg += "\n\n" + errors
            elif event_type == "quota" and bool(tele_cfg.get("notify_on_failure", True)):
                reset_info = kwargs.get("reset_info") or "Chưa rõ thời gian"
                msg = (
                    "⚠️ <b>[VEO3 AUTO] HẾT HẠN MỨC (QUOTA) CHATGPT</b>\n\n"
                    f"⏱️ <b>Thời gian đặt lại:</b> {html.escape(str(reset_info))}\n"
                    f"📁 <b>Thư mục hiện tại:</b> <code>{html.escape(str(self.active_running_folder or 'Mặc định'))}</code>\n"
                    "🛑 Tiến trình đã được dừng an toàn để bảo toàn checkpoint."
                )
                msg += "\n\n" + self.telegram_progress_summary(config, True)
            elif event_type == "safe_stop" and bool(tele_cfg.get("notify_on_failure", True)):
                reason = kwargs.get("reason", "Yêu cầu can thiệp trình duyệt")
                msg = (
                    "🛑 <b>[VEO3 AUTO] CẦN CAN THIỆP NGƯỜI DÙNG</b>\n\n"
                    f"⚠️ <b>Lý do:</b> {html.escape(str(reason))}\n"
                    f"📁 <b>Thư mục:</b> <code>{html.escape(str(self.active_running_folder or 'Mặc định'))}</code>\n"
                    "👉 Hãy mở Chrome kiểm tra và xử lý (Captcha / Đăng nhập), sau đó bấm chạy lại."
                )
                msg += "\n\n" + self.telegram_progress_summary(config, True)
            elif event_type == "pipeline_failed" and bool(tele_cfg.get("notify_on_failure", True)):
                summary = self.telegram_progress_summary(config, include_lists)
                errors = self.telegram_error_summary()
                msg = (
                    "🛑 <b>[VEO3 AUTO] PIPELINE DỪNG DO LỖI</b>\n\n"
                    f"📁 <b>Vị trí:</b> <code>{html.escape(str(kwargs.get('folder') or 'Mặc định'))}</code>\n"
                    f"{summary}"
                )
                if errors:
                    msg += "\n\n" + errors
            elif event_type == "pipeline_complete" and bool(tele_cfg.get("notify_on_complete", True)):
                duration_sec = kwargs.get("duration_sec", 0)
                dur_text = format_duration(int(duration_sec)) if duration_sec else "--"
                msg = (
                    "🎉 <b>[VEO3 AUTO] PIPELINE HOÀN TẤT THÀNH CÔNG!</b>\n\n"
                    f"⏱️ <b>Tổng thời gian chạy:</b> {dur_text}"
                )
                msg += "\n\n" + self.telegram_progress_summary(config, True)
                errors = self.telegram_error_summary()
                if errors:
                    msg += "\n\n" + errors

            if msg:
                threading.Thread(
                    target=self._async_send_telegram,
                    args=(bot_token, chat_id, msg),
                    daemon=True,
                ).start()
        except Exception as exc:
            self.append_log(f"[Telegram Error] {exc}\n")

    def _async_send_telegram(self, bot_token, chat_id, msg):
        ok, err = send_telegram_message(bot_token, chat_id, msg)
        if not ok:
            self.append_log(f"[Telegram Error] Gửi thông báo thất bại: {err}\n")
        else:
            self.append_log("[Telegram] Đã gửi thông báo thành công.\n")

    def test_telegram(self, payload):
        bot_token = str(payload.get("bot_token", "")).strip()
        chat_id = str(payload.get("chat_id", "")).strip()
        if not bot_token or not chat_id:
            config = load_json(self.config_path)
            tele_cfg = config.get("telegram", {})
            bot_token = bot_token or str(tele_cfg.get("bot_token", "")).strip()
            chat_id = chat_id or str(tele_cfg.get("chat_id", "")).strip()
        if not bot_token or not chat_id:
            return {"ok": False, "error": "Vui lòng nhập đầy đủ Bot Token và Chat ID."}
        
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        test_msg = (
            "🚀 <b>[VEO3 AUTO] KIỂM TRA KẾT NỐI TELEGRAM</b>\n\n"
            "✅ <b>Kết nối thành công!</b>\n"
            f"🕒 <b>Thời gian:</b> <code>{now}</code>\n"
            "📌 Hệ thống đã sẵn sàng gửi các thông báo tự động (Quota, Tiến độ, Lỗi, Hoàn thành)."
        )
        ok, err = send_telegram_message(bot_token, chat_id, test_msg)
        if ok:
            self.append_log(f"[Telegram] Gửi tin nhắn thử nghiệm thành công tới Chat ID: {chat_id}.\n")
            return {"ok": True, "message": "Gửi tin nhắn thử nghiệm thành công!"}
        else:
            self.append_log(f"[Telegram Error] Gửi thử thất bại: {err}\n")
            return {"ok": False, "error": err}

    def start_telegram_poller(self):
        with self.lock:
            self.telegram_poller_stop.set()
            self.telegram_polling_active = False

            config = load_json(self.config_path)
            tele_cfg = config.get("telegram", {})
            if not bool(tele_cfg.get("enabled")):
                return
            
            bot_token = str(tele_cfg.get("bot_token", "")).strip()
            chat_id = str(tele_cfg.get("chat_id", "")).strip()
            if not bot_token or not chat_id:
                return

            stop_event = threading.Event()
            self.telegram_poller_stop = stop_event
            self.telegram_polling_active = True
            self.telegram_poller_thread = threading.Thread(
                target=self._telegram_poller_loop,
                args=(bot_token, chat_id, stop_event),
                daemon=True
            )
            self.telegram_poller_thread.start()

    def _telegram_poller_loop(self, bot_token, chat_id, stop_event):
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"

        while not stop_event.is_set():
            try:
                # Use a long polling timeout of 30 seconds
                req_url = f"{url}?offset={self.telegram_offset}&timeout=30"
                req = urllib.request.Request(req_url)
                with urllib.request.urlopen(req, timeout=40) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    data = json.loads(body)
                    
                    if data.get("ok") and data.get("result"):
                        for update in data["result"]:
                            update_id = update["update_id"]
                            self.telegram_offset = update_id + 1
                            
                            msg = update.get("message")
                            if not msg:
                                continue
                                
                            sender_chat_id = str(msg.get("chat", {}).get("id"))
                            # Verify sender is the authorized chat_id
                            if sender_chat_id != chat_id:
                                continue
                                
                            text = msg.get("text", "").strip()
                            if text in {"/status", "📊 Xem tiến độ (Status)"}:
                                # Generate progress report
                                self._send_telegram_status_report(bot_token, chat_id)
                                
            except urllib.error.URLError:
                # Network error or timeout, wait before retrying to prevent rapid loop
                stop_event.wait(5)
            except Exception:
                stop_event.wait(5)
        with self.lock:
            if self.telegram_poller_stop is stop_event:
                self.telegram_polling_active = False
                
    def _send_telegram_status_report(self, bot_token, chat_id):
        try:
            config = load_json(self.config_path)
            total_time = 0
            if self.session_running and self.session_started_monotonic:
                total_time = time.monotonic() - self.session_started_monotonic
            time_str = format_duration(int(total_time)) if total_time > 0 else "00:00:00"
            status_text = "Đang chạy ⚡" if self.is_running() else "Nhàn rỗi 💤"
            tele_cfg = config.get("telegram", {})
            summary = self.telegram_progress_summary(config, True)
            report = (
                "📊 <b>BÁO CÁO TIẾN ĐỘ HIỆN TẠI</b>\n"
                f"Trạng thái: {status_text}\n"
                f"Thời gian phiên: {time_str}\n\n{summary}"
            )
            errors = self.telegram_error_summary()
            if errors:
                report += "\n\n" + errors
            send_telegram_message(bot_token, chat_id, report)
        except Exception as exc:
            self.append_log(f"[Telegram Poller Error] {exc}\n")

    def check_swatch_prerequisites(self, payload):
        """Find local source SKUs that do not yet have a usable seamless texture."""
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        selected_sku = str(payload.get("sku", "")).strip()
        limit_text = str(payload.get("limit", "")).strip()
        limit = int(limit_text) if limit_text.isdigit() and int(limit_text) > 0 else None
        targets = []
        local_fallback = False

        requested_folder = str(payload.get("folder", "")).strip()
        if source_mode == "drive":
            if requested_folder:
                requested_url = str(payload.get("url", "")).strip()
                if not requested_url:
                    requested_url = next(
                        (
                            str(item.get("url", "")).strip()
                            for item in payload.get("drive_urls", [])
                            if str(item.get("folder", "")).strip() == requested_folder
                        ),
                        "",
                    )
                targets = [(requested_folder, self.project_dir / "textures_raw" / requested_folder, requested_url)]
            else:
                targets = [
                    (
                        str(item.get("folder", "")).strip(),
                        self.project_dir / "textures_raw" / str(item.get("folder", "")).strip(),
                        str(item.get("url", "")).strip(),
                    )
                    for item in deduplicate_drive_items(payload.get("drive_urls", []))
                    if str(item.get("url", "")).strip()
                ]
                if not targets:
                    raw_root = self.project_dir / "textures_raw"
                    if safe_is_dir(raw_root):
                        targets = [
                            (path.name, path, "")
                            for path in sorted(raw_root.iterdir(), key=lambda item: item.name.casefold())
                            if path.is_dir()
                        ]
                        local_fallback = bool(targets)
        else:
            local_dir = resolve_project_path(self.project_dir, payload.get("local_source_dir", ""))
            targets = [(requested_folder, local_dir, "")]

        missing = []
        checked = 0
        config = load_json(self.config_path)
        drive_settings = config.get("google_drive", {})
        for folder, source_dir, drive_url in targets:
            sku_names = []
            if source_mode == "drive" and drive_url and bool(payload.get("flows", {}).get("import")):
                import import_google_drive

                entries = import_google_drive.list_public_folder(
                    import_google_drive.validate_share_url(drive_url),
                    int(drive_settings.get("timeout_seconds", 300)),
                )
                extensions = {
                    str(value).lower() if str(value).startswith(".") else f".{str(value).lower()}"
                    for value in drive_settings.get("extensions", import_google_drive.DEFAULT_EXTENSIONS)
                }
                images, _, _ = import_google_drive.select_images(
                    entries,
                    extensions,
                    bool(drive_settings.get("recursive", False)),
                )
                sku_names = sorted({Path(item["name"]).stem for item in images}, key=str.casefold)
            elif safe_is_dir(source_dir):
                sku_names = sorted(
                    {
                        path.stem
                        for path in source_dir.rglob("*")
                        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                    },
                    key=str.casefold,
                )

            folder_checked = 0
            for sku in sku_names:
                if selected_sku and sku.casefold() != selected_sku.casefold():
                    continue
                checked += 1
                folder_checked += 1
                output_dir = self.project_dir / "output" / "chatgpt"
                final_path = output_dir / folder / sku / "seamless_texture.png" if folder else output_dir / sku / "seamless_texture.png"
                valid = final_path.is_file() and final_path.stat().st_size > 0
                if valid:
                    try:
                        from PIL import Image
                        with Image.open(final_path) as image:
                            image.verify()
                    except Exception:
                        valid = False
                if not valid:
                    missing.append(f"{folder}/{sku}" if folder else sku)
                if limit is not None and folder_checked >= limit:
                    break

        return {
            "checked_count": checked,
            "missing_count": len(missing),
            "missing_skus": missing,
            "local_fallback": local_fallback,
        }

    def run_single_folder(self, payload):
        folder = str(payload.get("folder", "")).strip()
        url = str(payload.get("url", "")).strip()
        engine = str(payload.get("engine", "chatgpt")).strip().lower()
        
        config = load_json(self.config_path)
        if not url:
            for item in config.get("google_drive", {}).get("urls", []):
                if str(item.get("folder", "")).strip() == folder:
                    url = str(item.get("url", "")).strip()
                    break
        if not url:
            url = str(config.get("google_drive", {}).get("share_url", "")).strip()
        
        flows = payload.get("flows", {
            "import": True,
            "crop": True,
            "seamless": True,
            "fabric": True,
            "package": True,
        })
        
        run_payload = {
            "engine": engine,
            "seamless_engine": str(payload.get("seamless_engine", "chatgpt")),
            "source_mode": "drive",
            "drive_urls": [{"url": url, "folder": folder}],
            "sku": payload.get("sku", ""),
            "limit": payload.get("limit", ""),
            "images_per_chat": int(payload.get("images_per_chat", 10)),
            "auto_retry_enabled": bool(payload.get("auto_retry_enabled", True)),
            "auto_retry_delay_seconds": int(payload.get("auto_retry_delay_seconds", 120)),
            "auto_retry_max_attempts": int(payload.get("auto_retry_max_attempts", 10)),
            "dry_run": bool(payload.get("dry_run", False)),
            "force": bool(payload.get("force", False)),
            "seamless_missing_only": bool(payload.get("seamless_missing_only", False)),
            "flows": flows,
        }
        engine_label = "Thuật toán CV" if engine in {"algo", "algorithm"} else "Google Flow" if engine in {"flow", "google_flow"} else "ChatGPT"
        self.append_log(f"\n[YÊU CẦU] Chạy riêng thư mục Drive ({engine_label}): [{folder or 'Mặc định'}]\n")
        self.start_pipeline(run_payload)

    def run_single_sku(self, payload):
        sku = str(payload.get("sku", "")).strip()
        folder = str(payload.get("folder", "")).strip()
        if not sku:
            raise ValueError("Chưa chỉ định mã SKU.")
        force = bool(payload.get("force", True))
        images_per_chat = int(payload.get("images_per_chat", 1))
        engine = str(payload.get("engine", "chatgpt")).strip().lower()

        config = load_json(self.config_path)
        mode, _, _, _ = source_settings(self.project_dir, config)

        missing_labels = []
        if engine == "chatgpt":
            output_root = resolve_project_path(self.project_dir, "output/chatgpt")
            sku_output = output_root / folder / sku if folder else output_root / sku
            has_seamless = safe_is_file(sku_output / "seamless_texture.png")
            has_fabric = safe_is_file(sku_output / "image_1.png")
            if has_seamless and has_fabric:
                self.append_log(
                    f"\n[BỎ QUA] SKU {sku} đã có đủ Seamless và Swatch; không gửi prompt ChatGPT.\n"
                )
                return {
                    "ok": True,
                    "started": False,
                    "message": f"SKU {sku} đã có đủ Seamless và Swatch. Không cần tạo lại.",
                }
            flows = {
                "import": False,
                "crop": not has_seamless,
                "seamless": not has_seamless,
                "fabric": not has_fabric,
                "package": not has_seamless,
            }
            # Force is safe here because only missing output stages are enabled.
            force = True
            if not has_seamless:
                missing_labels.append("Seamless")
            if not has_fabric:
                missing_labels.append("Swatch")
        else:
            flows = {
                "import": False,
                "crop": True,
                "seamless": True,
                "fabric": True,
                "package": True,
            }

        if folder:
            self.apply_folder_config(config.get("google_drive", {}).get("share_url", ""), folder)

        run_payload = {
            "engine": engine,
            "source_mode": mode,
            "local_source_dir": config.get("app_ui", {}).get("local_source_dir", ""),
            "drive_urls": [{"url": config.get("google_drive", {}).get("share_url", ""), "folder": folder}],
            "sku": sku,
            "limit": "1",
            "images_per_chat": images_per_chat,
            "dry_run": False,
            "force": force,
            "flows": flows,
        }
        engine_label = "Thuật toán CV" if engine in {"algo", "algorithm"} else "Google Flow" if engine in {"flow", "google_flow"} else "ChatGPT"
        missing_text = f", Chỉ tạo: {', '.join(missing_labels)}" if missing_labels else ""
        self.append_log(f"\n[YÊU CẦU] Chạy riêng SKU ({engine_label}): {sku} (Folder: {folder or 'Mặc định'}, Force: {force}{missing_text})\n")
        self.start_pipeline(run_payload, persist_settings=False)
        return {"ok": True, "started": True, "missing": missing_labels}

    def delete_drive_folder(self, payload):
        folder = str(payload.get("folder", "")).strip().replace("/", "\\")
        url = str(payload.get("url", "")).strip()

        config = load_json(self.config_path)
        drive = config.setdefault("google_drive", {})
        urls = drive.get("urls", [])

        # This legacy endpoint used to hide the folder from Drive management.
        # Dismissing a pipeline card is now client-side only. Remove any stale
        # hidden marker while preserving the Drive link and all local data.
        if folder:
            hidden_folders = drive.get("hidden_folders", [])
            if not isinstance(hidden_folders, list):
                hidden_folders = []
            folder_key = folder.casefold()
            drive["hidden_folders"] = [
                item for item in hidden_folders
                if str(item).strip().replace("/", "\\").casefold() != folder_key
            ]

        save_json_atomic(self.config_path, config)

        self.append_log(
            f"Đã ẩn thẻ pipeline [{folder or url}] khỏi giao diện; "
            "link Drive, mục quản lý và dữ liệu local được giữ nguyên.\n"
        )
        return {"ok": True, "drive_urls": urls, "hidden_folders": drive.get("hidden_folders", [])}

    def dismiss_quota_alert(self):
        with self.lock:
            self.quota_alert = None
        return {"ok": True}

    def open_sku_folder(self, payload):
        sku = str(payload.get("sku", "")).strip()
        folder = str(payload.get("folder", "")).strip()

        base_out = resolve_project_path(self.project_dir, "output/chatgpt")
        
        target_dir = None
        if folder and sku:
            candidate = base_out / folder / sku
            if safe_is_dir(candidate):
                target_dir = candidate
            else:
                candidate = base_out / folder
                if safe_is_dir(candidate):
                    target_dir = candidate
        elif folder:
            candidate = base_out / folder
            target_dir = candidate if safe_is_dir(candidate) else base_out
        elif sku:
            candidate = base_out / sku
            if safe_is_dir(candidate):
                target_dir = candidate
            else:
                for match in base_out.glob(f"*/{sku}"):
                    if safe_is_dir(match):
                        target_dir = match
                        break
        
        if not target_dir:
            target_dir = base_out

        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = str(target_dir.resolve())
        if hasattr(os, "startfile"):
            os.startfile(target_path)
        else:
            subprocess.Popen(["explorer", target_path])
        return {"ok": True, "path": target_path}

    def audit_drive_folders(self, payload):
        urls = payload.get("urls", [])
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        target_child = str(payload.get("target_child", "all")).strip()
        results = []
        for url in urls:
            url_str = str(url).strip()
            if not url_str:
                continue
            res = audit_single_drive_url(self.project_dir, url_str, target_child=target_child)
            results.append(res)
        return {"results": results}

    def save_drive_link(self, payload):
        folder = str(payload.get("folder", "")).strip()
        url = str(payload.get("url", "")).strip()
        original_folder = str(payload.get("original_folder", folder)).strip()
        modified_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not folder:
            raise ValueError("Tên thư mục không được để trống.")
        if url and not validate_drive_url(url):
            raise ValueError("Link Google Drive không hợp lệ. Phải có dạng https://drive.google.com/drive/folders/...")
        
        config_path = self.project_dir / "config.json"
        config = load_json(config_path)
        drive = config.setdefault("google_drive", {})
        urls = drive.get("urls", [])
        if not isinstance(urls, list):
            urls = []

        new_folder_id = drive_folder_id(url) if url else ""
        for item in urls:
            item_folder = str(item.get("folder", "")).strip()
            same_original = item_folder.casefold() == original_folder.casefold()
            if (
                original_folder.casefold() != folder.casefold()
                and item_folder.casefold() == folder.casefold()
                and not same_original
            ):
                raise ValueError(f"Tên thư mục '{folder}' đã có trong danh sách Drive.")
            if (
                new_folder_id
                and drive_folder_id(item.get("url", "")) == new_folder_id
                and not same_original
            ):
                raise ValueError(
                    f"Link Drive này đã được gán cho thư mục '{item_folder or 'Mặc định'}'."
                )

        # Explicitly assigning/editing a link restores a previously hidden card.
        hidden_folders = drive.get("hidden_folders", [])
        if isinstance(hidden_folders, list):
            folder_key = folder.replace("/", "\\").casefold()
            drive["hidden_folders"] = [
                item for item in hidden_folders
                if str(item).strip().replace("/", "\\").casefold() != folder_key
            ]
        
        found = False
        new_urls = []
        for item in urls:
            item_folder = str(item.get("folder", "")).strip()
            if item_folder.casefold() == original_folder.casefold():
                if url:
                    new_urls.append({"url": url, "folder": folder, "modified_at": modified_at})
                found = True
            else:
                new_urls.append(item)
        if not found and url:
            new_urls.append({"url": url, "folder": folder, "modified_at": modified_at})
        
        drive["urls"] = new_urls
        if new_urls:
            drive["share_url"] = new_urls[0]["url"]
            drive["enabled"] = True
        else:
            drive["share_url"] = ""
            
        save_json_atomic(config_path, config)
        self.append_log(f"[CONFIG] Đã {'cập nhật' if url else 'gỡ'} link Drive cho thư mục '{folder}'\n")
        return {"ok": True, "folder": folder, "url": url, "drive_urls": new_urls}

    def audit_single_folder(self, payload):
        folder = str(payload.get("folder", "")).strip()
        url = str(payload.get("url", "")).strip()
        config_path = self.project_dir / "config.json"
        config = load_json(config_path) if safe_is_file(config_path) else {}
        
        if not url:
            for item in config.get("google_drive", {}).get("urls", []):
                if str(item.get("folder", "")).strip().casefold() == folder.casefold():
                    url = str(item.get("url", "")).strip()
                    break
        if not url:
            raise ValueError(f"Thư mục '{folder}' chưa được gán link Google Drive.")
        
        res = audit_single_drive_url(self.project_dir, url, target_child="chatgpt")
        
        # Save snapshot into status_drive_sync.json
        try:
            sync_file = self.project_dir / "status_drive_sync.json"
            sync_data = load_json(sync_file) if safe_is_file(sync_file) else {}
            folders_data = sync_data.setdefault("folders", {})
            check = next((c for c in res.get("checks", []) if c.get("engine") == "chatgpt"), None)
            if not check and res.get("checks"):
                check = res["checks"][0]
            
            drive_total = res.get("drive_total", 0)
            created_count = check.get("created_count", 0) if check else 0
            missing_count = check.get("missing_count", 0) if check else drive_total
            percent = check.get("percent", 0.0) if check else 0.0
            
            save_key = folder or res.get("base_code") or "root"
            folders_data[save_key] = {
                "folder": save_key,
                "drive_url": url,
                "last_sync_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "drive_total_images": drive_total,
                "created_count": created_count,
                "pending_count": missing_count,
                "progress_percent": percent,
                "last_status": "audited",
            }
            save_json_atomic(sync_file, sync_data)
        except Exception as exc:
            self.append_log(f"[WARN] Không lưu được status_drive_sync.json: {exc}\n")
            
        return res

    def auto_match_drive_urls(self, payload):
        urls = payload.get("urls", [])
        if isinstance(urls, str):
            urls = [u.strip() for u in re.split(r"[\r\n,]+", urls) if u.strip()]
        
        config_path = self.project_dir / "config.json"
        config = load_json(config_path)
        stats = compute_drive_folders_stats(self.project_dir, config, include_unlinked=True)
        existing_folders = [f["folder"] for f in stats.get("folders", [])]
        existing_map = {f.casefold(): f for f in existing_folders}
        
        matched = []
        unmatched = []
        
        for url in urls:
            url_str = str(url).strip()
            if not url_str or not validate_drive_url(url_str):
                unmatched.append({"url": url_str, "reason": "URL không hợp lệ hoặc không phải folder Drive."})
                continue
            try:
                title = fetch_drive_folder_title(url_str, timeout=6)
                base_code = extract_base_folder_code(title or "")
                
                matched_folder = None
                for cand in [title, base_code]:
                    if cand and cand.casefold() in existing_map:
                        matched_folder = existing_map[cand.casefold()]
                        break
                
                if not matched_folder and base_code:
                    for k, v in existing_map.items():
                        if k.startswith(base_code.casefold()) or base_code.casefold().startswith(k):
                            matched_folder = v
                            break
                            
                if matched_folder:
                    matched.append({
                        "url": url_str,
                        "folder": matched_folder,
                        "title": title or base_code,
                        "base_code": base_code,
                    })
                else:
                    unmatched.append({
                        "url": url_str,
                        "folder": base_code or title or "Chưa nhận diện",
                        "title": title,
                        "base_code": base_code,
                        "reason": "Không tìm thấy thư mục vải tương ứng trên máy.",
                    })
            except Exception as exc:
                unmatched.append({"url": url_str, "reason": str(exc)})
                
        if matched:
            drive = config.setdefault("google_drive", {})
            urls_list = drive.setdefault("urls", [])
            for m in matched:
                folder_name = m["folder"]
                found = False
                for item in urls_list:
                    if str(item.get("folder", "")).strip().casefold() == folder_name.casefold():
                        item["url"] = m["url"]
                        item["modified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        found = True
                        break
                if not found:
                    urls_list.append({
                        "url": m["url"],
                        "folder": folder_name,
                        "modified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
            if urls_list:
                drive["share_url"] = urls_list[0]["url"]
                drive["enabled"] = True
            save_json_atomic(config_path, config)
            self.append_log(f"[CONFIG] Đã tự động ghép nối {len(matched)} link Drive vào thư mục vải.\n")
            
        return {
            "ok": True,
            "matched": matched,
            "unmatched": unmatched,
            "total_matched": len(matched),
            "total_unmatched": len(unmatched),
        }

    def check_browser(self):
        if self.is_running():
            raise RuntimeError("Một tiến trình đang chạy.")
        self.python_exe = locate_python(self.project_dir)
        if not self.python_exe:
            raise ValueError("Không thể khởi tạo bộ chạy pipeline.")
        step = FlowStep(
            "browser",
            "Kiểm tra Chrome và ChatGPT",
            "run_chatgpt_texture_grouped_batch.py",
        )
        self.stop_requested.clear()
        self.worker = threading.Thread(
            target=self.run_steps, args=([(step, ["--check-browser"])],), daemon=True
        )
        self.worker.start()

    def check_flow_browser(self):
        if self.is_running():
            raise RuntimeError("Một tiến trình đang chạy.")
        self.python_exe = locate_python(self.project_dir)
        if not self.python_exe:
            raise ValueError("Không thể khởi tạo bộ chạy pipeline.")
        step = FlowStep(
            "browser_flow",
            "Kiểm tra Chrome và Google Flow",
            "run_flow_texture_batch.py",
        )
        self.stop_requested.clear()
        self.worker = threading.Thread(
            target=self.run_steps, args=([(step, ["--check-browser"])],), daemon=True
        )
        self.worker.start()

    def run_steps(self, steps):
        with self.lock:
            self.session_running = True
            self.session_started_monotonic = time.monotonic()
            self.session_ended_monotonic = None
            self.session_started_at_str = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                config = load_json(self.config_path)
                fp = fabric_progress(self.project_dir, config)
                self.session_start_created_count = fp.get("created_count", 0)
            except Exception:
                self.session_start_created_count = 0

        failed = False
        stopped = False
        for index, (step, arguments) in enumerate(steps, start=1):
            if self.stop_requested.is_set():
                stopped = True
                break
            self.set_status(f"{step.label} ({index}/{len(steps)})")
            self.append_log(f"\n>>> {step.label}\n")
            command = build_step_command(
                self.python_exe, self.project_dir, step, arguments
            )
            self.append_log("    " + subprocess.list2cmdline(command) + "\n")
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUTF8"] = "1"
            environment[PROJECT_DIR_ENV] = str(self.project_dir.resolve())
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=self.project_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                )
                if self.process.stdout:
                    for line in self.process.stdout:
                        self.append_log(line)
                return_code = self.process.wait()
            except Exception as exc:
                self.append_log(f"[ERROR] Không chạy được bước: {exc}\n")
                failed = True
                break
            finally:
                if self.process and self.process.stdout:
                    self.process.stdout.close()
                self.process = None
            if self.stop_requested.is_set():
                stopped = True
                break
            if return_code != 0:
                self.append_log(f"[ERROR] Bước kết thúc với mã lỗi {return_code}.\n")
                failed = True
                break
            self.append_log(f"<<< Hoàn tất {step.label}\n")

        with self.lock:
            self.session_running = False
            self.session_ended_monotonic = time.monotonic()
            self.session_ended_at_str = time.strftime("%Y-%m-%d %H:%M:%S")

        result = (
            "Đã dừng"
            if stopped
            else "Pipeline gặp lỗi"
            if failed
            else "Hoàn tất pipeline"
        )
        self.set_status(result)
        self.append_log(f"\n{result}\n")

    def _stop_process_with_escalation(self, process):
        """Give the active worker a short graceful exit, then force it to stop."""
        try:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                self.append_log(
                    "\n[STOP] Đã gửi yêu cầu dừng an toàn; chờ tối đa 3 giây...\n"
                )
                process.wait(timeout=3)
                self.append_log("[STOP] Worker đã dừng an toàn.\n")
                return
            except subprocess.TimeoutExpired:
                pass
            except Exception as exc:
                self.append_log(
                    f"[STOP] Không gửi được yêu cầu dừng an toàn ({exc}); "
                    "chuyển sang terminate.\n"
                )

            if process.poll() is None:
                self.append_log(
                    "[STOP] Worker chưa phản hồi sau 3 giây; đang cưỡng chế terminate...\n"
                )
                try:
                    process.terminate()
                except Exception as exc:
                    self.append_log(f"[STOP] terminate không thành công: {exc}\n")

            try:
                process.wait(timeout=2)
                self.append_log("[STOP] Worker đã được terminate.\n")
                return
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                if process.poll() is not None:
                    return

            if process.poll() is None:
                self.append_log(
                    "[STOP] Worker vẫn còn chạy; đang kill tiến trình worker hiện tại...\n"
                )
                try:
                    process.kill()
                    process.wait(timeout=2)
                    self.append_log("[STOP] Đã cưỡng chế dừng worker.\n")
                except Exception as exc:
                    self.append_log(f"[STOP] Không thể kill worker: {exc}\n")
        finally:
            with self.lock:
                if self.stop_escalation_thread is threading.current_thread():
                    self.stop_escalation_thread = None

    def stop(self):
        self.stop_requested.set()
        self.set_status("Đang dừng...")
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return
            if self.stop_escalation_thread and self.stop_escalation_thread.is_alive():
                return
            self.stop_escalation_thread = threading.Thread(
                target=self._stop_process_with_escalation,
                args=(process,),
                daemon=True,
                name="veo3-stop-escalation",
            )
            self.stop_escalation_thread.start()

    def skip_pipeline_folder(self, payload):
        folder = str(payload.get("folder", "")).strip()
        display_name = folder or "Mặc định"
        folder_key = display_name.casefold()
        with self.lock:
            queued_keys = {
                str(item).strip().casefold()
                for item in self.telegram_queue_folders
            }
            if not self.session_running or folder_key not in queued_keys:
                raise ValueError(f"Thư mục [{display_name}] không còn nằm trong pipeline đang chạy.")
            self.skipped_pipeline_folders.add(folder_key)
            is_current = str(self.active_running_folder or "Mặc định").strip().casefold() == folder_key
            process = self.process if is_current else None

        self.append_log(f"\n[YÊU CẦU] Bỏ qua thư mục pipeline [{display_name}].\n")
        if process and process.poll() is None:
            threading.Thread(
                target=self._terminate_skipped_process,
                args=(process, display_name),
                daemon=True,
                name="veo3-skip-folder",
            ).start()
        return {"ok": True, "folder": folder, "was_active": is_current}

    def _terminate_skipped_process(self, process, folder):
        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception as exc:
                self.append_log(f"[SKIP] Không thể dừng worker của [{folder}]: {exc}\n")
        except Exception as exc:
            if process.poll() is None:
                self.append_log(f"[SKIP] Không thể dừng worker của [{folder}]: {exc}\n")

    def start_automation_chrome(self):
        config = load_json(self.config_path)
        browser = config.get("browser", {})
        port = int(browser.get("cdp_port", 9333))
        profile = Path(
            os.path.expandvars(
                str(
                    browser.get(
                        "user_data_dir", "%LOCALAPPDATA%\\VEO3_AUTO\\ChromeProfile"
                    )
                )
            )
        )
        chrome = find_chrome()
        if not chrome:
            raise FileNotFoundError("Không tìm thấy Google Chrome.")
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [
                str(chrome),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://chatgpt.com/",
            ],
            cwd=self.project_dir,
        )
        self.append_log(f"Đã mở Chrome automation trên port {port}.\n")

    def request_shutdown(self):
        if self.is_running():
            self.stop()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AppHandler(BaseHTTPRequestHandler):
    server_version = "VEO3Auto/1.0"

    @property
    def controller(self):
        return self.server.controller

    def log_message(self, format_string, *args):
        return

    def send_bytes(self, body, content_type, status=HTTPStatus.OK, cache_seconds=0):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache_seconds > 0:
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_placeholder_svg(self, sku, kind):
        label = "CHƯA CÓ ẢNH"
        if kind == "output":
            label = "CHƯA TẠO SEAMLESS"
        elif kind == "fabric":
            label = "CHƯA TẠO SWATCH"
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
  <rect width="200" height="200" fill="#081321"/>
  <rect x="8" y="8" width="184" height="184" rx="10" fill="#0b1728" stroke="#1e2d41" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="100" y="95" fill="#64748b" font-family="Segoe UI,Arial,sans-serif" font-size="12" font-weight="bold" text-anchor="middle">{label}</text>
  <text x="100" y="120" fill="#38bdf8" font-family="Consolas,monospace" font-size="13" text-anchor="middle">{sku}</text>
</svg>"""
        return self.send_bytes(svg.encode("utf-8"), "image/svg+xml", cache_seconds=5)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1_000_000:
            raise ValueError("Request quá lớn.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Request phải là JSON object.")
        return value

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.send_bytes(
                inject_dev_reload(load_index_html()).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        if path == "/styles.css":
            return self.send_bytes(
                (frontend_dir() / "styles.css").read_bytes(),
                "text/css; charset=utf-8",
            )
        if path == "/app.js":
            return self.send_bytes(
                (frontend_dir() / "app.js").read_bytes(),
                "text/javascript; charset=utf-8",
            )
        if path == "/__dev__/version" and os.environ.get(DEV_MODE_ENV):
            return self.send_json({"version": dev_reload_version()})
        if path == "/api/state":
            query = parse_qs(parsed.query)
            folder_filter = query.get("folder", [None])[0]
            return self.send_json(self.controller.state(folder_filter=folder_filter))
        if path == "/api/sku-info":
            query = parse_qs(parsed.query)
            sku = query.get("sku", [""])[0].strip()
            folder = query.get("folder", [""])[0].strip() or None
            if not sku:
                return self.send_json({"error": "Thiếu mã SKU"}, HTTPStatus.BAD_REQUEST)
            try:
                info = get_sku_details(self.controller.project_dir, sku, folder=folder)
                return self.send_json(info)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/load-default-prompt":
            query = parse_qs(parsed.query)
            flow = query.get("flow", ["texture"])[0].strip()
            try:
                text = get_default_prompt_text(self.controller.project_dir, flow)
                return self.send_json({"ok": True, "prompt_text": text})
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/image":
            query = parse_qs(parsed.query)
            sku = query.get("sku", [""])[0].strip()
            kind = query.get("kind", ["output"])[0].strip().lower()
            folder = query.get("folder", [""])[0].strip() or None
            if not sku:
                return self.send_placeholder_svg("?", kind)
            img_path = get_image_file(self.controller.project_dir, sku, kind, folder=folder)
            if not img_path:
                return self.send_placeholder_svg(sku, kind)
            ext = img_path.suffix.lower()
            mime_type = "image/png"
            if ext in {".jpg", ".jpeg"}:
                mime_type = "image/jpeg"
            elif ext == ".webp":
                mime_type = "image/webp"
            elif ext == ".bmp":
                mime_type = "image/bmp"
            try:
                with open(img_path, "rb") as f:
                    body = f.read()
                return self.send_bytes(body, mime_type, cache_seconds=5)
            except Exception:
                return self.send_placeholder_svg(sku, kind)
        if path == "/api/quality-image":
            query = parse_qs(parsed.query)
            try:
                img_path = self.controller.resolve_quality_image(
                    query.get("path", [""])[0]
                )
                mime_types = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp",
                    ".bmp": "image/bmp", ".gif": "image/gif",
                    ".tif": "image/tiff", ".tiff": "image/tiff",
                }
                with open(img_path, "rb") as stream:
                    return self.send_bytes(
                        stream.read(), mime_types.get(img_path.suffix.lower(), "application/octet-stream")
                    )
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        self.send_json({"error": "Không tìm thấy."}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/select-folder":
                selected = choose_local_folder(payload.get("initial_dir"))
                return self.send_json({"path": selected})
            if path == "/api/select-quality-folder":
                return self.send_json(self.controller.select_quality_folder())
            if path == "/api/quality-image-failure":
                return self.send_json(self.controller.set_quality_image_failure(payload))
            if path == "/api/quality-rerun":
                return self.send_json(self.controller.rerun_quality_images(payload))
            if path == "/api/upload-quality-image":
                return self.send_json(self.controller.upload_quality_image(payload))
            if path == "/api/select-prompt-file":
                selected = choose_prompt_file(payload.get("initial_path"))
                return self.send_json({"path": selected})
            if path in {"/api/save", "/api/save-settings"}:
                self.controller.save_settings(payload)
                return self.send_json({"ok": True})
            if path == "/api/test-telegram":
                res = self.controller.test_telegram(payload)
                return self.send_json(res)
            if path == "/api/run":
                self.controller.start_pipeline(payload)
                return self.send_json({"ok": True})
            if path == "/api/check-swatch-prerequisites":
                return self.send_json(self.controller.check_swatch_prerequisites(payload))
            if path == "/api/run-folder":
                self.controller.run_single_folder(payload)
                return self.send_json({"ok": True})
            if path == "/api/run-sku":
                return self.send_json(self.controller.run_single_sku(payload))
            if path == "/api/open-folder":
                res = self.controller.open_sku_folder(payload)
                return self.send_json(res)
            if path == "/api/delete-drive-folder":
                res = self.controller.delete_drive_folder(payload)
                return self.send_json(res)
            if path == "/api/save-drive-link":
                res = self.controller.save_drive_link(payload)
                return self.send_json(res)
            if path == "/api/audit-single-folder":
                res = self.controller.audit_single_folder(payload)
                return self.send_json(res)
            if path == "/api/auto-match-drive-urls":
                res = self.controller.auto_match_drive_urls(payload)
                return self.send_json(res)
            if path == "/api/dismiss-quota-alert":
                res = self.controller.dismiss_quota_alert()
                return self.send_json(res)
            if path == "/api/audit-drive-folders":
                res = self.controller.audit_drive_folders(payload)
                return self.send_json(res)
            if path == "/api/stop":
                self.controller.stop()
                return self.send_json({"ok": True})
            if path == "/api/skip-pipeline-folder":
                return self.send_json(self.controller.skip_pipeline_folder(payload))
            if path == "/api/chrome":
                self.controller.start_automation_chrome()
                return self.send_json({"ok": True})
            if path == "/api/check-browser":
                self.controller.check_browser()
                return self.send_json({"ok": True})
            if path == "/api/check-flow-browser":
                self.controller.check_flow_browser()
                return self.send_json({"ok": True})
            if path == "/api/shutdown":
                self.controller.request_shutdown()
                return self.send_json({"ok": True})
            return self.send_json({"error": "Không tìm thấy."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.controller.append_log(f"[ERROR] {exc}\n")
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def open_app_window(url):
    chrome = find_chrome()
    if chrome:
        try:
            subprocess.Popen([str(chrome), f"--app={url}", "--new-window"])
            return
        except Exception:
            pass
    webbrowser.open(url)


def idle_watchdog(controller, server):
    while True:
        time.sleep(30)
        if getattr(server, "_BaseServer__shutdown_request", False):
            return
        if (
            time.monotonic() - controller.last_client_at > 1800
            and not controller.is_running()
        ):
            controller.append_log("Ứng dụng tự đóng sau 30 phút không sử dụng.\n")
            server.shutdown()
            return


DEFAULT_APP_PORT = 8765


def main(open_browser=True, port=DEFAULT_APP_PORT):
    controller = PipelineController()
    try:
        server = AppServer(("127.0.0.1", int(port)), AppHandler)
    except OSError as exc:
        raise RuntimeError(
            f"Cổng {port} đang được sử dụng. Hãy đóng phiên VEO3 Auto Pipeline cũ rồi mở lại app."
        ) from exc
    server.controller = controller
    controller.server = server
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    if sys.stdout:
        print(f"READY {url}", flush=True)
    controller.append_log(f"VEO3 Auto Pipeline: {url}\n")
    threading.Thread(
        target=idle_watchdog, args=(controller, server), daemon=True
    ).start()
    if open_browser:
        threading.Timer(0.35, open_app_window, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        if controller.is_running():
            controller.stop()
        server.server_close()


if __name__ == "__main__":
    if EMBEDDED_WORKER_FLAG in sys.argv:
        worker_index = sys.argv.index(EMBEDDED_WORKER_FLAG)
        if worker_index + 1 >= len(sys.argv):
            raise SystemExit(f"{EMBEDDED_WORKER_FLAG} requires a script name")
        worker_script = sys.argv[worker_index + 1]
        worker_arguments = sys.argv[worker_index + 2 :]
        raise SystemExit(run_embedded_worker(worker_script, worker_arguments))
    if EMBEDDED_GDOWN_FLAG in sys.argv:
        gdown_index = sys.argv.index(EMBEDDED_GDOWN_FLAG)
        raise SystemExit(run_embedded_gdown(sys.argv[gdown_index + 1 :]))

    no_browser = "--no-browser" in sys.argv
    selected_port = DEFAULT_APP_PORT
    if "--port" in sys.argv:
        port_index = sys.argv.index("--port") + 1
        if port_index >= len(sys.argv):
            raise SystemExit("--port requires an integer")
        selected_port = int(sys.argv[port_index])
    main(open_browser=not no_browser, port=selected_port)
