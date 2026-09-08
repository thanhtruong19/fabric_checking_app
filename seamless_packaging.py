import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from PIL import Image
from veo3_runtime import get_project_dir


PROJECT_DIR = get_project_dir(__file__)
CONFIG_PATH = PROJECT_DIR / "config.json"
TEXTURE_PATTERN = re.compile(r"^texture_(.+)\.[^.]+$", re.IGNORECASE)
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("config.json must contain a JSON object.")
    return value


def resolve_path(value):
    expanded = Path(os.path.expandvars(str(value)))
    return expanded if expanded.is_absolute() else PROJECT_DIR / expanded


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_sku(sku):
    value = str(sku).strip()
    if not value or value in {".", ".."}:
        raise ValueError("SKU cannot be empty.")
    if any(character in value for character in '<>:"/\\|?*'):
        raise ValueError(f"SKU contains a character that is invalid on Windows: {value}")
    return value


def inspect_image(path):
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format or "UNKNOWN"
    if width < 1 or height < 1:
        raise ValueError(f"Image has invalid dimensions: {path}")
    return {"width": width, "height": height, "format": image_format}


def packaging_paths(config=None):
    config = config or load_config()
    paths = config.get("paths", {})
    chatgpt = config.get("chatgpt", {})
    settings = config.get("seamless_package", {})
    textures_dir = resolve_path(paths.get("textures_dir", "textures"))
    output_root = resolve_path(
        settings.get("output_dir", chatgpt.get("output_dir", "output/chatgpt"))
    )
    filename = str(settings.get("filename", "seamless_texture.png")).strip()
    if Path(filename).name != filename or not filename.lower().endswith(".png"):
        raise ValueError("seamless_package.filename must be one local .png filename.")
    return settings, textures_dir, output_root, filename


def find_sku_source_folder(sku, project_dir=None):
    """Detect parent folder name (e.g. DEHQ) if SKU exists inside textures_raw/<folder> or textures_cropped/<folder>."""
    p_dir = Path(project_dir) if project_dir else PROJECT_DIR
    for base_name in ("textures_raw", "textures_cropped"):
        base_dir = p_dir / base_name
        if base_dir.exists() and base_dir.is_dir():
            for sub in base_dir.iterdir():
                if sub.is_dir():
                    for ext in SUPPORTED_EXTENSIONS:
                        if (sub / f"{sku}{ext}").exists():
                            return sub.name
    return None


def package_seamless_texture(sku, source_path, folder=None, force=False, config=None):
    config = config or load_config()
    settings, textures_dir, output_root, filename = packaging_paths(config)
    if not bool(settings.get("enabled", True)):
        return {"status": "disabled", "path": None}

    sku = validate_sku(sku)
    source_path = Path(source_path)
    source_info = inspect_image(source_path)

    # Detect folder from source_path or find in source folders
    if not folder:
        try:
            rel = source_path.parent.relative_to(textures_dir)
            if str(rel) != ".":
                folder = str(rel)
        except Exception:
            pass
    if not folder:
        folder = find_sku_source_folder(sku, project_dir=textures_dir.parent)

    if folder and str(folder) != ".":
        destination = output_root / folder / sku / filename
    else:
        destination = output_root / sku / filename

    if destination.exists():
        destination_valid = True
        try:
            inspect_image(destination)
        except Exception:
            destination_valid = False

        if destination_valid:
            try:
                src_stat = source_path.stat()
                dst_stat = destination.stat()
                if src_stat.st_size == dst_stat.st_size and src_stat.st_size > 0:
                    return {
                        "status": "unchanged",
                        "path": destination,
                        "sha256": None,
                        "image": source_info,
                    }
            except Exception:
                pass

        source_hash = sha256_file(source_path)
        destination_hash = sha256_file(destination) if destination_valid else None
        if destination_valid and destination_hash == source_hash:
            return {
                "status": "unchanged",
                "path": destination,
                "sha256": source_hash,
                "image": source_info,
            }
        if not force and not bool(settings.get("overwrite_when_changed", False)):
            return {
                "status": "conflict",
                "path": destination,
                "sha256": source_hash,
                "image": source_info,
                "destination_valid": destination_valid,
            }

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        if source_info["format"] == "PNG":
            shutil.copyfile(source_path, temporary)
        else:
            with Image.open(source_path) as image:
                image.load()
                image.save(temporary, format="PNG")
        inspect_image(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "status": "packaged",
        "path": destination,
        "sha256": sha256_file(destination),
        "image": source_info,
    }


def package_and_report(sku, source_path, folder=None, force=False, verbose=True):
    try:
        result = package_seamless_texture(sku, source_path, folder=folder, force=force)
    except Exception as exc:
        print(f"  [WARNING] Could not package seamless texture for {sku}: {exc}")
        return {"status": "error", "path": None, "error": str(exc)}
    status = result["status"]
    path = result.get("path")
    if status == "packaged":
        print(f"  Packaged seamless texture for {sku}: {path}")
    elif status == "unchanged" and verbose:
        print(f"  Packaged seamless texture is already current: {path}")
    elif status == "conflict":
        print(
            f"  [WARNING] Packaged texture differs for {sku}: {path}. "
            "Run package_seamless_textures.py --sku "
            f"{sku} --force to replace it."
        )
    return result


def discover_textures(textures_dir, selected_sku=None, selected_folder=None):
    keyed = {}
    duplicates = {}
    if not textures_dir.exists():
        return []
    
    # If selected_folder is specified and exists as subfolder, scan only that subfolder
    search_dir = textures_dir / selected_folder if selected_folder and (textures_dir / selected_folder).is_dir() else textures_dir
    
    for path in sorted(search_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        match = TEXTURE_PATTERN.fullmatch(path.name)
        if not match:
            continue
        sku = match.group(1)
        if selected_sku and sku != selected_sku:
            continue
        try:
            rel = path.parent.relative_to(textures_dir)
            folder = str(rel) if str(rel) != "." else None
        except Exception:
            folder = None
        if not folder:
            folder = find_sku_source_folder(sku, project_dir=textures_dir.parent)

        if selected_folder and folder and folder.casefold() != selected_folder.casefold():
            continue

        # If folder exists and path is at textures_dir root, copy to textures/<folder>/
        if folder and path.parent == textures_dir:
            nested_dir = textures_dir / folder
            nested_dir.mkdir(parents=True, exist_ok=True)
            nested_file = nested_dir / path.name
            if not nested_file.exists():
                try:
                    shutil.copy2(path, nested_file)
                except Exception:
                    pass

        folded = sku.casefold()
        if folded in keyed:
            existing_sku, existing_path, existing_folder = keyed[folded]
            # If one is nested and the other is root, prefer the nested one
            if existing_path.parent == textures_dir and path.parent != textures_dir:
                keyed[folded] = (sku, path, folder)
            elif existing_path.parent != textures_dir and path.parent == textures_dir:
                pass
            elif existing_path == path or existing_path.name == path.name:
                pass
            else:
                duplicates.setdefault(folded, [keyed[folded]]).append((sku, path, folder))
        else:
            keyed[folded] = (sku, path, folder)
    if duplicates:
        details = "; ".join(
            ", ".join(p.name for _, p, _ in paths) for paths in duplicates.values()
        )
        raise ValueError(f"Multiple texture files map to the same SKU: {details}")
    return sorted(keyed.values(), key=lambda item: item[0].casefold())


def main():
    parser = argparse.ArgumentParser(
        description="Copy canonical seamless textures into output/chatgpt/<SKU>."
    )
    parser.add_argument("--sku", help="Package one exact SKU")
    parser.add_argument("--sku-file", help="JSON file containing exact SKU names to package")
    parser.add_argument("--folder", help="Package textures inside a specific folder")
    parser.add_argument("--limit", type=int, help="Package at most this many textures")
    parser.add_argument("--dry-run", action="store_true", help="Show planned outputs")
    parser.add_argument("--force", action="store_true", help="Replace a changed package")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    config = load_config()
    _, textures_dir, output_root, filename = packaging_paths(config)
    items = discover_textures(textures_dir, selected_sku=args.sku, selected_folder=args.folder)
    if args.sku_file:
        with open(args.sku_file, "r", encoding="utf-8") as handle:
            values = json.load(handle)
        if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
            parser.error("--sku-file must contain a JSON array of non-empty SKU names")
        selected_skus = {value.casefold() for value in values}
        items = [item for item in items if item[0].casefold() in selected_skus]
    if args.limit is not None:
        items = items[: args.limit]
    if not items:
        folder_msg = f" for folder '{args.folder}'" if args.folder else ""
        print(f"No matching seamless textures found in {textures_dir}{folder_msg}")
        return

    if args.dry_run:
        print("Seamless packages:")
        for sku, source, folder in items:
            dest = (output_root / folder / sku / filename) if folder else (output_root / sku / filename)
            print(f"  {source.name} -> {dest}")
        return

    failures = 0
    packaged_count = 0
    unchanged_count = 0
    conflict_count = 0
    verbose = bool(args.sku)
    for sku, source, folder in items:
        try:
            result = package_and_report(sku, source, folder=folder, force=args.force, verbose=verbose)
            st = result["status"]
            if st == "packaged":
                packaged_count += 1
            elif st == "unchanged":
                unchanged_count += 1
            elif st == "conflict":
                conflict_count += 1
            elif st == "error":
                failures += 1
        except Exception as exc:
            failures += 1
            print(f"  [ERROR] {sku}: {exc}")
    if not verbose:
        print(f"Seamless packaging complete: {packaged_count} newly packaged, {unchanged_count} already current, {conflict_count} kept existing.")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
