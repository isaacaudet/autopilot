"""Reformat video to 9:16 vertical (1080x1920) for YouTube Shorts."""

import json
import subprocess
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg, get_ffprobe, get_encoder_args

console = Console()

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

# Audio normalization chain:
# - highpass at 80Hz removes rumble/mic handling noise
# - mild bass boost at 100Hz for impact on phone speakers
# - loudnorm to -14 LUFS (YouTube's target loudness)
AUDIO_FILTER = "highpass=f=80,bass=g=3:f=100:w=0.5,loudnorm=I=-14:TP=-1:LRA=11"


def _get_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Get width and height of a video using ffprobe."""
    cmd = [
        get_ffprobe(),
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)

    probe = json.loads(result.stdout)
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])

    raise RuntimeError(f"No video stream found in {video_path}")


def _is_approximately_vertical(width: int, height: int) -> bool:
    """Check if aspect ratio is already close to 9:16."""
    if height == 0:
        return False
    ratio = width / height
    target_ratio = 9 / 16  # 0.5625
    return abs(ratio - target_ratio) < 0.05


def format_for_shorts(video_path: Path, config: dict, verbose: bool = False) -> Path:
    """Reformat a video to 1080x1920 vertical for YouTube Shorts.

    For landscape (16:9) video: creates a blurred background with the original
    video centered on top. For already-vertical video: scales to 1080x1920.

    Also applies audio normalization (highpass + bass boost + loudnorm).

    Returns the path to the formatted output file.
    """
    video_path = Path(video_path)
    shorts_cfg = config.get("shorts", {})
    w = shorts_cfg.get("width", TARGET_WIDTH)
    h = shorts_cfg.get("height", TARGET_HEIGHT)

    output_path = video_path.with_name(f"{video_path.stem}_shorts.mp4")

    width, height = _get_video_dimensions(video_path)
    if verbose:
        console.print(f"  Source dimensions: {width}x{height}")

    if _is_approximately_vertical(width, height):
        # Already vertical — just scale
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        if verbose:
            console.print("  Already vertical, scaling to target.")
    else:
        # Landscape — blurred background + gameplay filling ~60% of vertical frame
        # Gameplay at 1080px wide → 608px tall (16:9). Positioned upper-third
        # leaves room for subtitles below without overlap.
        fg_w = w  # 1080
        fg_h = int(fg_w * height / width)  # maintain aspect ratio
        fg_y = int((h - fg_h) * 0.35)  # position slightly above center (35% from top)
        vf = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=30:5[bg];"
            f"[0:v]scale={fg_w}:{fg_h}[fg];"
            f"[bg][fg]overlay=0:{fg_y}"
        )
        if verbose:
            console.print(f"  Landscape detected, gameplay {fg_w}x{fg_h} at y={fg_y} on blurred bg.")

    ffmpeg_bin = get_ffmpeg()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-filter_complex" if "[" in vf else "-vf", vf,
        "-af", AUDIO_FILTER,
        *get_encoder_args(),
        "-c:a", "aac",
        "-b:a", "256k",
        str(output_path),
    ]

    if verbose:
        console.print(f"  Running ffmpeg: {ffmpeg_bin}")
        console.print(f"  Audio: {AUDIO_FILTER}")

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed:\n{e.stderr}")

    console.print(f"  [green]Formatted:[/green] {output_path.name}")
    return output_path
