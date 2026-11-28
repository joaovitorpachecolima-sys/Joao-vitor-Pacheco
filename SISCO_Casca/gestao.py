"""Gestão em cima das ocorrências, tudo guardado só no banco local do SISCO (a planilha e o Forms não são alterados):

1. famílias de causa (a Origem da planilha é texto livre; aqui ela é agrupada por palavras-chave);
2. ações de melhoria (achado → ação com dono, prazo e medição antes/depois);
3. linha do tempo de cada ocorrência (datas por etapa) e motivo da espera;
4. histórico diário da situação de cada grupo gerador.
"""
from __future__ import annotations

import json
import re
import statistics
import unicodedata
from datetime import date, datetime, timedelta

from core import Conflict, dumps, now

CAUSE_DEFAULTS = [
    {'name': 'Manutenção programada', 'keywords': ['preventiva', 'revisao geral', 'programada', 'inspecao']},
    {'name': 'Mancal', 'keywords': ['mancal']},
    {'name': 'Rede / distribuidora', 'keywords': ['oscilacao de sistema', 'grid', 'distribuidora', 'falta de energia', 'subtensao', 'sobretensao', 'rede externa', 'enel', 'falta de tensao', 'desligamento temporario']},
    {'name': 'Comunicação e TI', 'keywords': ['comunicacao', 'enlace', 'internet', 'fibra', 'link', 'supervisorio', 'medidor', 'smf', 'zabbix', 'teams']},
    {'name': 'Sistema hidráulico (UHCT / regulador)', 'keywords': ['uhct', 'regulador', 'oleo', 'hidraulic']},
    {'name': 'Vedação', 'keywords': ['gaxet', 'selo', 'vazamento']},
    {'name': 'Recurso hídrico', 'keywords': ['nivel do reservatorio', 'baixo nivel', 'vazao', 'afluencia', 'estiagem']},
    {'name': 'Adução e civil', 'keywords': ['conduto', 'canal', 'barragem', 'comporta', 'grade', 'limpeza', 'aducao', 'adutora']},
    {'name': 'Ventilação e temperatura', 'keywords': ['ventilacao', 'resfriamento', 'refrigeracao', 'temperatura', 'aquecimento', 'ventilador']},
    {'name': 'Elétrico e proteção', 'keywords': ['transformador', 'disjunt', 'dj', 'seccionadora', 'isolador', 'afci', 'cubiculo', 'rele', 'protecao', 'isolamento', 'excitatriz', 'exitatriz', 'bateria', 'queimado', 'inversor', 'pecp']},
    {'name': 'Clima', 'keywords': ['climatic', 'chuva', 'descarga atmosferica', 'raio', 'vento', 'tempestade']},
]
NO_FAMILY = 'Sem família'
WAIT_REASONS = ('Peça', 'Terceiro', 'Acesso', 'Clima', 'Aprovação', 'Informação incompleta', 'Parada programada', 'Outro')
ACTION_STATUS = ('aberta', 'concluida', 'cancelada')
STAGES = (  # (chave, nome, de, até, quem responde)
    ('reparo', 'Normalizar a usina', 'inicio', 'fim', 'Manutenção'),
    ('solicitacao', 'Enviar a solicitação de status', 'limite', 'forms', 'COG'),
    ('retorno', 'Responder a solicitação', 'forms', 'retorno', 'Manutenção / coordenação'),
    ('emitir', 'Enviar o registro depois do fim', 'fim', 'doc_enviado', 'COG'),
    ('devolver', 'Devolver o registro', 'doc_enviado', 'doc_devolvido', 'Coordenação'),
)
STEP_LABELS = {'inicio': 'Início da ocorrência', 'fim': 'Normalização', 'forms': 'Solicitação de status enviada', 'retorno': 'Retorno da manutenção',
               'word': 'Registro (Word) gerado', 'doc_enviado': 'Registro enviado à coordenação', 'doc_devolvido': 'Registro devolvido'}


def _norm(text):
    text = unicodedata.normalize('NFD', str(text or ''))
    return ' '.join(''.join(c for c in text if unicodedata.category(c) != 'Mn').casefold().split())


def familia(origin, families):
    """Família da causa: a primeira cuja palavra-chave aparece na Origem. '' = origem em branco."""
    text = _norm(origin)
    if text in ('', '-', 'nao informado', 'nao informada'):
        return ''
    for family in families:
        for keyword in family['keywords']:
            # Palavra curta (ex.: "dj") só vale no começo de uma palavra ("DJ2044"), para não casar dentro de outra.
            if re.search(rf'\b{re.escape(keyword)}', text) if len(keyword) <= 3 else keyword in text:
                return family['name']
    return NO_FAMILY


def validar_familias(families):
    if not isinstance(families, list) or not 1 <= len(families) <= 40:
        raise ValueError('Informe entre 1 e 40 famílias de causa.')
    clean, names = [], set()
    for family in families:
        if not isinstance(family, dict) or set(family) != {'name', 'keywords'} or not isinstance(family['name'], str) or not isinstance(family['keywords'], list):
            raise ValueError('Família de causa inválida.')
        name = ' '.join(family['name'].split())
        if not name or len(name) > 60 or _norm(name) in names or _norm(name) == _norm(NO_FAMILY):
            raise ValueError('Nome de família vazio, repetido ou reservado.')
        names.add(_norm(name))
        keywords = []
        for keyword in family['keywords']:
            if not isinstance(keyword, str) or len(keyword) > 60:
                raise ValueError('Palavra-chave inválida.')
            if _norm(keyword) and _norm(keyword) not in keywords:
                keywords.append(_norm(keyword))
        if not keywords or len(keywords) > 40:
            raise ValueError(f'Informe de 1 a 40 palavras-chave em "{name}".')
        clean.append({'name': name, 'keywords': keywords})
    return clean


def _stamp(day, time=''):
    try:
        base = date.fromisoformat(str(day or '')[:10])
    except ValueError:
        return ''
    match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::\d{2})?', str(time or '').strip())
    hour, minute = (int(match[1]), int(match[2])) if match and int(match[1]) < 24 and int(match[2]) < 60 else (0, 0)
    return datetime(base.year, base.month, base.day, hour, minute).isoformat(timespec='minutes')


def _stamp_in_text(text):
    """Data dentro de textos da planilha, como "Email enviado (07/10/2026 16:05)"."""
    match = re.search(r'(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?', str(text or ''))
    if not match:
        return ''
    try:
        return datetime(int(match[3]), int(match[2]), int(match[1]), int(match[4] or 0), int(match[5] or 0)).isoformat(timespec='minutes')
    except ValueError:
        return ''


def linha_do_tempo(item, first_forms=None, returns=(), sla_hours=24):
    """Datas de cada etapa da ocorrência, na ordem do processo. `first_forms` = (data do 1º envio visto, estimada?)."""
    v = item.get('values') or {}
    start, end = _stamp(v.get('start_date'), v.get('start_time')), _stamp(v.get('end_date'), v.get('end_time'))
    answered = sorted(str(r.get('submitted_at') or '')[:16] for r in returns if r.get('submitted_at'))
    forms, estimated = first_forms or (str(v.get('forms_sent_at') or '')[:16], True)
    times = {'inicio': start, 'fim': end, 'forms': forms, 'retorno': answered[0] if answered else '',
             'word': _stamp_in_text(v.get('word_status')), 'doc_enviado': _stamp_in_text(v.get('document_sent')),
             'doc_devolvido': _stamp(v.get('document_return_date'))}
    steps = [{'key': key, 'label': STEP_LABELS[key], 'at': times[key], 'estimated': key == 'forms' and bool(forms) and bool(estimated)} for key in STEP_LABELS]
    limit = (datetime.fromisoformat(start) + timedelta(hours=sla_hours)).isoformat(timespec='minutes') if start else ''
    return {'steps': steps, 'limit': limit}


def _days(begin, finish):
    try:
        return (datetime.fromisoformat(finish) - datetime.fromisoformat(begin)).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def etapas_por_mes(items, timelines, today=None):
    """Onde o tempo fica: mediana de dias em cada etapa, por mês de início. Etapa em andamento conta até hoje."""
    present = datetime.combine(today or date.today(), datetime.min.time()).replace(hour=23, minute=59).isoformat(timespec='minutes')
    months = {}
    for item in items:
        timeline = timelines.get(item['id'])
        if not timeline or not item.get('control_row'):
            continue
        times = {step['key']: step['at'] for step in timeline['steps']}
        times['limite'] = timeline['limit']
        if not times['inicio']:
            continue
        closed = ((item.get('workflow') or {}).get('category')) == 'closed'
        late = _norm((item.get('values') or {}).get('sla')) in ('superior 24h', 'analisar')
        for key, _name, begin, finish, _owner in STAGES:
            if not times.get(begin) or (key == 'reparo' and not late) or (key == 'solicitacao' and not times['forms']):
                continue
            running = not times.get(finish)
            if running and (closed or key == 'solicitacao'):
                continue  # encerrada sem passar pela etapa: não é espera
            span = _days(times[begin], times[finish] or present)
            if span is not None and span < 0:
                span = 0 if key == 'solicitacao' else None  # Forms enviado antes das 24 h: sem espera; datas trocadas: fora
            if span is not None:
                months.setdefault(times['inicio'][:7], {}).setdefault(key, []).append((span, item['id'], running))
    result = {}
    for month, stages in months.items():
        result[month] = []
        for key, name, _begin, _finish, owner in STAGES:
            spans = sorted(stages.get(key, []), reverse=True)
            if not spans:
                continue
            values = [span for span, _, _ in spans]
            result[month].append({'key': key, 'name': name, 'owner': owner, 'n': len(values), 'median': round(statistics.median(values), 1),
                                  'worst': round(values[0], 1), 'running': sum(1 for _, _, running in spans if running),
                                  'items': [item_id for _, item_id, _ in spans[:30]]})
    return result


class Gestao:
    def __init__(self, store):
        self.store = store
        with store.lock, store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS milestones (item_id INTEGER NOT NULL, key TEXT NOT NULL, at TEXT NOT NULL,
                    estimated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (item_id, key));
                CREATE TABLE IF NOT EXISTS waits (item_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
                    todo TEXT NOT NULL, owner TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    done_at TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', metric TEXT NOT NULL DEFAULT 'null');
                CREATE TABLE IF NOT EXISTS ug_history (day TEXT NOT NULL, plant TEXT NOT NULL, unit TEXT NOT NULL, color TEXT NOT NULL,
                    mw REAL, PRIMARY KEY (day, plant, unit));
            ''')

    # ---------- 1. Famílias de causa ----------
    def causes(self):
        with self.store.connect() as db:
            saved = db.execute("SELECT value FROM metadata WHERE key='cause_families'").fetchone()
        saved = json.loads(saved[0]) if saved else {}
        return {'version': saved.get('version', 0), 'families': saved.get('families') or [dict(f, keywords=list(f['keywords'])) for f in CAUSE_DEFAULTS]}

    def save_causes(self, body):
        if not isinstance(body, dict) or set(body) != {'version', 'families'} or type(body['version']) is not int:
            raise ValueError('Pedido de causas inválido.')
        families = validar_familias(body['families'])
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if body['version'] != self.causes()['version']:
                raise Conflict('As famílias de causa mudaram. Recarregue antes de salvar.')
            self.store._meta(db, 'cause_families', {'version': body['version'] + 1, 'families': families})
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)', (now(), 'Famílias de causa atualizadas', dumps(families)))
        return self.causes()

    # ---------- 3. Linha do tempo e motivo da espera ----------
    def first_forms(self, items, today=None):
        """Guarda a 1ª data de envio do Forms vista para cada ocorrência (a planilha sobrescreve a cada cobrança).

        Se o envio já era antigo quando foi visto pela primeira vez, fica marcado como estimado."""
        today = today or date.today()
        with self.store.lock, self.store.connect() as db:
            known = {r['item_id']: (r['at'], bool(r['estimated'])) for r in db.execute("SELECT * FROM milestones WHERE key='forms'")}
            for item in items:
                sent = str((item.get('values') or {}).get('forms_sent_at') or '')[:16]
                if not sent or item['id'] in known:
                    continue
                try:
                    sent_day = date.fromisoformat(sent[:10])
                except ValueError:
                    continue
                # Visto tarde, ou enviado muitos dias depois do início: pode já ser a data de um reenvio.
                old = (today - sent_day).days > 2
                try:
                    old = old or (sent_day - date.fromisoformat(str((item.get('values') or {}).get('start_date'))[:10])).days > 4
                except ValueError:
                    pass
                db.execute("INSERT OR IGNORE INTO milestones VALUES (?,'forms',?,?)", (item['id'], sent, int(old)))
                known[item['id']] = (sent, old)
        return known

    def waits(self):
        with self.store.connect() as db:
            return {r['item_id']: {'reason': r['reason'], 'note': r['note'], 'updated_at': r['updated_at']} for r in db.execute('SELECT * FROM waits')}

    def save_wait(self, item_id, body):
        if not isinstance(body, dict) or set(body) - {'reason', 'note'} or 'reason' not in body:
            raise ValueError('Pedido de motivo inválido.')
        reason, note = body['reason'], body.get('note', '')
        if reason not in ('', *WAIT_REASONS) or not isinstance(note, str) or len(note) > 300:
            raise ValueError('Motivo da espera inválido.')
        self.store.get(item_id)  # KeyError se a ocorrência não existe
        with self.store.lock, self.store.connect() as db:
            if reason:
                db.execute('INSERT INTO waits VALUES (?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET reason=excluded.reason,note=excluded.note,updated_at=excluded.updated_at',
                           (item_id, reason, note.strip(), now()))
            else:
                db.execute('DELETE FROM waits WHERE item_id=?', (item_id,))
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)', (item_id, now(), 'Motivo da espera', dumps({'reason': reason, 'note': note.strip()})))
        return self.waits().get(item_id)

    # ---------- 2. Ações de melhoria ----------
    def actions(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM actions WHERE status!='cancelada' ORDER BY status, due, id").fetchall()
        return [{**{k: r[k] for k in ('id', 'ref', 'title', 'detail', 'todo', 'owner', 'due', 'status', 'created_at', 'done_at', 'note')}, 'metric': json.loads(r['metric'])} for r in rows]

    @staticmethod
    def _text(body, field, limit, required=True):
        value = body.get(field, '')
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise ValueError({'title': 'Informe o título da ação.', 'owner': 'Informe o responsável.', 'todo': 'Informe o que será feito.'}.get(field, 'Texto inválido.'))
        return ' '.join(value.split())

    @staticmethod
    def _due(value):
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ValueError('Informe o prazo da ação.') from None

    def create_action(self, body, metric=None):
        if not isinstance(body, dict) or set(body) - {'ref', 'title', 'detail', 'todo', 'owner', 'due'}:
            raise ValueError('Pedido de ação inválido.')
        ref = self._text(body, 'ref', 200, required=False)
        record = (ref, self._text(body, 'title', 200), self._text(body, 'detail', 600, required=False), self._text(body, 'todo', 400),
                  self._text(body, 'owner', 80), self._due(body.get('due')), 'aberta', now(), dumps(metric))
        with self.store.lock, self.store.connect() as db:
            if ref and db.execute("SELECT 1 FROM actions WHERE ref=? AND status='aberta'", (ref,)).fetchone():
                raise Conflict('Já existe uma ação aberta para este achado.')
            cursor = db.execute('INSERT INTO actions(ref,title,detail,todo,owner,due,status,created_at,metric) VALUES (?,?,?,?,?,?,?,?,?)', record)
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)', (now(), 'Ação de melhoria criada', dumps({'id': cursor.lastrowid, 'title': record[1]})))
            return cursor.lastrowid

    def update_action(self, action_id, body):
        if not isinstance(body, dict) or not body or set(body) - {'status', 'owner', 'due', 'todo', 'note'}:
            raise ValueError('Pedido de ação inválido.')
        with self.store.lock, self.store.connect() as db:
            row = db.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            changes = {}
            if 'status' in body:
                if body['status'] not in ACTION_STATUS:
                    raise ValueError('Situação da ação inválida.')
                changes['status'] = body['status']
                changes['done_at'] = now() if body['status'] == 'concluida' else ''
            if 'owner' in body:
                changes['owner'] = self._text(body, 'owner', 80)
            if 'todo' in body:
                changes['todo'] = self._text(body, 'todo', 400)
            if 'note' in body:
                changes['note'] = self._text(body, 'note', 600, required=False)
            if 'due' in body:
                changes['due'] = self._due(body['due'])
            db.execute(f'UPDATE actions SET {",".join(f"{key}=?" for key in changes)} WHERE id=?', (*changes.values(), action_id))
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)', (now(), 'Ação de melhoria atualizada', dumps({'id': action_id, **changes})))

    # ---------- 4. Histórico diário das UGs ----------
    def record_units(self, generation):
        """Grava a situação do dia de cada grupo gerador (o relatório mais novo do dia vale)."""
        with self.store.lock, self.store.connect() as db:
            for plant in generation.get('usinas') or ():
                day = str(plant.get('recebido') or '')[:10]
                if len(day) != 10:
                    continue
                for unit in plant.get('unidades') or ():
                    db.execute('INSERT INTO ug_history VALUES (?,?,?,?,?) ON CONFLICT(day,plant,unit) DO UPDATE SET color=excluded.color,mw=excluded.mw',
                               (day, plant.get('chave', ''), unit.get('nome', ''), unit.get('cor', 'cinza'), unit.get('mw')))

    def unit_history(self, today=None, days=30):
        """{(usina, unidade): {dias, disponibilidade, paradas, sem ocorrência}} dos últimos `days` dias."""
        since = ((today or date.today()) - timedelta(days=days - 1)).isoformat()
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM ug_history WHERE day>=? ORDER BY day', (since,)).fetchall()
        history = {}
        for row in rows:
            history.setdefault((row['plant'], row['unit']), []).append({'day': row['day'], 'color': row['color']})
        result = {}
        for key, entries in history.items():
            known = [entry for entry in entries if entry['color'] != 'cinza']
            stopped = sum(1 for entry in known if entry['color'] in ('vermelho', 'amarelo'))
            result[key] = {'days': entries, 'known': len(known), 'stopped': stopped, 'unexplained': sum(1 for entry in known if entry['color'] == 'amarelo'),
                           'availability': round((len(known) - stopped) / len(known) * 100) if known else None}
        return result
