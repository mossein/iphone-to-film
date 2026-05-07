"""Runtime-resolved data paths.

In dev (`python -m web.app`) uploads/outputs sit next to the source.
In a PyInstaller-frozen .app the bundle is read-only, so we redirect those
writable dirs to the user's Application Support directory."""

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _user_data_root() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Film"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return Path(base) / "Film"
    # Linux / others
    base = os.environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(base) / "Film"


if _is_frozen():
    DATA_ROOT = _user_data_root()
else:
    # Source-tree dev: keep the current layout under web/.
    DATA_ROOT = Path(__file__).parent

UPLOAD_DIR = DATA_ROOT / "uploads"
OUTPUT_DIR = DATA_ROOT / "output"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def static_dir() -> Path:
    """Static assets live inside the bundle (read-only) when frozen."""
    if _is_frozen():
        return Path(sys._MEIPASS) / "web" / "static"
    return Path(__file__).parent / "static"
