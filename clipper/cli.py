"""Main CLI entry point for Clipper."""

import json
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from rich.console import Console

from clipper.config import load_config

console = Console()


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
@click.pass_context
def cli(ctx, verbose):
    """Clipper: Automated Twitch/Kick/YouTube → YouTube Shorts pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config"] = load_config()


# -- Fetch --


@cli.command()
@click.option(
    "--source",
    type=click.Choice(["twitch", "kick", "youtube", "all"]),
    default="all",
    help="Platform to fetch clips from.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be fetched without saving.")
@click.pass_context
def fetch(ctx, source, dry_run):
    """Fetch top clips from configured sources."""
    from clipper.fetch.twitch import fetch_twitch_clips
    from clipper.fetch.kick import fetch_kick_clips
    from clipper.fetch.youtube import fetch_youtube_clips

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]

    sources = [source] if source != "all" else ["twitch", "kick", "youtube"]

    all_clips = []
    for src in sources:
        if src == "twitch" and config["targets"].get("twitch"):
            clips = fetch_twitch_clips(config, dry_run=dry_run, verbose=verbose)
            all_clips.extend(clips)
        elif src == "kick" and config["targets"].get("kick"):
            clips = fetch_kick_clips(config, dry_run=dry_run, verbose=verbose)
            all_clips.extend(clips)
        elif src == "youtube" and config["targets"].get("youtube", {}).get("channels"):
            clips = fetch_youtube_clips(config, dry_run=dry_run, verbose=verbose)
            all_clips.extend(clips)

    console.print(f"\n[bold green]Fetched {len(all_clips)} clips total.[/bold green]")


# -- Review --


@cli.command()
@click.pass_context
def review(ctx):
    """Interactively review pending clips."""
    from clipper.queue.review import run_review

    config = ctx.obj["config"]
    run_review(config)


@cli.command()
@click.option("--top", "-n", default=5, help="Number of top clips to auto-approve.")
@click.option("--min-score", default=30, help="Minimum virality score to approve (0-100).")
@click.pass_context
def auto(ctx, top, min_score):
    """Auto-approve the top clips by virality score (skip manual review)."""
    from clipper.queue.review import auto_approve_top

    config = ctx.obj["config"]
    auto_approve_top(config, top_n=top, min_score=min_score)


@cli.command()
@click.option(
    "--source",
    type=click.Choice(["twitch", "kick", "youtube", "all"]),
    default="all",
    help="Platform to fetch clips from.",
)
@click.option("--top", "-n", default=5, help="Number of top clips to pick.")
@click.option("--min-score", default=30, help="Minimum virality score (0-100).")
@click.option("--discover", is_flag=True, help="Search all trending games (ignore config).")
@click.pass_context
def go(ctx, source, top, min_score, discover):
    """Full auto pipeline: fetch → pick best clips → download → subtitle → format."""
    from clipper.fetch.twitch import fetch_twitch_clips
    from clipper.fetch.kick import fetch_kick_clips
    from clipper.fetch.youtube import fetch_youtube_clips
    from clipper.queue.review import auto_approve_top

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]

    # 1. Fetch
    console.print("\n[bold cyan]Step 1/3: Fetching clips...[/bold cyan]")
    sources = [source] if source != "all" else ["twitch", "kick", "youtube"]
    all_clips = []
    for src in sources:
        if src == "twitch" and config["targets"].get("twitch"):
            all_clips.extend(fetch_twitch_clips(config, verbose=verbose, discover_mode=discover))
        elif src == "kick" and config["targets"].get("kick"):
            all_clips.extend(fetch_kick_clips(config, verbose=verbose))
        elif src == "youtube" and config["targets"].get("youtube", {}).get("channels"):
            all_clips.extend(fetch_youtube_clips(config, verbose=verbose))

    if not all_clips:
        console.print("[yellow]No clips found.[/yellow]")
        return

    # 2. Auto-approve top clips
    console.print(f"\n[bold cyan]Step 2/3: Picking top {top} clips by score...[/bold cyan]")
    approved = auto_approve_top(config, top_n=top, min_score=min_score)
    if not approved:
        console.print("[yellow]No clips met the score threshold.[/yellow]")
        return

    # 3. Process
    console.print(f"\n[bold cyan]Step 3/3: Processing {approved} clip(s)...[/bold cyan]")
    processed = _process_clips(config, verbose=verbose)
    console.print(f"\n[bold green]Pipeline complete:[/bold green] {len(processed)} succeeded")


# -- Process --


@cli.command()
@click.option("--clip-id", default=None, help="Process a specific clip by ID.")
@click.pass_context
def process(ctx, clip_id):
    """Download, transcribe, and format approved clips."""
    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]

    if clip_id:
        # Filter to specific clip — move others temporarily
        queue_dir = config["_queue_dir"] / "approved"
        clip_files = sorted(queue_dir.glob("*.json"))
        matching = [f for f in clip_files if clip_id in f.stem]
        if not matching:
            console.print(f"[yellow]No approved clip matching '{clip_id}'.[/yellow]")
            return

    processed = _process_clips(config, verbose=verbose)
    console.print(f"\n[bold]Results:[/bold] {len(processed)} succeeded")


# -- Upload --


@cli.command()
@click.option(
    "--privacy",
    type=click.Choice(["unlisted", "public", "private"]),
    default=None,
    help="Override default privacy setting.",
)
@click.option("--clip-id", default=None, help="Upload a specific clip by ID.")
@click.pass_context
def upload(ctx, privacy, clip_id):
    """Upload processed clips to YouTube."""
    from clipper.upload.youtube import upload_clip

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]
    output_dir = config["_output_dir"]
    privacy = privacy or config["upload"]["default_privacy"]

    meta_files = sorted(output_dir.glob("*.json"))
    if clip_id:
        meta_files = [f for f in meta_files if clip_id in f.stem]

    if not meta_files:
        console.print("[yellow]No processed clips to upload.[/yellow]")
        return

    for meta_file in meta_files:
        with open(meta_file) as f:
            clip = json.load(f)

        if "processed_path" not in clip:
            continue

        if clip.get("video_id"):
            console.print(f"[dim]Already uploaded:[/dim] {clip.get('title', meta_file.stem)[:50]} → https://youtube.com/watch?v={clip['video_id']}")
            continue

        console.print(f"\n[bold]Uploading:[/bold] {clip.get('title', meta_file.stem)}")
        video_id = upload_clip(clip, config, privacy=privacy, verbose=verbose)
        if video_id:
            clip["video_id"] = video_id
            with open(meta_file, "w") as f:
                json.dump(clip, f, indent=2)
            console.print(
                f"[green]Uploaded:[/green] https://youtube.com/watch?v={video_id}"
            )
        else:
            console.print("[red]Upload failed.[/red]")


# -- Snipe --


def _process_single_clip(
    clip_file: Path, config: dict, verbose: bool, for_compilation: bool,
    counter: dict, lock: threading.Lock, total: int,
    state=None,
) -> dict | None:
    """Process a single approved clip. Returns clip dict on success, None on failure.

    Args:
        state: Optional PipelineState for TUI dashboard updates.
    """
    from clipper.process.download import download_clip
    from clipper.process.subtitles import transcribe
    from clipper.process.format import format_for_shorts
    from clipper.process.burn import burn_subtitles
    from clipper.process.trim import trim_dead_air
    from clipper.process.titles import generate_hook_text

    output_dir = config["_output_dir"]

    with open(clip_file) as f:
        clip = json.load(f)

    clip_name = clip_file.stem
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
            return None

        if state:
            state.update_worker(worker_label, "transcribing")

        duration = clip.get("duration", 0)
        force_shorts = clip.get("force_shorts", False)
        shorts_threshold = config["settings"]["shorts_threshold"]
        is_shorts = not for_compilation and (force_shorts or (duration > 0 and duration <= shorts_threshold))

        _TALKING = {"just chatting", "irl", "talk shows & podcasts", "asmr"}
        if clip.get("game", "").lower() in _TALKING:
            video_path = trim_dead_air(video_path, verbose=verbose)

        # Each thread needs its own config copy for _current_is_shorts
        thread_config = dict(config)
        thread_config["_current_is_shorts"] = is_shorts
        subtitle_path = transcribe(video_path, thread_config, verbose=verbose)

        if is_shorts:
            if state:
                state.update_worker(worker_label, "formatting")
            video_path = format_for_shorts(video_path, thread_config, verbose=verbose)

        hook_text = generate_hook_text(clip) if (is_shorts or for_compilation) else None

        if state:
            state.update_worker(worker_label, "burning")

        # Build readable output name: {streamer}_{game_slug}_{short_id}_final.mp4
        streamer_slug = re.sub(r"[^a-z0-9]", "", clip.get("streamer", "unknown").lower())
        game_slug = re.sub(r"[^a-z0-9]", "", clip.get("game", "").lower())[:12]
        short_id = clip.get("id", clip_name)[:8]
        output_name = f"{streamer_slug}_{game_slug}_{short_id}" if game_slug else f"{streamer_slug}_{short_id}"

        if subtitle_path:
            final_path = burn_subtitles(
                video_path, subtitle_path, thread_config,
                is_shorts=is_shorts, hook_text=hook_text, verbose=verbose,
                output_name=output_name,
            )
        else:
            final_path = video_path

        clip["processed_path"] = str(final_path)
        clip["is_shorts"] = is_shorts
        meta_path = output_dir / f"{clip_name}.json"
        with open(meta_path, "w") as f:
            json.dump(clip, f, indent=2)

        clip_file.unlink(missing_ok=True)
        console.print(f"[green]  [{idx}/{total}] Done:[/green] {final_path}")

        if state:
            state.complete_clip(worker_label, Path(final_path).name)
        return clip

    except Exception as e:
        console.print(f"[red]  [{idx}/{total}] Error: {e} — skipping[/red]")
        if state:
            state.fail_clip(worker_label, f"{clip.get('streamer', '?')}: {e}")
        return None


def _process_clips(
    config: dict, verbose: bool = False, for_compilation: bool = False,
    state=None,
) -> list[dict]:
    """Process all approved clips concurrently. Returns list of processed clip metadata dicts.

    Args:
        for_compilation: If True, skip Shorts formatting (vertical, zoom, hook text,
            progress bar). Just download, transcribe, and burn subtitles in landscape.
            The compilation step handles scaling/overlays separately.
        state: Optional PipelineState for TUI dashboard updates.
    """
    queue_dir = config["_queue_dir"] / "approved"

    clip_files = sorted(queue_dir.glob("*.json"))
    if not clip_files:
        console.print("[yellow]No approved clips to process.[/yellow]")
        return []

    total = len(clip_files)
    counter = {"n": 0}
    lock = threading.Lock()

    if state:
        import time
        state.total_clips = total
        state.started_at = time.time()

    processed = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                _process_single_clip, cf, config, verbose, for_compilation,
                counter, lock, total, state,
            ): cf
            for cf in clip_files
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                processed.append(result)

    return processed


def _load_and_score_pending(config: dict, game: str = "") -> list[dict]:
    """Load pending clips, filter by game + English, score, and sort by score desc."""
    from clipper.process.score import score_clip
    from clipper.process.titles import is_english_clip

    pending_dir = config["_queue_dir"] / "pending"
    clips = []
    for path in sorted(pending_dir.glob("*.json")):
        try:
            with open(path) as f:
                clip = json.load(f)
            if game and game.lower() not in clip.get("game", "").lower():
                continue
            if not is_english_clip(clip):
                continue
            clip["_path"] = str(path)
            clip["_score"] = score_clip(clip)
            clips.append(clip)
        except (json.JSONDecodeError, OSError):
            continue
    clips.sort(key=lambda c: c["_score"], reverse=True)
    return clips


def _duration_picker(clips: list[dict], game: str) -> int:
    """Show compilation length options with score tradeoffs. Returns clip count."""
    import questionary
    from rich.table import Table

    targets = [
        (8, "8 min (mid-roll ads unlock)"),
        (10, "10 min"),
        (12, "12 min"),
        (15, "15 min"),
    ]

    tiers = []
    for target_min, label in targets:
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
            total_score += clip["_score"]

        if count >= 2:
            avg_score = total_score / count
            tiers.append((count, avg_score, cumulative / 60, label))

    if not tiers:
        console.print("[yellow]Not enough clips for any compilation tier.[/yellow]")
        return 0

    # Display tiers table
    table = Table(title=f"{game} Compilation — {len(clips)} clips available")
    table.add_column("Target", style="cyan")
    table.add_column("Clips", justify="right")
    table.add_column("Actual Length", justify="right")
    table.add_column("Avg Score", justify="right")
    table.add_column("Quality")

    for count, avg, actual, label in tiers:
        if avg >= 35:
            quality = "[green]Excellent[/green]"
        elif avg >= 25:
            quality = "[yellow]Good[/yellow]"
        else:
            quality = "[red]Decent[/red]"
        table.add_row(label, str(count), f"{actual:.1f} min", f"{avg:.0f}", quality)

    console.print(table)

    # Preview top clips
    console.print(f"\n[bold]Top 5 clips:[/bold]")
    for i, clip in enumerate(clips[:5], 1):
        console.print(
            f"  {i}. [cyan]{clip.get('streamer', '?')}[/cyan] — "
            f"{clip.get('title', '?')[:40]} "
            f"(score {clip['_score']:.0f}, {clip.get('view_count', 0):,} views)"
        )

    # Let user pick
    choices = [f"{label} — {count} clips, avg score {avg:.0f}" for count, avg, _, label in tiers]
    choices.append("Custom count")

    answer = questionary.select("\nPick compilation length:", choices=choices).ask()
    if answer is None:
        return 0

    if "Custom" in answer:
        custom = questionary.text(f"How many clips? (2-{len(clips)}):").ask()
        if custom is None:
            return 0
        try:
            return max(2, min(int(custom), len(clips)))
        except ValueError:
            return 0

    idx = choices.index(answer)
    return tiers[idx][0]


def _approve_clips(clips: list[dict], count: int, config: dict) -> int:
    """Move top `count` scored clips to approved, rest to skipped. Returns approved count."""
    queue_dir = config["_queue_dir"]
    approved_dir = queue_dir / "approved"
    skipped_dir = queue_dir / "skipped"
    approved_dir.mkdir(parents=True, exist_ok=True)
    skipped_dir.mkdir(parents=True, exist_ok=True)

    approved = 0
    for clip in clips:
        path = Path(clip["_path"])
        if not path.exists():
            continue
        if approved < count:
            shutil.move(str(path), str(approved_dir / path.name))
            console.print(
                f"[green]  +[/green] {clip.get('streamer', '?')} — "
                f"{clip.get('title', '?')[:40]} (score {clip['_score']:.0f})"
            )
            approved += 1
        else:
            shutil.move(str(path), str(skipped_dir / path.name))

    # Clean up any remaining pending (non-English clips skipped during load)
    pending_dir = queue_dir / "pending"
    for path in pending_dir.glob("*.json"):
        shutil.move(str(path), str(skipped_dir / path.name))

    return approved


@cli.command()
@click.option("--game", "-g", default="Deadlock", help="Game to snipe clips for.")
@click.option("--window", "-w", default="24h", help="Time window (e.g. 3h, 12h, 24h).")
@click.option("--upload", "do_upload", is_flag=True, help="Upload after processing.")
@click.option("--compile", "do_compile", is_flag=True, help="Compile into long-form video.")
@click.option("--count", "-n", default=None, type=int, help="Override clip count (skip picker).")
@click.pass_context
def snipe(ctx, game, window, do_upload, do_compile, count):
    """Daily clip sniping: fetch → pick → process → compile → upload.

    Fetches ALL clips for a game across every streamer, ranks by view
    velocity (views/hour). With --compile, shows an interactive duration
    picker so you can choose compilation length vs quality tradeoff.

    Examples:
        clipper snipe                        # fetch + process top 10
        clipper snipe --compile              # interactive duration picker + compile
        clipper snipe --compile --upload     # ...and upload to YouTube
        clipper snipe -g Valorant --compile  # Valorant compilation
        clipper snipe -n 30 --compile        # force 30 clips, skip picker
    """
    from clipper.fetch.twitch import fetch_twitch_clips

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]

    # Override config for game-based fetching (all streamers)
    twitch_cfg = config["targets"]["twitch"]
    saved = {
        "period": twitch_cfg.get("period"),
        "clips_per_source": twitch_cfg.get("clips_per_source"),
        "games": twitch_cfg.get("games"),
        "streamers": twitch_cfg.get("streamers"),
        "min_views": config["settings"].get("min_views"),
    }

    twitch_cfg["period"] = window
    twitch_cfg["clips_per_source"] = 500
    twitch_cfg["games"] = [game]
    twitch_cfg["streamers"] = []  # game-based = all streamers
    config["settings"]["min_views"] = 5

    console.print(f"\n[bold cyan]Sniping {game} clips[/bold cyan] (last {window})")

    # Move previously skipped clips back to pending for re-evaluation
    # (each snipe run should consider ALL clips from today, not just new ones)
    queue_dir = config["_queue_dir"]
    pending_dir = queue_dir / "pending"
    skipped_dir = queue_dir / "skipped"
    pending_dir.mkdir(parents=True, exist_ok=True)
    if skipped_dir.exists():
        recycled = 0
        for path in skipped_dir.glob("*.json"):
            shutil.move(str(path), str(pending_dir / path.name))
            recycled += 1
        if recycled:
            console.print(f"  [dim]Recycled {recycled} previously skipped clips[/dim]")

    # 1. Fetch
    console.print(f"\n[bold]Step 1: Fetching all {game} clips...[/bold]")
    clips = fetch_twitch_clips(config, verbose=verbose)

    # Restore config
    for k, v in saved.items():
        if k == "min_views":
            config["settings"]["min_views"] = v
        else:
            twitch_cfg[k] = v

    if not clips:
        console.print("[yellow]No clips found. Try a wider --window.[/yellow]")
        return

    # 2. Score all pending clips for this game
    pending = _load_and_score_pending(config, game=game)
    if not pending:
        console.print("[yellow]No English clips in queue.[/yellow]")
        return

    console.print(f"  {len(pending)} English clips scored and ranked")

    # 3. Pick clip count — interactive picker for compile, default for shorts
    if do_compile and count is None:
        clip_count = _duration_picker(pending, game)
        if clip_count == 0:
            console.print("[yellow]Cancelled.[/yellow]")
            return
    else:
        clip_count = count or (20 if do_compile else 10)

    # 4. Approve top clips, skip the rest
    console.print(f"\n[bold]Step 2: Approving top {clip_count} clips...[/bold]")
    approved = _approve_clips(pending, clip_count, config)

    if approved < 2 and do_compile:
        console.print(f"[yellow]Only {approved} clip(s) — need at least 2 for compilation.[/yellow]")
        return
    if approved == 0:
        console.print("[yellow]No clips approved.[/yellow]")
        return

    # 5. Process (download, subtitle, format)
    console.print(f"\n[bold]Step 3: Processing {approved} clip(s)...[/bold]")
    processed = _process_clips(config, verbose=verbose, for_compilation=do_compile)

    if not processed:
        console.print("[yellow]No clips processed successfully.[/yellow]")
        return

    console.print(f"\n[bold green]{len(processed)} clip(s) ready![/bold green]")

    # 6. Compile into long-form video with countdown ordering
    if do_compile and len(processed) >= 2:
        from clipper.process.compile import compile_clips as run_compile
        from clipper.process.compile import build_thumbnail, build_description
        from clipper.process.score import score_clip

        processed.sort(key=lambda c: score_clip(c), reverse=True)

        # Countdown ordering: hook opener (#2) → worst→best → finale (#1)
        if len(processed) >= 3:
            best = processed.pop(0)   # #1 by score — saved for finale
            hook = processed.pop(0)   # #2 by score — plays first as hook
            rest = list(reversed(processed))  # worst→best builds anticipation
            ordered = [hook] + rest + [best]
        else:
            ordered = processed  # only 2 clips: just play them

        from datetime import datetime

        date_str = datetime.now().strftime("%Y%m%d")
        comp_name = f"compilation_{game.lower().replace(' ', '_')}_{date_str}.mp4"
        comp_path = config["_output_dir"] / comp_name
        title = f"{game.upper()} Daily Highlights — Best Clips {datetime.now().strftime('%m/%d')}"

        console.print(f"\n[bold]Step 4: Compiling {len(ordered)} clips (countdown order)...[/bold]")
        run_compile(ordered, comp_path, config, verbose=verbose, countdown=True)

        # Generate thumbnail from highest-viewed clip
        thumb_path = comp_path.with_suffix(".jpg")
        thumb = build_thumbnail(ordered, thumb_path, title=title, game=game)
        if thumb:
            console.print(f"  [green]Thumbnail:[/green] {thumb}")

        # Generate description with timestamps + streamer credits
        description = build_description(ordered, game=game)

        if do_upload:
            from clipper.upload.youtube import upload_clip
            comp_clip = {
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
            console.print(f"\n[bold]Step 5: Uploading compilation (unlisted)...[/bold]")
            video_id = upload_clip(comp_clip, config, privacy="unlisted", verbose=verbose)
            if video_id:
                console.print(f"[bold green]Uploaded (unlisted):[/bold green] https://youtube.com/watch?v={video_id}")
                console.print("[dim]Run 'clipper publish' to flip to public after processing.[/dim]")
        else:
            console.print(f"\n[bold]Compilation ready:[/bold] {comp_path}")
            console.print(f"[dim]Upload: clipper upload-compilation '{comp_path}' -g '{game}'[/dim]")

    # Or upload individual Shorts
    elif do_upload:
        from clipper.upload.youtube import upload_clip

        console.print(f"\n[bold]Step 4: Uploading {len(processed)} Shorts (unlisted)...[/bold]")
        uploaded = 0
        for clip in processed:
            if clip.get("video_id"):
                continue
            console.print(f"  [bold]Uploading:[/bold] {clip.get('title', '?')[:50]}")
            video_id = upload_clip(clip, config, privacy="unlisted", verbose=verbose)
            if video_id:
                clip["video_id"] = video_id
                clip["_privacy"] = "unlisted"
                meta_path = config["_output_dir"] / f"{clip.get('id', 'unknown')}.json"
                if meta_path.exists():
                    with open(meta_path, "w") as f:
                        json.dump(clip, f, indent=2)
                console.print(f"  [green]Uploaded:[/green] https://youtube.com/watch?v={video_id}")
                uploaded += 1
        console.print(f"\n[bold green]{uploaded} Shorts uploaded (unlisted)![/bold green]")
        console.print("[dim]Run 'clipper publish' to flip to public after processing.[/dim]")


# -- Compile --


@cli.command()
@click.option("--game", "-g", default=None, help="Filter clips by game name.")
@click.option("--streamer", "-s", default=None, help="Filter clips by streamer name.")
@click.option("--count", "-n", default=5, help="Number of clips to include.")
@click.option("--output", "-o", default=None, help="Output file path.")
@click.pass_context
def compile(ctx, game, streamer, count, output):
    """Compile processed clips into a single long-form video."""
    from clipper.process.compile import compile_clips
    from clipper.process.score import score_clip

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]
    output_dir = config["_output_dir"]

    # Load all processed clip metadata
    meta_files = sorted(output_dir.glob("*.json"))
    clips = []
    for mf in meta_files:
        with open(mf) as f:
            clip = json.load(f)
        if "processed_path" not in clip:
            continue
        if not Path(clip["processed_path"]).exists():
            continue
        if clip.get("is_shorts"):
            continue  # skip vertical Shorts — compilation is landscape only
        if game and game.lower() not in clip.get("game", "").lower():
            continue
        if streamer and streamer.lower() not in clip.get("streamer", "").lower():
            continue
        clips.append(clip)

    if len(clips) < 2:
        console.print(f"[yellow]Need at least 2 clips to compile (found {len(clips)}).[/yellow]")
        return

    # Sort by score (highest first) and take top N
    for c in clips:
        c["_score"] = score_clip(c)
    clips.sort(key=lambda c: c["_score"], reverse=True)
    clips = clips[:count]

    console.print(f"[bold]Selected {len(clips)} clips for compilation:[/bold]")
    for i, c in enumerate(clips, 1):
        console.print(f"  {i}. {c.get('streamer', '?')} — {c.get('title', '?')[:50]} ({c.get('duration', 0):.0f}s)")

    if not output:
        game_slug = (game or "mixed").replace(" ", "_").lower()
        output = str(output_dir / f"compilation_{game_slug}.mp4")

    result = compile_clips(clips, Path(output), config, verbose=verbose)
    console.print(f"\n[bold green]Compilation ready:[/bold green] {result}")
    console.print(f"Upload with: [cyan]python -m clipper upload-compilation '{result}'[/cyan]")


# -- Upload Compilation --


@cli.command("upload-compilation")
@click.argument("video_path", type=click.Path(exists=True))
@click.option("--title", "-t", default=None, help="Video title (auto-generated if omitted).")
@click.option("--game", "-g", default=None, help="Game name for tags/description.")
@click.option(
    "--privacy",
    type=click.Choice(["unlisted", "public", "private"]),
    default=None,
    help="Override default privacy setting.",
)
@click.pass_context
def upload_compilation(ctx, video_path, title, game, privacy):
    """Upload a compiled long-form video to YouTube."""
    from clipper.upload.youtube import upload_clip

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]
    privacy = privacy or config["upload"]["default_privacy"]

    # Build a synthetic clip dict for the upload function
    if not title:
        game_label = game or "Gaming"
        title = f"Best {game_label} Clips of the Day | Stream Highlights"
    if len(title) > 100:
        title = title[:97] + "..."

    clip = {
        "title": title,
        "_title_override": title,
        "streamer": "",
        "game": game or "Gaming",
        "platform": "twitch",
        "url": "",
        "processed_path": str(Path(video_path).resolve()),
        "is_shorts": False,
    }

    console.print(f"[bold]Uploading compilation:[/bold] {title}")
    video_id = upload_clip(clip, config, privacy=privacy, verbose=verbose)
    if video_id:
        console.print(f"[bold green]Uploaded:[/bold green] https://youtube.com/watch?v={video_id}")
    else:
        console.print("[red]Upload failed.[/red]")


# -- Publish --


@cli.command()
@click.pass_context
def publish(ctx):
    """Flip unlisted uploads to public once YouTube finishes processing."""
    from clipper.upload.youtube import publish_video

    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]
    output_dir = config["_output_dir"]

    meta_files = sorted(output_dir.glob("*.json"))
    candidates = []
    for mf in meta_files:
        try:
            with open(mf) as f:
                clip = json.load(f)
            vid = clip.get("video_id")
            if vid and clip.get("_privacy") != "public":
                candidates.append((mf, clip, vid))
        except (json.JSONDecodeError, OSError):
            continue

    if not candidates:
        console.print("[yellow]No unlisted videos to publish.[/yellow]")
        return

    console.print(f"[bold]Found {len(candidates)} video(s) to publish...[/bold]")
    published = 0
    for mf, clip, vid in candidates:
        title = clip.get("title", vid)[:50]
        console.print(f"  Checking: {title}")
        if publish_video(vid, verbose=verbose):
            clip["_privacy"] = "public"
            with open(mf, "w") as f:
                json.dump(clip, f, indent=2)
            published += 1

    console.print(f"\n[bold green]{published} video(s) published.[/bold green]")


# -- Discover --


@cli.group()
@click.pass_context
def discover(ctx):
    """Discover trending niches and content gaps."""
    pass


@discover.command()
@click.pass_context
def trending(ctx):
    """Show trending games and categories by clip volume."""
    from clipper.discover.trends import show_trending

    config = ctx.obj["config"]
    show_trending(config)


@discover.command()
@click.pass_context
def gaps(ctx):
    """Analyze niche gaps: high clip activity, low YouTube competition."""
    from clipper.discover.gaps import show_gaps

    config = ctx.obj["config"]
    show_gaps(config)


# -- Run (interactive TUI) --


@cli.command()
@click.pass_context
def run(ctx):
    """Interactive pipeline: pick source → games/streamers → fetch → review → process → upload."""
    from clipper.tui import run_tui

    run_tui(verbose=ctx.obj["verbose"])


def main():
    cli()


if __name__ == "__main__":
    main()
