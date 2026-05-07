"""Runtime hook: bypass cv2's bootstrap to avoid PyInstaller recursion.

cv2/__init__.py does `importlib.import_module("cv2")` to load the native
.so. PyInstaller's frozen importer intercepts the name and re-runs the
package's __init__.py, hitting cv2's recursion guard.

We pre-load the native extension directly and register it as `sys.modules['cv2']`
before any user code runs, so `import cv2` short-circuits to the native module.
Our pipeline only uses top-level cv2 functions (imread, GaussianBlur, etc.),
which all live on the native module."""

import os
import sys
import importlib.machinery
import importlib.util


def _bootstrap():
    if not hasattr(sys, "_MEIPASS"):
        return
    cv2_dir = os.path.join(sys._MEIPASS, "cv2")
    if not os.path.isdir(cv2_dir):
        return
    # The shipped extension is named like cv2.abi3.so; pick whichever .so is
    # present so we don't hardcode the abi tag.
    so_path = None
    for name in os.listdir(cv2_dir):
        if name.startswith("cv2") and name.endswith(".so"):
            so_path = os.path.join(cv2_dir, name)
            break
    if not so_path:
        return
    if "cv2" in sys.modules:
        return
    loader = importlib.machinery.ExtensionFileLoader("cv2", so_path)
    spec = importlib.util.spec_from_file_location(
        "cv2", so_path, loader=loader
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cv2"] = module
    loader.exec_module(module)


_bootstrap()
