"""Compile multiple clips into a single long-form compilation video."""

import json
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg, get_ffprobe, get_encoder_args

console = Console()

COMP_WIDTH = 1920
COMP_HEIGHT = 1080

# Per-game thumbnail config: (asset_filename, steam_library_hero_url, primary_color, secondary_color)
_GAME_THUMB_CFG: dict[str, tuple[str, str, tuple, tuple]] = {
    "deadlock": (
        "deadlock_heroes.jpg",
        "https://cdn.cloudflare.steamstatic.com/steam/apps/1422450/library_hero.jpg",
        (255, 100, 0),    # orange
        (0, 220, 220),    # cyan
    ),
    "marathon": (
        "marathon_heroes.jpg",
        "https://cdn.akamai.steamstatic.com/steam/apps/3344100/header.jpg",
        (0, 220, 255),    # cyan-blue
        (255, 200, 0),    # gold
    ),
    "arc raiders": (
        "arcraiders_heroes.jpg",
        "https://cdn.cloudflare.steamstatic.com/steam/apps/2418950/library_hero.jpg",
        (255, 200, 0),    # gold
        (220, 80, 0),     # red-orange
    ),
    "valorant": (
        "valorant_heroes.jpg",
        "https://cdn.cloudflare.steamstatic.com/steam/apps/1085660/library_hero.jpg",
        (255, 70, 84),    # red
        (255, 180, 0),    # gold
    ),
}


def _get_duration(video_path: str) -> float:
    """Get video duration in seconds."""
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not read duration from {video_path} (ffprobe output: {result.stdout.strip()!r})")


def _get_dimensions(video_path: str) -> tuple[int, int]:
    """Get video width and height."""
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-print_format", "json",
         "-show_streams", video_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"ffprobe returned invalid JSON for {video_path}")
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise RuntimeError(f"No video stream in {video_path}")


def _normalize_clip(
    video_path: str,
    output_path: str,
    rank_text: str = "",
    verbose: bool = False,
    subtitle_path: str = "",
) -> str:
    """Normalize a clip to 1920x1080 with persistent streamer overlay and fade transitions.

    Args:
        rank_text: e.g. "#12 — StreamerName". Persistent bottom-left overlay
            with fade-in at start. Empty string = no overlay.
    """
    w, h = _get_dimensions(video_path)
    duration = _get_duration(video_path)
    ratio = w / h if h > 0 else 1.0

    if ratio < 1.0:
        scale_vf = (
            f"scale=-2:{COMP_HEIGHT}:flags=lanczos,"
            f"pad={COMP_WIDTH}:{COMP_HEIGHT}:(ow-iw)/2:0:black,"
            f"setsar=1"
        )
    else:
        scale_vf = (
            f"scale={COMP_WIDTH}:{COMP_HEIGHT}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={COMP_WIDTH}:{COMP_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1"
        )

    vf = scale_vf

    # Burn subtitles if provided (single-encode: subtitles + scale in one pass)
    if subtitle_path and Path(subtitle_path).exists():
        # Escape path for ffmpeg filter syntax
        esc = subtitle_path.replace("\\", "/").replace("'", "\\'").replace(":", "\\:")
        vf += f",ass='{esc}'"

    # Persistent streamer + rank overlay — fade-in over 0.3s, stays on screen
    if rank_text:
        safe_text = rank_text.replace("'", "\\'").replace(":", "\\:")
        vf += (
            f",drawtext=text='{safe_text}'"
            f":font=Impact:fontsize=48"
            f":fontcolor=white:borderw=3:bordercolor=black"
            f":box=1:boxcolor=black@0.5:boxborderw=14"
            f":x=20:y=h-60"
            f":alpha='if(lt(t\\,0.3)\\,t/0.3\\,1)'"
        )

    # Fade in/out for smooth transitions between clips
    fade_out_start = max(0, duration - 0.2)
    vf += f",fade=t=in:st=0:d=0.2,fade=t=out:st={fade_out_start:.3f}:d=0.2"

    cmd = [
        get_ffmpeg(), "-y",
        "-i", video_path,
        "-vf", vf,
        "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        "-r", "30",
        *get_encoder_args(intermediate=True),
        "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ]

    if verbose:
        console.print(f"  [dim]Normalizing: {Path(video_path).name} ({w}x{h})[/dim]")

    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_path


def build_thumbnail(
    clip_metas: list[dict],
    output_path: Path,
    title: str = "",
    game: str = "",
) -> Path | None:
    """Generate a compilation thumbnail with multi-frame collage layout.

    Layout (1920x1080):
      - Game hero art background, heavily darkened
      - 3 best clip frames in angled/stacked layout (right 60%)
      - Game title with glow accent (left side, massive)
      - "BEST OF" / "DAILY HIGHLIGHTS" subtitle
      - Streamer count + clip count badges
      - Accent color bar at bottom
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
    except ImportError:
        logger.warning("Pillow not installed — falling back to ffmpeg thumbnail")
        return _build_thumbnail_ffmpeg(clip_metas, output_path, game=game)

    output_path = Path(output_path)

    # ── Background: game-specific hero art ─────────────────────────────────
    assets_dir = Path(__file__).parent.parent.parent / "assets"
    game_key = (game or "deadlock").lower().strip()
    cfg = next((v for k, v in _GAME_THUMB_CFG.items() if k in game_key), _GAME_THUMB_CFG["deadlock"])
    asset_file, asset_url, primary_color, secondary_color = cfg
    bg_path = assets_dir / asset_file

    if not bg_path.exists():
        try:
            import urllib.request
            assets_dir.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(asset_url, str(bg_path))
        except Exception:
            pass

    W, H = COMP_WIDTH, COMP_HEIGHT

    if bg_path.exists():
        bg = Image.open(bg_path).convert("RGB")
        scale = max(W / bg.width, H / bg.height)
        new_w, new_h = int(bg.width * scale), int(bg.height * scale)
        bg = bg.resize((new_w, new_h), Image.LANCZOS)
        x_off = (new_w - W) // 2
        y_off = (new_h - H) // 2
        bg = bg.crop((x_off, y_off, x_off + W, y_off + H))
    else:
        bg = Image.new("RGB", (W, H), (10, 10, 15))

    # Darken background significantly for contrast
    bg = ImageEnhance.Brightness(bg).enhance(0.35)
    bg = ImageEnhance.Color(bg).enhance(1.4)

    # ── Gradient overlays ──────────────────────────────────────────────────
    bg = bg.convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)

    # Left side gradient for text area (heavier)
    for x in range(W // 2):
        alpha = int(160 * (1 - x / (W // 2)))
        draw_ov.line([(x, 0), (x, H)], fill=(0, 0, 0, alpha))

    # Bottom gradient
    for y in range(H - 200, H):
        alpha = int(200 * (y - (H - 200)) / 200)
        draw_ov.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    # Top gradient (subtle)
    for y in range(120):
        alpha = int(100 * (1 - y / 120))
        draw_ov.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    bg = Image.alpha_composite(bg, overlay).convert("RGB")

    # ── Multi-frame collage (top 3 clips by views) ────────────────────────
    import urllib.request as _urlreq
    import io

    top_clips = sorted(clip_metas, key=lambda c: c.get("view_count", 0), reverse=True)[:3]
    frames_loaded: list[Image.Image] = []

    for clip in top_clips:
        thumb_url = clip.get("thumbnail_url", "")
        thumb_url = thumb_url.replace("%{width}", "1280").replace("%{height}", "720")
        thumb_url = thumb_url.replace("{width}", "1280").replace("{height}", "720")
        if not thumb_url:
            continue
        try:
            req = _urlreq.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlreq.urlopen(req, timeout=10) as resp:
                frame = Image.open(io.BytesIO(resp.read())).convert("RGB")
            frame = ImageEnhance.Color(frame).enhance(1.2)
            frame = ImageEnhance.Contrast(frame).enhance(1.1)
            frames_loaded.append(frame)
        except Exception:
            continue

    draw = ImageDraw.Draw(bg)

    if frames_loaded:
        # Stacked frame layout — overlapping cards on the right side
        frame_positions = [
            # (x, y, w, h, rotation_placeholder)
            (780, 60, 680, 383, 0),    # top-right: largest, main frame
            (960, 460, 560, 315, 0),   # bottom-right: medium
            (700, 520, 480, 270, 0),   # bottom-left of stack: smaller
        ]
        border_w = 5

        for i, (fx, fy, fw, fh, _) in enumerate(frame_positions):
            if i >= len(frames_loaded):
                break
            frame = frames_loaded[i].resize((fw, fh), Image.LANCZOS)

            # Colored border (primary color for #1, secondary for others)
            border_color = primary_color if i == 0 else secondary_color
            draw.rectangle(
                [fx - border_w, fy - border_w, fx + fw + border_w, fy + fh + border_w],
                fill=border_color,
            )
            # Subtle shadow behind frame
            shadow_offset = 6
            draw.rectangle(
                [fx + shadow_offset, fy + shadow_offset,
                 fx + fw + shadow_offset, fy + fh + shadow_offset],
                fill=(0, 0, 0, 80) if bg.mode == "RGBA" else (0, 0, 0),
            )
            bg.paste(frame, (fx, fy))
            draw = ImageDraw.Draw(bg)

            # Clip rank badge on top-left of each frame
            badge_font_path = "/System/Library/Fonts/Supplemental/Impact.ttf"
            try:
                badge_font = ImageFont.truetype(badge_font_path, 36)
            except Exception:
                badge_font = ImageFont.load_default()
            rank_text = f"#{i + 1}"
            badge_x, badge_y = fx + 8, fy + 8
            # Badge background
            draw.rounded_rectangle(
                [badge_x, badge_y, badge_x + 56, badge_y + 44],
                radius=8,
                fill=(0, 0, 0, 200) if bg.mode == "RGBA" else (0, 0, 0),
            )
            draw.text((badge_x + 8, badge_y + 2), rank_text, font=badge_font, fill=primary_color)

    # ── Text layout ────────────────────────────────────────────────────────
    font_path = "/System/Library/Fonts/Supplemental/Impact.ttf"

    def _draw_text_bordered(d, pos, text, font, fill, border=6, shadow_offset=4):
        x, y = pos
        # Black border
        for dx in range(-border, border + 1):
            for dy in range(-border, border + 1):
                if abs(dx) + abs(dy) > 0:
                    d.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
        # Drop shadow
        d.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(0, 0, 0))
        d.text(pos, text, font=font, fill=fill)

    try:
        font_title = ImageFont.truetype(font_path, 140)
        font_sub = ImageFont.truetype(font_path, 64)
        font_badge = ImageFont.truetype(font_path, 56)
        font_date = ImageFont.truetype(font_path, 44)
    except Exception:
        font_title = font_sub = font_badge = font_date = ImageFont.load_default()

    game_label = (game or "DEADLOCK").upper()

    # Glow effect behind game title (primary color glow)
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.text((60, 180), game_label, font=font_title, fill=(*primary_color, 60))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=20))
    bg = bg.convert("RGBA")
    bg = Image.alpha_composite(bg, glow_layer).convert("RGB")
    draw = ImageDraw.Draw(bg)

    # Game title
    _draw_text_bordered(draw, (60, 180), game_label, font_title, fill=primary_color, border=8)

    # Subtitle
    _draw_text_bordered(draw, (64, 340), "BEST HIGHLIGHTS", font_sub, fill=secondary_color, border=5)

    # Date
    from datetime import date
    date_str = date.today().strftime("%b %d, %Y").upper()
    _draw_text_bordered(draw, (68, 420), date_str, font_date, fill=(180, 180, 180), border=3)

    # Bottom bar — accent color strip
    draw.rectangle([(0, H - 10), (W, H)], fill=primary_color)

    # Clip count badge — bottom left with background
    clip_count = len(clip_metas)
    streamers = set(c.get("streamer", "") for c in clip_metas if c.get("streamer"))
    badge_text = f"{clip_count} CLIPS"

    # Badge with background pill
    badge_bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    bw = badge_bbox[2] - badge_bbox[0]
    bh = badge_bbox[3] - badge_bbox[1]
    badge_x, badge_y = 60, H - 90
    draw.rounded_rectangle(
        [badge_x - 16, badge_y - 10, badge_x + bw + 16, badge_y + bh + 10],
        radius=12,
        fill=(0, 0, 0, 180) if bg.mode == "RGBA" else (20, 20, 20),
    )
    draw.text((badge_x, badge_y), badge_text, font=font_badge, fill=(255, 255, 255))

    # Streamer count badge — next to clip count
    if streamers:
        streamer_text = f"{len(streamers)} STREAMERS"
        s_bbox = draw.textbbox((0, 0), streamer_text, font=font_date)
        sw = s_bbox[2] - s_bbox[0]
        sh = s_bbox[3] - s_bbox[1]
        sx = badge_x + bw + 48
        draw.rounded_rectangle(
            [sx - 12, badge_y + 2, sx + sw + 12, badge_y + sh + 14],
            radius=10,
            fill=(0, 0, 0, 150) if bg.mode == "RGBA" else (20, 20, 20),
        )
        draw.text((sx, badge_y + 6), streamer_text, font=font_date, fill=secondary_color)

    bg = bg.convert("RGB")
    bg.save(str(output_path), "JPEG", quality=95)
    return output_path


def _build_thumbnail_ffmpeg(
    clip_metas: list[dict],
    output_path: Path,
    game: str = "",
) -> Path | None:
    """Fallback ffmpeg-based thumbnail (used if Pillow unavailable)."""
    output_path = Path(output_path)
    best = max(clip_metas, key=lambda c: c.get("view_count", 0), default=None)
    if not best:
        return None
    source = best.get("processed_path", "")
    if not source or not Path(source).exists():
        return None
    try:
        duration = _get_duration(source)
        timestamp = duration * 0.3
        escaped_game = (game or "CLIPS").upper().replace("'", "\\'").replace(":", "\\:")
        clip_count = len(clip_metas)
        vf = (
            f"scale={COMP_WIDTH}:{COMP_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={COMP_WIDTH}:{COMP_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
            f"eq=saturation=1.4:contrast=1.15,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill,"
            f"drawtext=text='{escaped_game}':font=Impact:fontsize=120"
            f":fontcolor=0xFF6600:borderw=6:bordercolor=black:x=(w-text_w)/2:y=h*0.30,"
            f"drawtext=text='DAILY HIGHLIGHTS':font=Impact:fontsize=56"
            f":fontcolor=0x00FFFF:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.30+135,"
            f"drawtext=text='{clip_count} CLIPS':font=Impact:fontsize=48"
            f":fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.72"
        )
        subprocess.run(
            [get_ffmpeg(), "-y", "-ss", str(timestamp), "-i", source,
             "-vframes", "1", "-vf", vf, "-q:v", "2", str(output_path)],
            capture_output=True, text=True, check=True,
        )
        return output_path
    except Exception as e:
        logger.warning("Thumbnail gen failed: %s", e)
        return None


def build_description(clip_metas: list[dict], game: str = "") -> str:
    """Build a YouTube description with timestamps and streamer credits.

    YouTube caps descriptions at 5000 characters. When the full description
    exceeds that, we progressively drop sections: individual Twitch links
    first, then the featuring list, then timestamps from the end.
    """
    MAX_CHARS = 4900  # leave margin for encoding differences

    if game:
        intro = f"Best {game} clips of the day! Daily highlights compilation.\n"
    else:
        intro = "Best clips of the day! Daily highlights compilation.\n"

    def _sanitize(text: str) -> str:
        """Strip characters YouTube's metadata API rejects (<, >, URL-like patterns)."""
        import re as _re
        text = text.replace("<", "").replace(">", "")
        return text

    # Timestamps
    ts_lines = ["TIMESTAMPS:"]
    cumulative = 0.0
    for clip in clip_metas:
        streamer = clip.get("streamer", "Unknown")
        title = _sanitize(clip.get("title", ""))[:40]
        mins = int(cumulative // 60)
        secs = int(cumulative % 60)
        ts_lines.append(f"{mins}:{secs:02d} — {streamer}: {title}")
        cumulative += clip.get("duration", 30)

    # Streamer credits — no URLs, YouTube API rejects competitor domain links
    streamers = sorted(set(c.get("streamer", "") for c in clip_metas if c.get("streamer")))
    featuring = f"\nFeaturing: {', '.join(streamers)}" if streamers else ""

    # Hashtags
    game_tag = game.lower().replace(" ", "") if game else "gaming"
    hashtags = f"\n#{game_tag} #gaming #clips #highlights #compilation #twitch"

    # Assemble, progressively dropping sections to fit under MAX_CHARS
    def _join(*sections: str) -> str:
        return "\n".join(s for s in sections if s)

    timestamps_block = "\n".join(ts_lines)

    # Try full description (no Twitch URLs — they trigger YouTube API rejection)
    full = _join(intro, timestamps_block, featuring, hashtags)
    if len(full) <= MAX_CHARS:
        return full

    # Drop featuring list too
    trimmed = _join(intro, timestamps_block, hashtags)
    if len(trimmed) <= MAX_CHARS:
        return trimmed

    # Truncate timestamps from the end until it fits
    while len(ts_lines) > 2:  # keep header + at least 1 timestamp
        ts_lines.pop()
        timestamps_block = "\n".join(ts_lines)
        trimmed = _join(intro, timestamps_block, f"... and {len(clip_metas) - len(ts_lines) + 1} more clips", hashtags)
        if len(trimmed) <= MAX_CHARS:
            return trimmed

    return _join(intro, hashtags)


def compile_clips(
    clip_metas: list[dict],
    output_path: Path,
    config: dict,
    verbose: bool = False,
    countdown: bool = False,
    state=None,
) -> Path:
    """Compile multiple clips into one video using concat demuxer.

    Clips play back-to-back with instant transitions. Each clip gets an
    animated streamer name + rank overlay that fades in/out over the first 1.5s.

    Args:
        clip_metas: List of clip metadata dicts (must have 'processed_path').
        output_path: Where to write the compiled video.
        config: Loaded config dict.
        verbose: Print extra info.
        countdown: If True, show countdown ranks (#N → #1) instead of sequential.
        state: Optional PipelineState for TUI progress updates.

    Returns:
        Path to the compiled video.
    """
    if len(clip_metas) < 2:
        raise ValueError("Need at least 2 clips to compile.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    valid_clips = []
    for clip in clip_metas:
        path = clip.get("processed_path", "")
        if path and Path(path).exists():
            valid_clips.append(clip)

    if len(valid_clips) < 2:
        raise ValueError(f"Only {len(valid_clips)} valid clip(s), need at least 2.")

    total = len(valid_clips)
    console.print(f"[bold]Compiling {total} clips...[/bold]")

    with tempfile.TemporaryDirectory(prefix="clipper_compile_") as tmpdir:
        segments = []

        for i, clip in enumerate(valid_clips, 1):
            streamer = clip.get("streamer", "Unknown")
            clip_title = clip.get("title", "")[:30]
            rank = total - i + 1 if countdown else i
            rank_text = f"#{rank} — {streamer}"

            console.print(f"  [{i}/{total}] {rank_text} — {clip_title}")

            if state:
                state.compile_step = f"Normalizing clip {i}/{total}"
                state.compile_progress = (i - 1) / total

            subtitle_path = clip.get("_subtitle_path", "")

            norm_path = str(Path(tmpdir) / f"clip_{i:03d}.mp4")
            try:
                _normalize_clip(
                    clip["processed_path"], norm_path,
                    rank_text=rank_text,
                    verbose=verbose,
                    subtitle_path=subtitle_path,
                )
            except Exception as e:
                console.print(f"  [yellow]Skipping clip {i} (normalize failed: {e})[/yellow]")
                continue
            segments.append(norm_path)

        if len(segments) < 2:
            raise ValueError(f"Only {len(segments)} clip(s) normalized successfully, need at least 2.")

        if state:
            state.compile_step = "Concatenating"
            state.compile_progress = 0.95

        console.print(f"  [dim]Concatenating {len(segments)} segments...[/dim]")
        concat_list = Path(tmpdir) / "concat.txt"
        with open(concat_list, "w") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")

        cmd = [
            get_ffmpeg(), "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Concat failed:[/red] {e.stderr[-300:]}")
            raise

    if state:
        state.compile_step = "Done"
        state.compile_progress = 1.0

    total_dur = _get_duration(str(output_path))
    console.print(f"[bold green]Compiled:[/bold green] {output_path.name} ({total_dur/60:.1f} min, {total} clips)")
    return output_path


def build_merged_subtitles(clip_metas: list[dict], output_path: Path) -> Path | None:
    """Merge per-clip ASS subtitles into one file with offset timestamps for the full compilation."""
    from clipper.process.subtitles import _format_ass_time, ASS_HEADER_REGULAR

    output_path = Path(output_path)
    all_events = []
    cumulative = 0.0

    for clip in clip_metas:
        sub_path = clip.get("_subtitle_path", "")
        proc_path = clip.get("processed_path", "")

        # Get clip duration for offset calculation
        if proc_path and Path(proc_path).exists():
            try:
                dur = _get_duration(proc_path)
            except (ValueError, IndexError, subprocess.SubprocessError):
                dur = clip.get("duration", 30)
        else:
            dur = clip.get("duration", 30)

        if sub_path and Path(sub_path).exists():
            with open(sub_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract Dialogue lines from [Events] section
            in_events = False
            for line in content.splitlines():
                if line.strip().startswith("[Events]"):
                    in_events = True
                    continue
                if line.strip().startswith("[") and in_events:
                    break  # next section
                if in_events and line.strip().startswith("Dialogue:"):
                    # Parse: Dialogue: layer,start,end,style,name,marginL,marginR,marginV,effect,text
                    parts = line.split(",", 9)
                    if len(parts) >= 10:
                        try:
                            start_s = _parse_ass_time(parts[1].strip()) + cumulative
                            end_s = _parse_ass_time(parts[2].strip()) + cumulative
                            parts[1] = _format_ass_time(start_s)
                            parts[2] = _format_ass_time(end_s)
                            all_events.append(",".join(parts))
                        except (ValueError, IndexError):
                            continue

        cumulative += dur

    if not all_events:
        return None

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER_REGULAR)
        f.write("\n".join(all_events))
        f.write("\n")

    console.print(f"[green]Merged subtitles:[/green] {output_path.name} ({len(all_events)} events)")
    return output_path


def _parse_ass_time(timestamp: str) -> float:
    """Parse ASS timestamp (H:MM:SS.cc) to seconds."""
    parts = timestamp.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid ASS timestamp: {timestamp}")
    hrs = int(parts[0])
    mins = int(parts[1])
    sec_parts = parts[2].split(".")
    secs = int(sec_parts[0])
    centis = int(sec_parts[1]) if len(sec_parts) > 1 else 0
    return hrs * 3600 + mins * 60 + secs + centis / 100.0
