"""Lightweight i18n support for Aixis platform.

Uses a simple dict-based translation system. Translations are loaded
from Python dicts rather than .po files to avoid the babel dependency.
"""
from typing import Callable

# Translation dictionaries
_translations: dict[str, dict[str, str]] = {}
_current_lang: str = "ja"


def load_translations():
    """Load all translations."""
    from .translations.en import messages as en_messages
    _translations["en"] = en_messages
    # Japanese is the default -- no translation needed (strings are already in JA)
    _translations["ja"] = {}


def get_translator(lang: str = "ja") -> Callable[[str], str]:
    """Get a translation function for the given language."""
    if not _translations:
        load_translations()

    trans = _translations.get(lang, {})

    def _(text: str) -> str:
        if lang == "ja":
            return text  # Japanese is the source language
        return trans.get(text, text)  # Fallback to original text

    return _


def detect_language(
    query_param: str | None = None,
    accept_language: str | None = None,
    user_pref: str | None = None,
    cookie_lang: str | None = None,
) -> str:
    """Detect the preferred language.

    Priority: query param > user preference > cookie > Accept-Language > default (ja)
    """
    supported = {"ja", "en"}

    if query_param and query_param in supported:
        return query_param
    if user_pref and user_pref in supported:
        return user_pref
    if cookie_lang and cookie_lang in supported:
        return cookie_lang
    if accept_language:
        # Parse Accept-Language header (simplified)
        for part in accept_language.split(","):
            lang = part.strip().split(";")[0].strip().lower()
            if lang in supported:
                return lang
            if lang.startswith("en"):
                return "en"
            if lang.startswith("ja"):
                return "ja"
    return "ja"
