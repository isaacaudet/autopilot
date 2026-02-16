"""Duration tier computation for compilation length selection."""

TIER_TARGETS = [
    (8, "8 min (mid-roll ads unlock)"),
    (10, "10 min"),
    (12, "12 min"),
    (15, "15 min"),
]


def compute_duration_tiers(clips: list[dict]) -> list[dict]:
    """Compute compilation duration tiers from scored clips.

    Args:
        clips: Clips sorted by _score descending. Each must have 'duration' and '_score'.

    Returns:
        List of tier dicts: {target_min, label, count, avg_score, actual_min, quality}.
        Only tiers with >=2 clips are included.
    """
    tiers = []
    for target_min, label in TIER_TARGETS:
        target_sec = target_min * 60
        cumulative = 0.0
        count = 0
        total_score = 0.0

        for clip in clips:
            clip_time = clip.get("duration", 30)
            if cumulative + clip_time > target_sec:
                break
            cumulative += clip_time
            count += 1
            total_score += clip.get("_score", 0)

        if count >= 2:
            avg_score = total_score / count
            quality = "excellent" if avg_score >= 35 else "good" if avg_score >= 25 else "decent"
            tiers.append({
                "target_min": target_min,
                "label": label,
                "count": count,
                "avg_score": avg_score,
                "actual_min": cumulative / 60,
                "quality": quality,
            })

    return tiers


def clips_for_duration(clips: list[dict], target_minutes: int) -> int:
    """Return the number of clips needed to fill a target duration.

    Args:
        clips: Clips sorted by _score descending. Each must have 'duration'.
        target_minutes: Target compilation length in minutes.

    Returns:
        Clip count (minimum 2).
    """
    target_sec = target_minutes * 60
    cumulative = 0.0
    count = 0
    for clip in clips:
        clip_time = clip.get("duration", 30)
        if cumulative + clip_time > target_sec:
            break
        cumulative += clip_time
        count += 1
    return max(2, count)
