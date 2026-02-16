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


def _opportunity_tier(score: float) -> str:
    """Map gap score to a readable opportunity tier."""
    if score >= 0.4:
        return "high"
    if score >= 0.2:
        return "medium"
    return "low"


def analyze_gaps(config: dict, limit: int = 20) -> dict:
    """Return structured gap analysis for web/CLI consumption."""
    try:
        _, headers = _get_twitch_auth()
        twitch_data = fetch_twitch_trending(headers)
    except Exception as e:
        return {
            "gaps": [],
            "degraded": True,
            "youtube_available": False,
            "detail": f"Twitch API error: {e}",
        }

    if not twitch_data:
        return {
            "gaps": [],
            "degraded": False,
            "youtube_available": False,
            "detail": "No Twitch trending data to analyze.",
        }

    youtube = None
    youtube_error: str | None = None
    try:
        from clipper.upload.auth import get_youtube_service

        youtube = get_youtube_service()
    except Exception as e:
        youtube_error = str(e)

    results = []
    for entry in twitch_data[:limit]:
        game_name = entry["game_name"]
        twitch_clips = int(entry["clip_count"])
        twitch_views = int(entry["total_views"])

        yt_videos = 0
        yt_views = 0
        if youtube:
            try:
                yt_videos, yt_views = _get_youtube_competition(game_name, youtube)
            except Exception:
                yt_videos, yt_views = 0, 0

        # Weighted competition model:
        # - yt_views captures demand saturation
        # - yt_videos captures content density
        # Normalize to [0..1+] for easier sorting/UX.
        competition = yt_views + (yt_videos * 500)
        supply = twitch_views + (twitch_clips * 250)
        gap_score = supply / max(competition + supply, 1)

        results.append(
            {
                "game_name": game_name,
                "twitch_clips": twitch_clips,
                "twitch_views": twitch_views,
                "yt_videos": int(yt_videos),
                "yt_views": int(yt_views),
                "gap_score": round(gap_score, 4),
                "opportunity": _opportunity_tier(gap_score),
            }
        )

    results.sort(key=lambda x: x["gap_score"], reverse=True)
    return {
        "gaps": results,
        "degraded": False,
        "youtube_available": youtube is not None,
        "detail": (
            f"YouTube competition unavailable: {youtube_error}"
            if youtube is None and youtube_error
            else None
        ),
    }


def show_gaps(config: dict) -> None:
    """Display niche gap analysis: Twitch engagement vs YouTube competition."""
    console.print("[bold]Analyzing content gaps...[/bold]\n")

    payload = analyze_gaps(config)
    rows = payload.get("gaps", [])
    if not rows:
        msg = payload.get("detail", "No content gaps found.")
        style = "red" if payload.get("degraded") else "yellow"
        console.print(f"[{style}]{msg}[/{style}]")
        return

    if payload.get("detail"):
        console.print(f"[yellow]{payload['detail']}[/yellow]\n")

    table = Table(title="Content Gap Analysis")
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Game", style="cyan")
    table.add_column("Twitch Clips (24h)", justify="right", style="green")
    table.add_column("Twitch Views", justify="right", style="bold green")
    table.add_column("YT Competition", justify="right", style="red")
    table.add_column("Gap Score", justify="right", style="bold yellow")
    table.add_column("Opportunity", justify="right")

    youtube_available = payload.get("youtube_available", False)
    for rank, entry in enumerate(rows, 1):
        yt_label = f"{entry['yt_videos']} videos" if youtube_available else "-"
        table.add_row(
            str(rank),
            entry["game_name"],
            str(entry["twitch_clips"]),
            f"{entry['twitch_views']:,}",
            yt_label,
            f"{entry['gap_score']:.2f}",
            entry.get("opportunity", "low"),
        )

    console.print(table)
