#!/usr/bin/env python3
"""
Film Emulation Web App — FastAPI backend.
Run: python -m web.app
"""

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.routes.upload import router as upload_router
from web.routes.stocks import router as stocks_router
from web.routes.preview import router as preview_router
from web.routes.process import router as process_router
from web.routes.gallery import router as gallery_router
from web.routes.batch import router as batch_router

app = FastAPI(title="Film Emulation")

# Static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")

# API routes
app.include_router(upload_router, prefix="/api")
app.include_router(stocks_router, prefix="/api")
app.include_router(preview_router, prefix="/api")
app.include_router(process_router, prefix="/api")
app.include_router(gallery_router, prefix="/api")
app.include_router(batch_router, prefix="/api")

# Serve index.html at root
from fastapi.responses import FileResponse

@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    print("\n  Film Emulation Web App")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
