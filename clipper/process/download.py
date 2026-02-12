"""Download clips using yt-dlp."""

import subprocess
from pathlib import Path

from rich.console import Console

console = Console()


def download_clip(url: str, output_dir: Path, verbose: bool = False) -> Path | None:
    """Download a clip from the given URL using yt-dlp.

    Returns the Path to the downloaded mp4 file, or None on failure.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(output_dir / "%(display_id)s.%(ext)s")

    # First, get the expected filename without downloading
    probe_cmd = [
        "yt-dlp",
        "--print", "filename",
        "-o", output_template,
        "--merge-output-format", "mp4",
        url,
    ]

    console.print(f"[blue]Downloading:[/blue] {url}")

    try:
        probe_result = subprocess.run(
            probe_cmd, capture_output=True, text=True, check=True
        )
        expected_path = Path(probe_result.stdout.strip())
        # Ensure .mp4 extension
        expected_path = expected_path.with_suffix(".mp4")
    except (subprocess.CalledProcessError, ValueError):
        expected_path = None

    # Skip if already downloaded
    if expected_path and expected_path.exists():
        console.print(f"[green]Already downloaded:[/green] {expected_path.name}")
        return expected_path

    # Download
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", output_template,
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if verbose:
            console.print(result.stdout)
    except FileNotFoundError:
        console.print("[red]Error: yt-dlp is not installed. Install it with: pip install yt-dlp[/red]")
        return None
    except subprocess.CalledProcessError as e:
        console.print(f"[red]yt-dlp failed:[/red] {e.stderr.strip()}")
        return None

    # Use the expected path if we have it
    if expected_path and expected_path.exists():
        console.print(f"[green]Downloaded:[/green] {expected_path.name}")
        return expected_path

    # Fallback: find the file by yt-dlp's output (parse "Merging formats into" or "[download] ... has already been downloaded")
    for line in result.stdout.splitlines():
        if "Merging formats into" in line:
            # Extract path from: Merging formats into "path/to/file.mp4"
            start = line.find('"')
            end = line.rfind('"')
            if start != -1 and end > start:
                path = Path(line[start + 1:end])
                if path.exists():
                    console.print(f"[green]Downloaded:[/green] {path.name}")
                    return path
        if "has already been downloaded" in line:
            start = line.find("[download] ")
            if start != -1:
                path_str = line[start + len("[download] "):].split(" has already")[0]
                path = Path(path_str)
                if path.exists():
                    console.print(f"[green]Already downloaded:[/green] {path.name}")
                    return path

    console.print("[red]Download completed but could not locate the file.[/red]")
    return None
