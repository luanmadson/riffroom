# -*- coding: utf-8 -*-
# Sala de Ensaio: tocar e gravar junto com a banda separada (faixas de cada instrumento), com o acorde e o pulso na tela.
# Abre um servidor local (só em 127.0.0.1) e a janela do Edge em modo app; fecha sozinho quando a janela fecha.
#
# Uso (pelo atalho): pythonw.exe -I -X utf8 sala_de_ensaio.py [--musica "<pasta da música>"]
#   --biblioteca PASTA  junta uma pasta de músicas à lista (fica guardada)    --configurar  só guarda e sai
#   --sem-janela        não abre a janela e não fecha sozinho (para testes)   --porta N     (padrão 8737)
import argparse, datetime, json, logging, os, queue, re, subprocess, sys, threading, time, traceback, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSAO = '1.0.0'
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
DADOS = os.environ.get('SALA_DE_ENSAIO_DADOS') or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
                                                                 'SalaDeEnsaio')
LOG = os.path.join(DADOS, 'sala_de_ensaio.log')
CONFIG = os.path.join(DADOS, 'config.json')
PORTA = 8737
WEB = os.path.join(AQUI, 'web')
TIPOS = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json'}
log = logging.getLogger('sala_de_ensaio')


def preparar_log():
    os.makedirs(DADOS, exist_ok=True)
    if os.path.exists(LOG) and os.path.getsize(LOG) > 2_000_000:
        os.replace(LOG, LOG + '.antigo')
    h = logging.FileHandler(LOG, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s: %(message)s'))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:  # pythonw: sem console
        f = open(LOG, 'a', encoding='utf-8')
        sys.stdout = sys.stderr = f
    threading.excepthook = lambda a: log.error('erro na thread %s', a.thread.name if a.thread else '?',
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


import numpy as np              # noqa: E402
import motor as M               # noqa: E402  (depois do sys.path)
import musica as MU             # noqa: E402
import preparar_musica as PM    # noqa: E402
import tentativas as T          # noqa: E402

SR = M.SR


def dica_nivel(pico_db, gt100=False):
    ganho = 'o PATCH LEVEL' if gt100 else 'o ganho da entrada'
    if pico_db > -1:
        return f'estourou: abaixe {ganho}'
    if pico_db < -30:
        return f'muito baixo: suba {ganho}'
    if pico_db < -20:
        return 'um pouco baixo'
    return 'nível bom'


def _tempo(s):
    s = max(0, int(round(s)))
    return f'{s // 60}:{s % 60:02d}'


class App:
    def __init__(self, manter=False):
        self.manter = manter
        self.cfg = ler_cfg()
        MU.BIBLIOTECAS[:] = bibliotecas(self.cfg)
        self.lock = threading.RLock()
        self.clientes = []
        self.motor = M.Motor()
        self.mus = None
        self.tent = None
        self.alvo = None
        self.gravando_parte = None
        self.carregando = False
        self.ocupado = None
        self.ultima_calibracao = None
        self.musicas = MU.listar()
        self.fim = threading.Event()
        self.finalizando = None
        self.teve_cliente = False
        self.sem_cliente_desde = time.time()
        self.dialogo = threading.Lock()
        self.motor.contagem = bool(self.cfg.get('contagem', True))
        self.motor.metronomo = bool(self.cfg.get('metronomo', False))
        self.motor.abrir(self.cfg.get('modo_saida', 'pc'), entrada=self.cfg.get('entrada'))
        threading.Thread(target=self._tique, daemon=True, name='tique').start()

    # ------------------------------------------------------------ configuração
    def _salvar_cfg(self):
        salvar_cfg(self.cfg)

    def _chave(self):
        m = self.motor
        return f'{m.api_entrada or m.api}|{M.sem_porta(m.nome_saida or "-")}|{M.sem_porta(m.nome_entrada or "-")}'

    def _ajuste(self):
        return float(self.cfg.get('ajustes_ms', {}).get(self._chave(), 0.0))

    def _guardar_mix(self):
        if not self.mus:
            return
        m = self.motor
        self.cfg.setdefault('musicas', {})[self.mus.id] = {
            'alvo': self.alvo, 'volume_geral_db': m.volume_geral_db,
            'trecho': [m.trecho[0] / SR, m.trecho[1] / SR] if m.trecho else None, 'trecho_ligado': m.trecho_ligado,
            'partes': {p.id: {'ligada': p.ligada, 'volume_db': p.volume_db, 'fonte': p.fonte, 'tentativa': p.tentativa}
                       for p in m.partes}}
        self.cfg['contagem'] = m.contagem
        self.cfg['metronomo'] = m.metronomo
        self._salvar_cfg()

    def _restaurar_mix(self):
        st = self.cfg.get('musicas', {}).get(self.mus.id)
        m = self.motor
        if not st:  # primeira vez: "você vai tocar" a primeira parte da lista (desligada)
            self.alvo = self.mus.alvos[0]
            for p in m.partes:
                p.ligada = p.id != self.alvo
            return
        self.alvo = st.get('alvo') if st.get('alvo') in [p.id for p in m.partes] else self.mus.alvos[0]
        m.volume_geral_db = float(st.get('volume_geral_db', 0.0))
        existentes = {f['id'] for f in self.tent.listar()}
        for p in m.partes:
            s = st.get('partes', {}).get(p.id, {})
            p.ligada = bool(s.get('ligada', True))
            p.volume_db = float(s.get('volume_db', 0.0))
            tid = s.get('tentativa')
            if s.get('fonte', 'original') != 'original' and tid in existentes:
                try:
                    self._carregar_tentativa(tid)
                    p.fonte, p.tentativa = s['fonte'], tid
                except Exception:
                    log.exception('restaurar tentativa')
        if st.get('trecho'):
            try:
                m.definir_trecho(st['trecho'][0], st['trecho'][1], bool(st.get('trecho_ligado', False)))
            except ValueError:
                pass

    # ------------------------------------------------------------ eventos para a tela
    def novo_cliente(self):
        q = queue.Queue()
        with self.lock:
            self.clientes.append(q)
            self.teve_cliente = True
            self.sem_cliente_desde = None
        return q

    def remover_cliente(self, q):
        with self.lock:
            if q in self.clientes:
                self.clientes.remove(q)
            if not self.clientes:
                self.sem_cliente_desde = time.time()

    def publicar(self, tipo, dados, tique=False):
        msg = f'event: {tipo}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n'
        with self.lock:
            cl = list(self.clientes)
        for q in cl:
            if tique and q.qsize() > 10:
                continue
            q.put(msg)

    def publicar_estado(self):
        self.publicar('estado', self.estado())

    def aviso(self, texto, tipo='info', acao=None):
        log.info('aviso: %s', texto)
        self.publicar('aviso', {'texto': texto, 'tipo': tipo, 'acao': acao})

    def estado(self):
        m = self.motor
        partes = []
        if self.mus:
            cores = {p['id']: p['cor'] for p in self.mus.partes}
            for p in m.partes:
                partes.append({'id': p.id, 'nome': p.nome, 'cor': cores.get(p.id), 'ligada': p.ligada,
                               'volume_db': p.volume_db, 'fonte': p.fonte, 'tentativa': p.tentativa})
        chave = self._chave()
        return {
            'versao': VERSAO,
            'musica': {'id': self.mus.id, 'titulo': self.mus.titulo, 'artista': self.mus.m.get('artista')} if self.mus else None,
            'musicas': [{k: v for k, v in x.items() if k != 'pasta'} for x in self.musicas],
            'bibliotecas': list(MU.BIBLIOTECAS),
            'carregando': self.carregando, 'ocupado': self.ocupado,
            'tocando': m.tocando, 'gravando': m.captura is not None and m.virtual is None,
            'calibrando': m.virtual is not None, 'pos': m.posicao(),
            'trecho': [m.trecho[0] / SR, m.trecho[1] / SR] if m.trecho else None, 'trecho_ligado': m.trecho_ligado,
            'contagem': m.contagem, 'metronomo': m.metronomo, 'volume_geral_db': m.volume_geral_db,
            'alvo': self.alvo, 'alvos': self.mus.alvos if self.mus else [], 'gravando_parte': self.gravando_parte,
            'partes': partes,
            'aparelhos': {'entrada_ok': m.entrada is not None, 'entrada': m.nome_entrada, 'entrada_apelido': M.apelido(m.nome_entrada),
                          'gt100': M.eh_gt100(m.nome_entrada) or (m.entrada is None and M.eh_gt100(m.entrada_preferida)),
                          'entrada_escolhida': m.entrada_preferida, 'entradas': m.entradas,
                          'saida': m.nome_saida, 'saida_apelido': M.apelido(m.nome_saida), 'api': m.api,
                          'modo_saida': m.modo_saida, 'tem_saida_interface': m.tem_saida_interface, 'erro': m.erro},
            'ajuste_ms': self._ajuste(), 'calibrado': chave in self.cfg.get('ajustes_ms', {}),
            'ultima_calibracao': self.ultima_calibracao,
            'tour_visto': bool(self.cfg.get('tour_visto')),
        }

    def _tique(self):
        conf = 0.0
        while not self.fim.is_set():
            time.sleep(0.05)
            try:
                m = self.motor
                if m.fim:
                    m.fim = False
                    threading.Thread(target=self._chegou_ao_fim, daemon=True).start()
                niv = m.ler_nivel()
                d = {'t': round(m.posicao(), 3), 'tocando': m.tocando,
                     'gravando': m.captura is not None and m.virtual is None, 'calibrando': m.virtual is not None}
                if niv:
                    d['pico_db'], d['rms_db'], d['clip'] = round(niv[0], 1), round(niv[1], 1), niv[2]
                if m.tocando and m.contagem_ate is not None:
                    d['contagem_ate'] = m.contagem_ate / SR
                self.publicar('tique', d, tique=True)
                agora = time.time()
                if agora - conf > 2:
                    conf = agora
                    if m.conferir():
                        self.publicar_estado()
                        if m.entrada is None:
                            self.aviso('A entrada de áudio sumiu. Confira o cabo USB e clique em Entrada > Procurar de novo.', 'erro')
                with self.lock:
                    desde = self.sem_cliente_desde
                if not self.manter and desde is not None and agora - desde > (10 if self.teve_cliente else 120):
                    log.info('sem janela aberta: encerrando')
                    self.fim.set()
            except Exception:
                log.exception('tique')

    def _chegou_ao_fim(self):
        if self.motor.captura is not None:
            self.parar()
        else:
            self.publicar_estado()

    # ------------------------------------------------------------ músicas
    def atualizar_musicas(self):
        MU.BIBLIOTECAS[:] = bibliotecas(self.cfg)
        self.musicas = MU.listar()
        self.publicar_estado()

    def abrir_musica(self, mid):
        with self.lock:
            if self.motor.captura is not None:
                raise RuntimeError('Pare a gravação antes de trocar de música.')
            self.carregando = True
        self.publicar_estado()
        try:
            self.musicas = MU.listar()
            item = next((x for x in self.musicas if x['id'] == mid), None)
            if item is None:
                raise KeyError(f'música não encontrada: {mid}')
            if not item['preparada']:
                self.ocupado = 'Preparando a música (só na primeira vez)...'
                self.publicar_estado()
                try:
                    PM.preparar(item['pasta'], avisar=lambda s: log.info('preparar: %s', s))
                except (ValueError, FileNotFoundError) as e:
                    raise RuntimeError(f'Não consegui preparar "{item["titulo"]}": {e}')
                finally:
                    self.ocupado = None
                self.musicas = MU.listar()
            mus = MU.Musica(item['pasta'], mid)
            mus.dados_tela()
            tent = T.Tentativas(item['pasta'])
            resgatadas = tent.resgatar()
            with self.lock:
                self.motor.carregar(mus)
                self.mus, self.tent = mus, tent
                self._restaurar_mix()
                self.cfg['ultima_musica'] = mus.id
                self._salvar_cfg()
            log.info('música aberta: %s', mus.id)
            if resgatadas:
                self.aviso(f'{len(resgatadas)} gravação(ões) interrompida(s) foram para Tentativas/Resgatadas.', 'erro')
        finally:
            self.carregando = False
            self.publicar('musica', {'id': self.mus.id if self.mus else None})
            self.publicar_estado()

    def dados_musica(self):
        if not self.mus:
            raise RuntimeError('Nenhuma música aberta.')
        return self.mus.dados_tela()

    def adicionar_biblioteca(self):
        if not self.dialogo.acquire(blocking=False):
            raise RuntimeError('A janela de escolher a pasta já está aberta.')
        try:
            pasta = escolher_pasta('Escolha a pasta das suas músicas (com as faixas separadas)')
        finally:
            self.dialogo.release()
        if not pasta:
            return
        pasta = os.path.normpath(pasta)
        libs = self.cfg.setdefault('bibliotecas', bibliotecas(self.cfg))
        if not any(os.path.normcase(pasta) == os.path.normcase(b) for b in libs):
            libs.append(pasta)
            self._salvar_cfg()
        self.atualizar_musicas()
        dentro = [x for x in self.musicas if os.path.normcase(x['pasta']).startswith(os.path.normcase(pasta))]
        if dentro:
            self.aviso(f'{len(dentro)} música(s) encontrada(s) em {os.path.basename(pasta)}.', 'ok')
        else:
            self.aviso('Nenhuma música nessa pasta ainda. Cada música precisa de uma pasta própria com as faixas separadas '
                       '(drums, bass, vocals... ou bateria, baixo, voz...).', 'erro')

    # ------------------------------------------------------------ transporte
    def tocar(self, t=None):
        self.motor.tocar(t)
        self.publicar_estado()

    def pausar(self):
        if self.motor.captura is not None:
            return self.parar()
        self.motor.pausar()
        self.publicar_estado()

    def tocar_pausar(self):
        if self.motor.tocando:
            return self.pausar()
        return self.tocar()

    def ir(self, t):
        self.motor.ir(t)
        self.publicar_estado()

    def inicio(self):
        m = self.motor
        m.ir(m.trecho[0] / SR if (m.trecho and m.trecho_ligado) else 0.0)
        self.publicar_estado()

    def mudar_compasso(self, delta):
        if not self.mus:
            return
        B = self.mus.m.get('compassos', [])
        t = self.motor.posicao()
        if not B:
            return self.ir(max(0.0, t + 5 * delta))
        if t < B[0] - 0.05:
            alvo = B[0] if delta > 0 else 0.0
        else:
            import bisect
            i = bisect.bisect_right(B, t + 0.05) - 1
            if delta < 0 and t - B[i] > 0.6:
                delta += 1  # no meio do compasso: "voltar" vai para o começo dele
            j = max(0, min(len(B) - 2, i + delta))
            alvo = B[j] if not (delta < 0 and i + delta < 0) else 0.0
        self.ir(alvo)

    def trecho(self, c):
        m = self.motor
        if c.get('limpar'):
            m.definir_trecho(None, None)
        elif 'a' in c and 'b' in c:
            m.definir_trecho(float(c['a']), float(c['b']), bool(c.get('ligado', True)))
            if m.trecho_ligado and not m.tocando:
                m.ir(m.trecho[0] / SR)
        elif 'ligado' in c:
            if m.trecho is None:
                raise ValueError('Primeiro marque o trecho: arraste na linha do tempo, ou dê dois cliques numa parte da música.')
            m.trecho_ligado = bool(c['ligado'])
        self._guardar_mix()
        self.publicar_estado()

    def definir_parte(self, pid, c):
        p = self.motor.parte(pid)
        if 'ligada' in c:
            p.ligada = bool(c['ligada'])
        if 'volume_db' in c:
            p.volume_db = max(-40.0, min(6.0, float(c['volume_db'])))
        if 'fonte' in c:
            if c['fonte'] == 'original':
                p.fonte = 'original'
            else:
                tid = c.get('tentativa') or p.tentativa
                if not tid:
                    raise ValueError('Escolha a tentativa.')
                self._carregar_tentativa(tid)
                p.tentativa, p.fonte = tid, ('ambos' if c['fonte'] == 'ambos' else 'tentativa')
        self._guardar_mix()
        self.publicar_estado()

    def definir_modo(self, modo):
        ids = [p.id for p in self.motor.partes]
        if modo != 'ouvir' and modo not in ids:
            raise ValueError(f'parte desconhecida: {modo}')
        for p in self.motor.partes:
            p.ligada = (modo == 'ouvir') or p.id != modo
        if modo != 'ouvir':
            self.alvo = modo
        self._guardar_mix()
        self.publicar_estado()

    def opcoes(self, c):
        m = self.motor
        if 'contagem' in c:
            m.contagem = bool(c['contagem'])
        if 'metronomo' in c:
            m.metronomo = bool(c['metronomo'])
        if 'volume_geral_db' in c:
            m.volume_geral_db = max(-40.0, min(6.0, float(c['volume_geral_db'])))
        if 'alvo' in c and c['alvo'] in [p.id for p in m.partes]:
            self.alvo = c['alvo']
        if 'tour_visto' in c:
            self.cfg['tour_visto'] = bool(c['tour_visto'])
        self._guardar_mix()
        self._salvar_cfg()
        self.publicar_estado()

    # ------------------------------------------------------------ gravação
    def gravar(self, parte=None):
        if not self.mus:
            raise RuntimeError('Abra uma música primeiro.')
        if self.finalizando is not None and self.finalizando.is_alive():
            raise RuntimeError('Espere: ainda estou guardando a tentativa anterior.')
        self.gravando_parte = parte or self.alvo
        self.motor.gravar(self.tent.tmp)
        log.info('gravando %s', self.gravando_parte)
        self.publicar_estado()

    def gravar_parar(self):
        if self.motor.captura is not None:
            return self.parar()
        return self.gravar()

    def parar(self):
        m = self.motor
        if m.captura is None:
            m.pausar()
            self.publicar_estado()
            return
        virtual = dict(m.virtual) if m.virtual else None
        parte = self.gravando_parte
        cap, _ = m.encerrar_captura()
        self.gravando_parte = None
        self.ocupado = 'Medindo o tempo...' if virtual else 'Guardando a tentativa...'
        self.publicar_estado()
        self.finalizando = threading.Thread(target=self._finalizar, args=(cap, virtual, parte), daemon=True, name='finalizar')
        self.finalizando.start()

    def _finalizar(self, cap, virtual, parte):
        import soundfile as sf
        try:
            x, _ = sf.read(cap.caminho, dtype='float32', always_2d=True)
            dur = len(x) / SR
            relogios = self.motor.relogios()
            if relogios is None and dur < 60:
                al = T.alinhar(cap.entrada, cap.saida, self.motor.atraso_entrada)
                if al is not None:
                    al = (al[0], 1.0)   # gravação curta e sessão recém-aberta: a deriva medida seria só ruído
            else:
                al = T.alinhar(cap.entrada, cap.saida, self.motor.atraso_entrada, relogios)
            if al is None or len(x) < SR // 2:
                self.aviso('A gravação ficou curta demais e não foi guardada.', 'erro')
                return
            inicio, alfa = al
            deriva = (alfa - 1) * 1e6
            log.info('alinhamento: início %.1f quadros, deriva %.1f ppm (%s), %d blocos de entrada, %d de saída, %.1f s',
                     inicio, deriva, 'relógios da sessão' if relogios else 'só a gravação', len(cap.entrada), len(cap.saida), dur)
            if virtual:
                at = T.ataques(x)
                ataques_s = [(inicio + a * alfa) / SR for a in at]
                r = T.medir_ajuste(ataques_s, virtual['cliques_s'])
                if r is None:
                    self.ultima_calibracao = {'ok': False}
                    self.aviso('Não deu para medir: toque as cordas abafadas com força, junto com cada clique, e tente de novo.', 'erro')
                else:
                    r['ok'] = True
                    self.ultima_calibracao = r
                    self.cfg.setdefault('ajustes_ms', {})[self._chave()] = round(r['ms'], 1)
                    self._salvar_cfg()
                    log.info('acertar o tempo: %s', r)
                    lado = 'atrasadas' if r['ms'] > 0 else 'adiantadas'
                    self.aviso(f'Tempo acertado: as gravações chegavam {abs(r["ms"]):.0f} ms {lado}. '
                               f'Daqui em diante, entram no tempo.', 'ok')
                self.publicar('calibracao', self.ultima_calibracao)
                return
            if abs(deriva) * 1e-6 * dur > 0.0015 and abs(deriva) < 1000:  # só se a diferença passar de 1,5 ms
                x = T.reamostrar(x, alfa)
            else:
                deriva = 0.0
            ajuste = self._ajuste()
            inicio_s = inicio / SR - ajuste / 1000
            if inicio_s < 0:
                x = x[int(round(-inicio_s * SR)):]
                inicio_s = 0.0
            y = T.mono_se_igual(x)
            p = self.motor.parte(parte) if parte in [q.id for q in self.motor.partes] else None
            m = self.motor
            ficha = self.tent.salvar(y, parte, p.nome if p else parte, inicio_s, {
                'deriva_ppm': round(deriva, 1), 'ajuste_tempo_ms': ajuste,
                'aparelhos': {'api': m.api_entrada, 'entrada': m.nome_entrada, 'saida': m.nome_saida},
                'trecho': [m.trecho[0] / SR, m.trecho[1] / SR] if (m.trecho and m.trecho_ligado) else None})
            texto = (f'{ficha["nome"]} guardada: {_tempo(ficha["duracao_s"])}, pico {ficha["pico_db"]:.0f} dB '
                     f'({dica_nivel(ficha["pico_db"], M.eh_gt100(m.nome_entrada))}).')
            self.aviso(texto, 'ok' if -30 <= ficha['pico_db'] <= -1 else 'erro',
                       acao={'tipo': 'ouvir', 'id': ficha['id'], 'texto': 'Ouvir com a banda'})
            self.publicar('tentativas', self.lista_tentativas())
        except Exception as e:
            log.exception('finalizar gravação')
            self.aviso(f'Erro ao guardar a gravação: {e}', 'erro')
        finally:
            try:
                os.remove(cap.caminho)
            except OSError:
                pass
            self.ocupado = None
            self.publicar_estado()

    def acertar_tempo(self):
        if self.motor.captura is not None:
            raise RuntimeError('Pare a gravação primeiro.')
        pasta = self.tent.tmp if self.tent else os.path.join(DADOS, 'tmp')
        self.motor.calibrar(pasta)
        self.publicar_estado()

    # ------------------------------------------------------------ tentativas
    def lista_tentativas(self):
        if not self.tent:
            return []
        usadas = {p.tentativa: p.fonte for p in self.motor.partes if p.fonte != 'original'}
        out = []
        for f in self.tent.listar():
            f = {k: v for k, v in f.items() if k != '_json'}
            f['em_uso'] = usadas.get(f['id'])
            out.append(f)
        return out

    def _carregar_tentativa(self, tid):
        f = self.tent.achar(tid)
        self.motor.por_tentativa(tid, self.tent.audio(f), f['inicio_s'] + f.get('deslocamento_ms', 0.0) / 1000)
        return f

    def editar_tentativa(self, c):
        tid = c['id']
        campos = {k: c[k] for k in ('nome', 'favorita', 'deslocamento_ms') if k in c}
        if 'nome' in campos:
            campos['nome'] = str(campos['nome']).strip()[:60] or None
            if not campos['nome']:
                del campos['nome']
        if 'deslocamento_ms' in campos:
            campos['deslocamento_ms'] = max(-500.0, min(500.0, float(campos['deslocamento_ms'])))
        f = self.tent.atualizar(tid, **campos)
        if tid in self.motor.tent:
            self.motor.tent[tid]['inicio'] = int(round((f['inicio_s'] + f.get('deslocamento_ms', 0.0) / 1000) * SR))
        self.publicar('tentativas', self.lista_tentativas())

    def apagar_tentativa(self, tid):
        for p in self.motor.partes:
            if p.tentativa == tid:
                p.fonte, p.tentativa = 'original', None
        f = self.tent.apagar(tid)
        self.motor.tent.pop(tid, None)
        self._guardar_mix()
        self.aviso(f'{f.get("nome", "Tentativa")} apagada.', 'info', acao={'tipo': 'restaurar', 'id': tid, 'texto': 'Desfazer'})
        self.publicar('tentativas', self.lista_tentativas())
        self.publicar_estado()

    def restaurar_tentativa(self, tid):
        f = self.tent.restaurar(tid)
        self.aviso(f'{f.get("nome", "Tentativa")} voltou.', 'ok')
        self.publicar('tentativas', self.lista_tentativas())

    def ouvir_tentativa(self, tid):
        f = self.tent.achar(tid)
        self.definir_parte(f['parte'], {'fonte': 'tentativa', 'tentativa': tid, 'ligada': True})
        t0 = max(0.0, f['inicio_s'] + f.get('deslocamento_ms', 0.0) / 1000)
        m = self.motor
        if m.trecho and m.trecho_ligado:
            a, b = m.trecho[0] / SR, m.trecho[1] / SR
            if t0 + f['duracao_s'] < a or t0 > b:
                m.trecho_ligado = False
        if m.tocando:
            m.pausar()
        m.tocar(t0, sem_contagem=True)
        self.publicar('tentativas', self.lista_tentativas())
        self.publicar_estado()

    def exportar(self, tipo, tid=None):
        if not self.mus:
            raise RuntimeError('Abra uma música primeiro.')
        if self.ocupado:
            raise RuntimeError('Espere terminar: ' + self.ocupado)
        self.ocupado = 'Exportando o MP3...'
        self.publicar_estado()
        threading.Thread(target=self._exportar, args=(tipo, tid), daemon=True, name='exportar').start()

    def _exportar(self, tipo, tid):
        try:
            agora = datetime.datetime.now()
            titulo, artista = self.mus.titulo, self.mus.m.get('artista', '')
            album = f'{titulo} ({artista})' if artista else titulo
            if tipo == 'tentativa':
                f = self.tent.achar(tid)
                audio = self.tent.audio(f)
                destino = os.path.join(self.tent.exportados, f['id'] + '.mp3')
                T.exportar_mp3(audio, SR, destino, f'{titulo} - {f["nome"]}', 'Sala de Ensaio', album,
                               'Gravação feita na Sala de Ensaio.')
            else:
                m = self.motor
                a, b = (m.trecho if (m.trecho and m.trecho_ligado) else (0, m.n_total))
                out = np.zeros((b - a, 2), np.float32)
                for p in m.partes:
                    go, gt = p.alvos()
                    if go:
                        out += p.dados[a:b] * np.float32(go / 32768)
                    ta = m.tent.get(p.tentativa) if p.tentativa else None
                    if gt and ta is not None:
                        d, ini = ta['dados'], ta['inicio']
                        i0, i1 = max(a - ini, 0), min(b - ini, len(d))
                        if i1 > i0:
                            out[ini + i0 - a:ini + i1 - a] += d[i0:i1] * np.float32(gt)
                out *= np.float32(10 ** (m.volume_geral_db / 20))
                quais = ', '.join(p.nome if p.fonte == 'original' else f'{p.nome} (sua)' for p in m.partes if p.ligada)
                sufixo = f' (trecho {_tempo(a / SR)}-{_tempo(b / SR)})' if (a, b) != (0, m.n_total) else ''
                nome = T._seguro(f'{agora:%Y-%m-%d %Hh%M} - {titulo} - mistura{sufixo}'.replace(':', 'm'))
                destino = os.path.join(self.tent.exportados, nome + '.mp3')
                T.exportar_mp3(out, SR, destino, f'{titulo} - mistura{sufixo}', 'Sala de Ensaio', album, 'Partes: ' + quais)
            self.aviso(f'MP3 pronto: {os.path.basename(destino)}', 'ok', acao={'tipo': 'pasta', 'qual': 'exportados', 'texto': 'Abrir a pasta'})
        except Exception as e:
            log.exception('exportar')
            self.aviso(f'Erro ao exportar: {e}', 'erro')
        finally:
            self.ocupado = None
            self.publicar_estado()

    def abrir_pasta(self, qual):
        if not self.tent:
            return
        p = self.tent.exportados if qual == 'exportados' else self.tent.pasta
        os.makedirs(p, exist_ok=True)
        os.startfile(p)

    # ------------------------------------------------------------ aparelhos
    def trocar_saida(self, modo):
        if modo == 'gt100':
            modo = 'interface'
        if modo not in ('pc', 'interface'):
            raise ValueError(modo)
        if self.motor.captura is not None:
            raise RuntimeError('Pare a gravação antes de trocar a saída.')
        self.motor.pausar()
        self.motor.abrir(modo)
        self.cfg['modo_saida'] = modo
        self._salvar_cfg()
        self.publicar_estado()

    def trocar_entrada(self, nome):
        if self.motor.captura is not None:
            raise RuntimeError('Pare a gravação antes de trocar a entrada.')
        self.motor.pausar()
        self.motor.abrir(entrada=nome or None)
        if nome:
            self.cfg['entrada'] = M.sem_porta(nome)
        else:
            self.cfg.pop('entrada', None)
        self._salvar_cfg()
        self.publicar_estado()
        if self.motor.entrada is not None:
            self.aviso(f'Gravando de: {M.apelido(self.motor.nome_entrada)}. Faça o "Acertar o tempo" com esta entrada.', 'ok')
        else:
            self.aviso(self.motor.sem_entrada(), 'erro')

    def procurar(self):
        if self.motor.captura is not None:
            raise RuntimeError('Pare a gravação primeiro.')
        self.motor.procurar()
        self.publicar_estado()
        if self.motor.entrada is not None:
            self.aviso(f'Entrada encontrada: {M.apelido(self.motor.nome_entrada)}.', 'ok')
        else:
            self.aviso(self.motor.sem_entrada(), 'erro')

    def fechar(self):
        try:
            if self.motor.captura is not None:
                self.parar()
            if self.finalizando is not None:
                self.finalizando.join(timeout=60)
            self._guardar_mix()
            self.motor._fechar_streams()
        except Exception:
            log.exception('fechar')


# ---------------------------------------------------------------- configuração (fora da classe, para o --configurar)
def ler_cfg():
    try:
        return json.load(open(CONFIG, encoding='utf-8'))
    except Exception:
        return {}


def salvar_cfg(cfg):
    try:
        os.makedirs(DADOS, exist_ok=True)
        tmp = CONFIG + '.tmp'
        json.dump(cfg, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, CONFIG)
    except Exception:
        log.exception('salvar configuração')


def bibliotecas(cfg):
    return list(cfg.get('bibliotecas') or [MU.pasta_musicas_padrao()])


def juntar_biblioteca(cfg, pasta):
    pasta = os.path.normpath(os.path.abspath(pasta))
    libs = cfg.setdefault('bibliotecas', bibliotecas(cfg))
    if not any(os.path.normcase(pasta) == os.path.normcase(b) for b in libs):
        libs.append(pasta)
    return pasta


def escolher_pasta(titulo):
    """Janela do Windows para escolher uma pasta (tkinter). Devolve o caminho ou None."""
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk()
    r.withdraw()
    r.attributes('-topmost', True)
    try:
        return filedialog.askdirectory(parent=r, title=titulo, mustexist=True) or None
    finally:
        r.destroy()


APP = None
ROTAS = {
    'tocar': lambda a, c: a.tocar(c.get('t')),
    'pausar': lambda a, c: a.pausar(),
    'tocar_pausar': lambda a, c: a.tocar_pausar(),
    'ir': lambda a, c: a.ir(float(c['t'])),
    'inicio': lambda a, c: a.inicio(),
    'compasso': lambda a, c: a.mudar_compasso(int(c.get('delta', 1))),
    'trecho': lambda a, c: a.trecho(c),
    'parte': lambda a, c: a.definir_parte(c['id'], c),
    'modo': lambda a, c: a.definir_modo(c['modo']),
    'opcoes': lambda a, c: a.opcoes(c),
    'gravar': lambda a, c: a.gravar(c.get('parte')),
    'parar': lambda a, c: a.parar(),
    'gravar_parar': lambda a, c: a.gravar_parar(),
    'tentativa': lambda a, c: a.editar_tentativa(c),
    'tentativa_apagar': lambda a, c: a.apagar_tentativa(c['id']),
    'tentativa_restaurar': lambda a, c: a.restaurar_tentativa(c['id']),
    'tentativa_ouvir': lambda a, c: a.ouvir_tentativa(c['id']),
    'exportar': lambda a, c: a.exportar(c.get('tipo', 'mistura'), c.get('id')),
    'saida': lambda a, c: a.trocar_saida(c['modo']),
    'entrada': lambda a, c: a.trocar_entrada(c.get('nome')),
    'procurar': lambda a, c: a.procurar(),
    'acertar_tempo': lambda a, c: a.acertar_tempo(),
    'musica': lambda a, c: a.abrir_musica(c['id']),
    'musicas': lambda a, c: a.atualizar_musicas(),
    'adicionar_biblioteca': lambda a, c: a.adicionar_biblioteca(),
    'abrir_pasta': lambda a, c: a.abrir_pasta(c.get('qual', 'tentativas')),
}


class Pedido(BaseHTTPRequestHandler):
    server_version = 'SalaDeEnsaio/' + VERSAO
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _enviar(self, codigo, corpo, tipo, extra=None):
        self.send_response(codigo)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, obj, codigo=200):
        self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _local(self):
        """Só atende a própria janela: o endereço tem de ser 127.0.0.1/localhost (contra "DNS rebinding") e, nos pedidos
        que mudam alguma coisa, a origem tem de ser a do app (um site aberto no navegador não pode mandar no app)."""
        porta = self.server.server_port
        if self.headers.get('Host') not in (f'127.0.0.1:{porta}', f'localhost:{porta}'):
            return False
        origem = self.headers.get('Origin')
        return self.command == 'GET' or origem in (None, f'http://127.0.0.1:{porta}', f'http://localhost:{porta}')

    def _arquivo(self, nome):
        caminho = os.path.normpath(os.path.join(WEB, nome))
        if not caminho.startswith(WEB) or not os.path.isfile(caminho):
            return self._json({'erro': 'não encontrado'}, 404)
        with open(caminho, 'rb') as f:
            corpo = f.read()
        self._enviar(200, corpo, TIPOS.get(os.path.splitext(caminho)[1].lower(), 'application/octet-stream'))

    def _sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        q = APP.novo_cliente()
        try:
            self.wfile.write(f'event: estado\ndata: {json.dumps(APP.estado(), ensure_ascii=False)}\n\n'.encode('utf-8'))
            self.wfile.flush()
            while not APP.fim.is_set():
                try:
                    msg = q.get(timeout=2.0)
                except queue.Empty:
                    msg = ': ping\n\n'
                self.wfile.write(msg.encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            APP.remover_cliente(q)
            self.close_connection = True

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        try:
            if not self._local():
                return self._json({'erro': 'pedido de fora do app'}, 403)
            if p in ('/', '/index.html'):
                return self._arquivo('index.html')
            if p.startswith('/web/'):
                return self._arquivo(p[5:])
            if p == '/favicon.ico':
                return self._arquivo('icone.svg')
            if p == '/api/eventos':
                return self._sse()
            if p == '/api/ping':
                return self._json({'app': 'sala-de-ensaio', 'versao': VERSAO})
            if p == '/api/estado':
                return self._json(APP.estado())
            if p == '/api/musica':
                return self._json(APP.dados_musica())
            if p == '/api/tentativas':
                return self._json(APP.lista_tentativas())
            if p == '/api/acorde.svg':
                nome = parse_qs(u.query).get('nome', [''])[0]
                if not APP.mus:
                    return self._json({'erro': 'sem música'}, 404)
                svg = APP.mus.desenho_svg(nome).encode('utf-8')
                return self._enviar(200, svg, 'image/svg+xml', {'Cache-Control': 'max-age=3600'})
            return self._json({'erro': 'não encontrado'}, 404)
        except KeyError as e:
            return self._json({'erro': f'não encontrado: {e}'}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            log.exception('GET %s', p)
            return self._json({'erro': str(e)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if not self._local():
                return self._json({'erro': 'pedido de fora do app'}, 403)
            n = int(self.headers.get('Content-Length') or 0)
            corpo = json.loads(self.rfile.read(n).decode('utf-8') or '{}') if n else {}
            if not p.startswith('/api/') or p[5:] not in ROTAS:
                return self._json({'erro': 'ação desconhecida'}, 404)
            ROTAS[p[5:]](APP, corpo)
            return self._json(APP.estado())
        except (ValueError, KeyError, RuntimeError) as e:
            log.info('POST %s: %s', p, e)
            return self._json({'erro': str(e).strip("'")}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            log.exception('POST %s', p)
            return self._json({'erro': f'Erro inesperado: {e}'}, 500)


def _get(porta, caminho, corpo=None, tempo=1.5):
    req = urllib.request.Request(f'http://127.0.0.1:{porta}{caminho}', method='POST' if corpo is not None else 'GET',
                                 data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=tempo) as r:
        return json.loads(r.read().decode('utf-8'))


def abrir_janela(porta):
    """Janela própria do Edge (ou do Chrome) em modo app, com perfil separado; sem eles, o navegador padrão."""
    url = f'http://127.0.0.1:{porta}/'
    pf, pf86, lad = (os.environ.get(k, '') for k in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'))
    candidatos = [os.path.join(pf86, r'Microsoft\Edge\Application\msedge.exe'), os.path.join(pf, r'Microsoft\Edge\Application\msedge.exe'),
                  os.path.join(pf, r'Google\Chrome\Application\chrome.exe'), os.path.join(pf86, r'Google\Chrome\Application\chrome.exe'),
                  os.path.join(lad, r'Google\Chrome\Application\chrome.exe')]
    for nav in candidatos:
        if nav and os.path.exists(nav):
            subprocess.Popen([nav, f'--app={url}', f'--user-data-dir={os.path.join(DADOS, "janela")}',
                              '--no-first-run', '--no-default-browser-check', '--disable-sync', '--disable-features=Translate',
                              '--window-size=1440,920', '--window-position=40,30'],
                             close_fds=True, creationflags=0x00000008 if os.name == 'nt' else 0)
            return
    import webbrowser
    webbrowser.open(url)


def main():
    ap = argparse.ArgumentParser(description='Sala de Ensaio: tocar e gravar junto com a banda separada.')
    ap.add_argument('--musica', help='pasta da música para abrir')
    ap.add_argument('--biblioteca', action='append', help='pasta de músicas para juntar à lista (fica guardada)')
    ap.add_argument('--configurar', action='store_true', help='só guarda as opções (--biblioteca) e sai')
    ap.add_argument('--sem-janela', action='store_true')
    ap.add_argument('--porta', type=int, default=PORTA)
    ap.add_argument('--versao', action='version', version=f'Sala de Ensaio {VERSAO}')
    a = ap.parse_args()
    preparar_log()
    if a.biblioteca or a.configurar:
        cfg = ler_cfg()
        for b in a.biblioteca or []:
            juntar_biblioteca(cfg, b)
        salvar_cfg(cfg)
        if a.configurar:
            print('bibliotecas:', '; '.join(bibliotecas(cfg)))
            return
    mid = None
    if a.musica:
        cfg = ler_cfg()
        MU.BIBLIOTECAS[:] = bibliotecas(cfg)
        mid = MU.id_da_pasta(a.musica)
        if mid is None:  # música fora das bibliotecas: a pasta de cima entra na lista
            juntar_biblioteca(cfg, os.path.dirname(os.path.abspath(a.musica)))
            salvar_cfg(cfg)
            MU.BIBLIOTECAS[:] = bibliotecas(cfg)
            mid = MU.id_da_pasta(a.musica)
    try:  # já está aberto? só abre outra janela
        if _get(a.porta, '/api/ping').get('app') == 'sala-de-ensaio':
            if mid:
                try:
                    _get(a.porta, '/api/musicas', {})
                    _get(a.porta, '/api/musica', {'id': mid}, tempo=30)
                except Exception:
                    pass
            if not a.sem_janela:
                abrir_janela(a.porta)
            return
    except Exception:
        pass
    srv, porta = None, a.porta
    for p in range(a.porta, a.porta + 10):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), Pedido)
            porta = p
            break
        except OSError:
            continue
    if srv is None:
        log.error('nenhuma porta livre')
        return
    srv.daemon_threads = True
    log.info('Sala de Ensaio %s iniciada na porta %d', VERSAO, porta)
    global APP
    APP = App(manter=a.sem_janela)
    threading.Thread(target=srv.serve_forever, daemon=True, name='servidor').start()
    if not mid:
        mid = APP.cfg.get('ultima_musica')
        ids = [x['id'] for x in APP.musicas]
        prontas = [x['id'] for x in APP.musicas if x['preparada']]
        if mid not in ids:
            mid = prontas[0] if prontas else None
    if mid:
        def abrir():
            try:
                APP.abrir_musica(mid)
            except Exception as e:
                log.exception('abrir música')
                APP.aviso(f'Não consegui abrir a música: {e}', 'erro')
        threading.Thread(target=abrir, daemon=True, name='abrir').start()
    if not a.sem_janela:
        abrir_janela(porta)
    APP.fim.wait()
    APP.fechar()
    srv.shutdown()
    log.info('Sala de Ensaio encerrada')


if __name__ == '__main__':
    main()
