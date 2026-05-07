# Notes for the next Claude session

## Project shape

Photochemical-LUT film emulator. Three entry points:

1. **CLI** — `python3 film_process.py <img> --stocks <name>...`
2. **Web** — `python3 -m web.app` → http://localhost:8000
3. **Desktop app** — `dist/Film.app` (PyInstaller bundle of the web app inside a pywebview window)

All three use the same pipeline in `core/pipeline.py`.

## Pipeline order (don't reorder without re-tuning)

```
load
  → acutance         (sRGB, perceptual softening)
  → channel_misreg   (sRGB)
  → chromatic_aberration   (sRGB, opt-in per stock)
  → spectral conv    (sRGB → sRGB; spectral_film_lut)
  → linearize
  →   halation       (linear; red-channel mask)
  →   bloom          (linear)
  → re-encode sRGB
  → color_grade      (sRGB; ASC-CDL, opt-in)
  → highlight_rolloff (sRGB; tanh shoulder)
  → breath           (sRGB)
  → vignette         (sRGB)
  → scanner_warmth   (sRGB)
  → light_leak       (sRGB; opt-in)
  → grain            (sRGB; midtone-peaked, chroma-gated)
  → base_color tint  (sRGB; for reversal/instant)
  → dust + scratches + hairs (sRGB)
  → sRGB encode → uint8/uint16
```

Halation/bloom run in linear because they're optical scattering. Everything else is in display-referred sRGB to keep tunings stable. `_to_linear` / `_to_srgb` use the piecewise IEC sRGB EOTF/OETF, not pure 2.2.

## Per-stock authoring

Stocks live in `core/stocks.py:_STOCK_DEFS`. Required fields: `name`, `category`, `description`, `neg_data`, `print_data`, `exposure_kelvin`, `exp_comp`, `sat`, `pre_flash_neg`, `black_offset`, `white_point`, `tint`, `halation_*`, `vignette`, `bloom`, `border_text`, `film_format_mm`.

Optional pipeline overrides (any stock can opt in; pipeline `.get()`s with a global default):
`acutance`, `rolloff_knee`, `rolloff_strength`, `breath`, `misregistration`,
`dust_amount`, `scanner_warmth`, `scanner_lift`, `light_leak`, `grade`,
`chromatic_aberration`, `auto_exposure`, `artifact_density`, `base_color`,
`base_color_strength`, `grain_amount`.

`grade` is an ASC-CDL dict: `{"slope": [b,g,r], "offset": [b,g,r], "power": [b,g,r], "sat": float}`. Order is BGR because the pipeline is BGR throughout.

Add a new field by:
1. Read it via `.get()` somewhere in `core/pipeline.py:process()` with a fallback.
2. Add it to `_PIPELINE_OVERRIDE_KEYS` in `core/stocks.py` and to the carry-through `**{...}` block in both `_build_stock` and `build_custom_stock`.
3. Add it to `_DEFAULT_PIPELINE_VALUES` in `core/stocks.py` so the `/api/stock-defaults/{key}` endpoint exposes it.
4. Add a `Query()` arg in both `web/routes/preview.py` and `web/routes/process.py` and include it in the `pipeline = {...}` dict.
5. Add a slider to `web/static/index.html` with `data-param` matching the API name; the JS auto-wires it.

## Web UI

- `web/static/index.html` + `app.js` + `style.css` — vanilla JS, no framework.
- Sliders: any `<div class="prm" data-param="X">` with a `<input type="range">` inside. JS captures the slider's HTML default as the baseline; moving away from baseline sends an override. On stock change, the "Look" sliders are re-baselined to that stock's authored values via `/api/stock-defaults/{key}`.
- The "Photochemical" sliders (exp_comp/sat/pre_flash_neg/black_offset/white_point/tint) survive stock changes; the "Look" sliders reset.

## Paths (bundle-aware)

`web/_paths.py` resolves `UPLOAD_DIR`, `OUTPUT_DIR`, and `static_dir()`:
- Dev: `web/uploads`, `web/output`, `web/static`
- Frozen .app: `~/Library/Application Support/Film/{uploads,output}`, `<bundle>/web/static`

Every route imports from `web._paths`. Don't reintroduce hardcoded `Path(__file__).parent.parent / "uploads"` anywhere.

## Building the .app (macOS)

```bash
# One-time setup
pip install --break-system-packages pywebview pyinstaller

# Build
rm -rf build dist
pyinstaller film.spec --noconfirm

# Run
open dist/Film.app
# or for logs:
dist/Film.app/Contents/MacOS/Film
```

Output: `dist/Film.app` (~460 MB). Build takes ~1 min.

The spec collects `spectral_film_lut`, `colour`, `rawpy`, `cv2` via `collect_all`. Runtime hook `pyi_rthook_cv2.py` pre-loads `cv2.abi3.so` to bypass opencv-python's bootstrap recursion under PyInstaller — don't remove it.

If you add a new top-level Python dependency that does dynamic imports or ships data files, add it to `film.spec`'s `collect_all` list.

To distribute outside your machine: `xattr -dr com.apple.quarantine dist/Film.app` to skip Gatekeeper warning, or codesign properly. The bundle is unsigned by default.

## Things that look weird but are intentional

- **Channel misregistration before spectral conversion** — it's a digital-source softening, applied while the image is still display-referred.
- **Highlight rolloff after halation** — halation can drive linear values >1; the tanh shoulder absorbs them into a film-like compress.
- **B&W stocks share one grain channel; color uses luma + gated chroma** — gating chroma below 6% gray fixes the magenta/green speckle in shadows.
- **Sprocket holes only on `film_format_mm == 35`** — medium-format and instant don't have them on the long edges.
- **The web upload route does a richer ProPhoto-domain RAW demosaic; the CLI loader (`_load_raw_as_float` in `core/pipeline.py`) does a simpler sRGB-domain demosaic.** Both work; the web path is preferred for RAW.

## What's known broken / unfinished

- The spectral LUT can produce NaN for out-of-gamut inputs. `apply_film_conversion` clips them. Don't remove that clip — it cascades through Gaussian blurs.
- Auto-exposure (`auto_exposure` field) is off by default everywhere; no stock authors it yet. Tune per stock if you want to enable.
- `grade` (CDL) field is plumbed end-to-end but no stock uses it yet.
- `light_leak`, `chromatic_aberration`, `artifact_density`, `base_color` similarly: plumbed, no per-stock authoring.

## Don't do

- Don't apply optical effects in sRGB-encoded space "to keep things simple." Halation and bloom belong in linear; everything else has been tuned in sRGB.
- Don't add a new `Path(__file__).parent` reference for writable data — it'll break inside the bundle.
- Don't `--collect-all` `numpy` or `scipy` in the spec; they're auto-detected and explicit collection breaks the build.
- Don't `pip install` packages without `--break-system-packages` on this machine; the user runs Homebrew Python with PEP 668.
