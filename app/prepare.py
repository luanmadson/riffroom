# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepares a song for Riffroom: writes riffroom.json with
  - the band parts (separated tracks) and the volume of each one, so that their sum sounds like the record;
  - optional: bars and beats (from the BPM or from the AI), the count-in, the chords with degree and fingering,
    and the song sections.
Hand-written fields (title, artist, tonic, sections, BPM...) are kept when it runs again.

Usage: python -I -X utf8 prepare.py "<song folder>" [--title T] [--artist A] [--tonic A] [--tuning -1]
         [--bpm 72 --time-signature 12/8 --first-bar 3.33] [--chords chords.txt] [--sections "1=Intro;5=Verse"]
         [--targets rhythm,lead] [--part id=file.wav ...] [--mix mix.wav]

Tracks are recognized by file name (wav, flac, mp3, ogg), like the ones Demucs writes:
  drums, bass, guitar, rhythm_guitar, lead_guitar, piano, vocals, other (also in Portuguese and Spanish);
  mix/mixture/original (the whole song, optional: used to set the volume of each part).
"""
import argparse
import glob
import json
import os
import re
import sys
import unicodedata

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import chords as CH  # noqa: E402
import legacy        # noqa: E402

MANIFEST = 'riffroom.json'
# id, default name, accepted file names (no extension; lower case, no accents, "_" for spaces and hyphens)
PARTS = [
    ('drums', 'Drums', ('drums', 'drum', 'bateria')),
    ('bass', 'Bass', ('bass', 'baixo', 'bajo')),
    ('rhythm', 'Rhythm guitar', ('rhythm_guitar', 'guitarra_base', 'guitarra_ritmica', 'rhythm', 'base', 'ritmica')),
    ('lead', 'Lead guitar', ('lead_guitar', 'guitarra_solo', 'guitarra_solista', 'lead', 'solo', 'solista')),
    ('guitar', 'Guitar', ('guitar', 'guitars', 'guitarra', 'guitarras')),
    ('piano', 'Piano', ('piano', 'keys', 'keyboard', 'teclado', 'teclados')),
    ('vocals', 'Vocals', ('vocals', 'vocal', 'voice', 'voz', 'vozes', 'voces')),
    ('other', 'Other instruments', ('other', 'others', 'outros', 'otros')),
]
DEFAULT_NAMES = {pid: name for pid, name, _ in PARTS}
MIX_NAMES = ('original_com_contagem', 'original', 'mixture', 'mix', 'full', 'song', 'completa', 'musica', 'cancion')
WORDS = {w for _, _, names in PARTS for n in names for w in n.split('_')}
AUDIO_EXT = ('.wav', '.flac', '.mp3', '.ogg', '.aiff', '.aif')
METERS = {'12/8': 4, '9/8': 3, '6/8': 2, '4/4': 4, '3/4': 3, '2/4': 2, '5/4': 5, '2/2': 2}
CHART_FILES = ('chords.txt', 'acordes.txt')


def _tokens(name):
    s = unicodedata.normalize('NFKD', os.path.splitext(name)[0]).encode('ascii', 'ignore').decode().lower()
    return [t for t in re.split(r'[\s_\-.()\[\]]+', s) if t]


def _matches(tokens, key):
    """Does the name end with the key (e.g. 'NightProwler_guitarra_base_isolada' -> 'guitarra base')?"""
    while tokens and tokens[-1] in ('isolada', 'isolado', 'aislada', 'aislado', 'stem', 'faixa', 'track', 'isolated'):
        tokens = tokens[:-1]
    k = key.split('_')
    if len(tokens) < len(k) or tokens[-len(k):] != k:
        return False
    before = tokens[:-len(k)]
    if any(t in ('sem', 'sin', 'no', 'without', 'minus', 'karaoke', 'instrumental') for t in before):
        return False
    return not (before and before[-1] in WORDS)   # "baixo_bateria" is both mixed together, not the drums


def find_parts_by_name(folder, files=None):
    """{part id: path} from the names of the audio files in the folder."""
    files = files if files is not None else os.listdir(folder)
    found = {}
    for pid, _, keys in PARTS:
        for key in sorted(keys, key=lambda c: -len(c.split('_'))):
            cands = [a for a in files if a.lower().endswith(AUDIO_EXT) and _matches(_tokens(a), key)]
            cands = [a for a in cands if os.path.join(folder, a) not in found.values()]
            if cands:
                found[pid] = os.path.join(folder, sorted(cands, key=len)[0])
                break
    return found


def find_mix(folder, files=None):
    files = files if files is not None else os.listdir(folder)
    for key in MIX_NAMES:
        cands = [a for a in files if a.lower().endswith(AUDIO_EXT) and _matches(_tokens(a), key)]
        if cands:
            return os.path.join(folder, sorted(cands, key=len)[0])
    return None


# ------------------------------------------------------------------ the original "Música" project layout (Faixas/ and Dados/)
PROJECT_SUFFIXES = [('drums', '_bateria.wav'), ('bass', '_baixo_isolado.wav'), ('rhythm', '_guitarra_base_isolada.wav'),
                    ('lead', '_guitarra_solo_isolada.wav'), ('vocals', '_voz_isolada.wav')]


def _project(folder):
    """Folders laid out as Faixas/Completas (WAV)/<Prefix>_original_com_contagem.wav plus the isolated parts."""
    wav = os.path.join(folder, 'Faixas', 'Completas (WAV)')
    mix = glob.glob(os.path.join(wav, '*_original_com_contagem.wav'))
    if len(mix) != 1:
        return None
    prefix = os.path.basename(mix[0])[:-len('_original_com_contagem.wav')]
    parts = {pid: os.path.join(wav, prefix + end) for pid, end in PROJECT_SUFFIXES
             if os.path.exists(os.path.join(wav, prefix + end))}
    return {'mix': mix[0], 'parts': parts, 'data': os.path.join(folder, 'Dados')}


def read_report(data_dir):
    """Count-in and soft-hit times, and the beat, from the processing report of the drum track."""
    out = {}
    for p in sorted(glob.glob(os.path.join(data_dir, 'relatorio_processamento_*.txt'))):
        t = open(p, encoding='utf-8').read()
        m = re.search(r'contagem \(tempo do arquivo final\): ([\d., ]+)', t)
        if m and 'count_in' not in out:
            out['count_in'] = [float(v) for v in m.group(1).split(',')]
        m = re.search(r'toques leves: \d+ -> ([\d., ]+)', t)
        if m and 'soft_hits' not in out:
            out['soft_hits'] = [float(v) for v in m.group(1).split(',')]
        m = re.search(r'pulso = ([\d.]+) s \(~([\d.]+) BPM\)', t)
        if m and 'bpm' not in out:
            out['bpm'] = float(m.group(2))
    return out


# ------------------------------------------------------------------ volumes
def balance(files, mix, sr):
    """Volume of each part so that their sum sounds like the record (least squares against the whole song) and a common
    scale that puts the peak of the sum at -1 dBFS. Without the whole song, all at 1 (Demucs keeps the volume)."""
    S = []
    for a in files:
        x, sr2 = sf.read(a, dtype='float32', always_2d=True)
        if sr2 != sr:
            raise ValueError(f'{os.path.basename(a)}: {sr2} Hz; all tracks need the same sample rate ({sr} Hz)')
        S.append(x[:, :2] if x.shape[1] >= 2 else np.repeat(x, 2, axis=1))
    residual_db, n_mix = None, 0
    if mix:
        y_mix, sr2 = sf.read(mix, dtype='float32', always_2d=True)
        if sr2 != sr:
            raise ValueError(f'{os.path.basename(mix)}: {sr2} Hz (the parts are at {sr} Hz)')
        y_mix = y_mix[:, :2] if y_mix.shape[1] >= 2 else np.repeat(y_mix, 2, axis=1)
        n_mix = len(y_mix)
        n = min([len(y_mix)] + [len(x) for x in S])
        if n > 120 * sr:
            a0, b0 = int(30 * sr), min(int(355 * sr), n - int(5 * sr))
        else:
            a0, b0 = int(0.1 * n), int(0.9 * n)
        st = 7
        A = np.stack([np.concatenate([x[a0:b0:st, 0], x[a0:b0:st, 1]]) for x in S], axis=1).astype(np.float64)
        y = np.concatenate([y_mix[a0:b0:st, 0], y_mix[a0:b0:st, 1]]).astype(np.float64)
        g, *_ = np.linalg.lstsq(A, y, rcond=None)
        res = y - A @ g
        residual_db = float(-10 * np.log10(np.mean(y ** 2) / max(np.mean(res ** 2), 1e-20)))
        g = np.maximum(g, 0.05)  # an almost empty part must not get a negative gain
    else:
        g = np.ones(len(S))
    L = max(len(x) for x in S)
    total = np.zeros((L, 2), np.float32)
    for x, gi in zip(S, g):
        total[:len(x)] += x * np.float32(gi)
    k = 0.891 / max(float(np.max(np.abs(total))), 1e-6)
    return [float(gi * k) for gi in g], residual_db, float(k), max(L, n_mix)


# ------------------------------------------------------------------ bars, chords and song sections
def beats_per_bar(time_signature):
    if time_signature in METERS:
        return METERS[time_signature]
    n, d = (int(v) for v in time_signature.split('/'))
    return n // 3 if d == 8 and n % 3 == 0 else n


def grid(bpm, time_signature, first_bar, duration):
    """Equal bars from `first_bar` (s), at `bpm` beats per minute."""
    bpb = beats_per_bar(time_signature)
    L = bpb * 60.0 / bpm
    B = [first_bar]
    while B[-1] < duration - 0.05:
        B.append(first_bar + len(B) * L)
    return [round(v, 4) for v in B], [bpb] * (len(B) - 1)


def read_chart(text):
    """Simple chord chart, one bar per word:
         [Intro]
         A5 A5 D5 A5        (each word is a bar; '|' may separate them)
         [Verse]
         A5 % % %           ('%' repeats the previous bar)
         E5 D5,C5 A5 -      ('D5,C5' splits the bar; '-' is a bar with no chord)
       Lines starting with '#' are comments. Returns (bars, sections): [[chords]], [{'bar', 'name'}]."""
    bars, sections = [], []
    for line in text.splitlines():
        if line.strip().startswith('#'):
            continue
        for tok in re.findall(r'\[[^\]]*\]|[^\s|]+', line):
            if tok.startswith('['):
                sections.append({'bar': len(bars) + 1, 'name': tok[1:-1].strip()})
            elif tok == '%':
                if not bars:
                    raise ValueError("'%' in the first bar: there is no previous bar to repeat")
                bars.append(list(bars[-1]))
            else:
                chs = [None if c in ('-', 'N.C.', 'NC') else c for c in tok.split(',')]
                for c in chs:
                    if c is not None and not CH.is_valid(c):
                        raise ValueError(f'unknown chord: "{c}"')
                bars.append(chs)
    return bars, sections


def chords_from_chart(chart_bars, B, bpb, tonic):
    """Chord segments (t0, t1, bar, beat) from the chart; equal chords in a row become one segment."""
    out = []
    for i, chs in enumerate(chart_bars):
        if i + 1 >= len(B):
            break
        t0, t1, n = B[i], B[i + 1], len(chs)
        for j, c in enumerate(chs):
            a0, a1 = t0 + (t1 - t0) * j / n, t0 + (t1 - t0) * (j + 1) / n
            if out and out[-1]['chord'] == c and abs(out[-1]['t1'] - a0) < 1e-6:
                out[-1]['t1'] = round(a1, 4)
                continue
            item = {'t0': round(a0, 4), 't1': round(a1, 4), 'bar': i + 1, 'beat': int(j * bpb[i] / n) + 1, 'chord': c}
            if c:
                item['degree'] = CH.degree(c, tonic)
            out.append(item)
    return out


def _sections(text):
    out = []
    for pair in text.split(';'):
        if pair.strip():
            bar, name = pair.split('=', 1)
            out.append({'bar': int(bar), 'name': name.strip()})
    return out


def _read_old(path):
    with open(path, encoding='utf-8') as f:
        m = json.load(f)
    return legacy.manifest(m) if legacy.is_old_manifest(m) else m


# ------------------------------------------------------------------ main
def prepare(folder, title=None, artist=None, album=None, tonic=None, sections=None, bpm=None, time_signature=None,
            first_bar=None, chords=None, targets=None, parts=None, mix=None, tuning=None, bars=None, bpb=None,
            count_in=None, count_in_in_drums=None, source=None, log=print):
    """Writes the song's riffroom.json and returns its contents."""
    folder = os.path.abspath(folder)
    proj = _project(folder)
    dest = os.path.join(folder, 'Dados', MANIFEST) if proj else os.path.join(folder, MANIFEST)
    old = {}
    for cand in (dest, os.path.join(os.path.dirname(dest), 'ensaio.json')):
        if os.path.exists(cand):
            old = _read_old(cand)
            break

    # tracks
    if parts:
        found = {}
        for pair in parts:
            pid, f = pair.split('=', 1)
            found[legacy.part_id(pid.strip())] = os.path.join(folder, f.strip())
        mix_file = os.path.join(folder, mix) if mix else find_mix(folder)
    elif proj:
        found, mix_file = proj['parts'], proj['mix']
    else:
        found, mix_file = {}, None
        for sub in [folder] + sorted(d for d in glob.glob(os.path.join(folder, '*')) if os.path.isdir(d)
                                     and os.path.basename(d) not in ('Gravações', 'Dados', 'Takes', 'Grabaciones')):
            found = find_parts_by_name(sub)
            if len(found) >= 2:
                mix_file = os.path.join(folder, mix) if mix else find_mix(sub)
                break
        if len(found) < 2:
            raise ValueError('separated tracks not found. The files must be named after the parts: '
                             'drums, bass, guitar, vocals, other, piano... (or bateria, baixo, guitarra, voz...)')
    order = [pid for pid, _, _ in PARTS]
    items = sorted(found.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    for pid, p in items:
        if not os.path.exists(p):
            raise FileNotFoundError(p)
    sr = sf.info(items[0][1]).samplerate
    for pid, p in items:
        i = sf.info(p)
        if i.samplerate != sr or i.channels not in (1, 2):
            raise ValueError(f'{os.path.basename(p)}: {i.samplerate} Hz and {i.channels} channels '
                             f'(the tracks need the same sample rate, {sr} Hz, and 1 or 2 channels)')

    log('setting the volume of each part...')
    gains, residual_db, scale, n = balance([p for _, p in items], mix_file, sr)
    if residual_db is not None:
        log(f'  what the separation missed: {residual_db:.1f} dB below the song')
    dur = n / sr

    old_names = {p['id']: p.get('name') for p in old.get('parts', [])}
    out = {
        'version': 2,
        'source': source or old.get('source') or ('project' if proj else 'manual'),
        'title': title or old.get('title') or os.path.basename(folder),
        'artist': artist or old.get('artist') or (os.path.basename(os.path.dirname(os.path.dirname(folder)))
                                                  if proj else os.path.basename(os.path.dirname(folder))),
        'album': album if album is not None else (old.get('album') or (os.path.basename(os.path.dirname(folder)) if proj else '')),
        'time_signature': time_signature or old.get('time_signature') or ('12/8' if proj else '4/4'),
        'bpm': bpm or old.get('bpm'),
        'tuning': int(tuning if tuning is not None else old.get('tuning') or 0),
        'sample_rate': sr,
        'duration_s': round(dur, 4),
        'mix': os.path.relpath(mix_file, folder).replace('\\', '/') if mix_file else None,
        'parts': [{'id': pid, 'name': old_names.get(pid) or DEFAULT_NAMES.get(pid, pid.capitalize()),
                   'file': os.path.relpath(p, folder).replace('\\', '/'), 'gain': round(g, 6),
                   'gain_db': round(20 * np.log10(g), 2)} for (pid, p), g in zip(items, gains)],
        'balance': {'residual_db': round(residual_db, 1) if residual_db is not None else None, 'scale': round(scale, 4),
                    'method': ('least squares of the parts against the whole song; sum peaking at -1 dBFS' if mix_file
                               else 'parts summed as they came from the separation; sum peaking at -1 dBFS')},
        'count_in_in_drums': bool(count_in_in_drums if count_in_in_drums is not None
                                  else old.get('count_in_in_drums', True if proj else False)),
    }
    ids = [p['id'] for p in out['parts']]
    if targets:
        out['targets'] = [legacy.part_id(a.strip()) for a in (targets.split(',') if isinstance(targets, str) else targets)
                          if legacy.part_id(a.strip()) in ids]
    elif old.get('targets'):
        out['targets'] = [a for a in old['targets'] if a in ids]
    else:
        out['targets'] = [a for a in ids if a != 'drums'] or ids

    # bars and beats: from the AI, from the BPM, from the original project map, or as they were
    report = read_report(proj['data']) if proj else {}
    segs = []
    if proj and os.path.exists(os.path.join(proj['data'], 'guitarra_base_acordes.json')):
        segs = json.load(open(os.path.join(proj['data'], 'guitarra_base_acordes.json'), encoding='utf-8'))['segments']
    B, npb, cin = [], [], list(old.get('count_in', []))
    if bars:
        B, npb = [round(float(v), 4) for v in bars], list(bpb)
        cin = list(count_in or [])
    elif bpm:
        start = float(first_bar if first_bar is not None else (old.get('bars') or [0.0])[0])
        B, npb = grid(float(bpm), out['time_signature'], start, dur)
        beat = 60.0 / float(bpm)
        cin = [round(start - k * beat, 4) for k in range(4, 0, -1) if start - k * beat >= -1e-6]
    elif proj and os.path.exists(os.path.join(proj['data'], 'baixo_transcricao.json')):
        B = [float(v) for v in json.load(open(os.path.join(proj['data'], 'baixo_transcricao.json'), encoding='utf-8'))['bar_starts']]
        first = [s['t0'] for s in segs if s.get('bar') == 1]
        if first and min(first) > B[0] + 0.3:  # the map puts bar 1 before the entrance; the 1st beat after the count-in wins
            B[0] = float(min(first))
        med = float(np.median(np.diff(B)))
        B = [round(v, 4) for v in B]
        npb = [2 if (B[i + 1] - B[i]) < 0.7 * med else 4 for i in range(len(B) - 1)]
        cin = report.get('count_in', cin)
        out['bpm'] = out['bpm'] or report.get('bpm')
    elif old.get('bars'):
        B, npb = old['bars'], old.get('beats_per_bar', [])
    if B:
        out['bars'], out['beats_per_bar'] = B, npb
    out['count_in'] = cin
    if report.get('soft_hits') or old.get('soft_hits'):
        out['soft_hits'] = report.get('soft_hits') or old.get('soft_hits')

    # chords: from the chart (chords.txt), from the original project, or as they were
    chart = chords
    if chart is None:
        chart = next((os.path.join(folder, f) for f in CHART_FILES if os.path.exists(os.path.join(folder, f))), None)
    chart_sections = []
    if chart:
        if not B:
            raise ValueError('to use the chord chart, give the tempo: --bpm (and --first-bar, the time of bar 1)')
        cb, chart_sections = read_chart(open(chart, encoding='utf-8').read())
        first_chord = next((c for bar in cb for c in bar if c), None)
        out['tonic'] = tonic or old.get('tonic') or (CH.split(first_chord)[0] if first_chord else 'A')
        out['chords'] = chords_from_chart(cb, B, npb, out['tonic'])
    elif segs:
        out['tonic'] = tonic or old.get('tonic') or 'A'
        out['chords'] = []
        for s in segs:
            c = s.get('chord')
            item = {'t0': round(s['t0'], 4), 't1': round(s['t1'], 4), 'bar': s.get('bar'), 'beat': s.get('beat'), 'chord': c}
            if c:
                item['degree'] = CH.degree(c, out['tonic'])
            out['chords'].append(item)
    else:
        out['tonic'] = tonic or old.get('tonic')
        out['chords'] = old.get('chords', [])
        if tonic and out['chords']:
            for c in out['chords']:
                if c.get('chord'):
                    c['degree'] = CH.degree(c['chord'], tonic)
    out['shapes'] = {c['chord']: CH.diagram(c['chord']) for c in out['chords'] if c.get('chord')}

    # song sections
    if sections:
        out['sections'] = _sections(sections) if isinstance(sections, str) else sections
    elif chart_sections:
        out['sections'] = chart_sections
    else:
        out['sections'] = old.get('sections', [])

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dest)
    log(f'written: {dest}')
    for p in out['parts']:
        log(f"  {p['name']:20s} volume {p['gain']:.4f} ({p['gain_db']:+.1f} dB)")
    log(f"  {max(0, len(out.get('bars', [])) - 1)} bars, {len(out['chords'])} chord segments, "
        f"{len(out['sections'])} sections, duration {dur:.2f} s")
    return out


def main():
    ap = argparse.ArgumentParser(description='Prepares a song (separated tracks) for Riffroom.')
    ap.add_argument('folder')
    ap.add_argument('--title'); ap.add_argument('--artist'); ap.add_argument('--album')
    ap.add_argument('--tonic', help='tonic note, for the degrees (e.g. A). Without it, the root of the first chord.')
    ap.add_argument('--tuning', type=int, help='guitar tuning in semitones (-1 = half step down); chords are named by shape')
    ap.add_argument('--bpm', type=float, help='beats per minute (the beat you tap your foot to)')
    ap.add_argument('--time-signature', help='4/4 (default), 3/4, 6/8, 12/8...')
    ap.add_argument('--first-bar', type=float, help='time (s) where bar 1 starts')
    ap.add_argument('--chords', help='chord chart file (default: chords.txt in the song folder)')
    ap.add_argument('--sections', help='"bar=name;bar=name", e.g. "1=Intro;7=Riff"')
    ap.add_argument('--targets', help='parts you play, for the "You will play" buttons (e.g. rhythm,lead)')
    ap.add_argument('--part', action='append', help='id=file, for tracks with other names (repeatable)')
    ap.add_argument('--mix', help='the whole song (to set the volume of the parts)')
    a = ap.parse_args()
    try:
        prepare(a.folder, a.title, a.artist, a.album, a.tonic, a.sections, a.bpm, a.time_signature, a.first_bar, a.chords,
                a.targets, a.part, a.mix, a.tuning)
    except (ValueError, FileNotFoundError) as e:
        print(f'error: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
