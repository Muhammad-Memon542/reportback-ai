"""Text helpers shared by the ingesters: decoding, whitespace, and PII scrubbing."""

from __future__ import annotations

import codecs
import re

_WS = re.compile(r"\s+")


def _cp1252_fallback(err: UnicodeError) -> tuple[str, int]:
    if not isinstance(err, UnicodeDecodeError):
        raise err
    bad = err.object[err.start : err.end]
    return bad.decode("cp1252", errors="replace"), err.end


codecs.register_error("reportback-cp1252", _cp1252_fallback)


def decode_html(content: bytes) -> str:
    """TMMIS pages declare UTF-8 but contain stray Windows-1252 bytes (e.g. 0xD7, the multiplication sign).

    Decode as UTF-8 and fall back to cp1252 only for the invalid bytes, so pages mixing
    real UTF-8 with cp1252 decode correctly (a whole-page cp1252 decode would mangle the UTF-8).
    """
    return content.decode("utf-8", errors="reportback-cp1252")


def norm_ws(text: str) -> str:
    return _WS.sub(" ", text.replace("\x00", "")).strip()


# PII scrubbing for any text we index. Officials' names stay (public role); contact details go.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\d.])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d])"
    r"(?:\s*(?:ext\.?|x)\s*\d{1,5})?",
    re.IGNORECASE,
)
EMAIL_TOKEN = "[email removed]"  # noqa: S105 (not a secret)
PHONE_TOKEN = "[phone removed]"  # noqa: S105


def scrub_pii(text: str) -> str:
    """Remove email addresses and North American phone numbers (and NUL bytes, which some
    PDFs' text layers contain and Postgres text columns reject)."""
    return _PHONE.sub(PHONE_TOKEN, _EMAIL.sub(EMAIL_TOKEN, text.replace("\x00", "")))
