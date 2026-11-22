"""JSON persistence for drafts, jobs, channels and OAuth tokens.

Design rules (plan §9.5 and §9.9):

* every read-modify-write happens while holding both an in-process ``RLock``
  and the cross-process file lock from :mod:`karaoke.settings`;
* JSON is written to a sibling temp file, fsynced, then ``os.replace``-d so a
  crash can never leave a half-written document;
* published files are chmod ``0600`` – ``tokens.json`` and ``channels.json``
  contain credentials/account metadata;
* the remote ``upload_url`` is persisted on jobs for resume but never leaves
  this module through the public service layer.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from karaoke.cache import data_root
from karaoke.publish.models import (
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_FINALIZING,
    JOB_INITIALIZING,
    JOB_PUBLISHED,
    JOB_QUEUED,
    JOB_STATUSES,
    JOB_UPLOADING,
    Channel,
    Draft,
    PublishJob,
    now_iso,
)
from karaoke.settings import _file_lock

DRAFTS_FILE = "drafts.json"
JOBS_FILE = "jobs.json"
CHANNELS_FILE = "channels.json"
TOKENS_FILE = "tokens.json"

DRAFT_MUTABLE_FIELDS = frozenset(
    {
        "project_path",
        "project_title",
        "platform",
        "video_role",
        "title",
        "description",
        "made_for_kids",
        "privacy",
        "playlist_id",
        "playlist_name",
    }
)
TOKEN_FIELDS = ("access_token", "refresh_token", "expires_at", "scopes", "token_type")

_PROCESS_LOCK = threading.RLock()
_MAX_LOG_LINES = 200


class StoreError(RuntimeError):
    """Persisted data is unreadable (manual edit, full disk, permissions…)."""


class DraftNotFound(StoreError):
    def __init__(self, draft_id: str):
        super().__init__(f"Không thấy nháp {draft_id}.")
        self.draft_id = draft_id


class Conflict(StoreError):
    """The caller's ``revision`` is stale; ``latest`` is the current draft."""

    def __init__(self, latest: Draft):
        super().__init__("Nháp đã được thay đổi ở nơi khác.")
        self.latest = latest


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _normal_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        # ``resolve`` also collapses macOS /var → /private/var, which matters
        # when the API receives the path the user picked and the project loader
        # returns the resolved one.
        return os.path.normcase(str(Path(text).expanduser().resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(os.path.expanduser(text)))


def _clean_token(payload: Any) -> dict:
    data = payload if isinstance(payload, dict) else {}
    result: dict[str, Any] = {}
    for key in TOKEN_FIELDS:
        value = data.get(key)
        if key == "scopes":
            result[key] = [item for item in value if isinstance(item, str)] if isinstance(value, (list, tuple)) else []
        elif value is None:
            result[key] = None
        elif isinstance(value, str):
            result[key] = value
    return result


class PublishStore:
    """Small repository around ``data_root()/publish``."""

    def __init__(self, root: Path | str | None = None):
        self._root = Path(root).expanduser() if root is not None else None

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else data_root("publish")

    def path(self, name: str) -> Path:
        return self.root / name

    # ------------------------------------------------------------------ io
    @contextmanager
    def _guard(self, name: str) -> Iterator[None]:
        # ``_file_lock`` gives cross-process exclusion; the re-entrant process
        # lock keeps two local threads from fighting over flock semantics.
        with _PROCESS_LOCK:
            with _file_lock(self.root / f".{name}.lock"):
                yield

    def _read_list_unlocked(self, name: str, key: str) -> list[dict]:
        path = self.path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as error:
            raise StoreError(f"Không đọc được {path.name}: {error}") from error
        if not text.strip():
            return []
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise StoreError(f"{path.name} bị hỏng: {error}") from error
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for candidate in (key, "items", "records"):
                value = payload.get(candidate)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _read_map_unlocked(self, name: str, key: str) -> dict:
        path = self.path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise StoreError(f"Không đọc được {path.name}: {error}") from error
        if not text.strip():
            return {}
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise StoreError(f"{path.name} bị hỏng: {error}") from error
        if isinstance(payload, dict):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
            return payload
        return {}

    def _write_unlocked(self, name: str, payload: Any) -> None:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_drafts_unlocked(self) -> list[Draft]:
        return [Draft.from_dict(item) for item in self._read_list_unlocked(DRAFTS_FILE, "drafts")]

    def _write_drafts_unlocked(self, drafts: list[Draft]) -> None:
        self._write_unlocked(DRAFTS_FILE, [draft.to_dict() for draft in drafts])

    def _read_jobs_unlocked(self) -> list[PublishJob]:
        return [PublishJob.from_dict(item) for item in self._read_list_unlocked(JOBS_FILE, "jobs")]

    def _write_jobs_unlocked(self, jobs: list[PublishJob]) -> None:
        self._write_unlocked(JOBS_FILE, [job.to_dict() for job in jobs])

    def _read_channels_unlocked(self) -> list[Channel]:
        return [
            Channel.from_dict(item)
            for item in self._read_list_unlocked(CHANNELS_FILE, "channels")
        ]

    def _write_channels_unlocked(self, channels: list[Channel]) -> None:
        self._write_unlocked(CHANNELS_FILE, [channel.to_dict() for channel in channels])

    def _read_tokens_unlocked(self) -> dict[str, dict]:
        raw = self._read_map_unlocked(TOKENS_FILE, "tokens")
        result: dict[str, dict] = {}
        for channel_id, payload in raw.items():
            if isinstance(payload, dict):
                result[str(channel_id)] = _clean_token(payload)
        return result

    def _write_tokens_unlocked(self, tokens: dict[str, dict]) -> None:
        self._write_unlocked(
            TOKENS_FILE,
            {str(channel_id): _clean_token(payload) for channel_id, payload in tokens.items()},
        )

    @staticmethod
    def _find_draft(drafts: list[Draft], draft_id: str) -> tuple[int, Draft]:
        for index, draft in enumerate(drafts):
            if draft.id == draft_id:
                return index, draft
        raise DraftNotFound(draft_id)

    @staticmethod
    def _apply_draft_changes(draft: Draft, changes: dict) -> Draft:
        data = draft.to_dict()
        for key in DRAFT_MUTABLE_FIELDS:
            if key not in changes:
                continue
            value = changes[key]
            if key == "made_for_kids":
                if isinstance(value, bool):
                    data[key] = value
            elif key in {"title", "description"}:
                if isinstance(value, str):
                    data[key] = value
            elif key in {"project_path", "project_title", "playlist_id", "playlist_name"}:
                if isinstance(value, str):
                    data[key] = value.strip() if key == "project_path" else value
            elif key in {"platform", "video_role", "privacy"}:
                if isinstance(value, str) and value.strip():
                    data[key] = value.strip()
        return Draft.from_dict(data)

    @staticmethod
    def _append_log(job: PublishJob, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        job.log.append(text)
        if len(job.log) > _MAX_LOG_LINES:
            del job.log[: len(job.log) - _MAX_LOG_LINES]

    # --------------------------------------------------------------- drafts
    def list_drafts(
        self,
        *,
        project: str | None = None,
        platform: str | None = None,
        video_role: str | None = None,
    ) -> list[Draft]:
        with self._guard(DRAFTS_FILE):
            drafts = self._read_drafts_unlocked()
        project_key = _normal_path(project) if project else ""
        platform_key = str(platform or "").strip().lower()
        role_key = str(video_role or "").strip().lower()
        if project_key:
            drafts = [draft for draft in drafts if _normal_path(draft.project_path) == project_key]
        if platform_key:
            drafts = [draft for draft in drafts if draft.platform.lower() == platform_key]
        if role_key:
            drafts = [draft for draft in drafts if draft.video_role.lower() == role_key]
        drafts.sort(key=lambda draft: (draft.updated, draft.created), reverse=True)
        return drafts

    def get_draft(self, draft_id: str) -> Draft | None:
        with self._guard(DRAFTS_FILE):
            for draft in self._read_drafts_unlocked():
                if draft.id == draft_id:
                    return draft
        return None

    def create_draft(self, payload: Draft | dict | None = None) -> Draft:
        source = payload.to_dict() if isinstance(payload, Draft) else dict(payload or {})
        draft = self._apply_draft_changes(Draft(id=""), source)
        draft.id = _new_id("draft")
        draft.created = now_iso()
        draft.updated = draft.created
        draft.revision = 1
        draft.last_published_at = None
        draft.last_video_id = None
        draft.last_url = None
        if draft.platform not in ("youtube", "tiktok"):
            draft.platform = "youtube"
        if draft.video_role not in ("vocal", "instrumental"):
            draft.video_role = "vocal"
        if draft.privacy not in ("public", "unlisted", "private"):
            draft.privacy = "public"
        with self._guard(DRAFTS_FILE):
            drafts = self._read_drafts_unlocked()
            drafts.append(draft)
            self._write_drafts_unlocked(drafts)
        return draft

    def update_draft(
        self,
        draft_id: str,
        changes: dict,
        *,
        expected_revision: int | None = None,
    ) -> Draft:
        with self._guard(DRAFTS_FILE):
            drafts = self._read_drafts_unlocked()
            index, existing = self._find_draft(drafts, draft_id)
            if expected_revision is not None:
                try:
                    wanted = int(expected_revision)
                except (TypeError, ValueError):
                    raise Conflict(existing) from None
                if wanted != existing.revision:
                    raise Conflict(existing)
            updated = self._apply_draft_changes(existing, dict(changes or {}))
            updated.id = existing.id
            updated.created = existing.created
            updated.revision = existing.revision + 1
            updated.updated = now_iso()
            updated.last_published_at = existing.last_published_at
            updated.last_video_id = existing.last_video_id
            updated.last_url = existing.last_url
            drafts[index] = updated
            self._write_drafts_unlocked(drafts)
        return updated

    def save_draft(self, draft: Draft, *, expected_revision: int | None = None) -> Draft:
        """Autosave helper: persist a full record through the revision check."""
        return self.update_draft(
            draft.id,
            draft.to_dict(),
            expected_revision=expected_revision,
        )

    # Explicit name used by the API layer / tests.
    autosave_draft = save_draft

    def duplicate_draft(self, draft_id: str, changes: dict | None = None) -> Draft:
        with self._guard(DRAFTS_FILE):
            drafts = self._read_drafts_unlocked()
            _, source = self._find_draft(drafts, draft_id)
            duplicate = self._apply_draft_changes(source, dict(changes or {}))
            duplicate.id = _new_id("draft")
            duplicate.created = now_iso()
            duplicate.updated = duplicate.created
            duplicate.revision = 1
            duplicate.last_published_at = None
            duplicate.last_video_id = None
            duplicate.last_url = None
            drafts.append(duplicate)
            self._write_drafts_unlocked(drafts)
        return duplicate

    def delete_draft(self, draft_id: str) -> bool:
        with self._guard(DRAFTS_FILE):
            drafts = self._read_drafts_unlocked()
            remaining = [draft for draft in drafts if draft.id != draft_id]
            if len(remaining) == len(drafts):
                return False
            self._write_drafts_unlocked(remaining)
        return True

    def mark_draft_published(
        self,
        draft_id: str,
        *,
        video_id: str,
        url: str,
        published_at: str | None = None,
    ) -> Draft | None:
        """Record history without bumping ``revision`` (avoids fake conflicts)."""
        with self._guard(DRAFTS_FILE):
            drafts = self._read_drafts_unlocked()
            for draft in drafts:
                if draft.id != draft_id:
                    continue
                draft.last_published_at = published_at or now_iso()
                draft.last_video_id = video_id
                draft.last_url = url
                draft.updated = now_iso()
                self._write_drafts_unlocked(drafts)
                return draft
        return None

    # ----------------------------------------------------------------- jobs
    def list_jobs(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
    ) -> list[PublishJob]:
        with self._guard(JOBS_FILE):
            jobs = self._read_jobs_unlocked()
        project_key = _normal_path(project) if project else ""
        status_key = str(status or "").strip().lower()
        if project_key:
            jobs = [job for job in jobs if _normal_path(job.project_path) == project_key]
        if status_key:
            jobs = [job for job in jobs if job.status.lower() == status_key]
        jobs.sort(key=lambda job: (job.created, job.id), reverse=True)
        return jobs

    def get_job(self, job_id: str) -> PublishJob | None:
        with self._guard(JOBS_FILE):
            for job in self._read_jobs_unlocked():
                if job.id == job_id:
                    return job
        return None

    def add_job(self, job: PublishJob) -> PublishJob:
        if not job.id:
            job.id = _new_id("job")
        if job.status not in JOB_STATUSES:
            job.status = JOB_QUEUED
        job.created = job.created or now_iso()
        job.updated = now_iso()
        with self._guard(JOBS_FILE):
            jobs = self._read_jobs_unlocked()
            jobs.append(job)
            self._write_jobs_unlocked(jobs)
        return job

    def save_job(self, job: PublishJob, *, preserve_cancel: bool = True) -> PublishJob:
        if job.status not in JOB_STATUSES:
            raise StoreError(f"Trạng thái job không hợp lệ: {job.status}")
        with self._guard(JOBS_FILE):
            jobs = self._read_jobs_unlocked()
            index = None
            existing: PublishJob | None = None
            for position, candidate in enumerate(jobs):
                if candidate.id == job.id:
                    index = position
                    existing = candidate
                    break
            if index is None:
                job.created = job.created or now_iso()
                job.updated = now_iso()
                jobs.append(job)
            else:
                if existing is not None:
                    if preserve_cancel and existing.cancel_requested:
                        job.cancel_requested = True
                    job.created = existing.created or job.created
                job.updated = now_iso()
                jobs[index] = job
            self._write_jobs_unlocked(jobs)
        return job

    def update_job(
        self,
        job_id: str,
        changes: dict,
        *,
        preserve_cancel: bool = True,
    ) -> PublishJob | None:
        with self._guard(JOBS_FILE):
            jobs = self._read_jobs_unlocked()
            for index, existing in enumerate(jobs):
                if existing.id != job_id:
                    continue
                data = existing.to_dict()
                for key, value in dict(changes or {}).items():
                    if key in data and key not in {"id", "created", "updated"}:
                        data[key] = value
                updated = PublishJob.from_dict(data)
                if preserve_cancel and existing.cancel_requested:
                    updated.cancel_requested = True
                updated.created = existing.created
                updated.updated = now_iso()
                jobs[index] = updated
                self._write_jobs_unlocked(jobs)
                return updated
        return None

    def recover_jobs(self) -> list[PublishJob]:
        """Apply the startup recovery rules from plan §9.6.

        An ``uploading`` job with a resumable URL keeps its URL and status so
        the worker can continue. Without one (or after the URL expires and is
        cleared) it returns to ``queued`` with a visible warning. Jobs that
        already have a YouTube video id go straight to the playlist step.
        Returns the jobs that still need processing.
        """
        active: list[PublishJob] = []
        with self._guard(JOBS_FILE):
            jobs = self._read_jobs_unlocked()
            changed = False
            for job in jobs:
                if job.status in (JOB_PUBLISHED, JOB_CANCELLED):
                    continue
                active.append(job)
                if job.status == JOB_UPLOADING:
                    if job.upload_url:
                        job.step = "upload"
                        continue
                    job.status = JOB_QUEUED
                    job.step = "upload"
                    self._append_log(
                        job,
                        "Phiên tải lên đã hết hiệu lực; xếp lại từ đầu.",
                    )
                    changed = True
                elif job.status == JOB_INITIALIZING:
                    job.status = JOB_QUEUED
                    self._append_log(job, "Khởi động lại job bị gián đoạn.")
                    changed = True
                elif job.status == JOB_FINALIZING:
                    job.status = JOB_QUEUED
                    if job.platform_video_id:
                        job.step = "playlist"
                        self._append_log(
                            job,
                            "Video đã tải lên; tiếp tục bước playlist.",
                        )
                    else:
                        job.step = "upload"
                        self._append_log(job, "Khởi động lại bước tải lên.")
                    changed = True
                elif job.status == JOB_FAILED:
                    # Failed jobs wait for an explicit retry.
                    active.pop()
            if changed:
                self._write_jobs_unlocked(jobs)
        return active

    def jobs_due(self, now: str | None = None) -> list[PublishJob]:
        """Queued jobs whose backoff has elapsed (or never had one)."""
        current = now or now_iso()
        due: list[PublishJob] = []
        with self._guard(JOBS_FILE):
            for job in self._read_jobs_unlocked():
                if job.status != JOB_QUEUED or job.cancel_requested:
                    continue
                if job.next_attempt_at and job.next_attempt_at > current:
                    continue
                due.append(job)
        return due

    # ------------------------------------------------------------- channels
    def list_channels(self, *, provider: str | None = None) -> list[Channel]:
        with self._guard(CHANNELS_FILE):
            channels = self._read_channels_unlocked()
        key = str(provider or "").strip().lower()
        if key:
            channels = [channel for channel in channels if channel.provider.lower() == key]
        channels.sort(key=lambda channel: channel.connected_at, reverse=True)
        return channels

    def get_channel(self, provider: str) -> Channel | None:
        for channel in self.list_channels(provider=provider):
            if channel.provider.lower() == str(provider).lower():
                return channel
        return None

    def upsert_channel(self, channel: Channel) -> Channel:
        old_ids: list[str] = []
        with self._guard(CHANNELS_FILE):
            channels = self._read_channels_unlocked()
            kept: list[Channel] = []
            for existing in channels:
                if existing.provider.lower() != channel.provider.lower():
                    kept.append(existing)
                elif existing.channel_id != channel.channel_id:
                    old_ids.append(existing.channel_id)
            channel.connected_at = channel.connected_at or now_iso()
            if not channel.id:
                channel.id = f"{channel.provider}:{channel.channel_id}"
            kept.append(channel)
            self._write_channels_unlocked(kept)
        for channel_id in old_ids:
            self.delete_token(channel_id)
        return channel

    def delete_channel(self, provider: str) -> bool:
        removed_ids: list[str] = []
        with self._guard(CHANNELS_FILE):
            channels = self._read_channels_unlocked()
            kept: list[Channel] = []
            for channel in channels:
                if channel.provider.lower() == str(provider).lower():
                    removed_ids.append(channel.channel_id)
                else:
                    kept.append(channel)
            if not removed_ids:
                return False
            self._write_channels_unlocked(kept)
        for channel_id in removed_ids:
            self.delete_token(channel_id)
        return True

    # --------------------------------------------------------------- tokens
    def get_token(self, channel_id: str) -> dict | None:
        with self._guard(TOKENS_FILE):
            tokens = self._read_tokens_unlocked()
        payload = tokens.get(str(channel_id))
        return dict(payload) if isinstance(payload, dict) else None

    def save_token(self, channel_id: str, tokens: dict) -> dict:
        with self._guard(TOKENS_FILE):
            stored = self._read_tokens_unlocked()
            stored[str(channel_id)] = _clean_token(tokens)
            self._write_tokens_unlocked(stored)
        return stored[str(channel_id)]

    def delete_token(self, channel_id: str) -> bool:
        with self._guard(TOKENS_FILE):
            stored = self._read_tokens_unlocked()
            if str(channel_id) not in stored:
                return False
            del stored[str(channel_id)]
            self._write_tokens_unlocked(stored)
        return True
