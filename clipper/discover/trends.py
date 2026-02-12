"""Discover trending games/categories by clip volume across platforms."""

from datetime import datetime, timedelta, timezone

import requests
from rich.console import Console
from rich.table import Table

from clipper.config import require_env

HELIX_BASE = "https://api.twitch.tv/helix"

console = Console()


def _get_twitch_auth() -> tuple[str, dict]:
    """Authenticate with Twitch and return (client_id, headers)."""
    client_id = require_env("TWITCH_CLIENT_ID")
    client_secret = require_env("TWITCH_CLIENT_SECRET")

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
    token = resp.json()["access_token"]

    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }
    return client_id, headers


def fetch_twitch_trending(headers: dict) -> list[dict]:
    """Fetch top games from Twitch and their clip stats for the last 24h.

    Returns list of dicts with keys:
        game_id, game_name, clip_count, total_views, avg_views
    """
    # Get top 20 games
    resp = requests.get(
        f"{HELIX_BASE}/games/top",
        headers=headers,
        params={"first": 20},
        timeout=15,
    )
    resp.raise_for_status()
    top_games = resp.json().get("data", [])

    started_at = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    results = []
    for game in top_games:
        game_id = game["id"]
        game_name = game["name"]

        # Fetch recent clips for this game
        clip_resp = requests.get(
            f"{HELIX_BASE}/clips",
            headers=headers,
            params={
                "game_id": game_id,
                "first": 20,
                "started_at": started_at,
            },
            timeout=15,
        )
        clip_resp.raise_for_status()
        clips = clip_resp.json().get("data", [])

        clip_count = len(clips)
        total_views = sum(c.get("view_count", 0) for c in clips)
        avg_views = total_views // clip_count if clip_count > 0 else 0

        results.append(
            {
                "game_id": game_id,
                "game_name": game_name,
                "platform": "Twitch",
                "clip_count": clip_count,
                "total_views": total_views,
                "avg_views": avg_views,
            }
        )

    return results


def _fetch_kick_trending() -> list[dict]:
    """Fetch trending categories from Kick (public API)."""
    try:
        resp = requests.get(
            "https://kick.com/api/v2/categories",
            timeout=15,
        )
        resp.raise_for_status()
        categories = resp.json()
    except (requests.RequestException, ValueError):
        console.print("[yellow]Could not fetch Kick categories.[/yellow]")
        return []

    results = []
    # Kick categories may have varying schemas; extract what we can
    for cat in categories[:20]:
        name = cat.get("name", cat.get("slug", "Unknown"))
        # Kick doesn't expose per-category clip stats in the same way,
        # so we use viewer count as a proxy if available
        viewers = cat.get("viewers", 0)
        results.append(
            {
                "game_id": str(cat.get("id", "")),
                "game_name": name,
                "platform": "Kick",
                "clip_count": 0,
                "total_views": viewers,
                "avg_views": 0,
            }
        )

    return results


def show_trending(config: dict) -> None:
    """Display a table of trending games/categories sorted by engagement."""
    console.print("[bold]Fetching trending data...[/bold]\n")

    # Twitch
    try:
        _, headers = _get_twitch_auth()
        twitch_data = fetch_twitch_trending(headers)
    except Exception as e:
        console.print(f"[red]Twitch API error: {e}[/red]")
        twitch_data = []

    # Kick
    kick_data = _fetch_kick_trending()

    combined = twitch_data + kick_data
    if not combined:
        console.print("[yellow]No trending data available.[/yellow]")
        return

    # Sort by total views descending
    combined.sort(key=lambda x: x["total_views"], reverse=True)

    table = Table(title="Trending Games & Categories")
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Game/Category", style="cyan")
    table.add_column("Platform", style="magenta")
    table.add_column("Clips (24h)", justify="right", style="green")
    table.add_column("Total Views", justify="right", style="bold green")
    table.add_column("Avg Views", justify="right", style="yellow")

    for rank, entry in enumerate(combined, 1):
        clip_count = str(entry["clip_count"]) if entry["clip_count"] > 0 else "-"
        table.add_row(
            str(rank),
            entry["game_name"],
            entry["platform"],
            clip_count,
            f"{entry['total_views']:,}",
            f"{entry['avg_views']:,}" if entry["avg_views"] > 0 else "-",
        )

    console.print(table)
