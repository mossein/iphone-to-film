"""Upload route — save image and return UUID. Converts HEIC and RAW to JPEG/TIFF."""

import re
import uuid
import logging
from io import BytesIO as _BytesIO
from pathlib import Path
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
import numpy as np
import cv2
import rawpy

register_heif_opener()
log = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

RAW_EXTS = (".cr3", ".cr2", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf")


def _demosaic_raw(image_id: str, contents: bytes, ext: str) -> Path | None:
    """Demosaic a RAW file into a 16-bit sRGB TIFF the pipeline can read.

    #1 — highlight reconstruction + no_auto_bright + tight auto-WB: preserves
         every stop the sensor captured instead of letting rawpy push a hot curve
         and clip to match a default JPEG render.
    #2 — ProPhoto gamut demosaic, then colour-science converts linear ProPhoto →
         linear sRGB → sRGB-encoded. Saturated reds/deep blues survive the RAW
         decode instead of getting gamut-clipped to sRGB primaries at demosaic
         time; clipping (if needed) happens once at the end, in the space the
         film LUT expects.
    """
    temp_path = UPLOAD_DIR / f"{image_id}{ext}"
    temp_path.write_bytes(contents)

    try:
        with rawpy.imread(str(temp_path)) as raw:
            rgb_wide = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                highlight_mode=rawpy.HighlightMode.ReconstructDefault,
                output_bps=16,
                gamma=(1, 1),  # linear — we apply sRGB encoding ourselves after gamut convert
                output_color=rawpy.ColorSpace.ProPhoto,
            )
        # uint16 linear ProPhoto → float32 linear ProPhoto
        lin_prophoto = rgb_wide.astype(np.float32) / 65535.0

        # Linear ProPhoto → linear sRGB (colorimetric). Values outside sRGB gamut
        # become negative or >1; the film LUT will later compress rather than clip.
        try:
            import colour
            lin_srgb = colour.RGB_to_RGB(
                lin_prophoto,
                input_colourspace=colour.RGB_COLOURSPACES['ProPhoto RGB'],
                output_colourspace=colour.RGB_COLOURSPACES['sRGB'],
                apply_cctf_decoding=False,
                apply_cctf_encoding=False,
            ).astype(np.float32)
        except Exception as e:
            log.warning("colour-science ProPhoto→sRGB failed (%s); falling back to matrix", e)
            # Bradford-adapted ProPhoto D50 → sRGB D65 matrix
            M = np.array([
                [ 2.0336, -0.7380, -0.2956],
                [-0.2257,  1.2317, -0.0060],
                [ 0.0105, -0.1453,  1.1348],
            ], dtype=np.float32)
            lin_srgb = lin_prophoto @ M.T

        # Exposure floor: no_auto_bright leaves linear data; stretch so the 99th
        # percentile lands around 0.9. Keeps highlights + prevents image from
        # being uselessly dark when the scene didn't expose to the right.
        p99 = float(np.percentile(lin_srgb, 99))
        if p99 > 1e-4:
            lin_srgb = lin_srgb * min(0.9 / p99, 4.0)

        # sRGB encode (OETF). Negative values clip here.
        lin_srgb = np.clip(lin_srgb, 0.0, None)
        try:
            import colour
            srgb = colour.cctf_encoding(lin_srgb, function='sRGB').astype(np.float32)
        except Exception:
            # Piecewise sRGB fallback
            srgb = np.where(
                lin_srgb <= 0.0031308,
                12.92 * lin_srgb,
                1.055 * np.power(np.clip(lin_srgb, 1e-10, None), 1/2.4) - 0.055,
            ).astype(np.float32)
        srgb = np.clip(srgb, 0.0, 1.0)

        # uint16 BGR for OpenCV
        bgr = (srgb[:, :, ::-1] * 65535.0 + 0.5).astype(np.uint16)
        save_path = UPLOAD_DIR / f"{image_id}.tiff"
        cv2.imwrite(str(save_path), bgr)
        temp_path.unlink()
        log.info("Demosaiced RAW (ProPhoto + hlrec + no-auto-bright) → 16-bit sRGB TIFF: %s",
                 save_path.name)
        return save_path
    except Exception as e:
        log.error("RAW demosaic failed: %s", e)
        if temp_path.exists():
            try: temp_path.unlink()
            except Exception: pass
        return None


@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif") + RAW_EXTS:
        return JSONResponse(status_code=400, content={"error": "Unsupported format"})

    image_id = str(uuid.uuid4())

    # Read the entire file into memory first (avoids async file handle issues)
    contents = await file.read()

    if ext in (".heic", ".heif"):
        # Convert HEIC/HEIF to JPEG — OpenCV can't read HEIC.
        # exif_transpose bakes EXIF orientation into pixels so cv2 + browser agree.
        temp_path = UPLOAD_DIR / f"{image_id}{ext}"
        temp_path.write_bytes(contents)

        try:
            img = ImageOps.exif_transpose(Image.open(temp_path))
            save_path = UPLOAD_DIR / f"{image_id}.jpg"
            img.save(str(save_path), "JPEG", quality=98)
            temp_path.unlink()
            log.info("Converted HEIC → JPEG: %s", save_path.name)
        except Exception as e:
            log.error("HEIC conversion failed: %s", e)
            save_path = temp_path
    elif ext in RAW_EXTS:
        save_path = _demosaic_raw(image_id, contents, ext)
        if save_path is None:
            return JSONResponse(status_code=500, content={"error": "RAW decode failed"})
    elif ext in (".jpg", ".jpeg"):
        # Re-encode JPEG through PIL so EXIF orientation is baked into pixels —
        # otherwise cv2.imread (which ignores EXIF) and the browser (which honors
        # it) will disagree and the before/after view rotates differently.
        try:
            img = ImageOps.exif_transpose(Image.open(_BytesIO(contents)))
            save_path = UPLOAD_DIR / f"{image_id}.jpg"
            img.save(str(save_path), "JPEG", quality=98)
        except Exception as e:
            log.error("JPEG normalize failed: %s", e)
            save_path = UPLOAD_DIR / f"{image_id}{ext}"
            save_path.write_bytes(contents)
    else:
        save_path = UPLOAD_DIR / f"{image_id}{ext}"
        save_path.write_bytes(contents)

    return {"id": image_id, "filename": file.filename, "path": str(save_path),
            "url": f"/api/original/{image_id}"}


@router.get("/original/{image_id}")
async def get_original(image_id: str):
    if not _UUID_RE.match(image_id):
        return JSONResponse(status_code=400, content={"error": "Invalid image ID"})
    for ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".heif"):
        p = UPLOAD_DIR / f"{image_id}{ext}"
        if p.exists():
            if ext in (".heic", ".heif"):
                try:
                    img = Image.open(p)
                    jpg_path = UPLOAD_DIR / f"{image_id}.jpg"
                    img.save(str(jpg_path), "JPEG", quality=98)
                    p.unlink()
                    return FileResponse(str(jpg_path), media_type="image/jpeg")
                except Exception:
                    return JSONResponse(status_code=500, content={"error": "HEIC conversion failed"})
            if ext in (".tiff", ".tif"):
                # Browsers can't display TIFF — emit a cached JPEG preview alongside it.
                preview = UPLOAD_DIR / f"{image_id}_preview.jpg"
                if not preview.exists():
                    try:
                        img = Image.open(p)
                        # 16-bit TIFF → 8-bit for browser display.
                        if img.mode not in ("RGB", "L"):
                            img = img.convert("RGB")
                        img.save(str(preview), "JPEG", quality=92)
                    except Exception as e:
                        return JSONResponse(status_code=500, content={"error": f"TIFF preview failed: {e}"})
                return FileResponse(str(preview), media_type="image/jpeg")
            return FileResponse(str(p))
    return JSONResponse(status_code=404, content={"error": "Not found"})
