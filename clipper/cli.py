"""Clipper entry point — web-first, minimal CLI."""

import click

from clipper.config import load_config


@click.group()
def cli():
    """Clipper — automated clip pipeline."""
    pass


@cli.command()
@click.option("--port", default=8420, help="Port to serve on.")
@click.option("--host", default="localhost", help="Host to bind to.")
def serve(port, host):
    """Launch the Clipper web app (production — serves built frontend)."""
    import uvicorn
    import webbrowser
    from clipper.api import create_app

    app = create_app(load_config())
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


@cli.command()
@click.option("--port", default=8420, help="API port.")
@click.option("--host", default="localhost", help="API host.")
def dev(port, host):
    """Launch backend + Vite dev server (hot reload)."""
    import subprocess
    import signal
    import sys
    import threading
    import webbrowser
    from pathlib import Path

    import uvicorn
    from clipper.api import create_app

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if not (web_dir / "package.json").exists():
        raise click.ClickException(f"web/ not found at {web_dir}")

    app = create_app(load_config())

    # Start Vite dev server in background
    vite = subprocess.Popen(
        ["npm", "run", "dev", "--", "--clearScreen", "false"],
        cwd=str(web_dir),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    def _shutdown(*_args):
        vite.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Open browser after a short delay (let Vite start)
    threading.Timer(2.0, lambda: webbrowser.open("http://localhost:5173")).start()

    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        vite.terminate()


@cli.command()
@click.option("--verbose", "-v", is_flag=True)
def release(verbose):
    """Execute pending scheduled releases (for cron)."""
    from clipper.schedule import execute_releases

    execute_releases(load_config(), verbose=verbose)


@cli.command()
@click.option("--count", type=int, default=None, help="How many clips to process this run.")
@click.option("--min-score", type=float, default=None, help="Minimum model score threshold.")
@click.option("--channel", "-c", default=None, help="Channel key from config.yaml.")
@click.option("--game", default=None, help="Game name (used for gamewide fetch scope).")
@click.option("--period", default=None, help="Fetch window (e.g. 24h, 48h, 7d).")
@click.option("--scope", type=click.Choice(["gamewide", "configured", "selected"]), default=None, help="Fetch scope.")
@click.option("--streamer", "streamers", multiple=True, help="Streamer login (repeat for selected scope).")
@click.option("--auto-upload/--no-auto-upload", default=None, help="Upload processed clips after processing.")
@click.option("--privacy", type=click.Choice(["unlisted", "private", "public"]), default=None, help="Upload privacy.")
@click.option("--daily-limit", type=int, default=None, help="Daily cap across repeated runs (defaults to autopilot.daily_count).")
@click.option("--upload-channels", default=None, help="Comma-separated extra channels to cross-post to (overrides autopilot.upload_channels).")
@click.option("--verbose", "-v", is_flag=True)
def autopilot(
    count,
    min_score,
    channel,
    game,
    period,
    scope,
    streamers,
    auto_upload,
    privacy,
    daily_limit,
    upload_channels,
    verbose,
):
    """Run autopilot shorts workflow (learn + fetch + score + process [+ optional upload])."""
    from clipper.workflow import count_output_shorts_today, run_autopilot_workflow

    config = load_config()
    autopilot_cfg = config.get("autopilot", {}) or {}
    upload_cfg = config.get("upload", {}) or {}

    resolved_count = max(1, int(count if count is not None else autopilot_cfg.get("daily_count", 8)))
    resolved_min_score = float(min_score if min_score is not None else autopilot_cfg.get("min_score", 45))
    resolved_channel = str(channel if channel is not None else autopilot_cfg.get("channel", "")).strip() or None
    resolved_game = str(game if game is not None else autopilot_cfg.get("game", "")).strip() or None
    resolved_period = str(period if period is not None else autopilot_cfg.get("period", "24h")).strip() or "24h"
    resolved_scope = str(scope if scope is not None else autopilot_cfg.get("scope", "configured")).strip().lower()
    if resolved_scope not in {"gamewide", "configured", "selected"}:
        resolved_scope = "configured"

    configured_streamers = autopilot_cfg.get("streamers")
    if streamers:
        resolved_streamers = [str(s).strip() for s in streamers if str(s).strip()]
    elif isinstance(configured_streamers, list):
        resolved_streamers = [str(s).strip() for s in configured_streamers if str(s).strip()]
    else:
        resolved_streamers = None

    if auto_upload is None:
        resolved_auto_upload = bool(autopilot_cfg.get("auto_upload", False))
    else:
        resolved_auto_upload = bool(auto_upload)

    resolved_privacy = str(
        privacy
        if privacy is not None
        else autopilot_cfg.get("privacy", upload_cfg.get("default_privacy", "unlisted"))
    ).strip().lower()
    if resolved_privacy not in {"unlisted", "private", "public"}:
        resolved_privacy = "unlisted"

    if daily_limit is None:
        daily_limit_value = int(autopilot_cfg.get("daily_count", resolved_count))
    else:
        daily_limit_value = int(daily_limit)
    resolved_daily_limit = daily_limit_value if daily_limit_value > 0 else None

    if resolved_channel:
        channels = config.get("channels", {}) or {}
        if resolved_channel not in channels:
            raise click.ClickException(f"Unknown channel: {resolved_channel}")

    if upload_channels:
        config.setdefault("autopilot", {})["upload_channels"] = [
            c.strip() for c in upload_channels.split(",") if c.strip()
        ]

    today = count_output_shorts_today(config, channel=resolved_channel)
    click.echo(
        f"Autopilot run: target={resolved_count} min_score={resolved_min_score:.0f} "
        f"scope={resolved_scope} period={resolved_period} "
        f"channel={resolved_channel or 'auto'} today={today}"
    )

    result = run_autopilot_workflow(
        config,
        count=resolved_count,
        min_score=resolved_min_score,
        channel=resolved_channel,
        game=resolved_game,
        period=resolved_period,
        scope=resolved_scope,
        streamers=resolved_streamers,
        auto_upload=resolved_auto_upload,
        privacy=resolved_privacy,
        daily_limit=resolved_daily_limit,
        verbose=verbose,
        state=None,
    )

    click.echo(
        "Autopilot complete: "
        f"approved={result.get('approved', 0)} "
        f"processed={result.get('processed', 0)} "
        f"uploaded={result.get('uploaded', 0)} "
        f"status={result.get('status', 'done')}"
    )


@cli.command("daily-compilation")
@click.option("--channel", "-c", default=None, help="Channel key from config.yaml.")
@click.option("--game", default=None, help="Game name (overrides config).")
@click.option("--duration", type=int, default=None, help="Target compilation length in minutes.")
@click.option("--privacy", type=click.Choice(["unlisted", "private", "public"]), default=None)
@click.option("--layout", type=click.Choice(["fill", "blur"]), default=None, help="Shorts layout override.")
@click.option("--scope", type=click.Choice(["gamewide", "configured"]), default=None, help="Fetch scope for Shorts autopilot.")
@click.option("--min-score", type=float, default=None, help="Minimum score threshold (overrides config).")
@click.option("--period", default=None, help="Fetch window override (e.g. 24h, 3d, 7d).")
@click.option("--skip-shorts", is_flag=True, help="Skip the Shorts autopilot run (compilation only).")
@click.option("--upload-channels", default=None, help="Comma-separated extra channels to cross-post to (overrides autopilot.upload_channels).")
@click.option("--verbose", "-v", is_flag=True)
def daily_compilation(channel, game, duration, privacy, layout, scope, min_score, period, skip_shorts, upload_channels, verbose):
    """Fetch the previous day's best clips and build + upload a daily compilation."""
    from clipper.workflow import run_compilation_workflow

    config = load_config()
    autopilot_cfg = config.get("autopilot", {}) or {}
    upload_cfg = config.get("upload", {}) or {}

    resolved_game = str(game or autopilot_cfg.get("game", "Deadlock")).strip()
    resolved_channel = str(channel or autopilot_cfg.get("channel", "")).strip() or None
    resolved_privacy = str(
        privacy or autopilot_cfg.get("privacy", upload_cfg.get("default_privacy", "unlisted"))
    ).strip().lower()
    if resolved_privacy not in {"unlisted", "private", "public"}:
        resolved_privacy = "unlisted"
    resolved_duration = int(duration or (config.get("compilation") or {}).get("target_minutes", 10))
    resolved_shorts_count = int(autopilot_cfg.get("daily_count", 20))
    resolved_min_score = float(min_score if min_score is not None else autopilot_cfg.get("min_score", 45))
    resolved_period = str(period or "24h").strip()
    if layout:
        config.setdefault("autopilot", {})["shorts_layout"] = layout

    if upload_channels:
        config.setdefault("autopilot", {})["upload_channels"] = [
            c.strip() for c in upload_channels.split(",") if c.strip()
        ]

    click.echo(
        f"Daily compilation: game={resolved_game} channel={resolved_channel or 'auto'} "
        f"target={resolved_duration}min privacy={resolved_privacy}"
        + ("" if skip_shorts else f" shorts={resolved_shorts_count}")
    )

    # Publish any scheduled releases that are due
    from clipper.schedule import execute_releases
    try:
        execute_releases(config, verbose=verbose)
    except Exception as e:
        click.echo(f"[warning] execute_releases failed (non-fatal): {e}")

    # Refresh YouTube analytics + retrain weights before selecting clips
    try:
        from clipper.learn import collect_performance, train_weights
        n = collect_performance(config)
        if n > 0 or True:  # always retrain so new manual weight edits apply
            train_weights(config)
            if verbose:
                click.echo(f"Learning: refreshed {n} performance snapshot(s), weights retrained")
    except Exception as e:
        if verbose:
            click.echo(f"Learning refresh failed (non-fatal): {e}")

    # 1. Compilation first — gets first pick of fresh gamewide clips before shorts narrows the pool
    run_compilation_workflow(
        config,
        game=resolved_game,
        duration=resolved_duration,
        channel=resolved_channel,
        auto=True,
        privacy=resolved_privacy,
        verbose=verbose,
    )

    # 2. Shorts autopilot — fetches configured-streamer clips (separate pool from gamewide compilation)
    if not skip_shorts:
        from clipper.workflow import run_autopilot_workflow
        click.echo(f"\nRunning Shorts autopilot ({resolved_shorts_count} clips)...")
        result = run_autopilot_workflow(
            config,
            count=resolved_shorts_count,
            min_score=resolved_min_score,
            channel=resolved_channel,
            game=resolved_game,
            period=resolved_period,
            scope=scope or "configured",
            auto_upload=True,
            privacy=resolved_privacy,
            daily_limit=resolved_shorts_count,
            verbose=verbose,
            state=None,
        )
        click.echo(
            f"Shorts complete: approved={result.get('approved', 0)} "
            f"processed={result.get('processed', 0)} "
            f"uploaded={result.get('uploaded', 0)}"
        )


@cli.command("autopilot-cron")
@click.option("--install", "install_job", is_flag=True, help="Install autopilot cron/launchd schedule.")
@click.option("--remove", "remove_job", is_flag=True, help="Remove autopilot cron/launchd schedule.")
@click.option("--status", "show_status", is_flag=True, help="Show autopilot cron/launchd status.")
def autopilot_cron(install_job, remove_job, show_status):
    """Manage scheduled autopilot runs."""
    from clipper.cron import get_status, install_autopilot, remove_autopilot

    if install_job and remove_job:
        raise click.ClickException("Use only one of --install or --remove.")

    if install_job:
        ok = install_autopilot()
        if not ok:
            raise click.ClickException("Failed to install autopilot schedule.")
        click.echo("Autopilot schedule installed.")
        return

    if remove_job:
        ok = remove_autopilot()
        if not ok:
            raise click.ClickException("Failed to remove autopilot schedule.")
        click.echo("Autopilot schedule removed.")
        return

    status = get_status()
    click.echo(
        f"Autopilot scheduled: {'yes' if status.get('autopilot_installed') else 'no'} "
        f"({status.get('autopilot_schedule', 'n/a')})"
    )


@cli.command()
@click.option("--channel", "-c", required=True, help="Channel key from config.yaml.")
def auth(channel):
    """Set up OAuth for a channel (auto-detects platform from config)."""
    config = load_config()
    platform = config.get("channels", {}).get(channel, {}).get("platform", "youtube")
    if platform == "youtube":
        from clipper.upload.auth import setup_channel_auth
        setup_channel_auth(channel, config)
    elif platform == "tiktok":
        from clipper.upload.auth import setup_tiktok_auth
        setup_tiktok_auth(channel, config)
    elif platform in ("instagram", "facebook"):
        from clipper.upload.auth import setup_meta_auth
        setup_meta_auth(channel, config, platform=platform)
    else:
        raise click.ClickException(f"Unknown platform: {platform}")


@cli.command()
@click.option("--verbose", "-v", is_flag=True)
def crosspost(verbose):
    """Cross-post top YouTube clips to Instagram/Facebook."""
    from clipper.crosspost import run_crosspost

    result = run_crosspost(load_config(), verbose=verbose)
    if result.get("clip"):
        click.echo(f"Cross-posted: {result['clip'][:50]}")
        if result.get("instagram"):
            click.echo(f"  Instagram: {result['instagram']}")
        if result.get("facebook"):
            click.echo(f"  Facebook: {result['facebook']}")
    else:
        click.echo("Nothing to cross-post.")


@cli.command()
def calendar():
    """Show the upcoming release schedule."""
    from clipper.schedule import show_calendar

    show_calendar(load_config())


@cli.command()
@click.option("--channel", "-c", default=None, help="Filter by channel.")
def status(channel):
    """Show pipeline health: tokens, release queue, recent uploads, cron status."""
    from datetime import datetime
    from rich.console import Console
    from rich.table import Table
    from clipper.db import get_db
    from clipper.cron import get_status
    from clipper.upload.auth import validate_token
    from clipper.config import get_project_root

    console = Console()
    config = load_config()
    conn = get_db(config)
    root = get_project_root()

    # Token health
    console.print("[bold]Tokens:[/bold]")
    for ch_key, ch_conf in config.get("channels", {}).items():
        token_file = ch_conf.get("token_file", "")
        if token_file:
            valid, msg = validate_token(str(root / token_file))
            icon = "[green]OK[/green]" if valid else "[red]FAIL[/red]"
            console.print(f"  {ch_key}: {icon} — {msg}")
    console.print()

    # Shorts processed today
    from clipper.workflow import count_output_shorts_today
    shorts_today = count_output_shorts_today(config, channel=channel)
    daily_count = (config.get("autopilot", {}) or {}).get("daily_count", 7)
    console.print(f"[bold]Shorts today:[/bold] {shorts_today}/{daily_count}")
    console.print()

    # Cron health
    cron = get_status()
    console.print("[bold]Cron jobs:[/bold]")
    for job in ("autopilot", "marathon", "release", "crosspost"):
        key = f"{job}_installed"
        installed = cron.get(key, False)
        console.print(f"  {job}: {'[green]active[/green]' if installed else '[red]not installed[/red]'}")
    console.print()

    # Release queue summary
    ch_filter = "AND r.channel = ?" if channel else ""
    params = [channel] if channel else []

    pending = conn.execute(
        f"SELECT COUNT(*) as n FROM releases r WHERE status IN ('pending','executing') {ch_filter}",
        params,
    ).fetchone()["n"]
    failed_24h = conn.execute(
        f"SELECT COUNT(*) as n FROM releases r WHERE status='failed' AND datetime(scheduled_at) > datetime('now','-24 hours') {ch_filter}",
        params,
    ).fetchone()["n"]
    published_24h = conn.execute(
        f"SELECT COUNT(*) as n FROM releases r WHERE status IN ('published','uploaded') AND datetime(scheduled_at) > datetime('now','-24 hours') {ch_filter}",
        params,
    ).fetchone()["n"]

    console.print(
        f"[bold]Releases (24h):[/bold] "
        f"[green]{published_24h} published[/green]  "
        f"[yellow]{pending} pending[/yellow]  "
        f"[red]{failed_24h} failed[/red]"
    )
    console.print()

    # Recent failures
    failures = conn.execute(
        f"SELECT r.id, r.clip_id, r.channel, r.last_error, r.scheduled_at "
        f"FROM releases r WHERE r.status='failed' AND datetime(r.scheduled_at) > datetime('now','-24 hours') {ch_filter} "
        f"ORDER BY r.scheduled_at DESC LIMIT 5",
        params,
    ).fetchall()
    if failures:
        table = Table(title="Recent failures")
        table.add_column("ID", style="dim")
        table.add_column("Channel")
        table.add_column("Scheduled")
        table.add_column("Error")
        for f in failures:
            f = dict(f)
            table.add_row(
                str(f["id"]),
                f["channel"],
                f["scheduled_at"][-11:-1] if f["scheduled_at"] else "?",
                (f.get("last_error") or "")[:60],
            )
        console.print(table)
        console.print()

    # Upcoming releases
    upcoming = conn.execute(
        f"SELECT r.id, r.clip_id, r.channel, r.status, r.scheduled_at, c.streamer, c.title "
        f"FROM releases r LEFT JOIN clips c ON r.clip_id = c.id "
        f"WHERE r.status IN ('pending','uploaded','executing') {ch_filter} "
        f"ORDER BY r.scheduled_at LIMIT 8",
        params,
    ).fetchall()
    if upcoming:
        table = Table(title="Upcoming releases")
        table.add_column("Time")
        table.add_column("Channel")
        table.add_column("Status")
        table.add_column("Clip")
        for u in upcoming:
            u = dict(u)
            sched = u["scheduled_at"][-11:-1] if u["scheduled_at"] else "?"
            label = f"{u.get('streamer', '?')} — {(u.get('title') or '?')[:30]}"
            table.add_row(sched, u["channel"], u["status"], label)
        console.print(table)


def main():
    cli()


if __name__ == "__main__":
    main()
