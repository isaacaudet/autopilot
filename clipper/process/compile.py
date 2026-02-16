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


def _get_duration(video_path: str) -> float:
    """Get video duration in seconds."""
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _get_dimensions(video_path: str) -> tuple[int, int]:
    """Get video width and height."""
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-print_format", "json",
         "-show_streams", video_path],
        capture_output=True, text=True,
    )
    probe = json.loads(result.stdout)
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
        *get_encoder_args(),
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
    """Generate a compilation thumbnail from the best clip's video.

    Extracts a frame, adds dark overlay, game name in large text,
    subtitle, and game logo if available at assets/logos/{game_slug}.png.
    """
    output_path = Path(output_path)

    # Find the clip with the most views for the thumbnail source
    best = max(clip_metas, key=lambda c: c.get("view_count", 0), default=None)
    if not best:
        return None

    source = best.get("processed_path", "")
    if not source or not Path(source).exists():
        return None

    # Check for game logo
    game_slug = game.lower().replace(" ", "_") if game else ""
    logo_dir = Path(__file__).parent.parent.parent / "assets" / "logos"
    logo_file = logo_dir / f"{game_slug}.png" if game_slug else None
    has_logo = logo_file is not None and logo_file.exists()

    try:
        duration = _get_duration(source)
        timestamp = duration * 0.3

        escaped_game = (game or "CLIPS").upper().replace("'", "\\'").replace(":", "\\:")
        escaped_sub = "DAILY HIGHLIGHTS".replace("'", "\\'")

        # Base filter: scale + pad + saturation/contrast + dark overlay + text
        base_vf = (
            f"scale={COMP_WIDTH}:{COMP_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={COMP_WIDTH}:{COMP_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
            f"eq=saturation=1.4:contrast=1.15,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill,"
            # Game name — big and centered
            f"drawtext=text='{escaped_game}'"
            f":font=Impact:fontsize=120"
            f":fontcolor=0xFF6600:borderw=6:bordercolor=black"
            f":x=(w-text_w)/2:y=h*0.30,"
            # Subtitle
            f"drawtext=text='{escaped_sub}'"
            f":font=Impact:fontsize=56"
            f":fontcolor=0x00FFFF:borderw=3:bordercolor=black"
            f":x=(w-text_w)/2:y=h*0.30+135"
        )

        if has_logo:
            # Use filter_complex to overlay the logo in top-right
            cmd = [
                get_ffmpeg(), "-y",
                "-ss", str(timestamp),
                "-i", source,
                "-i", str(logo_file),
                "-vframes", "1",
                "-filter_complex", (
                    f"[0:v]{base_vf}[bg];"
                    f"[1:v]scale=200:-1[logo];"
                    f"[bg][logo]overlay=W-w-40:40"
                ),
                "-q:v", "2",
                str(output_path),
            ]
        else:
            cmd = [
                get_ffmpeg(), "-y",
                "-ss", str(timestamp),
                "-i", source,
                "-vframes", "1",
                "-vf", base_vf,
                "-q:v", "2",
                str(output_path),
            ]

        subprocess.run(cmd, capture_output=True, text=True, check=True)
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

    # Timestamps
    ts_lines = ["TIMESTAMPS:"]
    cumulative = 0.0
    for clip in clip_metas:
        streamer = clip.get("streamer", "Unknown")
        title = clip.get("title", "")[:40]
        mins = int(cumulative // 60)
        secs = int(cumulative % 60)
        ts_lines.append(f"{mins}:{secs:02d} — {streamer}: {title}")
        cumulative += clip.get("duration", 30)

    # Streamer credits
    streamers = sorted(set(c.get("streamer", "") for c in clip_metas if c.get("streamer")))
    featuring = f"\nFeaturing: {', '.join(streamers)}" if streamers else ""
    twitch_lines = []
    if streamers:
        twitch_lines.append("\nWatch them live on Twitch:")
        for s in streamers:
            twitch_lines.append(f"  twitch.tv/{s}")

    # Hashtags
    game_tag = game.lower().replace(" ", "") if game else "gaming"
    hashtags = f"\n#{game_tag} #gaming #clips #highlights #compilation #twitch"

    # Assemble, progressively dropping sections to fit under MAX_CHARS
    def _join(*sections: str) -> str:
        return "\n".join(s for s in sections if s)

    timestamps_block = "\n".join(ts_lines)
    twitch_block = "\n".join(twitch_lines)

    # Try full description
    full = _join(intro, timestamps_block, featuring, twitch_block, hashtags)
    if len(full) <= MAX_CHARS:
        return full

    # Drop individual Twitch links
    trimmed = _join(intro, timestamps_block, featuring, hashtags)
    if len(trimmed) <= MAX_CHARS:
        return trimmed

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
            _normalize_clip(
                clip["processed_path"], norm_path,
                rank_text=rank_text,
                verbose=verbose,
                subtitle_path=subtitle_path,
            )
            segments.append(norm_path)

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
