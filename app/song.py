# SPDX-License-Identifier: GPL-3.0-or-later
"""Songs: finds the songs in the music folders (libraries), reads each song's riffroom.json (written by prepare.py)
and loads the band parts into memory, all with the same length."""
import base64
import json
import os

import numpy as np
import soundfile as sf

import chords as CH
import i18n
import legacy

LIBRARIES = []            # folders where songs are looked for (filled by the app from the configuration)
MANIFEST, OLD_MANIFEST = 'riffroom.json', 'ensaio.json'
PART_COLORS = {'drums': '#9aa5b1', 'bass': '#8b7cf6', 'rhythm': '#f0a030', 'guitar': '#f0a030', 'lead': '#ef5b5b',
               'vocals': '#3fb67a', 'piano': '#4cc3cc', 'other': '#b0a48a'}
AUDIO_EXT = ('.wav', '.flac', '.mp3', '.ogg', '.aiff', '.aif')


def default_library():
    """<Windows Music folder>\\Riffroom (the Music folder may have been moved: ask Windows)."""
    base = os.path.join(os.path.expanduser('~'), 'Music')
    if os.name == 'nt':
        try:
            import ctypes
            import uuid
            guid = uuid.UUID('{4BD8D571-6D19-48D3-BE97-422220080E43}')  # FOLDERID_Music
            buf = ctypes.create_string_buffer(guid.bytes_le)
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(buf, 0, None, ctypes.byref(p)) == 0:
                base = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
        except Exception:
            pass
    return os.path.join(base, 'Riffroom')


def manifest_path(folder):
    """Path of the song's manifest (in the folder itself or in Dados/), or None. riffroom.json wins over ensaio.json."""
    for name in (MANIFEST, OLD_MANIFEST):
        for p in (os.path.join(folder, name), os.path.join(folder, 'Dados', name)):
            if os.path.isfile(p):
                return p
    return None


def read_manifest(path):
    with open(path, encoding='utf-8') as f:
        m = json.load(f)
    return legacy.manifest(m) if legacy.is_old_manifest(m) else m


def _looks_like_song(folder, files, dirs):
    """Folder not prepared yet that has separated tracks: 'Faixas/' or audio files named after parts (drums, bass...)."""
    if 'Faixas' in dirs:
        return True
    import prepare
    return len(prepare.find_parts_by_name(folder, files)) >= 2


SKIP_DIRS = {'Takes', 'Exports', 'Gravações', 'Tentativas', 'Grabaciones', 'Tomas', 'Exportados'}


def list_songs():
    """Songs of all libraries: [{'id', 'folder', 'prepared', 'title', 'artist'}]."""
    out, seen = [], set()
    for lib in LIBRARIES:
        if not os.path.isdir(lib):
            continue
        for root, dirs, files in os.walk(lib):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP_DIRS]
            man = manifest_path(root)
            if not man and not _looks_like_song(root, files, dirs):
                continue
            rel = os.path.relpath(root, lib).replace('\\', '/')
            if rel == '.':
                rel = os.path.basename(root)
            sid, k = rel, 2
            while sid in seen:
                sid = f'{rel} ({k})'
                k += 1
            seen.add(sid)
            item = {'id': sid, 'folder': root, 'prepared': bool(man)}
            if man:
                try:
                    m = read_manifest(man)
                    item.update(title=m.get('title'), artist=m.get('artist'))
                except Exception:
                    item['prepared'] = False
            pieces = rel.split('/')
            item['title'] = item.get('title') or pieces[-1]
            item['artist'] = item.get('artist') or (pieces[0] if len(pieces) > 1 else '')
            out.append(item)
            dirs[:] = []
    return sorted(out, key=lambda i: ((i.get('artist') or '').lower(), (i.get('title') or '').lower()))


def folder_of(sid, songs=None):
    for x in (songs if songs is not None else list_songs()):
        if x['id'] == sid:
            return x['folder']
    raise KeyError(f'song not found: {sid}')


def id_of_folder(folder, songs=None):
    p = os.path.normcase(os.path.abspath(folder))
    for x in (songs if songs is not None else list_songs()):
        if os.path.normcase(os.path.abspath(x['folder'])) == p:
            return x['id']
    return None


def subdivisions(time_signature):
    """Divisions of each beat: 3 in compound meters (6/8, 9/8, 12/8), 2 otherwise."""
    try:
        n, d = (int(v) for v in str(time_signature).split('/'))
        return 3 if d == 8 and n % 3 == 0 and n >= 6 else 2
    except ValueError:
        return 2


def part_name(pid, fallback=None):
    key = 'part.' + pid
    return i18n.t(key) if i18n.has(key) else (fallback or pid)


def _b64(v):
    return base64.b64encode(v.astype(np.uint8).tobytes()).decode('ascii')


def _contour(pk):
    return np.clip(np.round(np.sqrt(np.clip(pk, 0, 1)) * 255), 0, 255)


class Song:
    def __init__(self, folder, sid=None):
        self.folder = folder
        self.id = sid or os.path.basename(folder)
        man = manifest_path(folder)
        if not man:
            raise FileNotFoundError(f'the song is not prepared: no {MANIFEST} in {folder}')
        self.m = read_manifest(man)
        self.sr = int(self.m['sample_rate'])
        self.n = int(round(self.m['duration_s'] * self.sr))
        self.parts = []
        for p in self.m['parts']:
            x, sr = sf.read(os.path.join(folder, p['file']), dtype='int16', always_2d=True)
            if sr != self.sr:
                raise ValueError(f"{p['file']}: {sr} Hz (expected {self.sr})")
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            buf = np.zeros((self.n, 2), np.int16)
            k = min(self.n, len(x))
            buf[:k] = x[:k, :2]
            self.parts.append({'id': p['id'], 'manifest_name': p.get('name'), 'gain': float(p['gain']), 'data': buf,
                               'color': PART_COLORS.get(p['id'], '#9aa5b1')})
        ids = [p['id'] for p in self.parts]
        self.targets = [a for a in (self.m.get('targets') or ids) if a in ids] or ids
        # beats: those of the count-in (before bar 1) and those of each bar; the 1st of each bar is the strong one
        t, strong, bar = [], [], []
        for i, v in enumerate(self.m.get('count_in', [])):
            t.append(v); strong.append(i == 0); bar.append(0)
        B = self.m.get('bars', [])
        bpb = self.m.get('beats_per_bar', [])
        for i in range(len(B) - 1):
            d = (B[i + 1] - B[i]) / bpb[i]
            for k in range(bpb[i]):
                t.append(B[i] + k * d); strong.append(k == 0); bar.append(i + 1)
        self.beats = np.array(t, np.float64)
        self.beat_strong = np.array(strong, bool)
        self.beat_bar = np.array(bar, np.int32)
        self._peaks = None

    @property
    def title(self):
        return self.m.get('title') or self.id

    def part(self, pid):
        for p in self.parts:
            if p['id'] == pid:
                return p
        raise KeyError(pid)

    def name_of(self, pid):
        p = next((x for x in self.parts if x['id'] == pid), None)
        return part_name(pid, p['manifest_name'] if p else None)

    def bar_start(self, t):
        """Start of the bar where time t falls (before bar 1: the start of the track; without bars: t itself)."""
        B = self.m.get('bars', [])
        if not B:
            return float(t)
        if t < B[0] - 0.05:
            return 0.0
        i = int(np.searchsorted(B, t + 0.05, side='right')) - 1
        return float(B[max(0, min(i, len(B) - 2))])

    def beats_before(self, t, how_many=4):
        """The `how_many` beats before t (for the count-in). Before bar 1, the song's own count-in."""
        i = int(np.searchsorted(self.beats, t - 0.02, side='left'))
        sel = list(self.beats[max(0, i - how_many):i])
        while 0 < len(sel) < how_many:  # near the start: complete backwards at the same tempo
            d = sel[1] - sel[0] if len(sel) > 1 else 0.91
            sel.insert(0, sel[0] - d)
        return [v for v in sel if v >= 0]

    def _contours(self):
        if self._peaks is not None:
            return self._peaks
        w = int(self.sr * 0.02)
        nw = self.n // w
        mix = np.zeros(nw, np.float32)
        per_part = {}
        step = w * 4000
        for c0 in range(0, nw * w, step):
            c1 = min(nw * w, c0 + step)
            s = np.zeros((c1 - c0, 2), np.float32)
            for p in self.parts:
                y = p['data'][c0:c1].astype(np.float32) * np.float32(p['gain'] / 32768)
                s += y
                per_part.setdefault(p['id'], []).append(np.abs(y).max(axis=1).reshape(-1, w).max(axis=1))
            mix[c0 // w:c1 // w] = np.abs(s).max(axis=1).reshape(-1, w).max(axis=1)
        parts_pk = {}
        for pid, pieces in per_part.items():
            v = np.concatenate(pieces)
            parts_pk[pid] = _b64(_contour(v / max(float(v.max()), 1e-6)))
        self._peaks = {'window_s': 0.02, 'mix': _b64(_contour(mix)), 'parts': parts_pk}
        return self._peaks

    def screen_data(self):
        """What the screen needs: bars, chords, song sections and the waveform contours (texts in the current language)."""
        m = self.m
        bpb = m.get('beats_per_bar', [])
        return {
            'id': self.id, 'title': m.get('title'), 'artist': m.get('artist'), 'album': m.get('album'),
            'duration': self.n / self.sr, 'tonic': m.get('tonic'), 'time_signature': m.get('time_signature'),
            'subdivisions': int(m.get('subdivisions') or subdivisions(m.get('time_signature', '4/4'))),
            'max_beats': max(bpb, default=4), 'bpm': m.get('bpm'), 'tuning': int(m.get('tuning') or 0),
            'bars': m.get('bars', []), 'beats_per_bar': bpb, 'count_in': m.get('count_in', []),
            'sections': m.get('sections', []), 'chords': m.get('chords', []),
            'chord_info': {c: CH.describe(c) for c in (m.get('shapes') or {})},
            'parts': [{'id': p['id'], 'name': self.name_of(p['id']), 'color': p['color']} for p in self.parts],
            'targets': self.targets, 'source': m.get('source'),
            'peaks': self._contours(),
        }

    def chord_svg(self, chord):
        d = (self.m.get('shapes') or {}).get(chord)
        if d is None:
            if not CH.is_valid(chord):
                raise KeyError(chord)
            d = CH.diagram(chord)
        return CH.svg(chord, d)
