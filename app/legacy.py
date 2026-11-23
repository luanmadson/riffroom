# SPDX-License-Identifier: GPL-3.0-or-later
"""Files from version 1 ("Sala de Ensaio"), written with Portuguese keys: song manifest (ensaio.json), take records and
the configuration. They are converted when read, so old songs and takes keep working."""
import os

PARTS = {'bateria': 'drums', 'baixo': 'bass', 'base': 'rhythm', 'solo': 'lead', 'guitarra': 'guitar', 'piano': 'piano',
         'voz': 'vocals', 'outros': 'other'}
SOURCES = {'original': 'original', 'tentativa': 'take', 'ambos': 'both'}


def part_id(pid):
    return PARTS.get(pid, pid)


def is_old_manifest(m):
    return 'partes' in m or 'titulo' in m


def manifest(old):
    shapes = {k: {'frets': d.get('casas'), 'root_string': d.get('corda_tonica'), 'intervals': d.get('intervalos')}
              for k, d in (old.get('desenhos') or {}).items()}
    bal = old.get('equilibrio') or {}
    return {
        'version': 2, 'source': 'v1',
        'title': old.get('titulo'), 'artist': old.get('artista'), 'album': old.get('album'), 'tonic': old.get('tonica'),
        'time_signature': old.get('compasso_tipo'), 'bpm': old.get('bpm'), 'tuning': 0,
        'sample_rate': old['taxa'], 'duration_s': old['duracao_s'], 'mix': old.get('original'),
        'parts': [{'id': part_id(p['id']), 'name': p.get('nome'), 'file': p['arquivo'], 'gain': p['ganho'],
                   'gain_db': p.get('ganho_db')} for p in old.get('partes', [])],
        'targets': [part_id(a) for a in old.get('alvos', [])] or None,
        'balance': {'residual_db': bal.get('sobra_db'), 'scale': bal.get('escala')},
        'count_in': old.get('contagem', []), 'count_in_in_drums': True, 'soft_hits': old.get('toques_leves', []),
        'bars': old.get('compassos', []), 'beats_per_bar': old.get('pulsos_por_compasso', []),
        'subdivisions': old.get('subdivisoes'),
        'chords': [{'t0': c['t0'], 't1': c['t1'], 'bar': c.get('compasso'), 'beat': c.get('pulso'), 'chord': c.get('acorde'),
                    'degree': c.get('grau')} for c in old.get('acordes', [])],
        'shapes': shapes,
        'sections': [{'bar': s['compasso'], 'name': s['nome']} for s in old.get('secoes', [])],
    }


def is_old_take(f):
    return 'arquivo' in f or 'versao' in f


def take(old):
    dev = old.get('aparelhos') or {}
    return {
        'version': 2, 'id': old['id'], 'number': old.get('numero'), 'name': old.get('nome'),
        'part': part_id(old.get('parte')), 'part_name': old.get('nome_parte'), 'created': old.get('criada'),
        'file': old['arquivo'], 'sample_rate': old.get('taxa'), 'channels': old.get('canais'),
        'duration_s': old.get('duracao_s'), 'start_s': old.get('inicio_s', 0.0), 'offset_ms': old.get('deslocamento_ms', 0.0),
        'favorite': old.get('favorita', False), 'peak_db': old.get('pico_db'), 'rms_db': old.get('rms_db'),
        'peaks': old.get('picos'), 'drift_ppm': old.get('deriva_ppm'), 'latency_ms': old.get('ajuste_tempo_ms'),
        'devices': {'api': dev.get('api'), 'input': dev.get('entrada'), 'output': dev.get('saida')},
        'loop': old.get('trecho'),
    }


def config(old, default_library):
    """Configuration of the "Sala de Ensaio" (%LOCALAPPDATA%\\SalaDeEnsaio\\config.json) in the Riffroom format."""
    songs = {}
    for sid, st in (old.get('musicas') or {}).items():
        songs[sid] = {
            'target': part_id(st.get('alvo')), 'master_db': st.get('volume_geral_db', 0.0),
            'loop': st.get('trecho'), 'loop_on': bool(st.get('trecho_ligado')),
            'parts': {part_id(pid): {'on': ps.get('ligada', True), 'volume_db': ps.get('volume_db', 0.0),
                                     'source': SOURCES.get(ps.get('fonte'), 'original'), 'take': ps.get('tentativa')}
                      for pid, ps in (st.get('partes') or {}).items()}}
    libraries = list(old.get('bibliotecas') or [])
    old_default = os.path.join(os.path.dirname(default_library), 'Sala de Ensaio')
    if not libraries:
        libraries = [default_library] + ([old_default] if os.path.isdir(old_default) else [])
    return {
        'libraries': libraries, 'input_device': old.get('entrada'),
        'output_mode': {'gt100': 'interface'}.get(old.get('modo_saida'), old.get('modo_saida') or 'pc'),
        'count_in': old.get('contagem', True), 'metronome': old.get('metronomo', False),
        'last_song': old.get('ultima_musica'), 'latency_ms': dict(old.get('ajustes_ms') or {}),
        'tour_seen': bool(old.get('tour_visto')), 'songs': songs,
    }
