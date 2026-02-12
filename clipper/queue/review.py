"""Interactive CLI review of pending clips."""

import json
import shutil
import webbrowser
from pathlib import Path

import questionary
from rich.console import Console
from rich.table import Table

console = Console()


def _load_pending_clips(pending_dir: Path) -> list[tuple[Path, dict]]:
    """Load all valid JSON clip files from the pending directory."""
    clips = []
    for path in sorted(pending_dir.glob("*.json")):
        try:
            with open(path) as f:
                clip = json.load(f)
            clips.append((path, clip))
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[yellow]Skipping corrupt file {path.name}: {e}[/yellow]")
    return clips


def _display_summary_table(clips: list[tuple[Path, dict]]) -> None:
    """Display a rich table summarizing all pending clips, sorted by score."""
    from clipper.process.score import score_clip

    # Score and sort clips
    scored = []
    for path, clip in clips:
        clip["score"] = score_clip(clip)
        scored.append((path, clip))
    scored.sort(key=lambda x: x[1].get("score", 0), reverse=True)

    table = Table(title="Pending Clips (sorted by virality score)")
    table.add_column("#", style="dim", width=4)
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Title", max_width=35)
    table.add_column("Streamer")
    table.add_column("Platform")
    table.add_column("Views", justify="right")
    table.add_column("Duration", justify="right")

    for i, (_path, clip) in enumerate(scored, 1):
        s = clip.get("score", 0)
        score_style = "green" if s >= 50 else "yellow" if s >= 30 else "red"
        table.add_row(
            str(i),
            f"[{score_style}]{s:.0f}[/{score_style}]",
            clip.get("title", "—"),
            clip.get("streamer", "—"),
            clip.get("platform", "—"),
            str(clip.get("view_count", "—")),
            f"{clip.get('duration', '—')}s" if clip.get("duration") else "—",
        )

    console.print(table)
    console.print(f"\n[bold]{len(clips)}[/bold] clip(s) pending review.\n")


def _display_clip_details(index: int, total: int, clip: dict) -> None:
    """Print details for a single clip before prompting."""
    score = clip.get("score", 0)
    score_style = "green" if score >= 50 else "yellow" if score >= 30 else "red"
    console.print(f"\n[bold cyan]— Clip {index}/{total} —[/bold cyan]  [{score_style}]Score: {score:.0f}/100[/{score_style}]")
    console.print(f"  [bold]Title:[/bold]    {clip.get('title', '—')}")
    console.print(f"  [bold]Streamer:[/bold] {clip.get('streamer', '—')}")
    console.print(f"  [bold]Platform:[/bold] {clip.get('platform', '—')}")
    console.print(f"  [bold]Views:[/bold]    {clip.get('view_count', '—')}")
    console.print(f"  [bold]Duration:[/bold] {clip.get('duration', '—')}s")
    console.print(f"  [bold]URL:[/bold]      {clip.get('url', '—')}")


def auto_approve_top(config: dict, top_n: int = 5, min_score: int = 30) -> int:
    """Auto-approve the top N clips by virality score.

    Skips the interactive review entirely — lets the scoring algorithm pick.
    Returns the number of clips approved.
    """
    from clipper.process.score import score_clip

    queue_dir: Path = config["_queue_dir"]
    pending_dir = queue_dir / "pending"
    approved_dir = queue_dir / "approved"
    skipped_dir = queue_dir / "skipped"

    approved_dir.mkdir(parents=True, exist_ok=True)
    skipped_dir.mkdir(parents=True, exist_ok=True)

    clips = _load_pending_clips(pending_dir)
    if not clips:
        console.print("[yellow]No pending clips to auto-approve.[/yellow]")
        return 0

    # Filter non-English clips before scoring
    from clipper.process.titles import is_english_clip

    filtered = []
    for path, clip in clips:
        if is_english_clip(clip):
            filtered.append((path, clip))
        else:
            shutil.move(str(path), str(skipped_dir / path.name))
            console.print(f"[dim]  Skipped (non-English):[/dim] {clip.get('title', path.name)[:50]}")

    clips = filtered
    if not clips:
        console.print("[yellow]No English clips to auto-approve.[/yellow]")
        return 0

    # Score and rank
    for _path, clip in clips:
        clip["score"] = score_clip(clip)
    ranked = sorted(clips, key=lambda x: x[1].get("score", 0), reverse=True)

    _display_summary_table(ranked)

    approved = 0
    skipped_count = 0

    for path, clip in ranked:
        score = clip.get("score", 0)
        if approved < top_n and score >= min_score:
            shutil.move(str(path), str(approved_dir / path.name))
            console.print(f"[green]  Auto-approved (score {score:.0f}):[/green] {clip.get('title', path.name)[:50]}")
            approved += 1
        else:
            shutil.move(str(path), str(skipped_dir / path.name))
            skipped_count += 1

    console.print(f"\n[bold]Auto-review:[/bold] {approved} approved, {skipped_count} skipped (min score: {min_score})")
    return approved


def run_review(config: dict) -> None:
    """Interactively review pending clips."""
    queue_dir: Path = config["_queue_dir"]
    pending_dir = queue_dir / "pending"
    approved_dir = queue_dir / "approved"
    skipped_dir = queue_dir / "skipped"

    # Ensure target directories exist
    approved_dir.mkdir(parents=True, exist_ok=True)
    skipped_dir.mkdir(parents=True, exist_ok=True)

    clips = _load_pending_clips(pending_dir)

    if not clips:
        console.print("[yellow]No pending clips to review.[/yellow]")
        return

    _display_summary_table(clips)

    kept = 0
    skipped = 0

    for i, (path, clip) in enumerate(clips, 1):
        reviewing = True
        while reviewing:
            _display_clip_details(i, len(clips), clip)

            action = questionary.select(
                "Action:",
                choices=["keep", "skip", "shorts", "open", "quit"],
            ).ask()

            if action is None or action == "quit":
                console.print("[bold]Stopping review.[/bold]")
                remaining = len(clips) - kept - skipped
                _print_summary(kept, skipped, remaining)
                return

            if action == "open":
                url = clip.get("url")
                if url:
                    webbrowser.open(url)
                    console.print("[dim]Opened in browser.[/dim]")
                else:
                    console.print("[yellow]No URL available.[/yellow]")
                # Re-prompt for this clip
                continue

            if action == "keep":
                shutil.move(str(path), str(approved_dir / path.name))
                console.print(f"[green]✓ Approved:[/green] {path.name}")
                kept += 1

            elif action == "skip":
                shutil.move(str(path), str(skipped_dir / path.name))
                console.print(f"[dim]✗ Skipped:[/dim] {path.name}")
                skipped += 1

            elif action == "shorts":
                clip["force_shorts"] = True
                with open(path, "w") as f:
                    json.dump(clip, f, indent=2)
                shutil.move(str(path), str(approved_dir / path.name))
                console.print(f"[green]✓ Approved (Shorts):[/green] {path.name}")
                kept += 1

            reviewing = False

    remaining = len(clips) - kept - skipped
    _print_summary(kept, skipped, remaining)


def _print_summary(kept: int, skipped: int, remaining: int) -> None:
    """Print the final review summary."""
    console.print(f"\n[bold]Review complete:[/bold] {kept} kept, {skipped} skipped, {remaining} remaining")
