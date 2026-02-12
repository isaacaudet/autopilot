"""Fetch clips from Kick channels."""

import json
import logging
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

KICK_API_BASE = "https://kick.com/api/v2/channels"


def _get_clips_for_channel(channel: str, limit: int = 20) -> list[dict]:
    """Fetch clips for a single Kick channel. Returns raw API response clips."""
    url = f"{KICK_API_BASE}/{channel}/clips"
    all_clips = []
    cursor = None

    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to fetch clips for %s: %s", channel, e)
            break

        data = resp.json()

        # Kick API may return clips under 'clips' key or as a list directly
        clips = data.get("clips") if isinstance(data, dict) else data
        if not clips:
            break

        all_clips.extend(clips)

        # Handle pagination — look for a next_cursor in the response
        if isinstance(data, dict):
            cursor = data.get("next_cursor") or data.get("cursor")
        else:
            cursor = None

        if not cursor:
            break

    return all_clips


def _normalize_clip(raw: dict, channel: str) -> dict:
    """Convert a raw Kick API clip into standardized clip dict."""
    clip_id = str(raw.get("id", ""))

    # Build clip URL from known Kick URL patterns
    clip_url = raw.get("url") or raw.get("clip_url") or ""
    if not clip_url and clip_id:
        clip_url = f"https://kick.com/{channel}/clips/{clip_id}"

    return {
        "id": clip_id,
        "title": raw.get("title", "Untitled"),
        "url": clip_url,
        "duration": raw.get("duration", 0),
        "view_count": raw.get("view_count", 0),
        "streamer": channel,
        "game": raw.get("category", {}).get("name", "") if isinstance(raw.get("category"), dict) else raw.get("category", ""),
        "thumbnail_url": raw.get("thumbnail_url", ""),
        "platform": "kick",
        "created_at": raw.get("created_at", ""),
    }


def _passes_filters(clip: dict, min_views: int, max_duration: int) -> bool:
    """Check whether a clip passes the configured filters."""
    if clip["view_count"] < min_views:
        return False
    if clip["duration"] > max_duration > 0:
        return False
    return True


def _save_to_queue(clip: dict, queue_dir: Path) -> None:
    """Save a clip dict as JSON in the pending queue."""
    pending_dir = queue_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    path = pending_dir / f"{clip['id']}.json"
    with open(path, "w") as f:
        json.dump(clip, f, indent=2)


def _print_dry_run_table(clips: list[dict]) -> None:
    """Print a rich table of clips for dry-run output."""
    table = Table(title="Kick Clips (dry run)")
    table.add_column("Streamer", style="cyan")
    table.add_column("Title", style="white", max_width=40)
    table.add_column("Views", justify="right", style="green")
    table.add_column("Duration", justify="right", style="yellow")
    table.add_column("Game", style="magenta")

    for clip in clips:
        table.add_row(
            clip["streamer"],
            clip["title"],
            str(clip["view_count"]),
            f"{clip['duration']}s",
            clip["game"],
        )

    console.print(table)


def fetch_kick_clips(
    config: dict,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """Fetch clips from all configured Kick streamers.

    Args:
        config: Loaded application config dict.
        dry_run: If True, print results but don't save to queue.
        verbose: If True, print extra status info.

    Returns:
        List of standardized clip dicts.
    """
    kick_cfg = config["targets"].get("kick", {})
    streamers = kick_cfg.get("streamers", [])
    clips_per_source = kick_cfg.get("clips_per_source", 10)

    min_views = config["settings"].get("min_views", 0)
    max_duration = config["settings"].get("max_duration", 0)

    queue_dir = config.get("_queue_dir")

    all_clips = []

    for channel in streamers:
        if verbose:
            console.print(f"[dim]Fetching clips for kick/{channel}...[/dim]")

        raw_clips = _get_clips_for_channel(channel, limit=clips_per_source)

        for raw in raw_clips:
            clip = _normalize_clip(raw, channel)

            if not _passes_filters(clip, min_views, max_duration):
                continue

            all_clips.append(clip)

            if not dry_run and queue_dir:
                _save_to_queue(clip, queue_dir)

    if verbose or dry_run:
        console.print(f"[bold]Kick:[/bold] {len(all_clips)} clips passed filters")

    if dry_run:
        _print_dry_run_table(all_clips)

    return all_clips
