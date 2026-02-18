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


def main():
    cli()


if __name__ == "__main__":
    main()
