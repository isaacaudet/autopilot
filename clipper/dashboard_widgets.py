"""Custom Textual widgets for the Clipper Dashboard TUI."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Label,
    OptionList,
    RichLog,
    Static,
)

# ---------------------------------------------------------------------------
# 1. MetricCard
# ---------------------------------------------------------------------------


class MetricCard(Static):
    """Bordered card showing a label and a reactive value."""

    value: reactive[str] = reactive("—")

    def __init__(self, label: str, value: str = "—", **kw: Any) -> None:
        super().__init__(**kw)
        self.label = label
        self.value = value

    def render(self) -> str:
        return f"[bold]{self.label}[/bold]\n{self.value}"

    def watch_value(self, new_value: str) -> None:
        self.update(f"[bold]{self.label}[/bold]\n{new_value}")


# ---------------------------------------------------------------------------
# 2. WorkerPanel
# ---------------------------------------------------------------------------

_STEP_COLORS = {
    "downloading": "blue",
    "transcribing": "yellow",
    "formatting": "yellow",
    "burning": "green",
    "done": "dim",
    "error": "red",
    "idle": "dim",
}


class WorkerPanel(Static):
    """Shows 3 fixed worker rows (W1-W3) with color-coded step status."""

    worker_data: reactive[dict] = reactive(dict, always_update=True)

    def render(self) -> str:
        lines: list[str] = []
        for i in range(1, 4):
            label = f"W{i}"
            info = self.worker_data.get(label)
            if info:
                clip_title, step = info
                color = _STEP_COLORS.get(step, "dim")
                title_display = clip_title[:28] if clip_title else ""
                lines.append(f"[{color}]{label}  {title_display:<28}  {step}[/{color}]")
            else:
                lines.append(f"[dim]{label}  {'—':<28}  idle[/dim]")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. PipelineProgressBar
# ---------------------------------------------------------------------------


class PipelineProgressBar(Static):
    """Unicode block progress bar — 30 chars wide with percentage and ETA."""

    completed: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    elapsed: reactive[float] = reactive(0.0)
    eta: reactive[float | None] = reactive(None)

    def render(self) -> str:
        if self.total <= 0:
            return "[dim]░" * 30 + "  0%[/dim]"

        pct = min(self.completed / self.total, 1.0)
        filled = int(pct * 30)
        bar = "█" * filled + "░" * (30 - filled)
        pct_str = f"{pct * 100:.0f}%"

        elapsed_str = _fmt_time(self.elapsed)
        eta_str = f"  ETA ~{_fmt_time(self.eta)}" if self.eta is not None else ""

        color = "green" if pct >= 1.0 else "cyan"
        return f"[{color}]{bar}[/{color}]  {pct_str}  {elapsed_str}{eta_str}"


def _fmt_time(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# 4. PipelineView
# ---------------------------------------------------------------------------


class PipelineView(Static):
    """Container for the Pipeline tab — metrics, workers, log, progress."""

    state_snapshot: reactive[dict] = reactive(dict, always_update=True)

    def compose(self) -> ComposeResult:
        with Horizontal(id="pipeline-metrics"):
            yield MetricCard("Completed", "0", id="metric-completed")
            yield MetricCard("Failed", "0", id="metric-failed")
            yield MetricCard("Uploads", "0", id="metric-uploads")
        with Horizontal(id="pipeline-body"):
            yield WorkerPanel(id="worker-panel")
            yield RichLog(id="pipeline-log", highlight=True, markup=True)
        yield PipelineProgressBar(id="pipeline-progress")

    def watch_state_snapshot(self, snapshot: dict) -> None:
        if not snapshot:
            return

        # Metrics
        completed = snapshot.get("completed", 0)
        failed = snapshot.get("failed", 0)
        total = snapshot.get("total_clips", 0)
        uploads_done = snapshot.get("uploads_done", 0)
        uploads_total = snapshot.get("uploads_total", 0)

        try:
            self.query_one("#metric-completed", MetricCard).value = (
                f"{completed}/{total}"
            )
            self.query_one("#metric-failed", MetricCard).value = str(failed)
            self.query_one("#metric-uploads", MetricCard).value = (
                f"{uploads_done}/{uploads_total}" if uploads_total else "—"
            )
        except Exception:
            pass

        # Workers — convert WorkerStatus dicts to (title, step) tuples
        raw_workers = snapshot.get("workers", {})
        worker_data: dict[str, tuple[str, str]] = {}
        for label, ws in raw_workers.items():
            if isinstance(ws, dict):
                worker_data[label] = (ws.get("clip_title", ""), ws.get("step", "idle"))
            else:
                worker_data[label] = (getattr(ws, "clip_title", ""), getattr(ws, "step", "idle"))

        try:
            self.query_one("#worker-panel", WorkerPanel).worker_data = worker_data
        except Exception:
            pass

        # Progress bar
        elapsed = snapshot.get("elapsed", 0.0)
        eta = snapshot.get("eta", None)
        try:
            bar = self.query_one("#pipeline-progress", PipelineProgressBar)
            bar.completed = completed
            bar.total = total
            bar.elapsed = elapsed
            bar.eta = eta
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5. QueueView
# ---------------------------------------------------------------------------

_QUEUE_TABS = ["pending", "approved", "output"]
_QUEUE_SORT_MODES = ["date", "views", "duration", "score"]


class QueueView(Static):
    """Container for the Queue tab — sub-tabs, DataTable, detail panel."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._sub_tab: int = 0
        self._sort_mode: int = 0
        self._entries: list[Any] = []
        self._search: str = ""
        self._config: dict | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="queue-tab-header")
        yield DataTable(id="queue-table")
        yield Static("[dim]Select a clip[/dim]", id="queue-detail")

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "St", "Streamer", "Title", "Score", "Dur")

    def load_data(self, config: dict) -> None:
        self._config = config
        self._refresh_table()

    def _refresh_table(self) -> None:
        if not self._config:
            return

        from clipper.navigator import ClipEntry, _load_all_clips, _sort_entries

        all_clips = _load_all_clips(self._config)

        tab_name = _QUEUE_TABS[self._sub_tab]
        if tab_name == "pending":
            entries = [e for e in all_clips.get("Queue", []) if e.source == "pending"]
        elif tab_name == "approved":
            entries = [e for e in all_clips.get("Queue", []) if e.source == "approved"]
        else:
            entries = all_clips.get("Output", [])

        # Search filter
        if self._search:
            q = self._search.lower()
            entries = [
                e for e in entries
                if q in e.title.lower() or q in e.streamer.lower() or q in e.game.lower()
            ]

        # Sort
        mode = _QUEUE_SORT_MODES[self._sort_mode]
        entries = _sort_entries(entries, mode)
        self._entries = entries

        # Update tab header
        parts: list[str] = []
        for i, name in enumerate(_QUEUE_TABS):
            label = name.capitalize()
            if i == self._sub_tab:
                parts.append(f"[bold reverse] {label} [/bold reverse]")
            else:
                parts.append(f" {label} ")
        sort_label = _QUEUE_SORT_MODES[self._sort_mode]
        header = " | ".join(parts) + f"    [dim]sort: {sort_label}[/dim]"
        try:
            self.query_one("#queue-tab-header", Static).update(header)
        except Exception:
            pass

        # Rebuild table
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        for i, entry in enumerate(entries, 1):
            score = "—"
            try:
                from clipper.process.score import score_clip
                score = f"{score_clip(entry.data):.0f}"
            except Exception:
                pass
            table.add_row(
                str(i),
                entry.status_icon,
                entry.streamer[:15],
                entry.title[:35],
                score,
                f"{entry.duration:.0f}s",
            )

        # Update detail
        if entries:
            self._update_detail(0)
        else:
            try:
                self.query_one("#queue-detail", Static).update("[dim]No clips[/dim]")
            except Exception:
                pass

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail(event.cursor_row)

    def _update_detail(self, row: int) -> None:
        if 0 <= row < len(self._entries):
            from clipper.navigator import _format_detail
            detail_text = _format_detail(self._entries[row])
            try:
                self.query_one("#queue-detail", Static).update(detail_text)
            except Exception:
                pass

    def cycle_sub_tab(self, forward: bool = True) -> None:
        if forward:
            self._sub_tab = (self._sub_tab + 1) % len(_QUEUE_TABS)
        else:
            self._sub_tab = (self._sub_tab - 1) % len(_QUEUE_TABS)
        self._refresh_table()

    def cycle_sort(self) -> None:
        self._sort_mode = (self._sort_mode + 1) % len(_QUEUE_SORT_MODES)
        self._refresh_table()

    def set_search(self, query: str) -> None:
        self._search = query
        self._refresh_table()


# ---------------------------------------------------------------------------
# 6. AnalyticsView
# ---------------------------------------------------------------------------

_ANALYTICS_CACHE_TTL = 300  # 5 minutes


class AnalyticsView(Static):
    """Container for the Analytics tab — stats cards, top videos, learned weights."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._cache_time: float = 0.0
        self._cached_videos: list[dict] = []
        self._cached_weights: dict | None = None
        self._config: dict | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="analytics-metrics"):
            yield MetricCard("Total Views", "—", id="metric-total-views")
            yield MetricCard("Avg Views/Video", "—", id="metric-avg-views")
            yield MetricCard("Video Count", "—", id="metric-video-count")
        yield DataTable(id="analytics-table")
        yield Static("[dim]Learned weights not available[/dim]", id="analytics-weights")

    def on_mount(self) -> None:
        table = self.query_one("#analytics-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "Title", "Views", "Likes", "Published")

    def load_data(self, config: dict) -> None:
        self._config = config
        now = time.time()
        if now - self._cache_time < _ANALYTICS_CACHE_TTL and self._cached_videos:
            self._render_data(self._cached_videos, self._cached_weights)
            return
        self._fetch_data()

    @work(thread=True)
    def _fetch_data(self) -> None:
        from clipper.analytics import fetch_channel_recent
        from clipper.learn import get_learned_weights

        videos: list[dict] = []
        weights: dict | None = None
        try:
            videos = fetch_channel_recent(days=30)
        except Exception:
            pass
        try:
            if self._config:
                weights = get_learned_weights(self._config)
        except Exception:
            pass

        self._cached_videos = videos
        self._cached_weights = weights
        self._cache_time = time.time()
        self.call_from_thread(self._render_data, videos, weights)

    def _render_data(self, videos: list[dict], weights: dict | None) -> None:
        # Stats cards
        total_views = sum(v.get("views", 0) for v in videos)
        avg_views = total_views // len(videos) if videos else 0
        try:
            self.query_one("#metric-total-views", MetricCard).value = f"{total_views:,}"
            self.query_one("#metric-avg-views", MetricCard).value = f"{avg_views:,}"
            self.query_one("#metric-video-count", MetricCard).value = str(len(videos))
        except Exception:
            pass

        # Top 10 table
        sorted_videos = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)[:10]
        table = self.query_one("#analytics-table", DataTable)
        table.clear()
        for i, v in enumerate(sorted_videos, 1):
            title = v.get("title", "?")
            if len(title) > 40:
                title = title[:37] + "..."
            published = v.get("published_at", "")[:10]
            table.add_row(
                str(i),
                title,
                f"{v.get('views', 0):,}",
                str(v.get("likes", 0)),
                published,
            )

        # Learned weights
        if weights:
            lines = ["[bold]Learned Scoring Weights[/bold]", ""]
            for k, v in weights.items():
                lines.append(f"  {k}: {v}")
            try:
                self.query_one("#analytics-weights", Static).update("\n".join(lines))
            except Exception:
                pass
        else:
            try:
                self.query_one("#analytics-weights", Static).update(
                    "[dim]Learned weights not available (need 20+ samples)[/dim]"
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 7. CalendarView
# ---------------------------------------------------------------------------

_STATUS_BADGES = {
    "published": "[green]✓[/green]",
    "uploaded": "[yellow]↑[/yellow]",
    "pending": "[dim]○[/dim]",
    "failed": "[red]✗[/red]",
}


class CalendarView(Static):
    """Container for the Calendar tab — weekly grid with release slots."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._releases: list[dict] = []
        self._week_offset: int = 0  # 0 = current week
        self._selected_day: int = 0  # 0-6 Mon-Sun
        self._selected_slot: int = 0
        self._config: dict | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="calendar-grid")
        yield Static("[dim]Select a slot[/dim]", id="calendar-detail")
        yield Static("", id="calendar-summary")

    def load_data(self, config: dict) -> None:
        self._config = config
        self._fetch_releases()

    @work(thread=True)
    def _fetch_releases(self) -> None:
        if not self._config:
            return
        from clipper.schedule import get_pending_releases
        releases = get_pending_releases(self._config)
        self._releases = releases
        self.call_from_thread(self._render_grid)

    def _render_grid(self) -> None:
        today = datetime.now().date()
        # Start of the current week (Monday)
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=self._week_offset)
        days = [start + timedelta(days=i) for i in range(7)]
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        # Group releases by date
        by_date: dict[str, list[dict]] = {}
        for r in self._releases:
            sched = r.get("scheduled_at", "")
            try:
                dt = datetime.fromisoformat(sched)
                key = dt.strftime("%Y-%m-%d")
                by_date.setdefault(key, []).append(r)
            except (ValueError, TypeError):
                continue

        # Build grid
        header = "  ".join(f"[bold]{dn}[/bold] {d.strftime('%m/%d')}" for dn, d in zip(day_names, days))
        lines = [header, ""]

        # Find max slots across the week for row alignment
        max_slots = 1
        for d in days:
            key = d.strftime("%Y-%m-%d")
            slots = by_date.get(key, [])
            max_slots = max(max_slots, len(slots))

        for slot_idx in range(max(max_slots, 3)):
            row_parts: list[str] = []
            for day_idx, d in enumerate(days):
                key = d.strftime("%Y-%m-%d")
                slots = by_date.get(key, [])
                if slot_idx < len(slots):
                    release = slots[slot_idx]
                    status = release.get("status", "pending")
                    badge = _STATUS_BADGES.get(status, "?")
                    sched = release.get("scheduled_at", "")
                    try:
                        time_str = datetime.fromisoformat(sched).strftime("%H:%M")
                    except (ValueError, TypeError):
                        time_str = "??:??"
                    selected = (day_idx == self._selected_day and slot_idx == self._selected_slot)
                    marker = "►" if selected else " "
                    row_parts.append(f"{marker}{badge} {time_str}   ")
                else:
                    selected = (day_idx == self._selected_day and slot_idx == self._selected_slot)
                    marker = "►" if selected else " "
                    row_parts.append(f"{marker}[dim]---[/dim]       ")
            lines.append("".join(row_parts))

        try:
            self.query_one("#calendar-grid", Static).update("\n".join(lines))
        except Exception:
            pass

        # Detail for selected slot
        self._update_selected_detail(days, by_date)

        # Summary
        counts = {"published": 0, "uploaded": 0, "pending": 0, "failed": 0}
        for r in self._releases:
            status = r.get("status", "pending")
            if status in counts:
                counts[status] += 1
        summary = (
            f"[green]✓ {counts['published']} published[/green]  "
            f"[yellow]↑ {counts['uploaded']} uploaded[/yellow]  "
            f"[dim]○ {counts['pending']} pending[/dim]  "
            f"[red]✗ {counts['failed']} failed[/red]"
        )
        try:
            self.query_one("#calendar-summary", Static).update(summary)
        except Exception:
            pass

    def _update_selected_detail(self, days: list, by_date: dict[str, list[dict]]) -> None:
        if self._selected_day < 0 or self._selected_day >= len(days):
            return
        d = days[self._selected_day]
        key = d.strftime("%Y-%m-%d")
        slots = by_date.get(key, [])
        if 0 <= self._selected_slot < len(slots):
            release = slots[self._selected_slot]
            lines = [
                f"[bold]{d.strftime('%A %b %d')}[/bold]",
                f"Status:    {release.get('status', '?')}",
                f"Channel:   {release.get('channel', '?')}",
                f"Scheduled: {release.get('scheduled_at', '?')}",
                f"Clip ID:   {release.get('clip_id', '?')}",
            ]
            vid = release.get("video_id")
            if vid:
                lines.append(f"YouTube:   https://youtube.com/watch?v={vid}")

            # Try to load clip title from meta
            meta_path = release.get("meta_path")
            if meta_path and Path(meta_path).exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    lines.append(f"Title:     {meta.get('title', '?')}")
                    lines.append(f"Streamer:  {meta.get('streamer', '?')}")
                except (json.JSONDecodeError, OSError):
                    pass

            detail = "\n".join(lines)
        else:
            detail = f"[dim]{d.strftime('%A %b %d')} — no release scheduled[/dim]"

        try:
            self.query_one("#calendar-detail", Static).update(detail)
        except Exception:
            pass

    def navigate(self, direction: str) -> None:
        """Arrow key navigation: left/right between days, up/down between slots."""
        if direction == "left":
            self._selected_day = max(0, self._selected_day - 1)
        elif direction == "right":
            self._selected_day = min(6, self._selected_day + 1)
        elif direction == "up":
            self._selected_slot = max(0, self._selected_slot - 1)
        elif direction == "down":
            self._selected_slot += 1
        self._render_grid()

    def shift_week(self, forward: bool = True) -> None:
        self._week_offset += 1 if forward else -1
        self._fetch_releases()


# ---------------------------------------------------------------------------
# 8. WorkflowModal
# ---------------------------------------------------------------------------


class WorkflowLaunch(Message):
    """Custom message posted when a workflow is launched from the modal."""

    def __init__(self, recipe: str, params: dict) -> None:
        super().__init__()
        self.recipe = recipe
        self.params = params


_RECIPES = [
    ("Quick Short", "1 clip, upload now"),
    ("Batch Shorts", "N shorts, scheduled"),
    ("Compilation", "8-15 min, scheduled"),
    ("Generate", "N clips, save only"),
]


class WorkflowModal(ModalScreen):
    """Overlay modal for picking and configuring a workflow recipe."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, config: dict, **kw: Any) -> None:
        super().__init__(**kw)
        self._config = config
        self._step: int = 1  # 1=recipe, 2=config, 3=launch
        self._recipe: str = ""
        self._params: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="workflow-modal-container"):
            yield Label("[bold]Launch Workflow[/bold]", id="workflow-title")
            yield OptionList(
                *[f"{name} — {desc}" for name, desc in _RECIPES],
                id="recipe-picker",
            )
            yield Static("", id="workflow-config")
            yield Static("", id="workflow-status")

    def on_mount(self) -> None:
        self.query_one("#workflow-config", Static).display = False
        self.query_one("#workflow-status", Static).display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._step == 1:
            self._recipe = _RECIPES[event.option_index][0]
            self._show_config_step()

    def _show_config_step(self) -> None:
        self._step = 2
        self.query_one("#recipe-picker", OptionList).display = False

        # Build config form based on recipe
        games = self._config.get("targets", {}).get("twitch", {}).get("games", [])
        channels = list(self._config.get("channels", {}).keys())

        game_list = ", ".join(games) if games else "(none configured)"
        channel_list = ", ".join(channels) if channels else "(none configured)"

        lines = [
            f"[bold]Recipe:[/bold] {self._recipe}",
            "",
            f"[bold]Game:[/bold] {games[0] if games else '?'}",
            f"  Available: {game_list}",
            "",
            f"[bold]Channel:[/bold] {channels[0] if channels else '?'}",
            f"  Available: {channel_list}",
        ]

        # Set defaults
        self._params["game"] = games[0] if games else None
        self._params["channel"] = channels[0] if channels else None

        if self._recipe == "Batch Shorts":
            self._params["count"] = 5
            lines.extend(["", "[bold]Count:[/bold] 5  (3, 5, 10)"])
        elif self._recipe == "Compilation":
            self._params["duration"] = 10
            lines.extend(["", "[bold]Duration:[/bold] 10 min  (8, 10, 12, 15)"])

        lines.extend(["", "[dim]Enter to launch · Esc to cancel[/dim]"])

        config_panel = self.query_one("#workflow-config", Static)
        config_panel.update("\n".join(lines))
        config_panel.display = True

    def action_cancel(self) -> None:
        if self._step == 2:
            # Go back to recipe picker
            self._step = 1
            self.query_one("#recipe-picker", OptionList).display = True
            self.query_one("#workflow-config", Static).display = False
        else:
            self.dismiss(None)

    def on_key(self, event: Any) -> None:
        if self._step != 2:
            return

        if event.key == "enter":
            self._launch()
        # Batch count cycling
        elif event.key in ("c", "n") and self._recipe == "Batch Shorts":
            choices = [3, 5, 10]
            current = self._params.get("count", 5)
            idx = (choices.index(current) + 1) % len(choices) if current in choices else 0
            self._params["count"] = choices[idx]
            self._show_config_step()
        # Duration cycling
        elif event.key in ("d",) and self._recipe == "Compilation":
            choices = [8, 10, 12, 15]
            current = self._params.get("duration", 10)
            idx = (choices.index(current) + 1) % len(choices) if current in choices else 0
            self._params["duration"] = choices[idx]
            self._show_config_step()
        # Game cycling
        elif event.key == "g":
            games = self._config.get("targets", {}).get("twitch", {}).get("games", [])
            if games:
                current = self._params.get("game", games[0])
                idx = (games.index(current) + 1) % len(games) if current in games else 0
                self._params["game"] = games[idx]
                self._show_config_step()

    def _launch(self) -> None:
        self.post_message(WorkflowLaunch(self._recipe, dict(self._params)))
        self.dismiss(self._recipe)


# ---------------------------------------------------------------------------
# 9. CronModal
# ---------------------------------------------------------------------------


class CronModal(ModalScreen):
    """Modal showing cron status with install/remove toggle."""

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
    ]

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._installed: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(id="cron-modal-container"):
            yield Label("[bold]Cron Scheduler[/bold]", id="cron-title")
            yield Static("", id="cron-status")
            yield Button("Install", id="cron-toggle", variant="success")

    def on_mount(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        try:
            from clipper.cron import get_status
            status = get_status()
            self._installed = status.get("installed", False)
            platform = status.get("platform", "unknown")
            interval = status.get("interval", "?")
            if self._installed:
                text = (
                    f"[green]Installed[/green]\n"
                    f"Platform:  {platform}\n"
                    f"Interval:  {interval}"
                )
            else:
                text = (
                    f"[yellow]Not installed[/yellow]\n"
                    f"Platform:  {platform}"
                )
        except ImportError:
            text = "[red]clipper.cron module not available[/red]"
            self._installed = False

        try:
            self.query_one("#cron-status", Static).update(text)
            btn = self.query_one("#cron-toggle", Button)
            if self._installed:
                btn.label = "Remove"
                btn.variant = "error"
            else:
                btn.label = "Install"
                btn.variant = "success"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cron-toggle":
            try:
                if self._installed:
                    from clipper.cron import remove
                    remove()
                else:
                    from clipper.cron import install
                    install()
            except ImportError:
                pass
            except Exception:
                pass
            self._refresh_status()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
