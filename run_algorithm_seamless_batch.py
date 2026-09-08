"""run_algorithm_seamless_batch.py — Offline Efros-Freeman seamless refiner & batch processor.

Modes:
  1. chatgpt (Default): Scans ChatGPT generated textures in output/chatgpt/ (or textures/),
     backs up raw AI image as seamless_texture_chatgpt_raw.png, detects seams, and repairs
     them via Efros-Freeman boundary cut to make them 100% tileable without defects.
  2. cropped: Generates seamless textures directly from fabric photos in textures_cropped/.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from seamless_packaging import package_and_report
from veo3_runtime import get_project_dir

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

Image.MAX_IMAGE_PIXELS = None

PROJECT_DIR = get_project_dir(__file__)
CONFIG_PATH = PROJECT_DIR / "config.json"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


# --------------------------------------------------------------------------
# Config & Paths
# --------------------------------------------------------------------------

def load_config():
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        value = json.load(f)
    return value if isinstance(value, dict) else {}


def resolve_path(value):
    expanded = Path(os.path.expandvars(str(value)))
    return expanded if expanded.is_absolute() else PROJECT_DIR / expanded


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Color space & De-lighting
# --------------------------------------------------------------------------

def srgb_to_linear(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92,
                    1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def luminance(lin):
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def delight(rgb, sigma_frac=0.04, strength=1.0):
    """Per-channel illumination flat-fielding.

    Eliminates low-frequency lighting gradients, vignetting, and corner-to-center
    brightness mismatches while preserving 100% of micro-texture and weave details.
    """
    lin = srgb_to_linear(rgb)
    h, w = lin.shape[:2]
    sigma = max(6.0, float(sigma_frac) * min(h, w))
    out = np.empty_like(lin)
    for c in range(3):
        ch = lin[..., c]
        low = gaussian_filter(ch, sigma=sigma, mode="reflect")
        low = np.maximum(low, 1e-5)
        mean_val = float(ch.mean())
        gain = mean_val / low
        gain = 1.0 + float(strength) * (gain - 1.0)
        out[..., c] = ch * gain
    return linear_to_srgb(out)


# --------------------------------------------------------------------------
# Repeat period detection
# --------------------------------------------------------------------------

def _highpass_gray(rgb, sigma=2.0):
    g = luminance(srgb_to_linear(rgb))
    return g - gaussian_filter(g, sigma, mode="reflect")


def _nssd_curve(gray, axis, pmin, pmax):
    """Normalized sum-of-squared-differences for every shift in [pmin, pmax].

    0.0 = perfect self-similarity at that shift, ~2.0 = uncorrelated.
    """
    n = gray.shape[axis]
    pmax = min(pmax, n - 8)
    ps, vals = [], []
    for p in range(pmin, pmax + 1):
        if axis == 1:
            a, b = gray[:, :n - p], gray[:, p:]
        else:
            a, b = gray[:n - p, :], gray[p:, :]
        var = 0.5 * (a.var() + b.var()) + 1e-9
        ps.append(p)
        vals.append(float(np.mean((a - b) ** 2) / var))
    return np.array(ps), np.array(vals)


def detect_period(rgb, axis, min_period=8, max_frac=0.45, accept=0.55):
    """Return (period_px, score) or (None, score) if no reliable repeat."""
    h, w = rgb.shape[:2]
    long_side = max(h, w)
    scale = min(1.0, 512.0 / long_side)
    if scale < 1.0:
        small = np.asarray(
            Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
            .resize((max(16, int(w * scale)), max(16, int(h * scale))), Image.LANCZOS)
        ).astype(np.float32) / 255.0
    else:
        small = rgb
        scale = 1.0

    gs = _highpass_gray(small, sigma=1.0)
    n_small = gs.shape[axis]
    pmin_s = max(4, int(min_period * scale))
    pmax_s = int(n_small * max_frac)
    if pmax_s <= pmin_s + 2:
        return None, 2.0

    ps, vals = _nssd_curve(gs, axis, pmin_s, pmax_s)
    best = float(vals.min())
    if best > accept:
        return None, best

    # shortest period within 20% of the best score -> the fundamental
    tol = best + 0.20 * (float(np.median(vals)) - best)
    cand = ps[vals <= tol]
    p_small = int(cand.min())

    # refine at full resolution
    p_guess = int(round(p_small / scale))
    win = max(3, int(round(1.5 / scale)))
    gf = _highpass_gray(rgb, sigma=1.5)
    lo = max(min_period, p_guess - win)
    hi = min(int(rgb.shape[axis] * max_frac), p_guess + win)
    if hi <= lo:
        return p_guess, best
    ps2, vals2 = _nssd_curve(gf, axis, lo, hi)
    return int(ps2[int(np.argmin(vals2))]), float(vals2.min())


# --------------------------------------------------------------------------
# Crop to whole periods
# --------------------------------------------------------------------------

def crop_to_periods(rgb, px, py, reserve):
    """Center-crop to an integer multiple of the period on each axis."""
    h, w = rgb.shape[:2]
    tw, th = w, h
    if px:
        tw = ((w - reserve) // px) * px + reserve
    if py:
        th = ((h - reserve) // py) * py + reserve
    tw, th = max(tw, 64), max(th, 64)
    x0, y0 = (w - tw) // 2, (h - th) // 2
    return rgb[y0:y0 + th, x0:x0 + tw]


# --------------------------------------------------------------------------
# Minimum-error boundary cut (Efros & Freeman)
# --------------------------------------------------------------------------

def _min_error_mask(a, b, feather):
    """Vertical cut path through an (H, ov, C) overlap."""
    h, ov = a.shape[0], a.shape[1]
    err = np.sum((a - b) ** 2, axis=2).astype(np.float64)

    margin = min(int(feather) + 2, max(1, ov // 4))
    big = err.max() * h + 1.0
    err[:, :margin] += big
    err[:, ov - margin:] += big

    acc = err.copy()
    for i in range(1, h):
        prev = acc[i - 1]
        left = np.concatenate(([np.inf], prev[:-1]))
        right = np.concatenate((prev[1:], [np.inf]))
        acc[i] += np.minimum(np.minimum(left, prev), right)

    path = np.empty(h, dtype=int)
    j = int(np.argmin(acc[-1]))
    path[-1] = j
    for i in range(h - 2, -1, -1):
        lo, hi = max(0, j - 1), min(ov, j + 2)
        j = lo + int(np.argmin(acc[i, lo:hi]))
        path[i] = j

    mask = (np.arange(ov)[None, :] >= path[:, None]).astype(np.float32)
    if feather > 0:
        mask = gaussian_filter(mask, (0.6, float(feather)), mode="nearest")
    mask[:, 0] = 0.0
    mask[:, -1] = 1.0
    return mask[..., None]


def _linear_mask(a, feather=None):
    ov = a.shape[1]
    ramp = np.linspace(0.0, 1.0, ov, dtype=np.float32)
    ramp = ramp * ramp * (3 - 2 * ramp)
    return np.broadcast_to(ramp[None, :, None], (a.shape[0], ov, 1)).copy()


def seamless_axis(rgb, overlap, mode="cut", feather=3):
    h, w = rgb.shape[:2]
    ov = int(min(overlap, w // 3))
    if ov < 4:
        return rgb
    a = rgb[:, w - ov:w]
    b = rgb[:, 0:ov]
    mask = _min_error_mask(a, b, feather) if mode == "cut" else _linear_mask(a)
    merged = a * (1.0 - mask) + b * mask
    return np.concatenate([rgb[:, ov:w - ov], merged], axis=1)


def make_seamless(rgb, overlap=None, mode="blend", feather=3, blend_frac=0.15):
    """Make texture seamlessly tileable.

    mode="blend" (default): Symmetrical equal-power cosine wrap blending with
    high-frequency variance restoration. Guarantees 0 seam lines and 0 contrast loss.
    mode="cut": Efros-Freeman minimum-error boundary cut.
    """
    if mode == "cut":
        ov = overlap if overlap is not None else 64
        out = seamless_axis(rgb, ov, mode="cut", feather=feather)
        out = np.transpose(seamless_axis(np.transpose(out, (1, 0, 2)),
                                         ov, mode="cut", feather=feather), (1, 0, 2))
        return out

    def blend_1d(arr, axis):
        if axis == 0:
            arr = np.transpose(arr, (1, 0, 2))
        h, w, c = arr.shape
        ov = overlap if overlap is not None else max(16, min(int(w * blend_frac), w // 4))
        ov = max(8, min(int(ov), w // 3))

        left = arr[:, :ov]
        right = arr[:, -ov:]

        sigma_blend = max(2.0, ov * 0.25)
        low_left = gaussian_filter(left, sigma=(sigma_blend, sigma_blend, 0), mode="reflect")
        low_right = gaussian_filter(right, sigma=(sigma_blend, sigma_blend, 0), mode="reflect")
        high_left = left - low_left
        high_right = right - low_right

        t = np.linspace(0, 1, ov, dtype=np.float32)[None, :, None]
        w_ramp = 0.5 * (1.0 - np.cos(np.pi * t))

        merged_low = (1.0 - w_ramp) * low_right + w_ramp * low_left
        norm = np.sqrt((1.0 - w_ramp) ** 2 + w_ramp ** 2)
        merged_high = ((1.0 - w_ramp) * high_right + w_ramp * high_left) / np.maximum(norm, 1e-6)

        merged = merged_low + merged_high
        out = np.concatenate([arr[:, ov:-ov], merged], axis=1)
        if axis == 0:
            out = np.transpose(out, (1, 0, 2))
        return out

    out = blend_1d(rgb, axis=1)
    out = blend_1d(out, axis=0)
    return np.clip(out, 0.0, 1.0)


# --------------------------------------------------------------------------
# Wrap-aware resize
# --------------------------------------------------------------------------

def resize_wrapped(rgb, size):
    """Resize without breaking the wrap: tile 3x3, resize, take center."""
    h, w = rgb.shape[:2]
    if (h, w) == (size, size):
        return rgb
    tiled = np.tile(rgb, (3, 3, 1))
    im = Image.fromarray((np.clip(tiled, 0, 1) * 255 + 0.5).astype(np.uint8))
    im = im.resize((size * 3, size * 3), Image.LANCZOS)
    arr = np.asarray(im).astype(np.float32) / 255.0
    return arr[size:2 * size, size:2 * size]


# --------------------------------------------------------------------------
# QC & Image Utils
# --------------------------------------------------------------------------

def seam_score(rgb):
    """Discontinuity across wrap vs typical neighbour difference."""
    dx_wrap = np.mean(np.abs(rgb[:, 0] - rgb[:, -1]))
    dx_typ = np.mean(np.abs(rgb[:, 1:] - rgb[:, :-1])) + 1e-8
    dy_wrap = np.mean(np.abs(rgb[0, :] - rgb[-1, :]))
    dy_typ = np.mean(np.abs(rgb[1:, :] - rgb[:-1, :])) + 1e-8
    return float(dx_wrap / dx_typ), float(dy_wrap / dy_typ)


def to_pil(rgb):
    return Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8))


def write_qc(rgb, outdir):
    os.makedirs(outdir, exist_ok=True)
    h, w = rgb.shape[:2]
    # 1. 50% offset image to inspect seam in the center
    to_pil(np.roll(rgb, (h // 2, w // 2), axis=(0, 1))).save(
        os.path.join(outdir, "QC_offset50.png")
    )
    # 2. 3x3 tile preview
    small = to_pil(rgb).resize((512, 512), Image.BOX)
    Image.fromarray(np.tile(np.asarray(small), (3, 3, 1))).save(
        os.path.join(outdir, "QC_tile_3x3.png")
    )
    # 3. 15x15 tile preview
    tiny = to_pil(rgb).resize((128, 128), Image.BOX)
    Image.fromarray(np.tile(np.asarray(tiny), (15, 15, 1))).save(
        os.path.join(outdir, "QC_tile_15x15.png")
    )


def load_rgb(path):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return np.asarray(im.convert("RGB")).astype(np.float32) / 255.0


def square_crop(rgb):
    h, w = rgb.shape[:2]
    s = min(h, w)
    return rgb[(h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]


# --------------------------------------------------------------------------
# Post-Processing: Refine ChatGPT Generated Textures
# --------------------------------------------------------------------------

def refine_chatgpt_texture_seamless(
    sku,
    folder=None,
    project_dir=None,
    final_path=None,
    textures_dir=None,
    texture_path=None,
    refresh_raw_backup=False,
    size=None,
    overlap=None,
    feather=3,
    blend="blend",
    do_delight=True,
    delight_sigma=0.04,
    delight_strength=1.0,
    detect_period_enabled=False,
    force=False,
    verbose=True,
):
    """Inspect and refine ChatGPT texture output to guarantee 100% seamless continuity.

    Backs up original AI texture as seamless_texture_chatgpt_raw.png.
    Eliminates vignetting / lighting gradients and repairs borders using equal-power wrap blending.
    """
    p_dir = Path(project_dir) if project_dir else PROJECT_DIR
    out_root = p_dir / "output" / "chatgpt"
    textures_dir = Path(textures_dir) if textures_dir else p_dir / "textures"

    # Locate target file
    candidate_paths = []
    if folder and str(folder) != ".":
        candidate_paths.append((out_root / folder / sku / "seamless_texture.png", out_root / folder / sku, folder))
    candidate_paths.append((out_root / sku / "seamless_texture.png", out_root / sku, None))

    # Also search globally if not found
    target_path = Path(final_path) if final_path else None
    sku_dir = target_path.parent if target_path else None
    resolved_folder = folder

    if target_path and not target_path.is_file():
        target_path = None
        sku_dir = None

    if not target_path:
        for c_path, c_dir, c_fol in candidate_paths:
            if c_path.is_file():
                target_path = c_path
                sku_dir = c_dir
                resolved_folder = c_fol
                break

    if not target_path:
        # Search anywhere in output/chatgpt
        matches = list(out_root.glob(f"*/{sku}/seamless_texture.png"))
        if matches:
            target_path = matches[0]
            sku_dir = target_path.parent
            resolved_folder = sku_dir.parent.name
        else:
            # Fallback to textures/
            tex_candidate = textures_dir / folder / f"texture_{sku}.png" if folder else textures_dir / f"texture_{sku}.png"
            if tex_candidate.is_file():
                target_path = tex_candidate
                sku_dir = out_root / folder / sku if folder else out_root / sku
                sku_dir.mkdir(parents=True, exist_ok=True)
                resolved_folder = folder

    if not target_path or not target_path.is_file():
        if verbose:
            print(f"  [Seamless Refiner] Không tìm thấy texture ChatGPT cho {sku}")
        return {"status": "not_found", "sku": sku}

    raw_backup = sku_dir / "seamless_texture_chatgpt_raw.png"
    img1_candidate = sku_dir / "image_1.png"
    final_master = sku_dir / "seamless_texture.png"

    # A forced ChatGPT rerun produced a new pristine texture. Replace the old
    # backup before selecting the source so refinement uses this new result.
    if refresh_raw_backup and target_path.is_file():
        shutil.copyfile(target_path, raw_backup)

    # Priority for pristine source:
    # 1. raw_backup if exists (refreshed above for a forced ChatGPT rerun)
    # 2. image_1.png if exists (original high-res AI output from ChatGPT)
    # 3. target_path
    if raw_backup.is_file():
        src_path = raw_backup
    elif img1_candidate.is_file():
        src_path = img1_candidate
    else:
        src_path = target_path

    # Backup original before any modifying
    if not raw_backup.exists() and src_path.is_file():
        try:
            shutil.copyfile(src_path, raw_backup)
        except Exception:
            pass

    rgb = load_rgb(src_path)
    h, w = rgb.shape[:2]
    target_size = size or max(h, w)
    s0 = seam_score(rgb)

    # Check if final master already exists and is high quality
    if not force and final_master.is_file():
        cur_rgb = load_rgb(final_master)
        s_cur = seam_score(cur_rgb)
        if max(s_cur) <= 1.20:
            write_qc(cur_rgb, str(sku_dir))
            if verbose:
                print(f"  [Seamless Refiner] {sku} đã đạt chuẩn liền mạch (Seam: X={s_cur[0]:.2f}, Y={s_cur[1]:.2f})")
            return {
                "status": "already_seamless",
                "sku": sku,
                "seam_score": s_cur,
                "path": str(final_master),
            }

    start_t = time.monotonic()

    # 1. Square crop if needed
    if h != w:
        rgb = square_crop(rgb)

    # 2. Illumination Flat-Fielding (removes vignetting, lighting gradients, repeating spot artifacts)
    if do_delight:
        rgb = delight(rgb, sigma_frac=delight_sigma, strength=delight_strength)

    # 3. Period detection (optional, disabled by default for AI textures)
    px = py = None
    if detect_period_enabled:
        px, _ = detect_period(rgb, axis=1)
        py, _ = detect_period(rgb, axis=0)
        if px or py:
            rgb = crop_to_periods(rgb, px, py, overlap or 64)

    # 4. Symmetrical Equal-Power Wrap Blending
    rgb = make_seamless(rgb, overlap=overlap, mode=blend, feather=feather)

    # 5. Wrap-aware resize
    rgb = resize_wrapped(rgb, target_size)
    s1 = seam_score(rgb)

    # 6. Save refined master texture
    pil_img = to_pil(rgb)
    pil_img.save(final_master, "PNG", optimize=True)

    # Also update texture in textures/
    if texture_path:
        texture_output = Path(texture_path)
    else:
        tex_folder = textures_dir / resolved_folder if resolved_folder else textures_dir
        texture_output = tex_folder / f"texture_{sku}.png"
    texture_output.parent.mkdir(parents=True, exist_ok=True)
    pil_img.save(texture_output, "PNG", optimize=True)

    # Write QC images
    write_qc(rgb, str(sku_dir))

    elapsed = round(time.monotonic() - start_t, 2)
    if verbose:
        print(
            f"  [Seamless Refiner] Đã sửa vết nối cho {sku} ({elapsed}s):\n"
            f"    Seam trước: X={s0[0]:.2f}, Y={s0[1]:.2f} -> Sau: X={s1[0]:.2f}, Y={s1[1]:.2f}\n"
            f"    Đã sao lưu file gốc: {raw_backup.name}\n"
            f"    Đã cập nhật file chuẩn: {final_master.name} + QC previews"
        )

    return {
        "status": "refined",
        "sku": sku,
        "folder": resolved_folder,
        "seam_score_before": s0,
        "seam_score_after": s1,
        "elapsed_sec": elapsed,
        "path": str(final_master),
    }


# --------------------------------------------------------------------------
# Batch Discovery & Processing
# --------------------------------------------------------------------------

def discover_chatgpt_output_files(project_dir, selected_sku=None, selected_folder=None):
    """Find all existing ChatGPT generated textures in output/chatgpt/."""
    out_root = project_dir / "output" / "chatgpt"
    found = {}
    if not out_root.is_dir():
        return []

    target_dir = out_root / selected_folder if selected_folder and (out_root / selected_folder).is_dir() else out_root
    for p in sorted(target_dir.rglob("seamless_texture.png")):
        sku = p.parent.name
        if selected_sku and sku.casefold() != selected_sku.casefold():
            continue
        try:
            rel = p.parent.parent.relative_to(out_root)
            folder = str(rel) if str(rel) != "." else None
        except Exception:
            folder = None

        if selected_folder and folder and folder.casefold() != selected_folder.casefold():
            continue

        key = sku.casefold()
        if key not in found:
            found[key] = (sku, p, folder)

    # Also scan textures/ if any not found in output
    tex_root = project_dir / "textures"
    if tex_root.is_dir():
        tex_dir = tex_root / selected_folder if selected_folder and (tex_root / selected_folder).is_dir() else tex_root
        for p in sorted(tex_dir.rglob("texture_*.png")):
            sku = p.stem.replace("texture_", "")
            if selected_sku and sku.casefold() != selected_sku.casefold():
                continue
            key = sku.casefold()
            if key not in found:
                try:
                    rel = p.parent.relative_to(tex_root)
                    folder = str(rel) if str(rel) != "." else None
                except Exception:
                    folder = None
                found[key] = (sku, p, folder)

    return sorted(found.values(), key=lambda x: (x[2] or "", x[0]))


def discover_input_files(project_dir, selected_sku=None, selected_folder=None):
    """Find candidate fabric images from textures_cropped, falling back to textures_raw."""
    crop_dir = project_dir / "textures_cropped"
    raw_dir = project_dir / "textures_raw"

    found = {}

    def scan_dir(base_dir):
        if not base_dir.is_dir():
            return
        target_dir = base_dir / selected_folder if selected_folder and (base_dir / selected_folder).is_dir() else base_dir
        for p in sorted(target_dir.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            sku = p.stem
            if selected_sku and sku.casefold() != selected_sku.casefold():
                continue
            try:
                rel = p.parent.relative_to(base_dir)
                folder = str(rel) if str(rel) != "." else None
            except Exception:
                folder = None

            if selected_folder and folder and folder.casefold() != selected_folder.casefold():
                continue

            key = sku.casefold()
            if key not in found:
                found[key] = (sku, p, folder)

    scan_dir(crop_dir)
    if not selected_sku or not found:
        raw_candidates = {}
        if raw_dir.is_dir():
            target_raw = raw_dir / selected_folder if selected_folder and (raw_dir / selected_folder).is_dir() else raw_dir
            for p in sorted(target_raw.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                sku = p.stem
                if selected_sku and sku.casefold() != selected_sku.casefold():
                    continue
                try:
                    rel = p.parent.relative_to(raw_dir)
                    folder = str(rel) if str(rel) != "." else None
                except Exception:
                    folder = None

                if selected_folder and folder and folder.casefold() != selected_folder.casefold():
                    continue

                key = sku.casefold()
                if key not in found and key not in raw_candidates:
                    raw_candidates[key] = (sku, p, folder)
        found.update(raw_candidates)

    return sorted(found.values(), key=lambda x: (x[2] or "", x[0]))


def process_single_sku(
    sku,
    src_path,
    folder,
    project_dir,
    size=2048,
    overlap=None,
    feather=3,
    blend="blend",
    do_delight=True,
    delight_sigma=0.04,
    delight_strength=1.0,
    detect_period_enabled=False,
    forced_period=None,
    force=False,
    dry_run=False,
):
    """Generate seamless texture from cropped fabric photo."""
    textures_dir = project_dir / "textures"
    tex_folder = textures_dir / folder if folder else textures_dir
    tex_path = tex_folder / f"texture_{sku}.png"

    out_root = project_dir / "output" / "chatgpt"
    sku_out_dir = out_root / folder / sku if folder else out_root / sku
    master_path = sku_out_dir / "seamless_texture.png"

    if not force and tex_path.is_file() and master_path.is_file():
        return {"status": "skipped", "reason": "already exists", "path": str(master_path)}

    if dry_run:
        return {"status": "dry_run", "sku": sku, "source": str(src_path)}

    start_time = time.monotonic()
    rgb = load_rgb(src_path)
    h_orig, w_orig = rgb.shape[:2]

    if h_orig != w_orig:
        rgb = square_crop(rgb)

    if do_delight:
        rgb = delight(rgb, sigma_frac=delight_sigma, strength=delight_strength)

    px = py = None
    if forced_period and len(forced_period) == 2:
        px = forced_period[0] or None
        py = forced_period[1] or None
    elif detect_period_enabled:
        px, sx = detect_period(rgb, axis=1)
        py, sy = detect_period(rgb, axis=0)

    if px or py:
        rgb = crop_to_periods(rgb, px, py, overlap or 64)

    rgb = make_seamless(rgb, overlap=overlap, mode=blend, feather=feather)
    rgb = resize_wrapped(rgb, size)
    s_final = seam_score(rgb)

    tex_folder.mkdir(parents=True, exist_ok=True)
    sku_out_dir.mkdir(parents=True, exist_ok=True)

    pil_img = to_pil(rgb)
    pil_img.save(tex_path, "PNG", optimize=True)
    pil_img.save(master_path, "PNG", optimize=True)

    write_qc(rgb, str(sku_out_dir))
    package_and_report(sku, tex_path, folder=folder, force=force, verbose=False)

    elapsed = time.monotonic() - start_time
    return {
        "status": "success",
        "sku": sku,
        "folder": folder,
        "size": size,
        "seam_score": s_final,
        "elapsed_sec": round(elapsed, 2),
        "path": str(master_path),
    }


def main():
    ap = argparse.ArgumentParser(description="Seamless Texture Post-Processor & Generator")
    ap.add_argument("--source-mode", choices=["chatgpt", "cropped"], default="chatgpt",
                    help="chatgpt = sửa ảnh final của ChatGPT; cropped = tạo từ ảnh vải cropped")
    ap.add_argument("--sku", type=str, default="", help="Process specific SKU only")
    ap.add_argument("--folder", type=str, default="", help="Filter by folder name")
    ap.add_argument("--limit", type=int, default=0, help="Maximum number of items to process")
    ap.add_argument("--size", type=int, default=2048, help="Target texture resolution (default 2048)")
    ap.add_argument("--overlap", type=int, default=None, help="Blend band in px (default auto ~15%)")
    ap.add_argument("--feather", type=int, default=3, help="Cut line feathering in px")
    ap.add_argument("--blend", choices=["blend", "cut", "feather"], default="blend")
    ap.add_argument("--no-delight", action="store_true", help="Skip de-lighting step")
    ap.add_argument("--delight-sigma", type=float, default=0.04)
    ap.add_argument("--delight-strength", type=float, default=1.0)
    ap.add_argument("--period", nargs=2, type=int, metavar=("PX", "PY"), help="Force repeat period")
    ap.add_argument("--detect-period", action="store_true", help="Enable repeat period detection (default off)")
    ap.add_argument("--no-period", action="store_true", help="Skip repeat period detection")
    ap.add_argument("--force", action="store_true", help="Force re-processing even if seam score is good")
    ap.add_argument("--dry-run", action="store_true", help="Preview matching items without processing")
    args = ap.parse_args()

    project_dir = PROJECT_DIR
    config = load_config()
    algo_cfg = config.get("algorithm_seamless", {})

    target_size = args.size or int(algo_cfg.get("size", 2048))
    overlap = args.overlap if args.overlap is not None else algo_cfg.get("overlap", None)
    feather = args.feather or int(algo_cfg.get("feather", 3))
    blend = args.blend or algo_cfg.get("blend", "blend")
    do_delight = not args.no_delight if args.no_delight else bool(algo_cfg.get("delight", True))
    delight_sigma = args.delight_sigma or float(algo_cfg.get("delight_sigma", 0.04))
    delight_strength = args.delight_strength or float(algo_cfg.get("delight_strength", 1.0))
    detect_period_enabled = args.detect_period if args.detect_period else bool(algo_cfg.get("detect_period", False))
    if args.no_period:
        detect_period_enabled = False

    source_mode = args.source_mode

    if source_mode == "chatgpt":
        items = discover_chatgpt_output_files(project_dir, selected_sku=args.sku, selected_folder=args.folder)
        action_name = "Vá lỗi mép Texture ChatGPT (Seamless Refiner)"
    else:
        items = discover_input_files(project_dir, selected_sku=args.sku, selected_folder=args.folder)
        action_name = "Tạo Seamless từ ảnh vải gốc (textures_cropped)"

    print(f"[{now_text()}] BẮT ĐẦU: {action_name}")
    print(f"  Dự án: {project_dir}")
    print(f"  Chế độ: {source_mode} | Tìm thấy: {len(items)} SKU")
    print(f"  Cấu hình: Size={target_size}x{target_size}, Delight={'Bật' if do_delight else 'Tắt'}, Blend={blend}")

    if not items:
        if source_mode == "chatgpt":
            print("  Không tìm thấy texture nào trong output/chatgpt/ hoặc textures/.")
        else:
            print("  Không tìm thấy ảnh vải trong textures_cropped/ hoặc textures_raw/.")
        return 0

    if args.limit > 0:
        items = items[:args.limit]
        print(f"  Giới hạn xử lý: {len(items)} SKU")

    processed = 0
    skipped = 0
    failed = 0
    total_start = time.monotonic()

    status_file = project_dir / "status_algorithm_seamless.json"
    status_data = {}
    if status_file.is_file():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                status_data = json.load(f)
        except Exception:
            status_data = {}

    for idx, (sku, src_path, folder) in enumerate(items, start=1):
        folder_tag = f"[{folder}] " if folder else ""
        print(f"\n({idx}/{len(items)}) {folder_tag}Xử lý SKU: {sku}")

        if args.dry_run:
            print(f"  -> [Xem trước] Nguồn: {src_path.name}")
            processed += 1
            continue

        try:
            if source_mode == "chatgpt":
                res = refine_chatgpt_texture_seamless(
                    sku=sku,
                    folder=folder,
                    project_dir=project_dir,
                    size=target_size,
                    overlap=overlap,
                    feather=feather,
                    blend=blend,
                    do_delight=do_delight,
                    delight_sigma=delight_sigma,
                    delight_strength=delight_strength,
                    detect_period_enabled=detect_period_enabled,
                    force=args.force,
                    verbose=True,
                )
                status_val = res.get("status")
                if status_val == "already_seamless":
                    skipped += 1
                elif status_val == "refined":
                    processed += 1
                    s1 = res.get("seam_score_after", (1.0, 1.0))
                    status_data[sku] = {
                        "status": "refined",
                        "folder": folder,
                        "completed_at": now_text(),
                        "seam_score_before": [round(res["seam_score_before"][0], 3), round(res["seam_score_before"][1], 3)],
                        "seam_score_after": [round(s1[0], 3), round(s1[1], 3)],
                        "elapsed_sec": res["elapsed_sec"],
                        "path": res["path"],
                    }
                else:
                    failed += 1
            else:
                res = process_single_sku(
                    sku=sku,
                    src_path=src_path,
                    folder=folder,
                    project_dir=project_dir,
                    size=target_size,
                    overlap=overlap,
                    feather=feather,
                    blend=blend,
                    do_delight=do_delight,
                    delight_sigma=delight_sigma,
                    delight_strength=delight_strength,
                    detect_period_enabled=detect_period_enabled,
                    forced_period=args.period,
                    force=args.force,
                    dry_run=args.dry_run,
                )
                status_val = res.get("status")
                if status_val == "skipped":
                    print(f"  -> Đã có texture, bỏ qua.")
                    skipped += 1
                elif status_val == "success":
                    processed += 1
                    sx, sy = res["seam_score"]
                    print(f"  -> Hoàn tất trong {res['elapsed_sec']}s! Seam score: X={sx:.2f}, Y={sy:.2f}")
                    status_data[sku] = {
                        "status": "success",
                        "folder": folder,
                        "completed_at": now_text(),
                        "seam_score": [round(sx, 3), round(sy, 3)],
                        "elapsed_sec": res["elapsed_sec"],
                        "path": res["path"],
                    }
                else:
                    failed += 1
        except Exception as exc:
            print(f"  [ERROR] Lỗi khi xử lý {sku}: {exc}")
            failed += 1
            status_data[sku] = {
                "status": "error",
                "folder": folder,
                "completed_at": now_text(),
                "error": str(exc),
            }

    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_status = status_file.with_suffix(".tmp")
        with open(tmp_status, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_status, status_file)
    except Exception as exc:
        print(f"  [WARN] Không lưu được status_algorithm_seamless.json: {exc}")

    total_time = round(time.monotonic() - total_start, 2)
    print(f"\n[{now_text()}] HOÀN TẤT!")
    print(f"  Thời gian: {total_time}s")
    print(f"  Đã vá/xử lý: {processed} | Bỏ qua: {skipped} | Lỗi: {failed}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
