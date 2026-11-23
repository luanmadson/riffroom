# SPDX-License-Identifier: GPL-3.0-or-later
"""Takes (recordings):
  - the alignment math: clock of the audio interface x clock of the sound card, from the times of the audio blocks;
  - "Fix the timing" (latency calibration): finds the guitar attacks and compares them with the clicks;
  - saving (FLAC + .json record), listing, renaming, deleting with undo and exporting MP3."""
import base64
import datetime
import glob
import json
import os
import re
import shutil
import struct
from fractions import Fraction

import numpy as np
import soundfile as sf

import i18n
import legacy

SR = 44100
# folders of the takes, in the language of the screen: (takes, trash, exports), relative to the song folder
LAYOUTS = {
    'en': ('Takes', os.path.join('Takes', 'Trash'), 'Exports'),
    'pt': (os.path.join('Gravações', 'Tentativas'), os.path.join('Gravações', 'Tentativas', 'Lixeira'),
           os.path.join('Gravações', 'Exportados')),
    'es': (os.path.join('Grabaciones', 'Tomas'), os.path.join('Grabaciones', 'Tomas', 'Papelera'),
           os.path.join('Grabaciones', 'Exportados')),
}


# ------------------------------------------------------------------ alignment
def fit_line(x, y):
    """Line y = a + b·x by least squares, dropping the blocks that Windows delayed (points off the line)."""
    x = np.asarray(x, np.float64); y = np.asarray(y, np.float64)
    x0, y0 = x.mean(), y.mean()
    xc, yc = x - x0, y - y0
    ok = np.ones(len(x), bool)
    for _ in range(4):
        b, a = np.polyfit(xc[ok], yc[ok], 1)
        r = yc - (a + b * xc)
        med = np.median(r[ok]); mad = np.median(np.abs(r[ok] - med)) * 1.4826 + 1e-5
        new = np.abs(r - med) < 4 * mad
        if new.sum() < max(5, len(x) // 4) or (new == ok).all():
            break
        ok = new
    return y0 + a - b * x0, b


def align(inputs, outputs, input_latency_s, clocks=None):
    """inputs: [(time of the input block, samples recorded up to the end of the block)];
    outputs: [(time when the output block starts sounding, song frame at the start of the block)];
    clocks: (s_in, s_out), seconds per sample measured over the session (firmer than those of the take alone).
    Returns (start, alpha): sample n of the take matches frame start + n·alpha of the song."""
    if len(inputs) < 3 or len(outputs) < 3:
        return None
    ti = np.array([e[0] for e in inputs], np.float64); ni = np.array([e[1] for e in inputs], np.float64)
    to = np.array([s[0] for s in outputs], np.float64); mo = np.array([s[1] for s in outputs], np.float64)
    if clocks:
        s_in, s_out = clocks
        a_in = float(np.median(ti - ni * s_in))
        a_out = float(np.median(to - mo * s_out))
    else:
        a_in, s_in = fit_line(ni, ti)     # time ~ a_in + n·s_in (n = end of the block)
        a_out, s_out = fit_line(mo, to)   # time when frame m sounds ~ a_out + m·s_out
    start = (a_in - input_latency_s - a_out) / s_out
    alpha = s_in / s_out
    return float(start), float(alpha)


def resample(x, alpha):
    """Fixes the clock difference: stretches (alpha > 1) or shrinks the take to match the song."""
    from scipy.signal import resample_poly
    fr = Fraction(alpha).limit_denominator(100000)
    if fr.numerator == fr.denominator:
        return x
    return resample_poly(x, fr.numerator, fr.denominator, axis=0).astype(np.float32)


def mono_if_same(x):
    """Keeps one channel when stereo adds nothing: both sides equal (a patch without stereo effects) or one side silent
    (an interface with the guitar on input 1 only)."""
    if x.ndim == 1:
        return x
    if x.shape[1] == 1:
        return x[:, 0]
    L, R = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    eL, eR = np.dot(L, L), np.dot(R, R)
    e = max(eL, eR)
    if e < 1e-9:
        return x[:, 0]
    d = L - R
    if np.dot(d, d) / e < 1e-4:  # difference 40 dB below the signal
        return (0.5 * (x[:, 0] + x[:, 1])).astype(np.float32)
    if min(eL, eR) / e < 1e-5:   # one side 50 dB below the other: just noise
        return x[:, 0 if eL >= eR else 1].copy()
    return x


# ------------------------------------------------------------------ latency calibration ("Fix the timing")
def attacks(x, sr=SR, refractory_s=0.15):
    """Samples where the strums start. For the calibration: muted strings and spaced clicks."""
    m = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
    h = max(1, sr // 1000)                    # 1 ms windows
    n = len(m) // h
    if n < 10:
        return []
    env = m[:n * h].reshape(n, h).max(axis=1)
    top = float(np.percentile(env, 99.7))
    floor = float(np.percentile(env, 30))
    threshold = max(floor * 6, top * 0.12, 10 ** (-60 / 20))
    out, i, skip = [], 0, int(refractory_s * 1000)
    while i < n:
        if env[i] >= threshold:
            j1 = min(n, i + 30)
            peak = float(env[i:j1].max())
            s0 = max(0, (i - 5) * h); s1 = j1 * h
            k = int(np.argmax(m[s0:s1] >= 0.25 * peak))
            out.append(s0 + k)
            i = j1 + skip
        else:
            i += 1
    return out


def measure_latency(attacks_s, clicks_s, window_s=0.25):
    """Typical delay (median) between the clicks and the strums, in ms. Ignores the 1st click (it is for getting the beat)."""
    diffs = []
    for c in clicks_s[1:]:
        near = [a - c for a in attacks_s if abs(a - c) <= window_s]
        if near:
            diffs.append(min(near, key=abs))
    if len(diffs) < 4:
        return None
    d = np.array(diffs) * 1000
    med = float(np.median(d))
    return {'ms': med, 'spread_ms': float(np.median(np.abs(d - med))), 'hits': len(diffs), 'clicks': len(clicks_s) - 1}


# ------------------------------------------------------------------ MP3 and ID3 tags
def id3_tag(title, artist, album, year, comment=''):
    """ID3v2.3 tag (title, artist, album, year and comment) in UTF-16, so phones show the right names."""
    def frame(fid, data):
        return fid.encode('ascii') + struct.pack('>I', len(data)) + b'\x00\x00' + data

    def txt(s):
        return b'\xff\xfe' + str(s).encode('utf-16-le')

    body = b''.join(frame(f, b'\x01' + txt(v)) for f, v in
                    (('TIT2', title), ('TPE1', artist), ('TALB', album), ('TYER', year)) if v)
    if comment:
        body += frame('COMM', b'\x01' + b'eng' + txt('') + b'\x00\x00' + txt(comment))
    n = len(body)
    size = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])  # "synchsafe" size
    return b'ID3\x03\x00\x00' + size + body


def read_id3(path):
    """Text frames of an ID3v2.3/2.4 tag at the start of an MP3 ({'TIT2': title, 'TPE1': artist, ...})."""
    with open(path, 'rb') as f:
        b = f.read(10)
        if b[:3] != b'ID3':
            return {}
        ver = b[3]
        n = (b[6] << 21) | (b[7] << 14) | (b[8] << 7) | b[9]
        data = f.read(n)
    i, out = 0, {}
    while i + 10 <= len(data):
        fid = data[i:i + 4].decode('ascii', 'replace')
        raw = data[i + 4:i + 8]
        size = ((raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]) if ver >= 4 else struct.unpack('>I', raw)[0]
        if not size or not fid.strip('\x00'):
            break
        d = data[i + 10:i + 10 + size]
        if fid.startswith('T') and d:
            enc, body = d[0], d[1:]
            codec = {0: 'latin-1', 1: 'utf-16', 2: 'utf-16-be', 3: 'utf-8'}.get(enc, 'latin-1')
            try:
                out[fid] = body.decode(codec).rstrip('\x00').split('\x00')[0].strip()
            except UnicodeDecodeError:
                pass
        i += 10 + size
    return out


def export_mp3(audio, sr, dest, title, artist, album, comment=''):
    import lameenc
    x = audio if audio.ndim == 2 else audio[:, None]
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.891:
        x = x * (0.891 / peak)
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)
    enc = lameenc.Encoder()
    enc.set_bit_rate(320); enc.set_in_sample_rate(sr); enc.set_channels(pcm.shape[1]); enc.set_quality(2)
    data = enc.encode(np.ascontiguousarray(pcm).tobytes()) + enc.flush()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(id3_tag(title, artist, album, str(datetime.date.today().year), comment))
        f.write(data)
    return dest


# ------------------------------------------------------------------ saving and organizing
def safe_name(s):
    return re.sub(r'[<>:"/\\|?*]', '-', s).strip()


def peaks(x, sr=SR, window_s=0.02):
    """Contour to draw the waveform: a peak every 20 ms, from 0 to 255 (square root, to see the quiet parts)."""
    m = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
    w = int(sr * window_s)
    n = len(m) // w
    if n == 0:
        return np.zeros(0, np.uint8)
    p = m[:n * w].reshape(n, w).max(axis=1)
    return np.clip(np.round(np.sqrt(np.minimum(p, 1.0)) * 255), 0, 255).astype(np.uint8)


def layout(song_folder, lang):
    """(takes, trash, exports) folders: the layout that already exists in the song, else the one of the language."""
    order = [lang] + [k for k in LAYOUTS if k != lang]
    for k in order:
        if os.path.isdir(os.path.join(song_folder, LAYOUTS[k][0])):
            return tuple(os.path.join(song_folder, p) for p in LAYOUTS[k])
    return tuple(os.path.join(song_folder, p) for p in LAYOUTS.get(lang, LAYOUTS['en']))


class Takes:
    def __init__(self, song_folder, lang='en'):
        self.dir, self.trash, self.exports = layout(song_folder, lang)
        self.tmp = os.path.join(self.dir, '.recording')

    def _records(self, folder):
        out = []
        for p in glob.glob(os.path.join(folder, '*.json')):
            try:
                with open(p, encoding='utf-8') as fh:
                    f = json.load(fh)
                if legacy.is_old_take(f):
                    f = legacy.take(f)
                f['_json'] = p
                out.append(f)
            except Exception:
                pass
        return out

    def list(self):
        fs = [f for f in self._records(self.dir) if os.path.exists(os.path.join(self.dir, f.get('file', '')))]
        return sorted(fs, key=lambda f: f.get('created', ''), reverse=True)

    def find(self, tid):
        for f in self.list():
            if f['id'] == tid:
                return f
        raise KeyError(f'take not found: {tid}')

    def next_number(self, part):
        nums = [f.get('number') or 0 for f in self._records(self.dir) + self._records(self.trash) if f.get('part') == part]
        return max(nums, default=0) + 1

    def save(self, audio, part, part_name, start_s, extra):
        os.makedirs(self.dir, exist_ok=True)
        now = datetime.datetime.now()
        num = self.next_number(part)
        base = safe_name(i18n.t('take.file', date=f'{now:%Y-%m-%d %Hh%M}', part=part_name, n=num))
        path = os.path.join(self.dir, base + '.flac')
        sf.write(path, audio, SR, subtype='PCM_24')
        channels = 1 if audio.ndim == 1 else audio.shape[1]
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if len(audio) else 0.0
        short = i18n.t('part.short.' + part) if i18n.has('part.short.' + part) else part_name
        rec = {
            'version': 2, 'id': base, 'number': num, 'name': i18n.t('take.name', part=short, n=num),
            'part': part, 'part_name': part_name, 'created': now.isoformat(timespec='seconds'),
            'file': base + '.flac', 'sample_rate': SR, 'channels': channels, 'duration_s': round(len(audio) / SR, 3),
            'start_s': round(float(start_s), 5), 'offset_ms': 0.0, 'favorite': False,
            'peak_db': round(20 * np.log10(max(peak, 1e-9)), 1), 'rms_db': round(20 * np.log10(max(rms, 1e-9)), 1),
            'peaks': base64.b64encode(peaks(audio).tobytes()).decode('ascii'),
        }
        rec.update(extra)
        with open(os.path.join(self.dir, base + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
        return rec

    def update(self, tid, **fields):
        f = self.find(tid)
        path = f.pop('_json')
        for k, v in fields.items():
            if k in ('name', 'favorite', 'offset_ms', 'analysis'):
                f[k] = v
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(f, fh, ensure_ascii=False, indent=1)
        return f

    def delete(self, tid):
        f = self.find(tid)
        os.makedirs(self.trash, exist_ok=True)
        for p in (os.path.join(self.dir, f['file']), f['_json']):
            shutil.move(p, os.path.join(self.trash, os.path.basename(p)))
        return f

    def restore(self, tid):
        for f in self._records(self.trash):
            if f['id'] == tid:
                for name in (f['file'], os.path.basename(f['_json'])):
                    shutil.move(os.path.join(self.trash, name), os.path.join(self.dir, name))
                return f
        raise KeyError(f'take is not in the trash: {tid}')

    def audio(self, rec):
        x, sr = sf.read(os.path.join(self.dir, rec['file']), dtype='float32', always_2d=True)
        if sr != SR:
            raise ValueError(f'{rec["file"]}: {sr} Hz')
        return x

    def rescue(self):
        """A recording left half-way (the app closed in the middle): goes to <takes>/Rescued, without alignment."""
        dest = os.path.join(self.dir, 'Rescued')
        moved = []
        for tmp in (self.tmp, os.path.join(self.dir, '.gravando')):
            for p in glob.glob(os.path.join(tmp, '*.wav')):
                try:
                    os.makedirs(dest, exist_ok=True)
                    shutil.move(p, os.path.join(dest, os.path.basename(p)))
                    moved.append(os.path.basename(p))
                except OSError:
                    pass
        return moved
