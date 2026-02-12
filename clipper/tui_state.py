"""Thread-safe shared state for the Clipper TUI dashboard."""

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
    errors: list[str] = field(default_factory=list)
    started_at: float = 0.0
    compile_step: str = ""
    compile_progress: float = 0.0
    uploads_done: int = 0
    uploads_total: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start_worker(self, label: str, clip_title: str, step: str) -> None:
        with self._lock:
            self.workers[label] = WorkerStatus(
                clip_title=clip_title, step=step, started_at=time.time(),
            )

    def update_worker(self, label: str, step: str) -> None:
        with self._lock:
            if label in self.workers:
                self.workers[label].step = step

    def complete_clip(self, label: str, filename: str) -> None:
        with self._lock:
            self.completed += 1
            self.completed_clips.append(filename)
            if label in self.workers:
                self.workers[label].step = "done"

    def fail_clip(self, label: str, error: str) -> None:
        with self._lock:
            self.failed += 1
            self.errors.append(error)
            if label in self.workers:
                self.workers[label].step = "error"

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
