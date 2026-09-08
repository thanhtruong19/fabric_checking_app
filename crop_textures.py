import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

from PIL import Image, ImageOps
from veo3_runtime import get_project_dir


PROJECT_DIR = get_project_dir(__file__)
CONFIG_PATH = PROJECT_DIR / "config.json"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


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


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def validate_box(value):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("crop.box must be [left, top, right, bottom].")
    left, top, right, bottom = map(float, value)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("crop.box values must define a normalized rectangle inside 0..1.")
    return left, top, right, bottom


def crop_signature(box, output_format):
    payload = json.dumps(
        {"mode": "normalized", "box": box, "output_format": output_format},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inspect_image(path):
    with Image.open(path) as image:
        size = image.size
        image_format = image.format or "UNKNOWN"
        image.verify()
    if size[0] < 1 or size[1] < 1:
        raise ValueError(f"Image has invalid dimensions: {path}")
    return {"width": size[0], "height": size[1], "format": image_format}


def discover_sources(source_dir, selected_sku=None):
    keyed = {}
    collisions = {}
    if not source_dir.exists():
        return []
    files = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    for path in files:
        sku = path.stem
        if selected_sku and sku != selected_sku:
            continue
        try:
            rel = path.parent.relative_to(source_dir)
            folder = str(rel) if str(rel) != "." else None
        except Exception:
            folder = None
        folded = sku.casefold()
        if folded in keyed:
            collisions.setdefault(folded, [keyed[folded]]).append((path, folder))
        else:
            keyed[folded] = (path, folder)
    if collisions:
        details = "; ".join(
            ", ".join(p.name for p, _ in paths) for paths in collisions.values()
        )
        raise ValueError(f"Multiple raw images map to the same SKU: {details}")
    return sorted(keyed.values(), key=lambda item: item[0].stem.casefold())


def crop_one(source, destination, box):
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        width, height = image.size
        left = max(0, min(width - 1, math.floor(box[0] * width)))
        top = max(0, min(height - 1, math.floor(box[1] * height)))
        right = max(left + 1, min(width, math.ceil(box[2] * width)))
        bottom = max(top + 1, min(height, math.ceil(box[3] * height)))
        cropped = image.crop((left, top, right, bottom))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            cropped.save(temporary, format="PNG")
            inspect_image(temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    return {
        "source_width": width,
        "source_height": height,
        "crop_box_pixels": [left, top, right, bottom],
        "width": right - left,
        "height": bottom - top,
        "format": "PNG",
    }


def select_pending_sources(sources, output_dir, status, signature, force, limit):
    """Skip valid current crops first, then apply the requested work limit."""
    selected = []
    skipped_current = 0
    for item in sources:
        is_tuple = isinstance(item, tuple)
        source = item[0] if is_tuple else item
        folder = item[1] if is_tuple else None
        destination = (output_dir / folder / f"{source.stem}.png") if folder else (output_dir / f"{source.stem}.png")
        is_current = False
        if not force and destination.exists():
            try:
                source_hash = sha256_file(source)
                record = status.get(source.stem, {})
                is_current = (
                    record.get("source_sha256") == source_hash
                    and record.get("crop_signature") == signature
                )
                if is_current:
                    inspect_image(destination)
            except Exception:
                is_current = False
        if is_current:
            skipped_current += 1
            continue
        selected.append(item)
        if limit is not None and len(selected) >= limit:
            break
    return selected, skipped_current


def main():
    parser = argparse.ArgumentParser(
        description="Crop raw fabric scans with one fixed normalized rectangle."
    )
    parser.add_argument("--sku", help="Crop one exact raw filename stem")
    parser.add_argument("--sku-file", help="JSON file containing exact SKU names to crop")
    parser.add_argument("--limit", type=int, help="Crop at most this many files")
    parser.add_argument("--dry-run", action="store_true", help="Show crop selections only")
    parser.add_argument("--force", action="store_true", help="Replace stale cropped images")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    config = load_config()
    settings = config.get("crop", {})
    if not bool(settings.get("enabled", True)):
        print("Fixed crop flow is disabled in config.json.")
        return
    mode = str(settings.get("mode", "normalized")).lower()
    if mode != "normalized":
        raise ValueError("The fixed crop flow currently supports mode=normalized only.")
    source_dir = resolve_path(settings.get("source_dir", "textures_raw"))
    output_dir = resolve_path(settings.get("output_dir", "textures_cropped"))
    status_path = resolve_path(settings.get("status_file", "status_crop.json"))
    output_format = str(settings.get("output_format", "png")).lower()
    if output_format != "png":
        raise ValueError("The fixed crop flow currently supports output_format=png only.")
    box = validate_box(settings.get("box", [0.02, 0.02, 0.65, 0.47]))
    signature = crop_signature(box, output_format)
    allow_overwrite = args.force or bool(settings.get("overwrite", False))
    status = load_status(status_path)
    selected_skus = None
    if args.sku_file:
        with open(args.sku_file, "r", encoding="utf-8") as handle:
            values = json.load(handle)
        if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
            parser.error("--sku-file must contain a JSON array of non-empty SKU names")
        selected_skus = {value.casefold() for value in values}
    sources = discover_sources(source_dir, args.sku)
    if selected_skus is not None:
        sources = [item for item in sources if item[0].stem.casefold() in selected_skus]
    if not sources:
        print(f"No matching raw images found in {source_dir}")
        return
    sources, skipped_current = select_pending_sources(
        sources,
        output_dir,
        status,
        signature,
        args.force,
        args.limit,
    )
    if not sources:
        print(
            "No crops need processing "
            f"(skipped {skipped_current} current crop(s))."
        )
        return

    if args.dry_run:
        print(f"Fixed normalized crop box: {list(box)}")
        print(f"Skipped current crops before limit: {skipped_current}")
        print("Selected raw images:")
        for source, folder in sources:
            dest = (output_dir / folder / f"{source.stem}.png") if folder else (output_dir / f"{source.stem}.png")
            print(f"  {source.name} -> {dest}")
        return

    failures = 0
    for source, folder in sources:
        sku = source.stem
        destination = (output_dir / folder / f"{sku}.png") if folder else (output_dir / f"{sku}.png")
        try:
            source_hash = sha256_file(source)
            record = status.get(sku, {})
            is_current = (
                destination.exists()
                and record.get("source_sha256") == source_hash
                and record.get("crop_signature") == signature
            )
            if is_current and not args.force:
                try:
                    inspect_image(destination)
                except Exception:
                    if not allow_overwrite:
                        raise
                else:
                    print(f"  Current crop exists; skipping {sku}: {destination}")
                    continue
            if destination.exists() and not allow_overwrite:
                record.update(
                    {
                        "status": "stale",
                        "source_file": source.name,
                        "source_sha256": source_hash,
                        "crop_signature": signature,
                        "error_type": "OUTPUT_CHANGED",
                    }
                )
                status[sku] = record
                save_status(status_path, status)
                print(f"  [WARNING] Stale crop exists for {sku}; use --force to replace it.")
                failures += 1
                continue

            output_info = crop_one(source, destination, box)
            status[sku] = {
                "status": "done",
                "source_file": source.name,
                "source_sha256": source_hash,
                "crop_signature": signature,
                "crop_box_normalized": list(box),
                "output_file": destination.name,
                "output_sha256": sha256_file(destination),
                "output": output_info,
                "error_type": None,
                "completed_at": now_text(),
            }
            save_status(status_path, status)
            print(
                f"  Cropped {sku}: {output_info['source_width']}x{output_info['source_height']} "
                f"-> {output_info['width']}x{output_info['height']} at {destination}"
            )
        except Exception as exc:
            failures += 1
            status[sku] = {
                "status": "error",
                "source_file": source.name,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "updated_at": now_text(),
            }
            save_status(status_path, status)
            print(f"  [ERROR] {sku}: {exc}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
