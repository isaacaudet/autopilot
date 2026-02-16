"""Unified workflows — shorts and compilation flows with review + scheduling.

Core orchestration functions live here (not in cli.py) so both the CLI
and the web API can import them without circular dependencies.
"""

import json
import logging
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
    from clipper.process.trim import trim_dead_air
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

        duration = clip.get("duration", 0)
        force_shorts = clip.get("force_shorts", False)
        shorts_threshold = config["settings"]["shorts_threshold"]
        is_shorts = not for_compilation and (force_shorts or (duration > 0 and duration <= shorts_threshold))

        _TALKING = {"just chatting", "irl", "talk shows & podcasts", "asmr"}
        if clip.get("game", "").lower() in _TALKING:
            video_path = trim_dead_air(video_path, verbose=verbose)

        # Smart trim for Shorts clips >40s — trim around peak moment
        if is_shorts and clip.get("duration", 0) > 40:
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
        subtitle_path, transcript_words = transcribe(video_path, thread_config, clip=clip, verbose=verbose)

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
            if for_compilation:
                clip["_subtitle_path"] = str(subtitle_path)
                final_path = video_path
            else:
                final_path = burn_subtitles(
                    video_path, subtitle_path, thread_config,
                    clip=clip,
                    is_shorts=is_shorts, hook_text=hook_text, verbose=verbose,
                    output_name=output_name,
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


def _load_and_score_pending(config: dict, game: str = "", use_game_multipliers: bool = False) -> list[dict]:
    """Load pending clips from DB, filter by game + English, score, and sort by score desc.

    Uses learned weights from the database if available,
    otherwise falls back to hardcoded defaults.
    """
    from clipper.process.score import score_clip
    from clipper.process.titles import is_english_clip
    from clipper.learn import get_learned_weights, get_game_multiplier
    from clipper.db import list_clips, update_clip

    weights = get_learned_weights(config)

    pending = list_clips(config, status="pending", game=game if game else None, limit=2000)
    clips = []
    for clip in pending:
        if not is_english_clip(clip):
            continue

        mult = 1.0
        if use_game_multipliers:
            mult = get_game_multiplier(clip.get("game", ""), config)
            clip["_game_multiplier"] = mult

        clip["_score"] = score_clip(clip, weights=weights, game_multiplier=mult)
        clips.append(clip)

    clips.sort(key=lambda c: c["_score"], reverse=True)
    return clips


def _approve_clips(clips: list[dict], count: int, config: dict, channel: str | None = None) -> int:
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
        if approved < count:
            updates = {"status": "approved", "score": clip.get("_score", 0)}
            if channel:
                updates["channel"] = channel
            update_clip(config, clip_id, **updates)
            console.print(
                f"[green]  +[/green] {clip.get('streamer', '?')} — "
                f"{clip.get('title', '?')[:40]} (score {clip['_score']:.0f})"
            )
            approved += 1
        else:
            update_clip(config, clip_id, status="skipped")

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

    fetch_config["settings"]["min_views"] = 5

    if scope_key == "gamewide":
        scope_text = f"gamewide ({game})"
    elif scope_key == "configured":
        scope_text = "configured streamers"
    else:
        scope_text = f"{len(fetch_config['targets']['twitch']['streamers'])} selected streamer(s)"

    console.print(f"\n[bold]Fetching clips ({scope_text}, last {period_str})...[/bold]")
    return fetch_twitch_clips(fetch_config, verbose=verbose)


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
    if state:
        state.set_phase("approving", f"Approving top {clip_count} clips")
    console.print(f"\n[bold]Approving top {clip_count} clips...[/bold]")
    approved_count = _approve_clips(pending, clip_count, config, channel=channel)

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
    if state:
        state.set_phase("approving", f"Approving top {clip_count} clips")
    console.print(f"\n[bold]Approving top {clip_count} clips...[/bold]")
    approved_count = _approve_clips(pending, clip_count, config, channel=channel)

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
