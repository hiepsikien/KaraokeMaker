"""Small, shared encoders for background segments.

All segments that are later concatenated with ``-c copy`` must share the same
H.264 parameters. Motion clips come from :mod:`karaoke.living`; still/cover/tail
segments are produced here with the matching settings.

Stills are decoded **once** and cloned with ``loop=loop=-1:size=1:start=0``.
Using ``-loop 1`` on an image input makes FFmpeg's image2 demuxer decode the
JPEG/PNG again for every output frame, which is very expensive for a full song.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from karaoke.living import (
    ENCODER_VERSION as SEGMENT_ENCODER_VERSION,
    X264_BFRAMES as SEGMENT_BFRAMES,
    X264_CRF as SEGMENT_CRF,
    X264_GOP as SEGMENT_GOP,
    X264_PIX_FMT as SEGMENT_PIX_FMT,
    X264_PRESET as SEGMENT_PRESET,
)
from karaoke.media import MediaError
from karaoke.render import _run


def segment_output_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        SEGMENT_PRESET,
        "-crf",
        str(SEGMENT_CRF),
        "-pix_fmt",
        SEGMENT_PIX_FMT,
        "-g",
        str(SEGMENT_GOP),
        "-bf",
        str(SEGMENT_BFRAMES),
        "-an",
        "-movflags",
        "+faststart",
    ]


def _replace(temporary: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, destination)
    return destination


def _temporary(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4"
    )


def encode_still(
    image: Path,
    destination: Path,
    *,
    frames: int,
    width: int,
    height: int,
    fps: int,
    log,
) -> Path:
    """Encode a still image to exactly ``frames`` frames, decoding it once."""
    frames = max(1, int(frames))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary(destination)
    filters = (
        "loop=loop=-1:size=1:start=0,"
        f"fps={fps},"
        f"scale={width}:{height}:in_range=auto:out_range=tv,"
        "setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS"
    )
    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(image),
        "-vf",
        filters,
        "-frames:v",
        str(frames),
        *segment_output_args(),
        str(temporary),
    ]
    try:
        _run(command, log)
        return _replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def encode_video(
    source: Path,
    destination: Path,
    *,
    frames: int,
    width: int,
    height: int,
    fps: int,
    log,
) -> Path:
    """Re-encode a video segment to exactly ``frames`` frames.

    Used for the last partial slot and for normalising Studio assets whose
    frame count does not match the requested image duration. Never trim with
    ``-c copy``: cutting packets can corrupt streams with B-frames.
    """
    frames = max(1, int(frames))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary(destination)
    filters = (
        f"fps={fps},"
        f"scale={width}:{height}:in_range=auto:out_range=tv,"
        "setsar=1,format=yuv420p,settb=AVTB,"
        f"trim=start_frame=0:end_frame={frames},setpts=PTS-STARTPTS"
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        filters,
        "-frames:v",
        str(frames),
        *segment_output_args(),
        str(temporary),
    ]
    try:
        _run(command, log)
        return _replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def encode_segment(
    source: Path,
    destination: Path,
    *,
    frames: int,
    width: int,
    height: int,
    fps: int,
    still: bool,
    log,
) -> Path:
    if not Path(source).is_file():
        raise MediaError(f"Không thấy segment nguồn: {source}")
    encoder = encode_still if still else encode_video
    return encoder(
        source,
        destination,
        frames=frames,
        width=width,
        height=height,
        fps=fps,
        log=log,
    )
