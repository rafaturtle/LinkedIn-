"""Generate one 1080x1080 PNG infographic per post.

Reads ``data/posts.json`` and writes ``out/infographics/<number>-<slug>.png``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "posts.json"
OUT_DIR = ROOT / "out" / "infographics"

CANVAS = 1080
MARGIN = 80

# Roima brand colors
ROIMA_GREEN = "#46B03B"
GRAY_900 = "#212121"
GRAY_600 = "#757575"
WHITE = "#FFFFFF"

THEME_COLOR = {
    "Diagnosis": "#b05f2a",       # Burnt Orange 500
    "Data byproduct": "#337A99",  # Cerulean 500
    "OEE plateau": "#46B03B",     # Primary Green 500
    "Orchestration": "#3A7152",   # Dk Green 500
    "Workforce": "#889E2E",       # Moss Green 500
    "SME myth": "#b05f2a",        # Burnt Orange 500
    "Quality": "#337A99",         # Cerulean 500
    "OEE rethink": "#3A7152",     # Dk Green 500
}

FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size=size)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def wrap_text(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont,
              max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for w in words:
            trial = (current + " " + w).strip()
            if draw.textlength(trial, font=f) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = w
        lines.append(current)
    return lines


def render(post: dict) -> Path:
    img = Image.new("RGB", (CANVAS, CANVAS), WHITE)
    draw = ImageDraw.Draw(img)

    theme_color = THEME_COLOR.get(post["theme"], ROIMA_GREEN)
    content_max_w = CANVAS - 2 * MARGIN

    # Theme pill (top left)
    pill_text = post["theme"].upper()
    pill_f = font(24, bold=True)
    pill_text_w = draw.textlength(pill_text, font=pill_f)
    pill_pad_x, pill_pad_y = 22, 12
    pill_w = int(pill_text_w + pill_pad_x * 2)
    pill_h = 24 + pill_pad_y * 2
    pill_x = MARGIN
    pill_y = MARGIN
    draw.rounded_rectangle(
        (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
        radius=pill_h // 2,
        fill=theme_color,
    )
    draw.text(
        (pill_x + pill_pad_x, pill_y + pill_pad_y - 4),
        pill_text,
        fill=WHITE,
        font=pill_f,
    )

    # Find the largest body size that fits comfortably (vertically centered)
    top_size = 42
    bottom_size = 72
    while bottom_size >= 44:
        top_f = font(top_size, bold=False)
        bottom_f = font(bottom_size, bold=True)
        top_lines = wrap_text(draw, post["hook_top"], top_f, content_max_w)
        bottom_lines = wrap_text(
            draw, post["hook_bottom"], bottom_f, content_max_w
        )
        top_lh = int(top_size * 1.2)
        bottom_lh = int(bottom_size * 1.15)
        gap = 28
        block_h = len(top_lines) * top_lh + gap + len(bottom_lines) * bottom_lh
        if block_h <= CANVAS - 360:
            break
        bottom_size -= 4
        top_size = max(36, top_size - 2)

    # Vertically center the block in the canvas
    y = (CANVAS - block_h) // 2

    for line in top_lines:
        draw.text((MARGIN, y), line, fill=GRAY_600, font=top_f)
        y += top_lh

    y += gap - top_lh + int(top_size * 1.2)  # small adjustment after last top line

    # Accent rule before the punch
    rule_w = 100
    rule_h = 6
    draw.rectangle(
        (MARGIN, y - gap // 2 - rule_h // 2,
         MARGIN + rule_w, y - gap // 2 + rule_h // 2),
        fill=theme_color,
    )

    for line in bottom_lines:
        draw.text((MARGIN, y), line, fill=GRAY_900, font=bottom_f)
        y += bottom_lh

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug(post['title'])}.png"
    img.save(out_path, "PNG", optimize=True)
    return out_path


def main() -> None:
    posts = json.loads(DATA_FILE.read_text())
    for post in posts:
        path = render(post)
        print(f"  -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
