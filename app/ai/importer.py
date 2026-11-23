# SPDX-License-Identifier: GPL-3.0-or-later
"""From an audio file to a practice song, on the user's computer:
read -> separate (Demucs) -> beats and bars (beat-this) -> silence before bar 1 for the count-in -> chord suggestions ->
write the tracks, the chart (chords.txt) and riffroom.json (prepare.py)."""
import os
import re
import time

import numpy as np
import soundfile as sf

import prepare as PR
import takes as TK
from ai import beats as BT
from ai import chord_detect as CD
from ai import separate as SP

SR = 44100
HARMONIC = ('guitar', 'piano', 'other')   # without the bass: a walking bass line spends more time on the 5th than on the root


def _tags(path):
    title = artist = None
    if path.lower().endswith('.mp3'):
        tags = TK.read_id3(path)
        title, artist = tags.get('TIT2'), tags.get('TPE1')
    else:
        try:
            with sf.SoundFile(path) as f:
                title, artist = (getattr(f, 'title', None) or None), (getattr(f, 'artist', None) or None)
        except Exception:
            pass
    if not title:
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r'\s*(.+?)\s+-\s+(.+)$', base)
        if m and not artist:
            artist, title = m.group(1), m.group(2)
        else:
            title = base
    return title.strip(), (artist or '').strip()


def _rms_db(x):
    return 20 * np.log10(max(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))), 1e-9))


def import_song(path, library, title=None, artist=None, tuning=0, time_signature=None, model='htdemucs_6s',
                progress=None, log=print):
    t_start = time.time()
    say = progress or (lambda frac, key, **kw: None)
    say(0.01, 'reading')
    try:
        audio, sr = sf.read(path, dtype='float32', always_2d=True)
    except Exception as ex:
        raise ValueError(f'could not read the audio ({ex}); convert it to WAV, MP3 or FLAC') from ex
    if sr != SR:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(sr), SR)
        audio = resample_poly(audio, SR // g, int(sr) // g, axis=0).astype(np.float32)
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    audio = audio[:, :2]
    t_title, t_artist = _tags(path)
    title, artist = (title or t_title), (artist if artist is not None else t_artist)
    folder = os.path.join(library, TK.safe_name(artist) or 'Imported', TK.safe_name(title))
    base_folder, k = folder, 2
    while os.path.exists(os.path.join(folder, 'riffroom.json')):
        folder = f'{base_folder} ({k})'; k += 1
    os.makedirs(folder, exist_ok=True)
    log(f'importing "{title}" by "{artist}" into {folder}')

    say(0.03, 'loading_model')
    stems, msr = SP.separate(audio, SR, model, progress=lambda f: say(0.05 + 0.75 * f, 'separating', pct=int(f * 100)), log=log)
    t_sep = time.time() - t_start
    n = len(audio)
    for name in stems:
        stems[name] = stems[name][:n] if len(stems[name]) >= n else np.pad(stems[name], ((0, n - len(stems[name])), (0, 0)))

    say(0.82, 'beats')
    mono = audio.mean(axis=1)
    beats, downbeats = BT.track(mono, SR)
    dur = n / SR

    say(0.86, 'chords')
    harm = sum(stems[s] for s in (HARMONIC if any(s in stems for s in HARMONIC) else ('bass',)) if s in stems).mean(axis=1)
    bass = stems['bass'].mean(axis=1) if 'bass' in stems else None
    segs = CD.detect(harm, SR, beats, bass=bass, tuning=int(tuning or 0))
    info = BT.bars_from(beats, downbeats, dur, segs)      # the chords help to tell 12/8 from 6/8
    from analysis import onsets
    drum_on = onsets(stems['drums'].mean(axis=1), SR) if 'drums' in stems else onsets(mono, SR)
    sub = BT.feel(drum_on, beats)
    ts = time_signature or BT.time_signature(info['numerator'], sub)

    # silence before bar 1, so the 4-beat count-in fits before the music
    first = info['bars'][0]
    pad_s = max(0.0, round(4 * info['beat_s'] + 0.3 - first, 3))
    if pad_s > 0:
        pad = np.zeros((int(round(pad_s * SR)), 2), np.float32)
        audio = np.concatenate([pad, audio])
        for name in stems:
            stems[name] = np.concatenate([pad, stems[name]])
        beats = beats + pad_s
        info['bars'] = [round(v + pad_s, 4) for v in info['bars']]
        segs = [(t0 + pad_s, t1 + pad_s, c) for t0, t1, c in segs]
    bar1 = info['bars'][0]
    count_in = [round(bar1 - k_ * info['beat_s'], 4) for k_ in range(4, 0, -1)]
    lines = CD.chart(segs, info['bars'], info['beats_per_bar'])
    tonic = CD.tonic(segs) or 'A'

    say(0.93, 'saving_stems')
    kept = []
    for name, x in stems.items():
        if _rms_db(x) < -50:
            log(f'  {name}: almost silent, left out')
            continue
        sf.write(os.path.join(folder, f'{name}.wav'), np.clip(x, -1, 1), SR, subtype='PCM_16')
        kept.append(name)
    sf.write(os.path.join(folder, 'mix.flac'), np.clip(audio, -1, 1), SR, subtype='PCM_16')
    header = ['# Chords suggested by the Riffroom AI. Listen and correct: one bar per word, "%" repeats the previous bar,',
              '# "A5,D5" splits the bar, "-" is a bar with no chord, [Name] starts a section. Then reopen the song.',
              f'# tempo ~{info["bpm"]} beats per minute, {ts}' + (f', guitar tuned {tuning:+d} semitone(s)' if tuning else ''), '']
    with open(os.path.join(folder, 'chords.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(header + lines) + '\n')

    say(0.96, 'writing')
    targets = [p for p in ('guitar', 'bass', 'vocals', 'piano', 'other') if p in kept] or None
    PR.prepare(folder, title=title, artist=artist, album='', tonic=tonic, bpm=info['bpm'], time_signature=ts,
               tuning=int(tuning or 0), bars=info['bars'], bpb=info['beats_per_bar'], count_in=count_in,
               count_in_in_drums=False, source='ai', targets=targets, log=log)
    say(1.0, 'done')
    return {'folder': folder, 'title': title, 'artist': artist, 'bpm': info['bpm'], 'time_signature': ts,
            'bars': len(info['bars']) - 1, 'stems': kept, 'tonic': tonic, 'pre_roll_s': pad_s,
            'separation_s': round(t_sep, 1), 'total_s': round(time.time() - t_start, 1)}
