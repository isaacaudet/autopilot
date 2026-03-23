"""
Per-clip branded thumbnail generator for YouTube Shorts.

Layout (1080x1920 portrait):
  - Best frame from clip as background, blurred + darkened for contrast
  - Clean frame inset (top 55% of image, no blur) — the "action window"
  - Dark gradient lower half for text legibility
  - Streamer name (medium, above title)
  - Clip title keywords (LARGE, center-bottom)
  - Game-accent color bar at very bottom
  - Channel brand mark bottom-left corner
"""

from __future__ import annotations

import logging
import re
import subprocess
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

# Canvas size
W, H = 1080, 1920

# Game color schemes: (primary, secondary)
GAME_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "deadlock":  ((0, 255, 220),  (255, 100, 0)),   # cyan + orange
    "marathon":  ((0, 255, 136),  (0, 102, 255)),    # green + blue
    "arc raiders": ((255, 200, 0), (220, 50, 0)),    # gold + red
    "valorant":  ((255, 70, 84),  (255, 180, 0)),    # red + gold
    "default":   ((0, 255, 220),  (255, 100, 0)),
}

FONT_PATH = "/System/Library/Fonts/Supplemental/Impact.ttf"
# Fallback to system sans-serif
FONT_FALLBACK = "/System/Library/Fonts/Helvetica.ttc"

# Title words to strip (streaming noise)
_STRIP_RE = re.compile(
    r"^\s*(clips?|highlights?|best\s+clips?|daily|stream|live|twitch|youtube)\s*[-|:]\s*",
    re.IGNORECASE,
)
_JUNK_WORDS = frozenset(
    ["the", "a", "an", "is", "it", "in", "on", "at", "of", "to", "and", "or", "but", "for"]
)


# ─────────────────────────── helpers ────────────────────────────────────────

def _game_key(game: str) -> str:
    return (game or "default").lower().strip()


def _game_colors(game: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    key = _game_key(game)
    for k, v in GAME_COLORS.items():
        if k in key:
            return v
    return GAME_COLORS["default"]


def _clean_title(raw: str, max_words: int = 6) -> str:
    """Strip streamer prefix and junk; return uppercased short title."""
    t = raw.strip()
    # Remove "streamer — title" prefix pattern
    t = re.sub(r"^[^—\-–]{1,30}[—\-–]\s*", "", t)
    # Remove common streaming noise
    t = _STRIP_RE.sub("", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    # Take first max_words non-junk words
    words = t.split()
    selected: list[str] = []
    for w in words:
        clean = re.sub(r"[^\w\s']", "", w)
        if len(selected) < max_words:
            selected.append(clean)
        if len(selected) >= max_words:
            break
    return " ".join(selected).upper() if selected else t.upper()[:40]


def _get_font(size: int):
    try:
        from PIL import ImageFont
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            return ImageFont.truetype(FONT_FALLBACK, size)
    except Exception:
        from PIL import ImageFont
        return ImageFont.load_default()


def _draw_shadowed_text(
    draw,
    pos: tuple[int, int],
    text: str,
    font,
    fill: tuple[int, int, int],
    shadow: tuple[int, int, int] = (0, 0, 0),
    border: int = 6,
    shadow_offset: int = 4,
) -> None:
    x, y = pos
    # Thick border
    for dx in range(-border, border + 1):
        for dy in range(-border, border + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=shadow)
    # Drop shadow
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow)
    # Main text
    draw.text((x, y), text, font=font, fill=fill)


def _draw_centered_text(
    draw,
    y: int,
    text: str,
    font,
    fill: tuple,
    shadow: tuple = (0, 0, 0),
    border: int = 6,
    shadow_offset: int = 4,
    max_width: int = W - 60,
) -> int:
    """Draw centered text; returns the y-coordinate after the text."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = max(30, (W - tw) // 2)
    # Wrap if too wide
    if tw > max_width:
        # Estimate chars per line
        avg_char = tw / max(len(text), 1)
        chars_per_line = max(8, int(max_width / avg_char))
        lines = textwrap.wrap(text, width=chars_per_line)
        for line in lines:
            _draw_centered_text(draw, y, line, font, fill, shadow, border, shadow_offset, max_width)
            bbox = draw.textbbox((0, 0), line, font=font)
            y += (bbox[3] - bbox[1]) + 12
        return y
    _draw_shadowed_text(draw, (x, y), text, font, fill, shadow, border, shadow_offset)
    return y + (bbox[3] - bbox[1]) + 12


# ─────────────────────────── frame extraction ───────────────────────────────

def _find_best_timestamp(video_path: str) -> float | None:
    """Find an action-packed timestamp using scene change detection."""
    from clipper.config import get_ffmpeg, get_ffprobe

    try:
        # Get duration
        probe = subprocess.run(
            [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip() or "0")
        if duration <= 0:
            return None

        # Scene detection — find the highest-score scene change
        result = subprocess.run(
            [get_ffmpeg(), "-i", video_path,
             "-vf", "select='gt(scene,0.15)',showinfo",
             "-vsync", "vfr", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # Parse pts_time from showinfo output
        timestamps: list[float] = []
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                m = re.search(r"pts_time:([\d.]+)", line)
                if m:
                    t = float(m.group(1))
                    # Prefer mid-clip timestamps (avoid first/last 10%)
                    if 0.1 * duration < t < 0.85 * duration:
                        timestamps.append(t)

        if timestamps:
            # Pick the scene change closest to 35% mark (often peak action)
            target = duration * 0.35
            return min(timestamps, key=lambda t: abs(t - target))

        # Fallback: 30% of duration
        return duration * 0.30

    except Exception as e:
        logger.debug("Scene detection failed: %s", e)
        return None


def extract_frame(video_path: str, output_path: str, timestamp: float | None = None) -> str | None:
    """Extract a single frame from video_path at timestamp, save to output_path."""
    from clipper.config import get_ffmpeg, get_ffprobe

    try:
        if timestamp is None:
            # 30% fallback
            probe = subprocess.run(
                [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True,
            )
            duration = float(probe.stdout.strip() or "30")
            timestamp = duration * 0.30

        subprocess.run(
            [get_ffmpeg(), "-y", "-ss", str(timestamp), "-i", video_path,
             "-vframes", "1", "-q:v", "1", output_path],
            capture_output=True, text=True, check=True,
        )
        return output_path
    except Exception as e:
        logger.warning("Frame extraction failed: %s", e)
        return None


# ─────────────────────────── main generator ─────────────────────────────────

def generate_clip_thumbnail(
    clip: dict,
    video_path: str | Path,
    output_path: str | Path,
    game: str = "",
) -> Path | None:
    """Generate a branded Shorts thumbnail (1080×1920) for a single clip.

    Design:
      - Full video frame fills the canvas (no black panels)
      - Subtle vignette darkening + saturation boost for pop
      - Heavy gradient at the very bottom (bottom 35%) for text readability
      - Streamer name (medium, secondary color, above title)
      - Title keywords (LARGE Impact, white, centered in lower third)
      - Thin accent bar at the bottom edge
    """
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    except ImportError:
        logger.warning("Pillow not installed — cannot generate thumbnail")
        return None

    video_path = str(video_path)
    output_path = Path(output_path)

    # ── 1. Extract best frame ──────────────────────────────────────────────
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_frame = tmp.name

    # Use the hook frame (0.8s in) — this is when the opening title text is fully
    # visible on screen, so the channel grid shows the clip's title in the thumbnail.
    frame_path = extract_frame(video_path, tmp_frame, timestamp=0.8)
    if not frame_path or not Path(frame_path).exists():
        logger.warning("Could not extract frame for thumbnail: %s", video_path)
        return None

    # ── 2. Open frame, scale to fill 1080×1920 ────────────────────────────
    frame = Image.open(frame_path).convert("RGB")
    fw, fh = frame.size
    if (fw / fh) > (W / H):
        new_h, new_w = H, int(fw * H / fh)
    else:
        new_w, new_h = W, int(fh * W / fw)
    frame = frame.resize((new_w, new_h), Image.LANCZOS)
    x_off = (new_w - W) // 2
    y_off = (new_h - H) // 3
    canvas = frame.crop((x_off, y_off, x_off + W, y_off + H))

    # ── 3. Visual enhancement — pop the frame ─────────────────────────────
    canvas = ImageEnhance.Color(canvas).enhance(1.3)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.15)
    canvas = ImageEnhance.Sharpness(canvas).enhance(1.2)

    # ── 4. Bottom-heavy gradient overlay for text legibility ──────────────
    # Gradient: transparent at top 55%, ramps to ~85% black at bottom
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)

    grad_start = int(H * 0.52)   # gradient starts here
    grad_mid   = int(H * 0.70)   # reaches ~60% opacity here
    grad_end   = H               # 88% opacity at very bottom

    for py in range(grad_start, grad_mid):
        t = (py - grad_start) / (grad_mid - grad_start)
        alpha = int(180 * t)       # 0 → 180
        draw_ov.line([(0, py), (W, py)], fill=(0, 0, 0, alpha))

    for py in range(grad_mid, grad_end):
        t = (py - grad_mid) / (grad_end - grad_mid)
        alpha = int(180 + int(44 * t))  # 180 → 224
        draw_ov.line([(0, py), (W, py)], fill=(0, 0, 0, alpha))

    # Light top vignette to soften edges
    for py in range(0, int(H * 0.10)):
        alpha = int(80 * (1 - py / (H * 0.10)))
        draw_ov.line([(0, py), (W, py)], fill=(0, 0, 0, alpha))

    canvas = canvas.convert("RGBA")
    canvas = Image.alpha_composite(canvas, overlay).convert("RGB")

    # ── 5. Text layout — anchored to bottom ───────────────────────────────
    primary, secondary = _game_colors(game or clip.get("game", ""))
    draw = ImageDraw.Draw(canvas)

    streamer   = (clip.get("streamer") or clip.get("broadcaster_name") or "").upper()
    raw_title  = clip.get("title") or clip.get("_title_override") or ""
    title_text = _clean_title(raw_title)

    # Accent bar at very bottom
    bar_h = 14
    draw.rectangle([(0, H - bar_h), (W, H)], fill=primary)

    # Position text from bottom up
    # Title sits just above the accent bar
    text_margin_bottom = bar_h + 60

    if title_text:
        if len(title_text) <= 12:
            font_title = _get_font(132)
        elif len(title_text) <= 22:
            font_title = _get_font(108)
        else:
            font_title = _get_font(84)

        # Measure title block height (may wrap)
        lines = _wrap_text(title_text, font_title, max_width=W - 60)
        line_bbox = draw.textbbox((0, 0), lines[0], font=font_title)
        line_h = line_bbox[3] - line_bbox[1]
        block_h = len(lines) * (line_h + 8) - 8

        title_y = H - text_margin_bottom - block_h

        for line in lines:
            _draw_centered_text(draw, title_y, line, font_title,
                                fill=(255, 255, 255), border=8)
            line_bbox = draw.textbbox((0, 0), line, font=font_title)
            title_y += (line_bbox[3] - line_bbox[1]) + 8

        # Streamer name sits 16px above title block
        if streamer:
            font_streamer = _get_font(64)
            streamer_y = H - text_margin_bottom - block_h - 16 - 70
            _draw_centered_text(draw, streamer_y, streamer, font_streamer,
                                fill=secondary, border=5)
    elif streamer:
        font_streamer = _get_font(80)
        _draw_centered_text(draw, H - text_margin_bottom - 90, streamer,
                            font_streamer, fill=secondary, border=6)

    # ── 6. Save ───────────────────────────────────────────────────────────
    canvas.save(str(output_path), "JPEG", quality=95)
    try:
        Path(tmp_frame).unlink(missing_ok=True)
    except Exception:
        pass

    logger.info("Thumbnail generated: %s", output_path)
    return output_path


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Break text into lines that fit within max_width pixels."""
    from PIL import Image as _Img, ImageDraw as _ID
    _d = _ID.Draw(_Img.new("RGB", (1, 1)))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = _d.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]
