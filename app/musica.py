# -*- coding: utf-8 -*-
"""Músicas da Sala de Ensaio: acha as músicas nas pastas de músicas (bibliotecas), lê o ensaio.json de cada uma
(feito por preparar_musica.py) e carrega as partes da banda na memória, todas no mesmo comprimento."""
import base64, json, os
import numpy as np
import soundfile as sf

import acordes as AC

BIBLIOTECAS = []          # pastas onde procurar músicas (o app preenche a partir da configuração)
CORES_PARTES = {'bateria': '#9aa5b1', 'baixo': '#8b7cf6', 'base': '#f0a030', 'guitarra': '#f0a030', 'solo': '#ef5b5b',
                'voz': '#3fb67a', 'piano': '#4cc3cc', 'outros': '#b0a48a'}
EXT_AUDIO = ('.wav', '.flac', '.mp3', '.ogg', '.aiff', '.aif')


def pasta_musicas_padrao():
    """<Músicas do Windows>\\Sala de Ensaio (a pasta Músicas pode ter sido mudada de lugar: pergunta ao Windows)."""
    base = os.path.join(os.path.expanduser('~'), 'Music')
    if os.name == 'nt':
        try:
            import ctypes, uuid
            guid = uuid.UUID('{4BD8D571-6D19-48D3-BE97-422220080E43}')  # FOLDERID_Music
            buf = ctypes.create_string_buffer(guid.bytes_le)
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(buf, 0, None, ctypes.byref(p)) == 0:
                base = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
        except Exception:
            pass
    return os.path.join(base, 'Sala de Ensaio')


def manifesto(pasta):
    """Caminho do ensaio.json da música (na própria pasta ou em Dados/), ou None."""
    for p in (os.path.join(pasta, 'ensaio.json'), os.path.join(pasta, 'Dados', 'ensaio.json')):
        if os.path.isfile(p):
            return p
    return None


def _parece_musica(pasta, arquivos, dirs):
    """Pasta ainda não preparada que tem faixas separadas: 'Faixas/' ou arquivos com nomes de partes (drums, bass...)."""
    if 'Faixas' in dirs:
        return True
    import preparar_musica as PM
    return len(PM.achar_partes_por_nome(pasta, arquivos)) >= 2


def listar():
    """Músicas de todas as bibliotecas: [{'id', 'pasta', 'preparada', 'titulo', 'artista'}]."""
    out, vistos = [], set()
    for bib in BIBLIOTECAS:
        if not os.path.isdir(bib):
            continue
        for raiz, dirs, arqs in os.walk(bib):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('Gravações', 'Tentativas', 'Exportados')]
            man = manifesto(raiz)
            if not man and not _parece_musica(raiz, arqs, dirs):
                continue
            rel = os.path.relpath(raiz, bib).replace('\\', '/')
            if rel == '.':
                rel = os.path.basename(raiz)
            mid = rel
            k = 2
            while mid in vistos:
                mid = f'{rel} ({k})'; k += 1
            vistos.add(mid)
            item = {'id': mid, 'pasta': raiz, 'preparada': bool(man)}
            if man:
                try:
                    m = json.load(open(man, encoding='utf-8'))
                    item.update(titulo=m.get('titulo'), artista=m.get('artista'))
                except Exception:
                    item['preparada'] = False
            pedacos = rel.split('/')
            item['titulo'] = item.get('titulo') or pedacos[-1]
            item['artista'] = item.get('artista') or (pedacos[0] if len(pedacos) > 1 else '')
            out.append(item)
            dirs[:] = []
    return sorted(out, key=lambda i: ((i.get('artista') or '').lower(), (i.get('titulo') or '').lower()))


def pasta_da_musica(mid, lista=None):
    for x in (lista if lista is not None else listar()):
        if x['id'] == mid:
            return x['pasta']
    raise KeyError(f'música não encontrada: {mid}')


def id_da_pasta(pasta, lista=None):
    p = os.path.normcase(os.path.abspath(pasta))
    for x in (lista if lista is not None else listar()):
        if os.path.normcase(os.path.abspath(x['pasta'])) == p:
            return x['id']
    return None


def _b64(v):
    return base64.b64encode(v.astype(np.uint8).tobytes()).decode('ascii')


def _contorno(pk):
    return np.clip(np.round(np.sqrt(np.clip(pk, 0, 1)) * 255), 0, 255)


def subdivisoes(compasso_tipo):
    """Divisões de cada pulso: 3 nos compassos compostos (6/8, 9/8, 12/8), 2 nos outros."""
    try:
        n, d = (int(v) for v in str(compasso_tipo).split('/'))
        return 3 if d == 8 and n % 3 == 0 and n >= 6 else 2
    except ValueError:
        return 2


class Musica:
    def __init__(self, pasta, mid=None):
        self.pasta = pasta
        self.id = mid or os.path.basename(pasta)
        man = manifesto(pasta)
        if not man:
            raise FileNotFoundError(f'a música não foi preparada: falta o ensaio.json em {pasta}')
        self.m = json.load(open(man, encoding='utf-8'))
        self.sr = int(self.m['taxa'])
        self.n = int(round(self.m['duracao_s'] * self.sr))
        self.partes = []
        for p in self.m['partes']:
            x, sr = sf.read(os.path.join(pasta, p['arquivo']), dtype='int16', always_2d=True)
            if sr != self.sr:
                raise ValueError(f"{p['arquivo']}: {sr} Hz (esperado {self.sr})")
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            buf = np.zeros((self.n, 2), np.int16)
            k = min(self.n, len(x))
            buf[:k] = x[:k, :2]
            self.partes.append({'id': p['id'], 'nome': p['nome'], 'ganho': float(p['ganho']), 'dados': buf,
                                'cor': CORES_PARTES.get(p['id'], '#9aa5b1')})
        ids = [p['id'] for p in self.partes]
        self.alvos = [a for a in self.m.get('alvos', ids) if a in ids] or ids
        # pulsos: os da contagem (antes do compasso 1) e os de cada compasso; o 1º de cada compasso é o forte
        t, forte, comp = [], [], []
        for i, v in enumerate(self.m.get('contagem', [])):
            t.append(v); forte.append(i == 0); comp.append(0)
        B = self.m.get('compassos', [])
        npc = self.m.get('pulsos_por_compasso', [])
        for i in range(len(B) - 1):
            d = (B[i + 1] - B[i]) / npc[i]
            for k in range(npc[i]):
                t.append(B[i] + k * d); forte.append(k == 0); comp.append(i + 1)
        self.pulsos = np.array(t, np.float64)
        self.pulso_forte = np.array(forte, bool)
        self.pulso_compasso = np.array(comp, np.int32)
        self._tela = None

    @property
    def titulo(self):
        return self.m.get('titulo', self.id)

    def parte(self, pid):
        for p in self.partes:
            if p['id'] == pid:
                return p
        raise KeyError(pid)

    def inicio_do_compasso(self, t):
        """Começo do compasso em que cai o tempo t (antes do compasso 1: o começo da faixa; sem compassos: o próprio t)."""
        B = self.m.get('compassos', [])
        if not B:
            return float(t)
        if t < B[0] - 0.05:
            return 0.0
        i = int(np.searchsorted(B, t + 0.05, side='right')) - 1
        return float(B[max(0, min(i, len(B) - 2))])

    def pulsos_antes(self, t, quantos=4):
        """Os `quantos` pulsos antes de t (para a contagem). Antes do compasso 1, a própria contagem da faixa."""
        i = int(np.searchsorted(self.pulsos, t - 0.02, side='left'))
        sel = list(self.pulsos[max(0, i - quantos):i])
        while 0 < len(sel) < quantos:  # perto do começo: completa para trás no mesmo andamento
            d = sel[1] - sel[0] if len(sel) > 1 else 0.91
            sel.insert(0, sel[0] - d)
        return [v for v in sel if v >= 0]

    def dados_tela(self):
        """O que a tela precisa: compassos, acordes, partes da música e o contorno das ondas."""
        if self._tela is not None:
            return self._tela
        w = int(self.sr * 0.02)
        nw = self.n // w
        mix = np.zeros(nw, np.float32)
        por_parte = {}
        passo = w * 4000
        for c0 in range(0, nw * w, passo):
            c1 = min(nw * w, c0 + passo)
            s = np.zeros((c1 - c0, 2), np.float32)
            for p in self.partes:
                y = p['dados'][c0:c1].astype(np.float32) * np.float32(p['ganho'] / 32768)
                s += y
                pk = np.abs(y).max(axis=1).reshape(-1, w).max(axis=1)
                por_parte.setdefault(p['id'], []).append(pk)
            mix[c0 // w:c1 // w] = np.abs(s).max(axis=1).reshape(-1, w).max(axis=1)
        partes_pk = {}
        for pid, pedacos in por_parte.items():
            v = np.concatenate(pedacos)
            partes_pk[pid] = _b64(_contorno(v / max(float(v.max()), 1e-6)))
        m = self.m
        npc = m.get('pulsos_por_compasso', [])
        self._tela = {
            'id': self.id, 'titulo': m.get('titulo'), 'artista': m.get('artista'), 'album': m.get('album'),
            'duracao': self.n / self.sr, 'tonica': m.get('tonica'), 'compasso_tipo': m.get('compasso_tipo'),
            'subdivisoes': int(m.get('subdivisoes') or subdivisoes(m.get('compasso_tipo', '4/4'))),
            'pulsos_max': max(npc, default=4),
            'bpm': m.get('bpm'), 'compassos': m.get('compassos', []), 'pulsos_por_compasso': npc,
            'contagem': m.get('contagem', []), 'secoes': m.get('secoes', []), 'acordes': m.get('acordes', []),
            'acordes_info': {a: AC.descrever(a) for a in m.get('desenhos', {})},
            'partes': [{'id': p['id'], 'nome': p['nome'], 'cor': p['cor']} for p in self.partes],
            'alvos': self.alvos,
            'picos': {'janela_s': 0.02, 'mistura': _b64(_contorno(mix)), 'partes': partes_pk},
        }
        return self._tela

    def desenho_svg(self, acorde):
        d = self.m.get('desenhos', {}).get(acorde)
        if d is None:
            if not AC.valido(acorde):
                raise KeyError(acorde)
            d = AC.desenho(acorde)
        return AC.svg(acorde, d)
