#!/usr/bin/env python3
"""Desktop launcher — starts the FastAPI server in a background thread and
opens it in a native pywebview window. Used both in dev (`python3
launcher.py`) and as the entry point for the PyInstaller .app bundle."""

import os
import socket
import sys
import threading
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*colour-science.*")

# When frozen, sys.path doesn't include the source tree; PyInstaller mounts
# everything under sys._MEIPASS. The web/core packages are bundled there.
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)
else:
    sys.path.insert(0, str(Path(__file__).parent))


def _find_free_port(preferred: int = 8765) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(port: int) -> None:
    import uvicorn
    from web.app import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning",
                access_log=False)


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)
    return False


def main() -> None:
    port = _find_free_port(8765)
    server_thread = threading.Thread(
        target=_start_server, args=(port,), daemon=True
    )
    server_thread.start()

    if not _wait_for_server(port):
        print(f"Server failed to start on port {port}", file=sys.stderr)
        sys.exit(1)

    import webview
    webview.create_window(
        "Film",
        f"http://127.0.0.1:{port}/",
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    # Cocoa is the macOS default; specifying it explicitly avoids accidental
    # fallback to a different backend if multiple are installed.
    webview.start(gui="cocoa" if sys.platform == "darwin" else None)


if __name__ == "__main__":
    main()
