"""Batch route — process multiple images with one stock."""

import uuid
import threading
from pathlib import Path
from fastapi import APIRouter, Query, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
import shutil
import zipfile
import io

from core.stocks import get_stock
from core.pipeline import process

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Batch job tracking
_batch_jobs = {}


def _run_batch(batch_id, image_paths, stock_key, include_border):
    built = get_stock(stock_key)
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

    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif"):
            continue
        image_id = str(uuid.uuid4())
        save_path = UPLOAD_DIR / f"{image_id}{ext}"
        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        image_paths.append(str(save_path))

    return {"batch_id": batch_id, "count": len(image_paths), "paths": image_paths}


@router.post("/batch/process")
async def batch_process(
    paths: list[str] = Query(...),
    stock: str = Query("portra400"),
    include_border: bool = Query(True),
):
    batch_id = str(uuid.uuid4())[:8]
    _batch_jobs[batch_id] = {
        "status": "processing",
        "progress": 0,
        "current": 0,
        "total": len(paths),
        "current_file": "",
    }

    t = threading.Thread(target=_run_batch, args=(batch_id, paths, stock, include_border))
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

    # Create zip
    zip_path = OUTPUT_DIR / f"{batch_id}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.glob("*.jpg"):
            zf.write(f, f.name)

    return FileResponse(str(zip_path), media_type="application/zip",
                        filename=f"film_batch_{batch_id}.zip")
