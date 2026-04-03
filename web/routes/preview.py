"""Preview route — fast low-res processing for instant feedback."""

import io
import hashlib
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import Response, JSONResponse
import cv2

from core.stocks import get_stock, build_custom_stock
from core.conversion import PRINT_STOCKS
from core.pipeline import process

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"

# Preview cache: (image_id, params_hash) -> jpeg bytes
_preview_cache = {}


def _params_hash(stock_key, print_stock, exp_comp, sat, pre_flash_neg,
                 black_offset, white_point, tint):
    raw = f"{stock_key}:{print_stock}:{exp_comp}:{sat}:{pre_flash_neg}:{black_offset}:{white_point}:{tint}"
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
):
    # Find the uploaded image
    img_path = None
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif"):
        p = UPLOAD_DIR / f"{image_id}{ext}"
        if p.exists():
            img_path = p
            break
    if not img_path:
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    # Check cache
    ph = _params_hash(stock, print_stock, exp_comp, sat, pre_flash_neg,
                      black_offset, white_point, tint)
    cache_key = (image_id, ph)
    if cache_key in _preview_cache:
        return Response(content=_preview_cache[cache_key], media_type="image/jpeg")

    # Build stock (custom params or default)
    has_custom = (print_stock is not None or exp_comp is not None or
                  sat is not None or pre_flash_neg != -4 or
                  black_offset != 0 or white_point != 1.0 or tint != 0)

    if has_custom:
        print_data = PRINT_STOCKS[print_stock]["data"] if print_stock else None
        built = build_custom_stock(
            stock, print_stock_data=print_data,
            exp_comp=exp_comp, sat=sat,
            pre_flash_neg=pre_flash_neg, black_offset=black_offset,
            white_point=white_point, tint=tint,
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
    _preview_cache[cache_key] = jpeg_bytes

    return Response(content=jpeg_bytes, media_type="image/jpeg")
