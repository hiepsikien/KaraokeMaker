"""Chọn backend Whisper: mlx-whisper (Apple GPU) hoặc stable_whisper.

MLX chỉ là tuỳ chọn. Module này không import ``mlx_whisper`` hay
``stable_whisper`` ở cấp module, nên việc import nó không bao giờ yêu cầu MLX.
Mọi lần import và tải model đều diễn ra bên trong hàm, khi thực sự cần.
"""

from __future__ import annotations

import platform
import sys

ENGINES = frozenset({"auto", "mlx", "stable"})


def _noop_log(_message: str) -> None:
    pass


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def mlx_available() -> bool:
    """Return True only on Darwin arm64 with both MLX imports working."""
    if not _is_apple_silicon():
        return False
    try:
        import mlx_whisper  # noqa: F401
    except Exception:
        return False
    try:
        from stable_whisper import load_mlx_whisper as _load  # noqa: F401
    except Exception:
        return False
    return True


def load_mlx_whisper(model_name: str):
    """Load the stable-whisper MLX wrapper without importing MLX eagerly."""
    from stable_whisper import load_mlx_whisper as _load

    return _load(model_name)


def load_stable_model(model_name: str, device: str):
    """Load a PyTorch stable-whisper model on ``device`` (cpu/mps)."""
    import stable_whisper

    return stable_whisper.load_model(model_name, device=device)


def _normalize_engine(engine: str) -> str:
    value = str(engine or "").strip().lower()
    return value if value in ENGINES else "auto"


def load_whisper_model(
    model_name: str,
    device: str,
    engine: str,
    log,
) -> tuple[object, str]:
    """Return (model, backend) where backend is 'mlx' or 'stable'.

    ``engine`` values are ``auto``, ``mlx`` and ``stable``; anything unknown is
    treated as ``auto``. MLX failures always fall back to stable_whisper so a
    missing/broken MLX install cannot break an align job.
    """
    emit = log if callable(log) else _noop_log
    resolved = _normalize_engine(engine)

    if resolved == "stable":
        return load_stable_model(model_name, device), "stable"

    if mlx_available():
        emit("Dùng mlx-whisper (Apple GPU).")
        try:
            return load_mlx_whisper(model_name), "mlx"
        except Exception as error:
            if resolved == "mlx":
                emit(f"mlx-whisper lỗi ({error}); fallback stable_whisper.")
            else:
                emit(
                    f"mlx-whisper không dùng được ({error}); "
                    "fallback stable_whisper."
                )
    elif resolved == "mlx":
        emit("mlx-whisper không khả dụng trên máy này; fallback stable_whisper.")

    return load_stable_model(model_name, device), "stable"
