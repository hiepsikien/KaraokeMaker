"""FastAPI router for the publish feature.

This module is intentionally **not** imported by ``karaoke/server.py``. Wire it
in once the rest of the branch is ready::

    from karaoke.publish.router import router as publish_router
    app.include_router(publish_router)

All routes are prefixed with ``/api/publish``.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from karaoke.publish.oauth import callback_html
from karaoke.publish.service import (
    Conflict,
    DraftNotFound,
    PublishError,
    PublishService,
    StoreError,
    get_service,
)

router = APIRouter(prefix="/api/publish", tags=["publish"])


class DraftBody(BaseModel):
    """Payload for draft create/autosave; omitted fields stay untouched."""

    model_config = ConfigDict(extra="ignore")

    project_path: str | None = None
    project_title: str | None = None
    platform: str | None = None
    video_role: str | None = None
    title: str | None = None
    description: str | None = None
    made_for_kids: bool | None = None
    privacy: str | None = None
    playlist_id: str | None = None
    playlist_name: str | None = None
    revision: int | None = None


class PublishBody(DraftBody):
    """Optional final edits + optimistic revision for "Đăng ngay"."""


def _service() -> PublishService:
    return get_service()


def _raise_http(error: Exception) -> None:
    if isinstance(error, Conflict):
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "draft": error.latest.to_dict(),
            },
        )
    if isinstance(error, DraftNotFound):
        raise HTTPException(status_code=404, detail=str(error))
    if isinstance(error, PublishError):
        raise HTTPException(status_code=error.status_code, detail=str(error))
    if isinstance(error, StoreError):
        raise HTTPException(status_code=500, detail=str(error))
    raise error


def _run(action: Callable[[], Any]) -> Any:
    try:
        return action()
    except Conflict as error:
        # Top-level (not under FastAPI's ``detail``) so the publish page can
        # read ``payload.draft`` and offer "reload latest".
        return JSONResponse(
            status_code=409,
            content={
                "message": str(error),
                "draft": error.latest.to_dict(),
                # Also keep the conventional FastAPI shape for other clients.
                "detail": {
                    "message": str(error),
                    "draft": error.latest.to_dict(),
                },
            },
        )
    except Exception as error:  # mapped to an HTTP response or re-raised
        _raise_http(error)
        raise  # pragma: no cover - _raise_http always raises


# ------------------------------------------------------------------- channels
@router.get("/channels")
def list_channels():
    return _run(lambda: _service().channels())


@router.post("/channels/{provider}/connect")
def connect_channel(provider: str):
    return _run(lambda: _service().connect_url(provider))


@router.get("/oauth/callback")
def oauth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """OAuth redirect target registered as ``/api/publish/oauth/callback``."""
    if error:
        message = error_description or error
        return HTMLResponse(
            callback_html(ok=False, message=f"Google báo lỗi: {message}"),
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            callback_html(ok=False, message="Thiếu mã đăng nhập hoặc state."),
            status_code=400,
        )
    try:
        _service().finish_oauth("youtube", code, state)
    except Exception as failure:
        if isinstance(failure, PublishError):
            return HTMLResponse(
                callback_html(ok=False, message=str(failure)),
                status_code=400,
            )
        raise
    # The Next.js app expects ``/publish?connected=1`` after the backend
    # callback. ``KARAOKEMAKER_WEB_URL`` lets a non-default UI port override it.
    web_base = (
        os.environ.get("KARAOKEMAKER_WEB_URL", "").strip()
        or "http://127.0.0.1:3000"
    ).rstrip("/")
    query = urllib.parse.urlencode({"connected": "1"})
    return RedirectResponse(f"{web_base}/publish?{query}", status_code=302)


@router.delete("/channels/{provider}")
def disconnect_channel(provider: str):
    return _run(lambda: _service().disconnect(provider))


# --------------------------------------------------------------- read helpers
@router.get("/youtube/playlists")
def youtube_playlists():
    return _run(lambda: _service().playlists("youtube"))


@router.get("/videos")
def list_videos(project_path: str = "", project: str = ""):
    target = project_path or project
    return _run(lambda: _service().videos(target))


# --------------------------------------------------------------------- drafts
@router.get("/drafts")
def list_drafts(project: str = "", platform: str = ""):
    return _run(
        lambda: _service().drafts(project=project or None, platform=platform or None)
    )


@router.post("/drafts")
def create_draft(body: DraftBody):
    payload = body.model_dump(exclude_unset=True)
    return _run(lambda: _service().create_draft(payload))


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: str, body: DraftBody):
    payload = body.model_dump(exclude_unset=True)
    revision = payload.pop("revision", None)
    return _run(lambda: _service().update_draft(draft_id, payload, revision=revision))


@router.post("/drafts/{draft_id}/duplicate")
def duplicate_draft(draft_id: str, body: DraftBody | None = None):
    payload = body.model_dump(exclude_unset=True) if body is not None else {}
    payload.pop("revision", None)
    return _run(lambda: _service().duplicate_draft(draft_id, payload))


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str):
    return _run(lambda: _service().delete_draft(draft_id))


@router.post("/drafts/{draft_id}/publish")
def publish_draft(draft_id: str, body: PublishBody | None = None):
    payload = body.model_dump(exclude_unset=True) if body is not None else {}
    revision = payload.pop("revision", None)
    return _run(
        lambda: _service().publish_draft(
            draft_id,
            overrides=payload or None,
            revision=revision,
        )
    )


# ----------------------------------------------------------------------- jobs
@router.get("/jobs")
def list_jobs(project: str = ""):
    return _run(lambda: _service().jobs(project=project or None))


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return _run(lambda: _service().job(job_id))


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    return _run(lambda: _service().retry_job(job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return _run(lambda: _service().cancel_job(job_id))


__all__ = ["router"]
