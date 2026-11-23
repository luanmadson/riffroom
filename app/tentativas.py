# -*- coding: utf-8 -*-
# Tentativas (gravações) da Sala de Ensaio:
#   - as contas do alinhamento: relógio da interface x relógio da placa de som, a partir das horas dos blocos de áudio;
#   - o "Acertar o tempo": acha os ataques da guitarra e compara com os cliques;
#   - guardar (FLAC + ficha .json), listar, renomear, apagar com desfazer e exportar MP3.
import base64, datetime, glob, json, os, re, shutil, struct
from fractions import Fraction
import numpy as np
import soundfile as sf

SR = 44100
NOME_CURTO = {'base': 'Base', 'solo': 'Solo', 'baixo': 'Baixo', 'voz': 'Voz', 'bateria': 'Bateria', 'guitarra': 'Guitarra',
              'piano': 'Piano', 'outros': 'Outros'}


# ------------------------------------------------------------------ alinhamento
def _reta(x, y):
    """Reta y = a + b·x por mínimos quadrados, descartando os blocos que o Windows atrasou (pontos fora da reta)."""
    x = np.asarray(x, np.float64); y = np.asarray(y, np.float64)
    x0, y0 = x.mean(), y.mean()
    xc, yc = x - x0, y - y0
    ok = np.ones(len(x), bool)
    for _ in range(4):
        b, a = np.polyfit(xc[ok], yc[ok], 1)
        r = yc - (a + b * xc)
        med = np.median(r[ok]); mad = np.median(np.abs(r[ok] - med)) * 1.4826 + 1e-5
        novo = np.abs(r - med) < 4 * mad
        if novo.sum() < max(5, len(x) // 4) or (novo == ok).all():
            break
        ok = novo
    return y0 + a - b * x0, b


def alinhar(entrada, saida, atraso_entrada_s, relogios=None):
    """entrada: [(hora do bloco de entrada, amostras gravadas até o fim do bloco)];
    saida: [(hora em que o bloco de saída começa a soar, quadro da música no começo do bloco)];
    relogios: (s_entrada, s_saida), segundos por amostra medidos ao longo da sessão (mais firmes que os da gravação).
    Devolve (inicio, alfa): a amostra n da gravação corresponde ao quadro inicio + n·alfa da música."""
    if len(entrada) < 3 or len(saida) < 3:
        return None
    ti = np.array([e[0] for e in entrada], np.float64); ni = np.array([e[1] for e in entrada], np.float64)
    to = np.array([s[0] for s in saida], np.float64); mo = np.array([s[1] for s in saida], np.float64)
    if relogios:
        s_in, s_out = relogios
        a_in = float(np.median(ti - ni * s_in))
        a_out = float(np.median(to - mo * s_out))
    else:
        a_in, s_in = _reta(ni, ti)     # hora ~ a_in + n·s_in (n = fim do bloco)
        a_out, s_out = _reta(mo, to)   # hora em que o quadro m soa ~ a_out + m·s_out
    inicio = (a_in - atraso_entrada_s - a_out) / s_out
    alfa = s_in / s_out
    return float(inicio), float(alfa)


def reamostrar(x, alfa):
    """Corrige a diferença de relógio: estica (alfa > 1) ou encolhe a gravação para casar com a música."""
    from scipy.signal import resample_poly
    fr = Fraction(alfa).limit_denominator(100000)
    if fr.numerator == fr.denominator:
        return x
    return resample_poly(x, fr.numerator, fr.denominator, axis=0).astype(np.float32)


def mono_se_igual(x):
    """Guarda um lado só quando o estéreo não traz nada: os dois lados iguais (timbre sem efeito estéreo) ou um lado
    em silêncio (interface com a guitarra só na entrada 1)."""
    if x.ndim == 1:
        return x
    if x.shape[1] == 1:
        return x[:, 0]
    L, R = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    eL, eR = np.dot(L, L), np.dot(R, R)
    e = max(eL, eR)
    if e < 1e-9:
        return x[:, 0]
    d = L - R
    if np.dot(d, d) / e < 1e-4:  # diferença 40 dB abaixo do sinal
        return (0.5 * (x[:, 0] + x[:, 1])).astype(np.float32)
    if min(eL, eR) / e < 1e-5:   # um lado 50 dB abaixo do outro: só ruído
        return x[:, 0 if eL >= eR else 1].copy()
    return x


# ------------------------------------------------------------------ acertar o tempo
def ataques(x, sr=SR, refratario_s=0.15):
    """Amostras em que começam as palhetadas (ataques). Para o "Acertar o tempo": cordas abafadas e cliques espaçados."""
    m = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
    h = max(1, sr // 1000)                    # janelas de 1 ms
    n = len(m) // h
    if n < 10:
        return []
    env = m[:n * h].reshape(n, h).max(axis=1)
    topo = float(np.percentile(env, 99.7))
    fundo = float(np.percentile(env, 30))
    limiar = max(fundo * 6, topo * 0.12, 10 ** (-60 / 20))
    out, i, pula = [], 0, int(refratario_s * 1000)
    while i < n:
        if env[i] >= limiar:
            j1 = min(n, i + 30)
            pico = float(env[i:j1].max())
            s0 = max(0, (i - 5) * h); s1 = j1 * h
            k = int(np.argmax(m[s0:s1] >= 0.25 * pico))
            out.append(s0 + k)
            i = j1 + pula
        else:
            i += 1
    return out


def medir_ajuste(ataques_s, cliques_s, janela_s=0.25):
    """Atraso típico (mediana) entre os cliques e as palhetadas, em ms. Ignora o 1º clique (é para pegar o tempo)."""
    difs = []
    for c in cliques_s[1:]:
        perto = [a - c for a in ataques_s if abs(a - c) <= janela_s]
        if perto:
            difs.append(min(perto, key=abs))
    if len(difs) < 4:
        return None
    d = np.array(difs) * 1000
    med = float(np.median(d))
    return {'ms': med, 'espalhamento_ms': float(np.median(np.abs(d - med))), 'acertos': len(difs), 'cliques': len(cliques_s) - 1}


# ------------------------------------------------------------------ MP3
def etiqueta_id3(titulo, artista, album, ano, comentario=''):
    """Etiqueta ID3v2.3 (título, artista, álbum, ano e comentário), em UTF-16, para o celular mostrar o nome certo."""
    def quadro(fid, dados):
        return fid.encode('ascii') + struct.pack('>I', len(dados)) + b'\x00\x00' + dados

    def txt(s):
        return b'\xff\xfe' + str(s).encode('utf-16-le')

    corpo = b''.join(quadro(f, b'\x01' + txt(v)) for f, v in
                     (('TIT2', titulo), ('TPE1', artista), ('TALB', album), ('TYER', ano)) if v)
    if comentario:
        corpo += quadro('COMM', b'\x01' + b'por' + txt('') + b'\x00\x00' + txt(comentario))
    n = len(corpo)
    tam = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])  # tamanho "synchsafe"
    return b'ID3\x03\x00\x00' + tam + corpo


def ler_id3(caminho):
    """Lê de volta os textos da etiqueta ID3v2.3 gravada por etiqueta_id3 (para os testes)."""
    b = open(caminho, 'rb').read()
    if b[:3] != b'ID3':
        return {}
    n = (b[6] << 21) | (b[7] << 14) | (b[8] << 7) | b[9]
    i, out = 10, {}
    while i + 10 <= 10 + n:
        fid, tam = b[i:i + 4].decode('ascii', 'replace'), struct.unpack('>I', b[i + 4:i + 8])[0]
        if not tam or not fid.strip('\x00'):
            break
        d = b[i + 10:i + 10 + tam]
        if fid.startswith('T') and d[:1] == b'\x01':
            out[fid] = d[1:].decode('utf-16')
        i += 10 + tam
    return out


def exportar_mp3(audio, sr, destino, titulo, artista, album, comentario=''):
    import lameenc
    x = audio if audio.ndim == 2 else audio[:, None]
    pico = float(np.max(np.abs(x))) if len(x) else 0.0
    if pico > 0.891:
        x = x * (0.891 / pico)
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)
    enc = lameenc.Encoder()
    enc.set_bit_rate(320); enc.set_in_sample_rate(sr); enc.set_channels(pcm.shape[1]); enc.set_quality(2)
    dados = enc.encode(np.ascontiguousarray(pcm).tobytes()) + enc.flush()
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, 'wb') as f:
        f.write(etiqueta_id3(titulo, artista, album, str(datetime.date.today().year), comentario))
        f.write(dados)
    return destino


# ------------------------------------------------------------------ guardar e organizar
def _seguro(s):
    return re.sub(r'[<>:"/\\|?*]', '-', s).strip()


def picos(x, sr=SR, janela_s=0.02):
    """Contorno para desenhar a onda: pico a cada 20 ms, de 0 a 255 (raiz quadrada, para ver as partes fracas)."""
    m = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
    w = int(sr * janela_s)
    n = len(m) // w
    if n == 0:
        return np.zeros(0, np.uint8)
    p = m[:n * w].reshape(n, w).max(axis=1)
    return np.clip(np.round(np.sqrt(np.minimum(p, 1.0)) * 255), 0, 255).astype(np.uint8)


class Tentativas:
    def __init__(self, pasta_musica):
        self.pasta = os.path.join(pasta_musica, 'Gravações', 'Tentativas')
        self.lixeira = os.path.join(self.pasta, 'Lixeira')
        self.tmp = os.path.join(self.pasta, '.gravando')
        self.exportados = os.path.join(pasta_musica, 'Gravações', 'Exportados')

    def _fichas(self, pasta):
        out = []
        for p in glob.glob(os.path.join(pasta, '*.json')):
            try:
                f = json.load(open(p, encoding='utf-8'))
                f['_json'] = p
                out.append(f)
            except Exception:
                pass
        return out

    def listar(self):
        fs = [f for f in self._fichas(self.pasta) if os.path.exists(os.path.join(self.pasta, f.get('arquivo', '')))]
        return sorted(fs, key=lambda f: f.get('criada', ''), reverse=True)

    def achar(self, tid):
        for f in self.listar():
            if f['id'] == tid:
                return f
        raise KeyError(f'tentativa não encontrada: {tid}')

    def proximo_numero(self, parte):
        nums = [f.get('numero', 0) for f in self._fichas(self.pasta) + self._fichas(self.lixeira) if f.get('parte') == parte]
        return max(nums, default=0) + 1

    def salvar(self, audio, parte, nome_parte, inicio_s, extra):
        os.makedirs(self.pasta, exist_ok=True)
        agora = datetime.datetime.now()
        num = self.proximo_numero(parte)
        base = _seguro(f'{agora:%Y-%m-%d %Hh%M} - {nome_parte} - tentativa {num}')
        caminho = os.path.join(self.pasta, base + '.flac')
        sf.write(caminho, audio, SR, subtype='PCM_24')
        canais = 1 if audio.ndim == 1 else audio.shape[1]
        pico = float(np.max(np.abs(audio))) if len(audio) else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if len(audio) else 0.0
        ficha = {
            'versao': 1, 'id': base, 'numero': num, 'nome': f'{NOME_CURTO.get(parte, nome_parte)} {num}',
            'parte': parte, 'nome_parte': nome_parte, 'criada': agora.isoformat(timespec='seconds'),
            'arquivo': base + '.flac', 'taxa': SR, 'canais': canais, 'duracao_s': round(len(audio) / SR, 3),
            'inicio_s': round(float(inicio_s), 5), 'deslocamento_ms': 0.0, 'favorita': False,
            'pico_db': round(20 * np.log10(max(pico, 1e-9)), 1), 'rms_db': round(20 * np.log10(max(rms, 1e-9)), 1),
            'picos': base64.b64encode(picos(audio).tobytes()).decode('ascii'),
        }
        ficha.update(extra)
        json.dump(ficha, open(os.path.join(self.pasta, base + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        return ficha

    def atualizar(self, tid, **campos):
        f = self.achar(tid)
        caminho = f.pop('_json')
        for k, v in campos.items():
            if k in ('nome', 'favorita', 'deslocamento_ms'):
                f[k] = v
        json.dump(f, open(caminho, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        return f

    def apagar(self, tid):
        f = self.achar(tid)
        os.makedirs(self.lixeira, exist_ok=True)
        for p in (os.path.join(self.pasta, f['arquivo']), f['_json']):
            shutil.move(p, os.path.join(self.lixeira, os.path.basename(p)))
        return f

    def restaurar(self, tid):
        for f in self._fichas(self.lixeira):
            if f['id'] == tid:
                for nome in (f['arquivo'], os.path.basename(f['_json'])):
                    shutil.move(os.path.join(self.lixeira, nome), os.path.join(self.pasta, nome))
                return f
        raise KeyError(f'tentativa não está na lixeira: {tid}')

    def audio(self, ficha):
        x, sr = sf.read(os.path.join(self.pasta, ficha['arquivo']), dtype='float32', always_2d=True)
        assert sr == SR, sr
        return x

    def resgatar(self):
        """Gravação que ficou pela metade (o app fechou no meio): vai para Tentativas/Resgatadas, sem alinhamento."""
        destino = os.path.join(self.pasta, 'Resgatadas')
        movidos = []
        for p in glob.glob(os.path.join(self.tmp, '*.wav')):
            try:
                os.makedirs(destino, exist_ok=True)
                shutil.move(p, os.path.join(destino, os.path.basename(p)))
                movidos.append(os.path.basename(p))
            except OSError:
                pass
        return movidos
