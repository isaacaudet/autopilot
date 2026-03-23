"""Facebook Reels upload via Graph API v21.0.

Requires:
  - META_APP_ID and META_APP_SECRET in .env
  - Page Access Token in channel's token file (obtained via ``clipper auth -c <channel>``)

Duration: Facebook Reels require 4-60 seconds.
"""

import json
import logging
from pathlib import Path

import requests
from rich.console import Console

from clipper.config import get_project_root

logger = logging.getLogger(__name__)
console = Console()

GRAPH_API = "https://graph.facebook.com/v21.0"


def _token_path_for_channel(channel: str | None, config: dict) -> Path:
    channels = config.get("channels", {})
    ch = channels.get(channel or "", {})
    token_file = ch.get("token_file", f".clipper_facebook_{channel}.json")
    return get_project_root() / token_file


def _load_token(token_path: Path) -> dict:
    if not token_path.exists():
        raise RuntimeError(
            f"Facebook token missing: {token_path.name}. "
            "Run `clipper auth -c <channel>` to authenticate."
        )
    return json.loads(token_path.read_text())


def _build_description(clip: dict, config: dict) -> str:
    """Build a Facebook Reels description from clip metadata."""
    title = clip.get("_title_override") or clip.get("title", "")
    title = title.replace(" #Shorts", "").strip()

    streamer = clip.get("streamer", "")
    game = clip.get("game", "")

    hashtags = []
    if game:
        hashtags.append(f"#{game.replace(' ', '').lower()}")
    if streamer:
        hashtags.append(f"#{streamer.replace(' ', '').lower()}")
    hashtags.extend(["#gaming", "#clips", "#reels"])

    return f"{title}\n\n{' '.join(hashtags)}"


def upload_clip(clip, config, privacy="unlisted", verbose=False, channel=None, publish_at=None) -> str | None:
    """Upload a clip as a Facebook Reel. Returns the video_id on success.

    publish_at: UTC ISO8601 string (e.g. "2026-03-04T15:00:00Z"). When set,
    uses video_state=SCHEDULED with scheduled_publish_time (Unix timestamp).
    """
    processed_path = clip.get("processed_path")
    if not processed_path or not Path(processed_path).exists():
        console.print(f"[red]Missing processed file: {processed_path}[/red]")
        return None

    duration = clip.get("duration", 0)
    if duration < 4 or duration > 60:
        console.print(f"[yellow]Skipping Facebook Reel: duration {duration:.1f}s outside 4-60s range.[/yellow]")
        return None

    token_path = _token_path_for_channel(channel, config)
    token_data = _load_token(token_path)
    page_id = token_data.get("page_id")
    page_token = token_data.get("page_access_token")

    if not page_id or not page_token:
        raise RuntimeError("Facebook token file missing page_id or page_access_token.")

    # Warn if user token is close to expiry (page tokens are non-expiring,
    # but the underlying user token expires in ~60 days and must be refreshed via re-auth)
    expires_at = token_data.get("expires_at", 0)
    import time as _time
    days_left = (expires_at - _time.time()) / 86400
    if expires_at and days_left < 14:
        console.print(f"[yellow]⚠ Facebook token expires in {days_left:.0f} days — run `clipper auth -c {channel or 'facebook_main'}` to refresh.[/yellow]")

    description = clip.get("_description_override") or _build_description(clip, config)

    # Determine publish state and optional scheduled timestamp
    unix_ts: int | None = None
    if publish_at:
        from datetime import datetime
        dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        unix_ts = int(dt.timestamp())
        video_state = "SCHEDULED"
    elif privacy == "public":
        video_state = "PUBLISHED"
    else:
        video_state = "DRAFT"

    try:
        # Step 1: Start upload session
        schedule_str = f" (scheduled {publish_at})" if publish_at else ""
        console.print(f"  [bold]Uploading Facebook Reel{schedule_str}...[/bold]")
        start_resp = requests.post(
            f"{GRAPH_API}/{page_id}/video_reels",
            params={
                "upload_phase": "start",
                "access_token": page_token,
            },
            timeout=30,
        )
        start_resp.raise_for_status()
        start_data = start_resp.json()
        video_id = start_data.get("video_id")

        if not video_id:
            console.print(f"[red]Facebook start failed: {start_data}[/red]")
            return None

        # Step 2: Upload binary
        file_size = Path(processed_path).stat().st_size
        with open(processed_path, "rb") as f:
            upload_resp = requests.post(
                f"https://rupload.facebook.com/video-upload/v21.0/{video_id}",
                headers={
                    "Authorization": f"OAuth {page_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                },
                data=f,
                timeout=300,
            )
            upload_resp.raise_for_status()

        # Step 3: Finish upload
        finish_params: dict = {
            "upload_phase": "finish",
            "access_token": page_token,
            "video_id": video_id,
            "video_state": video_state,
            "description": description,
        }
        if unix_ts:
            finish_params["scheduled_publish_time"] = str(unix_ts)

        finish_resp = requests.post(
            f"{GRAPH_API}/{page_id}/video_reels",
            params=finish_params,
            timeout=30,
        )
        finish_resp.raise_for_status()

        if publish_at:
            console.print(f"[green]Facebook Reel scheduled:[/green] {video_id} → {publish_at}")
        else:
            console.print(f"[green]Facebook Reel uploaded:[/green] {video_id}")
        return video_id

    except Exception as e:
        console.print(f"[red]Facebook upload error: {e}[/red]")
        clip["_upload_error_reason"] = "facebook_error"
        clip["_upload_error_message"] = str(e)
        clip["_upload_error_status"] = None
        return None


def publish_video(video_id, verbose=False, *, channel=None, config=None) -> bool:
    """Publish a draft Facebook Reel (re-send finish with PUBLISHED state)."""
    if not config or not channel:
        logger.error("publish_video requires config and channel for Facebook")
        return False

    token_path = _token_path_for_channel(channel, config)
    token_data = _load_token(token_path)
    page_id = token_data.get("page_id")
    page_token = token_data.get("page_access_token")

    if not page_id or not page_token:
        return False

    try:
        resp = requests.post(
            f"{GRAPH_API}/{page_id}/video_reels",
            params={
                "upload_phase": "finish",
                "access_token": page_token,
                "video_id": video_id,
                "video_state": "PUBLISHED",
            },
            timeout=30,
        )
        resp.raise_for_status()
        console.print(f"[green]Facebook Reel published:[/green] {video_id}")
        return True
    except Exception as e:
        console.print(f"[red]Facebook publish error: {e}[/red]")
        return False
