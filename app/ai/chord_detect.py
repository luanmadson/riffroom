# SPDX-License-Identifier: GPL-3.0-or-later
"""Chord suggestions: chroma per beat of the instruments with harmony (bass, guitar, piano, other), compared with chord
templates (power chord, major, minor), with the bass note as a hint for the root and Viterbi smoothing, so the chord
does not change at every beat. Sevenths are left out on purpose: distortion and overtones make false 7ths easily."""
import numpy as np

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
KINDS = [('5', (0, 7), 1.06), ('', (0, 4, 7), 1.0), ('m', (0, 3, 7), 1.0)]   # suffix, intervals, prior (rock: power chords)


def _templates():
    T, labels = [], []
    for suffix, ivs, prior in KINDS:
        for r in range(12):
            v = np.zeros(12)
            for k, iv in enumerate(ivs):
                v[(r + iv) % 12] = 1.0 if k == 0 else 0.85
            T.append(v / np.linalg.norm(v) * prior)
            labels.append((r, suffix))
    return np.array(T), labels


def beat_chroma(y, sr, beats, hop=1024, bass=False):
    """Chroma [12, beats]: column j is the median over the beat j (from beat j to beat j+1)."""
    import librosa
    y = np.asarray(y, np.float32)
    if bass:
        C = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop, fmin=librosa.note_to_hz('E1'), n_octaves=3, bins_per_octave=36, norm=None)
    else:
        C = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop, fmin=librosa.note_to_hz('E2'), n_octaves=5, bins_per_octave=36, norm=None)
    b = np.asarray(beats, np.float64)
    frames = np.clip(np.round(b * sr / hop).astype(int), 0, C.shape[1] - 1)
    out = np.zeros((12, len(b)), np.float32)
    for j in range(len(b)):
        f0 = frames[j]
        f1 = frames[j + 1] if j + 1 < len(b) else min(C.shape[1], f0 + max(1, int(np.median(np.diff(frames))) if len(b) > 1 else 1))
        seg = C[:, f0:max(f1, f0 + 1)]
        out[:, j] = np.median(seg, axis=1)
    return out, frames


def _smooth(C, context):
    """Each beat sees the chroma of `context` beats from it on (walking bass lines and riffs average out)."""
    if context <= 1:
        return C
    out = np.empty_like(C)
    for j in range(C.shape[1]):
        out[:, j] = C[:, j:j + context].mean(axis=1)
    return out


def detect(harmonic, sr, beats, bass=None, tuning=0, switch_penalty=3.5, bass_weight=0.1, context=1):
    """Returns [(t0, t1, chord or None)] per run of beats with the same chord. tuning: semitones of the guitar
    (-1 = half step down): the names follow the shape played, not the sounding pitch."""
    beats = np.asarray(beats, np.float64)
    C, fr = beat_chroma(harmonic, sr, beats)
    n = min(C.shape[1], len(beats))
    C = C[:, :n]
    energy = C.sum(axis=0)
    C = _smooth(C, context)
    Cn = C / np.maximum(np.linalg.norm(C, axis=0, keepdims=True), 1e-9)
    T, labels = _templates()
    score = T @ Cn                                   # [chords, beats]
    if bass is not None:
        B, _ = beat_chroma(bass, sr, beats, bass=True)
        B = _smooth(B[:, :n], context)
        Bn = B / np.maximum(B.max(axis=0, keepdims=True), 1e-9)
        root = np.array([r for r, _ in labels])
        score = score + bass_weight * Bn[root, :]
    # "no chord" when the harmony is almost silent
    quiet = energy < 0.15 * np.median(energy[energy > 0]) if np.any(energy > 0) else np.ones(n, bool)
    K = len(labels)
    emit = np.vstack([score * 10, np.full((1, n), -1e3)])
    emit[:-1, quiet] = -1e3
    emit[-1, quiet] = 0
    # Viterbi: staying costs nothing, changing costs switch_penalty
    S = K + 1
    dp = emit[:, 0].copy()
    back = np.zeros((S, n), np.int32)
    for j in range(1, n):
        best_prev = int(np.argmax(dp))
        stay = dp
        move = dp[best_prev] - switch_penalty
        choose_move = move > stay
        back[:, j] = np.where(choose_move, best_prev, np.arange(S))
        dp = np.where(choose_move, move, stay) + emit[:, j]
    path = [int(np.argmax(dp))]
    for j in range(n - 1, 0, -1):
        path.append(int(back[path[-1], j]))
    path = path[::-1]
    names = []
    for s in path:
        if s == K:
            names.append(None)
        else:
            r, suffix = labels[s]
            names.append(NAMES[(r - tuning) % 12] + suffix)
    out = []
    for j, c in enumerate(names):
        t0 = float(beats[j])
        t1 = float(beats[j + 1]) if j + 1 < len(beats) else t0 + float(np.median(np.diff(beats)))
        if out and out[-1][2] == c:
            out[-1] = (out[-1][0], t1, c)
        else:
            out.append((t0, t1, c))
    return out


def tonic(segments):
    """Root of the chord that sounds the longest (a good guess for the tonic in pop and rock)."""
    dur = {}
    for t0, t1, c in segments:
        if c:
            r = c[:2] if len(c) > 1 and c[1] == '#' else c[:1]
            dur[r] = dur.get(r, 0) + (t1 - t0)
    return max(dur, key=dur.get) if dur else None


def chart(segments, bars, beats_per_bar):
    """Text chart (one word per bar) from the chord segments: the chord of each half bar, joined with ',' if it changes."""
    lines, words = [], []
    for i in range(len(bars) - 1):
        a, b = bars[i], bars[i + 1]
        halves = []
        for h0, h1 in ((a, (a + b) / 2), ((a + b) / 2, b)):
            best, best_d = None, 0
            for t0, t1, c in segments:
                d = min(t1, h1) - max(t0, h0)
                if d > best_d:
                    best, best_d = c, d
            halves.append(best)
        tok = (halves[0] or '-') if halves[0] == halves[1] else f'{halves[0] or "-"},{halves[1] or "-"}'
        if tok == '-,-':
            tok = '-'
        words.append(tok)
        if len(words) == 4:
            lines.append(' '.join(words)); words = []
    if words:
        lines.append(' '.join(words))
    return lines
