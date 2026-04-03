"""
Film processing pipeline — all effect functions and the main process() entry point.
"""

import cv2
import numpy as np
from pathlib import Path


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
    h, w = img.shape[:2]
    fimg = img.astype(np.float64) / 255.0
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


# ─── RGB LAYER MISREGISTRATION ────────────────────────────────────────────────

def apply_channel_misregistration(img, strength=0.4):
    """Film has 3 physical emulsion layers that aren't perfectly aligned.
    Subtle per-channel shifts — the #1 subconscious 'this is film' cue."""
    h, w = img.shape[:2]
    result = img.copy()

    # Each channel gets a tiny random sub-pixel shift
    for c in range(3):
        dx = np.random.uniform(-strength, strength)
        dy = np.random.uniform(-strength, strength)
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        result[:, :, c] = cv2.warpAffine(img[:, :, c], M, (w, h),
                                          borderMode=cv2.BORDER_REFLECT)
    return result


# ─── HIGHLIGHT ROLLOFF ────────────────────────────────────────────────────────

def apply_highlight_rolloff(img, shoulder=0.82, strength=0.6):
    """Film doesn't clip to white — highlights compress with color retention.
    Soft shoulder curve that keeps detail where digital would blow out."""
    fimg = img.astype(np.float64) / 255.0
    # Soft knee: linear below shoulder, compressed above
    mask = fimg > shoulder
    compressed = shoulder + (1.0 - shoulder) * np.tanh(
        (fimg - shoulder) / (1.0 - shoulder) * 2.0
    ) * 0.5
    fimg = np.where(mask, fimg * (1.0 - strength) + compressed * strength, fimg)
    return (np.clip(fimg, 0, 1) * 255).astype(np.uint8)


# ─── SCANNER EMULATION ───────────────────────────────────────────────────────

def apply_scanner_warmth(img, warmth=0.012, lift=3):
    """Every film scan goes through a scanner that adds subtle warmth
    and lifts the deepest blacks slightly (scanner backlight bleed)."""
    fimg = img.astype(np.float64)
    # Slight warm shift — scanner light source is never perfectly neutral
    fimg[:, :, 2] *= (1.0 + warmth)       # R up slightly
    fimg[:, :, 1] *= (1.0 + warmth * 0.3) # G barely
    fimg[:, :, 0] *= (1.0 - warmth * 0.5) # B down slightly
    # Black point lift — scanner never reads true 0
    fimg = fimg + lift
    return np.clip(fimg, 0, 255).astype(np.uint8)


# ─── LIGHT LEAKS ─────────────────────────────────────────────────────────────

def apply_light_leak(img, intensity=0.06):
    """Subtle warm light bleed from imperfect camera light seals.
    Randomized per-frame — always different, always organic."""
    h, w = img.shape[:2]
    fimg = img.astype(np.float64) / 255.0

    # Pick a random edge/corner for the leak
    leak = np.zeros((h, w), dtype=np.float64)
    leak_type = np.random.randint(0, 4)

    if leak_type == 0:  # left edge
        gradient = np.linspace(1, 0, w).reshape(1, w) ** 2.5
        leak = np.tile(gradient, (h, 1))
    elif leak_type == 1:  # right edge
        gradient = np.linspace(0, 1, w).reshape(1, w) ** 2.5
        leak = np.tile(gradient, (h, 1))
    elif leak_type == 2:  # top-right corner
        y_grad = np.linspace(1, 0, h).reshape(h, 1) ** 2.0
        x_grad = np.linspace(0, 1, w).reshape(1, w) ** 2.0
        leak = y_grad * x_grad
    else:  # bottom-left corner
        y_grad = np.linspace(0, 1, h).reshape(h, 1) ** 2.0
        x_grad = np.linspace(1, 0, w).reshape(1, w) ** 2.0
        leak = y_grad * x_grad

    # Add some randomness to the leak shape
    noise = cv2.resize(np.random.normal(0.5, 0.3, (8, 8)).astype(np.float64),
                       (w, h), interpolation=cv2.INTER_CUBIC)
    leak = leak * np.clip(noise, 0.2, 1.0)

    # Warm color — light leaks are always warm (orange/red)
    leak_color = np.array([0.15, 0.45, 1.0])  # BGR: warm orange
    for c in range(3):
        fimg[:, :, c] += leak * intensity * leak_color[c]

    return (np.clip(fimg, 0, 1) * 255).astype(np.uint8)


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
    radius = min(max(radius, 5), 301)
    s1 = cv2.GaussianBlur(bright, (radius, radius), radius / 3)
    r2 = min(radius * 2 + 1, 601)
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

def process(img_path, stock, output_dir=None, max_dimension=None,
            skip_border=False, skip_dust=False, progress_callback=None):
    """
    Process an image through the film emulation pipeline.

    Args:
        img_path: Path to input image
        stock: Built stock dict (from get_stock() or build_custom_stock())
        output_dir: Where to save output (None = don't save, return image)
        max_dimension: Resize longest edge to this before processing (for preview)
        skip_border: Skip border generation (for preview)
        skip_dust: Skip dust/artifacts (for preview)
        progress_callback: Optional fn(step_name: str, percent: int)

    Returns:
        dict with 'clean' (np array) and optionally 'bordered' (np array)
    """
    def _progress(step, pct):
        if progress_callback:
            progress_callback(step, pct)

    _progress("Loading image", 0)
    img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {img_path}")

    # Resize for preview mode
    if max_dimension:
        h, w = img.shape[:2]
        if max(h, w) > max_dimension:
            scale = max_dimension / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    _progress("Film acutance", 5)
    img = apply_film_acutance(img, 0.20)

    _progress("Gate weave", 8)
    img = apply_gate_weave(img, 0.5)

    _progress("Negative → Print conversion", 10)
    img = apply_film_conversion(img, stock)

    _progress("Highlight rolloff", 25)
    img = apply_highlight_rolloff(img, shoulder=0.82, strength=0.6)

    _progress("Film breath", 35)
    img = apply_film_breath(img, 0.012)

    _progress("Halation", 45)
    img = apply_halation(img, stock)

    _progress("Bloom", 55)
    img = apply_bloom(img, stock["bloom"])

    _progress("Vignette", 60)
    img = apply_vignette(img, stock["vignette"])

    _progress("Volumetric grain", 70)
    img = apply_volumetric_grain(img, stock)

    if not skip_dust:
        _progress("Dust & artifacts", 88)
        img = apply_dust_and_artifacts(img, 10)

    result = {"clean": img}

    if not skip_border:
        _progress("Film border", 90)
        result["bordered"] = add_film_border(img, stock)

    # Save if output_dir provided
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        stem = Path(img_path).stem
        stock_key = stock.get("name", "film").lower().replace(" ", "_")
        base = f"{stem}_{stock_key}"

        if "bordered" in result:
            cv2.imwrite(str(output_dir / f"{base}_border.jpg"), result["bordered"],
                        [cv2.IMWRITE_JPEG_QUALITY, 98])
        # Save both JPEG (max quality) and lossless TIFF
        cv2.imwrite(str(output_dir / f"{base}_clean.jpg"), result["clean"],
                    [cv2.IMWRITE_JPEG_QUALITY, 98])
        cv2.imwrite(str(output_dir / f"{base}_clean.tiff"), result["clean"])

    _progress("Done", 100)
    return result
