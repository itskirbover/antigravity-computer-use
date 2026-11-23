#!/usr/bin/env python3
"""
Visual Grounding & OCR Helper (`vision_helper.py`) for Antigravity Computer Use.

Capabilities:
1. `grid`: Overlays a labeled coordinate grid (with major and minor tick lines and
   high-contrast coordinate pills) on a screenshot so the agent can visually read
   exact (x, y) pixel coordinates via `read_file`.
2. `crop`: Crops a region around `(x, y)` or `(x, y, w, h)` with a local sub-grid
   for sub-pixel / fine UI target verification.
3. `mark`: Draws a red crosshair and circle on `(x, y)` to verify click coordinates
   before or after clicking.
4. `ocr`: Runs `tesseract` (TSV output) on an image to return all detected text boxes
   with their exact center `(x, y)` and bounding box `(left, top, width, height)`.
5. `find-text`: Searches for a target word/phrase on screen via OCR and returns the
   exact center `(x, y)` pixel coordinates to click.
"""

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps


def draw_grid(
    input_path: str,
    output_path: str,
    step: int = 200,
    sub_step: int = 100,
) -> Dict[str, Any]:
    """Overlay a coordinate grid with labeled intersections on an image."""
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as base_im:
        im = base_im.convert("RGBA")
        w, h = im.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.load_default()

        # Draw minor grid lines
        if sub_step > 0:
            for x in range(0, w, sub_step):
                if x % step != 0:
                    draw.line([(x, 0), (x, h)], fill=(0, 220, 255, 70), width=1)
            for y in range(0, h, sub_step):
                if y % step != 0:
                    draw.line([(0, y), (w, y)], fill=(0, 220, 255, 70), width=1)

        # Draw major grid lines
        for x in range(0, w, step):
            draw.line([(x, 0), (x, h)], fill=(255, 50, 50, 150), width=1)
        for y in range(0, h, step):
            draw.line([(0, y), (w, y)], fill=(255, 50, 50, 150), width=1)

        # Draw coordinate labels at major intersections
        for y in range(0, h, step):
            for x in range(0, w, step):
                if x == 0 and y == 0:
                    continue
                label = f"{x},{y}"
                bbox = draw.textbbox((0, 0), label, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                px = min(w - tw - 6, max(2, x + 3))
                py = min(h - th - 6, max(2, y + 3))
                draw.rectangle(
                    [px - 2, py - 1, px + tw + 2, py + th + 2],
                    fill=(0, 0, 0, 185),
                    outline=(255, 255, 0, 200),
                )
                draw.text((px, py), label, fill=(255, 255, 0, 255), font=font)

        combined = Image.alpha_composite(im, overlay).convert("RGB")
        combined.save(dst, "PNG")

    return {
        "ok": True,
        "input": str(src),
        "output": str(dst),
        "image_width": w,
        "image_height": h,
        "step": step,
        "sub_step": sub_step,
    }


def mark_point(
    input_path: str,
    output_path: str,
    x: int,
    y: int,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Draw a high-visibility bullseye marker at (x, y) on an image."""
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as base_im:
        im = base_im.convert("RGBA")
        w, h = im.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.load_default()

        for r, color in [(24, (255, 0, 0, 220)), (12, (255, 255, 0, 240)), (3, (255, 0, 0, 255))]:
            draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=2)
        draw.line([(x - 32, y), (x + 32, y)], fill=(255, 0, 0, 230), width=2)
        draw.line([(x, y - 32), (x, y + 32)], fill=(255, 0, 0, 230), width=2)

        tag = label or f"({x}, {y})"
        bbox = draw.textbbox((0, 0), tag, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lx = min(w - tw - 8, max(4, x + 16))
        ly = min(h - th - 8, max(4, y - th - 12))
        draw.rectangle([lx - 3, ly - 2, lx + tw + 3, ly + th + 3], fill=(0, 0, 0, 210), outline=(255, 0, 0, 255))
        draw.text((lx, ly), tag, fill=(255, 255, 0, 255), font=font)

        combined = Image.alpha_composite(im, overlay).convert("RGB")
        combined.save(dst, "PNG")

    return {"ok": True, "output": str(dst), "x": x, "y": y, "image_width": w, "image_height": h}


def crop_region(
    input_path: str,
    output_path: str,
    x: int,
    y: int,
    width: int,
    height: int,
    with_grid: bool = True,
    grid_step: int = 50,
) -> Dict[str, Any]:
    """Crop a region from an image and optionally annotate with its original global coordinates."""
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as base_im:
        im = base_im.convert("RGBA")
        img_w, img_h = im.size
        x0 = max(0, min(x, img_w - 1))
        y0 = max(0, min(y, img_h - 1))
        x1 = max(x0 + 1, min(x0 + width, img_w))
        y1 = max(y0 + 1, min(y0 + height, img_h))

        cropped = im.crop((x0, y0, x1, y1))
        cw, ch = cropped.size

        if with_grid and grid_step > 0:
            overlay = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            font = ImageFont.load_default()

            start_gx = (x0 // grid_step) * grid_step
            start_gy = (y0 // grid_step) * grid_step

            for gx in range(start_gx, x1, grid_step):
                if gx >= x0:
                    lx = gx - x0
                    draw.line([(lx, 0), (lx, ch)], fill=(255, 60, 60, 140), width=1)
            for gy in range(start_gy, y1, grid_step):
                if gy >= y0:
                    ly = gy - y0
                    draw.line([(0, ly), (cw, ly)], fill=(255, 60, 60, 140), width=1)

            for gy in range(start_gy, y1, grid_step):
                for gx in range(start_gx, x1, grid_step):
                    if gx >= x0 and gy >= y0:
                        lx, ly = gx - x0, gy - y0
                        label = f"{gx},{gy}"
                        bbox = draw.textbbox((0, 0), label, font=font)
                        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                        if lx + tw + 4 < cw and ly + th + 4 < ch:
                            draw.rectangle([lx + 1, ly + 1, lx + tw + 4, ly + th + 3], fill=(0, 0, 0, 185))
                            draw.text((lx + 2, ly + 2), label, fill=(255, 255, 0, 255), font=font)

            cropped = Image.alpha_composite(cropped, overlay)

        cropped.convert("RGB").save(dst, "PNG")

    return {
        "ok": True,
        "output": str(dst),
        "origin_x": x0,
        "origin_y": y0,
        "crop_width": cw,
        "crop_height": ch,
    }


def _detect_tesseract_lang(preferred: str = "eng") -> str:
    """Detect available Tesseract language pack, falling back to a Latin-script pack if 'eng' is missing."""
    try:
        res = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=3)
        langs = [
            ln.strip()
            for ln in res.stdout.splitlines()
            if ln.strip() and not ln.lower().startswith("list of available")
        ]
        if preferred in langs:
            return preferred
        for candidate in [l for l in langs if l != "osd"]:
            return candidate
    except Exception:
        pass
    return preferred


def run_ocr(
    input_path: str,
    min_conf: float = 35.0,
    lang: Optional[str] = None,
    mode: str = "normal",
) -> Dict[str, Any]:
    """Run Tesseract OCR on an image and extract word and line bounding boxes + click centers."""
    if not shutil.which("tesseract"):
        return {"ok": False, "error": "tesseract binary not found in PATH"}

    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        return {"ok": False, "error": f"Image not found: {src}"}

    ocr_target = src
    tmp_proc: Optional[Path] = None
    with Image.open(src) as im:
        img_w, img_h = im.size
        if mode == "invert":
            tmp_proc = Path(f"/tmp/_agy_ocr_{os.getpid()}_inv.png")
            ImageOps.invert(im.convert("L")).save(tmp_proc, "PNG")
            ocr_target = tmp_proc
        elif mode == "bright-text":
            tmp_proc = Path(f"/tmp/_agy_ocr_{os.getpid()}_bright.png")
            gray = im.convert("L")
            gray.point(lambda p: 0 if p > 175 else 255).save(tmp_proc, "PNG")
            ocr_target = tmp_proc

    chosen_lang = lang or _detect_tesseract_lang("eng")
    try:
        res = subprocess.run(
            ["tesseract", str(ocr_target), "stdout", "-l", chosen_lang, "--psm", "11", "tsv"],
            capture_output=True, text=True, timeout=15
        )
    finally:
        if tmp_proc and tmp_proc.exists():
            try:
                tmp_proc.unlink()
            except OSError:
                pass
    if res.returncode != 0:
        return {"ok": False, "error": res.stderr.strip(), "lang": chosen_lang}

    words: List[Dict[str, Any]] = []
    lines_map: Dict[str, Dict[str, Any]] = {}

    reader = csv.DictReader(io.StringIO(res.stdout), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf", -1))
        except ValueError:
            conf = -1.0
        if conf < min_conf:
            continue

        left = int(row["left"])
        top = int(row["top"])
        width = int(row["width"])
        height = int(row["height"])
        cx = left + width // 2
        cy = top + height // 2

        word_item = {
            "text": text,
            "center_x": cx,
            "center_y": cy,
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "confidence": round(conf, 1),
        }
        words.append(word_item)

        line_key = f"{row.get('block_num')}_{row.get('par_num')}_{row.get('line_num')}"
        if line_key not in lines_map:
            lines_map[line_key] = {
                "words": [text],
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
            }
        else:
            lm = lines_map[line_key]
            lm["words"].append(text)
            lm["left"] = min(lm["left"], left)
            lm["top"] = min(lm["top"], top)
            lm["right"] = max(lm["right"], left + width)
            lm["bottom"] = max(lm["bottom"], top + height)

    lines: List[Dict[str, Any]] = []
    for lm in lines_map.values():
        lw = lm["right"] - lm["left"]
        lh = lm["bottom"] - lm["top"]
        lines.append({
            "text": " ".join(lm["words"]),
            "center_x": lm["left"] + lw // 2,
            "center_y": lm["top"] + lh // 2,
            "left": lm["left"],
            "top": lm["top"],
            "width": lw,
            "height": lh,
        })

    return {
        "ok": True,
        "image": str(src),
        "image_width": img_w,
        "image_height": img_h,
        "line_count": len(lines),
        "word_count": len(words),
        "lines": lines,
        "words": words,
    }


def find_text(input_path: str, query: str, exact: bool = False, min_conf: float = 30.0) -> Dict[str, Any]:
    """Locate a word or phrase on screen via OCR (testing both standard and dark-mode inverted contrast)."""
    q = query.strip().lower()
    matches: List[Dict[str, Any]] = []
    seen_coords = set()

    for ocr_mode in ("normal", "bright-text", "invert"):
        ocr_data = run_ocr(input_path, min_conf=min_conf, mode=ocr_mode)
        if not ocr_data.get("ok"):
            if ocr_mode == "normal":
                return ocr_data
            continue

        for w in ocr_data["words"]:
            wt = w["text"].lower()
            if (exact and wt == q) or (not exact and q in wt):
                key = ("word", wt, w["center_x"] // 8, w["center_y"] // 8)
                if key not in seen_coords:
                    seen_coords.add(key)
                    matches.append({"match_type": "word", "ocr_mode": ocr_mode, **w})

        for ln in ocr_data["lines"]:
            lt = ln["text"].lower()
            if (exact and lt == q) or (not exact and q in lt):
                key = ("line", lt, ln["center_x"] // 8, ln["center_y"] // 8)
                if key not in seen_coords:
                    seen_coords.add(key)
                    matches.append({"match_type": "line", "ocr_mode": ocr_mode, **ln})

        if matches:
            break

    return {
        "ok": True,
        "query": query,
        "image_width": ocr_data.get("image_width"),
        "image_height": ocr_data.get("image_height"),
        "match_count": len(matches),
        "matches": matches,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vision_helper.py",
        description="Visual Grounding & OCR Helper for Antigravity Computer Use",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # grid
    p_grid = sub.add_parser("grid", help="Overlay a labeled coordinate grid on a screenshot")
    p_grid.add_argument("input", help="Input screenshot PNG path")
    p_grid.add_argument("-o", "--output", default="/tmp/agy_screenshot_grid.png", help="Output PNG path")
    p_grid.add_argument("--step", type=int, default=200, help="Major grid spacing in pixels")
    p_grid.add_argument("--sub-step", type=int, default=100, help="Minor grid spacing in pixels")

    # mark
    p_mark = sub.add_parser("mark", help="Draw a crosshair marker at (x, y) on an image to verify target")
    p_mark.add_argument("input", help="Input screenshot PNG path")
    p_mark.add_argument("x", type=int, help="X coordinate")
    p_mark.add_argument("y", type=int, help="Y coordinate")
    p_mark.add_argument("-o", "--output", default="/tmp/agy_screenshot_mark.png", help="Output PNG path")
    p_mark.add_argument("-l", "--label", help="Optional custom label")

    # crop
    p_crop = sub.add_parser("crop", help="Crop a region (x, y, w, h) with global coordinate grid labels")
    p_crop.add_argument("input", help="Input screenshot PNG path")
    p_crop.add_argument("x", type=int, help="Top-left X coordinate")
    p_crop.add_argument("y", type=int, help="Top-left Y coordinate")
    p_crop.add_argument("width", type=int, help="Crop width")
    p_crop.add_argument("height", type=int, help="Crop height")
    p_crop.add_argument("-o", "--output", default="/tmp/agy_screenshot_crop.png", help="Output PNG path")
    p_crop.add_argument("--no-grid", action="store_true", help="Disable coordinate grid overlay on crop")
    p_crop.add_argument("--step", type=int, default=50, help="Grid step in pixels")

    # ocr
    p_ocr = sub.add_parser("ocr", help="Extract all visible text and bounding-box click centers via Tesseract")
    p_ocr.add_argument("input", help="Input screenshot PNG path")
    p_ocr.add_argument("--min-conf", type=float, default=35.0, help="Minimum OCR confidence (0-100)")

    # find-text
    p_find = sub.add_parser("find-text", help="Find on-screen coordinates of a specific text label via OCR")
    p_find.add_argument("input", help="Input screenshot PNG path")
    p_find.add_argument("query", help="Text string to find")
    p_find.add_argument("--exact", action="store_true", help="Require exact case-insensitive match")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "grid":
        res = draw_grid(args.input, args.output, step=args.step, sub_step=args.sub_step)
    elif args.subcommand == "mark":
        res = mark_point(args.input, args.output, args.x, args.y, label=args.label)
    elif args.subcommand == "crop":
        res = crop_region(
            args.input, args.output,
            args.x, args.y, args.width, args.height,
            with_grid=not args.no_grid, grid_step=args.step,
        )
    elif args.subcommand == "ocr":
        res = run_ocr(args.input, min_conf=args.min_conf)
    elif args.subcommand == "find-text":
        res = find_text(args.input, args.query, exact=args.exact)
    else:
        res = {"ok": False, "error": f"Unknown subcommand: {args.subcommand}"}

    print(json.dumps(res, indent=2))
    return 0 if res.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())
