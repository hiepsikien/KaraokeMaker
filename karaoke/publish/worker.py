"""One-thread publish worker.

The worker is deliberately synchronous in the middle: tests and the API can
call :meth:`Worker.process_job` directly with a fake provider, while the app
uses :meth:`start` to run the same code on a daemon thread.

Retry rules (plan §9.6):
* retry only network/5xx/429 errors, with 30 s, 2 min, 10 min backoff;
* playlist failures never re-upload the video (``platform_video_id`` is saved
  before the playlist call and ``step`` becomes ``playlist``);
* job progress is persisted at most once per second or per 5 percentage points;
* an interrupted ``uploading`` job resumes its resumable URL when possible.
"""

from __future__ import annotations

import inspect
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from karaoke.publish.models import (
    JOB_CANCEL_REQUESTED,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_FINALIZING,
    JOB_INITIALIZING,
    JOB_PUBLISHED,
    JOB_QUEUED,
    JOB_UPLOADING,
    PublishJob,
    can_transition,
    now_iso,
    plus_seconds_iso,
)
from karaoke.publish.oauth import redact
from karaoke.publish.store import PublishStore
from karaoke.publish.youtube import (
    UploadCancelled,
    UploadSessionExpired,
    YouTubeError,
    build_video_metadata,
)

logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS = (30.0, 120.0, 600.0)
_MAX_LOG_LINES = 200


def _provider_kwargs(callable_object: Callable[..., Any], values: dict) -> dict:
    """Only forward hooks the provider actually accepts.

    Production uses the full YouTube provider, but a tiny fake with just
    ``upload(path, metadata)`` must keep working in tests.
    """
    try:
        signature = inspect.signature(callable_object)
    except (TypeError, ValueError):
        return dict(values)
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return dict(values)
    return {key: value for key, value in values.items() if key in signature.parameters}


def _clip_log(job: PublishJob, message: str) -> None:
    text = redact(message).strip()
    if not text:
        return
    job.log.append(text)
    if len(job.log) > _MAX_LOG_LINES:
        del job.log[: len(job.log) - _MAX_LOG_LINES]


class _ProgressWriter:
    """Persist upload progress at most every second or every 5 percent."""

    def __init__(self, worker: "Worker", job: PublishJob):
        self.worker = worker
        self.job = job
        self.last_time = float(worker.clock())
        self.last_percent = int(job.progress)

    def update(self, bytes_uploaded: int, total: int, *, force: bool = False) -> None:
        total = max(0, int(total or 0))
        uploaded = max(0, int(bytes_uploaded or 0))
        percent = int(uploaded * 100 / total) if total else 0
        self.job.bytes_uploaded = max(self.job.bytes_uploaded, uploaded)
        # 100 is reserved for "published"; during upload the bar stops at 99.
        self.job.progress = max(self.job.progress, min(99, percent))
        now = float(self.worker.clock())
        should_write = (
            force
            or now - self.last_time >= self.worker.progress_interval
            or abs(self.job.progress - self.last_percent) >= 5
        )
        if should_write:
            self.worker.store.save_job(self.job)
            self.last_time = now
            self.last_percent = self.job.progress


class Worker:
    """Serial queue for publish jobs.

    ``provider_factory`` is ``callable(job) -> provider``. The provider only
    needs ``upload`` and ``add_to_playlist`` (plus the read methods used by the
    service). Keeping the protocol tiny makes a fake provider trivial.
    """

    def __init__(
        self,
        store: PublishStore,
        provider_factory: Callable[[PublishJob], Any],
        *,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.time,
        progress_interval: float = 1.0,
    ):
        self.store = store
        if callable(provider_factory):
            self.provider_factory = provider_factory
        else:
            # Convenience for tests: passing a ready-made fake provider.
            provider_object = provider_factory
            self.provider_factory = lambda _job: provider_object
        self.retry_delays = tuple(float(item) for item in retry_delays) or DEFAULT_RETRY_DELAYS
        self.max_attempts = max(1, int(max_attempts))
        self.clock = clock
        self.progress_interval = max(0.05, float(progress_interval))
        self._queue: queue.Queue[str] = queue.Queue()
        self._queued: set[str] = set()
        self._queued_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_lock = threading.Lock()

    # ---------------------------------------------------------------- queue
    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self.recover_jobs()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="karaoke-publish-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def enqueue(self, job_id: str) -> None:
        key = str(job_id or "").strip()
        if not key:
            return
        with self._queued_lock:
            if key in self._queued:
                return
            self._queued.add(key)
        self._queue.put(key)

    def pending(self) -> int:
        with self._queued_lock:
            return len(self._queued)

    def drain(self) -> None:
        """Process everything currently queued (useful for tests)."""
        while True:
            try:
                job_id = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._queued_lock:
                self._queued.discard(job_id)
            self.process_job(job_id)

    def sweep(self) -> None:
        """Enqueue queued jobs whose retry backoff has elapsed."""
        for job in self.store.jobs_due():
            self.enqueue(job.id)

    def recover_jobs(self) -> list[str]:
        """Apply startup recovery and enqueue work that still needs a run."""
        active = self.store.recover_jobs()
        ids: list[str] = []
        for job in active:
            if job.status in (JOB_FAILED, JOB_PUBLISHED, JOB_CANCELLED):
                continue
            ids.append(job.id)
            self.enqueue(job.id)
        return ids

    # ------------------------------------------------------------- main loop
    def _run_loop(self) -> None:
        last_sweep = 0.0
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                now = float(self.clock())
                if now - last_sweep >= 1.0:
                    last_sweep = now
                    try:
                        self.sweep()
                    except Exception:
                        logger.exception("Publish worker sweep failed.")
                continue
            with self._queued_lock:
                self._queued.discard(job_id)
            try:
                self.process_job(job_id)
            except Exception:
                logger.exception("Publish worker job crashed.")

    # ------------------------------------------------------------- processing
    def process_job(self, job_id: str) -> PublishJob | None:
        """Run one job synchronously. Safe to call from tests."""
        job = self.store.get_job(job_id)
        if job is None or job.status in (JOB_PUBLISHED, JOB_CANCELLED, JOB_FAILED):
            return job
        if job.cancel_requested or job.status == JOB_CANCEL_REQUESTED:
            if job.platform_video_id:
                _clip_log(
                    job,
                    "Video đã được tải lên YouTube nên không thể huỷ; "
                    "đang hoàn tất bước còn lại.",
                )
            else:
                return self._cancel(job)

        if job.platform_video_id and job.step == "playlist":
            return self._finalize(job)
        if job.platform_video_id and job.step == "done":
            return self._publish(job)
        return self._upload(job)

    def _upload(self, job: PublishJob) -> PublishJob:
        if job.status not in (JOB_UPLOADING, JOB_QUEUED):
            if can_transition(job.status, JOB_INITIALIZING):
                job.status = JOB_INITIALIZING
            else:
                job.status = JOB_QUEUED
        if job.status != JOB_UPLOADING:
            job.status = JOB_INITIALIZING
            job.started_at = job.started_at or now_iso()
            job.next_attempt_at = None
            job.last_error = ""
            _clip_log(job, "Bắt đầu tải video lên YouTube.")
            self.store.save_job(job)
            job.status = JOB_UPLOADING
            self.store.save_job(job)

        path = Path(job.video_path)
        if not path.is_file():
            return self._fail(job, "Không thấy file video để đăng.", retryable=False)

        try:
            provider = self.provider_factory(job)
        except Exception as error:
            return self._fail(
                job,
                f"Không lấy được quyền YouTube: {error}",
                retryable=False,
            )

        try:
            metadata = build_video_metadata(
                title=job.title,
                description=job.description,
                made_for_kids=job.made_for_kids,
                privacy=job.privacy,
                category_id=job.category_id,
            )
        except Exception as error:
            return self._fail(job, str(error), retryable=False)

        writer = _ProgressWriter(self, job)

        def on_session(upload_url: str) -> None:
            # Persist immediately: losing the URL means re-uploading from zero.
            job.upload_url = str(upload_url or "")
            writer.update(job.bytes_uploaded, job.file_size, force=True)

        def on_progress(offset: int, total: int) -> None:
            if total:
                job.file_size = max(job.file_size, int(total))
            writer.update(offset, total)

        def cancel_check() -> bool:
            latest = self.store.get_job(job.id)
            return bool(latest and latest.cancel_requested) or self._stop.is_set()

        call_kwargs = _provider_kwargs(
            provider.upload,
            {
                "resume_url": job.upload_url,
                "start_offset": job.bytes_uploaded,
                "on_session": on_session,
                "on_progress": on_progress,
                "cancel_check": cancel_check,
            },
        )
        try:
            result = provider.upload(path, metadata, **call_kwargs)
        except UploadCancelled:
            return self._cancel(job)
        except Exception as error:
            return self._failure(job, error)

        video_id = self._video_id(result)
        if not video_id:
            return self._failure(
                job,
                YouTubeError(
                    "YouTube không trả video id sau khi tải lên.",
                    reason="missing_video_id",
                    retryable=False,
                ),
            )

        job.platform_video_id = video_id
        job.url = self._video_url(result, video_id)
        # The capability URL must not outlive the completed session.
        job.upload_url = None
        job.bytes_uploaded = job.file_size
        job.progress = 100
        job.step = "playlist" if job.playlist_id else "done"
        job.status = JOB_FINALIZING
        job.last_error = ""
        job.next_attempt_at = None
        _clip_log(job, f"Đã tải video lên YouTube ({video_id}).")
        # Persist before the playlist call: a crash here must not re-upload.
        self.store.save_job(job)

        if job.playlist_id:
            return self._finalize(job)
        return self._publish(job)

    def _finalize(self, job: PublishJob) -> PublishJob:
        if job.cancel_requested:
            _clip_log(
                job,
                "Không thể huỷ sau khi video đã tải lên; vẫn thêm vào playlist.",
            )
        if job.playlist_id:
            try:
                provider = self.provider_factory(job)
                item_id = provider.add_to_playlist(job.platform_video_id or "", job.playlist_id)
            except Exception as error:
                job.step = "playlist"
                return self._failure(job, error)
            job.playlist_item_id = str(item_id or "")
            _clip_log(job, "Đã thêm video vào playlist.")
        return self._publish(job)

    def _publish(self, job: PublishJob) -> PublishJob:
        if can_transition(job.status, JOB_PUBLISHED):
            job.status = JOB_PUBLISHED
        else:
            job.status = JOB_PUBLISHED
        job.step = "done"
        job.progress = 100
        job.last_error = ""
        job.next_attempt_at = None
        job.published_at = job.published_at or now_iso()
        job.finished_at = now_iso()
        job.url = job.url or (
            f"https://youtu.be/{job.platform_video_id}" if job.platform_video_id else ""
        )
        _clip_log(job, "Đã đăng video.")
        job.published_at = job.published_at or now_iso()
        self.store.save_job(job)
        try:
            self.store.mark_draft_published(
                job.draft_id,
                video_id=job.platform_video_id or "",
                url=job.url,
                published_at=job.published_at,
            )
        except Exception:
            logger.exception("Could not update draft publish history.")
        return job

    def _cancel(self, job: PublishJob) -> PublishJob:
        job.cancel_requested = True
        job.status = JOB_CANCELLED
        job.finished_at = now_iso()
        job.next_attempt_at = None
        job.upload_url = None
        job.last_error = "Đã huỷ theo yêu cầu."
        _clip_log(job, "Đã huỷ job trước khi video được đăng.")
        self.store.save_job(job)
        return job

    # --------------------------------------------------------------- failure
    def _failure(self, job: PublishJob, error: Exception) -> PublishJob:
        message = redact(str(error) or error.__class__.__name__)[:500]
        retryable = bool(getattr(error, "retryable", False))
        if isinstance(error, UploadSessionExpired):
            job.upload_url = None
            job.bytes_uploaded = 0
            job.progress = 0
            _clip_log(job, "Phiên tải lên đã hết hiệu lực; sẽ tải lại từ đầu.")
        return self._fail(job, message, retryable=retryable)

    def _fail(self, job: PublishJob, message: str, *, retryable: bool) -> PublishJob:
        job.attempts = int(job.attempts) + 1
        job.last_error = redact(message)[:500]
        if retryable and job.attempts < max(job.max_attempts, self.max_attempts):
            delay = self.retry_delays[min(job.attempts - 1, len(self.retry_delays) - 1)]
            base = datetime.fromtimestamp(float(self.clock()), timezone.utc)
            job.next_attempt_at = plus_seconds_iso(delay, base=base)
            job.status = JOB_QUEUED
            _clip_log(
                job,
                f"Lỗi tạm thời (lần {job.attempts}/{max(job.max_attempts, self.max_attempts)}): "
                f"{job.last_error} Thử lại sau {int(delay)} giây.",
            )
        else:
            job.status = JOB_FAILED
            job.next_attempt_at = None
            job.finished_at = now_iso()
            _clip_log(job, f"Đăng thất bại: {job.last_error}")
        self.store.save_job(job)
        return job

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _video_id(result: Any) -> str:
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            value = result.get("video_id") or result.get("id")
            if value:
                return str(value).strip()
            raw = result.get("raw")
            if isinstance(raw, dict) and raw.get("id"):
                return str(raw["id"]).strip()
        return ""

    @staticmethod
    def _video_url(result: Any, video_id: str) -> str:
        if isinstance(result, dict) and result.get("url"):
            return str(result["url"])
        return f"https://youtu.be/{video_id}" if video_id else ""


__all__ = ["DEFAULT_RETRY_DELAYS", "Worker"]
