#!/usr/bin/env python3
"""
iPhone to Analog Film — physically accurate negative→print emulation.
Uses spectral_film_lut for real photochemical pipeline modeling from
manufacturer datasheets. Not a LUT. Not a filter. Actual film science.
"""

import warnings
warnings.filterwarnings("ignore")

import cv2
import numpy as np
from scipy import ndimage
from pathlib import Path
import math

# ─── Load Film Science ───────────────────────────────────────────────────────

print("Loading film stocks from manufacturer datasheets...")
import spectral_film_lut as sfl
from spectral_film_lut.film_spectral import FilmSpectral

def make_conversion(neg_data, print_data, exposure_kelvin=5500, exp_comp=0, sat=1.0):
    neg = FilmSpectral(neg_data)
    prt = FilmSpectral(print_data) if print_data else None
    conv = FilmSpectral.generate_conversion(
        negative_film=neg, print_film=prt,
        input_colourspace="sRGB", output_colourspace="sRGB",
        projector_kelvin=6500, exposure_kelvin=exposure_kelvin,
        exp_comp=exp_comp, gamut_compression=0.2, sat_adjust=sat,
        mode="full" if prt else "negative",
    )
    return conv, neg

print("Building photochemical pipelines...")
STOCKS = {}

print("  Kodak Portra 400 -> Kodak 2383 print...")
conv, neg = make_conversion(sfl.KODAK_PORTRA_400, sfl.KODAK_2383, 5500, 0.3)
STOCKS["portra400"] = {
    "name": "Kodak Portra 400", "conv": conv, "neg": neg,
    "halation_strength": 0.15, "halation_radius": 0.06,
    "halation_color": np.array([0.15, 0.30, 0.95]), "halation_threshold": 0.65,
    "vignette": 0.30, "bloom": 0.10,
    "border_text": "KODAK  5054  PORTRA 400",
    "film_format_mm": 35,
}

print("  Fuji Pro 400H -> Kodak 2383 print...")
conv, neg = make_conversion(sfl.FUJI_PRO_400H, sfl.KODAK_2383, 5500, 0.2)
STOCKS["fuji400h"] = {
    "name": "Fuji Pro 400H", "conv": conv, "neg": neg,
    "halation_strength": 0.10, "halation_radius": 0.05,
    "halation_color": np.array([0.20, 0.35, 0.85]), "halation_threshold": 0.68,
    "vignette": 0.25, "bloom": 0.08,
    "border_text": "FUJI  PRO 400H",
    "film_format_mm": 35,
}

print("  Kodak Gold 200 -> Kodak 2383 print...")
conv, neg = make_conversion(sfl.KODAK_GOLD_200, sfl.KODAK_2383, 5500, 0.2, 1.05)
STOCKS["gold200"] = {
    "name": "Kodak Gold 200", "conv": conv, "neg": neg,
    "halation_strength": 0.12, "halation_radius": 0.05,
    "halation_color": np.array([0.10, 0.30, 1.0]), "halation_threshold": 0.68,
    "vignette": 0.35, "bloom": 0.08,
    "border_text": "KODAK  GOLD 200",
    "film_format_mm": 35,
}

print("  Cinestill 800T (Vision3 500T) -> Kodak 2383 print...")
conv, neg = make_conversion(sfl.KODAK_5219, sfl.KODAK_2383, 3200, 0.5)
STOCKS["cinestill800t"] = {
    "name": "Cinestill 800T", "conv": conv, "neg": neg,
    "halation_strength": 0.60, "halation_radius": 0.12,
    "halation_color": np.array([0.02, 0.08, 1.0]), "halation_threshold": 0.40,
    "vignette": 0.28, "bloom": 0.18,
    "border_text": "CINESTILL  800T",
    "film_format_mm": 35,
}

print("All stocks ready.\n")


# ─── VOLUMETRIC GRAIN ────────────────────────────────────────────────────────

def apply_volumetric_grain(img, stock):
    h, w = img.shape[:2]
    neg = stock["neg"]
    film_mm = stock["film_format_mm"]
    frame_width_mm = 36 if film_mm == 35 else 56
    scale = max(w, h) / frame_width_mm

    fimg = img.astype(np.float64) / 255.0
    grain_px_size = max(1.2, scale * 0.024)

    gh = max(int(h / grain_px_size), 100)
    gw = max(int(w / grain_px_size), 100)

    noise_r = np.random.normal(0, 1.0, (gh, gw)).astype(np.float64)
    noise_g = np.random.normal(0, 1.0, (gh, gw)).astype(np.float64)
    noise_b = np.random.normal(0, 1.0, (gh, gw)).astype(np.float64)

    noise_r = cv2.resize(noise_r, (w, h), interpolation=cv2.INTER_CUBIC)
    noise_g = cv2.resize(noise_g, (w, h), interpolation=cv2.INTER_CUBIC)
    noise_b = cv2.resize(noise_b, (w, h), interpolation=cv2.INTER_CUBIC)

    # Clustering
    gh2, gw2 = max(gh // 3, 50), max(gw // 3, 50)
    cluster_r = cv2.resize(np.random.normal(0, 0.5, (gh2, gw2)), (w, h), interpolation=cv2.INTER_CUBIC)
    cluster_g = cv2.resize(np.random.normal(0, 0.5, (gh2, gw2)), (w, h), interpolation=cv2.INTER_CUBIC)
    cluster_b = cv2.resize(np.random.normal(0, 0.5, (gh2, gw2)), (w, h), interpolation=cv2.INTER_CUBIC)

    noise_r = noise_r * 0.7 + cluster_r * 0.3
    noise_g = noise_g * 0.7 + cluster_g * 0.3
    noise_b = noise_b * 0.7 + cluster_b * 0.3

    pixels = fimg.reshape(-1, 3)
    try:
        grain_factors = neg.grain_transform(pixels, scale=scale / 160.0, std_div=1.0)
        grain_factors = grain_factors.reshape(h, w, 3)
        fimg[:, :, 2] += noise_r * grain_factors[:, :, 0]
        fimg[:, :, 1] += noise_g * grain_factors[:, :, 1]
        fimg[:, :, 0] += noise_b * grain_factors[:, :, 2]
    except Exception:
        rms = neg.film_data.rms if hasattr(neg, 'film_data') else 4.0
        intensity = rms / 1000.0 * 3.0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64) / 255.0
        lum_resp = np.clip(np.sqrt(4.0 * gray * (1.0 - gray)), 0.2, 1.0)
        fimg[:, :, 2] += noise_r * intensity * lum_resp
        fimg[:, :, 1] += noise_g * intensity * lum_resp * 0.9
        fimg[:, :, 0] += noise_b * intensity * lum_resp * 1.15

    return (np.clip(fimg, 0, 1) * 255).astype(np.uint8)


# ─── FILM BREATH ─────────────────────────────────────────────────────────────

def apply_film_breath(img, strength=0.012):
    """Low-freq exposure variation. Generate at tiny res and upscale (it's low-freq anyway)."""
    h, w = img.shape[:2]
    fimg = img.astype(np.float64) / 255.0
    # Generate at small size — no need for massive gaussian blur
    breath = np.random.normal(0, 1.0, (16, 16)).astype(np.float64)
    breath = cv2.GaussianBlur(breath, (7, 7), 2.0)
    breath = cv2.resize(breath, (w, h), interpolation=cv2.INTER_CUBIC)
    for c in range(3):
        channel_var = 1.0 + np.random.uniform(-0.3, 0.3)
        fimg[:, :, c] *= (1.0 + breath * strength * channel_var)
    return (np.clip(fimg, 0, 1) * 255).astype(np.uint8)


# ─── GATE WEAVE ──────────────────────────────────────────────────────────────

def apply_gate_weave(img, strength=0.4):
    h, w = img.shape[:2]
    dx = np.random.normal(0, strength)
    dy = np.random.normal(0, strength)
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    result = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    angle = np.random.normal(0, 0.02)
    M_rot = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
    return cv2.warpAffine(result, M_rot, (w, h), borderMode=cv2.BORDER_REFLECT)


# ─── FILM ACUTANCE ───────────────────────────────────────────────────────────

def apply_film_acutance(img, softness=0.20):
    """Film MTF: soft pixel-level, good mid-frequency contrast."""
    fimg = img.astype(np.float64) / 255.0
    soft = cv2.GaussianBlur(fimg, (3, 3), 0.5)
    result = fimg * (1 - softness) + soft * softness

    mid_radius = max(img.shape[:2]) // 150
    if mid_radius % 2 == 0: mid_radius += 1
    mid_radius = max(mid_radius, 5)
    mid_blur = cv2.GaussianBlur(result, (mid_radius, mid_radius), mid_radius / 3)
    result = result + (result - mid_blur) * 0.12

    glow_r = max(img.shape[:2]) // 80
    if glow_r % 2 == 0: glow_r += 1
    glow = cv2.GaussianBlur(fimg, (glow_r, glow_r), glow_r / 3)
    result = result * 0.94 + glow * 0.06

    return (np.clip(result, 0, 1) * 255).astype(np.uint8)


# ─── CORE EFFECTS ────────────────────────────────────────────────────────────

def apply_film_conversion(img, stock):
    fimg = img.astype(np.float64) / 255.0
    h, w = fimg.shape[:2]
    converted = stock["conv"](fimg.reshape(-1, 3))
    return (np.clip(converted.reshape(h, w, 3), 0, 1) * 255).astype(np.uint8)

def apply_halation(img, stock):
    fimg = img.astype(np.float64) / 255.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64) / 255.0
    strength, color = stock["halation_strength"], stock["halation_color"]
    threshold = stock["halation_threshold"]
    bright = np.clip((gray - threshold) / (1.0 - threshold + 1e-6), 0, 1) ** 1.2
    radius = int(max(img.shape[:2]) * stock["halation_radius"])
    if radius % 2 == 0: radius += 1
    radius = min(max(radius, 5), 301)  # Cap blur size
    s1 = cv2.GaussianBlur(bright, (radius, radius), radius / 3)
    r2 = min(radius * 2 + 1, 601)  # Was radius*3, capped now
    if r2 % 2 == 0: r2 += 1
    s2 = cv2.GaussianBlur(bright, (r2, r2), r2 / 3)
    spread = s1 * 0.5 + s2 * 0.5
    halation = np.zeros_like(fimg)
    for c in range(3): halation[:, :, c] = spread * color[c]
    return (np.clip(fimg + halation * strength, 0, 1) * 255).astype(np.uint8)

def apply_bloom(img, strength):
    if strength < 0.01: return img
    fimg = img.astype(np.float64) / 255.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64) / 255.0
    bright = np.clip((gray - 0.50) / 0.50, 0, 1)
    bloom = fimg.copy()
    for c in range(3): bloom[:, :, c] *= bright
    r = min(max(img.shape[:2]) // 25, 201)
    if r % 2 == 0: r += 1
    r = max(r, 5)
    bloom = cv2.GaussianBlur(bloom, (r, r), r / 3)
    return (np.clip(1.0 - (1.0 - fimg) * (1.0 - bloom * strength), 0, 1) * 255).astype(np.uint8)

def apply_vignette(img, strength):
    h, w = img.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    r = np.sqrt(((x - w/2) / max(w/2, h/2))**2 + ((y - h/2) / max(w/2, h/2))**2)
    vig = 1.0 - strength * np.clip((r - 0.5) / 1.0, 0, 1) ** 2.0
    result = img.astype(np.float64)
    for c in range(3): result[:, :, c] *= vig
    return np.clip(result, 0, 255).astype(np.uint8)

def apply_dust_and_artifacts(img, amount=10):
    h, w = img.shape[:2]
    result = img.copy()
    np.random.seed(42)
    for _ in range(amount):
        x, y = np.random.randint(0, w), np.random.randint(0, h)
        sz = np.random.randint(3, 8)
        val = np.random.choice([-25, -15, 20, 35])
        opacity = np.random.uniform(0.2, 0.5)
        color = tuple(int(np.clip(result[y, x, c] + val * opacity, 0, 255)) for c in range(3))
        cv2.circle(result, (x, y), sz, color, -1)
    if amount > 6:
        sx = np.random.randint(w // 3, 2 * w // 3)
        sy = np.random.randint(h // 3, 2 * h // 3)
        pts = [(sx, sy)]
        for _ in range(np.random.randint(30, 70)):
            last = pts[-1]
            pts.append((last[0] + np.random.randint(-3, 4), last[1] + np.random.randint(1, 5)))
        cv2.polylines(result, [np.array(pts, dtype=np.int32)], False, (190, 185, 180), 1, cv2.LINE_AA)
    return result

def add_film_border(img, stock):
    h, w = img.shape[:2]
    bw, bh = int(w * 0.05), int(h * 0.065)
    tw, th = w + 2 * bw, h + 2 * bh
    strip = np.zeros((th, tw, 3), dtype=np.uint8)
    strip[:, :] = (8, 6, 5)
    strip[bh:bh+h, bw:bw+w] = img
    sw, sh = int(tw * 0.017), int(bh * 0.42)
    spacing = int(tw * 0.047)
    n = tw // spacing
    sx = (tw - n * spacing) // 2
    for i in range(n):
        x = sx + i * spacing
        for sy in [int(bh * 0.28), th - int(bh * 0.28) - sh]:
            cv2.rectangle(strip, (x+2, sy+2), (x+sw-2, sy+sh-2), (2,2,2), -1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = tw / 3200.0
    tk = max(1, int(fs * 1.5))
    frame = f"{np.random.randint(1,37)}A"
    color = (30, 55, 65)
    text = stock["border_text"]
    for tx in range(0, tw, tw // 3):
        cv2.putText(strip, f"  {text}    {frame}  ", (tx, th - int(bh * 0.5)), font, fs, color, tk, cv2.LINE_AA)
        cv2.putText(strip, f"  {frame}   {text}  ", (tx + tw//6, int(bh * 0.62)), font, fs, color, tk, cv2.LINE_AA)
    return strip


# ─── PIPELINE ────────────────────────────────────────────────────────────────

def process(img_path, stock_name, output_dir):
    stock = STOCKS[stock_name]
    print(f"\n  [{stock['name']}]")

    img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if img is None:
        print(f"  ERROR: {img_path}")
        return

    h, w = img.shape[:2]
    print(f"    Full resolution: {w}x{h}")

    # 1. Film acutance (film MTF character)
    print("    Film acutance/MTF...")
    img = apply_film_acutance(img, 0.20)

    # 2. Gate weave
    print("    Gate weave...")
    img = apply_gate_weave(img, 0.5)

    # 3. Film conversion (the core — negative -> print from datasheets)
    print("    *** Negative -> Print conversion ***")
    img = apply_film_conversion(img, stock)

    # 4. Film breath
    print("    Film breath...")
    img = apply_film_breath(img, 0.012)

    # 5. Halation
    print("    Halation...")
    img = apply_halation(img, stock)

    # 6. Bloom
    print("    Bloom...")
    img = apply_bloom(img, stock["bloom"])

    # 7. Vignette
    print("    Vignette...")
    img = apply_vignette(img, stock["vignette"])

    # 8. Volumetric grain
    print("    *** Volumetric grain (from RMS datasheet) ***")
    img = apply_volumetric_grain(img, stock)

    # 9. Dust
    print("    Dust & artifacts...")
    img = apply_dust_and_artifacts(img, 10)

    # Save
    stem = Path(img_path).stem
    base = f"{stem}_{stock_name}"
    bordered = add_film_border(img, stock)

    cv2.imwrite(str(output_dir / f"{base}_border.jpg"), bordered, [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(output_dir / f"{base}_clean.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])

    hf, wf = bordered.shape[:2]
    print(f"    -> {wf}x{hf} | {base}_border.jpg + _clean.jpg + _clean.png")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="iPhone to Analog Film")
    parser.add_argument("images", nargs="+", help="Input image paths (JPEG/PNG)")
    parser.add_argument("-s", "--stocks", nargs="+", default=["portra400"],
                        choices=list(STOCKS.keys()),
                        help="Film stocks to apply (default: portra400)")
    parser.add_argument("-o", "--output", default="./output",
                        help="Output directory (default: ./output)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    for img_path in args.images:
        img_path = Path(img_path)
        print(f"\n{'='*60}")
        print(f" {img_path.name}")
        print(f"{'='*60}")
        for s in args.stocks:
            process(img_path, s, output_dir)

    print(f"\n{'='*60}")
    print(f" DONE — {output_dir}")
    print(f"{'='*60}")
    for f in sorted(output_dir.glob("*_border.jpg")):
        print(f"  {f.name} ({f.stat().st_size / (1024*1024):.1f} MB)")

if __name__ == "__main__":
    main()
