"""Retornos locais: identidade explícita, aplicação atômica e histórico separado."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256

from core import Conflict, dumps, now

FIELDS = {'maintenance_status', 'forecast_date', 'start_date', 'start_time', 'end_date', 'end_time'}


def read_returns(rows, date1904=False):
    from importer import _normalize, _value, _raw, _id, _date, _time, _timestamp, _text
    headers = {'A': 'id', 'F': 'id da ocorrencia', 'J': 'data de inicio da ocorrencia',
               'K': 'hora de inicio da ocorrencia', 'L': 'status da ocorrencia',
               'M': 'data prevista para finalizacao', 'N': 'data fim', 'O': 'hora fim'}
    issues, records = [], []
    if any(_normalize(_value(rows.get(1, {}), col)) != name for col, name in headers.items()):
        return None, [{'kind': 'return_headers', 'message': 'Cabeçalhos do retorno de manutenção não reconhecidos; retornos não importados.'}]
    for number, row in rows.items():
        if number <= 1 or not any(_text(_value(row, c)) for c in headers):
            continue
        rid, occurrence = _id(_value(row, 'A')), _id(_value(row, 'F'))
        if not rid:
            issues.append({'kind': 'return_id', 'source_row': number, 'message': 'Retorno sem ID de resposta válido; não importado.'})
            continue
        errors = []
        status = _normalize(_value(row, 'L'))
        status = 'Em Aberto' if status in ('aberta', 'em aberto') else 'Finalizada' if status in ('finalizada', 'encerrada', 'concluida') else ''
        values = {'maintenance_status': status}
        if not status:
            errors.append('Status de manutenção não reconhecido.')
        if not occurrence:
            errors.append('ID da ocorrência ausente ou inválido.')
        for field, col in {'start_date': 'J', 'start_time': 'K', 'forecast_date': 'M', 'end_date': 'N', 'end_time': 'O'}.items():
            raw = _value(row, col)
            values[field] = _date(raw, date1904) if field.endswith('_date') else _time(raw)
            if row.get(col, {}).get('error') or row.get(col, {}).get('uncached') or (not values[field] and _text(raw) not in ('', '-')):
                errors.append(f'{field}: valor ausente no cache ou inválido.')
        records.append({'response_id': rid, 'occurrence_id': occurrence, 'values': values,
                        'submitted_at': _timestamp(_value(row, 'C'), date1904), 'errors': errors,
                        'raw_fields': _raw(row, rows[1], tuple('AFGHIJKLMNO'))})
    counts = Counter(r['response_id'] for r in records)
    for record in records:
        if counts[record['response_id']] > 1:
            record['errors'].append('ID de resposta duplicado; associação bloqueada.')
    duplicates = {rid: [r['raw_fields'] for r in records if r['response_id'] == rid]
                  for rid, count in counts.items() if count > 1}
    for record in records:
        if record['response_id'] in duplicates:
            record['raw_fields'] = {'Respostas com o mesmo ID': {str(n + 1): raw for n, raw in enumerate(sorted(duplicates[record['response_id']], key=dumps))}}
    # A duplicidade continua nos erros, mas a chave ocupa uma única posição na caixa.
    return list({r['response_id']: r for r in records}.values()), issues


def setup(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS maintenance_returns (
            response_key TEXT PRIMARY KEY, response_id INTEGER, occurrence_id TEXT NOT NULL,
            item_id INTEGER, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
            status TEXT NOT NULL, message TEXT NOT NULL, received_at TEXT NOT NULL, applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS maintenance_managed (item_id INTEGER PRIMARY KEY, fields TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS maintenance_return_versions (
            response_key TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, seen_at TEXT NOT NULL,
            PRIMARY KEY(response_key, fingerprint)
        );
    ''')


def ingest(db, records, source_issues):
    from core import Store
    first = not db.execute("SELECT 1 FROM metadata WHERE key='maintenance_baseline'").fetchone()
    stamp = now()
    for record in records:
        key = 'forms-return:' + record['response_id']
        payload = dumps(record)
        digest = sha256(payload.encode()).hexdigest()
        previous = db.execute('SELECT * FROM maintenance_returns WHERE response_key=?', (key,)).fetchone()
        if previous:
            if previous['fingerprint'] != digest:
                # Guardar ambas as versões; mostrar a atual sem reaplicar a resposta.
                for fingerprint, content in ((previous['fingerprint'], previous['payload']), (digest, payload)):
                    db.execute('INSERT OR IGNORE INTO maintenance_return_versions VALUES (?,?,?,?)', (key, fingerprint, content, stamp))
                status = 'error' if record.get('errors') else 'conflict'
                message = ' '.join(record['errors']) if record.get('errors') else 'Resposta já conhecida mudou na fonte. Confira a origem e registre a correção manualmente.'
                db.execute('UPDATE maintenance_returns SET status=?,message=?,payload=?,fingerprint=? WHERE response_key=?',
                           (status, message, payload, digest, key))
            continue
        status = 'historical' if first else 'pending'
        message = 'Referência anterior à ativação local; não aplicada.' if first else 'Aguardando vínculo e revisão local.'
        if record.get('errors'):
            status, message = 'error', ' '.join(record['errors'])
        db.execute('INSERT INTO maintenance_returns VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                   (key, int(record['response_id']), record['occurrence_id'], None, payload, digest, status, message, stamp))
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('maintenance_baseline',?)", (dumps({'at': stamp}),))
    # Nunca vincular retorno pelo ID do Forms inicial, usina, texto ou posição.
    candidates = defaultdict(list)
    for row in db.execute('SELECT * FROM items WHERE source_present=1'):
        item = Store.item(row)
        if item.get('legacy_id'):
            candidates[item['legacy_id']].append(item['id'])
    ambiguous = {str(i.get('legacy_id')) for i in source_issues if i.get('kind') in ('duplicate_legacy_id', 'ambiguous_legacy')}
    for ret in db.execute("SELECT * FROM maintenance_returns WHERE response_key LIKE 'forms-return:%'").fetchall():
        ids = candidates[ret['occurrence_id']]
        target = ids[0] if len(ids) == 1 and ret['occurrence_id'] not in ambiguous else None
        if json.loads(ret['payload'])['occurrence_id'] != ret['occurrence_id']:
            target = None
            db.execute("UPDATE maintenance_returns SET status='conflict',message='O ID da ocorrência desta resposta foi alterado na fonte; vínculo automático suspenso. Confira a resposta original.' WHERE response_key=?", (ret['response_key'],))
        db.execute('UPDATE maintenance_returns SET item_id=? WHERE response_key=?', (target, ret['response_key']))
        if ret['status'] == 'pending':
            if str(ret['response_id']) not in {r['response_id'] for r in records}:
                db.execute("UPDATE maintenance_returns SET status='conflict',message='Resposta pendente ausente na nova leitura; nenhum dado alterado.' WHERE response_key=?", (ret['response_key'],))
                continue
            newer = db.execute('SELECT 1 FROM maintenance_returns WHERE occurrence_id=? AND response_id>? LIMIT 1',
                               (ret['occurrence_id'], ret['response_id'])).fetchone()
            if newer:
                db.execute("UPDATE maintenance_returns SET status='superseded',message='Existe resposta mais recente para esta ocorrência.' WHERE response_key=?", (ret['response_key'],))
            elif target is None:
                db.execute('UPDATE maintenance_returns SET message=? WHERE response_key=?',
                           ('ID da ocorrência não localizado ou duplicado; nenhum dado alterado.', ret['response_key']))
    apply_pending(db)


def validated(values):
    if not isinstance(values, dict) or set(values) - FIELDS:
        raise ValueError('Campos de retorno inválidos.')
    if values.get('maintenance_status') not in ('Em Aberto', 'Finalizada'):
        raise ValueError('Informe Em Aberto ou Finalizada.')
    for field, value in values.items():
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError('Valor de retorno inválido.')
        if not value or field == 'maintenance_status':
            continue
        fmt = '%Y-%m-%d' if field.endswith('_date') else '%H:%M'
        try:
            if datetime.strptime(value, fmt).strftime(fmt) != value:
                raise ValueError()
        except ValueError:
            raise ValueError(f'Data ou horário inválido: {field}.') from None
    if values['maintenance_status'] == 'Em Aberto' and (values.get('end_date') or values.get('end_time')):
        raise ValueError('Um retorno em aberto não pode informar encerramento.')
    return values


def apply_values(db, row, values, key, automatic):
    from core import Store
    from importer import _normalize
    values = validated(values)
    item = Store.item(row)
    current = item['values']
    is_closed = _normalize(current.get('maintenance_status')) in ('finalizada', 'encerrada', 'concluida') or current.get('end_date') or current.get('end_time')
    if values['maintenance_status'] == 'Em Aberto' and is_closed:
        return 'ignored', 'Ocorrência já possui encerramento; o retorno não reabriu o cadastro.'
    changes = {'maintenance_status': values['maintenance_status']}
    for field in ('start_date', 'start_time', 'end_date', 'end_time'):
        if values.get(field):
            if current.get(field) and current[field] != values[field]:
                raise ValueError(f'{field}: retorno diverge do cadastro. Confira e corrija o campo na ocorrência antes de reaplicar.')
            changes[field] = values[field]
    if values['maintenance_status'] == 'Finalizada':
        changes.update(forecast_date='', forms_due_at='')
    elif values.get('forecast_date'):
        changes['forecast_date'] = values['forecast_date']
        changes['forms_due_at'] = (datetime.strptime(values['forecast_date'], '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%dT09:00')
    merged = {**current, **changes}
    if all(merged.get(k) for k in ('start_date', 'start_time', 'end_date', 'end_time')) and merged['end_date'] + 'T' + merged['end_time'] < merged['start_date'] + 'T' + merged['start_time']:
        raise ValueError('O fim informado é anterior ao início. Nenhum campo do retorno foi aplicado.')
    edits = json.loads(row['edits'])
    managed_row = db.execute('SELECT fields FROM maintenance_managed WHERE item_id=?', (row['id'],)).fetchone()
    managed = json.loads(managed_row[0]) if managed_row else {}
    if automatic:
        for field, value in changes.items():
            if field in FIELDS and field in edits and current.get(field) != value and managed.get(field) != current.get(field):
                raise ValueError(f'{field}: existe ajuste local diferente do retorno. Confira antes de substituir.')
    edits.update(changes)
    managed.update(changes)
    stamp = now()
    db.execute('UPDATE items SET edits=?,version=version+1,updated_at=? WHERE id=?', (dumps(edits), stamp, row['id']))
    db.execute('INSERT INTO maintenance_managed VALUES (?,?) ON CONFLICT(item_id) DO UPDATE SET fields=excluded.fields', (row['id'], dumps(managed)))
    db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',
               (row['id'], stamp, 'Retorno da manutenção aplicado', dumps({'resposta': key, 'origem': 'Planilha de dados' if automatic else 'Registro manual no SISCO', 'antes': current, 'depois': merged})))
    incomplete = values['maintenance_status'] == 'Finalizada' and not (merged.get('end_date') and merged.get('end_time'))
    return 'applied', 'Finalização registrada; falta completar data ou hora do fim.' if incomplete else 'Retorno aplicado na base local.'


def apply_pending(db, item_id=None):
    query = "SELECT * FROM maintenance_returns WHERE status='pending' AND item_id IS NOT NULL"
    args = ()
    if item_id is not None:
        query += ' AND item_id=?'
        args = (item_id,)
    for ret in db.execute(query, args).fetchall():
        row = db.execute('SELECT * FROM items WHERE id=?', (ret['item_id'],)).fetchone()
        if not row or not row['source_present'] or not row['reviewed']:
            continue
        record = json.loads(ret['payload'])
        try:
            status, message = apply_values(db, row, record['values'], ret['response_key'], True)
        except ValueError as exc:
            status, message = 'conflict', str(exc)
        db.execute('UPDATE maintenance_returns SET status=?,message=?,applied_at=? WHERE response_key=?',
                   (status, message, now() if status == 'applied' else None, ret['response_key']))


def register(store, item_id, payload):
    import re
    if not isinstance(payload, dict) or set(payload) != {'version', 'request_id', 'values'} or type(payload['version']) is not int:
        raise ValueError('Pedido de retorno inválido.')
    if not isinstance(payload['request_id'], str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', payload['request_id']):
        raise ValueError('Identificador da operação inválido.')
    values = validated(payload['values'])
    key = 'manual:' + payload['request_id']
    serialized = dumps({'values': values, 'item_id': item_id})
    digest = sha256(serialized.encode()).hexdigest()
    with store.lock, store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT fingerprint FROM maintenance_returns WHERE response_key=?', (key,)).fetchone()
        if previous:
            if previous[0] != digest:
                raise Conflict('Este identificador de operação já foi usado com outros dados.')
        else:
            row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row['version'] != payload['version']:
                raise Conflict('A ocorrência mudou. Recarregue antes de registrar o retorno.')
            if not row['reviewed']:
                raise ValueError('Revise a ocorrência antes de registrar tratativas.')
            status, message = apply_values(db, row, values, key, False)
            from core import Store
            if status == 'applied':
                db.execute("UPDATE maintenance_returns SET status='resolved',message=? WHERE item_id=? AND status IN ('pending','conflict')",
                           ('Tratado pelo registro manual ' + payload['request_id'] + '. Evidência original preservada.', item_id))
            db.execute('INSERT INTO maintenance_returns VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (key, None, Store.item(row).get('legacy_id', ''), item_id, serialized, digest, status, message, now(), now() if status == 'applied' else None))
    return store.get(item_id)


def list_returns(db, item_id=None):
    query = 'SELECT response_key,response_id,occurrence_id,item_id,status,message,received_at,applied_at,payload FROM maintenance_returns'
    args = ()
    if item_id is not None:
        query += ' WHERE item_id=?'
        args = (item_id,)
    query += ' ORDER BY received_at DESC,response_id DESC'
    result = []
    for row in db.execute(query, args):
        entry = dict(row)
        payload = json.loads(entry.pop('payload'))
        entry['values'] = payload['values']
        entry['submitted_at'] = payload.get('submitted_at', '')
        entry['raw_fields'] = payload.get('raw_fields', {})
        result.append(entry)
    return result
