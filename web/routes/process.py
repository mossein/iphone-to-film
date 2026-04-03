"""Process route — full-resolution export with progress tracking."""

import uuid
import threading
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, FileResponse

from core.stocks import get_stock, build_custom_stock
from core.conversion import PRINT_STOCKS
from core.pipeline import process

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Job tracking: job_id -> {status, progress, step, result_clean, result_bordered}
_jobs = {}


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
    include_border: bool = Query(True),
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

    # Build stock
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

    # Create job
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"status": "processing", "progress": 0, "step": "Starting"}

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

    return FileResponse(
        file_path,
        media_type="image/jpeg",
        filename=Path(file_path).name,
    )
