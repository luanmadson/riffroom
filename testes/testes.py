# -*- coding: utf-8 -*-
"""Testes da Sala de Ensaio que não precisam de aparelho de som (rodam também no GitHub Actions).
Uso: python -X utf8 testes/testes.py"""
import json, os, sys, tempfile, time, types
import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, 'app'))
sys.path.insert(0, os.path.join(RAIZ, 'demo'))
import soundfile as sf          # noqa: E402
import acordes as AC            # noqa: E402
import motor as M               # noqa: E402
import musica as MU             # noqa: E402
import preparar_musica as PM    # noqa: E402
import tentativas as T          # noqa: E402

SR = M.SR
falhas = []
print('Testes da Sala de Ensaio')


def teste(nome):
    def deco(f):
        t0 = time.time()
        try:
            f()
            print(f'  ok     {nome} ({time.time() - t0:.1f} s)')
        except AssertionError as e:
            falhas.append(nome)
            print(f'  FALHOU {nome}: {e}')
        except Exception as e:
            falhas.append(nome)
            print(f'  ERRO   {nome}: {type(e).__name__}: {e}')
        return f
    return deco


def musica_falsa(n=SR * 20, partes=None):
    mus = MU.Musica.__new__(MU.Musica)
    mus.sr, mus.n, mus.id = SR, n, 'teste'
    mus.m = {'compassos': [4.0 + 3.6 * i for i in range(5)], 'contagem': [0.4, 1.3, 2.2, 3.1], 'pulsos_por_compasso': [4] * 4}
    mus.partes = partes or []
    t, forte, comp = [], [], []
    for i, v in enumerate(mus.m['contagem']):
        t.append(v); forte.append(i == 0); comp.append(0)
    B = mus.m['compassos']
    for i in range(len(B) - 1):
        for k in range(4):
            t.append(B[i] + k * 0.9); forte.append(k == 0); comp.append(i + 1)
    mus.pulsos, mus.pulso_forte, mus.pulso_compasso = np.array(t), np.array(forte), np.array(comp)
    return mus


def rodar(m, blocos, frames=M.BLOCO_SAIDA):
    out = []
    for j in range(blocos):
        buf = np.zeros((frames, 2), np.float32)
        tinfo = types.SimpleNamespace(outputBufferDacTime=j * frames / SR, currentTime=j * frames / SR)
        m._cb_saida(buf, frames, tinfo, None)
        out.append(buf.copy())
    return np.concatenate(out)


# ------------------------------------------------------------------ motor
@teste('a mistura com os volumes bate com a soma das partes')
def _():
    rng = np.random.default_rng(1)
    n = SR * 6
    d1 = (rng.standard_normal((n, 2)) * 3000).astype(np.int16)
    d2 = (rng.standard_normal((n, 2)) * 3000).astype(np.int16)
    mus = musica_falsa(n, [{'id': 'base', 'nome': 'Base', 'ganho': 0.7, 'dados': d1}, {'id': 'voz', 'nome': 'Voz', 'ganho': 0.5, 'dados': d2}])
    m = M.Motor(); m.carregar(mus); m.contagem = False
    m.parte('voz').volume_db = -6.0
    m.tocar(de=1.0)
    y = rodar(m, 20)
    ini = SR
    esperado = (d1[ini:ini + len(y)].astype(np.float32) * 0.7 + d2[ini:ini + len(y)].astype(np.float32) * 0.5 * 10 ** (-6 / 20)) / 32768
    k = M.BLOCO_SAIDA  # o 1º bloco tem a rampa de entrada e a dos ganhos
    err = np.abs(y[k:] - esperado[k:]).max()
    assert err < 1e-5, f'erro máximo {err}'


@teste('desligar uma parte some com ela, sem estalo')
def _():
    n = SR * 4
    d = np.full((n, 2), 10000, np.int16)
    mus = musica_falsa(n, [{'id': 'base', 'nome': 'Base', 'ganho': 1.0, 'dados': d}])
    m = M.Motor(); m.carregar(mus); m.contagem = False
    m.tocar(de=0.5)
    a = rodar(m, 3)
    m.parte('base').ligada = False
    b = rodar(m, 3)
    y = np.concatenate([a, b])[:, 0]
    assert abs(b[-1, 0]) < 1e-6, 'a parte continuou soando'
    salto = np.abs(np.diff(y[M.BLOCO_SAIDA:])).max()
    assert salto < 0.002, f'degrau de {salto:.4f} (estalo)'


@teste('a volta do trecho repetido não estala e volta ao começo do trecho')
def _():
    n = SR * 10
    d = np.full((n, 2), 12000, np.int16)
    mus = musica_falsa(n, [{'id': 'base', 'nome': 'Base', 'ganho': 1.0, 'dados': d}])
    m = M.Motor(); m.carregar(mus); m.contagem = False
    m.definir_trecho(2.0, 3.0)
    m.tocar(de=2.0)
    y = rodar(m, 60)[:, 0]
    salto = np.abs(np.diff(y[M.BLOCO_SAIDA:])).max()
    assert salto < 0.004, f'degrau de {salto:.4f} na emenda'
    assert m.tocando and 2 * SR <= m.pos <= 3 * SR, f'posição {m.pos / SR:.2f} s fora do trecho'


@teste('a contagem antes de um compasso cai nos 4 pulsos anteriores')
def _():
    mus = musica_falsa()
    m = M.Motor(); m.carregar(mus)
    m.tocar(de=mus.m['compassos'][2])
    _, ini, cq, cs, _, ate = m.cmds[-1]
    esperado = [mus.m['compassos'][1] + k * 0.9 for k in range(4)]
    assert np.allclose(np.array(cq) / SR, esperado, atol=1e-4), (np.array(cq) / SR, esperado)
    assert ate == int(round(mus.m['compassos'][2] * SR))
    assert ini < cq[0]


@teste('nomes dos aparelhos: sem a porta USB, aparelho e apelido')
def _():
    assert M.sem_porta('PRIMARY (3- GT-100 Ver2-1)') == 'PRIMARY (GT-100 Ver2-1)'
    assert M.placa('Alto-falantes (Realtek(R) Audio)') == 'Realtek(R) Audio'
    assert M.apelido('Alto-falantes (Realtek(R) Audio)') == 'Alto-falantes'
    assert M.apelido('PRIMARY (3- GT-100 Ver2-1)') == 'GT-100 Ver2-1'
    assert M.apelido('Analogue 1 + 2 (Focusrite USB Audio)') == 'Focusrite USB Audio'
    assert M.mesmo_nome('PRIMARY (3- GT-100 Ver2-1)', 'PRIMARY (5- GT-100 Ver2-1)')
    assert M.mesmo_nome('Alto-falantes (Realtek(R) Audi', 'Alto-falantes (Realtek(R) Audio)')  # nome cortado do MME
    assert not M.mesmo_nome('Microfone (Realtek(R) Audio)', 'PRIMARY (GT-100 Ver2-1)')


# ------------------------------------------------------------------ alinhamento e "Acertar o tempo"
@teste('o alinhamento recupera o deslocamento e a diferença de relógio (erro < 1 ms)')
def _():
    rng = np.random.default_rng(3)
    r_o, r_i = 1 + 31e-6, 1 - 12e-6      # relógios da placa de som e da interface em relação ao do Windows
    T0, T1, S0 = 1000.0, 1000.137, 5 * SR
    saida = [(T0 + j * 2048 / (SR * r_o) + rng.normal(0, 0.002), S0 + j * 2048) for j in range(4000)]
    entrada, n = [], 0
    for k in range(8000):
        n += 1024
        entrada.append((T1 + n / (SR * r_i) + 0.004 + rng.normal(0, 0.002), n))
    inicio, alfa = T.alinhar(entrada, saida, 0.004)
    ini_ok = S0 + (T1 - T0) * SR * r_o
    alfa_ok = r_o / r_i
    assert abs(inicio - ini_ok) / SR < 0.001, f'início errado em {(inicio - ini_ok) / SR * 1000:.2f} ms'
    fim = 8000 * 1024
    erro_fim = abs((inicio + fim * alfa) - (ini_ok + fim * alfa_ok)) / SR
    assert erro_fim < 0.001, f'erro de {erro_fim * 1000:.2f} ms no fim da gravação'


@teste('com os relógios da sessão, uma gravação curta também alinha (erro < 1 ms)')
def _():
    rng = np.random.default_rng(6)
    r_o, r_i = 1 + 31e-6, 1 - 12e-6
    T0, T1, S0 = 500.0, 500.311, 9 * SR
    saida = [(T0 + j * 2048 / (SR * r_o) + rng.normal(0, 0.004), S0 + j * 2048) for j in range(60)]   # ~3 s
    entrada, n = [], 0
    for k in range(120):
        n += 1024
        entrada.append((T1 + n / (SR * r_i) + rng.normal(0, 0.004), n))
    inicio, alfa = T.alinhar(entrada, saida, 0.0, (1 / (SR * r_i), 1 / (SR * r_o)))
    ini_ok = S0 + (T1 - T0) * SR * r_o
    assert abs(inicio - ini_ok) / SR < 0.001, f'início errado em {(inicio - ini_ok) / SR * 1000:.2f} ms'
    assert abs(alfa - r_o / r_i) < 1e-9


@teste('reamostrar corrige a diferença de relógio')
def _():
    x = np.sin(np.arange(SR * 10) / SR * 2 * np.pi * 110).astype(np.float32)[:, None]
    y = T.reamostrar(x, 1 + 50e-6)
    assert abs(len(y) - len(x) * (1 + 50e-6)) <= 2, (len(x), len(y))


@teste('mono: lados iguais ou um lado em silêncio viram um canal só')
def _():
    x = np.random.default_rng(4).standard_normal((SR, 1)).astype(np.float32)
    assert T.mono_se_igual(np.concatenate([x, x], axis=1)).ndim == 1
    assert T.mono_se_igual(np.concatenate([x, x * 0.5], axis=1)).ndim == 2
    so_esq = T.mono_se_igual(np.concatenate([x, x * 1e-4], axis=1))
    assert so_esq.ndim == 1 and np.allclose(so_esq, x[:, 0])


@teste('"Acertar o tempo" acha um atraso conhecido (erro < 2 ms)')
def _():
    rng = np.random.default_rng(5)
    cliques = [1.5 + i * 0.6 for i in range(8)]
    atraso = 0.023
    x = (rng.standard_normal(int(SR * 7)) * 0.0005).astype(np.float32)
    for c in cliques:
        i = int((c + atraso + rng.normal(0, 0.003)) * SR)
        L = int(0.05 * SR)
        x[i:i + L] += (rng.standard_normal(L) * np.exp(-np.arange(L) / (0.008 * SR)) * 0.4).astype(np.float32)
    r = T.medir_ajuste([a / SR for a in T.ataques(x)], cliques)
    assert r is not None, 'não mediu'
    assert abs(r['ms'] - atraso * 1000) < 2.0, f'mediu {r["ms"]:.1f} ms (esperado {atraso * 1000:.0f})'


# ------------------------------------------------------------------ tentativas e MP3
@teste('o MP3 exportado tem a duração certa e a etiqueta com o título')
def _():
    x = (np.sin(np.arange(SR * 3) / SR * 2 * np.pi * 220) * 0.5).astype(np.float32)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'teste.mp3')
        T.exportar_mp3(x, SR, p, 'Ensaio de teste – ção', 'Sala de Ensaio', 'Testes', 'comentário')
        dur = sf.info(p).duration
        tags = T.ler_id3(p)
    assert abs(dur - 3.0) < 0.15, f'duração {dur:.2f} s'
    assert tags.get('TIT2') == 'Ensaio de teste – ção' and tags.get('TPE1') == 'Sala de Ensaio', tags


@teste('guardar, renomear, apagar e desfazer uma tentativa')
def _():
    with tempfile.TemporaryDirectory() as d:
        tt = T.Tentativas(d)
        f = tt.salvar(np.zeros(SR, np.float32), 'base', 'Guitarra base', 12.5, {})
        assert f['nome'] == 'Base 1' and os.path.exists(os.path.join(tt.pasta, f['arquivo']))
        tt.atualizar(f['id'], nome='Boa', favorita=True)
        assert tt.achar(f['id'])['nome'] == 'Boa'
        tt.apagar(f['id'])
        assert not tt.listar()
        tt.restaurar(f['id'])
        assert len(tt.listar()) == 1
        f2 = tt.salvar(np.zeros(SR, np.float32), 'base', 'Guitarra base', 0, {})
        assert f2['numero'] == 2


# ------------------------------------------------------------------ preparar músicas
@teste('faixas reconhecidas pelo nome (Demucs e português), sem confundir misturas')
def _():
    nomes = ['drums.wav', 'bass.wav', 'other.wav', 'vocals.wav', 'guitar.wav', 'piano.mp3', 'mixture.wav']
    achou = {k: os.path.basename(v) for k, v in PM.achar_partes_por_nome('x', nomes).items()}
    assert achou == {'bateria': 'drums.wav', 'baixo': 'bass.wav', 'outros': 'other.wav', 'voz': 'vocals.wav',
                     'guitarra': 'guitar.wav', 'piano': 'piano.mp3'}, achou
    assert os.path.basename(PM.achar_original('x', nomes)) == 'mixture.wav'
    nomes = ['Musica_baixo_bateria.wav', 'Musica_sem_voz.wav', 'Musica_guitarra_base_isolada.wav', 'Musica - Guitarra Solo.flac']
    achou = {k: os.path.basename(v) for k, v in PM.achar_partes_por_nome('x', nomes).items()}
    assert achou == {'base': 'Musica_guitarra_base_isolada.wav', 'solo': 'Musica - Guitarra Solo.flac'}, achou


@teste('cifra: compassos, repetição, compasso dividido e partes da música')
def _():
    cc, secs = PM.ler_cifra('# comentário\n[Intro]\nA5 % | D5,C5 -\n[Verso]\nF#m7 Bb\n')
    assert cc == [['A5'], ['A5'], ['D5', 'C5'], [None], ['F#m7'], ['Bb']], cc
    assert secs == [{'compasso': 1, 'nome': 'Intro'}, {'compasso': 5, 'nome': 'Verso'}], secs
    B, npc = PM.grade(60, '4/4', 1.0, 30)
    itens = PM.acordes_da_cifra(cc, B, npc, 'A')
    assert itens[0]['acorde'] == 'A5' and itens[0]['t1'] == B[2], 'os dois A5 seguidos deviam virar um trecho só'
    assert itens[1]['acorde'] == 'D5' and itens[1]['pulso'] == 1 and itens[2]['acorde'] == 'C5' and itens[2]['pulso'] == 3
    assert itens[1]['grau'] == 'IV' and itens[2]['grau'] == '♭III'
    try:
        PM.ler_cifra('H7 A5')
        raise AssertionError('aceitou um acorde inválido')
    except ValueError:
        pass


@teste('acordes: grau, descrição e desenho')
def _():
    assert AC.grau('E5', 'A') == 'V' and AC.grau('G', 'A') == '♭VII'
    assert AC.descrever('F#m')['nota'] == 'Fá♯' and AC.descrever('Bb7')['nota'] == 'Si♭'
    d = AC.desenho('C')
    assert d['casas'] == ['x', 3, 2, 0, 1, 0] and d['corda_tonica'] == 1 and d['intervalos'][1] == '1'
    assert AC.forma('G#5')[1:3] == [6, 6]
    svg = AC.svg('A5')
    assert svg.startswith('<svg') and svg.endswith('</svg>') and 'power chord' in svg
    assert MU.subdivisoes('12/8') == 3 and MU.subdivisoes('6/8') == 3 and MU.subdivisoes('4/4') == 2


@teste('música de demonstração: gera, prepara, aparece na lista e abre')
def _():
    import gerar_demo
    with tempfile.TemporaryDirectory() as d:
        pasta = gerar_demo.gerar(d, voltas=1, avisar=lambda s: None)
        man = json.load(open(os.path.join(pasta, 'ensaio.json'), encoding='utf-8'))
        assert [p['id'] for p in man['partes']] == ['bateria', 'baixo', 'base', 'solo'], man['partes']
        assert man['alvos'] == ['base', 'solo'] and man['compasso_tipo'] == '12/8'
        assert np.allclose(man['contagem'], [0.5, 1.3333, 2.1667, 3.0], atol=1e-3), man['contagem']
        assert [s['nome'] for s in man['secoes']] == ['Primeira volta', 'Final']
        assert man['acordes'][0]['acorde'] == 'A5' and man['acordes'][0]['t1'] - man['acordes'][0]['t0'] > 13
        # uma pasta nova, ainda sem preparar, com nomes do Demucs
        nova = os.path.join(d, 'Outra banda', 'Outra música')
        os.makedirs(nova)
        for nome in ('drums.wav', 'bass.wav', 'vocals.wav'):
            sf.write(os.path.join(nova, nome), np.zeros((SR, 2), np.float32), SR)
        MU.BIBLIOTECAS[:] = [d]
        lst = {x['id']: x for x in MU.listar()}
        assert lst['Demonstração']['preparada'], lst
        assert not lst['Outra banda/Outra música']['preparada'], lst
        PM.preparar(nova, avisar=lambda s: None)
        assert MU.listar()[0]['preparada'] or all(x['preparada'] for x in MU.listar())
        mus = MU.Musica(pasta, 'demo')
        tela = mus.dados_tela()
        assert tela['subdivisoes'] == 3 and tela['pulsos_max'] == 4 and len(tela['compassos']) >= 15
        assert mus.pulsos_antes(man['compassos'][0]) == man['contagem']
        assert mus.inicio_do_compasso(man['compassos'][3] + 1.0) == man['compassos'][3]
        assert mus.desenho_svg('E5').startswith('<svg')
        m = M.Motor(); m.carregar(mus); m.contagem = True
        m.tocar(de=0.0)
        y = rodar(m, 80)
        assert np.abs(y).max() > 0.05, 'a demonstração não soou'


if falhas:
    print(f'{len(falhas)} falha(s): {falhas}')
    sys.exit(1)
print('tudo certo')
