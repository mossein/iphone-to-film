#!/usr/bin/env python3
"""
iPhone to Analog Film — physically accurate negative→print emulation.
Uses spectral_film_lut for real photochemical pipeline modeling from
manufacturer datasheets. Not a LUT. Not a filter. Actual film science.
"""

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

# Add project root to path so core/ is importable
sys.path.insert(0, str(Path(__file__).parent))

from core.stocks import get_stock, get_all_stocks
from core.pipeline import process


def main():
    import argparse
    all_stocks = get_all_stocks()
    parser = argparse.ArgumentParser(description="iPhone to Analog Film")
    parser.add_argument("images", nargs="+", help="Input image paths (JPEG/PNG)")
    parser.add_argument("-s", "--stocks", nargs="+", default=["portra400"],
                        choices=list(all_stocks.keys()),
                        help="Film stocks to apply (default: portra400)")
    parser.add_argument("-o", "--output", default="./output",
                        help="Output directory (default: ./output)")
    args = parser.parse_args()

    output_dir = Path(args.output)

    for img_path in args.images:
        img_path = Path(img_path)
        print(f"\n{'='*60}")
        print(f" {img_path.name}")
        print(f"{'='*60}")
        for s in args.stocks:
            stock = get_stock(s)
            print(f"\n  [{stock['name']}]")

            def progress(step, pct):
                print(f"    {step}...")

            process(img_path, stock, output_dir, progress_callback=progress)

    print(f"\n{'='*60}")
    print(f" DONE — {output_dir}")
    print(f"{'='*60}")
    for f in sorted(output_dir.glob("*_border.jpg")):
        print(f"  {f.name} ({f.stat().st_size / (1024*1024):.1f} MB)")

if __name__ == "__main__":
    main()
