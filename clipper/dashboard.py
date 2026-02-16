"""Clipper Dashboard — Premium full-screen TUI with live pipeline, queue, analytics, calendar."""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    OptionList,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from clipper.tui_state import PipelineState


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _fmt_duration(secs: float | None) -> str:
    if secs is None or secs <= 0:
        return "0s"
    m, s = divmod(int(secs), 60)
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------

class MetricCard(Static):
    """Bordered card showing a label + value."""

    value: reactive[str] = reactive("—")

    def __init__(self, label: str, value: str = "—", **kwargs):
        super().__init__(**kwargs)
        self._label = label
        self.value = value

    def render(self) -> str:
        return f"[dim]{self._label}[/dim]\n[bold]{self.value}[/bold]"


class WorkerPanel(Static):
    """Shows 3 fixed worker rows."""

    worker_data: reactive[dict] = reactive(dict, always_update=True)

    def render(self) -> str:
        lines = []
        for i in range(1, 4):
            label = f"W{i}"
            info = self.worker_data.get(label)
            if info:
                title, step = info
                if step in ("done", "error", "idle"):
                    lines.append(f"  [dim]{label} (idle)[/dim]")
                else:
                    color = {"burning": "green", "transcribing": "yellow",
                             "downloading": "blue", "formatting": "cyan"}.get(step, "white")
                    lines.append(f"  {label} {title[:30]:<30}  [{color}]{step}[/{color}]")
            else:
                lines.append(f"  [dim]{label} (idle)[/dim]")
        return "\n".join(lines)


class PipelineProgressBar(Static):
    """Unicode block progress bar with elapsed + ETA."""

    completed: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    elapsed: reactive[float] = reactive(0.0)
    eta: reactive[float | None] = reactive(None)

    def render(self) -> str:
        t = max(self.total, 1)
        pct = min(self.completed / t, 1.0)
        width = 30
        filled = int(pct * width)
        bar = "\u2588" * filled + "\u2591" * (width - filled)
        color = "green" if pct >= 1.0 else "cyan"
        elapsed_str = _fmt_duration(self.elapsed)
        eta_str = f"ETA ~{_fmt_duration(self.eta)}" if self.eta is not None else "..."
        return f"  [{color}]{bar}[/{color}]  {pct*100:.0f}%  {elapsed_str}  {eta_str}"


class StatusBar(Static):
    """Bottom status bar showing game, channel, cron status."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._game = ""
        self._channel = ""
        self._cron = False

    def set_info(self, game: str = "", channel: str = "", cron: bool = False) -> None:
        self._game = game
        self._channel = channel
        self._cron = cron
        self.update(self.render())

    def render(self) -> str:
        parts = []
        if self._game:
            parts.append(f"Game: [cyan]{self._game}[/cyan]")
        if self._channel:
            parts.append(f"Ch: [cyan]{self._channel}[/cyan]")
        cron_str = "[green]active[/green]" if self._cron else "[dim]off[/dim]"
        parts.append(f"Cron: {cron_str}")
        return "  " + "  |  ".join(parts)


# ---------------------------------------------------------------------------
# Workflow Modal
# ---------------------------------------------------------------------------

RECIPES = [
    ("quick_short", "Quick Short", "1 clip, upload now"),
    ("batch_shorts", "Batch Shorts", "N shorts, scheduled"),
    ("compilation", "Compilation", "8-15 min, scheduled"),
    ("generate", "Generate", "N clips, save only"),
]


class WorkflowModal(ModalScreen[dict | None]):
    """Modal overlay for picking and configuring a workflow recipe."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self._step = 1
        self._recipe: str = ""
        self._params: dict = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="workflow-modal"):
            yield Static("[bold]Quick Workflow[/bold]", id="modal-title")
            yield OptionList(
                *[Option(f"{name} \u2014 {desc}", id=key) for key, name, desc in RECIPES],
                id="recipe-list",
            )
            yield Static("", id="config-display")

    def on_mount(self) -> None:
        self.query_one("#config-display", Static).display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._step == 1:
            self._recipe = event.option.id
            self._show_config()
        elif self._step == 2:
            text = event.option.prompt
            if isinstance(text, str):
                if "Launch" in text:
                    self.dismiss({"recipe": self._recipe, **self._params})
                    return
                self._apply_config_option(text)
                self._rebuild_config_list()
                self._update_config_summary()

    def _apply_config_option(self, text: str) -> None:
        """Apply a config option from its display text (strips checkmark prefix)."""
        clean = text.lstrip("\u2713 ")
        if clean.startswith("Game:"):
            self._params["game"] = clean.split(": ", 1)[1]
        elif clean.startswith("Count:"):
            self._params["count"] = int(clean.split(": ")[1])
        elif clean.startswith("Duration:"):
            self._params["duration"] = int(clean.split(": ")[1].replace(" min", ""))
        elif clean.startswith("Channel:"):
            self._params["channel"] = clean.split(": ", 1)[1]

    def _show_config(self) -> None:
        """Replace recipe list with config options."""
        self._step = 2
        self._games = self._config.get("targets", {}).get("twitch", {}).get("games", ["Deadlock"])
        self._channels = list(self._config.get("channels", {}).keys())

        # Set defaults
        self._params = {
            "game": self._games[0] if self._games else "Deadlock",
            "channel": self._channels[0] if self._channels else None,
            "count": 5,
            "duration": 10,
        }

        recipe_name = next((n for k, n, _ in RECIPES if k == self._recipe), "Workflow")
        self.query_one("#modal-title", Static).update(f"[bold]{recipe_name}[/bold]")

        self._rebuild_config_list()
        self._update_config_summary()

    def _rebuild_config_list(self) -> None:
        """Rebuild the config option list with checkmarks on active selections."""
        options: list[str] = []
        for g in self._games:
            mark = "\u2713 " if g == self._params["game"] else "  "
            options.append(f"{mark}Game: {g}")
        if self._recipe in ("batch_shorts", "generate"):
            for c in (3, 5, 10):
                mark = "\u2713 " if c == self._params["count"] else "  "
                options.append(f"{mark}Count: {c}")
        elif self._recipe == "compilation":
            for d in (8, 10, 12, 15):
                mark = "\u2713 " if d == self._params["duration"] else "  "
                options.append(f"{mark}Duration: {d} min")
        if self._channels:
            for ch in self._channels:
                mark = "\u2713 " if ch == self._params["channel"] else "  "
                options.append(f"{mark}Channel: {ch}")
        options.append("\u2192\u2192\u2192 Launch \u2190\u2190\u2190")

        recipe_list = self.query_one("#recipe-list", OptionList)
        highlighted = recipe_list.highlighted
        recipe_list.clear_options()
        for opt in options:
            recipe_list.add_option(Option(opt))
        if highlighted is not None and highlighted < len(options):
            recipe_list.highlighted = highlighted

    def _update_config_summary(self) -> None:
        """Update the summary bar below the option list."""
        summary = (
            f"Game: [cyan]{self._params.get('game', '?')}[/cyan]  "
            f"Channel: [cyan]{self._params.get('channel', 'none')}[/cyan]"
        )
        if self._recipe in ("batch_shorts", "generate"):
            summary += f"  Count: [cyan]{self._params.get('count', 5)}[/cyan]"
        elif self._recipe == "compilation":
            summary += f"  Duration: [cyan]{self._params.get('duration', 10)} min[/cyan]"

        config_display = self.query_one("#config-display", Static)
        config_display.update(summary)
        config_display.display = True

    def action_cancel(self) -> None:
        if self._step == 2:
            # Go back to recipe picker
            self._step = 1
            self.query_one("#modal-title", Static).update("[bold]Quick Workflow[/bold]")
            recipe_list = self.query_one("#recipe-list", OptionList)
            recipe_list.clear_options()
            for key, name, desc in RECIPES:
                recipe_list.add_option(Option(f"{name} \u2014 {desc}", id=key))
            self.query_one("#config-display", Static).display = False
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Cron Modal
# ---------------------------------------------------------------------------


class CronModal(ModalScreen):
    """Shows cron status and install/remove toggle."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "toggle", "Install/Remove"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="cron-modal"):
            yield Static("[bold]Cron Setup[/bold]", id="modal-title")
            yield Static("Loading...", id="cron-status")

    def on_mount(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        from clipper import cron as cron_mod

        status = cron_mod.get_status()
        installed = status["installed"]
        text = (
            f"Platform:  {status['platform']}\n"
            f"Status:    {'[green]Installed[/green]' if installed else '[yellow]Not installed[/yellow]'}\n"
            f"Interval:  {status['interval']}\n"
            f"Command:   {status['command']}\n\n"
            f"{'[dim]Press Enter to remove[/dim]' if installed else '[bold]Press Enter to install[/bold]'}"
        )
        self.query_one("#cron-status", Static).update(text)

    def action_toggle(self) -> None:
        from clipper import cron as cron_mod

        if cron_mod.is_installed():
            cron_mod.remove()
            self.notify("Cron job removed")
        else:
            if cron_mod.install():
                self.notify("Cron job installed!")
            else:
                self.notify("Failed to install cron job", severity="error")
        self._refresh_status()

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Console redirection for module-level prints during pipeline
# ---------------------------------------------------------------------------

_MODULES_TO_SILENCE = [
    "clipper.process.download",
    "clipper.process.subtitles",
    "clipper.process.format",
    "clipper.process.burn",
    "clipper.process.trim",
    "clipper.process.compile",
]


def _silence_consoles() -> dict:
    """Replace module-level console objects with /dev/null writers. Returns originals."""
    import sys
    from rich.console import Console

    silenced = {}
    for mod_path in _MODULES_TO_SILENCE:
        mod = sys.modules.get(mod_path)
        if mod and hasattr(mod, "console"):
            silenced[mod_path] = mod.console
            mod.console = Console(file=open(os.devnull, "w"))
    return silenced


def _restore_consoles(silenced: dict) -> None:
    """Restore original module-level consoles."""
    import sys

    for mod_path, orig in silenced.items():
        mod = sys.modules.get(mod_path)
        if mod:
            if hasattr(mod.console, "file") and getattr(mod.console.file, "name", "") == os.devnull:
                mod.console.file.close()
            mod.console = orig


# ---------------------------------------------------------------------------
# Main Dashboard App
# ---------------------------------------------------------------------------

class ClipperDashboard(App):
    """Full-screen TUI dashboard for the Clipper pipeline."""

    CSS_PATH = "dashboard.tcss"
    TITLE = "Clipper Dashboard"

    BINDINGS = [
        Binding("1", "tab_pipeline", "Pipeline", show=True),
        Binding("2", "tab_queue", "Queue", show=True),
        Binding("3", "tab_analytics", "Analytics", show=True),
        Binding("4", "tab_calendar", "Calendar", show=True),
        Binding("w", "workflow", "Workflow"),
        Binding("c", "cron_setup", "Cron"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._config: dict | None = None
        self._pipeline_state: PipelineState | None = None
        self._poll_timer = None
        self._silenced: dict = {}
        # Analytics cache
        self._analytics_cache: list[dict] = []
        self._analytics_ts: float = 0.0
        # Track what's been logged to avoid duplicate log entries
        self._logged_completed: int = 0
        self._logged_errors: int = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Pipeline", id="tab-pipeline"):
                with Vertical(id="pipeline"):
                    with Horizontal(classes="metrics-row"):
                        yield MetricCard("Fetched", "0", id="metric-fetched")
                        yield MetricCard("Scored", "0", id="metric-scored")
                        yield MetricCard("Processing", "0/0", id="metric-processing")
                        yield MetricCard("Done", "0", id="metric-done")
                    with Horizontal(id="pipeline-content"):
                        with Vertical(id="worker-table"):
                            yield Static("[bold]Workers[/bold]", classes="text-primary")
                            yield WorkerPanel(id="worker-panel")
                        with Vertical(id="log-panel"):
                            yield Static("[bold]Log[/bold]", classes="text-primary")
                            yield RichLog(id="pipeline-log", markup=True, max_lines=50)
                    yield PipelineProgressBar(id="pipeline-progress")
            with TabPane("Queue", id="tab-queue"):
                with Vertical(id="queue"):
                    yield Static(
                        "[bold reverse] Pending [/bold reverse]  Approved  Output  "
                        "[dim]Tab/Shift+Tab to switch | /=search | s=sort[/dim]",
                        id="queue-subtabs",
                    )
                    yield DataTable(id="queue-table")
                    yield Static("[dim]Select a clip[/dim]", id="queue-detail")
            with TabPane("Analytics", id="tab-analytics"):
                with Vertical(id="analytics"):
                    with Horizontal(classes="metrics-row"):
                        yield MetricCard("Total Views", "\u2014", id="metric-views")
                        yield MetricCard("Avg/Video", "\u2014", id="metric-avg")
                        yield MetricCard("Videos", "\u2014", id="metric-count")
                    yield DataTable(id="analytics-table")
                    yield Static("[dim]Learned weights: not loaded[/dim]", id="learned-weights")
            with TabPane("Calendar", id="tab-calendar"):
                with Vertical(id="calendar"):
                    yield Static("[bold]This Week[/bold]", id="calendar-header")
                    with Horizontal(id="weekly-grid"):
                        for day_name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
                            with Vertical(classes="day-column"):
                                yield Static(f"[bold]{day_name}[/bold]", classes="day-header")
                                yield Static("", id=f"day-{day_name.lower()}")
                    yield Static("", id="release-detail")
                    yield Static("", id="calendar-summary")
        yield StatusBar(id="status-bar")
        yield Footer()

    # -- Lifecycle --

    def on_mount(self) -> None:
        self._load_config()

    @work(thread=True)
    def _load_config(self) -> None:
        from clipper.config import load_config
        self._config = load_config()
        self.call_from_thread(self._on_config_loaded)

    def _on_config_loaded(self) -> None:
        self._setup_tables()
        self._update_status_bar()
        self._populate_queue()

    def _setup_tables(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "St", "Streamer", "Title", "Score", "Dur")

        analytics_table = self.query_one("#analytics-table", DataTable)
        analytics_table.cursor_type = "row"
        analytics_table.add_columns("#", "Title", "Views", "Likes", "Published")

    def _update_status_bar(self) -> None:
        bar = self.query_one("#status-bar", StatusBar)
        games = self._config.get("targets", {}).get("twitch", {}).get("games", [])
        channels = list(self._config.get("channels", {}).keys())

        cron_installed = False
        try:
            from clipper import cron as cron_mod
            cron_installed = cron_mod.is_installed()
        except Exception:
            pass

        bar.set_info(
            game=games[0] if games else "",
            channel=channels[0] if channels else "",
            cron=cron_installed,
        )

    # -- Tab switching --

    def action_tab_pipeline(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-pipeline"

    def action_tab_queue(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-queue"
        self._populate_queue()

    def action_tab_analytics(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-analytics"
        self._load_analytics()

    def action_tab_calendar(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-calendar"
        self._load_calendar()

    # -- Queue Tab --

    _queue_subtab: int = 0  # 0=pending, 1=approved, 2=output
    _queue_sort: int = 0
    _queue_clips: list = []
    _queue_filtered: list = []

    QUEUE_SUBTAB_NAMES = ["Pending", "Approved", "Output"]
    QUEUE_SORT_MODES = ["date", "views", "duration", "score"]

    def _populate_queue(self) -> None:
        if not self._config:
            return

        from clipper.navigator import _load_all_clips, _sort_entries

        all_clips = _load_all_clips(self._config)
        # Map subtab index to navigator tab names
        tab_map = {0: "Queue", 1: "Queue", 2: "Output"}
        tab_name = tab_map[self._queue_subtab]
        entries = list(all_clips.get(tab_name, []))

        # Filter for subtab
        if self._queue_subtab == 0:
            entries = [e for e in entries if e.source == "pending"]
        elif self._queue_subtab == 1:
            entries = [e for e in entries if e.source == "approved"]

        mode = self.QUEUE_SORT_MODES[self._queue_sort]
        entries = _sort_entries(entries, mode)
        self._queue_clips = entries
        self._queue_filtered = entries

        # Update subtab header
        counts = [
            len([e for e in all_clips.get("Queue", []) if e.source == "pending"]),
            len([e for e in all_clips.get("Queue", []) if e.source == "approved"]),
            len(all_clips.get("Output", [])),
        ]
        parts = []
        for i, name in enumerate(self.QUEUE_SUBTAB_NAMES):
            label = f"{name} ({counts[i]})"
            if i == self._queue_subtab:
                parts.append(f"[bold reverse] {label} [/bold reverse]")
            else:
                parts.append(f" {label} ")
        sort_label = self.QUEUE_SORT_MODES[self._queue_sort]
        header = "  ".join(parts) + f"    [dim]sort: {sort_label}[/dim]"
        self.query_one("#queue-subtabs", Static).update(header)

        # Rebuild table
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for i, entry in enumerate(entries, 1):
            table.add_row(
                str(i),
                entry.status_icon,
                entry.streamer[:15],
                entry.title[:40],
                f"{entry.data.get('_score', 0):.0f}" if entry.data.get("_score") else "\u2014",
                f"{entry.duration:.0f}s",
            )

        if entries:
            self._update_queue_detail(0)
        else:
            self.query_one("#queue-detail", Static).update("[dim]No clips[/dim]")

    def _update_queue_detail(self, row: int) -> None:
        if 0 <= row < len(self._queue_filtered):
            from clipper.navigator import _format_detail
            detail = _format_detail(self._queue_filtered[row])
            self.query_one("#queue-detail", Static).update(detail)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "queue-table":
            self._update_queue_detail(event.cursor_row)

    def action_refresh(self) -> None:
        active_tab = self.query_one("#tabs", TabbedContent).active
        if active_tab == "tab-queue":
            self._populate_queue()
        elif active_tab == "tab-analytics":
            self._analytics_ts = 0  # force refresh
            self._load_analytics()
        elif active_tab == "tab-calendar":
            self._load_calendar()
        self.notify("Refreshed")

    # -- Queue key bindings --

    def key_tab(self) -> None:
        active = self.query_one("#tabs", TabbedContent).active
        if active == "tab-queue":
            self._queue_subtab = (self._queue_subtab + 1) % 3
            self._populate_queue()

    def key_shift_tab(self) -> None:
        active = self.query_one("#tabs", TabbedContent).active
        if active == "tab-queue":
            self._queue_subtab = (self._queue_subtab - 1) % 3
            self._populate_queue()

    def key_s(self) -> None:
        active = self.query_one("#tabs", TabbedContent).active
        if active == "tab-queue":
            self._queue_sort = (self._queue_sort + 1) % len(self.QUEUE_SORT_MODES)
            self._populate_queue()

    def key_p(self) -> None:
        """Preview selected clip video."""
        active = self.query_one("#tabs", TabbedContent).active
        if active != "tab-queue":
            return
        table = self.query_one("#queue-table", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._queue_filtered):
            entry = self._queue_filtered[row]
            video_path = entry.data.get("processed_path")
            if video_path and Path(video_path).exists():
                import subprocess
                try:
                    subprocess.Popen(["mpv", "--really-quiet", video_path])
                except FileNotFoundError:
                    self.notify("mpv not installed", severity="warning")
            else:
                self.notify("No video file", severity="warning")

    # -- Analytics Tab --

    @work(thread=True)
    def _load_analytics(self) -> None:
        if not self._config:
            return

        # Check cache (5 min)
        if time.time() - self._analytics_ts < 300 and self._analytics_cache:
            self.call_from_thread(self._render_analytics, self._analytics_cache)
            return

        try:
            from clipper.analytics import fetch_channel_recent
            videos = fetch_channel_recent(days=30, verbose=False)
            self._analytics_cache = videos
            self._analytics_ts = time.time()
            self.call_from_thread(self._render_analytics, videos)
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Analytics error: {e}", severity="error"
            )

    def _render_analytics(self, videos: list[dict]) -> None:
        if not videos:
            self.query_one("#metric-views", MetricCard).value = "0"
            self.query_one("#metric-avg", MetricCard).value = "0"
            self.query_one("#metric-count", MetricCard).value = "0"
            return

        total_views = sum(v.get("views", 0) for v in videos)
        avg_views = total_views / len(videos) if videos else 0

        self.query_one("#metric-views", MetricCard).value = f"{total_views:,}"
        self.query_one("#metric-avg", MetricCard).value = f"{avg_views:,.0f}"
        self.query_one("#metric-count", MetricCard).value = str(len(videos))

        # Top performers table
        videos_sorted = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)
        table = self.query_one("#analytics-table", DataTable)
        table.clear()
        for i, v in enumerate(videos_sorted[:10], 1):
            published = v.get("published_at", "")[:10]
            table.add_row(
                str(i),
                v.get("title", "?")[:40],
                f"{v.get('views', 0):,}",
                str(v.get("likes", 0)),
                published,
            )

        # Learned weights
        try:
            from clipper.learn import get_learned_weights
            weights = get_learned_weights(self._config)
            if weights:
                parts = [f"{k}: {v}" for k, v in weights.items()]
                self.query_one("#learned-weights", Static).update(
                    "[bold]Learned Weights[/bold]\n" + "  |  ".join(parts)
                )
            else:
                self.query_one("#learned-weights", Static).update(
                    "[dim]Learned weights: not enough data yet[/dim]"
                )
        except Exception:
            pass

    # -- Calendar Tab --

    @work(thread=True)
    def _load_calendar(self) -> None:
        if not self._config:
            return
        try:
            from clipper.schedule import get_pending_releases
            releases = get_pending_releases(self._config)
            self.call_from_thread(self._render_calendar, releases)
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Calendar error: {e}", severity="error"
            )

    def _render_calendar(self, releases: list[dict]) -> None:
        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        days = [monday + timedelta(days=i) for i in range(7)]
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

        # Group releases by date
        by_date: dict[str, list[dict]] = {}
        for r in releases:
            try:
                dt = datetime.fromisoformat(r.get("scheduled_at", ""))
                date_key = dt.date().isoformat()
                by_date.setdefault(date_key, []).append(r)
            except (ValueError, TypeError):
                continue

        STATUS_BADGE = {
            "published": "[green]\u2713[/green]",
            "uploaded": "[yellow]\u2191[/yellow]",
            "pending": "[white]\u25cb[/white]",
            "failed": "[red]\u2717[/red]",
        }

        counts = {"pending": 0, "uploaded": 0, "published": 0, "failed": 0}

        for day, name in zip(days, day_names):
            date_str = day.isoformat()
            day_releases = by_date.get(date_str, [])

            lines = [f"[dim]{day.strftime('%m/%d')}[/dim]"]
            if day_releases:
                for r in day_releases:
                    status = r.get("status", "pending")
                    counts[status] = counts.get(status, 0) + 1
                    badge = STATUS_BADGE.get(status, "?")
                    try:
                        dt = datetime.fromisoformat(r.get("scheduled_at", ""))
                        time_str = dt.strftime("%H:%M")
                    except (ValueError, TypeError):
                        time_str = "?"

                    clip_label = r.get("clip_id", "?")[:8]
                    meta_path = r.get("meta_path")
                    if meta_path and Path(meta_path).exists():
                        try:
                            with open(meta_path) as f:
                                meta = json.load(f)
                            clip_label = meta.get("streamer", "")[:8] or clip_label
                        except (json.JSONDecodeError, OSError):
                            pass

                    lines.append(f"{time_str} {badge}{clip_label}")
            else:
                lines.append("[dim]---[/dim]")

            try:
                self.query_one(f"#day-{name}", Static).update("\n".join(lines))
            except Exception:
                pass

        # Summary
        summary_parts = []
        if counts["pending"]:
            summary_parts.append(f"{counts['pending']} pending")
        if counts["uploaded"]:
            summary_parts.append(f"{counts['uploaded']} uploaded")
        if counts["published"]:
            summary_parts.append(f"{counts['published']} published")
        if counts["failed"]:
            summary_parts.append(f"[red]{counts['failed']} failed[/red]")
        summary = "  |  ".join(summary_parts) if summary_parts else "No releases scheduled"
        self.query_one("#calendar-summary", Static).update(summary)

    # -- Workflow Modal --

    def action_workflow(self) -> None:
        if not self._config:
            self.notify("Config not loaded yet", severity="warning")
            return
        self.push_screen(WorkflowModal(self._config), self._on_workflow_result)

    def _on_workflow_result(self, result: dict | None) -> None:
        if not result:
            return
        self.query_one("#tabs", TabbedContent).active = "tab-pipeline"
        self._launch_workflow(result)

    @work(thread=True)
    def _launch_workflow(self, params: dict) -> None:
        """Run a workflow in a background thread, polling state into the dashboard."""
        recipe = params["recipe"]
        game = params.get("game", "Deadlock")
        channel = params.get("channel")
        count = params.get("count", 5)
        duration = params.get("duration")

        state = PipelineState()
        self._pipeline_state = state
        self._logged_completed = 0
        self._logged_errors = 0

        # Start polling
        self.call_from_thread(self._start_pipeline_polling)

        # Silence module consoles
        self._silenced = _silence_consoles()

        try:
            if recipe == "quick_short":
                from clipper.workflow import run_shorts_workflow
                run_shorts_workflow(
                    self._config, game=game, count=1, auto=True,
                    channel=channel, verbose=False, state=state,
                )
            elif recipe == "batch_shorts":
                from clipper.workflow import run_shorts_workflow
                run_shorts_workflow(
                    self._config, game=game, count=count, auto=True,
                    channel=channel, verbose=False, state=state,
                )
            elif recipe == "compilation":
                from clipper.workflow import run_compilation_workflow
                run_compilation_workflow(
                    self._config, game=game, duration=duration, auto=True,
                    channel=channel, verbose=False, state=state,
                )
            elif recipe == "generate":
                from clipper.workflow import run_shorts_workflow
                run_shorts_workflow(
                    self._config, game=game, count=count, auto=True,
                    channel=None, verbose=False, state=state,
                )
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Workflow error: {e}", severity="error"
            )
        finally:
            _restore_consoles(self._silenced)
            self._silenced = {}
            self.call_from_thread(self._stop_pipeline_polling)
            self.call_from_thread(self._populate_queue)

    def _start_pipeline_polling(self) -> None:
        """Start a 250ms timer to poll PipelineState."""
        if self._poll_timer:
            self._poll_timer.stop()
        self._poll_timer = self.set_interval(0.25, self._poll_pipeline)

        log = self.query_one("#pipeline-log", RichLog)
        log.clear()
        log.write("[bold cyan]Workflow started...[/bold cyan]")

    def _stop_pipeline_polling(self) -> None:
        """Stop polling and show final state."""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None

        self._poll_pipeline()

        log = self.query_one("#pipeline-log", RichLog)
        log.write("[bold green]Workflow complete![/bold green]")
        self.notify("Workflow complete!")

    def _poll_pipeline(self) -> None:
        """Poll PipelineState and update pipeline widgets."""
        state = self._pipeline_state
        if not state:
            return

        snap = state.snapshot()

        # Update metrics
        total = snap["total"]
        completed = snap["completed"]
        failed = snap["failed"]
        done = completed + failed

        self.query_one("#metric-fetched", MetricCard).value = str(total)
        self.query_one("#metric-processing", MetricCard).value = f"{done}/{total}"
        self.query_one("#metric-done", MetricCard).value = str(completed)

        # Update workers
        panel = self.query_one("#worker-panel", WorkerPanel)
        panel.worker_data = snap["workers"]

        # Update progress bar
        progress = self.query_one("#pipeline-progress", PipelineProgressBar)
        progress.completed = done
        progress.total = total
        progress.elapsed = snap["elapsed"]
        progress.eta = snap["eta"]

        # Log only NEW completions/errors (avoid duplicates)
        log = self.query_one("#pipeline-log", RichLog)
        completed_clips = snap["completed_clips"]
        errors = snap["errors"]

        for name in completed_clips[self._logged_completed:]:
            log.write(f"[green]\u2713 {name}[/green]")
        self._logged_completed = len(completed_clips)

        for err in errors[self._logged_errors:]:
            log.write(f"[red]\u2717 {err}[/red]")
        self._logged_errors = len(errors)

    # -- Cron Modal --

    def action_cron_setup(self) -> None:
        self.push_screen(CronModal())

    # -- Cleanup --

    def on_unmount(self) -> None:
        if self._silenced:
            _restore_consoles(self._silenced)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_dashboard():
    """Launch the Clipper Dashboard TUI."""
    app = ClipperDashboard()
    app.run()
