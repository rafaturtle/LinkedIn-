"""Generate one 1080x1080 PNG infographic per post.

Reads ``data/posts.json`` and writes ``out/infographics/<number>-<slug>.png``.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "posts.json"
OUT_DIR = ROOT / "out" / "infographics"

CANVAS = 1080
MARGIN = 80
BAR_WIDTH = 28

# Roima brand colors
ROIMA_GREEN = "#46B03B"
GRAY_900 = "#212121"
GRAY_600 = "#757575"
GRAY_100 = "#F5F5F5"
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


def format_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%b %d, %Y").replace(" 0", " ")


def draw_pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
              fill: str, text_color: str = WHITE) -> int:
    pad_x, pad_y = 22, 12
    f = font(26, bold=True)
    text_w = draw.textlength(text, font=f)
    text_h = 26
    w = int(text_w + pad_x * 2)
    h = text_h + pad_y * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill)
    draw.text((x + pad_x, y + pad_y - 4), text, fill=text_color, font=f)
    return w


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

    # Left Roima-green bar
    draw.rectangle((0, 0, BAR_WIDTH, CANVAS), fill=ROIMA_GREEN)

    # Top wordmark
    wordmark_f = font(28, bold=True)
    draw.text((MARGIN, MARGIN), "ROIMA", fill=GRAY_900, font=wordmark_f)

    # Theme pill (top right)
    pill_text = post["theme"].upper()
    pill_f = font(22, bold=True)
    pill_text_w = draw.textlength(pill_text, font=pill_f)
    pill_pad_x, pill_pad_y = 20, 10
    pill_w = int(pill_text_w + pill_pad_x * 2)
    pill_h = 22 + pill_pad_y * 2
    pill_x = CANVAS - MARGIN - pill_w
    pill_y = MARGIN - 6
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

    # Big post number, theme-colored
    num_f = font(180, bold=True)
    num_y = MARGIN + 80
    draw.text((MARGIN, num_y), post["number"], fill=theme_color, font=num_f)

    # Accent rule under number
    rule_y = num_y + 200
    draw.rectangle((MARGIN, rule_y, MARGIN + 120, rule_y + 6), fill=theme_color)

    # Hook top (smaller, label-like)
    top_f = font(38, bold=False)
    top_y = rule_y + 36
    top_lines = wrap_text(draw, post["hook_top"], top_f, CANVAS - 2 * MARGIN)
    for line in top_lines:
        draw.text((MARGIN, top_y), line, fill=GRAY_600, font=top_f)
        top_y += 50

    # Hook bottom (the punch — bold, large)
    bottom_size = 64
    while bottom_size >= 40:
        bottom_f = font(bottom_size, bold=True)
        bottom_lines = wrap_text(
            draw, post["hook_bottom"], bottom_f, CANVAS - 2 * MARGIN
        )
        line_h = int(bottom_size * 1.15)
        block_h = len(bottom_lines) * line_h
        # Reserve ~180 px at the bottom for footer
        if top_y + block_h <= CANVAS - 200:
            break
        bottom_size -= 4

    by = top_y + 8
    for line in bottom_lines:
        draw.text((MARGIN, by), line, fill=GRAY_900, font=bottom_f)
        by += int(bottom_size * 1.15)

    # Footer: date (left) and post title (right, small)
    footer_f = font(22, bold=False)
    footer_title_f = font(22, bold=True)
    footer_y = CANVAS - MARGIN - 22
    draw.text(
        (MARGIN, footer_y),
        format_date(post["date"]),
        fill=GRAY_600,
        font=footer_f,
    )

    title_text = post["title"]
    title_w = draw.textlength(title_text, font=footer_title_f)
    draw.text(
        (CANVAS - MARGIN - title_w, footer_y),
        title_text,
        fill=GRAY_900,
        font=footer_title_f,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{post['number']}-{slug(post['title'])}.png"
    img.save(out_path, "PNG", optimize=True)
    return out_path


def main() -> None:
    posts = json.loads(DATA_FILE.read_text())
    for post in posts:
        path = render(post)
        print(f"  -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
