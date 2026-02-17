"""Instagram Reels upload via Graph API v21.0.

Requires:
  - META_APP_ID and META_APP_SECRET in .env
  - Token file with ig_user_id + long-lived token (via ``clipper auth -c <channel>``)

Instagram pulls the video by URL — cannot upload binary directly. The video
must be accessible via a public URL. Uses the existing ``/api/video/{clip_id}``
endpoint. Set ``instagram.video_base_url`` in config.yaml to the public base
URL (e.g. ngrok URL for dev, domain for prod).

Container expires after 24h — don't create speculatively.
Max 30 hashtags, max 2200 chars caption.
"""

import json
import logging
import time
from pathlib import Path

import requests
from rich.console import Console

from clipper.config import get_project_root, require_env

logger = logging.getLogger(__name__)
console = Console()

GRAPH_API = "https://graph.facebook.com/v21.0"


def _token_path_for_channel(channel: str | None, config: dict) -> Path:
    channels = config.get("channels", {})
    ch = channels.get(channel or "", {})
    token_file = ch.get("token_file", f".clipper_instagram_{channel}.json")
    return get_project_root() / token_file


def _load_token(token_path: Path) -> dict:
    if not token_path.exists():
        raise RuntimeError(
            f"Instagram token missing: {token_path.name}. "
            "Run `clipper auth -c <channel>` to authenticate."
        )
    return json.loads(token_path.read_text())


def _refresh_token_if_needed(token_data: dict, token_path: Path) -> str:
    """Return a valid access token, refreshing long-lived token if near expiry."""
    expires_at = token_data.get("expires_at", 0)
    if time.time() < expires_at - 86400:  # refresh if <1 day left
        return token_data["access_token"]

    # Long-lived tokens can be refreshed before they expire
    try:
        resp = requests.get(
            f"{GRAPH_API}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": require_env("META_APP_ID"),
                "client_secret": require_env("META_APP_SECRET"),
                "fb_exchange_token": token_data["access_token"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token_data["access_token"] = data["access_token"]
        token_data["expires_at"] = time.time() + data.get("expires_in", 5184000)
        token_path.write_text(json.dumps(token_data, indent=2))
        logger.info("Instagram token refreshed")
    except Exception as e:
        logger.warning("Instagram token refresh failed (may still be valid): %s", e)

    return token_data["access_token"]


def _build_caption(clip: dict, config: dict) -> str:
    """Build an Instagram Reels caption (max 2200 chars, max 30 hashtags)."""
    title = clip.get("_title_override") or clip.get("title", "")
    title = title.replace(" #Shorts", "").strip()

    streamer = clip.get("streamer", "")
    game = clip.get("game", "")

    hashtags = []
    if game:
        hashtags.append(f"#{game.replace(' ', '').lower()}")
    if streamer:
        hashtags.append(f"#{streamer.replace(' ', '').lower()}")
    hashtags.extend(["#gaming", "#clips", "#reels", "#fyp", "#viral"])

    # Cap at 30 hashtags (Instagram limit)
    hashtags = hashtags[:30]
    caption = f"{title}\n\n{' '.join(hashtags)}"
    return caption[:2200]


def _get_video_url(clip: dict, config: dict) -> str:
    """Build the public URL for the clip video.

    Requires ``instagram.video_base_url`` in config.yaml.
    """
    base_url = config.get("instagram", {}).get("video_base_url", "")
    if not base_url:
        raise RuntimeError(
            "instagram.video_base_url not set in config.yaml. "
            "Set it to a public URL (e.g. ngrok URL) where the API is accessible."
        )
    clip_id = clip.get("id", "")
    return f"{base_url.rstrip('/')}/api/video/{clip_id}"


def _poll_container_status(access_token: str, container_id: str, max_wait: int = 600) -> bool:
    """Poll container status until FINISHED or ERROR. Returns True on success."""
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(
            f"{GRAPH_API}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=15,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")

        if status == "FINISHED":
            return True
        if status == "ERROR":
            logger.error("Instagram container failed: %s", resp.json())
            return False

        time.sleep(10)

    logger.error("Instagram container timed out after %ds", max_wait)
    return False


def upload_clip(clip, config, privacy="unlisted", verbose=False, channel=None) -> str | None:
    """Upload a clip as an Instagram Reel. Returns the media_id on success."""
    processed_path = clip.get("processed_path")
    if not processed_path or not Path(processed_path).exists():
        console.print(f"[red]Missing processed file: {processed_path}[/red]")
        return None

    token_path = _token_path_for_channel(channel, config)
    token_data = _load_token(token_path)
    access_token = _refresh_token_if_needed(token_data, token_path)
    ig_user_id = token_data.get("ig_user_id")

    if not ig_user_id:
        raise RuntimeError("Instagram token file missing ig_user_id.")

    video_url = _get_video_url(clip, config)
    caption = clip.get("_description_override") or _build_caption(clip, config)

    try:
        # Step 1: Create media container
        console.print("  [bold]Creating Instagram Reel container...[/bold]")
        create_resp = requests.post(
            f"{GRAPH_API}/{ig_user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": access_token,
            },
            timeout=30,
        )
        create_resp.raise_for_status()
        container_id = create_resp.json().get("id")

        if not container_id:
            console.print(f"[red]Instagram container creation failed: {create_resp.json()}[/red]")
            return None

        # Step 2: Poll until container is ready
        console.print("  [dim]Waiting for Instagram processing...[/dim]")
        if not _poll_container_status(access_token, container_id):
            console.print("[red]Instagram container failed or timed out.[/red]")
            return None

        # Step 3: Publish the container
        publish_resp = requests.post(
            f"{GRAPH_API}/{ig_user_id}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": access_token,
            },
            timeout=30,
        )
        publish_resp.raise_for_status()
        media_id = publish_resp.json().get("id")

        console.print(f"[green]Instagram Reel published:[/green] {media_id}")
        return media_id

    except Exception as e:
        console.print(f"[red]Instagram upload error: {e}[/red]")
        clip["_upload_error_reason"] = "instagram_error"
        clip["_upload_error_message"] = str(e)
        clip["_upload_error_status"] = None
        return None


def publish_video(video_id, verbose=False, *, channel=None, config=None) -> bool:
    """No-op — Instagram publishes during the upload step."""
    return True
