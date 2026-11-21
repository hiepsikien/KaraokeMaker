"""Render profiles for Living Still exports (P1b).

One profile per export. ``strip_rect`` is the lyric strip in output pixels and
is a no-effect zone: effect masks must be exactly zero inside it.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PROFILE_ID = "landscape_1080p"


@dataclass(frozen=True)
class RenderProfile:
    id: str
    width: int
    height: int
    fps: int
    fit_mode: str
    strip_rect: tuple[int, int, int, int]
    safe_top: int
    safe_bottom: int
    safe_left: int
    safe_right: int
    lyric_font_size: int
    max_lyric_width: int
    lyric_row_gap: int
    cover_title_size: int
    cover_artist_size: int
    cover_line_size: int
    cover_max_width: int


PROFILES: dict[str, RenderProfile] = {
    "landscape_1080p": RenderProfile(
        id="landscape_1080p",
        width=1920,
        height=1080,
        fps=30,
        fit_mode="contain",
        strip_rect=(0, 800, 1920, 280),
        safe_top=0,
        safe_bottom=0,
        safe_left=0,
        safe_right=0,
        lyric_font_size=56,
        max_lyric_width=1700,
        lyric_row_gap=10,
        cover_title_size=78,
        cover_artist_size=40,
        cover_line_size=30,
        cover_max_width=1500,
    ),
    "portrait_1080x1920": RenderProfile(
        id="portrait_1080x1920",
        width=1080,
        height=1920,
        fps=30,
        fit_mode="contain",
        # Strip bottom 1613, leaving 307 px for the app's bottom safe area.
        strip_rect=(0, 1333, 1080, 280),
        safe_top=173,
        safe_bottom=307,
        safe_left=0,
        safe_right=118,
        lyric_font_size=48,
        max_lyric_width=960,
        lyric_row_gap=8,
        cover_title_size=64,
        cover_artist_size=34,
        cover_line_size=26,
        cover_max_width=900,
    ),
    "square_1080": RenderProfile(
        id="square_1080",
        width=1080,
        height=1080,
        fps=30,
        fit_mode="contain",
        strip_rect=(0, 720, 1080, 280),
        safe_top=0,
        safe_bottom=0,
        safe_left=0,
        safe_right=0,
        lyric_font_size=44,
        max_lyric_width=960,
        lyric_row_gap=8,
        cover_title_size=56,
        cover_artist_size=30,
        cover_line_size=24,
        cover_max_width=900,
    ),
    "portrait_4x5": RenderProfile(
        id="portrait_4x5",
        width=1080,
        height=1350,
        fps=30,
        fit_mode="contain",
        strip_rect=(0, 990, 1080, 280),
        safe_top=0,
        safe_bottom=0,
        safe_left=0,
        safe_right=0,
        lyric_font_size=44,
        max_lyric_width=960,
        lyric_row_gap=8,
        cover_title_size=58,
        cover_artist_size=32,
        cover_line_size=24,
        cover_max_width=900,
    ),
}


def normalize_profile_id(profile_id: str | None) -> str:
    """Return a known profile id, falling back to the landscape default."""
    text = str(profile_id or "").strip()
    return text if text in PROFILES else DEFAULT_PROFILE_ID


def get_profile(profile_id: str | None) -> RenderProfile:
    return PROFILES[normalize_profile_id(profile_id)]
