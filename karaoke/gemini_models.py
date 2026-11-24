"""Single source of truth for the Gemini models this app may use."""

from __future__ import annotations

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
AUDIO_MODELS = frozenset({DEFAULT_GEMINI_MODEL})
