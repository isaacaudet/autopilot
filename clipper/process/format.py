"""Reformat video to 9:16 vertical (1080x1920) for YouTube Shorts."""

import json
import subprocess
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg, get_ffprobe, get_encoder_args
from clipper.layout_profiles import load_facecam_profiles, normalize_layout_tuning, normalize_rect

console = Console()

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

_TUNING_KEYS = (
    "safe_top_ratio",
    "safe_bottom_ratio",
    "facecam_band_ratio",
    "facecam_x_bias",
    "facecam_y_bias",
    "facecam_zoom",
    "gameplay_zoom",
    "gameplay_zoom_no_facecam",
    "gameplay_x_bias",
    "gameplay_y_bias",
    "hud_height_ratio",
    "hud_scale",
    "hud_x_ratio",
    "hud_y_ratio",
    "title_y_ratio",
    "subtitle_margin_ratio",
)

# Audio normalization chain:
# - highpass at 80Hz removes rumble/mic handling noise
# - mild bass boost at 100Hz for impact on phone speakers
# - loudnorm to -14 LUFS (YouTube's target loudness)
AUDIO_FILTER = "highpass=f=80,bass=g=3:f=100:w=0.5,loudnorm=I=-14:TP=-1:LRA=11"


def _get_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Get width and height of a video using ffprobe."""
    cmd = [
        get_ffprobe(),
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)

    probe = json.loads(result.stdout)
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])

    raise RuntimeError(f"No video stream found in {video_path}")


def _is_approximately_vertical(width: int, height: int) -> bool:
    """Check if aspect ratio is already close to 9:16."""
    if height == 0:
        return False
    ratio = width / height
    target_ratio = 9 / 16  # 0.5625
    return abs(ratio - target_ratio) < 0.05


def _pick_shorts_layout_mode(clip: dict | None, config: dict) -> str:
    raw = ""
    if clip:
        raw = str(clip.get("_shorts_layout") or "").strip()
    if not raw:
        raw = str(config.get("shorts", {}).get("layout") or "").strip()
    raw = raw.lower()
    mode = "fill" if raw in ("fill", "stack", "stacked", "crop", "split") else "blur"

    return mode


def _get_streamer_profile(clip: dict | None, config: dict) -> dict | None:
    """Return effective profile dict (streamer profile + per-clip override)."""
    if not clip:
        return None

    merged: dict = {}
    streamer = str(clip.get("streamer") or "").strip().lower()
    if streamer:
        prof = load_facecam_profiles(config).get(streamer)
        if isinstance(prof, dict):
            merged.update(prof)

    override = clip.get("_layout_override")
    if isinstance(override, dict):
        if isinstance(override.get("facecam"), dict):
            merged["facecam"] = override.get("facecam")
        if isinstance(override.get("hud"), dict):
            merged["hud"] = override.get("hud")

        if override.get("facecam_enabled") is not None:
            merged["facecam_enabled"] = bool(override.get("facecam_enabled"))
        if override.get("hud_enabled") is not None:
            merged["hud_enabled"] = bool(override.get("hud_enabled"))

        tuning_override = {k: override.get(k) for k in _TUNING_KEYS if k in override}
        merged.update(normalize_layout_tuning(tuning_override))

    return merged or None


def _get_facecam_rect(clip: dict | None, config: dict, profile: dict | None = None) -> dict:
    """Return {x,y,w,h} in normalized 0..1 coordinates."""
    # Default: Deadlock-style streams tend to have a facecam in the top-left,
    # slightly below the HUD row.
    default = {"x": 0.0, "y": 0.18, "w": 0.30, "h": 0.34}

    fill_cfg = (config.get("shorts", {}) or {}).get("fill", {}) or {}
    cfg_default = fill_cfg.get("facecam_default")
    if isinstance(cfg_default, dict):
        try:
            default = normalize_rect(cfg_default)
        except Exception:
            pass

    if not clip:
        return default

    prof = profile if isinstance(profile, dict) else _get_streamer_profile(clip, config)
    if not isinstance(prof, dict):
        return default

    rect = prof.get("facecam") if isinstance(prof.get("facecam"), dict) else prof
    if not isinstance(rect, dict):
        return default

    try:
        return normalize_rect(rect)
    except Exception:
        return default


def _get_hud_rect(clip: dict | None, config: dict, profile: dict | None = None) -> dict:
    """Return {x,y,w,h} in normalized 0..1 coordinates for the HUD/items crop."""
    # Default: bottom-left HUD bar area (health + abilities). User should still
    # calibrate per streamer, but a wide + short rectangle is usually correct.
    default = {"x": 0.0, "y": 0.84, "w": 0.58, "h": 0.16}

    fill_cfg = (config.get("shorts", {}) or {}).get("fill", {}) or {}
    hud_cfg = fill_cfg.get("hud") if isinstance(fill_cfg.get("hud"), dict) else {}
    cfg_default = None
    if isinstance(hud_cfg, dict):
        cfg_default = hud_cfg.get("default")
    if cfg_default is None:
        cfg_default = fill_cfg.get("hud_default")
    if isinstance(cfg_default, dict):
        try:
            default = normalize_rect(cfg_default)
        except Exception:
            pass

    if not clip:
        return default

    prof = profile if isinstance(profile, dict) else _get_streamer_profile(clip, config)
    if not isinstance(prof, dict):
        return default

    rect = prof.get("hud")
    if not isinstance(rect, dict):
        return default

    try:
        return normalize_rect(rect)
    except Exception:
        return default


def _get_layout_tuning(clip: dict | None, config: dict, profile: dict | None = None) -> dict:
    """Resolve per-streamer fill-layout tuning with config defaults."""
    fill_cfg = (config.get("shorts", {}) or {}).get("fill", {}) or {}
    gameplay_cfg = fill_cfg.get("gameplay", {}) if isinstance(fill_cfg.get("gameplay"), dict) else {}
    hud_cfg = fill_cfg.get("hud", {}) if isinstance(fill_cfg.get("hud"), dict) else {}
    shorts_cfg = config.get("shorts", {}) or {}

    h = int(shorts_cfg.get("height", TARGET_HEIGHT) or TARGET_HEIGHT)
    subtitle_margin_v = float(shorts_cfg.get("subtitle_margin_v", 450) or 450)
    subtitle_margin_ratio = subtitle_margin_v / float(max(1, h))

    safe_top_ratio = float(fill_cfg.get("safe_top_ratio", 0.08) or 0.0)
    safe_bottom_ratio = float(fill_cfg.get("safe_bottom_ratio", 0.13) or 0.0)
    title_y_default = max(0.03, safe_top_ratio * 0.25)

    defaults = {
        "safe_top_ratio": safe_top_ratio,
        "safe_bottom_ratio": safe_bottom_ratio,
        "facecam_band_ratio": float(fill_cfg.get("facecam_band_ratio", 0.20) or 0.20),
        "gameplay_zoom": float(gameplay_cfg.get("zoom", 1.02) or 1.02),
        "gameplay_zoom_no_facecam": float(gameplay_cfg.get("zoom_no_facecam", 1.12) or 1.12),
        "gameplay_x_bias": float(gameplay_cfg.get("x_bias", 0.0) or 0.0),
        "gameplay_y_bias": float(gameplay_cfg.get("y_bias", 0.0) or 0.0),
        "hud_height_ratio": float(hud_cfg.get("height_ratio", 0.08) or 0.08),
        "hud_scale": float(hud_cfg.get("scale", 1.0) or 1.0),
        "hud_x_ratio": float(hud_cfg.get("x_ratio", 0.5) or 0.5),
        "hud_y_ratio": float(hud_cfg.get("y_ratio", 0.88) or 0.88),
        "title_y_ratio": float(fill_cfg.get("title_y_ratio", title_y_default) or title_y_default),
        "subtitle_margin_ratio": float(fill_cfg.get("subtitle_margin_ratio", subtitle_margin_ratio) or subtitle_margin_ratio),
    }
    merged = normalize_layout_tuning(defaults)

    prof = profile if isinstance(profile, dict) else _get_streamer_profile(clip, config)
    if isinstance(prof, dict):
        merged.update(normalize_layout_tuning(prof))

    return merged


def _build_fill_filter(
    *,
    w: int,
    h: int,
    facecam_h: int,
    clip: dict | None,
    config: dict,
) -> str:
    """Build a filter_complex that fills 9:16 with two stacked crops: facecam + gameplay."""
    fill_cfg = (config.get("shorts", {}) or {}).get("fill", {}) or {}
    gameplay_cfg = fill_cfg.get("gameplay", {}) if isinstance(fill_cfg.get("gameplay"), dict) else {}
    hud_cfg = fill_cfg.get("hud", {}) if isinstance(fill_cfg.get("hud"), dict) else {}
    prof = _get_streamer_profile(clip, config)
    tuning = _get_layout_tuning(clip, config, prof)

    # "Safe" areas are blurred background only. Title text can live in safe_top,
    # and HUD can live in safe_bottom so the gameplay region stays clean.
    safe_top_ratio = float(tuning.get("safe_top_ratio", 0.08))
    safe_top = int(round(h * safe_top_ratio))

    # Give a bit more room at the bottom so HUD can live outside the gameplay
    # band without getting covered by platform UI.
    safe_bottom_ratio = float(tuning.get("safe_bottom_ratio", 0.13))
    safe_bottom = int(round(h * safe_bottom_ratio))

    content_h = h - safe_top - safe_bottom
    if content_h <= 1:
        content_h = h

    # Determine per-streamer enabled flags.
    facecam_enabled = True
    hud_enabled = bool(hud_cfg.get("enabled", True))
    if isinstance(prof, dict):
        if prof.get("facecam_enabled") is False:
            facecam_enabled = False
        if prof.get("hud_enabled") is False:
            hud_enabled = False

    face_rect = _get_facecam_rect(clip, config, prof)
    hud_rect = _get_hud_rect(clip, config, prof)

    # Gameplay source crop. Keep this explicit-only:
    # we do not auto-derive crop bounds from HUD/facecam rects because that
    # makes zoom/pan feel inconsistent between clips.
    bottom_crop = float(gameplay_cfg.get("bottom_crop", 0.0) or 0.0)
    bottom_crop = max(0.0, min(0.35, bottom_crop))
    top_crop = float(gameplay_cfg.get("top_crop", 0.0) or 0.0)
    top_crop = max(0.0, min(0.45, top_crop))

    hud_rect_configured = isinstance(prof, dict) and isinstance((prof or {}).get("hud"), dict)
    if hud_enabled and not hud_rect_configured:
        # Safety net: don't crop the gameplay or attempt a HUD overlay if the HUD
        # rect was never calibrated for this streamer.
        hud_enabled = False
        bottom_crop = 0.0

    # Keep gameplay mostly centered and readable; no-facecam can zoom harder.
    # Backward-compatible zoom semantics:
    # - positive values are absolute scale (legacy behavior)
    # - zero/negative values are zoom-out offsets from 1.0 (e.g. -0.3 -> 0.7x)
    raw_zoom = float(tuning.get("gameplay_zoom", 1.02))
    zoom = max(0.2, (1.0 + raw_zoom) if raw_zoom <= 0.0 else raw_zoom)
    if not facecam_enabled:
        # Game-only layout can zoom more to fill the screen.
        raw_zoom_no_facecam = float(tuning.get("gameplay_zoom_no_facecam", 1.12))
        zoom_no_facecam = max(0.2, (1.0 + raw_zoom_no_facecam) if raw_zoom_no_facecam <= 0.0 else raw_zoom_no_facecam)
        zoom = zoom_no_facecam

    # Game band height: fill from facecam bottom (or safe_top) to the
    # frame bottom.  No safe_bottom gap — the HUD overlays on gameplay
    # directly, which looks cleaner than a blurred dark band.
    if facecam_enabled:
        game_h = max(1, h - facecam_h)
    else:
        game_h = max(1, h - safe_top)

    # Slightly compress the facecam band when it is wide (full-width stack).
    # The user's preference is "mostly gameplay"; keep facecam <= ~1/3 by default.
    # If you want a bigger facecam, override shorts.fill.facecam_band_ratio in config.yaml.

    # HUD overlay size (relative to full 9:16 height).
    hud_ratio = float(tuning.get("hud_height_ratio", 0.08))
    hud_pad_x = int(hud_cfg.get("pad_x", 24) or 0)
    hud_pad_y = int(hud_cfg.get("pad_y", 18) or 0)
    hud_target_h = int(round(h * hud_ratio))

    # HUD overlay height — sized by hud_height_ratio, capped at 20% of frame.
    hud_h = max(1, min(hud_target_h, int(round(h * 0.20))))

    pan_x = float(tuning.get("gameplay_x_bias", 0.0))
    # Gameplay Y is handled as output-plane translation (not crop zoom/pan).
    gameplay_y_bias = float(tuning.get("gameplay_y_bias", 0.0))
    pan_x_n = max(0.0, min(1.0, (pan_x + 1.0) / 2.0))
    scale_target_w = max(2, int(round(w * zoom)))
    scale_target_h = max(2, int(round(game_h * zoom)))

    # Facecam crop -> scale/crop to top band, with optional pan + zoom.
    facecam_x_bias = float(tuning.get("facecam_x_bias", 0.0))
    facecam_zoom = float(tuning.get("facecam_zoom", 1.0))
    facecam_zoom = max(0.5, min(2.0, facecam_zoom))
    face_w = max(0.05, float(face_rect.get("w", 0.30) or 0.30))
    face_h_ratio = max(0.05, float(face_rect.get("h", 0.34) or 0.34))
    face_y_ratio = max(0.0, min(1.0 - face_h_ratio, float(face_rect.get("y", 0.18) or 0.18)))
    face_base_x = max(0.0, min(1.0 - face_w, float(face_rect.get("x", 0.0) or 0.0)))
    face_span = max(1e-6, 1.0 - face_w)
    face_base_n = max(0.0, min(1.0, face_base_x / face_span))
    # Bias nudges the sampled source window relative to the calibrated base rect.
    face_src_x_n = max(0.0, min(1.0, face_base_n + facecam_x_bias))
    if facecam_enabled:
        face_scale_w = max(1, int(round(w * facecam_zoom)))
        face_scale_h = max(1, int(round(facecam_h * facecam_zoom)))
        face_src_x = f"max(0,min(iw-(iw*{face_w:.4f}),(iw-(iw*{face_w:.4f}))*{face_src_x_n:.4f}))"
        face = (
            f"[face_src]crop="
            f"w=iw*{face_w:.4f}:h=ih*{face_h_ratio:.4f}:"
            f"x='{face_src_x}':y=ih*{face_y_ratio:.4f},"
            f"scale={face_scale_w}:{face_scale_h}:force_original_aspect_ratio=increase,setsar=1,"
            f"crop={w}:{facecam_h}:x=(iw-ow)/2:y=(ih-oh)/2"
            f"[face]"
        )

    # Gameplay transform:
    # 1) Optionally crop explicit top/bottom source slices.
    # 2) Scale from source using zoom.
    # 3) Keep gameplay as a free layer (no fixed-size pre-crop canvas);
    #    final clipping only happens against the output frame edges.
    if top_crop + bottom_crop >= 0.9:
        top_crop = max(0.0, min(top_crop, 0.45))
        bottom_crop = max(0.0, min(bottom_crop, 0.14))
        if top_crop + bottom_crop >= 0.9:
            top_crop = 0.0
            bottom_crop = 0.0

    game_overlay_x_expr = f"if(gte(W,w),(W-w)*{pan_x_n:.4f},-(w-W)*{pan_x_n:.4f})"
    # Center gameplay relative to the gameplay band height, then apply plane Y.
    game_overlay_y_center_expr = f"({game_h}-h)/2"
    game = (
        f"[game_src]crop=w=iw:h=ih*(1-{top_crop}-{bottom_crop}):x=0:y=ih*{top_crop},"
        f"scale={scale_target_w}:{scale_target_h}:force_original_aspect_ratio=increase,setsar=1"
        f"[game]"
    )

    # Background blur fills the full 9:16 frame (safety net behind the stack).
    bg = (
        f"[bg_src]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,boxblur=30:5"
        f"[bg]"
    )

    hud_scale = float(tuning.get("hud_scale", 1.0))
    hud_scale = max(0.5, min(2.0, hud_scale))
    hud_x_ratio = float(tuning.get("hud_x_ratio", 0.5))
    hud_y_ratio = float(tuning.get("hud_y_ratio", 0.88))
    hud_x_ratio = max(0.0, min(1.0, hud_x_ratio))
    hud_y_ratio = max(0.0, min(1.0, hud_y_ratio))
    hud_target_w = max(1, int(round(w * hud_scale)))
    hud_target_h = max(1, int(round(hud_h * hud_scale)))

    if hud_enabled:
        hud = (
            f"[hud_src]crop="
            f"w=iw*{hud_rect['w']}:h=ih*{hud_rect['h']}:"
            f"x=iw*{hud_rect['x']}:y=ih*{hud_rect['y']},"
            f"scale={hud_target_w}:{hud_target_h}:force_original_aspect_ratio=decrease,setsar=1"
            f"[hud]"
        )

    # Wider travel so gameplay Y behaves like a true layout-position control.
    gameplay_shift_limit = int(round(h * 0.35))
    gameplay_shift = int(round(gameplay_shift_limit * max(-1.0, min(1.0, gameplay_y_bias))))
    facecam_y_bias = float(tuning.get("facecam_y_bias", 0.0))
    face_y = max(0, int(round(h * facecam_y_bias))) if facecam_enabled else 0
    game_y_base = (face_y + facecam_h) if facecam_enabled else max(0, safe_top)
    game_y = game_y_base + gameplay_shift

    game_overlay_y_expr = f"{game_y}+{game_overlay_y_center_expr}"

    # Place HUD in user-controlled location (defaults near safe-bottom center).
    hud_overlay_x_expr = f"max(0,min(W-w,(W-w)*{hud_x_ratio:.4f}))"
    hud_overlay_y_expr = f"max(0,min(H-h,(H-h)*{hud_y_ratio:.4f}))"

    if facecam_enabled and hud_enabled:
        return (
            "[0:v]split=4[face_src][game_src][hud_src][bg_src];"
            f"{face};"
            f"{game};"
            f"{hud};"
            f"{bg};"
            f"[bg][face]overlay=x=0:y={face_y}[lay1];"
            f"[lay1][game]overlay=x='{game_overlay_x_expr}':y='{game_overlay_y_expr}'[base];"
            f"[base][hud]overlay=x='{hud_overlay_x_expr}':y='{hud_overlay_y_expr}'"
        )

    if facecam_enabled and not hud_enabled:
        return (
            "[0:v]split=3[face_src][game_src][bg_src];"
            f"{face};"
            f"{game};"
            f"{bg};"
            f"[bg][face]overlay=x=0:y={face_y}[lay1];"
            f"[lay1][game]overlay=x='{game_overlay_x_expr}':y='{game_overlay_y_expr}'"
        )

    if not facecam_enabled and hud_enabled:
        return (
            "[0:v]split=3[game_src][hud_src][bg_src];"
            f"{game};"
            f"{hud};"
            f"{bg};"
            f"[bg][game]overlay=x='{game_overlay_x_expr}':y='{game_overlay_y_expr}'[base];"
            f"[base][hud]overlay=x='{hud_overlay_x_expr}':y='{hud_overlay_y_expr}'"
        )

    # no facecam, no hud
    return (
        "[0:v]split=2[game_src][bg_src];"
        f"{game};"
        f"{bg};"
        f"[bg][game]overlay=x='{game_overlay_x_expr}':y='{game_overlay_y_expr}'"
    )


def format_for_shorts(video_path: Path, config: dict, clip: dict | None = None, verbose: bool = False) -> Path:
    """Reformat a video to 1080x1920 vertical for YouTube Shorts.

    For landscape (16:9) video: creates a blurred background with the original
    video centered on top. For already-vertical video: scales to 1080x1920.

    Also applies audio normalization (highpass + bass boost + loudnorm).

    Returns the path to the formatted output file.
    """
    video_path = Path(video_path)
    shorts_cfg = config.get("shorts", {})
    w = shorts_cfg.get("width", TARGET_WIDTH)
    h = shorts_cfg.get("height", TARGET_HEIGHT)
    layout_mode = _pick_shorts_layout_mode(clip, config)

    output_path = video_path.with_name(f"{video_path.stem}_shorts.mp4")

    width, height = _get_video_dimensions(video_path)
    if verbose:
        console.print(f"  Source dimensions: {width}x{height}")

    if _is_approximately_vertical(width, height):
        # Already vertical — just scale
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        if verbose:
            console.print("  Already vertical, scaling to target.")
    else:
        if layout_mode == "fill":
            tuning = _get_layout_tuning(clip, config)
            safe_top_ratio = float(tuning.get("safe_top_ratio", 0.08))
            safe_top = int(round(h * safe_top_ratio))

            safe_bottom_ratio = float(tuning.get("safe_bottom_ratio", 0.13))
            safe_bottom = int(round(h * safe_bottom_ratio))

            content_h = h - safe_top - safe_bottom
            if content_h <= 1:
                content_h = h

            ratio = float(tuning.get("facecam_band_ratio", 0.20))
            facecam_h = int(round(h * ratio))
            vf = _build_fill_filter(w=w, h=h, facecam_h=facecam_h, clip=clip, config=config)
            if verbose:
                gameplay_h = max(0, h - facecam_h)
                console.print(
                    "  Landscape detected, fill layout: "
                    f"facecam_h={facecam_h}px, gameplay_h={gameplay_h}px, "
                    f"safe_top={safe_top}px"
                )
        else:
            # Landscape — blurred background + gameplay filling ~60% of vertical frame
            # Gameplay at 1080px wide → 608px tall (16:9). Positioned upper-third
            # leaves room for subtitles below without overlap.
            fg_w = w  # 1080
            fg_h = int(fg_w * height / width)  # maintain aspect ratio
            fg_y = int((h - fg_h) * 0.35)  # position slightly above center (35% from top)
            vf = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},boxblur=30:5[bg];"
                f"[0:v]scale={fg_w}:{fg_h}[fg];"
                f"[bg][fg]overlay=0:{fg_y}"
            )
            if verbose:
                console.print(f"  Landscape detected, gameplay {fg_w}x{fg_h} at y={fg_y} on blurred bg.")

    ffmpeg_bin = get_ffmpeg()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-filter_complex" if "[" in vf else "-vf", vf,
        "-af", AUDIO_FILTER,
        *get_encoder_args(),
        "-c:a", "aac",
        "-b:a", "256k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    if verbose:
        console.print(f"  Running ffmpeg: {ffmpeg_bin}")
        console.print(f"  Audio: {AUDIO_FILTER}")

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed:\n{e.stderr}")

    console.print(f"  [green]Formatted:[/green] {output_path.name}")
    return output_path
