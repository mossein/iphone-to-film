"""
Film processing pipeline — all effect functions and the main process() entry point.

Convention: every stage takes a float32 BGR image in [0,1] and returns the same.
The loader normalizes uint8/uint16/float inputs once; the final save step quantizes
back to uint8 (JPEG) and uint16 (TIFF). Intermediate stages never touch 0-255.
"""

import cv2
import math
import numpy as np
from pathlib import Path


# ─── NPS GRAIN KERNEL (from Stephenson & Saunders paper) ─────────────────────
# Extracted from spectral_film_lut.grain_generation to avoid PyQt6 dependency.
# Models dye particle size distribution as 2-component lognormal, then builds
# a convolution kernel from the noise-power spectrum via inverse FFT.

def _two_component_params(grain_size_mm, sigma, p=2):
    mu = np.log(grain_size_mm) - 0.5 * sigma**2
    d1 = math.exp(mu - sigma)
    d2 = math.exp(mu + sigma)
    def _cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    w1 = _cdf(-p * sigma)
    w2 = 1.0 - w1
    return d1, d2, w1, w2

def _grain_kernel(pixel_size_mm, grain_size_mm=0.006, grain_sigma=0.3):
    d1, d2, w1, w2 = _two_component_params(grain_size_mm, grain_sigma)
    kernel_size_mm = 4.24 * max(d1, d2)
    kernel_size = round(kernel_size_mm / pixel_size_mm)
    if kernel_size % 2 == 0: kernel_size += 1
    if kernel_size < 3: return None

    fx = np.fft.fftfreq(kernel_size, d=pixel_size_mm)
    fy = np.fft.fftfreq(kernel_size, d=pixel_size_mm)
    FX, FY = np.meshgrid(fx, fy)
    f = np.sqrt(FX**2 + FY**2)

    nps1 = np.exp(-((np.pi * f * d1) ** 2)) * w1
    nps2 = np.exp(-((np.pi * f * d2) ** 2)) * w2
    nps = nps1 + nps2

    kernel = np.fft.ifft2(np.sqrt(nps))
    kernel = np.fft.fftshift(kernel.real)
    kernel /= np.sqrt(np.sum(kernel))
    return kernel

def _generate_grain(h, w, channels, ppmm, grain_size_mm=0.006, grain_sigma=0.3):
    """Generate film grain with proper NPS spatial structure."""
    kernel = _grain_kernel(1.0 / ppmm, grain_size_mm=grain_size_mm, grain_sigma=grain_sigma)
    noise = np.random.standard_normal((h, w, channels)).astype(np.float32)
    if kernel is not None:
        for c in range(channels):
            noise[:, :, c] = cv2.filter2D(noise[:, :, c], -1, kernel)
    return noise


# ─── VOLUMETRIC GRAIN ────────────────────────────────────────────────────────
# Models silver halide crystal clumps in three independent emulsion layers.
# Multi-scale: fine grain structure + medium clumping + large development effects.
# Per-channel tonal response from manufacturer RMS data via grain_transform().

def apply_volumetric_grain(img, stock):
    h, w = img.shape[:2]
    neg = stock["neg"]
    film_mm = stock["film_format_mm"]
    frame_width_mm = 36 if film_mm == 35 else 56
    ppmm = max(w, h) / frame_width_mm

    fimg = img.copy()
    is_bw = stock.get("category") == "bw"

    rms = getattr(neg, 'rms', 4.0)
    grain_size_mm = 0.008 + rms * 0.002

    # B&W film has one emulsion layer, not three — all channels share the same
    # grain texture. Independent per-channel grain on a B&W image produces
    # magenta/green chromatic speckle.
    n_channels = 1 if is_bw else 3
    grain_texture = _generate_grain(h, w, n_channels, ppmm,
                                     grain_size_mm=grain_size_mm,
                                     grain_sigma=0.3)

    pixels = fimg.reshape(-1, 3).astype(np.float64)
    try:
        gf = neg.grain_transform(pixels, scale=ppmm, std_div=0.1)
        gf = np.asarray(gf, dtype=np.float32)
        if gf.size == h * w * 3:
            grain_factors = gf.reshape(h, w, 3)
        elif gf.shape[0] == h * w and gf.shape[-1] == 3:
            grain_factors = gf.reshape(h, w, 3)
        else:
            raise ValueError(f"Unexpected grain_factors shape: {gf.shape}")
    except Exception:
        gray = cv2.cvtColor(fimg, cv2.COLOR_BGR2GRAY)
        lum_resp = np.clip(np.sqrt(4.0 * gray * (1.0 - gray)), 0.25, 1.0)
        grain_factors = np.stack([lum_resp * rms * 0.008] * 3, axis=-1).astype(np.float32)

    if is_bw:
        # Single mono grain, averaged per-pixel intensity so R=G=B stays R=G=B.
        gt = grain_texture[:, :, 0]
        gmag = grain_factors.mean(axis=-1)
        delta = gt * gmag
        for c in range(3):
            fimg[:, :, c] += delta
    else:
        fimg[:, :, 2] += grain_texture[:, :, 0] * grain_factors[:, :, 0]  # R
        fimg[:, :, 1] += grain_texture[:, :, 1] * grain_factors[:, :, 1]  # G
        fimg[:, :, 0] += grain_texture[:, :, 2] * grain_factors[:, :, 2]  # B

    return np.clip(fimg, 0, 1)


# ─── FILM BREATH ─────────────────────────────────────────────────────────────

def apply_film_breath(img, strength=0.012):
    h, w = img.shape[:2]
    fimg = img.copy()
    breath = np.random.normal(0, 1.0, (16, 16)).astype(np.float32)
    breath = cv2.GaussianBlur(breath, (7, 7), 2.0)
    breath = cv2.resize(breath, (w, h), interpolation=cv2.INTER_CUBIC)
    for c in range(3):
        channel_var = 1.0 + np.random.uniform(-0.3, 0.3)
        fimg[:, :, c] *= (1.0 + breath * strength * channel_var)
    return np.clip(fimg, 0, 1)


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
    """Film MTF: soften the digital crispness, kill iPhone computational sharpness.
    Real film + lens can't resolve pixel-level detail — everything is slightly soft
    with gentle highlight glow from lens flare / internal reflections."""
    h, w = img.shape[:2]

    blur_r = max(3, int(max(h, w) / 1500))
    if blur_r % 2 == 0: blur_r += 1
    soft = cv2.GaussianBlur(img, (blur_r, blur_r), blur_r * 0.4)
    result = img * (1.0 - softness) + soft * softness

    glow_r = max(max(h, w) // 60, 5)
    if glow_r % 2 == 0: glow_r += 1
    glow = cv2.GaussianBlur(img, (glow_r, glow_r), glow_r / 3)
    result = result * 0.92 + glow * 0.08

    return np.clip(result, 0, 1)


# ─── RGB LAYER MISREGISTRATION ────────────────────────────────────────────────

def apply_channel_misregistration(img, strength=0.4):
    """Film has 3 physical emulsion layers that aren't perfectly aligned.
    Subtle per-channel shifts — the #1 subconscious 'this is film' cue."""
    h, w = img.shape[:2]
    result = img.copy()
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
    mask = img > shoulder
    compressed = shoulder + (1.0 - shoulder) * np.tanh(
        (img - shoulder) / (1.0 - shoulder) * 2.0
    ) * 0.5
    out = np.where(mask, img * (1.0 - strength) + compressed * strength, img)
    return np.clip(out, 0, 1).astype(np.float32)


# ─── SCANNER EMULATION ───────────────────────────────────────────────────────

def apply_scanner_warmth(img, warmth=0.012, lift=3/255.0):
    """Every film scan goes through a scanner that adds subtle warmth
    and lifts the deepest blacks slightly (scanner backlight bleed)."""
    fimg = img.copy()
    fimg[:, :, 2] *= (1.0 + warmth)
    fimg[:, :, 1] *= (1.0 + warmth * 0.3)
    fimg[:, :, 0] *= (1.0 - warmth * 0.5)
    fimg = fimg + lift
    return np.clip(fimg, 0, 1)


# ─── LIGHT LEAKS ─────────────────────────────────────────────────────────────

def apply_light_leak(img, intensity=0.06):
    """Subtle warm light bleed from imperfect camera light seals.
    Randomized per-frame — always different, always organic."""
    h, w = img.shape[:2]
    fimg = img.copy()

    leak = np.zeros((h, w), dtype=np.float32)
    leak_type = np.random.randint(0, 4)

    if leak_type == 0:
        gradient = np.linspace(1, 0, w).reshape(1, w) ** 2.5
        leak = np.tile(gradient, (h, 1))
    elif leak_type == 1:
        gradient = np.linspace(0, 1, w).reshape(1, w) ** 2.5
        leak = np.tile(gradient, (h, 1))
    elif leak_type == 2:
        y_grad = np.linspace(1, 0, h).reshape(h, 1) ** 2.0
        x_grad = np.linspace(0, 1, w).reshape(1, w) ** 2.0
        leak = y_grad * x_grad
    else:
        y_grad = np.linspace(0, 1, h).reshape(h, 1) ** 2.0
        x_grad = np.linspace(1, 0, w).reshape(1, w) ** 2.0
        leak = y_grad * x_grad

    noise = cv2.resize(np.random.normal(0.5, 0.3, (8, 8)).astype(np.float32),
                       (w, h), interpolation=cv2.INTER_CUBIC)
    leak = (leak * np.clip(noise, 0.2, 1.0)).astype(np.float32)

    leak_color = np.array([0.15, 0.45, 1.0], dtype=np.float32)  # BGR: warm orange
    for c in range(3):
        fimg[:, :, c] += leak * intensity * leak_color[c]

    return np.clip(fimg, 0, 1)


# ─── CORE EFFECTS ────────────────────────────────────────────────────────────

def apply_film_conversion(img, stock):
    h, w = img.shape[:2]
    # stock["conv"] expects sRGB-encoded [0,1] input — pipeline input is already that.
    converted = np.asarray(stock["conv"](img.reshape(-1, 3).astype(np.float64)),
                           dtype=np.float32)
    if converted.shape[-1] > 3:
        converted = converted[:, :3]
    return np.clip(converted.reshape(h, w, 3), 0, 1)

def apply_halation(img, stock):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
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
    halation = np.zeros_like(img)
    for c in range(3):
        halation[:, :, c] = spread * color[c]
    return np.clip(img + halation * strength, 0, 1)

def apply_bloom(img, strength):
    if strength < 0.01:
        return img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bright = np.clip((gray - 0.50) / 0.50, 0, 1)
    bloom = img.copy()
    for c in range(3):
        bloom[:, :, c] *= bright
    r = min(max(img.shape[:2]) // 25, 201)
    if r % 2 == 0: r += 1
    r = max(r, 5)
    bloom = cv2.GaussianBlur(bloom, (r, r), r / 3)
    return np.clip(1.0 - (1.0 - img) * (1.0 - bloom * strength), 0, 1)

def apply_vignette(img, strength):
    h, w = img.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((x - w/2) / max(w/2, h/2))**2 + ((y - h/2) / max(w/2, h/2))**2)
    vig = 1.0 - strength * np.clip((r - 0.5) / 1.0, 0, 1) ** 2.0
    result = img.copy()
    for c in range(3):
        result[:, :, c] *= vig
    return np.clip(result, 0, 1)

def apply_dust_and_artifacts(img, amount=10):
    """Works on a float32 [0,1] image. cv2.circle/polylines/putText all accept float."""
    h, w = img.shape[:2]
    result = img.copy()
    for _ in range(amount):
        x, y = np.random.randint(0, w), np.random.randint(0, h)
        sz = np.random.randint(3, 8)
        val = np.random.choice([-25, -15, 20, 35]) / 255.0
        opacity = np.random.uniform(0.2, 0.5)
        color = tuple(float(np.clip(result[y, x, c] + val * opacity, 0, 1)) for c in range(3))
        cv2.circle(result, (x, y), sz, color, -1)
    if amount > 6:
        sx = np.random.randint(w // 3, 2 * w // 3)
        sy = np.random.randint(h // 3, 2 * h // 3)
        pts = [(sx, sy)]
        for _ in range(np.random.randint(30, 70)):
            last = pts[-1]
            pts.append((last[0] + np.random.randint(-3, 4), last[1] + np.random.randint(1, 5)))
        scratch = (190/255.0, 185/255.0, 180/255.0)
        cv2.polylines(result, [np.array(pts, dtype=np.int32)], False, scratch, 1, cv2.LINE_AA)
    return result

def add_film_border(img, stock):
    """Float32 [0,1] in, float32 [0,1] out."""
    h, w = img.shape[:2]
    bw, bh = int(w * 0.05), int(h * 0.065)
    tw, th = w + 2 * bw, h + 2 * bh
    strip = np.zeros((th, tw, 3), dtype=np.float32)
    strip[:, :] = (8/255.0, 6/255.0, 5/255.0)
    strip[bh:bh+h, bw:bw+w] = img
    sw, sh = int(tw * 0.017), int(bh * 0.42)
    spacing = int(tw * 0.047)
    n = tw // spacing
    sx = (tw - n * spacing) // 2
    for i in range(n):
        x = sx + i * spacing
        for sy_pos in [int(bh * 0.28), th - int(bh * 0.28) - sh]:
            cv2.rectangle(strip, (x+2, sy_pos+2), (x+sw-2, sy_pos+sh-2), (2/255.0, 2/255.0, 2/255.0), -1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = tw / 3200.0
    tk = max(1, int(fs * 1.5))
    frame = f"{np.random.randint(1,37)}A"
    color = (30/255.0, 55/255.0, 65/255.0)
    text = stock["border_text"]
    for tx in range(0, tw, tw // 3):
        cv2.putText(strip, f"  {text}    {frame}  ", (tx, th - int(bh * 0.5)), font, fs, color, tk, cv2.LINE_AA)
        cv2.putText(strip, f"  {frame}   {text}  ", (tx + tw//6, int(bh * 0.62)), font, fs, color, tk, cv2.LINE_AA)
    return strip


# ─── LOADER & OUTPUT QUANTIZATION ────────────────────────────────────────────

def _load_as_float(img_path):
    """Read image at its native bit depth and normalize to float32 BGR [0,1].

    Supports uint8 (JPEG/PNG) and uint16 (16-bit TIFF from RAW demosaic).
    Preserves full 16-bit dynamic range when present — that's the whole point
    of uploading RAW instead of JPEG.
    """
    raw = cv2.imread(str(img_path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise ValueError(f"Could not read image: {img_path}")

    # Drop alpha, expand grayscale
    if raw.ndim == 2:
        raw = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
    elif raw.shape[2] == 4:
        raw = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

    if raw.dtype == np.uint8:
        return raw.astype(np.float32) / 255.0
    elif raw.dtype == np.uint16:
        return raw.astype(np.float32) / 65535.0
    elif raw.dtype in (np.float32, np.float64):
        out = raw.astype(np.float32)
        if out.max() > 1.5:
            out /= 255.0
        return out
    else:
        # Fallback: re-read forcing 8-bit
        img8 = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        return img8.astype(np.float32) / 255.0


def _to_uint8(img):
    return (np.clip(np.nan_to_num(img, nan=0.0, posinf=1.0, neginf=0.0), 0, 1)
            * 255.0 + 0.5).astype(np.uint8)

def _to_uint16(img):
    return (np.clip(np.nan_to_num(img, nan=0.0, posinf=1.0, neginf=0.0), 0, 1)
            * 65535.0 + 0.5).astype(np.uint16)


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
        dict with 'clean' (uint8 BGR) and optionally 'bordered' (uint8 BGR)
    """
    def _progress(step, pct):
        if progress_callback:
            progress_callback(step, pct)

    _progress("Loading image", 0)
    img = _load_as_float(img_path)

    if max_dimension:
        h, w = img.shape[:2]
        if max(h, w) > max_dimension:
            scale = max_dimension / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    _progress("Film acutance", 5)
    img = apply_film_acutance(img, 0.35)

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

    # Quantize to uint8 for display/JPEG; keep a float copy for 16-bit TIFF.
    clean_float = img
    clean_u8 = _to_uint8(clean_float)
    result = {"clean": clean_u8}

    if not skip_border:
        _progress("Film border", 90)
        bordered_float = add_film_border(clean_float, stock)
        result["bordered"] = _to_uint8(bordered_float)

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        stem = Path(img_path).stem
        stock_key = stock.get("name", "film").lower().replace(" ", "_")
        base = f"{stem}_{stock_key}"

        if "bordered" in result:
            cv2.imwrite(str(output_dir / f"{base}_border.jpg"), result["bordered"],
                        [cv2.IMWRITE_JPEG_QUALITY, 98])
        cv2.imwrite(str(output_dir / f"{base}_clean.jpg"), result["clean"],
                    [cv2.IMWRITE_JPEG_QUALITY, 98])
        # Lossless TIFF preserves the 16-bit pipeline output for further editing.
        cv2.imwrite(str(output_dir / f"{base}_clean.tiff"), _to_uint16(clean_float))

    _progress("Done", 100)
    return result
