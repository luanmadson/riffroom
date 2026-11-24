# SPDX-License-Identifier: GPL-3.0-or-later
"""Riffroom tests that need no audio device (they also run on GitHub Actions).
The local AI tests run only when the AI is installed (install.bat -AI).
Usage: python -X utf8 tests/run_tests.py"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, os.path.join(ROOT, 'demo'))
os.environ.setdefault('RIFFROOM_DATA', tempfile.mkdtemp(prefix='riffroom-tests-'))
import soundfile as sf     # noqa: E402
import analysis as AN      # noqa: E402
import chords as CH        # noqa: E402
import engine as E         # noqa: E402
import i18n                # noqa: E402
import legacy              # noqa: E402
import lyrics as LY        # noqa: E402
import prepare as PR       # noqa: E402
import song as SG          # noqa: E402
import takes as TK         # noqa: E402

SR = E.SR
failures = []
print('Riffroom tests')


def test(name):
    def deco(f):
        t0 = time.time()
        try:
            r = f()
            print(f'  {"skip" if r == "skip" else "ok":6s} {name} ({time.time() - t0:.1f} s)')
        except AssertionError as e:
            failures.append(name)
            print(f'  FAILED {name}: {e}')
        except Exception as e:
            failures.append(name)
            print(f'  ERROR  {name}: {type(e).__name__}: {e}')
        return f
    return deco


def fake_song(n=SR * 20, parts=None):
    s = SG.Song.__new__(SG.Song)
    s.sr, s.n, s.id, s.folder = SR, n, 'test', '.'
    s.m = {'bars': [4.0 + 3.6 * i for i in range(5)], 'count_in': [0.4, 1.3, 2.2, 3.1], 'beats_per_bar': [4] * 4,
           'count_in_in_drums': True}
    s.parts = parts or []
    t, strong, bar = [], [], []
    for i, v in enumerate(s.m['count_in']):
        t.append(v); strong.append(i == 0); bar.append(0)
    B = s.m['bars']
    for i in range(len(B) - 1):
        for k in range(4):
            t.append(B[i] + k * 0.9); strong.append(k == 0); bar.append(i + 1)
    s.beats, s.beat_strong, s.beat_bar = np.array(t), np.array(strong), np.array(bar)
    return s


def part(pid, gain, data):
    return {'id': pid, 'manifest_name': pid, 'gain': gain, 'data': data, 'color': '#fff'}


def run(eng, blocks, frames=E.OUT_BLOCK):
    out = []
    for j in range(blocks):
        buf = np.zeros((frames, 2), np.float32)
        tinfo = types.SimpleNamespace(outputBufferDacTime=j * frames / SR, currentTime=j * frames / SR)
        eng._cb_out(buf, frames, tinfo, None)
        out.append(buf.copy())
    return np.concatenate(out)


def pluck_track(times, seconds, rng, level=0.5):
    """Distorted plucks at the given times (like a strummed guitar)."""
    x = (rng.standard_normal(int(SR * seconds)) * 1e-4).astype(np.float32)
    L = int(0.25 * SR); tt = np.arange(L) / SR
    note = (np.sign(np.sin(2 * np.pi * 110 * tt)) * 0.3 + np.sin(2 * np.pi * 220 * tt) * 0.2) * np.exp(-tt / 0.08)
    note *= np.clip((0.25 - tt) / 0.02, 0, 1)
    for a in times:
        i = int(a * SR)
        x[i:i + L] += (np.tanh(3 * note) * level).astype(np.float32)[:len(x) - i]
    return x


# ------------------------------------------------------------------ engine
@test('the mix with the volumes matches the sum of the parts')
def _():
    rng = np.random.default_rng(1)
    n = SR * 6
    d1 = (rng.standard_normal((n, 2)) * 3000).astype(np.int16)
    d2 = (rng.standard_normal((n, 2)) * 3000).astype(np.int16)
    s = fake_song(n, [part('rhythm', 0.7, d1), part('vocals', 0.5, d2)])
    e = E.Engine(); e.load(s); e.count_in = False
    e.part('vocals').volume_db = -6.0
    e.play(start=1.0)
    y = run(e, 20)
    ini = SR
    expected = (d1[ini:ini + len(y)].astype(np.float32) * 0.7 + d2[ini:ini + len(y)].astype(np.float32) * 0.5 * 10 ** (-6 / 20)) / 32768
    k = E.OUT_BLOCK  # the 1st block has the fade-in and the gain ramps
    err = np.abs(y[k:] - expected[k:]).max()
    assert err < 1e-5, f'max error {err}'


@test('turning a part off silences it without a click')
def _():
    n = SR * 4
    s = fake_song(n, [part('rhythm', 1.0, np.full((n, 2), 10000, np.int16))])
    e = E.Engine(); e.load(s); e.count_in = False
    e.play(start=0.5)
    a = run(e, 3)
    e.part('rhythm').on = False
    b = run(e, 3)
    y = np.concatenate([a, b])[:, 0]
    assert abs(b[-1, 0]) < 1e-6, 'the part kept sounding'
    jump = np.abs(np.diff(y[E.OUT_BLOCK:])).max()
    assert jump < 0.002, f'step of {jump:.4f} (click)'


@test('the loop wraps around without a click and back to its start')
def _():
    n = SR * 10
    s = fake_song(n, [part('rhythm', 1.0, np.full((n, 2), 12000, np.int16))])
    e = E.Engine(); e.load(s); e.count_in = False
    e.set_loop(2.0, 3.0)
    e.play(start=2.0)
    y = run(e, 60)[:, 0]
    jump = np.abs(np.diff(y[E.OUT_BLOCK:])).max()
    assert jump < 0.004, f'step of {jump:.4f} at the splice'
    assert e.playing and 2 * SR <= e.pos <= 3 * SR, f'position {e.pos / SR:.2f} s outside the loop'


@test('the count-in before a bar falls on the 4 previous beats')
def _():
    s = fake_song()
    e = E.Engine(); e.load(s)
    e.play(start=s.m['bars'][2])
    _, ini, cq, cs, _, until = e.cmds[-1]
    expected = [s.m['bars'][1] + k * 0.9 for k in range(4)]
    assert np.allclose(np.array(cq) / SR, expected, atol=1e-4), (np.array(cq) / SR, expected)
    assert until == int(round(s.m['bars'][2] * SR))
    assert ini < cq[0]


@test('song start: clicks only when the drum track has no count-in (or the drums are off)')
def _():
    n = SR * 20
    s = fake_song(n, [part('drums', 1.0, np.zeros((n, 2), np.int16))])
    e = E.Engine(); e.load(s)
    e.play(start=0.0)
    assert len(e.cmds[-1][2]) == 0, 'clicked over a drum track that has its own count-in'
    s.m['count_in_in_drums'] = False
    e.play(start=0.0)
    assert len(e.cmds[-1][2]) == 4, 'no clicks for a song whose drums have no count-in'


@test('device names: no USB port, device and short name')
def _():
    assert E.no_port('PRIMARY (3- GT-100 Ver2-1)') == 'PRIMARY (GT-100 Ver2-1)'
    assert E.device_of('Alto-falantes (Realtek(R) Audio)') == 'Realtek(R) Audio'
    assert E.short_name('Alto-falantes (Realtek(R) Audio)') == 'Alto-falantes'
    assert E.short_name('PRIMARY (3- GT-100 Ver2-1)') == 'GT-100 Ver2-1'
    assert E.short_name('Analogue 1 + 2 (Focusrite USB Audio)') == 'Focusrite USB Audio'
    assert E.same_device('PRIMARY (3- GT-100 Ver2-1)', 'PRIMARY (5- GT-100 Ver2-1)')
    assert E.same_device('Alto-falantes (Realtek(R) Audi', 'Alto-falantes (Realtek(R) Audio)')  # name cut by MME
    assert not E.same_device('Microfone (Realtek(R) Audio)', 'PRIMARY (GT-100 Ver2-1)')


# ------------------------------------------------------------------ alignment and calibration
@test('alignment recovers the offset and the clock difference (error < 1 ms)')
def _():
    rng = np.random.default_rng(3)
    r_o, r_i = 1 + 31e-6, 1 - 12e-6      # clocks of the sound card and of the interface against Windows
    T0, T1, S0 = 1000.0, 1000.137, 5 * SR
    outs = [(T0 + j * 2048 / (SR * r_o) + rng.normal(0, 0.002), S0 + j * 2048) for j in range(4000)]
    ins, n = [], 0
    for k in range(8000):
        n += 1024
        ins.append((T1 + n / (SR * r_i) + 0.004 + rng.normal(0, 0.002), n))
    start, alpha = TK.align(ins, outs, 0.004)
    ok_start = S0 + (T1 - T0) * SR * r_o
    ok_alpha = r_o / r_i
    assert abs(start - ok_start) / SR < 0.001, f'start off by {(start - ok_start) / SR * 1000:.2f} ms'
    end = 8000 * 1024
    err_end = abs((start + end * alpha) - (ok_start + end * ok_alpha)) / SR
    assert err_end < 0.001, f'error of {err_end * 1000:.2f} ms at the end of the take'


@test('with the session clocks, a short take also aligns (error < 1 ms)')
def _():
    rng = np.random.default_rng(6)
    r_o, r_i = 1 + 31e-6, 1 - 12e-6
    T0, T1, S0 = 500.0, 500.311, 9 * SR
    outs = [(T0 + j * 2048 / (SR * r_o) + rng.normal(0, 0.004), S0 + j * 2048) for j in range(60)]
    ins, n = [], 0
    for k in range(120):
        n += 1024
        ins.append((T1 + n / (SR * r_i) + rng.normal(0, 0.004), n))
    start, alpha = TK.align(ins, outs, 0.0, (1 / (SR * r_i), 1 / (SR * r_o)))
    ok_start = S0 + (T1 - T0) * SR * r_o
    assert abs(start - ok_start) / SR < 0.001, f'start off by {(start - ok_start) / SR * 1000:.2f} ms'
    assert abs(alpha - r_o / r_i) < 1e-9


@test('resampling fixes the clock difference')
def _():
    x = np.sin(np.arange(SR * 10) / SR * 2 * np.pi * 110).astype(np.float32)[:, None]
    y = TK.resample(x, 1 + 50e-6)
    assert abs(len(y) - len(x) * (1 + 50e-6)) <= 2, (len(x), len(y))


@test('mono: equal sides or one silent side become one channel')
def _():
    x = np.random.default_rng(4).standard_normal((SR, 1)).astype(np.float32)
    assert TK.mono_if_same(np.concatenate([x, x], axis=1)).ndim == 1
    assert TK.mono_if_same(np.concatenate([x, x * 0.5], axis=1)).ndim == 2
    left = TK.mono_if_same(np.concatenate([x, x * 1e-4], axis=1))
    assert left.ndim == 1 and np.allclose(left, x[:, 0])


@test('"Fix the timing" finds a known delay (error < 2 ms)')
def _():
    rng = np.random.default_rng(5)
    clicks = [1.5 + i * 0.6 for i in range(8)]
    delay = 0.023
    x = (rng.standard_normal(int(SR * 7)) * 0.0005).astype(np.float32)
    for c in clicks:
        i = int((c + delay + rng.normal(0, 0.003)) * SR)
        L = int(0.05 * SR)
        x[i:i + L] += (rng.standard_normal(L) * np.exp(-np.arange(L) / (0.008 * SR)) * 0.4).astype(np.float32)
    r = TK.measure_latency([a / SR for a in TK.attacks(x)], clicks)
    assert r is not None, 'did not measure'
    assert abs(r['ms'] - delay * 1000) < 2.0, f'measured {r["ms"]:.1f} ms (expected {delay * 1000:.0f})'


# ------------------------------------------------------------------ timing analysis
@test('onsets: every strum found, to the millisecond, and no false ones while notes die away')
def _():
    rng = np.random.default_rng(2)
    truth = np.array([2.0 + k * 0.5556 + rng.normal(0, 0.003) for k in range(30)])
    on = AN.onsets(pluck_track(truth, 20, rng))
    assert len(on) == len(truth), f'{len(on)} onsets for {len(truth)} strums'
    _, d = AN._nearest(truth, on)
    assert np.abs(d).max() < 0.003, f'max error {np.abs(d).max() * 1000:.1f} ms'


@test('analysis: a take 30 ms late against the original part reads +30 ms; rushing bars are found')
def _():
    rng = np.random.default_rng(9)
    beat = 60 / 72
    bars = [2.0 + 4 * beat * i for i in range(9)]
    beats = [b + k * beat for b in bars[:-1] for k in range(4)]
    beat_bar = [i // 4 + 1 for i in range(len(beats))]
    grid = AN.grid_points(beats, beat_bar, 3)
    orig = np.array([b + j * beat / 3 for b in beats for j in (0, 2)])
    take = orig + 0.030 + rng.normal(0, 0.003, len(orig))
    early = (take > bars[5]) & (take < bars[7])
    take[early] -= 0.060                                  # bars 6 and 7: 30 ms early
    r = AN.analyze(take, grid, bars, orig, (0, 40))
    assert r['reference'] == 'original', r['reference']
    assert abs(r['original']['median_ms'] - 30) < 3, r['original']
    assert any(s['kind'] == 'early' and s['from_bar'] == 6 and s['to_bar'] == 7 for s in r['streaks']), r['streaks']
    assert 6 in r['worst_bars'] and 7 in r['worst_bars'], r['worst_bars']


# ------------------------------------------------------------------ takes and MP3
@test('the exported MP3 has the right length and the tag with the title')
def _():
    x = (np.sin(np.arange(SR * 3) / SR * 2 * np.pi * 220) * 0.5).astype(np.float32)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'test.mp3')
        TK.export_mp3(x, SR, p, 'Test take – ção', 'Riffroom', 'Tests', 'comment')
        dur = sf.info(p).duration
        tags = TK.read_id3(p)
    assert abs(dur - 3.0) < 0.15, f'length {dur:.2f} s'
    assert tags.get('TIT2') == 'Test take – ção' and tags.get('TPE1') == 'Riffroom', tags


@test('save, rename, delete and undo a take; folders follow the language')
def _():
    with tempfile.TemporaryDirectory() as d:
        i18n.set_language('pt')
        tt = TK.Takes(d, 'pt')
        f = tt.save(np.zeros(SR, np.float32), 'rhythm', 'Guitarra base', 12.5, {})
        assert f['name'] == 'Base 1' and 'tentativa 1' in f['file'], f
        assert os.path.isdir(os.path.join(d, 'Gravações', 'Tentativas'))
        tt.update(f['id'], name='Boa', favorite=True)
        assert tt.find(f['id'])['name'] == 'Boa'
        tt.delete(f['id'])
        assert not tt.list() and os.path.isdir(os.path.join(d, 'Gravações', 'Tentativas', 'Lixeira'))
        tt.restore(f['id'])
        assert len(tt.list()) == 1
        i18n.set_language('en')
        tt2 = TK.Takes(d, 'en')                       # the existing Portuguese layout wins
        assert tt2.dir == tt.dir
        f2 = tt2.save(np.zeros(SR, np.float32), 'rhythm', 'Rhythm guitar', 0, {})
        assert f2['number'] == 2 and f2['name'] == 'Rhythm 2'


@test('delete several takes at once and undo them; a take whose file is open stays')
def _():
    with tempfile.TemporaryDirectory() as d:
        i18n.set_language('en')
        tt = TK.Takes(d, 'en')
        fs = [tt.save(np.zeros(SR // 4, np.float32), 'rhythm', 'Rhythm guitar', 0, {}) for _ in range(4)]
        ids = [f['id'] for f in fs]
        moved, failed = tt.delete_many([ids[0], ids[2], ids[0], 'no such take'])
        assert [f['id'] for f in moved] == [ids[0], ids[2]] and not failed, (moved, failed)
        assert sorted(f['id'] for f in tt.list()) == sorted([ids[1], ids[3]])
        assert sorted(os.listdir(tt.trash)) == sorted([ids[0] + '.flac', ids[0] + '.json', ids[2] + '.flac', ids[2] + '.json'])
        back, failed = tt.restore_many([ids[0], ids[2], ids[1]])   # ids[1] is not in the trash: skipped
        assert sorted(f['id'] for f in back) == sorted([ids[0], ids[2]]) and not failed
        assert len(tt.list()) == 4 and not os.listdir(tt.trash)
        if os.name == 'nt':                                       # an open file cannot move on Windows
            with open(os.path.join(tt.dir, ids[1] + '.flac'), 'rb'):
                moved, failed = tt.delete_many(ids)
            assert [f['id'] for f in failed] == [ids[1]] and len(moved) == 3, (moved, failed)
            assert [f['id'] for f in tt.list()] == [ids[1]], 'the take in use stays whole, audio and record'
            assert not os.path.exists(os.path.join(tt.trash, ids[1] + '.flac'))


# ------------------------------------------------------------------ version 1 files
@test('legacy: manifest, take record and configuration of version 1 are converted')
def _():
    old = {'versao': 1, 'titulo': 'Song', 'artista': 'Band', 'tonica': 'A', 'compasso_tipo': '12/8', 'taxa': 44100,
           'duracao_s': 10.0, 'partes': [{'id': 'base', 'nome': 'Guitarra base', 'arquivo': 'x.wav', 'ganho': 0.5}],
           'alvos': ['base', 'solo'], 'compassos': [1.0, 4.0], 'pulsos_por_compasso': [4], 'contagem': [0.1],
           'acordes': [{'t0': 1.0, 't1': 4.0, 'compasso': 1, 'pulso': 1, 'acorde': 'A5', 'grau': 'I'}],
           'desenhos': {'A5': {'casas': ['x', 0, 2, 2, 'x', 'x'], 'corda_tonica': 1, 'intervalos': ['', '1', '5', '1', '', '']}},
           'secoes': [{'compasso': 1, 'nome': 'Intro'}]}
    m = legacy.manifest(old)
    assert m['parts'][0]['id'] == 'rhythm' and m['targets'] == ['rhythm', 'lead'] and m['chords'][0]['chord'] == 'A5'
    assert m['shapes']['A5']['root_string'] == 1 and m['sections'] == [{'bar': 1, 'name': 'Intro'}] and m['count_in_in_drums']
    take = legacy.take({'id': 'x', 'arquivo': 'x.flac', 'parte': 'solo', 'inicio_s': 2.0, 'deslocamento_ms': 5, 'favorita': True})
    assert take['part'] == 'lead' and take['file'] == 'x.flac' and take['offset_ms'] == 5 and take['favorite']
    cfg = legacy.config({'modo_saida': 'gt100', 'ajustes_ms': {'k': 12.0}, 'entrada': 'PRIMARY (GT-100 Ver2-1)',
                         'musicas': {'s': {'alvo': 'base', 'partes': {'base': {'ligada': False, 'fonte': 'tentativa', 'tentativa': 't'}}}}},
                        'C:/Music/Riffroom')
    assert cfg['output_mode'] == 'interface' and cfg['latency_ms'] == {'k': 12.0} and cfg['input_device'].startswith('PRIMARY')
    assert cfg['songs']['s']['target'] == 'rhythm' and cfg['songs']['s']['parts']['rhythm'] == {'on': False, 'volume_db': 0.0, 'source': 'take', 'take': 't'}


# ------------------------------------------------------------------ preparing songs
@test('tracks recognized by name (Demucs, English, Portuguese), without confusing mixes')
def _():
    names = ['drums.wav', 'bass.wav', 'other.wav', 'vocals.wav', 'guitar.wav', 'piano.mp3', 'mixture.wav']
    found = {k: os.path.basename(v) for k, v in PR.find_parts_by_name('x', names).items()}
    assert found == {'drums': 'drums.wav', 'bass': 'bass.wav', 'other': 'other.wav', 'vocals': 'vocals.wav',
                     'guitar': 'guitar.wav', 'piano': 'piano.mp3'}, found
    assert os.path.basename(PR.find_mix('x', names)) == 'mixture.wav'
    names = ['Song_baixo_bateria.wav', 'Song_sem_voz.wav', 'Song_guitarra_base_isolada.wav', 'Song - Lead Guitar.flac',
             'rhythm_guitar.wav']
    found = {k: os.path.basename(v) for k, v in PR.find_parts_by_name('x', names).items()}
    assert found == {'rhythm': 'rhythm_guitar.wav', 'lead': 'Song - Lead Guitar.flac'}, found


@test('chart: bars, repeats, split bars and sections')
def _():
    cb, secs = PR.read_chart('# comment\n[Intro]\nA5 % | D5,C5 -\n[Verse]\nF#m7 Bb\n')
    assert cb == [['A5'], ['A5'], ['D5', 'C5'], [None], ['F#m7'], ['Bb']], cb
    assert secs == [{'bar': 1, 'name': 'Intro'}, {'bar': 5, 'name': 'Verse'}], secs
    B, bpb = PR.grid(60, '4/4', 1.0, 30)
    items = PR.chords_from_chart(cb, B, bpb, 'A')
    assert items[0]['chord'] == 'A5' and items[0]['t1'] == B[2], 'the two A5 in a row should be one segment'
    assert items[1]['chord'] == 'D5' and items[1]['beat'] == 1 and items[2]['chord'] == 'C5' and items[2]['beat'] == 3
    assert items[1]['degree'] == 'IV' and items[2]['degree'] == '♭III'
    try:
        PR.read_chart('H7 A5')
        raise AssertionError('accepted an invalid chord')
    except ValueError:
        pass


@test('chords: degree, description in three languages and diagram')
def _():
    assert CH.degree('E5', 'A') == 'V' and CH.degree('G', 'A') == '♭VII'
    assert CH.transpose('G#5', 1) == 'A5'
    for lang, note in (('en', 'F♯'), ('pt', 'Fá♯'), ('es', 'Fa♯')):
        i18n.set_language(lang)
        assert CH.describe('F#m')['note'] == note, (lang, CH.describe('F#m'))
    i18n.set_language('en')
    d = CH.diagram('C')
    assert d['frets'] == ['x', 3, 2, 0, 1, 0] and d['root_string'] == 1 and d['intervals'][1] == '1'
    assert CH.shape('G#5')[1:3] == [6, 6]
    s = CH.svg('A5')
    assert s.startswith('<svg') and s.endswith('</svg>') and 'power chord' in s
    assert SG.subdivisions('12/8') == 3 and SG.subdivisions('6/8') == 3 and SG.subdivisions('4/4') == 2


@test('languages: the three have the same keys and the same placeholders')
def _():
    L = {k: json.load(open(os.path.join(ROOT, 'app', 'locales', f'{k}.json'), encoding='utf-8')) for k in i18n.LANGUAGES}
    for k in ('pt', 'es'):
        assert set(L[k]) == set(L['en']), (k, sorted(set(L['en']) ^ set(L[k]))[:10])
        for key, v in L['en'].items():
            assert set(re.findall(r'\{(\w+)\}', v)) == set(re.findall(r'\{(\w+)\}', L[k][key])), (k, key)


@test('demo song: made, prepared, listed and opened')
def _():
    import make_demo
    with tempfile.TemporaryDirectory() as d:
        folder = make_demo.make(d, choruses=1, log=lambda s: None)
        m = json.load(open(os.path.join(folder, 'riffroom.json'), encoding='utf-8'))
        assert [p['id'] for p in m['parts']] == ['drums', 'bass', 'rhythm', 'lead'], m['parts']
        assert m['targets'] == ['rhythm', 'lead'] and m['time_signature'] == '12/8' and m['count_in_in_drums']
        assert np.allclose(m['count_in'], [0.5, 1.3333, 2.1667, 3.0], atol=1e-3), m['count_in']
        assert [s['name'] for s in m['sections']] == ['First chorus', 'Ending']
        assert m['chords'][0]['chord'] == 'A5' and m['chords'][0]['t1'] - m['chords'][0]['t0'] > 13
        new = os.path.join(d, 'Another band', 'Another song')        # a new folder, not prepared, Demucs names
        os.makedirs(new)
        for name in ('drums.wav', 'bass.wav', 'vocals.wav'):
            sf.write(os.path.join(new, name), np.zeros((SR, 2), np.float32), SR)
        SG.LIBRARIES[:] = [d]
        songs = {x['id']: x for x in SG.list_songs()}
        assert songs['Demo']['prepared'] and not songs['Another band/Another song']['prepared'], songs
        PR.prepare(new, log=lambda s: None)
        assert all(x['prepared'] for x in SG.list_songs())
        s = SG.Song(folder, 'Demo')
        sd = s.screen_data()
        assert sd['subdivisions'] == 3 and sd['max_beats'] == 4 and len(sd['bars']) >= 15
        assert s.beats_before(m['bars'][0]) == m['count_in']
        assert s.bar_start(m['bars'][3] + 1.0) == m['bars'][3]
        assert s.chord_svg('E5').startswith('<svg')
        e = E.Engine(); e.load(s); e.count_in = True
        e.play(start=0.0)
        assert np.abs(run(e, 80)).max() > 0.05, 'the demo did not sound'
        # timing analysis of the demo rhythm guitar against itself: on time
        x = s.part('rhythm')['data'].astype(np.float32).mean(axis=1) / 32768
        rec = {'id': 'self', 'part': 'rhythm', 'start_s': 0.0, 'offset_ms': 0.0}
        r = AN.analyze_take(s, rec, x)
        assert r['reference'] == 'original' and abs(r['original']['median_ms']) < 2, r.get('original')


# ------------------------------------------------------------------ lyrics
@test('lyrics: LRC and plain text, repeated chorus, offset, round trip, shift')
def _():
    d = LY.parse('\ufeff[ti:Song]\n[ar:Band]\n[offset:+500]\n[00:12.50]First line\n[00:20.00][01:10.00]Chorus <00:21.00>line\n'
                 '\n\n\nAfter a break\n[00:30.5]Third\n[02:00.00]\n')
    assert d['meta'] == {'ti': 'Song', 'ar': 'Band'}, d['meta']
    L = d['lines']
    assert [ln['t'] for ln in L] == [12.0, 19.5, None, None, 30.0, 69.5, 119.5], L
    assert L[1]['text'] == 'Chorus line' and L[5]['text'] == 'Chorus line' and L[2]['text'] == '' and L[6]['text'] == ''
    assert LY.parse(LY.dump(L, d['meta'])) == {'lines': L, 'meta': d['meta']}
    assert LY.fmt(65.456) == '[01:05.46]' and LY.timed(L) and not LY.timed(LY.parse('a\nb')['lines'])
    S = LY.shift(L, -12.5)
    assert S[0]['t'] == 0.0 and S[1]['t'] == 7.0 and S[3]['t'] is None
    p = LY.parse('Line one\n\nLine two\n')['lines']
    assert p == [{'t': None, 'text': 'Line one'}, {'t': None, 'text': ''}, {'t': None, 'text': 'Line two'}], p
    with tempfile.TemporaryDirectory() as d1:
        sub = os.path.join(d1, 'Dados')
        os.makedirs(sub)
        assert LY.find([sub, d1]) == (None, False)
        open(os.path.join(d1, 'from the web.lrc'), 'w', encoding='cp1252').write('[00:01.00]Ol\xe1\n')
        path, own = LY.find([sub, d1])
        assert not own and path.endswith('from the web.lrc') and LY.parse(LY.read_file(path))['lines'][0]['text'] == 'Ol\xe1'
        LY.write_file(sub, L, d['meta'])
        assert LY.find([sub, d1]) == (os.path.join(sub, LY.FILE), True)


@test('lyrics: lined up with the voice (lines 3.4 s off are found within 0.05 s)')
def _():
    rng = np.random.default_rng(7)
    starts = np.cumsum(rng.uniform(2.0, 4.5, 30)) + 8.0          # where the voice comes in, 2-4.5 s apart
    n = int((starts[-1] + 6) * SR)
    voice = (rng.standard_normal(n) * 0.0005).astype(np.float32)   # leak of the band, ~60 dB down
    for a in starts:
        i0, i1 = int(a * SR), int((a + rng.uniform(1.0, 1.8)) * SR)
        voice[i0:i1] += (0.3 * np.sin(2 * np.pi * 220 * np.arange(i1 - i0) / SR)).astype(np.float32)
    lines = [{'t': float(a - 3.4 + rng.normal(0, 0.05)), 'text': f'line {k}'} for k, a in enumerate(starts)]
    sh, q = LY.align(lines, voice, SR)
    assert sh is not None and abs(sh - 3.4) < 0.05 and q > 0.8, (sh, q)
    sh2, q2 = LY.align(LY.shift(lines, 9.0), voice, SR)
    assert abs(sh2 - (3.4 - 9.0)) < 0.05, sh2
    assert LY.align(lines[:2], voice, SR) == (None, 0.0)


# ------------------------------------------------------------------ MCP server (in memory, with a fake app)
@test('MCP: tools list, song search, sections and bars turned into seconds')
def _():
    import mcp_server as MS
    from mcp import Client

    bars = [3.0 + 2.0 * i for i in range(17)]
    song = {'id': 'Example Band/First Album (2020)/Night Drive', 'title': 'Night Drive', 'artist': 'Example Band',
            'duration': 40.0, 'bars': bars, 'count_in': [1.0, 1.5, 2.0, 2.5], 'bpm': 66, 'time_signature': '12/8',
            'tonic': 'A', 'tuning': -1, 'parts': [{'id': 'rhythm', 'name': 'Rhythm guitar'}], 'targets': ['rhythm'],
            'sections': [{'bar': 1, 'name': 'Intro'}, {'bar': 5, 'name': 'Verse 1'}, {'bar': 9, 'name': 'Chorus'}],
            'chords': [{'t0': 3.0, 't1': 11.0, 'chord': 'A5'}, {'t0': 11.0, 't1': 35.0, 'chord': 'D5'}], 'source': 'v1'}
    sent = []

    class Fake(MS.API):
        def alive(self):
            return True

        def call(self, path, body=None, timeout=30):
            sent.append((path, body))
            if path == '/api/state':
                return {'song': {'id': song['id'], 'title': 'Night Drive'}, 'songs': [
                    {'id': song['id'], 'title': 'Night Drive', 'artist': 'Example Band', 'prepared': True},
                    {'id': 'Demo', 'title': 'Demo – Slow Blues in A', 'artist': 'Riffroom', 'prepared': True}],
                    'playing': False, 'recording': False, 'calibrating': False, 'busy': None, 'count_in': True,
                    'metronome': False, 'master_db': 0, 'target': 'rhythm', 'calibrated': False, 'latency_ms': 0,
                    'parts': [], 'devices': {}, 'pos': 12.5, 'loop': None, 'loop_on': False, 'loading': False}
            if path == '/api/song':
                return song
            if path == '/api/takes':
                return [{'id': 'take-1', 'name': 'Rhythm 1', 'favorite': True}, {'id': 'take-2', 'name': 'Rhythm 2'},
                        {'id': 'take-3', 'name': 'Rhythm 3'}]
            if path == '/api/lyrics':
                return {'timed': True, 'file': 'lyrics.lrc', 'lines': [{'t': 4.0, 'text': 'In the night'}, {'t': None, 'text': ''},
                                                                     {'t': 19.0, 'text': 'Chorus words'}]}
            return {}

    MS.api = Fake()

    async def go():
        async with Client(MS.mcp) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert {'open_song', 'set_loop', 'record_take', 'analyze_take', 'import_song'} <= names, names
            r = await c.call_tool('open_song', {'song': 'night drive'})
            assert not r.is_error and r.structured_content['sections'][2]['from_bar'] == 9, r
            assert ('/api/song', {'id': song['id']}) in sent
            assert 'A5' in r.structured_content['chord_chart'] and '[Chorus]' in r.structured_content['chord_chart']
            sent.clear()
            await c.call_tool('set_loop', {'section': 'chorus'})
            assert ('/api/loop', {'a': bars[8], 'b': bars[16], 'on': True}) in sent, sent
            sent.clear()
            await c.call_tool('set_loop', {'start_bar': 5, 'end_bar': 8})
            assert ('/api/loop', {'a': bars[4], 'b': bars[8], 'on': True}) in sent, sent
            r = await c.call_tool('set_loop', {'section': 'bridge'})
            assert r.is_error and 'Verse 1' in r.content[0].text, r.content
            r = await c.call_tool('play', {'from_bar': 99})
            assert r.is_error and '1 to 16' in r.content[0].text, r.content
            st = (await c.call_tool('get_status', {})).structured_content
            assert st['position']['bar'] == 5 and st['position']['section'] == 'Verse 1', st['position']
            ly = (await c.call_tool('get_lyrics', {})).structured_content
            assert ly['current_line'] == 1 and ly['lines'][2]['section'] == 'Chorus' and ly['lines'][2]['bar'] == 9, ly
            sent.clear()
            r = await c.call_tool('delete_take', {'take_ids': ['rhythm 2', 'take-3', 'Rhythm 2']})
            assert not r.is_error and ('/api/take_delete', {'ids': ['take-2', 'take-3']}) in sent, sent
            r = await c.call_tool('delete_take', {'take_id': 'Rhythm 9'})
            assert r.is_error and 'Rhythm 1' in r.content[0].text, r.content
            assert (await c.call_tool('delete_take', {})).is_error
    asyncio.run(go())


# ------------------------------------------------------------------ local AI (only when installed)
def ai_installed():
    try:
        import torch  # noqa: F401
        import librosa  # noqa: F401
        import beat_this  # noqa: F401
        return True
    except Exception:
        return False


@test('AI: beats, 12/8 feel and chords of the demo mix (beat-this + chroma)')
def _():
    if not ai_installed():
        return 'skip'
    import make_demo
    from ai import beats as BT
    from ai import chord_detect as CD
    with tempfile.TemporaryDirectory() as d:
        folder = make_demo.make(d, choruses=2, log=lambda s: None)
        m = json.load(open(os.path.join(folder, 'riffroom.json'), encoding='utf-8'))
        stems = {n: sf.read(os.path.join(folder, n + '.wav'), dtype='float32')[0] for n in ('drums', 'bass', 'rhythm_guitar', 'lead_guitar')}
        mix = sum(stems.values()).mean(axis=1)
        beats, downbeats = BT.track(mix, SR)
        harm = (stems['rhythm_guitar'] + stems['lead_guitar']).mean(axis=1)
        segs = CD.detect(harm, SR, beats, bass=stems['bass'].mean(axis=1))
        info = BT.bars_from(beats, downbeats, len(mix) / SR, segs)
        assert abs(info['bpm'] - 72) < 4, f"tempo {info['bpm']}"
        assert info['numerator'] == 4, f"{info['numerator']} beats per bar"
        b1 = min(info['bars'], key=lambda v: abs(v - m['bars'][0]))
        assert abs(b1 - m['bars'][0]) < 0.06, f'bar 1 at {b1:.3f} s (expected {m["bars"][0]:.3f})'
        drum_on = AN.onsets(stems['drums'].mean(axis=1), SR)
        assert BT.feel(drum_on, beats) == 3, 'the shuffle should read as triplets (12/8)'
        truth = [(c['t0'], c['t1'], c['chord']) for c in m['chords'] if c['chord']]
        ok = tot = 0
        for t0, t1, c in truth:
            for t0b, t1b, cb in segs:
                ov = min(t1, t1b) - max(t0, t0b)
                if ov > 0:
                    tot += ov
                    ok += ov if cb == c else 0
        assert ok / tot > 0.8, f'only {ok / tot:.0%} of the time with the right chord'
        assert CD.tonic(segs) == 'A'


if failures:
    print(f'{len(failures)} failure(s): {failures}')
    sys.exit(1)
print('all good')
