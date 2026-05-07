"""
Film processing pipeline — all effect functions and the main process() entry point.

Convention: every stage takes a float32 BGR image in [0,1] and returns the same.
The loader normalizes uint8/uint16/float inputs once; the final save step quantizes
back to uint8 (JPEG) and uint16 (TIFF). Intermediate stages never touch 0-255.

Color-space convention:
  • Input to the pipeline is sRGB-encoded (gamma-companded). The spectral
    conversion expects and returns sRGB-encoded.
  • Optical effects (halation, bloom, vignette, grain, breath, scanner warmth)
    model light scattering and detector behavior — they are physically defined
    in *linear* light and run in a linear sRGB stage between the spectral LUT
    and final sRGB encoding.
  • Tonal/perceptual effects (acutance, highlight rolloff, dust, border) stay
    in sRGB-encoded space.
"""

import cv2
import math
import numpy as np
from pathlib import Path


# ─── sRGB ⇄ LINEAR ───────────────────────────────────────────────────────────
# Piecewise sRGB EOTF/OETF (IEC 61966-2-1). Stays correct near zero where
# the pure-2.2 power approximation has the wrong slope.

def _to_linear(img):
    a = 0.055
    return np.where(
        img <= 0.04045,
        img / 12.92,
        np.power(np.clip((img + a) / (1.0 + a), 1e-10, None), 2.4),
    ).astype(np.float32)

def _to_srgb(img):
    a = 0.055
    return np.where(
        img <= 0.0031308,
        12.92 * img,
        (1.0 + a) * np.power(np.clip(img, 1e-10, None), 1.0 / 2.4) - a,
    ).astype(np.float32)


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
    # L2-normalize so convolution preserves unit noise variance regardless of
    # kernel size. The previous sqrt(sum(kernel)) normalization let variance
    # explode at full-res (large kernel) — B&W exports came out as binary noise.
    norm = np.sqrt(np.sum(kernel ** 2))
    if norm > 0:
        kernel /= norm
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

    grain_amount = float(stock.get("grain_amount", 1.0))
    if grain_amount <= 0:
        return img

    fimg = img.copy()
    is_bw = stock.get("category") == "bw"

    rms_attr = getattr(neg, 'rms', None)
    rms = float(rms_attr) if (rms_attr is not None and np.isfinite(rms_attr)) else 4.0

    # B&W manufacturer RMS values (Tri-X ≈ 17, Double-X ≈ 14) are reported on a
    # different scale than the per-layer dye-cloud RMS used by color stocks
    # (≈ 3–5). Using the same coefficients yields huge, chunky grain on B&W.
    if is_bw:
        grain_size_mm = 0.008 + rms * 0.0008   # ~0.022mm for Tri-X (vs 0.042 with color formula)
        grain_mag_coef = 0.0022                # ~0.037 std on midtones (vs 0.136)
    else:
        grain_size_mm = 0.008 + rms * 0.002
        grain_mag_coef = 0.008

    if is_bw:
        # B&W: single emulsion → one shared grain channel.
        grain_texture = _generate_grain(h, w, 1, ppmm,
                                         grain_size_mm=grain_size_mm,
                                         grain_sigma=0.3)
        gray = cv2.cvtColor(fimg, cv2.COLOR_BGR2GRAY)
        lum_resp = np.clip(np.sqrt(4.0 * gray * (1.0 - gray)), 0.0, 1.0)
        gmag = (lum_resp * rms * grain_mag_coef * grain_amount).astype(np.float32)[..., None]
        delta = grain_texture[:, :, 0:1] * gmag
        fimg += delta
        return np.clip(fimg, 0, 1)

    # ── Color path ─────────────────────────────────────────────────────────
    # Generate luminance grain and chroma grain *separately* so we can gate
    # them differently. Real color film grain is dominantly luminance; chroma
    # speckle should fade out in shadows (no dye activation in deep blacks)
    # AND in highlights (saturated dye), leaving only luma grain there.
    luma_tex = _generate_grain(h, w, 1, ppmm,
                                grain_size_mm=grain_size_mm,
                                grain_sigma=0.3)
    chroma_tex = _generate_grain(h, w, 3, ppmm,
                                  grain_size_mm=grain_size_mm * 1.4,
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
        gray_fb = cv2.cvtColor(fimg, cv2.COLOR_BGR2GRAY)
        lum_resp = np.clip(np.sqrt(4.0 * gray_fb * (1.0 - gray_fb)), 0.0, 1.0)
        grain_factors = np.stack([lum_resp * rms * 0.008] * 3, axis=-1).astype(np.float32)

    gray = cv2.cvtColor(fimg, cv2.COLOR_BGR2GRAY)
    # Tonal response: midtone-peaked, *zero* at pure black and pure white.
    # The previous floor of 0.20 was the source of the chromatic speckle in
    # deep blacks (sunglasses, fabric) — it forced 20% of midtone grain into
    # regions that should be silent.
    tone_resp = np.sqrt(np.clip(4.0 * gray * (1.0 - gray), 0.0, 1.0))[..., None]
    # Chroma gate: chroma noise only meaningful where there's signal in the
    # midtones. Off below ~6% gray, full above ~30%, soft transition.
    chroma_gate = np.clip((gray - 0.06) / 0.24, 0.0, 1.0)[..., None]

    luma_scale   = 0.40 * grain_amount
    chroma_scale = 0.10 * grain_amount  # was 0.25 — too loud, drove the speckle

    luma_delta = luma_tex * grain_factors * tone_resp * luma_scale
    chroma_delta = (chroma_tex * grain_factors * tone_resp
                    * chroma_gate * chroma_scale)
    delta = luma_delta + chroma_delta

    fimg[:, :, 2] += delta[:, :, 0]  # R
    fimg[:, :, 1] += delta[:, :, 1]  # G
    fimg[:, :, 0] += delta[:, :, 2]  # B

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

def apply_auto_exposure(img, target=0.50, percentile=60.0, strength=1.0):
    """Push the image toward a target sRGB midtone by finding the chosen
    percentile of luminance and rescaling so it lands at `target`. Scene-aware
    pre-conversion exposure adjustment — what a lab does when printing.

    target=0.50 (sRGB midtone), percentile=60 picks "average bright" as the
    anchor. strength=0 is a no-op; strength=1 fully applies. Works in sRGB
    space (matches what the spectral LUT consumes)."""
    if strength <= 0.0:
        return img
    luma = (0.0722 * img[:, :, 0] + 0.7152 * img[:, :, 1]
            + 0.2126 * img[:, :, 2])
    p = float(np.percentile(luma, percentile))
    if p <= 1e-4:
        return img
    raw_scale = target / p
    # Clamp the multiplier so deeply over/underexposed shots don't get nuked.
    scale = float(np.clip(raw_scale, 0.4, 2.5))
    scale = 1.0 + (scale - 1.0) * strength
    return np.clip(img * scale, 0.0, 1.0).astype(np.float32)


def apply_chromatic_aberration(img, strength=0.0015):
    """Cheap radial CA — slightly scale R in, scale B out. strength is the
    relative scale offset (0.0015 = 0.15%). At zero, no-op."""
    if strength <= 0:
        return img
    h, w = img.shape[:2]
    cy, cx = h / 2.0, w / 2.0
    M_r = cv2.getRotationMatrix2D((cx, cy), 0, 1.0 - strength)
    M_b = cv2.getRotationMatrix2D((cx, cy), 0, 1.0 + strength)
    out = img.copy()
    out[:, :, 2] = cv2.warpAffine(img[:, :, 2], M_r, (w, h), borderMode=cv2.BORDER_REFLECT)
    out[:, :, 0] = cv2.warpAffine(img[:, :, 0], M_b, (w, h), borderMode=cv2.BORDER_REFLECT)
    return out


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

    # BGR warm orange. Original was [0.15, 0.45, 1.0] which is blue-dominant
    # despite the misleading comment. Real light-seal leaks are warm because
    # they pass through the orange film base.
    leak_color = np.array([0.05, 0.40, 1.00], dtype=np.float32)  # BGR
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
    out = converted.reshape(h, w, 3)
    # The spectral LUT produces NaN for some out-of-domain inputs (power(negative,frac)).
    # In the old uint8-per-stage pipeline those silently cast to 0; in the float pipeline
    # they'd spread via the next GaussianBlur (halation/bloom/acutance) until most of the
    # image was NaN. Kill them at the boundary where they're created.
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=0.0)
    return np.clip(out, 0, 1)

def apply_halation(img, stock):
    """Halation = red light passing through the silver-halide layers, hitting
    the film base, and reflecting back. Mask from the RED channel (red/IR
    penetrates deepest, which is why halation reads as red even on neutral
    light sources). Inputs are expected in linear light; the per-stock
    threshold is authored in sRGB-display convention so we linearize it here.
    """
    strength, color = stock["halation_strength"], stock["halation_color"]
    threshold_srgb = stock["halation_threshold"]
    # piecewise sRGB → linear, scalar form
    threshold = (threshold_srgb / 12.92 if threshold_srgb <= 0.04045
                 else ((threshold_srgb + 0.055) / 1.055) ** 2.4)

    # Red channel in BGR is index 2.
    red = img[:, :, 2]
    bright = np.clip((red - threshold) / (1.0 - threshold + 1e-6), 0, 1)

    # Two-scale point-spread: tighter core + much wider tail. Closer to the
    # actual scattering profile than a single gaussian or a 0.5/0.5 dual.
    # Format scaling: halation_radius is authored as a fraction of the 35mm
    # frame. On larger formats the same physical scattering distance covers a
    # smaller fraction of the frame, so the radius shrinks.
    fmt_scale = 35.0 / float(stock.get("film_format_mm", 35))
    radius = int(max(img.shape[:2]) * stock["halation_radius"] * fmt_scale)
    if radius < 3: radius = 3
    if radius % 2 == 0: radius += 1
    radius = min(radius, 301)
    s1 = cv2.GaussianBlur(bright, (radius, radius), radius / 3)
    r2 = min(radius * 3 + 1, 901)
    if r2 % 2 == 0: r2 += 1
    s2 = cv2.GaussianBlur(bright, (r2, r2), r2 / 3)
    spread = s1 * 0.7 + s2 * 0.3

    halation = np.empty_like(img)
    halation[:, :, 0] = spread * color[2]  # B
    halation[:, :, 1] = spread * color[1]  # G
    halation[:, :, 2] = spread * color[0]  # R
    return np.clip(img + halation * strength, 0, None)

def apply_bloom(img, strength):
    """Bloom = lens/film internal scattering of bright sources. Linear-light
    input expected; threshold is the sRGB value 0.5 linearized (= 0.214)."""
    if strength < 0.01:
        return img
    threshold = 0.21404114  # _to_linear(0.5)
    # Rec.709 linear luma — a perceptual-but-linear estimate of brightness.
    gray = (0.0722 * img[:, :, 0] + 0.7152 * img[:, :, 1]
            + 0.2126 * img[:, :, 2])
    bright = np.clip((gray - threshold) / (1.0 - threshold), 0, 1)
    bloom = img * bright[..., None]
    r = min(max(img.shape[:2]) // 25, 201)
    if r % 2 == 0: r += 1
    r = max(r, 5)
    bloom = cv2.GaussianBlur(bloom, (r, r), r / 3).astype(np.float32)
    # Screen blend in linear: 1 - (1-a)(1-b). Don't clip top — outputs can
    # exceed 1.0 here; sRGB encode at the end of the linear stage will clip.
    return np.maximum(1.0 - (1.0 - img) * (1.0 - bloom * strength), 0.0)

def apply_vignette(img, strength):
    h, w = img.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((x - w/2) / max(w/2, h/2))**2 + ((y - h/2) / max(w/2, h/2))**2)
    vig = 1.0 - strength * np.clip((r - 0.5) / 1.0, 0, 1) ** 2.0
    result = img.copy()
    for c in range(3):
        result[:, :, c] *= vig
    return np.clip(result, 0, 1)

def apply_dust_and_artifacts(img, amount=10, stock=None):
    """Works on a float32 [0,1] image. Dots (always), plus rare scratches and
    hairs gated on stock["artifact_density"] (0..1, default 0)."""
    h, w = img.shape[:2]
    result = img.copy()
    for _ in range(amount):
        x, y = np.random.randint(0, w), np.random.randint(0, h)
        sz = np.random.randint(3, 8)
        val = np.random.choice([-25, -15, 20, 35]) / 255.0
        opacity = np.random.uniform(0.2, 0.5)
        color = tuple(float(np.clip(result[y, x, c] + val * opacity, 0, 1)) for c in range(3))
        cv2.circle(result, (x, y), sz, color, -1)

    artifacts = (stock.get("artifact_density", 0.0) if stock else 0.0)
    if artifacts > 0:
        # Vertical scratches: 1–2px wide, full-height occasional, slightly
        # desaturated near-white (silver scratch on negative reads bright).
        n_scratches = int(np.random.poisson(artifacts * 1.5))
        for _ in range(n_scratches):
            sx = np.random.randint(0, w)
            length = np.random.randint(h // 3, h)
            sy = np.random.randint(0, max(1, h - length))
            thickness = np.random.choice([1, 1, 2])
            shade = np.random.uniform(0.85, 1.0)
            tint = np.random.uniform(-0.05, 0.05)
            color = (float(np.clip(shade + tint, 0, 1)),
                     float(np.clip(shade, 0, 1)),
                     float(np.clip(shade - tint, 0, 1)))
            cv2.line(result, (sx, sy), (sx, sy + length), color, thickness, cv2.LINE_AA)

        # Hairs: thin dark curves. Bezier approximated by a few line segments
        # between random control points.
        n_hairs = int(np.random.poisson(artifacts * 0.6))
        for _ in range(n_hairs):
            cx, cy = np.random.randint(0, w), np.random.randint(0, h)
            length = np.random.randint(40, min(w, h) // 2)
            angle = np.random.uniform(0, 2 * np.pi)
            curl = np.random.uniform(-0.5, 0.5)
            pts = []
            for t in np.linspace(0, 1, 12):
                a = angle + curl * t
                px = int(cx + length * t * np.cos(a))
                py = int(cy + length * t * np.sin(a))
                pts.append((px, py))
            shade = np.random.uniform(0.0, 0.15)
            for i in range(len(pts) - 1):
                cv2.line(result, pts[i], pts[i + 1], (shade, shade, shade), 1, cv2.LINE_AA)

    return result


def apply_color_grade(img, grade):
    """ASC-CDL-style display-referred grade. grade is a dict with optional keys:
        slope:  per-channel multiplier, [b, g, r] in BGR (default [1,1,1])
        offset: per-channel additive,   [b, g, r] (default [0,0,0])
        power:  per-channel gamma,      [b, g, r] (default [1,1,1])
        sat:    saturation around Rec.709 luma  (default 1.0)
    Output is sRGB-display-referred float32, clipped [0,1]."""
    out = img.astype(np.float32)
    slope  = np.asarray(grade.get("slope",  [1.0, 1.0, 1.0]), dtype=np.float32)
    offset = np.asarray(grade.get("offset", [0.0, 0.0, 0.0]), dtype=np.float32)
    power  = np.asarray(grade.get("power",  [1.0, 1.0, 1.0]), dtype=np.float32)
    sat    = float(grade.get("sat", 1.0))
    out = out * slope + offset
    out = np.where(out > 0, np.power(np.clip(out, 1e-10, None), power), out)
    if sat != 1.0:
        luma = (0.0722 * out[:, :, 0] + 0.7152 * out[:, :, 1]
                + 0.2126 * out[:, :, 2])[..., None]
        out = luma + (out - luma) * sat
    return np.clip(out, 0, 1).astype(np.float32)

def add_film_border(img, stock):
    """Float32 [0,1] in, float32 [0,1] out. 35mm gets sprocket holes; larger
    formats get a clean black border (medium-format / instant don't have them
    on the long edges)."""
    h, w = img.shape[:2]
    fmt_mm = int(stock.get("film_format_mm", 35))
    is_35 = fmt_mm == 35
    bw = int(w * (0.05 if is_35 else 0.04))
    bh = int(h * (0.065 if is_35 else 0.05))
    tw, th = w + 2 * bw, h + 2 * bh
    strip = np.zeros((th, tw, 3), dtype=np.float32)
    strip[:, :] = (8/255.0, 6/255.0, 5/255.0)
    strip[bh:bh+h, bw:bw+w] = img

    if is_35:
        sw, sh = int(tw * 0.017), int(bh * 0.42)
        spacing = int(tw * 0.047)
        n = tw // spacing
        sx = (tw - n * spacing) // 2
        for i in range(n):
            x = sx + i * spacing
            for sy_pos in [int(bh * 0.28), th - int(bh * 0.28) - sh]:
                cv2.rectangle(strip, (x+2, sy_pos+2), (x+sw-2, sy_pos+sh-2),
                              (2/255.0, 2/255.0, 2/255.0), -1)

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

_RAW_EXTS = (".cr3", ".cr2", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf",
             ".pef", ".srw", ".x3f")

def _load_raw_as_float(path):
    """RAW dispatch for the CLI/loader path.

    The web upload route does its own (better) ProPhoto-domain demosaic and
    saves a 16-bit sRGB TIFF, so this function is only hit by film_process.py
    when given a RAW directly. Output is sRGB-encoded BGR float32 [0,1] —
    matches what the spectral LUT expects downstream.
    """
    import rawpy  # imported lazily so non-RAW paths don't pay the cost
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            highlight_mode=rawpy.HighlightMode.ReconstructDefault,
            output_bps=16,
            gamma=(2.4, 12.92),  # piecewise sRGB OETF — output is sRGB-encoded
            output_color=rawpy.ColorSpace.sRGB,
        )
    rgb = rgb.astype(np.float32) / 65535.0
    # Auto-stretch: rawpy with no_auto_bright leaves the image at sensor scale.
    # Push the 99th percentile to ~0.9 so the image isn't uselessly dark.
    p99 = float(np.percentile(rgb, 99)) if rgb.size else 0.0
    if 1e-4 < p99 < 0.9:
        rgb = np.clip(rgb * (0.9 / p99), 0.0, 1.0)
    bgr = rgb[:, :, ::-1].copy()
    return bgr

def _load_as_float(img_path):
    """Read image at its native bit depth and normalize to float32 BGR [0,1].

    Supports uint8 (JPEG/PNG), uint16 (TIFF from RAW demosaic), and RAW
    (.cr3/.nef/.arw/.dng etc.) via rawpy. RAW dispatch matters for the CLI;
    the web upload route already demosaics on intake.
    """
    img_path = Path(img_path)
    if img_path.suffix.lower() in _RAW_EXTS:
        return _load_raw_as_float(img_path)

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

    # Per-stock with sensible global fallbacks (Tier 2.1).
    acutance       = stock.get("acutance", 0.35)
    rolloff_knee   = stock.get("rolloff_knee", 0.82)
    rolloff_str    = stock.get("rolloff_strength", 0.6)
    breath_str     = stock.get("breath", 0.012)
    misreg_str     = stock.get("misregistration", 0.5)
    dust_amount    = stock.get("dust_amount", 10)
    scanner_warmth = stock.get("scanner_warmth", 0.012)
    scanner_lift   = stock.get("scanner_lift", 3.0 / 255.0)
    light_leak_int = stock.get("light_leak", 0.0)
    grade          = stock.get("grade")  # optional CDL dict
    ca_strength    = stock.get("chromatic_aberration", 0.0)
    auto_exp_str   = stock.get("auto_exposure", 0.0)  # 0=off; 0.5–1.0 sensible range

    # ── Adaptive exposure (lab-style auto-print). Off by default; per-stock.
    if auto_exp_str > 0:
        _progress("Auto-exposure", 3)
        img = apply_auto_exposure(img, strength=auto_exp_str)

    # ── sRGB pre-conversion: perceptual softening + emulsion-layer registration
    _progress("Film acutance", 5)
    img = apply_film_acutance(img, acutance)

    _progress("Channel misregistration", 8)
    img = apply_channel_misregistration(img, misreg_str)

    if ca_strength > 0:
        _progress("Chromatic aberration", 9)
        img = apply_chromatic_aberration(img, ca_strength)

    # ── Spectral negative → print (sRGB in, sRGB out)
    _progress("Negative → Print conversion", 10)
    img = apply_film_conversion(img, stock)

    # ── Linear-light optical stage: halation + bloom are physical scattering
    # phenomena and need linear light to behave correctly. Vignette/grain/breath
    # are perceptually-tuned and stay in sRGB to avoid re-balancing 50 stocks.
    _progress("Halation", 30)
    img_lin = _to_linear(img)
    img_lin = apply_halation(img_lin, stock)
    _progress("Bloom", 40)
    img_lin = apply_bloom(img_lin, stock["bloom"])
    img = _to_srgb(img_lin)

    # ── Display-referred grade + tonal compression
    if grade:
        _progress("Color grade", 45)
        img = apply_color_grade(img, grade)

    _progress("Highlight rolloff", 50)
    img = apply_highlight_rolloff(img, shoulder=rolloff_knee, strength=rolloff_str)

    _progress("Film breath", 55)
    img = apply_film_breath(img, breath_str)

    _progress("Vignette", 60)
    img = apply_vignette(img, stock["vignette"])

    _progress("Scanner warmth", 65)
    img = apply_scanner_warmth(img, warmth=scanner_warmth, lift=scanner_lift)

    if light_leak_int > 0:
        _progress("Light leak", 68)
        img = apply_light_leak(img, intensity=light_leak_int)

    _progress("Volumetric grain", 70)
    img = apply_volumetric_grain(img, stock)

    base_color = stock.get("base_color")  # BGR list/tuple, e.g. instant warm white
    if base_color is not None:
        _progress("Film base tint", 85)
        bc = np.asarray(base_color, dtype=np.float32).reshape(1, 1, 3)
        # Tint scales with darkness — base color is most visible in shadows
        # of un-printed reversal/instant stocks, where the emulsion base shows.
        gray = (0.0722 * img[:, :, 0] + 0.7152 * img[:, :, 1]
                + 0.2126 * img[:, :, 2])[..., None]
        weight = (1.0 - gray) * float(stock.get("base_color_strength", 0.08))
        img = np.clip(img * (1.0 - weight) + bc * weight, 0, 1).astype(np.float32)

    if not skip_dust:
        _progress("Dust & artifacts", 88)
        img = apply_dust_and_artifacts(img, dust_amount, stock=stock)

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
