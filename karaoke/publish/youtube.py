"""YouTube Data API v3 adapter using plain ``requests``.

The adapter is intentionally stateless apart from the access token. Resumable
upload bookkeeping (upload URL, byte offset) lives on :class:`PublishJob` so a
restarted process can continue instead of re-uploading a large video.
"""

from __future__ import annotations

import copy
import json
import mimetypes
from pathlib import Path
from typing import Any, Callable

import requests

from karaoke.publish.oauth import redact

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"
CHUNK_SIZE = 8 * 1024 * 1024
CATEGORY_MUSIC = 10
CATEGORY_PEOPLE_BLOGS = 22
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000
PRIVACY_VALUES = ("public", "unlisted", "private")
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})


class ValidationError(ValueError):
    """Title/description/privacy failed local validation (API 400)."""


class YouTubeError(RuntimeError):
    """Remote API failure with enough information for the retry policy."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reason: str = "",
        retryable: bool = False,
        payload: dict | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.retryable = retryable
        self.payload = payload or {}

    @classmethod
    def from_response(cls, response: Any) -> "YouTubeError":
        status = int(getattr(response, "status_code", 0) or 0)
        payload: dict = {}
        try:
            raw = response.json()
            payload = raw if isinstance(raw, dict) else {}
        except Exception:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        reason = ""
        message = ""
        if isinstance(error, dict):
            reason = str(error.get("status") or "").strip()
            errors = error.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason") or reason).strip()
            message = str(error.get("message") or "").strip()
        if not message and isinstance(error, str):
            message = error
        retryable = status == 429 or 500 <= status < 600
        detail = message or reason or f"HTTP {status}"
        return cls(
            f"YouTube từ chối yêu cầu ({status}, {redact(detail)[:400]}).",
            status_code=status,
            reason=reason,
            retryable=retryable,
            payload=payload,
        )


class UploadSessionExpired(YouTubeError):
    def __init__(self, status_code: int = 404):
        super().__init__(
            "Phiên tải lên YouTube đã hết hiệu lực; sẽ xếp lại từ đầu.",
            status_code=status_code,
            reason="upload_session_expired",
            retryable=True,
        )


class UploadCancelled(RuntimeError):
    """The user requested cancellation while a chunk loop was running."""


# --------------------------------------------------------------------- validation
def validate_title(title: Any, *, require: bool = True) -> str:
    """Validate a title without truncating emoji or surrogate pairs."""
    value = str(title or "").strip()
    if not value:
        if require:
            raise ValidationError("Hãy nhập tiêu đề video.")
        return ""
    if "<" in value or ">" in value:
        raise ValidationError("Tiêu đề không được chứa dấu < hoặc >.")
    if len(value) > MAX_TITLE_LENGTH:
        raise ValidationError(
            f"Tiêu đề tối đa {MAX_TITLE_LENGTH} ký tự (hiện {len(value)})."
        )
    return value


def validate_description(description: Any) -> str:
    """Descriptions are kept verbatim apart from the length limit."""
    value = "" if description is None else str(description)
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise ValidationError(
            f"Mô tả tối đa {MAX_DESCRIPTION_LENGTH} ký tự (hiện {len(value)})."
        )
    return value


def validate_privacy(privacy: Any, *, default: str = "public") -> str:
    value = str(privacy or default).strip().lower()
    if value not in PRIVACY_VALUES:
        raise ValidationError(
            "Chế độ đăng phải là public, unlisted hoặc private."
        )
    return value


def validate_category(category_id: Any, *, default: int = CATEGORY_MUSIC) -> int:
    try:
        value = int(category_id)
    except (TypeError, ValueError):
        return int(default)
    return value if value > 0 else int(default)


def build_video_metadata(
    *,
    title: Any,
    description: Any = "",
    made_for_kids: Any = False,
    privacy: Any = "public",
    category_id: Any = CATEGORY_MUSIC,
    require_title: bool = True,
) -> dict:
    """Build exactly the five MVP fields (category is internal)."""
    clean_title = validate_title(title, require=require_title)
    clean_description = validate_description(description)
    clean_privacy = validate_privacy(privacy)
    clean_category = validate_category(category_id)
    return {
        "snippet": {
            "title": clean_title,
            "description": clean_description,
            "categoryId": str(clean_category),
        },
        "status": {
            "privacyStatus": clean_privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }


# --------------------------------------------------------------------- helpers
def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    try:
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
    except Exception:
        value = None
    return str(value or "")


def parse_range_offset(value: str | None) -> int | None:
    """``bytes=0-8388607`` → 8388608 (next byte to send)."""
    text = str(value or "").strip()
    if not text:
        return None
    if "=" in text:
        text = text.split("=", 1)[1].strip()
    if "-" not in text:
        return None
    _, end = text.split("-", 1)
    try:
        return int(end.strip()) + 1
    except (TypeError, ValueError):
        return None


def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}" if video_id else ""


def _is_category_error(response: Any) -> bool:
    if int(getattr(response, "status_code", 0) or 0) != 400:
        return False
    try:
        text = str(response.text or "").lower()
    except Exception:
        text = ""
    return "category" in text


class YouTubeClient:
    """Thin REST wrapper. Tests inject any object with get/post/put methods."""

    def __init__(
        self,
        access_token: str,
        *,
        session: Any | None = None,
        chunk_size: int = CHUNK_SIZE,
        api_base: str = YOUTUBE_API_BASE,
        upload_base: str = YOUTUBE_UPLOAD_BASE,
        timeout: float = 60.0,
        upload_timeout: float = 180.0,
    ):
        token = str(access_token or "").strip()
        if not token:
            raise ValidationError("Chưa có access token YouTube.")
        self._access_token = token
        self.session = session if session is not None else requests
        self.chunk_size = max(256 * 1024, int(chunk_size))
        self.api_base = api_base.rstrip("/")
        self.upload_base = upload_base.rstrip("/")
        self.timeout = float(timeout)
        self.upload_timeout = float(upload_timeout)

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------ resumable
    def create_resumable(
        self,
        video_path: str | Path,
        metadata: dict,
        *,
        category_id: int | None = None,
    ) -> str:
        path = Path(video_path)
        try:
            total = path.stat().st_size
        except OSError as error:
            raise ValidationError(f"Không đọc được video: {error}") from error
        if total <= 0:
            raise ValidationError("File video rỗng.")
        mime = mimetypes.guess_type(path.name)[0] or "video/*"
        requested = validate_category(
            category_id if category_id is not None else metadata.get("snippet", {}).get("categoryId")
        )
        candidates = [requested]
        if requested == CATEGORY_MUSIC:
            candidates.append(CATEGORY_PEOPLE_BLOGS)

        url = f"{self.upload_base}/videos?uploadType=resumable&part=snippet,status"
        last_response = None
        for category in candidates:
            body = copy.deepcopy(metadata)
            body.setdefault("snippet", {})["categoryId"] = str(category)
            headers = {
                **self._auth_headers(),
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime,
                "X-Upload-Content-Length": str(total),
            }
            try:
                response = self.session.post(
                    url,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                raise YouTubeError(
                    f"Không kết nối được YouTube: {error}",
                    retryable=True,
                ) from error
            last_response = response
            status = int(getattr(response, "status_code", 0) or 0)
            if status in (200, 201):
                location = _header(response, "Location")
                if not location:
                    raise YouTubeError("YouTube không trả upload URL.", status_code=status)
                return location
            if category == CATEGORY_MUSIC and _is_category_error(response):
                continue
            raise YouTubeError.from_response(response)
        assert last_response is not None
        raise YouTubeError.from_response(last_response)

    def upload_resumable(
        self,
        video_path: str | Path,
        *,
        metadata: dict | None = None,
        upload_url: str | None = None,
        start_offset: int = 0,
        on_session: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict:
        """Upload (or resume) a file and return the ``videos.insert`` payload."""
        path = Path(video_path)
        try:
            total = path.stat().st_size
        except OSError as error:
            raise ValidationError(f"Không đọc được video: {error}") from error
        if total <= 0:
            raise ValidationError("File video rỗng.")

        session_url = str(upload_url or "").strip()
        resumed = bool(session_url)
        if not session_url:
            if metadata is None:
                raise ValidationError("Thiếu metadata để bắt đầu tải lên.")
            session_url = self.create_resumable(path, metadata)
        if on_session is not None:
            on_session(session_url)

        # A fresh session always starts at byte 0, even if a stale offset was
        # passed together with the new upload (defensive; the worker clears it).
        offset = max(0, min(int(start_offset or 0), total)) if resumed else 0
        if on_progress is not None:
            on_progress(offset, total)

        attempts = 0
        with path.open("rb") as handle:
            while True:
                attempts += 1
                if attempts > 100_000:  # pathological guard, never hit normally
                    raise YouTubeError("Tải lên không tiến triển.", retryable=False)
                if cancel_check is not None and cancel_check():
                    raise UploadCancelled("Đã huỷ theo yêu cầu.")

                if offset >= total:
                    # Ask for the final state without re-sending bytes.
                    response = self._put(
                        session_url,
                        b"",
                        {
                            **self._auth_headers(),
                            "Content-Length": "0",
                            "Content-Range": f"bytes */{total}",
                        },
                    )
                    status = int(getattr(response, "status_code", 0) or 0)
                    if status in (200, 201):
                        return self._upload_payload(response)
                    if status == 308:
                        next_offset = parse_range_offset(_header(response, "Range"))
                        if next_offset is None or next_offset >= total:
                            # Do not claim success without a video id; let the
                            # worker retry the status check/session instead.
                            raise YouTubeError(
                                "YouTube chưa xác nhận hoàn tất tải lên.",
                                status_code=308,
                                reason="incomplete_upload",
                                retryable=True,
                            )
                        offset = next_offset
                        if on_progress is not None:
                            on_progress(offset, total)
                        continue
                    self._raise_upload_error(response)
                    continue

                handle.seek(offset)
                chunk = handle.read(self.chunk_size)
                if not chunk:
                    raise YouTubeError("Không đọc được dữ liệu video.", retryable=False)
                end = offset + len(chunk) - 1
                headers = {
                    **self._auth_headers(),
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{total}",
                }
                response = self._put(session_url, chunk, headers)
                status = int(getattr(response, "status_code", 0) or 0)
                if status in (200, 201):
                    payload = self._upload_payload(response)
                    if on_progress is not None:
                        on_progress(total, total)
                    return payload
                if status == 308:
                    next_offset = parse_range_offset(_header(response, "Range"))
                    if next_offset is None or next_offset <= offset:
                        # Missing/invalid Range: trust the bytes we just sent.
                        next_offset = end + 1
                    offset = max(offset, min(next_offset, total))
                    if on_progress is not None:
                        on_progress(offset, total)
                    continue
                self._raise_upload_error(response)

    def upload_video(
        self,
        video_path: str | Path,
        metadata: dict,
        **kwargs,
    ) -> dict:
        """Convenience alias for a complete resumable ``videos.insert``."""
        return self.upload_resumable(video_path, metadata=metadata, **kwargs)

    def _put(self, url: str, data: bytes, headers: dict) -> Any:
        try:
            return self.session.put(
                url,
                data=data,
                headers=headers,
                timeout=self.upload_timeout,
            )
        except requests.RequestException as error:
            raise YouTubeError(
                f"Kết nối YouTube bị gián đoạn: {error}",
                retryable=True,
            ) from error

    @staticmethod
    def _upload_payload(response: Any) -> dict:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        video_id = str(payload.get("id") or "").strip()
        if not video_id:
            raise YouTubeError(
                "YouTube không trả video id sau khi tải lên.",
                status_code=int(getattr(response, "status_code", 0) or 0),
                reason="missing_video_id",
                retryable=False,
            )
        return payload

    @staticmethod
    def _raise_upload_error(response: Any) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        if status in (404, 410):
            raise UploadSessionExpired(status)
        raise YouTubeError.from_response(response)

    # --------------------------------------------------------------- playlist
    def add_to_playlist(self, video_id: str, playlist_id: str) -> str:
        video = str(video_id or "").strip()
        playlist = str(playlist_id or "").strip()
        if not video or not playlist:
            return ""
        body = {
            "snippet": {
                "playlistId": playlist,
                "resourceId": {"kind": "youtube#video", "videoId": video},
            }
        }
        url = f"{self.api_base}/playlistItems?part=snippet"
        try:
            response = self.session.post(
                url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={
                    **self._auth_headers(),
                    "Content-Type": "application/json; charset=UTF-8",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise YouTubeError(
                f"Không thêm được video vào playlist: {error}",
                retryable=True,
            ) from error
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in (200, 201):
            raise YouTubeError.from_response(response)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        return str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""

    # --------------------------------------------------------------- read APIs
    def list_playlists(self, *, max_pages: int = 20) -> list[dict]:
        results: list[dict] = []
        page_token = ""
        for _ in range(max_pages):
            params = {
                "part": "snippet,contentDetails",
                "mine": "true",
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get_json(f"{self.api_base}/playlists", params=params)
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                details = (
                    item.get("contentDetails")
                    if isinstance(item.get("contentDetails"), dict)
                    else {}
                )
                results.append(
                    {
                        "id": str(item.get("id") or ""),
                        "title": str(snippet.get("title") or ""),
                        "description": str(snippet.get("description") or ""),
                        "item_count": int(details.get("itemCount") or 0),
                    }
                )
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                break
        results.sort(key=lambda item: item["title"].casefold())
        return results

    def channel_info(self) -> dict:
        payload = self._get_json(
            f"{self.api_base}/channels",
            params={"part": "snippet", "mine": "true"},
        )
        items = payload.get("items") or []
        if not items or not isinstance(items[0], dict):
            raise YouTubeError(
                "Tài khoản Google chưa có kênh YouTube.",
                reason="no_channel",
                retryable=False,
            )
        item = items[0]
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        avatar = ""
        for key in ("default", "medium", "high"):
            entry = thumbnails.get(key)
            if isinstance(entry, dict) and entry.get("url"):
                avatar = str(entry["url"])
                break
        return {
            "channel_id": str(item.get("id") or ""),
            "name": str(snippet.get("title") or ""),
            "avatar": avatar,
        }

    def video_categories(self, *, region_code: str = "VN") -> list[dict]:
        payload = self._get_json(
            f"{self.api_base}/videoCategories",
            params={"part": "snippet", "regionCode": region_code},
        )
        result = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            result.append(
                {
                    "id": str(item.get("id") or ""),
                    "title": str(snippet.get("title") or ""),
                    "assignable": bool(snippet.get("assignable", True)),
                }
            )
        return result

    def _get_json(self, url: str, *, params: dict) -> dict:
        try:
            response = self.session.get(
                url,
                params=params,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise YouTubeError(
                f"Không kết nối được YouTube: {error}",
                retryable=True,
            ) from error
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise YouTubeError.from_response(response)
        try:
            payload = response.json()
        except Exception as error:
            raise YouTubeError(
                "YouTube trả dữ liệu không đọc được.",
                status_code=status,
                reason="invalid_json",
                retryable=False,
            ) from error
        return payload if isinstance(payload, dict) else {}


class YouTubeProvider:
    """The small provider protocol consumed by the worker."""

    name = "youtube"

    def __init__(self, client: YouTubeClient):
        self.client = client

    def upload(
        self,
        video_path: str | Path,
        metadata: dict,
        *,
        resume_url: str | None = None,
        start_offset: int = 0,
        on_session: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict:
        payload = self.client.upload_resumable(
            video_path,
            metadata=metadata,
            upload_url=resume_url,
            start_offset=start_offset,
            on_session=on_session,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        video_id = str(payload.get("id") or "").strip()
        return {"video_id": video_id, "url": video_url(video_id), "raw": payload}

    def add_to_playlist(self, video_id: str, playlist_id: str) -> str:
        return self.client.add_to_playlist(video_id, playlist_id)

    def list_playlists(self) -> list[dict]:
        return self.client.list_playlists()

    def channel_info(self) -> dict:
        return self.client.channel_info()


__all__ = [
    "CATEGORY_MUSIC",
    "CATEGORY_PEOPLE_BLOGS",
    "CHUNK_SIZE",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_TITLE_LENGTH",
    "PRIVACY_VALUES",
    "UploadCancelled",
    "UploadSessionExpired",
    "VIDEO_SUFFIXES",
    "ValidationError",
    "YouTubeClient",
    "YouTubeError",
    "YouTubeProvider",
    "build_video_metadata",
    "parse_range_offset",
    "validate_category",
    "validate_description",
    "validate_privacy",
    "validate_title",
    "video_url",
]
