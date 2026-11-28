"""Banco local do SISCO (SQLite). Nunca escreve na planilha de dados."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from configuracao import email_de_teste

EDITABLE = {'classification', 'equipment', 'origin', 'description',
            'start_date', 'start_time', 'end_date', 'end_time',
            'maintenance_status', 'forecast_date', 'document_return_date',
            'generation_impact', 'maintenance_type'}
# Único destinatário dos envios de teste (configuracao.json → "email_de_teste").
TEST_EMAIL = email_de_teste()


def now():
    return datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec='seconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL, edits TEXT NOT NULL DEFAULT '{}',
                    reviewed INTEGER NOT NULL DEFAULT 0, reviewed_at TEXT,
                    source_changed INTEGER NOT NULL DEFAULT 0,
                    source_present INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL,
                    at TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, item_id INTEGER NOT NULL, filename TEXT NOT NULL,
                    path TEXT NOT NULL, created_at TEXT NOT NULL, kind TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, item_id INTEGER NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT,
                    payload TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS phase_log (
                    item_id INTEGER PRIMARY KEY, category TEXT NOT NULL,
                    since TEXT NOT NULL, estimated INTEGER NOT NULL DEFAULT 0
                );
            ''')
            from maintenance import setup
            setup(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _meta(self, db, key, value):
        db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, dumps(value)))

    def sync_info(self):
        with self.connect() as db:
            r = db.execute("SELECT value FROM metadata WHERE key='sync'").fetchone()
            return json.loads(r[0]) if r else {'issues': [], 'summary': {}, 'error': None}

    def sync_error(self, message):
        with self.lock, self.connect() as db:
            info = self.sync_info()
            info.update(last_attempt=now(), error=message)
            self._meta(db, 'sync', info)

    @staticmethod
    def substantive(record):
        return {k: v for k, v in record.items() if k not in {'source_row', 'forms_row', 'control_row', 'preparation'}}

    def ingest(self, snapshot):
        records = snapshot['records']
        keys = [r['source_key'] for r in records]
        if len(set(keys)) != len(keys):
            raise ValueError('A leitura contém chaves repetidas. Nenhum registro desta leitura foi gravado.')
        stamp = now()
        added = changed = absent = 0
        with self.lock, self.connect() as db:
            old = {r['source_key']: r for r in db.execute('SELECT * FROM items')}
            for record in records:
                key = record['source_key']
                prev = old.pop(key, None)
                if prev is None:
                    # "Revisada = Sim" na planilha vale como revisão inicial; depois, a revisão é feita no SISCO.
                    reviewed = bool(record.get('source_reviewed'))
                    cur = db.execute('INSERT INTO items(source_key,source,reviewed,reviewed_at,updated_at) VALUES (?,?,?,?,?)',
                                     (key, dumps(record), int(reviewed), stamp if reviewed else None, stamp))
                    db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',
                               (cur.lastrowid, stamp, 'Importada da planilha de dados', dumps({'source_key': key, 'revisada_na_planilha': reviewed})))
                    added += 1
                else:
                    prior = json.loads(prev['source'])
                    comparison = self.substantive(record)
                    if 'conclusion' not in prior.get('source_values', {}):
                        # One-time reader upgrade: adding previously unexposed
                        # workflow fields is not an operational source correction.
                        comparison = {**comparison, 'source_values': {k:v for k,v in record.get('source_values',{}).items() if k in prior.get('source_values',{})}}
                    meaningful = self.substantive(prior) != comparison or not prev['source_present']
                    if meaningful:
                        db.execute('UPDATE items SET source=?,source_changed=1,source_present=1,version=version+1,updated_at=? WHERE id=?',
                                   (dumps(record), stamp, prev['id']))
                        db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',
                                   (prev['id'], stamp, 'Fonte atualizada; ajustes locais preservados',
                                    dumps({'anterior': prior, 'atual': record})))
                        changed += 1
                    elif prev['source'] != dumps(record):
                        db.execute('UPDATE items SET source=? WHERE id=?', (dumps(record), prev['id']))
            for prev in old.values():
                if prev['source_present']:
                    db.execute('UPDATE items SET source_present=0,source_changed=1,version=version+1,updated_at=? WHERE id=?',
                               (stamp, prev['id']))
                    db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',
                               (prev['id'], stamp, 'Ausente na nova leitura; cadastro local preservado', '{}'))
                    absent += 1
            info = {'last_success': stamp, 'last_attempt': stamp, 'error': None,
                    'source_path': snapshot['source_path'], 'source_saved_at': snapshot.get('saved_at'),
                    'sha256': snapshot['sha256'], 'issues': snapshot.get('issues', []),
                    'summary': {**snapshot.get('stats', {}), 'added': added, 'changed': changed,
                                'absent': absent, 'read': len(records)}}
            self._meta(db, 'sync', info)
            if snapshot.get('configuration'):
                self._meta(db, 'source_configuration', snapshot['configuration'])
            if snapshot.get('maintenance_returns') is not None:
                from maintenance import ingest
                ingest(db, snapshot['maintenance_returns'], snapshot.get('issues', []))
        return info

    @staticmethod
    def item(row):
        source = json.loads(row['source'])
        edits = json.loads(row['edits'])
        values = {**source.get('source_values', {}), **edits}
        warnings = list(source.get('warnings', []))
        if not row['source_present']:
            warnings.append('Este registro não foi localizado na última leitura. Os dados locais foram preservados.')
        return {**source, 'id': row['id'], 'values': values, 'warnings': warnings,
                'reviewed': bool(row['reviewed']), 'reviewed_at': row['reviewed_at'],
                'source_changed': bool(row['source_changed']), 'source_present': bool(row['source_present']),
                'workflow_local': bool(set(edits) & {'classification','start_date','start_time','end_date','end_time','maintenance_status','forecast_date','document_return_date','forms_sent','word_status','document_sent'}),
                'version': row['version'], 'updated_at': row['updated_at']}

    def list_items(self):
        with self.connect() as db:
            items = [self.item(r) for r in db.execute('SELECT * FROM items ORDER BY id DESC')]
            return sorted(items, key=lambda i: (i['values'].get('start_date', ''),
                          i['values'].get('start_time', ''), i['id']), reverse=True)

    def get(self, item_id):
        with self.connect() as db:
            r = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if r is None:
                raise KeyError(item_id)
            result = self.item(r)
            result['history'] = [dict(h) for h in db.execute(
                'SELECT at,action,details FROM history WHERE item_id=? ORDER BY id DESC LIMIT 30', (item_id,))]
            from maintenance import list_returns
            result['maintenance_returns'] = list_returns(db, item_id)
            return result

    def track_phases(self, phases, estimates):
        """
        Guarda desde quando cada ocorrência está na fase atual (fase = categoria do fluxo).
        Primeira vez que o item é visto: usa a estimativa pelos dados (marcada como estimada).
        Mudou de fase: passa a contar a partir de agora. Devolve {id: (desde, estimado)}.
        """
        stamp = now()[:16]
        with self.lock, self.connect() as db:
            known = {r['item_id']: r for r in db.execute('SELECT * FROM phase_log')}
            result = {}
            for item_id, category in phases.items():
                row = known.get(item_id)
                if row is not None and row['category'] == category:
                    result[item_id] = (row['since'], bool(row['estimated']))
                    continue
                since, estimated = (stamp, False) if row is not None else ((estimates.get(item_id) or stamp), True)
                db.execute('INSERT INTO phase_log VALUES (?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET '
                           'category=excluded.category,since=excluded.since,estimated=excluded.estimated',
                           (item_id, category, since, int(estimated)))
                result[item_id] = (since, estimated)
            return result

    def returns(self):
        from maintenance import list_returns
        with self.connect() as db:
            return list_returns(db)

    def update(self, item_id, payload):
        if not isinstance(payload, dict) or set(payload) - {'version', 'values', 'reviewed'}:
            raise ValueError('Pedido de edição inválido.')
        values = payload.get('values', {})
        if not isinstance(values, dict) or set(values) - EDITABLE:
            raise ValueError('Campo não permitido para edição.')
        if 'reviewed' in payload and not isinstance(payload['reviewed'], bool):
            raise ValueError('Revisão deve ser verdadeira ou falsa.')
        if type(payload.get('version')) is not int:
            raise ValueError('Versão do cadastro é obrigatória.')
        values = dict(values)
        for field, allowed in {'generation_impact': ('', 'Sim', 'Não'),
                               'maintenance_type': ('', 'Preventiva', 'Corretiva', 'Emergencial')}.items():
            if field in values and values[field] not in allowed:
                raise ValueError('Selecione uma opção válida para geração e tipo de manutenção.')
        for name, value in values.items():
            if not isinstance(value, str) or len(value) > (20000 if name == 'description' else 300):
                raise ValueError('Valor de campo inválido.')
            if name.endswith('_date') and value:
                try:
                    if datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d') != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError('Informe uma data válida.') from None
            if name.endswith('_time') and value:
                try:
                    if datetime.strptime(value, '%H:%M').strftime('%H:%M') != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError('Informe um horário válido.') from None
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row['version'] != payload['version']:
                raise Conflict('Os dados mudaram desde que você abriu a ocorrência. Atualize e confira antes de salvar.')
            edits = json.loads(row['edits'])
            before = self.item(row)
            from importer import _normalize
            classification = values.get('classification', before['values'].get('classification', ''))
            if _normalize(classification) != 'manutencao':
                if values.get('maintenance_type'):
                    raise ValueError('Tipo de manutenção só se aplica à classificação Manutenção.')
                if before['values'].get('maintenance_type'):
                    values['maintenance_type'] = ''
            if any(key in values and values[key] != before['values'].get(key, '') for key in ('maintenance_status','forecast_date','document_return_date')) and not (row['reviewed'] or payload.get('reviewed') is True):
                raise ValueError('Revise a ocorrência antes de registrar tratativas.')
            for key, val in values.items():
                if before['values'].get(key, '') != val:
                    edits[key] = val
            combined = {**before['values'], **values}
            if all(combined.get(k) for k in ('start_date','start_time','end_date','end_time')):
                begin = combined['start_date'] + 'T' + combined['start_time']
                end = combined['end_date'] + 'T' + combined['end_time']
                if end < begin:
                    raise ValueError('O fim não pode ser anterior ao início. Confira as datas e os horários.')
            reviewed = payload.get('reviewed', bool(row['reviewed']))
            stamp = now()
            reviewed_at = (stamp if reviewed and (not row['reviewed'] or row['source_changed']) else row['reviewed_at']) if reviewed else None
            # Confirmar revisão explicitamente reconhece a versão da fonte exibida.
            source_changed = 0 if payload.get('reviewed') is True else row['source_changed']
            db.execute('UPDATE items SET edits=?,reviewed=?,reviewed_at=?,source_changed=?,version=version+1,updated_at=? WHERE id=?',
                       (dumps(edits), int(reviewed), reviewed_at, source_changed, stamp, item_id))
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',
                       (item_id, stamp, 'Revisão confirmada' if reviewed else 'Dados salvos',
                        dumps({'autor': 'Usuário local', 'antes': before['values'],
                               'depois': combined, 'revisado_antes': before['reviewed'], 'revisado': reviewed})))
            if reviewed:
                from maintenance import apply_pending
                apply_pending(db, item_id)
        return self.get(item_id)
