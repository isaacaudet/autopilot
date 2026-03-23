"""Platform-aware upload routing.

Routes upload/publish calls to the correct platform module based on
the channel's ``platform`` field in config.yaml (defaults to "youtube").
"""

import importlib
import logging

logger = logging.getLogger(__name__)

_PLATFORM_MODULES = {
    "youtube": "clipper.upload.youtube",
    "tiktok": "clipper.upload.tiktok",
    "instagram": "clipper.upload.instagram",
    "facebook": "clipper.upload.facebook",
}

_ID_COLUMNS = {
    "youtube": "video_id",
    "tiktok": "tiktok_id",
    "instagram": "instagram_id",
    "facebook": "facebook_id",
}


def get_channel_platform(channel: str | None, config: dict) -> str:
    """Return the platform string for a channel key (defaults to 'youtube')."""
    if not channel:
        return "youtube"
    channels = config.get("channels", {})
    ch = channels.get(channel, {})
    return ch.get("platform", "youtube")


def platform_id_column(platform: str) -> str:
    """Return the DB column name that stores the upload ID for a platform."""
    return _ID_COLUMNS.get(platform, "video_id")


def _get_module(platform: str):
    mod_name = _PLATFORM_MODULES.get(platform)
    if not mod_name:
        raise ValueError(f"Unsupported upload platform: {platform}")
    return importlib.import_module(mod_name)


def _ensure_formatted(clip: dict, config: dict) -> dict:
    """Ensure the clip's processed file has been formatted for Shorts.

    If processed_path points to a raw source file (no _final/_shorts/_clean
    suffix), runs format_for_shorts and updates the clip + DB.
    Compilations are exempt (they're already in final form).
    """
    from pathlib import Path

    pp = str(clip.get("processed_path", ""))
    if not pp:
        return clip

    stem = Path(pp).stem
    clip_id = str(clip.get("id", ""))

    # Compilations are already formatted
    if "compilation" in clip_id or "compilation" in stem:
        return clip

    # Already formatted
    if any(tag in stem for tag in ("_final", "_shorts", "_clean", "_ig_")):
        return clip

    # Raw source — format it
    if not Path(pp).exists():
        return clip

    logger.info("Raw source detected for %s, formatting before upload", clip_id)
    from clipper.process.format import format_for_shorts
    formatted = format_for_shorts(pp, config, clip=clip)
    clip["processed_path"] = str(formatted)

    if clip_id:
        from clipper.db import update_clip
        update_clip(config, clip_id, processed_path=str(formatted))

    return clip


def upload_clip(clip, config, privacy="unlisted", verbose=False, channel=None, publish_at=None) -> str | None:
    """Upload a clip via the platform module for the given channel."""
    clip = _ensure_formatted(clip, config)
    platform = get_channel_platform(channel, config)
    mod = _get_module(platform)
    fn = mod.upload_clip
    import inspect
    if "publish_at" in inspect.signature(fn).parameters:
        return fn(clip, config, privacy=privacy, verbose=verbose, channel=channel, publish_at=publish_at)
    return fn(clip, config, privacy=privacy, verbose=verbose, channel=channel)


def publish_video(video_id, verbose=False, *, channel=None, config=None) -> bool:
    """Publish (make public) a previously uploaded video."""
    platform = get_channel_platform(channel, config or {})
    mod = _get_module(platform)
    return mod.publish_video(video_id, verbose=verbose, channel=channel, config=config)
