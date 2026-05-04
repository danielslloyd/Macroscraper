"""In-process async job queue.

A single worker pulls jobs off an `asyncio.Queue` and runs them serially.
Recovery: on startup we scan master.json for any product still in `cleaning`
status (orphaned by a crash) and re-enqueue it. Pending-scrape recovery is
opportunistic — the UI knows which products are missing and can re-queue.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Literal

from .storage import load_master_sync, utcnow_iso

JobType = Literal["crawl", "scrape", "clean"]
JobStatus = Literal["pending", "running", "completed", "failed"]

JobHandler = Callable[["Job"], Awaitable[None]]


@dataclass
class Job:
    id: str
    type: JobType
    payload: dict
    status: JobStatus = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    product_id: str | None = None
    retailer: str | None = None
    activity_label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _State:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    activity: list[Job] = field(default_factory=list)
    pending: dict[str, Job] = field(default_factory=dict)
    running: Job | None = None
    handlers: dict[JobType, JobHandler] = field(default_factory=dict)
    worker_task: asyncio.Task | None = None
    activity_limit: int = 50


_state = _State()


def register_handler(job_type: JobType, handler: JobHandler) -> None:
    _state.handlers[job_type] = handler


def submit(
    job_type: JobType,
    payload: dict,
    *,
    product_id: str | None = None,
    retailer: str | None = None,
    activity_label: str = "",
) -> Job:
    job = Job(
        id=f"job_{uuid.uuid4().hex[:12]}",
        type=job_type,
        payload=payload,
        product_id=product_id,
        retailer=retailer,
        activity_label=activity_label or job_type,
    )
    _state.pending[job.id] = job
    _state.queue.put_nowait(job)
    return job


def snapshot() -> dict:
    pending_scrape = sum(
        1 for j in _state.pending.values() if j.type == "scrape" and j.status == "pending"
    )
    pending_clean = sum(
        1 for j in _state.pending.values() if j.type == "clean" and j.status == "pending"
    )
    processing = 1 if _state.running is not None else 0
    return {
        "pending_scrape": pending_scrape,
        "pending_clean": pending_clean,
        "processing": processing,
    }


def recent_activity(limit: int = 10) -> list[dict]:
    return [j.to_dict() for j in list(reversed(_state.activity))[:limit]]


async def _worker() -> None:
    while True:
        job: Job = await _state.queue.get()
        handler = _state.handlers.get(job.type)
        job.status = "running"
        job.started_at = utcnow_iso()
        _state.running = job
        try:
            if handler is None:
                raise RuntimeError(f"no handler registered for job type {job.type}")
            await handler(job)
            job.status = "completed"
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.completed_at = utcnow_iso()
            _state.running = None
            _state.pending.pop(job.id, None)
            _state.activity.append(job)
            if len(_state.activity) > _state.activity_limit:
                _state.activity = _state.activity[-_state.activity_limit :]
            _state.queue.task_done()


def start_worker() -> None:
    if _state.worker_task is None or _state.worker_task.done():
        _state.worker_task = asyncio.create_task(_worker())


def recover_orphans() -> list[Job]:
    """Re-enqueue any product stuck in `cleaning` status from a previous run."""
    data = load_master_sync()
    requeued: list[Job] = []
    for pid, prod in data.get("products", {}).items():
        if prod.get("status") == "cleaning":
            requeued.append(
                submit(
                    "clean",
                    {"product_id": pid},
                    product_id=pid,
                    retailer=prod.get("retailer"),
                    activity_label=f"recover:clean:{pid}",
                )
            )
    return requeued
