# SPDX-License-Identifier: GPL-3.0-or-later
# Riffroom: play and record along with the separated band (one track per instrument), with the chord and the beat on screen.
# Opens a local server (127.0.0.1 only) and an Edge window in app mode; closes by itself when the window closes.
# An AI assistant can drive it through mcp_server.py, which talks to this same local API.
#
# Usage (from the shortcut): pythonw.exe -I -X utf8 riffroom.py [--song "<song folder>"]
#   --library FOLDER  adds a music folder to the list (kept)     --configure  only saves the options and exits
#   --language en|pt|es                                            --no-window  no window, no auto-close (tests, AI)
#   --port N  (default 8737)
import argparse
import datetime
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = '2.1.0'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.environ.get('RIFFROOM_DATA') or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Riffroom')
OLD_DATA = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'SalaDeEnsaio')
LOG = os.path.join(DATA, 'riffroom.log')
CONFIG = os.path.join(DATA, 'config.json')
PORT = 8737
WEB = os.path.join(HERE, 'web')
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json'}
log = logging.getLogger('riffroom')


def setup_log():
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(LOG) and os.path.getsize(LOG) > 2_000_000:
        os.replace(LOG, LOG + '.old')
    h = logging.FileHandler(LOG, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s: %(message)s'))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:  # pythonw: no console
        f = open(LOG, 'a', encoding='utf-8')
        sys.stdout = sys.stderr = f
    threading.excepthook = lambda a: log.error('error in thread %s', a.thread.name if a.thread else '?',
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


import numpy as np            # noqa: E402
import analysis as AN         # noqa: E402
import engine as E            # noqa: E402
import i18n                   # noqa: E402
import legacy                 # noqa: E402
import lyrics as LY           # noqa: E402
import prepare as PR          # noqa: E402
import song as SG             # noqa: E402
import takes as TK            # noqa: E402

SR = E.SR
t = i18n.t


# ---------------------------------------------------------------- configuration
def read_config():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        pass
    old = os.path.join(OLD_DATA, 'config.json')     # first run after version 1 ("Sala de Ensaio")
    if os.path.exists(old):
        try:
            with open(old, encoding='utf-8') as f:
                cfg = legacy.config(json.load(f), SG.default_library())
            save_config(cfg)
            return cfg
        except Exception:
            log.exception('migrate the old configuration')
    return {}


def save_config(cfg):
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = CONFIG + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CONFIG)
    except Exception:
        log.exception('save configuration')


def libraries(cfg):
    return list(cfg.get('libraries') or [SG.default_library()])


def add_library(cfg, folder):
    folder = os.path.normpath(os.path.abspath(folder))
    libs = cfg.setdefault('libraries', libraries(cfg))
    if not any(os.path.normcase(folder) == os.path.normcase(b) for b in libs):
        libs.append(folder)
    return folder


def pick_folder(title):
    """Windows dialog to choose a folder (tkinter). Returns the path or None."""
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)
    try:
        return filedialog.askdirectory(parent=r, title=title, mustexist=True) or None
    finally:
        r.destroy()


def pick_file(title, filetypes=None):
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)
    try:
        return filedialog.askopenfilename(parent=r, title=title, filetypes=filetypes or [
            ('Audio', '*.mp3 *.wav *.flac *.ogg *.m4a *.aif *.aiff'), ('*', '*.*')]) or None
    finally:
        r.destroy()


def level_tip(peak_db, gt100=False):
    gain = t('tip.gain_gt100') if gt100 else t('tip.gain')
    if peak_db > -1:
        return t('tip.clipped', gain=gain)
    if peak_db < -30:
        return t('tip.too_low', gain=gain)
    if peak_db < -20:
        return t('tip.bit_low')
    return t('tip.good')


def secs_txt(v):
    """+3.60 s (decimal comma in Portuguese and Spanish)."""
    s = f'{v:+.2f}'
    return (s.replace('.', ',') if i18n.language() in ('pt', 'es') else s) + ' s'


def mmss(s):
    s = max(0, int(round(s)))
    return f'{s // 60}:{s % 60:02d}'


def analysis_sentences(res):
    """The analysis in plain words, in the current language."""
    if res.get('too_few'):
        return [t('an.too_few')]
    out = []
    ref = res.get('original') if res.get('reference') == 'original' else res.get('grid')
    who = t('an.ref.original') if res.get('reference') == 'original' else t('an.ref.grid')
    med = ref['median_ms']
    if abs(med) <= 10:
        out.append(t('an.on_time', ms=f'{med:+.0f}', ref=who))
    elif med > 0:
        out.append(t('an.late', ms=f'{med:.0f}', ref=who))
    else:
        out.append(t('an.early', ms=f'{-med:.0f}', ref=who))
    out.append(t('an.consistency', pct=ref['within_40'], spread=f'{ref["spread_ms"]:.0f}'))
    for s in res.get('streaks', [])[:3]:
        key = 'an.streak.late' if s['kind'] == 'late' else 'an.streak.early'
        out.append(t(key, a=s['from_bar'], b=s['to_bar'], ms=f'{abs(s["median_ms"]):.0f}'))
    if res.get('worst_bars'):
        out.append(t('an.worst', bars=', '.join(str(b) for b in sorted(res['worst_bars']))))
    if res.get('missed_bars'):
        mb = res['missed_bars']
        out.append(t('an.missed', bars=', '.join(str(b) for b in mb[:12]) + ('…' if len(mb) > 12 else '')))
    if not res.get('calibrated'):
        out.append(t('an.not_calibrated'))
    return out


class App:
    def __init__(self, keep=False):
        self.keep = keep
        self.cfg = read_config()
        i18n.set_language(self.cfg.get('language', 'en'))
        SG.LIBRARIES[:] = libraries(self.cfg)
        self.lock = threading.RLock()
        self.clients = []
        self.engine = E.Engine()
        self.song = None
        self.takes = None
        self.target = None
        self.recording_part = None
        self.loading = False
        self.busy = None
        self.last_calibration = None
        self.songs = SG.list_songs()
        self.end = threading.Event()
        self.finishing = None
        self.had_client = False
        self.no_client_since = time.time()
        self.dialog = threading.Lock()
        self.jobs = {}
        self.lyrics_lock = threading.Lock()
        self.engine.count_in = bool(self.cfg.get('count_in', True))
        self.engine.metronome = bool(self.cfg.get('metronome', False))
        self.engine.open(self.cfg.get('output_mode', 'pc'), input_name=self.cfg.get('input_device'))
        threading.Thread(target=self._tick, daemon=True, name='tick').start()

    # ------------------------------------------------------------ configuration
    def _save_cfg(self):
        save_config(self.cfg)

    def _device_key(self):
        e = self.engine
        return f'{e.in_api or e.api}|{E.no_port(e.out_name or "-")}|{E.no_port(e.in_name or "-")}'

    def _latency(self):
        return float(self.cfg.get('latency_ms', {}).get(self._device_key(), 0.0))

    def _store_mix(self):
        if not self.song:
            return
        e = self.engine
        self.cfg.setdefault('songs', {})[self.song.id] = {
            'target': self.target, 'master_db': e.master_db,
            'loop': [e.loop[0] / SR, e.loop[1] / SR] if e.loop else None, 'loop_on': e.loop_on,
            'parts': {p.id: {'on': p.on, 'volume_db': p.volume_db, 'source': p.source, 'take': p.take} for p in e.parts}}
        self.cfg['count_in'] = e.count_in
        self.cfg['metronome'] = e.metronome
        self._save_cfg()

    def _restore_mix(self):
        st = self.cfg.get('songs', {}).get(self.song.id)
        e = self.engine
        if not st:  # first time: "you will play" the first part of the list (turned off)
            self.target = self.song.targets[0]
            for p in e.parts:
                p.on = p.id != self.target
            return
        ids = [p.id for p in e.parts]
        self.target = st.get('target') if st.get('target') in ids else self.song.targets[0]
        e.master_db = float(st.get('master_db', 0.0))
        existing = {f['id'] for f in self.takes.list()}
        for p in e.parts:
            s = st.get('parts', {}).get(p.id, {})
            p.on = bool(s.get('on', True))
            p.volume_db = float(s.get('volume_db', 0.0))
            tid = s.get('take')
            if s.get('source', 'original') != 'original' and tid in existing:
                try:
                    self._load_take(tid)
                    p.source, p.take = s['source'], tid
                except Exception:
                    log.exception('restore take')
        if st.get('loop'):
            try:
                e.set_loop(st['loop'][0], st['loop'][1], bool(st.get('loop_on', False)))
            except ValueError:
                pass

    # ------------------------------------------------------------ events for the screen
    def new_client(self):
        q = queue.Queue()
        with self.lock:
            self.clients.append(q)
            self.had_client = True
            self.no_client_since = None
        return q

    def drop_client(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)
            if not self.clients:
                self.no_client_since = time.time()

    def publish(self, kind, data, tick=False):
        msg = f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
        with self.lock:
            cl = list(self.clients)
        for q in cl:
            if tick and q.qsize() > 10:
                continue
            q.put(msg)

    def publish_state(self):
        self.publish('state', self.state())

    def notice(self, text, kind='info', action=None):
        log.info('notice: %s', text)
        self.publish('notice', {'text': text, 'kind': kind, 'action': action})

    def state(self):
        e = self.engine
        parts = []
        if self.song:
            colors = {p['id']: p['color'] for p in self.song.parts}
            for p in e.parts:
                parts.append({'id': p.id, 'name': self.song.name_of(p.id), 'color': colors.get(p.id), 'on': p.on,
                              'volume_db': p.volume_db, 'source': p.source, 'take': p.take})
        key = self._device_key()
        return {
            'version': VERSION, 'language': i18n.language(),
            'song': {'id': self.song.id, 'title': self.song.title, 'artist': self.song.m.get('artist'),
                     'tuning': int(self.song.m.get('tuning') or 0)} if self.song else None,
            'songs': [{k: v for k, v in x.items() if k != 'folder'} for x in self.songs],
            'libraries': list(SG.LIBRARIES),
            'loading': self.loading, 'busy': self.busy,
            'playing': e.playing, 'recording': e.capture is not None and e.virtual is None,
            'calibrating': e.virtual is not None, 'pos': e.position(),
            'loop': [e.loop[0] / SR, e.loop[1] / SR] if e.loop else None, 'loop_on': e.loop_on,
            'count_in': e.count_in, 'metronome': e.metronome, 'master_db': e.master_db,
            'target': self.target, 'targets': self.song.targets if self.song else [], 'recording_part': self.recording_part,
            'parts': parts,
            'devices': {'input_ok': e.in_stream is not None, 'input': e.in_name, 'input_short': E.short_name(e.in_name),
                        'gt100': E.is_gt100(e.in_name) or (e.in_stream is None and E.is_gt100(e.preferred_input)),
                        'input_chosen': e.preferred_input, 'inputs': e.inputs,
                        'output': e.out_name, 'output_short': E.short_name(e.out_name), 'api': e.api,
                        'output_mode': e.output_mode, 'has_interface_output': e.has_interface_output, 'error': e.error},
            'latency_ms': self._latency(), 'calibrated': key in self.cfg.get('latency_ms', {}),
            'last_calibration': self.last_calibration,
            'tour_seen': bool(self.cfg.get('tour_seen')),
            'lyrics': {'show': bool(self.cfg.get('lyrics_show', True)), 'follow': bool(self.cfg.get('lyrics_follow', True))},
            'jobs': [self._job_view(j) for j in self.jobs.values() if j['status'] == 'running'],
        }

    def _tick(self):
        check = 0.0
        while not self.end.is_set():
            time.sleep(0.05)
            try:
                e = self.engine
                if e.ended:
                    e.ended = False
                    threading.Thread(target=self._reached_end, daemon=True).start()
                lvl = e.read_level()
                d = {'t': round(e.position(), 3), 'playing': e.playing,
                     'recording': e.capture is not None and e.virtual is None, 'calibrating': e.virtual is not None}
                if lvl:
                    d['peak_db'], d['rms_db'], d['clip'] = round(lvl[0], 1), round(lvl[1], 1), lvl[2]
                if e.playing and e.count_until is not None:
                    d['count_until'] = e.count_until / SR
                self.publish('tick', d, tick=True)
                now = time.time()
                if now - check > 2:
                    check = now
                    if e.check():
                        self.publish_state()
                        if e.in_stream is None:
                            self.notice(t('msg.input_gone'), 'error')
                with self.lock:
                    since = self.no_client_since
                busy_job = any(j['status'] == 'running' for j in self.jobs.values())
                if not self.keep and not busy_job and since is not None and now - since > (10 if self.had_client else 120):
                    log.info('no window open: closing')
                    self.end.set()
            except Exception:
                log.exception('tick')

    def _reached_end(self):
        if self.engine.capture is not None:
            self.stop()
        else:
            self.publish_state()

    # ------------------------------------------------------------ songs
    def rescan(self):
        SG.LIBRARIES[:] = libraries(self.cfg)
        self.songs = SG.list_songs()
        self.publish_state()

    def open_song(self, sid):
        with self.lock:
            if self.engine.capture is not None:
                raise RuntimeError(t('err.stop_recording_first'))
            self.loading = True
        self.publish_state()
        try:
            self.songs = SG.list_songs()
            item = next((x for x in self.songs if x['id'] == sid), None)
            if item is None:
                raise KeyError(t('err.song_not_found', song=sid))
            if not item['prepared']:
                self.busy = t('busy.preparing')
                self.publish_state()
                try:
                    PR.prepare(item['folder'], log=lambda s: log.info('prepare: %s', s))
                except (ValueError, FileNotFoundError) as ex:
                    raise RuntimeError(t('err.prepare_failed', song=item['title'], error=ex))
                finally:
                    self.busy = None
                self.songs = SG.list_songs()
            song = SG.Song(item['folder'], sid)
            song.screen_data()
            takes = TK.Takes(item['folder'], i18n.language())
            rescued = takes.rescue()
            with self.lock:
                self.engine.load(song)
                self.song, self.takes = song, takes
                self._restore_mix()
                self.cfg['last_song'] = song.id
                self._save_cfg()
            log.info('song opened: %s', song.id)
            if rescued:
                self.notice(t('msg.rescued', n=len(rescued)), 'error')
        finally:
            self.loading = False
            self.publish('song', {'id': self.song.id if self.song else None})
            self.publish_state()

    def reload_song(self):
        if self.song:
            self.open_song(self.song.id)

    def song_data(self):
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        return self.song.screen_data()

    def add_library(self):
        if not self.dialog.acquire(blocking=False):
            raise RuntimeError(t('err.dialog_open'))
        try:
            folder = pick_folder(t('dlg.pick_library'))
        finally:
            self.dialog.release()
        if not folder:
            return
        folder = add_library(self.cfg, folder)
        self._save_cfg()
        self.rescan()
        inside = [x for x in self.songs if os.path.normcase(x['folder']).startswith(os.path.normcase(folder))]
        if inside:
            self.notice(t('msg.songs_found', n=len(inside), folder=os.path.basename(folder)), 'ok')
        else:
            self.notice(t('msg.no_songs_there'), 'error')

    # ------------------------------------------------------------ transport
    def play(self, t0=None):
        self.engine.play(t0)
        self.publish_state()

    def pause(self):
        if self.engine.capture is not None:
            return self.stop()
        self.engine.pause()
        self.publish_state()

    def toggle_play(self):
        if self.engine.playing:
            return self.pause()
        return self.play()

    def seek(self, t0):
        self.engine.seek(t0)
        self.publish_state()

    def home(self):
        e = self.engine
        e.seek(e.loop[0] / SR if (e.loop and e.loop_on) else 0.0)
        self.publish_state()

    def step_bar(self, delta):
        if not self.song:
            return
        B = self.song.m.get('bars', [])
        pos = self.engine.position()
        if not B:
            return self.seek(max(0.0, pos + 5 * delta))
        if pos < B[0] - 0.05:
            dest = B[0] if delta > 0 else 0.0
        else:
            import bisect
            i = bisect.bisect_right(B, pos + 0.05) - 1
            if delta < 0 and pos - B[i] > 0.6:
                delta += 1  # in the middle of the bar: "back" goes to its start
            j = max(0, min(len(B) - 2, i + delta))
            dest = B[j] if not (delta < 0 and i + delta < 0) else 0.0
        self.seek(dest)

    def set_loop(self, c):
        e = self.engine
        if c.get('clear'):
            e.set_loop(None, None)
        elif 'a' in c and 'b' in c:
            e.set_loop(float(c['a']), float(c['b']), bool(c.get('on', True)))
            if e.loop_on and not e.playing:
                e.seek(e.loop[0] / SR)
        elif 'on' in c:
            if e.loop is None:
                raise ValueError(t('err.mark_loop_first'))
            e.loop_on = bool(c['on'])
        self._store_mix()
        self.publish_state()

    def set_part(self, pid, c):
        p = self.engine.part(pid)
        if 'on' in c:
            p.on = bool(c['on'])
        if 'volume_db' in c:
            p.volume_db = max(-40.0, min(6.0, float(c['volume_db'])))
        if 'source' in c:
            if c['source'] == 'original':
                p.source = 'original'
            else:
                tid = c.get('take') or p.take
                if not tid:
                    raise ValueError(t('err.choose_take'))
                self._load_take(tid)
                p.take, p.source = tid, ('both' if c['source'] == 'both' else 'take')
        self._store_mix()
        self.publish_state()

    def set_mode(self, mode):
        ids = [p.id for p in self.engine.parts]
        if mode != 'listen' and mode not in ids:
            raise ValueError(t('err.unknown_part', part=mode))
        for p in self.engine.parts:
            p.on = (mode == 'listen') or p.id != mode
        if mode != 'listen':
            self.target = mode
        self._store_mix()
        self.publish_state()

    def options(self, c):
        e = self.engine
        if 'count_in' in c:
            e.count_in = bool(c['count_in'])
        if 'metronome' in c:
            e.metronome = bool(c['metronome'])
        if 'master_db' in c:
            e.master_db = max(-40.0, min(6.0, float(c['master_db'])))
        if 'target' in c and c['target'] in [p.id for p in e.parts]:
            self.target = c['target']
        if 'tour_seen' in c:
            self.cfg['tour_seen'] = bool(c['tour_seen'])
        for k in ('lyrics_show', 'lyrics_follow'):
            if k in c:
                self.cfg[k] = bool(c[k])
        if 'language' in c and c['language'] in i18n.LANGUAGES:
            self.cfg['language'] = c['language']
            i18n.set_language(c['language'])
        self._store_mix()
        self._save_cfg()
        self.publish_state()

    # ------------------------------------------------------------ recording
    def record(self, part=None):
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        if self.finishing is not None and self.finishing.is_alive():
            raise RuntimeError(t('err.still_saving'))
        self.recording_part = part or self.target
        self.engine.record(self.takes.tmp)
        log.info('recording %s', self.recording_part)
        self.publish_state()

    def toggle_record(self):
        if self.engine.capture is not None:
            return self.stop()
        return self.record()

    def stop(self):
        e = self.engine
        if e.capture is None:
            e.pause()
            self.publish_state()
            return
        virtual = dict(e.virtual) if e.virtual else None
        part = self.recording_part
        cap, _ = e.end_capture()
        self.recording_part = None
        self.busy = t('busy.measuring') if virtual else t('busy.saving')
        self.publish_state()
        self.finishing = threading.Thread(target=self._finish, args=(cap, virtual, part), daemon=True, name='finish')
        self.finishing.start()

    def _finish(self, cap, virtual, part):
        import soundfile as sf
        try:
            x, _ = sf.read(cap.path, dtype='float32', always_2d=True)
            dur = len(x) / SR
            clocks = self.engine.clocks()
            if clocks is None and dur < 60:
                al = TK.align(cap.inputs, cap.outputs, self.engine.input_latency)
                if al is not None:
                    al = (al[0], 1.0)   # short take, fresh session: the measured drift would be just noise
            else:
                al = TK.align(cap.inputs, cap.outputs, self.engine.input_latency, clocks)
            if al is None or len(x) < SR // 2:
                self.notice(t('msg.too_short'), 'error')
                return
            start, alpha = al
            drift = (alpha - 1) * 1e6
            log.info('alignment: start %.1f frames, drift %.1f ppm (%s), %d input blocks, %d output blocks, %.1f s',
                     start, drift, 'session clocks' if clocks else 'take only', len(cap.inputs), len(cap.outputs), dur)
            if virtual:
                at = TK.attacks(x)
                attacks_s = [(start + a * alpha) / SR for a in at]
                r = TK.measure_latency(attacks_s, virtual['clicks_s'])
                if r is None:
                    self.last_calibration = {'ok': False}
                    self.notice(t('msg.calib_failed'), 'error')
                else:
                    r['ok'] = True
                    self.last_calibration = r
                    self.cfg.setdefault('latency_ms', {})[self._device_key()] = round(r['ms'], 1)
                    self._save_cfg()
                    log.info('calibration: %s', r)
                    side = t('msg.late_word') if r['ms'] > 0 else t('msg.early_word')
                    self.notice(t('msg.calib_done', ms=f'{abs(r["ms"]):.0f}', side=side), 'ok')
                self.publish('calibration', self.last_calibration)
                return
            if abs(drift) * 1e-6 * dur > 0.0015 and abs(drift) < 1000:  # only if the difference passes 1.5 ms
                x = TK.resample(x, alpha)
            else:
                drift = 0.0
            latency = self._latency()
            start_s = start / SR - latency / 1000
            if start_s < 0:
                x = x[int(round(-start_s * SR)):]
                start_s = 0.0
            y = TK.mono_if_same(x)
            e = self.engine
            name = self.song.name_of(part) if part in [q.id for q in e.parts] else part
            rec = self.takes.save(y, part, name, start_s, {
                'drift_ppm': round(drift, 1), 'latency_ms': latency,
                'devices': {'api': e.in_api, 'input': e.in_name, 'output': e.out_name},
                'loop': [e.loop[0] / SR, e.loop[1] / SR] if (e.loop and e.loop_on) else None})
            text = t('msg.take_saved', name=rec['name'], dur=mmss(rec['duration_s']), peak=f'{rec["peak_db"]:.0f}',
                     tip=level_tip(rec['peak_db'], E.is_gt100(e.in_name)))
            self.notice(text, 'ok' if -30 <= rec['peak_db'] <= -1 else 'error',
                        action={'kind': 'listen', 'id': rec['id'], 'text': t('act.listen_band')})
            self.publish('takes', self.list_takes())
        except Exception as ex:
            log.exception('finish recording')
            self.notice(t('err.saving', error=ex), 'error')
        finally:
            try:
                os.remove(cap.path)
            except OSError:
                pass
            self.busy = None
            self.publish_state()

    def calibrate(self):
        if self.engine.capture is not None:
            raise RuntimeError(t('err.stop_recording_first'))
        folder = self.takes.tmp if self.takes else os.path.join(DATA, 'tmp')
        self.engine.calibrate(folder)
        self.publish_state()

    # ------------------------------------------------------------ takes
    def list_takes(self):
        if not self.takes:
            return []
        used = {p.take: p.source for p in self.engine.parts if p.source != 'original'}
        out = []
        for f in self.takes.list():
            f = {k: v for k, v in f.items() if k not in ('_json', 'analysis')}
            f['in_use'] = used.get(f['id'])
            out.append(f)
        return out

    def _load_take(self, tid):
        f = self.takes.find(tid)
        self.engine.set_take(tid, self.takes.audio(f), f['start_s'] + f.get('offset_ms', 0.0) / 1000)
        return f

    def edit_take(self, c):
        tid = c['id']
        fields = {k: c[k] for k in ('name', 'favorite', 'offset_ms') if k in c}
        if 'name' in fields:
            fields['name'] = str(fields['name']).strip()[:60] or None
            if not fields['name']:
                del fields['name']
        if 'offset_ms' in fields:
            fields['offset_ms'] = max(-500.0, min(500.0, float(fields['offset_ms'])))
        f = self.takes.update(tid, **fields)
        if tid in self.engine.takes:
            self.engine.takes[tid]['start'] = int(round((f['start_s'] + f.get('offset_ms', 0.0) / 1000) * SR))
        self.publish('takes', self.list_takes())

    def delete_take(self, tid):
        for p in self.engine.parts:
            if p.take == tid:
                p.source, p.take = 'original', None
        f = self.takes.delete(tid)
        self.engine.takes.pop(tid, None)
        self._store_mix()
        self.notice(t('msg.take_deleted', name=f.get('name', '')), 'info', action={'kind': 'restore', 'id': tid, 'text': t('act.undo')})
        self.publish('takes', self.list_takes())
        self.publish_state()

    def restore_take(self, tid):
        f = self.takes.restore(tid)
        self.notice(t('msg.take_restored', name=f.get('name', '')), 'ok')
        self.publish('takes', self.list_takes())

    def play_take(self, tid):
        f = self.takes.find(tid)
        self.set_part(f['part'], {'source': 'take', 'take': tid, 'on': True})
        t0 = max(0.0, f['start_s'] + f.get('offset_ms', 0.0) / 1000)
        e = self.engine
        if e.loop and e.loop_on:
            a, b = e.loop[0] / SR, e.loop[1] / SR
            if t0 + f['duration_s'] < a or t0 > b:
                e.loop_on = False
        if e.playing:
            e.pause()
        e.play(t0, no_count=True)
        self.publish('takes', self.list_takes())
        self.publish_state()

    def analyze_take(self, tid):
        """Timing analysis of a take (cached in its record while the timing offset does not change)."""
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        f = self.takes.find(tid)
        old = f.get('analysis')
        if old and old.get('version') == AN.VERSION and old.get('offset_ms') == f.get('offset_ms', 0.0) \
                and old.get('calibrated') == bool(f.get('latency_ms')):
            res = old
        else:
            res = AN.analyze_take(self.song, f, self.takes.audio(f), calibrated=bool(f.get('latency_ms')))
            self.takes.update(tid, analysis=res)
        res = dict(res)
        res['name'] = f.get('name')
        res['sentences'] = analysis_sentences(res)
        return res

    def export(self, kind, tid=None):
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        if self.busy:
            raise RuntimeError(t('err.wait', what=self.busy))
        self.busy = t('busy.exporting')
        self.publish_state()
        threading.Thread(target=self._export, args=(kind, tid), daemon=True, name='export').start()

    def _export(self, kind, tid):
        try:
            now = datetime.datetime.now()
            title, artist = self.song.title, self.song.m.get('artist', '')
            album = f'{title} ({artist})' if artist else title
            if kind == 'take':
                f = self.takes.find(tid)
                audio = self.takes.audio(f)
                dest = os.path.join(self.takes.exports, f['id'] + '.mp3')
                TK.export_mp3(audio, SR, dest, f'{title} - {f["name"]}', 'Riffroom', album, t('mp3.take_comment'))
            else:
                e = self.engine
                a, b = (e.loop if (e.loop and e.loop_on) else (0, e.n_total))
                out = np.zeros((b - a, 2), np.float32)
                for p in e.parts:
                    go, gt = p.targets()
                    if go:
                        out += p.data[a:b] * np.float32(go / 32768)
                    tk = e.takes.get(p.take) if p.take else None
                    if gt and tk is not None:
                        d, ini = tk['data'], tk['start']
                        i0, i1 = max(a - ini, 0), min(b - ini, len(d))
                        if i1 > i0:
                            out[ini + i0 - a:ini + i1 - a] += d[i0:i1] * np.float32(gt)
                out *= np.float32(10 ** (e.master_db / 20))
                which = ', '.join(p.name if p.source == 'original' else t('mp3.yours', part=p.name) for p in e.parts if p.on)
                suffix = f' ({mmss(a / SR)}-{mmss(b / SR)})' if (a, b) != (0, e.n_total) else ''
                name = TK.safe_name(f'{now:%Y-%m-%d %Hh%M} - {title} - {t("mp3.mix")}{suffix}'.replace(':', 'm'))
                dest = os.path.join(self.takes.exports, name + '.mp3')
                TK.export_mp3(out, SR, dest, f'{title} - {t("mp3.mix")}{suffix}', 'Riffroom', album, t('mp3.parts', parts=which))
            self.notice(t('msg.mp3_ready', file=os.path.basename(dest)), 'ok',
                        action={'kind': 'folder', 'which': 'exports', 'text': t('act.open_folder')})
        except Exception as ex:
            log.exception('export')
            self.notice(t('err.export', error=ex), 'error')
        finally:
            self.busy = None
            self.publish_state()

    def open_folder(self, which):
        if not self.takes:
            return
        p = self.takes.exports if which == 'exports' else self.takes.dir
        os.makedirs(p, exist_ok=True)
        os.startfile(p)

    # ------------------------------------------------------------ lyrics
    def _lyrics_dirs(self):
        """Where the lyrics of the open song are: the folder of its riffroom.json first (where lyrics.lrc goes), then the
        song folder."""
        f = self.song.folder
        man = SG.manifest_path(f)
        d = os.path.dirname(man) if man else f
        return [d] if os.path.normcase(d) == os.path.normcase(f) else [d, f]

    def _voice(self):
        p = next((p for p in self.song.parts if p['id'] == 'vocals'), None)
        if p is None:
            return None
        x = p['data']
        return (x[:, 0].astype(np.float32) + x[:, 1].astype(np.float32)) / 65536.0

    def _lyrics_view(self, lines, path, meta=None, note=None):
        return {'song': self.song.id, 'lines': lines, 'timed': LY.timed(lines), 'text': LY.dump(lines, meta),
                'file': os.path.relpath(path, self.song.folder).replace(os.sep, '/') if path else None,
                'has_voice': any(p['id'] == 'vocals' for p in self.song.parts), 'note': note}

    def _align_lines(self, lines):
        """Timed lyrics lined up with the song's voice: (lines, shift in s), or (lines, None) when it was not possible."""
        v = self._voice()
        if v is None or not LY.timed(lines):
            return lines, None
        sh, q = LY.align(lines, v, self.song.sr)
        log.info('lyrics: line-up %s s, quality %.2f', sh, q)
        if sh is None or q < LY.MIN_QUALITY:
            return lines, None
        return LY.shift(lines, sh), sh

    def lyrics(self):
        """Lyrics of the open song. The first time, a lyrics file brought from outside (an .lrc in the song folder) is
        copied to lyrics.lrc, lined up with the voice when the song has one."""
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        with self.lyrics_lock:
            return self._lyrics_now()

    def _lyrics_now(self):
        dirs = self._lyrics_dirs()
        path, own = LY.find(dirs)
        if path is None:
            return self._lyrics_view([], None)
        d = LY.parse(LY.read_file(path))
        if own:
            return self._lyrics_view(d['lines'], path, d['meta'])
        return self._import_lyrics(d, path, dirs[0])

    def _import_lyrics(self, d, src, folder):
        lines, sh = self._align_lines(d['lines'])
        name = os.path.basename(src)
        meta = dict(d['meta'], re='Riffroom')
        meta['#'] = f'from {name}' + (f', lined up with the voice ({sh:+.2f} s)' if sh is not None else '')
        path = LY.write_file(folder, lines, meta)
        if sh is not None:
            note = t('msg.lyrics_imported', file=name) + ' ' + t('msg.lyrics_aligned', shift=secs_txt(sh))
        elif LY.timed(lines) and self._voice() is not None:
            note = t('msg.lyrics_imported', file=name) + ' ' + t('msg.lyrics_not_aligned')
        else:
            note = t('msg.lyrics_imported', file=name)
        log.info('lyrics: %s -> %s', src, path)
        self.notice(note, 'ok')
        return self._lyrics_view(lines, path, meta, note)

    def _own_lyrics(self):
        """(parsed lyrics.lrc, its folder); imports the file brought from outside first, when that is all there is."""
        dirs = self._lyrics_dirs()
        path, own = LY.find(dirs)
        if path is not None and not own:
            self._lyrics_now()
            path, own = LY.find(dirs)
        if path is None:
            raise RuntimeError(t('err.lyrics_none'))
        return LY.parse(LY.read_file(path)), dirs[0]

    def save_lyrics(self, text, align=False):
        """Writes the lyrics typed or pasted on the screen (LRC or plain text). align: line the times up with the voice."""
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        with self.lyrics_lock:
            d = LY.parse(text)
            lines, sh = self._align_lines(d['lines']) if align else (d['lines'], None)
            path = LY.write_file(self._lyrics_dirs()[0], lines, d['meta'])
            note = t('msg.lyrics_saved', n=sum(1 for ln in lines if ln['text']))
            if sh is not None:
                note += ' ' + t('msg.lyrics_aligned', shift=secs_txt(sh))
            elif align and LY.timed(lines) and self._voice() is not None:
                note += ' ' + t('msg.lyrics_not_aligned')
            self.notice(note, 'ok')
            return self._lyrics_view(lines, path, d['meta'], note)

    def shift_lyrics(self, seconds):
        """Moves every line by `seconds` (negative: earlier)."""
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        with self.lyrics_lock:
            d, folder = self._own_lyrics()
            if not LY.timed(d['lines']):
                raise RuntimeError(t('err.lyrics_untimed'))
            lines = LY.shift(d['lines'], max(-60.0, min(60.0, float(seconds))))
            return self._lyrics_view(lines, LY.write_file(folder, lines, d['meta']), d['meta'])

    def align_lyrics(self):
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        with self.lyrics_lock:
            d, folder = self._own_lyrics()
            if not LY.timed(d['lines']):
                raise RuntimeError(t('err.lyrics_untimed'))
            if self._voice() is None:
                raise RuntimeError(t('err.lyrics_no_voice'))
            lines, sh = self._align_lines(d['lines'])
            if sh is None:
                note = t('msg.lyrics_not_aligned')
                self.notice(note, 'error')
                return self._lyrics_view(d['lines'], os.path.join(folder, LY.FILE), d['meta'], note)
            note = t('msg.lyrics_aligned', shift=secs_txt(sh))
            self.notice(note, 'ok')
            return self._lyrics_view(lines, LY.write_file(folder, lines, d['meta']), d['meta'], note)

    def set_lyrics_times(self, times):
        """Times marked by the player while the song played: [[line index, seconds], ...]."""
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        with self.lyrics_lock:
            d, folder = self._own_lyrics()
            lines = d['lines']
            n = 0
            for i, v in times:
                i = int(i)
                if 0 <= i < len(lines) and lines[i]['text']:
                    lines[i] = dict(lines[i], t=round(max(0.0, float(v)), 2))
                    n += 1
            note = t('msg.lyrics_times', n=n)
            self.notice(note, 'ok')
            return self._lyrics_view(lines, LY.write_file(folder, lines, d['meta']), d['meta'], note)

    def open_lyrics_file(self):
        """Window to choose a lyrics file (.lrc or .txt); it becomes the lyrics of the open song (lined up with the voice)."""
        if not self.song:
            raise RuntimeError(t('err.no_song'))
        if not self.dialog.acquire(blocking=False):
            raise RuntimeError(t('err.dialog_open'))
        try:
            path = pick_file(t('dlg.pick_lyrics'), [(t('dlg.lyrics_files'), '*.lrc *.txt'), ('*', '*.*')])
        finally:
            self.dialog.release()
        if not path:
            return {'cancelled': True}
        with self.lyrics_lock:
            d = LY.parse(LY.read_file(path))
            if not any(ln['text'] for ln in d['lines']):
                raise ValueError(t('err.lyrics_empty_file', file=os.path.basename(path)))
            return self._import_lyrics(d, path, self._lyrics_dirs()[0])

    # ------------------------------------------------------------ chords and preparing songs (screen and AI)
    def set_chords(self, c):
        """Writes the chord chart of a song (chords.txt) and prepares it again. Optional: tempo, meter, tonic, tuning."""
        sid = c.get('song') or (self.song.id if self.song else None)
        if not sid:
            raise RuntimeError(t('err.no_song'))
        folder = SG.folder_of(sid, self.songs)
        if 'chart' in c and c['chart'] is not None:
            PR.read_chart(c['chart'])          # validates before writing
            with open(os.path.join(folder, 'chords.txt'), 'w', encoding='utf-8') as f:
                f.write(c['chart'].strip() + '\n')
        PR.prepare(folder, bpm=c.get('bpm'), time_signature=c.get('time_signature'), first_bar=c.get('first_bar'),
                   tonic=c.get('tonic'), tuning=c.get('tuning'), sections=c.get('sections'),
                   log=lambda s: log.info('prepare: %s', s))
        self.songs = SG.list_songs()
        if self.song and self.song.id == sid:
            self.reload_song()
        else:
            self.publish_state()

    def prepare_folder(self, c):
        folder = c['folder']
        if not os.path.isdir(folder):
            raise ValueError(t('err.no_folder', folder=folder))
        PR.prepare(folder, title=c.get('title'), artist=c.get('artist'), bpm=c.get('bpm'),
                   time_signature=c.get('time_signature'), first_bar=c.get('first_bar'), tonic=c.get('tonic'),
                   tuning=c.get('tuning'), targets=c.get('targets'), log=lambda s: log.info('prepare: %s', s))
        if not any(os.path.normcase(os.path.abspath(folder)).startswith(os.path.normcase(os.path.abspath(b)))
                   for b in SG.LIBRARIES):
            add_library(self.cfg, os.path.dirname(os.path.abspath(folder)))
            self._save_cfg()
        self.rescan()
        return SG.id_of_folder(folder, self.songs)

    # ------------------------------------------------------------ jobs (long tasks: importing a song with the AI)
    def _job_view(self, j):
        return {k: j[k] for k in ('id', 'kind', 'status', 'progress', 'message', 'result', 'error', 'title')}

    def job(self, jid):
        if jid not in self.jobs:
            raise KeyError(t('err.no_job', job=jid))
        return self._job_view(self.jobs[jid])

    def import_song(self, c):
        """Starts importing an audio file with the local AI (separate, find beats and chords). Returns the job."""
        path = c.get('path')
        if not path:
            if not self.dialog.acquire(blocking=False):
                raise RuntimeError(t('err.dialog_open'))
            try:
                path = pick_file(t('dlg.pick_audio'))
            finally:
                self.dialog.release()
            if not path:
                return None
        if not os.path.isfile(path):
            raise ValueError(t('err.no_file', file=path))
        try:
            from ai import importer  # noqa: F401  (heavy: torch, demucs)
        except Exception as ex:
            log.info('AI not installed: %s', ex)
            raise RuntimeError(t('err.ai_missing'))
        jid = uuid.uuid4().hex[:8]
        j = {'id': jid, 'kind': 'import', 'status': 'running', 'progress': 0.0, 'message': t('job.starting'),
             'result': None, 'error': None, 'title': os.path.splitext(os.path.basename(path))[0]}
        self.jobs[jid] = j
        threading.Thread(target=self._run_import, args=(j, path, c), daemon=True, name='import').start()
        self.publish_state()
        return jid

    def _run_import(self, j, path, c):
        from ai import importer
        last = [0.0]

        def progress(frac, key, **kw):
            j['progress'] = round(float(frac), 3)
            j['message'] = t('job.' + key, **kw)
            now = time.time()
            if now - last[0] > 0.5 or frac >= 1:
                last[0] = now
                self.publish('job', self._job_view(j))

        try:
            library = c.get('library') or libraries(self.cfg)[0]
            res = importer.import_song(path, library, title=c.get('title'), artist=c.get('artist'),
                                       tuning=int(c.get('tuning') or 0), time_signature=c.get('time_signature'),
                                       progress=progress, log=lambda s: log.info('import: %s', s))
            if not any(os.path.normcase(library) == os.path.normcase(b) for b in SG.LIBRARIES):
                add_library(self.cfg, library)
                self._save_cfg()
            self.songs = SG.list_songs()
            res['song_id'] = SG.id_of_folder(res['folder'], self.songs)
            j['result'] = res
            j['status'] = 'done'
            j['progress'] = 1.0
            j['message'] = t('job.done')
            self.publish('job', self._job_view(j))
            self.notice(t('msg.imported', title=res.get('title', '')), 'ok',
                        action={'kind': 'open_song', 'id': res['song_id'], 'text': t('act.open')})
            self.publish_state()
        except Exception as ex:
            log.exception('import')
            j['status'] = 'error'
            j['error'] = str(ex)
            j['message'] = t('err.import', error=ex)
            self.publish('job', self._job_view(j))
            self.notice(j['message'], 'error')
            self.publish_state()

    # ------------------------------------------------------------ devices
    def set_output(self, mode):
        if mode == 'gt100':
            mode = 'interface'
        if mode not in ('pc', 'interface'):
            raise ValueError(mode)
        if self.engine.capture is not None:
            raise RuntimeError(t('err.stop_recording_first'))
        self.engine.pause()
        self.engine.open(mode)
        self.cfg['output_mode'] = mode
        self._save_cfg()
        self.publish_state()

    def set_input(self, name):
        if self.engine.capture is not None:
            raise RuntimeError(t('err.stop_recording_first'))
        self.engine.pause()
        self.engine.open(input_name=name or None)
        if name:
            self.cfg['input_device'] = E.no_port(name)
        else:
            self.cfg.pop('input_device', None)
        self._save_cfg()
        self.publish_state()
        if self.engine.in_stream is not None:
            self.notice(t('msg.recording_from', name=E.short_name(self.engine.in_name)), 'ok')
        else:
            self.notice(self.engine.no_input_message(), 'error')

    def rescan_devices(self):
        if self.engine.capture is not None:
            raise RuntimeError(t('err.stop_recording_first'))
        self.engine.rescan()
        self.publish_state()
        if self.engine.in_stream is not None:
            self.notice(t('msg.input_found', name=E.short_name(self.engine.in_name)), 'ok')
        else:
            self.notice(self.engine.no_input_message(), 'error')

    def close(self):
        try:
            if self.engine.capture is not None:
                self.stop()
            if self.finishing is not None:
                self.finishing.join(timeout=60)
            self._store_mix()
            self.engine._close_streams()
        except Exception:
            log.exception('close')


APP = None
ROUTES = {
    'play': lambda a, c: a.play(c.get('t')),
    'pause': lambda a, c: a.pause(),
    'toggle_play': lambda a, c: a.toggle_play(),
    'seek': lambda a, c: a.seek(float(c['t'])),
    'home': lambda a, c: a.home(),
    'bar': lambda a, c: a.step_bar(int(c.get('delta', 1))),
    'loop': lambda a, c: a.set_loop(c),
    'part': lambda a, c: a.set_part(c['id'], c),
    'mode': lambda a, c: a.set_mode(c['mode']),
    'options': lambda a, c: a.options(c),
    'record': lambda a, c: a.record(c.get('part')),
    'stop': lambda a, c: a.stop(),
    'toggle_record': lambda a, c: a.toggle_record(),
    'take': lambda a, c: a.edit_take(c),
    'take_delete': lambda a, c: a.delete_take(c['id']),
    'take_restore': lambda a, c: a.restore_take(c['id']),
    'take_play': lambda a, c: a.play_take(c['id']),
    'export': lambda a, c: a.export(c.get('kind', 'mix'), c.get('id')),
    'output': lambda a, c: a.set_output(c['mode']),
    'input': lambda a, c: a.set_input(c.get('name')),
    'rescan_devices': lambda a, c: a.rescan_devices(),
    'calibrate': lambda a, c: a.calibrate(),
    'song': lambda a, c: a.open_song(c['id']),
    'rescan': lambda a, c: a.rescan(),
    'add_library': lambda a, c: a.add_library(),
    'open_folder': lambda a, c: a.open_folder(c.get('which', 'takes')),
    'chords': lambda a, c: a.set_chords(c),
}
# routes that answer with their own result instead of the state
RESULT_ROUTES = {
    'analyze': lambda a, c: a.analyze_take(c['id']),
    'import': lambda a, c: {'job': a.import_song(c)},
    'prepare': lambda a, c: {'song_id': a.prepare_folder(c)},
    'lyrics_save': lambda a, c: a.save_lyrics(c.get('text') or '', bool(c.get('align'))),
    'lyrics_shift': lambda a, c: a.shift_lyrics(float(c['seconds'])),
    'lyrics_align': lambda a, c: a.align_lyrics(),
    'lyrics_times': lambda a, c: a.set_lyrics_times(c.get('times') or []),
    'lyrics_open': lambda a, c: a.open_lyrics_file(),
}


class Handler(BaseHTTPRequestHandler):
    server_version = 'Riffroom/' + VERSION
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _local(self):
        """Serves only its own window and local programs: the address must be 127.0.0.1/localhost (against "DNS rebinding")
        and, in requests that change something, the origin must be the app's (a website open in the browser cannot
        drive the app)."""
        port = self.server.server_port
        if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            return False
        origin = self.headers.get('Origin')
        return self.command == 'GET' or origin in (None, f'http://127.0.0.1:{port}', f'http://localhost:{port}')

    def _file(self, name):
        path = os.path.normpath(os.path.join(WEB, name))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._json({'error': 'not found'}, 404)
        with open(path, 'rb') as f:
            body = f.read()
        self._send(200, body, TYPES.get(os.path.splitext(path)[1].lower(), 'application/octet-stream'))

    def _events(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        q = APP.new_client()
        try:
            self.wfile.write(f'event: state\ndata: {json.dumps(APP.state(), ensure_ascii=False)}\n\n'.encode('utf-8'))
            self.wfile.flush()
            while not APP.end.is_set():
                try:
                    msg = q.get(timeout=2.0)
                except queue.Empty:
                    msg = ': ping\n\n'
                self.wfile.write(msg.encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            APP.drop_client(q)
            self.close_connection = True

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        qs = parse_qs(u.query)
        try:
            if not self._local():
                return self._json({'error': 'request from outside the app'}, 403)
            if p in ('/', '/index.html'):
                return self._file('index.html')
            if p.startswith('/web/'):
                return self._file(p[5:])
            if p == '/favicon.ico':
                return self._file('icon.svg')
            if p == '/api/events':
                return self._events()
            if p == '/api/ping':
                return self._json({'app': 'riffroom', 'version': VERSION})
            if p == '/api/state':
                return self._json(APP.state())
            if p == '/api/song':
                return self._json(APP.song_data())
            if p == '/api/takes':
                return self._json(APP.list_takes())
            if p == '/api/locale':
                return self._json({'language': i18n.language(), 'languages': i18n.LANGUAGES, 'strings': i18n.strings()})
            if p == '/api/job':
                return self._json(APP.job(qs.get('id', [''])[0]))
            if p == '/api/analysis':
                return self._json(APP.analyze_take(qs.get('id', [''])[0]))
            if p == '/api/lyrics':
                return self._json(APP.lyrics())
            if p == '/api/chord.svg':
                name = qs.get('name', [''])[0]
                if not APP.song:
                    return self._json({'error': 'no song'}, 404)
                return self._send(200, APP.song.chord_svg(name).encode('utf-8'), 'image/svg+xml')
            return self._json({'error': 'not found'}, 404)
        except KeyError as ex:
            return self._json({'error': str(ex).strip("'")}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as ex:
            log.exception('GET %s', p)
            return self._json({'error': str(ex)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if not self._local():
                return self._json({'error': 'request from outside the app'}, 403)
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n).decode('utf-8') or '{}') if n else {}
            name = p[5:] if p.startswith('/api/') else ''
            if name in RESULT_ROUTES:
                return self._json(RESULT_ROUTES[name](APP, body))
            if name not in ROUTES:
                return self._json({'error': 'unknown action'}, 404)
            ROUTES[name](APP, body)
            return self._json(APP.state())
        except (ValueError, KeyError, RuntimeError) as ex:
            log.info('POST %s: %s', p, ex)
            return self._json({'error': str(ex).strip("'")}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as ex:
            log.exception('POST %s', p)
            return self._json({'error': t('err.unexpected', error=ex)}, 500)


def _call(port, path, body=None, timeout=1.5):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', method='POST' if body is not None else 'GET',
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def open_window(port):
    """Own Edge (or Chrome) window in app mode, with a separate profile; without them, the default browser."""
    url = f'http://127.0.0.1:{port}/'
    pf, pf86, lad = (os.environ.get(k, '') for k in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'))
    browsers = [os.path.join(pf86, r'Microsoft\Edge\Application\msedge.exe'), os.path.join(pf, r'Microsoft\Edge\Application\msedge.exe'),
                os.path.join(pf, r'Google\Chrome\Application\chrome.exe'), os.path.join(pf86, r'Google\Chrome\Application\chrome.exe'),
                os.path.join(lad, r'Google\Chrome\Application\chrome.exe')]
    for b in browsers:
        if b and os.path.exists(b):
            subprocess.Popen([b, f'--app={url}', f'--user-data-dir={os.path.join(DATA, "window")}',
                              '--no-first-run', '--no-default-browser-check', '--disable-sync', '--disable-features=Translate',
                              '--window-size=1440,920', '--window-position=40,30'],
                             close_fds=True, creationflags=0x00000008 if os.name == 'nt' else 0)
            return
    import webbrowser
    webbrowser.open(url)


def main():
    ap = argparse.ArgumentParser(description='Riffroom: play and record along with the separated band.')
    ap.add_argument('--song', help='folder of the song to open')
    ap.add_argument('--library', action='append', help='music folder to add to the list (kept)')
    ap.add_argument('--language', choices=sorted(i18n.LANGUAGES), help='interface language (kept)')
    ap.add_argument('--configure', action='store_true', help='only saves the options (--library, --language) and exits')
    ap.add_argument('--no-window', action='store_true')
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--version', action='version', version=f'Riffroom {VERSION}')
    a = ap.parse_args()
    setup_log()
    if a.library or a.language or a.configure:
        cfg = read_config()
        for b in a.library or []:
            add_library(cfg, b)
        if a.language:
            cfg['language'] = a.language
        save_config(cfg)
        if a.configure:
            print('libraries:', '; '.join(libraries(cfg)), '| language:', cfg.get('language', 'en'))
            return
    sid = None
    if a.song:
        cfg = read_config()
        SG.LIBRARIES[:] = libraries(cfg)
        sid = SG.id_of_folder(a.song)
        if sid is None:  # song outside the libraries: the folder above joins the list
            add_library(cfg, os.path.dirname(os.path.abspath(a.song)))
            save_config(cfg)
            SG.LIBRARIES[:] = libraries(cfg)
            sid = SG.id_of_folder(a.song)
    try:  # already open? just open another window
        if _call(a.port, '/api/ping').get('app') == 'riffroom':
            if sid:
                try:
                    _call(a.port, '/api/rescan', {})
                    _call(a.port, '/api/song', {'id': sid}, timeout=30)
                except Exception:
                    pass
            if not a.no_window:
                open_window(a.port)
            return
    except Exception:
        pass
    srv, port = None, a.port
    for p in range(a.port, a.port + 10):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        log.error('no free port')
        return
    srv.daemon_threads = True
    log.info('Riffroom %s started on port %d', VERSION, port)
    global APP
    APP = App(keep=a.no_window)
    threading.Thread(target=srv.serve_forever, daemon=True, name='server').start()
    if not sid:
        sid = APP.cfg.get('last_song')
        ids = [x['id'] for x in APP.songs]
        ready = [x['id'] for x in APP.songs if x['prepared']]
        if sid not in ids:
            sid = ready[0] if ready else None
    if sid:
        def open_it():
            try:
                APP.open_song(sid)
            except Exception as ex:
                log.exception('open song')
                APP.notice(t('err.open_song', error=ex), 'error')
        threading.Thread(target=open_it, daemon=True, name='open').start()
    if not a.no_window:
        open_window(port)
    APP.end.wait()
    APP.close()
    srv.shutdown()
    log.info('Riffroom closed')


if __name__ == '__main__':
    main()
