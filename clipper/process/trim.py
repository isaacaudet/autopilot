"""Trim silence and tighten pacing for Shorts clips."""

import re
import subprocess
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg

console = Console()


def _detect_silence(video_path: Path, noise_db: float = -30, min_duration: float = 0.5) -> list[tuple[float, float]]:
    """Detect silent segments in a video using FFmpeg silencedetect.

    Returns list of (start, end) tuples for each silent segment.
    """
    ffmpeg_bin = get_ffmpeg()
    cmd = [
        ffmpeg_bin,
        "-i", str(video_path),
        "-af", f"silencedetect=n={noise_db}dB:d={min_duration}",
        "-f", "null",
        "-",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    stderr = result.stderr

    silences = []
    current_start = None

    for line in stderr.splitlines():
        if "silence_start:" in line:
            match = re.search(r"silence_start:\s*([\d.]+)", line)
            if match:
                current_start = float(match.group(1))
        elif "silence_end:" in line and current_start is not None:
            match = re.search(r"silence_end:\s*([\d.]+)", line)
            if match:
                silences.append((current_start, float(match.group(1))))
                current_start = None

    return silences


def _get_duration(video_path: Path) -> float:
    """Get video duration in seconds."""
    from clipper.config import get_ffprobe

    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def trim_dead_air(video_path: Path, verbose: bool = False) -> Path:
    """Trim leading/trailing silence and long interior pauses from a clip.

    Only trims silence longer than 0.8s. Keeps a small 0.15s pad
    at boundaries so cuts don't feel abrupt.

    Returns path to trimmed video (may be same as input if no trimming needed).
    """
    video_path = Path(video_path)
    duration = _get_duration(video_path)
    silences = _detect_silence(video_path, noise_db=-30, min_duration=0.8)

    if not silences:
        if verbose:
            console.print("  No dead air detected.")
        return video_path

    # Build list of segments to keep (non-silent portions)
    # Pad silence boundaries by 0.15s so cuts feel natural
    PAD = 0.15
    keep_segments = []
    prev_end = 0.0

    for silence_start, silence_end in silences:
        # Keep the segment before this silence
        seg_start = prev_end
        seg_end = silence_start + PAD  # keep a tiny bit into the silence

        if seg_end > seg_start + 0.1:  # only keep meaningful segments
            keep_segments.append((max(0, seg_start), min(duration, seg_end)))

        prev_end = silence_end - PAD  # resume a tiny bit before silence ends

    # Keep the final segment after the last silence
    if prev_end < duration:
        keep_segments.append((max(0, prev_end), duration))

    if not keep_segments:
        if verbose:
            console.print("  No segments to keep after trimming.")
        return video_path

    # Calculate total trim savings
    original_dur = duration
    trimmed_dur = sum(end - start for start, end in keep_segments)
    savings = original_dur - trimmed_dur

    if savings < 0.5:
        if verbose:
            console.print(f"  Only {savings:.1f}s of silence — not worth trimming.")
        return video_path

    if verbose:
        console.print(f"  Found {len(silences)} silent segment(s), trimming {savings:.1f}s of dead air")

    # Use FFmpeg concat filter to join the kept segments
    ffmpeg_bin = get_ffmpeg()
    output_path = video_path.with_name(f"{video_path.stem}_trimmed.mp4")

    # Build filter_complex with trim + concat
    parts_v = []
    parts_a = []
    for i, (start, end) in enumerate(keep_segments):
        parts_v.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]")
        parts_a.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]")

    n = len(keep_segments)
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    filter_complex = ";".join(parts_v + parts_a) + f";{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "256k",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        console.print(f"  [green]Trimmed:[/green] {savings:.1f}s of dead air removed ({original_dur:.1f}s -> {trimmed_dur:.1f}s)")
        return output_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if verbose:
            console.print(f"  [yellow]Trim failed, using original: {e}[/yellow]")
        return video_path
