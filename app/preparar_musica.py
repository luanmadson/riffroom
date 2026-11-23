# -*- coding: utf-8 -*-
"""Prepara uma música para a Sala de Ensaio: grava o ensaio.json com
  - as partes da banda (faixas separadas) e o volume de cada uma, para a soma soar como a gravação;
  - opcional: compassos e pulsos (pelo BPM), a contagem, os acordes com grau e desenho, e as partes da música.
Os campos escritos à mão (título, artista, tônica, partes da música, BPM...) são mantidos ao rodar de novo.

Uso: python -I -X utf8 preparar_musica.py "<pasta da música>" [--titulo T] [--artista A] [--tonica A]
         [--bpm 72 --compasso 12/8 --inicio 3.33] [--acordes acordes.txt] [--secoes "1=Intro;5=Verso"]
         [--alvos base,solo] [--parte id=arquivo.wav ...] [--original mix.wav]

Faixas reconhecidas pelo nome do arquivo (wav, flac, mp3, ogg), como as que o Demucs gera:
  drums/bateria, bass/baixo, guitar/guitarra, guitarra_base, guitarra_solo, piano, vocals/voz, other/outros;
  original/mix (a música inteira, opcional: serve para acertar o volume de cada parte).
"""
import argparse, glob, json, os, re, sys, unicodedata
import numpy as np
import soundfile as sf

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)
import acordes as AC  # noqa: E402

# id, nome na tela, nomes de arquivo aceitos (sem extensão; minúsculas, sem acento, "_" no lugar de espaço e hífen)
PARTES = [
    ('bateria', 'Bateria', ('bateria', 'drums', 'drum')),
    ('baixo', 'Baixo', ('baixo', 'bass')),
    ('base', 'Guitarra base', ('guitarra_base', 'rhythm_guitar', 'rhythm', 'base')),
    ('solo', 'Guitarra solo', ('guitarra_solo', 'lead_guitar', 'lead', 'solo')),
    ('guitarra', 'Guitarra', ('guitarra', 'guitarras', 'guitar', 'guitars')),
    ('piano', 'Piano', ('piano', 'teclado', 'teclados', 'keys', 'keyboard')),
    ('voz', 'Voz', ('voz', 'vozes', 'vocals', 'vocal', 'voice')),
    ('outros', 'Outros instrumentos', ('outros', 'other', 'others')),
]
NOMES_PARTES = {pid: nome for pid, nome, _ in PARTES}
ORIGINAL = ('original_com_contagem', 'original', 'mixture', 'mix', 'completa', 'musica')
PALAVRAS = {w for _, _, ns in PARTES for n in ns for w in n.split('_')}
EXT_AUDIO = ('.wav', '.flac', '.mp3', '.ogg', '.aiff', '.aif')
COMPASSOS = {'12/8': 4, '9/8': 3, '6/8': 2, '4/4': 4, '3/4': 3, '2/4': 2, '5/4': 5, '2/2': 2}


def _norm(nome):
    s = unicodedata.normalize('NFKD', os.path.splitext(nome)[0]).encode('ascii', 'ignore').decode().lower()
    return [t for t in re.split(r'[\s_\-.()\[\]]+', s) if t]


def _bate(tokens, chave):
    """O nome termina com a chave (ex.: 'MinhaMusica_guitarra_base_isolada' -> 'guitarra base')?"""
    while tokens and tokens[-1] in ('isolada', 'isolado', 'stem', 'faixa', 'track'):
        tokens = tokens[:-1]
    k = chave.split('_')
    if len(tokens) < len(k) or tokens[-len(k):] != k:
        return False
    antes = tokens[:-len(k)]
    if any(t in ('sem', 'no', 'without', 'minus', 'karaoke', 'instrumental') for t in antes):
        return False
    return not (antes and antes[-1] in PALAVRAS)   # "baixo_bateria" é a mistura das duas, não a bateria


def achar_partes_por_nome(pasta, arquivos=None):
    """{id da parte: caminho} pelos nomes dos arquivos de áudio da pasta."""
    arquivos = arquivos if arquivos is not None else os.listdir(pasta)
    achados = {}
    for pid, _, chaves in PARTES:
        for chave in sorted(chaves, key=lambda c: -len(c.split('_'))):
            cands = [a for a in arquivos if a.lower().endswith(EXT_AUDIO) and _bate(_norm(a), chave)]
            cands = [a for a in cands if os.path.join(pasta, a) not in achados.values()]
            if cands:
                achados[pid] = os.path.join(pasta, sorted(cands, key=len)[0])
                break
    return achados


def achar_original(pasta, arquivos=None):
    arquivos = arquivos if arquivos is not None else os.listdir(pasta)
    for chave in ORIGINAL:
        cands = [a for a in arquivos if a.lower().endswith(EXT_AUDIO) and _bate(_norm(a), chave)]
        if cands:
            return os.path.join(pasta, sorted(cands, key=len)[0])
    return None


# ------------------------------------------------------------------ dados do projeto de origem (Faixas/ e Dados/)
SUFIXOS_PROJETO = [('bateria', '_bateria.wav'), ('baixo', '_baixo_isolado.wav'), ('base', '_guitarra_base_isolada.wav'),
                   ('solo', '_guitarra_solo_isolada.wav'), ('voz', '_voz_isolada.wav')]


def _projeto(pasta):
    """Pastas no formato do projeto de origem: Faixas/Completas (WAV)/<Prefixo>_original_com_contagem.wav e as isoladas."""
    wav = os.path.join(pasta, 'Faixas', 'Completas (WAV)')
    orig = glob.glob(os.path.join(wav, '*_original_com_contagem.wav'))
    if len(orig) != 1:
        return None
    prefixo = os.path.basename(orig[0])[:-len('_original_com_contagem.wav')]
    partes = {pid: os.path.join(wav, prefixo + fim) for pid, fim in SUFIXOS_PROJETO
              if os.path.exists(os.path.join(wav, prefixo + fim))}
    return {'original': orig[0], 'partes': partes, 'dados': os.path.join(pasta, 'Dados')}


def ler_relatorio(dados):
    """Tempos da contagem e dos toques leves, e o pulso, do relatório da faixa da bateria."""
    out = {}
    for p in sorted(glob.glob(os.path.join(dados, 'relatorio_processamento_*.txt'))):
        t = open(p, encoding='utf-8').read()
        m = re.search(r'contagem \(tempo do arquivo final\): ([\d., ]+)', t)
        if m and 'contagem' not in out:
            out['contagem'] = [float(v) for v in m.group(1).split(',')]
        m = re.search(r'toques leves: \d+ -> ([\d., ]+)', t)
        if m and 'toques' not in out:
            out['toques'] = [float(v) for v in m.group(1).split(',')]
        m = re.search(r'pulso = ([\d.]+) s \(~([\d.]+) BPM\)', t)
        if m and 'bpm' not in out:
            out['pulso_s'], out['bpm'] = float(m.group(1)), float(m.group(2))
    return out


# ------------------------------------------------------------------ volumes
def equilibrio(arqs, original, sr):
    """Volume de cada parte para a soma soar como a gravação (mínimos quadrados contra a original) e escala comum
    para o pico da soma ficar em -1 dBFS. Sem a original, todas em 1 (o Demucs separa sem mudar o volume)."""
    S = []
    for a in arqs:
        x, sr2 = sf.read(a, dtype='float32', always_2d=True)
        if sr2 != sr:
            raise ValueError(f'{os.path.basename(a)}: {sr2} Hz; as faixas precisam ter a mesma taxa ({sr} Hz)')
        S.append(x[:, :2] if x.shape[1] >= 2 else np.repeat(x, 2, axis=1))
    residuo_db, n_orig = None, 0
    if original:
        mix, sr2 = sf.read(original, dtype='float32', always_2d=True)
        if sr2 != sr:
            raise ValueError(f'{os.path.basename(original)}: {sr2} Hz (as partes estão em {sr} Hz)')
        mix = mix[:, :2] if mix.shape[1] >= 2 else np.repeat(mix, 2, axis=1)
        n_orig = len(mix)
        n = min([len(mix)] + [len(x) for x in S])
        if n > 120 * sr:
            a0, b0 = int(30 * sr), min(int(355 * sr), n - int(5 * sr))
        else:
            a0, b0 = int(0.1 * n), int(0.9 * n)
        st = 7
        A = np.stack([np.concatenate([x[a0:b0:st, 0], x[a0:b0:st, 1]]) for x in S], axis=1).astype(np.float64)
        y = np.concatenate([mix[a0:b0:st, 0], mix[a0:b0:st, 1]]).astype(np.float64)
        g, *_ = np.linalg.lstsq(A, y, rcond=None)
        res = y - A @ g
        residuo_db = float(-10 * np.log10(np.mean(y ** 2) / max(np.mean(res ** 2), 1e-20)))
        g = np.maximum(g, 0.05)  # uma parte quase vazia não pode virar ganho negativo
    else:
        g = np.ones(len(S))
    L = max(len(x) for x in S)
    soma = np.zeros((L, 2), np.float32)
    for x, gi in zip(S, g):
        soma[:len(x)] += x * np.float32(gi)
    k = 0.891 / max(float(np.max(np.abs(soma))), 1e-6)
    return [float(gi * k) for gi in g], residuo_db, float(k), max(L, n_orig)


# ------------------------------------------------------------------ compassos, acordes e partes da música
def pulsos_do_compasso(compasso):
    if compasso in COMPASSOS:
        return COMPASSOS[compasso]
    n, d = (int(v) for v in compasso.split('/'))
    return n // 3 if d == 8 and n % 3 == 0 else n


def grade(bpm, compasso, inicio, duracao):
    """Compassos iguais a partir de `inicio` (s), com `bpm` pulsos por minuto."""
    npc = pulsos_do_compasso(compasso)
    L = npc * 60.0 / bpm
    B = [inicio]
    while B[-1] < duracao - 0.05:
        B.append(inicio + len(B) * L)
    return [round(v, 4) for v in B], [npc] * (len(B) - 1)


def ler_cifra(texto):
    """Cifra simples, um compasso por palavra:
         [Intro]
         A5 A5 D5 A5        (cada palavra é um compasso; '|' pode separar, se quiser)
         [Verso]
         A5 % % %           ('%' repete o compasso anterior)
         E5 D5,C5 A5 -      ('D5,C5' divide o compasso; '-' é compasso sem acorde)
       Linhas começando com '#' são comentários. Devolve (compassos, partes): [[acordes]], [{'compasso', 'nome'}]."""
    compassos, secoes = [], []
    for linha in texto.splitlines():
        if linha.strip().startswith('#'):
            continue
        for tok in re.findall(r'\[[^\]]*\]|[^\s|]+', linha):
            if tok.startswith('['):
                secoes.append({'compasso': len(compassos) + 1, 'nome': tok[1:-1].strip()})
            elif tok == '%':
                if not compassos:
                    raise ValueError("'%' no primeiro compasso: não há compasso anterior para repetir")
                compassos.append(list(compassos[-1]))
            else:
                acs = [None if a in ('-', 'N.C.', 'NC') else a for a in tok.split(',')]
                for a in acs:
                    if a is not None and not AC.valido(a):
                        raise ValueError(f'acorde não reconhecido: "{a}"')
                compassos.append(acs)
    return compassos, secoes


def acordes_da_cifra(compassos_cifra, B, npc, tonica):
    """Trechos de acorde (t0, t1, compasso, pulso) a partir da cifra; acordes iguais seguidos viram um trecho só."""
    out = []
    for i, acs in enumerate(compassos_cifra):
        if i + 1 >= len(B):
            break
        t0, t1, n = B[i], B[i + 1], len(acs)
        for j, a in enumerate(acs):
            a0, a1 = t0 + (t1 - t0) * j / n, t0 + (t1 - t0) * (j + 1) / n
            if out and out[-1]['acorde'] == a and abs(out[-1]['t1'] - a0) < 1e-6:
                out[-1]['t1'] = round(a1, 4)
                continue
            item = {'t0': round(a0, 4), 't1': round(a1, 4), 'compasso': i + 1, 'pulso': int(j * npc[i] / n) + 1, 'acorde': a}
            if a:
                item['grau'] = AC.grau(a, tonica)
                item['letra'] = a[0]
            out.append(item)
    return out


def _secoes(texto):
    out = []
    for par in texto.split(';'):
        if par.strip():
            c, nome = par.split('=', 1)
            out.append({'compasso': int(c), 'nome': nome.strip()})
    return out


# ------------------------------------------------------------------ principal
def preparar(pasta, titulo=None, artista=None, album=None, tonica=None, secoes=None, bpm=None, compasso=None,
             inicio=None, acordes=None, alvos=None, partes=None, original=None, avisar=print):
    """Grava o ensaio.json da música e devolve o conteúdo dele."""
    pasta = os.path.abspath(pasta)
    proj = _projeto(pasta)
    destino = os.path.join(pasta, 'Dados', 'ensaio.json') if proj else os.path.join(pasta, 'ensaio.json')
    antigo = json.load(open(destino, encoding='utf-8')) if os.path.exists(destino) else {}

    # faixas
    if partes:
        achadas = {}
        for par in partes:
            pid, arq = par.split('=', 1)
            achadas[pid.strip()] = os.path.join(pasta, arq.strip())
        orig = os.path.join(pasta, original) if original else achar_original(pasta)
    elif proj:
        achadas, orig = proj['partes'], proj['original']
    else:
        achadas, orig = {}, None
        for sub in [pasta] + sorted(d for d in glob.glob(os.path.join(pasta, '*')) if os.path.isdir(d)
                                    and os.path.basename(d) not in ('Gravações', 'Dados')):
            achadas = achar_partes_por_nome(sub)
            if len(achadas) >= 2:
                orig = os.path.join(pasta, original) if original else achar_original(sub)
                break
        if len(achadas) < 2:
            raise ValueError('não achei as faixas separadas. Os arquivos precisam se chamar como as partes: '
                             'drums/bateria, bass/baixo, guitar/guitarra, vocals/voz, other/outros, piano...')
    ordem = [pid for pid, _, _ in PARTES]
    lista = sorted(achadas.items(), key=lambda kv: ordem.index(kv[0]) if kv[0] in ordem else 99)
    for pid, p in lista:
        if not os.path.exists(p):
            raise FileNotFoundError(p)
    sr = sf.info(lista[0][1]).samplerate
    for pid, p in lista:
        i = sf.info(p)
        if i.samplerate != sr or i.channels not in (1, 2):
            raise ValueError(f'{os.path.basename(p)}: {i.samplerate} Hz e {i.channels} canais '
                             f'(as faixas precisam ter a mesma taxa, {sr} Hz, e 1 ou 2 canais)')

    avisar('calculando o volume de cada parte...')
    ganhos, residuo_db, escala, n = equilibrio([p for _, p in lista], orig, sr)
    if residuo_db is not None:
        avisar(f'  o que a separação não pegou: {residuo_db:.1f} dB abaixo da música')
    dur = n / sr

    nomes_antigos = {p['id']: p['nome'] for p in antigo.get('partes', [])}
    out = {
        'versao': 1,
        'titulo': titulo or antigo.get('titulo') or os.path.basename(pasta),
        'artista': artista or antigo.get('artista') or (os.path.basename(os.path.dirname(os.path.dirname(pasta)))
                                                        if proj else os.path.basename(os.path.dirname(pasta))),
        'album': album or antigo.get('album') or (os.path.basename(os.path.dirname(pasta)) if proj else ''),
        'compasso_tipo': compasso or antigo.get('compasso_tipo') or ('12/8' if proj else '4/4'),
        'bpm': bpm or antigo.get('bpm'),
        'taxa': sr,
        'duracao_s': round(dur, 4),
        'original': os.path.relpath(orig, pasta).replace('\\', '/') if orig else None,
        'partes': [{'id': pid, 'nome': nomes_antigos.get(pid) or NOMES_PARTES.get(pid, pid.capitalize()),
                    'arquivo': os.path.relpath(p, pasta).replace('\\', '/'), 'ganho': round(g, 6),
                    'ganho_db': round(20 * np.log10(g), 2)} for (pid, p), g in zip(lista, ganhos)],
        'equilibrio': {'sobra_db': round(residuo_db, 1) if residuo_db is not None else None, 'escala': round(escala, 4),
                       'como': ('mínimos quadrados das partes contra a original; soma com pico em -1 dBFS' if orig
                                else 'partes somadas como vieram da separação; soma com pico em -1 dBFS')},
    }
    ids = [p['id'] for p in out['partes']]
    if alvos:
        out['alvos'] = [a.strip() for a in (alvos.split(',') if isinstance(alvos, str) else alvos) if a.strip() in ids]
    elif antigo.get('alvos'):
        out['alvos'] = [a for a in antigo['alvos'] if a in ids]
    else:
        out['alvos'] = [a for a in ids if a != 'bateria'] or ids

    # compassos e pulsos: pelo BPM informado, pelo mapa do projeto de origem ou como estavam
    rel = ler_relatorio(proj['dados']) if proj else {}
    segs = []
    if proj and os.path.exists(os.path.join(proj['dados'], 'guitarra_base_acordes.json')):
        segs = json.load(open(os.path.join(proj['dados'], 'guitarra_base_acordes.json'), encoding='utf-8'))['segments']
    B, npc, contagem = [], [], list(antigo.get('contagem', []))
    if bpm:
        ini = float(inicio if inicio is not None else antigo.get('compassos', [0.0])[0] if antigo.get('compassos') else 0.0)
        B, npc = grade(float(bpm), out['compasso_tipo'], ini, dur)
        p = 60.0 / float(bpm)
        contagem = [round(ini - k * p, 4) for k in range(4, 0, -1) if ini - k * p >= -1e-6]
    elif proj and os.path.exists(os.path.join(proj['dados'], 'baixo_transcricao.json')):
        B = [float(v) for v in json.load(open(os.path.join(proj['dados'], 'baixo_transcricao.json'), encoding='utf-8'))['bar_starts']]
        prim = [s['t0'] for s in segs if s.get('bar') == 1]
        if prim and min(prim) > B[0] + 0.3:  # o mapa põe o compasso 1 antes da entrada; vale o 1º tempo depois da contagem
            B[0] = float(min(prim))
        med = float(np.median(np.diff(B)))
        B = [round(v, 4) for v in B]
        npc = [2 if (B[i + 1] - B[i]) < 0.7 * med else 4 for i in range(len(B) - 1)]
        contagem = rel.get('contagem', contagem)
        out['bpm'] = out['bpm'] or rel.get('bpm')
    elif antigo.get('compassos'):
        B, npc = antigo['compassos'], antigo.get('pulsos_por_compasso', [])
    if B:
        out['compassos'], out['pulsos_por_compasso'] = B, npc
    out['contagem'] = contagem
    if rel.get('toques'):
        out['toques_leves'] = rel['toques']

    # acordes: da cifra (acordes.txt), do projeto de origem ou como estavam
    cifra = acordes or (os.path.join(pasta, 'acordes.txt') if os.path.exists(os.path.join(pasta, 'acordes.txt')) else None)
    secoes_cifra = []
    if cifra:
        if not B:
            raise ValueError('para usar a cifra, informe o andamento: --bpm (e --inicio, o tempo do compasso 1)')
        cc, secoes_cifra = ler_cifra(open(cifra, encoding='utf-8').read())
        primeiro = next((a for bar in cc for a in bar if a), None)
        out['tonica'] = tonica or antigo.get('tonica') or (AC.separar(primeiro)[0] if primeiro else 'A')
        out['acordes'] = acordes_da_cifra(cc, B, npc, out['tonica'])
    elif segs:
        out['tonica'] = tonica or antigo.get('tonica') or 'A'
        out['acordes'] = []
        for s in segs:
            ch = s.get('chord')
            item = {'t0': round(s['t0'], 4), 't1': round(s['t1'], 4), 'compasso': s.get('bar'), 'pulso': s.get('beat'), 'acorde': ch}
            if ch:
                item['grau'] = AC.grau(ch, out['tonica'])
                item['letra'] = ch[0]
            out['acordes'].append(item)
    else:
        out['tonica'] = tonica or antigo.get('tonica')
        out['acordes'] = antigo.get('acordes', [])
    out['desenhos'] = {a['acorde']: AC.desenho(a['acorde']) for a in out['acordes'] if a.get('acorde')}

    # partes da música
    if secoes:
        out['secoes'] = _secoes(secoes) if isinstance(secoes, str) else secoes
    elif secoes_cifra:
        out['secoes'] = secoes_cifra
    else:
        out['secoes'] = antigo.get('secoes', [])

    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + '.tmp'
    json.dump(out, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, destino)
    avisar(f'gravado: {destino}')
    for p in out['partes']:
        avisar(f"  {p['nome']:20s} volume {p['ganho']:.4f} ({p['ganho_db']:+.1f} dB)")
    avisar(f"  {max(0, len(out.get('compassos', [])) - 1)} compassos, {len(out['acordes'])} trechos de acorde, "
           f"{len(out['secoes'])} partes da música, duração {dur:.2f} s")
    return out


def main():
    ap = argparse.ArgumentParser(description='Prepara uma música (faixas separadas) para a Sala de Ensaio.')
    ap.add_argument('pasta')
    ap.add_argument('--titulo'); ap.add_argument('--artista'); ap.add_argument('--album')
    ap.add_argument('--tonica', help='nota da tônica, para os graus (ex.: A). Sem ela, vale a do 1º acorde.')
    ap.add_argument('--bpm', type=float, help='pulsos por minuto (o pulso em que se bate o pé)')
    ap.add_argument('--compasso', help='4/4 (padrão), 3/4, 6/8, 12/8...')
    ap.add_argument('--inicio', type=float, help='tempo (s) em que começa o compasso 1')
    ap.add_argument('--acordes', help='arquivo com a cifra (padrão: acordes.txt na pasta da música)')
    ap.add_argument('--secoes', help='"compasso=nome;compasso=nome", ex.: "1=Intro;7=Riff"')
    ap.add_argument('--alvos', help='partes que você toca, para os botões "Você vai tocar" (ex.: base,solo)')
    ap.add_argument('--parte', action='append', help='id=arquivo, para faixas com outros nomes (pode repetir)')
    ap.add_argument('--original', help='a música inteira (para acertar o volume das partes)')
    a = ap.parse_args()
    try:
        preparar(a.pasta, a.titulo, a.artista, a.album, a.tonica, a.secoes, a.bpm, a.compasso, a.inicio, a.acordes,
                 a.alvos, a.parte, a.original)
    except (ValueError, FileNotFoundError) as e:
        print(f'erro: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
