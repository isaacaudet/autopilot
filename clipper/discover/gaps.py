"""Analyze niche gaps: high clip activity on Twitch, low YouTube competition."""

from rich.console import Console
from rich.table import Table

from clipper.discover.trends import _get_twitch_auth, fetch_twitch_trending

console = Console()


def _get_youtube_competition(game_name: str, youtube) -> tuple[int, int]:
    """Search YouTube for existing clip channels for a game.

    Returns (video_count, total_view_count) as a proxy for competition.
    """
    request = youtube.search().list(
        q=f"{game_name} clips",
        type="video",
        maxResults=10,
        order="viewCount",
    )
    response = request.execute()
    items = response.get("items", [])
    video_count = len(items)

    # Get view counts for these videos
    if not items:
        return 0, 0

    video_ids = [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]
    if not video_ids:
        return video_count, 0

    stats_request = youtube.videos().list(
        part="statistics",
        id=",".join(video_ids),
    )
    stats_response = stats_request.execute()

    total_views = 0
    for video in stats_response.get("items", []):
        total_views += int(video.get("statistics", {}).get("viewCount", 0))

    return video_count, total_views


def show_gaps(config: dict) -> None:
    """Display niche gap analysis: Twitch engagement vs YouTube competition."""
    console.print("[bold]Analyzing content gaps...[/bold]\n")

    # Fetch Twitch trending data
    try:
        _, headers = _get_twitch_auth()
        twitch_data = fetch_twitch_trending(headers)
    except Exception as e:
        console.print(f"[red]Twitch API error: {e}[/red]")
        return

    if not twitch_data:
        console.print("[yellow]No Twitch trending data to analyze.[/yellow]")
        return

    # Try to get YouTube service for competition analysis
    youtube = None
    try:
        from clipper.upload.auth import get_youtube_service

        youtube = get_youtube_service()
    except Exception as e:
        console.print(
            f"[yellow]YouTube API not available ({e}). "
            "Showing Twitch data only.[/yellow]\n"
        )

    results = []
    for entry in twitch_data:
        game_name = entry["game_name"]
        twitch_clips = entry["clip_count"]
        twitch_views = entry["total_views"]

        yt_videos = 0
        yt_views = 0

        if youtube:
            try:
                yt_videos, yt_views = _get_youtube_competition(game_name, youtube)
            except Exception:
                pass  # Skip YouTube data for this game on error

        # Gap score: Twitch engagement / YouTube competition
        # Higher = more underserved niche
        gap_score = twitch_views / (yt_views + 1)

        results.append(
            {
                "game_name": game_name,
                "twitch_clips": twitch_clips,
                "twitch_views": twitch_views,
                "yt_videos": yt_videos,
                "yt_views": yt_views,
                "gap_score": gap_score,
            }
        )

    # Sort by gap score descending
    results.sort(key=lambda x: x["gap_score"], reverse=True)

    table = Table(title="Content Gap Analysis")
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Game", style="cyan")
    table.add_column("Twitch Clips (24h)", justify="right", style="green")
    table.add_column("Twitch Views", justify="right", style="bold green")
    table.add_column("YT Competition", justify="right", style="red")
    table.add_column("Gap Score", justify="right", style="bold yellow")

    for rank, entry in enumerate(results, 1):
        yt_label = f"{entry['yt_videos']} videos" if youtube else "-"
        table.add_row(
            str(rank),
            entry["game_name"],
            str(entry["twitch_clips"]),
            f"{entry['twitch_views']:,}",
            yt_label,
            f"{entry['gap_score']:.2f}",
        )

    console.print(table)
