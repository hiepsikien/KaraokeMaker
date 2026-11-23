"""Gate 0 for Approach B: how good are Gemini line timestamps?

Example:
    python scripts/gate0_gemini_lines.py --project "Quả Cam Cờ Nghĩa" --start 30 --duration 60
    python scripts/gate0_gemini_lines.py --project "Bài Ca Khải Hoàn" --whole

This script only calls Gemini line-level; it does not run Whisper or matching.
Load GEMINI_API_KEY from .env or the shell.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None


def load_env() -> None:
    if dotenv_values is None:
        return
    for key, value in dotenv_values(ROOT / ".env").items():
        if value is not None and not os.environ.get(key, "").strip():
            os.environ[key] = value


def find_project(root: Path, title: str) -> dict | None:
    for path_text in glob.glob(str(root / "**" / "*.karaoke.json"), recursive=True):
        path = Path(path_text)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("title") == title:
            return data
    return None


def overlap(line: dict, start: float, end: float) -> float:
    return max(0.0, min(float(line["end"]), end) - max(float(line["start"]), start))


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.expanduser("~/Downloads/Audio/Karaoke"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--start", type=float, default=None)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--whole", action="store_true")
    parser.add_argument("--context", action="store_true", help="Send reference lyrics.")
    parser.add_argument("--model", default="gemini-3.8-flash")
    args = parser.parse_args()

    project = find_project(Path(args.root).expanduser(), args.project)
    if not project:
        print("Không thấy dự án.")
        return 1
    vocal = Path(project["vocal_audio"])
    if not vocal.is_file():
        print("Không thấy vocal.")
        return 1
    gold = list(project.get("lines") or [])
    lyrics = str(project.get("lyrics") or "") if args.context else ""
    start = None if args.whole else float(args.start if args.start is not None else gold[0]["start"])
    end = None if args.whole else start + args.duration

    from karaoke.gemini_lines import transcribe_lines

    try:
        heard = transcribe_lines(
            vocal,
            lyrics,
            language="vi",
            model=args.model,
            start=start,
            end=end,
            log=print,
            use_cache=False,
        )
    except Exception as error:
        print(f"Gemini lỗi: {error}")
        return 1

    window_start = 0.0 if args.whole else start
    window_end = float("inf") if args.whole else end
    expected = [
        line
        for line in gold
        if overlap(line, window_start, window_end) > 0.2
    ]
    print(f"\nGemini lines: {len(heard)}; gold lines in window: {len(expected)}")
    print(f"{'#':>2} {'gold start':>10} {'gemini start':>12} {'delta':>7}  text")
    for index, line in enumerate(expected[:40]):
        candidates = [
            item
            for item in heard
            if overlap({"start": item["start"], "end": item["end"]}, line["start"], line["end"]) > 0.1
        ]
        if not candidates:
            print(f"{index:2d} {line['start']:10.2f} {'MISS':>12} {'':>7}  {line['text'][:40]}")
            continue
        closest = min(candidates, key=lambda item: abs(item["start"] - line["start"]))
        print(
            f"{index:2d} {line['start']:10.2f} {closest['start']:12.2f} "
            f"{closest['start'] - line['start']:7.2f}  {line['text'][:40]}"
        )
    print("\nGhi chú: so theo overlap thô; cần kiểm tra tay các câu lệch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
