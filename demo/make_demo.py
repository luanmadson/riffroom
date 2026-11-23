# SPDX-License-Identifier: GPL-3.0-or-later
"""Makes the Riffroom demo song: a slow blues in A (12/8, 72 beats per minute) with drums, bass, rhythm guitar and lead
guitar as separate tracks. Everything is synthesized here (no one's recording), and then the song is prepared
(riffroom.json) with bars, chords and sections, like a real song. It goes to <music folder>\\Demo.

Usage: python -I -X utf8 make_demo.py [music folder] [--choruses 2]
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import prepare as PR   # noqa: E402
import song as SG      # noqa: E402

SR = 44100
BPM = 72.0                       # beats (dotted quarter notes) per minute
BEAT = 60.0 / BPM
TRIPLET = BEAT / 3
BAR = 4 * BEAT
LEAD_IN = 0.5                    # first click of the count-in
ONE = LEAD_IN + 4 * BEAT         # start of bar 1
TITLE, ARTIST = 'Demo – Slow Blues in A', 'Riffroom'
CHORUS = ['A5', 'A5', 'A5', 'A5', 'D5', 'D5', 'A5', 'A5', 'E5', 'D5', 'A5', 'E5']   # the 12 bars of the blues
RHYTHM_ROOT = {'A5': 45, 'D5': 50, 'E5': 40}       # A2, D3, E2 (MIDI)
BASS_ROOT = {'A5': 33, 'D5': 38, 'E5': 40}
# lead phrases (A minor pentatonic): (bar of the chorus, beat, triplet, MIDI note, length in triplets)
LEAD = [
    (1, 1, 1, 69, 2), (1, 1, 3, 72, 1), (1, 2, 1, 74, 3), (1, 3, 1, 72, 2), (1, 3, 3, 69, 1), (1, 4, 1, 69, 3),
    (2, 2, 1, 76, 2), (2, 2, 3, 74, 1), (2, 3, 1, 72, 2), (2, 3, 3, 69, 1), (2, 4, 1, 67, 2), (2, 4, 3, 69, 1),
    (3, 1, 1, 69, 6), (3, 3, 1, 72, 1), (3, 3, 2, 74, 1), (3, 3, 3, 76, 1), (3, 4, 1, 79, 3),
    (4, 1, 1, 81, 6), (4, 4, 1, 76, 1), (4, 4, 2, 74, 1), (4, 4, 3, 72, 1),
    (5, 1, 1, 74, 3), (5, 2, 1, 72, 2), (5, 2, 3, 69, 1), (5, 3, 1, 74, 2), (5, 3, 3, 76, 1), (5, 4, 1, 74, 3),
    (6, 1, 1, 69, 3), (6, 2, 1, 72, 2), (6, 2, 3, 74, 1), (6, 3, 1, 75, 1), (6, 3, 2, 76, 2), (6, 4, 1, 79, 3),
    (7, 1, 1, 81, 9), (7, 4, 1, 76, 1), (7, 4, 2, 72, 1), (7, 4, 3, 69, 1),
    (8, 1, 1, 72, 2), (8, 1, 3, 69, 1), (8, 2, 1, 67, 2), (8, 2, 3, 64, 1), (8, 3, 1, 69, 6),
    (9, 1, 1, 76, 3), (9, 2, 1, 74, 2), (9, 2, 3, 71, 1), (9, 3, 1, 76, 3), (9, 4, 1, 79, 3),
    (10, 1, 1, 81, 3), (10, 2, 1, 79, 2), (10, 2, 3, 76, 1), (10, 3, 1, 74, 3), (10, 4, 1, 72, 2), (10, 4, 3, 69, 1),
    (11, 1, 1, 69, 6), (11, 3, 1, 72, 1), (11, 3, 2, 73, 1), (11, 3, 3, 76, 1), (11, 4, 1, 81, 3),
    (12, 1, 1, 68, 3), (12, 2, 1, 71, 3), (12, 3, 1, 76, 3),
]


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def filt(x, f, kind='low', order=4):
    return sosfilt(butter(order, f, kind, fs=SR, output='sos'), x, axis=0)


def string(f, dur, decay=0.996, bright=0.6, rng=None):
    """Plucked string (Karplus-Strong), computed one period at a time."""
    N = max(2, int(round(SR / f - 0.5)))
    n = int(dur * SR)
    ex = rng.uniform(-1, 1, N)
    ex = bright * ex + (1 - bright) * np.convolve(ex, np.ones(4) / 4, mode='same')
    y = np.zeros(n + N + 1)
    y[1:N + 1] = ex
    i = N + 1
    while i < len(y):
        k = min(N, len(y) - i)
        y[i:i + k] = decay * 0.5 * (y[i - N:i - N + k] + y[i - N - 1:i - N - 1 + k])
        i += k
    return y[1:n + 1]


def damp(x, ms=18):
    k = min(len(x), int(SR * ms / 1000))
    if k:
        x[-k:] *= np.linspace(1, 0, k)
    return x


def put(track, t, x, gain=1.0, pan=(1.0, 1.0)):
    i = int(round(t * SR))
    if i < 0 or i >= len(track):
        return
    k = min(len(x), len(track) - i)
    track[i:i + k, 0] += x[:k] * gain * pan[0]
    track[i:i + k, 1] += x[:k] * gain * pan[1]


def env(L, tau):
    return np.exp(-np.arange(L) / (tau * SR))


def kick(rng):
    L = int(0.4 * SR); t = np.arange(L) / SR
    f = 48 + 75 * np.exp(-t / 0.035)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(L, 0.16)
    x[:120] += rng.uniform(-0.4, 0.4, 120) * np.linspace(1, 0, 120)
    return x


def snare(rng):
    L = int(0.35 * SR); t = np.arange(L) / SR
    noise = filt(filt(rng.uniform(-1, 1, L), 1400, 'high'), 7500) * env(L, 0.085)
    body = np.sin(2 * np.pi * 186 * t) * env(L, 0.05)
    return 0.9 * noise / np.abs(noise).max() + 0.45 * body


def hihat(rng):
    L = int(0.09 * SR)
    x = filt(rng.uniform(-1, 1, L), 7000, 'high') * env(L, 0.022)
    return x / np.abs(x).max()


def crash(rng):
    L = int(2.8 * SR)
    x = filt(rng.uniform(-1, 1, L), 3800, 'high') * env(L, 0.8)
    return x / np.abs(x).max()


def sticks(rng):
    L = int(0.06 * SR); t = np.arange(L) / SR
    x = filt(filt(rng.uniform(-1, 1, L), 2000, 'high'), 6000) * env(L, 0.007) + 0.5 * np.sin(2 * np.pi * 2400 * t) * env(L, 0.012)
    return x / np.abs(x).max()


def lead_note(midi, dur, vibrato):
    """Additive synthesis with vibrato on long notes (drive and cabinet come later)."""
    L = int((dur + 0.12) * SR); t = np.arange(L) / SR
    f0 = hz(midi)
    depth = 0.012 * np.clip((t - 0.25) / 0.35, 0, 1) if vibrato else 0.0
    f = f0 * (1 + depth * np.sin(2 * np.pi * 5.6 * t))
    phase = 2 * np.pi * np.cumsum(f) / SR
    K = max(1, int(5200 / f0))
    x = sum(np.sin(k * phase) / k for k in range(1, K + 1))
    a = np.minimum(1, t / 0.008) * np.exp(-t / (1.6 if vibrato else 0.9))
    release = np.clip((dur + 0.1 - t) / 0.1, 0, 1)
    return x * a * release


def make(dest, choruses=2, log=print):
    """Writes the tracks and the chart into <dest>\\Demo and prepares the song. Returns the song folder."""
    rng = np.random.default_rng(1979)
    chords = CHORUS * choruses + ['A5', 'A5']               # plus two bars of ending
    nb = len(chords)
    total = ONE + nb * BAR + 2.5
    n = int(total * SR)
    drums, bass, rhythm, lead = (np.zeros((n, 2)) for _ in range(4))
    swing = lambda: rng.normal(0, 0.004)                   # a bit of "human hands" in the timing
    vel = lambda: 1 + rng.normal(0, 0.06)

    for k in range(4):                                     # count-in with the sticks
        put(drums, LEAD_IN + k * BEAT, sticks(rng), 0.55 if k else 0.7)
    for b, chord in enumerate(chords):
        t0 = ONE + b * BAR
        last = b == nb - 1
        # drums (shuffle): hi-hat on the 1st and 3rd triplet; kick on 1 and 3; snare on 2 and 4
        if b % 12 == 0 or last:
            put(drums, t0, crash(rng), 0.35)
        for p in range(4):
            tp = t0 + p * BEAT
            if last and p > 0:
                break
            put(drums, tp + swing(), hihat(rng), 0.22 * vel(), (0.8, 1.0))
            if not last:
                put(drums, tp + 2 * TRIPLET + swing(), hihat(rng), 0.14 * vel(), (0.8, 1.0))
            if p in (0, 2):
                put(drums, tp + swing() * 0.5, kick(rng), 0.9 * vel())
            if p == 2 and not last:
                put(drums, tp + 2 * TRIPLET + swing(), kick(rng), 0.55 * vel())
            if p in (1, 3) and not last:
                put(drums, tp + swing() * 0.5, snare(rng), 0.62 * vel())
            if p == 3 and not last and b % 4 == 3:
                put(drums, tp + 2 * TRIPLET + swing(), snare(rng), 0.22 * vel())
        # bass: the boogie R, 3, 5, 6, b7, 6, 5, 3 on the shuffle
        r = BASS_ROOT[chord]
        for k, iv in enumerate([0, 4, 7, 9, 10, 9, 7, 4]):
            if last and k > 0:
                break
            p, short = divmod(k, 2)
            tk = t0 + p * BEAT + (2 * TRIPLET if short else 0) + swing()
            dur = 3.0 if last else (TRIPLET * 0.95 if short else 2 * TRIPLET * 0.95)
            put(bass, tk, damp(string(hz(r + iv), dur, 0.997, 0.35, rng)), 0.75 * vel())
        # rhythm guitar: fifth and sixth in turns (A5, A6, A5, A6), palm muted
        rr = RHYTHM_ROOT[chord]
        for p in range(4):
            for short in (0, 1):
                if last and (p > 0 or short):
                    break
                tk = t0 + p * BEAT + (2 * TRIPLET if short else 0) + swing()
                dur = 3.0 if last else (TRIPLET * 0.9 if short else 2 * TRIPLET * 0.9)
                second = rr + (9 if p % 2 else 7)
                for j, note in enumerate((rr, second) + ((rr + 12,) if last else ())):
                    x = damp(string(hz(note), dur, 0.998 if last else 0.985, 0.8, rng))
                    put(rhythm, tk + j * 0.006, x, 0.5 * vel(), (1.0, 0.55))
    if choruses >= 2:                                      # lead in the second chorus
        for (c, p, tr, note, nt) in LEAD:
            tk = ONE + (12 + c - 1) * BAR + (p - 1) * BEAT + (tr - 1) * TRIPLET + swing()
            dur = nt * TRIPLET * 0.96
            put(lead, tk, lead_note(note, dur, nt >= 3), 0.30 * vel(), (0.6, 1.0))

    # tones: drive and guitar cabinet on the guitars, a bit of low end on the bass
    rhythm = filt(filt(np.tanh(2.8 * rhythm) / np.tanh(2.8), 3300), 90, 'high')
    lead = filt(filt(np.tanh(3.5 * lead) / np.tanh(3.5), 4800), 160, 'high')
    bass = filt(bass, 1800)
    tracks = {'drums': drums * 0.55, 'bass': bass * 0.9, 'rhythm_guitar': rhythm * 0.5, 'lead_guitar': lead * 0.24}
    peak = np.abs(sum(tracks.values())).max()
    k = 0.85 / peak
    folder = os.path.join(dest, 'Demo')
    os.makedirs(folder, exist_ok=True)
    for name, x in tracks.items():
        sf.write(os.path.join(folder, name + '.wav'), np.clip(x * k, -1, 1).astype(np.float32), SR, subtype='PCM_16')
    lines = ['# Chord chart of the demo song: each word is a bar (12/8: 4 beats, each split in 3 triplets).',
             "# '%' repeats the previous bar; [Name] marks the start of a section of the song.", '']
    for v in range(choruses):
        lines.append('[First chorus]' if v == 0 else '[Solo]' if v == 1 else f'[Chorus {v + 1}]')
        lines += ['A5 % % %', 'D5 % A5 %', 'E5 D5 A5 E5']
    lines += ['[Ending]', 'A5 A5', '']
    with open(os.path.join(folder, 'chords.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    PR.prepare(folder, title=TITLE, artist=ARTIST, album='', tonic='A', bpm=BPM, time_signature='12/8',
               first_bar=round(ONE, 4), targets='rhythm,lead', count_in_in_drums=True, source='demo', log=log)
    return folder


def main():
    ap = argparse.ArgumentParser(description='Makes the demo song (a blues in A) and prepares it for Riffroom.')
    ap.add_argument('folder', nargs='?', help='music folder (default: <Music>\\Riffroom)')
    ap.add_argument('--choruses', type=int, default=2, help='how many times the 12 bars (2: the second has a solo)')
    a = ap.parse_args()
    dest = a.folder or SG.default_library()
    print('making the demo song...')
    folder = make(dest, a.choruses)
    print('ready:', folder)


if __name__ == '__main__':
    main()
