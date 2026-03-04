"""Configuration loading from .env and config.yaml."""

import os
import shutil
from pathlib import Path

import yaml
from dotenv import load_dotenv


def get_project_root() -> Path:
    """Return the project root directory (where config.yaml lives)."""
    # Walk up from this file to find config.yaml
    current = Path(__file__).resolve().parent.parent
    if (current / "config.yaml").exists():
        return current
    # Fallback to cwd
    cwd = Path.cwd()
    if (cwd / "config.yaml").exists():
        return cwd
    raise FileNotFoundError(
        "Cannot find config.yaml. Run clipper from the project root."
    )


def load_config() -> dict:
    """Load config.yaml and .env, return merged config dict."""
    root = get_project_root()

    # Load .env
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # Load YAML
    config_path = root / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Inject paths
    config["_root"] = root
    config["_queue_dir"] = root / "queue"
    config["_output_dir"] = root / "output"

    return config


def require_env(name: str) -> str:
    """Get a required environment variable or raise."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_ffmpeg() -> str:
    """Return path to an ffmpeg binary with subtitle support.

    Prefers imageio-ffmpeg's bundled binary (has libass + drawtext),
    falls back to system ffmpeg.
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    system = shutil.which("ffmpeg")
    if system:
        return system
    raise FileNotFoundError("ffmpeg not found. Install with: pip install imageio-ffmpeg")


def get_encoder_args() -> list[str]:
    """Return FFmpeg encoder args — hardware if available, software fallback.

    Tries h264_videotoolbox (macOS Apple Silicon hardware encoder) first.
    Falls back to libx264 software encoding.
    """
    import subprocess

    ffmpeg = get_ffmpeg()
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        if "h264_videotoolbox" in result.stdout:
            return ["-c:v", "h264_videotoolbox", "-q:v", "30"]
    except (subprocess.TimeoutExpired, OSError):
        pass

    return ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]


def get_ffprobe() -> str:
    """Return path to ffprobe. Falls back to system binary."""
    # imageio-ffmpeg doesn't ship ffprobe, but the system one works for probing
    system = shutil.which("ffprobe")
    if system:
        return system
    raise FileNotFoundError("ffprobe not found. Install ffmpeg.")
