# SPDX-License-Identifier: GPL-3.0-or-later
"""Song lyrics: reads and writes LRC (the time of each line, [mm:ss.xx]) or plain text, finds the lyrics of a song,
shifts the times and lines them up with the song's separated voice.

The app keeps its own copy, lyrics.lrc, next to the song's riffroom.json, with the times of the song as Riffroom plays
it. A lyrics file brought from outside (any .lrc in the song folder, usually timed to the original record) is copied to
lyrics.lrc the first time and lined up with the voice when the song has a vocals part; the original file stays as it is.
Riffroom never fetches lyrics: they come from the player."""
import glob
import os
import re

import numpy as np

FILE = 'lyrics.lrc'
TEXT_NAMES = ('lyrics.txt', 'letra.txt', 'letras.txt')   # plain-text lyrics brought from outside (besides any .lrc)
MIN_QUALITY = 0.25   # share of lines that must start on an entry of the voice (by chance: ~0.1) to accept a line-up

_TIME = re.compile(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]')
_META = re.compile(r'^\[([A-Za-z#][\w#]*):([^\]]*)\]$')
_WORD = re.compile(r'<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>')


def _secs(m):
    v = int(m.group(1)) * 60 + int(m.group(2))
    if m.group(3):
        v += int(m.group(3)) / 10 ** len(m.group(3))
    return float(v)


def parse(text):
    """{'lines': [{'t': seconds or None, 'text': str}], 'meta': {tag: value}} from LRC or plain text.
    A line with several times (a chorus that comes back) becomes one line for each; [offset:ms] is applied (positive:
    the lines show earlier); per-word times (<mm:ss.xx>) are dropped. Blank lines separate verses."""
    meta, items = {}, []
    key, seq = -1.0, 0
    for raw in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        s = raw.strip().lstrip('﻿').strip()
        m = _META.match(s)
        if m and not _TIME.match(s):
            meta[m.group(1).lower()] = m.group(2).strip()
            continue
        times = []
        m = _TIME.match(s)
        while m:
            times.append(_secs(m))
            s = s[m.end():].strip()
            m = _TIME.match(s)
        s = re.sub(r'\s+', ' ', _WORD.sub('', s)).strip()
        if times:
            for v in times:
                items.append((v, seq, {'t': v, 'text': s}))
                seq += 1
            key = times[0]      # an untimed line stays after the line written before it in the file
        else:
            items.append((key, seq, {'t': None, 'text': s}))
            seq += 1
    items.sort(key=lambda x: (x[0], x[1]))
    try:
        off = float(meta.pop('offset', 0) or 0) / 1000.0
    except ValueError:
        off = 0.0
    lines = []
    for _, _, ln in items:
        if ln['t'] is not None:
            ln['t'] = round(max(0.0, ln['t'] - off), 3)
        blank = not ln['text'] and ln['t'] is None
        if blank and (not lines or (not lines[-1]['text'] and lines[-1]['t'] is None)):
            continue  # one blank line between verses is enough
        lines.append(ln)
    while lines and not lines[-1]['text'] and lines[-1]['t'] is None:
        lines.pop()
    return {'lines': lines, 'meta': meta}


def fmt(t):
    cs = int(round(max(0.0, float(t)) * 100))
    return f'[{cs // 6000:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}]'


def dump(lines, meta=None):
    """LRC text: the tags (title, artist...) and then a line per sung line, with its time when it has one."""
    out = [f'[{k}:{v}]' for k, v in (meta or {}).items() if v not in (None, '') and k != 'offset']
    for ln in lines:
        out.append((fmt(ln['t']) if ln.get('t') is not None else '') + (ln.get('text') or ''))
    return '\n'.join(out).strip('\n') + '\n' if out else ''


def timed(lines):
    return any(ln.get('t') is not None and ln.get('text') for ln in lines)


def shift(lines, dt):
    return [dict(ln, t=round(max(0.0, ln['t'] + dt), 3)) if ln.get('t') is not None else dict(ln) for ln in lines]


# ---------------------------------------------------------------- files
def find(dirs):
    """(path, own): the app's lyrics.lrc when it exists; otherwise the newest lyrics file brought from outside (any
    .lrc, lyrics.txt, letra.txt or letras.txt) in those folders; (None, False) when there is none."""
    for d in dirs:
        p = os.path.join(d, FILE)
        if os.path.isfile(p):
            return p, True
    found = []
    for d in dirs:
        cands = glob.glob(os.path.join(glob.escape(d), '*.lrc')) + [os.path.join(d, n) for n in TEXT_NAMES]
        found += [p for p in cands if os.path.isfile(p) and os.path.basename(p).lower() != FILE]
    if not found:
        return None, False
    return max(found, key=os.path.getmtime), False


def read_file(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('latin-1')


def write_file(folder, lines, meta=None):
    """Writes <folder>/lyrics.lrc (a temporary file first, so a failure never leaves half a file)."""
    p = os.path.join(folder, FILE)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(dump(lines, meta))
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- lining up with the voice
HOP = 0.02


def _envelope_db(voice, sr):
    h = int(sr * HOP)
    n = len(voice) // h
    x = np.asarray(voice[:n * h], dtype=np.float32).reshape(n, h)
    return 10 * np.log10(np.mean(x * x, axis=1) + 1e-12)


def voice_starts(voice, sr, floor_db=-24.0, gap=0.2):
    """Seconds where the separated voice comes in after at least `gap` s of silence. 'Silence' is `floor_db` below the
    loud singing (99th percentile of 20 ms windows), so what leaked from the band into the voice does not count."""
    db = _envelope_db(voice, sr)
    n = len(db)
    if n < 10:
        return np.zeros(0)
    act = db > np.percentile(db, 99) + floor_db
    k = max(1, int(round(gap / HOP)))
    prev = np.convolve(act.astype(np.int32), np.ones(k, np.int32))[:n]       # active windows among the last k
    before = np.concatenate([[0], prev[:-1]])
    return np.flatnonzero(act & (before == 0)) * HOP


def align(lines, voice, sr, max_shift=20.0):
    """(shift in s, quality 0-1): moves the lyrics so that they fall where the separated voice sings.
    1. Rough, within +-max_shift: the stretch of each line (from its time to the next line, at most 6 s) against where the
       voice is active; the long gaps (intro, solos) make this unambiguous.
    2. Fine, within +-0.6 s of that: the start of each line on a rise of the voice's loudness.
    Quality: share of lines that then start within 0.3 s of an entry of the voice. (None, 0) without enough to compare."""
    tl = np.array(sorted(ln['t'] for ln in lines if ln.get('t') is not None and ln.get('text')), np.float64)
    db = _envelope_db(voice, sr) if len(voice) >= int(sr * HOP) * 10 else np.zeros(0)
    n = len(db)
    if len(tl) < 3 or n < 10:
        return None, 0.0
    act = (db > np.percentile(db, 99) - 24.0).astype(np.float32)
    if act.mean() < 0.01:
        return None, 0.0
    sung = np.zeros(n, bool)
    ends = np.minimum(np.append(tl[1:], tl[-1] + 6.0), tl + 6.0)
    for a, b in zip(tl, ends):
        sung[max(0, int(a / HOP)):max(0, min(n, int(b / HOP)))] = True
    idx = np.arange(max(0, int(tl[0] / HOP) - int(5 / HOP)), min(n, int(ends[-1] / HOP) + int(5 / HOP)))
    best_o, best_s = 0.0, -1.0
    for o in np.arange(-max_shift, max_shift + 1e-9, 0.05):
        j = idx + int(round(o / HOP))
        ok = (j >= 0) & (j < n)
        if ok.sum() < len(idx) // 2:
            continue
        a, m = act[j[ok]], sung[idx[ok]]
        if m.any() and (~m).any():
            sc = float(a[m].mean() - a[~m].mean())
            if sc > best_s:
                best_o, best_s = float(o), sc
    if best_s <= 0:
        return None, 0.0
    sm = np.convolve(db, np.ones(3) / 3, 'same')
    rise = np.maximum(0.0, np.diff(sm, prepend=sm[0]))
    g = np.exp(-0.5 * (np.arange(-15, 16) * HOP / 0.08) ** 2)
    rise = np.convolve(rise, g, 'same')
    fine = np.arange(best_o - 0.6, best_o + 0.6001, 0.005)
    sc = [rise[np.clip(np.round((tl + o) / HOP).astype(int), 0, n - 1)].mean() for o in fine]
    shift_s = float(round(fine[int(np.argmax(sc))], 3))
    starts = voice_starts(voice, sr)
    if len(starts) == 0:
        return shift_s, 0.0
    x = tl + shift_s
    i = np.clip(np.searchsorted(starts, x), 1, max(1, len(starts) - 1))
    d = np.minimum(np.abs(starts[np.minimum(i, len(starts) - 1)] - x), np.abs(starts[i - 1] - x))
    return shift_s, float(np.mean(d <= 0.3))
