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
