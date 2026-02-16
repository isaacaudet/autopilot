"""Layout profiles for Shorts formatting (e.g., per-streamer facecam + HUD crops)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _profiles_path(config: dict) -> Path:
    return Path(config["_queue_dir"]) / "facecam_profiles.json"


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize_rect(rect: dict) -> dict:
    """Normalize/clamp an (x,y,w,h) rect in 0..1 space."""
    x = float(rect.get("x", 0.0) or 0.0)
    y = float(rect.get("y", 0.0) or 0.0)
    w = float(rect.get("w", rect.get("width", 0.0)) or 0.0)
    h = float(rect.get("h", rect.get("height", 0.0)) or 0.0)

    # Sensible minimums (avoid zero-sized crops)
    w = _clamp(w, 0.05, 1.0)
    h = _clamp(h, 0.05, 1.0)
    x = _clamp(x, 0.0, 1.0 - w)
    y = _clamp(y, 0.0, 1.0 - h)

    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


LAYOUT_TUNING_BOUNDS: dict[str, tuple[float, float]] = {
    "safe_top_ratio": (0.0, 0.2),
    "safe_bottom_ratio": (0.0, 0.25),
    "facecam_band_ratio": (0.16, 0.5),
    "facecam_x_bias": (-1.0, 1.0),
    "facecam_y_bias": (0.0, 0.5),
    "facecam_zoom": (0.5, 2.0),
    "gameplay_zoom": (0.75, 1.6),
    "gameplay_zoom_no_facecam": (0.75, 1.7),
    "gameplay_x_bias": (-1.0, 1.0),
    "gameplay_y_bias": (-2.0, 2.0),
    "hud_height_ratio": (0.05, 0.22),
    "hud_scale": (0.5, 2.0),
    "hud_x_ratio": (0.0, 1.0),
    "hud_y_ratio": (0.0, 1.0),
    "title_y_ratio": (0.0, 0.6),
    "subtitle_margin_ratio": (0.05, 0.45),
}


def normalize_layout_tuning(tuning: dict) -> dict:
    """Normalize/clamp per-streamer fill-layout tuning values."""
    out: dict[str, float] = {}
    if not isinstance(tuning, dict):
        return out

    for key, (lo, hi) in LAYOUT_TUNING_BOUNDS.items():
        if key not in tuning:
            continue
        try:
            value = float(tuning.get(key))
        except (TypeError, ValueError):
            continue
        out[key] = round(_clamp(value, lo, hi), 4)
    return out


def load_facecam_profiles(config: dict) -> dict[str, dict]:
    """Return normalized streamer->profile mapping from the database."""
    from clipper.db import list_facecam_profiles
    return list_facecam_profiles(config)


def save_facecam_profiles(config: dict, profiles: dict[str, dict]) -> None:
    from clipper.db import save_facecam_profile
    for streamer, profile in profiles.items():
        save_facecam_profile(config, streamer, profile)


def upsert_facecam_profile(config: dict, streamer: str, rect: dict) -> dict:
    """Upsert the facecam rect for a streamer. Returns normalized rect.

    Kept for backward compatibility; prefer upsert_layout_profile().
    """
    profile = upsert_layout_profile(config, streamer, facecam=rect)
    facecam = profile.get("facecam")
    return facecam if isinstance(facecam, dict) else normalize_rect(rect)


def upsert_layout_profile(
    config: dict,
    streamer: str,
    *,
    facecam: dict | None = None,
    hud: dict | None = None,
    facecam_enabled: bool | None = None,
    hud_enabled: bool | None = None,
    layout_tuning: dict | None = None,
) -> dict:
    """Upsert one or more layout rects for a streamer.

    Keys:
      - facecam: rect in normalized 0..1 coords (x,y,w,h)
      - hud: rect in normalized 0..1 coords (x,y,w,h)
      - facecam_enabled: if False, treat as "no facecam" and degrade to classic layout
      - hud_enabled: if False, skip HUD overlay
      - layout_tuning: optional per-streamer fill tuning values (zoom/safe bands/text guides)
    """
    if facecam is None and hud is None and facecam_enabled is None and hud_enabled is None and layout_tuning is None:
        raise ValueError("facecam/hud rect or enabled flags are required")

    profiles = load_facecam_profiles(config)
    key = _normalize_key(streamer)
    if not key:
        raise ValueError("streamer is required")

    current = profiles.get(key, {})
    if not isinstance(current, dict):
        current = {}

    if facecam is not None:
        current["facecam"] = normalize_rect(facecam)
    if hud is not None:
        current["hud"] = normalize_rect(hud)
    if facecam_enabled is not None:
        current["facecam_enabled"] = bool(facecam_enabled)
    if hud_enabled is not None:
        current["hud_enabled"] = bool(hud_enabled)
    if layout_tuning is not None:
        current.update(normalize_layout_tuning(layout_tuning))

    profiles[key] = current
    save_facecam_profiles(config, profiles)
    return current


def delete_facecam_profile(config: dict, streamer: str) -> bool:
    from clipper.db import delete_facecam_profile_db
    key = _normalize_key(streamer)
    if not key:
        return False
    return delete_facecam_profile_db(config, key)
