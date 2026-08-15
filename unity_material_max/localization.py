"""English / Russian text for the 3ds Max dialog.

The same per-tool switch the Blender front-end has: 3ds Max is often installed
in English while the person in front of it is not, so the language of the tool
is a setting of the tool and nothing else.

The default follows the Windows UI language, because the first run is exactly
the moment when nobody has set anything yet.
"""

from __future__ import annotations

import os


EN = "EN"
RU = "RU"
LANGUAGES = (EN, RU)

_language = ""


def system_language() -> str:
    """Russian if Windows itself is Russian, English otherwise."""
    try:
        import ctypes  # noqa: PLC0415  (Windows only, and only for this question)

        # The low 10 bits of the LCID are the primary language; 0x19 is Russian.
        if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x19:
            return RU
    except Exception:  # noqa: BLE001  (no locale is not a reason to fail)
        pass

    for name in ("LANG", "LANGUAGE", "LC_ALL"):
        if os.environ.get(name, "").casefold().startswith("ru"):
            return RU
    return EN


def set_language(value: str) -> str:
    """Set the language; an empty value means "follow the system"."""
    global _language
    cleaned = (value or "").strip().upper()
    _language = cleaned if cleaned in LANGUAGES else system_language()
    return _language


def language() -> str:
    return _language or set_language("")


def text(english: str, russian: str) -> str:
    return russian if language() == RU else english
