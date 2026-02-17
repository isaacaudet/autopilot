"""Release queue management — schedule clips to channels and publish on time."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()



def _load_channel_config(channel: str, config: dict) -> dict:
    """Load a channel's config from the channels section. Raises if not found."""
    channels = config.get("channels", {})
    if channel not in channels:
        raise ValueError(
            f"Channel '{channel}' not found in config.yaml. "
            f"Available: {', '.join(channels.keys()) or '(none)'}"
        )
    return channels[channel]


def get_next_slots(channel: str, config: dict, count: int = 1) -> list[datetime]:
    """Return the next N available release times for a channel.

    Reads the channel's schedule (shorts_per_day, release_times) and scans
    existing releases to find unfilled slots starting from now.
    """
    from clipper.db import list_releases

    ch_config = _load_channel_config(channel, config)
    schedule = ch_config.get("schedule", {})
    release_times = schedule.get("release_times", ["12:00"])

    # Parse release times into (hour, minute) tuples
    time_slots = []
    for t in release_times:
        parts = t.split(":")
        time_slots.append((int(parts[0]), int(parts[1])))
    time_slots.sort()

    # Load existing releases for this channel
    releases = list_releases(config, channel=channel)
    taken = {r.get("scheduled_at", "") for r in releases if r.get("status") != "failed"}

    # Find next available slots
    now = datetime.now()
    slots = []
    day = now.date()

    # Look up to 30 days ahead
    for _ in range(30):
        for hour, minute in time_slots:
            candidate = datetime(day.year, day.month, day.day, hour, minute)
            if candidate <= now:
                continue
            iso = candidate.isoformat()
            if iso not in taken:
                slots.append(candidate)
                taken.add(iso)  # Don't double-assign
                if len(slots) >= count:
                    return slots
        day += timedelta(days=1)

    return slots


def schedule_release(
    clip_id: str,
    channel: str,
    scheduled_at: datetime,
    config: dict,
    meta_path: str | None = None,
    privacy: str | None = None,
) -> int:
    """Create a release queue entry. Returns release ID."""
    from clipper.db import create_release

    ch_config = _load_channel_config(channel, config)

    if privacy is None:
        privacy = ch_config.get("default_privacy", "unlisted")

    return create_release(
        config, clip_id, channel, scheduled_at.isoformat(),
        privacy=privacy, meta_path=meta_path,
    )


def get_pending_releases(config: dict) -> list[dict]:
    """Load all releases sorted by scheduled_at."""
    from clipper.db import list_releases
    return list_releases(config)


def execute_releases(config: dict, verbose: bool = False) -> int:
    """Upload + publish all releases whose time has come. Returns count published."""
    from clipper.upload.dispatcher import upload_clip, publish_video, get_channel_platform, platform_id_column
    from clipper.db import pending_releases_due, update_release, update_clip as db_update_clip

    due = pending_releases_due(config)
    published = 0

    for data in due:
        status = data.get("status", "")
        release_id = data.get("id")

        # Upload pending releases
        if status == "pending":
            meta_path = data.get("meta_path")
            if not meta_path or not Path(meta_path).exists():
                console.print(f"[yellow]Missing meta for {data.get('clip_id')}, skipping.[/yellow]")
                update_release(config, release_id, status="failed")
                continue

            with open(meta_path) as f:
                clip = json.load(f)

            channel = data.get("channel", "")
            privacy = data.get("privacy", "unlisted")
            platform = get_channel_platform(channel, config)

            console.print(f"[bold]Uploading:[/bold] {clip.get('title', '?')[:50]} → {channel} ({platform})")
            video_id = upload_clip(clip, config, privacy=privacy, verbose=verbose, channel=channel)

            if video_id:
                update_release(config, release_id, status="uploaded", video_id=video_id)

                # Store in correct field
                id_col = platform_id_column(platform)
                clip[id_col] = video_id
                with open(meta_path, "w") as f:
                    json.dump(clip, f, indent=2)

                # Update DB
                clip_id = data.get("clip_id", "")
                if clip_id:
                    db_update_clip(config, clip_id, **{id_col: video_id})

                console.print(f"[green]  Uploaded ({platform}):[/green] {video_id}")
            else:
                update_release(config, release_id, status="failed")
                console.print("[red]  Upload failed.[/red]")
            continue

        # Publish uploaded releases
        if status == "uploaded":
            video_id = data.get("video_id")
            if not video_id:
                continue
            channel = data.get("channel", "")
            if publish_video(video_id, verbose=verbose, channel=channel, config=config):
                update_release(config, release_id, status="published")
                published += 1

    if published:
        console.print(f"\n[bold green]{published} video(s) published.[/bold green]")
    elif verbose:
        console.print("[dim]No releases due right now.[/dim]")

    return published


def show_calendar(config: dict):
    """Display the upcoming release schedule."""
    releases = get_pending_releases(config)

    if not releases:
        console.print("[yellow]No scheduled releases.[/yellow]")
        return

    # Group by channel
    by_channel: dict[str, list[dict]] = {}
    for r in releases:
        ch = r.get("channel", "unknown")
        by_channel.setdefault(ch, []).append(r)

    channels = config.get("channels", {})

    for channel, items in by_channel.items():
        ch_config = channels.get(channel, {})
        display_name = ch_config.get("name", channel)

        table = Table(title=f"{channel} — {display_name}")
        table.add_column("Date", style="cyan")
        table.add_column("Time", style="cyan")
        table.add_column("Status")
        table.add_column("Clip")

        for item in items:
            scheduled = item.get("scheduled_at", "")
            try:
                dt = datetime.fromisoformat(scheduled)
                date_str = dt.strftime("%b %d")
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                date_str = "?"
                time_str = "?"

            status = item.get("status", "?")
            status_display = {
                "pending": "[dim]○ pending[/dim]",
                "uploaded": "[yellow]↑ uploaded[/yellow]",
                "published": "[green]✓ published[/green]",
                "failed": "[red]✗ failed[/red]",
            }.get(status, status)

            # Try to load clip title from meta
            clip_label = item.get("clip_id", "?")[:12]
            meta_path = item.get("meta_path")
            if meta_path and Path(meta_path).exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    streamer = meta.get("streamer", "")
                    title = meta.get("title", "")[:30]
                    clip_label = f"{streamer} — {title}" if streamer else title
                except (json.JSONDecodeError, OSError):
                    pass

            table.add_row(date_str, time_str, status_display, clip_label)

        console.print(table)
        console.print()
