"""Gallery route — tiny thumbnails of every stock for quick comparison."""

import io
import hashlib
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import Response, JSONResponse
import cv2
import numpy as np

from core.stocks import get_stock, get_all_stocks, build_custom_stock, _STOCK_DEFS
from core.conversion import PRINT_STOCKS
from core.pipeline import (apply_film_acutance, apply_film_conversion,
                           apply_highlight_rolloff, apply_halation,
                           apply_vignette, apply_volumetric_grain)

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"

# Cache: (image_id, stock_key, print_key) -> jpeg bytes
_thumb_cache = {}

# Thread pool for thumbnail generation — doesn't block the main event loop
_thumb_pool = ThreadPoolExecutor(max_workers=2)


def _process_thumbnail(img, stock):
    """Minimal pipeline for fast thumbnails — just the essentials."""
    img = apply_film_acutance(img, 0.20)
    img = apply_film_conversion(img, stock)
    img = apply_highlight_rolloff(img, shoulder=0.82, strength=0.6)
    img = apply_halation(img, stock)
    img = apply_vignette(img, stock["vignette"])
    img = apply_volumetric_grain(img, stock)
    return img


def _find_and_load(image_id, max_dim=400):
    """Find uploaded image and resize for thumbnail."""
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif"):
        p = UPLOAD_DIR / f"{image_id}{ext}"
        if p.exists():
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                if max(h, w) > max_dim:
                    scale = max_dim / max(h, w)
                    img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                     interpolation=cv2.INTER_AREA)
                return img
    return None


def _generate_thumb(image_id, stock_key, print_stock_key):
    """Synchronous thumbnail generation — runs in thread pool."""
    cache_key = (image_id, stock_key, print_stock_key or "default")
    if cache_key in _thumb_cache:
        return _thumb_cache[cache_key]

    img = _find_and_load(image_id)
    if img is None:
        return None

    if print_stock_key and print_stock_key in PRINT_STOCKS:
        defn = _STOCK_DEFS.get(stock_key)
        if defn and defn.get("print_data") is not None:
            built = build_custom_stock(stock_key, print_stock_data=PRINT_STOCKS[print_stock_key]["data"])
        else:
            built = get_stock(stock_key)
    else:
        built = get_stock(stock_key)

    result = _process_thumbnail(img, built)

    _, buf = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, 70])
    jpeg_bytes = buf.tobytes()
    _thumb_cache[cache_key] = jpeg_bytes
    return jpeg_bytes


@router.get("/thumbnail/{image_id}")
async def thumbnail(
    image_id: str,
    stock: str = Query("portra400"),
    print_stock: str = Query(None),
):
    cache_key = (image_id, stock, print_stock or "default")
    if cache_key in _thumb_cache:
        return Response(content=_thumb_cache[cache_key], media_type="image/jpeg")

    # Run in thread pool so it doesn't block the event loop
    loop = asyncio.get_event_loop()
    jpeg_bytes = await loop.run_in_executor(_thumb_pool, _generate_thumb, image_id, stock, print_stock)

    if jpeg_bytes is None:
        return JSONResponse(status_code=404, content={"error": "Image not found"})

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


@router.get("/gallery-combos")
async def gallery_combos():
    """Return all stock + print stock combinations for the full gallery."""
    all_stocks = get_all_stocks()
    prints = {k: v["name"] for k, v in PRINT_STOCKS.items()}

    # Categories that support print stock mixing (color negatives only)
    # B&W negatives on color prints = garbage. Reversal films have no print stage.
    mixable_categories = {"pro_color", "consumer", "cinema", "vintage_cinema"}

    combos = []
    for key, info in all_stocks.items():
        defn = _STOCK_DEFS.get(key)
        has_print = defn and defn.get("print_data") is not None
        can_mix = has_print and info["category"] in mixable_categories

        # Default combo
        combos.append({
            "key": key,
            "name": info["name"],
            "category": info["category"],
            "print_key": None,
            "print_name": "Default",
            "can_mix": can_mix,
        })

        # Print variations (only for color negative stocks)
        if can_mix:
            for pk, pname in prints.items():
                combos.append({
                    "key": key,
                    "name": info["name"],
                    "category": info["category"],
                    "print_key": pk,
                    "print_name": pname,
                    "can_mix": True,
                })

    return {"combos": combos, "print_stocks": prints}
