"""Instagram Reels upload via Graph API v21.0.

Requires:
  - INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET in .env
  - Token file with ig_user_id + long-lived token (via ``clipper auth -c <channel>``)

Upload flow (binary — no public URL needed):
  1. Create container with upload_type=resumable → get container_id + upload_uri
  2. POST video binary to upload_uri (Meta's resumable upload endpoint)
  3. Poll container status until FINISHED
  4. Publish via media_publish

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

GRAPH_API_FB = "https://graph.facebook.com/v21.0"   # Facebook Login for Business (legacy)
GRAPH_API_IG = "https://graph.instagram.com/v21.0"  # Instagram Login (current)


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


def _api_base(token_data: dict) -> str:
    """Return the correct Graph API base URL for this token's login type."""
    if token_data.get("login_type") == "instagram_login":
        return GRAPH_API_IG
    return GRAPH_API_FB


def _refresh_token_if_needed(token_data: dict, token_path: Path) -> str:
    """Return a valid access token, refreshing long-lived token if near expiry."""
    expires_at = token_data.get("expires_at", 0)
    if time.time() < expires_at - 86400:  # refresh if <1 day left
        return token_data["access_token"]

    try:
        if token_data.get("login_type") == "instagram_login":
            # Instagram Login: refresh via graph.instagram.com
            resp = requests.get(
                "https://graph.instagram.com/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": token_data["access_token"],
                },
                timeout=15,
            )
        else:
            # Facebook Login for Business (legacy)
            resp = requests.get(
                f"{GRAPH_API_FB}/oauth/access_token",
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


_IG_CAPTION_PROMPT = """\
You write viral Instagram Reels captions for gaming clips.

Clip metadata:
- Streamer: {streamer}
- Game: {game}
- Title: {title}
- Duration: {duration}s
- Views: {views}

Write ONE short, punchy hook sentence (max 12 words) that makes someone stop scrolling.
Focus on the action/emotion, NOT the streamer name or game title.
Do NOT use quotation marks. Do NOT start with "Watch" or "Check out".
Examples of good hooks: "The reaction says everything 😭", "No way that just happened 💀", "This is why he's top 500"

Respond with ONLY the hook sentence, nothing else."""


def _gemini_hook(clip: dict) -> str | None:
    """Generate an Instagram hook sentence via Gemini. Returns None on failure."""
    try:
        from clipper.process.analyze import _get_gemini_client, _get_gemini_model
        client = _get_gemini_client()
        if not client:
            return None
        title = (clip.get("_title_override") or clip.get("title", "")).replace(" #Shorts", "").strip()
        prompt = _IG_CAPTION_PROMPT.format(
            streamer=clip.get("streamer", "unknown"),
            game=clip.get("game", "unknown"),
            title=title,
            duration=int(float(clip.get("duration", 0) or 0)),
            views=clip.get("view_count", 0),
        )
        resp = client.models.generate_content(model=_get_gemini_model(), contents=prompt)
        hook = (resp.text or "").strip().strip('"').strip("'")
        return hook if hook else None
    except Exception as e:
        logger.warning("Gemini caption generation failed: %s", e)
        return None


def _build_caption(clip: dict, config: dict) -> str:
    """Build an Instagram Reels caption (max 2200 chars, no hashtags).

    Instagram's algorithm no longer rewards hashtag spam — clean captions
    with a strong hook sentence perform better for Reels discovery.
    """
    title = clip.get("_title_override") or clip.get("title", "")
    title = title.replace(" #Shorts", "").strip()

    # Try Gemini-generated hook first
    hook = _gemini_hook(clip)
    caption = hook if hook else title
    return caption[:2200]


_TMPFILES_MAX_BYTES = 90 * 1024 * 1024  # 90 MB — tmpfiles.org hard limit is 100 MB


def _compress_for_upload(video_path: str) -> str:
    """Re-encode video to fit tmpfiles.org 100 MB limit. Returns path to compressed copy."""
    import subprocess, tempfile, os
    from clipper.config import get_ffmpeg
    ffmpeg = get_ffmpeg()
    out = video_path.replace(".mp4", "_ig_compressed.mp4")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(
        [ffmpeg, "-y", "-i", video_path,
         "-c:v", "libx264", "-crf", "28", "-preset", "fast",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", out],
        check=True, capture_output=True,
    )
    return out


def _upload_to_temp_host(video_path: str) -> str | None:
    """Upload video to tmpfiles.org and return the public HTTPS direct-download URL.

    tmpfiles.org: free, no sign-up, files available for ~24h.
    Instagram API (graph.instagram.com) requires a public URL — it cannot accept
    binary uploads directly.
    """
    upload_path = video_path
    compressed = None
    try:
        if Path(video_path).stat().st_size > _TMPFILES_MAX_BYTES:
            console.print("  [yellow]File >90 MB — compressing for Instagram upload...[/yellow]")
            try:
                compressed = _compress_for_upload(video_path)
                upload_path = compressed
                console.print(f"  [dim]Compressed: {Path(compressed).stat().st_size // (1024*1024)} MB[/dim]")
            except Exception as ce:
                logger.warning("Compression failed, trying original: %s", ce)
        with open(upload_path, "rb") as f:
            resp = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (Path(video_path).name, f, "video/mp4")},
                timeout=180,
            )
        if resp.status_code == 200:
            data = resp.json()
            page_url = data.get("data", {}).get("url", "")
            # Convert page URL to direct download URL: /123/file.mp4 → /dl/123/file.mp4
            import re
            direct_url = re.sub(r"https?://tmpfiles\.org/(\d+/)", r"https://tmpfiles.org/dl/\1", page_url)
            logger.info("Uploaded to tmpfiles.org: %s", direct_url)
            return direct_url
        logger.error("tmpfiles.org upload failed %d: %s", resp.status_code, resp.text[:200])
        return None
    except Exception as e:
        logger.error("tmpfiles.org upload exception: %s", e)
        return None
    finally:
        if compressed and Path(compressed).exists():
            try:
                Path(compressed).unlink()
            except Exception:
                pass


def _poll_container_status(access_token: str, container_id: str, max_wait: int = 600, api: str = GRAPH_API_FB) -> bool:
    """Poll container status until FINISHED or ERROR. Returns True on success."""
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(
            f"{api}/{container_id}",
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


def upload_clip(clip, config, privacy="unlisted", verbose=False, channel=None, publish_at=None) -> str | None:
    """Upload a clip as an Instagram Reel. Returns the media_id on success.

    publish_at: UTC ISO8601 string (e.g. "2026-03-04T15:00:00Z"). When set,
    the Reel is scheduled — Meta auto-publishes at that time, no media_publish
    call needed. Must be 10 min to 75 days in the future.
    """
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

    caption = clip.get("_description_override") or _build_caption(clip, config)
    api = _api_base(token_data)

    # Convert UTC ISO8601 → Unix timestamp for Meta API
    unix_ts: int | None = None
    if publish_at:
        from datetime import datetime
        dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        unix_ts = int(dt.timestamp())

    try:
        # Step 1: Upload video to transfer.sh to get a temporary public URL.
        # Instagram API (graph.instagram.com) requires a public URL — it cannot
        # accept binary upload directly. transfer.sh is free, no sign-up, 1-day expiry.
        schedule_str = f" (scheduled {publish_at})" if publish_at else ""
        console.print(f"  [bold]Uploading to temp host for public URL...[/bold]")
        video_url = _upload_to_temp_host(processed_path)
        if not video_url:
            console.print("[red]Failed to get public URL via transfer.sh.[/red]")
            return None
        console.print(f"  [dim]Public URL: {video_url}[/dim]")

        # Step 2: Create media container
        console.print(f"  [bold]Creating Instagram Reel container{schedule_str}...[/bold]")
        container_params: dict = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": access_token,
        }
        if unix_ts:
            container_params["published"] = "false"
            container_params["scheduled_publish_time"] = str(unix_ts)

        container_id = None
        for attempt in range(3):
            if attempt:
                import time as _time
                _time.sleep(10 * attempt)
                console.print(f"  [yellow]Retrying container creation (attempt {attempt + 1}/3)...[/yellow]")
            create_resp = requests.post(
                f"{api}/{ig_user_id}/media",
                params=container_params,
                timeout=30,
            )
            if create_resp.status_code < 500:
                create_resp.raise_for_status()
                container_id = create_resp.json().get("id")
                break
            logger.warning("Instagram 5xx on container create (attempt %d): %s", attempt + 1, create_resp.text[:200])

        if not container_id:
            console.print(f"[red]Instagram container creation failed: {create_resp.json()}[/red]")
            return None

        # Step 3: Poll until container is ready
        console.print("  [dim]Waiting for Instagram processing...[/dim]")
        if not _poll_container_status(access_token, container_id, api=api):
            console.print("[red]Instagram container failed or timed out.[/red]")
            return None

        # Step 5: Publish immediately, or return container_id for scheduled posts
        if unix_ts:
            console.print(f"[green]Instagram Reel scheduled:[/green] {container_id} → {publish_at}")
            return container_id

        publish_resp = requests.post(
            f"{api}/{ig_user_id}/media_publish",
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
