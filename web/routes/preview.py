"""Preview route — fast low-res processing for instant feedback."""

import io
import re
import hashlib
from collections import OrderedDict
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import Response, JSONResponse
import cv2

from core.stocks import get_stock, build_custom_stock
from core.conversion import PRINT_STOCKS
from core.pipeline import process

router = APIRouter()

from web._paths import UPLOAD_DIR  # bundle-aware writable dir

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Bounded preview cache: (image_id, params_hash) -> jpeg bytes
_PREVIEW_CACHE_MAX = 200
_preview_cache = OrderedDict()


def _cache_put(key, value):
    _preview_cache[key] = value
    while len(_preview_cache) > _PREVIEW_CACHE_MAX:
        _preview_cache.popitem(last=False)


# Pipeline-stage override params surfaced in the UI (see /web/static/index.html).
# Shared between preview.py and process.py.
PIPELINE_PARAMS = (
    "halation_strength", "halation_radius", "halation_threshold",
    "bloom", "vignette", "acutance", "grain_amount", "rolloff_knee",
    "rolloff_strength", "breath", "misregistration", "dust_amount",
    "scanner_warmth", "scanner_lift", "light_leak",
    "chromatic_aberration", "auto_exposure", "artifact_density",
)


def _params_hash(stock_key, print_stock, exp_comp, sat, pre_flash_neg,
                 black_offset, white_point, tint, pipeline):
    raw = f"{stock_key}:{print_stock}:{exp_comp}:{sat}:{pre_flash_neg}:{black_offset}:{white_point}:{tint}"
    raw += "|" + ":".join(f"{k}={pipeline.get(k)}" for k in PIPELINE_PARAMS)
    return hashlib.md5(raw.encode()).hexdigest()


@router.get("/preview/{image_id}")
async def preview(
    image_id: str,
    stock: str = Query("portra400"),
    print_stock: str = Query(None),
    exp_comp: float = Query(None),
    sat: float = Query(None),
    pre_flash_neg: float = Query(-4),
    black_offset: float = Query(0),
    white_point: float = Query(1.0),
    tint: float = Query(0),
    halation_strength: float = Query(None),
    halation_radius: float = Query(None),
    halation_threshold: float = Query(None),
    bloom: float = Query(None),
    vignette: float = Query(None),
    acutance: float = Query(None),
    grain_amount: float = Query(None),
    rolloff_knee: float = Query(None),
    rolloff_strength: float = Query(None),
    breath: float = Query(None),
    misregistration: float = Query(None),
    dust_amount: int = Query(None),
    scanner_warmth: float = Query(None),
    scanner_lift: float = Query(None),
    light_leak: float = Query(None),
    chromatic_aberration: float = Query(None),
    auto_exposure: float = Query(None),
    artifact_density: float = Query(None),
):
    if not _UUID_RE.match(image_id):
        return JSONResponse(status_code=400, content={"error": "Invalid image ID"})

    # Find the uploaded image
    img_path = None
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif"):
        p = UPLOAD_DIR / f"{image_id}{ext}"
        if p.exists():
            img_path = p
            break
    if not img_path:
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    pipeline = {k: v for k, v in {
        "halation_strength": halation_strength,
        "halation_radius": halation_radius,
        "halation_threshold": halation_threshold,
        "bloom": bloom,
        "vignette": vignette,
        "acutance": acutance,
        "grain_amount": grain_amount,
        "rolloff_knee": rolloff_knee,
        "rolloff_strength": rolloff_strength,
        "breath": breath,
        "misregistration": misregistration,
        "dust_amount": dust_amount,
        "scanner_warmth": scanner_warmth,
        "scanner_lift": scanner_lift,
        "light_leak": light_leak,
        "chromatic_aberration": chromatic_aberration,
        "auto_exposure": auto_exposure,
        "artifact_density": artifact_density,
    }.items() if v is not None}

    # Check cache
    ph = _params_hash(stock, print_stock, exp_comp, sat, pre_flash_neg,
                      black_offset, white_point, tint, pipeline)
    cache_key = (image_id, ph)
    if cache_key in _preview_cache:
        _preview_cache.move_to_end(cache_key)
        return Response(content=_preview_cache[cache_key], media_type="image/jpeg")

    # Build stock (custom params or default)
    has_custom = (print_stock is not None or exp_comp is not None or
                  sat is not None or pre_flash_neg != -4 or
                  black_offset != 0 or white_point != 1.0 or tint != 0
                  or bool(pipeline))

    if has_custom:
        print_data = PRINT_STOCKS[print_stock]["data"] if print_stock else None
        built = build_custom_stock(
            stock, print_stock_data=print_data,
            exp_comp=exp_comp, sat=sat,
            pre_flash_neg=pre_flash_neg, black_offset=black_offset,
            white_point=white_point, tint=tint,
            overrides=pipeline,
        )
    else:
        built = get_stock(stock)

    # Process at preview resolution
    result = process(
        img_path, built,
        max_dimension=1200,
        skip_border=True, skip_dust=True,
    )

    # Encode to JPEG
    _, buf = cv2.imencode(".jpg", result["clean"], [cv2.IMWRITE_JPEG_QUALITY, 80])
    jpeg_bytes = buf.tobytes()

    # Cache it
    _cache_put(cache_key, jpeg_bytes)

    return Response(content=jpeg_bytes, media_type="image/jpeg")
