import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from karaoke.approach_b import (
    _needs_chunk,
    _region_has_missing_lyrics,
    align_with_gemini_lines,
)
from karaoke.errors import JobError
from karaoke.align import WhisperFailed
from karaoke.gemini_lines import GeminiLineError
from karaoke.pipeline import align_job, align_options, apply_env_overrides


def tone(path: Path, spans: list[tuple[float, float]], seconds: float = 7.0) -> None:
    rate = 8000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(int(seconds * rate)):
            moment = index / rate
            loud = any(start <= moment < end for start, end in spans)
            sample = int(12000 * math.sin(2 * math.pi * 220 * moment)) if loud else 0
            frames += struct.pack("<h", sample)
        handle.writeframes(frames)


def words(items):
    return [{"text": text, "start": start, "end": end} for text, start, end in items]


class ApproachBTests(unittest.TestCase):
    def test_line_boundary_is_recomputed_from_words(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0), (2.5, 4.0), (4.5, 6.0)])
            segments = [
                {"words": words([("Màn", 0.5, 0.9), ("sương", 0.9, 1.4), ("buông", 1.4, 2.0)])},
                {"words": words([("Anh", 4.5, 4.9), ("yêu", 4.9, 5.4), ("em", 5.4, 6.0)])},
            ]
            gemini = [
                {"text": "màn sương buông", "start": 0.1, "end": 2.5, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "hò ơi hò khoan", "start": 2.5, "end": 4.0, "status": "extra", "confidence": 0.8},
                {"text": "anh yêu em", "start": 3.8, "end": 6.4, "status": "matched", "confidence": 0.9, "reference_index": 1},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông\nAnh yêu em",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
            lyric = [line for line in lines if line.get("kind") != "nlv"]
            self.assertEqual([line["text"] for line in lyric], ["Màn sương buông", "Anh yêu em"])
            self.assertAlmostEqual(lyric[0]["start"], 0.5, delta=0.08)
            self.assertAlmostEqual(lyric[1]["start"], 4.5, delta=0.08)
            self.assertTrue(any(line.get("kind") == "nlv" for line in lines))

    def test_short_full_song_answer_triggers_chunked(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 1.0), (2.5, 3.0), (4.5, 5.0)])
            segments = [
                {
                    "words": words(
                        [("một", 0.5, 1.0), ("hai", 2.5, 3.0), ("ba", 4.5, 5.0)]
                    )
                }
            ]
            short = [
                {
                    "text": "một",
                    "start": 0.5,
                    "end": 1.0,
                    "status": "matched",
                    "confidence": 0.9,
                    "reference_index": 0,
                }
            ]
            full = [
                {"text": "một", "start": 0.5, "end": 1.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "hai", "start": 2.5, "end": 3.0, "status": "matched", "confidence": 0.9, "reference_index": 1},
                {"text": "ba", "start": 4.5, "end": 5.0, "status": "matched", "confidence": 0.9, "reference_index": 2},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=short
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=full
            ) as chunked:
                lines = align_with_gemini_lines(
                    vocal,
                    "một\nhai\nba",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        self.assertTrue(chunked.called)
        self.assertEqual([line["text"] for line in lines], ["một", "hai", "ba"])

    def test_low_line_ratio_with_high_word_coverage_does_not_chunk(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 1.5), (5.5, 6.5)])
            segments = [
                {
                    "words": words(
                        [("một", 0.5, 1.0), ("ba", 5.5, 6.0)]
                    )
                }
            ]
            gemini = [
                {"text": "một", "start": 0.5, "end": 1.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "ba", "start": 5.5, "end": 6.0, "status": "matched", "confidence": 0.9, "reference_index": 2},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=gemini
            ) as chunked:
                lines = align_with_gemini_lines(
                    vocal,
                    "một\nhai\nba",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        self.assertFalse(chunked.called)
        self.assertEqual([line["text"] for line in lines], ["một", "ba"])

    def test_whisper_load_falls_back_to_cpu(self):
        from types import SimpleNamespace

        from karaoke import align as align_module

        calls: list[str] = []
        fake_model = Mock()
        fake_model.transcribe.return_value = SimpleNamespace(segments=[])

        def loader(_name, device, _engine, _log):
            calls.append(device)
            if device != "cpu":
                raise RuntimeError("mps load failed")
            return fake_model, "stable"

        with patch.object(align_module, "load_whisper_model", side_effect=loader), patch.object(
            align_module, "_log_model_cache"
        ), patch.object(align_module, "_allow_mps_alignment"):
            result = align_module.transcribe_whisper(
                Path("vocal.wav"), "vi", "small", "mps", lambda message: None
            )
        self.assertEqual(result, [])
        self.assertEqual(calls, ["mps", "cpu"])

    def test_mlx_transcribe_failure_falls_back_to_stable_cpu(self):
        from types import SimpleNamespace

        from karaoke import align as align_module

        calls: list[tuple[str, str]] = []
        bad_model = Mock()
        bad_model.transcribe.side_effect = RuntimeError("mlx failed")
        good_model = Mock()
        good_model.transcribe.return_value = SimpleNamespace(segments=[])

        def loader(_name, device, engine, _log):
            calls.append((device, engine))
            if engine == "mlx":
                return bad_model, "mlx"
            if device != "cpu":
                raise RuntimeError("stable mps failed")
            return good_model, "stable"

        with patch.object(align_module, "load_whisper_model", side_effect=loader), patch.object(
            align_module, "_log_model_cache"
        ), patch.object(align_module, "_allow_mps_alignment"):
            result = align_module.transcribe_whisper(
                Path("vocal.wav"),
                "vi",
                "small",
                "mps",
                lambda message: None,
                engine="mlx",
            )
        self.assertEqual(result, [])
        self.assertEqual(
            calls, [("mps", "mlx"), ("mps", "stable"), ("cpu", "stable")]
        )

    def test_clip_for_whisper_rejects_fragmented_or_mostly_voiced(self):
        from karaoke.voiced import clip_for_whisper

        self.assertIsNone(
            clip_for_whisper([(i, i + 1.0) for i in range(0, 13)], 30.0)
        )
        self.assertIsNone(clip_for_whisper([(0.0, 29.0)], 30.0))
        clip = clip_for_whisper([(0.0, 5.0), (20.0, 25.0)], 30.0)
        self.assertEqual(clip, [0.0, 5.0, 20.0, 25.0])

    def test_needs_chunk_and_region_missing(self):
        words = [
            {"text": "a", "start": 1.0, "end": 1.5},
            {"text": "b", "start": 2.0, "end": 2.5},
            {"text": "c", "start": 3.0, "end": 3.5},
        ]
        self.assertTrue(_needs_chunk([], words, ["a", "b", "c"], 10.0))
        self.assertFalse(
            _needs_chunk([{"start": 0.0, "end": 9.0}], words, ["a", "b", "c"], 10.0)
        )
        self.assertTrue(
            _region_has_missing_lyrics(0.0, 5.0, words, [], [100.0] * 100, 0.1)
        )
        self.assertFalse(
            _region_has_missing_lyrics(
                0.0, 5.0, words, [{"start": 0.0, "end": 5.0}], [100.0] * 100, 0.1
            )
        )

    def test_gemini_failure_falls_back_to_whisper(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0)])
            segments = [{"words": words([("Màn", 0.5, 0.9), ("sương", 0.9, 1.4), ("buông", 1.4, 2.0)])}]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", side_effect=GeminiLineError("429")
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked",
                side_effect=GeminiLineError("429"),
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["text"], "Màn sương buông")
            self.assertAlmostEqual(lines[0]["start"], 0.5, delta=0.1)

    def test_garbage_extra_is_not_promoted_to_nlv(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0)])
            segments = [{"words": words([("Màn", 0.5, 0.9), ("sương", 0.9, 1.4), ("buông", 1.4, 2.0)])}]
            gemini = [
                {"text": "màn sương buông", "start": 0.5, "end": 2.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "H creepy iftsalay", "start": 2.5, "end": 3.9, "status": "extra", "confidence": 0.4, "kind": "nlv"},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=gemini
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        self.assertFalse(any(line.get("kind") == "nlv" for line in lines))
        self.assertEqual([line["text"] for line in lines], ["Màn sương buông"])

    def test_nlv_text_matches_the_saved_word_list(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0), (2.5, 4.0)])
            segments = [
                {
                    "words": words(
                        [
                            ("Màn", 0.5, 0.9),
                            ("sương", 0.9, 1.4),
                            ("buông", 1.4, 2.0),
                            ("hò", 2.5, 2.9),
                            ("ơi", 2.9, 3.9),
                        ]
                    )
                }
            ]
            gemini = [
                {"text": "màn sương buông", "start": 0.5, "end": 2.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "hò ơi hò khoan", "start": 2.5, "end": 3.9, "status": "extra", "confidence": 0.8, "kind": "nlv"},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=gemini
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        chant = [line for line in lines if line.get("kind") == "nlv"]
        self.assertEqual(len(chant), 1)
        self.assertEqual(chant[0]["text"], "hò ơi")
        self.assertEqual(chant[0]["text"], " ".join(word["text"] for word in chant[0]["words"]))

    def test_window_coverage_detects_missing_middle(self):
        from karaoke.approach_b import _window_coverage

        words = [
            {"text": "a", "start": 1.0, "end": 2.0},
            {"text": "b", "start": 50.0, "end": 51.0},
        ]
        self.assertAlmostEqual(_window_coverage(words, [{"start": 0.0, "end": 3.0}]), 0.5)

    def test_word_times_never_overlap_after_snap(self):
        from karaoke.approach_b import _words_for_line

        def snap(moment, *_args):
            return moment - 0.3 if moment >= 1.25 else moment

        heard = [
            {"text": "a", "start": 1.0, "end": 1.2},
            {"text": "b", "start": 1.3, "end": 1.5},
        ]
        with patch("karaoke.approach_b._snap_time", side_effect=snap):
            result = _words_for_line("a b", 0.5, 2.0, heard, [1.0] * 100, 0.03, 0.5)
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(result[1]["start"], result[0]["end"])


class AlignStrategyTests(unittest.TestCase):
    def test_align_options_validates_strategy_and_consent(self):
        options = align_options(
            {
                "align_strategy": "gemini_lines",
                "gemini_model": "gemini-3.8-flash",
                "google_audio_consent": "yes",
            }
        )
        self.assertEqual(options["align_strategy"], "gemini_lines")
        self.assertTrue(options["google_audio_consent"])

    def test_default_strategy_is_gemini_lines(self):
        from karaoke.project import empty_project

        self.assertEqual(align_options({})["align_strategy"], "gemini_lines")
        self.assertEqual(empty_project()["align_strategy"], "gemini_lines")

    def test_env_overrides_do_not_persist_in_project(self):
        from karaoke.project import project_from_fields

        with patch.dict(
            os.environ,
            {"KARAOKE_WHISPER_ENGINE": "stable", "KARAOKE_GEMINI_WORKERS": "1"},
        ):
            project = project_from_fields(
                {
                    "title": "t",
                    "artist": "a",
                    "whisper_engine": "mlx",
                    "gemini_chunk_workers": 3,
                }
            )
            self.assertEqual(project["whisper_engine"], "mlx")
            self.assertEqual(project["gemini_chunk_workers"], 3)
            runtime = apply_env_overrides(project)
            self.assertEqual(runtime["whisper_engine"], "stable")
            self.assertEqual(runtime["gemini_chunk_workers"], 1)

    def test_align_parallel_none_defaults_true(self):
        self.assertTrue(align_options({"align_parallel": None})["align_parallel"])

    def test_explicit_whisper_strategy_is_kept(self):
        self.assertEqual(align_options({"align_strategy": "whisper"})["align_strategy"], "whisper")

    def test_rejects_unknown_strategy(self):
        with self.assertRaises(JobError):
            align_options({"align_strategy": "magic"})

    def test_rejects_unknown_gemini_model_for_a_new_job(self):
        with self.assertRaises(JobError):
            align_options({"gemini_model": "gemini-old"})

    def test_old_project_model_is_sanitized_when_loaded(self):
        from karaoke.project import project_from_fields

        project = project_from_fields(
            {
                "title": "x",
                "artist": "y",
                "lyrics": "một",
                "align_strategy": "gemini_lines",
                "gemini_model": "gemini-old",
                "google_audio_consent": True,
                "audio_consent_receipt": {"model": "gemini-old", "vocal_hash": "sha256:x"},
            }
        )
        self.assertEqual(project["gemini_model"], "gemini-3.8-flash")
        self.assertTrue(project["google_audio_consent"])
        self.assertEqual(project["audio_consent_receipt"]["vocal_hash"], "sha256:x")

    def test_align_job_uses_gemini_when_consented(self):
        prepared = {
            "vocal_audio": "/tmp/vocal.wav",
            "lyrics": "một",
            "language": "vi",
            "model_name": "small",
            "device": "cpu",
            "align_strategy": "gemini_lines",
            "gemini_model": "gemini-3.8-flash",
            "google_audio_consent": True,
        }
        with patch("karaoke.pipeline.align_with_gemini_lines", return_value=[{"text": "B"}]) as gemini:
            self.assertEqual(align_job(prepared, lambda message: None), [{"text": "B"}])
            self.assertTrue(gemini.called)

    def test_align_job_falls_back_without_consent(self):
        prepared = {
            "vocal_audio": "/tmp/vocal.wav",
            "lyrics": "một",
            "language": "vi",
            "model_name": "small",
            "device": "cpu",
            "align_strategy": "gemini_lines",
            "gemini_model": "gemini-3.8-flash",
            "google_audio_consent": False,
        }
        with patch("karaoke.pipeline.align_with_gemini_lines") as gemini, patch(
            "karaoke.pipeline.align_lyrics", return_value=[{"text": "A"}]
        ) as whisper:
            self.assertEqual(align_job(prepared, lambda message: None), [{"text": "A"}])
            self.assertFalse(gemini.called)
            self.assertTrue(whisper.called)


    def test_whisper_failed_does_not_run_whisper_twice(self):
        prepared = {
            "vocal_audio": "/tmp/vocal.wav",
            "lyrics": "một",
            "language": "vi",
            "model_name": "small",
            "device": "cpu",
            "align_strategy": "gemini_lines",
            "gemini_model": "gemini-3.8-flash",
            "google_audio_consent": True,
        }
        with patch(
            "karaoke.pipeline.align_with_gemini_lines",
            side_effect=WhisperFailed("load failed"),
        ), patch("karaoke.pipeline.align_lyrics") as whisper:
            with self.assertRaises(JobError):
                align_job(prepared, lambda message: None)
            self.assertFalse(whisper.called)


if __name__ == "__main__":
    unittest.main()
