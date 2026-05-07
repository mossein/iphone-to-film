"""Process route — full-resolution export with progress tracking."""

import re
import time
import uuid
import threading
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, FileResponse

from core.stocks import get_stock, build_custom_stock
from core.conversion import PRINT_STOCKS
from core.pipeline import process

router = APIRouter()

from web._paths import UPLOAD_DIR, OUTPUT_DIR  # bundle-aware writable dirs

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Job tracking: job_id -> {status, progress, step, result_clean, result_bordered, created_at}
_jobs = {}
_JOB_TTL = 3600  # 1 hour


def _cleanup_old_jobs():
    now = time.time()
    expired = [k for k, v in _jobs.items()
               if v.get("status") in ("done", "error") and now - v.get("created_at", now) > _JOB_TTL]
    for k in expired:
        del _jobs[k]


def _run_job(job_id, img_path, built, include_border):
    def progress(step, pct):
        _jobs[job_id]["step"] = step
        _jobs[job_id]["progress"] = pct

    try:
        result = process(
            img_path, built,
            output_dir=str(OUTPUT_DIR / job_id),
            skip_border=not include_border,
            progress_callback=progress,
        )
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["progress"] = 100

        # Find output files
        out_dir = OUTPUT_DIR / job_id
        clean_files = list(out_dir.glob("*_clean.jpg"))
        border_files = list(out_dir.glob("*_border.jpg"))
        tiff_files = list(out_dir.glob("*_clean.tiff"))
        _jobs[job_id]["result_clean"] = str(clean_files[0]) if clean_files else None
        _jobs[job_id]["result_bordered"] = str(border_files[0]) if border_files else None
        _jobs[job_id]["result_tiff"] = str(tiff_files[0]) if tiff_files else None

    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(e)


@router.post("/process")
async def start_process(
    image_id: str = Query(...),
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

    # Build stock
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

    # Create job (and clean up old ones)
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"status": "processing", "progress": 0, "step": "Starting", "created_at": time.time()}

    # Run in background thread
    t = threading.Thread(target=_run_job, args=(job_id, img_path, built, include_border))
    t.daemon = True
    t.start()

    return {"job_id": job_id}


@router.get("/status/{job_id}")
async def job_status(job_id: str):
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    job = _jobs[job_id]
    return {
        "status": job["status"],
        "progress": job["progress"],
        "step": job.get("step", ""),
        "error": job.get("error"),
    }


@router.get("/download/{job_id}")
async def download(job_id: str, variant: str = Query("clean")):
    if job_id not in _jobs:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    job = _jobs[job_id]
    if job["status"] != "done":
        return JSONResponse(status_code=400, content={"error": "Job not finished"})

    key = f"result_{variant}"
    file_path = job.get(key)
    if not file_path or not Path(file_path).exists():
        return JSONResponse(status_code=404, content={"error": f"No {variant} output"})

    media = "image/tiff" if variant == "tiff" else "image/jpeg"
    return FileResponse(
        file_path,
        media_type=media,
        filename=Path(file_path).name,
    )
