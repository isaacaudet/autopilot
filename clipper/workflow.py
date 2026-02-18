"""Unified workflows — shorts and compilation flows with review + scheduling.

Core orchestration functions live here (not in cli.py) so both the CLI
and the web API can import them without circular dependencies.
"""

import json
import logging
import math
import re
import threading
from pathlib import Path

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
#  Core pipeline helpers (moved from cli.py)
# ---------------------------------------------------------------------------


def _process_single_clip(
    clip: dict, config: dict, verbose: bool, for_compilation: bool,
    counter: dict, lock: threading.Lock, total: int,
    state=None,
) -> dict | None:
    """Process a single approved clip. Returns clip dict on success, None on failure.

    Args:
        clip: Clip dict loaded from the database.
        state: Optional PipelineState for dashboard updates.
    """
    from clipper.process.download import download_clip
    from clipper.process.subtitles import transcribe
    from clipper.process.format import format_for_shorts
    from clipper.process.burn import burn_subtitles
    from clipper.process.trim import get_video_duration, trim_dead_air, trim_manual_window
    from clipper.process.titles import generate_hook_text
    from clipper.db import update_clip

    output_dir = config["_output_dir"]

    clip_name = clip.get("id", "unknown")
    clip_title = clip.get("title", clip_name)[:40]

    with lock:
        counter["n"] += 1
        idx = counter["n"]
    worker_label = f"W{idx}"
    console.print(f"\n[bold][{idx}/{total}] Processing:[/bold] {clip.get('title', clip_name)}")

    if state:
        state.start_worker(worker_label, clip_title, "downloading")

    try:
        video_path = download_clip(clip["url"], output_dir, verbose=verbose)
        if not video_path:
            console.print(f"[red]  [{idx}/{total}] Download failed — skipping[/red]")
            if state:
                state.fail_clip(worker_label, f"{clip.get('streamer', '?')}: download failed")
            update_clip(config, clip_name, status="skipped")
            return None

        force_shorts = clip.get("force_shorts", False)
        shorts_threshold = config["settings"]["shorts_threshold"]

        trim_start = max(0.0, float(clip.get("_trim_start", 0.0) or 0.0))
        trim_end = max(0.0, float(clip.get("_trim_end", 0.0) or 0.0))
        if trim_start > 0.0 or trim_end > 0.0:
            if state:
                state.update_worker(worker_label, "trimming")
            video_path = trim_manual_window(
                video_path,
                trim_start=trim_start,
                trim_end=trim_end,
                verbose=verbose,
            )

        _TALKING = {"just chatting", "irl", "talk shows & podcasts", "asmr"}
        if clip.get("game", "").lower() in _TALKING:
            video_path = trim_dead_air(video_path, verbose=verbose)

        try:
            effective_duration = get_video_duration(video_path)
        except Exception:
            effective_duration = float(clip.get("duration", 0) or 0.0)
        if effective_duration > 0:
            clip["duration"] = round(effective_duration, 2)

        layout = str(clip.get("_shorts_layout") or "").strip().lower()
        layout_forces_shorts = layout in {"fill", "blur"}
        is_shorts = not for_compilation and (
            layout_forces_shorts or force_shorts or (effective_duration > 0 and effective_duration <= shorts_threshold)
        )

        # Smart trim for Shorts clips >40s — trim around peak moment
        if is_shorts and effective_duration > 40:
            from clipper.process.trim import find_peak_moment, smart_trim
            if state:
                state.update_worker(worker_label, "trimming")
            peak = find_peak_moment(video_path, analysis=None, verbose=verbose)
            if peak is not None:
                video_path = smart_trim(video_path, peak, target_duration=30.0, verbose=verbose)

        if state:
            state.update_worker(worker_label, "transcribing")

        # Each thread needs its own config copy for _current_is_shorts
        thread_config = dict(config)
        thread_config["_current_is_shorts"] = is_shorts
        subtitle_path, transcript_words, censor_ranges = transcribe(video_path, thread_config, clip=clip, verbose=verbose)
        if censor_ranges:
            clip["censor_ranges"] = censor_ranges

        # LLM analysis — gracefully degrades if no API key
        if transcript_words:
            from clipper.process.analyze import analyze_clip
            transcript_text = " ".join(w.get("word", "") for w in transcript_words)
            analysis = analyze_clip(clip, transcript_text, verbose=verbose, video_path=str(video_path))
            if analysis:
                clip["_analysis"] = analysis

        # Ensure title_variants exist — fall back to keyword-based generation
        from clipper.process.titles import generate_title
        if not clip.get("_analysis", {}).get("title_variants"):
            clip.setdefault("_analysis", {})["title_variants"] = [generate_title(clip)]

        if is_shorts:
            if state:
                state.update_worker(worker_label, "formatting")
            clip["_source_path"] = str(video_path)

            layout = str(clip.get("_shorts_layout") or thread_config.get("shorts", {}).get("layout") or "").strip()
            if layout:
                thread_config["_shorts_layout"] = layout
            video_path = format_for_shorts(video_path, thread_config, clip=clip, verbose=verbose)

        hook_text = generate_hook_text(clip) if (is_shorts or for_compilation) else None

        if state:
            state.update_worker(worker_label, "burning")

        # Build readable output name: {streamer}_{game_slug}_{short_id}_final.mp4
        streamer_slug = re.sub(r"[^a-z0-9]", "", clip.get("streamer", "unknown").lower())
        game_slug = re.sub(r"[^a-z0-9]", "", clip.get("game", "").lower())[:12]
        short_id = clip.get("id", clip_name)[:8]
        output_name = f"{streamer_slug}_{game_slug}_{short_id}" if game_slug else f"{streamer_slug}_{short_id}"

        if subtitle_path:
            clip["_subtitle_path"] = str(subtitle_path)
            if for_compilation:
                final_path = video_path
            else:
                final_path = burn_subtitles(
                    video_path, subtitle_path, thread_config,
                    clip=clip,
                    is_shorts=is_shorts, hook_text=hook_text, verbose=verbose,
                    output_name=output_name,
                    censor_ranges=clip.get("censor_ranges"),
                )
        else:
            final_path = video_path

        clip["processed_path"] = str(final_path)
        clip["is_shorts"] = is_shorts

        # Save output metadata to both DB and JSON (JSON still needed for upload/publish)
        meta_path = output_dir / f"{clip_name}.json"
        with open(meta_path, "w") as f:
            json.dump(clip, f, indent=2)

        update_clip(config, clip_name, status="output",
                    processed_path=str(final_path), is_shorts=is_shorts,
                    _subtitle_path=clip.get("_subtitle_path"),
                    _source_path=clip.get("_source_path"),
                    _analysis=clip.get("_analysis"))

        console.print(f"[green]  [{idx}/{total}] Done:[/green] {final_path}")

        if state:
            state.complete_clip(worker_label, Path(final_path).name, clip_id=clip_name)
        return clip

    except Exception as e:
        console.print(f"[red]  [{idx}/{total}] Error: {e} — skipping[/red]")
        if state:
            state.fail_clip(worker_label, f"{clip.get('streamer', '?')}: {e}")
        update_clip(config, clip_name, status="skipped")
        return None


def _process_clips(
    config: dict, verbose: bool = False, for_compilation: bool = False,
    state=None,
) -> list[dict]:
    """Process all approved clips concurrently. Returns list of processed clip metadata dicts."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    from clipper.db import list_clips

    approved = list_clips(config, status="approved")
    if not approved:
        console.print("[yellow]No approved clips to process.[/yellow]")
        return []

    total = len(approved)
    counter = {"n": 0}
    lock = threading.Lock()

    if state:
        state.total_clips = total
        state.started_at = time.time()

    processed = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                _process_single_clip, clip, config, verbose, for_compilation,
                counter, lock, total, state,
            ): clip["id"]
            for clip in approved
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                processed.append(result)

    return processed


def _load_and_score_pending(
    config: dict,
    game: str = "",
    use_game_multipliers: bool = False,
    persist: bool = True,
) -> list[dict]:
    """Load pending clips from DB, filter by game + English, score, and sort by score desc.

    Uses learned weights from the database if available,
    otherwise falls back to hardcoded defaults.
    """
    from clipper.process.score import rank_clips_v2
    from clipper.process.titles import is_english_clip
    from clipper.learn import get_learned_weights, get_game_multiplier
    from clipper.db import list_clips, update_clip

    weights = get_learned_weights(config)

    pending = list_clips(config, status="pending", game=game if game else None, limit=2000)
    clips: list[dict] = []
    for clip in pending:
        clip_id = clip.get("id")
        if not is_english_clip(clip):
            if persist and clip_id:
                update_clip(config, clip_id, status="skipped", score=0)
            continue

        mult = 1.0
        if use_game_multipliers:
            mult = get_game_multiplier(clip.get("game", ""), config)
            clip["_game_multiplier"] = mult

        clips.append(clip)

    source_priors = _build_source_priors(config, clips)
    ranked = rank_clips_v2(
        clips,
        weights=weights,
        source_priors=source_priors,
        diversify=True,
    )

    for clip in ranked:
        clip_id = clip.get("id")
        clip["_score"] = round(float(clip.get("_score", 0.0) or 0.0), 2)
        if persist and clip_id:
            update_clip(config, clip_id, score=clip["_score"])

    return ranked


def _build_source_priors(config: dict, clips: list[dict]) -> dict[str, float]:
    """Build streamer/game priors from historical output performance (win-rate proxy)."""
    from clipper.db import list_performance

    if not clips:
        return {}

    perf = list_performance(config)
    if not perf:
        return {}

    global_logs: list[float] = []
    by_game: dict[str, list[float]] = {}
    by_streamer_game: dict[str, list[float]] = {}

    for row in perf:
        youtube = row.get("youtube", {}) or {}
        views = float(youtube.get("views", 0) or 0)
        if views <= 0:
            continue

        lv = math.log1p(views)
        global_logs.append(lv)

        features = row.get("features", {}) or {}
        game = str(features.get("game", "") or "").strip().lower()
        streamer = str(features.get("streamer", "") or "").strip().lower()

        if game:
            by_game.setdefault(game, []).append(lv)
        if streamer and game:
            by_streamer_game.setdefault(f"{streamer}|{game}", []).append(lv)

    if not global_logs:
        return {}

    global_mean = sum(global_logs) / len(global_logs)
    priors: dict[str, float] = {}

    def _prior_from_logs(values: list[float]) -> float:
        if not values:
            return 0.0
        avg = sum(values) / len(values)
        delta = avg - global_mean
        # Translate log-view delta into score points with sample-size shrinkage.
        shrink = len(values) / (len(values) + 5.0)
        return max(-6.0, min(8.0, (delta * 6.0) * shrink))

    for game, logs in by_game.items():
        priors[f"game::{game}"] = round(_prior_from_logs(logs), 3)
    for key, logs in by_streamer_game.items():
        priors[key] = round(_prior_from_logs(logs), 3)

    return priors


def _select_review_candidates(
    clips: list[dict],
    config: dict,
    *,
    min_score: float | None = None,
    min_keep: int | None = None,
    max_keep: int | None = None,
) -> list[dict]:
    """Select clips for manual review with a quality floor + backfill safety."""
    if not clips:
        return []

    settings = config.get("settings", {}) or {}
    floor = float(
        min_score
        if min_score is not None
        else settings.get("review_min_score", settings.get("shorts_min_score", 42))
    )
    min_count = int(min_keep if min_keep is not None else settings.get("review_min_keep", 25))
    max_count = int(max_keep if max_keep is not None else settings.get("review_max_keep", 120))

    ranked = sorted(clips, key=lambda c: float(c.get("_score", 0.0) or 0.0), reverse=True)
    selected = [c for c in ranked if float(c.get("_score", 0.0) or 0.0) >= floor]
    if len(selected) < min_count:
        selected = ranked[: max(1, min(min_count, len(ranked)))]
    if max_count > 0:
        selected = selected[:max_count]
    return selected


def _approve_clips(
    clips: list[dict],
    count: int,
    config: dict,
    channel: str | None = None,
    min_score: float | None = None,
) -> int:
    """Mark top `count` scored clips as approved, rest as skipped. Returns approved count."""
    from clipper.db import update_clip, get_db

    conn = get_db(config)

    # Reset any previously approved clips back to skipped
    conn.execute("UPDATE clips SET status = 'skipped' WHERE status = 'approved'")
    conn.commit()

    approved = 0
    for clip in clips:
        clip_id = clip.get("id")
        if not clip_id:
            continue
        clip_score = float(clip.get("_score", 0.0) or 0.0)
        if min_score is not None and clip_score < float(min_score):
            update_clip(config, clip_id, status="skipped", score=clip_score)
            continue
        if approved < count:
            updates = {"status": "approved", "score": clip_score}
            if channel:
                updates["channel"] = channel
            update_clip(config, clip_id, **updates)
            console.print(
                f"[green]  +[/green] {clip.get('streamer', '?')} — "
                f"{clip.get('title', '?')[:40]} (score {clip_score:.0f})"
            )
            approved += 1
        else:
            update_clip(config, clip_id, status="skipped", score=clip_score)

    # Skip any remaining pending (non-English clips not in the scored list)
    conn.execute(
        "UPDATE clips SET status = 'skipped' WHERE status = 'pending'"
    )
    conn.commit()

    return approved


def _fetch_clips(
    config: dict,
    game: str,
    *,
    period: str = "24h",
    scope: str = "gamewide",
    streamers: list[str] | None = None,
    clips_per_source: int | None = None,
    verbose: bool = False,
) -> list[dict]:
    """Fetch clips for scoring, with configurable scope/window."""
    import copy
    from clipper.fetch.twitch import fetch_twitch_clips

    fetch_config = copy.deepcopy(config)
    scope_key = str(scope or "gamewide").strip().lower()
    if scope_key not in {"gamewide", "configured", "selected"}:
        scope_key = "gamewide"

    period_str = str(period or "24h").strip().lower()
    fetch_config["targets"]["twitch"]["period"] = period_str

    if clips_per_source is None:
        cps = 500 if scope_key == "gamewide" else 160
    else:
        cps = max(20, min(int(clips_per_source), 800))
    fetch_config["targets"]["twitch"]["clips_per_source"] = cps

    if scope_key == "gamewide":
        fetch_config["targets"]["twitch"]["games"] = [game]
        fetch_config["targets"]["twitch"]["streamers"] = []
    elif scope_key == "configured":
        fetch_config["targets"]["twitch"]["games"] = []
        fetch_config["targets"]["twitch"]["streamers"] = list(config.get("targets", {}).get("twitch", {}).get("streamers", []) or [])
    else:
        fetch_config["targets"]["twitch"]["games"] = []
        fetch_config["targets"]["twitch"]["streamers"] = [str(s).strip() for s in (streamers or []) if str(s).strip()]
        if not fetch_config["targets"]["twitch"]["streamers"]:
            raise ValueError("Selected-streamer scope requires at least one streamer")

    # Fetch with a wider net than the final quality floor.
    # `min_views` is a quality floor for approval; discovery should stay broader.
    settings_cfg = config.get("settings", {}) or {}
    cfg_min_views = int(settings_cfg.get("min_views", 0) or 0)
    raw_fetch_min = settings_cfg.get("fetch_min_views")
    if raw_fetch_min is None:
        # Adaptive default: if global floor is high (e.g. 100+), still pull a broader
        # review candidate set and let scoring/ranking decide.
        if cfg_min_views <= 0:
            fetch_min_views = 20
        else:
            fetch_min_views = max(10, min(40, cfg_min_views // 4))
    else:
        fetch_min_views = max(0, int(raw_fetch_min))
    fetch_config["settings"]["min_views"] = fetch_min_views

    if scope_key == "gamewide":
        scope_text = f"gamewide ({game})"
    elif scope_key == "configured":
        scope_text = "configured streamers"
    else:
        scope_text = f"{len(fetch_config['targets']['twitch']['streamers'])} selected streamer(s)"

    console.print(
        f"\n[bold]Fetching clips ({scope_text}, last {period_str}, min {fetch_min_views} views)...[/bold]"
    )
    return fetch_twitch_clips(fetch_config, verbose=verbose)


def count_output_shorts_today(config: dict, *, channel: str | None = None) -> int:
    """Return how many Shorts outputs were produced today (local day boundary)."""
    from clipper.db import get_db

    conn = get_db(config)
    conditions = [
        "status = 'output'",
        "id NOT LIKE 'compilation_%'",
        "is_shorts = 1",
        "datetime(COALESCE(updated_at, fetched_at, created_at)) >= datetime('now', 'localtime', 'start of day')",
    ]
    params: list[str] = []
    if channel:
        conditions.append("channel = ?")
        params.append(channel)

    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM clips WHERE {' AND '.join(conditions)}",
        params,
    ).fetchone()
    return int((row["n"] if row else 0) or 0)


def _normalize_privacy(value: str | None) -> str:
    v = str(value or "").strip().lower()
    return v if v in {"unlisted", "private", "public"} else "unlisted"


_PROFILE_TUNING_KEYS = (
    "safe_top_ratio",
    "safe_bottom_ratio",
    "facecam_band_ratio",
    "facecam_x_bias",
    "facecam_y_bias",
    "facecam_zoom",
    "gameplay_zoom",
    "gameplay_zoom_no_facecam",
    "gameplay_x_bias",
    "gameplay_y_bias",
    "hud_height_ratio",
    "hud_scale",
    "hud_x_ratio",
    "hud_y_ratio",
    "title_y_ratio",
    "subtitle_margin_ratio",
)


def _profile_calibration_score(profile: dict | None) -> float:
    """Return 0..1 readiness score for a streamer layout profile."""
    if not isinstance(profile, dict) or not profile:
        return 0.0

    facecam_enabled = bool(profile.get("facecam_enabled", True))
    hud_enabled = bool(profile.get("hud_enabled", True))
    facecam_ready = (not facecam_enabled) or isinstance(profile.get("facecam"), dict)
    hud_ready = (not hud_enabled) or isinstance(profile.get("hud"), dict)

    box_score = 0.0
    if facecam_ready:
        box_score += 0.5
    if hud_ready:
        box_score += 0.5

    tuning_count = sum(1 for k in _PROFILE_TUNING_KEYS if profile.get(k) is not None)
    tuning_score = min(1.0, tuning_count / 6.0)
    return max(0.0, min(1.0, (0.7 * box_score) + (0.3 * tuning_score)))


def _prioritize_calibrated_streamers(clips: list[dict], config: dict) -> list[dict]:
    """Prefer clips from streamers with saved layout profiles in autopilot mode."""
    autopilot_cfg = config.get("autopilot", {}) or {}
    if autopilot_cfg.get("prefer_calibrated_streamers", True) is False:
        return clips

    from clipper.layout_profiles import load_facecam_profiles

    profiles = load_facecam_profiles(config) or {}
    bonus_points = float(autopilot_cfg.get("profile_bonus_points", 8.0) or 8.0)

    prioritized: list[dict] = []
    for clip in clips:
        streamer_key = str(clip.get("streamer", "")).strip().lower()
        profile = profiles.get(streamer_key) if streamer_key else None
        readiness = _profile_calibration_score(profile)
        rank_score = float(clip.get("_score", 0.0) or 0.0) + (readiness * bonus_points)
        clip["_autopilot_profile_score"] = round(readiness, 3)
        clip["_autopilot_rank_score"] = round(rank_score, 3)
        prioritized.append(clip)

    prioritized.sort(
        key=lambda c: (
            float(c.get("_autopilot_rank_score", 0.0) or 0.0),
            float(c.get("_score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return prioritized


def _upload_processed_clips(
    processed: list[dict],
    config: dict,
    *,
    channel: str | None = None,
    privacy: str = "unlisted",
    state=None,
    verbose: bool = False,
) -> dict:
    """Upload processed clips and persist platform IDs to JSON + DB."""
    from clipper.db import update_clip as db_update_clip
    from clipper.upload.dispatcher import (
        get_channel_platform,
        platform_id_column,
        upload_clip,
    )

    privacy_key = _normalize_privacy(privacy)
    out_dir = config["_output_dir"]
    uploaded = 0
    failed = 0
    uploaded_ids: list[str] = []

    if state is not None:
        state.uploads_total = len(processed)
        state.uploads_done = 0
        state.set_phase("uploading", f"Uploading {len(processed)} clips ({privacy_key})")

    for clip in processed:
        clip_id = str(clip.get("id", "")).strip()
        if not clip_id:
            failed += 1
            continue

        upload_id = upload_clip(clip, config, privacy=privacy_key, channel=channel, verbose=verbose)
        if not upload_id:
            failed += 1
            if state is not None:
                state.uploads_done += 1
            continue

        platform = get_channel_platform(channel, config)
        id_col = platform_id_column(platform)
        clip[id_col] = upload_id
        if channel:
            clip["channel"] = channel
            clip["_target_channel"] = channel

        # Keep output JSON in sync for studio/upload views.
        meta_path = out_dir / f"{clip_id}.json"
        if meta_path.exists():
            try:
                current = json.loads(meta_path.read_text())
            except Exception:
                current = {}
        else:
            current = {}
        current.update(clip)
        meta_path.write_text(json.dumps(current, indent=2))

        db_update_clip(config, clip_id, **{id_col: upload_id, "channel": channel or ""})
        uploaded += 1
        uploaded_ids.append(upload_id)
        if state is not None:
            state.uploads_done += 1

    return {
        "uploaded": uploaded,
        "failed_uploads": failed,
        "uploaded_ids": uploaded_ids,
        "privacy": privacy_key,
    }


def run_autopilot_workflow(
    config: dict,
    *,
    count: int = 8,
    min_score: float = 45.0,
    channel: str | None = None,
    game: str | None = None,
    period: str = "24h",
    scope: str = "configured",
    streamers: list[str] | None = None,
    auto_upload: bool = False,
    privacy: str = "unlisted",
    daily_limit: int | None = None,
    verbose: bool = False,
    state=None,
) -> dict:
    """Autopilot flow: learn -> fetch -> score -> approve -> process -> optional upload."""
    from clipper.learn import collect_game_stats, collect_performance, train_weights

    requested_count = max(1, int(count))
    target_count = requested_count
    daily_cap = int(daily_limit or 0)
    if daily_cap > 0:
        already = count_output_shorts_today(config, channel=channel)
        remaining = max(0, daily_cap - already)
        if remaining <= 0:
            detail = f"Daily cap reached ({daily_cap}); already produced {already} short(s) today."
            if state is not None:
                state.set_phase("done", detail)
            return {
                "requested": requested_count,
                "daily_limit": daily_cap,
                "already_today": already,
                "approved": 0,
                "processed": 0,
                "uploaded": 0,
                "status": "daily_cap_reached",
            }
        target_count = min(target_count, remaining)

    if state is not None:
        state.set_phase("learning", "Refreshing game stats...")

    # Best-effort learning refresh.
    try:
        collect_game_stats(config)
    except Exception:
        pass

    if state is not None:
        state.set_phase("learning", "Training scoring weights...")
    try:
        collect_performance(config)
        train_weights(config)
    except Exception:
        pass

    scope_key = str(scope or "configured").strip().lower()
    if scope_key not in {"gamewide", "configured", "selected"}:
        scope_key = "configured"

    game_name = str(game or "").strip()
    if not game_name:
        configured_games = list(config.get("targets", {}).get("twitch", {}).get("games", []) or [])
        game_name = configured_games[0] if configured_games else "Deadlock"

    if state is not None:
        state.set_phase("fetching", "Discovering clips...")
    _fetch_clips(
        config,
        game_name,
        period=period,
        scope=scope_key,
        streamers=streamers,
        verbose=verbose,
    )

    if state is not None:
        state.set_phase("scoring", "Scoring clip candidates...")
    # Enforce game filtering whenever a game is specified, even for configured/selected scopes.
    game_filter = game_name if game_name else ""
    pending = _load_and_score_pending(config, game=game_filter, use_game_multipliers=True)
    if not pending:
        if state is not None:
            state.set_phase("done", "No qualifying clips found")
        return {"requested": requested_count, "approved": 0, "processed": 0, "uploaded": 0, "status": "no_clips"}

    qualifying = [c for c in pending if float(c.get("_score", 0.0) or 0.0) >= float(min_score)]
    if not qualifying:
        detail = f"No clips above score {float(min_score):.0f}"
        if state is not None:
            state.set_phase("done", detail)
        return {"requested": requested_count, "approved": 0, "processed": 0, "uploaded": 0, "status": "below_threshold"}

    qualifying = _prioritize_calibrated_streamers(qualifying, config)

    if state is not None:
        state.set_phase("approving", f"Approving top {target_count} clips...")
    approved = _approve_clips(
        qualifying,
        target_count,
        config,
        channel=channel,
        min_score=min_score,
    )
    if approved <= 0:
        if state is not None:
            state.set_phase("done", "No clips approved")
        return {"requested": requested_count, "approved": 0, "processed": 0, "uploaded": 0, "status": "none_approved"}

    if state is not None:
        state.set_phase("processing", f"Processing {approved} clips...")
    processed = _process_clips(config, verbose=verbose, state=state)

    summary = {
        "requested": requested_count,
        "approved": approved,
        "processed": len(processed),
        "uploaded": 0,
        "status": "processed",
    }

    if auto_upload and processed:
        upload_summary = _upload_processed_clips(
            processed,
            config,
            channel=channel,
            privacy=privacy,
            state=state,
            verbose=verbose,
        )
        summary["uploaded"] = int(upload_summary.get("uploaded", 0) or 0)
        summary["failed_uploads"] = int(upload_summary.get("failed_uploads", 0) or 0)
        summary["privacy"] = upload_summary.get("privacy", "unlisted")
        summary["status"] = "uploaded"

    if state is not None and state.phase != "error":
        detail = (
            f"{summary['processed']} processed"
            + (f", {summary['uploaded']} uploaded" if summary["uploaded"] else "")
        )
        state.set_phase("done", detail)

    return summary


# ---------------------------------------------------------------------------
#  High-level workflow runners
# ---------------------------------------------------------------------------


def run_shorts_workflow(
    config: dict,
    game: str | None = None,
    count: int = 5,
    channel: str | None = None,
    verbose: bool = False,
    state=None,
):
    """Shorts flow: fetch -> rank -> approve -> process."""
    if not game:
        raise ValueError("game is required")

    # 1. Fetch
    if state:
        state.set_phase("fetching", f"Fetching {game} clips from Twitch")
    raw_clips = _fetch_clips(config, game, verbose=verbose)
    if not raw_clips:
        console.print("[yellow]No clips found.[/yellow]")
        if state:
            state.set_phase("done", "No clips found")
        return

    # 2. Score & rank
    if state:
        state.set_phase("scoring", f"Scoring and ranking clips")
    pending = _load_and_score_pending(config, game=game)
    if not pending:
        console.print("[yellow]No English clips found.[/yellow]")
        if state:
            state.set_phase("done", "No English clips found")
        return

    console.print(f"  {len(pending)} English clips scored and ranked")

    # 3. Approve
    clip_count = min(count, len(pending))
    shorts_min_score = float(
        (config.get("settings", {}) or {}).get(
            "shorts_min_score",
            (config.get("settings", {}) or {}).get("review_min_score", 40),
        )
    )
    if state:
        state.set_phase("approving", f"Approving top {clip_count} clips")
    console.print(f"\n[bold]Approving top {clip_count} clips (min score {shorts_min_score:.0f})...[/bold]")
    approved_count = _approve_clips(
        pending,
        clip_count,
        config,
        channel=channel,
        min_score=shorts_min_score,
    )

    if approved_count == 0:
        console.print("[yellow]No clips approved.[/yellow]")
        if state:
            state.set_phase("done", "No clips approved")
        return

    # 4. Process
    if state:
        state.set_phase("processing", f"Processing {approved_count} clips")
    console.print(f"\n[bold]Processing {approved_count} clips...[/bold]")
    processed = _process_clips(config, verbose=verbose, state=state)

    if not processed:
        console.print("[yellow]No clips processed successfully.[/yellow]")
        if state:
            state.set_phase("done", "No clips processed successfully")
        return

    console.print(f"\n[bold green]{len(processed)} clips ready![/bold green]")


def run_compilation_workflow(
    config: dict,
    game: str | None = None,
    duration: int | None = None,
    channel: str | None = None,
    verbose: bool = False,
    state=None,
):
    """Compilation flow: fetch -> rank -> approve -> process -> compile."""
    if not game:
        raise ValueError("game is required")

    # 1. Fetch
    if state:
        state.set_phase("fetching", f"Fetching {game} clips from Twitch")
    raw_clips = _fetch_clips(config, game, verbose=verbose)
    if not raw_clips:
        console.print("[yellow]No clips found.[/yellow]")
        if state:
            state.set_phase("done", "No clips found")
        return

    # 2. Score & rank
    if state:
        state.set_phase("scoring", f"Scoring and ranking clips")
    pending = _load_and_score_pending(config, game=game)
    if not pending:
        console.print("[yellow]No English clips found.[/yellow]")
        if state:
            state.set_phase("done", "No English clips found")
        return

    console.print(f"  {len(pending)} English clips scored and ranked")

    # 3. Pick clip count by target duration
    from clipper.process.tiers import clips_for_duration
    clip_count = clips_for_duration(pending, duration or 12)

    # 4. Approve
    compilation_min_score = float(
        (config.get("settings", {}) or {}).get("compilation_min_score", 30)
    )
    if state:
        state.set_phase("approving", f"Approving top {clip_count} clips")
    console.print(f"\n[bold]Approving top {clip_count} clips (min score {compilation_min_score:.0f})...[/bold]")
    approved_count = _approve_clips(
        pending,
        clip_count,
        config,
        channel=channel,
        min_score=compilation_min_score,
    )

    if approved_count < 2:
        console.print(f"[yellow]Only {approved_count} clip(s) — need at least 2 for compilation.[/yellow]")
        if state:
            state.set_phase("done", f"Only {approved_count} clip(s) — need at least 2")
        return

    # 5. Process (landscape mode for compilation)
    if state:
        state.set_phase("processing", f"Processing {approved_count} clips")
    console.print(f"\n[bold]Processing {approved_count} clips...[/bold]")
    processed = _process_clips(config, verbose=verbose, for_compilation=True, state=state)

    if not processed or len(processed) < 2:
        console.print("[yellow]Not enough clips processed for compilation.[/yellow]")
        if state:
            state.set_phase("done", "Not enough clips processed for compilation")
        return

    # 6. Score and order (countdown: best clip last)
    from clipper.process.score import enhanced_score

    for c in processed:
        c["_score"] = enhanced_score(c)
    processed.sort(key=lambda c: c["_score"], reverse=True)

    if len(processed) >= 3:
        best = processed.pop(0)
        hook = processed.pop(0)
        rest = list(reversed(processed))
        ordered = [hook] + rest + [best]
    else:
        ordered = processed

    # 7. Compile
    from clipper.process.compile import compile_clips, build_thumbnail, build_description, build_merged_subtitles
    from datetime import datetime

    date_str = datetime.now().strftime("%Y%m%d")
    comp_name = f"compilation_{game.lower().replace(' ', '_')}_{date_str}.mp4"
    comp_path = config["_output_dir"] / comp_name
    title = f"{game.upper()} Daily Highlights — Best Clips {datetime.now().strftime('%m/%d')}"

    if state:
        state.set_phase("compiling", f"Compiling {len(ordered)} clips")
    console.print(f"\n[bold]Compiling {len(ordered)} clips (countdown order)...[/bold]")
    compile_clips(ordered, comp_path, config, verbose=verbose, countdown=True)

    merged_subs = build_merged_subtitles(ordered, comp_path.with_suffix(".ass"))
    if merged_subs:
        console.print(f"  [green]Merged subtitles:[/green] {merged_subs}")

    thumb_path = comp_path.with_suffix(".jpg")
    thumb = build_thumbnail(ordered, thumb_path, title=title, game=game)
    if thumb:
        console.print(f"  [green]Thumbnail:[/green] {thumb}")

    description = build_description(ordered, game=game)

    comp_clip = {
        "id": comp_name.replace(".mp4", ""),
        "title": title,
        "_title_override": title,
        "_description_override": description,
        "streamer": "",
        "game": game,
        "platform": "twitch",
        "url": "",
        "processed_path": str(comp_path),
        "is_shorts": False,
    }
    if channel:
        comp_clip["_target_channel"] = channel

    comp_meta_path = comp_path.with_suffix(".json")
    with open(comp_meta_path, "w") as f:
        json.dump(comp_clip, f, indent=2)

    console.print(f"\n[bold green]Done![/bold green] Compilation ready: {comp_path}")
