"""Score clips by virality potential using audio energy, duration, and engagement."""

import subprocess
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg

console = Console()


def _analyze_audio_energy(video_path: str) -> dict | None:
    """Analyze audio energy using FFmpeg volumedetect.

    Returns dict with mean_volume, max_volume, and dynamic_range_db.
    High dynamic range (>15dB) indicates reaction moments / highlights.
    """
    ffmpeg_bin = get_ffmpeg()
    cmd = [
        ffmpeg_bin,
        "-i", video_path,
        "-af", "volumedetect",
        "-f", "null",
        "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stderr = result.stderr

        mean_vol = None
        max_vol = None

        for line in stderr.splitlines():
            if "mean_volume:" in line:
                mean_vol = float(line.split("mean_volume:")[1].strip().split()[0])
            if "max_volume:" in line:
                max_vol = float(line.split("max_volume:")[1].strip().split()[0])

        if mean_vol is not None and max_vol is not None:
            return {
                "mean_volume": mean_vol,
                "max_volume": max_vol,
                "dynamic_range_db": max_vol - mean_vol,
            }
    except (subprocess.TimeoutExpired, Exception):
        pass

    return None




# Title keywords that signal viral/reaction-heavy clips
_VIRAL_KEYWORDS = {
    # Strong signals (high value)
    "insane", "crazy", "clutch", "1v5", "1v4", "1v3", "ace", "rage", "toxic",
    "screaming", "freakout", "banned", "destroyed", "exposed", "drama",
    "world record", "impossible", "unbelievable", "epic", "fastest",
    # Moderate signals
    "funny", "fail", "rage quit", "tilted", "malding", "copium",
    "reaction", "shocked", "crying", "heated", "roast", "flamed",
    "outplay", "1hp", "no way", "wtf", "omg",
}

_STRONG_KEYWORDS = {
    "insane", "crazy", "clutch", "1v5", "1v4", "ace", "rage", "banned",
    "destroyed", "exposed", "drama", "world record", "impossible",
    "screaming", "freakout", "unbelievable",
}


def score_clip(clip: dict, downloaded_path: str | None = None) -> float:
    """Score a clip's virality potential (0-100).

    Factors:
    - Duration: 15-30s clips score highest (best retention on Shorts)
    - View count: higher views = proven engagement
    - Title: viral keywords signal reaction/highlight moments
    - Recency: newer clips are more relevant
    - Audio energy: high dynamic range = reaction moments (post-download only)

    Args:
        clip: Clip metadata dict (from fetch).
        downloaded_path: Path to downloaded video (for audio analysis). Optional.

    Returns:
        Score from 0-100.
    """
    score = 0.0

    # --- Duration score (0-25 points) ---
    # 15-30s is the sweet spot for Shorts retention
    duration = clip.get("duration", 0)
    if 15 <= duration <= 30:
        score += 25  # ideal range
    elif 10 <= duration <= 45:
        score += 18  # acceptable
    elif duration <= 60:
        score += 10  # okay for Shorts
    # >60s gets 0 duration points

    # --- View velocity score (0-35 points) ---
    # Views/hour is a much stronger signal than raw views.
    # A clip with 400 views in 2 hours (200/hr) is hotter than
    # 2000 views in 48 hours (42/hr).
    import datetime
    views = clip.get("view_count", 0)
    created = clip.get("created_at", "")
    age_hours = 24.0  # fallback if no timestamp

    if created:
        try:
            created_dt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_hours = max(0.5, (datetime.datetime.now(datetime.timezone.utc) - created_dt).total_seconds() / 3600)
        except (ValueError, TypeError):
            pass

    velocity = views / age_hours

    if velocity >= 500:
        score += 35  # explosive — grab immediately
    elif velocity >= 200:
        score += 30  # very hot
    elif velocity >= 100:
        score += 25
    elif velocity >= 50:
        score += 20  # promising
    elif velocity >= 20:
        score += 15
    elif velocity >= 10:
        score += 10  # moderate
    else:
        score += 5   # low velocity

    # --- Title score (0-15 points) ---
    # Titles with viral keywords predict engagement on Shorts
    title = clip.get("title", "").lower()
    title_score = 0
    for keyword in _VIRAL_KEYWORDS:
        if keyword in title:
            if keyword in _STRONG_KEYWORDS:
                title_score = max(title_score, 15)
            else:
                title_score = max(title_score, 10)
    score += title_score

    # --- Recency bonus (0-5 points) ---
    # Slight extra nudge for very fresh clips (velocity already captures most of this)
    if age_hours <= 3:
        score += 5
    elif age_hours <= 6:
        score += 3
    elif age_hours <= 12:
        score += 1

    # --- Audio energy score (0-20 points) ---
    if downloaded_path and Path(downloaded_path).exists():
        audio = _analyze_audio_energy(downloaded_path)
        if audio:
            dr = audio["dynamic_range_db"]
            # High dynamic range = reaction moment
            # >20dB = screaming/huge reaction, >15dB = solid reaction
            if dr >= 20:
                score += 20
            elif dr >= 15:
                score += 17
            elif dr >= 10:
                score += 12
            elif dr >= 5:
                score += 7
            else:
                score += 3

    return min(100, score)


def rank_clips(clips: list[dict]) -> list[dict]:
    """Rank a list of clips by their virality score (descending).

    Adds a 'score' key to each clip dict.
    """
    for clip in clips:
        clip["score"] = score_clip(clip)

    return sorted(clips, key=lambda c: c["score"], reverse=True)
