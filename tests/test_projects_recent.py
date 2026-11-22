import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from karaoke.project import recent_projects


class RecentProjectsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def write_project(
        self,
        name: str,
        *,
        title: str,
        artist: str = "",
        mtime: float = 1000.0,
        status: str = "draft",
        profile: str = "landscape_1080p",
        cover_image: str = "",
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "title": title,
                    "artist": artist,
                    "status": status,
                    "output_profile": profile,
                    "cover_image": cover_image,
                }
            ),
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))
        return path

    def test_dedupes_resolved_paths_and_skips_missing_files(self):
        path = self.write_project("one.karaoke.json", title="Một")
        same = Path(str(self.root / "." / path.name))
        missing = self.root / "missing.karaoke.json"

        items = recent_projects([path, same, missing])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Một")
        self.assertEqual(items[0]["path"], str(path.resolve()))

    def test_sorts_by_mtime_desc_and_caps(self):
        oldest = self.write_project("old.karaoke.json", title="Cũ", mtime=1000)
        middle = self.write_project("mid.karaoke.json", title="Giữa", mtime=2000)
        newest = self.write_project("new.karaoke.json", title="Mới", mtime=3000)

        items = recent_projects([oldest, middle, newest], limit=2)
        self.assertEqual([item["title"] for item in items], ["Mới", "Giữa"])

        all_items = recent_projects([middle, oldest, newest], limit=12)
        self.assertEqual(
            [item["title"] for item in all_items],
            ["Mới", "Giữa", "Cũ"],
        )

    def test_skips_broken_json_and_returns_compact_fields(self):
        good = self.write_project(
            "good.karaoke.json",
            title="Bài hay",
            artist="Tác giả",
            mtime=1234,
            status="exported",
            profile="portrait_1080x1920",
            cover_image="/tmp/cover.png",
        )
        broken = self.root / "broken.karaoke.json"
        broken.write_text("{not-json", encoding="utf-8")
        os.utime(broken, (5678, 5678))

        items = recent_projects([broken, good])

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["artist"], "Tác giả")
        self.assertEqual(item["status"], "exported")
        self.assertEqual(item["output_profile"], "portrait_1080x1920")
        self.assertEqual(item["cover_image"], "/tmp/cover.png")
        self.assertEqual(
            datetime.fromisoformat(item["updated"]).timestamp(), 1234
        )
        self.assertEqual(
            set(item), {"path", "title", "artist", "status", "updated", "output_profile", "cover_image"}
        )

    def test_falls_back_for_unknown_status_and_profile(self):
        path = self.write_project(
            "fallback.karaoke.json",
            title="Fallback",
            status="weird",
            profile="not-a-profile",
        )
        item = recent_projects([path])[0]
        self.assertEqual(item["status"], "draft")
        self.assertEqual(item["output_profile"], "landscape_1080p")

    def test_zero_limit_returns_empty(self):
        path = self.write_project("one.karaoke.json", title="Một")
        self.assertEqual(recent_projects([path], limit=0), [])


if __name__ == "__main__":
    unittest.main()
