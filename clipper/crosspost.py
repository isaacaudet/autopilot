"""Daily cross-post: post top YouTube clip to Instagram (game-filtered, two channels).

Run manually: python -m clipper.crosspost
Run via launchd: installed by `clipper cron --install-crosspost`
"""

import logging
from pathlib import Path

from rich.console import Console

from clipper.config import load_config
from clipper.db import get_db, update_clip
from clipper.upload import instagram

logger = logging.getLogger(__name__)
console = Console()

def _get_crosspost_channels(config: dict) -> list[dict]:
    """Build crosspost channel list from config — one entry per Instagram channel with its game."""
    channels = config.get("channels", {})
    result = []
    for key, ch in channels.items():
        if ch.get("platform") == "instagram" and ch.get("game"):
            result.append({"instagram": key, "game": ch["game"]})
    return result


def _get_next_clip(conn, config: dict, channel: str, game: str) -> dict | None:
    """Get highest-YT-views clip for this game not yet scheduled to this channel."""
    crosspost_cfg = config.get("crosspost", {}) or {}
    min_yt_views = int(crosspost_cfg.get("min_yt_views", 10000))

    row = conn.execute("""
        SELECT c.id, c.title, c.streamer, c.game, c.duration, c.processed_path,
               c.view_count, c.score, c.video_id,
               COALESCE(CAST(json_extract(p.youtube, '$.views') AS INTEGER), 0) as yt_views
        FROM clips c
        LEFT JOIN performance p ON c.id = p.clip_id
        WHERE c.video_id IS NOT NULL
          AND c.processed_path IS NOT NULL
          AND c.game LIKE ?
          AND COALESCE(CAST(json_extract(p.youtube, '$.views') AS INTEGER), 0) >= ?
          AND (c.instagram_id IS NULL OR c.instagram_id = '')
          AND c.id NOT IN (
              SELECT clip_id FROM releases
              WHERE channel = ?
              AND status IN ('pending','uploaded','executing','published','scheduled')
          )
        ORDER BY yt_views DESC
        LIMIT 1
    """, (f"%{game}%", min_yt_views, channel)).fetchone()
    return dict(row) if row else None


def run_crosspost(config: dict | None = None, *, verbose: bool = False) -> dict:
    if config is None:
        config = load_config()
    conn = get_db(config)
    summary = {}

    crosspost_channels = _get_crosspost_channels(config)
    for entry in crosspost_channels:
        ig_channel = entry["instagram"]
        game = entry["game"]

        clip = _get_next_clip(conn, config, ig_channel, game)
        if not clip:
            console.print(f"[dim]{ig_channel}: no qualifying {game} clips (10k+ YT views)[/dim]")
            summary[ig_channel] = None
            continue

        if not Path(clip["processed_path"]).exists():
            console.print(f"[red]{ig_channel}: file missing for {clip['title'][:40]}[/red]")
            summary[ig_channel] = None
            continue

        console.print(
            f"\n[bold]{ig_channel}:[/bold] {clip['streamer']} — {clip['title'][:50]}\n"
            f"  yt_views={clip['yt_views']:,} | {clip['duration']:.0f}s"
        )

        iid = instagram.upload_clip(clip, config, channel=ig_channel)
        if iid:
            update_clip(config, clip["id"], instagram_id=iid)
            console.print(f"[green]  Posted:[/green] {iid}")
            summary[ig_channel] = iid
        else:
            console.print(f"[red]  Upload failed[/red]")
            summary[ig_channel] = None

    return summary


if __name__ == "__main__":
    run_crosspost()
