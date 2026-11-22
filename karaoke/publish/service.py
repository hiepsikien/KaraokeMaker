"""API-facing publish operations.

The service owns the only mutable dependencies (OAuth client, store, worker)
and exposes small methods the FastAPI router can call. Tests can inject a fake
provider through ``provider_factory(provider, tokens)`` and a fake worker.
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from karaoke.access import AccessError, grant
from karaoke.assets import known_project_paths
from karaoke.publish.models import (
    Channel,
    Draft,
    PublishJob,
    is_expired,
    now_iso,
)
from karaoke.publish.oauth import GoogleOAuth, OAuthError, redact
from karaoke.publish.store import Conflict, DraftNotFound, PublishStore, StoreError
from karaoke.publish.worker import Worker
from karaoke.publish.youtube import (
    VIDEO_SUFFIXES,
    ValidationError,
    YouTubeClient,
    YouTubeError,
    YouTubeProvider,
    build_video_metadata,
    validate_description,
    validate_privacy,
    validate_title,
    video_url,
)
from karaoke.project import JobError, load_project, project_path_for

ProviderFactory = Callable[[str, dict], Any]


@runtime_checkable
class PublishProvider(Protocol):
    """The tiny surface the worker/service need; easy to fake in tests."""

    def upload(
        self,
        video_path: str | Path,
        metadata: dict,
        **kwargs: Any,
    ) -> Any:
        ...

    def add_to_playlist(self, video_id: str, playlist_id: str) -> str:
        ...

    def list_playlists(self) -> list[dict]:
        ...

    def channel_info(self) -> dict:
        ...


class PublishError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)


class PublishNotFound(PublishError):
    def __init__(self, message: str):
        super().__init__(message, status_code=404)


def default_provider_factory(provider: str, tokens: dict) -> Any:
    """Build the real YouTube provider from a stored token bundle."""
    if provider != "youtube":
        raise PublishError("Nền tảng này chưa được hỗ trợ.", status_code=400)
    return YouTubeProvider(YouTubeClient(str((tokens or {}).get("access_token") or "")))


class PublishService:
    def __init__(
        self,
        *,
        store: PublishStore | None = None,
        oauth: GoogleOAuth | None = None,
        provider_factory: ProviderFactory | None = None,
        worker: Worker | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store or PublishStore()
        self.oauth = oauth or GoogleOAuth(clock=clock)
        self.provider_factory = provider_factory or default_provider_factory
        self.worker = worker or Worker(self.store, self._provider_for_job, clock=clock)

    # ------------------------------------------------------------- channels
    def channels(self) -> list[dict]:
        result = []
        for channel in self.store.list_channels():
            token = self.store.get_token(channel.channel_id)
            payload = channel.to_dict()
            payload["connected"] = bool(token and token.get("access_token"))
            payload["expired"] = not token or is_expired(token.get("expires_at"))
            result.append(payload)
        return result

    def connect_url(self, provider: str = "youtube") -> dict:
        self._require_youtube(provider)
        try:
            request = self.oauth.start()
        except OAuthError as error:
            raise PublishError(str(error), status_code=400) from error
        return {
            "provider": "youtube",
            "url": request["url"],
            "redirect_uri": request["redirect_uri"],
        }

    def finish_oauth(self, provider: str, code: str, state: str) -> Channel:
        self._require_youtube(provider)
        try:
            tokens = self.oauth.exchange_code(code, state)
        except OAuthError as error:
            raise PublishError(str(error), status_code=400) from error
        try:
            adapter = _build_provider(self.provider_factory, "youtube", tokens)
            info = adapter.channel_info()
        except Exception as error:
            raise self._translate_provider_error(error) from error
        channel_id = str(info.get("channel_id") or "").strip()
        if not channel_id:
            raise PublishError("Không đọc được thông tin kênh YouTube.", status_code=502)
        previous = self.store.get_token(channel_id)
        if previous and not tokens.get("refresh_token"):
            tokens["refresh_token"] = previous.get("refresh_token") or ""
        channel = Channel(
            id=f"youtube:{channel_id}",
            provider="youtube",
            channel_id=channel_id,
            name=str(info.get("name") or ""),
            avatar=str(info.get("avatar") or ""),
            scopes=_scope_labels(tokens.get("scopes") or []),
            expires_at=tokens.get("expires_at"),
            connected_at=now_iso(),
        )
        self.store.upsert_channel(channel)
        self.store.save_token(channel_id, tokens)
        return channel

    def disconnect(self, provider: str = "youtube") -> dict:
        self._require_youtube(provider)
        channel = self.store.get_channel("youtube")
        revoked = False
        if channel is not None:
            token = self.store.get_token(channel.channel_id) or {}
            remote = str(token.get("refresh_token") or token.get("access_token") or "")
            if remote:
                revoked = self.oauth.revoke(remote)
            self.store.delete_channel("youtube")
        return {"provider": "youtube", "disconnected": channel is not None, "revoked": revoked}

    # ------------------------------------------------------------ read data
    def playlists(self, provider: str = "youtube") -> list[dict]:
        self._require_youtube(provider)
        adapter = self._provider_for_provider("youtube")
        try:
            return adapter.list_playlists()
        except Exception as error:
            raise self._translate_provider_error(error) from error

    def videos(self, project_path: str | None = None) -> list[dict]:
        """Resolve exported videos from one project or all registered projects.

        The publish page calls this without a path so it can show the latest
        vocal/instrumental export from each project in the recent registry.
        """
        raw = str(project_path or "").strip()
        if raw:
            path = Path(raw).expanduser()
            payload = self._read_project_payload(path)
            if payload is None:
                raise PublishNotFound(f"Không thấy hoặc không đọc được dự án: {path}")
            return self._videos_for_project(path, payload, _mtime(path))

        result: list[dict] = []
        try:
            candidates = known_project_paths()
        except Exception:
            candidates = []
        candidates = sorted(candidates, key=_mtime, reverse=True)
        seen: set[tuple[str, str]] = set()
        for path in candidates[:50]:
            payload = self._read_project_payload(path)
            if payload is None:
                continue
            for video in self._videos_for_project(path, payload, _mtime(path)):
                key = (video["project_path"], video["role"])
                if key in seen:
                    continue
                seen.add(key)
                result.append(video)
        return result

    @staticmethod
    def _read_project_payload(path: Path) -> dict | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _videos_for_project(
        project_path: Path,
        payload: dict,
        project_mtime: float,
    ) -> list[dict]:
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            return []
        title = str(payload.get("title") or "").strip()
        if not title:
            title = project_path.stem
            if title.endswith(".karaoke"):
                title = title[: -len(".karaoke")]
        updated = datetime.fromtimestamp(
            project_mtime or 0, tz=timezone.utc
        ).isoformat()
        labels = {"vocal": "Có giọng", "instrumental": "Không lời"}
        result = []
        for role in ("vocal", "instrumental"):
            value = str(outputs.get(role) or "").strip()
            if not value:
                continue
            video = Path(value).expanduser()
            try:
                stat = video.stat()
                size = int(stat.st_size)
                mtime = int(stat.st_mtime)
                exists = video.is_file()
            except OSError:
                size = 0
                mtime = 0
                exists = False
            if not exists:
                continue
            result.append(
                {
                    "id": f"{project_path}|{role}",
                    "project_path": str(project_path),
                    "project_title": title,
                    "role": role,
                    "label": labels[role],
                    "path": str(video),
                    "filename": video.name,
                    "exists": exists,
                    "size": size,
                    "mtime": mtime,
                    "updated": updated,
                }
            )
        return result

    # --------------------------------------------------------------- drafts
    def drafts(self, *, project: str | None = None, platform: str | None = None) -> list[dict]:
        return [
            draft.to_dict()
            for draft in self.store.list_drafts(project=project, platform=platform)
        ]

    def draft(self, draft_id: str) -> dict:
        return self._draft_or_404(draft_id).to_dict()

    def create_draft(self, payload: dict) -> dict:
        changes = self._validate_draft_changes(dict(payload or {}), creating=True)
        project_path = str(changes.get("project_path") or "").strip()
        if project_path and not str(changes.get("project_title") or "").strip():
            changes["project_title"] = self._project_title(project_path)
        draft = self.store.create_draft(changes)
        return draft.to_dict()

    def update_draft(
        self,
        draft_id: str,
        changes: dict,
        *,
        revision: int | None = None,
    ) -> dict:
        self._draft_or_404(draft_id)
        clean = self._validate_draft_changes(dict(changes or {}), creating=False)
        if revision is None and "revision" in (changes or {}):
            revision = changes["revision"]
        updated = self.store.update_draft(draft_id, clean, expected_revision=revision)
        return updated.to_dict()

    def duplicate_draft(self, draft_id: str, changes: dict | None = None) -> dict:
        self._draft_or_404(draft_id)
        clean = self._validate_draft_changes(dict(changes or {}), creating=False)
        return self.store.duplicate_draft(draft_id, clean).to_dict()

    def delete_draft(self, draft_id: str) -> dict:
        if not self.store.delete_draft(draft_id):
            raise PublishNotFound("Không thấy nháp.")
        return {"deleted": True, "id": draft_id}

    def publish_draft(
        self,
        draft_id: str,
        *,
        overrides: dict | None = None,
        revision: int | None = None,
    ) -> dict:
        if overrides:
            self.update_draft(draft_id, overrides, revision=revision)
        draft = self._draft_or_404(draft_id)
        self._require_youtube(draft.platform)
        try:
            title = validate_title(draft.title, require=True)
            description = validate_description(draft.description)
            privacy = validate_privacy(draft.privacy)
        except ValidationError as error:
            raise PublishError(str(error), status_code=400) from error

        project_file = Path(draft.project_path).expanduser() if draft.project_path else None
        if project_file is None or not project_file.is_file():
            project_file = self._relink_project(draft)
        if project_file is None:
            raise PublishError(
                "Dự án không còn. Hãy chọn lại dự án cho nháp này.",
                status_code=404,
            )

        try:
            project, resolved = self._load_project_for_publish(project_file)
        except AccessError as error:
            raise PublishError(str(error), status_code=403) from error
        except JobError as error:
            raise PublishError(str(error), status_code=400) from error

        outputs = project.get("outputs") if isinstance(project.get("outputs"), dict) else {}
        raw_video = str(outputs.get(draft.video_role) or "").strip()
        if not raw_video:
            raise PublishError(
                "Dự án chưa có video đã xuất cho bản này. Hãy xuất video rồi thử lại.",
                status_code=400,
            )
        video_path = Path(raw_video).expanduser()
        if not video_path.is_file():
            raise PublishError(
                "Không thấy file video mới nhất. Hãy xuất lại video rồi thử lại.",
                status_code=400,
            )
        if video_path.suffix.lower() not in VIDEO_SUFFIXES:
            raise PublishError(
                f"Định dạng {video_path.suffix} chưa được hỗ trợ để đăng YouTube.",
                status_code=400,
            )
        try:
            file_size = int(video_path.stat().st_size)
        except OSError as error:
            raise PublishError(f"Không đọc được video: {error}") from error
        if file_size <= 0:
            raise PublishError("File video rỗng.", status_code=400)

        # Validate before queueing so the user gets the error in the form.
        try:
            build_video_metadata(
                title=title,
                description=description,
                made_for_kids=draft.made_for_kids,
                privacy=privacy,
            )
        except ValidationError as error:
            raise PublishError(str(error), status_code=400) from error

        created = now_iso()
        job = PublishJob(
            id=f"job_{uuid.uuid4().hex[:16]}",
            draft_id=draft.id,
            platform="youtube",
            project_path=str(resolved),
            project_title=str(project.get("title") or draft.project_title or ""),
            video_role=draft.video_role,
            video_path=str(video_path),
            title=title,
            description=description,
            made_for_kids=bool(draft.made_for_kids),
            privacy=privacy,
            playlist_id=draft.playlist_id,
            playlist_name=draft.playlist_name,
            category_id=10,
            status="queued",
            step="upload",
            file_size=file_size,
            max_attempts=3,
            created=created,
            updated=created,
            log=["Đã xếp hàng đăng YouTube."],
        )
        self.store.add_job(job)
        self._enqueue_job(job.id)
        return job.public_dict()

    # ----------------------------------------------------------------- jobs
    def jobs(self, *, project: str | None = None) -> list[dict]:
        return [job.public_dict() for job in self.store.list_jobs(project=project)]

    def job(self, job_id: str) -> dict:
        return self._job_or_404(job_id).public_dict()

    def retry_job(self, job_id: str) -> dict:
        job = self._job_or_404(job_id)
        if job.status == "published":
            raise PublishError("Video đã đăng; không cần thử lại.", status_code=400)
        job.cancel_requested = False
        job.status = "queued"
        job.attempts = 0
        job.last_error = ""
        job.next_attempt_at = None
        job.finished_at = None
        if job.platform_video_id:
            job.step = "playlist" if job.playlist_id else "done"
        else:
            job.step = "upload"
        self.store.save_job(job, preserve_cancel=False)
        self._enqueue_job(job.id)
        return self._job_or_404(job_id).public_dict()

    def cancel_job(self, job_id: str) -> dict:
        job = self._job_or_404(job_id)
        if job.status in ("published", "failed", "cancelled"):
            return job.public_dict()
        job.cancel_requested = True
        if job.status != "uploading":
            job.status = "cancel_requested"
        self.store.save_job(job, preserve_cancel=False)
        # Let the worker notice the flag between chunks / before starting.
        self._enqueue_job(job.id)
        return self._job_or_404(job_id).public_dict()

    # ------------------------------------------------------------- internals
    def _enqueue_job(self, job_id: str) -> None:
        starter = getattr(self.worker, "start", None)
        if callable(starter):
            starter()
        enqueue = getattr(self.worker, "enqueue", None)
        if callable(enqueue):
            enqueue(job_id)

    @staticmethod
    def _require_youtube(provider: Any) -> None:
        text = str(provider or "").strip().lower()
        if text == "tiktok":
            raise PublishError("TikTok chưa được hỗ trợ trong phiên bản này.", status_code=400)
        if text != "youtube":
            raise PublishError("Nền tảng đăng bài không hợp lệ.", status_code=400)

    def _draft_or_404(self, draft_id: str) -> Draft:
        draft = self.store.get_draft(str(draft_id or "").strip())
        if draft is None:
            raise PublishNotFound("Không thấy nháp.")
        return draft

    def _job_or_404(self, job_id: str) -> PublishJob:
        job = self.store.get_job(str(job_id or "").strip())
        if job is None:
            raise PublishNotFound("Không thấy job đăng bài.")
        return job

    def _validate_draft_changes(self, changes: dict, *, creating: bool) -> dict:
        result = dict(changes)
        result.pop("id", None)
        result.pop("created", None)
        result.pop("updated", None)
        result.pop("revision", None)
        result.pop("last_published_at", None)
        result.pop("last_video_id", None)
        result.pop("last_url", None)
        if "platform" in result:
            platform = str(result["platform"] or "").strip().lower()
            if platform != "youtube":
                raise PublishError(
                    "Chỉ hỗ trợ YouTube trong phiên bản này.", status_code=400
                )
            result["platform"] = "youtube"
        else:
            result["platform"] = "youtube"
        if "video_role" in result:
            role = str(result["video_role"] or "").strip().lower()
            if role not in ("vocal", "instrumental"):
                raise PublishError(
                    "Video phải là bản có giọng hoặc không lời.",
                    status_code=400,
                )
            result["video_role"] = role
        try:
            if "title" in result:
                result["title"] = validate_title(result.get("title"), require=False)
            if "description" in result:
                result["description"] = validate_description(result.get("description"))
            if "privacy" in result:
                result["privacy"] = validate_privacy(result.get("privacy"))
            elif creating:
                result["privacy"] = "public"
        except ValidationError as error:
            raise PublishError(str(error), status_code=400) from error
        if "made_for_kids" in result and not isinstance(result["made_for_kids"], bool):
            raise PublishError(
                "Trường 'trẻ em có xem được' phải là true/false.",
                status_code=400,
            )
        for key in ("project_path", "project_title", "playlist_id", "playlist_name"):
            if key in result and result[key] is not None and not isinstance(result[key], str):
                raise PublishError(
                    f"Trường {key} phải là chuỗi.",
                    status_code=400,
                )
        if "project_path" in result:
            result["project_path"] = str(result["project_path"] or "").strip()
        return result

    @staticmethod
    def _project_title(project_path: str) -> str:
        try:
            project, _ = load_project(Path(project_path).expanduser())
        except (JobError, AccessError, OSError):
            return ""
        return str(project.get("title") or "").strip()

    @staticmethod
    def _load_project_for_publish(project_file: Path) -> tuple[dict, Path]:
        """Load a project, granting the folder the user selected for publishing.

        A draft is user-created state; publishing it is the equivalent of
        picking the project in a file dialog, so the same grant the open flow
        uses is applied here. This also makes "Đăng lại" work on a fresh server
        without first visiting the editor page.
        """
        try:
            return load_project(project_file)
        except AccessError:
            grant(project_file.expanduser().parent)
            return load_project(project_file)

    @staticmethod
    def _relink_project(draft: Draft) -> Path | None:
        """Best-effort re-link after a project move/rename (plan §9.5)."""
        if not draft.project_path:
            return None
        parent = Path(draft.project_path).expanduser().parent
        if draft.project_title:
            candidate = project_path_for(parent, draft.project_title)
            if candidate.is_file():
                return candidate
        return None

    def _channel_and_token(self) -> tuple[Channel, dict]:
        channel = self.store.get_channel("youtube")
        if channel is None:
            raise PublishError("Chưa kết nối YouTube.", status_code=400)
        token = self.store.get_token(channel.channel_id)
        if not token or not token.get("access_token"):
            raise PublishError("Chưa kết nối YouTube.", status_code=400)
        if is_expired(token.get("expires_at")):
            refresh_token = str(token.get("refresh_token") or "")
            if not refresh_token:
                raise PublishError(
                    "Phiên YouTube đã hết hạn. Hãy kết nối lại.",
                    status_code=401,
                )
            try:
                fresh = self.oauth.refresh(refresh_token)
            except OAuthError as error:
                raise PublishError(
                    f"Không làm mới được phiên YouTube: {error}. Hãy kết nối lại.",
                    status_code=401,
                ) from error
            if not fresh.get("scopes") and token.get("scopes"):
                fresh["scopes"] = token.get("scopes") or []
            token = {**token, **fresh}
            self.store.save_token(channel.channel_id, token)
            channel.expires_at = token.get("expires_at")
            channel.scopes = _scope_labels(token.get("scopes") or [])
            self.store.upsert_channel(channel)
        return channel, dict(token)

    def _provider_for_provider(self, provider: str) -> Any:
        self._require_youtube(provider)
        _, token = self._channel_and_token()
        try:
            return _build_provider(self.provider_factory, "youtube", token)
        except Exception as error:
            raise self._translate_provider_error(error) from error

    def _provider_for_job(self, job: PublishJob) -> Any:
        self._require_youtube(job.platform)
        _, token = self._channel_and_token()
        return _build_provider(self.provider_factory, "youtube", token)

    @staticmethod
    def _translate_provider_error(error: Exception) -> PublishError:
        text = redact(str(error) or error.__class__.__name__)
        if isinstance(error, ValidationError):
            return PublishError(text, status_code=400)
        if isinstance(error, YouTubeError):
            status = 502 if getattr(error, "retryable", False) else 400
            return PublishError(text, status_code=status)
        if isinstance(error, StoreError):
            return PublishError(text, status_code=500)
        return PublishError(text, status_code=502)


def _build_provider(factory: ProviderFactory, provider: str, tokens: dict) -> Any:
    """Call a 2-arg, 1-arg, or 0-arg provider factory.

    The default factory is ``(provider, tokens)``; a test may pass
    ``lambda: fake`` or ``lambda provider: fake`` without ceremony.
    """
    if not callable(factory):
        return factory
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(provider, tokens)
    parameters = list(signature.parameters.values())
    if any(item.kind is inspect.Parameter.VAR_POSITIONAL for item in parameters):
        return factory(provider, tokens)
    positional = [
        item
        for item in parameters
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return factory(provider, tokens)
    if len(positional) == 1:
        return factory(provider)
    return factory()


def _mtime(path: Path) -> float:
    try:
        return float(Path(path).stat().st_mtime)
    except OSError:
        return 0.0


def _scope_labels(scopes: Any) -> list[str]:
    """Keep full Google scope URLs but add short names for the UI badge."""
    result: list[str] = []
    for item in scopes or []:
        text = str(item or "").strip()
        if not text or text in result:
            continue
        result.append(text)
        short = text.rsplit("/", 1)[-1]
        if short and short not in result:
            result.append(short)
    return result


# --------------------------------------------------------------------- default
_SERVICE: PublishService | None = None
_SERVICE_LOCK = threading.Lock()


def get_service() -> PublishService:
    """Process-wide default used by ``router.py``."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = PublishService()
        return _SERVICE


def set_service(service: PublishService | None) -> None:
    """Replace the default (tests / embedding)."""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = service


def start_publish_worker() -> PublishService:
    """Start the default worker (and recover interrupted jobs) at app startup."""
    service = get_service()
    starter = getattr(service.worker, "start", None)
    if callable(starter):
        starter()
    return service


def reset_service() -> None:
    set_service(None)


__all__ = [
    "Conflict",
    "DraftNotFound",
    "PublishError",
    "PublishNotFound",
    "PublishProvider",
    "PublishService",
    "default_provider_factory",
    "get_service",
    "reset_service",
    "set_service",
    "start_publish_worker",
    "video_url",
]
