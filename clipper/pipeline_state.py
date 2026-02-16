"""Thread-safe shared state for the Clipper pipeline (web + CLI dashboards)."""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class WorkerStatus:
    clip_title: str = ""
    step: str = ""  # downloading | transcribing | formatting | burning | done | error
    started_at: float = 0.0


@dataclass
class PipelineState:
    total_clips: int = 0
    completed: int = 0
    failed: int = 0
    workers: dict[str, WorkerStatus] = field(default_factory=dict)
    completed_clips: list[str] = field(default_factory=list)
    completed_clip_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = 0.0
    compile_step: str = ""
    compile_progress: float = 0.0
    uploads_done: int = 0
    uploads_total: int = 0
    recipe: str = ""  # shorts | compilation | snipe
    phase: str = ""  # fetching | scoring | approving | processing | compiling | uploading | done | error
    phase_detail: str = ""  # human-readable detail for current phase
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self, recipe: str = "", phase: str = "", detail: str = "") -> None:
        """Reset all counters and collections for a new pipeline run."""
        with self._lock:
            self.total_clips = 0
            self.completed = 0
            self.failed = 0
            self.workers.clear()
            self.completed_clips.clear()
            self.completed_clip_ids.clear()
            self.errors.clear()
            self.started_at = time.time()
            self.compile_step = ""
            self.compile_progress = 0.0
            self.uploads_done = 0
            self.uploads_total = 0
            self.recipe = recipe
            self.phase = phase
            self.phase_detail = detail

    def start_worker(self, label: str, clip_title: str, step: str) -> None:
        with self._lock:
            self.workers[label] = WorkerStatus(
                clip_title=clip_title, step=step, started_at=time.time(),
            )

    def update_worker(self, label: str, step: str) -> None:
        with self._lock:
            if label in self.workers:
                self.workers[label].step = step

    def complete_clip(self, label: str, filename: str, *, clip_id: str = "") -> None:
        with self._lock:
            self.completed += 1
            self.completed_clips.append(filename)
            if clip_id:
                self.completed_clip_ids.append(clip_id)
            if label in self.workers:
                self.workers[label].step = "done"

    def fail_clip(self, label: str, error: str) -> None:
        with self._lock:
            self.failed += 1
            self.errors.append(error)
            if label in self.workers:
                self.workers[label].step = "error"

    def set_phase(self, phase: str, detail: str = "") -> None:
        with self._lock:
            self.phase = phase
            self.phase_detail = detail

    def set_error(self, error: str) -> None:
        with self._lock:
            self.phase = "error"
            self.phase_detail = error
            self.errors.append(error)

    def elapsed(self) -> float:
        if self.started_at <= 0:
            return 0.0
        return time.time() - self.started_at

    def eta_seconds(self) -> float | None:
        done = self.completed + self.failed
        if done == 0 or self.total_clips == 0:
            return None
        rate = self.elapsed() / done
        remaining = self.total_clips - done
        return rate * remaining

    def snapshot(self) -> dict:
        """Thread-safe snapshot of current state for dashboards and SSE streaming."""
        with self._lock:
            return {
                "total": self.total_clips,
                "completed": self.completed,
                "failed": self.failed,
                "workers": {
                    k: (v.clip_title, v.step, v.started_at)
                    for k, v in self.workers.items()
                },
                "completed_clips": list(self.completed_clips[-10:]),
                "completed_clip_ids": list(self.completed_clip_ids[-10:]),
                "errors": list(self.errors[-5:]),
                "elapsed": self.elapsed(),
                "eta": self.eta_seconds(),
                "compile_step": self.compile_step,
                "compile_progress": self.compile_progress,
                "uploads_done": self.uploads_done,
                "uploads_total": self.uploads_total,
                "recipe": self.recipe,
                "phase": self.phase,
                "phase_detail": self.phase_detail,
            }
