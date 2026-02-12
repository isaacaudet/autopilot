"""Fetch YouTube clips using yt-dlp."""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


def fetch_youtube_clips(
    config: dict, dry_run: bool = False, verbose: bool = False
) -> list[dict]:
    """Fetch recent clips from configured YouTube channels.

    Uses yt-dlp --dump-json --flat-playlist to get video metadata
    without downloading any media.
    """
    if not shutil.which("yt-dlp"):
        console.print(
            "[red bold]yt-dlp is not installed.[/red bold]\n"
            "Install it with: [cyan]pip install yt-dlp[/cyan] or [cyan]brew install yt-dlp[/cyan]"
        )
        return []

    channels = config["targets"]["youtube"].get("channels", [])
    if not channels:
        console.print("[yellow]No YouTube channels configured.[/yellow]")
        return []

    settings = config.get("settings", {})
    max_duration = settings.get("max_duration", 300)
    min_views = settings.get("min_views", 1000)
    clips_per_source = config["targets"]["youtube"].get("clips_per_source", 10)

    all_clips = []

    for channel_url in channels:
        console.print(f"[bold]Fetching from:[/bold] {channel_url}")
        clips = _fetch_channel(
            channel_url,
            max_duration=max_duration,
            min_views=min_views,
            limit=clips_per_source,
            verbose=verbose,
        )
        all_clips.extend(clips)
        if verbose:
            console.print(f"  Found {len(clips)} clips matching filters")

    if dry_run:
        _print_dry_run_table(all_clips)
    else:
        _save_clips(all_clips, config["_queue_dir"])

    return all_clips


def _fetch_channel(
    channel_url: str,
    max_duration: int,
    min_views: int,
    limit: int,
    verbose: bool,
) -> list[dict]:
    """Fetch and filter videos from a single YouTube channel."""
    # Fetch more than we need since we'll filter some out
    fetch_limit = limit * 3

    cmd = [
        "yt-dlp",
        "--dump-json",
        "--flat-playlist",
        "--playlist-end", str(fetch_limit),
        "--no-warnings",
        channel_url,
    ]

    if verbose:
        console.print(f"  Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        console.print(f"[red]Timeout fetching {channel_url}[/red]")
        return []

    if result.returncode != 0:
        stderr = result.stderr.strip()
        console.print(f"[red]yt-dlp error for {channel_url}:[/red] {stderr}")
        return []

    clips = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        try:
            video = json.loads(line)
        except json.JSONDecodeError:
            continue

        duration = video.get("duration") or 0
        view_count = video.get("view_count") or 0

        if duration > max_duration:
            continue
        if view_count < min_views:
            continue

        video_id = video.get("id", "")
        url = video.get("url") or video.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"

        clip = {
            "id": video_id,
            "title": video.get("title", ""),
            "url": url,
            "duration": duration,
            "view_count": view_count,
            "streamer": video.get("channel", video.get("uploader", "")),
            "game": "",
            "thumbnail_url": video.get("thumbnail", ""),
            "platform": "youtube",
            "created_at": video.get("upload_date", ""),
        }
        clips.append(clip)

        if len(clips) >= limit:
            break

    return clips


def _save_clips(clips: list[dict], queue_dir: Path) -> None:
    """Save clip metadata as individual JSON files in queue/pending/."""
    pending_dir = queue_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for clip in clips:
        clip_path = pending_dir / f"{clip['id']}.json"
        if clip_path.exists():
            console.print(f"  [dim]Skipping (already queued):[/dim] {clip['title']}")
            continue
        clip["fetched_at"] = datetime.now(timezone.utc).isoformat()
        with open(clip_path, "w") as f:
            json.dump(clip, f, indent=2)
        saved += 1
        console.print(f"  [green]Queued:[/green] {clip['title']}")

    console.print(f"[bold green]Saved {saved} new YouTube clips to queue.[/bold green]")


def _print_dry_run_table(clips: list[dict]) -> None:
    """Print a rich table showing what would be fetched."""
    if not clips:
        console.print("[yellow]No YouTube clips matched the filters.[/yellow]")
        return

    table = Table(title="YouTube Clips (dry run)")
    table.add_column("Channel", style="cyan")
    table.add_column("Title", style="white", max_width=50)
    table.add_column("Duration", justify="right")
    table.add_column("Views", justify="right", style="green")
    table.add_column("URL", style="dim")

    for clip in clips:
        duration = clip.get("duration", 0)
        mins, secs = divmod(int(duration), 60)
        table.add_row(
            clip.get("streamer", ""),
            clip.get("title", ""),
            f"{mins}:{secs:02d}",
            f"{clip.get('view_count', 0):,}",
            clip.get("url", ""),
        )

    console.print(table)
