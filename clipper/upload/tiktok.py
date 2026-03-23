"""TikTok upload via Content Posting API v2.

Requires:
  - TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env
  - OAuth token file (obtained via ``clipper auth -c <channel>``)

Rate limits: 6 init calls/min/user, ~15 posts/day/creator.
Sandbox: 5 test users max, SELF_ONLY forced until Direct Post Audit (2-4 weeks).
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

API_BASE = "https://open.tiktokapis.com"

_PRIVACY_MAP = {
    "public": "PUBLIC_TO_EVERYONE",
    "unlisted": "SELF_ONLY",  # TikTok has no unlisted — map to private
    "private": "SELF_ONLY",
}


def _token_path_for_channel(channel: str | None, config: dict) -> Path:
    channels = config.get("channels", {})
    ch = channels.get(channel or "", {})
    token_file = ch.get("token_file", f".clipper_tiktok_{channel}.json")
    return get_project_root() / token_file


def _load_token(token_path: Path) -> dict:
    if not token_path.exists():
        raise RuntimeError(
            f"TikTok token missing: {token_path.name}. "
            "Run `clipper auth -c <channel>` to authenticate."
        )
    return json.loads(token_path.read_text())


def _refresh_token_if_needed(token_data: dict, token_path: Path) -> str:
    """Return a valid access token, refreshing if expired."""
    expires_at = token_data.get("expires_at", 0)
    if time.time() < expires_at - 300 and "access_token" in token_data:  # 5 min buffer
        return token_data["access_token"]

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("TikTok refresh token missing — re-authenticate.")

    resp = requests.post(
        f"{API_BASE}/v2/oauth/token/",
        json={
            "client_key": require_env("TIKTOK_CLIENT_KEY"),
            "client_secret": require_env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"TikTok token refresh failed: {data.get('error_description') or data}")
    token_data["access_token"] = data["access_token"]
    token_data["refresh_token"] = data.get("refresh_token", refresh_token)
    token_data["expires_at"] = time.time() + data.get("expires_in", 86400)
    token_path.write_text(json.dumps(token_data, indent=2))
    logger.info("TikTok token refreshed")
    return token_data["access_token"]


def _query_creator_info(access_token: str) -> dict:
    """Query creator info — required before every post."""
    resp = requests.post(
        f"{API_BASE}/v2/post/publish/creator_info/query/",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


_TT_CAPTION_PROMPT = """\
You write viral TikTok captions for gaming clips.

Clip metadata:
- Streamer: {streamer}
- Game: {game}
- Title: {title}
- Duration: {duration}s
- Views: {views}

Write ONE short hook sentence (max 12 words) + 3-5 relevant hashtags on the same line.
Focus on the action/emotion, NOT the streamer name or game title.
Do NOT use quotation marks. Do NOT start with "Watch" or "Check out".
Always include #gaming and #fyp. Add 1-2 game-specific tags.
Example: "Nobody saw that coming 💀 #gaming #fyp #deadlock #clutch"

Respond with ONLY the caption line, nothing else."""


def _gemini_hook(clip: dict) -> str | None:
    """Generate a TikTok caption via Gemini. Returns None on failure."""
    try:
        from clipper.process.analyze import _get_gemini_client, _get_gemini_model
        client = _get_gemini_client()
        if not client:
            return None
        title = (clip.get("_title_override") or clip.get("title", "")).replace(" #Shorts", "").strip()
        prompt = _TT_CAPTION_PROMPT.format(
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
        logger.warning("Gemini TikTok caption generation failed: %s", e)
        return None


def _build_caption(clip: dict, config: dict) -> str:
    """Build a TikTok caption from clip metadata (max 2200 chars)."""
    title = clip.get("_title_override") or clip.get("title", "")
    title = title.replace(" #Shorts", "").strip()

    streamer = clip.get("streamer", "")
    game = clip.get("game", "")

    # Try Gemini-generated caption first (includes hashtags)
    hook = _gemini_hook(clip)
    if hook:
        return hook[:2200]

    # Fallback: manual construction
    hashtags = []
    if game:
        hashtags.append(f"#{game.replace(' ', '').lower()}")
    if streamer:
        hashtags.append(f"#{streamer.replace(' ', '').lower()}")
    hashtags.extend(["#gaming", "#fyp", "#clips"])

    caption = f"{title} {' '.join(hashtags)}"
    return caption[:2200]


def _poll_publish_status(access_token: str, publish_id: str, max_wait: int = 300) -> bool:
    """Poll until PUBLISH_COMPLETE or FAILED. Returns True on success."""
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.post(
            f"{API_BASE}/v2/post/publish/status/fetch/",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"publish_id": publish_id},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        status = data.get("status")

        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return True
        if status == "FAILED":
            logger.error("TikTok publish failed: %s", data.get("fail_reason"))
            return False
        logger.debug("TikTok status: %s", status)

        time.sleep(5)

    logger.error("TikTok publish timed out after %ds", max_wait)
    return False


def upload_clip(clip, config, privacy="unlisted", verbose=False, channel=None) -> str | None:
    """Upload a clip to TikTok. Returns the publish_id on success, None on failure."""
    processed_path = clip.get("processed_path")
    if not processed_path or not Path(processed_path).exists():
        console.print(f"[red]Missing processed file: {processed_path}[/red]")
        return None

    file_size = Path(processed_path).stat().st_size
    if file_size > 64 * 1024 * 1024:
        console.print("[red]TikTok single-chunk upload limit is 64MB.[/red]")
        return None

    token_path = _token_path_for_channel(channel, config)
    token_data = _load_token(token_path)
    access_token = _refresh_token_if_needed(token_data, token_path)

    caption = clip.get("_description_override") or _build_caption(clip, config)
    tt_privacy = _PRIVACY_MAP.get(privacy, "SELF_ONLY")

    try:
        # Step 1: Query creator info
        _query_creator_info(access_token)

        # Step 2: Init upload — inbox endpoint (works in sandbox).
        # Videos appear in TikTok creator inbox for review/posting.
        # To post directly as private: requires Direct Post API audit approval.
        init_resp = requests.post(
            f"{API_BASE}/v2/post/publish/inbox/video/init/",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,
                    "total_chunk_count": 1,
                },
            },
            timeout=30,
        )
        init_resp.raise_for_status()
        init_data = init_resp.json().get("data", {})
        publish_id = init_data.get("publish_id")
        upload_url = init_data.get("upload_url")

        if not publish_id or not upload_url:
            console.print(f"[red]TikTok init failed: {init_resp.json()}[/red]")
            return None

        # Step 3: Upload binary
        console.print(f"  [bold]Uploading to TikTok...[/bold]")
        with open(processed_path, "rb") as f:
            put_resp = requests.put(
                upload_url,
                headers={
                    "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    "Content-Type": "video/mp4",
                },
                data=f,
                timeout=300,
            )
            put_resp.raise_for_status()

        # Step 4: Poll for publish status
        console.print("  [dim]Waiting for TikTok processing...[/dim]")
        if _poll_publish_status(access_token, publish_id):
            console.print(f"[green]TikTok publish complete:[/green] {publish_id}")
            return publish_id
        else:
            console.print("[red]TikTok publish failed or timed out.[/red]")
            return None

    except Exception as e:
        console.print(f"[red]TikTok upload error: {e}[/red]")
        clip["_upload_error_reason"] = "tiktok_error"
        clip["_upload_error_message"] = str(e)
        clip["_upload_error_status"] = None
        return None


def publish_video(video_id, verbose=False, *, channel=None, config=None) -> bool:
    """No-op — TikTok publishes on upload."""
    return True
