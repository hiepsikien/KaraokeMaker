#!/usr/bin/env python3
"""Evaluate the Living Still scene director on the core eval images.

Reads ``docs/eval-manifest.json`` and the hand labels in
``docs/eval-ground-truth.json``, calls :func:`karaoke.director.analyze_scene`
once per image, and writes ``docs/eval-results.json``.

No image is copied into the repo.  Only paths and metadata are stored.
The Gemini API key is read by ``karaoke.director`` from the environment and is
never printed here.

Usage:
    .venv-karaoke/bin/python scripts/eval_living_still.py
    .venv-karaoke/bin/python scripts/eval_living_still.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from karaoke import director  # noqa: E402
from karaoke.director import DirectorError, analyze_scene  # noqa: E402

DEFAULT_MANIFEST = ROOT / "docs" / "eval-manifest.json"
DEFAULT_GROUND_TRUTH = ROOT / "docs" / "eval-ground-truth.json"
DEFAULT_OUTPUT = ROOT / "docs" / "eval-results.json"

# Per 1M tokens (USD). Thinking tokens are billed as output.
PRICES = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}
DEFAULT_PRICE_MODEL = "gemini-3.8-flash"

# Prompts chosen per image so the intent call has something concrete to extract.
PROMPTS = {
    "still-era02-le-cuoi.png": "khói bốc từ nồi nhẹ",
    "still-era02-qua-doi.png": "khói bốc từ nồi nhẹ",
    "disaster_flash_flood.png": "mưa nhẹ",
    "disaster_great_blizzard.png": "tuyết rơi nhẹ",
    "disaster_wildfire_lightning.png": "gió làm lửa lay nhẹ",
    "still-era06-song-cuon-bach-dang.png": "sóng nước nhẹ",
    "still-era03-dem-suong-nui-nua.png": "gió làm lửa lay nhẹ",
    "still-era06-hoi-nghi-dien-hong.png": "ánh sáng nhẹ, bụi bay",
    "still-era07-bat-song-moc-thanh.png": "mưa nhẹ",
    "hero-nguyen-trai.png": "gió làm áo bay nhẹ",
}


SYNONYMS = {
    "water": ("water", "liquid", "river", "sea", "ocean", "wave", "sóng", "nước"),
    "fire": ("fire", "flame", "burning", "hearth", "lửa"),
    "book": ("book", "scroll", "manuscript", "sách"),
    "face": ("face", "person", "head", "mặt"),
    "sky": ("sky", "cloud", "trời"),
    "light": ("light", "ray", "sun", "glow", "ánh sáng"),
    "mountain": ("mountain", "hill", "peak", "núi"),
    "ground": ("ground", "terrain", "floor", "valley", "đất"),
    "boat": ("boat", "ship", "canoe", "thuyền"),
    "flag": ("flag", "banner", "cờ"),
    "tree": ("tree", "forest", "wood", "cây"),
    "pot": ("pot", "bowl", "cauldron", "nồi"),
}


class UsageRecorder:
    """Record per-model token usage without changing product code.

    ``analyze_scene`` merges the intent and inventory usage.  Wrapping
    ``_post_generate`` keeps the split so the cost report can apply the
    flash-lite price to the text-only intent call and the flash price to the
    vision inventory call.
    """

    def __init__(self) -> None:
        self.by_model: dict[str, dict[str, int]] = {}
        self._original = None

    def __enter__(self):
        self._original = director._post_generate

        def wrapped(model, payload, api_key, *, timeout):
            data = self._original(model, payload, api_key, timeout=timeout)
            usage = director._usage_from(data)
            previous = self.by_model.get(model)
            if previous is None:
                self.by_model[model] = usage
            else:
                self.by_model[model] = {
                    key: int(previous.get(key) or 0) + int(usage.get(key) or 0)
                    for key in ("prompt", "output", "thoughts", "total")
                }
            return data

        director._post_generate = wrapped
        return self

    def __exit__(self, *_exc):
        if self._original is not None:
            director._post_generate = self._original
        return False


def add_usage(first: dict, second: dict) -> dict[str, int]:
    return {
        key: int(first.get(key) or 0) + int(second.get(key) or 0)
        for key in ("prompt", "output", "thoughts", "total")
    }


def zero_usage() -> dict[str, int]:
    return {"prompt": 0, "output": 0, "thoughts": 0, "total": 0}


def cost_usd(usage_by_model: dict[str, dict], fallback: dict, inventory_model: str) -> tuple[float, str]:
    """Return (USD, basis).  Cache hits have no token usage, so they cost zero."""
    combined = zero_usage()
    for usage in usage_by_model.values():
        combined = add_usage(combined, usage)
    fallback = fallback or zero_usage()

    if usage_by_model and combined["total"] == int(fallback.get("total") or 0):
        total = 0.0
        for model, usage in usage_by_model.items():
            price = PRICES.get(model, PRICES[DEFAULT_PRICE_MODEL])
            total += usage["prompt"] * price["input"] / 1_000_000
            total += (usage["output"] + usage["thoughts"]) * price["output"] / 1_000_000
        return total, "per-model usage recorded during analyze_scene"

    if int(fallback.get("total") or 0) <= 0:
        return 0.0, "no API usage (cache hit)"

    price = PRICES.get(inventory_model, PRICES[DEFAULT_PRICE_MODEL])
    total = fallback["prompt"] * price["input"] / 1_000_000
    total += (fallback["output"] + fallback["thoughts"]) * price["output"] / 1_000_000
    return total, f"all response usage priced as {inventory_model}"


def schema_checks(result: dict) -> dict[str, bool]:
    plan = result.get("effect_plan") if isinstance(result.get("effect_plan"), dict) else {}
    image_hash = result.get("image_hash")
    width = result.get("width")
    height = result.get("height")
    return {
        "image_hash": isinstance(image_hash, str)
        and image_hash.startswith("sha256:")
        and len(image_hash) > len("sha256:"),
        "dimensions": isinstance(width, int)
        and isinstance(height, int)
        and width > 0
        and height > 0,
        "effect_plan_items": isinstance(plan.get("items"), list),
        "regions": isinstance(result.get("regions"), list),
        "protect": isinstance(result.get("protect"), list),
    }


def label_hits(expected: list[str], objects: list[dict]) -> tuple[list[str], list[str]]:
    haystacks = [
        f"{item.get('label', '')} {item.get('id', '')}".casefold()
        for item in objects
        if isinstance(item, dict)
    ]
    found: list[str] = []
    missing: list[str] = []
    for label in expected:
        needle = label.casefold()
        if any(needle in haystack for haystack in haystacks):
            found.append(label)
        else:
            missing.append(label)
    return found, missing


def semantic_haystacks(result: dict) -> list[str]:
    """Search objects, regions, protect labels, and the scene sentence.

    Strict recall still only uses object labels/ids. Semantic recall accepts
    synonyms and region/protect/scene evidence (e.g. "liquid" for water,
    "scroll" for book, "face" in protect).
    """
    parts: list[str] = []
    for item in result.get("objects") or []:
        if isinstance(item, dict):
            parts.append(f"{item.get('label', '')} {item.get('id', '')}")
    for item in result.get("regions") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("id") or ""))
    for item in result.get("protect") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("label") or ""))
    if result.get("scene"):
        parts.append(str(result["scene"]))
    return [part.casefold() for part in parts if part]


def label_hits_semantic(expected: list[str], result: dict) -> tuple[list[str], list[str]]:
    haystacks = semantic_haystacks(result)
    found: list[str] = []
    missing: list[str] = []
    for label in expected:
        words = SYNONYMS.get(label.casefold(), (label.casefold(),))
        if any(word.casefold() in haystack for haystack in haystacks for word in words):
            found.append(label)
        else:
            missing.append(label)
    return found, missing


def evaluate_one(core_item: dict, ground_truth: dict, source_root: Path) -> dict:
    filename = str(core_item.get("file") or "")
    key = Path(filename).stem
    truth = ground_truth.get(key) if isinstance(ground_truth, dict) else None
    truth = truth if isinstance(truth, dict) else {}
    expected_labels = [str(item).casefold() for item in truth.get("labels") or []]
    expected_effects = [str(item).casefold() for item in truth.get("effects") or []]
    prompt = PROMPTS.get(filename, "mưa nhẹ")

    path = source_root / filename
    record: dict[str, Any] = {
        "file": filename,
        "key": key,
        "path": str(path),
        "prompt": prompt,
        "expected_labels": expected_labels,
        "expected_effects": expected_effects,
        "ok": False,
        "error": "",
        "elapsed_s": 0.0,
        "cached": None,
        "usage": zero_usage(),
        "usage_by_model": {},
        "cost_usd": 0.0,
        "cost_basis": "",
    }

    if not path.is_file():
        record["error"] = f"missing image: {path}"
        return record

    recorder = UsageRecorder()
    started = time.perf_counter()
    try:
        with recorder:
            result = analyze_scene(path, prompt, consent=True)
    except DirectorError as error:
        record["elapsed_s"] = round(time.perf_counter() - started, 3)
        record["error"] = str(error)
        record["usage_by_model"] = recorder.by_model
        return record
    except Exception as error:  # keep the run going for unexpected failures
        record["elapsed_s"] = round(time.perf_counter() - started, 3)
        record["error"] = f"{type(error).__name__}: {error}"
        record["usage_by_model"] = recorder.by_model
        return record

    elapsed = time.perf_counter() - started
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else zero_usage()
    objects = result.get("objects") if isinstance(result.get("objects"), list) else []
    plan = result.get("effect_plan") if isinstance(result.get("effect_plan"), dict) else {}
    plan_items = plan.get("items") if isinstance(plan.get("items"), list) else []
    effect_types = sorted(
        {
            str(item.get("effect"))
            for item in plan_items
            if isinstance(item, dict) and item.get("effect")
        }
    )
    found_labels, missing_labels = label_hits(expected_labels, objects)
    found_semantic, missing_semantic = label_hits_semantic(expected_labels, result)
    cost, basis = cost_usd(
        recorder.by_model,
        usage,
        str(result.get("model") or DEFAULT_PRICE_MODEL),
    )
    checks = schema_checks(result)

    record.update(
        {
            "ok": True,
            "elapsed_s": round(elapsed, 3),
            "cached": bool(result.get("cached")),
            "model": result.get("model"),
            "image_hash": result.get("image_hash"),
            "width": result.get("width"),
            "height": result.get("height"),
            "usage": usage,
            "usage_by_model": recorder.by_model,
            "cost_usd": round(cost, 6),
            "cost_basis": basis,
            "object_count": len(objects),
            "object_labels": [
                str(item.get("label") or "")
                for item in objects
                if isinstance(item, dict)
            ],
            "region_count": len(result.get("regions") or []),
            "protect_count": len(result.get("protect") or []),
            "effect_types": effect_types,
            "found_labels": found_labels,
            "missing_labels": missing_labels,
            "label_recall": round(len(found_labels) / len(expected_labels), 3)
            if expected_labels
            else 1.0,
            "found_labels_semantic": found_semantic,
            "missing_labels_semantic": missing_semantic,
            "label_recall_semantic": round(len(found_semantic) / len(expected_labels), 3)
            if expected_labels
            else 1.0,
            "effect_match": set(expected_effects).issubset(set(effect_types)),
            "schema": checks,
            "schema_pass": all(checks.values()),
        }
    )
    return record


def summarize(records: list[dict]) -> dict:
    successful = [item for item in records if item.get("ok")]
    failed = [item for item in records if not item.get("ok")]
    expected_total = sum(len(item.get("expected_labels") or []) for item in successful)
    found_total = sum(len(item.get("found_labels") or []) for item in successful)
    found_semantic_total = sum(len(item.get("found_labels_semantic") or []) for item in successful)
    schema_passed = sum(1 for item in successful if item.get("schema_pass"))
    effect_matched = sum(1 for item in successful if item.get("effect_match"))
    usage = zero_usage()
    for item in successful:
        usage = add_usage(usage, item.get("usage") or {})

    return {
        "images": len(records),
        "succeeded": len(successful),
        "failed": len(failed),
        "cached": sum(1 for item in successful if item.get("cached")),
        "schema_passed": schema_passed,
        "schema_pass_rate": round(schema_passed / len(successful), 3) if successful else 0.0,
        "label_recall_micro": round(found_total / expected_total, 3) if expected_total else 1.0,
        "label_recall_macro": round(
            sum(float(item.get("label_recall") or 0.0) for item in successful) / len(successful),
            3,
        )
        if successful
        else 0.0,
        "label_recall_semantic_micro": round(found_semantic_total / expected_total, 3)
        if expected_total
        else 1.0,
        "label_recall_semantic_macro": round(
            sum(float(item.get("label_recall_semantic") or 0.0) for item in successful)
            / len(successful),
            3,
        )
        if successful
        else 0.0,
        "effect_match": effect_matched,
        "effect_match_rate": round(effect_matched / len(successful), 3) if successful else 0.0,
        "elapsed_s": round(sum(float(item.get("elapsed_s") or 0.0) for item in records), 3),
        "usage": usage,
        "cost_usd": round(sum(float(item.get("cost_usd") or 0.0) for item in records), 6),
    }


def print_report(records: list[dict], summary: dict) -> None:
    header = f"{'image':38} {'ok':>3} {'cache':>5} {'obj':>4} {'recall':>7} {'effects':<18} {'schema':>6} {'s':>7} {'cost':>9}"
    print(header)
    print("-" * len(header))
    for item in records:
        name = str(item.get("key") or item.get("file") or "?")[:38]
        cached = "-" if item.get("cached") is None else ("yes" if item.get("cached") else "no")
        effects = ",".join(item.get("effect_types") or []) or "-"
        schema = "pass" if item.get("schema_pass") else "FAIL"
        recall = f"{float(item.get('label_recall') or 0.0):.2f}" if item.get("ok") else "-"
        print(
            f"{name:38} {('yes' if item.get('ok') else 'NO'):>3} {cached:>5} "
            f"{item.get('object_count', 0):>4} {recall:>7} {effects[:18]:<18} "
            f"{schema:>6} {float(item.get('elapsed_s') or 0.0):>7.2f} "
            f"${float(item.get('cost_usd') or 0.0):>8.5f}"
        )
        if not item.get("ok"):
            print(f"    error: {item.get('error')}")

    print()
    print("Summary")
    print(
        f"  images: {summary['images']}  succeeded: {summary['succeeded']}  "
        f"failed: {summary['failed']}  cached: {summary['cached']}"
    )
    print(
        f"  schema: {summary['schema_passed']}/{summary['succeeded']} pass "
        f"({summary['schema_pass_rate']:.1%})"
    )
    print(
        f"  label recall strict: micro {summary['label_recall_micro']:.3f} / "
        f"macro {summary['label_recall_macro']:.3f}"
    )
    print(
        f"  label recall semantic: micro {summary['label_recall_semantic_micro']:.3f} / "
        f"macro {summary['label_recall_semantic_macro']:.3f}"
    )
    print(
        f"  expected effect types present: {summary['effect_match']}/{summary['succeeded']} "
        f"({summary['effect_match_rate']:.1%})"
    )
    usage = summary["usage"]
    print(
        f"  tokens: prompt {usage['prompt']}  output {usage['output']}  "
        f"thinking {usage['thoughts']}  total {usage['total']}"
    )
    print(f"  elapsed: {summary['elapsed_s']:.2f}s")
    print(f"  estimated cost: ${summary['cost_usd']:.5f}")
    print(
        "  prices: 3.8-flash $0.75/$3.75 per 1M, "
        "3.1-flash-lite $0.25/$1.50 per 1M (thinking counted as output)"
    )
    missing = [item.get("missing_labels") for item in records if item.get("missing_labels")]
    if missing:
        print("  label misses:")
        for item in records:
            if item.get("missing_labels"):
                print(f"    {item.get('key')}: {', '.join(item['missing_labels'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0, help="evaluate only the first N core images")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    ground_truth_payload = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    ground_truth = ground_truth_payload.get("images") if isinstance(ground_truth_payload, dict) else {}
    ground_truth = ground_truth if isinstance(ground_truth, dict) else {}

    source_root = Path(str(manifest.get("source_root") or "")).expanduser()
    core = manifest.get("core") if isinstance(manifest.get("core"), list) else []
    if args.limit and args.limit > 0:
        core = core[: args.limit]

    records = [evaluate_one(item, ground_truth, source_root) for item in core]
    summary = summarize(records)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(args.manifest),
        "ground_truth": str(args.ground_truth),
        "source_root": str(source_root),
        "prices_per_million_usd": PRICES,
        "summary": summary,
        "images": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print_report(records, summary)
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
