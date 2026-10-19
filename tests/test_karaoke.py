import math
import os
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from karaoke.access import AccessError, audio_root, clear_grants, ensure_allowed, grant
from karaoke.align import (
    _segments_match_lines,
    lines_from_transcript,
    reconcile_lyrics_with_transcript,
    _vocal_rms,
    fit_lines_to_voice,
    loosen_against_next_line,
    repair_stretched_words,
)
from karaoke.lyrics import extract_sung_lines
from karaoke.mix import JobError as MixJobError
from karaoke.mix import mix_filtergraph, parse_mix_settings, render_mix
from karaoke.pipeline import (
    JobError,
    export_gaps,
    load_timing_file,
    normalize_export_format,
    open_project_file,
    render_job,
    save_timing_file,
    unique_export_names,
)
from karaoke.project import load_project, project_from_fields, project_path_for, save_project, sync_project_file
from karaoke.render import (
    COUNTDOWN,
    LONG_GAP,
    LYRIC_FONT_SIZE,
    MAX_LYRIC_WIDTH,
    _render_text_pair,
    _wrap_word_rows,
    cue_at,
)


class LyricsTests(unittest.TestCase):
    def test_skips_blank_lines_and_stage_directions(self):
        lyrics = "\n[Intro]\n[Tiếng trống]\n\nMười tám con người\n"
        self.assertEqual(extract_sung_lines(lyrics), ["Mười tám con người"])


class RepairTests(unittest.TestCase):
    def test_pulls_a_word_back_from_a_long_gap(self):
        words = [
            {"text": "TRỜI", "start": 121.30, "end": 122.28},
            {"text": "NAM!", "start": 191.46, "end": 192.68},
        ]
        repaired, moved = repair_stretched_words(words)
        self.assertEqual(moved, 1)
        self.assertLess(repaired[1]["start"], 123)
        self.assertGreater(repaired[1]["end"], repaired[1]["start"])

    def test_keeps_a_short_pause(self):
        words = [
            {"text": "vọng", "start": 10.0, "end": 10.4},
            {"text": "trời", "start": 10.9, "end": 11.4},
        ]
        repaired, moved = repair_stretched_words(words)
        self.assertEqual(moved, 0)
        self.assertEqual(repaired[1]["start"], 10.9)

    def test_loosens_a_line_after_pulling_the_last_word_back(self):
        words = [
            {"text": "BÌNH", "start": 118.56, "end": 118.84},
            {"text": "NAM!", "start": 191.46, "end": 192.68},
        ]
        repaired, moved = repair_stretched_words(words)
        self.assertEqual(moved, 1)
        lines = [
            {"text": "điệp khúc", "start": repaired[0]["start"], "end": repaired[-1]["end"], "words": repaired, "repaired": True},
            {"text": "sau", "start": 192.68, "end": 195.0, "words": [{"text": "sau", "start": 192.68, "end": 195.0}]},
        ]
        loosened = loosen_against_next_line(lines)
        self.assertLess(loosened[0]["words"][1]["start"], 130)
        self.assertGreaterEqual(loosened[0]["words"][0]["end"] - loosened[0]["words"][0]["start"], 0.42)
        self.assertLess(loosened[0]["end"], 192.68)


class CueTests(unittest.TestCase):
    def test_constants_match_the_typescript_preview(self):
        source = Path("web/src/lib/cue.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const LONG_GAP_SECONDS = {int(LONG_GAP)};", source)
        self.assertIn(f"export const COUNTDOWN_SECONDS = {int(COUNTDOWN)};", source)

    def test_countdown_only_after_a_long_gap(self):
        lines = [
            {"text": "đầu", "start": 46.0, "end": 50.0},
            {"text": "sau", "start": 120.0, "end": 124.0},
        ]
        self.assertEqual(cue_at(lines, 40.0)[0], "blank")
        mode, _line, number = cue_at(lines, 41.5)
        self.assertEqual(mode, "countdown")
        self.assertEqual(number, 5)
        self.assertEqual(cue_at(lines, 46.2)[0], "sing")
        self.assertEqual(cue_at(lines, 80.0)[0], "blank")
        self.assertEqual(cue_at(lines, 115.5)[0], "countdown")
        self.assertEqual(cue_at(lines, 120.1)[0], "sing")

    def test_shows_the_next_line_as_soon_as_the_previous_one_ends(self):
        lines = [
            {"text": "trước", "start": 86.0, "end": 89.72},
            {"text": "Tốt", "start": 90.02, "end": 92.9},
        ]
        mode, line, _number = cue_at(lines, 89.5)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "trước")
        mode, line, _number = cue_at(lines, 89.8)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "Tốt")

    def test_switches_as_soon_as_the_note_ends(self):
        lines = [
            {"text": "trước", "start": 48.0, "end": 52.24},
            {"text": "sau", "start": 54.06, "end": 57.0},
        ]
        mode, line, _number = cue_at(lines, 52.1)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "trước")
        mode, line, _number = cue_at(lines, 52.3)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "sau")


class VoiceFitTests(unittest.TestCase):
    def _tone(self, path: Path, spans: list[tuple[float, float]], seconds: float = 3.0) -> None:
        rate = 8000
        count = int(seconds * rate)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            frames = bytearray()
            for index in range(count):
                moment = index / rate
                loud = any(start <= moment < end for start, end in spans)
                sample = int(12000 * math.sin(2 * math.pi * 220 * moment)) if loud else 0
                frames += struct.pack("<h", sample)
            handle.writeframes(frames)

    def test_pulls_a_word_out_of_the_silence_after_the_phrase(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            self._tone(vocal, [(0.40, 1.30)])
            lines = [
                {
                    "text": "bình ngô đại cáo",
                    "start": 0.40,
                    "end": 2.60,
                    "words": [
                        {"text": "bình", "start": 0.40, "end": 2.05},
                        {"text": "ngô", "start": 2.05, "end": 2.20},
                        {"text": "đại", "start": 2.20, "end": 2.40},
                        {"text": "cáo", "start": 2.40, "end": 2.60},
                    ],
                }
            ]
            fitted = fit_lines_to_voice(lines, vocal)
            self.assertLess(fitted[0]["end"], 1.45)
            self.assertGreater(fitted[0]["words"][-1]["start"], 0.9)
            self.assertLess(fitted[0]["words"][0]["end"] - fitted[0]["words"][0]["start"], 0.5)

    def test_keeps_the_last_word_on_screen_while_the_note_is_held(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            self._tone(vocal, [(0.40, 1.30)])
            lines = [
                {
                    "text": "một hai",
                    "start": 0.40,
                    "end": 0.90,
                    "words": [
                        {"text": "một", "start": 0.40, "end": 0.70},
                        {"text": "hai", "start": 0.70, "end": 0.90},
                    ],
                }
            ]
            fitted = fit_lines_to_voice(lines, vocal)
            self.assertAlmostEqual(fitted[0]["words"][0]["start"], 0.40, delta=0.08)
            self.assertGreater(fitted[0]["end"], 1.15)
            self.assertLess(fitted[0]["end"], 1.45)

    def test_logs_when_the_phrase_is_too_quiet_to_move(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "quiet.wav"
            self._tone(vocal, [])
            notes: list[str] = []
            lines = [
                {"text": "một", "start": 0.4, "end": 0.8, "words": [{"text": "một", "start": 0.4, "end": 0.8}]},
                {"text": "hai", "start": 1.2, "end": 1.6, "words": [{"text": "hai", "start": 1.2, "end": 1.6}]},
            ]
            fitted = fit_lines_to_voice(lines, vocal, notes.append)
            self.assertEqual(fitted[0]["end"], 0.8)
            self.assertEqual(notes, ["Giữ nguyên 2 câu vì không thấy đoạn hát đủ lớn."])

    def test_measures_a_vocal_that_is_not_pcm16(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("cần ffmpeg để chuyển vocal")
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "narrow.wav"
            rate = 8000
            with wave.open(str(vocal), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(1)
                handle.setframerate(rate)
                frames = bytearray()
                for index in range(rate * 2):
                    moment = index / rate
                    frames.append(220 if 0.4 <= moment < 1.2 else 128)
                handle.writeframes(frames)
            levels, _hop = _vocal_rms(vocal)
            self.assertGreater(max(levels), 80)


class SegmentMatchTests(unittest.TestCase):
    def test_marks_a_line_when_the_word_count_differs(self):
        class Word:
            def __init__(self, word, start, end):
                self.word = word
                self.start = start
                self.end = end

        class Segment:
            def __init__(self, words):
                self.words = words
                self.text = " ".join(word.word for word in words)
                self.start = words[0].start
                self.end = words[-1].end

        segment = Segment([Word("mot", 1.0, 1.8)])
        matched = _segments_match_lines(["một hai"], [segment])
        self.assertIsNotNone(matched)
        self.assertTrue(matched[0]["uncertain"])
        self.assertEqual([word["text"] for word in matched[0]["words"]], ["một", "hai"])
        self.assertEqual(matched[0]["words"][0]["start"], 1.0)
        self.assertEqual(matched[0]["words"][1]["end"], 1.8)


class TranscriptLineTests(unittest.TestCase):
    def test_keeps_whisper_words_and_times_without_using_typed_lyrics(self):
        segments = [
            {
                "words": [
                    {"text": "Hà", "start": 54.0, "end": 54.4},
                    {"text": "Nội", "start": 54.4, "end": 54.9},
                ]
            }
        ]
        lines = lines_from_transcript(segments)
        self.assertEqual(lines[0]["text"], "Hà Nội")
        self.assertEqual(lines[0]["start"], 54.0)
        self.assertEqual(lines[0]["end"], 54.9)
        self.assertNotIn("uncertain", lines[0])
        self.assertNotIn("supplemented", lines[0])


class TranscriptReconcileTests(unittest.TestCase):
    def test_keeps_the_verse_at_the_heard_minute_after_a_long_intro(self):
        lyrics = ["Màn sương buông vương lối phố rêu phong"]
        early = [
            {"text": word, "start": 8 + index * 0.4, "end": 8.3 + index * 0.4}
            for index, word in enumerate("la la la".split())
        ]
        heard = "man suong buong vuong loi pho reu phong".split()
        verse = [
            {"text": word, "start": 2880 + index * 0.4, "end": 2880.35 + index * 0.4}
            for index, word in enumerate(heard)
        ]
        matched = reconcile_lyrics_with_transcript(lyrics, [{"words": early}, {"words": verse}])
        lyric = next(line for line in matched if not line.get("supplemented"))
        self.assertGreater(lyric["start"], 2800)
        self.assertEqual(lyric["words"][0]["text"], "Màn")
        self.assertTrue(all(line.get("supplemented") for line in matched if line["start"] < 100))

    def test_adds_a_heard_ending_the_lyrics_omitted(self):
        lyrics = ["Màn sương buông"]
        lead = [
            {"text": "màn", "start": 10, "end": 10.4},
            {"text": "sương", "start": 10.4, "end": 10.8},
            {"text": "buông", "start": 10.8, "end": 11.2},
        ]
        ending = [
            {"text": "hát", "start": 40, "end": 40.4},
            {"text": "bè", "start": 40.4, "end": 40.9},
            {"text": "cuối", "start": 40.9, "end": 41.4},
        ]
        matched = reconcile_lyrics_with_transcript(lyrics, [{"words": lead}, {"words": ending}])
        added = [line for line in matched if line.get("supplemented")]
        self.assertEqual([line["text"] for line in added], ["hát bè cuối"])
        self.assertGreater(added[0]["start"], 30)
        self.assertEqual(matched[0]["words"][0]["text"], "Màn")
        self.assertEqual(matched[0]["start"], 10)

    def test_puts_an_unmatched_lyric_just_before_the_next_heard_line(self):
        lyrics = ["câu chưa nghe thấy gì cả", "Màn sương buông"]
        lead = [
            {"text": "màn", "start": 2880, "end": 2880.4},
            {"text": "sương", "start": 2880.4, "end": 2880.8},
            {"text": "buông", "start": 2880.8, "end": 2881.2},
        ]
        matched = reconcile_lyrics_with_transcript(lyrics, [{"words": lead}])
        self.assertEqual(matched[0]["text"], lyrics[0])
        self.assertTrue(matched[0]["uncertain"])
        self.assertGreater(matched[0]["start"], 2870)
        self.assertLess(matched[0]["end"], matched[1]["start"])

    def test_repeated_lines_take_successive_hearings(self):
        lyrics = ["Màn sương buông", "Màn sương buông"]

        def phrase(start):
            return [
                {"text": "màn", "start": start, "end": start + 0.4},
                {"text": "sương", "start": start + 0.4, "end": start + 0.8},
                {"text": "buông", "start": start + 0.8, "end": start + 1.2},
            ]

        matched = reconcile_lyrics_with_transcript(lyrics, [{"words": phrase(100)}, {"words": phrase(200)}])
        sung = [line for line in matched if not line.get("supplemented")]
        self.assertEqual(sung[0]["start"], 100)
        self.assertEqual(sung[1]["start"], 200)

    def test_rejects_lyrics_that_match_nothing_that_was_heard(self):
        from karaoke.align import AlignError

        with self.assertRaises(AlignError):
            reconcile_lyrics_with_transcript(
                ["hoàn toàn khác biệt"],
                [{"words": [{"text": "một", "start": 1, "end": 1.4}, {"text": "hai", "start": 1.4, "end": 1.8}]}],
            )


class AccessTests(unittest.TestCase):
    def tearDown(self):
        clear_grants()

    def test_rejects_a_path_outside_the_audio_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inside = root / "song.wav"
            inside.write_bytes(b"")
            outside = Path(tempfile.mkdtemp()) / "other.wav"
            outside.write_bytes(b"")
            previous = os.environ.get("KARAOKE_AUDIO_ROOT")
            os.environ["KARAOKE_AUDIO_ROOT"] = str(root)
            try:
                self.assertEqual(audio_root(), root)
                self.assertEqual(ensure_allowed(inside), inside.resolve())
                with self.assertRaises(AccessError):
                    ensure_allowed(outside)
                grant(outside)
                self.assertEqual(ensure_allowed(outside), outside.resolve())
            finally:
                clear_grants()
                if previous is None:
                    os.environ.pop("KARAOKE_AUDIO_ROOT", None)
                else:
                    os.environ["KARAOKE_AUDIO_ROOT"] = previous

    def test_opening_a_project_grants_its_stored_image_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            images = Path(tempfile.mkdtemp())
            cover = images / "cover.jpg"
            cover.write_bytes(b"JPEG")
            bg = images / "bg.jpg"
            bg.write_bytes(b"JPEG")
            previous = os.environ.get("KARAOKE_AUDIO_ROOT")
            os.environ["KARAOKE_AUDIO_ROOT"] = str(root)
            try:
                grant(root)
                project = project_from_fields(
                    {
                        "title": "Ảnh",
                        "artist": "A",
                        "output_dir": str(root),
                        "cover_image": str(cover),
                        "background_images": [str(bg)],
                        "vocal_audio": "",
                    },
                    status="draft",
                )
                path = save_project(project_path_for(root, "Ảnh"), project)
                clear_grants()
                grant(path)  # browse grants the project file only
                with self.assertRaises(AccessError):
                    ensure_allowed(bg)
                with patch("karaoke.pipeline.probe_duration", return_value=10.0):
                    prepared, _lines, opened = open_project_file(str(path))
                self.assertEqual(Path(prepared["cover_image"]).resolve(), cover.resolve())
                self.assertEqual([Path(item).resolve() for item in prepared["background_images"]], [bg.resolve()])
                self.assertEqual(ensure_allowed(bg), bg.resolve())
                self.assertEqual(opened["background_images"][0], prepared["background_images"][0])
            finally:
                clear_grants()
                if previous is None:
                    os.environ.pop("KARAOKE_AUDIO_ROOT", None)
                else:
                    os.environ["KARAOKE_AUDIO_ROOT"] = previous
                shutil.rmtree(images, ignore_errors=True)


class TimingFileTests(unittest.TestCase):
    def test_saves_and_reloads_edited_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            try:
                path = root / "Hào Khí timing.json"
                save_timing_file(
                    path,
                    [
                        {
                            "text": "một hai",
                            "words": [
                                {"text": "một", "start": 1, "end": 1.2},
                                {"text": "hai", "start": 1.2, "end": 1.5},
                            ],
                        }
                    ],
                )
                loaded = load_timing_file(path)
                self.assertEqual(loaded[0]["text"], "một hai")
                self.assertEqual(loaded[0]["end"], 1.5)
            finally:
                clear_grants()

    def test_names_files_still_needed_before_export(self):
        gaps = export_gaps(
            {"original_audio": "", "instrumental_audio": "", "cover_image": "", "background_images": []}
        )
        self.assertEqual(gaps, ["audio gốc", "file instrumental", "ảnh bìa hoặc ảnh nền"])

    def test_render_stops_before_ffmpeg_when_files_are_missing(self):
        with self.assertRaises(JobError) as caught:
            render_job(
                {
                    "title": "Bài",
                    "artist": "",
                    "cover_line": "",
                    "original_audio": "",
                    "instrumental_audio": "",
                    "cover_image": "",
                    "background_images": [],
                    "output_dir": "/tmp",
                    "cover_duration": 0,
                    "image_duration": 8,
                },
                [{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.2}]}],
                lambda _message: None,
            )
        self.assertIn("audio gốc", str(caught.exception))
        self.assertIn("khai báo", str(caught.exception))

    def test_render_writes_the_opened_timing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            try:
                for name in ("gốc.wav", "đệm.wav"):
                    (root / name).write_bytes(b"not-audio")
                (root / "ảnh.jpg").write_bytes(b"not-image")
                opened = root / "V4" / "Hào Khí timing.json"
                opened.parent.mkdir()
                prepared = {
                    "title": "Tên khác",
                    "artist": "Ai đó",
                    "cover_line": "",
                    "original_audio": str(root / "gốc.wav"),
                    "instrumental_audio": str(root / "đệm.wav"),
                    "vocal_audio": str(root / "gốc.wav"),
                    "cover_image": str(root / "ảnh.jpg"),
                    "background_images": [],
                    "output_dir": str(root / "ra"),
                    "cover_duration": 0,
                    "image_duration": 8,
                    "timing_path": str(opened),
                }
                (root / "ra").mkdir()
                with patch("karaoke.pipeline.render_videos", return_value=(root / "a.mp4", root / "b.mp4")):
                    result = render_job(
                        prepared,
                        [{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.4}]}],
                        lambda _message: None,
                    )
                self.assertEqual(Path(result["timing"]), opened.resolve())
                self.assertTrue(opened.is_file())
                self.assertFalse((root / "ra" / "Tên khác timing.json").exists())
            finally:
                clear_grants()

    def test_rejects_a_timing_file_that_is_not_a_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(JobError):
                load_timing_file(path)


class ProjectFileTests(unittest.TestCase):
    def tearDown(self):
        clear_grants()

    def test_saves_and_reloads_a_project_with_embedded_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            path = project_path_for(root, "Hào Khí")
            project = project_from_fields(
                {
                    "title": "Hào Khí",
                    "artist": "Ai đó",
                    "vocal_audio": str(root / "v.wav"),
                    "output_dir": str(root),
                },
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.3}]}],
                status="aligned",
            )
            saved = save_project(path, project)
            loaded, loaded_path = load_project(saved)
            self.assertEqual(loaded_path, saved.resolve())
            self.assertEqual(loaded["title"], "Hào Khí")
            self.assertEqual(loaded["status"], "aligned")
            self.assertEqual(loaded["lines"][0]["text"], "một")

    def test_migrates_legacy_timing_json_on_open(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            vocal = root / "vocal.wav"
            vocal.write_bytes(b"")
            timing = root / "Hào Khí timing.json"
            timing.write_text(
                '[{"text":"một","words":[{"text":"một","start":0,"end":0.2}]}]',
                encoding="utf-8",
            )
            # open_project_file needs a real audio duration — mock probe
            with patch("karaoke.pipeline.probe_duration", return_value=12.0):
                prepared, lines, project = open_project_file(str(timing))
            self.assertEqual(lines[0]["text"], "một")
            self.assertTrue(Path(prepared["project_path"]).name.endswith(".karaoke.json"))
            self.assertTrue(Path(prepared["project_path"]).is_file())
            self.assertEqual(project["title"], "Hào Khí")

    def test_sync_project_file_writes_beside_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            prepared = {
                "title": "Bài",
                "artist": "A",
                "cover_line": "",
                "lyrics": "một",
                "original_audio": "",
                "vocal_audio": "",
                "instrumental_audio": "",
                "cover_image": "",
                "background_images": [],
                "output_dir": str(root),
                "cover_duration": 5,
                "image_duration": 8,
                "language": "vi",
                "model_name": "small",
                "device": "cpu",
            }
            path = sync_project_file(
                prepared,
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.2}]}],
                status="aligned",
            )
            self.assertEqual(path.name, "Bài.karaoke.json")
            self.assertEqual(prepared["project_path"], str(path))

    def test_sync_moves_project_when_title_or_output_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            other = root / "other"
            other.mkdir()
            grant(root)
            prepared = {
                "title": "Cũ",
                "artist": "A",
                "lyrics": "một",
                "output_dir": str(root),
                "cover_duration": 5,
                "image_duration": 8,
                "language": "vi",
                "model_name": "small",
                "device": "cpu",
                "original_audio": "",
                "vocal_audio": "",
                "instrumental_audio": "",
                "cover_image": "",
                "background_images": [],
            }
            old = sync_project_file(
                prepared,
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.2}]}],
                status="aligned",
            )
            self.assertTrue(old.is_file())
            prepared["title"] = "Mới"
            prepared["output_dir"] = str(other)
            new = sync_project_file(prepared, status="aligned")
            self.assertEqual(new.name, "Mới.karaoke.json")
            self.assertEqual(new.parent.resolve(), other.resolve())
            self.assertFalse(old.exists())
            loaded, _ = load_project(new)
            self.assertEqual(loaded["title"], "Mới")
            self.assertEqual(loaded["lines"][0]["text"], "một")

    def test_sync_keeps_exported_status_when_saving_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            prepared = {
                "title": "Xong",
                "artist": "A",
                "lyrics": "một",
                "output_dir": str(root),
                "cover_duration": 5,
                "image_duration": 8,
                "language": "vi",
                "model_name": "small",
                "device": "cpu",
                "original_audio": "",
                "vocal_audio": "",
                "instrumental_audio": "",
                "cover_image": "",
                "background_images": [],
            }
            path = sync_project_file(
                prepared,
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.2}]}],
                status="exported",
                outputs={"directory": str(root), "vocal": "a.mp4", "instrumental": "b.mp4"},
            )
            again = sync_project_file(
                prepared,
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.3}]}],
                status="aligned",
            )
            loaded, _ = load_project(again)
            self.assertEqual(path, again)
            self.assertEqual(loaded["status"], "exported")

    def test_session_style_sync_relocates_and_keeps_exported(self):
        """Khai báo → Lưu must use sync (status=None), not save_project on the old path."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            prepared = {
                "title": "Cũ",
                "artist": "A",
                "lyrics": "một",
                "output_dir": str(root),
                "cover_duration": 5,
                "image_duration": 8,
                "language": "vi",
                "model_name": "small",
                "device": "cpu",
                "original_audio": "",
                "vocal_audio": "",
                "instrumental_audio": "",
                "cover_image": "",
                "background_images": [],
            }
            old = sync_project_file(
                prepared,
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.2}]}],
                status="exported",
                outputs={"directory": str(root), "vocal": "a.mp4", "instrumental": "b.mp4"},
            )
            prepared["title"] = "Mới"
            prepared["project_path"] = str(old)
            new = sync_project_file(prepared, status=None)
            self.assertEqual(new.name, "Mới.karaoke.json")
            self.assertFalse(old.exists())
            loaded, _ = load_project(new)
            self.assertEqual(loaded["status"], "exported")
            self.assertEqual(loaded["title"], "Mới")
            self.assertEqual(loaded["outputs"]["vocal"], "a.mp4")

    def test_sync_refuses_to_overwrite_another_project_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            other = project_from_fields(
                {"title": "Other", "artist": "B", "output_dir": str(root), "lyrics": "hai"},
                lines=[{"text": "hai", "words": [{"text": "hai", "start": 0, "end": 0.2}]}],
                status="aligned",
            )
            save_project(project_path_for(root, "Other"), other)
            prepared = {
                "title": "Mine",
                "artist": "A",
                "lyrics": "một",
                "output_dir": str(root),
                "cover_duration": 5,
                "image_duration": 8,
                "language": "vi",
                "model_name": "small",
                "device": "cpu",
                "original_audio": "",
                "vocal_audio": "",
                "instrumental_audio": "",
                "cover_image": "",
                "background_images": [],
            }
            mine = sync_project_file(
                prepared,
                lines=[{"text": "một", "words": [{"text": "một", "start": 0, "end": 0.2}]}],
                status="aligned",
            )
            prepared["title"] = "Other"
            with self.assertRaises(JobError) as caught:
                sync_project_file(prepared, status="aligned")
            self.assertIn("Other.karaoke.json", str(caught.exception))
            self.assertTrue(mine.is_file())
            loaded, _ = load_project(project_path_for(root, "Other"))
            self.assertEqual(loaded["title"], "Other")
            self.assertEqual(loaded["lines"][0]["text"], "hai")


class ExportNamingTests(unittest.TestCase):
    def test_rejects_unknown_export_format(self):
        with self.assertRaises(JobError):
            normalize_export_format("avi")

    def test_unique_export_names_add_a_timestamp_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vocal, instrumental = unique_export_names(root, "Bài", "mp4")
            self.assertTrue(vocal.startswith("Bài (có giọng) "))
            self.assertTrue(vocal.endswith(".mp4"))
            self.assertTrue(instrumental.startswith("Bài (không lời) "))
            self.assertNotEqual(vocal, instrumental)
            (root / vocal).write_bytes(b"x")
            (root / instrumental).write_bytes(b"x")
            again_vocal, again_instrumental = unique_export_names(root, "Bài", "mp4")
            self.assertNotEqual(again_vocal, vocal)
            self.assertTrue(again_vocal.endswith(".mp4"))
            self.assertFalse((root / again_vocal).exists())
            self.assertFalse((root / again_instrumental).exists())


class LyricLayoutTests(unittest.TestCase):
    def test_uses_one_fixed_font_size_for_short_and_long_lines(self):
        short = {
            "text": "một hai",
            "start": 0,
            "end": 1,
            "words": [
                {"text": "một", "start": 0, "end": 0.4},
                {"text": "hai", "start": 0.4, "end": 1.0},
            ],
        }
        long_words = [{"text": f"chữ{index}", "start": index * 0.2, "end": index * 0.2 + 0.15} for index in range(18)]
        long = {
            "text": " ".join(word["text"] for word in long_words),
            "start": 0,
            "end": 4,
            "words": long_words,
        }
        short_layout = _render_text_pair(short)
        long_layout = _render_text_pair(long)
        self.assertEqual(len({box["height"] for box in short_layout["boxes"]}), 1)
        self.assertEqual(short_layout["boxes"][0]["height"], long_layout["boxes"][0]["height"])
        row_ys = {round(box["y"], 2) for box in long_layout["boxes"]}
        self.assertGreaterEqual(len(row_ys), 2)
        self.assertLessEqual(long_layout["white"].width, MAX_LYRIC_WIDTH + 8)
        self.assertEqual(LYRIC_FONT_SIZE, 56)

    def test_wrap_keeps_a_short_line_on_one_row(self):
        rows = _wrap_word_rows([40.0, 40.0, 40.0], 10.0, MAX_LYRIC_WIDTH)
        self.assertEqual(rows, [[0, 1, 2]])


def _tone(path: Path, frequency: float, seconds: float = 0.4) -> None:
    rate = 44100
    count = int(rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(count):
            sample = int(16000 * math.sin(2 * math.pi * frequency * index / rate))
            frames += struct.pack("<hh", sample, sample)
        handle.writeframes(frames)


class MixRestoreTests(unittest.TestCase):
    def test_accepts_default_band_settings(self):
        settings = parse_mix_settings({})
        self.assertEqual(settings["low_hz"], 120)
        self.assertEqual(settings["high_hz"], 8000)
        self.assertEqual(settings["high_volume"], 50)
        graph = mix_filtergraph(settings)
        self.assertIn("highpass=f=8000", graph)
        self.assertIn("lowpass=f=120", graph)
        self.assertIn("volume=0.5", graph)
        self.assertIn("amix=inputs=3:normalize=0:duration=longest", graph)

    def test_rejects_cutoff_and_volume_outside_range(self):
        with self.assertRaises(MixJobError):
            parse_mix_settings({"low_hz": 10, "high_hz": 8000})
        with self.assertRaises(MixJobError):
            parse_mix_settings({"low_hz": 120, "high_hz": 100})
        with self.assertRaises(MixJobError):
            parse_mix_settings({"high_volume": 140})

    def test_project_keeps_the_instrumental_stem(self):
        project = project_from_fields(
            {
                "title": "Bài",
                "artist": "Tác",
                "restore": {"instrumental_stem": "/tmp/song_(Instrumental).wav", "low_hz": 90, "high_volume": 40},
            }
        )
        self.assertEqual(project["restore"]["instrumental_stem"], "/tmp/song_(Instrumental).wav")
        self.assertEqual(project["restore"]["low_hz"], 90)
        self.assertEqual(project["restore"]["high_volume"], 40)
        self.assertEqual(project["restore"]["high_hz"], 8000)

    def test_render_mix_writes_a_wav(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is not installed")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "gốc.wav"
            instrumental = root / "inst.wav"
            _tone(original, 100)
            _tone(instrumental, 8000)
            destination = root / "nền.wav"
            render_mix(
                instrumental,
                original,
                destination,
                {"high_hz": 4000, "low_hz": 150, "high_volume": 50, "low_volume": 50},
            )
            self.assertTrue(destination.is_file())
            self.assertGreater(destination.stat().st_size, 44)


class SeparateStreamTests(unittest.TestCase):
    def test_stderr_flood_does_not_block_the_result_line(self):
        import subprocess
        import sys
        import threading

        from karaoke.separate_runner import _read_separation

        script = (
            "import sys\n"
            "sys.stderr.write('x' * 200000)\n"
            "sys.stderr.flush()\n"
            "print('PROGRESS 0.5', flush=True)\n"
            'print(\'RESULT {"vocal": "v", "instrumental": "i"}\', flush=True)\n'
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        holder: dict = {}

        def run() -> None:
            holder["value"] = _read_separation(process, None)

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(5)
        if worker.is_alive():
            process.kill()
            worker.join(2)
            self.fail("Reading stdout stalled while stderr filled the pipe.")
        code, result, stderr = holder["value"]
        self.assertEqual(code, 0)
        self.assertEqual(result, {"vocal": "v", "instrumental": "i"})
        self.assertGreater(len(stderr), 100000)


class WhisperOptionTests(unittest.TestCase):
    def test_large_is_allowed_and_retry_defaults_on(self):
        from karaoke.pipeline import whisper_options

        options = whisper_options({"model_name": "large"})
        self.assertEqual(options["model_name"], "large")
        self.assertIsNone(options["no_speech_threshold"])
        self.assertTrue(options["whisper_retry"])

    def test_rejects_an_unknown_model_and_a_bad_threshold(self):
        from karaoke.pipeline import whisper_options

        with self.assertRaises(JobError):
            whisper_options({"model_name": "huge"})
        with self.assertRaises(JobError):
            whisper_options({"no_speech_threshold": 1.4})

    def test_project_stores_the_align_options(self):
        project = project_from_fields(
            {"title": "Bài", "model_name": "large-v2", "whisper_retry": False, "no_speech_threshold": 0.2}
        )
        self.assertEqual(project["model_name"], "large-v2")
        self.assertFalse(project["whisper_retry"])
        self.assertEqual(project["no_speech_threshold"], 0.2)


if __name__ == "__main__":
    unittest.main()
