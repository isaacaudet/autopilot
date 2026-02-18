"""Score clips by virality potential (v2): quality penalties + contextual ranking."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from clipper.config import get_ffmpeg

logger = logging.getLogger(__name__)


def _analyze_audio_energy(video_path: str) -> dict | None:
    """Analyze audio energy using FFmpeg volumedetect."""
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

SUPPORTED_FEATURES = (
    "duration",
    "velocity",
    "views",
    "keywords",
    "recency",
    "audio",
    "llm",
    "retention",
)

# Views-first default objective.
DEFAULT_WEIGHTS = {
    "duration": 16,
    "velocity": 34,
    "views": 26,
    "keywords": 4,
    "recency": 14,
    "audio": 4,
    "llm": 2,
}

_TITLE_STOPWORDS = {
    "the", "a", "an", "this", "that", "is", "it", "to", "for", "of", "and",
    "in", "on", "at", "my", "your", "our", "with", "from", "clip", "clips",
    "best", "moment", "moments", "live", "stream", "streamer", "new",
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _analysis_dict(clip: dict) -> dict:
    analysis = clip.get("_analysis") or clip.get("analysis") or {}
    if isinstance(analysis, str):
        try:
            analysis = json.loads(analysis)
        except Exception:
            analysis = {}
    return analysis if isinstance(analysis, dict) else {}


def _age_hours(clip: dict) -> float:
    created = str(clip.get("created_at", "") or "").strip()
    if not created:
        return 24.0
    try:
        created_dt = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        now = _dt.datetime.now(_dt.timezone.utc)
        return max(0.5, (now - created_dt).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return 24.0


def _duration_quality(duration: float) -> float:
    if duration <= 0:
        return 0.0
    if 12 <= duration <= 38:
        return 1.0
    if 8 <= duration <= 55:
        return 0.75
    if 6 <= duration <= 75:
        return 0.45
    if duration <= 95:
        return 0.2
    return 0.05


def _velocity_quality(views: float, age_hours: float) -> float:
    velocity = max(0.0, views) / max(0.5, age_hours)
    return _clamp(math.log1p(velocity) / math.log1p(700.0), 0.0, 1.0)


def _views_quality(views: float) -> float:
    return _clamp(math.log1p(max(0.0, views)) / math.log1p(12000.0), 0.0, 1.0)


def _keyword_quality(title: str) -> float:
    t = (title or "").lower()
    strong_hits = sum(1 for kw in _STRONG_KEYWORDS if kw in t)
    if strong_hits > 0:
        return 1.0
    moderate_hits = sum(1 for kw in _VIRAL_KEYWORDS if kw in t)
    if moderate_hits >= 2:
        return 0.8
    if moderate_hits == 1:
        return 0.55
    return 0.0


def _recency_quality(age_hours: float) -> float:
    if age_hours <= 3:
        return 1.0
    if age_hours <= 8:
        return 0.85
    if age_hours <= 24:
        return 0.65
    if age_hours <= 72:
        return 0.4
    if age_hours <= 168:
        return 0.22
    return 0.1


def _audio_quality(dynamic_range_db: float) -> float:
    if dynamic_range_db >= 20:
        return 1.0
    if dynamic_range_db >= 15:
        return 0.85
    if dynamic_range_db >= 10:
        return 0.60
    if dynamic_range_db >= 5:
        return 0.35
    return 0.15


def _llm_quality(clip: dict) -> float | None:
    analysis = _analysis_dict(clip)
    llm = _to_float(analysis.get("entertainment_score"), -1.0)
    if llm < 0:
        return None
    return _clamp(llm / 10.0, 0.0, 1.0)


def _retention_quality(clip: dict) -> float | None:
    analysis = _analysis_dict(clip)
    retention = _to_float(analysis.get("retention_prediction"), -1.0)
    if retention < 0:
        return None
    return _clamp(retention / 100.0, 0.0, 1.0)


def _looks_garbage_title(title: str) -> bool:
    cleaned = str(title or "").strip()
    if len(cleaned) < 4:
        return True
    ascii_chars = sum(1 for c in cleaned if ord(c) < 128)
    if ascii_chars / max(1, len(cleaned)) < 0.5:
        return True
    if len(set(cleaned.lower())) <= 2:
        return True
    return False


def _title_penalty_and_keyword_scale(title: str) -> tuple[float, float]:
    """Penalty-first title signal, with a reduced keyword influence for weak titles."""
    t = str(title or "").strip()
    if not t:
        return 24.0, 0.0

    tl = t.lower()
    penalty = 0.0
    if len(t) < 4:
        penalty += 20.0
    elif len(t) < 8:
        penalty += 6.0

    ascii_chars = sum(1 for c in t if ord(c) < 128)
    ascii_ratio = ascii_chars / max(1, len(t))
    if ascii_ratio < 0.5:
        penalty += 16.0
    elif ascii_ratio < 0.7:
        penalty += 8.0

    unique_ratio = len(set(tl)) / max(1, len(tl))
    if unique_ratio < 0.25:
        penalty += 10.0

    alpha_chars = sum(1 for c in t if c.isalpha())
    symbol_ratio = 1.0 - (alpha_chars / max(1, len(t)))
    if symbol_ratio > 0.55:
        penalty += 8.0

    # Long repeated-char runs (e.g. "loooool!!!!") are often noisy.
    if re.search(r"(.)\1{3,}", tl):
        penalty += 4.0

    if tl in {".", "-", "_", "..." }:
        penalty += 14.0

    if _looks_garbage_title(t):
        penalty += 6.0

    keyword_scale = 1.0
    if penalty >= 18:
        keyword_scale = 0.0
    elif penalty >= 12:
        keyword_scale = 0.25
    elif penalty >= 8:
        keyword_scale = 0.55
    return penalty, keyword_scale


def _quality_penalty(title: str, duration: float, views: float, velocity: float, age_hours: float) -> tuple[float, float, float]:
    """Penalty signals with freshness-aware low-view/velocity behavior."""
    title_penalty, keyword_scale = _title_penalty_and_keyword_scale(title)
    penalty = 0.0
    penalty += title_penalty

    if duration < 6 or duration > 95:
        penalty += 10.0
    elif duration < 8 or duration > 75:
        penalty += 5.0

    # Fresh clips get grace; older weak clips get stronger penalties.
    if age_hours <= 2:
        if views < 40 and velocity < 6:
            penalty += 2.0
    elif age_hours <= 6:
        if views < 80 and velocity < 8:
            penalty += 6.0
        elif views < 120 and velocity < 10:
            penalty += 3.0
    else:
        if views < 120 and velocity < 8:
            penalty += 8.0
        elif views < 200 and velocity < 12:
            penalty += 5.0

    if age_hours > 2 and views < 200 and velocity < 12 and _keyword_quality(title) <= 0.0:
        penalty += 4.0

    return penalty, keyword_scale, title_penalty


def _relative_quality(metric: float, baseline: float, scale: float = 10.0) -> float | None:
    if baseline <= 0:
        return None
    ratio = max(0.0, metric) / max(1e-6, baseline)
    return _clamp(math.log1p(ratio * scale) / math.log1p(scale * 4.0), 0.0, 1.0)


def _confidence_factor(
    qualities: dict[str, float | None],
    views: float,
    velocity: float,
    age_hours: float,
    title_penalty: float,
) -> float:
    """Confidence gating: weak evidence clips should rank lower even if one signal spikes."""
    evidence = 0.0
    if views >= 180 or _to_float(qualities.get("views"), 0.0) >= 0.55:
        evidence += 1.0
    if velocity >= 12 or _to_float(qualities.get("velocity"), 0.0) >= 0.52:
        evidence += 1.0
    if age_hours <= 6 and (views >= 70 or velocity >= 9):
        evidence += 0.5
    if qualities.get("llm") is not None:
        evidence += 0.75
    if qualities.get("audio") is not None:
        evidence += 0.25
    if title_penalty <= 6:
        evidence += 0.5

    if evidence >= 2.5:
        return 1.0
    if evidence >= 1.8:
        return 0.95
    if evidence >= 1.2:
        return 0.88
    if evidence >= 0.7:
        return 0.8
    return 0.72


def _canonical_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(title or "").lower())
    tokens = [t for t in text.split() if t and len(t) > 1 and t not in _TITLE_STOPWORDS]
    return " ".join(tokens[:12])


def _source_key(clip: dict) -> tuple[str, str]:
    streamer = str(clip.get("streamer", "") or "").strip().lower()
    game = str(clip.get("game", "") or "").strip().lower()
    return streamer, game


def _resolve_weights(weights: dict | None, available: dict[str, float | None]) -> dict[str, float]:
    merged = dict(DEFAULT_WEIGHTS)
    if isinstance(weights, dict):
        for key, raw in weights.items():
            if key not in SUPPORTED_FEATURES:
                continue
            value = _to_float(raw, -1.0)
            if value >= 0:
                merged[key] = value

    # In v2, titles are mostly penalties. Keep keyword bonus intentionally small.
    merged["keywords"] = min(_to_float(merged.get("keywords"), 0.0), 8.0)

    active = {
        key: weight
        for key, weight in merged.items()
        if weight > 0 and available.get(key) is not None
    }
    if active:
        return active

    # Last-resort fallback so callers always get a bounded score.
    return {
        key: value
        for key, value in DEFAULT_WEIGHTS.items()
        if value > 0 and available.get(key) is not None
    }


def score_clip(
    clip: dict,
    downloaded_path: str | None = None,
    weights: dict | None = None,
    game_multiplier: float = 1.0,
    context: dict | None = None,
    source_prior: float | None = None,
    extra_penalty: float = 0.0,
) -> float:
    """Score a clip's virality potential (0-100), v2."""
    duration = _to_float(clip.get("duration"), 0.0)
    views = _to_float(clip.get("view_count"), 0.0)
    age_h = _age_hours(clip)
    velocity = views / max(0.5, age_h)
    title = str(clip.get("title", "") or "")

    audio_dr = _to_float(clip.get("_audio_energy_db"), float("nan"))
    if math.isnan(audio_dr):
        audio_dr = _to_float(clip.get("audio_energy_db"), float("nan"))
    if math.isnan(audio_dr) and downloaded_path and Path(downloaded_path).exists():
        audio = _analyze_audio_energy(downloaded_path)
        if audio:
            audio_dr = _to_float(audio.get("dynamic_range_db"), float("nan"))

    qualities: dict[str, float | None] = {
        "duration": _duration_quality(duration),
        "velocity": _velocity_quality(views, age_h),
        "views": _views_quality(views),
        "keywords": _keyword_quality(title),
        "recency": _recency_quality(age_h),
        "audio": None if math.isnan(audio_dr) else _audio_quality(audio_dr),
        "llm": _llm_quality(clip),
        "retention": _retention_quality(clip),
    }

    # Per-game normalization in context: score clips relative to their own game's baseline.
    if context:
        game_key = str(clip.get("game", "") or "").strip().lower()
        game_baselines = context.get("game_baselines", {}) if isinstance(context, dict) else {}
        baseline = game_baselines.get(game_key) if isinstance(game_baselines, dict) else None
        if isinstance(baseline, dict):
            rel_vel = _relative_quality(velocity, _to_float(baseline.get("median_velocity"), 0.0), scale=10.0)
            rel_views = _relative_quality(views, _to_float(baseline.get("median_views"), 0.0), scale=10.0)
            if rel_vel is not None:
                qualities["velocity"] = 0.65 * _to_float(qualities.get("velocity"), 0.0) + 0.35 * rel_vel
            if rel_views is not None:
                qualities["views"] = 0.65 * _to_float(qualities.get("views"), 0.0) + 0.35 * rel_views

    penalty, keyword_scale, title_penalty = _quality_penalty(title, duration, views, velocity, age_h)
    qualities["keywords"] = _to_float(qualities.get("keywords"), 0.0) * keyword_scale

    active_weights = _resolve_weights(weights, qualities)
    total_weight = sum(active_weights.values()) or 1.0
    weighted_quality = sum(
        active_weights[name] * _to_float(qualities.get(name), 0.0)
        for name in active_weights
    ) / total_weight

    base_score = weighted_quality * 100.0
    prior_points = _to_float(source_prior if source_prior is not None else clip.get("_source_prior"), 0.0)
    raw = base_score - penalty - max(0.0, _to_float(extra_penalty, 0.0)) + prior_points
    raw *= max(0.1, _to_float(game_multiplier, 1.0))

    confidence = _confidence_factor(qualities, views, velocity, age_h, title_penalty)
    raw *= 0.92 + (0.08 * confidence)
    # Confidence cap stops weak-evidence clips from dominating the board.
    confidence_cap = 70.0 + (30.0 * confidence)
    final = min(raw, confidence_cap)
    return _clamp(final, 0.0, 100.0)


def build_v2_context(clips: list[dict]) -> dict:
    """Build ranking context (currently per-game medians for normalization)."""
    by_game_vel: dict[str, list[float]] = defaultdict(list)
    by_game_views: dict[str, list[float]] = defaultdict(list)

    for clip in clips:
        game = str(clip.get("game", "") or "").strip().lower()
        if not game:
            continue
        views = _to_float(clip.get("view_count"), 0.0)
        age_h = _age_hours(clip)
        velocity = views / max(0.5, age_h)
        by_game_vel[game].append(max(0.0, velocity))
        by_game_views[game].append(max(0.0, views))

    game_baselines: dict[str, dict[str, float]] = {}
    for game in set(list(by_game_vel.keys()) + list(by_game_views.keys())):
        vel_values = sorted(by_game_vel.get(game, []))
        view_values = sorted(by_game_views.get(game, []))
        if not vel_values and not view_values:
            continue
        mid_v = vel_values[len(vel_values) // 2] if vel_values else 0.0
        mid_views = view_values[len(view_values) // 2] if view_values else 0.0
        game_baselines[game] = {
            "median_velocity": float(mid_v),
            "median_views": float(mid_views),
        }

    return {"game_baselines": game_baselines}


def _source_prior_from_map(clip: dict, source_priors: dict[str, float] | None) -> float:
    if not source_priors:
        return _to_float(clip.get("_source_prior"), 0.0)
    streamer, game = _source_key(clip)
    return _to_float(
        source_priors.get(f"{streamer}|{game}", source_priors.get(f"game::{game}", clip.get("_source_prior", 0.0))),
        0.0,
    )


def _compute_duplicate_penalties(clips: list[dict], pre_scores: dict[int, float]) -> dict[int, float]:
    """Penalize duplicate/near-duplicate clips so one moment doesn't fill the board."""
    by_streamer_title: dict[str, list[dict]] = defaultdict(list)
    by_title: dict[str, list[dict]] = defaultdict(list)

    for clip in clips:
        canonical = _canonical_title(clip.get("title", ""))
        if not canonical:
            continue
        streamer = str(clip.get("streamer", "") or "").strip().lower()
        by_streamer_title[f"{streamer}|{canonical}"].append(clip)
        by_title[canonical].append(clip)

    penalties: dict[int, float] = {}

    for _, group in by_streamer_title.items():
        if len(group) < 2:
            continue
        ranked = sorted(group, key=lambda c: pre_scores.get(id(c), 0.0), reverse=True)
        for idx, clip in enumerate(ranked):
            if idx == 0:
                continue
            penalties[id(clip)] = penalties.get(id(clip), 0.0) + min(12.0, 4.0 * idx)

    for _, group in by_title.items():
        if len(group) < 3:
            continue
        ranked = sorted(group, key=lambda c: pre_scores.get(id(c), 0.0), reverse=True)
        for idx, clip in enumerate(ranked):
            if idx == 0:
                continue
            penalties[id(clip)] = penalties.get(id(clip), 0.0) + min(5.0, 1.5 * idx)

    return penalties


def _apply_diversity_rerank(scored: list[tuple[float, dict]], diversify: bool = True) -> list[tuple[float, dict]]:
    if not diversify:
        return sorted(scored, key=lambda pair: pair[0], reverse=True)

    remaining = sorted(scored, key=lambda pair: pair[0], reverse=True)
    picked: list[tuple[float, dict]] = []
    streamer_counts: dict[str, int] = defaultdict(int)

    while remaining:
        best_idx = 0
        best_adjusted = -1.0
        for idx, (score, clip) in enumerate(remaining):
            streamer = str(clip.get("streamer", "") or "").strip().lower()
            repeat = streamer_counts.get(streamer, 0)
            diversity_penalty = min(8.0, repeat * 2.5)
            adjusted = score - diversity_penalty
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_idx = idx

        base_score, chosen = remaining.pop(best_idx)
        streamer = str(chosen.get("streamer", "") or "").strip().lower()
        repeat = streamer_counts.get(streamer, 0)
        diversity_penalty = min(8.0, repeat * 2.5)
        final_score = _clamp(base_score - diversity_penalty, 0.0, 100.0)
        picked.append((final_score, chosen))
        streamer_counts[streamer] += 1

    return picked


def rank_clips_v2(
    clips: list[dict],
    *,
    weights: dict | None = None,
    source_priors: dict[str, float] | None = None,
    diversify: bool = True,
) -> list[dict]:
    """Rank clips with v2 logic: contextual scoring + duplicate suppression + diversity."""
    if not clips:
        return []

    context = build_v2_context(clips)

    pre_scored: list[tuple[float, dict]] = []
    for clip in clips:
        mult = _to_float(clip.get("_game_multiplier"), 1.0)
        prior = _source_prior_from_map(clip, source_priors)
        base = score_clip(
            clip,
            weights=weights,
            game_multiplier=mult,
            context=context,
            source_prior=prior,
        )
        pre_scored.append((base, clip))

    pre_map = {id(c): s for s, c in pre_scored}
    dup_penalties = _compute_duplicate_penalties(clips, pre_map)

    adjusted: list[tuple[float, dict]] = []
    for _, clip in pre_scored:
        mult = _to_float(clip.get("_game_multiplier"), 1.0)
        prior = _source_prior_from_map(clip, source_priors)
        score = score_clip(
            clip,
            weights=weights,
            game_multiplier=mult,
            context=context,
            source_prior=prior,
            extra_penalty=dup_penalties.get(id(clip), 0.0),
        )
        adjusted.append((score, clip))

    ranked = _apply_diversity_rerank(adjusted, diversify=diversify)
    for score, clip in ranked:
        rounded = round(_clamp(score, 0.0, 100.0), 2)
        clip["_score"] = rounded
        clip["score"] = rounded
    return [clip for _, clip in ranked]


def enhanced_score(clip: dict) -> float:
    """Score with modest post-analysis boosts for ordering finalized clips."""
    base = score_clip(clip)
    analysis = _analysis_dict(clip)
    if not analysis:
        return base

    llm = _to_float(analysis.get("entertainment_score"), 5.0)
    retention = _to_float(analysis.get("retention_prediction"), 50.0)
    llm_bonus = (llm - 5.0) * 2.0  # -8 .. +10
    retention_bonus = ((retention - 50.0) / 50.0) * 5.0  # -5 .. +5
    return _clamp(base + llm_bonus + retention_bonus, 0.0, 100.0)


def rank_clips(clips: list[dict]) -> list[dict]:
    """Backward-compatible rank helper."""
    return rank_clips_v2(clips, diversify=False)
