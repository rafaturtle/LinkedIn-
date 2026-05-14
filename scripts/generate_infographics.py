"""Generate LinkedIn-post infographics for Notion posts.

Notion is the source of truth: the script queries the Posts database,
renders a 1080x1080 PNG per qualifying row into ``out/infographics/``,
commits and pushes the new files, and finally PATCHes each page so its
``Image`` field points at the new raw URL.

Default filter is ``Status == "Approved"`` AND ``Image is empty``.
Pass ``--all`` to regenerate every post regardless of status/image.

Background photos come from Pixabay (one per Theme), are cached at
``assets/backgrounds/<theme-slug>.jpg`` and committed alongside the
infographics so re-runs are deterministic.

Required env (or in a project-local .env file):
    NOTION_KEY (or NOTION_TOKEN)
    PIXABAY_KEY (or PIXABAY_API_KEY)

Optional env:
    NOTION_POSTS_DATABASE_ID
    GITHUB_RAW_BASE
    SKIP_GIT — set to "1" to skip git add/commit/push.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out" / "infographics"
BG_DIR = ROOT / "assets" / "backgrounds"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_DATABASE_ID = "467cca82-320c-4d30-9393-2dfba0629d01"

PIXABAY_API = "https://pixabay.com/api/"

CANVAS = 1080
MARGIN = 80
OVERLAY_ALPHA = 180  # 0-255, applied as solid black on top of the photo

ROIMA_GREEN = "#46B03B"
GRAY_900 = "#212121"
GRAY_600 = "#757575"
WHITE = "#FFFFFF"
LIGHT_GRAY = "#DDDDDD"

THEME_COLOR = {
    "Diagnosis": "#b05f2a",
    "Data byproduct": "#337A99",
    "OEE plateau": "#46B03B",
    "Orchestration": "#3A7152",
    "Workforce": "#889E2E",
    "SME myth": "#b05f2a",
    "Quality": "#337A99",
    "OEE rethink": "#3A7152",
}

THEME_QUERY = {
    "Diagnosis": "beverage bottling factory inspection",
    "Data byproduct": "beverage bottling line dashboard",
    "OEE plateau": "beverage bottling line automation",
    "Orchestration": "beverage industrial factory automation",
    "Workforce": "beverage bottling factory worker",
    "SME myth": "small beverage bottling factory",
    "Quality": "beverage bottling quality control",
    "OEE rethink": "beverage bottling line machinery",
}

FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size=size)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], text=True
    ).strip()


def github_repo() -> str:
    raw = git("remote", "get-url", "origin")
    m = re.search(r"[:/]([^/:]+)/([^/:]+?)(?:\.git)?/?$", raw)
    if not m:
        raise RuntimeError(f"Could not parse owner/repo from remote URL: {raw!r}")
    return f"{m.group(1)}/{m.group(2)}"


def raw_url_for(filename: str) -> str:
    override = os.environ.get("GITHUB_RAW_BASE")
    if override:
        base = override.rstrip("/")
    else:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        base = f"https://raw.githubusercontent.com/{github_repo()}/{branch}"
    return f"{base}/out/infographics/{filename}"


def notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def query_pages(token: str, database_id: str,
                payload: dict | None = None) -> list[dict]:
    pages: list[dict] = []
    cursor: str | None = None
    while True:
        body = dict(payload or {})
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"{NOTION_API}/databases/{database_id}/query",
            headers=notion_headers(token),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        pages.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages


def query_approved_without_image(token: str, database_id: str) -> list[dict]:
    return query_pages(token, database_id, {
        "filter": {
            "and": [
                {"property": "Status", "status": {"equals": "Approved"}},
                {"property": "Image", "files": {"is_empty": True}},
            ]
        }
    })


def set_notion_image(token: str, page_id: str, url: str) -> None:
    name = url.rsplit("/", 1)[-1]
    r = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=notion_headers(token),
        json={
            "properties": {
                "Image": {
                    "files": [
                        {"type": "external", "name": name, "external": {"url": url}}
                    ]
                }
            }
        },
        timeout=30,
    )
    r.raise_for_status()


def plain_text(rich_text: Iterable[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in rich_text)


def extract_hook(body: str) -> tuple[str, str]:
    body = body.replace("<br>", "\n")
    first_para = re.split(r"\n\s*\n", body, maxsplit=1)[0].strip()
    lines = [line.strip() for line in first_para.split("\n") if line.strip()]
    if len(lines) >= 2:
        return lines[0], "\n".join(lines[1:])
    if not lines:
        return "", ""
    return "", lines[0]


def post_from_page(page: dict) -> dict:
    props = page["properties"]
    title = plain_text(props["Title"]["title"]) or "untitled"
    theme = (props.get("Theme", {}).get("select") or {}).get("name") or ""
    body = plain_text(props.get("Body", {}).get("rich_text", []))
    hook_top, hook_bottom = extract_hook(body)
    return {
        "id": page["id"],
        "title": title,
        "theme": theme,
        "hook_top": hook_top,
        "hook_bottom": hook_bottom,
    }


def fetch_background(theme: str, key: str) -> Path | None:
    if not theme:
        return None
    BG_DIR.mkdir(parents=True, exist_ok=True)
    cache = BG_DIR / f"{slug(theme)}.jpg"
    if cache.exists():
        return cache
    query = THEME_QUERY.get(theme, theme)
    try:
        r = requests.get(
            PIXABAY_API,
            params={
                "key": key,
                "q": query,
                "image_type": "photo",
                "orientation": "horizontal",
                "min_width": 1280,
                "safesearch": "true",
                "per_page": 5,
            },
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            print(f"  pixabay: no hits for {query!r}", file=sys.stderr)
            return None
        url = hits[0].get("largeImageURL") or hits[0].get("webformatURL")
        if not url:
            return None
        img_r = requests.get(url, timeout=30)
        img_r.raise_for_status()
        cache.write_bytes(img_r.content)
        return cache
    except requests.RequestException as e:
        print(f"  pixabay error: {e}", file=sys.stderr)
        return None


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


def render(post: dict, pixabay_key: str | None) -> Path:
    bg_path = fetch_background(post["theme"], pixabay_key) if pixabay_key else None

    if bg_path:
        bg = Image.open(bg_path).convert("RGB")
        bg = ImageOps.fit(bg, (CANVAS, CANVAS), method=Image.LANCZOS)
        overlay = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, OVERLAY_ALPHA))
        img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
        top_color = LIGHT_GRAY
        bottom_color = WHITE
    else:
        img = Image.new("RGB", (CANVAS, CANVAS), WHITE)
        top_color = GRAY_600
        bottom_color = GRAY_900

    draw = ImageDraw.Draw(img)
    theme_color = THEME_COLOR.get(post["theme"], ROIMA_GREEN)
    content_max_w = CANVAS - 2 * MARGIN

    pill_text = (post["theme"] or "").upper()
    if pill_text:
        pill_f = font(24, bold=True)
        pill_text_w = draw.textlength(pill_text, font=pill_f)
        pill_pad_x, pill_pad_y = 22, 12
        pill_w = int(pill_text_w + pill_pad_x * 2)
        pill_h = 24 + pill_pad_y * 2
        draw.rounded_rectangle(
            (MARGIN, MARGIN, MARGIN + pill_w, MARGIN + pill_h),
            radius=pill_h // 2,
            fill=theme_color,
        )
        draw.text(
            (MARGIN + pill_pad_x, MARGIN + pill_pad_y - 4),
            pill_text,
            fill=WHITE,
            font=pill_f,
        )

    top_size = 42
    bottom_size = 72
    while True:
        top_f = font(top_size, bold=False)
        bottom_f = font(bottom_size, bold=True)
        top_lines = (
            wrap_text(draw, post["hook_top"], top_f, content_max_w)
            if post["hook_top"]
            else []
        )
        bottom_lines = wrap_text(draw, post["hook_bottom"], bottom_f, content_max_w)
        top_lh = int(top_size * 1.2)
        bottom_lh = int(bottom_size * 1.15)
        gap = 28 if top_lines else 0
        block_h = len(top_lines) * top_lh + gap + len(bottom_lines) * bottom_lh
        if block_h <= CANVAS - 360 or bottom_size <= 36:
            break
        bottom_size -= 4
        top_size = max(32, top_size - 2)

    y = (CANVAS - block_h) // 2

    for line in top_lines:
        draw.text((MARGIN, y), line, fill=top_color, font=top_f)
        y += top_lh

    if top_lines:
        rule_y = y + gap // 2 - 3
        draw.rectangle((MARGIN, rule_y, MARGIN + 100, rule_y + 6), fill=theme_color)
        y += gap

    for line in bottom_lines:
        draw.text((MARGIN, y), line, fill=bottom_color, font=bottom_f)
        y += bottom_lh

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug(post['title'])}.png"
    img.save(out_path, "PNG", optimize=True)
    return out_path


def commit_and_push(paths: list[Path]) -> bool:
    if not paths:
        return True
    try:
        git("add", *[str(p) for p in paths])
        diff = git("diff", "--cached", "--name-only")
        if not diff:
            return True
        msg = f"Update {len(paths)} infographic{'s' if len(paths) != 1 else ''}"
        git("commit", "-m", msg)
        git("push", "origin", "HEAD")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  git step failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all",
        action="store_true",
        help="Regenerate every post (ignore Status/Image filter).",
    )
    args = parser.parse_args()

    token = os.environ.get("NOTION_KEY") or os.environ.get("NOTION_TOKEN")
    if not token:
        print(
            "error: NOTION_KEY env var is not set.\n"
            "  1. Create an internal integration: "
            "https://www.notion.so/profile/integrations\n"
            "  2. Share the LinkedIn Content Calendar > Posts database with it.\n"
            "  3. export NOTION_KEY=<secret>  (or put it in .env)",
            file=sys.stderr,
        )
        return 2

    pixabay_key = (
        os.environ.get("PIXABAY_KEY") or os.environ.get("PIXABAY_API_KEY")
    )
    if not pixabay_key:
        print("warning: PIXABAY_KEY not set — using white background.", file=sys.stderr)

    database_id = os.environ.get("NOTION_POSTS_DATABASE_ID", DEFAULT_DATABASE_ID)
    if args.all:
        pages = query_pages(token, database_id)
        print(f"--all: regenerating {len(pages)} posts")
    else:
        pages = query_approved_without_image(token, database_id)

    if not pages:
        print("No qualifying posts. Nothing to do.")
        return 0

    rendered: list[tuple[dict, Path]] = []
    for page in pages:
        post = post_from_page(page)
        path = render(post, pixabay_key)
        rendered.append((post, path))
        print(f"  rendered {path.relative_to(ROOT)}  ({post['theme']})")

    if os.environ.get("SKIP_GIT") != "1":
        png_paths = [p for _, p in rendered]
        bg_paths = list(BG_DIR.glob("*.jpg")) if BG_DIR.exists() else []
        if not commit_and_push(png_paths + bg_paths):
            print("aborting Notion update because git push failed", file=sys.stderr)
            return 1

    for post, path in rendered:
        url = raw_url_for(path.name)
        set_notion_image(token, post["id"], url)
        print(f"  updated Notion {post['title']} -> {url}")

    print(f"\n{len(rendered)} rendered and synced to Notion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
