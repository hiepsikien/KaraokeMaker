from __future__ import annotations

import re
import unicodedata


def extract_sung_lines(lyrics: str) -> list[str]:
    """Giữ các câu hát. Bỏ dòng trống và dòng ghi chú nằm trọn trong [ngoặc]."""
    sung: list[str] = []
    for raw in lyrics.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        sung.append(line)
    return sung


def normalize_token(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = re.sub(r"[^\w]+", "", text, flags=re.UNICODE)
    return text


def display_name_from_stem(filename: str) -> str:
    """Tên bài để hiện, giữ chữ hoa gốc. ``1_Bài_(Vocals).wav`` → ``Bài``."""
    stem = unicodedata.normalize("NFC", filename)
    stem = re.sub(r"\.[^.]+$", "", stem)
    stem = re.sub(r"_\((?:Vocals|Instrumental)\)$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^\d+_", "", stem)
    return stem.strip()


def song_key_from_stem(filename: str) -> str:
    """Khóa so khớp tên bài, không phân biệt hoa thường."""
    return display_name_from_stem(filename).casefold()
