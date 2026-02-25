"""Constants shared across the TTS backend modules."""

VCTK_SPEAKERS = {
    "p225", "p226", "p227", "p228", "p229", "p230", "p231", "p232", "p233", "p234",
    "p236", "p237", "p238", "p239", "p240", "p241", "p243", "p244", "p245", "p246",
}

VOICE_ALIASES = {
    # UI-friendly labels → concrete choices
    "vits_english": {"lang": "en", "speaker": "p225"},
    "xtts_french": {"lang": "fr", "speaker": "Alma María"},
    "alma_maria": {"speaker": "Alma María"},
}

DEFAULT_SPEAKER_BY_LANG = {"en": "p225", "fr": "", "es": ""}

MODEL_BY_LANG = {
    "en": "tts_models/en/vctk/vits",
    "fr": "tts_models/fr/css10/vits",
    "es": "tts_models/es/css10/vits",
}

__all__ = [
    "VCTK_SPEAKERS",
    "VOICE_ALIASES",
    "DEFAULT_SPEAKER_BY_LANG",
    "MODEL_BY_LANG",
]
