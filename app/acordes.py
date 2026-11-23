# -*- coding: utf-8 -*-
"""Acordes: nome da nota, tipo, grau em relação à tônica, digitação na guitarra e o desenho do acorde em SVG."""
import html

NOMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
BEMOL = {'Db': 'C#', 'Eb': 'D#', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#'}
ROMANOS = ['I', '♭II', 'II', '♭III', 'III', 'IV', '♭V', 'V', '♭VI', 'VI', '♭VII', 'VII']
INTERVALOS = ['1', '♭2', '2', '♭3', '3', '4', '♭5', '5', '♭6', '6', '♭7', '7']
CORDAS_SOLTAS = [40, 45, 50, 55, 59, 64]  # Mi grave ... Mi aguda (afinação padrão)
NOTAS_PT = {'C': 'Dó', 'D': 'Ré', 'E': 'Mi', 'F': 'Fá', 'G': 'Sol', 'A': 'Lá', 'B': 'Si'}
TIPOS = {'5': 'power chord: tônica e 5ª', '': 'maior: 1, 3 e 5', 'm': 'menor: 1, ♭3 e 5', 'sus2': 'sus2: 1, 2 e 5',
         'sus4': 'sus4: 1, 4 e 5', '7': 'com 7ª: 1, 3, 5 e ♭7', 'm7': 'menor com 7ª: 1, ♭3, 5 e ♭7',
         '6': 'com 6ª: 1, 3, 5 e 6', 'maj7': 'com 7ª maior: 1, 3, 5 e 7'}
# Digitações (da 6ª corda à 1ª; 'x' = não tocar), em posição aberta quando existe
FORMAS = {'A5': ['x', 0, 2, 2, 'x', 'x'], 'A': ['x', 0, 2, 2, 2, 0], 'Am': ['x', 0, 2, 2, 1, 0], 'Asus2': ['x', 0, 2, 2, 0, 0],
          'A7': ['x', 0, 2, 0, 2, 0], 'Am7': ['x', 0, 2, 0, 1, 0],
          'D5': ['x', 'x', 0, 2, 3, 'x'], 'D': ['x', 'x', 0, 2, 3, 2], 'Dm': ['x', 'x', 0, 2, 3, 1], 'Dsus2': ['x', 'x', 0, 2, 3, 0],
          'D7': ['x', 'x', 0, 2, 1, 2],
          'C5': ['x', 3, 5, 5, 'x', 'x'], 'C': ['x', 3, 2, 0, 1, 0], 'Csus2': ['x', 3, 0, 0, 3, 3], 'Cm': ['x', 3, 5, 5, 4, 3],
          'G5': [3, 5, 5, 'x', 'x', 'x'], 'G': [3, 2, 0, 0, 0, 3], 'Gsus2': [3, 0, 0, 2, 3, 3], 'Gm': [3, 5, 5, 3, 3, 3],
          'G7': [3, 2, 0, 0, 0, 1],
          'E5': [0, 2, 2, 'x', 'x', 'x'], 'E': [0, 2, 2, 1, 0, 0], 'Em': [0, 2, 2, 0, 0, 0], 'Esus2': [0, 2, 4, 4, 'x', 'x'],
          'E7': [0, 2, 0, 1, 0, 0], 'Em7': [0, 2, 0, 0, 0, 0],
          'F5': [1, 3, 3, 'x', 'x', 'x'], 'F': [1, 3, 3, 2, 1, 1], 'F#5': [2, 4, 4, 'x', 'x', 'x'],
          'B5': ['x', 2, 4, 4, 'x', 'x'], 'Bm': ['x', 2, 4, 4, 3, 2], 'B7': ['x', 2, 1, 2, 0, 2]}


def raiz(acorde):
    """Número da nota da tônica (0 = Dó ... 11 = Si)."""
    r = acorde[:2] if len(acorde) > 1 and acorde[1] in '#b' else acorde[:1]
    return NOMES.index(BEMOL.get(r, r))


def separar(acorde):
    """'F#m7' -> ('F#', 'm7')."""
    r = acorde[:2] if len(acorde) > 1 and acorde[1] in '#b' else acorde[:1]
    return r, acorde[len(r):]


def valido(acorde):
    try:
        r, _ = separar(acorde)
        return bool(r) and r[0] in 'ABCDEFG' and raiz(acorde) >= 0
    except ValueError:
        return False


def grau(acorde, tonica):
    return ROMANOS[(raiz(acorde) - raiz(tonica)) % 12]


def descrever(acorde):
    r, resto = separar(acorde)
    nota = NOTAS_PT.get(r[0], r[0]) + ('♯' if r.endswith('#') else '♭' if r.endswith('b') else '')
    return {'nota': nota, 'tipo': TIPOS.get(resto, resto)}


def forma(acorde):
    if acorde in FORMAS:
        return FORMAS[acorde]
    pc = raiz(acorde)
    f6, f5 = (pc - 4) % 12, (pc - 9) % 12  # power chord na 6ª ou na 5ª corda, o que ficar mais perto da pestana
    return [f6, f6 + 2, f6 + 2, 'x', 'x', 'x'] if f6 <= f5 else ['x', f5, f5 + 2, f5 + 2, 'x', 'x']


def desenho(acorde):
    """Digitação, corda da tônica (a mais grave) e o intervalo de cada corda em relação à tônica do acorde."""
    fr = forma(acorde); r = raiz(acorde)
    sob, corda_raiz = [], None
    for i, f in enumerate(fr):
        if f == 'x':
            sob.append(''); continue
        iv = (CORDAS_SOLTAS[i] + f - r) % 12
        sob.append(INTERVALOS[iv])
        if iv == 0 and corda_raiz is None:
            corda_raiz = i
    return {'casas': fr, 'corda_tonica': corda_raiz, 'intervalos': sob}


# ---------------------------------------------------------------- desenho em SVG (6ª corda à esquerda)
INK, INK2, MUTED, GOLD, DGOLD = '#1d2329', '#4a525c', '#7c838d', '#e0a526', '#9a6a00'
FONTE = "font-family=\"'Segoe UI', sans-serif\""
NOTAS_SUSTENIDO = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def _texto(x, y, s, size=10, fill=INK, anchor='middle', weight=400, extra=''):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}" {extra}>{html.escape(str(s), quote=True)}</text>')


def caixa(nome, casas, dedos=None, corda_tonica=None, sub='', embaixo=None):
    """Diagrama do acorde. casas/dedos: 6 itens da 6ª corda (Mi grave) à 1ª; 'x' = não tocar.
    embaixo: 6 rótulos escritos sob as cordas (ex.: os intervalos); sem ele, sai o nome da nota."""
    dedos = dedos or [None] * 6
    W, H = 122, 196
    x0, dx, y0, dy = 26, 14, 58, 22
    tocadas = [f for f in casas if f != 'x' and f > 0]
    base = 1 if not tocadas or max(tocadas) <= 4 else min(tocadas)
    o = [_texto(W / 2, 17, nome, 16, INK, weight=700)]
    if sub:
        o.append(_texto(W / 2, 31, sub, 8.5, MUTED))
    for i in range(6):
        x = x0 + i * dx
        o.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + 5 * dy}" stroke="#9aa1aa" stroke-width="{1.6 - i * 0.12:.2f}"/>')
    for k in range(6):
        y = y0 + k * dy
        o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + 5 * dx}" y2="{y}" stroke="#9aa1aa" stroke-width="1"/>')
    if base == 1:
        o.append(f'<rect x="{x0 - 1}" y="{y0 - 4}" width="{5 * dx + 2}" height="5" fill="{INK}" rx="1"/>')
    else:
        o.append(_texto(x0 - 9, y0 + dy * 0.5 + 4, f'{base}ª', 9.5, INK2, anchor='end', weight=600))
    for i, f in enumerate(casas):
        x = x0 + i * dx
        if f == 'x':
            o.append(_texto(x, y0 - 10, '×', 12, MUTED))
        elif f == 0:
            cor = GOLD if corda_tonica == i else INK2
            fundo = '#fff6dd' if corda_tonica == i else 'none'
            o.append(f'<circle cx="{x}" cy="{y0 - 14}" r="4.2" fill="{fundo}" stroke="{cor}" stroke-width="1.6"/>')
        else:
            cy = y0 + (f - base + 0.5) * dy
            fill, tc = (GOLD, INK) if corda_tonica == i else (INK, '#ffffff')
            o.append(f'<circle cx="{x}" cy="{cy}" r="7.2" fill="{fill}"/>')
            if dedos[i]:
                o.append(_texto(x, cy + 3.6, dedos[i], 9.5, tc, weight=700))
        if f != 'x':
            if embaixo:
                if embaixo[i]:
                    o.append(_texto(x, y0 + 5 * dy + 15, embaixo[i], 8.6, DGOLD if embaixo[i] == '1' else INK2, weight=700,
                                    extra='letter-spacing="-0.4"'))
            else:
                pc = (CORDAS_SOLTAS[i] + f) % 12
                o.append(_texto(x, y0 + 5 * dy + 15, NOTAS_SUSTENIDO[pc], 8, INK2, extra='letter-spacing="-0.4"'))
    corpo = ''.join(o)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" {FONTE}>'
            f'{corpo}</svg>')


def svg(acorde, d=None):
    """Desenho do acorde com o tipo em cima e os intervalos embaixo. d: o 'desenho' guardado na música (opcional)."""
    d = d or desenho(acorde)
    return caixa(acorde, d['casas'], None, d['corda_tonica'], descrever(acorde)['tipo'].split(':')[0], d['intervalos'])
