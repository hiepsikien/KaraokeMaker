import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from karaoke.publish.models import PublishJob, now_iso
from karaoke.publish.store import PublishStore
from karaoke.publish.worker import Worker
from karaoke.publish.youtube import (
    ValidationError,
    YouTubeClient,
    YouTubeError,
    build_video_metadata,
    parse_range_offset,
    validate_description,
    validate_title,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text or (
            json.dumps(self._payload, ensure_ascii=False)
            if isinstance(self._payload, dict)
            else str(self._payload)
        )

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, *, post=None, put=None, get=None):
        self._responses = {
            "POST": list(post or []),
            "PUT": list(put or []),
            "GET": list(get or []),
        }
        self.calls = []

    def _take(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        responses = self._responses[method]
        if not responses:
            raise AssertionError(f"Unexpected {method} {url}")
        item = responses.pop(0)
        if callable(item):
            return item(method, url, kwargs)
        return item

    def post(self, url, **kwargs):
        return self._take("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._take("PUT", url, **kwargs)

    def get(self, url, **kwargs):
        return self._take("GET", url, **kwargs)


class FakeProvider:
    def __init__(self, *, fail_playlist_times=0):
        self.fail_playlist_times = fail_playlist_times
        self.upload_calls = 0
        self.playlist_calls = 0
        self.resume = None
        self.metadata = None

    def upload(
        self,
        video_path,
        metadata,
        *,
        resume_url=None,
        start_offset=0,
        on_session=None,
        on_progress=None,
        cancel_check=None,
    ):
        self.upload_calls += 1
        self.resume = (resume_url, start_offset)
        self.metadata = metadata
        if on_session:
            on_session("https://upload.example/secret-session")
        size = Path(video_path).stat().st_size
        if on_progress:
            on_progress(size, size)
        return {"video_id": "vid123", "url": "https://youtu.be/vid123"}

    def add_to_playlist(self, video_id, playlist_id):
        self.playlist_calls += 1
        if self.playlist_calls <= self.fail_playlist_times:
            raise YouTubeError("tạm lỗi", status_code=429, retryable=True)
        return "playlist-item-1"


class YouTubeMetadataTests(unittest.TestCase):
    def test_metadata_defaults_and_emoji(self):
        metadata = build_video_metadata(
            title="🎤 Bài hát",
            description="🎶 Mô tả 👨‍👩‍👧",
            made_for_kids=False,
            privacy="public",
        )
        self.assertEqual(metadata["snippet"]["categoryId"], "10")
        self.assertEqual(metadata["snippet"]["title"], "🎤 Bài hát")
        self.assertEqual(metadata["snippet"]["description"], "🎶 Mô tả 👨‍👩‍👧")
        self.assertFalse(metadata["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(metadata["status"]["privacyStatus"], "public")

    def test_validation_uses_code_points_and_rejects_angle_brackets(self):
        self.assertEqual(validate_title("🎤" * 100), "🎤" * 100)
        with self.assertRaises(ValidationError):
            validate_title("🎤" * 101)
        with self.assertRaises(ValidationError):
            validate_title("Bài <hát>")
        with self.assertRaises(ValidationError):
            validate_title("")
        with self.assertRaises(ValidationError):
            validate_description("🎵" * 5001)
        self.assertEqual(len(validate_description("🎵" * 5000)), 5000)
        self.assertEqual(validate_description(""), "")

    def test_description_emoji_is_not_truncated(self):
        description = "🎶" * 4999 + "🎤"
        self.assertEqual(validate_description(description), description)


class YouTubeUploadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"x" * 1024)
        self.metadata = build_video_metadata(title="🎤 Bài", description="🎶")

    def tearDown(self):
        self.folder.cleanup()

    def test_create_resumable_returns_location_and_preserves_utf8(self):
        session = FakeSession(
            post=[
                FakeResponse(
                    200,
                    headers={"Location": "https://upload.example/session-1"},
                )
            ]
        )
        client = YouTubeClient("token", session=session)
        location = client.create_resumable(self.video, self.metadata)
        self.assertEqual(location, "https://upload.example/session-1")
        body = json.loads(session.calls[0]["data"].decode("utf-8"))
        self.assertEqual(body["snippet"]["title"], "🎤 Bài")
        self.assertIn("🎤", session.calls[0]["data"].decode("utf-8"))
        self.assertEqual(body["snippet"]["categoryId"], "10")

    def test_category_falls_back_to_people_blogs(self):
        session = FakeSession(
            post=[
                FakeResponse(
                    400,
                    {"error": {"message": "invalid categoryId"}},
                    text="invalid categoryId",
                ),
                FakeResponse(
                    200,
                    headers={"Location": "https://upload.example/session-2"},
                ),
            ]
        )
        client = YouTubeClient("token", session=session)
        client.create_resumable(self.video, self.metadata)
        first = json.loads(session.calls[0]["data"].decode("utf-8"))
        second = json.loads(session.calls[1]["data"].decode("utf-8"))
        self.assertEqual(first["snippet"]["categoryId"], "10")
        self.assertEqual(second["snippet"]["categoryId"], "22")

    def test_chunk_math_is_eight_megabytes_and_resumes_308(self):
        size = 8 * 1024 * 1024 + 123
        self.video.write_bytes(b"y" * size)
        session = FakeSession(
            post=[
                FakeResponse(
                    200,
                    headers={"Location": "https://upload.example/session-3"},
                )
            ],
            put=[
                FakeResponse(308, headers={"Range": "bytes=0-8388607"}),
                FakeResponse(200, {"id": "vid-abc"}),
            ],
        )
        client = YouTubeClient("token", session=session)
        payload = client.upload_resumable(self.video, metadata=self.metadata)
        self.assertEqual(payload["id"], "vid-abc")

        puts = [call for call in session.calls if call["method"] == "PUT"]
        self.assertEqual(len(puts), 2)
        self.assertEqual(puts[0]["headers"]["Content-Range"], f"bytes 0-8388607/{size}")
        self.assertEqual(int(puts[0]["headers"]["Content-Length"]), 8 * 1024 * 1024)
        self.assertEqual(
            puts[1]["headers"]["Content-Range"],
            f"bytes 8388608-{size - 1}/{size}",
        )
        self.assertEqual(int(puts[1]["headers"]["Content-Length"]), 123)

    def test_resume_starts_at_offset_without_creating_a_session(self):
        self.video.write_bytes(b"z" * 5000)
        session = FakeSession(put=[FakeResponse(200, {"id": "vid-resume"})])
        client = YouTubeClient("token", session=session)
        payload = client.upload_resumable(
            self.video,
            upload_url="https://upload.example/session-4",
            start_offset=1000,
        )
        self.assertEqual(payload["id"], "vid-resume")
        self.assertEqual([call["method"] for call in session.calls], ["PUT"])
        call = session.calls[0]
        self.assertEqual(call["headers"]["Content-Range"], "bytes 1000-4999/5000")

    def test_playlist_payload(self):
        session = FakeSession(post=[FakeResponse(200, {"id": "item-9"})])
        client = YouTubeClient("token", session=session)
        item = client.add_to_playlist("vid-abc", "PL123")
        self.assertEqual(item, "item-9")
        body = json.loads(session.calls[0]["data"].decode("utf-8"))
        self.assertEqual(body["snippet"]["playlistId"], "PL123")
        self.assertEqual(body["snippet"]["resourceId"]["videoId"], "vid-abc")

    def test_range_parser(self):
        self.assertEqual(parse_range_offset("bytes=0-1023"), 1024)
        self.assertIsNone(parse_range_offset(""))
        self.assertIsNone(parse_range_offset("garbage"))


class PublishWorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.data_root = Path(self.folder.name) / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.env = patch.dict(os.environ, {"KARAOKE_DATA_DIR": str(self.data_root)})
        self.env.start()
        self.store = PublishStore()
        self.video = Path(self.folder.name) / "v.mp4"
        self.video.write_bytes(b"v" * 4096)
        self.draft = self.store.create_draft(
            {
                "project_path": "/tmp/project.karaoke.json",
                "project_title": "Bài",
                "title": "🎤 Bài",
                "playlist_id": "PL1",
                "playlist_name": "Karaoke",
            }
        )

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def make_job(self, **overrides):
        payload = {
            "id": "job_test",
            "draft_id": self.draft.id,
            "video_path": str(self.video),
            "title": "🎤 Bài",
            "playlist_id": "PL1",
            "playlist_name": "Karaoke",
            "file_size": self.video.stat().st_size,
            "status": "queued",
            "max_attempts": 3,
            "created": now_iso(),
        }
        payload.update(overrides)
        job = PublishJob(**payload)
        self.store.add_job(job)
        return job

    def test_playlist_failure_retry_does_not_reupload(self):
        provider = FakeProvider(fail_playlist_times=1)
        worker = Worker(self.store, lambda job: provider, retry_delays=(0.0,))
        self.make_job()

        failed = worker.process_job("job_test")
        self.assertEqual(failed.status, "queued")
        self.assertEqual(failed.step, "playlist")
        self.assertEqual(failed.platform_video_id, "vid123")
        self.assertEqual(failed.attempts, 1)
        self.assertEqual(provider.upload_calls, 1)
        self.assertEqual(provider.playlist_calls, 1)
        self.assertFalse(failed.upload_url)  # capability URL is cleared

        retried = worker.process_job("job_test")
        self.assertEqual(retried.status, "published")
        self.assertEqual(retried.step, "done")
        self.assertEqual(retried.url, "https://youtu.be/vid123")
        self.assertEqual(provider.upload_calls, 1)
        self.assertEqual(provider.playlist_calls, 2)
        self.assertEqual(self.store.get_draft(self.draft.id).last_video_id, "vid123")
        self.assertEqual(retried.log[-1], "Đã đăng video.")

    def test_recovery_resumes_upload_session(self):
        provider = FakeProvider()
        worker = Worker(self.store, lambda job: provider)
        self.make_job(
            status="uploading",
            step="upload",
            upload_url="https://upload.example/session-resume",
            bytes_uploaded=1024,
        )
        job = worker.process_job("job_test")
        self.assertEqual(job.status, "published")
        self.assertEqual(
            provider.resume,
            ("https://upload.example/session-resume", 1024),
        )

    def test_cancel_before_upload(self):
        provider = FakeProvider()
        worker = Worker(self.store, lambda job: provider)
        self.make_job(cancel_requested=True)
        job = worker.process_job("job_test")
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(provider.upload_calls, 0)

    def test_cancel_after_upload_cannot_recall_video(self):
        provider = FakeProvider()
        worker = Worker(self.store, lambda job: provider)
        self.make_job(
            status="finalizing",
            step="playlist",
            platform_video_id="vid123",
            cancel_requested=True,
        )
        job = worker.process_job("job_test")
        self.assertEqual(job.status, "published")
        self.assertEqual(provider.upload_calls, 0)
        self.assertEqual(provider.playlist_calls, 1)
        self.assertTrue(any("không thể huỷ" in line for line in job.log))

    def test_non_retryable_error_fails_job(self):
        class BrokenProvider(FakeProvider):
            def upload(self, *args, **kwargs):
                raise YouTubeError("forbidden", status_code=403, retryable=False)

        worker = Worker(self.store, lambda job: BrokenProvider())
        self.make_job()
        job = worker.process_job("job_test")
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(job.next_attempt_at)

    def test_retryable_error_schedules_backoff(self):
        class FlakyProvider(FakeProvider):
            def upload(self, *args, **kwargs):
                raise YouTubeError("server", status_code=503, retryable=True)

        worker = Worker(
            self.store,
            lambda job: FlakyProvider(),
            retry_delays=(30.0, 120.0, 600.0),
        )
        self.make_job()
        job = worker.process_job("job_test")
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.next_attempt_at)
        self.assertFalse(job.cancel_requested)


if __name__ == "__main__":
    unittest.main()
