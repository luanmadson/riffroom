# -*- coding: utf-8 -*-
# Motor de áudio da Sala de Ensaio (sounddevice/PortAudio):
#   - saída: mistura as partes da banda (volume e liga/desliga suavizados), as tentativas escolhidas no lugar
#     da parte original, a contagem e o metrônomo; trecho repetido; começa, para e pula sem estalo;
#   - entrada: a interface de áudio (a GT-100, se estiver ligada, ou a escolhida), sempre aberta para o medidor;
#     ao gravar, guarda o áudio e a hora de cada bloco.
# As horas dos blocos de entrada e de saída servem para alinhar a gravação com a música (tentativas.alinhar).
import collections, logging, math, os, queue, re, threading, time
import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import butter, sosfilt

SR = 44100
BLOCO_SAIDA = 2048
BLOCO_ENTRADA = 1024
FADE = 256                                   # ~6 ms de rampa para começar, parar e emendar sem estalo
SOBE = np.linspace(0.0, 1.0, FADE, dtype=np.float32)
DESCE = SOBE[::-1].copy()
RUINS = ('Digital Output', 'SPDIF', 'S/PDIF', 'Sound Mapper', 'Primary Sound', 'Driver de som', 'Mapeador de som')
log = logging.getLogger('sala_de_ensaio')


def som_clique(nivel_db):
    """Clique curto e seco (ruído agudo com um 'ping'), para a contagem e o metrônomo."""
    L = int(0.06 * SR); t = np.arange(L) / SR
    rng = np.random.default_rng(7)
    ruido = sosfilt(butter(4, 5500, 'hp', fs=SR, output='sos'), rng.standard_normal(L)) * np.exp(-t / 0.018)
    ping = np.sin(2 * np.pi * 2200 * t) * np.exp(-t / 0.006)
    x = 0.8 * ruido / np.abs(ruido).max() + 0.35 * ping
    return (10 ** (nivel_db / 20) * x / np.abs(x).max()).astype(np.float32)


def sem_porta(nome):
    """Tira o número da porta USB que o Windows põe no nome: 'PRIMARY (3- GT-100 Ver2-1)' -> 'PRIMARY (GT-100 Ver2-1)'."""
    return re.sub(r'\(\d+- ', '(', nome or '').strip()


def placa(nome):
    """O aparelho, sem o nome da entrada ou da saída: 'Alto-falantes (Realtek(R) Audio)' -> 'Realtek(R) Audio'."""
    n = sem_porta(nome)
    i = n.find('(')
    return n[i + 1:].strip().rstrip(')').strip() if i >= 0 else n


def apelido(nome):
    """Nome curto para a tela: 'Alto-falantes (Realtek(R) Audio)' -> 'Alto-falantes'; 'PRIMARY (GT-100 Ver2-1)' -> 'GT-100 Ver2-1'."""
    if not nome:
        return ''
    n = sem_porta(nome)
    i = n.find('(')
    if i < 0:
        return n
    ponto = n[:i].strip()
    genericos = ('analog', 'line', 'linha', 'entrada', 'input', 'output', 'saída', 'in ', 'out ')
    return placa(n) if (not ponto or ponto.isupper() or ponto.lower().startswith(genericos)) else ponto


def mesmo_nome(a, b):
    """Compara nomes de aparelhos sem o número da porta USB (o MME corta os nomes em 31 letras: vale o começo)."""
    x, y = sem_porta(a), sem_porta(b)
    if x == y:
        return True
    k = min(len(x), len(y))
    return k >= 24 and x[:k] == y[:k]


def eh_gt100(nome):
    return 'GT-100' in (nome or '')


def achar_aparelhos(modo_saida, entrada=None):
    """Para cada sistema de áudio (WASAPI e, de reserva, MME): a entrada e a saída.
    Entrada: a escolhida (pelo nome); sem escolha, a GT-100 (PRIMARY) se estiver ligada, senão a entrada padrão do Windows.
    Saída 'pc': a padrão do Windows (menos as digitais, que não soam); 'interface': a saída do mesmo aparelho da entrada."""
    devs = sd.query_devices()
    opcoes = []
    for api in ('Windows WASAPI', 'MME'):
        ia = next((i for i, h in enumerate(sd.query_hostapis()) if h['name'] == api), None)
        if ia is None:
            continue
        h = sd.query_hostapis(ia)
        lista = [(i, d) for i, d in enumerate(devs) if d['hostapi'] == ia]
        ents = [i for i, d in lista if d['max_input_channels'] >= 1 and not any(r in d['name'] for r in RUINS)]
        if entrada:
            ent = next((i for i in ents if mesmo_nome(devs[i]['name'], entrada)), None)
        else:
            ent = next((i for i in ents if eh_gt100(devs[i]['name']) and 'PRIMARY' in devs[i]['name']), None)
            if ent is None and h['default_input_device'] in ents:
                ent = h['default_input_device']
        saidas = [i for i, d in lista if d['max_output_channels'] >= 2]
        da_interface = None
        if ent is not None:
            pl, nome_ent = placa(devs[ent]['name']), sem_porta(devs[ent]['name'])
            mesmas = [i for i in saidas if placa(devs[i]['name']) == pl]
            iguais = [i for i in mesmas if sem_porta(devs[i]['name']) == nome_ent]
            da_interface = (iguais or mesmas or [None])[0]
        pcs = [i for i in saidas if not any(r in devs[i]['name'] for r in RUINS)]
        padrao = h['default_output_device']
        if modo_saida == 'interface' and da_interface is not None:
            sai = da_interface
        elif padrao in pcs:
            sai = padrao
        else:
            pref = [i for i in pcs if any(k in devs[i]['name'] for k in ('Alto-falantes', 'Speakers', 'Fone', 'Headphone'))]
            sai = (pref or pcs or [None])[0]
        opcoes.append({'api': api, 'entrada': ent, 'saida': sai, 'saida_interface': da_interface,
                       'entradas': [sem_porta(devs[i]['name']) for i in ents]})
    return opcoes


class Parte:
    def __init__(self, pid, nome, dados, ganho):
        self.id, self.nome, self.dados, self.ganho = pid, nome, dados, ganho
        self.ligada = True
        self.volume_db = 0.0
        self.fonte = 'original'   # 'original' | 'tentativa' | 'ambos'
        self.tentativa = None     # id da tentativa usada em 'tentativa' e 'ambos'
        self.go = 0.0             # ganho aplicado agora na parte original (suavizado bloco a bloco)
        self.gt = 0.0             # ... e na tentativa

    def alvos(self):
        v = 10 ** (self.volume_db / 20) if self.ligada else 0.0
        go = self.ganho * v if self.fonte in ('original', 'ambos') else 0.0
        gt = v if (self.fonte in ('tentativa', 'ambos') and self.tentativa) else 0.0
        return go, gt


class Captura:
    """Grava a entrada num WAV temporário (thread própria) e guarda as horas dos blocos de entrada e de saída."""

    def __init__(self, pasta_tmp):
        os.makedirs(pasta_tmp, exist_ok=True)
        self.caminho = os.path.join(pasta_tmp, time.strftime('%Y%m%d-%H%M%S') + '.wav')
        self.arq = sf.SoundFile(self.caminho, 'w', samplerate=SR, channels=2, subtype='FLOAT')
        self.fila = queue.Queue()
        self.entrada = []          # (hora do bloco, amostras até o fim do bloco)
        self.saida = []            # (hora em que o bloco soa, quadro da música no começo do bloco)
        self.n = 0
        self.th = threading.Thread(target=self._escrever, daemon=True, name='gravador')
        self.th.start()

    def receber(self, dados, hora):
        self.n += len(dados)
        self.entrada.append((hora, self.n))
        self.fila.put(dados.copy())

    def _escrever(self):
        while True:
            x = self.fila.get()
            if x is None:
                break
            try:
                self.arq.write(x)
            except Exception:
                log.exception('gravador')
        self.arq.close()

    def fechar(self):
        self.fila.put(None)
        self.th.join(timeout=60)


class Motor:
    def __init__(self):
        self.lock = threading.RLock()
        self.musica = None
        self.n_total = 0
        self.partes = []
        self.tent = {}                     # id -> {'dados': float32 [n, c], 'inicio': quadro}
        self.pos = 0
        self.tocando = False
        self.trecho = None                 # (a, b) em quadros
        self.trecho_ligado = False
        self.contagem = True
        self.metronomo = False
        self.volume_geral_db = 0.0
        self.cliques = (np.zeros(0, np.int64), [])
        self.metro = (np.zeros(0, np.int64), [])
        self.cmds = collections.deque()
        self.saida = self.entrada = None
        self.api = self.api_entrada = self.nome_saida = self.nome_entrada = None
        self.atraso_entrada = 0.0
        self.modo_saida = 'pc'
        self.entrada_preferida = None      # nome da entrada escolhida na tela (None: automática)
        self.entradas = []                 # nomes das entradas disponíveis, para o menu
        self.tem_saida_interface = False
        self.canais_entrada = 2
        self.erro = None
        self.captura = None
        self.ultimos = collections.deque(maxlen=64)
        # histórico (hora, amostras) das duas streams, para medir o relógio de cada aparelho ao longo da sessão
        self.hist_entrada = collections.deque(maxlen=3000)
        self.hist_saida = collections.deque(maxlen=3000)
        self.n_entrada = 0; self.n_saida = 0
        self.pico = 0.0; self.som2 = 0.0; self.nsom = 0
        self.parar_em = None
        self.fim = False                   # chegou ao fim (música, trecho gravado ou cliques do "Acertar o tempo")
        self.contagem_ate = None           # quadro em que a contagem desta vez termina (para a tela)
        self.virtual = None                # "Acertar o tempo": só cliques, sem a música
        self._parar_pedido = None
        self._salto = None
        self._tocando_bloco = False        # o que o bloco de áudio está fazendo (self.tocando é o que a tela pediu)
        self._fi = None                    # âncora da rampa de entrada (onde a música recomeçou)
        self._pos_antes = 0
        self.buf = np.zeros((16384, 2), np.float32)
        self.som_forte, self.som_fraco = som_clique(-14), som_clique(-20)
        self.som_cont1, self.som_cont = som_clique(-16), som_clique(-21)
        self.som_calib = som_clique(-10)

    # ------------------------------------------------------------ aparelhos
    def _fechar_streams(self):
        for s in (self.saida, self.entrada):
            if s is not None:
                try:
                    s.abort(); s.close()
                except Exception:
                    log.exception('fechar stream')
        self.saida = self.entrada = None
        self.nome_saida = self.nome_entrada = None
        self.hist_entrada.clear(); self.hist_saida.clear()
        self.n_entrada = 0; self.n_saida = 0

    def relogios(self, janela_s=1200, minimo_s=90):
        """Segundos por amostra da entrada e da saída, medidos ao longo da sessão (as streams ficam sempre abertas).
        Devolve (s_entrada, s_saida) ou None se ainda não há tempo suficiente para uma medida firme."""
        from tentativas import _reta
        out = []
        for h in (list(self.hist_entrada), list(self.hist_saida)):
            if len(h) < 20:
                return None
            fim = h[-1][0]
            h = [x for x in h if x[0] >= fim - janela_s]
            if h[-1][0] - h[0][0] < minimo_s:
                return None
            _, s = _reta([x[1] for x in h], [x[0] for x in h])
            out.append(s)
        return tuple(out)

    def abrir(self, modo_saida=None, entrada=False):
        """Abre a saída e a entrada. entrada: nome da entrada escolhida, None para automática, False para manter."""
        with self.lock:
            if modo_saida:
                self.modo_saida = 'interface' if modo_saida == 'gt100' else modo_saida
            if entrada is not False:
                self.entrada_preferida = entrada or None
            self._fechar_streams()
            self.erro = None
            opcoes = achar_aparelhos(self.modo_saida, self.entrada_preferida)
            self.entradas = opcoes[0]['entradas'] if opcoes else []
            self.tem_saida_interface = any(op['saida_interface'] is not None for op in opcoes)
            for op in opcoes:
                if op['saida'] is None:
                    continue
                extra = sd.WasapiSettings(auto_convert=True) if op['api'] == 'Windows WASAPI' else None
                try:
                    s = sd.OutputStream(device=op['saida'], samplerate=SR, channels=2, dtype='float32', blocksize=BLOCO_SAIDA,
                                        latency='high', extra_settings=extra, callback=self._cb_saida)
                    s.start()
                except Exception as e:
                    log.warning('saída %s (%s) não abriu: %s', op['saida'], op['api'], e)
                    continue
                self.saida, self.api = s, op['api']
                self.nome_saida = sd.query_devices(op['saida'])['name']
                log.info('saída: %s (%s), latência %.1f ms', self.nome_saida, self.api, s.latency * 1000)
                break
            for op in opcoes:
                if op['entrada'] is None:
                    continue
                extra = sd.WasapiSettings(auto_convert=True) if op['api'] == 'Windows WASAPI' else None
                canais = max(1, min(2, sd.query_devices(op['entrada'])['max_input_channels']))
                try:
                    e = sd.InputStream(device=op['entrada'], samplerate=SR, channels=canais, dtype='float32', blocksize=BLOCO_ENTRADA,
                                       latency='high', extra_settings=extra, callback=self._cb_entrada)
                    e.start()
                except Exception as ex:
                    log.warning('entrada %s (%s) não abriu: %s', op['entrada'], op['api'], ex)
                    continue
                self.entrada, self.api_entrada = e, op['api']
                self.canais_entrada = canais
                self.nome_entrada = sd.query_devices(op['entrada'])['name']
                self.atraso_entrada = max(0.0, float(e.latency) - BLOCO_ENTRADA / SR)
                log.info('entrada: %s (%s), latência %.1f ms', self.nome_entrada, self.api_entrada, e.latency * 1000)
                break
            if self.saida is None:
                self.erro = 'Nenhuma saída de som abriu. Confira a caixa de som e clique em "Procurar de novo".'

    def procurar(self):
        """Fecha tudo e pede ao PortAudio a lista nova de aparelhos (para achar a interface ligada depois)."""
        with self.lock:
            self.tocando = False
            self._fechar_streams()
            try:
                sd._terminate(); sd._initialize()
            except Exception:
                log.exception('reiniciar PortAudio')
            self.abrir()

    def conferir(self):
        """Confere se as streams continuam vivas (a GT-100 pode ter sido desligada). Devolve True se algo mudou."""
        mudou = False
        e = self.entrada
        if e is not None and not e.active:
            log.warning('a entrada parou (interface desligada?)')
            try:
                e.close()
            except Exception:
                pass
            self.entrada = None; self.nome_entrada = None; mudou = True
        s = self.saida
        if s is not None and not s.active:
            log.warning('a saída parou; reabrindo')
            self.abrir(); mudou = True
        return mudou

    # ------------------------------------------------------------ música e tentativas
    def carregar(self, mus):
        with self.lock:
            self.tocando = False
            self._tocando_bloco = False
            self.cmds.clear()
            self.captura = None
            self.virtual = None
            self.musica = mus
            self.n_total = mus.n
            self.partes = [Parte(p['id'], p['nome'], p['dados'], p['ganho']) for p in mus.partes]
            self.tent = {}
            self.pos = 0
            self.trecho = None; self.trecho_ligado = False
            q = np.round(mus.pulsos * SR).astype(np.int64)
            ok = (q >= 0) & (q < mus.n) & (mus.pulso_compasso > 0)
            self.metro = (q[ok], [self.som_forte if f else self.som_fraco for f in mus.pulso_forte[ok]])

    def parte(self, pid):
        for p in self.partes:
            if p.id == pid:
                return p
        raise KeyError(pid)

    def por_tentativa(self, tid, dados, inicio_s):
        self.tent[tid] = {'dados': dados, 'inicio': int(round(inicio_s * SR))}

    # ------------------------------------------------------------ transporte (chamado pela tela)
    def posicao(self):
        """Posição (s) do que está soando agora: o PortAudio diz quando cada bloco toca."""
        if not self.tocando:
            return self.pos / SR
        try:
            agora = self.saida.time
        except Exception:
            return self.pos / SR
        primeiro = None
        for dac, frames, segs in reversed(self.ultimos):
            if segs:
                primeiro = segs[0][1]
            if dac <= agora:
                if not segs:
                    return (primeiro if primeiro is not None else self.pos) / SR
                d = (agora - dac) * SR
                for off, q, n, _, _ in segs:
                    if off <= d < off + n:
                        return (q + d - off) / SR
                off, q, n, _, _ = segs[-1]
                return (q + n) / SR
        return (primeiro if primeiro is not None else self.pos) / SR

    def tocar(self, de=None, sem_contagem=False):
        with self.lock:
            if self.musica is None:
                return
            mus = self.musica
            p = int(round(de * SR)) if de is not None else self.pos
            tr = self.trecho if self.trecho_ligado else None
            if tr and not (tr[0] <= p < tr[1] - SR // 2):
                p = tr[0]
            if p >= self.n_total - SR:
                p = 0
            ini, cq, cs, ate = p, [], [], None
            if self.contagem and not sem_contagem:
                t = p / SR
                B = mus.m.get('compassos') or [0.0]
                if t < B[0] + 0.05:
                    # começo da música: vale a contagem da própria faixa (na bateria); sem a bateria, o app faz os cliques
                    ini = 0
                    bateria = next((x for x in self.partes if x.id == 'bateria'), None)
                    ouvindo_bateria = bateria is not None and bateria.ligada and bateria.fonte != 'tentativa'
                    if not ouvindo_bateria:
                        for k, c in enumerate(mus.m.get('contagem', [])):
                            cq.append(int(c * SR)); cs.append(self.som_cont1 if k == 0 else self.som_cont)
                    ate = int(B[0] * SR)
                else:
                    pul = mus.pulsos_antes(t, 4)
                    if pul:
                        for k, c in enumerate(pul):
                            cq.append(int(round(c * SR))); cs.append(self.som_cont1 if k == 0 else self.som_cont)
                        ini = max(0, int((pul[0] - 0.25) * SR))
                        ate = p
            parar_em = None
            if self.captura is not None and tr:
                parar_em = tr[1]      # gravando com trecho: uma passada só
            self.virtual = None
            self.fim = False
            self.cmds.append(('tocar', ini, np.array(cq, np.int64), cs, parar_em, ate))
            self.tocando = True       # a tela já mostra "tocando"; o bloco seguinte aplica

    def pausar(self):
        with self.lock:
            if not self.tocando:
                return
            pos = int(self.posicao() * SR)
            self.cmds.append(('parar', pos))
            self.tocando = False

    def ir(self, t):
        with self.lock:
            q = max(0, min(int(round(t * SR)), max(0, self.n_total - 1)))
            if self.tocando:
                self.cmds.append(('ir', q))
            else:
                self.pos = q

    def definir_trecho(self, a, b, ligado=True):
        with self.lock:
            if a is None or b is None:
                self.trecho = None; self.trecho_ligado = False
                return
            qa, qb = int(round(a * SR)), int(round(b * SR))
            if qb - qa < SR // 2:
                raise ValueError('O trecho precisa ter pelo menos meio segundo.')
            self.trecho = (max(0, qa), min(self.n_total, qb))
            self.trecho_ligado = ligado

    # ------------------------------------------------------------ gravação e "Acertar o tempo"
    def sem_entrada(self):
        if self.entrada_preferida:
            return (f'A entrada "{apelido(self.entrada_preferida)}" não foi encontrada. Confira o cabo USB e se ela está '
                    f'ligada, e clique em Entrada > Procurar de novo.')
        return 'Nenhuma entrada de áudio foi encontrada. Ligue a interface (ex.: a GT-100) e clique em Entrada > Procurar de novo.'

    def gravar(self, pasta_tmp):
        with self.lock:
            if self.entrada is None:
                raise RuntimeError(self.sem_entrada())
            if self.captura is not None:
                return
            de = None
            if self.tocando:  # já tocando: recomeça no começo do compasso atual, com a contagem antes
                de = self.musica.inicio_do_compasso(self.posicao())
                self.pausar()
                self.cmds.append(('esperar',))
            self.captura = Captura(pasta_tmp)
            self.tocar(de=de)

    def calibrar(self, pasta_tmp, n=8, inicio=1.5, passo=0.6):
        with self.lock:
            if self.entrada is None:
                raise RuntimeError(self.sem_entrada())
            if self.tocando:
                self._pos_antes = int(self.posicao() * SR)
                self.pausar()
                self.cmds.append(('esperar',))
            else:
                self._pos_antes = self.pos
            cliques = [inicio + i * passo for i in range(n)]
            self.virtual = {'n': int((inicio + n * passo + 0.8) * SR), 'cliques_s': cliques}
            self.captura = Captura(pasta_tmp)
            self.fim = False
            self.cmds.append(('tocar', 0, np.array([int(c * SR) for c in cliques], np.int64), [self.som_calib] * n, None, None))
            self.tocando = True

    def encerrar_captura(self):
        """Para a gravação (ou o "Acertar o tempo") e devolve a captura, com o arquivo já fechado."""
        with self.lock:
            cap = self.captura
            self.captura = None
            if self.tocando:
                self.pausar()
            era_virtual = self.virtual is not None
            if era_virtual:
                self.cmds.append(('voltar', getattr(self, '_pos_antes', 0)))
        if cap is not None:
            cap.fechar()
        return cap, era_virtual

    # ------------------------------------------------------------ medidor
    def ler_nivel(self):
        pk, s2, n = self.pico, self.som2, self.nsom
        self.pico = 0.0; self.som2 = 0.0; self.nsom = 0
        if self.entrada is None:
            return None
        return (20 * math.log10(max(pk, 1e-6)), 10 * math.log10(max(s2 / n, 1e-12)) if n else -120.0, pk >= 0.999)

    # ------------------------------------------------------------ blocos de áudio (thread do PortAudio)
    def _cb_entrada(self, indata, frames, tinfo, status):
        try:
            pk = float(np.max(np.abs(indata)))
            if pk > self.pico:
                self.pico = pk
            ch = indata[:, 0]
            self.som2 += float(np.dot(ch, ch)); self.nsom += frames
            self.n_entrada += frames
            if (self.n_entrada // frames) % 20 == 0:
                self.hist_entrada.append((tinfo.currentTime, self.n_entrada))
            c = self.captura
            if c is not None:
                c.receber(indata if indata.shape[1] == 2 else np.repeat(indata[:, :1], 2, axis=1), tinfo.currentTime)
        except Exception:
            log.exception('bloco de entrada')

    def _plano(self, frames):
        """Aplica os comandos da tela e decide que pedaços da música tocam neste bloco:
        [(posição no bloco, quadro da música, tamanho, âncora da rampa de entrada, âncora da rampa de saída)].
        As âncoras são quadros da música: a rampa de entrada vai de fi a fi+FADE; a de saída, de fo-FADE a fo.
        Assim a rampa continua certa mesmo quando atravessa a divisa entre dois blocos."""
        while self.cmds:
            c = self.cmds.popleft()
            if c[0] == 'tocar':
                _, ini, cq, cs, parar_em, ate = c
                self.pos = ini; self.cliques = (cq, cs); self.parar_em = parar_em; self.contagem_ate = ate
                self._tocando_bloco = True; self._parar_pedido = None; self._salto = None; self._fi = ini
            elif c[0] == 'parar':
                if self._tocando_bloco:
                    self._parar_pedido = c[1]
                else:
                    self.pos = c[1]
            elif c[0] == 'ir':
                if self._tocando_bloco:
                    self._salto = c[1]
                else:
                    self.pos = c[1]
            elif c[0] == 'voltar':
                self.virtual = None
                self.pos = c[1]
            elif c[0] == 'esperar':
                break       # o 'parar' deste bloco precisa soar antes do próximo 'tocar'
        if not self._tocando_bloco:
            return []
        segs, p, off = [], self.pos, 0
        if self._parar_pedido is not None:
            n = min(FADE, frames)
            segs.append((0, p, n, self._fi, p + n))
            self._tocando_bloco = False
            self.pos = self._parar_pedido; self._parar_pedido = None
            self.contagem_ate = None
            return segs
        if self._salto is not None:
            n = min(FADE, frames)
            segs.append((0, p, n, self._fi, p + n))
            p, off = self._salto, n
            self._fi = p
            self._salto = None
        total = self.virtual['n'] if self.virtual else self.n_total
        while off < frames:
            tr = self.trecho if (self.trecho_ligado and self.captura is None and self.virtual is None) else None
            if tr and p >= tr[1]:   # fim do trecho: volta ao começo dele
                p = tr[0]; self._fi = p
            lim = total if not tr else min(total, tr[1])
            if self.parar_em is not None:
                lim = min(lim, self.parar_em)
            n = min(frames - off, lim - p)
            if n > 0:
                segs.append((off, p, n, self._fi, lim))
                p += n; off += n
                continue
            if tr and p >= tr[1]:
                continue
            self._tocando_bloco = False
            self.tocando = False
            self.fim = True
            break
        self.pos = p
        return segs

    def _misturar(self, out, parte, segs, frames):
        go1, gt1 = parte.alvos()
        go0, gt0 = parte.go, parte.gt
        if go0 or go1:
            rampa = None if go0 == go1 else (np.linspace(go0, go1, frames, dtype=np.float32) / np.float32(32768))[:, None]
            k = np.float32(go1 / 32768)
            for off, q, n, _, _ in segs:
                x = parte.dados[q:q + n]
                m = len(x)
                if m:
                    out[off:off + m] += x * (k if rampa is None else rampa[off:off + m])
        ta = self.tent.get(parte.tentativa) if parte.tentativa else None
        if (gt0 or gt1) and ta is not None:
            d, ini = ta['dados'], ta['inicio']
            rampa = None if gt0 == gt1 else np.linspace(gt0, gt1, frames, dtype=np.float32)[:, None]
            for off, q, n, _, _ in segs:
                i0 = q - ini
                a0, a1 = max(i0, 0), min(i0 + n, len(d))
                if a1 <= a0:
                    continue
                o0 = off + (a0 - i0)
                g = np.float32(gt1) if rampa is None else rampa[o0:o0 + a1 - a0]
                out[o0:o0 + a1 - a0] += d[a0:a1] * g
        parte.go, parte.gt = go1, gt1

    def _por_cliques(self, out, segs):
        fontes = [self.cliques]
        if self.metronomo and self.virtual is None:
            fontes.append(self.metro)
        L = len(self.som_forte)
        for qs, sons in fontes:
            if len(qs) == 0:
                continue
            for off, q, n, _, _ in segs:
                i0 = int(np.searchsorted(qs, q - L, 'left')); i1 = int(np.searchsorted(qs, q + n, 'left'))
                for k in range(i0, i1):
                    c = int(qs[k]); s = sons[k]
                    a0, a1 = max(c, q), min(c + len(s), q + n)
                    if a1 > a0:
                        out[off + a0 - q:off + a1 - q] += s[a0 - c:a1 - c, None]

    def _cb_saida(self, outdata, frames, tinfo, status):
        try:
            segs = self._plano(frames)
            out = self.buf[:frames]
            out.fill(0.0)
            if segs:
                if self.virtual is None:
                    for p in self.partes:
                        self._misturar(out, p, segs, frames)
                self._por_cliques(out, segs)
                for off, q, n, fi, fo in segs:
                    if fi is not None:
                        a, b = max(q, fi), min(q + n, fi + FADE)
                        if b > a:
                            out[off + a - q:off + b - q] *= SOBE[a - fi:b - fi, None]
                    if fo is not None:
                        a, b = max(q, fo - FADE), min(q + n, fo)
                        if b > a:
                            out[off + a - q:off + b - q] *= DESCE[a - fo + FADE:b - fo + FADE, None]
                g = 10 ** (self.volume_geral_db / 20)
                if g != 1.0:
                    out *= np.float32(g)
                np.clip(out, -1.0, 1.0, out=out)
            outdata[:] = out
            self.n_saida += frames
            if (self.n_saida // frames) % 10 == 0:
                self.hist_saida.append((tinfo.outputBufferDacTime, self.n_saida))
            self.ultimos.append((tinfo.outputBufferDacTime, frames, segs))
            c = self.captura
            if c is not None and len(segs) == 1 and segs[0][2] == frames:
                c.saida.append((tinfo.outputBufferDacTime, segs[0][1]))
        except Exception:
            log.exception('bloco de saída')
            outdata.fill(0)
