import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from karaoke.publish.models import (
    JOB_FINALIZING,
    JOB_QUEUED,
    JOB_PUBLISHED,
    JOB_UPLOADING,
    Channel,
    PublishJob,
    can_transition,
    now_iso,
)
from karaoke.publish.store import PublishStore


class PublishStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.data_root = Path(self.folder.name) / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.env = patch.dict(os.environ, {"KARAOKE_DATA_DIR": str(self.data_root)})
        self.env.start()
        self.store = PublishStore()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def make_draft(self, **overrides):
        payload = {
            "project_path": "/tmp/Bài hát.karaoke.json",
            "project_title": "Bài hát",
            "platform": "youtube",
            "video_role": "vocal",
            "title": "🎤 Bài hát",
            "description": "🎶 Mô tả",
            "privacy": "public",
        }
        payload.update(overrides)
        return self.store.create_draft(payload)

    def test_draft_round_trip_and_file_modes(self):
        draft = self.make_draft()
        self.assertTrue(draft.id.startswith("draft_"))
        self.assertEqual(draft.revision, 1)
        self.assertEqual(draft.title, "🎤 Bài hát")

        path = self.store.path("drafts.json")
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        reopened = PublishStore(self.store.root)
        loaded = reopened.get_draft(draft.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.to_dict(), draft.to_dict())
        self.assertEqual(len(self.store.list_drafts(platform="youtube")), 1)
        self.assertEqual(len(self.store.list_drafts(platform="tiktok")), 0)
        self.assertEqual(len(self.store.list_drafts(video_role="instrumental")), 0)

        channel = Channel(
            id="youtube:UC123",
            channel_id="UC123",
            name="Kênh thử",
            scopes=["scope"],
            expires_at=now_iso(),
            connected_at=now_iso(),
        )
        self.store.upsert_channel(channel)
        self.store.save_token("UC123", {"access_token": "a", "refresh_token": "r"})
        self.assertEqual(stat.S_IMODE(self.store.path("channels.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.path("tokens.json").stat().st_mode), 0o600)
        self.assertEqual(self.store.get_channel("youtube").name, "Kênh thử")
        self.assertEqual(self.store.get_token("UC123")["refresh_token"], "r")
        self.assertTrue(self.store.delete_channel("youtube"))
        self.assertIsNone(self.store.get_token("UC123"))

    def test_atomic_write_keeps_previous_document(self):
        draft = self.make_draft()
        path = self.store.path("drafts.json")
        before = path.read_text(encoding="utf-8")

        with patch("karaoke.publish.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update_draft(draft.id, {"title": "Mới"})

        self.assertEqual(path.read_text(encoding="utf-8"), before)
        temps = [item.name for item in path.parent.iterdir() if item.name.endswith(".tmp")]
        self.assertEqual(temps, [])
        self.assertEqual(self.store.get_draft(draft.id).title, "🎤 Bài hát")

    def test_revision_conflict_returns_latest_draft(self):
        draft = self.make_draft()
        second = self.store.update_draft(draft.id, {"title": "Lần 2"})
        self.assertEqual(second.revision, 2)
        with self.assertRaises(Exception) as caught:
            self.store.update_draft(draft.id, {"title": "Tab cũ"}, expected_revision=1)
        from karaoke.publish.store import Conflict

        self.assertIsInstance(caught.exception, Conflict)
        latest = caught.exception.latest
        self.assertEqual(latest.revision, 2)
        self.assertEqual(latest.title, "Lần 2")

    def test_concurrent_autosaves_are_serialised(self):
        draft = self.make_draft()
        barrier = threading.Barrier(8)

        def save(index: int):
            barrier.wait()
            return self.store.update_draft(draft.id, {"title": f"Lần {index}"})

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(save, range(8)))
        self.assertEqual(sorted(item.revision for item in results), list(range(2, 10)))
        self.assertEqual(self.store.get_draft(draft.id).revision, 9)

    def test_duplicate_and_delete(self):
        draft = self.make_draft()
        self.store.mark_draft_published(
            draft.id,
            video_id="vid123",
            url="https://youtu.be/vid123",
        )
        duplicate = self.store.duplicate_draft(draft.id)
        self.assertNotEqual(duplicate.id, draft.id)
        self.assertEqual(duplicate.title, draft.title)
        self.assertEqual(duplicate.revision, 1)
        self.assertIsNone(duplicate.last_video_id)
        self.assertEqual(len(self.store.list_drafts()), 2)
        self.assertTrue(self.store.delete_draft(draft.id))
        self.assertFalse(self.store.delete_draft(draft.id))
        self.assertIsNone(self.store.get_draft(draft.id))

    def test_state_machine(self):
        self.assertTrue(can_transition("queued", "initializing"))
        self.assertFalse(can_transition("queued", "uploading"))
        self.assertTrue(can_transition("uploading", "finalizing"))
        self.assertTrue(can_transition("finalizing", "published"))
        self.assertTrue(can_transition("uploading", "queued"))
        self.assertTrue(can_transition("failed", "queued"))
        self.assertTrue(can_transition("cancel_requested", "cancelled"))
        self.assertFalse(can_transition("published", "queued"))

    def test_recovery_rules(self):
        base = {
            "draft_id": "draft_x",
            "video_path": "/tmp/video.mp4",
            "title": "Bài",
            "created": now_iso(),
        }
        resumable = PublishJob(
            id="job_resume",
            status=JOB_UPLOADING,
            step="upload",
            upload_url="https://upload.example/secret-session",
            bytes_uploaded=123,
            **base,
        )
        no_url = PublishJob(
            id="job_no_url",
            status=JOB_UPLOADING,
            step="upload",
            **base,
        )
        finalizing = PublishJob(
            id="job_playlist",
            status=JOB_FINALIZING,
            step="upload",
            platform_video_id="vid123",
            playlist_id="PL1",
            **base,
        )
        published = PublishJob(
            id="job_done",
            status=JOB_PUBLISHED,
            step="done",
            platform_video_id="vid999",
            **base,
        )
        for job in (resumable, no_url, finalizing, published):
            self.store.add_job(job)

        active = self.store.recover_jobs()
        ids = {job.id for job in active}
        self.assertIn("job_resume", ids)
        self.assertIn("job_no_url", ids)
        self.assertIn("job_playlist", ids)
        self.assertNotIn("job_done", ids)

        resumed = self.store.get_job("job_resume")
        self.assertEqual(resumed.status, JOB_UPLOADING)
        self.assertTrue(resumed.upload_url)

        reset = self.store.get_job("job_no_url")
        self.assertEqual(reset.status, JOB_QUEUED)
        self.assertTrue(any("hết hiệu lực" in line for line in reset.log))

        playlist = self.store.get_job("job_playlist")
        self.assertEqual(playlist.status, JOB_QUEUED)
        self.assertEqual(playlist.step, "playlist")

    def test_job_snapshot_and_jobs_due(self):
        first = self.make_draft()
        job = PublishJob(
            id="job_1",
            draft_id=first.id,
            project_path=first.project_path,
            status=JOB_QUEUED,
            title=first.title,
            video_path="/tmp/old.mp4",
            created=now_iso(),
        )
        self.store.add_job(job)
        self.store.update_draft(first.id, {"title": "Đã sửa sau khi bấm đăng"})
        self.assertEqual(self.store.get_job("job_1").title, "🎤 Bài hát")
        self.assertEqual([item.id for item in self.store.jobs_due()], ["job_1"])
        self.assertEqual(len(self.store.list_jobs(project=first.project_path)), 1)
        self.assertEqual(len(self.store.list_jobs(project="/tmp/khác")), 0)


if __name__ == "__main__":
    unittest.main()
