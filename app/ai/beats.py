# SPDX-License-Identifier: GPL-3.0-or-later
"""Beats and bars with beat-this (ISMIR 2024, MIT license), and the feel of the beat (triplets or straight eighths)."""
import numpy as np


def track(mono, sr, device=None):
    """Times (s) of the beats and of the downbeats (the 1st beat of each bar)."""
    import torch
    from beat_this.inference import Audio2Beats
    dev = device or ('cuda' if torch.cuda.is_available() else 'cpu')
    a2b = Audio2Beats(checkpoint_path='final0', device=dev, dbn=False)
    beats, downbeats = a2b(np.asarray(mono, np.float32), sr)
    return np.asarray(beats, np.float64), np.asarray(downbeats, np.float64)


def grid_choice(downbeat_idx, change_idx):
    """Length of the bar (beats) and the beat where the bars start, from the downbeats and the chord changes.

    When the downbeats are regular (every 3 or 4 beats) they are trusted. Otherwise - typically a slow 12/8 heard as
    bars of 6/8, with downbeats every 2 beats and sometimes every 4 - every regular grid of 4, 3 and 2 beats is scored by
    how many downbeats and chord changes fall on it, and the longest bar that explains most of them wins."""
    di = list(downbeat_idx)
    if len(di) >= 4:
        sp = np.diff(di)
        vals, cnt = np.unique(sp, return_counts=True)
        m, share = int(vals[np.argmax(cnt)]), cnt.max() / len(sp)
        if m in (3, 4) and share >= 0.75:
            phases = np.bincount([i % m for i in di], minlength=m)
            return m, int(np.argmax(phases))
    ci = list(change_idx)
    best_any = None
    for L in (4, 3, 2):
        best = None
        for phi in range(L):
            hd = float(np.mean([(i - phi) % L == 0 for i in di])) if di else 0.0
            hc = float(np.mean([(i - phi) % L == 0 for i in ci])) if len(ci) >= 4 else None
            score = hd if hc is None else 0.4 * hd + 0.6 * hc
            if best is None or score > best[0]:
                best = (score, L, phi)
        if best[0] >= 0.8:
            return best[1], best[2]
        if best_any is None or best[0] > best_any[0] + 0.1:
            best_any = best
    return best_any[1], best_any[2]


def clean_downbeats(di, L, n_beats):
    """Beat indices of the bar starts, from the detected downbeats: a downbeat in the middle of a bar that the next
    downbeat completes is dropped (half-bar or count-in detections); gaps longer than a bar are filled every L beats.
    A short bar between two normal ones (like a 2-beat bar in a 4/4 song) is kept."""
    di = sorted(set(di))
    kept = [di[0]] if di else [0]
    for k, i in enumerate(di[1:], start=1):
        last = kept[-1]
        nxt = di[k + 1] if k + 1 < len(di) else None
        if i - last < L and nxt is not None and nxt - last <= L:
            continue
        while i - kept[-1] > L + 1:          # missing downbeats
            kept.append(kept[-1] + L)
        kept.append(i)
    while kept[-1] + L < n_beats:
        kept.append(kept[-1] + L)
    return kept


def bars_from(beats, downbeats, duration, chord_segments=None):
    """Bars over the tracked beats (they follow the tempo of the song): start times, beats in each bar, the usual beats
    per bar and the tempo. chord_segments (optional, [(t0, t1, chord)]) helps tell 12/8 from 6/8.

    When enough downbeats come every 3 or more beats, they are followed (cleaned); when almost all come every 2 beats
    (a slow 12/8 heard as 6/8), a regular grid chosen with the help of the chord changes is used."""
    beats = np.asarray(beats, np.float64)
    db = np.asarray(downbeats, np.float64)
    if len(beats) < 8:
        raise ValueError('too few beats found: is the audio silent or without rhythm?')
    ibi = float(np.median(np.diff(beats)))

    def to_idx(times):
        out = []
        for t in times:
            k = int(np.argmin(np.abs(beats - t)))
            if abs(beats[k] - t) < 0.2 * ibi:
                out.append(k)
        return out

    changes = []
    if chord_segments:
        changes = [s[0] for i, s in enumerate(chord_segments) if i and s[2] and s[2] != chord_segments[i - 1][2]]
    di = sorted(set(to_idx(db)))
    sp = np.diff(di) if len(di) > 1 else np.array([])
    long = sp[sp >= 3]
    if len(sp) and len(long) >= 0.25 * len(sp):
        vals, cnt = np.unique(long, return_counts=True)
        L = int(vals[np.argmax(cnt)])
        idx = clean_downbeats(di, L, len(beats))
    else:
        L, phi = grid_choice(di, to_idx(changes))
        idx = list(range(phi, len(beats), L))
    idx = [i for i in idx if i < len(beats)]
    starts = [beats[i] for i in idx]
    bpb = [b - a for a, b in zip(idx, idx[1:])] + [L]
    bars = starts + [min(starts[-1] + L * ibi, duration + L * ibi)]
    return {'bars': [round(float(v), 4) for v in bars], 'beats_per_bar': [int(x) for x in bpb], 'numerator': L,
            'bpm': round(60.0 / ibi, 1), 'beat_s': ibi}


def feel(onset_times, beats):
    """3 if the notes between the beats fall on triplets (shuffle, 12/8), 2 if on straight eighths/sixteenths."""
    b = np.asarray(beats, np.float64)
    on = np.asarray(onset_times, np.float64)
    if len(b) < 4 or len(on) < 8:
        return 2
    i = np.searchsorted(b, on, side='right') - 1
    ok = (i >= 0) & (i < len(b) - 1)
    i, on = i[ok], on[ok]
    phase = (on - b[i]) / (b[i + 1] - b[i])
    phase = phase[(phase > 0.12) & (phase < 0.88)]
    trip = np.sum(np.abs(phase - 1 / 3) < 0.07) + np.sum(np.abs(phase - 2 / 3) < 0.07)
    straight = np.sum(np.abs(phase - 0.5) < 0.07) + np.sum(np.abs(phase - 0.25) < 0.05) + np.sum(np.abs(phase - 0.75) < 0.05)
    return 3 if trip >= 8 and trip > 1.3 * straight else 2


def time_signature(numerator, sub):
    return f'{numerator * 3}/8' if sub == 3 else f'{numerator}/4'
