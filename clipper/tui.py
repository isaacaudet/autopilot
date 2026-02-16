"""Interactive TUI for running the full Clipper pipeline.

Uses a Rich Live dashboard to show real-time per-worker progress during
processing, compile progress, and upload status. Pauses Live for
interactive questionary prompts.
"""

import json
import os
import time

import questionary
import requests
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from clipper.config import load_config, require_env
from clipper.tui_state import PipelineState

console = Console()

HELIX_BASE = "https://api.twitch.tv/helix"


# ---------------------------------------------------------------------------
# ClipperDashboard — manages Rich Live lifecycle and phase renderers
# ---------------------------------------------------------------------------


class ClipperDashboard:
    """Rich Live dashboard showing per-worker progress and ETAs."""

    def __init__(self, state: PipelineState) -> None:
        self.state = state
        self._live: Live | None = None
        self._phase: str = "process"  # process | compile | upload
        self._silenced: dict[str, Console] = {}  # module_path -> original console

    def start(self) -> None:
        self._silence_consoles()
        self._live = Live(self._render(), console=console, refresh_per_second=4)
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None
        self._restore_consoles()

    def pause(self) -> None:
        """Pause Live so questionary prompts work."""
        if self._live:
            self._live.stop()
            self._live = None
        self._restore_consoles()

    def resume(self) -> None:
        """Resume Live after interactive prompt."""
        self._silence_consoles()
        self._live = Live(self._render(), console=console, refresh_per_second=4)
        self._live.start()

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    def update(self) -> None:
        if self._live:
            self._live.update(self._render())

    def _render(self):
        if self._phase == "process":
            return self._render_process()
        elif self._phase == "compile":
            return self._render_compile()
        elif self._phase == "upload":
            return self._render_upload()
        return self._render_process()

    # -- Phase renderers --

    def _render_process(self):
        st = self.state
        done = st.completed + st.failed
        total = max(st.total_clips, 1)
        pct = done / total

        # Progress bar text
        filled = int(pct * 20)
        bar = "\u2588" * filled + "\u2591" * (20 - filled)

        elapsed = st.elapsed()
        elapsed_str = _fmt_duration(elapsed)
        eta = st.eta_seconds()
        eta_str = f"~{_fmt_duration(eta)}" if eta is not None else "..."

        header = Text()
        header.append(f"  Processing clips  {done}/{total}  ", style="bold")
        header.append(bar, style="cyan")

        lines = Text()
        lines.append(f"\n  Elapsed: {elapsed_str}  |  ETA: {eta_str}  |  OK: {st.completed}", style="dim")
        if st.failed:
            lines.append(f"  |  Failed: {st.failed}", style="red")
        lines.append("\n")

        # Worker table
        worker_table = Table(show_header=True, show_edge=False, pad_edge=False, box=None)
        worker_table.add_column("Worker", style="bold", width=8)
        worker_table.add_column("Clip", width=36, no_wrap=True)
        worker_table.add_column("Step", width=12)

        for label in sorted(st.workers.keys()):
            ws = st.workers[label]
            if ws.step in ("done", "error"):
                continue
            step_style = "green" if ws.step == "burning" else "yellow" if ws.step == "transcribing" else "blue"
            step_text = ws.step[:12]
            worker_table.add_row(label, ws.clip_title[:36], Text(step_text, style=step_style))

        # Completed / error log (last 5)
        log = Text()
        for name in st.completed_clips[-5:]:
            log.append(f"\n  \u2713 {name}", style="green")
        for err in st.errors[-3:]:
            log.append(f"\n  \u2717 {err}", style="red")

        group = Text()
        group.append_text(header)
        group.append_text(lines)

        panel_content = Table(show_header=False, show_edge=False, box=None, pad_edge=False)
        panel_content.add_row(header)
        panel_content.add_row(lines)
        panel_content.add_row(worker_table)
        panel_content.add_row(log)

        return Panel(panel_content, title="Clipper Processing", border_style="cyan")

    def _render_compile(self):
        st = self.state
        step = st.compile_step or "Waiting..."
        pct = st.compile_progress
        filled = int(pct * 20)
        bar = "\u2588" * filled + "\u2591" * (20 - filled)

        content = Text()
        content.append(f"  {step}\n\n", style="bold")
        content.append(f"  {bar}  {pct*100:.0f}%", style="cyan")

        return Panel(content, title="Clipper Compiling", border_style="cyan")

    def _render_upload(self):
        st = self.state
        done = st.uploads_done
        total = max(st.uploads_total, 1)
        filled = int((done / total) * 20)
        bar = "\u2588" * filled + "\u2591" * (20 - filled)

        content = Text()
        content.append(f"  Uploading  {done}/{total}\n\n", style="bold")
        content.append(f"  {bar}", style="cyan")

        return Panel(content, title="Clipper Upload", border_style="cyan")

    # -- Console silencing (suppress module-level prints during Live) --

    _MODULES_TO_SILENCE = [
        "clipper.process.download",
        "clipper.process.subtitles",
        "clipper.process.format",
        "clipper.process.burn",
        "clipper.process.trim",
        "clipper.process.compile",
    ]

    def _silence_consoles(self) -> None:
        """Replace module-level `console` objects with /dev/null writers."""
        import sys
        for mod_path in self._MODULES_TO_SILENCE:
            mod = sys.modules.get(mod_path)
            if mod and hasattr(mod, "console"):
                self._silenced[mod_path] = mod.console
                mod.console = Console(file=open(os.devnull, "w"))

    def _restore_consoles(self) -> None:
        """Restore original module-level consoles."""
        import sys
        for mod_path, orig in self._silenced.items():
            mod = sys.modules.get(mod_path)
            if mod:
                # Close the devnull console's file handle
                if hasattr(mod.console, "file") and mod.console.file.name == os.devnull:
                    mod.console.file.close()
                mod.console = orig
        self._silenced.clear()


def _fmt_duration(secs: float) -> str:
    if secs <= 0:
        return "0s"
    m, s = divmod(int(secs), 60)
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Twitch helpers (unchanged logic, kept here for run_tui)
# ---------------------------------------------------------------------------


def _twitch_auth() -> dict:
    client_id = require_env("TWITCH_CLIENT_ID")
    client_secret = require_env("TWITCH_CLIENT_SECRET")
    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {"Client-ID": client_id, "Authorization": f"Bearer {token}"}


def _fetch_top_games(headers: dict, count: int = 20) -> list[dict]:
    resp = requests.get(f"{HELIX_BASE}/games/top", headers=headers, params={"first": count}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _pick_source() -> str:
    return "twitch"


def _pick_mode() -> str:
    return questionary.select(
        "Find clips by:",
        choices=[
            "search all (best clips across all trending games)",
            "browse trending games",
            "enter game/category names",
            "enter streamer names",
            "use config.yaml defaults",
        ],
    ).ask()


def _pick_games_interactive(headers: dict) -> list[str]:
    console.print("[dim]Loading top Twitch games...[/dim]")
    games = _fetch_top_games(headers)
    if not games:
        console.print("[yellow]Could not load games.[/yellow]")
        return []

    table = Table(title="Top Twitch Games Right Now")
    table.add_column("#", style="dim", width=4)
    table.add_column("Game", style="cyan")
    for i, g in enumerate(games, 1):
        table.add_row(str(i), g["name"])
    console.print(table)

    choices = [g["name"] for g in games]
    selected = questionary.checkbox(
        "Pick games to clip from (space to select, enter to confirm):",
        choices=choices,
    ).ask()
    return selected or []


def _pick_streamers_interactive() -> list[str]:
    raw = questionary.text("Enter streamer names (comma-separated):").ask()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _pick_clips_per_source() -> int:
    val = questionary.select("Clips per source:", choices=["5", "10", "20", "50"], default="10").ask()
    return int(val)


def _run_fetch(config: dict, source: str, verbose: bool = False, discover_mode: bool = False) -> list[dict]:
    from clipper.fetch.twitch import fetch_twitch_clips
    if not config["targets"].get("twitch"):
        return []
    return fetch_twitch_clips(config, dry_run=False, verbose=verbose, discover_mode=discover_mode)


def _run_review(config: dict) -> None:
    from clipper.queue.review import run_review
    run_review(config)


def _run_upload(config: dict, verbose: bool = False) -> None:
    from clipper.upload.youtube import upload_clip

    output_dir = config["_output_dir"]
    meta_files = sorted(output_dir.glob("*.json"))
    if not meta_files:
        console.print("[yellow]No processed clips to upload.[/yellow]")
        return

    uploadable = []
    for mf in meta_files:
        with open(mf) as f:
            clip = json.load(f)
        if "processed_path" in clip:
            uploadable.append((mf, clip))

    if not uploadable:
        console.print("[yellow]No clips ready for upload.[/yellow]")
        return

    console.print(f"\n[bold]{len(uploadable)} clip(s) ready to upload.[/bold]")

    privacy_choice = questionary.select(
        "Upload privacy:",
        choices=["unlisted", "public", "private", "skip upload"],
        default="unlisted",
    ).ask()

    if privacy_choice == "skip upload":
        return

    for meta_file, clip in uploadable:
        if clip.get("video_id"):
            console.print(
                f"[dim]Already uploaded:[/dim] {clip.get('title', meta_file.stem)[:50]}"
                f" \u2192 https://youtube.com/watch?v={clip['video_id']}"
            )
            continue

        console.print(f"\n[bold]Uploading:[/bold] {clip.get('title', meta_file.stem)}")
        video_id = upload_clip(clip, config, privacy=privacy_choice, verbose=verbose)
        if video_id:
            clip["video_id"] = video_id
            with open(meta_file, "w") as f:
                json.dump(clip, f, indent=2)
            console.print(f"[green]Uploaded:[/green] https://youtube.com/watch?v={video_id}")
        else:
            console.print("[red]Upload failed.[/red]")


# ---------------------------------------------------------------------------
# Main TUI entry point
# ---------------------------------------------------------------------------


def run_tui(verbose: bool = False) -> None:
    """Run the full interactive pipeline with a Live dashboard during processing."""
    from clipper.workflow import _process_clips

    config = load_config()

    console.print("[bold cyan]Clipper[/bold cyan] \u2014 Interactive Pipeline\n")

    # -- Interactive prompts (no Live) --

    source = _pick_source()
    if not source:
        return

    mode = _pick_mode()
    if not mode:
        return

    if mode == "browse trending games":
        headers = _twitch_auth()
        games = _pick_games_interactive(headers)
        if not games:
            console.print("[yellow]No games selected.[/yellow]")
            return
        config["targets"]["twitch"]["games"] = games
        config["targets"]["twitch"]["streamers"] = []
        clips_count = _pick_clips_per_source()
        config["targets"]["twitch"]["clips_per_source"] = clips_count

    elif mode == "enter game/category names":
        raw = questionary.text("Enter game/category names (comma-separated):").ask()
        if not raw:
            return
        games = [g.strip() for g in raw.split(",") if g.strip()]
        config["targets"]["twitch"]["games"] = games
        config["targets"]["twitch"]["streamers"] = []
        clips_count = _pick_clips_per_source()
        config["targets"]["twitch"]["clips_per_source"] = clips_count

    elif mode == "enter streamer names":
        streamers = _pick_streamers_interactive()
        if not streamers:
            console.print("[yellow]No streamers entered.[/yellow]")
            return
        config["targets"]["twitch"]["streamers"] = streamers
        config["targets"]["twitch"]["games"] = []
        clips_count = _pick_clips_per_source()
        config["targets"]["twitch"]["clips_per_source"] = clips_count

    # -- Fetch (spinner) --

    discover = mode.startswith("search all")
    console.print("\n[bold]Step 1/4: Fetching clips...[/bold]")
    clips = _run_fetch(config, source, verbose=verbose, discover_mode=discover)
    if not clips:
        console.print("[yellow]No clips found matching filters.[/yellow]")
        return

    console.print(f"\n[bold green]{len(clips)} clip(s) fetched and queued.[/bold green]")

    # -- Review (interactive, no Live) --

    review_mode = questionary.select(
        "Review clips:",
        choices=[
            "auto-pick top clips (by score)",
            "manual review (one by one)",
            "skip review",
        ],
    ).ask()

    if review_mode and "auto-pick" in review_mode:
        from clipper.queue.review import auto_approve_top
        top_n = int(questionary.select(
            "How many top clips?",
            choices=["3", "5", "10", "20"],
            default="5",
        ).ask())
        console.print(f"\n[bold]Step 2/4: Auto-picking top {top_n} clips...[/bold]")
        auto_approve_top(config, top_n=top_n, min_score=30)
    elif review_mode and "manual" in review_mode:
        console.print("\n[bold]Step 2/4: Review clips...[/bold]")
        _run_review(config)

    approved_dir = config["_queue_dir"] / "approved"
    approved_count = len(list(approved_dir.glob("*.json")))
    if approved_count == 0:
        console.print("[yellow]No approved clips. Pipeline complete.[/yellow]")
        return

    # -- Process (Live dashboard) --

    do_process = questionary.confirm(
        f"Process {approved_count} approved clip(s)? (download + subtitles + format)",
        default=True,
    ).ask()

    if do_process:
        console.print("\n[bold]Step 3/4: Processing clips...[/bold]")

        state = PipelineState()
        dashboard = ClipperDashboard(state)
        dashboard.set_phase("process")
        dashboard.start()

        # Background thread to refresh dashboard
        import threading
        _stop_refresh = threading.Event()

        def _refresh_loop():
            while not _stop_refresh.is_set():
                dashboard.update()
                _stop_refresh.wait(0.25)

        refresh_thread = threading.Thread(target=_refresh_loop, daemon=True)
        refresh_thread.start()

        try:
            _process_clips(config, verbose=verbose, state=state)
        finally:
            _stop_refresh.set()
            refresh_thread.join(timeout=1)
            dashboard.stop()

        console.print(
            f"\n[bold]Results:[/bold] {state.completed} succeeded, {state.failed} failed"
        )

    # -- Upload (interactive prompt then progress) --

    do_upload = questionary.confirm("Upload to YouTube?", default=False).ask()
    if do_upload:
        console.print("\n[bold]Step 4/4: Uploading...[/bold]")
        _run_upload(config, verbose=verbose)

    console.print("\n[bold green]Pipeline complete![/bold green]")
