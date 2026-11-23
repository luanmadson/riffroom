# -*- coding: utf-8 -*-
"""Gera a música de demonstração da Sala de Ensaio: um blues lento em Lá (12/8, 72 pulsos por minuto), com bateria,
baixo, guitarra base e guitarra solo em faixas separadas. Tudo é sintetizado aqui mesmo, sem gravação de ninguém,
e no fim a música é preparada (ensaio.json) com os compassos, os acordes e as partes, como uma música de verdade.
A música fica em <pasta das músicas>\Demonstração.

Uso: python -I -X utf8 gerar_demo.py [pasta das músicas] [--voltas 2]
     (sem a pasta: <Músicas do Windows>\\Sala de Ensaio)
"""
import argparse, os, sys
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, '..', 'app'))
import musica as MU            # noqa: E402
import preparar_musica as PM   # noqa: E402

SR = 44100
BPM = 72.0                       # pulsos (semínima pontuada) por minuto
PULSO = 60.0 / BPM
TERCINA = PULSO / 3
COMPASSO = 4 * PULSO
ENTRADA = 0.5                    # primeiro toque da contagem
UM = ENTRADA + 4 * PULSO         # começo do compasso 1
TITULO, ARTISTA = 'Blues de demonstração', 'Sala de Ensaio'
VOLTA = ['A5', 'A5', 'A5', 'A5', 'D5', 'D5', 'A5', 'A5', 'E5', 'D5', 'A5', 'E5']   # os 12 compassos do blues
RAIZ_BASE = {'A5': 45, 'D5': 50, 'E5': 40}        # Lá2, Ré3, Mi2 (MIDI)
RAIZ_BAIXO = {'A5': 33, 'D5': 38, 'E5': 40}
# frases do solo (pentatônica de Lá menor): (compasso da volta, pulso, tercina, nota MIDI, duração em tercinas)
SOLO = [
    (1, 1, 1, 69, 2), (1, 1, 3, 72, 1), (1, 2, 1, 74, 3), (1, 3, 1, 72, 2), (1, 3, 3, 69, 1), (1, 4, 1, 69, 3),
    (2, 2, 1, 76, 2), (2, 2, 3, 74, 1), (2, 3, 1, 72, 2), (2, 3, 3, 69, 1), (2, 4, 1, 67, 2), (2, 4, 3, 69, 1),
    (3, 1, 1, 69, 6), (3, 3, 1, 72, 1), (3, 3, 2, 74, 1), (3, 3, 3, 76, 1), (3, 4, 1, 79, 3),
    (4, 1, 1, 81, 6), (4, 4, 1, 76, 1), (4, 4, 2, 74, 1), (4, 4, 3, 72, 1),
    (5, 1, 1, 74, 3), (5, 2, 1, 72, 2), (5, 2, 3, 69, 1), (5, 3, 1, 74, 2), (5, 3, 3, 76, 1), (5, 4, 1, 74, 3),
    (6, 1, 1, 69, 3), (6, 2, 1, 72, 2), (6, 2, 3, 74, 1), (6, 3, 1, 75, 1), (6, 3, 2, 76, 2), (6, 4, 1, 79, 3),
    (7, 1, 1, 81, 9), (7, 4, 1, 76, 1), (7, 4, 2, 72, 1), (7, 4, 3, 69, 1),
    (8, 1, 1, 72, 2), (8, 1, 3, 69, 1), (8, 2, 1, 67, 2), (8, 2, 3, 64, 1), (8, 3, 1, 69, 6),
    (9, 1, 1, 76, 3), (9, 2, 1, 74, 2), (9, 2, 3, 71, 1), (9, 3, 1, 76, 3), (9, 4, 1, 79, 3),
    (10, 1, 1, 81, 3), (10, 2, 1, 79, 2), (10, 2, 3, 76, 1), (10, 3, 1, 74, 3), (10, 4, 1, 72, 2), (10, 4, 3, 69, 1),
    (11, 1, 1, 69, 6), (11, 3, 1, 72, 1), (11, 3, 2, 73, 1), (11, 3, 3, 76, 1), (11, 4, 1, 81, 3),
    (12, 1, 1, 68, 3), (12, 2, 1, 71, 3), (12, 3, 1, 76, 3),
]


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def passa(x, f, tipo='low', ordem=4):
    return sosfilt(butter(ordem, f, tipo, fs=SR, output='sos'), x, axis=0)


def corda(f, dur, decai=0.996, brilho=0.6, rng=None):
    """Corda dedilhada (Karplus-Strong), calculada um período por vez."""
    N = max(2, int(round(SR / f - 0.5)))
    n = int(dur * SR)
    ex = rng.uniform(-1, 1, N)
    ex = brilho * ex + (1 - brilho) * np.convolve(ex, np.ones(4) / 4, mode='same')
    y = np.zeros(n + N + 1)
    y[1:N + 1] = ex
    i = N + 1
    while i < len(y):
        k = min(N, len(y) - i)
        y[i:i + k] = decai * 0.5 * (y[i - N:i - N + k] + y[i - N - 1:i - N - 1 + k])
        i += k
    return y[1:n + 1]


def abafar(x, ms=18):
    k = min(len(x), int(SR * ms / 1000))
    if k:
        x[-k:] *= np.linspace(1, 0, k)
    return x


def por(trilha, t, x, ganho=1.0, pan=(1.0, 1.0)):
    i = int(round(t * SR))
    if i < 0 or i >= len(trilha):
        return
    k = min(len(x), len(trilha) - i)
    trilha[i:i + k, 0] += x[:k] * ganho * pan[0]
    trilha[i:i + k, 1] += x[:k] * ganho * pan[1]


# ------------------------------------------------------------------ bateria
def env(L, tau):
    return np.exp(-np.arange(L) / (tau * SR))


def bumbo(rng):
    L = int(0.4 * SR); t = np.arange(L) / SR
    f = 48 + 75 * np.exp(-t / 0.035)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(L, 0.16)
    x[:120] += rng.uniform(-0.4, 0.4, 120) * np.linspace(1, 0, 120)
    return x


def caixa(rng):
    L = int(0.35 * SR); t = np.arange(L) / SR
    ruido = passa(passa(rng.uniform(-1, 1, L), 1400, 'high'), 7500) * env(L, 0.085)
    corpo = np.sin(2 * np.pi * 186 * t) * env(L, 0.05)
    return 0.9 * ruido / np.abs(ruido).max() + 0.45 * corpo


def chimbal(rng, aberto=False):
    L = int((0.25 if aberto else 0.09) * SR)
    x = passa(rng.uniform(-1, 1, L), 7000, 'high') * env(L, 0.09 if aberto else 0.022)
    return x / np.abs(x).max()


def prato(rng):
    L = int(2.8 * SR)
    x = passa(rng.uniform(-1, 1, L), 3800, 'high') * env(L, 0.8)
    return x / np.abs(x).max()


def baqueta(rng):
    L = int(0.06 * SR); t = np.arange(L) / SR
    x = passa(passa(rng.uniform(-1, 1, L), 2000, 'high'), 6000) * env(L, 0.007) + 0.5 * np.sin(2 * np.pi * 2400 * t) * env(L, 0.012)
    return x / np.abs(x).max()


# ------------------------------------------------------------------ solo (síntese aditiva, com vibrato e drive)
def nota_solo(midi, dur, vib):
    L = int((dur + 0.12) * SR); t = np.arange(L) / SR
    f0 = hz(midi)
    prof = 0.012 * np.clip((t - 0.25) / 0.35, 0, 1) if vib else 0.0
    f = f0 * (1 + prof * np.sin(2 * np.pi * 5.6 * t))
    fase = 2 * np.pi * np.cumsum(f) / SR
    K = max(1, int(5200 / f0))
    x = sum(np.sin(k * fase) / k for k in range(1, K + 1))
    a = np.minimum(1, t / 0.008) * np.exp(-t / (1.6 if vib else 0.9))
    solta = np.clip((dur + 0.1 - t) / 0.1, 0, 1)
    return x * a * solta


def gerar(destino, voltas=2, avisar=print):
    """Grava as faixas e a cifra em `destino` e prepara a música. Devolve a pasta da música."""
    rng = np.random.default_rng(1979)
    ac = VOLTA * voltas + ['A5', 'A5']               # mais dois compassos de final
    nb = len(ac)
    total = UM + nb * COMPASSO + 2.5
    n = int(total * SR)
    bat, bai, bas, sol = (np.zeros((n, 2)) for _ in range(4))
    ginga = lambda: rng.normal(0, 0.004)             # um pouco de "mão humana" no tempo
    vel = lambda: 1 + rng.normal(0, 0.06)

    # contagem com as baquetas
    for k in range(4):
        por(bat, ENTRADA + k * PULSO, baqueta(rng), 0.55 if k else 0.7)
    for b, acorde in enumerate(ac):
        t0 = UM + b * COMPASSO
        final = b == nb - 1
        # bateria (shuffle): chimbal na 1ª e na 3ª tercina; bumbo no 1 e no 3; caixa no 2 e no 4
        if b % 12 == 0 or final:
            por(bat, t0, prato(rng), 0.35)
        for p in range(4):
            tp = t0 + p * PULSO
            if final and p > 0:
                break
            por(bat, tp + ginga(), chimbal(rng), 0.22 * vel(), (0.8, 1.0))
            if not final:
                por(bat, tp + 2 * TERCINA + ginga(), chimbal(rng), 0.14 * vel(), (0.8, 1.0))
            if p in (0, 2):
                por(bat, tp + ginga() * 0.5, bumbo(rng), 0.9 * vel())
            if p == 2 and not final:
                por(bat, tp + 2 * TERCINA + ginga(), bumbo(rng), 0.55 * vel())
            if p in (1, 3) and not final:
                por(bat, tp + ginga() * 0.5, caixa(rng), 0.62 * vel())
            if p == 3 and not final and b % 4 == 3:
                por(bat, tp + 2 * TERCINA + ginga(), caixa(rng), 0.22 * vel())
        # baixo: o "boogie" R, 3, 5, 6, ♭7, 6, 5, 3 no balanço do shuffle
        r = RAIZ_BAIXO[acorde]
        for k, iv in enumerate([0, 4, 7, 9, 10, 9, 7, 4]):
            if final and k > 0:
                break
            p, longa = divmod(k, 2)
            tk = t0 + p * PULSO + (2 * TERCINA if longa else 0) + ginga()
            dur = 3.0 if final else (TERCINA * 0.95 if longa else 2 * TERCINA * 0.95)
            x = abafar(corda(hz(r + iv), dur, 0.997, 0.35, rng))
            por(bai, tk, x, 0.75 * vel())
        # guitarra base: quinta e sexta alternadas (A5, A6, A5, A6), cordas abafadas na palma
        rb = RAIZ_BASE[acorde]
        for p in range(4):
            for longa in (0, 1):
                if final and (p > 0 or longa):
                    break
                tk = t0 + p * PULSO + (2 * TERCINA if longa else 0) + ginga()
                dur = 3.0 if final else (TERCINA * 0.9 if longa else 2 * TERCINA * 0.9)
                segunda = rb + (9 if p % 2 else 7)
                for j, nota in enumerate((rb, segunda) + ((rb + 12,) if final else ())):
                    x = abafar(corda(hz(nota), dur, 0.998 if final else 0.985, 0.8, rng))
                    por(bas, tk + j * 0.006, x, 0.5 * vel(), (1.0, 0.55))
    # solo na segunda volta
    if voltas >= 2:
        for (c, p, tr, nota, nt) in SOLO:
            tk = UM + (12 + c - 1) * COMPASSO + (p - 1) * PULSO + (tr - 1) * TERCINA + ginga()
            dur = nt * TERCINA * 0.96
            por(sol, tk, nota_solo(nota, dur, nt >= 3), 0.30 * vel(), (0.6, 1.0))

    # timbres: drive e caixa de guitarra nas guitarras, um pouco de grave no baixo
    bas = passa(passa(np.tanh(2.8 * bas) / np.tanh(2.8), 3300), 90, 'high')
    sol = passa(passa(np.tanh(3.5 * sol) / np.tanh(3.5), 4800), 160, 'high')
    bai = passa(bai, 1800)
    faixas = {'bateria': bat * 0.55, 'baixo': bai * 0.9, 'guitarra_base': bas * 0.5, 'guitarra_solo': sol * 0.24}
    pico = np.abs(sum(faixas.values())).max()
    k = 0.85 / pico
    pasta = os.path.join(destino, 'Demonstração')
    os.makedirs(pasta, exist_ok=True)
    for nome, x in faixas.items():
        sf.write(os.path.join(pasta, nome + '.wav'), np.clip(x * k, -1, 1).astype(np.float32), SR, subtype='PCM_16')
    linhas = ['# Cifra da música de demonstração: cada palavra é um compasso (12/8: 4 pulsos, cada um com 3 tercinas).',
              "# '%' repete o compasso anterior; [Nome] marca o começo de uma parte da música.", '']
    for v in range(voltas):
        linhas.append('[Primeira volta]' if v == 0 else '[Solo]' if v == 1 else f'[Volta {v + 1}]')
        linhas += ['A5 % % %', 'D5 % A5 %', 'E5 D5 A5 E5']
    linhas += ['[Final]', 'A5 A5', '']
    open(os.path.join(pasta, 'acordes.txt'), 'w', encoding='utf-8').write('\n'.join(linhas))
    PM.preparar(pasta, titulo=TITULO, artista=ARTISTA, album='', tonica='A', bpm=BPM, compasso='12/8', inicio=round(UM, 4),
                alvos='base,solo', avisar=avisar)
    return pasta


def main():
    ap = argparse.ArgumentParser(description='Gera a música de demonstração (blues em Lá) e prepara para a Sala de Ensaio.')
    ap.add_argument('pasta', nargs='?', help='pasta das músicas (padrão: <Músicas>\\Sala de Ensaio)')
    ap.add_argument('--voltas', type=int, default=2, help='quantas vezes os 12 compassos (2: a segunda tem solo)')
    a = ap.parse_args()
    destino = a.pasta or MU.pasta_musicas_padrao()
    print('gerando a música de demonstração...')
    pasta = gerar(destino, a.voltas)
    print('pronta:', pasta)


if __name__ == '__main__':
    main()
