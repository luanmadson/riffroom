// SPDX-License-Identifier: GPL-3.0-or-later
'use strict';
// Riffroom — the screen. Talks to the local server (riffroom.py) with JSON and receives the events by SSE.
// The server plays and records; here we only show and ask.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const CHORD_COLORS = { A: '#eda100', D: '#2a78d6', C: '#eb6834', G: '#1baf7a', E: '#e87ba4', F: '#9b8cf2', B: '#4cc3cc' };
const chordColor = c => CHORD_COLORS[(c || '')[0]] || '#94a3b8';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const noPort = s => (s || '').replace(/\(\d+- /, '(').trim();

const S = {
  state: null, song: null, takes: [], L: {}, lang: 'en', modes: [],
  base: { t: 0, at: 0, playing: false },
  tick: {}, peakHold: -90, peakHoldAt: 0, levels: [],
  last: {}, recStart: null, drag: null, hover: null,
  dpr: 1, W: 0, H: 0, bg: null, lit: null, openMenu: null, loadingSong: false,
};

// ---------------------------------------------------------------- languages
function t(key, vars) {
  let s = S.L[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split('{' + k + '}').join(v);
  return s;
}
function applyI18n(root = document) {
  $$('[data-i18n]', root).forEach(el => { el.textContent = t(el.dataset.i18n); });
  $$('[data-i18n-html]', root).forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  $$('[data-i18n-title]', root).forEach(el => { el.title = t(el.dataset.i18nTitle); });
}
async function loadLocale() {
  const r = await fetch('/api/locale');
  const j = await r.json();
  S.L = j.strings; S.lang = j.language;
  document.documentElement.lang = { en: 'en', pt: 'pt-BR', es: 'es' }[S.lang] || 'en';
  applyI18n();
  $('#chip-lang .tx').textContent = S.lang.toUpperCase();
  const m = $('#menu-lang'); m.innerHTML = '';
  for (const [code, name] of Object.entries(j.languages)) {
    const b = document.createElement('button');
    b.textContent = name; b.classList.toggle('active', code === S.lang);
    b.onclick = async () => { closeMenus(); if (code !== S.lang) { await api('options', { language: code }); location.reload(); } };
    m.append(b);
  }
}

// ---------------------------------------------------------------- talking to the server
async function api(action, body = {}) {
  try {
    const r = await fetch('/api/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) { notice(j.error || t('err.generic'), 'error'); return null; }
    if (j && j.version) applyState(j);
    return j;
  } catch (e) {
    notice(t('err.no_answer', { error: e.message }), 'error');
    return null;
  }
}
async function get(path) {
  const r = await fetch(path);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
function connect() {
  const es = new EventSource('/api/events');
  es.addEventListener('state', e => applyState(JSON.parse(e.data)));
  es.addEventListener('tick', e => applyTick(JSON.parse(e.data)));
  es.addEventListener('notice', e => { const d = JSON.parse(e.data); notice(d.text, d.kind, d.action); });
  es.addEventListener('song', () => loadSong());
  es.addEventListener('takes', e => { S.takes = JSON.parse(e.data); afterTakes(); });
  es.addEventListener('calibration', e => showCalibration(JSON.parse(e.data)));
  es.addEventListener('job', e => showJob(JSON.parse(e.data)));
  es.onopen = () => { const d = $('#disconnected'); if (d) d.remove(); };
  es.onerror = () => {
    if (!$('#disconnected')) {
      const d = document.createElement('div');
      d.id = 'disconnected'; d.className = 'disconnected';
      d.textContent = t('ui.disconnected');
      document.body.append(d);
    }
  };
}

// ---------------------------------------------------------------- notices
function notice(text, kind = 'info', action = null) {
  const el = document.createElement('div');
  el.className = 'notice ' + (kind || 'info');
  const tx = document.createElement('div'); tx.className = 'tx'; tx.textContent = text; el.append(tx);
  if (action) {
    const b = document.createElement('button'); b.textContent = action.text;
    b.onclick = () => { doAction(action); el.remove(); };
    el.append(b);
  }
  const x = document.createElement('button'); x.className = 'x'; x.textContent = '✕'; x.title = t('ui.close'); x.onclick = () => el.remove();
  el.append(x);
  $('#notices').append(el);
  while ($('#notices').children.length > 4) $('#notices').firstChild.remove();
  setTimeout(() => el.remove(), action ? 10000 : 6000);
}
function doAction(a) {
  if (a.kind === 'listen') api('take_play', { id: a.id });
  else if (a.kind === 'restore') api('take_restore', { id: a.id });
  else if (a.kind === 'folder') api('open_folder', { which: a.which });
  else if (a.kind === 'open_song') api('song', { id: a.id });
}

// ---------------------------------------------------------------- utilities
const fmt = s => { s = Math.max(0, Math.floor(s + 0.0001)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
function b64(s) { const b = atob(s); const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
function lastAtOrBefore(arr, x) { // last index with arr[i] <= x, or -1
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m] <= x) { r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
function changed(key, value) { if (S.last[key] === value) return false; S.last[key] = value; return true; }
function when(iso) {
  const d = new Date(iso); const today = new Date(); const yday = new Date(Date.now() - 864e5);
  const loc = document.documentElement.lang;
  const hm = d.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === today.toDateString()) return t('ui.today', { time: hm });
  if (d.toDateString() === yday.toDateString()) return t('ui.yesterday', { time: hm });
  return d.toLocaleDateString(loc, { day: '2-digit', month: '2-digit' }) + ' ' + hm;
}
function levelText(db) {
  if (db >= -1) return [t('lvl.clipped'), 'bad'];
  if (db < -30) return [t('lvl.too_low'), 'bad'];
  if (db < -20) return [t('lvl.bit_low'), ''];
  return [t('lvl.good'), 'good'];
}
const fmtDb = v => (v > 0 ? '+' : '') + (Math.round(v * 2) / 2).toFixed(v % 1 ? 1 : 0) + ' dB';

// ---------------------------------------------------------------- song
async function loadSong() {
  if (S.loadingSong) return;
  S.loadingSong = true;
  try {
    const m = await get('/api/song');
    prepareSong(m);
    S.song = m;
    S.last = {};
    buildCounter();
    $('#time-total').textContent = fmt(m.duration);
    await loadTakes();
    resize();
    drawParts(true);
    if (S.state && !S.state.tour_seen && !localStorage.getItem('riffroom.tour')) setTimeout(() => tour(0), 600);
  } catch (e) { console.warn(e); }
  S.loadingSong = false;
}
async function loadTakes() {
  try { S.takes = await get('/api/takes'); } catch (e) { S.takes = []; }
  afterTakes();
}
function afterTakes() {
  drawTakes();
  drawParts(true);
  if (S.song) drawBackground();
}
function prepareSong(m) {
  m.mix = b64(m.peaks.mix);
  m.partPeaks = {};
  for (const [k, v] of Object.entries(m.peaks.parts)) m.partPeaks[k] = b64(v);
  const B = m.bars, bpb = m.beats_per_bar;
  m.beats = [];
  const cin = m.count_in || [];
  cin.forEach((x, i) => m.beats.push({ t: x, bar: 0, k: i, n: cin.length, d: (i + 1 < cin.length ? cin[i + 1] : (B[0] ?? x + 0.9)) - x }));
  for (let i = 0; i < B.length - 1; i++) {
    const d = (B[i + 1] - B[i]) / bpb[i];
    for (let k = 0; k < bpb[i]; k++) m.beats.push({ t: B[i] + k * d, bar: i + 1, k, n: bpb[i], d });
  }
  m.beatT = m.beats.map(p => p.t);
  m.chordT = m.chords.map(a => a.t0);
  const sec = m.sections || [];
  m.sec = sec.map((s, i) => ({
    name: s.name, bar: s.bar,
    t0: B[s.bar - 1] ?? 0,
    t1: i + 1 < sec.length ? B[sec[i + 1].bar - 1] : m.duration,
  }));
  m.secT = m.sec.map(s => s.t0);
  m.nBars = Math.max(0, B.length - 1);
  m.hasBars = B.length > 1;
  m.sub = m.subdivisions || 3;
}
function barStart(x) {
  const B = S.song.bars;
  if (!B.length) return x;
  if (x < B[0] - 0.05) return 0;
  return B[Math.max(0, lastAtOrBefore(B, x + 0.02))];
}
function barEnd(x) {
  const B = S.song.bars;
  if (!B.length) return Math.min(S.song.duration, x);
  if (x < B[0]) return B[0];
  const i = lastAtOrBefore(B, x - 0.02);
  return Math.min(S.song.duration, B[Math.min(B.length - 1, i + 1)]);
}
function sectionAt(x) { const m = S.song; const i = lastAtOrBefore(m.secT, x + 0.01); return i >= 0 ? m.sec[i] : null; }
function barAt(x) {
  const B = S.song.bars;
  if (!B.length || x < B[0] - 0.01) return 0;
  return Math.min(S.song.nBars, lastAtOrBefore(B, x + 0.01) + 1);
}

// ---------------------------------------------------------------- state from the server
function applyState(e) {
  const before = S.state;
  S.state = e;
  if (e.song && !e.loading && (!S.song || S.song.id !== e.song.id)) loadSong();
  if (!before || before.playing !== e.playing || Math.abs((before.pos ?? 0) - e.pos) > 0.3) {
    if (!e.playing || !S.base.playing) S.base = { t: e.pos, at: performance.now(), playing: e.playing };
  }
  $('#loading').hidden = !e.loading;
  const job = (e.jobs || [])[0];
  if (job) showJob(job);
  else if (!S.jobShown) {
    $('#busy').hidden = !e.busy;
    $('#busy-bar').hidden = true;
    if (e.busy) $('#busy .tx').textContent = e.busy;
  }
  // songs
  const sel = $('#sel-song');
  if (changed('songs', JSON.stringify(e.songs.map(m => [m.id, m.prepared, m.title])))) {
    sel.innerHTML = '';
    for (const m of e.songs) {
      const o = document.createElement('option');
      o.value = m.id;
      o.textContent = (m.artist ? `${m.title} · ${m.artist}` : m.title) + (m.prepared ? '' : ' ' + t('ui.new_song'));
      sel.append(o);
    }
    if (!e.songs.length) { const o = document.createElement('option'); o.value = ''; o.textContent = t('ui.no_songs'); sel.append(o); }
    for (const [v, k] of [['__import__', 'ui.import_song'], ['__library__', 'ui.add_library']]) {
      const o = document.createElement('option'); o.value = v; o.textContent = t(k); sel.append(o);
    }
  }
  sel.value = e.song ? e.song.id : (e.songs.length ? sel.value : '');
  const tun = $('#tuning');
  const tuning = e.song ? e.song.tuning : 0;
  tun.hidden = !tuning;
  if (tuning) tun.textContent = tuning === -1 ? t('ui.tuning_half_down') : t('ui.tuning_n', { n: tuning > 0 ? '+' + tuning : String(tuning) });
  // input and output
  const dv = e.devices;
  const ci = $('#chip-input');
  ci.className = 'chip ' + (dv.input_ok ? 'ok' : 'error');
  $('.tx', ci).textContent = dv.input_ok ? t('ui.input_is', { name: dv.input_short }) : t('ui.input_missing');
  ci.title = dv.input_ok ? t('ui.recording_from', { name: dv.input }) : t('ui.input_missing_title');
  if (changed('inputs', JSON.stringify([dv.inputs, dv.input_chosen, dv.input]))) buildInputMenu(dv);
  $('#meter-label').textContent = t('ui.your_guitar') + (dv.input_ok ? ` · ${dv.input_short}` : '');
  $('#chip-output .tx').textContent = t('ui.output_is', { name: dv.output_short || t('ui.none') });
  $('#chip-output').title = dv.output ? t('ui.band_plays_on', { name: dv.output, api: dv.api }) : (dv.error || '');
  $$('#menu-output button').forEach(b => b.classList.toggle('active', b.dataset.output === dv.output_mode));
  $('#menu-output button[data-output="interface"]').disabled = !dv.has_interface_output;
  $('#output-int-tx').textContent = dv.input_ok ? t('ui.output_int_named', { name: dv.input_short }) : t('ui.output_int');
  $('#output-int-tip').textContent = !dv.has_interface_output ? t('ui.output_int_none')
    : dv.gt100 ? t('ui.output_int_tip_gt100') : t('ui.output_int_tip');
  $('#output-pc-tip').textContent = dv.gt100 ? t('ui.output_pc_tip_gt100') : t('ui.output_pc_tip');
  if (dv.error && changed('dev_error', dv.error)) notice(dv.error, 'error');
  const cb = $('#btn-calibrate');
  cb.className = 'chip ' + (e.calibrated ? 'ok' : 'pending');
  $('.tx', cb).textContent = e.calibrated ? t('ui.calibrated', { ms: Math.round(e.latency_ms) }) : t('ui.calibrate');
  // "you will play"
  const parts = e.parts || [];
  const targets = e.targets || [];
  if (changed('modes', JSON.stringify([targets, parts.map(p => [p.id, p.name])]))) buildModes(targets, parts);
  const off = parts.filter(p => !p.on).map(p => p.id);
  const mode = off.length === 0 ? 'listen' : (off.length === 1 && targets.includes(off[0]) ? off[0] : null);
  $$('#modes button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  // transport
  const bp = $('#b-play');
  bp.classList.toggle('playing', e.playing && !e.recording);
  $('#b-play-icon').textContent = e.playing ? '⏸' : '▶';
  $('#b-play-tx').textContent = e.playing ? t('ui.pause') : t('ui.play');
  const br = $('#b-record');
  if (e.recording && !S.recStart) S.recStart = performance.now();
  if (!e.recording) S.recStart = null;
  br.classList.toggle('active', e.recording);
  const targetPart = parts.find(p => p.id === e.target);
  const short = e.target ? (S.L['part.short.' + e.target] || (targetPart ? targetPart.name : '')) : '';
  br.title = e.recording ? t('ui.stop_recording_title') : t('ui.record_part_title', { part: (targetPart ? targetPart.name : '').toLowerCase() });
  if (!e.recording) $('#b-record-tx').textContent = short ? t('ui.record_part', { part: short }) : t('ui.record');
  $('#b-loop').setAttribute('aria-pressed', String(!!e.loop_on));
  $('#b-loop').title = e.loop ? t('ui.loop_range', { a: fmt(e.loop[0]), b: fmt(e.loop[1]) }) : t('ui.loop_mark_first');
  $('#b-countin').setAttribute('aria-pressed', String(!!e.count_in));
  $('#b-metro').setAttribute('aria-pressed', String(!!e.metronome));
  const mv = $('#master');
  if (document.activeElement !== mv) mv.value = e.master_db;
  drawParts(false);
  if (S.song && before && JSON.stringify(before.loop) !== JSON.stringify(e.loop)) S.last.loop = null;
  if (S.song && before && JSON.stringify((before.parts || []).map(p => [p.source, p.take])) !== JSON.stringify(parts.map(p => [p.source, p.take]))) drawBackground();
  if ($('#dlg-calibrate').open) updateCalibration();
}

function applyTick(d) {
  const now = performance.now();
  const prev = posNow();
  if (d.playing && S.base.playing && Math.abs(prev - d.t) < 0.12) {
    S.base = { t: prev + (d.t - prev) * 0.2, at: now, playing: true };
  } else {
    S.base = { t: d.t, at: now, playing: d.playing };
  }
  S.tick = d;
  updateMeter(d);
  if ($('#dlg-calibrate').open && d.calibrating) {
    const n = Math.max(0, Math.min(8, Math.floor((d.t - 1.5) / 0.6) + 1));
    $('#cal-status').innerHTML = n === 0 ? `<span class="big">${esc(t('cal.get_ready'))}</span>`
      : `<span class="big">${esc(t('cal.click_n', { n }))}</span><br>${esc(t('cal.strum_with'))}`;
  }
}
function posNow() {
  const b = S.base;
  if (!b.playing) return b.t;
  let x = b.t + (performance.now() - b.at) / 1000;
  const e = S.state;
  if (e && e.loop && e.loop_on && !e.recording && x > e.loop[1] && b.t <= e.loop[1]) x = e.loop[0] + (x - e.loop[1]);
  return x;
}

// ---------------------------------------------------------------- input menu and "you will play"
function buildInputMenu(dv) {
  const m = $('#menu-input');
  m.innerHTML = '';
  const item = (tx, tip, active, fn, inUse) => {
    const b = document.createElement('button');
    b.innerHTML = esc(tx) + (inUse ? `<span class="in-use">${esc(t('ui.in_use'))}</span>` : '') + (tip ? `<small>${esc(tip)}</small>` : '');
    b.classList.toggle('active', active);
    b.onclick = () => { closeMenus(); fn(); };
    m.append(b);
  };
  item(t('ui.input_auto'), t('ui.input_auto_tip'), !dv.input_chosen, () => api('input', { name: null }));
  const current = noPort(dv.input);
  for (const n of dv.inputs || []) item(n, '', dv.input_chosen === n, () => api('input', { name: n }), n === current);
  m.append(document.createElement('hr'));
  item(t('ui.rescan_devices'), t('ui.rescan_devices_tip'), false, () => api('rescan_devices'));
}
function buildModes(targets, parts) {
  const box = $('#modes');
  box.innerHTML = `<span>${esc(t('ui.you_will_play'))}</span>`;
  const names = Object.fromEntries(parts.map(p => [p.id, p.name]));
  const list = targets.filter(a => names[a]).map(a => [a, names[a]]).concat([['listen', t('ui.just_listen')]]);
  list.forEach(([id, name], i) => {
    const b = document.createElement('button');
    b.dataset.mode = id;
    b.innerHTML = esc(name) + (i < 9 ? `<kbd>${i + 1}</kbd>` : '');
    b.onclick = () => api('mode', { mode: id });
    box.append(b);
  });
  S.modes = list.map(x => x[0]);
  const tx = $('#keys-modes');
  if (tx) tx.textContent = t('help.k_modes_list', { list: list.map(([, n], i) => `${i + 1} = ${n.toLowerCase()}`).join(', ') });
}

// ---------------------------------------------------------------- meter
function updateMeter(d) {
  const tx = $('#meter-tx');
  if (!S.state || !S.state.devices.input_ok || d.peak_db === undefined) {
    $('#meter-bar').style.width = '0%'; $('#meter-peak').style.left = '0%';
    tx.className = 'meter-tx bad'; tx.textContent = t('ui.input_missing_short');
    return;
  }
  const gain = S.state.devices.gt100 ? t('tip.gain_gt100') : t('tip.gain');
  const db = d.peak_db;
  const pct = v => Math.max(0, Math.min(100, (v + 60) / 60 * 100));
  $('#meter-bar').style.width = pct(db) + '%';
  const now = performance.now();
  if (db > S.peakHold || now - S.peakHoldAt > 1500) { S.peakHold = db; S.peakHoldAt = now; }
  $('#meter-peak').style.left = `calc(${pct(S.peakHold)}% - 2px)`;
  S.levels.push(db); if (S.levels.length > 30) S.levels.shift();
  const m = Math.max(...S.levels);
  let txt, cls;
  if (d.clip || m >= -1) { txt = t('tip.clipped', { gain }); cls = 'bad'; }
  else if (m < -55) { txt = t('lvl.silent'); cls = ''; }
  else if (m < -30) { txt = t('tip.too_low', { gain }); cls = 'low'; }
  else if (m < -20) { txt = t('tip.bit_low'); cls = 'low'; }
  else { txt = t('tip.good'); cls = 'good'; }
  if (changed('metertx', txt + Math.round(m))) { tx.className = 'meter-tx ' + cls; tx.textContent = `${Math.round(m)} dB · ${txt}`; }
}

// ---------------------------------------------------------------- NOW / NEXT
function buildCounter() {
  const c = $('#counter');
  c.innerHTML = '';
  const m = S.song, n = m ? Math.max(1, Math.min(8, m.max_beats || 4)) : 4, sub = m ? m.sub : 3;
  for (let g = 0; g < n; g++) {
    const el = document.createElement('div'); el.className = 'group';
    el.innerHTML = `<i class="n">${g + 1}</i>` + '<i></i>'.repeat(sub - 1);
    c.append(el);
  }
  const leg = $('#counter-legend');
  if (m && !m.hasBars) { c.hidden = true; leg.textContent = t('ui.no_bars'); return; }
  c.hidden = false;
  const say = sub === 3 ? t('count.sub3') : t('count.sub2');
  leg.innerHTML = esc(t('ui.count_along')) + ' ' + Array.from({ length: n }, (_, i) => `<b>${i + 1}</b>${esc(say)}`).join(', ') + '. ' + esc(t('ui.count_foot'));
}
function updateNow(x) {
  const m = S.song;
  // count-in before playing
  const until = S.tick.count_until;
  const counting = S.base.playing && until != null && x < until - 0.03 && !S.tick.calibrating;
  if (changed('counting', counting)) $('#countin').hidden = !counting;
  if (counting) {
    const idx = lastAtOrBefore(m.beatT, until - 0.03);
    const pre = m.beatT.slice(Math.max(0, idx - 3), idx + 1);
    const lit = pre.filter(p => p <= x + 0.01).length;
    if (changed('lit', lit)) $$('#countin .nums span').forEach((s, i) => s.classList.toggle('on', i < lit));
  }
  // current and next chord
  const i = lastAtOrBefore(m.chordT, x);
  const cur = i >= 0 && x < m.chords[i].t1 ? m.chords[i] : null;
  const name = cur && cur.chord ? cur.chord : null;
  const beforeBar1 = x < (m.bars[0] ?? 0) - 0.02;
  if (changed('chord', name + '|' + beforeBar1)) {
    const el = $('#now-name');
    if (name) {
      el.textContent = name; el.style.color = chordColor(name);
      $('#now-degree').textContent = cur.degree ? t('ui.degree', { d: cur.degree }) : '';
      const inf = m.chord_info[name];
      $('#now-desc').textContent = inf ? `${inf.note} · ${inf.type}` : '';
      $('#now-diagram').innerHTML = `<img alt="" src="/api/chord.svg?name=${encodeURIComponent(name)}">`;
    } else {
      el.textContent = beforeBar1 ? t('ui.countin_word') : '—'; el.style.color = beforeBar1 ? '#a6adb8' : '#6d7583';
      $('#now-degree').textContent = '';
      $('#now-desc').textContent = beforeBar1 ? t('ui.song_starts_bar1') : (m.chords.length ? t('ui.no_chord_here') : t('ui.no_chords'));
      $('#now-diagram').innerHTML = '';
    }
  }
  let j = i + 1;
  while (j < m.chords.length && (!m.chords[j].chord || m.chords[j].chord === name)) j++;
  const next = j < m.chords.length ? m.chords[j] : null;
  const pi = lastAtOrBefore(m.beatT, x + 0.005);
  const p = pi >= 0 ? m.beats[pi] : null;
  if (changed('next', next ? next.chord + next.t0 : '-')) {
    if (next) {
      $('#next-name').textContent = next.chord; $('#next-name').style.color = chordColor(next.chord);
      $('#next-degree').textContent = next.degree ? t('ui.degree', { d: next.degree }) : '';
      $('#next-diagram').innerHTML = `<img alt="" src="/api/chord.svg?name=${encodeURIComponent(next.chord)}">`;
    } else {
      $('#next-name').textContent = '—'; $('#next-name').style.color = '#6d7583'; $('#next-degree').textContent = ''; $('#next-diagram').innerHTML = '';
    }
  }
  let whenTx = '';
  if (next) {
    const n = lastAtOrBefore(m.beatT, next.t0 - 0.02) - pi + 1;
    if (n <= 1) whenTx = t('ui.on_next_beat');
    else whenTx = t('ui.in_n_beats', { n }) + (n > 8 ? ' ' + t('ui.at_bar', { bar: next.bar }) : '');
  }
  if (changed('when', whenTx)) $('#next-when').innerHTML = whenTx;
  // beat and subdivision counter
  const sub = m.sub;
  const key = p ? `${p.bar}|${p.k}|${Math.min(sub - 1, Math.floor((x - p.t) / p.d * sub))}` : 'x';
  if (changed('beat', key)) {
    const groups = $$('#counter .group');
    const nb = p && p.bar > 0 ? p.n : (m.max_beats || 4);
    const s = p ? Math.max(0, Math.min(sub - 1, Math.floor((x - p.t) / p.d * sub))) : -1;
    groups.forEach((g, gi) => {
      g.style.display = gi < nb ? '' : 'none';
      g.classList.toggle('current', !!p && p.bar > 0 && gi === p.k);
      $$('i', g).forEach((el, ii) => {
        el.classList.toggle('on', !!p && p.bar > 0 && gi === p.k && ii <= s);
        el.classList.toggle('past', !!p && p.bar > 0 && gi < p.k);
      });
    });
  }
  // where
  const c = barAt(x);
  const sec = c > 0 ? sectionAt(x) : null;
  if (changed('where', c + (sec ? sec.name : ''))) {
    if (!m.hasBars) {
      $('#where-section').textContent = m.title || ''; $('#where-bar').textContent = '';
    } else {
      $('#where-section').textContent = c === 0 ? t('ui.countin_word') : (sec ? sec.name : '');
      $('#where-bar').textContent = c === 0 ? t('ui.before_bar1') : t('ui.bar_of', { bar: c, n: m.nBars });
    }
  }
  const tt = fmt(x);
  if (changed('time', tt)) $('#time-now').textContent = tt;
}

// ---------------------------------------------------------------- timeline
const cv = () => $('#cv');
function lanes() {
  const k = S.H / 156;
  return { sec: [4 * k, 24 * k], chord: [28 * k, 50 * k], wave: [55 * k, 122 * k], take: [127 * k, 151 * k] };
}
const PAD = 10;
const xOf = x => PAD + (x / S.song.duration) * (S.W - 2 * PAD);
const tOf = x => Math.max(0, Math.min(S.song.duration, (x - PAD) / (S.W - 2 * PAD) * S.song.duration));

function resize() {
  const c = cv();
  const r = c.getBoundingClientRect();
  S.dpr = window.devicePixelRatio || 1;
  S.W = r.width; S.H = r.height;
  c.width = Math.round(r.width * S.dpr); c.height = Math.round(r.height * S.dpr);
  if (S.song) drawBackground();
}
function waves(ctx, pk, win, t0, t1, y0, y1, color) {
  const x0 = Math.max(0, Math.floor(xOf(t0))), x1 = Math.min(S.W, Math.ceil(xOf(t1)));
  const mid = (y0 + y1) / 2, amp = (y1 - y0) / 2;
  ctx.fillStyle = color;
  for (let x = x0; x < x1; x++) {
    const ta = tOf(x), tb = tOf(x + 1);
    const a = Math.floor((ta - t0) / win), b = Math.max(a + 1, Math.floor((tb - t0) / win));
    let v = 0;
    for (let i = Math.max(0, a); i < Math.min(pk.length, b); i++) if (pk[i] > v) v = pk[i];
    const h = Math.max(0.5, (v / 255) * amp);
    ctx.fillRect(x, mid - h, 1, h * 2);
  }
}
function drawBackground() {
  const m = S.song; if (!m || !S.W) return;
  const mk = () => { const c = document.createElement('canvas'); c.width = Math.round(S.W * S.dpr); c.height = Math.round(S.H * S.dpr); const x = c.getContext('2d'); x.scale(S.dpr, S.dpr); return [c, x]; };
  const [f, ctx] = mk();
  const [cl, ctx2] = mk();
  const F = lanes();
  ctx.font = '600 12px "Segoe UI", sans-serif';
  ctx.textBaseline = 'middle';
  m.sec.forEach((s, i) => {
    const xa = xOf(s.t0), xb = xOf(s.t1);
    ctx.fillStyle = i % 2 ? '#1f242d' : '#262c37';
    ctx.fillRect(xa, F.sec[0], xb - xa - 1, F.sec[1] - F.sec[0]);
    ctx.save(); ctx.beginPath(); ctx.rect(xa, F.sec[0], xb - xa - 4, F.sec[1] - F.sec[0]); ctx.clip();
    ctx.fillStyle = '#cfd4dc'; ctx.fillText(s.name, xa + 5, (F.sec[0] + F.sec[1]) / 2 + 1);
    ctx.restore();
  });
  m.bars.forEach(x => {
    const px = Math.round(xOf(x)) + 0.5;
    const strong = m.sec.some(s => Math.abs(s.t0 - x) < 0.01);
    ctx.fillStyle = strong ? '#ffffff30' : '#ffffff0e';
    ctx.fillRect(px, F.chord[0], 1, F.wave[1] - F.chord[0]);
  });
  ctx.font = '700 11.5px "Segoe UI", sans-serif';
  for (const a of m.chords) {
    if (!a.chord) continue;
    const xa = xOf(a.t0) + 0.5, xb = xOf(a.t1) - 0.5;
    if (xb - xa < 1) continue;
    ctx.fillStyle = chordColor(a.chord);
    ctx.beginPath(); ctx.roundRect(xa, F.chord[0], xb - xa, F.chord[1] - F.chord[0], 3); ctx.fill();
    const w = ctx.measureText(a.chord).width;
    if (xb - xa > w + 6) { ctx.fillStyle = a.chord[0] === 'D' ? '#fff' : '#14161a'; ctx.fillText(a.chord, (xa + xb - w) / 2, (F.chord[0] + F.chord[1]) / 2 + 1); }
  }
  waves(ctx, m.mix, m.peaks.window_s, 0, m.duration, F.wave[0], F.wave[1], '#4d5461');
  waves(ctx2, m.mix, m.peaks.window_s, 0, m.duration, F.wave[0], F.wave[1], '#d3d8df');
  const used = (S.state?.parts || []).filter(p => p.source !== 'original' && p.take);
  const list = used.map(p => [p, S.takes.find(x => x.id === p.take)]).filter(x => x[1] && x[1].peaks);
  if (list.length) {
    const h = (F.take[1] - F.take[0]) / list.length;
    list.forEach(([p, tk], k) => {
      const pk = b64(tk.peaks), ini = tk.start_s + (tk.offset_ms || 0) / 1000;
      const y0 = F.take[0] + k * h;
      ctx.fillStyle = '#ffffff08'; ctx.fillRect(xOf(ini), y0, xOf(ini + tk.duration_s) - xOf(ini), h - 1);
      waves(ctx, pk, 0.02, ini, ini + tk.duration_s, y0, y0 + h - 1, p.color || '#ffc43c');
      waves(ctx2, pk, 0.02, ini, ini + tk.duration_s, y0, y0 + h - 1, p.color || '#ffc43c');
    });
  } else {
    ctx.fillStyle = '#6d7583'; ctx.font = '12px "Segoe UI", sans-serif';
    ctx.fillText(t('ui.takes_lane_empty'), PAD + 4, (F.take[0] + F.take[1]) / 2);
  }
  S.bg = f; S.lit = cl;
}
function drawTimeline(x) {
  const c = cv(); const ctx = c.getContext('2d');
  if (!S.bg) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.drawImage(S.bg, 0, 0);
  const xp = xOf(Math.min(x, S.song.duration));
  ctx.save(); ctx.beginPath(); ctx.rect(0, 0, xp * S.dpr, c.height); ctx.clip(); ctx.drawImage(S.lit, 0, 0); ctx.restore();
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  const e = S.state;
  let lp = e && e.loop ? e.loop.slice() : null;
  const a = S.drag;
  if (a && a.moved) {
    if (a.edge && lp) { if (a.edge === 'a') lp[0] = Math.min(barStart(a.t1), lp[1] - 0.5); else lp[1] = Math.max(barEnd(a.t1), lp[0] + 0.5); }
    else if (!a.edge) lp = [barStart(Math.min(a.t0, a.t1)), barEnd(Math.max(a.t0, a.t1))];
  }
  if (lp) {
    const xa = xOf(lp[0]), xb = xOf(lp[1]);
    const on = (e && e.loop_on) || (a && a.moved);
    ctx.fillStyle = on ? '#ffc43c22' : '#ffffff10';
    ctx.fillRect(xa, 0, xb - xa, S.H);
    ctx.fillStyle = on ? '#ffc43c' : '#8a919c';
    ctx.fillRect(xa - 1, 0, 2, S.H); ctx.fillRect(xb - 1, 0, 2, S.H);
    ctx.fillRect(xa - 5, 0, 10, 6); ctx.fillRect(xb - 5, 0, 10, 6);
  }
  if (S.hover && !a) { ctx.fillStyle = '#ffffff40'; ctx.fillRect(Math.round(S.hover.x), 0, 1, S.H); }
  ctx.fillStyle = '#ff5a5f';
  ctx.fillRect(xp - 1, 0, 2, S.H);
  ctx.beginPath(); ctx.moveTo(xp - 6, 0); ctx.lineTo(xp + 6, 0); ctx.lineTo(xp, 7); ctx.fill();
}
function loopEdge(x) {
  const lp = S.state && S.state.loop;
  if (!lp) return null;
  if (Math.abs(x - xOf(lp[0])) < 7) return 'a';
  if (Math.abs(x - xOf(lp[1])) < 7) return 'b';
  return null;
}
function bindTimeline() {
  const c = cv();
  c.addEventListener('mousedown', e => {
    if (!S.song || e.button !== 0) return;
    S.drag = { x0: e.offsetX, t0: tOf(e.offsetX), t1: tOf(e.offsetX), edge: loopEdge(e.offsetX), moved: false };
  });
  window.addEventListener('mousemove', e => {
    if (!S.song) return;
    const r = c.getBoundingClientRect(); const x = e.clientX - r.left; const y = e.clientY - r.top;
    if (S.drag) {
      if (Math.abs(x - S.drag.x0) > 4) S.drag.moved = true;
      S.drag.t1 = tOf(x);
    }
    const inside = x >= 0 && x <= r.width && y >= 0 && y <= r.height;
    const tip = $('#timeline-tip');
    if (inside) {
      const tx = tOf(x);
      S.hover = { x, t: tx };
      const bar = barAt(tx), sec = bar > 0 ? sectionAt(tx) : null;
      c.style.cursor = loopEdge(x) ? 'ew-resize' : 'pointer';
      tip.hidden = false;
      tip.style.left = Math.max(110, Math.min(r.width - 110, x)) + 'px';
      tip.textContent = !S.song.hasBars ? fmt(tx) : bar === 0 ? `${t('ui.countin_word')} · ${fmt(tx)}`
        : `${t('ui.bar_n', { bar })} · ${sec ? sec.name : ''} · ${fmt(tx)}`;
    } else {
      S.hover = null; tip.hidden = true;
    }
  });
  window.addEventListener('mouseup', () => {
    const a = S.drag; S.drag = null;
    if (!a || !S.song) return;
    if (a.edge && a.moved && S.state.loop) {
      let [la, lb] = S.state.loop;
      if (a.edge === 'a') la = barStart(a.t1); else lb = barEnd(a.t1);
      if (lb - la >= 0.5) api('loop', { a: la, b: lb, on: true });
    } else if (a.moved) {
      const la = barStart(Math.min(a.t0, a.t1)), lb = barEnd(Math.max(a.t0, a.t1));
      if (lb - la >= 0.5) api('loop', { a: la, b: lb, on: true });
    } else {
      api('seek', { t: barStart(a.t0) });
    }
  });
  c.addEventListener('dblclick', e => {
    if (!S.song) return;
    const F = lanes();
    if (e.offsetY <= F.sec[1] + 2) {
      const s = sectionAt(tOf(e.offsetX));
      if (s) { api('loop', { a: s.t0, b: s.t1, on: true }); notice(t('ui.looping', { name: s.name }), 'ok'); }
    }
  });
  c.addEventListener('mouseleave', () => { S.hover = null; $('#timeline-tip').hidden = true; });
}

// ---------------------------------------------------------------- band
function sourceOptions(p) {
  const mine = S.takes.filter(x => x.part === p.id);
  if (!mine.length) return null;
  const o = [['original', esc(t('ui.original'))]];
  for (const x of mine) o.push(['t:' + x.id, esc(t('ui.mine', { name: x.name })) + (x.favorite ? ' ★' : '')]);
  for (const x of mine) o.push(['b:' + x.id, esc(t('ui.original_plus', { name: x.name }))]);
  return o;
}
function drawParts(rebuild) {
  const box = $('#parts');
  const parts = S.state ? S.state.parts : [];
  const key = JSON.stringify(parts.map(p => [p.id, p.name])) + JSON.stringify(S.takes.map(x => [x.id, x.name, x.favorite, x.part]));
  if (rebuild || changed('parts_key', key)) {
    S.last.parts_key = key;
    box.innerHTML = '';
    for (const p of parts) {
      const row = document.createElement('div');
      row.className = 'part'; row.dataset.id = p.id;
      const ops = sourceOptions(p);
      row.innerHTML = `
        <span class="color" style="--c:${esc(p.color)}"></span>
        <span class="name">${esc(p.name)}<canvas class="mini" width="120" height="12"></canvas></span>
        <button class="onoff"><span class="dot"></span><span class="tx"></span></button>
        <input type="range" class="vol" min="-30" max="6" step="0.5" title="${esc(t('ui.volume_title'))}">
        <span class="vol-tx"></span>
        ${ops ? `<select class="source" title="${esc(t('ui.source_title'))}">${ops.map(([v, x]) => `<option value="${esc(v)}">${x}</option>`).join('')}</select>` : '<span></span>'}`;
      box.append(row);
      const onoff = $('.onoff', row), vol = $('.vol', row), src = $('.source', row);
      onoff.onclick = () => { const pp = S.state.parts.find(x => x.id === p.id); api('part', { id: p.id, on: !pp.on }); };
      let wait = null;
      vol.oninput = () => {
        $('.vol-tx', row).textContent = fmtDb(+vol.value);
        clearTimeout(wait); wait = setTimeout(() => api('part', { id: p.id, volume_db: +vol.value }), 60);
      };
      vol.ondblclick = () => { vol.value = 0; api('part', { id: p.id, volume_db: 0 }); };
      if (src) src.onchange = () => {
        const v = src.value;
        if (v === 'original') api('part', { id: p.id, source: 'original' });
        else api('part', { id: p.id, source: v.startsWith('b:') ? 'both' : 'take', take: v.slice(2) });
        src.blur();
      };
      const mini = $('.mini', row);
      if (S.song && S.song.partPeaks[p.id]) {
        const ctx = mini.getContext('2d'); const pk = S.song.partPeaks[p.id];
        ctx.fillStyle = p.color;
        for (let x = 0; x < 120; x++) {
          const a = Math.floor(x / 120 * pk.length), b = Math.floor((x + 1) / 120 * pk.length);
          let v = 0; for (let i = a; i < b; i++) if (pk[i] > v) v = pk[i];
          const h = Math.max(1, v / 255 * 12); ctx.globalAlpha = 0.85; ctx.fillRect(x, 12 - h, 1, h);
        }
      }
      mini.style.cssText = 'display:block;margin-top:3px;opacity:.75';
      mini.title = t('ui.mini_title');
    }
  }
  for (const p of parts) {
    const row = box.querySelector(`.part[data-id="${CSS.escape(p.id)}"]`); if (!row) continue;
    row.classList.toggle('off', !p.on);
    const onoff = $('.onoff', row);
    onoff.classList.toggle('off', !p.on);
    $('.tx', onoff).textContent = p.on ? t('ui.part_on') : t('ui.part_off');
    onoff.title = p.on ? t('ui.part_on_title') : t('ui.part_off_title');
    const vol = $('.vol', row);
    if (document.activeElement !== vol) { vol.value = p.volume_db; $('.vol-tx', row).textContent = fmtDb(p.volume_db); }
    const src = $('.source', row);
    if (src && document.activeElement !== src) {
      src.value = p.source === 'original' ? 'original' : (p.source === 'both' ? 'b:' : 't:') + p.take;
      src.classList.toggle('mine', p.source !== 'original');
    }
  }
}

// ---------------------------------------------------------------- takes
function drawTakes() {
  const box = $('#list');
  box.innerHTML = '';
  if (!S.takes.length) { box.innerHTML = `<div class="empty">${t('ui.no_takes_html')}</div>`; return; }
  for (const f of S.takes) {
    const el = document.createElement('div');
    el.className = 'take' + (f.in_use ? ' in-use' : '');
    const [lv, cls] = levelText(f.peak_db);
    const use = f.in_use === 'take' ? ` · <span class="use">${esc(t('ui.playing_instead'))}</span>`
      : f.in_use === 'both' ? ` · <span class="use">${esc(t('ui.playing_with'))}</span>` : '';
    el.innerHTML = `
      <button class="t-play" title="${esc(t('ui.listen_title'))}">▶</button>
      <button class="t-fav ${f.favorite ? 'yes' : ''}" title="${esc(f.favorite ? t('ui.favorite') : t('ui.make_favorite'))}">${f.favorite ? '★' : '☆'}</button>
      <div class="t-info">
        <div class="t-name" title="${esc(t('ui.rename_title'))}"></div>
        <div class="t-meta">${esc(f.part_name || '')} · ${esc(when(f.created))} · ${fmt(f.duration_s)} · <span class="${cls}">${esc(lv)}</span>${use}</div>
      </div>
      <div class="t-actions"><button class="t-more" title="${esc(t('ui.more'))}">⋯</button>
        <div class="menu" hidden>
          <button class="t-analyze">${esc(t('ui.analyze'))}<small>${esc(t('ui.analyze_tip'))}</small></button>
          <div class="offset">${esc(t('ui.offset_tip'))}
            <div class="offset-row"><button data-d="-10">−10</button><button data-d="-2">−2</button><span class="val"></span><button data-d="2">+2</button><button data-d="10">+10 ms</button></div></div>
          <button class="t-mp3">${esc(t('ui.export_mp3'))}</button>
          <button class="t-rename">${esc(t('ui.rename'))}</button>
          <button class="t-delete danger">${esc(t('ui.delete'))}</button>
        </div></div>`;
    $('.t-name', el).textContent = f.name;
    const val = $('.val', el);
    const showOffset = v => { val.textContent = (v > 0 ? '+' : '') + Math.round(v) + ' ms'; };
    showOffset(f.offset_ms || 0);
    $('.t-play', el).onclick = () => api('take_play', { id: f.id });
    $('.t-fav', el).onclick = () => api('take', { id: f.id, favorite: !f.favorite });
    const menu = $('.menu', el);
    $('.t-more', el).onclick = ev => { ev.stopPropagation(); openMenu(menu); };
    $('.t-analyze', el).onclick = () => { closeMenus(); openAnalysis(f); };
    $$('.offset-row button', el).forEach(b => b.onclick = ev => {
      ev.stopPropagation();
      f.offset_ms = (f.offset_ms || 0) + Number(b.dataset.d);
      showOffset(f.offset_ms);
      api('take', { id: f.id, offset_ms: f.offset_ms });
    });
    $('.t-mp3', el).onclick = () => { closeMenus(); api('export', { kind: 'take', id: f.id }); };
    const rename = () => {
      closeMenus();
      const nm = $('.t-name', el);
      nm.innerHTML = '<input maxlength="60">';
      const inp = $('input', nm); inp.value = f.name; inp.focus(); inp.select();
      let done = false;
      const finish = save => { if (done) return; done = true; if (save && inp.value.trim()) api('take', { id: f.id, name: inp.value.trim() }); else nm.textContent = f.name; };
      inp.onkeydown = ev => { if (ev.key === 'Enter') finish(true); if (ev.key === 'Escape') finish(false); ev.stopPropagation(); };
      inp.onblur = () => finish(true);
    };
    $('.t-rename', el).onclick = rename;
    $('.t-name', el).ondblclick = rename;
    $('.t-delete', el).onclick = () => { closeMenus(); api('take_delete', { id: f.id }); };
    box.append(el);
  }
}
function openMenu(menu) {
  const wasOpen = !menu.hidden;
  closeMenus();
  if (!wasOpen) { menu.hidden = false; S.openMenu = menu; }
}
function closeMenus() { $$('.menu').forEach(m => m.hidden = true); S.openMenu = null; }

// ---------------------------------------------------------------- timing analysis (diverging chart: early blue, late red)
async function openAnalysis(f) {
  const d = $('#dlg-analysis');
  $('#an-title').textContent = t('an.title', { name: f.name });
  $('#an-sentences').innerHTML = `<li>${esc(t('an.working'))}</li>`;
  $('#an-chart').innerHTML = ''; $('#an-table').innerHTML = '';
  if (!d.open) d.showModal();
  let r;
  try { r = await get('/api/analysis?id=' + encodeURIComponent(f.id)); }
  catch (e) { $('#an-sentences').innerHTML = `<li>${esc(t('err.analysis', { error: e.message }))}</li>`; return; }
  $('#an-sentences').innerHTML = (r.sentences || []).map(s => `<li>${esc(s)}</li>`).join('');
  if (r.bars && r.bars.length) drawAnalysisChart(r);
}
function barPath(x, w, y0, y1, r) {
  const up = y1 < y0, h = Math.abs(y1 - y0);
  r = Math.min(r, h, w / 2);
  if (h < 0.5) return `M${x},${y0}h${w}`;
  if (up) return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z`;
  return `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 - r}V${y0}Z`;
}
function drawAnalysisChart(r) {
  const box = $('#an-chart');
  const nBars = S.song && S.song.nBars ? S.song.nBars : Math.max(...r.bars.map(b => b.bar));
  const W = 800, H = 200, L = 52, R = 10, T = 12, B = 26;
  const pw = W - L - R, ph = H - T - B;
  const maxAbs = Math.max(60, ...r.bars.map(b => Math.abs(b.median_ms)));
  const lim = Math.min(200, Math.ceil(maxAbs / 20) * 20);
  const y = v => T + ph / 2 - (Math.max(-lim, Math.min(lim, v)) / lim) * (ph / 2);
  const band = pw / nBars, bw = Math.max(1.5, band - 2);
  const col = v => v > 10 ? 'var(--late)' : v < -10 ? 'var(--early)' : 'var(--neutral)';
  let g = '';
  for (const v of [lim / 2, -lim / 2]) {
    g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#ffffff14" stroke-dasharray="3 4"/>`;
    g += `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="#8a919c">${v > 0 ? '+' : '−'}${Math.abs(v)} ms</text>`;
  }
  g += `<text x="${L - 6}" y="${T + 9}" text-anchor="end" font-size="11" fill="#a6adb8">${esc(t('an.axis_late'))}</text>`;
  g += `<text x="${L - 6}" y="${H - B - 2}" text-anchor="end" font-size="11" fill="#a6adb8">${esc(t('an.axis_early'))}</text>`;
  const step = nBars > 60 ? 10 : nBars > 24 ? 5 : 2;
  for (let b = 1; b <= nBars; b++) {
    if (b === 1 || b % step === 0) g += `<text x="${L + (b - 0.5) * band}" y="${H - 8}" text-anchor="middle" font-size="11" fill="#8a919c">${b}</text>`;
  }
  for (const b of r.bars) {
    const x = L + (b.bar - 1) * band + (band - bw) / 2;
    const v = b.median_ms;
    const y1 = Math.abs(v) < 2 ? y(0) - (v >= 0 ? 2 : -2) : y(v);
    g += `<path d="${barPath(x, bw, y(0), y1, 4)}" fill="${col(v)}" opacity="${b.n >= 3 ? 1 : 0.45}"/>`;
  }
  g += `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="#a6adb8" stroke-width="1"/>`;
  box.innerHTML = `<div class="an-legend"><span><i style="background:var(--early)"></i>${esc(t('an.legend_early'))}</span>`
    + `<span><i style="background:var(--late)"></i>${esc(t('an.legend_late'))}</span>`
    + `<span><i style="background:var(--neutral)"></i>${esc(t('an.legend_on_time'))}</span>`
    + `<span>${esc(t('an.legend_faint'))}</span></div>`
    + `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t('an.chart_label'))}">${g}</svg><div class="an-tip" hidden></div>`;
  const svg = $('svg', box), tip = $('.an-tip', box);
  const byBar = Object.fromEntries(r.bars.map(b => [b.bar, b]));
  svg.addEventListener('mousemove', ev => {
    const rc = svg.getBoundingClientRect();
    const sx = (ev.clientX - rc.left) * W / rc.width;
    const bar = Math.floor((sx - L) / band) + 1;
    const b = byBar[bar];
    if (!b || sx < L) { tip.hidden = true; return; }
    const side = b.median_ms > 0 ? t('an.side_late') : t('an.side_early');
    tip.textContent = t('an.tip', { bar, ms: Math.abs(Math.round(b.median_ms)), side, n: b.n });
    tip.hidden = false;
    tip.style.left = (ev.clientX - rc.left) + 'px';
    tip.style.top = (ev.clientY - rc.top) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.hidden = true; });
  $('#an-table').innerHTML = `<table><tr><th>${esc(t('an.col_bar'))}</th><th>${esc(t('an.col_n'))}</th><th>${esc(t('an.col_ms'))}</th></tr>`
    + r.bars.map(b => `<tr><td>${b.bar}</td><td>${b.n}</td><td>${b.median_ms > 0 ? '+' : ''}${Math.round(b.median_ms)}</td></tr>`).join('') + '</table>';
}

// ---------------------------------------------------------------- jobs (importing a song with the AI)
function showJob(j) {
  const busy = $('#busy');
  if (j.status === 'running') {
    S.jobShown = true;
    busy.hidden = false;
    $('#busy .tx').textContent = `${j.title} · ${j.message}`;
    $('#busy-bar').hidden = false;
    $('#busy-bar div').style.width = Math.round((j.progress || 0) * 100) + '%';
  } else {
    S.jobShown = false;
    busy.hidden = !(S.state && S.state.busy);
    $('#busy-bar').hidden = true;
  }
}
async function startImport() {
  try {
    const r = await fetch('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const j = await r.json();
    if (!r.ok) notice(j.error || t('err.generic'), 'error');
  } catch (e) { notice(t('err.no_answer', { error: e.message }), 'error'); }
}

// ---------------------------------------------------------------- latency calibration
function openCalibration() {
  const d = $('#dlg-calibrate');
  if (!d.open) d.showModal();
  updateCalibration(true);
}
function updateCalibration(start) {
  const e = S.state; const st = $('#cal-status');
  const btn = $('#cal-start');
  btn.disabled = !e.devices.input_ok || e.calibrating || e.recording;
  if (!e.devices.input_ok) { st.innerHTML = `<span class="bad">${esc(t('cal.no_input'))}</span>`; return; }
  if (e.calibrating) return;
  if (e.busy) { st.textContent = e.busy; return; }
  if (start) st.innerHTML = e.calibrated ? t('cal.current', { ms: Math.round(e.latency_ms) }) : esc(t('cal.not_done'));
}
function showCalibration(r) {
  const st = $('#cal-status');
  if (!$('#dlg-calibrate').open) return;
  if (!r || !r.ok) { st.innerHTML = `<span class="bad">${esc(t('cal.failed_short'))}</span> ${esc(t('cal.failed_tip'))}`; return; }
  const side = r.ms > 0 ? t('msg.late_word') : t('msg.early_word');
  let txt = `<span class="big">${Math.abs(Math.round(r.ms))} ms</span><br><span class="good">${esc(t('cal.ready'))}</span> `
    + esc(t('cal.result', { ms: Math.abs(Math.round(r.ms)), side, hits: r.hits, clicks: r.clicks }));
  if (r.spread_ms > 20) txt += '<br>' + esc(t('cal.spread', { ms: Math.round(r.spread_ms) }));
  st.innerHTML = txt;
}

// ---------------------------------------------------------------- step by step and help
const TOUR = ['modes', 'play', 'now', 'record', 'calibrate'];
const TOUR_TARGET = { modes: '#modes', play: '#b-play', now: '#card-now', record: '#b-record', calibrate: '#btn-calibrate' };
let tourI = -1;
function tour(i) {
  $$('.highlight').forEach(x => x.classList.remove('highlight'));
  const d = $('#dlg-tour');
  if (i >= TOUR.length || i < 0) {
    if (d.open) d.close();
    localStorage.setItem('riffroom.tour', '1');
    if (S.state && !S.state.tour_seen) api('options', { tour_seen: true });
    tourI = -1; return;
  }
  tourI = i;
  const k = TOUR[i];
  $('#tour-step').textContent = t('tour.step', { i: i + 1, n: TOUR.length });
  $('#tour-title').textContent = t(`tour.${k}.title`);
  $('#tour-text').textContent = t(`tour.${k}.text`);
  $('#tour-next').textContent = i === TOUR.length - 1 ? t('tour.calibrate_now') : t('tour.next');
  $('#tour-skip').textContent = i === TOUR.length - 1 ? t('tour.later') : t('tour.skip');
  const target = $(TOUR_TARGET[k]); if (target) target.classList.add('highlight');
  if (!d.open) d.show();
  d.style.position = 'fixed'; d.style.margin = '0'; d.style.left = '50%'; d.style.top = '38%'; d.style.transform = 'translate(-50%, -50%)'; d.style.zIndex = 80;
}

// ---------------------------------------------------------------- frame by frame
function frame() {
  try {
    const x = posNow();
    if (S.song) { updateNow(x); drawTimeline(x); }
    if (S.state && S.state.recording && S.recStart) {
      const tx = t('ui.stop_n', { time: fmt((performance.now() - S.recStart) / 1000) });
      if (changed('rec', tx)) $('#b-record-tx').textContent = tx;
    }
  } catch (e) { console.error(e); }
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- buttons and keys
function toggleLoop() {
  if (!S.state || !S.state.loop) { notice(t('ui.loop_mark_first')); return; }
  api('loop', { on: !S.state.loop_on });
}
function bindButtons() {
  $('#b-home').onclick = () => api('home');
  $('#b-prev').onclick = () => api('bar', { delta: -1 });
  $('#b-next').onclick = () => api('bar', { delta: 1 });
  $('#b-play').onclick = () => api('toggle_play');
  $('#b-record').onclick = () => api('toggle_record');
  $('#b-loop').onclick = toggleLoop;
  $('#b-countin').onclick = () => api('options', { count_in: !S.state.count_in });
  $('#b-metro').onclick = () => api('options', { metronome: !S.state.metronome });
  const mv = $('#master'); let wait = null;
  mv.oninput = () => { clearTimeout(wait); wait = setTimeout(() => api('options', { master_db: +mv.value }), 60); };
  mv.ondblclick = () => { mv.value = 0; api('options', { master_db: 0 }); };
  $('#sel-song').onchange = e => {
    const v = e.target.value;
    const back = () => { e.target.value = S.state && S.state.song ? S.state.song.id : ''; };
    if (v === '__library__') { back(); api('add_library'); }
    else if (v === '__import__') { back(); startImport(); }
    else if (v) api('song', { id: v });
    e.target.blur();
  };
  $('#chip-input').onclick = ev => { ev.stopPropagation(); openMenu($('#menu-input')); };
  $('#chip-output').onclick = ev => { ev.stopPropagation(); openMenu($('#menu-output')); };
  $('#chip-lang').onclick = ev => { ev.stopPropagation(); openMenu($('#menu-lang')); };
  $$('#menu-output button').forEach(b => b.onclick = () => { closeMenus(); api('output', { mode: b.dataset.output }); });
  $('#btn-calibrate').onclick = openCalibration;
  $('#cal-start').onclick = () => { $('#cal-status').innerHTML = `<span class="big">${esc(t('cal.get_ready'))}</span>`; api('calibrate'); };
  $('#cal-close').onclick = () => $('#dlg-calibrate').close();
  $('#an-close').onclick = () => $('#dlg-analysis').close();
  $('#btn-help').onclick = () => $('#dlg-help').showModal();
  $('#help-close').onclick = () => $('#dlg-help').close();
  $('#help-tour').onclick = () => { $('#dlg-help').close(); tour(0); };
  $('#tour-next').onclick = () => { const last = tourI === TOUR.length - 1; tour(tourI + 1); if (last) openCalibration(); };
  $('#tour-skip').onclick = () => tour(TOUR.length);
  $('#btn-export-mix').onclick = () => api('export', { kind: 'mix' });
  $('#btn-folder').onclick = () => api('open_folder', { which: 'takes' });
  let lastBack = 0;
  window.addEventListener('focus', () => {  // back from another window: the list of songs may have changed
    if (S.state && !S.state.recording && performance.now() - lastBack > 3000) { lastBack = performance.now(); api('rescan'); }
  });
  document.addEventListener('click', e => {
    if (S.openMenu && !e.target.closest('.menu')) closeMenus();
    const b = e.target.closest('button'); if (b) b.blur();
  });
  document.addEventListener('keyup', e => { if (e.key === ' ' && e.target.tagName === 'BUTTON') e.preventDefault(); });
  document.addEventListener('keydown', e => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.target.closest('input:not([type=range]), select, textarea')) return;
    if ($('#dlg-calibrate').open || $('#dlg-help').open || $('#dlg-analysis').open) return;
    const k = e.key;
    if (/^[1-9]$/.test(k) && S.modes && S.modes[+k - 1]) { e.preventDefault(); if (!e.repeat) api('mode', { mode: S.modes[+k - 1] }); return; }
    const actions = {
      ' ': () => api('toggle_play'), r: () => api('toggle_record'), R: () => api('toggle_record'),
      Home: () => api('home'), ArrowLeft: () => api('bar', { delta: -1 }), ArrowRight: () => api('bar', { delta: 1 }),
      l: toggleLoop, L: toggleLoop,
      c: () => api('options', { count_in: !S.state.count_in }), C: () => api('options', { count_in: !S.state.count_in }),
      m: () => api('options', { metronome: !S.state.metronome }), M: () => api('options', { metronome: !S.state.metronome }),
      '?': () => $('#dlg-help').showModal(),
    };
    if (actions[k]) { e.preventDefault(); if (e.repeat) return; actions[k](); }
  });
  window.addEventListener('resize', () => resize());
}

async function start() {
  try { await loadLocale(); } catch (e) { console.warn(e); }
  buildCounter();
  bindButtons();
  bindTimeline();
  resize();
  try { applyState(await get('/api/state')); } catch (err) { console.warn(err); }
  connect();
  requestAnimationFrame(frame);
}
start();
