"""Burn subtitles into video using FFmpeg."""

import subprocess
from pathlib import Path

from rich.console import Console

from clipper.config import get_ffmpeg, get_encoder_args

console = Console()


def _build_zoom_filter() -> str:
    """Build FFmpeg filter for a zoom-settle (108% -> 100%) over 0.5s.

    Creates visible motion that catches the eye during Shorts scrolling.
    Crops a smaller center region (= zoomed in) then scales back to 1080x1920.
    After 0.5s the crop covers the full frame (no visible effect).

    Assumes input is 1080x1920 (applied after format_for_shorts).
    """
    # zoom factor: 1.08 at t=0, easing to 1.0 at t=0.5, stays 1.0 after
    # crop width = iw/zoom, crop height = ih/zoom, centered
    return (
        "crop="
        "w='iw/(1.08-0.08*min(t/0.5,1))':"
        "h='ih/(1.08-0.08*min(t/0.5,1))':"
        "x='(iw-ow)/2':"
        "y='(ih-oh)/2'"
        ",scale=1080:1920:flags=lanczos"
    )


def _build_hook_filter(hook_text: str, config: dict) -> str:
    """Build FFmpeg drawtext filters for the first-frame hook overlay.

    Renders large Impact text with black shadow + outline, centered at y=15%,
    visible from 0.1s to 2.0s with fade in/out.
    """
    escaped = hook_text.replace("'", "\\'").replace(":", "\\:")

    alpha_expr = "if(lt(t,0.1),0,if(lt(t,0.3),(t-0.1)/0.2,if(lt(t,1.7),1,(2.0-t)/0.3)))"

    # Shadow layer (offset black text behind the main text)
    shadow = (
        f"drawtext=text='{escaped}'"
        f":font=Impact"
        f":fontsize=88"
        f":fontcolor=black@0.6"
        f":x=(w-text_w)/2+3"
        f":y=h*0.15+3"
        f":alpha='{alpha_expr}'"
        f":enable='between(t,0.1,2.0)'"
    )

    # Main text layer
    main = (
        f"drawtext=text='{escaped}'"
        f":font=Impact"
        f":fontsize=88"
        f":fontcolor=white"
        f":borderw=6"
        f":bordercolor=black"
        f":x=(w-text_w)/2"
        f":y=h*0.15"
        f":alpha='{alpha_expr}'"
        f":enable='between(t,0.1,2.0)'"
    )

    return f"{shadow},{main}"


def _build_progress_bar_filter(duration: float) -> str:
    """Build FFmpeg drawbox filter for a progress bar at the bottom.

    Thin cyan bar that fills left-to-right over the video duration.
    Increases retention — viewers subconsciously watch to see it complete.
    """
    return (
        f"drawbox=x=0:y=ih-12:w=iw:h=12:color=333333@0.6:t=fill,"
        f"drawbox=x=0:y=ih-10:w='min(iw,iw*t/{duration:.2f})':h=8:color=00FFFF@0.9:t=fill"
    )


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    config: dict,
    is_shorts: bool = False,
    hook_text: str | None = None,
    verbose: bool = False,
    output_name: str | None = None,
) -> Path:
    """Burn subtitles (ASS or SRT) into a video file using FFmpeg.

    ASS files carry their own styling. SRT files get force_style applied.
    Returns the Path to the output video with burned-in subtitles.
    """
    video_path = Path(video_path)
    subtitle_path = Path(subtitle_path)
    if output_name:
        output_path = video_path.with_name(f"{output_name}_final.mp4")
    else:
        # Clean stem — strip any previous _shorts/_subtitled suffixes to avoid nesting
        base_stem = video_path.stem.replace("_subtitled", "").replace("_shorts", "")
        output_path = video_path.with_name(f"{base_stem}_final.mp4")

    ffmpeg_bin = get_ffmpeg()

    # Check that the ffmpeg binary has subtitle support
    try:
        check = subprocess.run(
            [ffmpeg_bin, "-filters"], capture_output=True, text=True
        )
        has_ass = "ass" in check.stdout
        has_subtitles = "subtitles" in check.stdout
    except FileNotFoundError:
        console.print("[red]ffmpeg not found.[/red]")
        return video_path

    if not has_ass and not has_subtitles:
        console.print(
            "[red]FFmpeg has no subtitle support.[/red]\n"
            "Fix: [cyan]pip install imageio-ffmpeg[/cyan]"
        )
        return video_path

    # Escape path for FFmpeg filter
    sub_escaped = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    ext = subtitle_path.suffix.lower()

    if ext == ".ass":
        # ASS files have embedded styling — use the ass filter directly
        vf = f"ass='{sub_escaped}'"
    elif ext == ".srt":
        if is_shorts:
            sub_config = config.get("subtitles", {})
            shorts_config = config.get("shorts", {})
            font_size = sub_config.get("font_size", 28)
            outline = sub_config.get("outline_width", 3)
            margin_v = shorts_config.get("subtitle_margin_v", 80)

            force_style = (
                f"FontSize={font_size},"
                f"Bold=1,"
                f"Alignment=2,"
                f"MarginV={margin_v},"
                f"PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,"
                f"Outline={outline}"
            )
            vf = f"subtitles='{sub_escaped}':force_style='{force_style}'"
        else:
            vf = f"subtitles='{sub_escaped}'"
    else:
        console.print(f"[yellow]Unknown subtitle format: {ext}[/yellow]")
        return video_path

    # Zoom-settle — Shorts only (hardcoded to 1080x1920 input)
    if is_shorts:
        zoom_filter = _build_zoom_filter()
        vf = f"{zoom_filter},{vf}"

    # Progress bar + hook text — works in both Shorts and landscape (compilations)
    if hook_text:
        # Progress bar — probe duration first
        from clipper.config import get_ffprobe
        try:
            probe = subprocess.run(
                [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True,
            )
            vid_duration = float(probe.stdout.strip())
            progress_filter = _build_progress_bar_filter(vid_duration)
            vf = f"{vf},{progress_filter}"
        except (ValueError, subprocess.SubprocessError):
            pass  # skip progress bar if probe fails

        # Hook text overlay (rendered last = always on top)
        hook_filter = _build_hook_filter(hook_text, config)
        vf = f"{vf},{hook_filter}"

    cmd = [
        ffmpeg_bin,
        "-i", str(video_path),
        "-vf", vf,
        *get_encoder_args(),
        "-c:a", "aac",
        "-b:a", "256k",
        "-y",
        str(output_path),
    ]

    console.print(f"[blue]Burning subtitles:[/blue] {video_path.name}")
    if verbose:
        console.print(f"[dim]FFmpeg: {ffmpeg_bin}[/dim]")
        console.print(f"[dim]Filter: {vf}[/dim]")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if verbose:
            console.print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]FFmpeg failed:[/red] {e.stderr[-300:]}")
        raise

    console.print(f"[green]Subtitled video:[/green] {output_path.name}")
    return output_path
