"""Closed-loop learning: YouTube performance → scoring weight optimization."""

import math
from datetime import datetime, timezone

from rich.console import Console

console = Console()

DEFAULT_WEIGHTS = {
    "duration": 18,
    "velocity": 32,
    "views": 22,
    "keywords": 10,
    "recency": 10,
    "audio": 6,
    "llm": 2,
}

_MIN_SAMPLES = 20
_MIN_WEIGHT = 5


def collect_performance(config: dict, min_age_hours: float = 48) -> int:
    """Pull all channel uploads from YouTube, batch-fetch stats, save to performance table.

    Uses the uploads playlist approach (ceiling: ~10 API calls for 400 videos)
    instead of one call per clip. Backlills video_id on DB clips that are missing it.
    Re-collects on a schedule: every 24h for <7d clips, 72h for 7-30d, 14d for older.
    Returns number of snapshots saved/updated.
    """
    from clipper.analytics import fetch_channel_recent_ex
    from clipper.db import list_clips, performance_ids, save_performance, update_clip
    from clipper.process.score import _STRONG_KEYWORDS, _VIRAL_KEYWORDS

    existing = performance_ids(config)  # {clip_id: collected_at}
    now = datetime.now(timezone.utc)
    collected = 0

    # Pull ALL channel uploads in one sweep (uploads playlist + batch stats)
    # 365 days covers the full history; fetch_channel_recent_ex batches in 50s
    channel_key = (config.get("autopilot", {}) or {}).get("channel") or "default"
    yt_videos, err = fetch_channel_recent_ex(
        config=config, channel=channel_key, days=365, refresh=True
    )
    if err and not yt_videos:
        console.print(f"[yellow]collect_performance: YouTube fetch failed ({err})[/yellow]")
        return 0

    console.print(f"  collect_performance: {len(yt_videos)} channel videos fetched")

    # Build video_id → YT stats map
    yt_by_video_id: dict[str, dict] = {v["video_id"]: v for v in yt_videos if v.get("video_id")}

    # Load all output clips and index by video_id for fast lookup
    output_clips = list_clips(config, status="output", limit=5000)
    clips_by_video_id: dict[str, dict] = {}
    clips_by_id: dict[str, dict] = {}
    for clip in output_clips:
        vid = clip.get("video_id", "")
        if vid and vid != "previously_uploaded":
            clips_by_video_id[vid] = clip
        clips_by_id[clip.get("id", "")] = clip

    # Backfill video_id for DB clips that are missing it — match by title
    untracked_clips = [c for c in output_clips if not c.get("video_id") or c.get("video_id") == "previously_uploaded"]
    if untracked_clips:
        # Build title → video lookup from YouTube side
        yt_by_title: dict[str, dict] = {}
        for v in yt_videos:
            t = (v.get("title") or "").strip().lower()
            if t:
                yt_by_title[t] = v

        backfilled = 0
        for clip in untracked_clips:
            clip_title = (clip.get("title") or "").strip().lower()
            if clip_title and clip_title in yt_by_title:
                matched = yt_by_title[clip_title]
                vid = matched["video_id"]
                update_clip(config, clip["id"], video_id=vid)
                clip["video_id"] = vid
                clips_by_video_id[vid] = clip
                backfilled += 1
        if backfilled:
            console.print(f"  Backfilled video_id for {backfilled} untracked clips")

    # Now save performance for all YT videos we can match to a DB clip
    for video_id, yt in yt_by_video_id.items():
        clip = clips_by_video_id.get(video_id)
        if not clip:
            continue

        clip_id = clip.get("id", "")

        # Check published age
        published_at = yt.get("published_at", "")
        age_hours = 72.0
        if published_at:
            try:
                pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                age_hours = max(0.0, (now - pub_dt).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

        if age_hours < min_age_hours:
            continue

        # Re-collection schedule
        if clip_id in existing:
            try:
                last_dt = datetime.fromisoformat(existing[clip_id].replace("Z", "+00:00"))
                hours_since = (now - last_dt).total_seconds() / 3600
                if age_hours < 7 * 24:
                    interval = 24.0
                elif age_hours < 30 * 24:
                    interval = 72.0
                else:
                    interval = 14 * 24.0
                if hours_since < interval:
                    continue
            except (ValueError, TypeError):
                pass

        # Build features from DB clip metadata
        title = clip.get("title", "").lower()
        has_strong = any(kw in title for kw in _STRONG_KEYWORDS)
        has_moderate = any(kw in title for kw in _VIRAL_KEYWORDS) and not has_strong
        analysis = clip.get("_analysis", {}) or {}
        source_views = clip.get("view_count", 0) or 0
        age_h = max(0.5, age_hours)

        features = {
            "duration_sec": clip.get("duration", 0),
            "velocity": source_views / age_h,
            "source_views": source_views,
            "age_hours": age_h,
            "streamer": clip.get("streamer", ""),
            "has_strong_keyword": has_strong,
            "has_moderate_keyword": has_moderate,
            "audio_energy_db": clip.get("_audio_energy_db", 0),
            "llm_score": analysis.get("entertainment_score", 0),
            "category": analysis.get("category", "other"),
            "game": clip.get("game", ""),
        }
        youtube = {
            "views": yt.get("views", 0),
            "likes": yt.get("likes", 0),
            "comments": yt.get("comments", 0),
            "avg_view_pct": 0,
            "avg_duration_sec": 0,
        }

        save_performance(config, clip_id, now.isoformat(), features, youtube)
        existing[clip_id] = now.isoformat()
        collected += 1
        console.print(f"  [green]+[/green] {clip.get('streamer','?')} — {clip.get('title','?')[:40]}: {yt.get('views',0):,} views")

    return collected


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation coefficient. Returns 0 if not computable."""
    n = len(xs)
    if n < 3:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def train_weights(config: dict) -> dict | None:
    """Analyze performance data, compute feature->performance correlations, save learned weights.

    Returns the weights dict with metadata, or None if not enough data.
    """
    from clipper.db import list_performance, save_weights

    perf_data = list_performance(config)

    if not perf_data:
        console.print("[yellow]No performance data yet. Run 'clipper learn' after uploading clips.[/yellow]")
        return None

    if len(perf_data) < _MIN_SAMPLES:
        console.print(
            f"[yellow]Only {len(perf_data)} samples (need {_MIN_SAMPLES}). "
            f"Upload more clips and run again.[/yellow]"
        )
        return None

    # Target: log-transformed YouTube views at collection time.
    # This reduces outlier domination from a few extreme hits.
    target = [math.log1p(max(0.0, float((e.get("youtube") or {}).get("views", 0)))) for e in perf_data]

    def _feature(entry: dict, key: str, default=0.0) -> float:
        return float((entry.get("features") or {}).get(key, default) or default)

    source_views: list[float] = []
    age_hours: list[float] = []
    velocities: list[float] = []

    for e in perf_data:
        vel = max(0.0, _feature(e, "velocity", 0.0))
        age = max(0.0, _feature(e, "age_hours", 0.0))
        src = max(0.0, _feature(e, "source_views", 0.0))

        # Backfill historical entries collected before these fields existed.
        if src <= 0 and vel > 0 and age > 0:
            src = vel * age
        if age <= 0 and vel > 0 and src > 0:
            age = src / vel
        if age <= 0:
            age = 24.0

        source_views.append(src)
        age_hours.append(age)
        velocities.append(vel)

    # Feature vectors
    features = {
        "duration": [_feature(e, "duration_sec", 0.0) for e in perf_data],
        "velocity": velocities,
        "views": source_views,
        "keywords": [
            (
                1.0
                if (e.get("features") or {}).get("has_strong_keyword")
                else 0.5 if (e.get("features") or {}).get("has_moderate_keyword") else 0.0
            )
            for e in perf_data
        ],
        "recency": [1.0 / max(1.0, age) for age in age_hours],
        "audio": [_feature(e, "audio_energy_db", 0.0) for e in perf_data],
    }

    # Add LLM score if enough clips have it
    llm_scores = [_feature(e, "llm_score", 0.0) for e in perf_data]
    if sum(1 for s in llm_scores if s > 0) >= _MIN_SAMPLES * 0.5:
        features["llm"] = llm_scores

    # Add retention midpoint if enough clips have it
    def _retention_50pct(entry: dict) -> float:
        curve = entry.get("retention_curve")
        if curve and len(curve) > 1:
            return curve[len(curve) // 2]
        return 0.0

    retention_vals = [_retention_50pct(e) for e in perf_data]
    if sum(1 for v in retention_vals if v > 0) >= _MIN_SAMPLES * 0.5:
        features["retention"] = retention_vals

    # Compute correlations
    correlations = {}
    for name, values in features.items():
        correlations[name] = abs(_pearson(values, target))

    total_corr = sum(correlations.values())
    if total_corr == 0:
        console.print("[yellow]No meaningful correlations found.[/yellow]")
        return None

    # Normalize to sum to 100, with minimum floor
    raw_weights = {k: (v / total_corr) * 100 for k, v in correlations.items()}

    # Apply floor
    weights = {}
    for k, v in raw_weights.items():
        weights[k] = max(_MIN_WEIGHT, round(v))

    # Re-normalize to exactly 100
    total = sum(weights.values())
    if total != 100:
        biggest = max(weights, key=weights.get)
        weights[biggest] += 100 - total

    # Apply guardrails: velocity floor + llm cap
    weights = _apply_weight_guardrails(weights)

    rounded_corrs = {k: round(v, 3) for k, v in correlations.items()}
    save_weights(config, weights, rounded_corrs, len(perf_data))

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(perf_data),
        "weights": weights,
        "correlations": rounded_corrs,
    }

    console.print("[green]Learned weights saved to database[/green]")
    for k, v in weights.items():
        default = DEFAULT_WEIGHTS.get(k, "new")
        console.print(f"  {k}: {v} (default: {default}, r={correlations[k]:.3f})")

    return result


def get_learned_weights(config: dict) -> dict | None:
    """Load learned weights from database, or None if not enough data."""
    from clipper.db import get_weights

    data = get_weights(config)
    if not data:
        return None
    if data.get("sample_size", 0) < _MIN_SAMPLES:
        return None
    return data.get("weights")


def reset_weights(config: dict) -> None:
    """Delete learned weights from the database, reverting to hardcoded defaults."""
    from clipper.db import get_db

    conn = get_db(config)
    conn.execute("DELETE FROM scoring_weights WHERE id = 1")
    conn.commit()
    console.print("[yellow]Learned weights reset to defaults.[/yellow]")


def _apply_weight_guardrails(weights: dict) -> dict:
    """Enforce hard constraints on learned weights before saving.

    1. velocity >= 20 (never gut the viral signal)
    2. llm <= velocity (LLM was post-selection; can't lead velocity)
    """
    w = dict(weights)

    # Guardrail 1: velocity floor of 20
    velocity = float(w.get("velocity", 0))
    if velocity < 20.0:
        shortfall = 20.0 - velocity
        w["velocity"] = 20.0
        # Drain shortfall proportionally from other features
        others = {k: float(v) for k, v in w.items() if k != "velocity" and float(v) > 0}
        total_others = sum(others.values())
        if total_others > 0:
            for k in others:
                drain = shortfall * (others[k] / total_others)
                w[k] = max(0.0, float(w[k]) - drain)

    # Guardrail 2: llm <= velocity
    if "llm" in w:
        velocity = float(w.get("velocity", 0))
        llm = float(w.get("llm", 0))
        if llm > velocity:
            excess = llm - velocity
            w["llm"] = velocity
            w["velocity"] = velocity + excess

    # Re-normalize to sum to 100
    total = sum(float(v) for v in w.values())
    if total > 0 and abs(total - 100.0) > 0.5:
        factor = 100.0 / total
        w = {k: round(float(v) * factor) for k, v in w.items()}
        # Fix rounding error
        total2 = sum(w.values())
        if total2 != 100:
            biggest = max(w, key=lambda k: w[k])
            w[biggest] += 100 - total2

    return {k: int(round(v)) for k, v in w.items()}


_GAME_STATS_MIN_UPLOADS = 3


def _compute_multiplier(avg_views: float) -> float:
    """Compute game multiplier from average YouTube views."""
    if avg_views >= 500:
        return 2.0
    if avg_views >= 200:
        return 1.6
    if avg_views >= 100:
        return 1.3
    if avg_views >= 50:
        return 1.0
    return 0.6


def _infer_game_from_title(title: str) -> str:
    """Infer game name from a YouTube video title.

    Looks for known game names in the title text. Returns 'Unknown' if
    no game can be identified.
    """
    t = title.lower()
    # Ordered by specificity — check multi-word games first
    _GAME_PATTERNS = [
        ("arc raiders", "ARC Raiders"),
        ("league of legends", "League of Legends"),
        ("grand theft auto", "Grand Theft Auto V"),
        ("gta v", "Grand Theft Auto V"),
        ("gta", "Grand Theft Auto V"),
        ("world of warcraft", "World of Warcraft"),
        ("counter-strike", "Counter-Strike"),
        ("cs2", "Counter-Strike"),
        ("just chatting", "Just Chatting"),
        ("apex legends", "Apex Legends"),
        ("overwatch", "Overwatch 2"),
        ("valorant", "VALORANT"),
        ("deadlock", "Deadlock"),
        ("fortnite", "Fortnite"),
        ("minecraft", "Minecraft"),
        ("mewgenics", "Mewgenics"),
        ("slots", "Slots"),
        ("irl", "IRL"),
    ]
    for pattern, game_name in _GAME_PATTERNS:
        if pattern in t:
            return game_name
    return "Unknown"


def collect_game_stats(config: dict) -> dict:
    """Compute per-game YouTube performance metrics from ALL channel uploads.

    Queries the YouTube Data API directly for all channel videos, infers game
    from title, and computes upload count, avg views, success rate, and
    multiplier. Uses output/*.json for game metadata when available, falls back
    to title inference for clips uploaded outside the pipeline.

    Saves to queue/game_stats.json. Returns the stats dict.
    """
    from clipper.upload.auth import get_youtube_service
    from clipper.db import list_clips

    # Build a lookup from video_id → game using output clips in DB
    local_game_map: dict[str, str] = {}
    output_clips = list_clips(config, status="output", has_video_id=True, limit=5000)
    for clip in output_clips:
        video_id = clip.get("video_id")
        if video_id and video_id != "previously_uploaded":
            game = clip.get("game", "")
            if game:
                local_game_map[video_id] = game

    # Fetch ALL channel uploads from YouTube
    yt = get_youtube_service()
    channels = yt.channels().list(part="id", mine=True).execute()
    items = channels.get("items", [])
    if not items:
        console.print("[yellow]No YouTube channel found.[/yellow]")
        return {"updated_at": datetime.now(timezone.utc).isoformat(), "games": {}}

    channel_id = items[0]["id"]

    # Get all videos via search (more reliable than uploads playlist for Shorts)
    all_videos: list[dict] = []
    page_token = None
    while True:
        response = yt.search().list(
            part="snippet",
            channelId=channel_id,
            order="date",
            type="video",
            maxResults=50,
            pageToken=page_token,
        ).execute()
        for item in response.get("items", []):
            all_videos.append({
                "video_id": item["id"]["videoId"],
                "title": item["snippet"].get("title", ""),
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if not all_videos:
        console.print("[yellow]No videos found on channel.[/yellow]")
        return {"updated_at": datetime.now(timezone.utc).isoformat(), "games": {}}

    # Batch-fetch stats for all videos
    video_ids = [v["video_id"] for v in all_videos]
    stats_map: dict[str, int] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = yt.videos().list(
            part="statistics",
            id=",".join(batch),
        ).execute()
        for item in response.get("items", []):
            stats_map[item["id"]] = int(
                item.get("statistics", {}).get("viewCount", 0)
            )

    # Group by game: prefer local metadata, fall back to title inference
    game_views: dict[str, list[int]] = {}
    for v in all_videos:
        vid = v["video_id"]
        views = stats_map.get(vid, 0)

        # Resolve game: local metadata first, then infer from YouTube title
        game = local_game_map.get(vid) or _infer_game_from_title(v["title"])
        if game == "Unknown":
            continue  # skip unidentifiable videos

        if game not in game_views:
            game_views[game] = []
        game_views[game].append(views)

    # Compute per-game stats
    games: dict[str, dict] = {}
    for game, views_list in game_views.items():
        uploads = len(views_list)
        avg_views = sum(views_list) / uploads
        success_rate = sum(1 for v in views_list if v >= 100) / uploads

        if uploads >= _GAME_STATS_MIN_UPLOADS:
            multiplier = _compute_multiplier(avg_views)
        else:
            multiplier = 1.0  # neutral for untested games

        games[game] = {
            "uploads": uploads,
            "avg_views": round(avg_views, 1),
            "success_rate": round(success_rate, 3),
            "multiplier": multiplier,
        }

    from clipper.db import save_game_stats

    save_game_stats(config, games)

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "games": games,
    }

    console.print("[green]Game stats saved to database[/green]")
    for game, g in sorted(games.items(), key=lambda x: x[1]["avg_views"], reverse=True):
        console.print(
            f"  {game}: {g['uploads']} uploads, {g['avg_views']:.0f} avg views, "
            f"{g['success_rate']:.0%} success, {g['multiplier']}x"
        )

    return result


def get_game_multiplier(game: str, config: dict) -> float:
    """Get the game scoring multiplier from the database.

    Returns 1.0 (neutral) if no stats available or game has <3 uploads.
    """
    from clipper.db import get_game_multiplier_db
    return get_game_multiplier_db(config, game)
