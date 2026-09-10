import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, urlparse

from PIL import Image
from veo3_runtime import get_project_dir, is_embedded_worker

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


PROJECT_DIR = get_project_dir(__file__)
CONFIG_PATH = PROJECT_DIR / "config.json"
DEFAULT_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
DRIVE_NOT_PUBLIC_MESSAGE = (
    "Không truy cập được thư mục Drive — link Drive hiện không public để có thể truy cập."
)
WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("config.json must contain a JSON object.")
    return config


def resolve_path(value):
    expanded = Path(os.path.expandvars(str(value)))
    return expanded if expanded.is_absolute() else PROJECT_DIR / expanded


def load_status(path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Status file must contain a JSON object: {path}")
    return value


def save_status(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=4, ensure_ascii=False)
    os.replace(temporary, path)


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def validate_share_url(value):
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "drive.google.com":
        raise ValueError("google_drive.share_url must be an https://drive.google.com URL.")
    if "/folders/" not in parsed.path:
        raise ValueError("google_drive.share_url must point to a shared Drive folder.")
    return url


def run_gdown(arguments, timeout_seconds):
    if is_embedded_worker():
        command = [sys.executable, "--embedded-gdown", *arguments]
    else:
        command = [sys.executable, "-m", "gdown", *arguments]
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gdown timed out after {timeout_seconds} seconds.") from exc
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        if "No module named gdown" in details:
            details = "gdown is not available in this application build."
        elif "--folder" in arguments and any(
            marker in details.casefold()
            for marker in (
                "failed to retrieve folder contents",
                "anyone with the link",
                "permission",
                "cp932",
                "multibyte sequence",
            )
        ):
            details = DRIVE_NOT_PUBLIC_MESSAGE
        raise RuntimeError(details or f"gdown exited with code {result.returncode}.")
    return result.stdout


def _parse_public_folder_listing(output):
    try:
        entries = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gdown returned an invalid folder listing.") from exc
    if not isinstance(entries, list):
        raise RuntimeError("gdown folder listing must be a JSON array.")
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url", "")).strip()
        path = str(entry.get("path", "")).replace("\\", "/").strip()
        if not url or not path:
            continue
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise RuntimeError(f"Drive returned an unsafe path: {path}")
        normalized.append({"url": url, "path": pure})
    return normalized


def _require_unlimited_folder_gdown():
    """Reject gdown releases that silently stop at Drive's former 50-file limit."""
    try:
        import gdown

        version_text = str(getattr(gdown, "__version__", "0"))
        numbers = tuple(int(value) for value in re.findall(r"\d+", version_text)[:3])
        version = numbers + (0,) * (3 - len(numbers))
    except Exception as exc:
        raise RuntimeError("Không xác định được phiên bản gdown.") from exc
    if version < (6, 2, 0):
        raise RuntimeError(
            f"gdown {version_text} chỉ có thể đọc thiếu file trong folder Drive lớn. "
            "Hãy chạy setup.bat để nâng cấp lên gdown >= 6.2.0."
        )


def list_public_folder(share_url, timeout_seconds):
    """List a public folder completely, including entries indexed slightly later."""
    _require_unlimited_folder_gdown()
    arguments = [share_url, "--folder", "--json", "--quiet"]
    listing = _parse_public_folder_listing(run_gdown(arguments, timeout_seconds))

    # Small folders are returned atomically. Large public folders historically stop
    # at 50 and the unlimited embedded view can briefly grow while Drive indexes it.
    if len(listing) < 50:
        return listing

    merged = {(entry["url"], str(entry["path"])): entry for entry in listing}
    previous_keys = set(merged)
    stable_rounds = 0
    for _ in range(4):
        time.sleep(1)
        current = _parse_public_folder_listing(run_gdown(arguments, timeout_seconds))
        current_keys = {(entry["url"], str(entry["path"])) for entry in current}
        if current_keys == previous_keys:
            stable_rounds += 1
        else:
            stable_rounds = 0
        for entry in current:
            merged[(entry["url"], str(entry["path"]))] = entry
        previous_keys = current_keys
        if stable_rounds >= 2:
            break
    return list(merged.values())


def strip_listing_root(entries):
    if not entries:
        return entries
    first_parts = [entry["path"].parts[0] for entry in entries if entry["path"].parts]
    has_nested_path = any(len(entry["path"].parts) > 1 for entry in entries)
    common_root = (
        first_parts[0]
        if has_nested_path and first_parts and all(part == first_parts[0] for part in first_parts)
        else None
    )
    if not common_root:
        return entries
    result = []
    for entry in entries:
        parts = entry["path"].parts[1:]
        if parts:
            result.append({**entry, "path": PurePosixPath(*parts)})
    return result


def validate_windows_filename(name):
    if not name or name in {".", ".."} or WINDOWS_INVALID.search(name):
        raise ValueError(f"Drive filename is invalid on Windows: {name}")
    if name.endswith((" ", ".")):
        raise ValueError(f"Drive filename cannot end in a space or dot on Windows: {name}")
    if Path(name).stem.casefold() in WINDOWS_RESERVED:
        raise ValueError(f"Drive filename is reserved on Windows: {name}")


def drive_file_identity(url):
    """Return a stable identity for common Google Drive download URLs."""
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("id")
    if query_id and query_id[0]:
        return ("id", query_id[0])
    path_match = re.search(r"/d/([^/]+)", parsed.path)
    if path_match:
        return ("id", path_match.group(1))
    return ("url", url)


def download_public_drive_file(url, destination, timeout_seconds):
    """Download a public Drive file directly when gdown cannot resolve its URL."""
    identity_type, identity = drive_file_identity(url)
    if identity_type != "id":
        raise ValueError("Không tìm thấy Google Drive file ID trong URL.")
    direct_url = (
        "https://drive.usercontent.google.com/download"
        f"?id={quote(identity, safe='')}&export=download&confirm=t"
    )
    request = urllib.request.Request(
        direct_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        if "text/html" in content_type:
            raise RuntimeError("Google Drive trả về trang HTML thay vì nội dung file.")
        with open(destination, "wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("Google Drive trả về file rỗng.")


def select_images(entries, extensions, recursive):
    selected = []
    skipped_nested = 0
    skipped_duplicate_names = 0
    seen_entries = set()
    seen_names = set()
    for entry in strip_listing_root(entries):
        relative = entry["path"]
        if len(relative.parts) != 1 and not recursive:
            skipped_nested += 1
            continue
        name = relative.name
        if Path(name).suffix.lower() not in extensions:
            continue
        validate_windows_filename(name)
        entry_identity = (
            str(relative).casefold(),
            drive_file_identity(entry["url"]),
        )
        if entry_identity in seen_entries:
            continue
        seen_entries.add(entry_identity)
        folded_name = name.casefold()
        if folded_name in seen_names:
            skipped_duplicate_names += 1
            continue
        seen_names.add(folded_name)
        selected.append({**entry, "name": name})

    by_sku = {}
    collisions = {}
    for entry in selected:
        folded = Path(entry["name"]).stem.casefold()
        if folded in by_sku:
            collisions.setdefault(folded, [by_sku[folded]]).append(entry)
        else:
            by_sku[folded] = entry
    if collisions:
        details = "; ".join(
            ", ".join(item["name"] for item in items) for items in collisions.values()
        )
        raise ValueError(f"Multiple Drive images map to the same SKU: {details}")
    return (
        sorted(selected, key=lambda item: item["name"].casefold()),
        skipped_nested,
        skipped_duplicate_names,
    )


def inspect_image(path):
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format or "UNKNOWN"
        image.verify()
    if width < 1 or height < 1:
        raise ValueError(f"Downloaded image has invalid dimensions: {path}")
    return {"width": width, "height": height, "format": image_format}


def download_entry(entry, destination, staging_dir, timeout_seconds, retries):
    staging_dir.mkdir(parents=True, exist_ok=True)
    temporary = staging_dir / f".{destination.name}.{os.getpid()}.part"
    last_error = None
    try:
        for attempt in range(1, retries + 1):
            if temporary.exists():
                temporary.unlink()
            try:
                direct_error = None
                try:
                    download_public_drive_file(entry["url"], temporary, timeout_seconds)
                except Exception as exc:
                    direct_error = exc
                    run_gdown(
                        [entry["url"], "-O", str(temporary), "--continue", "--no-cookies"],
                        timeout_seconds,
                    )
                image_info = inspect_image(temporary)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, destination)
                return image_info
            except Exception as exc:
                last_error = (
                    RuntimeError(f"Tải trực tiếp lỗi ({direct_error}); gdown lỗi ({exc})")
                    if direct_error is not None
                    else exc
                )
                print(f"    Download attempt {attempt}/{retries} failed: {exc}")
        raise RuntimeError(str(last_error))
    finally:
        if temporary.exists():
            temporary.unlink()


def existing_files_by_sku(destination_dir, extensions):
    result = {}
    if not destination_dir.exists():
        return result
    for path in destination_dir.iterdir():
        if path.is_file() and path.suffix.lower() in extensions:
            result.setdefault(path.stem.casefold(), []).append(path)
    return result


def resolve_folder_name(args_folder, destination_dir, config):
    if args_folder and str(args_folder).strip():
        return str(args_folder).strip()
    destination_dir = Path(destination_dir)
    raw_root = resolve_path("textures_raw")
    try:
        rel = destination_dir.resolve().relative_to(raw_root.resolve())
        if str(rel) not in {".", ""}:
            return rel.parts[0]
    except Exception:
        pass
    share_url = str(config.get("google_drive", {}).get("share_url", "")).strip()
    for item in config.get("google_drive", {}).get("urls", []):
        if str(item.get("url", "")).strip() == share_url and item.get("folder"):
            return str(item.get("folder")).strip()
    return destination_dir.name if destination_dir.name != "textures_raw" else "root"


def compute_folder_creation_stats(project_dir, folder_name, drive_skus=None):
    """
    Check output/chatgpt/<folder_name> (and flat output/chatgpt) to find how many SKUs
    have completed artifacts (seamless_texture.png or image_1.png).
    """
    output_root = Path(project_dir) / "output" / "chatgpt"
    output_name = "seamless_texture.png"
    fabric_name = "image_1.png"

    target_dirs = []
    if folder_name and folder_name != "root":
        f_dir = output_root / folder_name
        if f_dir.is_dir():
            target_dirs.append(f_dir)
    if output_root.is_dir():
        target_dirs.append(output_root)

    created_skus = set()
    seamless_skus = set()
    fabric_skus = set()

    for out_dir in target_dirs:
        for sub in out_dir.iterdir():
            if not sub.is_dir():
                continue
            sku_name = sub.name
            has_seamless = (sub / output_name).is_file()
            has_fabric = (sub / fabric_name).is_file()
            if has_seamless:
                seamless_skus.add(sku_name)
            if has_fabric:
                fabric_skus.add(sku_name)
            if has_seamless or has_fabric:
                created_skus.add(sku_name)

    crop_root = Path(project_dir) / "textures_cropped"
    target_crop = crop_root / folder_name if folder_name and folder_name != "root" else crop_root
    cropped_count = 0
    if target_crop.is_dir():
        cropped_count = sum(
            1 for p in target_crop.iterdir()
            if p.is_file() and p.suffix.lower() in DEFAULT_EXTENSIONS
        )

    drive_skus_set = {str(s).strip() for s in (drive_skus or []) if str(s).strip()}
    if drive_skus_set:
        matched_created = created_skus.intersection(drive_skus_set)
        matched_seamless = seamless_skus.intersection(drive_skus_set)
        matched_fabric = fabric_skus.intersection(drive_skus_set)
        created_count = len(matched_created)
        seamless_count = len(matched_seamless)
        fabric_count = len(matched_fabric)
    else:
        created_count = len(created_skus)
        seamless_count = len(seamless_skus)
        fabric_count = len(fabric_skus)

    return {
        "created_count": created_count,
        "seamless_count": seamless_count,
        "fabric_count": fabric_count,
        "cropped_count": cropped_count,
        "total_created_in_output": len(created_skus),
    }


def record_drive_sync_trace(
    sync_file_path,
    folder_name,
    drive_url,
    drive_total_images,
    new_imported,
    already_existed,
    download_errors,
    local_raw_total,
    creation_stats,
    status="success",
    duration_seconds=0.0,
    triggered_by="cli",
    max_history=200,
):
    """
    Atomically updates status_drive_sync.json with snapshot and history entry.
    """
    data = load_status(sync_file_path)
    if "folders" not in data or not isinstance(data["folders"], dict):
        data["folders"] = {}
    if "history" not in data or not isinstance(data["history"], list):
        data["history"] = []

    created_count = int(creation_stats.get("created_count", 0))
    seamless_count = int(creation_stats.get("seamless_count", 0))
    fabric_count = int(creation_stats.get("fabric_count", 0))
    cropped_count = int(creation_stats.get("cropped_count", 0))

    pending_count = max(0, drive_total_images - created_count)
    progress_percent = (
        round(created_count * 100 / drive_total_images, 1)
        if drive_total_images > 0
        else 0.0
    )

    timestamp_str = now_text()
    sync_id = f"sync_{time.strftime('%Y%m%d_%H%M%S')}_{re.sub(r'[^a-zA-Z0-9_]', '_', folder_name or 'root')}"

    # 1. Update folder snapshot
    data["folders"][folder_name or "root"] = {
        "folder": folder_name or "root",
        "drive_url": drive_url,
        "last_sync_at": timestamp_str,
        "drive_total_images": drive_total_images,
        "local_raw_total": local_raw_total,
        "created_count": created_count,
        "pending_count": pending_count,
        "progress_percent": progress_percent,
        "seamless_count": seamless_count,
        "fabric_count": fabric_count,
        "cropped_count": cropped_count,
        "last_imported_count": new_imported,
        "last_status": status,
    }

    # 2. Add history record
    history_entry = {
        "sync_id": sync_id,
        "timestamp": timestamp_str,
        "folder": folder_name or "root",
        "drive_url": drive_url,
        "stats": {
            "drive_total_images": drive_total_images,
            "new_imported": new_imported,
            "already_existed": already_existed,
            "download_errors": download_errors,
            "local_raw_total": local_raw_total,
            "created_count": created_count,
            "pending_count": pending_count,
            "progress_percent": progress_percent,
            "seamless_count": seamless_count,
            "fabric_count": fabric_count,
            "cropped_count": cropped_count,
        },
        "status": status,
        "duration_seconds": round(duration_seconds, 2),
        "triggered_by": triggered_by,
    }

    data["history"].append(history_entry)
    if len(data["history"]) > max_history:
        data["history"] = data["history"][-max_history:]

    save_status(sync_file_path, data)
    return history_entry


def main():
    start_time = time.monotonic()
    parser = argparse.ArgumentParser(
        description="Import new raw fabric images from an Anyone-with-the-link Drive folder."
    )
    parser.add_argument("--folder", help="Folder name identifier for logging/storage")
    parser.add_argument("--sku", help="Import one exact filename stem")
    parser.add_argument("--limit", type=int, help="Import at most this many new images")
    parser.add_argument("--dry-run", action="store_true", help="List remote images without downloading")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    config = load_config()
    settings = config.get("google_drive", {})
    if not bool(settings.get("enabled", False)):
        print("Google Drive import is disabled. Set google_drive.enabled=true in config.json.")
        return
    share_url = validate_share_url(settings.get("share_url"))
    destination_dir = resolve_path(settings.get("destination_dir", "textures_raw"))
    status_path = resolve_path(
        settings.get("status_file", "status_google_drive_import.json")
    )
    sync_status_path = resolve_path(
        settings.get("sync_status_file", "status_drive_sync.json")
    )
    folder_name = resolve_folder_name(args.folder, destination_dir, config)
    log_dir = resolve_path(settings.get("log_dir", "logs/google_drive_import"))
    staging_dir = log_dir / "downloads"
    extensions = {
        str(value).lower() if str(value).startswith(".") else f".{str(value).lower()}"
        for value in settings.get("extensions", DEFAULT_EXTENSIONS)
    }
    recursive = bool(settings.get("recursive", False))
    timeout_seconds = int(settings.get("timeout_seconds", 300))
    retries = int(settings.get("max_retries", 3))
    if timeout_seconds < 1 or retries < 1:
        raise ValueError("Google Drive timeout and max_retries must be positive.")

    print(f"Reading public Google Drive folder (folder: {folder_name})...")
    entries = list_public_folder(share_url, timeout_seconds)
    images, skipped_nested, skipped_duplicate_names = select_images(
        entries, extensions, recursive
    )
    if args.sku:
        images = [entry for entry in images if Path(entry["name"]).stem == args.sku]
    if not images:
        print("No matching supported images were found in the shared Drive folder.")
        creation_stats = compute_folder_creation_stats(PROJECT_DIR, folder_name, [])
        record_drive_sync_trace(
            sync_file_path=sync_status_path,
            folder_name=folder_name,
            drive_url=share_url,
            drive_total_images=0,
            new_imported=0,
            already_existed=0,
            download_errors=0,
            local_raw_total=0,
            creation_stats=creation_stats,
            status="empty",
            duration_seconds=time.monotonic() - start_time,
            triggered_by="app" if is_embedded_worker() else "cli",
        )
        return

    existing = existing_files_by_sku(destination_dir, extensions)
    pending = []
    conflicts = []
    unchanged = []
    for entry in images:
        sku_key = Path(entry["name"]).stem.casefold()
        local_matches = existing.get(sku_key, [])
        exact = [path for path in local_matches if path.name.casefold() == entry["name"].casefold()]
        if exact:
            try:
                inspect_image(exact[0])
            except Exception:
                conflicts.append((entry, exact))
            else:
                unchanged.append((entry, exact[0]))
        elif local_matches:
            conflicts.append((entry, local_matches))
        else:
            pending.append(entry)
    if args.limit is not None:
        pending = pending[: args.limit]

    print(
        f"Drive images: {len(images)} | new: {len(pending)} | "
        f"existing: {len(unchanged)} | conflicts: {len(conflicts)}"
    )
    if skipped_nested:
        print(f"Nested files ignored: {skipped_nested}")
    if skipped_duplicate_names:
        print(
            f"Duplicate Drive filenames ignored: {skipped_duplicate_names} "
            "(first item kept)"
        )
    for entry, paths in conflicts:
        print(
            f"  [CONFLICT] {entry['name']} has the same SKU as "
            + ", ".join(path.name for path in paths)
        )

    if args.dry_run:
        print("New images to import:")
        for entry in pending:
            print(f"  {entry['path']} -> {destination_dir / entry['name']}")
        if not pending:
            print("  None")
        drive_skus = [Path(entry["name"]).stem for entry in images]
        creation_stats = compute_folder_creation_stats(PROJECT_DIR, folder_name, drive_skus)
        print(f"\n[DRY-RUN STATS] Thư mục: [{folder_name}]")
        print(f"  • Tổng ảnh Drive: {len(images)} | Tải mới: {len(pending)} | Đã có: {len(unchanged)}")
        print(f"  • Đã tạo thành phẩm: {creation_stats['created_count']}/{len(images)}")
        return

    status = load_status(status_path)
    downloaded_count = 0
    failures = len(conflicts)
    for entry in pending:
        sku = Path(entry["name"]).stem
        destination = destination_dir / entry["name"]
        try:
            print(f"  Importing {entry['path']}...")
            image_info = download_entry(
                entry, destination, staging_dir, timeout_seconds, retries
            )
            downloaded_count += 1
            status[sku] = {
                "status": "done",
                "remote_path": str(entry["path"]),
                "remote_url": entry["url"],
                "local_file": destination.name,
                "image": image_info,
                "error_type": None,
                "completed_at": now_text(),
            }
            save_status(status_path, status)
            print(
                f"    Saved {image_info['width']}x{image_info['height']} "
                f"{image_info['format']} to {destination}"
            )
        except Exception as exc:
            failures += 1
            status[sku] = {
                "status": "error",
                "remote_path": str(entry["path"]),
                "remote_url": entry["url"],
                "error_type": type(exc).__name__,
                "error": str(exc),
                "updated_at": now_text(),
            }
            save_status(status_path, status)
            print(f"  [ERROR] {sku}: {exc}")

    # Compute sync and creation trace
    local_raw_total = 0
    if destination_dir.is_dir():
        local_raw_total = sum(
            1 for p in destination_dir.iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        )
    drive_skus = [Path(entry["name"]).stem for entry in images]
    creation_stats = compute_folder_creation_stats(PROJECT_DIR, folder_name, drive_skus)
    duration_seconds = time.monotonic() - start_time
    overall_status = "error" if failures > 0 else "success"

    record_drive_sync_trace(
        sync_file_path=sync_status_path,
        folder_name=folder_name,
        drive_url=share_url,
        drive_total_images=len(images),
        new_imported=downloaded_count,
        already_existed=len(unchanged),
        download_errors=failures,
        local_raw_total=local_raw_total,
        creation_stats=creation_stats,
        status=overall_status,
        duration_seconds=duration_seconds,
        triggered_by="app" if is_embedded_worker() else "cli",
    )

    percent_str = f"{creation_stats['created_count'] * 100 / len(images):.1f}%" if images else "0%"
    print("\n" + "=" * 60)
    print(f"[LƯU VẾT ĐỒNG BỘ DRIVE] Thư mục: [{folder_name}]")
    print(f"  • Tổng ảnh trên Google Drive: {len(images)} ảnh")
    print(f"  • Tải mới: {downloaded_count} | Đã có sẵn: {len(unchanged)} | Lỗi: {failures}")
    print(f"  • Tổng ảnh raw trong máy: {local_raw_total} ảnh")
    print(f"  • Đã tạo thành phẩm: {creation_stats['created_count']}/{len(images)} ({percent_str})")
    print(f"    - Seamless texture: {creation_stats['seamless_count']} | Swatch vải: {creation_stats['fabric_count']}")
    print(f"  • Số ảnh còn chờ tạo: {max(0, len(images) - creation_stats['created_count'])} ảnh")
    print(f"  • Đã ghi vết vào: {sync_status_path.name}")
    print("=" * 60 + "\n")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

