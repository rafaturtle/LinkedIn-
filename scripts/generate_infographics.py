"""Generate LinkedIn-post infographics for Notion pages that are approved
and have no Image yet, then commit, push, and update Notion's Image
field with the public raw GitHub URL.

Notion is the source of truth: the script queries the Posts database for
rows where ``Status == "Approved"`` and the ``Image`` file property is
empty, renders a 1080x1080 PNG per qualifying row into
``out/infographics/``, commits and pushes the new files, and finally
PATCHes each page so its ``Image`` field points at the new raw URL.

Required env (or in a project-local .env file):
    NOTION_KEY (or NOTION_TOKEN) — internal integration token with read
        + update access to the Posts database.

Optional env:
    NOTION_POSTS_DATABASE_ID — defaults to the LinkedIn Content
        Calendar's Posts database.
    GITHUB_RAW_BASE — override the raw-content base URL. Defaults to
        the current branch on origin.
    SKIP_GIT — set to "1" to skip git add/commit/push (just generate
        and update Notion).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out" / "infographics"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_DATABASE_ID = "467cca82-320c-4d30-9393-2dfba0629d01"

CANVAS = 1080
MARGIN = 80

ROIMA_GREEN = "#46B03B"
GRAY_900 = "#212121"
GRAY_600 = "#757575"
WHITE = "#FFFFFF"

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


def query_approved_without_image(token: str, database_id: str) -> list[dict]:
    payload = {
        "filter": {
            "and": [
                {"property": "Status", "status": {"equals": "Approved"}},
                {"property": "Image", "files": {"is_empty": True}},
            ]
        }
    }
    pages: list[dict] = []
    cursor: str | None = None
    while True:
        body = dict(payload)
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
        draw.text((MARGIN, y), line, fill=GRAY_600, font=top_f)
        y += top_lh

    if top_lines:
        rule_y = y + gap // 2 - 3
        draw.rectangle((MARGIN, rule_y, MARGIN + 100, rule_y + 6), fill=theme_color)
        y += gap

    for line in bottom_lines:
        draw.text((MARGIN, y), line, fill=GRAY_900, font=bottom_f)
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
        msg = f"Add {len(paths)} infographic{'s' if len(paths) != 1 else ''}"
        git("commit", "-m", msg)
        git("push", "origin", "HEAD")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  git step failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    load_dotenv()
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

    database_id = os.environ.get("NOTION_POSTS_DATABASE_ID", DEFAULT_DATABASE_ID)
    pages = query_approved_without_image(token, database_id)
    if not pages:
        print("No approved posts without an image. Nothing to do.")
        return 0

    rendered: list[tuple[dict, Path]] = []
    for page in pages:
        post = post_from_page(page)
        path = render(post)
        rendered.append((post, path))
        print(f"  rendered {path.relative_to(ROOT)}  ({post['theme']})")

    if os.environ.get("SKIP_GIT") != "1":
        if not commit_and_push([p for _, p in rendered]):
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
