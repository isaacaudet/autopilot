"""Score clips by virality potential using audio energy, duration, and engagement."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

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
    except subprocess.TimeoutExpired:
        logger.debug("Audio analysis timed out for %s", video_path)
    except Exception as e:
        logger.debug("Audio analysis failed for %s: %s", video_path, e)

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


DEFAULT_WEIGHTS = {
    "duration": 25,
    "velocity": 35,
    "keywords": 15,
    "recency": 5,
    "audio": 20,
}


def score_clip(
    clip: dict,
    downloaded_path: str | None = None,
    weights: dict | None = None,
    game_multiplier: float = 1.0,
) -> float:
    """Score a clip's virality potential (0-100).

    Factors:
    - Duration: 15-30s clips score highest (best retention on Shorts)
    - View count: higher views = proven engagement
    - Title: viral keywords signal reaction/highlight moments
    - Recency: newer clips are more relevant
    - Audio energy: high dynamic range = reaction moments (post-download only)
    - Game multiplier: scales score based on game's YouTube performance history

    Args:
        clip: Clip metadata dict (from fetch).
        downloaded_path: Path to downloaded video (for audio analysis). Optional.
        weights: Optional learned weights dict (keys: duration, velocity, keywords,
            recency, audio, llm). Values are max points per category. If None,
            uses hardcoded defaults.
        game_multiplier: Multiplier based on game's YouTube performance (default 1.0).

    Returns:
        Score from 0-100.
    """
    w = weights or DEFAULT_WEIGHTS
    score = 0.0

    # --- Duration score ---
    max_dur = w.get("duration", 25)
    duration = clip.get("duration", 0)
    if 15 <= duration <= 30:
        score += max_dur
    elif 10 <= duration <= 45:
        score += max_dur * 0.72
    elif duration <= 60:
        score += max_dur * 0.40

    # --- View velocity score ---
    max_vel = w.get("velocity", 35)
    import datetime
    views = clip.get("view_count", 0)
    created = clip.get("created_at", "")
    age_hours = 24.0

    if created:
        try:
            created_dt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_hours = max(0.5, (datetime.datetime.now(datetime.timezone.utc) - created_dt).total_seconds() / 3600)
        except (ValueError, TypeError):
            pass

    velocity = views / age_hours

    if velocity >= 500:
        score += max_vel
    elif velocity >= 200:
        score += max_vel * 0.86
    elif velocity >= 100:
        score += max_vel * 0.71
    elif velocity >= 50:
        score += max_vel * 0.57
    elif velocity >= 20:
        score += max_vel * 0.43
    elif velocity >= 10:
        score += max_vel * 0.29
    else:
        score += max_vel * 0.14

    # --- Title score ---
    max_kw = w.get("keywords", 15)
    title = clip.get("title", "").lower()
    title_score = 0
    for keyword in _VIRAL_KEYWORDS:
        if keyword in title:
            if keyword in _STRONG_KEYWORDS:
                title_score = max(title_score, max_kw)
            else:
                title_score = max(title_score, max_kw * 0.67)
    score += title_score

    # --- Recency bonus ---
    max_rec = w.get("recency", 5)
    if age_hours <= 3:
        score += max_rec
    elif age_hours <= 6:
        score += max_rec * 0.6
    elif age_hours <= 12:
        score += max_rec * 0.2

    # --- Audio energy score ---
    max_audio = w.get("audio", 20)
    if downloaded_path and Path(downloaded_path).exists():
        audio = _analyze_audio_energy(downloaded_path)
        if audio:
            dr = audio["dynamic_range_db"]
            if dr >= 20:
                score += max_audio
            elif dr >= 15:
                score += max_audio * 0.85
            elif dr >= 10:
                score += max_audio * 0.60
            elif dr >= 5:
                score += max_audio * 0.35
            else:
                score += max_audio * 0.15

    # --- LLM bonus (if learned weights include it) ---
    max_llm = w.get("llm", 0)
    if max_llm > 0:
        analysis = clip.get("_analysis")
        if analysis:
            llm_score = analysis.get("entertainment_score", 5)
            score += max_llm * (llm_score / 10.0)

    return min(100, score * game_multiplier)


def enhanced_score(clip: dict) -> float:
    """Score with LLM entertainment bonus (post-analysis).

    Adds up to +/-20 points based on LLM entertainment_score.
    Falls back to base score_clip() if no analysis available.
    """
    base = score_clip(clip)
    analysis = clip.get("_analysis")
    if not analysis:
        return base
    llm_bonus = (analysis.get("entertainment_score", 5) - 5) * 4  # -16 to +20
    return min(100, max(0, base + llm_bonus))


def rank_clips(clips: list[dict]) -> list[dict]:
    """Rank a list of clips by their virality score (descending).

    Adds a 'score' key to each clip dict.
    """
    for clip in clips:
        clip["score"] = score_clip(clip)

    return sorted(clips, key=lambda c: c["score"], reverse=True)
