"""Fetch top clips from Twitch using the Helix API."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table

from clipper.config import require_env

HELIX_BASE = "https://api.twitch.tv/helix"

console = Console()


def _get_app_access_token(client_id: str, client_secret: str) -> str:
    """Obtain an app access token via client credentials flow."""
    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"Twitch token response missing access_token: {resp.text[:200]}")
    return token


def _parse_period(period: str) -> datetime:
    """Parse a period string like '24h', '7d', '48h' into a started_at datetime."""
    match = re.fullmatch(r"(\d+)([hdw])", period.strip().lower())
    if not match:
        raise ValueError(f"Invalid period format: {period!r}. Expected e.g. '24h', '7d', '1w'.")

    value, unit = int(match.group(1)), match.group(2)
    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "d":
        delta = timedelta(days=value)
    elif unit == "w":
        delta = timedelta(weeks=value)
    else:
        raise ValueError(f"Unknown period unit: {unit}")

    return datetime.now(timezone.utc) - delta


def _helix_headers(client_id: str, token: str) -> dict:
    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }


def _resolve_user_ids(names: list[str], headers: dict) -> dict[str, str]:
    """Resolve Twitch login names to broadcaster IDs. Returns {name: id}."""
    if not names:
        return {}

    result = {}
    # Helix allows up to 100 logins per request
    for i in range(0, len(names), 100):
        batch = names[i : i + 100]
        resp = requests.get(
            f"{HELIX_BASE}/users",
            headers=headers,
            params=[("login", name) for name in batch],
            timeout=15,
        )
        resp.raise_for_status()
        for user in resp.json().get("data", []):
            result[user["login"].lower()] = user["id"]

    return result


def _resolve_game_ids(names: list[str], headers: dict) -> dict[str, str]:
    """Resolve game names to game IDs. Returns {name: id}."""
    if not names:
        return {}

    result = {}
    for i in range(0, len(names), 100):
        batch = names[i : i + 100]
        resp = requests.get(
            f"{HELIX_BASE}/games",
            headers=headers,
            params=[("name", name) for name in batch],
            timeout=15,
        )
        resp.raise_for_status()
        for game in resp.json().get("data", []):
            result[game["name"]] = game["id"]

    return result


def _resolve_broadcaster_languages(
    broadcaster_ids: list[str], headers: dict
) -> dict[str, str]:
    """Resolve broadcaster IDs to their stream language. Returns {id: language}."""
    if not broadcaster_ids:
        return {}

    result = {}
    # Helix /channels endpoint accepts up to 100 broadcaster_ids
    for i in range(0, len(broadcaster_ids), 100):
        batch = broadcaster_ids[i : i + 100]
        resp = requests.get(
            f"{HELIX_BASE}/channels",
            headers=headers,
            params=[("broadcaster_id", bid) for bid in batch],
            timeout=15,
        )
        resp.raise_for_status()
        for channel in resp.json().get("data", []):
            lang = channel.get("broadcaster_language", "")
            result[channel["broadcaster_id"]] = lang.lower()

    return result


def _fetch_clips(
    headers: dict,
    *,
    broadcaster_id: str | None = None,
    game_id: str | None = None,
    first: int = 100,
    started_at: datetime | None = None,
) -> list[dict]:
    """Fetch clips from Helix with pagination, filtering by broadcaster or game."""
    params: dict = {"first": min(first, 100)}
    if broadcaster_id:
        params["broadcaster_id"] = broadcaster_id
    elif game_id:
        params["game_id"] = game_id
    else:
        return []

    if started_at:
        params["started_at"] = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_clips = []
    remaining = first

    while remaining > 0:
        params["first"] = min(remaining, 100)
        resp = requests.get(
            f"{HELIX_BASE}/clips",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        clips = data.get("data", [])
        if not clips:
            break
        all_clips.extend(clips)
        remaining -= len(clips)

        # Paginate if there's a cursor and we need more
        cursor = data.get("pagination", {}).get("cursor")
        if not cursor:
            break
        params["after"] = cursor

    return all_clips


def _fetch_top_games(headers: dict, count: int = 20) -> dict[str, str]:
    """Fetch top trending games from Twitch. Returns {name: game_id}."""
    resp = requests.get(
        f"{HELIX_BASE}/games/top",
        headers=headers,
        params={"first": count},
        timeout=15,
    )
    resp.raise_for_status()
    return {g["name"]: g["id"] for g in resp.json().get("data", [])}


def _standardize_clip(clip: dict) -> dict:
    """Convert a Twitch Helix clip object to our standardized format."""
    return {
        "id": clip.get("id", ""),
        "title": clip.get("title", ""),
        "url": clip.get("url", ""),
        "duration": clip.get("duration", 0),
        "view_count": clip.get("view_count", 0),
        "streamer": clip.get("broadcaster_name", ""),
        "game": clip.get("game_id", ""),
        "thumbnail_url": clip.get("thumbnail_url", ""),
        "platform": "twitch",
        "created_at": clip.get("created_at", ""),
        "language": clip.get("language", ""),
    }


def _save_to_queue(clips: list[dict], config: dict) -> int:
    """Save clips to the pending queue in SQLite. Returns count of new clips saved.

    Skips clips that have ever been fetched before (tracked in history table).
    """
    from clipper.db import save_clips_batch
    return save_clips_batch(config, clips, status="pending")


def _print_clips_table(clips: list[dict]) -> None:
    """Print a rich table summarizing the clips, ranked by virality score."""
    from clipper.process.score import score_clip

    for clip in clips:
        clip["score"] = score_clip(clip)
    ranked = sorted(clips, key=lambda c: c["score"], reverse=True)

    table = Table(title="Twitch Clips Found (ranked by score)")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Streamer", style="cyan")
    table.add_column("Title", style="white", max_width=35)
    table.add_column("Views", justify="right", style="green")
    table.add_column("Duration", justify="right", style="yellow")

    for clip in ranked:
        s = clip.get("score", 0)
        score_style = "green" if s >= 50 else "yellow" if s >= 30 else "red"
        table.add_row(
            f"[{score_style}]{s:.0f}[/{score_style}]",
            clip["streamer"],
            clip["title"],
            f"{clip['view_count']:,}",
            f"{clip['duration']:.0f}s",
        )

    console.print(table)


def fetch_twitch_clips(
    config: dict,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    discover_mode: bool = False,
    game_priorities: dict[str, float] | None = None,
) -> list[dict]:
    """Fetch top Twitch clips based on config, optionally saving to queue.

    If discover_mode=True, ignores configured games/streamers and sweeps
    top 20 trending games across all of Twitch for the best clips.

    Args:
        game_priorities: Optional dict of {game_name: multiplier} from
            game_stats.json. When in discover mode, proven winners get
            fetched first with higher clip quotas.

    Returns list of standardized clip dicts.
    """
    twitch_cfg = config["targets"]["twitch"]
    settings = config["settings"]

    client_id = require_env("TWITCH_CLIENT_ID")
    client_secret = require_env("TWITCH_CLIENT_SECRET")

    # Authenticate
    if verbose:
        console.print("[dim]Authenticating with Twitch...[/dim]")
    token = _get_app_access_token(client_id, client_secret)
    headers = _helix_headers(client_id, token)

    clips_per_source = twitch_cfg.get("clips_per_source", 10)
    period = twitch_cfg.get("period", "24h")
    started_at = _parse_period(period)
    min_views = settings.get("min_views", 0)
    max_duration = settings.get("max_duration", 300)
    language = settings.get("language", "")

    all_clips: list[dict] = []
    seen_ids: set[str] = set()
    total_fetched = 0

    # Cache of broadcaster_id -> language (filled lazily)
    broadcaster_langs: dict[str, str] = {}

    def _passes_filters(clip: dict) -> bool:
        if clip["id"] in seen_ids:
            return False
        if clip["view_count"] < min_views:
            return False
        if clip["duration"] > max_duration:
            return False
        # Language check uses broadcaster language (reliable) not clip language (unreliable)
        if language:
            bid = clip.get("broadcaster_id", "")
            if bid and bid in broadcaster_langs:
                if broadcaster_langs[bid] != language.lower():
                    return False
        return True

    # -- Discover mode: sweep top trending games --
    if discover_mode:
        console.print("[bold]Discovering top clips across all trending games...[/bold]")
        top_games = _fetch_top_games(headers, count=20)
        console.print(f"  Scanning {len(top_games)} trending games...")

        # Sort games by priority: proven winners first, then by trending rank
        if game_priorities:
            sorted_games = sorted(
                top_games.items(),
                key=lambda x: game_priorities.get(x[0], 1.0),
                reverse=True,
            )
        else:
            sorted_games = list(top_games.items())

        # Three sweeps with decreasing freshness, increasing view requirements:
        # 1h: ultra-fresh clips just starting to pop (very low bar)
        # 3h: clips actively blowing up (low bar)
        # 24h: proven performers (standard bar)
        # The velocity scoring will rank fresh high-velocity clips above stale ones.
        sweeps = [
            ("1h", _parse_period("1h"), max(10, min_views // 10)),  # ultra-fresh, minimal bar
            ("3h", _parse_period("3h"), max(20, min_views // 5)),   # fresh clips
            ("24h", _parse_period("24h"), min_views),                # proven performers
        ]

        for sweep_label, sweep_start, sweep_min_views in sweeps:
            sweep_count = 0
            console.print(f"  [dim]Sweep: last {sweep_label} (min {sweep_min_views} views)...[/dim]")

            for name, gid in sorted_games:
                # Scale clip quota by game priority: winners get more clips
                priority = game_priorities.get(name, 1.0) if game_priorities else 1.0
                game_clip_count = max(20, int(clips_per_source * priority))
                if verbose:
                    priority_label = f" [{priority}x]" if priority != 1.0 else ""
                    console.print(f"[dim]    {name}{priority_label} ({game_clip_count} clips)...[/dim]")
                raw_clips = _fetch_clips(
                    headers,
                    game_id=gid,
                    first=game_clip_count,
                    started_at=sweep_start,
                )
                total_fetched += len(raw_clips)

                if language and raw_clips:
                    unknown_bids = [
                        c["broadcaster_id"] for c in raw_clips
                        if c.get("broadcaster_id") and c["broadcaster_id"] not in broadcaster_langs
                    ]
                    if unknown_bids:
                        resolved = _resolve_broadcaster_languages(list(set(unknown_bids)), headers)
                        broadcaster_langs.update(resolved)

                for clip in raw_clips:
                    if clip["id"] in seen_ids:
                        continue
                    if clip["view_count"] < sweep_min_views:
                        continue
                    if clip["duration"] > max_duration:
                        continue
                    if language:
                        bid = clip.get("broadcaster_id", "")
                        if bid and bid in broadcaster_langs:
                            if broadcaster_langs[bid] != language.lower():
                                continue
                    seen_ids.add(clip["id"])
                    std = _standardize_clip(clip)
                    std["game"] = name
                    all_clips.append(std)
                    sweep_count += 1

            console.print(f"  [dim]{sweep_label}: {sweep_count} clips passed filters[/dim]")

        console.print(f"[bold]Discover:[/bold] {total_fetched} fetched → {len(all_clips)} passed filters across {len(top_games)} games")

        if not all_clips:
            return all_clips

        if dry_run:
            _print_clips_table(all_clips)
        else:
            saved = _save_to_queue(all_clips, config)
            console.print(f"  Saved {saved} new clip(s) to queue ({len(all_clips) - saved} already seen)")

        return all_clips

    # -- Configured mode: fetch by streamer --
    streamers = twitch_cfg.get("streamers", [])
    game_filter = str(twitch_cfg.get("game_filter", "") or "").strip()
    filter_game_id = ""
    if game_filter:
        gid_map = _resolve_game_ids([game_filter], headers)
        filter_game_id = gid_map.get(game_filter, "")
        if filter_game_id:
            console.print(f"[dim]  Game filter: '{game_filter}' → game_id={filter_game_id}[/dim]")
        else:
            console.print(f"[yellow]  Warning: could not resolve game '{game_filter}' — no game filter applied[/yellow]")

    if streamers:
        if verbose:
            console.print(f"[dim]Resolving {len(streamers)} streamer(s)...[/dim]")
        user_ids = _resolve_user_ids([str(s) for s in streamers], headers)

        game_filtered = 0
        for name, uid in user_ids.items():
            if verbose:
                console.print(f"[dim]  Fetching clips for {name}...[/dim]")
            raw_clips = _fetch_clips(
                headers,
                broadcaster_id=uid,
                first=clips_per_source,
                started_at=started_at,
            )
            total_fetched += len(raw_clips)
            for clip in raw_clips:
                if filter_game_id and clip.get("game_id") != filter_game_id:
                    game_filtered += 1
                    continue
                if not _passes_filters(clip):
                    continue
                seen_ids.add(clip["id"])
                std = _standardize_clip(clip)
                if game_filter:
                    std["game"] = game_filter
                all_clips.append(std)

        if filter_game_id and game_filtered > 0:
            console.print(f"[dim]  Game filter: {game_filtered} non-{game_filter} clips dropped[/dim]")

    # -- Fetch by game --
    games = twitch_cfg.get("games", [])
    if games:
        if verbose:
            console.print(f"[dim]Resolving {len(games)} game(s)...[/dim]")
        game_ids = _resolve_game_ids([str(g) for g in games], headers)

        for name, gid in game_ids.items():
            if verbose:
                console.print(f"[dim]  Fetching clips for game: {name}...[/dim]")
            raw_clips = _fetch_clips(
                headers,
                game_id=gid,
                first=clips_per_source,
                started_at=started_at,
            )
            total_fetched += len(raw_clips)

            # Bulk-resolve broadcaster languages for language filtering
            if language and raw_clips:
                unknown_bids = [
                    c["broadcaster_id"] for c in raw_clips
                    if c.get("broadcaster_id") and c["broadcaster_id"] not in broadcaster_langs
                ]
                if unknown_bids:
                    resolved = _resolve_broadcaster_languages(list(set(unknown_bids)), headers)
                    broadcaster_langs.update(resolved)

            for clip in raw_clips:
                if not _passes_filters(clip):
                    continue
                seen_ids.add(clip["id"])
                std = _standardize_clip(clip)
                # Override game with resolved name since Helix only gives game_id
                std["game"] = name
                all_clips.append(std)

    console.print(f"[bold]Twitch:[/bold] {total_fetched} fetched → {len(all_clips)} passed filters (min {min_views} views, max {max_duration}s)")

    if not all_clips:
        return all_clips

    if dry_run:
        _print_clips_table(all_clips)
    else:
        saved = _save_to_queue(all_clips, config)
        console.print(f"  Saved {saved} new clip(s) to queue ({len(all_clips) - saved} already queued)")

    return all_clips
