# SPDX-License-Identifier: GPL-3.0-or-later
"""Riffroom MCP server: lets an AI assistant (Claude Desktop, Claude Code or any MCP client) drive Riffroom.

It talks to the Riffroom app through the same local API the screen uses (http://127.0.0.1:8737) and starts the app,
with its window, when it is closed. Nothing is sent to the internet: the assistant only sees what the tools return
(song structure, states and timing analyses), never the audio.

Run by the assistant (stdio): <python> -I -X utf8 mcp_server.py
Register: claude mcp add riffroom --scope user -- <python> -I -X utf8 <path>/mcp_server.py"""
import json
import logging
import os
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('RIFFROOM_PORT', '8737'))
DATA = os.environ.get('RIFFROOM_DATA') or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Riffroom')
log = logging.getLogger('riffroom.mcp')

INSTRUCTIONS = """Riffroom is a practice room for musicians (mostly guitarists): it plays a song split into separate parts
(drums, bass, rhythm guitar, lead guitar, vocals...), shows the current chord and beat, loops sections, records the
player's takes already aligned with the band and analyzes their timing.

Typical session: list_songs -> open_song -> get_song_map (sections, bars, chords) -> set_target (mutes the part the player
will play) -> set_loop on a section -> record_take (one pass of the loop; the player plays along) -> analyze_take ->
explain in plain words where they rushed or dragged and suggest what to practice. Bars are numbered from 1.
Before the first recording of a session, check get_status: if 'calibrated' is false, suggest calibrate_latency.
get_lyrics gives the lyrics with the time, bar and section of each line (e.g. to loop the chorus by its words). Lyrics come
from the player: write with set_lyrics only lyrics they gave you, never song lyrics from memory.
The player hears everything on their own speakers; talk to them as a friendly teacher, in their language."""

mcp = MCPServer('riffroom', title='Riffroom', instructions=INSTRUCTIONS, version='2.1.0')


# ---------------------------------------------------------------- local API of the app
class API:
    base = f'http://127.0.0.1:{PORT}'

    def call(self, path, body=None, timeout=30):
        req = urllib.request.Request(self.base + path, method='POST' if body is not None else 'GET',
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode('utf-8')).get('error')
            except Exception:
                msg = str(e)
            raise ToolError(msg or str(e))

    def get(self, path, timeout=60):
        return self.call(path, None, timeout)

    def post(self, action, body=None, timeout=60):
        return self.call('/api/' + action, body or {}, timeout)

    def alive(self):
        try:
            return self.call('/api/ping', None, 1.5).get('app') == 'riffroom'
        except Exception:
            return False


api = API()


def ensure_app():
    """Starts Riffroom (with its window) if it is not running and waits until it answers."""
    if api.alive():
        return
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), 'pythonw.exe')
    args = [pyw if os.path.exists(pyw) else py, '-I', '-X', 'utf8', os.path.join(HERE, 'riffroom.py'), '--port', str(PORT)]
    if os.environ.get('RIFFROOM_MCP_HEADLESS'):
        args.append('--no-window')
    subprocess.Popen(args, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008 if os.name == 'nt' else 0)
    for _ in range(100):
        time.sleep(0.25)
        if api.alive():
            for _ in range(80):          # wait for the last song to finish opening
                st = api.get('/api/state')
                if not st.get('loading') and (st.get('song') or not st.get('songs')):
                    return
                time.sleep(0.25)
            return
    raise ToolError('Riffroom did not start. Open it from its shortcut and try again.')


def state():
    ensure_app()
    return api.get('/api/state')


def song_map_raw():
    st = state()
    if not st.get('song'):
        raise ToolError('No song is open. Call list_songs and open_song first.')
    return api.get('/api/song')


# ---------------------------------------------------------------- helpers
def _norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in s).split())


def find_song(query, songs):
    q = _norm(query)
    if not q:
        raise ToolError('Say which song.')
    for s in songs:
        if _norm(s['id']) == q or _norm(s.get('title')) == q:
            return s
    words = q.split()
    scored = []
    for s in songs:
        hay = _norm(f"{s.get('title', '')} {s.get('artist', '')} {s['id']}")
        if all(w in hay for w in words):
            scored.append((0 if q in _norm(s.get('title')) else 1, len(hay), s))
    if scored:
        return sorted(scored, key=lambda x: (x[0], x[1]))[0][2]
    names = '; '.join(f"{s.get('title')} ({s.get('artist')})" for s in songs) or 'none'
    raise ToolError(f'Song not found: "{query}". Available: {names}')


def bar_time(m, bar):
    B = m['bars']
    if not B:
        raise ToolError('This song has no bars marked; use seconds instead.')
    if bar < 1 or bar > len(B) - 1:
        raise ToolError(f'Bar {bar} does not exist: the song has bars 1 to {len(B) - 1}.')
    return B[bar - 1]


def sections(m):
    B, secs = m['bars'], m.get('sections') or []
    out = []
    for i, s in enumerate(secs):
        end_bar = (secs[i + 1]['bar'] - 1) if i + 1 < len(secs) else len(B) - 1
        out.append({'name': s['name'], 'from_bar': s['bar'], 'to_bar': end_bar,
                    'start_s': round(B[s['bar'] - 1], 2) if s['bar'] - 1 < len(B) else None,
                    'end_s': round(B[end_bar] if end_bar < len(B) else m['duration'], 2)})
    return out


def find_section(m, name):
    secs = sections(m)
    if not secs:
        raise ToolError('This song has no sections marked; use bars instead.')
    q = _norm(name)
    for s in secs:
        if _norm(s['name']) == q:
            return s
    for s in secs:
        if q in _norm(s['name']):
            return s
    words = q.split()
    for s in secs:
        if all(w in _norm(s['name']) for w in words):
            return s
    raise ToolError(f'Section not found: "{name}". Sections: ' + ', '.join(s['name'] for s in secs))


def where(m, t):
    B = m['bars']
    if not B or t < B[0] - 0.01:
        return {'seconds': round(t, 2), 'bar': 0}
    import bisect
    bar = min(len(B) - 1, bisect.bisect_right(B, t + 0.01))
    sec = None
    for s in m.get('sections') or []:
        if s['bar'] <= bar:
            sec = s['name']
    return {'seconds': round(t, 2), 'bar': bar, 'section': sec}


def chart(m):
    """Chords bar by bar, as text ("A5 A5 D5 A5 | ..."), with the section names."""
    B = m['bars']
    if not B:
        return None
    per_bar = {}
    for c in m.get('chords') or []:
        if not c.get('chord'):
            continue
        for i in range(len(B) - 1):
            a, b = max(c['t0'], B[i]), min(c['t1'], B[i + 1])
            if b - a > 0.05:
                per_bar.setdefault(i + 1, []).append(c['chord'])
    starts = {s['bar']: s['name'] for s in m.get('sections') or []}
    lines, line = [], []
    for bar in range(1, len(B)):
        if bar in starts:
            if line:
                lines.append(' '.join(line)); line = []
            lines.append(f'[{starts[bar]}]')
        chs = per_bar.get(bar, [])
        uniq = [c for i, c in enumerate(chs) if i == 0 or c != chs[i - 1]]
        line.append(','.join(uniq) if uniq else '-')
        if len(line) == 4:
            lines.append(' '.join(line)); line = []
    if line:
        lines.append(' '.join(line))
    return '\n'.join(lines)


def _wait_idle(timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api.get('/api/state')
        if not st['recording'] and not st['calibrating'] and not st.get('busy'):
            return st
        time.sleep(0.4)
    raise ToolError('Timed out waiting for Riffroom.')


def _take_summary(f):
    return {'id': f['id'], 'name': f.get('name'), 'part': f.get('part'), 'created': f.get('created'),
            'duration_s': f.get('duration_s'), 'start_s': f.get('start_s'), 'favorite': f.get('favorite'),
            'peak_db': f.get('peak_db'), 'in_use': f.get('in_use')}


def _analysis_summary(r):
    keep = ('name', 'part', 'reference', 'grid', 'original', 'worst_bars', 'streaks', 'missed_bars', 'calibrated', 'onsets',
            'too_few', 'sentences', 'span')
    out = {k: r[k] for k in keep if k in r}
    out['bars'] = [[b['bar'], b['median_ms'], b['n']] for b in r.get('bars', [])]
    out['bars_format'] = '[bar, median deviation in ms (+ late, - early), strums]'
    return out


# ---------------------------------------------------------------- tools
@mcp.tool(structured_output=True)
def get_status() -> dict[str, Any]:
    """Current state of Riffroom: open song, position (seconds, bar, section), playing/recording, loop, the band parts
    (on/off, volume, original or a take), the part the player will play, audio input/output and whether the latency
    calibration ("Fix the timing") was done."""
    st = state()
    out = {'song': st.get('song'), 'playing': st['playing'], 'recording': st['recording'], 'busy': st.get('busy'),
           'count_in': st['count_in'], 'metronome': st['metronome'], 'master_volume_db': st['master_db'],
           'target_part': st.get('target'), 'calibrated': st.get('calibrated'), 'latency_ms': st.get('latency_ms'),
           'parts': [{'id': p['id'], 'name': p['name'], 'on': p['on'], 'volume_db': p['volume_db'], 'source': p['source'],
                      'take_id': p.get('take')} for p in st.get('parts', [])],
           'input': st['devices'].get('input'), 'output': st['devices'].get('output'), 'language': st.get('language'),
           'jobs': st.get('jobs')}
    if st.get('song'):
        m = api.get('/api/song')
        out['position'] = where(m, st['pos'])
        if st.get('loop'):
            a, b = st['loop']
            out['loop'] = {'on': st['loop_on'], 'from': where(m, a), 'to': where(m, b - 0.05)}   # ends with that bar
    return out


@mcp.tool(structured_output=True)
def list_songs() -> list[dict[str, Any]]:
    """Songs in the player's music folders. 'prepared' false means the separated tracks exist but the song was never
    opened (opening it prepares it)."""
    st = state()
    return [{'id': s['id'], 'title': s.get('title'), 'artist': s.get('artist'), 'prepared': s.get('prepared')}
            for s in st.get('songs', [])]


@mcp.tool(structured_output=True)
def open_song(song: str) -> dict[str, Any]:
    """Opens a song by id or by (part of) its title/artist, e.g. "night prowler". Returns the song map."""
    st = state()
    s = find_song(song, st.get('songs', []))
    api.post('song', {'id': s['id']}, timeout=180)
    return get_song_map()


@mcp.tool(structured_output=True)
def get_song_map() -> dict[str, Any]:
    """Structure of the open song: tempo, time signature, tonic, guitar tuning, number of bars, sections with their bars
    and times, the chord chart bar by bar, and the band parts."""
    m = song_map_raw()
    return {'title': m.get('title'), 'artist': m.get('artist'), 'duration_s': round(m['duration'], 1), 'bpm': m.get('bpm'),
            'time_signature': m.get('time_signature'), 'tonic': m.get('tonic'),
            'tuning_semitones': m.get('tuning'), 'bars': max(0, len(m['bars']) - 1),
            'count_in_beats': len(m.get('count_in') or []), 'sections': sections(m), 'chord_chart': chart(m),
            'parts': [{'id': p['id'], 'name': p['name']} for p in m['parts']], 'targets': m.get('targets'),
            'chords_source': m.get('source')}


@mcp.tool(structured_output=True)
def play(from_bar: int | None = None, from_section: str | None = None, from_seconds: float | None = None) -> dict[str, Any]:
    """Starts playing (with the 4-beat count-in when it is on), from a bar, a section or a time; without arguments, from
    where it is."""
    st = state()
    t = None
    if from_bar is not None or from_section is not None:
        m = api.get('/api/song')
        t = bar_time(m, from_bar) if from_bar is not None else find_section(m, from_section)['start_s']
    elif from_seconds is not None:
        t = float(from_seconds)
    if st['playing']:
        api.post('pause')
    api.post('play', {'t': t} if t is not None else {})
    return get_status()


@mcp.tool(structured_output=True)
def pause() -> dict[str, Any]:
    """Pauses (and stops a recording in progress, saving the take)."""
    state()
    api.post('pause')
    return get_status()


@mcp.tool(structured_output=True)
def set_loop(section: str | None = None, start_bar: int | None = None, end_bar: int | None = None,
             enabled: bool = True) -> dict[str, Any]:
    """Loops a section (by name) or a range of bars (start_bar to end_bar, inclusive). While recording with the loop on,
    one pass is recorded and the recording stops by itself."""
    m = song_map_raw()
    if section:
        s = find_section(m, section)
        a, b = s['start_s'], s['end_s']
    elif start_bar is not None:
        end_bar = end_bar or start_bar
        a = bar_time(m, start_bar)
        b = m['bars'][end_bar] if end_bar < len(m['bars']) else m['duration']
        if end_bar < start_bar:
            raise ToolError('end_bar must be after start_bar.')
    else:
        api.post('loop', {'on': bool(enabled)})
        return get_status()
    api.post('loop', {'a': a, 'b': b, 'on': bool(enabled)})
    return get_status()


@mcp.tool(structured_output=True)
def clear_loop() -> dict[str, Any]:
    """Removes the loop."""
    state()
    api.post('loop', {'clear': True})
    return get_status()


@mcp.tool(structured_output=True)
def set_part(part: str, on: bool | None = None, volume_db: float | None = None, source: str | None = None,
             take_id: str | None = None) -> dict[str, Any]:
    """Changes one band part (id from get_song_map, e.g. 'drums', 'bass', 'rhythm', 'lead', 'vocals'): turn it on/off,
    volume in dB (-40 to +6; 0 = as on the record), and source: 'original', 'take' (one of the player's takes instead of
    the original) or 'both'."""
    state()
    body = {'id': part}
    if on is not None:
        body['on'] = on
    if volume_db is not None:
        body['volume_db'] = volume_db
    if source is not None:
        body['source'] = source
        if take_id:
            body['take'] = take_id
    api.post('part', body)
    return get_status()


@mcp.tool(structured_output=True)
def set_target(part: str) -> dict[str, Any]:
    """"You will play": turns off the part the player will play (so they replace it) and makes it the part recorded.
    Use 'listen' to turn every part on."""
    state()
    api.post('mode', {'mode': part})
    return get_status()


@mcp.tool(structured_output=True)
def set_options(count_in: bool | None = None, metronome: bool | None = None,
                master_volume_db: float | None = None) -> dict[str, Any]:
    """Count-in (4 beats before playing/recording), metronome (a click on every beat) and the band volume."""
    state()
    body = {}
    if count_in is not None:
        body['count_in'] = count_in
    if metronome is not None:
        body['metronome'] = metronome
    if master_volume_db is not None:
        body['master_db'] = master_volume_db
    api.post('options', body)
    return get_status()


@mcp.tool(structured_output=True)
def record_take(part: str | None = None, max_wait_s: float = 900) -> dict[str, Any]:
    """Records a take of the target part (or of `part`). With a loop on, it records exactly one pass and waits for it to
    finish, then returns the take and its timing analysis: tell the player to get ready before calling. Without a loop
    it starts recording and returns at once: call stop_recording when the player is done."""
    st = state()
    if st['recording']:
        raise ToolError('Already recording.')
    before = {f['id'] for f in api.get('/api/takes')}
    api.post('record', {'part': part} if part else {})
    if not st.get('loop_on'):
        return {'recording': True, 'note': 'Recording until stop_recording is called (no loop is on).'}
    time.sleep(1.0)
    _wait_idle(max_wait_s)
    return _new_take(before)


def _new_take(before):
    new = [f for f in api.get('/api/takes') if f['id'] not in before]
    if not new:
        raise ToolError('No take was saved (too short, or the input is silent).')
    f = new[0]
    out = {'take': _take_summary(f)}
    try:
        out['analysis'] = _analysis_summary(api.get('/api/analysis?id=' + urllib.parse.quote(f['id']), timeout=300))
    except ToolError as e:
        out['analysis_error'] = str(e)
    return out


@mcp.tool(structured_output=True)
def stop_recording() -> dict[str, Any]:
    """Stops the recording in progress; returns the new take and its timing analysis."""
    st = state()
    if not st['recording']:
        raise ToolError('Nothing is being recorded.')
    before = {f['id'] for f in api.get('/api/takes')}
    api.post('stop')
    time.sleep(0.5)
    _wait_idle(300)
    return _new_take(before)


@mcp.tool(structured_output=True)
def list_takes() -> list[dict[str, Any]]:
    """The player's takes of the open song, newest first."""
    state()
    return [_take_summary(f) for f in api.get('/api/takes')]


def _take_id(take_id):
    takes = api.get('/api/takes')
    if not takes:
        raise ToolError('There are no takes in this song yet.')
    if not take_id:
        return takes[0]['id']
    for f in takes:
        if f['id'] == take_id or _norm(f.get('name')) == _norm(take_id):
            return f['id']
    raise ToolError(f'Take not found: "{take_id}". Takes: ' + ', '.join(f.get('name', f['id']) for f in takes))


@mcp.tool(structured_output=True)
def play_take(take_id: str | None = None) -> dict[str, Any]:
    """Plays a take (default: the newest) in place of the original part, together with the band."""
    state()
    api.post('take_play', {'id': _take_id(take_id)})
    return get_status()


@mcp.tool(structured_output=True)
def update_take(take_id: str, name: str | None = None, favorite: bool | None = None,
                offset_ms: float | None = None) -> dict[str, Any]:
    """Renames a take, marks it as favorite, or shifts its timing by ear (offset_ms, -500 to +500)."""
    state()
    body = {'id': _take_id(take_id)}
    if name is not None:
        body['name'] = name
    if favorite is not None:
        body['favorite'] = favorite
    if offset_ms is not None:
        body['offset_ms'] = offset_ms
    api.post('take', body)
    return {'takes': list_takes()}


@mcp.tool(structured_output=True)
def delete_take(take_id: str) -> dict[str, Any]:
    """Moves a take to the trash (it can be restored from the app)."""
    state()
    api.post('take_delete', {'id': _take_id(take_id)})
    return {'takes': list_takes()}


@mcp.tool(structured_output=True)
def analyze_take(take_id: str | None = None) -> dict[str, Any]:
    """Timing analysis of a take (default: the newest): median deviation in ms (+ late, - early) against the original
    part (or the beat grid), consistency, bars to practice, streaks of rushing/dragging and bars where the player
    stopped. 'sentences' is a ready summary in the player's language."""
    state()
    tid = _take_id(take_id)
    return _analysis_summary(api.get('/api/analysis?id=' + urllib.parse.quote(tid), timeout=300))


@mcp.tool(structured_output=True)
def compare_takes(take_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Compares the timing of several takes (default: all of the open song)."""
    state()
    ids = [_take_id(t) for t in take_ids] if take_ids else [f['id'] for f in api.get('/api/takes')]
    out = []
    for tid in ids:
        r = api.get('/api/analysis?id=' + urllib.parse.quote(tid), timeout=300)
        ref = r.get('original') if r.get('reference') == 'original' else r.get('grid')
        out.append({'take': r.get('name'), 'id': tid, 'reference': r.get('reference'),
                    'median_ms': ref and ref['median_ms'], 'within_40_pct': ref and ref['within_40'],
                    'spread_ms': ref and ref['spread_ms'], 'worst_bars': r.get('worst_bars')})
    return out


@mcp.tool(structured_output=True)
def export_mp3(kind: str = 'mix', take_id: str | None = None) -> dict[str, Any]:
    """Exports an MP3: kind 'mix' (what is playing now: the loop, if on) or 'take' (one take)."""
    state()
    body = {'kind': kind}
    if kind == 'take':
        body['id'] = _take_id(take_id)
    api.post('export', body)
    _wait_idle(300)
    return {'ok': True, 'note': 'The MP3 is in the Exports folder of the song.'}


@mcp.tool(structured_output=True)
def set_chords(chart_text: str, song: str | None = None, bpm: float | None = None, time_signature: str | None = None,
               first_bar_s: float | None = None, tonic: str | None = None, tuning: int | None = None) -> dict[str, Any]:
    """Writes the chord chart of a song (chords.txt) and prepares it again. Format: one bar per word, '%' repeats the
    previous bar, 'D5,C5' splits a bar, '-' is a bar with no chord, '[Name]' starts a section; e.g.
    "[Intro]\\nA5 % D5 A5\\n[Verse]\\nA5 % % %". If the song has no bars yet, also give bpm, time_signature and first_bar_s
    (the second where bar 1 starts). tuning: -1 for a guitar tuned a half step down (chords named by the shape played)."""
    st = state()
    sid = find_song(song, st.get('songs', []))['id'] if song else None
    api.post('chords', {'song': sid, 'chart': chart_text, 'bpm': bpm, 'time_signature': time_signature,
                        'first_bar': first_bar_s, 'tonic': tonic, 'tuning': tuning}, timeout=300)
    return get_song_map()


def _lyrics_out(ly):
    st = state()
    m = song_map_raw()
    lines, cur = [], None
    for i, ln in enumerate(ly.get('lines') or []):
        d = {'n': i + 1, 'text': ln['text']}
        if ln.get('t') is not None:
            d.update(where(m, ln['t']))
            if ln['text'] and ln['t'] <= st['pos'] + 0.05:
                cur = i + 1
        lines.append(d)
    return {'timed': ly.get('timed'), 'file': ly.get('file'), 'current_line': cur, 'lines': lines,
            'note': ly.get('note')}


@mcp.tool(structured_output=True)
def get_lyrics() -> dict[str, Any]:
    """Lyrics of the open song, line by line (blank text: a break between verses). When the lyrics are timed, each line
    has the second, bar and section where it is sung; 'current_line' is the line at the player's position. Empty lines
    list when the song has no lyrics yet (the player can paste them in Riffroom, or give them to you for set_lyrics)."""
    return _lyrics_out(api.get('/api/lyrics'))


@mcp.tool(structured_output=True)
def set_lyrics(text: str, align_to_voice: bool = True) -> dict[str, Any]:
    """Writes the lyrics of the open song (lyrics.lrc in the song folder): plain text, one sung line per line and a blank
    line between verses, or LRC with the time of each line ("[01:04.17]text"). Use only lyrics the player gave you.
    align_to_voice: for LRC timed to another recording, shift the times to line up with the song's separated voice."""
    return _lyrics_out(api.post('lyrics_save', {'text': text, 'align': align_to_voice}))


@mcp.tool(structured_output=True)
def shift_lyrics(seconds: float) -> dict[str, Any]:
    """Moves every timed line of the lyrics by `seconds` (negative: the lines show earlier), when the player says the
    lyrics run ahead of or behind the song."""
    return _lyrics_out(api.post('lyrics_shift', {'seconds': seconds}))


@mcp.tool(structured_output=True)
def prepare_song(folder: str, title: str | None = None, artist: str | None = None, bpm: float | None = None,
                 time_signature: str | None = None, first_bar_s: float | None = None) -> dict[str, Any]:
    """Prepares a folder that already has separated tracks (drums.wav, bass.wav, vocals.wav... as Demucs writes them)
    so it shows up as a song; the folder above joins the music folders if needed."""
    state()
    r = api.post('prepare', {'folder': folder, 'title': title, 'artist': artist, 'bpm': bpm,
                             'time_signature': time_signature, 'first_bar': first_bar_s}, timeout=600)
    return {'song_id': r.get('song_id')}


@mcp.tool(structured_output=True)
def import_song(path: str, title: str | None = None, artist: str | None = None, tuning: int = 0,
                time_signature: str | None = None) -> dict[str, Any]:
    """Turns an audio file of the player's (MP3, WAV, FLAC...) into a practice song with the local AI: separates the
    instruments (Demucs), finds beats and bars (beat-this) and suggests chords. It runs on the player's computer and
    takes a few minutes; returns a job id: poll get_job every ~20 s. tuning: -1 if the guitar on the record is tuned a
    half step down. Needs the AI add-on (install.bat -AI)."""
    state()
    r = api.post('import', {'path': path, 'title': title, 'artist': artist, 'tuning': tuning,
                            'time_signature': time_signature}, timeout=120)
    return {'job': r.get('job')}


@mcp.tool(structured_output=True)
def get_job(job_id: str) -> dict[str, Any]:
    """Progress of a long task (import_song): status 'running' / 'done' / 'error', progress 0..1, message, result."""
    state()
    return api.get('/api/job?id=' + urllib.parse.quote(job_id))


@mcp.tool(structured_output=True)
def calibrate_latency() -> dict[str, Any]:
    """Runs "Fix the timing": 8 clicks; the player strums muted strings together with each click (tell them first!).
    Measures the delay between the band and the guitar input so that the takes land on time."""
    state()
    api.post('calibrate')
    time.sleep(1.0)
    st = _wait_idle(60)
    return {'result': st.get('last_calibration'), 'calibrated': st.get('calibrated'), 'latency_ms': st.get('latency_ms')}


@mcp.prompt()
def coach_session(song: str = '', part: str = 'rhythm') -> str:
    """A guided practice session: pick a section, loop it, record, analyze and explain."""
    return (f"Run a practice session in Riffroom{' with the song ' + song if song else ''}, part '{part}'. "
            "1) Open the song and read its map. 2) Ask the player which section to practice (or pick the first one with "
            "chords) and loop it. 3) Set the target part so the original is muted; keep the count-in on. 4) Check that "
            "the latency calibration was done; if not, explain it and run calibrate_latency. 5) Tell the player to get "
            "ready, then record_take. 6) Explain the analysis in plain words: early or late, how consistent, which bars "
            "to repeat. 7) Suggest a focused next step (e.g. loop only the worst bars, use the metronome) and repeat.")


def main():
    os.makedirs(DATA, exist_ok=True)
    logging.basicConfig(filename=os.path.join(DATA, 'mcp.log'), level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    log.info('Riffroom MCP server started (port %d)', PORT)
    mcp.run()


if __name__ == '__main__':
    main()
