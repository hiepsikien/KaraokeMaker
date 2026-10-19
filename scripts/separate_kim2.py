"""Headless Kim Vocal 2 separation for KaraokeMaker.

Run with the UVR virtualenv, from the repo root:

    .venv/bin/python scripts/separate_kim2.py --input song.wav --output-dir out

Writes ``{stem}_(Vocals).wav`` and ``{stem}_(Instrumental).wav``.
Progress lines are ``PROGRESS <fraction>``. The last stdout line is ``RESULT <json>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from onnx import load
from onnx2pytorch import ConvertModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib_v5.tfc_tdf_v3 import STFT  # noqa: E402

MODEL_PATH = ROOT / "models" / "MDX_Net_Models" / "Kim_Vocal_2.onnx"
MODEL_DATA_PATH = ROOT / "models" / "MDX_Net_Models" / "model_data" / "model_data.json"
HOP = 1024
SAMPLE_RATE = 44100


def _emit(kind: str, payload: str) -> None:
    print(f"{kind} {payload}", flush=True)


def _model_hash(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(-10000 * 1024, 2)
        return hashlib.md5(handle.read()).hexdigest()


def _model_settings(path: Path) -> dict:
    digest = _model_hash(path)
    table = json.loads(MODEL_DATA_PATH.read_text(encoding="utf-8"))
    settings = table.get(digest)
    if not isinstance(settings, dict) or "mdx_n_fft_scale_set" not in settings:
        raise RuntimeError(f"Không có thông số MDX cho model hash {digest}.")
    return settings


def _load_mix(path: Path) -> np.ndarray:
    import librosa

    mix, _sr = librosa.load(str(path), mono=False, sr=SAMPLE_RATE)
    if mix.ndim == 1:
        mix = np.asfortranarray([mix, mix])
    return mix.astype(np.float32, copy=False)


def _channels_last(audio: np.ndarray) -> np.ndarray:
    data = np.squeeze(np.asarray(audio, dtype=np.float32))
    if data.ndim == 1:
        data = np.stack([data, data], axis=0)
    if data.ndim != 2:
        raise RuntimeError(f"Audio không đúng dạng stereo: {data.shape}")
    if data.shape[0] == 2 and data.shape[1] != 2:
        data = data.T
    return np.ascontiguousarray(data)


def separate(
    audio_path: Path,
    output_dir: Path,
    *,
    overlap: float = 0.25,
    segment_size: int = 512,
    device_name: str = "mps",
) -> dict[str, str]:
    if not MODEL_PATH.is_file():
        raise RuntimeError(f"Không thấy model Kim Vocal 2: {MODEL_PATH}")
    if not audio_path.is_file():
        raise RuntimeError(f"Không thấy audio: {audio_path}")

    settings = _model_settings(MODEL_PATH)
    compensate = float(settings["compensate"])
    dim_f = int(settings["mdx_dim_f_set"])
    n_fft = int(settings["mdx_n_fft_scale_set"])
    if device_name == "mps" and torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")

    _emit("PROGRESS", "0.02")
    module = ConvertModel(load(str(MODEL_PATH)))
    module.to(device).eval()
    stft = STFT(n_fft, HOP, dim_f, device)

    mix = _load_mix(audio_path)
    trim = n_fft // 2
    chunk_size = HOP * (segment_size - 1)
    gen_size = chunk_size - 2 * trim
    step = int((1 - overlap) * chunk_size)
    if step < 1:
        raise RuntimeError("Overlap không hợp lệ.")

    pad = gen_size + trim - (mix.shape[-1] % gen_size)
    mixture = np.concatenate(
        (
            np.zeros((2, trim), dtype=np.float32),
            mix,
            np.zeros((2, pad), dtype=np.float32),
        ),
        axis=1,
    )
    result = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    divider = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    total_chunks = max(1, (mixture.shape[-1] + step - 1) // step)
    done = 0

    for start in range(0, mixture.shape[-1], step):
        done += 1
        end = min(start + chunk_size, mixture.shape[-1])
        chunk_size_actual = end - start
        window = np.hanning(chunk_size_actual).astype(np.float32)
        window = np.tile(window[None, None, :], (1, 2, 1))
        mix_part = mixture[:, start:end]
        if end != start + chunk_size:
            mix_part = np.concatenate(
                (mix_part, np.zeros((2, (start + chunk_size) - end), dtype=np.float32)),
                axis=-1,
            )
        mix_wave = torch.tensor(mix_part[None, ...], dtype=torch.float32).to(device)
        with torch.no_grad():
            spek = stft(mix_wave)
            spek[:, :, :3, :] *= 0
            spec_pred = module(spek)
            if not isinstance(spec_pred, torch.Tensor):
                spec_pred = torch.tensor(spec_pred)
            tar = stft.inverse(spec_pred.to(device)).cpu().detach().numpy()
        tar[..., :chunk_size_actual] *= window
        divider[..., start:end] += window
        result[..., start:end] += tar[..., : end - start]
        _emit("PROGRESS", f"{0.05 + 0.9 * done / total_chunks:.4f}")

    divider[divider == 0] = 1
    estimate = (result / divider)[:, :, trim:-trim]
    estimate = np.concatenate(estimate, axis=-1)[:, : mix.shape[-1]]
    estimate = estimate * compensate

    vocal = _channels_last(estimate)
    instrumental = _channels_last(mix)[: vocal.shape[0]] - vocal

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem
    vocal_path = output_dir / f"{stem}_(Vocals).wav"
    instrumental_path = output_dir / f"{stem}_(Instrumental).wav"
    sf.write(str(vocal_path), vocal, SAMPLE_RATE, subtype="PCM_16")
    sf.write(str(instrumental_path), instrumental, SAMPLE_RATE, subtype="PCM_16")
    _emit("PROGRESS", "1")
    return {"vocal": str(vocal_path), "instrumental": str(instrumental_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Tách vocal bằng Kim Vocal 2")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="mps", choices=("cpu", "mps"))
    parser.add_argument("--overlap", type=float, default=0.25)
    parser.add_argument("--segment-size", type=int, default=512)
    args = parser.parse_args()
    try:
        paths = separate(
            Path(args.input).expanduser(),
            Path(args.output_dir).expanduser(),
            overlap=args.overlap,
            segment_size=args.segment_size,
            device_name=args.device,
        )
    except Exception as error:
        print(f"ERROR {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
    _emit("RESULT", json.dumps(paths, ensure_ascii=False))


if __name__ == "__main__":
    main()
