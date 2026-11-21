import json
import os
import struct
import subprocess
import tempfile
import time
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from karaoke.background import (
    BackgroundBuild,
    BackgroundRef,
    RenderResult,
    _frames,
    _motion_identity,
    background_status,
    build_background,
    cleanup_pictures,
    profile_fingerprint,
    render_videos,
)
from karaoke.living import MOTION_CACHE_VERSION, visual_plan_hash
from karaoke.profiles import get_profile
from karaoke.project import prepared_from_project, project_from_fields, public_project


def small_profile():
    return replace(
        get_profile("landscape_1080p"),
        width=320,
        height=180,
        fps=10,
        strip_rect=(0, 120, 320, 60),
        lyric_font_size=20,
        max_lyric_width=280,
        lyric_row_gap=4,
        cover_title_size=24,
        cover_artist_size=16,
        cover_line_size=12,
        cover_max_width=280,
    )


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        count = int(seconds * rate)
        handle.writeframes(b"".join(struct.pack("<h", 0) for _ in range(count)))
    return path


def make_image(path: Path, size=(320, 180), color=(30, 60, 90)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,nb_frames,r_frame_rate,pix_fmt:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class FrameAndIdentityTests(unittest.TestCase):
    def test_frames_normalize_cover_and_song(self):
        cover, image, song, total = _frames(0.0, 8.0, 240.0, 30)
        self.assertEqual((cover, image, song, total), (0, 240, 7200, 7200))

        cover, _image, song, total = _frames(0.01, 8.0, 240.0, 30)
        self.assertEqual((cover, song, total), (0, 7200, 7200))

        cover, _image, song, total = _frames(5.0, 8.0, 240.01, 30)
        self.assertEqual((cover, song, total), (150, 7201, 7351))

    def test_visual_hash_ignores_audio_and_legacy_loop_duration(self):
        first = {
            "items": [
                {
                    "effect": "rain",
                    "params": {"intensity": 0.5, "audio": False, "loop_duration": 8},
                }
            ]
        }
        second = {
            "items": [
                {
                    "effect": "rain",
                    "params": {"intensity": 0.5, "audio": True, "loop_duration": 16},
                }
            ]
        }
        self.assertEqual(visual_plan_hash(first), visual_plan_hash(second))

    def test_motion_identity_ignores_audio_but_changes_with_profile(self):
        plan = {"items": [{"effect": "rain", "params": {"intensity": 0.5, "audio": False}}]}
        other = {"items": [{"effect": "rain", "params": {"intensity": 0.5, "audio": True}}]}
        profile = small_profile()
        first = _motion_identity(plan, "a" * 64, 80, profile)
        second = _motion_identity(other, "a" * 64, 80, profile)
        self.assertEqual(first, second)

        wider = replace(profile, width=640, height=360)
        self.assertNotEqual(first, _motion_identity(plan, "a" * 64, 80, wider))
        self.assertEqual(MOTION_CACHE_VERSION, 32)

    def test_profile_fingerprint_includes_cover_typography(self):
        profile = small_profile()
        first = profile_fingerprint(profile)
        second = profile_fingerprint(replace(profile, cover_title_size=32))
        self.assertNotEqual(first, second)


class BackgroundBuildTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.image = make_image(self.root / "bg.png")
        self.audio = make_wav(self.root / "audio.wav")
        self.profile = small_profile()
        self.prepared = {
            "title": "Bài",
            "artist": "Tác giả",
            "cover_line": "Lời",
            "cover_image": str(self.image),
            "background_images": [str(self.image)],
            "background_effects": [],
            "original_audio": str(self.audio),
            "instrumental_audio": str(self.audio),
            "cover_duration": 0.2,
            "image_duration": 0.5,
            "output_profile": "landscape_1080p",
        }
        self.profile_patch = patch("karaoke.background.get_profile", return_value=self.profile)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.folder.cleanup()

    def test_static_background_is_frame_exact_silent_and_reused(self):
        first = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertTrue(first.path.is_file())
        self.assertEqual(first.total_frames, 12)
        self.assertEqual(first.cover_frames, 2)
        self.assertEqual(first.segments[0]["kind"], "static")

        meta = probe(first.path)
        videos = [stream for stream in meta["streams"] if stream["codec_type"] == "video"]
        audios = [stream for stream in meta["streams"] if stream["codec_type"] == "audio"]
        self.assertEqual(videos[0]["nb_frames"], "12")
        self.assertEqual(videos[0]["pix_fmt"], "yuv420p")
        self.assertEqual(audios, [])

        second = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.hash, second.hash)

    def test_static_background_uses_concat_copy_and_pins_segments(self):
        segments_root = self.root / "segments"
        logs: list[str] = []
        with patch("karaoke.background.SEGMENT_CACHE_ROOT", segments_root), patch(
            "karaoke.background._assemble_background_filter"
        ) as fallback:
            build = build_background(
                self.prepared,
                output_dir=self.root,
                profile=self.profile,
                log=logs.append,
            )
        fallback.assert_not_called()
        self.assertTrue(any(line.startswith("Hiệu ứng:") for line in logs), logs)
        self.assertEqual(build.total_frames, 12)
        segment_files = list(segments_root.glob("static-*.mp4"))
        self.assertTrue(segment_files)
        for path in segment_files:
            self.assertTrue(path.with_name(f".{path.name}.pin").is_file())

    def test_motion_segments_concat_without_reencode(self):
        import hashlib

        image_hash = "sha256:" + hashlib.sha256(self.image.read_bytes()).hexdigest()
        plan = {
            "version": 1,
            "image_hash": image_hash,
            "profile_id": "landscape_1080p",
            "fit_transform": {
                "mode": "contain",
                "source_width": 320,
                "source_height": 180,
                "frame_width": 320,
                "frame_height": 180,
                "scale": 1.0,
                "offset_x": 0.0,
                "offset_y": 0.0,
            },
            "regions": [
                {
                    "id": "outdoor",
                    "polygon": [
                        {"x": 0.0, "y": 0.0},
                        {"x": 1.0, "y": 0.0},
                        {"x": 1.0, "y": 1.0},
                        {"x": 0.0, "y": 1.0},
                    ],
                }
            ],
            "items": [
                {
                    "effect": "rain",
                    "target_ids": [],
                    "params": {"intensity": 0.5, "audio": False},
                }
            ],
        }
        prepared = dict(self.prepared)
        prepared["image_duration"] = 0.5
        prepared["background_effects"] = [
            {
                "image_path": str(self.image),
                "image_hash": image_hash,
                "effect_plan": plan,
            }
        ]
        with patch("karaoke.living.CACHE_ROOT", self.root / "living"), patch(
            "karaoke.background.SEGMENT_CACHE_ROOT", self.root / "segments"
        ), patch("karaoke.background._assemble_background_filter") as fallback:
            build = build_background(
                prepared,
                output_dir=self.root,
                profile=self.profile,
                log=lambda _message: None,
            )
        fallback.assert_not_called()
        self.assertEqual(build.segments[0]["kind"], "motion")
        meta = probe(build.path)
        video = next(stream for stream in meta["streams"] if stream["codec_type"] == "video")
        self.assertEqual(video["nb_frames"], "12")

    def test_picture_is_reused_for_unchanged_lines(self):
        from karaoke import background as background_module

        lines = [
            {
                "text": "xin chào",
                "start": 0.1,
                "end": 0.8,
                "words": [
                    {"text": "xin", "start": 0.1, "end": 0.4},
                    {"text": "chào", "start": 0.4, "end": 0.8},
                ],
            }
        ]
        with patch(
            "karaoke.background.render_picture",
            wraps=background_module.render_picture,
        ) as fake:
            render_videos(
                lines=lines,
                title="Bài",
                artist="Tác giả",
                cover_line="Lời",
                cover_photo=self.image,
                background_images=[self.image],
                original_audio=self.audio,
                instrumental_audio=self.audio,
                output_dir=self.root,
                vocal_name="vocal-a.mp4",
                instrumental_name="inst-a.mp4",
                cover_duration=0.2,
                image_duration=0.5,
                background_effects=[],
                output_profile="landscape_1080p",
                log=lambda _message: None,
            )
            render_videos(
                lines=lines,
                title="Bài",
                artist="Tác giả",
                cover_line="Lời",
                cover_photo=self.image,
                background_images=[self.image],
                original_audio=self.audio,
                instrumental_audio=self.audio,
                output_dir=self.root,
                vocal_name="vocal-b.mp4",
                instrumental_name="inst-b.mp4",
                cover_duration=0.2,
                image_duration=0.5,
                background_effects=[],
                output_profile="landscape_1080p",
                log=lambda _message: None,
            )
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(
            len(list((self.root / ".karaoke-media" / "pictures").glob("picture-*.mp4"))),
            1,
        )

    def test_validate_background_result_is_cached(self):
        build = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        from karaoke.background import _validate_background

        valid, _reason = _validate_background(
            build.path,
            width=self.profile.width,
            height=self.profile.height,
            fps=self.profile.fps,
            total_frames=build.total_frames,
        )
        self.assertTrue(valid)
        with patch("karaoke.background._probe_video") as probe_mock:
            valid, _reason = _validate_background(
                build.path,
                width=self.profile.width,
                height=self.profile.height,
                fps=self.profile.fps,
                total_frames=build.total_frames,
            )
        self.assertTrue(valid)
        probe_mock.assert_not_called()

    def test_cleanup_pictures_keeps_current_and_recent(self):
        root = self.root / ".karaoke-media" / "pictures"
        root.mkdir(parents=True)
        old = root / "picture-old.mp4"
        current = root / "picture-current.mp4"
        old.write_bytes(b"old")
        current.write_bytes(b"new")
        older = time.time() - 10 * 86400
        os.utime(old, (older, older))
        deleted = cleanup_pictures(self.root, keep=[current], max_files=1, min_age_days=0)
        self.assertTrue(current.is_file())
        self.assertFalse(old.is_file())
        self.assertEqual(deleted, [old.name])

    def test_invalid_clip_workers_env_falls_back(self):
        second = make_image(self.root / "bg2.png", color=(200, 10, 10))
        prepared = dict(self.prepared)
        prepared["background_images"] = [str(self.image), str(second)]
        prepared["image_duration"] = 0.5
        logs: list[str] = []
        with patch.dict(os.environ, {"KARAOKE_CLIP_WORKERS": "not-a-number"}), patch(
            "karaoke.background.SEGMENT_CACHE_ROOT", self.root / "segments"
        ):
            build = build_background(
                prepared,
                output_dir=self.root,
                profile=self.profile,
                log=logs.append,
            )
        self.assertTrue(build.path.is_file())
        self.assertTrue(any("không hợp lệ" in line for line in logs), logs)

    def test_force_rebuilds_and_changed_image_changes_hash(self):
        first = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        forced = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            force=True,
            log=lambda _message: None,
        )
        self.assertEqual(first.path, forced.path)

        make_image(self.image, (320, 180))
        Image.new("RGB", (320, 180), (200, 10, 10)).save(self.image)
        changed = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertNotEqual(first.hash, changed.hash)
        self.assertNotEqual(first.path, changed.path)

    def test_render_videos_returns_result_and_two_formats(self):
        lines = [
            {
                "text": "xin chào",
                "start": 0.1,
                "end": 0.8,
                "words": [
                    {"text": "xin", "start": 0.1, "end": 0.4},
                    {"text": "chào", "start": 0.4, "end": 0.8},
                ],
            }
        ]
        with patch("karaoke.background.get_profile", return_value=self.profile):
            events: list[float] = []
            result = render_videos(
                lines=lines,
                title="Bài",
                artist="Tác giả",
                cover_line="Lời",
                cover_photo=self.image,
                background_images=[self.image],
                original_audio=self.audio,
                instrumental_audio=self.audio,
                output_dir=self.root,
                vocal_name="vocal.mp4",
                instrumental_name="inst.mp4",
                cover_duration=0.2,
                image_duration=0.5,
                background_effects=[],
                output_profile="landscape_1080p",
                progress=lambda done, total: events.append(float(done) / max(1.0, float(total))),
                log=lambda _message: None,
            )
        self.assertAlmostEqual(events[-1], 1.0)
        self.assertIsInstance(result, RenderResult)
        self.assertTrue(result.vocal.is_file())
        self.assertTrue(result.instrumental.is_file())
        self.assertTrue(result.background.path.is_file())
        self.assertEqual(list(result), [result.vocal, result.instrumental])
        self.assertEqual(result.background.total_frames, 12)

        for path in (result.vocal, result.instrumental):
            meta = probe(path)
            kinds = {stream["codec_type"] for stream in meta["streams"]}
            self.assertEqual(kinds, {"video", "audio"})
            video = next(stream for stream in meta["streams"] if stream["codec_type"] == "video")
            self.assertEqual(video["nb_frames"], "12")

    def test_two_threads_build_same_background_without_corruption(self):
        def run():
            return build_background(
                self.prepared,
                output_dir=self.root,
                profile=self.profile,
                log=lambda _message: None,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _index: run(), range(2)))
        self.assertEqual(first.path, second.path)
        meta = probe(first.path)
        video = next(stream for stream in meta["streams"] if stream["codec_type"] == "video")
        self.assertEqual(video["nb_frames"], "12")

    def test_missing_cover_falls_back_to_first_background(self):
        prepared = dict(self.prepared)
        prepared["cover_image"] = ""
        build = build_background(
            prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertTrue(build.path.is_file())
        self.assertEqual(build.total_frames, 12)
        prepared["background_render"] = build.to_project()
        status = background_status(prepared, self.root)
        self.assertEqual(status["state"], "ready", status)

    def test_output_dir_change_copies_valid_background(self):
        from karaoke.access import clear_grants, grant_project_paths

        old_dir = self.root / "old"
        new_dir = self.root / "new"
        prepared = dict(self.prepared)
        prepared["output_dir"] = str(old_dir)
        try:
            old = build_background(
                prepared,
                output_dir=old_dir,
                profile=self.profile,
                log=lambda _message: None,
            )
            clear_grants()
            grant_project_paths({"background_render": old.to_project()})
            ref = BackgroundRef.from_project(old.to_project(), new_dir)
            logs: list[str] = []
            copied = build_background(
                prepared,
                output_dir=new_dir,
                profile=self.profile,
                background=ref,
                log=logs.append,
            )
        finally:
            clear_grants()
        self.assertTrue(any("copy" in line.lower() for line in logs), logs)
        self.assertEqual(copied.hash, old.hash)
        self.assertTrue(copied.path.is_file())
        self.assertTrue(old.path.is_file())
        self.assertNotEqual(copied.path, old.path)

    def test_duplicate_images_keep_full_cycle_order(self):
        second = make_image(self.root / "bg2.png", color=(200, 10, 10))
        prepared = dict(self.prepared)
        prepared["background_images"] = [str(self.image), str(second), str(self.image)]
        prepared["image_duration"] = 0.3
        first = build_background(
            prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertEqual(len(first.segments), 2)
        simpler = dict(prepared)
        simpler["background_images"] = [str(self.image), str(second)]
        second_build = build_background(
            simpler,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertNotEqual(first.hash, second_build.hash)
        self.assertEqual(first.total_frames, second_build.total_frames)
        self.assertTrue(first.path.is_file())

    def test_finish_render_record_propagates_background_metadata(self):
        from karaoke.server import _finish_render_record, _public_job

        build = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        prepared = dict(self.prepared)
        prepared["output_dir"] = str(self.root)
        prepared["background_render"] = build.to_project()
        record = {
            "id": "job",
            "status": "rendering",
            "created": 0,
            "log": [],
            "error": "",
            "outputs": None,
            "lines": [],
            "duration": 0,
            "prepared": {},
            "project": None,
        }
        _finish_render_record(record, prepared, [], {"vocal": "a", "instrumental": "b"})
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["prepared"]["background_render"]["hash"], build.hash)
        public = _public_job(record)
        self.assertEqual(public["background"]["state"], "ready")

    def test_render_job_persists_background_metadata(self):
        from karaoke.access import clear_grants, grant
        from karaoke.pipeline import render_job
        from karaoke.project import load_project, project_path_for

        out = self.root / "out"
        out.mkdir()
        for name in ("original.wav", "instrumental.wav", "cover.jpg"):
            (out / name).write_bytes(b"x")
        grant(self.root)
        build = BackgroundBuild(
            path=out / ".karaoke-media" / "backgrounds" / f"background-{'a' * 64}.mp4",
            hash="sha256:" + "a" * 64,
            profile_id="landscape_1080p",
            profile_fingerprint="b" * 64,
            width=320,
            height=180,
            fps=10,
            total_frames=12,
            cover_frames=2,
            song_frames=10,
            image_frames=5,
            segments=[{"index": 0, "kind": "static", "degraded": False}],
        )
        result = RenderResult(
            vocal=out / "vocal.mp4",
            instrumental=out / "inst.mp4",
            background=build,
        )
        prepared = {
            "title": "Bài",
            "artist": "Tác giả",
            "cover_line": "",
            "original_audio": str(out / "original.wav"),
            "instrumental_audio": str(out / "instrumental.wav"),
            "cover_image": str(out / "cover.jpg"),
            "background_images": [str(out / "cover.jpg")],
            "background_effects": [],
            "output_dir": str(out),
            "cover_duration": 5,
            "image_duration": 8,
        }
        try:
            with patch("karaoke.pipeline.render_videos", return_value=result):
                outputs = render_job(
                    prepared,
                    [{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.4}]}],
                    lambda _message: None,
                )
            project_path = project_path_for(out, "Bài")
            project, _source = load_project(project_path)
            self.assertEqual(project["background_render"]["hash"], build.hash)
            self.assertEqual(outputs["background"], str(build.path))
        finally:
            clear_grants()

    def test_render_videos_writes_requested_container_extensions(self):
        lines = [
            {
                "text": "một",
                "start": 0.1,
                "end": 0.5,
                "words": [{"text": "một", "start": 0.1, "end": 0.5}],
            }
        ]
        with patch("karaoke.background.get_profile", return_value=self.profile):
            result = render_videos(
                lines=lines,
                title="Bài",
                artist="Tác giả",
                cover_line="",
                cover_photo=self.image,
                background_images=[self.image],
                original_audio=self.audio,
                instrumental_audio=self.audio,
                output_dir=self.root,
                vocal_name="vocal.mov",
                instrumental_name="inst.mkv",
                cover_duration=0.2,
                image_duration=0.5,
                background_effects=[],
                output_profile="landscape_1080p",
                log=lambda _message: None,
            )
        self.assertEqual(result.vocal.suffix, ".mov")
        self.assertEqual(result.instrumental.suffix, ".mkv")
        self.assertTrue(result.vocal.is_file())
        self.assertTrue(result.instrumental.is_file())

    def test_background_ref_derives_current_and_source_paths(self):
        old_dir = self.root / "old"
        new_dir = self.root / "new"
        data = {
            "hash": "sha256:" + "a" * 64,
            "source_output_dir": str(old_dir),
            "profile_fingerprint": "b" * 64,
            "total_frames": 12,
            "cover_frames": 2,
            "song_frames": 10,
            "image_frames": 5,
            "width": 320,
            "height": 180,
            "fps": 10,
            "degraded": False,
        }
        ref = BackgroundRef.from_project(data, new_dir)
        self.assertEqual(ref.path, new_dir / ".karaoke-media/backgrounds" / f"background-{'a' * 64}.mp4")
        self.assertEqual(
            ref.source_output_dir,
            old_dir,
        )
        self.assertEqual(ref.total_frames, 12)

    def test_project_round_trip_keeps_background_metadata(self):
        build = BackgroundBuild(
            path=self.root
            / "out"
            / ".karaoke-media"
            / "backgrounds"
            / f"background-{'a' * 64}.mp4",
            hash="sha256:" + "a" * 64,
            profile_id="landscape_1080p",
            profile_fingerprint="b" * 64,
            width=320,
            height=180,
            fps=10,
            total_frames=12,
            cover_frames=2,
            song_frames=10,
            image_frames=5,
            segments=[{"index": 0, "kind": "static", "degraded": False}],
        )
        project = project_from_fields(
            {
                "title": "Bài",
                "artist": "Tác giả",
                "background_render": build.to_project(),
            }
        )
        self.assertIsNotNone(public_project(project)["background_render"])
        self.assertIsNotNone(prepared_from_project(project)["background_render"])
        self.assertNotIn("path", build.to_project())

        from karaoke.access import clear_grants, grant
        from karaoke.project import load_project, project_path_for, save_project

        grant(self.root)
        try:
            path = save_project(project_path_for(self.root, "Bài"), project)
            loaded, _source = load_project(path)
            self.assertEqual(loaded["background_render"]["hash"], build.hash)
        finally:
            clear_grants()


if __name__ == "__main__":
    unittest.main()
