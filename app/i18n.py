# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface languages: English (default), Portuguese and Spanish.

The texts live in locales/<lang>.json and are shared by the server (t()) and the screen (GET /api/locale).
A missing key falls back to English, then to the key itself."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LANGUAGES = {'en': 'English', 'pt': 'Português', 'es': 'Español'}
_cache = {}
_lang = 'en'


def _load(lang):
    if lang not in _cache:
        with open(os.path.join(HERE, 'locales', f'{lang}.json'), encoding='utf-8') as f:
            _cache[lang] = json.load(f)
    return _cache[lang]


def set_language(lang):
    global _lang
    _lang = lang if lang in LANGUAGES else 'en'


def language():
    return _lang


def has(key):
    return key in _load(_lang) or key in _load('en')


def t(key, **values):
    s = _load(_lang).get(key)
    if s is None:
        s = _load('en').get(key, key)
    return s.format(**values) if values else s


def strings(lang=None):
    """All texts of a language, completed with English where a translation is missing."""
    lang = lang or _lang
    out = dict(_load('en'))
    out.update(_load(lang))
    return out


def system_language():
    """Language of the Windows interface, if it is one of ours (used only as a hint by the installer)."""
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x16: 'pt', 0x0A: 'es'}.get(lid, 'en')
    except Exception:
        return 'en'
