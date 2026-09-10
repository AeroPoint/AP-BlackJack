"""A small in-process job runner.

Some of what the engine does is fast enough to answer inside a request -- a full
solve is 31 ms -- and some is not. An index sweep is a couple of seconds, a
simulation is however long you ask for, and both grow with what the caller
requests. Holding an HTTP connection open for that is wrong even when it happens
to work: the client cannot show progress, a refresh restarts the work, and a
slow request is indistinguishable from a hung one.

So the long operations are jobs. ``POST`` starts one and returns an id; ``GET``
polls it. The solver already accepts a ``(done, total)`` progress callback, which
is what feeds the poll response.

Why threads, and why in-process
-------------------------------
A thread pool, not a process pool: the arguments are ``RuleSet`` and
``CountSystem`` objects that would otherwise have to survive pickling, and the
heaviest work -- ``solve_all_cells`` -- releases the GIL inside the Rust core
anyway. Pure-Python jobs will contend for the GIL and slow other requests, which
is an acceptable trade for a local single-user application and is the first
thing to revisit if this is ever hosted.

In-process, not Redis or Celery: this is a desktop application that happens to
speak HTTP. Adding a broker would be infrastructure nobody asked for, and the
whole point of the engine having no dependencies is not to acquire them here.

State is lost on restart, deliberately. Every job is reproducible from its
request plus the config fingerprint recorded on the result, so there is nothing
worth persisting that could not be recomputed faster than it could be read back.

Cancellation
------------
Cooperative. ``Future.cancel()` only works before a job starts, which is never
the case for the one you actually want to stop. Instead the progress callback
checks a flag and raises, so a cancelled sweep stops at its next checkpoint
rather than running to completion in a thread nobody is listening to.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """Where a job is."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        """Whether the job will change no further."""
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class JobCancelledError(Exception):
    """Raised inside a job's own thread when the caller asks it to stop."""


@dataclass(slots=True)
class Job:
    """One unit of background work."""

    id: str
    kind: str
    status: JobStatus = JobStatus.PENDING
    done: int = 0
    total: int = 0
    result: Any = None
    error: str | None = None
    traceback: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    fingerprint: str | None = None
    """Config fingerprint of the request, so a result can be traced to its inputs."""

    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _future: Future[Any] | None = field(default=None, repr=False)

    @property
    def progress(self) -> float:
        """Fraction complete in ``[0, 1]``. Zero when the total is not yet known."""
        if self.status is JobStatus.SUCCEEDED:
            return 1.0
        return self.done / self.total if self.total else 0.0

    @property
    def elapsed(self) -> float:
        """Seconds spent running so far, or in total once finished."""
        if self.started_at is None:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at

    def to_dict(self, *, include_result: bool = True) -> dict[str, Any]:
        """JSON-safe view, for the poll response."""
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "progress": round(self.progress, 4),
            "done": self.done,
            "total": self.total,
            "elapsed_seconds": round(self.elapsed, 3),
            "fingerprint": self.fingerprint,
        }
        if self.status is JobStatus.FAILED:
            payload["error"] = self.error
        if include_result and self.status is JobStatus.SUCCEEDED:
            payload["result"] = self.result
        return payload


ProgressFn = Callable[[int, int], None]
JobFn = Callable[[ProgressFn], Any]


class JobRunner:
    """Runs jobs on a small thread pool and remembers their results.

    Args:
        workers: Concurrent jobs. Deliberately small -- the native core already
            saturates every core inside a single solve, so running several jobs
            at once mostly makes them all slower.
        keep: How many finished jobs to retain. Old ones are evicted oldest
            first, because a desktop session will otherwise accumulate results
            nobody will read again.
    """

    def __init__(self, workers: int = 2, keep: int = 64) -> None:
        """Start the pool."""
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bj-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, kind: str, fn: JobFn, *, fingerprint: str | None = None) -> Job:
        """Queue a job.

        Args:
            kind: Label for the operation, e.g. ``"indices"``.
            fn: The work. It is handed a ``(done, total)`` progress callback and
                must call it periodically for progress and cancellation to work.
            fingerprint: Config fingerprint to record on the result.

        Returns:
            The job, already registered and pollable.
        """
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, fingerprint=fingerprint)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_locked()
        job._future = self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: JobFn) -> None:
        """Execute one job, recording whatever happens.

        The terminal status is assigned **last**, after every other field, and
        that ordering is load-bearing. A poller watches ``status`` to decide the
        job is done; if the status flipped first, a client could see ``failed``
        with no error message attached, or ``succeeded`` with a progress bar
        stuck at 90%. Writing the status last makes it the commit point for
        everything else.
        """
        job.started_at = time.time()
        job.status = JobStatus.RUNNING

        def progress(done: int, total: int) -> None:
            if job._cancel.is_set():
                raise JobCancelledError(job.id)
            job.done = done
            job.total = total

        terminal = JobStatus.FAILED
        try:
            result = fn(progress)
        except JobCancelledError:
            terminal = JobStatus.CANCELLED
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            job.traceback = traceback.format_exc()
        else:
            job.result = result
            if job.total:
                job.done = job.total
            terminal = JobStatus.SUCCEEDED

        job.finished_at = time.time()
        job.status = terminal

    def get(self, job_id: str) -> Job | None:
        """Look up a job."""
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop at its next progress checkpoint.

        Returns:
            True if the job existed and was not already finished.
        """
        job = self.get(job_id)
        if job is None or job.status.finished:
            return False
        job._cancel.set()
        if job._future is not None:
            # Only succeeds if it has not started; harmless either way.
            job._future.cancel()
        if job.status is JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
        return True

    def list(self) -> list[Job]:
        """Every remembered job, newest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs. Caller holds the lock."""
        finished = [j for j in self._jobs.values() if j.status.finished]
        if len(finished) <= self._keep:
            return
        finished.sort(key=lambda j: j.finished_at or 0.0)
        for job in finished[: len(finished) - self._keep]:
            self._jobs.pop(job.id, None)

    def shutdown(self, wait: bool = False) -> None:
        """Stop the pool. Used by tests and by application shutdown."""
        for job in self.list():
            if not job.status.finished:
                job._cancel.set()
        self._pool.shutdown(wait=wait, cancel_futures=True)
