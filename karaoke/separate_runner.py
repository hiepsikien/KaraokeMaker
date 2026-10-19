"""Run Kim Vocal 2 in the UVR virtualenv and report progress."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from karaoke.errors import JobError

ROOT = Path(__file__).resolve().parents[1]
UVR_PYTHON = ROOT / ".venv" / "bin" / "python"
SEPARATE_SCRIPT = ROOT / "scripts" / "separate_kim2.py"


def separate_kim2(
    audio_path: Path,
    output_dir: Path,
    *,
    device: str = "mps",
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, str]:
    if not UVR_PYTHON.is_file():
        raise JobError("Không thấy môi trường UVR tại .venv. Hãy cài UVR trước khi tách giọng.")
    if not SEPARATE_SCRIPT.is_file():
        raise JobError("Thiếu script tách Kim Vocal 2.")
    chosen = device if device in {"cpu", "mps"} else "cpu"
    command = [
        str(UVR_PYTHON),
        str(SEPARATE_SCRIPT),
        "--input",
        str(audio_path),
        "--output-dir",
        str(output_dir),
        "--device",
        chosen,
        "--overlap",
        "0.25",
        "--segment-size",
        "512",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    result: dict[str, str] | None = None
    assert process.stdout is not None
    for line in process.stdout:
        text = line.strip()
        if text.startswith("PROGRESS ") and on_progress is not None:
            try:
                on_progress(float(text.split(None, 1)[1]))
            except ValueError:
                continue
        elif text.startswith("RESULT "):
            try:
                payload = json.loads(text.split(" ", 1)[1])
            except json.JSONDecodeError as error:
                process.kill()
                raise JobError("Script tách giọng trả về kết quả không đọc được.") from error
            if isinstance(payload, dict):
                result = {"vocal": str(payload.get("vocal") or ""), "instrumental": str(payload.get("instrumental") or "")}
    stderr = ""
    if process.stderr is not None:
        stderr = process.stderr.read()
    code = process.wait()
    if code != 0 or result is None:
        detail = stderr.strip().splitlines()
        message = detail[-1] if detail else "Tách giọng Kim Vocal 2 thất bại."
        if message.startswith("ERROR "):
            message = message[6:]
        raise JobError(message)
    if not result["vocal"] or not result["instrumental"]:
        raise JobError("Tách giọng không ghi đủ file vocal và instrumental.")
    return result
