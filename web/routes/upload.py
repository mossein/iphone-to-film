"""Upload route — save image and return UUID."""

import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif"):
        return JSONResponse(status_code=400, content={"error": "Unsupported format"})

    image_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{image_id}{ext}"

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"id": image_id, "filename": file.filename, "path": str(save_path)}
