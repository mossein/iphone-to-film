"""Batch route — process multiple images with one stock."""

import uuid
import time
import logging
import threading
from pathlib import Path
from fastapi import APIRouter, Query, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
import shutil
import zipfile
import io
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from core.stocks import get_stock, build_custom_stock
from core.conversion import PRINT_STOCKS
from core.pipeline import process

register_heif_opener()
log = logging.getLogger(__name__)

router = APIRouter()

from web._paths import UPLOAD_DIR, OUTPUT_DIR  # bundle-aware writable dirs

# Batch job tracking
_batch_jobs = {}
_BATCH_TTL = 3600


def _cleanup_old_batch_jobs():
    now = time.time()
    expired = [k for k, v in _batch_jobs.items()
               if v.get("status") in ("done", "error") and now - v.get("created_at", now) > _BATCH_TTL]
    for k in expired:
        del _batch_jobs[k]


def _run_batch(batch_id, image_paths, built, include_border):
    total = len(image_paths)
    results = []

    for i, img_path in enumerate(image_paths):
        _batch_jobs[batch_id]["current"] = i + 1
        _batch_jobs[batch_id]["current_file"] = Path(img_path).name
        _batch_jobs[batch_id]["progress"] = int((i / total) * 100)

        try:
            out_dir = OUTPUT_DIR / batch_id
            out_dir.mkdir(parents=True, exist_ok=True)
            result = process(
                img_path, built,
                output_dir=str(out_dir),
                skip_border=not include_border,
            )
            results.append({"file": Path(img_path).name, "status": "done"})
        except Exception as e:
            results.append({"file": Path(img_path).name, "status": "error", "error": str(e)})

    _batch_jobs[batch_id]["status"] = "done"
    _batch_jobs[batch_id]["progress"] = 100
    _batch_jobs[batch_id]["results"] = results


@router.post("/batch/upload")
async def batch_upload(files: list[UploadFile] = File(...)):
    batch_id = str(uuid.uuid4())[:8]
    image_paths = []

    from web.routes.upload import RAW_EXTS, _demosaic_raw
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif") + RAW_EXTS:
            continue
        image_id = str(uuid.uuid4())
        contents = await file.read()

        if ext in (".heic", ".heif"):
            tmp = UPLOAD_DIR / f"{image_id}{ext}"
            tmp.write_bytes(contents)
            try:
                img = ImageOps.exif_transpose(Image.open(tmp))
                save_path = UPLOAD_DIR / f"{image_id}.jpg"
                img.save(str(save_path), "JPEG", quality=98)
                tmp.unlink()
                log.info("Batch: converted HEIC → JPEG: %s", save_path.name)
            except Exception as e:
                log.error("Batch HEIC conversion failed: %s", e)
                continue
        elif ext in RAW_EXTS:
            save_path = _demosaic_raw(image_id, contents, ext)
            if save_path is None:
                continue
        elif ext in (".jpg", ".jpeg"):
            # Bake EXIF orientation into pixels so pipeline ≡ browser rotation.
            try:
                import io
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(contents)))
                save_path = UPLOAD_DIR / f"{image_id}.jpg"
                img.save(str(save_path), "JPEG", quality=98)
            except Exception as e:
                log.error("Batch JPEG normalize failed: %s", e)
                save_path = UPLOAD_DIR / f"{image_id}{ext}"
                save_path.write_bytes(contents)
        else:
            save_path = UPLOAD_DIR / f"{image_id}{ext}"
            save_path.write_bytes(contents)

        image_paths.append(str(save_path))

    return {"batch_id": batch_id, "count": len(image_paths), "paths": image_paths}


@router.post("/batch/process")
async def batch_process(
    paths: list[str] = Query(...),
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
    include_border: bool = Query(True),
):
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

    # Build the stock once and reuse for every image — same recipe applied
    # uniformly. has_custom mirrors the logic in /api/process so a plain
    # batch (no overrides) goes through get_stock for cache hits.
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

    _cleanup_old_batch_jobs()
    batch_id = str(uuid.uuid4())[:8]
    _batch_jobs[batch_id] = {
        "status": "processing",
        "progress": 0,
        "current": 0,
        "total": len(paths),
        "current_file": "",
        "created_at": time.time(),
    }

    t = threading.Thread(target=_run_batch, args=(batch_id, paths, built, include_border))
    t.daemon = True
    t.start()

    return {"batch_id": batch_id}


@router.get("/batch/status/{batch_id}")
async def batch_status(batch_id: str):
    if batch_id not in _batch_jobs:
        return JSONResponse(status_code=404, content={"error": "Batch not found"})
    return _batch_jobs[batch_id]


@router.get("/batch/download/{batch_id}")
async def batch_download(batch_id: str):
    out_dir = OUTPUT_DIR / batch_id
    if not out_dir.exists():
        return JSONResponse(status_code=404, content={"error": "No output"})

    # Create zip — include JPEG, TIFF, and bordered outputs
    zip_path = OUTPUT_DIR / f"{batch_id}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".tiff"):
                zf.write(f, f.name)

    return FileResponse(str(zip_path), media_type="application/zip",
                        filename=f"film_batch_{batch_id}.zip")
