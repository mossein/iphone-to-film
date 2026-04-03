"""Gallery route — tiny thumbnails of every stock for quick comparison."""

import io
import hashlib
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import Response, JSONResponse
import cv2
import numpy as np

from core.stocks import get_stock, get_all_stocks
from core.pipeline import (apply_film_acutance, apply_film_conversion,
                           apply_highlight_rolloff, apply_halation,
                           apply_vignette, apply_volumetric_grain)

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"

# Cache: (image_id, stock_key) -> jpeg bytes
_thumb_cache = {}


def _process_thumbnail(img, stock):
    """Minimal pipeline for fast thumbnails — just the essentials."""
    img = apply_film_acutance(img, 0.20)
    img = apply_film_conversion(img, stock)
    img = apply_highlight_rolloff(img, shoulder=0.82, strength=0.6)
    img = apply_halation(img, stock)
    img = apply_vignette(img, stock["vignette"])
    img = apply_volumetric_grain(img, stock)
    return img


@router.get("/thumbnail/{image_id}")
async def thumbnail(image_id: str, stock: str = Query("portra400")):
    cache_key = (image_id, stock)
    if cache_key in _thumb_cache:
        return Response(content=_thumb_cache[cache_key], media_type="image/jpeg")

    # Find uploaded image
    img_path = None
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif"):
        p = UPLOAD_DIR / f"{image_id}{ext}"
        if p.exists():
            img_path = p
            break
    if not img_path:
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if img is None:
        return JSONResponse(status_code=404, content={"error": "Cannot read image"})

    # Resize to tiny thumbnail
    h, w = img.shape[:2]
    max_dim = 400
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    built = get_stock(stock)
    result = _process_thumbnail(img, built)

    _, buf = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, 70])
    jpeg_bytes = buf.tobytes()
    _thumb_cache[cache_key] = jpeg_bytes

    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.get("/gallery-stocks")
async def gallery_stocks():
    """Return flat list of all stocks for gallery grid."""
    all_stocks = get_all_stocks()
    return [
        {"key": key, "name": info["name"], "category": info["category"],
         "description": info["description"]}
        for key, info in all_stocks.items()
    ]
