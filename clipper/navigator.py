"""Clipper Navigator — LazyGit-style TUI for browsing clips, queue, and uploads."""

import json
import subprocess
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static


@dataclass
class ClipEntry:
    data: dict
    source: str
    json_path: Path
    status: str

    @property
    def title(self) -> str:
        return self.data.get("title", self.json_path.stem)

    @property
    def streamer(self) -> str:
        return self.data.get("streamer", "?")

    @property
    def views(self) -> int:
        return self.data.get("view_count", 0)

    @property
    def duration(self) -> float:
        return self.data.get("duration", 0)

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")

    @property
    def game(self) -> str:
        return self.data.get("game", "")

    @property
    def status_icon(self) -> str:
        return {"uploaded": "\u2713", "processed": "\u25cb", "pending": "\u25cc", "skipped": "\u2717"}.get(
            self.status, "?"
        )


SORT_MODES = ["date", "views", "duration", "score"]

TAB_NAMES = ["Output", "Queue", "Uploads"]


def _load_all_clips(config: dict) -> dict[str, list[ClipEntry]]:
    """Load clips from output/ and queue/*/ into tab-keyed lists."""
    output_dir = config["_output_dir"]
    queue_dir = config["_queue_dir"]

    entries: dict[str, list[ClipEntry]] = {"Output": [], "Queue": [], "Uploads": []}

    # Output clips
    for p in sorted(output_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if "processed_path" not in data:
            continue
        has_video_id = bool(data.get("video_id"))
        status = "uploaded" if has_video_id else "processed"
        entry = ClipEntry(data=data, source="output", json_path=p, status=status)
        entries["Output"].append(entry)
        if has_video_id:
            entries["Uploads"].append(entry)

    # Queue clips (pending, approved, skipped)
    for subdir_name in ("pending", "approved", "skipped"):
        subdir = queue_dir / subdir_name
        if not subdir.exists():
            continue
        status_map = {"pending": "pending", "approved": "pending", "skipped": "skipped"}
        status = status_map[subdir_name]
        for p in sorted(subdir.glob("*.json")):
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            entries["Queue"].append(
                ClipEntry(data=data, source=subdir_name, json_path=p, status=status)
            )

    return entries


def _sort_entries(entries: list[ClipEntry], mode: str) -> list[ClipEntry]:
    """Sort entries by the given mode."""
    if mode == "date":
        return sorted(entries, key=lambda e: e.created_at, reverse=True)
    elif mode == "views":
        return sorted(entries, key=lambda e: e.views, reverse=True)
    elif mode == "duration":
        return sorted(entries, key=lambda e: e.duration, reverse=True)
    elif mode == "score":
        try:
            from clipper.process.score import score_clip
            return sorted(entries, key=lambda e: score_clip(e.data), reverse=True)
        except ImportError:
            return sorted(entries, key=lambda e: e.views, reverse=True)
    return entries


def _format_detail(entry: ClipEntry) -> str:
    """Build rich-text detail panel for a clip."""
    d = entry.data
    lines = []

    lines.append(f"[bold]{entry.title}[/bold]")
    lines.append("")
    lines.append(f"Streamer:  [cyan]{entry.streamer}[/cyan]")
    lines.append(f"Game:      {entry.game}")
    lines.append(f"Platform:  {d.get('platform', '?')}")
    lines.append(f"Duration:  {entry.duration:.0f}s")
    lines.append(f"Views:     {entry.views:,}")
    lines.append(f"Created:   {entry.created_at[:10]}")
    lines.append(f"Status:    {entry.status_icon} {entry.status}")
    lines.append(f"Language:  {d.get('language', '?')}")

    # YouTube info
    vid = d.get("video_id")
    if vid and vid != "previously_uploaded":
        lines.append("")
        lines.append(f"YouTube:   [link]https://youtube.com/watch?v={vid}[/link]")
        lines.append(f"Privacy:   {d.get('_privacy', 'unknown')}")
    elif vid == "previously_uploaded":
        lines.append("")
        lines.append("YouTube:   [dim]previously uploaded[/dim]")

    # File paths
    lines.append("")
    pp = d.get("processed_path")
    if pp:
        lines.append(f"Video:     [dim]{pp}[/dim]")
    lines.append(f"JSON:      [dim]{entry.json_path}[/dim]")

    # Clip URL
    url = d.get("url")
    if url:
        lines.append(f"Source:    [link]{url}[/link]")

    # LLM analysis
    analysis = d.get("_analysis")
    if analysis:
        lines.append("")
        lines.append("[bold]LLM Analysis[/bold]")
        if "score" in analysis:
            lines.append(f"  Score:    {analysis['score']}/10")
        if "category" in analysis:
            lines.append(f"  Category: {analysis['category']}")
        if "quote" in analysis:
            lines.append(f'  Quote:    "{analysis["quote"]}"')
        if "moment" in analysis:
            lines.append(f"  Moment:   {analysis['moment']}")
        if "summary" in analysis:
            lines.append(f"  Summary:  {analysis['summary']}")

    return "\n".join(lines)


class ClipNavigator(App):
    CSS = """
    #main {
        height: 1fr;
    }
    #left-pane {
        width: 60%;
        border-right: solid $primary-background;
    }
    #right-pane {
        width: 40%;
        padding: 1 2;
        overflow-y: auto;
    }
    #tab-header {
        height: 1;
        padding: 0 1;
        background: $primary-background;
    }
    #search-box {
        display: none;
        height: 1;
    }
    #search-box.visible {
        display: block;
    }
    #detail-content {
        height: 1fr;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "next_tab", "Next Tab"),
        Binding("shift+tab", "prev_tab", "Prev Tab"),
        Binding("enter", "open_url", "Open"),
        Binding("p", "preview", "Preview"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("slash", "search", "Search"),
        Binding("escape", "clear_search", "Clear"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self):
        super().__init__()
        self._all_clips: dict[str, list[ClipEntry]] = {}
        self._filtered: list[ClipEntry] = []
        self._current_tab = 0
        self._sort_mode = 0
        self._config: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left-pane"):
                yield Static("Loading...", id="tab-header")
                yield Input(placeholder="Search clips...", id="search-box")
                yield DataTable(id="clip-table")
            with Vertical(id="right-pane"):
                yield Static("Select a clip", id="detail-content")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#clip-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("", "Streamer", "Title", "Views")
        self._load_data()

    @work(thread=True)
    def _load_data(self) -> None:
        from clipper.config import load_config

        self._config = load_config()
        self._all_clips = _load_all_clips(self._config)
        self.call_from_thread(self._populate_table)

    def _populate_table(self) -> None:
        tab_name = TAB_NAMES[self._current_tab]
        entries = list(self._all_clips.get(tab_name, []))

        # Apply search filter
        search_box = self.query_one("#search-box", Input)
        query = search_box.value.strip().lower()
        if query:
            entries = [
                e for e in entries
                if query in e.title.lower()
                or query in e.streamer.lower()
                or query in e.game.lower()
            ]

        # Sort
        mode = SORT_MODES[self._sort_mode]
        entries = _sort_entries(entries, mode)
        self._filtered = entries

        # Update tab header
        counts = {name: len(self._all_clips.get(name, [])) for name in TAB_NAMES}
        parts = []
        for i, name in enumerate(TAB_NAMES):
            label = f"{name} ({counts[name]})"
            if i == self._current_tab:
                parts.append(f"[bold reverse] {label} [/bold reverse]")
            else:
                parts.append(f" {label} ")
        sort_label = SORT_MODES[self._sort_mode]
        header_text = " | ".join(parts) + f"    [dim]sort: {sort_label}[/dim]"
        self.query_one("#tab-header", Static).update(header_text)

        # Rebuild table
        table = self.query_one("#clip-table", DataTable)
        table.clear()
        for entry in entries:
            title_display = entry.title[:45]
            views_display = f"{entry.views:,}"
            table.add_row(
                entry.status_icon,
                entry.streamer[:15],
                title_display,
                views_display,
            )

        # Update detail for first row
        if entries:
            self._update_detail(0)
        else:
            self.query_one("#detail-content", Static).update("[dim]No clips[/dim]")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail(event.cursor_row)

    def _update_detail(self, row: int) -> None:
        if 0 <= row < len(self._filtered):
            detail_text = _format_detail(self._filtered[row])
            self.query_one("#detail-content", Static).update(detail_text)

    def _get_selected(self) -> ClipEntry | None:
        table = self.query_one("#clip-table", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    # -- Actions --

    def action_next_tab(self) -> None:
        self._current_tab = (self._current_tab + 1) % len(TAB_NAMES)
        self._populate_table()

    def action_prev_tab(self) -> None:
        self._current_tab = (self._current_tab - 1) % len(TAB_NAMES)
        self._populate_table()

    def action_open_url(self) -> None:
        entry = self._get_selected()
        if not entry:
            return
        # Prefer YouTube link for uploads, else clip source URL
        vid = entry.data.get("video_id")
        if vid and vid != "previously_uploaded":
            webbrowser.open(f"https://youtube.com/watch?v={vid}")
        elif entry.data.get("url"):
            webbrowser.open(entry.data["url"])

    def action_preview(self) -> None:
        entry = self._get_selected()
        if not entry:
            return
        video_path = entry.data.get("processed_path")
        if not video_path or not Path(video_path).exists():
            self.notify("No video file found", severity="warning")
            return
        try:
            subprocess.Popen(["mpv", "--really-quiet", video_path])
        except FileNotFoundError:
            self.notify("mpv not installed", severity="warning")

    def action_cycle_sort(self) -> None:
        self._sort_mode = (self._sort_mode + 1) % len(SORT_MODES)
        self._populate_table()

    def action_search(self) -> None:
        search_box = self.query_one("#search-box", Input)
        search_box.add_class("visible")
        search_box.focus()

    def action_clear_search(self) -> None:
        search_box = self.query_one("#search-box", Input)
        search_box.value = ""
        search_box.remove_class("visible")
        self.query_one("#clip-table", DataTable).focus()
        self._populate_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """When Enter is pressed in the search box, apply filter and refocus table."""
        self._populate_table()
        self.query_one("#clip-table", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live filter as user types."""
        self._populate_table()

    def action_refresh(self) -> None:
        self.notify("Refreshing...")
        self._load_data()


def run_navigator():
    """Entry point for `clipper nav`."""
    app = ClipNavigator()
    app.run()
