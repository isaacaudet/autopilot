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


def get_video_duration(video_path: Path) -> float:
    """Public helper to read media duration in seconds."""
    return _get_duration(Path(video_path))


def trim_manual_window(
    video_path: Path,
    *,
    trim_start: float = 0.0,
    trim_end: float = 0.0,
    verbose: bool = False,
) -> Path:
    """Trim a clip by removing seconds from start/end.

    Args:
        trim_start: Seconds to remove from the beginning.
        trim_end: Seconds to remove from the end.
    """
    video_path = Path(video_path)
    start_cut = max(0.0, float(trim_start or 0.0))
    end_cut = max(0.0, float(trim_end or 0.0))
    if start_cut <= 0.0 and end_cut <= 0.0:
        return video_path

    try:
        duration = _get_duration(video_path)
    except (ValueError, OSError):
        return video_path

    if duration <= 0.0:
        return video_path

    start = min(start_cut, max(0.0, duration - 0.2))
    end = max(start + 0.2, duration - end_cut)
    if end - start < 0.35:
        if verbose:
            console.print(
                f"  [yellow]Manual trim ignored:[/yellow] invalid window "
                f"({start:.2f}s → {end:.2f}s)"
            )
        return video_path

    output_path = video_path.with_name(f"{video_path.stem}_manualtrim.mp4")
    cmd = [
        get_ffmpeg(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)
        if verbose:
            kept = max(0.0, end - start)
            console.print(
                f"  [green]Manual trim:[/green] "
                f"-{start_cut:.2f}s start, -{end_cut:.2f}s end "
                f"({duration:.2f}s → {kept:.2f}s)"
            )
        return output_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if verbose:
            console.print(f"  [yellow]Manual trim failed, using original: {e}[/yellow]")
        return video_path


def find_peak_moment(video_path: Path, analysis: dict | None = None, verbose: bool = False) -> float | None:
    """Find the peak entertainment moment in a clip using audio + LLM signals.

    Returns timestamp in seconds, or None if no clear signal.
    Audio RMS peak (weight 0.6) + LLM moment_timestamp (weight 0.4).
    """
    video_path = Path(video_path)
    try:
        duration = _get_duration(video_path)
    except (ValueError, OSError):
        return None
    signals = []

    # Audio peak detection via volumedetect with windowed analysis
    ffmpeg_bin = get_ffmpeg()
    # Use astats to get per-second RMS levels
    cmd = [
        ffmpeg_bin,
        "-i", str(video_path),
        "-af", "astats=metadata=1:reset=1",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # Parse RMS levels from stderr — look for peak timestamps
        max_rms = -999.0
        max_rms_time = None
        current_time = 0.0

        for line in result.stderr.splitlines():
            if "lavfi.astats.Overall.RMS_level" in line:
                match = re.search(r"RMS_level=([-\d.]+)", line)
                if match:
                    rms = float(match.group(1))
                    if rms > max_rms:
                        max_rms = rms
                        max_rms_time = current_time
                current_time += 1.0  # reset=1 gives per-second stats

        if max_rms_time is not None and max_rms > -50:
            signals.append(("audio", max_rms_time, 0.6))
            if verbose:
                console.print(f"  [dim]Audio peak at {max_rms_time:.1f}s (RMS: {max_rms:.1f}dB)[/dim]")
    except (subprocess.TimeoutExpired, Exception):
        pass

    # LLM moment timestamp
    if analysis and isinstance(analysis.get("moment_timestamp"), (int, float)):
        llm_ts = float(analysis["moment_timestamp"])
        if 0 <= llm_ts <= duration:
            signals.append(("llm", llm_ts, 0.4))
            if verbose:
                console.print(f"  [dim]LLM peak at {llm_ts:.1f}s[/dim]")

    if not signals:
        return None

    # Weighted average
    total_weight = sum(w for _, _, w in signals)
    peak = sum(ts * w for _, ts, w in signals) / total_weight
    return max(0, min(duration, peak))


def smart_trim(video_path: Path, peak_timestamp: float, target_duration: float = 30.0, verbose: bool = False) -> Path:
    """Trim clip around peak moment using stream copy (instant, no re-encode).

    Centers window around peak: 5s before, 3s after, expands to target_duration.
    Only trims if saving >=5s. Returns original path if not worthwhile.
    """
    video_path = Path(video_path)
    try:
        duration = _get_duration(video_path)
    except (ValueError, OSError):
        return video_path

    # Not worth trimming if already near target
    if duration <= target_duration + 5:
        if verbose:
            console.print(f"  [dim]Clip is {duration:.1f}s — no smart trim needed[/dim]")
        return video_path

    # Build window around peak
    # Asymmetric: more time before peak (buildup) than after (payoff)
    before_peak = target_duration * 0.6
    after_peak = target_duration * 0.4

    start = peak_timestamp - before_peak
    end = peak_timestamp + after_peak

    # Clamp to video bounds
    if start < 0:
        end -= start  # shift right
        start = 0
    if end > duration:
        start -= (end - duration)  # shift left
        end = duration
    start = max(0, start)

    trimmed_duration = end - start
    savings = duration - trimmed_duration

    if savings < 5:
        if verbose:
            console.print(f"  [dim]Smart trim would only save {savings:.1f}s — skipping[/dim]")
        return video_path

    if verbose:
        console.print(f"  [blue]Smart trim:[/blue] {duration:.1f}s → {trimmed_duration:.1f}s (peak at {peak_timestamp:.1f}s)")

    ffmpeg_bin = get_ffmpeg()
    output_path = video_path.with_name(f"{video_path.stem}_smarttrim.mp4")

    cmd = [
        ffmpeg_bin, "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(trimmed_duration),
        "-c", "copy",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        console.print(f"  [green]Smart trimmed:[/green] {duration:.1f}s → {trimmed_duration:.1f}s (saved {savings:.1f}s)")
        return output_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if verbose:
            console.print(f"  [yellow]Smart trim failed, using original: {e}[/yellow]")
        return video_path


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
        "-movflags", "+faststart",
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
