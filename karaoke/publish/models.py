"""Plain data records for the YouTube publisher.

Everything here is JSON-friendly so the store can persist records directly and
the API layer can hand ``to_dict()`` output to FastAPI without a second mapping.
Timestamps are ISO-8601 UTC strings with a ``Z`` suffix; they sort
lexicographically and survive a JSON round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_PLATFORM = "youtube"
SUPPORTED_PLATFORMS = ("youtube",)
VIDEO_ROLES = ("vocal", "instrumental")
PRIVACY_VALUES = ("public", "unlisted", "private")

# Job states from the plan (§9.6). ``cancel_requested`` is a request, not a
# promise: once YouTube has accepted the video it cannot be recalled.
JOB_QUEUED = "queued"
JOB_INITIALIZING = "initializing"
JOB_UPLOADING = "uploading"
JOB_FINALIZING = "finalizing"
JOB_PUBLISHED = "published"
JOB_FAILED = "failed"
JOB_CANCEL_REQUESTED = "cancel_requested"
JOB_CANCELLED = "cancelled"

JOB_STATUSES = (
    JOB_QUEUED,
    JOB_INITIALIZING,
    JOB_UPLOADING,
    JOB_FINALIZING,
    JOB_PUBLISHED,
    JOB_FAILED,
    JOB_CANCEL_REQUESTED,
    JOB_CANCELLED,
)

TERMINAL_JOB_STATUSES = frozenset({JOB_PUBLISHED, JOB_FAILED, JOB_CANCELLED})

# Allowed forward transitions. Recovery may put an interrupted job back into
# ``queued``; manual retry moves ``failed`` back to ``queued`` as well.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    JOB_QUEUED: frozenset({JOB_INITIALIZING, JOB_CANCEL_REQUESTED, JOB_CANCELLED}),
    JOB_INITIALIZING: frozenset(
        {JOB_UPLOADING, JOB_QUEUED, JOB_FAILED, JOB_CANCEL_REQUESTED, JOB_CANCELLED}
    ),
    JOB_UPLOADING: frozenset(
        {
            JOB_FINALIZING,
            JOB_QUEUED,
            JOB_FAILED,
            JOB_CANCEL_REQUESTED,
            JOB_CANCELLED,
        }
    ),
    JOB_FINALIZING: frozenset(
        {JOB_PUBLISHED, JOB_QUEUED, JOB_FAILED, JOB_CANCEL_REQUESTED, JOB_CANCELLED}
    ),
    JOB_FAILED: frozenset({JOB_QUEUED, JOB_CANCELLED}),
    JOB_CANCEL_REQUESTED: frozenset({JOB_CANCELLED, JOB_PUBLISHED, JOB_FAILED}),
    JOB_PUBLISHED: frozenset(),
    JOB_CANCELLED: frozenset(),
}


def now_iso() -> str:
    """Current UTC time as an ISO string with millisecond precision."""
    moment = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return moment.replace("+00:00", "Z")


def plus_seconds_iso(seconds: float, *, base: datetime | None = None) -> str:
    moment = (base or datetime.now(timezone.utc)) + timedelta(seconds=float(seconds))
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp; accept ``Z`` and naive values (assumed UTC)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_expired(value: str | None, *, skew_seconds: float = 60.0) -> bool:
    """True when a token expiry is missing, invalid, or within ``skew_seconds``."""
    parsed = parse_iso(value)
    if parsed is None:
        return True
    return parsed <= datetime.now(timezone.utc) + timedelta(seconds=float(skew_seconds))


def can_transition(current: str, target: str) -> bool:
    """Pure state-machine check used by tests and the worker."""
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else None


def _flag(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str)]


class Record:
    """Small mixin providing a stable ``to_dict`` based on dataclass fields."""

    def to_dict(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass
class Draft(Record):
    """Saved publish content, keyed by project + platform + video role."""

    id: str
    project_path: str = ""
    project_title: str = ""
    platform: str = DEFAULT_PLATFORM
    video_role: str = "vocal"
    title: str = ""
    description: str = ""
    made_for_kids: bool = False
    privacy: str = "public"
    playlist_id: str = ""
    playlist_name: str = ""
    created: str = ""
    updated: str = ""
    revision: int = 1
    last_published_at: str | None = None
    last_video_id: str | None = None
    last_url: str | None = None

    @classmethod
    def from_dict(cls, payload: Any) -> "Draft":
        data = _mapping(payload)
        return cls(
            id=_text(data.get("id")),
            project_path=_text(data.get("project_path")),
            project_title=_text(data.get("project_title")),
            platform=_text(data.get("platform"), DEFAULT_PLATFORM) or DEFAULT_PLATFORM,
            video_role=_text(data.get("video_role"), "vocal") or "vocal",
            title=_text(data.get("title")),
            description=_text(data.get("description")),
            made_for_kids=_flag(data.get("made_for_kids"), False),
            privacy=_text(data.get("privacy"), "public") or "public",
            playlist_id=_text(data.get("playlist_id")),
            playlist_name=_text(data.get("playlist_name")),
            created=_text(data.get("created")),
            updated=_text(data.get("updated")),
            revision=max(1, _integer(data.get("revision"), 1)),
            last_published_at=_optional_text(data.get("last_published_at")),
            last_video_id=_optional_text(data.get("last_video_id")),
            last_url=_optional_text(data.get("last_url")),
        )


@dataclass
class Channel(Record):
    """A connected publishing channel. Never contains a token."""

    id: str
    provider: str = DEFAULT_PLATFORM
    channel_id: str = ""
    name: str = ""
    avatar: str = ""
    scopes: list[str] = field(default_factory=list)
    expires_at: str | None = None
    connected_at: str = ""

    @classmethod
    def from_dict(cls, payload: Any) -> "Channel":
        data = _mapping(payload)
        provider = _text(data.get("provider"), DEFAULT_PLATFORM) or DEFAULT_PLATFORM
        channel_id = _text(data.get("channel_id"))
        return cls(
            id=_text(data.get("id")) or f"{provider}:{channel_id}",
            provider=provider,
            channel_id=channel_id,
            name=_text(data.get("name")),
            avatar=_text(data.get("avatar")),
            scopes=_string_list(data.get("scopes")),
            expires_at=_optional_text(data.get("expires_at")),
            connected_at=_text(data.get("connected_at")),
        )


@dataclass
class PublishJob(Record):
    """Snapshot of one publish attempt plus resumable upload bookkeeping."""

    id: str
    draft_id: str = ""
    platform: str = DEFAULT_PLATFORM
    project_path: str = ""
    project_title: str = ""
    video_role: str = "vocal"
    video_path: str = ""
    title: str = ""
    description: str = ""
    made_for_kids: bool = False
    privacy: str = "public"
    playlist_id: str = ""
    playlist_name: str = ""
    category_id: int = 10

    status: str = JOB_QUEUED
    step: str = "upload"  # upload | playlist | done
    progress: int = 0
    bytes_uploaded: int = 0
    file_size: int = 0
    attempts: int = 0
    max_attempts: int = 3
    last_error: str = ""
    next_attempt_at: str | None = None

    platform_video_id: str | None = None
    playlist_item_id: str | None = None
    # Capability URLs / remote ids are persisted for recovery but must never
    # be returned by the API or written to a log.
    upload_url: str | None = None
    publish_id: str | None = None

    cancel_requested: bool = False
    url: str = ""
    created: str = ""
    updated: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    published_at: str | None = None
    log: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Any) -> "PublishJob":
        data = _mapping(payload)
        status = _text(data.get("status"), JOB_QUEUED) or JOB_QUEUED
        if status not in JOB_STATUSES:
            status = JOB_QUEUED
        return cls(
            id=_text(data.get("id")),
            draft_id=_text(data.get("draft_id")),
            platform=_text(data.get("platform"), DEFAULT_PLATFORM) or DEFAULT_PLATFORM,
            project_path=_text(data.get("project_path")),
            project_title=_text(data.get("project_title")),
            video_role=_text(data.get("video_role"), "vocal") or "vocal",
            video_path=_text(data.get("video_path")),
            title=_text(data.get("title")),
            description=_text(data.get("description")),
            made_for_kids=_flag(data.get("made_for_kids"), False),
            privacy=_text(data.get("privacy"), "public") or "public",
            playlist_id=_text(data.get("playlist_id")),
            playlist_name=_text(data.get("playlist_name")),
            category_id=_integer(data.get("category_id"), 10),
            status=status,
            step=_text(data.get("step"), "upload") or "upload",
            progress=max(0, min(100, _integer(data.get("progress"), 0))),
            bytes_uploaded=max(0, _integer(data.get("bytes_uploaded"), 0)),
            file_size=max(0, _integer(data.get("file_size"), 0)),
            attempts=max(0, _integer(data.get("attempts"), 0)),
            max_attempts=max(1, _integer(data.get("max_attempts"), 3)),
            last_error=_text(data.get("last_error")),
            next_attempt_at=_optional_text(data.get("next_attempt_at")),
            platform_video_id=_optional_text(data.get("platform_video_id")),
            playlist_item_id=_optional_text(data.get("playlist_item_id")),
            upload_url=_optional_text(data.get("upload_url")),
            publish_id=_optional_text(data.get("publish_id")),
            cancel_requested=_flag(data.get("cancel_requested"), False),
            url=_text(data.get("url")),
            created=_text(data.get("created")),
            updated=_text(data.get("updated")),
            started_at=_optional_text(data.get("started_at")),
            finished_at=_optional_text(data.get("finished_at")),
            published_at=_optional_text(data.get("published_at")),
            log=_string_list(data.get("log")),
        )

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    def public_dict(self) -> dict:
        """API-safe view: no capability URL, no remote publish id, no token."""
        data = self.to_dict()
        data.pop("upload_url", None)
        data.pop("publish_id", None)
        if not data.get("url") and self.platform_video_id:
            data["url"] = f"https://youtu.be/{self.platform_video_id}"
        return data
