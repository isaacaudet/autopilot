"""Burn subtitles into video using FFmpeg."""

import functools
import hashlib
import re
import subprocess
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg, get_encoder_args
from clipper.layout_profiles import load_facecam_profiles, normalize_layout_tuning

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - graceful fallback if Pillow is unavailable
    Image = None
    ImageDraw = None
    ImageFont = None

console = Console()

_TUNING_KEYS = (
    "safe_top_ratio",
    "safe_bottom_ratio",
    "facecam_band_ratio",
    "gameplay_zoom",
    "gameplay_zoom_no_facecam",
    "gameplay_x_bias",
    "gameplay_y_bias",
    "hud_height_ratio",
    "hud_scale",
    "hud_x_ratio",
    "hud_y_ratio",
    "title_y_ratio",
    "subtitle_margin_ratio",
)

_HOOK_MAIN_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
)

_HOOK_EMOJI_FONT_CANDIDATES = (
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
)

_APPLE_EMOJI_SIZES = (20, 26, 32, 40, 48, 52, 64, 96, 160)


def _effective_layout_profile(config: dict, clip: dict | None) -> dict:
    """Return streamer profile merged with optional per-clip layout override."""
    merged: dict = {}
    if clip:
        streamer = str(clip.get("streamer") or "").strip().lower()
        if streamer:
            prof = load_facecam_profiles(config).get(streamer)
            if isinstance(prof, dict):
                merged.update(prof)

        override = clip.get("_layout_override")
        if isinstance(override, dict):
            if override.get("facecam_enabled") is not None:
                merged["facecam_enabled"] = bool(override.get("facecam_enabled"))
            if override.get("hud_enabled") is not None:
                merged["hud_enabled"] = bool(override.get("hud_enabled"))

            tuning_override = {k: override.get(k) for k in _TUNING_KEYS if k in override}
            merged.update(normalize_layout_tuning(tuning_override))
    return merged


def _build_zoom_filter() -> str:
    """Build FFmpeg filter for a zoom-settle (108% -> 100%) over 0.5s.

    Creates visible motion that catches the eye during Shorts scrolling.
    Crops a smaller center region (= zoomed in) then scales back to 1080x1920.
    After 0.5s the crop covers the full frame (no visible effect).

    Assumes input is 1080x1920 (applied after format_for_shorts).
    """
    # zoom factor: 1.08 at t=0, easing to 1.0 at t=0.5, stays 1.0 after
    # crop width = iw/zoom, crop height = ih/zoom, centered
    return (
        "crop="
        "w='iw/(1.08-0.08*min(t/0.5,1))':"
        "h='ih/(1.08-0.08*min(t/0.5,1))':"
        "x='(iw-ow)/2':"
        "y='(ih-oh)/2'"
        ",scale=1080:1920:flags=lanczos"
    )


def _resolve_text_layout(config: dict, clip: dict | None = None) -> dict:
    """Resolve title/subtitle placement for Shorts from config + streamer profile."""
    shorts_cfg = config.get("shorts", {}) or {}
    fill_cfg = shorts_cfg.get("fill", {}) if isinstance(shorts_cfg.get("fill"), dict) else {}
    h = int(shorts_cfg.get("height", 1920) or 1920)

    safe_top_ratio = float(fill_cfg.get("safe_top_ratio", 0.08) or 0.08)
    title_y_default = max(0.03, safe_top_ratio * 0.25)
    subtitle_margin_v = float(shorts_cfg.get("subtitle_margin_v", 450) or 450)
    subtitle_margin_ratio = subtitle_margin_v / float(max(1, h))

    merged = normalize_layout_tuning({
        "safe_top_ratio": safe_top_ratio,
        "title_y_ratio": float(fill_cfg.get("title_y_ratio", title_y_default) or title_y_default),
        "subtitle_margin_ratio": float(fill_cfg.get("subtitle_margin_ratio", subtitle_margin_ratio) or subtitle_margin_ratio),
    })

    prof = _effective_layout_profile(config, clip)
    if prof:
        merged.update(normalize_layout_tuning(prof))

    margin_v = int(round(float(merged.get("subtitle_margin_ratio", subtitle_margin_ratio)) * h))
    merged["subtitle_margin_v"] = max(20, min(h - 20, margin_v))
    return merged


def _first_existing_path(paths: tuple[str, ...]) -> str | None:
    for raw in paths:
        p = Path(raw)
        if p.exists():
            return str(p)
    return None


def _is_emoji_codepoint(cp: int) -> bool:
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x1F1E6 <= cp <= 0x1F1FF
    )


def _split_hook_runs(text: str) -> list[tuple[str, bool]]:
    """Split a string into text runs, separating likely emoji graphemes."""
    runs: list[tuple[str, bool]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        cp = ord(ch)
        if _is_emoji_codepoint(cp):
            start = i
            i += 1
            while i < len(text):
                nxt = text[i]
                nxt_cp = ord(nxt)
                if nxt_cp in (0xFE0F, 0x200D) or 0x1F3FB <= nxt_cp <= 0x1F3FF or _is_emoji_codepoint(nxt_cp):
                    i += 1
                    continue
                break
            runs.append((text[start:i], True))
            continue

        start = i
        i += 1
        while i < len(text) and not _is_emoji_codepoint(ord(text[i])):
            i += 1
        runs.append((text[start:i], False))
    return runs


def _load_hook_font(font_path: str | None, size: int, *, emoji: bool = False):
    if ImageFont is None or not font_path:
        return None
    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        if emoji and "Apple Color Emoji" in font_path:
            ordered = sorted(_APPLE_EMOJI_SIZES, key=lambda px: abs(px - size))
            for px in ordered:
                try:
                    return ImageFont.truetype(font_path, px)
                except OSError:
                    continue
    return None


def _measure_mixed_line(draw, line: str, main_font, emoji_font) -> tuple[int, int, list[tuple[str, bool, int, int]]]:
    runs = _split_hook_runs(line)
    measured: list[tuple[str, bool, int, int]] = []
    width = 0
    max_h = 0

    for segment, is_emoji in runs:
        font = emoji_font if is_emoji and emoji_font is not None else main_font
        kwargs = {"font": font}
        if is_emoji and emoji_font is not None:
            kwargs["embedded_color"] = True

        seg_w = int(round(draw.textlength(segment, **kwargs)))
        # Space-only runs can report zero width on some font backends.
        if seg_w <= 0 and segment.isspace():
            seg_w = int(round(draw.textlength(" ", **kwargs)))

        probe = segment if segment.strip() else "Ag"
        bbox = draw.textbbox((0, 0), probe, **kwargs)
        seg_h = max(1, int(bbox[3] - bbox[1]))

        measured.append((segment, is_emoji, max(0, seg_w), seg_h))
        width += max(0, seg_w)
        max_h = max(max_h, seg_h)

    return width, max_h, measured


def _wrap_hook_text(draw, text: str, main_font, emoji_font, max_width: int, max_lines: int = 2) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []

    tokens = re.split(r"(\s+)", text)
    lines: list[str] = []
    current = ""

    for token in tokens:
        if not token:
            continue
        candidate = f"{current}{token}"
        cand_w, _, _ = _measure_mixed_line(draw, candidate, main_font, emoji_font)
        if not current or cand_w <= max_width:
            current = candidate
            continue

        lines.append(current.strip())
        current = token.lstrip()

    if current.strip():
        lines.append(current.strip())

    if len(lines) <= max_lines:
        return lines

    # Collapse overflow into the last visible line.
    kept = lines[: max_lines - 1]
    tail = " ".join(lines[max_lines - 1 :]).strip()
    while tail:
        tail_w, _, _ = _measure_mixed_line(draw, tail, main_font, emoji_font)
        if tail_w <= max_width:
            break
        if len(tail) <= 2:
            break
        tail = f"{tail[:-2].rstrip()}..."
    kept.append(tail or lines[max_lines - 1])
    return kept


def _render_hook_overlay_png(hook_text: str, config: dict, clip: dict | None = None) -> Path | None:
    """Render hook text as RGBA PNG with emoji support using Pillow."""
    if Image is None or ImageDraw is None or ImageFont is None:
        return None

    text = " ".join(str(hook_text or "").split())
    if not text:
        return None

    shorts_cfg = config.get("shorts", {}) or {}
    w = int(shorts_cfg.get("width", 1080) or 1080)
    h = int(shorts_cfg.get("height", 1920) or 1920)
    layout = _resolve_text_layout(config, clip)
    title_y_ratio = float(layout.get("title_y_ratio", 0.03))
    y_top = int(max(h * 0.015, h * title_y_ratio))

    main_font_path = _first_existing_path(_HOOK_MAIN_FONT_CANDIDATES)
    if not main_font_path:
        return None
    emoji_font_path = _first_existing_path(_HOOK_EMOJI_FONT_CANDIDATES)

    max_line_width = int(round(w * 0.86))
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    chosen = None
    for font_size in range(92, 43, -2):
        main_font = _load_hook_font(main_font_path, font_size, emoji=False)
        if main_font is None:
            continue
        emoji_font = _load_hook_font(emoji_font_path, font_size, emoji=True) if emoji_font_path else None
        lines = _wrap_hook_text(draw, text, main_font, emoji_font, max_width=max_line_width, max_lines=2)
        if not lines:
            continue

        metrics: list[tuple[int, int, list[tuple[str, bool, int, int]]]] = []
        fits = True
        for line in lines:
            line_w, line_h, runs = _measure_mixed_line(draw, line, main_font, emoji_font)
            if line_w > max_line_width:
                fits = False
                break
            metrics.append((line_w, line_h, runs))

        if fits:
            chosen = (font_size, main_font, emoji_font, metrics)
            break

    if chosen is None:
        return None

    font_size, main_font, emoji_font, lines = chosen
    line_gap = max(8, int(round(font_size * 0.18)))
    cursor_y = y_top
    stroke_w = max(2, int(round(font_size * 0.05)))
    for line_w, line_h, runs in lines:
        cursor_x = int((w - line_w) / 2)
        for segment, is_emoji, seg_w, seg_h in runs:
            run_y = int(cursor_y + max(0, (line_h - seg_h) / 2))
            if is_emoji and emoji_font is not None:
                draw.text((cursor_x, run_y), segment, font=emoji_font, embedded_color=True)
            else:
                draw.text(
                    (cursor_x, run_y),
                    segment,
                    font=main_font,
                    fill=(255, 255, 255, 255),
                    stroke_width=stroke_w,
                    stroke_fill=(0, 0, 0, 240),
                )
            cursor_x += seg_w
        cursor_y += line_h + line_gap

    queue_dir = config.get("_queue_dir")
    if not isinstance(queue_dir, Path):
        queue_dir = Path("queue")
    cache_dir = queue_dir / "hook_overlays"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = hashlib.sha1(
        f"hook-v5|{text}|{w}x{h}|{title_y_ratio:.4f}".encode("utf-8")
    ).hexdigest()[:16]
    overlay_path = cache_dir / f"hook_{cache_key}.png"
    if not overlay_path.exists():
        canvas.save(overlay_path)
    return overlay_path


def _get_subscribe_assets() -> tuple[Path | None, Path | None]:
    """Return (mov_path, bell_wav_path) for the animated subscribe overlay.

    Assets bundled in clipper/assets/:
    - subscribe_anim.mov  — ProRes with alpha (VP8/VP9 WebM silently loses alpha)
    - subscribe_bell.wav  — macOS Ping.aiff converted to WAV
    """
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    mov = assets_dir / "subscribe_anim.mov"
    bell = assets_dir / "subscribe_bell.wav"
    if mov.exists() and bell.exists():
        return mov, bell
    return None, None


def _render_follow_cta_png(config: dict, clip: dict | None = None) -> Path | None:
    """Render a 'FOLLOW FOR MORE' CTA overlay as RGBA PNG for Instagram.

    Positioned at the bottom of the screen (similar placement to subscribe CTA).
    Clean, minimal design: rounded pill shape with 'FOLLOW' text + profile icon.
    """
    if Image is None or ImageDraw is None or ImageFont is None:
        return None

    shorts_cfg = config.get("shorts", {}) or {}
    w = int(shorts_cfg.get("width", 1080) or 1080)
    h = int(shorts_cfg.get("height", 1920) or 1920)

    queue_dir = config.get("_queue_dir")
    if not isinstance(queue_dir, Path):
        queue_dir = Path("queue")
    cache_dir = queue_dir / "hook_overlays"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = f"follow_cta_v2_{w}x{h}"
    overlay_path = cache_dir / f"{cache_key}.png"
    if overlay_path.exists():
        return overlay_path

    main_font_path = _first_existing_path(_HOOK_MAIN_FONT_CANDIDATES)
    if not main_font_path:
        return None

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Button dimensions and position
    btn_text = "FOLLOW FOR MORE"
    font_size = 52
    font = _load_hook_font(main_font_path, font_size, emoji=False)
    if not font:
        return None

    bbox = draw.textbbox((0, 0), btn_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x, pad_y = 60, 28
    btn_w = text_w + 2 * pad_x
    btn_h = text_h + 2 * pad_y
    btn_x = (w - btn_w) // 2
    btn_y = h - 340  # above progress bar area

    # Draw pill-shaped button background
    radius = btn_h // 2
    # Background pill (Instagram gradient: purple-pink-orange)
    pill = Image.new("RGBA", (btn_w, btn_h), (0, 0, 0, 0))
    pill_draw = ImageDraw.Draw(pill)
    pill_draw.rounded_rectangle(
        [(0, 0), (btn_w - 1, btn_h - 1)],
        radius=radius,
        fill=(225, 48, 108, 230),  # Instagram pink
    )
    # Gradient accent — left side more purple, right side more orange
    for x in range(btn_w):
        t = x / btn_w
        r = int(131 + (253 - 131) * t)  # purple→orange
        g = int(58 + (89 - 58) * t * 0.3)
        b = int(180 + (0 - 180) * t)
        alpha = 140
        pill_draw.line([(x, 0), (x, btn_h - 1)], fill=(r, g, b, alpha))

    # Re-draw rounded rect as a mask to clip the gradient
    mask = Image.new("L", (btn_w, btn_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [(0, 0), (btn_w - 1, btn_h - 1)],
        radius=radius,
        fill=255,
    )
    pill.putalpha(mask)

    canvas.paste(pill, (btn_x, btn_y), pill)

    # Draw "FOLLOW FOR MORE" text centered in button
    draw = ImageDraw.Draw(canvas)  # re-create after paste
    text_x = btn_x + (btn_w - text_w) // 2
    text_y = btn_y + (btn_h - text_h) // 2 - 2
    # Shadow
    draw.text((text_x + 2, text_y + 2), btn_text, font=font, fill=(0, 0, 0, 120))
    # Main text
    draw.text((text_x, text_y), btn_text, font=font, fill=(255, 255, 255, 255))

    # Small arrow/chevron indicator below button
    arrow_y = btn_y + btn_h + 16
    arrow_font = _load_hook_font(main_font_path, 32, emoji=False)
    if arrow_font:
        arrow_text = "▼"
        abbox = draw.textbbox((0, 0), arrow_text, font=arrow_font)
        aw = abbox[2] - abbox[0]
        draw.text(((w - aw) // 2, arrow_y), arrow_text, font=arrow_font, fill=(255, 255, 255, 160))

    canvas.save(overlay_path)
    return overlay_path


def _build_hook_filter(hook_text: str, config: dict, clip: dict | None = None, duration: float = 2.0) -> str:
    """Build FFmpeg drawtext filters for the first-frame hook overlay.

    Renders large Impact text with black shadow + outline, centered at y=15%,
    visible from 0.1s to 2.0s with fade in/out.
    """
    if duration <= 0:
        return ""

    # drawtext breaks on raw newlines/commas/special chars; normalize first.
    text = " ".join(str(hook_text or "").split())
    escaped = (
        text
        .replace("\\", "\\\\")
        .replace("'", "\u2019")   # typographic apostrophe — visually identical, avoids FFmpeg quoting hell
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("%", "\\%")
    )

    fade_out_start = duration - 0.3
    alpha_expr = f"if(lt(t,0.1),0,if(lt(t,0.3),(t-0.1)/0.2,if(lt(t,{fade_out_start:.2f}),1,({duration:.2f}-t)/0.3)))"

    layout = _resolve_text_layout(config, clip)
    title_y_ratio = float(layout.get("title_y_ratio", 0.03))
    y_expr = f"'max(h*0.015,h*{title_y_ratio:.4f})'"
    y_expr_shadow = f"'max(h*0.015,h*{title_y_ratio:.4f})+3'"

    # Shadow layer (offset black text behind the main text)
    shadow = (
        f"drawtext=text='{escaped}'"
        f":font=Impact"
        f":fontsize=84"
        f":fontcolor=black@0.6"
        f":x=(w-text_w)/2+3"
        f":y={y_expr_shadow}"
        f":alpha='{alpha_expr}'"
        f":enable='between(t,0.1,{duration:.2f})'"
    )

    # Main text layer
    main = (
        f"drawtext=text='{escaped}'"
        f":font=Impact"
        f":fontsize=84"
        f":fontcolor=white"
        f":borderw=5"
        f":bordercolor=black@0.95"
        f":box=1"
        f":boxcolor=black@0.22"
        f":boxborderw=18"
        f":x=(w-text_w)/2"
        f":y={y_expr}"
        f":alpha='{alpha_expr}'"
        f":enable='between(t,0.1,{duration:.2f})'"
    )

    return f"{shadow},{main}"


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_progress_bar_filter(duration: float) -> str:
    """Build FFmpeg drawbox filter for a progress bar at the bottom.

    Thin cyan bar that fills left-to-right over the video duration.
    Increases retention — viewers subconsciously watch to see it complete.
    """
    return (
        f"drawbox=x=0:y=ih-12:w=iw:h=12:color=333333@0.6:t=fill,"
        f"drawbox=x=0:y=ih-10:w='min(iw,iw*t/{duration:.2f})':h=8:color=00FFFF@0.9:t=fill"
    )


def _build_censor_audio_filter(censor_ranges: list[tuple[float, float]]) -> str:
    """Build FFmpeg volume filter to mute audio during profanity."""
    conditions = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in censor_ranges)
    return f"volume='if({conditions},0,1)':eval=frame"


@functools.lru_cache(maxsize=1)
def _ffmpeg_filter_support() -> tuple[bool, bool]:
    """Check FFmpeg subtitle filter support. Cached at module level."""
    ffmpeg_bin = get_ffmpeg()
    try:
        check = subprocess.run(
            [ffmpeg_bin, "-filters"], capture_output=True, text=True
        )
        return ("ass" in check.stdout, "subtitles" in check.stdout)
    except FileNotFoundError:
        return (False, False)


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    config: dict,
    clip: dict | None = None,
    is_shorts: bool = False,
    hook_text: str | None = None,
    verbose: bool = False,
    output_name: str | None = None,
    censor_ranges: list | None = None,
    skip_subscribe_cta: bool = False,
    encoder_preset: str | None = None,
    follow_cta: bool = False,
) -> Path:
    """Burn subtitles (ASS or SRT) into a video file using FFmpeg.

    ASS files carry their own styling. SRT files get force_style applied.
    Returns the Path to the output video with burned-in subtitles.

    encoder_preset: If set (e.g. "veryfast"), override the default encoder with
    libx264 at this preset. Useful for cross-platform copies where the receiving
    platform recompresses anyway.
    """
    video_path = Path(video_path)
    subtitle_path = Path(subtitle_path)
    if output_name:
        output_path = video_path.with_name(f"{output_name}_final.mp4")
    else:
        # Clean stem — strip any previous _shorts/_subtitled suffixes to avoid nesting
        base_stem = video_path.stem.replace("_subtitled", "").replace("_shorts", "")
        output_path = video_path.with_name(f"{base_stem}_final.mp4")

    ffmpeg_bin = get_ffmpeg()

    # Check that the ffmpeg binary has subtitle support (cached)
    has_ass, has_subtitles = _ffmpeg_filter_support()

    if not has_ass and not has_subtitles:
        console.print(
            "[red]FFmpeg has no subtitle support.[/red]\n"
            "Fix: [cyan]pip install imageio-ffmpeg[/cyan]"
        )
        return video_path

    # Escape path for FFmpeg filter
    sub_escaped = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    ext = subtitle_path.suffix.lower()

    if ext == ".ass":
        # ASS files have embedded styling — use the ass filter directly
        vf = f"ass='{sub_escaped}'"
    elif ext == ".srt":
        if is_shorts:
            sub_config = config.get("subtitles", {})
            font_size = sub_config.get("font_size", 28)
            outline = sub_config.get("outline_width", 3)
            layout = _resolve_text_layout(config, clip)
            margin_v = int(layout.get("subtitle_margin_v", 450))

            force_style = (
                f"FontSize={font_size},"
                f"Bold=1,"
                f"Alignment=2,"
                f"MarginV={margin_v},"
                f"PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,"
                f"Outline={outline}"
            )
            vf = f"subtitles='{sub_escaped}':force_style='{force_style}'"
        else:
            vf = f"subtitles='{sub_escaped}'"
    else:
        console.print(f"[yellow]Unknown subtitle format: {ext}[/yellow]")
        return video_path

    # Zoom-settle — Shorts only (hardcoded to 1080x1920 input)
    if is_shorts:
        zoom_filter = _build_zoom_filter()
        vf = f"{zoom_filter},{vf}"


    hook_overlay_path: Path | None = None
    hook_duration = float(clip.get("_hook_duration", 2.0)) if clip else 2.0

    # Probe duration for Shorts upfront (needed for subscribe CTA timing).
    vid_duration: float | None = None
    if is_shorts:
        from clipper.config import get_ffprobe
        try:
            probe = subprocess.run(
                [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True, timeout=10,
            )
            vid_duration = float(probe.stdout.strip())
        except (ValueError, subprocess.SubprocessError, FileNotFoundError):
            pass

    # Hook text overlay always; progress bar only for non-Shorts outputs.
    if hook_text:
        if not is_shorts:
            # Progress bar — probe duration first
            from clipper.config import get_ffprobe
            try:
                probe = subprocess.run(
                    [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                    capture_output=True, text=True,
                )
                pb_duration = float(probe.stdout.strip())
                progress_filter = _build_progress_bar_filter(pb_duration)
                vf = f"{vf},{progress_filter}"
            except (ValueError, subprocess.SubprocessError):
                pass  # skip progress bar if probe fails

        # Shorts: render a polished hook card with emoji support via RGBA overlay.
        # Fallback to drawtext if Pillow/font rendering fails.
        if is_shorts:
            hook_overlay_path = _render_hook_overlay_png(hook_text, config, clip=clip)
        if hook_overlay_path is None:
            hook_filter = _build_hook_filter(hook_text, config, clip=clip, duration=hook_duration)
            if hook_filter:
                vf = f"{vf},{hook_filter}"

    # Subscribe CTA overlay — animated WebM with bell sound over the last 6s.
    # Skipped for non-YouTube platforms (Instagram/TikTok get clean video).
    sub_anim_path: Path | None = None
    sub_bell_path: Path | None = None
    if is_shorts and not skip_subscribe_cta and not follow_cta:
        shorts_cfg_inner = config.get("shorts", {}) or {}
        if shorts_cfg_inner.get("subscribe_cta", True):
            sub_anim_path, sub_bell_path = _get_subscribe_assets()

    # Instagram Follow CTA — static PNG overlay in last ~4s of clip.
    follow_overlay_path: Path | None = None
    if is_shorts and follow_cta and vid_duration and vid_duration > 10.0:
        follow_overlay_path = _render_follow_cta_png(config, clip=clip)

    audio_args = []
    if censor_ranges:
        audio_args.extend(["-af", _build_censor_audio_filter(censor_ranges)])
    audio_args.extend(["-c:a", "aac", "-b:a", "256k"])

    # Build PNG overlay chain: hook (start) → follow/subscribe CTA (end).
    png_overlays: list[dict] = []
    if hook_overlay_path is not None:
        png_overlays.append({
            "path": hook_overlay_path,
            "label": "hook",
            "fade": "",
            "enable": f"between(t,0,{hook_duration:.2f})",
        })
    if follow_overlay_path is not None:
        follow_start = max(0.5, vid_duration - 4.0)
        fade_in = ",fade=t=in:st=0:d=0.3:alpha=1"
        png_overlays.append({
            "path": follow_overlay_path,
            "label": "follow",
            "fade": fade_in,
            "enable": f"between(t,{follow_start:.2f},{vid_duration:.2f})",
        })
    # Animated subscribe CTA — last ~6s of the clip.
    # Requires vid_duration > 10s to avoid dominating short clips.
    use_anim_cta = (
        sub_anim_path is not None
        and sub_bell_path is not None
        and vid_duration is not None
        and vid_duration > 10.0
    )
    if use_anim_cta:
        sub_start = max(0.5, vid_duration - 6.3)
    else:
        sub_start = 0.0

    filter_complex: str | None = None
    use_bell_audio = use_anim_cta

    # Resolve encoder args — use fast preset override if requested
    if encoder_preset:
        enc_args = ["-c:v", "libx264", "-preset", encoder_preset, "-crf", "20"]
    else:
        enc_args = list(get_encoder_args())

    if png_overlays or use_anim_cta:
        filter_parts = [f"[0:v]{vf}[base]"]
        prev_label = "base"

        # Static PNG overlays (hook card, etc.)
        for i, ov in enumerate(png_overlays):
            out_label = f"lay{i}"
            path_escaped = _escape_filter_path(ov["path"])
            filter_parts.append(
                f"movie='{path_escaped}',format=rgba{ov['fade']}[{ov['label']}]"
            )
            filter_parts.append(
                f"[{prev_label}][{ov['label']}]overlay=x=0:y=0:enable='{ov['enable']}'[{out_label}]"
            )
            prev_label = out_label

        # Animated subscribe MOV — crop button area (630x380 @ x=645,y=700 from 1920x1080),
        # scale to 560px wide, centered at bottom safe zone.
        # Prepend a transparent blank stream so the animation starts at the right time.
        # (movie= plays continuously regardless of enable=; blank concat is the correct fix.)
        if use_anim_cta:
            anim_escaped = _escape_filter_path(sub_anim_path)
            if sub_start > 0.05:
                filter_parts.append(
                    f"movie='{anim_escaped}',"
                    f"crop=630:380:645:700,scale=560:-2,setsar=1,format=rgba[ani_raw]"
                )
                filter_parts.append(
                    f"color=s=560x338:d={sub_start:.3f}:r=30,"
                    f"format=rgba,colorchannelmixer=aa=0[blank]"
                )
                filter_parts.append("[blank][ani_raw]concat=n=2:v=1:a=0[sub_anim]")
            else:
                filter_parts.append(
                    f"movie='{anim_escaped}',"
                    f"crop=630:380:645:700,scale=560:-2,setsar=1,format=rgba[sub_anim]"
                )
            filter_parts.append(
                f"[{prev_label}][sub_anim]overlay=(W-w)/2:H-h-280:eof_action=pass[v]"
            )
            prev_label = "v"
        else:
            # Rename last label to [v] for consistent mapping
            if png_overlays:
                filter_parts[-1] = filter_parts[-1].replace(f"[{prev_label}]", "[v]")
                prev_label = "v"

        # Audio: mix in bell if animated CTA is active
        if use_bell_audio:
            bell_escaped = _escape_filter_path(sub_bell_path)
            bell_delay_ms = int(sub_start * 1000)
            filter_parts.append(
                f"amovie='{bell_escaped}',adelay={bell_delay_ms}|{bell_delay_ms}[bell]"
            )
            filter_parts.append(
                "[0:a][bell]amix=inputs=2:duration=first:weights='1 0.35'[a]"
            )

        if prev_label != "v" and png_overlays and not use_anim_cta:
            pass  # already handled above
        elif not use_anim_cta and not png_overlays:
            pass

        filter_complex = ";".join(filter_parts)

        audio_map = "[a]" if use_bell_audio else "0:a?"
        cmd = [
            ffmpeg_bin,
            "-i", str(video_path),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", audio_map,
            *enc_args,
            *(audio_args if not use_bell_audio else [a for a in audio_args if not a.startswith("-af")]),
            "-c:a", "aac", "-b:a", "256k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]
    else:
        cmd = [
            ffmpeg_bin,
            "-i", str(video_path),
            "-vf", vf,
            *enc_args,
            *audio_args,
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

    console.print(f"[blue]Burning subtitles:[/blue] {video_path.name}")
    if verbose:
        console.print(f"[dim]FFmpeg: {ffmpeg_bin}[/dim]")
        if filter_complex:
            for ov in png_overlays:
                console.print(f"[dim]Overlay ({ov['label']}): {ov['path']}[/dim]")
            console.print(f"[dim]Filter complex: {filter_complex}[/dim]")
        else:
            console.print(f"[dim]Filter: {vf}[/dim]")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if verbose:
            console.print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]FFmpeg failed:[/red] {e.stderr[-300:]}")
        raise

    console.print(f"[green]Subtitled video:[/green] {output_path.name}")
    return output_path
