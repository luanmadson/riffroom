# SPDX-License-Identifier: GPL-3.0-or-later
"""Riffroom audio engine (sounddevice/PortAudio):
  - output: mixes the band parts (smoothed volume and on/off), the takes chosen in place of the original part,
    the count-in and the metronome; loop; starts, stops and jumps without clicks;
  - input: the audio interface (the GT-100 when connected, or the chosen one), always open for the meter;
    when recording, keeps the audio and the time of each block.
The times of the input and output blocks are used to align the take with the song (takes.align)."""
import collections
import logging
import math
import os
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import butter, sosfilt

import i18n

SR = 44100
OUT_BLOCK = 2048
IN_BLOCK = 1024
FADE = 256                                   # ~6 ms ramp to start, stop and splice without clicks
RAMP_UP = np.linspace(0.0, 1.0, FADE, dtype=np.float32)
RAMP_DOWN = RAMP_UP[::-1].copy()
SKIP = ('Digital Output', 'SPDIF', 'S/PDIF', 'Sound Mapper', 'Primary Sound', 'Driver de som', 'Mapeador de som')
log = logging.getLogger('riffroom')


def click_sound(level_db):
    """Short dry click (high noise with a 'ping'), for the count-in and the metronome."""
    L = int(0.06 * SR); t = np.arange(L) / SR
    rng = np.random.default_rng(7)
    noise = sosfilt(butter(4, 5500, 'hp', fs=SR, output='sos'), rng.standard_normal(L)) * np.exp(-t / 0.018)
    ping = np.sin(2 * np.pi * 2200 * t) * np.exp(-t / 0.006)
    x = 0.8 * noise / np.abs(noise).max() + 0.35 * ping
    return (10 ** (level_db / 20) * x / np.abs(x).max()).astype(np.float32)


def no_port(name):
    """Removes the USB port number Windows puts in the name: 'PRIMARY (3- GT-100 Ver2-1)' -> 'PRIMARY (GT-100 Ver2-1)'."""
    return re.sub(r'\(\d+- ', '(', name or '').strip()


def device_of(name):
    """The device, without the endpoint name: 'Alto-falantes (Realtek(R) Audio)' -> 'Realtek(R) Audio'."""
    n = no_port(name)
    i = n.find('(')
    return n[i + 1:].strip().rstrip(')').strip() if i >= 0 else n


def short_name(name):
    """Short name for the screen: 'Speakers (Realtek(R) Audio)' -> 'Speakers'; 'PRIMARY (GT-100 Ver2-1)' -> 'GT-100 Ver2-1'."""
    if not name:
        return ''
    n = no_port(name)
    i = n.find('(')
    if i < 0:
        return n
    endpoint = n[:i].strip()
    generic = ('analog', 'line', 'linha', 'entrada', 'input', 'output', 'saída', 'in ', 'out ')
    return device_of(n) if (not endpoint or endpoint.isupper() or endpoint.lower().startswith(generic)) else endpoint


def same_device(a, b):
    """Compares device names without the USB port number (MME cuts names at 31 characters: the start counts)."""
    x, y = no_port(a), no_port(b)
    if x == y:
        return True
    k = min(len(x), len(y))
    return k >= 24 and x[:k] == y[:k]


def is_gt100(name):
    return 'GT-100' in (name or '')


def find_devices(output_mode, input_name=None):
    """For each audio system (WASAPI and, as a fallback, MME): the input and the output.
    Input: the chosen one (by name); without a choice, the GT-100 (PRIMARY) if connected, else the Windows default input.
    Output 'pc': the Windows default (except digital outputs, which make no sound); 'interface': the output of the
    same device as the input."""
    devs = sd.query_devices()
    options = []
    for api in ('Windows WASAPI', 'MME'):
        ia = next((i for i, h in enumerate(sd.query_hostapis()) if h['name'] == api), None)
        if ia is None:
            continue
        h = sd.query_hostapis(ia)
        items = [(i, d) for i, d in enumerate(devs) if d['hostapi'] == ia]
        ins = [i for i, d in items if d['max_input_channels'] >= 1 and not any(r in d['name'] for r in SKIP)]
        if input_name:
            inp = next((i for i in ins if same_device(devs[i]['name'], input_name)), None)
        else:
            inp = next((i for i in ins if is_gt100(devs[i]['name']) and 'PRIMARY' in devs[i]['name']), None)
            if inp is None and h['default_input_device'] in ins:
                inp = h['default_input_device']
        outs = [i for i, d in items if d['max_output_channels'] >= 2]
        interface_out = None
        if inp is not None:
            dev, in_name = device_of(devs[inp]['name']), no_port(devs[inp]['name'])
            same = [i for i in outs if device_of(devs[i]['name']) == dev]
            equal = [i for i in same if no_port(devs[i]['name']) == in_name]
            interface_out = (equal or same or [None])[0]
        pcs = [i for i in outs if not any(r in devs[i]['name'] for r in SKIP)]
        default = h['default_output_device']
        if output_mode == 'interface' and interface_out is not None:
            out = interface_out
        elif default in pcs:
            out = default
        else:
            pref = [i for i in pcs if any(k in devs[i]['name'] for k in ('Alto-falantes', 'Speakers', 'Fone', 'Headphone'))]
            out = (pref or pcs or [None])[0]
        options.append({'api': api, 'input': inp, 'output': out, 'interface_output': interface_out,
                        'inputs': [no_port(devs[i]['name']) for i in ins]})
    return options


class Part:
    def __init__(self, pid, name, data, gain):
        self.id, self.name, self.data, self.gain = pid, name, data, gain
        self.on = True
        self.volume_db = 0.0
        self.source = 'original'   # 'original' | 'take' | 'both'
        self.take = None           # id of the take used in 'take' and 'both'
        self.g_orig = 0.0          # gain applied right now to the original part (smoothed block by block)
        self.g_take = 0.0          # ... and to the take

    def targets(self):
        v = 10 ** (self.volume_db / 20) if self.on else 0.0
        go = self.gain * v if self.source in ('original', 'both') else 0.0
        gt = v if (self.source in ('take', 'both') and self.take) else 0.0
        return go, gt


class Capture:
    """Records the input into a temporary WAV (own thread) and keeps the times of the input and output blocks."""

    def __init__(self, tmp_dir):
        os.makedirs(tmp_dir, exist_ok=True)
        self.path = os.path.join(tmp_dir, time.strftime('%Y%m%d-%H%M%S') + '.wav')
        self.file = sf.SoundFile(self.path, 'w', samplerate=SR, channels=2, subtype='FLOAT')
        self.queue = queue.Queue()
        self.inputs = []           # (time of the block, samples up to the end of the block)
        self.outputs = []          # (time when the block sounds, song frame at the start of the block)
        self.n = 0
        self.thread = threading.Thread(target=self._write, daemon=True, name='recorder')
        self.thread.start()

    def receive(self, data, when):
        self.n += len(data)
        self.inputs.append((when, self.n))
        self.queue.put(data.copy())

    def _write(self):
        while True:
            x = self.queue.get()
            if x is None:
                break
            try:
                self.file.write(x)
            except Exception:
                log.exception('recorder')
        self.file.close()

    def close(self):
        self.queue.put(None)
        self.thread.join(timeout=60)


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.song = None
        self.n_total = 0
        self.parts = []
        self.takes = {}                    # id -> {'data': float32 [n, c], 'start': frame}
        self.pos = 0
        self.playing = False
        self.loop = None                   # (a, b) in frames
        self.loop_on = False
        self.count_in = True
        self.metronome = False
        self.master_db = 0.0
        self.clicks = (np.zeros(0, np.int64), [])
        self.metro = (np.zeros(0, np.int64), [])
        self.cmds = collections.deque()
        self.out_stream = self.in_stream = None
        self.api = self.in_api = self.out_name = self.in_name = None
        self.input_latency = 0.0
        self.output_mode = 'pc'
        self.preferred_input = None        # input chosen on the screen (None: automatic)
        self.inputs = []                   # names of the available inputs, for the menu
        self.has_interface_output = False
        self.in_channels = 2
        self.error = None
        self.capture = None
        self.recent = collections.deque(maxlen=64)
        # (time, samples) history of both streams, to measure the clock of each device during the session
        self.in_history = collections.deque(maxlen=3000)
        self.out_history = collections.deque(maxlen=3000)
        self.n_in = 0; self.n_out = 0
        self.peak = 0.0; self.sq_sum = 0.0; self.sq_n = 0
        self.stop_at = None
        self.ended = False                 # reached the end (song, recorded loop or calibration clicks)
        self.count_until = None            # frame where this count-in ends (for the screen)
        self.virtual = None                # calibration: clicks only, without the song
        self._stop_req = None
        self._jump = None
        self._playing_block = False        # what the audio block is doing (self.playing is what the screen asked)
        self._fade_in = None               # anchor of the fade-in ramp (where the music restarted)
        self._pos_before = 0
        self.buf = np.zeros((16384, 2), np.float32)
        self.snd_strong, self.snd_weak = click_sound(-14), click_sound(-20)
        self.snd_count1, self.snd_count = click_sound(-16), click_sound(-21)
        self.snd_calib = click_sound(-10)

    # ------------------------------------------------------------ devices
    def _close_streams(self):
        for s in (self.out_stream, self.in_stream):
            if s is not None:
                try:
                    s.abort(); s.close()
                except Exception:
                    log.exception('close stream')
        self.out_stream = self.in_stream = None
        self.out_name = self.in_name = None
        self.in_history.clear(); self.out_history.clear()
        self.n_in = 0; self.n_out = 0

    def clocks(self, window_s=1200, minimum_s=90):
        """Seconds per sample of the input and of the output, measured over the session (the streams stay open).
        Returns (s_in, s_out) or None while there is not enough time for a firm measure."""
        from takes import fit_line
        out = []
        for h in (list(self.in_history), list(self.out_history)):
            if len(h) < 20:
                return None
            end = h[-1][0]
            h = [x for x in h if x[0] >= end - window_s]
            if h[-1][0] - h[0][0] < minimum_s:
                return None
            _, s = fit_line([x[1] for x in h], [x[0] for x in h])
            out.append(s)
        return tuple(out)

    def open(self, output_mode=None, input_name=False):
        """Opens the output and the input. input_name: chosen input, None for automatic, False to keep the current one."""
        with self.lock:
            if output_mode:
                self.output_mode = 'interface' if output_mode == 'gt100' else output_mode
            if input_name is not False:
                self.preferred_input = input_name or None
            self._close_streams()
            self.error = None
            options = find_devices(self.output_mode, self.preferred_input)
            self.inputs = options[0]['inputs'] if options else []
            self.has_interface_output = any(op['interface_output'] is not None for op in options)
            for op in options:
                if op['output'] is None:
                    continue
                extra = sd.WasapiSettings(auto_convert=True) if op['api'] == 'Windows WASAPI' else None
                try:
                    s = sd.OutputStream(device=op['output'], samplerate=SR, channels=2, dtype='float32', blocksize=OUT_BLOCK,
                                        latency='high', extra_settings=extra, callback=self._cb_out)
                    s.start()
                except Exception as e:
                    log.warning('output %s (%s) did not open: %s', op['output'], op['api'], e)
                    continue
                self.out_stream, self.api = s, op['api']
                self.out_name = sd.query_devices(op['output'])['name']
                log.info('output: %s (%s), latency %.1f ms', self.out_name, self.api, s.latency * 1000)
                break
            for op in options:
                if op['input'] is None:
                    continue
                extra = sd.WasapiSettings(auto_convert=True) if op['api'] == 'Windows WASAPI' else None
                channels = max(1, min(2, sd.query_devices(op['input'])['max_input_channels']))
                try:
                    e = sd.InputStream(device=op['input'], samplerate=SR, channels=channels, dtype='float32', blocksize=IN_BLOCK,
                                       latency='high', extra_settings=extra, callback=self._cb_in)
                    e.start()
                except Exception as ex:
                    log.warning('input %s (%s) did not open: %s', op['input'], op['api'], ex)
                    continue
                self.in_stream, self.in_api = e, op['api']
                self.in_channels = channels
                self.in_name = sd.query_devices(op['input'])['name']
                self.input_latency = max(0.0, float(e.latency) - IN_BLOCK / SR)
                log.info('input: %s (%s), latency %.1f ms', self.in_name, self.in_api, e.latency * 1000)
                break
            if self.out_stream is None:
                self.error = i18n.t('err.no_output')

    def rescan(self):
        """Closes everything and asks PortAudio for the new list of devices (to find an interface connected later)."""
        with self.lock:
            self.playing = False
            self._close_streams()
            try:
                sd._terminate(); sd._initialize()
            except Exception:
                log.exception('restart PortAudio')
            self.open()

    def check(self):
        """Checks that the streams are alive (the interface may have been unplugged). Returns True if something changed."""
        changed = False
        e = self.in_stream
        if e is not None and not e.active:
            log.warning('the input stopped (interface unplugged?)')
            try:
                e.close()
            except Exception:
                pass
            self.in_stream = None; self.in_name = None; changed = True
        s = self.out_stream
        if s is not None and not s.active:
            log.warning('the output stopped; reopening')
            self.open(); changed = True
        return changed

    def no_input_message(self):
        if self.preferred_input:
            return i18n.t('err.input_missing', name=short_name(self.preferred_input))
        return i18n.t('err.no_input')

    # ------------------------------------------------------------ song and takes
    def load(self, song):
        with self.lock:
            self.playing = False
            self._playing_block = False
            self.cmds.clear()
            self.capture = None
            self.virtual = None
            self.song = song
            self.n_total = song.n
            self.parts = [Part(p['id'], song.name_of(p['id']), p['data'], p['gain']) for p in song.parts]
            self.takes = {}
            self.pos = 0
            self.loop = None; self.loop_on = False
            q = np.round(song.beats * SR).astype(np.int64)
            ok = (q >= 0) & (q < song.n) & (song.beat_bar > 0)
            self.metro = (q[ok], [self.snd_strong if f else self.snd_weak for f in song.beat_strong[ok]])

    def part(self, pid):
        for p in self.parts:
            if p.id == pid:
                return p
        raise KeyError(pid)

    def set_take(self, tid, data, start_s):
        self.takes[tid] = {'data': data, 'start': int(round(start_s * SR))}

    # ------------------------------------------------------------ transport (called by the screen)
    def position(self):
        """Position (s) of what is sounding now: PortAudio tells when each block plays."""
        if not self.playing:
            return self.pos / SR
        try:
            now = self.out_stream.time
        except Exception:
            return self.pos / SR
        first = None
        for dac, frames, segs in reversed(self.recent):
            if segs:
                first = segs[0][1]
            if dac <= now:
                if not segs:
                    return (first if first is not None else self.pos) / SR
                d = (now - dac) * SR
                for off, q, n, _, _ in segs:
                    if off <= d < off + n:
                        return (q + d - off) / SR
                off, q, n, _, _ = segs[-1]
                return (q + n) / SR
        return (first if first is not None else self.pos) / SR

    def play(self, start=None, no_count=False):
        with self.lock:
            if self.song is None:
                return
            song = self.song
            p = int(round(start * SR)) if start is not None else self.pos
            lp = self.loop if self.loop_on else None
            if lp and not (lp[0] <= p < lp[1] - SR // 2):
                p = lp[0]
            if p >= self.n_total - SR:
                p = 0
            ini, cq, cs, until = p, [], [], None
            if self.count_in and not no_count:
                t = p / SR
                B = song.m.get('bars') or [0.0]
                if t < B[0] + 0.05:
                    # start of the song: the count-in of the song itself (in the drum track, when it has one);
                    # otherwise the app plays the clicks
                    ini = 0
                    drums = next((x for x in self.parts if x.id == 'drums'), None)
                    hearing = drums is not None and drums.on and drums.source != 'take'
                    if not (song.m.get('count_in_in_drums') and hearing):
                        for k, c in enumerate(song.m.get('count_in', [])):
                            cq.append(int(c * SR)); cs.append(self.snd_count1 if k == 0 else self.snd_count)
                    until = int(B[0] * SR)
                else:
                    beats = song.beats_before(t, 4)
                    if beats:
                        for k, c in enumerate(beats):
                            cq.append(int(round(c * SR))); cs.append(self.snd_count1 if k == 0 else self.snd_count)
                        ini = max(0, int((beats[0] - 0.25) * SR))
                        until = p
            stop_at = None
            if self.capture is not None and lp:
                stop_at = lp[1]      # recording with a loop: one pass only
            self.virtual = None
            self.ended = False
            self.cmds.append(('play', ini, np.array(cq, np.int64), cs, stop_at, until))
            self.playing = True      # the screen already shows "playing"; the next block applies it

    def pause(self):
        with self.lock:
            if not self.playing:
                return
            pos = int(self.position() * SR)
            self.cmds.append(('stop', pos))
            self.playing = False

    def seek(self, t):
        with self.lock:
            q = max(0, min(int(round(t * SR)), max(0, self.n_total - 1)))
            if self.playing:
                self.cmds.append(('seek', q))
            else:
                self.pos = q

    def set_loop(self, a, b, on=True):
        with self.lock:
            if a is None or b is None:
                self.loop = None; self.loop_on = False
                return
            qa, qb = int(round(a * SR)), int(round(b * SR))
            if qb - qa < SR // 2:
                raise ValueError(i18n.t('err.loop_short'))
            self.loop = (max(0, qa), min(self.n_total, qb))
            self.loop_on = on

    # ------------------------------------------------------------ recording and latency calibration
    def record(self, tmp_dir):
        with self.lock:
            if self.in_stream is None:
                raise RuntimeError(self.no_input_message())
            if self.capture is not None:
                return
            start = None
            if self.playing:  # already playing: restarts at the start of the current bar, with the count-in before
                start = self.song.bar_start(self.position())
                self.pause()
                self.cmds.append(('wait',))
            self.capture = Capture(tmp_dir)
            self.play(start=start)

    def calibrate(self, tmp_dir, n=8, start=1.5, step=0.6):
        with self.lock:
            if self.in_stream is None:
                raise RuntimeError(self.no_input_message())
            if self.playing:
                self._pos_before = int(self.position() * SR)
                self.pause()
                self.cmds.append(('wait',))
            else:
                self._pos_before = self.pos
            clicks = [start + i * step for i in range(n)]
            self.virtual = {'n': int((start + n * step + 0.8) * SR), 'clicks_s': clicks}
            self.capture = Capture(tmp_dir)
            self.ended = False
            self.cmds.append(('play', 0, np.array([int(c * SR) for c in clicks], np.int64), [self.snd_calib] * n, None, None))
            self.playing = True

    def end_capture(self):
        """Stops the recording (or the calibration) and returns the capture, with the file already closed."""
        with self.lock:
            cap = self.capture
            self.capture = None
            if self.playing:
                self.pause()
            was_virtual = self.virtual is not None
            if was_virtual:
                self.cmds.append(('back', getattr(self, '_pos_before', 0)))
        if cap is not None:
            cap.close()
        return cap, was_virtual

    # ------------------------------------------------------------ meter
    def read_level(self):
        pk, s2, n = self.peak, self.sq_sum, self.sq_n
        self.peak = 0.0; self.sq_sum = 0.0; self.sq_n = 0
        if self.in_stream is None:
            return None
        return (20 * math.log10(max(pk, 1e-6)), 10 * math.log10(max(s2 / n, 1e-12)) if n else -120.0, pk >= 0.999)

    # ------------------------------------------------------------ audio blocks (PortAudio thread)
    def _cb_in(self, indata, frames, tinfo, status):
        try:
            pk = float(np.max(np.abs(indata)))
            if pk > self.peak:
                self.peak = pk
            ch = indata[:, 0]
            self.sq_sum += float(np.dot(ch, ch)); self.sq_n += frames
            self.n_in += frames
            if (self.n_in // frames) % 20 == 0:
                self.in_history.append((tinfo.currentTime, self.n_in))
            c = self.capture
            if c is not None:
                c.receive(indata if indata.shape[1] == 2 else np.repeat(indata[:, :1], 2, axis=1), tinfo.currentTime)
        except Exception:
            log.exception('input block')

    def _plan(self, frames):
        """Applies the commands from the screen and decides which pieces of the song play in this block:
        [(position in the block, song frame, size, fade-in anchor, fade-out anchor)].
        The anchors are song frames: the fade-in goes from fi to fi+FADE; the fade-out, from fo-FADE to fo.
        So the ramp stays right even when it crosses the border between two blocks."""
        while self.cmds:
            c = self.cmds.popleft()
            if c[0] == 'play':
                _, ini, cq, cs, stop_at, until = c
                self.pos = ini; self.clicks = (cq, cs); self.stop_at = stop_at; self.count_until = until
                self._playing_block = True; self._stop_req = None; self._jump = None; self._fade_in = ini
            elif c[0] == 'stop':
                if self._playing_block:
                    self._stop_req = c[1]
                else:
                    self.pos = c[1]
            elif c[0] == 'seek':
                if self._playing_block:
                    self._jump = c[1]
                else:
                    self.pos = c[1]
            elif c[0] == 'back':
                self.virtual = None
                self.pos = c[1]
            elif c[0] == 'wait':
                break       # the 'stop' of this block must sound before the next 'play'
        if not self._playing_block:
            return []
        segs, p, off = [], self.pos, 0
        if self._stop_req is not None:
            n = min(FADE, frames)
            segs.append((0, p, n, self._fade_in, p + n))
            self._playing_block = False
            self.pos = self._stop_req; self._stop_req = None
            self.count_until = None
            return segs
        if self._jump is not None:
            n = min(FADE, frames)
            segs.append((0, p, n, self._fade_in, p + n))
            p, off = self._jump, n
            self._fade_in = p
            self._jump = None
        total = self.virtual['n'] if self.virtual else self.n_total
        while off < frames:
            lp = self.loop if (self.loop_on and self.capture is None and self.virtual is None) else None
            if lp and p >= lp[1]:   # end of the loop: back to its start
                p = lp[0]; self._fade_in = p
            lim = total if not lp else min(total, lp[1])
            if self.stop_at is not None:
                lim = min(lim, self.stop_at)
            n = min(frames - off, lim - p)
            if n > 0:
                segs.append((off, p, n, self._fade_in, lim))
                p += n; off += n
                continue
            if lp and p >= lp[1]:
                continue
            self._playing_block = False
            self.playing = False
            self.ended = True
            break
        self.pos = p
        return segs

    def _mix(self, out, part, segs, frames):
        go1, gt1 = part.targets()
        go0, gt0 = part.g_orig, part.g_take
        if go0 or go1:
            ramp = None if go0 == go1 else (np.linspace(go0, go1, frames, dtype=np.float32) / np.float32(32768))[:, None]
            k = np.float32(go1 / 32768)
            for off, q, n, _, _ in segs:
                x = part.data[q:q + n]
                m = len(x)
                if m:
                    out[off:off + m] += x * (k if ramp is None else ramp[off:off + m])
        tk = self.takes.get(part.take) if part.take else None
        if (gt0 or gt1) and tk is not None:
            d, ini = tk['data'], tk['start']
            ramp = None if gt0 == gt1 else np.linspace(gt0, gt1, frames, dtype=np.float32)[:, None]
            for off, q, n, _, _ in segs:
                i0 = q - ini
                a0, a1 = max(i0, 0), min(i0 + n, len(d))
                if a1 <= a0:
                    continue
                o0 = off + (a0 - i0)
                g = np.float32(gt1) if ramp is None else ramp[o0:o0 + a1 - a0]
                out[o0:o0 + a1 - a0] += d[a0:a1] * g
        part.g_orig, part.g_take = go1, gt1

    def _add_clicks(self, out, segs):
        sources = [self.clicks]
        if self.metronome and self.virtual is None:
            sources.append(self.metro)
        L = len(self.snd_strong)
        for qs, sounds in sources:
            if len(qs) == 0:
                continue
            for off, q, n, _, _ in segs:
                i0 = int(np.searchsorted(qs, q - L, 'left')); i1 = int(np.searchsorted(qs, q + n, 'left'))
                for k in range(i0, i1):
                    c = int(qs[k]); s = sounds[k]
                    a0, a1 = max(c, q), min(c + len(s), q + n)
                    if a1 > a0:
                        out[off + a0 - q:off + a1 - q] += s[a0 - c:a1 - c, None]

    def _cb_out(self, outdata, frames, tinfo, status):
        try:
            segs = self._plan(frames)
            out = self.buf[:frames]
            out.fill(0.0)
            if segs:
                if self.virtual is None:
                    for p in self.parts:
                        self._mix(out, p, segs, frames)
                self._add_clicks(out, segs)
                for off, q, n, fi, fo in segs:
                    if fi is not None:
                        a, b = max(q, fi), min(q + n, fi + FADE)
                        if b > a:
                            out[off + a - q:off + b - q] *= RAMP_UP[a - fi:b - fi, None]
                    if fo is not None:
                        a, b = max(q, fo - FADE), min(q + n, fo)
                        if b > a:
                            out[off + a - q:off + b - q] *= RAMP_DOWN[a - fo + FADE:b - fo + FADE, None]
                g = 10 ** (self.master_db / 20)
                if g != 1.0:
                    out *= np.float32(g)
                np.clip(out, -1.0, 1.0, out=out)
            outdata[:] = out
            self.n_out += frames
            if (self.n_out // frames) % 10 == 0:
                self.out_history.append((tinfo.outputBufferDacTime, self.n_out))
            self.recent.append((tinfo.outputBufferDacTime, frames, segs))
            c = self.capture
            if c is not None and len(segs) == 1 and segs[0][2] == frames:
                c.outputs.append((tinfo.outputBufferDacTime, segs[0][1]))
        except Exception:
            log.exception('output block')
            outdata.fill(0)
