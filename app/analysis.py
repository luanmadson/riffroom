# SPDX-License-Identifier: GPL-3.0-or-later
"""Timing analysis of a take: finds each strum (onset) and compares it with the beat grid of the song (beats and their
subdivisions) and with the attacks of the original part, when the song has it isolated.

Onsets: log spectral flux (5 ms hop) with an adaptive threshold, then refined to ~1 ms on the peak envelope
(the same refinement used by the latency calibration)."""
import hashlib
import json
import os

import numpy as np

SR = 44100
VERSION = 2


# ------------------------------------------------------------------ onsets
def _flux(x, sr, n_fft=1024, hop=220, chunk=4000):
    """Positive log spectral flux, frame by frame (computed in chunks to keep the memory low)."""
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (freqs >= 70) & (freqs <= 9000)
    nfr = 1 + max(0, (len(x) - n_fft)) // hop
    flux = np.zeros(nfr, np.float32)
    prev = None
    for f0 in range(0, nfr, chunk):
        f1 = min(nfr, f0 + chunk)
        idx = np.arange(n_fft)[None, :] + hop * np.arange(f0, f1)[:, None]
        frames = x[idx] * win
        S = np.log1p(100.0 * np.abs(np.fft.rfft(frames, axis=1)[:, band])).astype(np.float32)
        if prev is None:
            prev = S[:1]
        d = np.diff(np.concatenate([prev, S]), axis=0)
        flux[f0:f1] = np.maximum(d, 0).sum(axis=1)
        prev = S[-1:]
    return flux, hop, n_fft


def onsets(x, sr=SR, min_gap_s=0.07, sensitivity=1.0):
    """Times (s) of the attacks in x (mono or stereo)."""
    m = (x if x.ndim == 1 else x.mean(axis=1)).astype(np.float32)
    if len(m) < sr // 4:
        return np.zeros(0)
    flux, hop, n_fft = _flux(m, sr)
    if not len(flux) or flux.max() <= 0:
        return np.zeros(0)
    f = flux / max(float(np.percentile(flux, 99.5)), 1e-9)
    w = max(3, int(0.3 * sr / hop) | 1)
    local = np.convolve(f, np.ones(w) / w, mode='same')
    thr = 1.5 * local + 0.06 / sensitivity
    peaks = []
    k = 3
    for i in range(k, len(f) - k):
        if f[i] > thr[i] and f[i] >= f[i - k:i + k + 1].max():
            peaks.append(i)
    gap = int(min_gap_s * sr / hop)
    kept = []
    for i in peaks:
        if kept and i - kept[-1] < gap:
            if f[i] > f[kept[-1]]:
                kept[-1] = i
            continue
        kept.append(i)
    # refinement: where the 1 ms peak envelope crosses 25% of the way from the floor to the peak. The envelope is taken
    # above 1.5 kHz: in a compressed, distorted guitar the level hardly changes between strums, but the pick transient does.
    from scipy.signal import butter, sosfilt
    h = max(1, sr // 1000)
    a = np.abs(sosfilt(butter(4, 1500, 'hp', fs=sr, output='sos'), m)).astype(np.float32)
    n_env = len(a) // h
    env = a[:n_env * h].reshape(n_env, h).max(axis=1)
    from scipy.ndimage import maximum_filter1d
    wide = maximum_filter1d(env, size=9)   # bridges the dips of low notes (82 Hz: a dip every 6 ms)
    noise = float(np.percentile(env, 10)) + 1e-6
    out = []
    for i in kept:
        t_est = (i * hop + n_fft * 0.5) / sr
        c = int(t_est * sr / h)                    # envelope index (h samples each, not exactly 1 ms)
        lo, hi = max(0, c - 45), min(n_env, c + 25)
        if hi - lo < 10:
            continue
        seg = env[lo:hi]
        pk = int(np.argmax(seg))
        floor = float(env[max(0, lo - 30):lo + 5].min()) if lo > 0 else float(seg[:5].min())
        level = floor + 0.25 * (float(seg[pk]) - floor)
        below = np.nonzero(wide[lo:lo + pk + 1] < level)[0]   # the rise starts after the last point below the level
        j0 = int(below[-1]) + 1 if len(below) else 0
        j = j0 + int(np.argmax(seg[j0:pk + 1] >= level))
        c0 = lo + j
        # a strum makes the sound louder: the envelope right after must rise above the one right before
        # (a note dying away also changes the spectrum, but it never gets louder)
        # (compared on the smoothed envelope on both sides: a spiky envelope always "rises" from its mean to its max)
        before = float(np.median(wide[max(0, c0 - 30):max(1, c0 - 6)])) if c0 > 7 else noise
        after = float(np.median(wide[c0:min(n_env, c0 + 12)]))
        if after < 1.6 * before or after < 4 * noise:
            continue
        s0 = c0 * h
        fine = a[max(0, s0 - h):s0 + h]
        k2 = int(np.argmax(fine >= level)) if len(fine) else 0
        out.append((max(0, s0 - h) + k2) / sr)
    return np.array(out)


# ------------------------------------------------------------------ grid and matching
def grid_points(beats, beat_bar, sub):
    """Grid: every beat of the bars and its subdivisions. Returns (times, bar of each point, is_beat)."""
    b = np.asarray(beats, np.float64)
    bb = np.asarray(beat_bar, np.int32)
    sel = bb > 0
    b, bb = b[sel], bb[sel]
    if len(b) < 2:
        return np.zeros(0), np.zeros(0, np.int32), np.zeros(0, bool)
    t, bars, on = [], [], []
    for i in range(len(b)):
        d = (b[i + 1] - b[i]) if i + 1 < len(b) else (b[i] - b[i - 1])
        for k in range(sub):
            t.append(b[i] + k * d / sub); bars.append(bb[i]); on.append(k == 0)
    return np.array(t), np.array(bars, np.int32), np.array(on, bool)


def _nearest(sorted_ref, t):
    i = np.searchsorted(sorted_ref, t)
    i0 = np.clip(i - 1, 0, len(sorted_ref) - 1)
    i1 = np.clip(i, 0, len(sorted_ref) - 1)
    pick = np.where(np.abs(t - sorted_ref[i0]) <= np.abs(t - sorted_ref[i1]), i0, i1)
    return pick, t - sorted_ref[pick]


def _stats(dev_ms):
    if len(dev_ms) == 0:
        return None
    d = np.asarray(dev_ms, np.float64)
    med = float(np.median(d))
    return {'median_ms': round(med, 1), 'spread_ms': round(float(np.median(np.abs(d - med))) * 1.4826, 1),
            'within_20': round(float(np.mean(np.abs(d) <= 20)) * 100), 'within_40': round(float(np.mean(np.abs(d) <= 40)) * 100),
            'early_pct': round(float(np.mean(d < -10)) * 100), 'late_pct': round(float(np.mean(d > 10)) * 100), 'n': int(len(d))}


def analyze(take_onsets_song, grid, bars, reference_onsets=None, span=None):
    """take_onsets_song: attack times of the take, in song time. grid: (times, bar, is_beat) from grid_points.
    bars: bar start times. reference_onsets: attacks of the original part (song time) or None.
    span: (t0, t1) of the take in song time."""
    gt, gbar, _ = grid
    t = np.asarray(take_onsets_song, np.float64)
    out = {'version': VERSION, 'onsets': int(len(t))}
    if len(t) < 4 or len(gt) < 4:
        out['too_few'] = True
        return out
    step = np.median(np.diff(gt))
    pick, d = _nearest(gt, t)
    ok = np.abs(d) <= 0.4 * step
    out['grid'] = _stats(d[ok] * 1000)
    ref_ok, ref_d = None, None
    if reference_onsets is not None and len(reference_onsets) >= 4:
        r = np.asarray(reference_onsets, np.float64)
        if span:
            r = r[(r >= span[0] - 0.05) & (r <= span[1] + 0.05)]
        if len(r) >= 4:
            _, rd = _nearest(r, t)
            ref_ok = np.abs(rd) <= max(0.06, 0.35 * step)
            ref_d = rd
            if ref_ok.mean() >= 0.3:
                out['original'] = _stats(rd[ref_ok] * 1000)
                # bars where the original plays and the take is silent
                _, back = _nearest(np.sort(t), r)
                silent = np.abs(back) > max(0.1, 0.45 * step)
                Bn = np.asarray(bars)
                rbar = np.searchsorted(Bn, r, side='right')
                missed = {}
                for b_, s_ in zip(rbar, silent):
                    if 0 < b_ < len(Bn):
                        tot, miss = missed.get(int(b_), (0, 0))
                        missed[int(b_)] = (tot + 1, miss + int(s_))
                out['missed_bars'] = sorted(b_ for b_, (tot, miss) in missed.items() if tot >= 2 and miss / tot >= 0.8)
    use_ref = 'original' in out
    out['reference'] = 'original' if use_ref else 'grid'
    dev = (ref_d if use_ref else d) * 1000
    good = ref_ok if use_ref else ok
    Bn = np.asarray(bars)
    tbar = np.searchsorted(Bn, t, side='right')
    per_bar = {}
    for b_, dv, g_ in zip(tbar, dev, good):
        if g_ and 0 < b_ < len(Bn):
            per_bar.setdefault(int(b_), []).append(float(dv))
    rows = [{'bar': b_, 'n': len(v), 'median_ms': round(float(np.median(v)), 1)} for b_, v in sorted(per_bar.items())]
    out['bars'] = rows
    # bars to practice and streaks are measured against the player's own average: a take that is late by the same
    # amount everywhere has a "feel" (or latency) to fix, not bars to practice
    center = float(np.median(dev[good])) if np.any(good) else 0.0
    firm = [dict(r, rel=r['median_ms'] - center) for r in rows if r['n'] >= 3]
    out['worst_bars'] = [r['bar'] for r in sorted(firm, key=lambda r: -abs(r['rel']))[:5] if abs(r['rel']) > 15]
    streaks, run = [], []

    def close(run):
        if len(run) >= 2:
            streaks.append({'from_bar': run[0][0]['bar'], 'to_bar': run[-1][0]['bar'], 'kind': run[0][1],
                            'median_ms': round(float(np.median([x[0]['rel'] for x in run])), 1)})

    for r in firm:
        kind = 'late' if r['rel'] > 15 else 'early' if r['rel'] < -15 else None
        if run and (kind != run[-1][1] or r['bar'] != run[-1][0]['bar'] + 1):
            close(run)
            run = []
        if kind:
            run.append((r, kind))
    close(run)
    out['streaks'] = streaks
    return out


# ------------------------------------------------------------------ original part onsets, cached
def cache_dir():
    base = os.environ.get('RIFFROOM_DATA') or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Riffroom')
    d = os.path.join(base, 'cache')
    os.makedirs(d, exist_ok=True)
    return d


def part_onsets(song, pid):
    """Attacks of an original part of the song (song time), computed once and cached by file and date."""
    p = next((x for x in song.m['parts'] if x['id'] == pid), None)
    if p is None:
        return None
    path = os.path.join(song.folder, p['file'])
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f'{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{VERSION}'.encode()).hexdigest()[:16]
    cached = os.path.join(cache_dir(), f'onsets_{key}.json')
    if os.path.exists(cached):
        with open(cached, encoding='utf-8') as f:
            return np.array(json.load(f))
    data = song.part(pid)['data']
    x = data.astype(np.float32).mean(axis=1) / 32768
    on = onsets(x, song.sr)
    with open(cached, 'w', encoding='utf-8') as f:
        json.dump([round(float(v), 4) for v in on], f)
    return on


def analyze_take(song, take_rec, audio, calibrated=True):
    """Full analysis of a saved take (dict ready to be stored in the take record and sent to the screen or the AI)."""
    import song as song_mod
    start = float(take_rec.get('start_s', 0.0)) + float(take_rec.get('offset_ms', 0.0)) / 1000
    on = onsets(audio, SR) + start
    sub = int(song.m.get('subdivisions') or song_mod.subdivisions(song.m.get('time_signature', '4/4')))
    grid = grid_points(song.beats, song.beat_bar, sub)
    ref = part_onsets(song, take_rec.get('part')) if take_rec.get('part') in [p['id'] for p in song.parts] else None
    span = (start, start + len(audio) / SR)
    res = analyze(on, grid, song.m.get('bars', []), ref, span)
    res.update({'take': take_rec['id'], 'part': take_rec.get('part'), 'offset_ms': take_rec.get('offset_ms', 0.0),
                'calibrated': bool(calibrated), 'span': [round(span[0], 3), round(span[1], 3)]})
    return res
