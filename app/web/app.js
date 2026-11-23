'use strict';
// Sala de Ensaio — a tela. Fala com o servidor local (sala_de_ensaio.py) por JSON e recebe os eventos por SSE.
// Quem toca e grava é o servidor; aqui só se mostra e se pede.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const CORES = { A: '#eda100', D: '#2a78d6', C: '#eb6834', G: '#1baf7a', E: '#e87ba4', F: '#9b8cf2', B: '#4cc3cc' };
const corAcorde = a => CORES[(a || '')[0]] || '#94a3b8';
const curto = nome => (nome || '').replace(/^Guitarra /i, '').toLowerCase();
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const semPorta = s => (s || '').replace(/\(\d+- /, '(').trim();

const S = {
  est: null, mus: null, tent: [],
  base: { t: 0, at: 0, tocando: false },
  tique: {}, picoHold: -90, picoHoldAt: 0, niveis: [],
  ult: {}, gravInicio: null, arrasto: null, hover: null,
  dpr: 1, W: 0, H: 0, fundo: null, claro: null, menuAberto: null,
};

// ---------------------------------------------------------------- conversa com o servidor
async function api(acao, corpo = {}) {
  try {
    const r = await fetch('/api/' + acao, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
    const j = await r.json();
    if (!r.ok) { aviso(j.erro || 'Algo deu errado.', 'erro'); return null; }
    aplicarEstado(j);
    return j;
  } catch (e) {
    aviso('Sem resposta do app: ' + e.message, 'erro');
    return null;
  }
}
async function pegar(caminho) {
  const r = await fetch(caminho);
  const j = await r.json();
  if (!r.ok) throw new Error(j.erro || r.status);
  return j;
}

function conectar() {
  const es = new EventSource('/api/eventos');
  es.addEventListener('estado', e => aplicarEstado(JSON.parse(e.data)));
  es.addEventListener('tique', e => aplicarTique(JSON.parse(e.data)));
  es.addEventListener('aviso', e => { const d = JSON.parse(e.data); aviso(d.texto, d.tipo, d.acao); });
  es.addEventListener('musica', () => carregarMusica());
  es.addEventListener('tentativas', e => { S.tent = JSON.parse(e.data); depoisTentativas(); });
  es.addEventListener('calibracao', e => mostrarCalibracao(JSON.parse(e.data)));
  es.onopen = () => { const d = $('#desconectado'); if (d) d.remove(); };
  es.onerror = () => {
    if (!$('#desconectado')) {
      const d = document.createElement('div');
      d.id = 'desconectado'; d.className = 'desconectado';
      d.textContent = 'Sem conexão com o app. Se ele foi fechado, abra de novo pelo atalho "Sala de Ensaio".';
      document.body.append(d);
    }
  };
}

// ---------------------------------------------------------------- avisos
function aviso(texto, tipo = 'info', acao = null) {
  const el = document.createElement('div');
  el.className = 'aviso ' + (tipo || 'info');
  const tx = document.createElement('div'); tx.className = 'tx'; tx.textContent = texto; el.append(tx);
  if (acao) {
    const b = document.createElement('button'); b.textContent = acao.texto;
    b.onclick = () => { fazerAcao(acao); el.remove(); };
    el.append(b);
  }
  const x = document.createElement('button'); x.className = 'x'; x.textContent = '✕'; x.title = 'Fechar'; x.onclick = () => el.remove();
  el.append(x);
  $('#avisos').append(el);
  while ($('#avisos').children.length > 4) $('#avisos').firstChild.remove();
  setTimeout(() => el.remove(), acao ? 10000 : 6000);
}
function fazerAcao(a) {
  if (a.tipo === 'ouvir') api('tentativa_ouvir', { id: a.id });
  else if (a.tipo === 'restaurar') api('tentativa_restaurar', { id: a.id });
  else if (a.tipo === 'pasta') api('abrir_pasta', { qual: a.qual });
}

// ---------------------------------------------------------------- utilidades
const fmt = s => { s = Math.max(0, Math.floor(s + 0.0001)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
function b64(s) { const b = atob(s); const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
function buscar(arr, t) { // último índice com arr[i] <= t, ou -1
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m] <= t) { r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
function mudou(chave, valor) { if (S.ult[chave] === valor) return false; S.ult[chave] = valor; return true; }
function quando(iso) {
  const d = new Date(iso); const hoje = new Date(); const ontem = new Date(Date.now() - 864e5);
  const hm = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === hoje.toDateString()) return 'hoje ' + hm;
  if (d.toDateString() === ontem.toDateString()) return 'ontem ' + hm;
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }) + ' ' + hm;
}
function nivelTexto(db) {
  if (db >= -1) return ['estourou', 'ruim'];
  if (db < -30) return ['muito baixo', 'ruim'];
  if (db < -20) return ['um pouco baixo', ''];
  return ['nível bom', 'bom'];
}

// ---------------------------------------------------------------- música
async function carregarMusica() {
  if (S.carregandoMus) return;
  S.carregandoMus = true;
  try {
    const m = await pegar('/api/musica');
    preparar(m);
    S.mus = m;
    S.ult = {};
    montarContador();
    $('#tempo-total').textContent = fmt(m.duracao);
    await carregarTentativas();
    redimensionar();
    desenharPartes(true);
    if (S.est && !S.est.tour_visto && !localStorage.getItem('ensaio.tour')) setTimeout(() => tour(0), 600);
  } catch (e) { console.warn(e); }
  S.carregandoMus = false;
}
async function carregarTentativas() {
  try { S.tent = await pegar('/api/tentativas'); } catch (e) { S.tent = []; }
  depoisTentativas();
}
function depoisTentativas() {
  desenharTentativas();
  desenharPartes(true);
  if (S.mus) desenharFundo();
}
function preparar(m) {
  m.mix = b64(m.picos.mistura);
  m.partesPk = {};
  for (const [k, v] of Object.entries(m.picos.partes)) m.partesPk[k] = b64(v);
  const B = m.compassos, npc = m.pulsos_por_compasso;
  m.pul = [];
  const cont = m.contagem || [];
  cont.forEach((t, i) => m.pul.push({ t, c: 0, k: i, n: cont.length, d: (i + 1 < cont.length ? cont[i + 1] : (B[0] ?? t + 0.9)) - t }));
  for (let i = 0; i < B.length - 1; i++) {
    const d = (B[i + 1] - B[i]) / npc[i];
    for (let k = 0; k < npc[i]; k++) m.pul.push({ t: B[i] + k * d, c: i + 1, k, n: npc[i], d });
  }
  m.pulT = m.pul.map(p => p.t);
  m.acT = m.acordes.map(a => a.t0);
  const sec = m.secoes || [];
  m.sec = sec.map((s, i) => ({
    nome: s.nome, c0: s.compasso,
    t0: B[s.compasso - 1] ?? 0,
    t1: i + 1 < sec.length ? B[sec[i + 1].compasso - 1] : m.duracao,
  }));
  m.secT = m.sec.map(s => s.t0);
  m.nComp = Math.max(0, B.length - 1);
  m.temCompassos = B.length > 1;
  m.sub = m.subdivisoes || 3;
}
function inicioCompasso(t) {
  const B = S.mus.compassos;
  if (!B.length) return t;
  if (t < B[0] - 0.05) return 0;
  return B[Math.max(0, buscar(B, t + 0.02))];
}
function fimCompasso(t) {
  const B = S.mus.compassos;
  if (!B.length) return Math.min(S.mus.duracao, t);
  if (t < B[0]) return B[0];
  const i = buscar(B, t - 0.02);
  return Math.min(S.mus.duracao, B[Math.min(B.length - 1, i + 1)]);
}
function secaoEm(t) { const m = S.mus; const i = buscar(m.secT, t + 0.01); return i >= 0 ? m.sec[i] : null; }
function compassoEm(t) {
  const B = S.mus.compassos;
  if (!B.length || t < B[0] - 0.01) return 0;
  return Math.min(S.mus.nComp, buscar(B, t + 0.01) + 1);
}

// ---------------------------------------------------------------- estado vindo do servidor
function aplicarEstado(e) {
  const antes = S.est;
  S.est = e;
  if (e.musica && !e.carregando && (!S.mus || S.mus.id !== e.musica.id)) carregarMusica();
  if (!antes || antes.tocando !== e.tocando || Math.abs((antes.pos ?? 0) - e.pos) > 0.3) {
    if (!e.tocando || !S.base.tocando) S.base = { t: e.pos, at: performance.now(), tocando: e.tocando };
  }
  // carregando / ocupado
  $('#carregando').hidden = !e.carregando;
  $('#ocupado').hidden = !e.ocupado;
  if (e.ocupado) $('#ocupado .tx').textContent = e.ocupado;
  // músicas
  const sel = $('#sel-musica');
  const opcoes = JSON.stringify(e.musicas.map(m => [m.id, m.preparada]));
  if (mudou('musicas', opcoes)) {
    sel.innerHTML = '';
    for (const m of e.musicas) {
      const o = document.createElement('option');
      o.value = m.id;
      o.textContent = (m.artista ? `${m.titulo} · ${m.artista}` : m.titulo) + (m.preparada ? '' : ' (nova: será preparada ao abrir)');
      sel.append(o);
    }
    if (!e.musicas.length) { const o = document.createElement('option'); o.value = ''; o.textContent = 'Nenhuma música ainda'; sel.append(o); }
    const o = document.createElement('option'); o.value = '__adicionar__'; o.textContent = '＋ Adicionar pasta de músicas…'; sel.append(o);
  }
  sel.value = e.musica ? e.musica.id : (e.musicas.length ? sel.value : '');
  // entrada e saída
  const ap = e.aparelhos;
  const ce = $('#chip-entrada');
  ce.className = 'chip ' + (ap.entrada_ok ? 'ok' : 'erro');
  $('.tx', ce).textContent = ap.entrada_ok ? `Entrada: ${ap.entrada_apelido}` : 'Entrada não encontrada';
  ce.title = ap.entrada_ok ? `Gravando de: ${ap.entrada} (clique para trocar)` : 'Ligue a interface (ex.: a GT-100) e escolha "Procurar aparelhos de novo" neste menu.';
  if (mudou('entradas', JSON.stringify([ap.entradas, ap.entrada_escolhida, ap.entrada]))) montarMenuEntrada(ap);
  $('#medidor-rot').textContent = 'SUA GUITARRA' + (ap.entrada_ok ? ` · ${ap.entrada_apelido}` : '');
  $('#chip-saida .tx').textContent = 'Saída: ' + (ap.saida_apelido || 'nenhuma');
  $('#chip-saida').title = ap.saida ? `A banda toca em: ${ap.saida} (${ap.api})` : (ap.erro || '');
  $$('#menu-saida button').forEach(b => b.classList.toggle('ativo', b.dataset.saida === ap.modo_saida));
  $('#menu-saida button[data-saida="interface"]').disabled = !ap.tem_saida_interface;
  $('#saida-int-tx').textContent = ap.entrada_ok ? `Pela interface (${ap.entrada_apelido})` : 'Pela interface';
  $('#saida-int-dica').textContent = !ap.tem_saida_interface ? 'Esta entrada não tem saída de som.'
    : ap.gt100 ? 'A banda toca junto com a guitarra, na saída da GT-100 (fone ou amp). Ajuste o volume da banda no MIX LEVEL da USB.'
    : 'A banda toca junto com a guitarra, na saída da interface (fone ou amp).';
  $('#saida-pc-dica').textContent = ap.gt100 ? 'A banda toca na caixa ou no fone do PC; a guitarra, no amp ou no fone da GT-100.'
    : 'A banda toca na saída padrão do Windows; a guitarra, no amp ou no fone da sua interface.';
  if (ap.erro && mudou('erro_ap', ap.erro)) aviso(ap.erro, 'erro');
  const tp = $('#btn-tempo');
  tp.className = 'chip ' + (e.calibrado ? 'ok' : 'pendente');
  $('.tx', tp).textContent = e.calibrado ? `Tempo acertado (${Math.round(e.ajuste_ms)} ms)` : 'Acertar o tempo';
  // modos ("você vai tocar")
  const partes = e.partes || [];
  const alvos = e.alvos || [];
  if (mudou('modos', JSON.stringify([alvos, partes.map(p => [p.id, p.nome])]))) montarModos(alvos, partes);
  const desl = partes.filter(p => !p.ligada).map(p => p.id);
  const modo = desl.length === 0 ? 'ouvir' : (desl.length === 1 && alvos.includes(desl[0]) ? desl[0] : null);
  $$('#modos button').forEach(b => b.classList.toggle('ativo', b.dataset.modo === modo));
  // transporte
  const bt = $('#b-tocar');
  bt.classList.toggle('tocando', e.tocando && !e.gravando);
  $('#b-tocar-ic').textContent = e.tocando ? '⏸' : '▶';
  $('#b-tocar-tx').textContent = e.tocando ? 'Pausar' : 'Tocar';
  const bg = $('#b-gravar');
  if (e.gravando && !S.gravInicio) S.gravInicio = performance.now();
  if (!e.gravando) S.gravInicio = null;
  bg.classList.toggle('ativo', e.gravando);
  const nomeAlvo = (partes.find(p => p.id === e.alvo) || {}).nome || '';
  bg.title = e.gravando ? 'Parar a gravação (R ou Espaço)' : `Gravar uma tentativa de ${nomeAlvo.toLowerCase()} (R)`;
  if (!e.gravando) $('#b-gravar-tx').textContent = ('Gravar ' + curto(nomeAlvo)).trim();
  $('#b-trecho').setAttribute('aria-pressed', String(!!e.trecho_ligado));
  $('#b-trecho').title = e.trecho ? `Repetir ${fmt(e.trecho[0])}–${fmt(e.trecho[1])} (L)` : 'Marque um trecho: arraste na linha do tempo, ou dê dois cliques numa parte da música (faixa de cima).';
  $('#b-contagem').setAttribute('aria-pressed', String(!!e.contagem));
  $('#b-metro').setAttribute('aria-pressed', String(!!e.metronomo));
  const vg = $('#vol-geral');
  if (document.activeElement !== vg) vg.value = e.volume_geral_db;
  desenharPartes(false);
  if (S.mus && antes && JSON.stringify(antes.trecho) !== JSON.stringify(e.trecho)) S.ult.trecho = null;
  if (S.mus && antes && JSON.stringify((antes.partes || []).map(p => [p.fonte, p.tentativa])) !== JSON.stringify(partes.map(p => [p.fonte, p.tentativa]))) desenharFundo();
  if ($('#dlg-tempo').open) atualizarDialogoTempo();
}

function aplicarTique(d) {
  const agora = performance.now();
  const prev = posAgora();
  if (d.tocando && S.base.tocando && Math.abs(prev - d.t) < 0.12) {
    S.base = { t: prev + (d.t - prev) * 0.2, at: agora, tocando: true };
  } else {
    S.base = { t: d.t, at: agora, tocando: d.tocando };
  }
  S.tique = d;
  atualizarMedidor(d);
  if ($('#dlg-tempo').open && d.calibrando) {
    const n = Math.max(0, Math.min(8, Math.floor((d.t - 1.5) / 0.6) + 1));
    $('#tempo-status').innerHTML = n === 0 ? '<span class="grande">Prepare-se…</span>' : `<span class="grande">Clique ${n} de 8</span><br>Palhete junto com cada clique.`;
  }
}
function posAgora() {
  const b = S.base;
  if (!b.tocando) return b.t;
  let t = b.t + (performance.now() - b.at) / 1000;
  const e = S.est;
  if (e && e.trecho && e.trecho_ligado && !e.gravando && t > e.trecho[1] && b.t <= e.trecho[1]) t = e.trecho[0] + (t - e.trecho[1]);
  return t;
}

// ---------------------------------------------------------------- entrada e "você vai tocar"
function montarMenuEntrada(ap) {
  const m = $('#menu-entrada');
  m.innerHTML = '';
  const item = (tx, dica, ativo, fn, emUso) => {
    const b = document.createElement('button');
    b.innerHTML = esc(tx) + (emUso ? '<span class="em-uso">em uso</span>' : '') + (dica ? `<small>${esc(dica)}</small>` : '');
    b.classList.toggle('ativo', ativo);
    b.onclick = () => { fecharMenus(); fn(); };
    m.append(b);
  };
  item('Automática', 'A GT-100, se estiver ligada; senão, a entrada padrão do Windows.', !ap.entrada_escolhida, () => api('entrada', { nome: null }));
  const atual = semPorta(ap.entrada);
  for (const n of ap.entradas || []) item(n, '', ap.entrada_escolhida === n, () => api('entrada', { nome: n }), n === atual);
  m.append(document.createElement('hr'));
  item('Procurar aparelhos de novo', 'Depois de ligar o cabo USB da interface.', false, () => api('procurar'));
}
function montarModos(alvos, partes) {
  const box = $('#modos');
  box.innerHTML = '<span>Você vai tocar:</span>';
  const nomes = Object.fromEntries(partes.map(p => [p.id, p.nome]));
  const lista = alvos.filter(a => nomes[a]).map(a => [a, nomes[a]]).concat([['ouvir', 'Só ouvir']]);
  lista.forEach(([id, nome], i) => {
    const b = document.createElement('button');
    b.dataset.modo = id;
    b.innerHTML = esc(nome) + (i < 9 ? `<kbd>${i + 1}</kbd>` : '');
    b.onclick = () => api('modo', { modo: id });
    box.append(b);
  });
  S.modos = lista.map(x => x[0]);
  const tx = $('#teclas-modos');
  if (tx) tx.textContent = 'você vai tocar: ' + lista.map(([, n], i) => `${i + 1} = ${n.toLowerCase()}`).join(', ');
}

// ---------------------------------------------------------------- medidor
function atualizarMedidor(d) {
  const tx = $('#med-txt');
  if (!S.est || !S.est.aparelhos.entrada_ok || d.pico_db === undefined) {
    $('#med-barra').style.width = '0%'; $('#med-pico').style.left = '0%';
    tx.className = 'medidor-txt ruim'; tx.textContent = 'entrada não encontrada';
    return;
  }
  const ganho = S.est.aparelhos.gt100 ? 'o PATCH LEVEL' : 'o ganho';
  const db = d.pico_db;
  const pct = v => Math.max(0, Math.min(100, (v + 60) / 60 * 100));
  $('#med-barra').style.width = pct(db) + '%';
  const agora = performance.now();
  if (db > S.picoHold || agora - S.picoHoldAt > 1500) { S.picoHold = db; S.picoHoldAt = agora; }
  $('#med-pico').style.left = `calc(${pct(S.picoHold)}% - 2px)`;
  S.niveis.push(db); if (S.niveis.length > 30) S.niveis.shift();
  const m = Math.max(...S.niveis);
  let txt, cls;
  if (d.clip || m >= -1) { txt = `estourando: abaixe ${ganho}`; cls = 'ruim'; }
  else if (m < -55) { txt = 'em silêncio (toque para ver o nível)'; cls = ''; }
  else if (m < -30) { txt = `muito baixo: suba ${ganho}`; cls = 'baixo'; }
  else if (m < -20) { txt = 'um pouco baixo'; cls = 'baixo'; }
  else { txt = 'nível bom'; cls = 'bom'; }
  if (mudou('medtx', txt + Math.round(m))) { tx.className = 'medidor-txt ' + cls; tx.textContent = `${Math.round(m)} dB · ${txt}`; }
}

// ---------------------------------------------------------------- AGORA / PRÓXIMO
function montarContador() {
  const c = $('#contador');
  c.innerHTML = '';
  const m = S.mus, n = m ? Math.max(1, Math.min(8, m.pulsos_max || 4)) : 4, sub = m ? m.sub : 3;
  for (let g = 0; g < n; g++) {
    const el = document.createElement('div'); el.className = 'grupo';
    el.innerHTML = `<i class="n">${g + 1}</i>` + '<i></i>'.repeat(sub - 1);
    c.append(el);
  }
  const leg = $('#contador-leg');
  if (m && !m.temCompassos) {
    c.hidden = true;
    leg.textContent = 'Esta música não tem os compassos marcados: rode o preparar_musica.py com --bpm para ver os pulsos aqui.';
    return;
  }
  c.hidden = false;
  const fala = sub === 3 ? '-na-na' : '-e';
  leg.innerHTML = 'Conte junto: ' + Array.from({ length: n }, (_, i) => `<b>${i + 1}</b>${fala}`).join(', ') + '. O número cai no pé.';
}
function atualizarAgora(t) {
  const m = S.mus;
  // contagem antes de tocar
  const ate = S.tique.contagem_ate;
  const contando = S.base.tocando && ate != null && t < ate - 0.03 && !S.tique.calibrando;
  if (mudou('contando', contando)) $('#contagem').hidden = !contando;
  if (contando) {
    const idx = buscar(m.pulT, ate - 0.03);
    const pre = m.pulT.slice(Math.max(0, idx - 3), idx + 1);
    const lit = pre.filter(p => p <= t + 0.01).length;
    if (mudou('lit', lit)) $$('#contagem .nums span').forEach((s, i) => s.classList.toggle('on', i < lit));
  }
  // acorde atual e próximo
  const i = buscar(m.acT, t);
  const cur = i >= 0 && t < m.acordes[i].t1 ? m.acordes[i] : null;
  const nome = cur && cur.acorde ? cur.acorde : null;
  if (mudou('ac', nome + '|' + (t < (m.compassos[0] ?? 0) - 0.02))) {
    const el = $('#ag-nome');
    if (nome) {
      el.textContent = nome; el.style.color = corAcorde(nome);
      $('#ag-grau').textContent = cur.grau ? 'grau ' + cur.grau : '';
      const inf = m.acordes_info[nome];
      $('#ag-desc').textContent = inf ? `${inf.nota} · ${inf.tipo}` : '';
      $('#ag-desenho').innerHTML = `<img alt="" src="/api/acorde.svg?nome=${encodeURIComponent(nome)}">`;
    } else {
      const antes = t < (m.compassos[0] ?? 0) - 0.02;
      el.textContent = antes ? 'Contagem' : '—'; el.style.color = antes ? '#a6adb8' : '#6d7583';
      $('#ag-grau').textContent = '';
      $('#ag-desc').textContent = antes ? 'A música começa no compasso 1.'
        : (m.acordes.length ? 'Sem acorde neste trecho.' : 'Esta música não tem os acordes marcados.');
      $('#ag-desenho').innerHTML = '';
    }
  }
  let j = i + 1;
  while (j < m.acordes.length && (!m.acordes[j].acorde || m.acordes[j].acorde === nome)) j++;
  const prox = j < m.acordes.length ? m.acordes[j] : null;
  const pi = buscar(m.pulT, t + 0.005);
  const p = pi >= 0 ? m.pul[pi] : null;
  if (mudou('px', prox ? prox.acorde + prox.t0 : '-')) {
    if (prox) {
      $('#px-nome').textContent = prox.acorde; $('#px-nome').style.color = corAcorde(prox.acorde);
      $('#px-grau').textContent = prox.grau ? 'grau ' + prox.grau : '';
      $('#px-desenho').innerHTML = `<img alt="" src="/api/acorde.svg?nome=${encodeURIComponent(prox.acorde)}">`;
    } else {
      $('#px-nome').textContent = '—'; $('#px-nome').style.color = '#6d7583'; $('#px-grau').textContent = ''; $('#px-desenho').innerHTML = '';
    }
  }
  let quandoTx = '';
  if (prox) {
    const n = buscar(m.pulT, prox.t0 - 0.02) - pi + 1;
    if (n <= 1) quandoTx = 'no <b>próximo pulso</b>';
    else quandoTx = `em <b>${n}</b> pulsos` + (n > 8 ? ` (compasso ${prox.compasso})` : '');
  }
  if (mudou('quando', quandoTx)) $('#px-quando').innerHTML = quandoTx;
  // contador de pulsos e tercinas
  const sub = m.sub;
  const chave = p ? `${p.c}|${p.k}|${Math.min(sub - 1, Math.floor((t - p.t) / p.d * sub))}` : 'x';
  if (mudou('pulso', chave)) {
    const grupos = $$('#contador .grupo');
    const npul = p && p.c > 0 ? p.n : (m.pulsos_max || 4);
    const terc = p ? Math.max(0, Math.min(sub - 1, Math.floor((t - p.t) / p.d * sub))) : -1;
    grupos.forEach((g, gi) => {
      g.style.display = gi < npul ? '' : 'none';
      g.classList.toggle('atual', !!p && p.c > 0 && gi === p.k);
      $$('i', g).forEach((el, ii) => {
        const on = !!p && p.c > 0 && gi === p.k && ii <= terc;
        el.classList.toggle('on', on);
        el.classList.toggle('passou', !!p && p.c > 0 && gi < p.k);
      });
    });
  }
  // onde
  const c = compassoEm(t);
  const sec = c > 0 ? secaoEm(t) : null;
  if (mudou('onde', c + (sec ? sec.nome : ''))) {
    if (!m.temCompassos) {
      $('#onde-sec').textContent = m.titulo || ''; $('#onde-comp').textContent = '';
    } else {
      $('#onde-sec').textContent = c === 0 ? 'Contagem' : (sec ? sec.nome : '');
      $('#onde-comp').textContent = c === 0 ? 'antes do compasso 1' : `Compasso ${c} de ${m.nComp}`;
    }
  }
  const tt = fmt(t);
  if (mudou('tempo', tt)) $('#tempo-atual').textContent = tt;
}

// ---------------------------------------------------------------- linha do tempo
const cv = () => $('#cv');
function faixas() {
  const k = S.H / 156;
  return { sec: [4 * k, 24 * k], ac: [28 * k, 50 * k], onda: [55 * k, 122 * k], tent: [127 * k, 151 * k] };
}
const PAD = 10;
const xDeT = t => PAD + (t / S.mus.duracao) * (S.W - 2 * PAD);
const tDeX = x => Math.max(0, Math.min(S.mus.duracao, (x - PAD) / (S.W - 2 * PAD) * S.mus.duracao));

function redimensionar() {
  const c = cv();
  const r = c.getBoundingClientRect();
  S.dpr = window.devicePixelRatio || 1;
  S.W = r.width; S.H = r.height;
  c.width = Math.round(r.width * S.dpr); c.height = Math.round(r.height * S.dpr);
  if (S.mus) desenharFundo();
}
function ondas(ctx, pk, janela, t0, t1, y0, y1, cor) {
  const x0 = Math.max(0, Math.floor(xDeT(t0))), x1 = Math.min(S.W, Math.ceil(xDeT(t1)));
  const meio = (y0 + y1) / 2, amp = (y1 - y0) / 2;
  ctx.fillStyle = cor;
  for (let x = x0; x < x1; x++) {
    const ta = tDeX(x), tb = tDeX(x + 1);
    const a = Math.floor((ta - t0) / janela), b = Math.max(a + 1, Math.floor((tb - t0) / janela));
    let v = 0;
    for (let i = Math.max(0, a); i < Math.min(pk.length, b); i++) if (pk[i] > v) v = pk[i];
    const h = Math.max(0.5, (v / 255) * amp);
    ctx.fillRect(x, meio - h, 1, h * 2);
  }
}
function desenharFundo() {
  const m = S.mus; if (!m || !S.W) return;
  const mk = () => { const c = document.createElement('canvas'); c.width = Math.round(S.W * S.dpr); c.height = Math.round(S.H * S.dpr); const x = c.getContext('2d'); x.scale(S.dpr, S.dpr); return [c, x]; };
  const [f, ctx] = mk();
  const [cl, ctx2] = mk();
  const F = faixas();
  ctx.font = '600 12px "Segoe UI", sans-serif';
  ctx.textBaseline = 'middle';
  // partes da música
  m.sec.forEach((s, i) => {
    const xa = xDeT(s.t0), xb = xDeT(s.t1);
    ctx.fillStyle = i % 2 ? '#1f242d' : '#262c37';
    ctx.fillRect(xa, F.sec[0], xb - xa - 1, F.sec[1] - F.sec[0]);
    ctx.save(); ctx.beginPath(); ctx.rect(xa, F.sec[0], xb - xa - 4, F.sec[1] - F.sec[0]); ctx.clip();
    ctx.fillStyle = '#cfd4dc'; ctx.fillText(s.nome, xa + 5, (F.sec[0] + F.sec[1]) / 2 + 1);
    ctx.restore();
  });
  // linhas dos compassos
  m.compassos.forEach((t, i) => {
    const x = Math.round(xDeT(t)) + 0.5;
    const forte = m.sec.some(s => Math.abs(s.t0 - t) < 0.01);
    ctx.fillStyle = forte ? '#ffffff30' : '#ffffff0e';
    ctx.fillRect(x, F.ac[0], 1, F.onda[1] - F.ac[0]);
  });
  // acordes
  ctx.font = '700 11.5px "Segoe UI", sans-serif';
  for (const a of m.acordes) {
    if (!a.acorde) continue;
    const xa = xDeT(a.t0) + 0.5, xb = xDeT(a.t1) - 0.5;
    if (xb - xa < 1) continue;
    ctx.fillStyle = corAcorde(a.acorde);
    ctx.beginPath(); ctx.roundRect(xa, F.ac[0], xb - xa, F.ac[1] - F.ac[0], 3); ctx.fill();
    const w = ctx.measureText(a.acorde).width;
    if (xb - xa > w + 6) { ctx.fillStyle = a.acorde[0] === 'D' ? '#fff' : '#14161a'; ctx.fillText(a.acorde, (xa + xb - w) / 2, (F.ac[0] + F.ac[1]) / 2 + 1); }
  }
  // onda da música
  ondas(ctx, m.mix, m.picos.janela_s, 0, m.duracao, F.onda[0], F.onda[1], '#4d5461');
  ondas(ctx2, m.mix, m.picos.janela_s, 0, m.duracao, F.onda[0], F.onda[1], '#d3d8df');
  // tentativas em uso
  const usadas = (S.est?.partes || []).filter(p => p.fonte !== 'original' && p.tentativa);
  const lista = usadas.map(p => [p, S.tent.find(x => x.id === p.tentativa)]).filter(x => x[1] && x[1].picos);
  if (lista.length) {
    const alt = (F.tent[1] - F.tent[0]) / lista.length;
    lista.forEach(([p, tt], k) => {
      const pk = b64(tt.picos), ini = tt.inicio_s + (tt.deslocamento_ms || 0) / 1000;
      const y0 = F.tent[0] + k * alt;
      ctx.fillStyle = '#ffffff08'; ctx.fillRect(xDeT(ini), y0, xDeT(ini + tt.duracao_s) - xDeT(ini), alt - 1);
      ondas(ctx, pk, 0.02, ini, ini + tt.duracao_s, y0, y0 + alt - 1, p.cor || '#ffc43c');
      ondas(ctx2, pk, 0.02, ini, ini + tt.duracao_s, y0, y0 + alt - 1, p.cor || '#ffc43c');
    });
  } else {
    ctx.fillStyle = '#6d7583'; ctx.font = '12px "Segoe UI", sans-serif';
    ctx.fillText('As tentativas que estiverem tocando no lugar da original aparecem aqui.', PAD + 4, (F.tent[0] + F.tent[1]) / 2);
  }
  S.fundo = f; S.claro = cl;
}
function desenharLinha(t) {
  const c = cv(); const ctx = c.getContext('2d');
  if (!S.fundo) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.drawImage(S.fundo, 0, 0);
  const xp = xDeT(Math.min(t, S.mus.duracao));
  ctx.save(); ctx.beginPath(); ctx.rect(0, 0, xp * S.dpr, c.height); ctx.clip(); ctx.drawImage(S.claro, 0, 0); ctx.restore();
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  const F = faixas();
  // trecho
  const e = S.est;
  let tr = e && e.trecho ? e.trecho.slice() : null;
  const a = S.arrasto;
  if (a && a.moveu) {
    if (a.borda && tr) { if (a.borda === 'a') tr[0] = Math.min(inicioCompasso(a.t1), tr[1] - 0.5); else tr[1] = Math.max(fimCompasso(a.t1), tr[0] + 0.5); }
    else if (!a.borda) tr = [inicioCompasso(Math.min(a.t0, a.t1)), fimCompasso(Math.max(a.t0, a.t1))];
  }
  if (tr) {
    const xa = xDeT(tr[0]), xb = xDeT(tr[1]);
    const lig = (e && e.trecho_ligado) || (a && a.moveu);
    ctx.fillStyle = lig ? '#ffc43c22' : '#ffffff10';
    ctx.fillRect(xa, 0, xb - xa, S.H);
    ctx.fillStyle = lig ? '#ffc43c' : '#8a919c';
    ctx.fillRect(xa - 1, 0, 2, S.H); ctx.fillRect(xb - 1, 0, 2, S.H);
    ctx.fillRect(xa - 5, 0, 10, 6); ctx.fillRect(xb - 5, 0, 10, 6);
  }
  // cursor de passar o mouse
  if (S.hover && !a) {
    ctx.fillStyle = '#ffffff40'; ctx.fillRect(Math.round(S.hover.x), 0, 1, S.H);
  }
  // cursor da música
  ctx.fillStyle = '#ff5a5f';
  ctx.fillRect(xp - 1, 0, 2, S.H);
  ctx.beginPath(); ctx.moveTo(xp - 6, 0); ctx.lineTo(xp + 6, 0); ctx.lineTo(xp, 7); ctx.fill();
}
function bordaTrecho(x) {
  const tr = S.est && S.est.trecho;
  if (!tr) return null;
  if (Math.abs(x - xDeT(tr[0])) < 7) return 'a';
  if (Math.abs(x - xDeT(tr[1])) < 7) return 'b';
  return null;
}
function ligarLinha() {
  const c = cv();
  c.addEventListener('mousedown', e => {
    if (!S.mus || e.button !== 0) return;
    S.arrasto = { x0: e.offsetX, t0: tDeX(e.offsetX), t1: tDeX(e.offsetX), borda: bordaTrecho(e.offsetX), moveu: false };
  });
  window.addEventListener('mousemove', e => {
    if (!S.mus) return;
    const r = c.getBoundingClientRect(); const x = e.clientX - r.left; const y = e.clientY - r.top;
    if (S.arrasto) {
      if (Math.abs(x - S.arrasto.x0) > 4) S.arrasto.moveu = true;
      S.arrasto.t1 = tDeX(x);
    }
    const dentro = x >= 0 && x <= r.width && y >= 0 && y <= r.height;
    const dica = $('#linha-dica');
    if (dentro) {
      const t = tDeX(x);
      S.hover = { x, t };
      const cmp = compassoEm(t), sec = cmp > 0 ? secaoEm(t) : null;
      const borda = bordaTrecho(x);
      c.style.cursor = borda ? 'ew-resize' : 'pointer';
      dica.hidden = false;
      dica.style.left = Math.max(110, Math.min(r.width - 110, x)) + 'px';
      dica.textContent = !S.mus.temCompassos ? fmt(t) : cmp === 0 ? `Contagem · ${fmt(t)}` : `Compasso ${cmp} · ${sec ? sec.nome : ''} · ${fmt(t)}`;
    } else {
      S.hover = null; dica.hidden = true;
    }
  });
  window.addEventListener('mouseup', () => {
    const a = S.arrasto; S.arrasto = null;
    if (!a || !S.mus) return;
    if (a.borda && a.moveu && S.est.trecho) {
      let [ta, tb] = S.est.trecho;
      if (a.borda === 'a') ta = inicioCompasso(a.t1); else tb = fimCompasso(a.t1);
      if (tb - ta >= 0.5) api('trecho', { a: ta, b: tb, ligado: true });
    } else if (a.moveu) {
      const ta = inicioCompasso(Math.min(a.t0, a.t1)), tb = fimCompasso(Math.max(a.t0, a.t1));
      if (tb - ta >= 0.5) api('trecho', { a: ta, b: tb, ligado: true });
    } else {
      api('ir', { t: inicioCompasso(a.t0) });
    }
  });
  c.addEventListener('dblclick', e => {
    if (!S.mus) return;
    const F = faixas();
    if (e.offsetY <= F.sec[1] + 2) {
      const s = secaoEm(tDeX(e.offsetX));
      if (s) { api('trecho', { a: s.t0, b: s.t1, ligado: true }); aviso(`Repetindo: ${s.nome}. Tecla L desliga.`, 'ok'); }
    }
  });
  c.addEventListener('mouseleave', () => { S.hover = null; $('#linha-dica').hidden = true; });
}

// ---------------------------------------------------------------- banda
function opcoesFonte(p) {
  const minhas = S.tent.filter(x => x.parte === p.id);
  if (!minhas.length) return null;
  const o = [['original', 'Original']];
  for (const x of minhas) o.push(['t:' + x.id, 'Minha: ' + esc(x.nome) + (x.favorita ? ' ★' : '')]);
  for (const x of minhas) o.push(['a:' + x.id, 'Original + ' + esc(x.nome)]);
  return o;
}
function desenharPartes(refazer) {
  const box = $('#partes');
  const partes = S.est ? S.est.partes : [];
  const chave = JSON.stringify(partes.map(p => p.id)) + JSON.stringify(S.tent.map(x => [x.id, x.nome, x.favorita, x.parte]));
  if (refazer || mudou('partes_chave', chave)) {
    S.ult.partes_chave = chave;
    box.innerHTML = '';
    for (const p of partes) {
      const row = document.createElement('div');
      row.className = 'parte'; row.dataset.id = p.id;
      const ops = opcoesFonte(p);
      row.innerHTML = `
        <span class="cor" style="--c:${p.cor}"></span>
        <span class="nome">${esc(p.nome)}<canvas class="mini" width="120" height="12"></canvas></span>
        <button class="liga"><span class="bola"></span><span class="tx"></span></button>
        <input type="range" class="vol" min="-30" max="6" step="0.5" title="Volume (dois cliques: 0 dB)">
        <span class="vol-tx"></span>
        ${ops ? `<select class="fonte" title="Tocar a parte original ou uma tentativa sua">${ops.map(([v, t]) => `<option value="${esc(v)}">${t}</option>`).join('')}</select>` : '<span></span>'}`;
      box.append(row);
      const liga = $('.liga', row), vol = $('.vol', row), fonte = $('.fonte', row);
      liga.onclick = () => { const pp = S.est.partes.find(x => x.id === p.id); api('parte', { id: p.id, ligada: !pp.ligada }); };
      let espera = null;
      vol.oninput = () => {
        $('.vol-tx', row).textContent = fmtDb(+vol.value);
        clearTimeout(espera); espera = setTimeout(() => api('parte', { id: p.id, volume_db: +vol.value }), 60);
      };
      vol.ondblclick = () => { vol.value = 0; api('parte', { id: p.id, volume_db: 0 }); };
      if (fonte) fonte.onchange = () => {
        const v = fonte.value;
        if (v === 'original') api('parte', { id: p.id, fonte: 'original' });
        else api('parte', { id: p.id, fonte: v.startsWith('a:') ? 'ambos' : 'tentativa', tentativa: v.slice(2) });
        fonte.blur();
      };
      const mini = $('.mini', row);
      if (S.mus && S.mus.partesPk[p.id]) {
        const ctx = mini.getContext('2d'); const pk = S.mus.partesPk[p.id];
        ctx.fillStyle = p.cor;
        for (let x = 0; x < 120; x++) {
          const a = Math.floor(x / 120 * pk.length), b = Math.floor((x + 1) / 120 * pk.length);
          let v = 0; for (let i = a; i < b; i++) if (pk[i] > v) v = pk[i];
          const h = Math.max(1, v / 255 * 12); ctx.globalAlpha = 0.85; ctx.fillRect(x, 12 - h, 1, h);
        }
      }
      mini.style.cssText = 'display:block;margin-top:3px;opacity:.75';
      mini.title = 'Onde esta parte toca na música';
    }
  }
  for (const p of partes) {
    const row = box.querySelector(`.parte[data-id="${p.id}"]`); if (!row) continue;
    row.classList.toggle('desligada', !p.ligada);
    const liga = $('.liga', row);
    liga.classList.toggle('off', !p.ligada);
    $('.tx', liga).textContent = p.ligada ? 'tocando' : 'desligada';
    liga.title = p.ligada ? 'Clique para desligar esta parte' : 'Clique para ligar esta parte';
    const vol = $('.vol', row);
    if (document.activeElement !== vol) { vol.value = p.volume_db; $('.vol-tx', row).textContent = fmtDb(p.volume_db); }
    const fonte = $('.fonte', row);
    if (fonte && document.activeElement !== fonte) {
      fonte.value = p.fonte === 'original' ? 'original' : (p.fonte === 'ambos' ? 'a:' : 't:') + p.tentativa;
      fonte.classList.toggle('sua', p.fonte !== 'original');
    }
  }
}
const fmtDb = v => (v > 0 ? '+' : '') + (Math.round(v * 2) / 2).toFixed(v % 1 ? 1 : 0) + ' dB';

// ---------------------------------------------------------------- tentativas
function desenharTentativas() {
  const box = $('#lista');
  box.innerHTML = '';
  if (!S.tent.length) {
    box.innerHTML = '<div class="vazio">Nenhuma tentativa ainda.<br>Escolha em <b>Você vai tocar</b> a parte e aperte <b>● Gravar</b> (tecla R). ' +
      'Cada gravação aparece aqui, já alinhada com a banda.</div>';
    return;
  }
  for (const f of S.tent) {
    const el = document.createElement('div');
    el.className = 'tent' + (f.em_uso ? ' em-uso' : '');
    const [nv, cls] = nivelTexto(f.pico_db);
    const uso = f.em_uso === 'tentativa' ? ' · <span class="uso">tocando no lugar da original</span>' : f.em_uso === 'ambos' ? ' · <span class="uso">tocando junto com a original</span>' : '';
    el.innerHTML = `
      <button class="t-ouvir" title="Ouvir com a banda, no lugar da original">▶</button>
      <button class="t-fav ${f.favorita ? 'sim' : ''}" title="${f.favorita ? 'Favorita' : 'Marcar como favorita'}">${f.favorita ? '★' : '☆'}</button>
      <div class="t-info">
        <div class="t-nome" title="Dois cliques para renomear"></div>
        <div class="t-meta">${esc(f.nome_parte || '')} · ${quando(f.criada)} · ${fmt(f.duracao_s)} · <span class="${cls}">${nv}</span>${uso}</div>
      </div>
      <div class="t-acoes"><button class="t-mais" title="Mais opções">⋯</button>
        <div class="menu" hidden>
          <div class="ajuste-tempo">Tempo desta tentativa: se ela soa adiantada ou atrasada em relação à banda.
            <div class="linha-aj"><button data-d="-10">−10</button><button data-d="-2">−2</button><span class="val"></span><button data-d="2">+2</button><button data-d="10">+10 ms</button></div></div>
          <button class="t-mp3">Exportar MP3</button>
          <button class="t-ren">Renomear</button>
          <button class="t-apagar perigo">Apagar</button>
        </div></div>`;
    $('.t-nome', el).textContent = f.nome;
    const val = $('.val', el);
    const mostraDesl = v => { val.textContent = (v > 0 ? '+' : '') + Math.round(v) + ' ms'; };
    mostraDesl(f.deslocamento_ms || 0);
    $('.t-ouvir', el).onclick = () => api('tentativa_ouvir', { id: f.id });
    $('.t-fav', el).onclick = () => api('tentativa', { id: f.id, favorita: !f.favorita });
    const menu = $('.menu', el);
    $('.t-mais', el).onclick = ev => { ev.stopPropagation(); abrirMenu(menu); };
    $$('.linha-aj button', el).forEach(b => b.onclick = ev => {
      ev.stopPropagation();
      f.deslocamento_ms = (f.deslocamento_ms || 0) + Number(b.dataset.d);
      mostraDesl(f.deslocamento_ms);
      api('tentativa', { id: f.id, deslocamento_ms: f.deslocamento_ms });
    });
    $('.t-mp3', el).onclick = () => { fecharMenus(); api('exportar', { tipo: 'tentativa', id: f.id }); };
    const renomear = () => {
      fecharMenus();
      const nm = $('.t-nome', el);
      nm.innerHTML = '<input maxlength="60">';
      const inp = $('input', nm); inp.value = f.nome; inp.focus(); inp.select();
      let feito = false;
      const ok = salvar => { if (feito) return; feito = true; if (salvar && inp.value.trim()) api('tentativa', { id: f.id, nome: inp.value.trim() }); else nm.textContent = f.nome; };
      inp.onkeydown = ev => { if (ev.key === 'Enter') ok(true); if (ev.key === 'Escape') ok(false); ev.stopPropagation(); };
      inp.onblur = () => ok(true);
    };
    $('.t-ren', el).onclick = renomear;
    $('.t-nome', el).ondblclick = renomear;
    $('.t-apagar', el).onclick = () => { fecharMenus(); api('tentativa_apagar', { id: f.id }); };
    box.append(el);
  }
}
function abrirMenu(menu) {
  const aberto = !menu.hidden;
  fecharMenus();
  if (!aberto) { menu.hidden = false; S.menuAberto = menu; }
}
function fecharMenus() { $$('.menu').forEach(m => m.hidden = true); S.menuAberto = null; }

// ---------------------------------------------------------------- acertar o tempo
function abrirTempo() {
  const d = $('#dlg-tempo');
  if (!d.open) d.showModal();
  atualizarDialogoTempo(true);
}
function atualizarDialogoTempo(inicio) {
  const e = S.est; const st = $('#tempo-status');
  const btn = $('#tempo-comecar');
  btn.disabled = !e.aparelhos.entrada_ok || e.calibrando || e.gravando;
  if (!e.aparelhos.entrada_ok) { st.innerHTML = '<span class="ruim">Nenhuma entrada de áudio foi encontrada. Ligue a interface (ex.: a GT-100) e escolha a entrada no menu "Entrada", no topo.</span>'; return; }
  if (e.calibrando) return;  // o tique mostra o clique atual
  if (e.ocupado) { st.textContent = e.ocupado; return; }
  if (inicio) {
    st.innerHTML = e.calibrado ? `Ajuste atual: <b>${Math.round(e.ajuste_ms)} ms</b>. Pode medir de novo quando quiser (por exemplo, depois de trocar a saída de som).`
      : 'Ainda não foi feito. Leva uns 10 segundos.';
  }
}
function mostrarCalibracao(r) {
  const st = $('#tempo-status');
  if (!$('#dlg-tempo').open) return;
  if (!r || !r.ok) {
    st.innerHTML = '<span class="ruim">Não deu para medir.</span> Toque as cordas abafadas com firmeza, junto com cada clique, e clique em Começar de novo.';
    return;
  }
  const lado = r.ms > 0 ? 'atrasadas' : 'adiantadas';
  let txt = `<span class="grande">${Math.abs(Math.round(r.ms))} ms</span><br><span class="bom">Pronto!</span> As gravações chegavam ${Math.abs(Math.round(r.ms))} ms ${lado}; daqui em diante, entram no tempo. ` +
    `(${r.acertos} de ${r.cliques} toques medidos.)`;
  if (r.espalhamento_ms > 20) txt += `<br>Seus toques variaram bastante (±${Math.round(r.espalhamento_ms)} ms). Se quiser, repita com toques mais firmes.`;
  st.innerHTML = txt;
}

// ---------------------------------------------------------------- passo a passo e ajuda
const TOUR = [
  { alvo: '#modos', titulo: 'Escolha o que você vai tocar', texto: 'Clique na parte que você vai tocar (por exemplo, "Guitarra base"): o app desliga essa parte da banda, e é nela que a gravação vai entrar. "Só ouvir" liga tudo.' },
  { alvo: '#b-tocar', titulo: 'Toque junto', texto: 'Aperte Tocar (ou Espaço). Antes de a música andar, a contagem dá 4 pulsos: entre no UM seguinte. Um clique na linha do tempo leva a outro compasso; arrastar marca um trecho para repetir.' },
  { alvo: '#cartao-atual', titulo: 'Siga o painel AGORA', texto: 'Ele mostra o acorde que está soando, o grau e o desenho no braço. As bolinhas são os pulsos do compasso, cada um dividido em partes iguais: conte junto, do jeito que está escrito embaixo delas.' },
  { alvo: '#b-gravar', titulo: 'Grave e compare', texto: 'Aperte Gravar (ou R) e toque; aperte de novo para parar. A tentativa aparece em "Suas tentativas", já alinhada: ▶ toca a sua no lugar da original.' },
  { alvo: '#btn-tempo', titulo: 'Antes da primeira gravação', texto: 'Confira no topo a Entrada (de onde vem a sua guitarra) e faça uma vez o "Acertar o tempo" (uns 10 segundos). Ele mede o atraso do computador, e as gravações passam a entrar certinho no tempo.' },
];
let tourI = -1;
function tour(i) {
  $$('.destaque').forEach(x => x.classList.remove('destaque'));
  const d = $('#dlg-tour');
  if (i >= TOUR.length || i < 0) {
    if (d.open) d.close();
    localStorage.setItem('ensaio.tour', '1');
    if (S.est && !S.est.tour_visto) api('opcoes', { tour_visto: true });
    tourI = -1; return;
  }
  tourI = i;
  const p = TOUR[i];
  $('#tour-passo').textContent = `Passo ${i + 1} de ${TOUR.length}`;
  $('#tour-titulo').textContent = p.titulo;
  $('#tour-texto').textContent = p.texto;
  $('#tour-prox').textContent = i === TOUR.length - 1 ? 'Acertar o tempo agora' : 'Próximo';
  $('#tour-pular').textContent = i === TOUR.length - 1 ? 'Depois' : 'Pular';
  const alvo = $(p.alvo); if (alvo) alvo.classList.add('destaque');
  if (!d.open) d.show();
  d.style.position = 'fixed'; d.style.margin = '0'; d.style.left = '50%'; d.style.top = '38%'; d.style.transform = 'translate(-50%, -50%)'; d.style.zIndex = 80;
}

// ---------------------------------------------------------------- quadro a quadro
function quadro() {
  try {
    const t = posAgora();
    if (S.mus) { atualizarAgora(t); desenharLinha(t); }
    if (S.est && S.est.gravando && S.gravInicio) {
      const tx = 'Parar · ' + fmt((performance.now() - S.gravInicio) / 1000);
      if (mudou('grav', tx)) $('#b-gravar-tx').textContent = tx;
    }
  } catch (e) { console.error(e); }
  requestAnimationFrame(quadro);
}

// ---------------------------------------------------------------- botões e teclas
function alternarTrecho() {
  if (!S.est || !S.est.trecho) {
    aviso('Primeiro marque o trecho: arraste na linha do tempo, ou dê dois cliques numa parte da música (faixa de cima).');
    return;
  }
  api('trecho', { ligado: !S.est.trecho_ligado });
}
function ligarBotoes() {
  $('#b-inicio').onclick = () => api('inicio');
  $('#b-ant').onclick = () => api('compasso', { delta: -1 });
  $('#b-prox').onclick = () => api('compasso', { delta: 1 });
  $('#b-tocar').onclick = () => api('tocar_pausar');
  $('#b-gravar').onclick = () => api('gravar_parar');
  $('#b-trecho').onclick = alternarTrecho;
  $('#b-contagem').onclick = () => api('opcoes', { contagem: !S.est.contagem });
  $('#b-metro').onclick = () => api('opcoes', { metronomo: !S.est.metronomo });
  const vg = $('#vol-geral'); let espera = null;
  vg.oninput = () => { clearTimeout(espera); espera = setTimeout(() => api('opcoes', { volume_geral_db: +vg.value }), 60); };
  vg.ondblclick = () => { vg.value = 0; api('opcoes', { volume_geral_db: 0 }); };
  $('#sel-musica').onchange = e => {
    const v = e.target.value;
    if (v === '__adicionar__') { e.target.value = S.est && S.est.musica ? S.est.musica.id : ''; api('adicionar_biblioteca'); }
    else if (v) api('musica', { id: v });
    e.target.blur();
  };
  $('#chip-entrada').onclick = ev => { ev.stopPropagation(); abrirMenu($('#menu-entrada')); };
  let ultimaVolta = 0;
  window.addEventListener('focus', () => {  // voltou de outra janela: a lista de músicas pode ter mudado
    if (S.est && !S.est.gravando && performance.now() - ultimaVolta > 3000) { ultimaVolta = performance.now(); api('musicas'); }
  });
  $('#chip-saida').onclick = ev => { ev.stopPropagation(); abrirMenu($('#menu-saida')); };
  $$('#menu-saida button').forEach(b => b.onclick = () => { fecharMenus(); api('saida', { modo: b.dataset.saida }); });
  $('#btn-tempo').onclick = abrirTempo;
  $('#tempo-comecar').onclick = () => { $('#tempo-status').innerHTML = '<span class="grande">Prepare-se…</span>'; api('acertar_tempo'); };
  $('#tempo-fechar').onclick = () => $('#dlg-tempo').close();
  $('#btn-ajuda').onclick = () => $('#dlg-ajuda').showModal();
  $('#ajuda-fechar').onclick = () => $('#dlg-ajuda').close();
  $('#ajuda-tour').onclick = () => { $('#dlg-ajuda').close(); tour(0); };
  $('#tour-prox').onclick = () => { const ult = tourI === TOUR.length - 1; tour(tourI + 1); if (ult) abrirTempo(); };
  $('#tour-pular').onclick = () => tour(TOUR.length);
  $('#btn-exp-mix').onclick = () => api('exportar', { tipo: 'mistura' });
  $('#btn-pasta').onclick = () => api('abrir_pasta', { qual: 'tentativas' });
  document.addEventListener('click', e => {
    if (S.menuAberto && !e.target.closest('.menu')) fecharMenus();
    const b = e.target.closest('button'); if (b) b.blur();
  });
  document.addEventListener('keyup', e => { if (e.key === ' ' && e.target.tagName === 'BUTTON') e.preventDefault(); });
  document.addEventListener('keydown', e => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.target.closest('input:not([type=range]), select, textarea')) return;
    if ($('#dlg-tempo').open || $('#dlg-ajuda').open) return;
    const k = e.key;
    if (/^[1-9]$/.test(k) && S.modos && S.modos[+k - 1]) { e.preventDefault(); if (!e.repeat) api('modo', { modo: S.modos[+k - 1] }); return; }
    const acoes = {
      ' ': () => api('tocar_pausar'), r: () => api('gravar_parar'), R: () => api('gravar_parar'),
      Home: () => api('inicio'), ArrowLeft: () => api('compasso', { delta: -1 }), ArrowRight: () => api('compasso', { delta: 1 }),
      l: alternarTrecho, L: alternarTrecho,
      c: () => api('opcoes', { contagem: !S.est.contagem }), C: () => api('opcoes', { contagem: !S.est.contagem }),
      m: () => api('opcoes', { metronomo: !S.est.metronomo }), M: () => api('opcoes', { metronomo: !S.est.metronomo }),
      '?': () => $('#dlg-ajuda').showModal(),
    };
    if (acoes[k]) { e.preventDefault(); if (e.repeat) return; acoes[k](); }
  });
  window.addEventListener('resize', () => redimensionar());
}

async function iniciar() {
  montarContador();
  ligarBotoes();
  ligarLinha();
  redimensionar();
  try {
    aplicarEstado(await pegar('/api/estado'));
  } catch (err) { console.warn(err); }
  conectar();
  requestAnimationFrame(quadro);
}
iniciar();
