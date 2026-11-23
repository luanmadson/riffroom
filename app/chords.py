# SPDX-License-Identifier: GPL-3.0-or-later
"""Chords: note name, chord type, scale degree relative to the tonic, guitar fingering and the chord diagram (SVG)."""
import html

import i18n

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
FLATS = {'Db': 'C#', 'Eb': 'D#', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#'}
ROMAN = ['I', '♭II', 'II', '♭III', 'III', 'IV', '♭V', 'V', '♭VI', 'VI', '♭VII', 'VII']
INTERVALS = ['1', '♭2', '2', '♭3', '3', '4', '♭5', '5', '♭6', '6', '♭7', '7']
OPEN_STRINGS = [40, 45, 50, 55, 59, 64]  # low E ... high E (standard tuning, MIDI)
TYPE_KEYS = {'5': '5', '': 'maj', 'm': 'm', 'sus2': 'sus2', 'sus4': 'sus4', '7': '7', 'm7': 'm7', '6': '6', 'maj7': 'maj7'}
# Fingerings from the 6th string to the 1st; 'x' = not played. Open position when there is one.
SHAPES = {'A5': ['x', 0, 2, 2, 'x', 'x'], 'A': ['x', 0, 2, 2, 2, 0], 'Am': ['x', 0, 2, 2, 1, 0], 'Asus2': ['x', 0, 2, 2, 0, 0],
          'A7': ['x', 0, 2, 0, 2, 0], 'Am7': ['x', 0, 2, 0, 1, 0],
          'D5': ['x', 'x', 0, 2, 3, 'x'], 'D': ['x', 'x', 0, 2, 3, 2], 'Dm': ['x', 'x', 0, 2, 3, 1], 'Dsus2': ['x', 'x', 0, 2, 3, 0],
          'D7': ['x', 'x', 0, 2, 1, 2],
          'C5': ['x', 3, 5, 5, 'x', 'x'], 'C': ['x', 3, 2, 0, 1, 0], 'Csus2': ['x', 3, 0, 0, 3, 3], 'Cm': ['x', 3, 5, 5, 4, 3],
          'G5': [3, 5, 5, 'x', 'x', 'x'], 'G': [3, 2, 0, 0, 0, 3], 'Gsus2': [3, 0, 0, 2, 3, 3], 'Gm': [3, 5, 5, 3, 3, 3],
          'G7': [3, 2, 0, 0, 0, 1],
          'E5': [0, 2, 2, 'x', 'x', 'x'], 'E': [0, 2, 2, 1, 0, 0], 'Em': [0, 2, 2, 0, 0, 0], 'Esus2': [0, 2, 4, 4, 'x', 'x'],
          'E7': [0, 2, 0, 1, 0, 0], 'Em7': [0, 2, 0, 0, 0, 0],
          'F5': [1, 3, 3, 'x', 'x', 'x'], 'F': [1, 3, 3, 2, 1, 1], 'F#5': [2, 4, 4, 'x', 'x', 'x'],
          'B5': ['x', 2, 4, 4, 'x', 'x'], 'Bm': ['x', 2, 4, 4, 3, 2], 'B7': ['x', 2, 1, 2, 0, 2]}


def split(chord):
    """'F#m7' -> ('F#', 'm7')."""
    r = chord[:2] if len(chord) > 1 and chord[1] in '#b' else chord[:1]
    return r, chord[len(r):]


def root(chord):
    """Pitch class of the root (0 = C ... 11 = B)."""
    r, _ = split(chord)
    return NAMES.index(FLATS.get(r, r))


def is_valid(chord):
    try:
        r, _ = split(chord)
        return bool(r) and r[0] in 'ABCDEFG' and root(chord) >= 0
    except ValueError:
        return False


def degree(chord, tonic):
    return ROMAN[(root(chord) - root(tonic)) % 12]


def transpose(chord, semitones):
    """Same chord type, root moved by `semitones` (written with sharps)."""
    _, rest = split(chord)
    return NAMES[(root(chord) + semitones) % 12] + rest


def note_name(letter_root):
    """'F#' -> 'F♯' (English), 'Fá♯' (Portuguese), 'Fa♯' (Spanish)."""
    name = i18n.t('note.' + letter_root[0])
    return name + ('♯' if letter_root.endswith('#') else '♭' if letter_root.endswith('b') else '')


def describe(chord):
    r, rest = split(chord)
    key = TYPE_KEYS.get(rest)
    return {'note': note_name(r), 'type': i18n.t('chord.type.' + key) if key else rest}


def shape(chord):
    if chord in SHAPES:
        return SHAPES[chord]
    pc = root(chord)
    f6, f5 = (pc - 4) % 12, (pc - 9) % 12  # power chord on the 6th or the 5th string, whichever is closer to the nut
    return [f6, f6 + 2, f6 + 2, 'x', 'x', 'x'] if f6 <= f5 else ['x', f5, f5 + 2, f5 + 2, 'x', 'x']


def diagram(chord):
    """Fingering, string of the root (the lowest one) and the interval of each string relative to the chord root."""
    frets, r = shape(chord), root(chord)
    below, root_string = [], None
    for i, f in enumerate(frets):
        if f == 'x':
            below.append('')
            continue
        iv = (OPEN_STRINGS[i] + f - r) % 12
        below.append(INTERVALS[iv])
        if iv == 0 and root_string is None:
            root_string = i
    return {'frets': frets, 'root_string': root_string, 'intervals': below}


# ---------------------------------------------------------------- SVG diagram (6th string on the left)
INK, INK2, MUTED, GOLD, DGOLD = '#1d2329', '#4a525c', '#7c838d', '#e0a526', '#9a6a00'
FONT = "font-family=\"'Segoe UI', sans-serif\""


def _text(x, y, s, size=10, fill=INK, anchor='middle', weight=400, extra=''):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}" {extra}>{html.escape(str(s), quote=True)}</text>')


def box(name, frets, fingers=None, root_string=None, subtitle='', below=None):
    """Chord box. frets/fingers: 6 items from the 6th string (low E) to the 1st; 'x' = not played.
    below: 6 labels written under the strings (e.g. the intervals); without it, the note names."""
    fingers = fingers or [None] * 6
    W, H = 122, 196
    x0, dx, y0, dy = 26, 14, 58, 22
    played = [f for f in frets if f != 'x' and f > 0]
    base = 1 if not played or max(played) <= 4 else min(played)
    o = [_text(W / 2, 17, name, 16, INK, weight=700)]
    if subtitle:
        o.append(_text(W / 2, 31, subtitle, 8.5, MUTED))
    for i in range(6):
        x = x0 + i * dx
        o.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + 5 * dy}" stroke="#9aa1aa" stroke-width="{1.6 - i * 0.12:.2f}"/>')
    for k in range(6):
        y = y0 + k * dy
        o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + 5 * dx}" y2="{y}" stroke="#9aa1aa" stroke-width="1"/>')
    if base == 1:
        o.append(f'<rect x="{x0 - 1}" y="{y0 - 4}" width="{5 * dx + 2}" height="5" fill="{INK}" rx="1"/>')
    else:
        o.append(_text(x0 - 9, y0 + dy * 0.5 + 4, f'{base}', 9.5, INK2, anchor='end', weight=600))
    for i, f in enumerate(frets):
        x = x0 + i * dx
        if f == 'x':
            o.append(_text(x, y0 - 10, '×', 12, MUTED))
        elif f == 0:
            color = GOLD if root_string == i else INK2
            fill = '#fff6dd' if root_string == i else 'none'
            o.append(f'<circle cx="{x}" cy="{y0 - 14}" r="4.2" fill="{fill}" stroke="{color}" stroke-width="1.6"/>')
        else:
            cy = y0 + (f - base + 0.5) * dy
            fill, tc = (GOLD, INK) if root_string == i else (INK, '#ffffff')
            o.append(f'<circle cx="{x}" cy="{cy}" r="7.2" fill="{fill}"/>')
            if fingers[i]:
                o.append(_text(x, cy + 3.6, fingers[i], 9.5, tc, weight=700))
        if f != 'x':
            if below:
                if below[i]:
                    o.append(_text(x, y0 + 5 * dy + 15, below[i], 8.6, DGOLD if below[i] == '1' else INK2, weight=700,
                                   extra='letter-spacing="-0.4"'))
            else:
                o.append(_text(x, y0 + 5 * dy + 15, NAMES[(OPEN_STRINGS[i] + f) % 12], 8, INK2, extra='letter-spacing="-0.4"'))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" {FONT}>'
            f'{"".join(o)}</svg>')


def svg(chord, d=None):
    """Chord diagram with the chord type on top and the intervals below. d: the stored 'shape' of the song (optional)."""
    d = d or diagram(chord)
    return box(chord, d['frets'], None, d['root_string'], describe(chord)['type'].split(':')[0], d['intervals'])
