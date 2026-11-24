"""Match reference lyric lines to Gemini line-level transcript.

Deterministic second stage of Approach B:

* Gemini supplies line text + coarse start/end.
* Whisper word offsets are a second opinion for the text.
* DP allows a reference line to be skipped (unsung) instead of forcing a
  one-to-one match.
* Repeats are handled in a second pass and never become NLV.
"""

from __future__ import annotations

import math
from difflib import SequenceMatcher
from functools import lru_cache

from karaoke.align import _fold_token, _folded_similarity

SIM_MIN = 0.30
MATCH_BASE = 0.95
GAP_REF = -0.45
GAP_HEARD = -0.15
W_TEXT = 0.55
W_DURATION = 0.35
W_ORDER = 0.10
REPEAT_SIM = 0.70
NLV_SIM = 0.35
MAX_REPEATS = 4
DEFAULT_SYLLABLE_SECONDS = 0.35


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in (_fold_token(part) for part in str(text).split()) if token)


@lru_cache(maxsize=65536)
def _token_lcs(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    previous = [0.0] * (len(right) + 1)
    for token in left:
        current = [0.0] * (len(right) + 1)
        for index, other in enumerate(right, start=1):
            current[index] = max(
                previous[index],
                current[index - 1],
                previous[index - 1] + _folded_similarity(token, other),
            )
        previous = current
    return previous[-1] / max(len(left), len(right))


@lru_cache(maxsize=65536)
def sim_text(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    lcs = _token_lcs(left_tokens, right_tokens)
    char = SequenceMatcher(None, " ".join(left_tokens), " ".join(right_tokens)).ratio()
    return 0.8 * lcs + 0.2 * char


def whisper_text_in_window(words: list[dict], start: float, end: float) -> str:
    return " ".join(
        str(word.get("text") or "")
        for word in words
        if start <= (float(word["start"]) + float(word["end"])) / 2.0 <= end
    )


def _heard_text(line: dict) -> str:
    return " ".join(str(line.get("text") or line.get("heard_text") or "").split())


def _syllable_seconds(refs: list[str], heard: list[dict]) -> float:
    ratios: list[float] = []
    for line in heard:
        syllables = max(1, len(_heard_text(line).split()))
        duration = float(line.get("end", 0)) - float(line.get("start", 0))
        if duration > 0:
            ratios.append(duration / syllables)
    if len(ratios) < 3:
        return DEFAULT_SYLLABLE_SECONDS
    ratios.sort()
    return ratios[len(ratios) // 2]


def _duration_fit(ref: str, line: dict, syllable_seconds: float) -> float:
    expected = max(0.3, syllable_seconds * max(1, len(ref.split())))
    actual = max(0.2, float(line.get("end", 0)) - float(line.get("start", 0)))
    return 1.0 - min(1.0, abs(actual - expected) / max(actual, expected, 1e-3))


def _order_bonus(row_index: int, col_index: int, ref_count: int, heard_count: int) -> float:
    if ref_count <= 1 or heard_count <= 1:
        return 1.0
    expected = row_index / (ref_count - 1)
    actual = col_index / (heard_count - 1)
    return max(0.0, 1.0 - abs(expected - actual))


def match_lines(
    reference: list[str],
    heard: list[dict],
    whisper_words: list[dict] | None = None,
) -> dict:
    """Return ``matched``, ``repeat``, ``unsung`` and ``extra`` buckets."""
    reference = [str(line) for line in reference]
    heard = [
        {
            "text": _heard_text(line),
            "start": float(line.get("start", 0)),
            "end": float(line.get("end", 0)),
            "confidence": float(line.get("confidence") or 0.0),
        }
        for line in heard
        if _heard_text(line)
    ]
    whisper_words = whisper_words or []
    rows, cols = len(reference), len(heard)
    if not rows or not cols:
        return {
            "matched": [],
            "repeat": [],
            "unsung": [{"reference_index": index, "text": line} for index, line in enumerate(reference)],
            "extra": [
                {
                    "text": line["text"],
                    "start": line["start"],
                    "end": line["end"],
                    "kind": "extra",
                }
                for line in heard
            ],
        }

    syllable_seconds = _syllable_seconds(reference, heard)
    sim_matrix: list[list[float]] = []
    for row in range(rows):
        values: list[float] = []
        for line in heard:
            gemini_sim = sim_text(reference[row], line["text"])
            similarity = gemini_sim
            # Second opinion only rescues a weak Gemini line. Taking max()
            # unconditionally lets a shifted/hallucinated Whisper transcript
            # inflate wrong pairs and pull the DP into a bad block.
            if whisper_words and gemini_sim < 0.50:
                whisper_sim = sim_text(
                    reference[row],
                    whisper_text_in_window(
                        whisper_words, float(line["start"]), float(line["end"])
                    ),
                )
                similarity = max(gemini_sim, whisper_sim)
            values.append(similarity)
        sim_matrix.append(values)

    score = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    move = [[("skip_ref", 0) for _ in range(cols + 1)] for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        score[row][0] = score[row - 1][0] + GAP_REF
        move[row][0] = ("skip_ref", 0)
    for col in range(1, cols + 1):
        score[0][col] = score[0][col - 1] + GAP_HEARD
        move[0][col] = ("skip_heard", 0)

    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            line = heard[col - 1]
            similarity = sim_matrix[row - 1][col - 1]
            skip_ref_score = score[row - 1][col] + GAP_REF
            skip_heard_score = score[row][col - 1] + GAP_HEARD
            best_gap = max(skip_ref_score, skip_heard_score)
            best_move = (
                ("skip_ref", 0) if skip_ref_score >= skip_heard_score else ("skip_heard", 0)
            )
            if similarity < SIM_MIN:
                score[row][col] = best_gap
                move[row][col] = best_move
                continue
            raw = (
                W_TEXT * similarity
                + W_DURATION * _duration_fit(reference[row - 1], line, syllable_seconds)
                + W_ORDER * _order_bonus(row - 1, col - 1, rows, cols)
            )
            match_score = raw - MATCH_BASE
            # Tie prefers a gap: better to leave a line unsung than place it wrong.
            if match_score > best_gap + 1e-9:
                score[row][col] = match_score
                move[row][col] = ("match", 0)
            else:
                score[row][col] = best_gap
                move[row][col] = best_move

    pairs: list[tuple[int, int, float]] = []
    row, col = rows, cols
    while row > 0 and col > 0:
        kind, _ = move[row][col]
        if kind == "match":
            similarity = sim_matrix[row - 1][col - 1]
            line = heard[col - 1]
            raw = (
                W_TEXT * similarity
                + W_DURATION * _duration_fit(reference[row - 1], line, syllable_seconds)
                + W_ORDER * _order_bonus(row - 1, col - 1, rows, cols)
            )
            pairs.append((row - 1, col - 1, raw))
            row -= 1
            col -= 1
        elif kind == "skip_ref":
            row -= 1
        else:
            col -= 1
    pairs.reverse()

    used_ref = {row_index for row_index, _col, _score in pairs}
    used_heard = {col_index for _row, col_index, _score in pairs}
    matched = [
        {
            "reference_index": row_index,
            "text": reference[row_index],
            "heard_text": heard[col_index]["text"],
            "start": heard[col_index]["start"],
            "end": heard[col_index]["end"],
            "status": "matched",
            "confidence": _raw,
            "text_similarity": sim_matrix[row_index][col_index],
        }
        for row_index, col_index, _raw in pairs
    ]

    # Second pass: repeats. A chorus is similar to a reference line; it must be
    # labelled repeat, not left to the NLV rule.
    occurrences: dict[int, int] = {}
    for row_index, _col, _score in pairs:
        occurrences[row_index] = occurrences.get(row_index, 0) + 1
    repeat: list[dict] = []
    for col_index, line in enumerate(heard):
        if col_index in used_heard:
            continue
        best_row = max(
            range(rows),
            key=lambda index: sim_text(reference[index], line["text"]),
        )
        similarity = sim_text(reference[best_row], line["text"])
        if similarity < REPEAT_SIM:
            continue
        occurrence = occurrences.get(best_row, 0)
        if occurrence >= MAX_REPEATS:
            continue
        occurrences[best_row] = occurrence + 1
        repeat.append(
            {
                "reference_index": best_row,
                "occurrence": occurrence,
                "text": reference[best_row],
                "heard_text": line["text"],
                "start": line["start"],
                "end": line["end"],
                "status": "repeat",
                "confidence": similarity,
                "text_similarity": similarity,
            }
        )
        used_ref.add(best_row)
        used_heard.add(col_index)

    unsung = [
        {"reference_index": index, "text": line, "status": "unsung"}
        for index, line in enumerate(reference)
        if index not in used_ref
    ]
    extra: list[dict] = []
    for col_index, line in enumerate(heard):
        if col_index in used_heard:
            continue
        best = max(
            (sim_text(reference[index], line["text"]) for index in range(rows)),
            default=0.0,
        )
        duration = line["end"] - line["start"]
        kind = "nlv" if best < NLV_SIM and duration >= 1.2 else "extra"
        extra.append(
            {
                "text": line["text"],
                "start": line["start"],
                "end": line["end"],
                "status": "extra",
                "kind": kind,
                "text_similarity": best,
            }
        )

    return {
        "matched": matched,
        "repeat": repeat,
        "unsung": unsung,
        "extra": extra,
        "syllable_seconds": syllable_seconds,
        "similarity": sim_matrix,
    }
