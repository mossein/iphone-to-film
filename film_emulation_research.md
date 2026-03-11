# Authentic Film Emulation — Complete Research & Findings

## The Core Insight

**You cannot color grade a digital photo to look like film.** The entire approach of adjusting curves/saturation/tints in output space is fundamentally wrong. Real film is a **two-stage photochemical process**, and you must model both stages.

### The Two Stages
1. **Negative film** — light exposes silver halide crystals in 3 emulsion layers (cyan/magenta/yellow). Each layer has its own **characteristic curve** (density vs log exposure), measured via **densitometry**. The gamma is ~0.6.
2. **Print film** — the negative is optically printed onto print stock (e.g., Kodak 2383), which has its OWN steeper characteristic curve (gamma ~3.0). The print stock's S-curve is what creates the final contrast and color.

The *entire look* of film — color crossover, highlight rolloff, shadow color shifts — emerges from light passing through these two stages. No amount of Lightroom sliders or LUT color grading in output space can replicate this.

---

## What We Built (v1–v6 progression)

### v1–v4: Hand-tuned (wrong approach)
- Custom per-channel curves, saturation, tinting, halation, grain
- Each version got better but still looked like "iPhone with a filter"
- **Key lesson**: hand-tuning color in output space cannot match the nonlinear photochemical pipeline

### v5: Physically accurate (breakthrough)
- Used **spectral_film_lut** to model the actual negative→print pipeline from manufacturer datasheets
- Color science is no longer guesswork — it's computed from real Kodak/Fuji densitometry data
- Massive improvement in color authenticity

### v6: Ultimate (everything the best tools do)
- All of v5's color science
- **Volumetric grain** from actual RMS granularity data per film stock
- **Film breath** — subtle exposure/color variation across frame
- **Gate weave** — micro position shifts (film doesn't sit perfectly in gate)
- **Film acutance/MTF** — soft high frequencies + boosted mid-frequency contrast
- Dust, scratches, hair fibers
- DOF simulation, halation, bloom, vignette, film borders

---

## The Best Tools (Ranked)

### 1. Filmbox by Video Village — $995 (THE gold standard)
- Uses "dense datasets" to transform spectral radiance values
- Models the complete Kodak Vision3 ecosystem
- Two-module approach: Negative Module + Print Module
- Grain sampled from real film scans, reproducing tonal distribution across density of each channel
- Used on major Hollywood productions
- **Only works in DaVinci Resolve**
- https://videovillage.com/filmbox/

### 2. Dehancer — $449 (best balance of quality/price)
- 60+ film profiles from actual darkroom prints (not scans)
- Multi-LUT format: 3 profiles per stock at different exposures
- Grain is 3D particle simulation (volumetric, not overlay)
- Profiles built via colorimetry + densitometry of real prints
- Custom mathematical model for 3D color morphing (not standard LUT interpolation)
- Works in Resolve, Premiere, FCP, Lightroom
- https://www.dehancer.com/

### 3. Color.io — newer competitor
- "Volumetric film grain" — deconstructs image and rebuilds pixel-by-pixel through grain layer
- Film Resolution control in nanometers
- Film softness/acutance simulation
- https://www.color.io/

### 4. spectral_film_lut — FREE, open source Python
- Generates LUTs from actual film manufacturer datasheets
- Models spectral response of both negative and print film
- Has RMS granularity data per stock for authentic grain
- Supports Kodak Portra 400, Gold 200, Fuji Pro 400H, Vision3 stocks, and many more
- Can export separate negative/print stages
- https://github.com/JanLohse/spectral_film_lut

---

## Key Techniques Explained

### Film Grain — NOT Random Noise
- Real grain = silver halide crystal clumps, not pixel noise
- **RMS granularity**: measured with 48μm aperture at density 1.0 (from datasheets)
- Grain varies per channel (each emulsion layer is independent)
- Grain intensity depends on density/exposure (more in midtones, less in highlights/deep shadows)
- Grain has spatial structure — clumps/clusters, not uniform
- **Crystal types**: cubic (Kodak), tabular/T-Grain, sigma (Fuji)
- Scale depends on format: 35mm grain is coarser than medium format at same print size

### Halation
- Light passes through emulsion, bounces off film base, re-exposes from behind
- Creates warm glow around bright areas
- **Cinestill 800T** has extreme halation because the remjet anti-halation layer is removed
- Color is typically warm red-orange (from the film base color)
- Multi-scale spread for natural falloff

### Film Compression
- Film naturally compresses highlights with a gentle rolloff (never hard clips)
- Digital sensors clip abruptly; film's shoulder curve preserves highlight gradation
- This is built into the characteristic curve, not a separate effect

### Film Breath
- Subtle frame-to-frame exposure/color variation
- Caused by uneven emulsion coating, development irregularities, shutter instability
- Creates organic "living" quality that digital lacks

### Gate Weave
- Mechanical swinging of film strip in camera/projector gate
- Very subtle position shifts (1-2 pixels)
- Breaks digital perfection

### Film Acutance vs Sharpness
- Film has lower resolution than digital but often looks "sharper" due to high acutance
- Acutance = mid-frequency contrast (edge definition)
- Film's MTF: soft at high frequencies, strong in midrange
- This is why film looks "sharp but not pixel-sharp"

---

## How Dehancer Builds Film Profiles

1. **No scanner involved** — they use optical printing to exclude scanner artifacts
2. **Darkroom prints** (not negatives) are the reference — this captures the full neg→print pipeline
3. **Colorimetry + densitometry** to extract color, contrast, grain data
4. **Each profile = multi-LUT** (3 exposures) with interpolation between them
5. **Custom math model** for 3D color morphing — standard LUT interpolation is insufficient
6. **Limited patch challenge**: any color target has a finite set of patches, so they developed methods for stable interpolation between measured colors

---

## How spectral_film_lut Works

1. **Digitizes film datasheets** — the manufacturer's published characteristic curves
2. **Multi-step color pipeline** simulates light → negative → print
3. **Spectral analysis** — works in spectral domain, not just RGB
4. Has **RMS granularity curves per channel** for each stock
5. Supports separate negative + print stage export
6. Available stocks include: all Kodak Portra, Gold, Ektar, Vision3, Ultramax, Tri-X, plus Fuji Pro 400H, Superia, Velvia, Provia, C200, plus Cinestill via Vision3 500T

---

## Professional LUTs Downloaded

Located at `/Users/mo/Pictures/film_luts/`

### Print Film LUTs (the projection/display stage)
- `Rec709_Kodak_2383_D65.cube` — Industry standard Kodak print film
- `Rec709_Kodak_2393_D65.cube` — Lower contrast variant
- `Rec709_Fujifilm_3510_D65.cube` — Fuji print film
- Plus DCI-P3 variants for each

### Negative Stock LUTs (in `negative_stocks/`)
- Kodak Vision3: 50D (5203), 200T (5213), 250D (5207), 500T (5219)
- Kodak Vision2: 250D (5205), 500T (5218)
- Kodak Vision: 200T (5274), 500T (5279)
- Fujifilm Eterna: 250D (8563), 500T (8573), Vivid 160T (8543)
- Kodachrome 25

### How to Use for Neg→Print Pipeline
Apply **negative LUT first** → then **print LUT** = models actual photochemical pipeline

---

## Our Processing Scripts

All in `/Users/mo/Pictures/`:

| Script | Approach | Quality |
|--------|----------|---------|
| `film_process.py` (v1) | Hand-tuned curves, basic effects | Low — looks like Instagram filter |
| `film_process_v2.py` | Per-channel curves, undo iPhone processing | Medium — better but still digital |
| `film_process_v3.py` | 4x upscale, grain-as-resolution, pixel grid dissolution | Medium+ — better texture |
| `film_process_v4.py` | + DOF, light leaks, dust, film borders, aggressive color | Good — more convincing |
| `film_process_v5.py` | **spectral_film_lut** neg→print pipeline | **Great** — real color science |
| `film_process_v6.py` | + volumetric RMS grain, film breath, gate weave, acutance | **Best** — everything combined |

---

## Output Directories

- `/Users/mo/Pictures/film_output/` — v1
- `/Users/mo/Pictures/film_output_v2/` — v2
- `/Users/mo/Pictures/film_output_v3/` — v3
- `/Users/mo/Pictures/film_output_v4/` — v4
- `/Users/mo/Pictures/film_output_v5/` — v5
- `/Users/mo/Pictures/film_output_v6/` — v6 (ultimate)

---

## What Still Can't Be Faked

Even with the best processing, some things are baked into the iPhone capture:

1. **Depth of field** — iPhone's tiny sensor = deep DOF. We simulate it but it's never as natural as a real 50mm f/1.4 on 35mm.
2. **Lens rendering** — real film lenses have specific bokeh, flare, and aberration characteristics
3. **Dynamic range handling** — iPhone's computational HDR stacks multiple exposures. Film captures a single moment with its own latitude.
4. **Motion blur** — mechanical shutter vs electronic
5. **Aspect ratio** — 35mm film is 3:2, iPhone is taller

---

## Sources

- [Dehancer — How We Build Film Profiles](https://www.dehancer.com/learn/learn_articles/how-we-build-film-profiles)
- [Dehancer — Why Dehancer is Not a LUT](https://www.dehancer.com/learn/articles/why-dehancer-is-not-a-lut)
- [Dehancer — How Film Grain Works](https://www.dehancer.com/learn/articles/how-does-film-grain-work-in-dehancer-ofx-plugin)
- [Dehancer — Halation Simulation](https://www.dehancer.com/learn/articles/halation-in-dehancer)
- [Dehancer — Film Breath and Gate Weave](https://www.dehancer.com/learn/articles/film-breath-gate-weave)
- [Dehancer — Film Compression](https://www.dehancer.com/learn/articles/dehancer-film-compression)
- [Filmbox by Video Village](https://videovillage.com/filmbox/)
- [Filmbox — Film Print Emulation Deep Dive](https://mixinglight.com/color-grading-tutorials/fpe-part-4-filmbox-ml1143/)
- [spectral_film_lut — GitHub](https://github.com/JanLohse/spectral_film_lut)
- [Juan Melara — Print Film Emulation LUTs](https://juanmelara.com.au/blog/print-film-emulation-luts-for-download)
- [Color.io — Volumetric Film Grain](https://www.color.io/user-guide/volumetric-film-grain)
- [Building an Authentic Film Grain Simulator](https://cityframe-photography.com/blog/building-an-authentic-film-grain-simulator.html)
- [ARRI Digital Intermediate — Motion Picture Film](http://dicomp.arri.de/digital/digital_systems/DIcompanion/ch02.html)
- [Film Emulation — Wikipedia](https://en.wikipedia.org/wiki/Film_emulation)
- [Comparing Film Emulation Plugins](https://www.cinematools.co/blog/film-emulation-compared)
- [Dehancer vs Filmbox Comparison](https://filmmakingelements.com/dehancer-vs-filmbox/)
- [Dehancer Pro Review 2025](https://www.henrydavidphotography.com/blog/dehancer-pro-review-2025)
