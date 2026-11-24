"""Measure Approach B line starts against a curated project file.

The project's saved lines are the reference ("gold") timing: they were checked
and exported from the editor. The script maps each gold line to a lyric line by
text, then compares Approach B's matched start for the same lyric line.

Example:
    python scripts/eval_b_alignment.py --projects "Quả Cam Cờ Nghĩa,Bài Ca Khải Hoàn"
    python scripts/eval_b_alignment.py --projects "Văn Lang Trường Tồn" --details

Loads GEMINI_API_KEY from .env. Whisper runs on CPU; Gemini line results are
cached, so a second run is much faster.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
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


def find_project(root: Path, title: str) -> tuple[dict, Path] | None:
    for path_text in glob.glob(str(root / "**" / "*.karaoke.json"), recursive=True):
        path = Path(path_text)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("title") == title:
            return data, path
    return None


def map_texts(gold: list[str], refs: list[str]) -> dict[int, int]:
    """Map reference index -> gold index, skipping lines only one side has."""
    from karaoke.line_match import sim_text

    rows, cols = len(gold), len(refs)
    score = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    move = [[0] * (cols + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            similarity = sim_text(gold[row - 1], refs[col - 1])
            pair = score[row - 1][col - 1] + similarity if similarity >= 0.55 else -1.0
            best = max(pair, score[row - 1][col], score[row][col - 1])
            score[row][col] = best
            if pair >= best:
                move[row][col] = 1
            elif score[row - 1][col] >= score[row][col - 1]:
                move[row][col] = 2
            else:
                move[row][col] = 3
    mapping: dict[int, int] = {}
    row, col = rows, cols
    while row > 0 and col > 0:
        step = move[row][col]
        if step == 1:
            mapping[col - 1] = row - 1
            row -= 1
            col -= 1
        elif step == 2:
            row -= 1
        else:
            col -= 1
    return mapping


def metrics(diffs: list[float]) -> dict:
    if not diffs:
        return {"n": 0}
    ordered = sorted(diffs)
    p90 = ordered[max(0, int(len(ordered) * 0.9) - 1)]
    return {
        "n": len(diffs),
        "mae": round(sum(diffs) / len(diffs), 3),
        "p90": round(p90, 3),
        "max": round(max(diffs), 3),
        "le05": round(sum(1 for value in diffs if value <= 0.5) / len(diffs), 3),
        "le1": round(sum(1 for value in diffs if value <= 1.0) / len(diffs), 3),
    }


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.expanduser("~/Downloads/Audio/Karaoke"))
    parser.add_argument("--projects", default="")
    parser.add_argument("--model", default="medium")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--gemini-model", default="gemini-3.8-flash")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()

    from karaoke.approach_b import align_with_gemini_lines
    from karaoke.lyrics import extract_sung_lines

    root = Path(args.root).expanduser()
    titles = [item.strip() for item in args.projects.split(",") if item.strip()]
    if not titles:
        print("Cần --projects.")
        return 1

    for title in titles:
        found = find_project(root, title)
        if found is None:
            print(f"\n==== {title}: không thấy dự án ====")
            continue
        project, path = found
        vocal = Path(project["vocal_audio"])
        if not vocal.is_file():
            print(f"\n==== {title}: không thấy vocal ====")
            continue
        refs = extract_sung_lines(str(project.get("lyrics") or ""))
        gold_lines = list(project.get("lines") or [])
        mapping = map_texts([str(line.get("text") or "") for line in gold_lines], refs)

        print(f"\n==== {title} ====")
        print(f"project={path}")
        print(f"refs={len(refs)} gold={len(gold_lines)} mapped={len(mapping)}")

        started = time.time()
        lines = align_with_gemini_lines(
            vocal,
            str(project.get("lyrics") or ""),
            args.language,
            args.model,
            "cpu",
            print,
            gemini_model=args.gemini_model,
        )
        elapsed = time.time() - started

        matched = {int(line["reference_index"]): line for line in lines if line.get("status") == "matched" and line.get("reference_index") is not None}
        diffs: list[float] = []
        rows: list[tuple[int, float, float, str]] = []
        for ref_index, gold_index in sorted(mapping.items()):
            line = matched.get(ref_index)
            if line is None:
                if args.details:
                    rows.append((ref_index, float(gold_lines[gold_index]["start"]), -1.0, str(gold_lines[gold_index].get("text") or "")))
                continue
            diff = abs(float(line["start"]) - float(gold_lines[gold_index]["start"]))
            diffs.append(diff)
            rows.append((ref_index, float(gold_lines[gold_index]["start"]), float(line["start"]), str(gold_lines[gold_index].get("text") or "")))

        result = metrics(diffs)
        print(f"B lines={len(lines)} matched refs={len(matched)} compared={result.get('n', 0)}/{len(refs)} time={elapsed:.1f}s")
        print(f"B {result}")
        if args.details:
            for ref_index, gold_start, heard_start, text in rows:
                delta = "" if heard_start < 0 else f"{heard_start - gold_start:+.2f}"
                print(f"{ref_index:3d} gold {gold_start:7.2f} b {heard_start:7.2f} {delta:>7}  {text[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
