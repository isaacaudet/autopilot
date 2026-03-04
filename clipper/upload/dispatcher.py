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


def upload_clip(clip, config, privacy="unlisted", verbose=False, channel=None, publish_at=None) -> str | None:
    """Upload a clip via the platform module for the given channel."""
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
