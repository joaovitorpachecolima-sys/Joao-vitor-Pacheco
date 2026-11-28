"""KPIs da gestão de ocorrências: indicador com meta, tendência, leitura e achados (padrões).

Cálculo puro sobre as linhas já importadas do CONTROLE: nada é gravado e nada é enviado.
Cada indicador responde a uma pergunta de gestão, tem meta e aponta as ocorrências que o puxam para baixo.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta

from gestao import CAUSE_DEFAULTS, NO_FAMILY, familia

# better: 'up' = quanto maior, melhor. tolerance: distância da meta ainda tratada como "atenção".
KPIS = (
    {'key': 'resolucao_24h', 'name': 'Manutenção normalizada em até 24 h', 'unit': '%', 'better': 'up', 'default': 75, 'min': 1, 'max': 100, 'tolerance': 10,
     'question': 'Quando a falha é nossa, a usina volta rápido?',
     'how': 'Ocorrências de manutenção iniciadas no mês com Controle 24h "Ok", sobre as já decididas (Ok, Superior 24h e Analisar).'},
    {'key': 'duracao_longas', 'name': 'Duração das manutenções acima de 24 h', 'unit': 'd', 'better': 'down', 'default': 5, 'min': 1, 'max': 365, 'tolerance': 2,
     'question': 'Quando passa de 24 h, quanto tempo leva?',
     'how': 'Mediana, em dias, das ocorrências de manutenção do mês que passaram de 24 h. As ainda abertas contam até hoje.'},
    {'key': 'backlog_30d', 'name': 'Abertas há mais de 30 dias', 'unit': '', 'better': 'down', 'default': 5, 'min': 0, 'max': 500, 'tolerance': 3,
     'question': 'Estamos deixando ocorrência envelhecer?',
     'how': 'Ocorrências em acompanhamento, sem data de fim, com mais de 30 dias no último dia do mês (ou hoje).'},
    {'key': 'reincidencia', 'name': 'Reincidência de equipamento', 'unit': '%', 'better': 'down', 'default': 20, 'min': 0, 'max': 100, 'tolerance': 10,
     'question': 'Estamos resolvendo a causa ou só religando?',
     'how': 'Ocorrências de manutenção do mês com a mesma causa (família) no mesmo equipamento da mesma usina nos 60 dias anteriores.'},
    {'key': 'registro_prazo', 'name': 'Registros devolvidos no prazo', 'unit': '%', 'better': 'up', 'default': 90, 'min': 1, 'max': 100, 'tolerance': 10,
     'question': 'A formalização com a coordenação está em dia?',
     'how': 'Registros (Word) com prazo no mês devolvidos até o prazo, sobre os já decididos (devolvidos ou vencidos).'},
    {'key': 'causa_informada', 'name': 'Ocorrências com causa informada', 'unit': '%', 'better': 'up', 'default': 95, 'min': 1, 'max': 100, 'tolerance': 5,
     'question': 'Nossos dados permitem aprender com as falhas?',
     'how': 'Ocorrências do mês com a coluna Origem preenchida.'},
)
KPI_DEFAULTS = {k['key']: k['default'] for k in KPIS}
MIN_BASE = 5        # percentual com menos casos que isso não é julgado (poucos dados)
RECURRENCE_DAYS = 60  # reincidência = mesma causa (família), no mesmo equipamento da mesma usina, em até 60 dias
MONTH_NAMES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro')
BLOCKS = ('0h–4h', '4h–8h', '8h–12h', '12h–16h', '16h–20h', '20h–24h')
WEEKDAYS = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')


def _norm(text):
    text = unicodedata.normalize('NFD', str(text or ''))
    return ' '.join(''.join(c for c in text if unicodedata.category(c) != 'Mn').casefold().split())


def _day(text):
    try:
        return date.fromisoformat(str(text or '')[:10])
    except ValueError:
        return None


def _moment(day, time):
    if not day:
        return None
    match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::\d{2})?', str(time or '').strip())
    hour, minute = (int(match[1]), int(match[2])) if match and int(match[1]) < 24 and int(match[2]) < 60 else (0, 0)
    return datetime(day.year, day.month, day.day, hour, minute)


def _filled(text):
    return _norm(text) not in ('', '-', 'nao informado', 'nao informada')


def validar_metas(metas):
    """Metas enviadas pela tela: só chaves conhecidas, inteiros dentro do limite de cada indicador."""
    if not isinstance(metas, dict) or set(metas) - set(KPI_DEFAULTS):
        raise ValueError('Meta desconhecida.')
    clean = {}
    for kpi in KPIS:
        value = metas.get(kpi['key'], kpi['default'])
        if type(value) is not int or not kpi['min'] <= value <= kpi['max']:
            raise ValueError(f'Meta de "{kpi["name"]}" deve ficar entre {kpi["min"]} e {kpi["max"]}.')
        clean[kpi['key']] = value
    return clean


def equipamentos(equipment, plant=''):
    """Chaves de equipamento para reincidência: GG01, GG02... ou o nome do equipamento. Usina inteira não conta."""
    text = _norm(equipment)
    units = {f'GG{int(number):02d}' for number in re.findall(r'(?:gg|ug)[\s\-]*[o0]?(\d{1,2})\b', text)}
    if units:
        return units
    text = re.sub(r'^usina\s*[-/]?\s*', '', text).strip(' .;')
    if not text or text == _norm(plant) or re.match(r'(usina|ufvs?|pch|cgh)\b', text) or _norm(plant) and _norm(plant) in text:
        return set()
    return {text.upper()}


def linhas(items, issues=(), families=None):
    """Linhas do CONTROLE em formato simples. As de ID repetido (sem card) entram só nas contagens, como no relatório mensal."""
    rows = _linhas(items, issues)
    for row in rows:
        row['family'] = familia(row['origin'], families if families is not None else CAUSE_DEFAULTS)
    return rows


def _linhas(items, issues):
    rows = []
    for item in items:
        if not item.get('control_row'):
            continue
        v, wf = item.get('values') or {}, item.get('workflow') or {}
        start_day = _day(v.get('start_date'))
        if not start_day:
            continue
        end_day = _day(v.get('end_date'))
        legacy = str(item.get('legacy_id') or '').strip()
        rows.append({
            'id': item.get('id'), 'label': f'Ocorrência {legacy}' if legacy else f'Forms {item.get("forms_id") or "?"}',
            'plant': ' '.join(str(v.get('plant') or '').split()) or 'Usina não informada',
            'equipment': str(v.get('equipment') or '').strip(), 'classification': str(v.get('classification') or '').strip() or 'Não classificada',
            'origin': str(v.get('origin') or '').strip() if _filled(v.get('origin')) else '',
            'coordination': str(v.get('coordination') or '').strip() or 'Sem coordenação',
            'start': _moment(start_day, v.get('start_time')), 'has_time': bool(re.match(r'\d{1,2}:\d{2}', str(v.get('start_time') or '').strip())),
            'end': _moment(end_day, v.get('end_time')) if end_day else None,
            'sla': _norm(v.get('sla')), 'closed': (wf.get('category') or wf.get('phase')) == 'closed', 'status': _norm(v.get('status')),
            'word': _norm(v.get('word_status')), 'doc_sent': _norm(v.get('document_sent')),
            'doc_deadline': _day(v.get('document_deadline_date')), 'doc_return': _day(v.get('document_return_date')),
        })
    for issue in issues or ():
        if issue.get('kind') != 'ambiguous_legacy':
            continue
        raw = (issue.get('raw_fields') or {}).get('controle') or {}
        col = lambda letter: next((value for key, value in raw.items() if key.startswith(f'{letter} ·')), '')
        serial = col('F')
        if not isinstance(serial, (int, float)) or serial <= 0:
            continue
        start_day = date(1899, 12, 30) + timedelta(days=int(serial))
        rows.append({'id': None, 'label': f'Ocorrência {col("C")}', 'plant': ' '.join(str(col('M') or '').split()) or 'Usina não informada', 'equipment': '',
                     'classification': str(col('D') or '').strip() or 'Não classificada', 'origin': '', 'coordination': 'Sem coordenação',
                     'start': _moment(start_day, ''), 'has_time': False, 'end': None, 'sla': _norm(col('S')), 'closed': _norm(col('Z')) == 'encerrada',
                     'status': _norm(col('H')), 'word': _norm(col('W')), 'doc_sent': _norm(col('X')), 'doc_deadline': None, 'doc_return': None, 'pseudo': True})
    return rows


def _month_of(row):
    return row['start'].strftime('%Y-%m')


def _shift(month, step):
    year, number = int(month[:4]), int(month[5:7]) - 1 + step
    return f'{year + number // 12:04d}-{number % 12 + 1:02d}'


def _month_end(month):
    return _day(_shift(month, 1) + '-01') - timedelta(days=1)


def mes_extenso(month):
    return f'{MONTH_NAMES[int(month[5:7]) - 1]} de {month[:4]}'


def _ids(rows):
    return [row['id'] for row in rows if row.get('id') is not None]


def _top(rows, field):
    counts = Counter(row[field] for row in rows if row.get(field))
    return counts.most_common(1)[0] if counts else ('', 0)


def _plural(count, one, many):
    return f'{count} {one if count == 1 else many}'


def situacao(kpi, value, base, meta):
    """ok = meta cumprida · warn = perto da meta · bad = fora · none = sem dados para julgar."""
    if value is None or (kpi['unit'] == '%' and base < MIN_BASE) or (kpi['key'] == 'duracao_longas' and base < 3):
        return 'none'
    gap = (meta - value) if kpi['better'] == 'up' else (value - meta)
    return 'ok' if gap <= 0 else 'warn' if gap <= kpi['tolerance'] else 'bad'


def _aberta_em(row, ref):
    """Em acompanhamento na data: já tinha começado, não tinha terminado e não foi dada como encerrada sem data de fim."""
    if row['start'].date() > ref:
        return False
    if row['end']:
        return row['end'].date() > ref
    return not row['closed']


def _repeticoes(scope_rows):
    """Para cada linha de manutenção: os equipamentos em que a mesma causa já tinha ocorrido, na mesma usina, em 60 dias.

    "Mesma causa" = mesma família de causa. Origem em branco ou sem família não conta como repetição."""
    seen, repeats = {}, {}
    for row in sorted(scope_rows, key=lambda r: r['start']):
        keys = equipamentos(row['equipment'], row['plant'])
        row['_keys'] = keys
        limit = row['start'] - timedelta(days=RECURRENCE_DAYS)
        family = row['family'] if row['family'] not in ('', NO_FAMILY) else None
        repeats[id(row)] = {key for key in keys if family and any(limit <= moment < row['start'] for moment in seen.get((_norm(row['plant']), key, family), ()))}
        for key in keys:
            if family:
                seen.setdefault((_norm(row['plant']), key, family), []).append(row['start'])
    return repeats


def _kpis_do_mes(month, rows, scope, repeats, metas, today):
    ref = min(today, _month_end(month))
    now = datetime(today.year, today.month, today.day, 23, 59)
    of_month = [row for row in rows if _month_of(row) == month]
    maintenance = [row for row in of_month if _norm(row['classification']) in scope]
    result = {}

    ok = [row for row in maintenance if row['sla'] == 'ok']
    late = [row for row in maintenance if row['sla'] in ('superior 24h', 'analisar')]
    base = len(ok) + len(late)
    plant, count = _top(late, 'plant')
    reading = 'Nenhuma ocorrência de manutenção decidida neste mês.' if not base else \
        f'Todas as {base} voltaram em até 24 h.' if not late else \
        f'{len(late)} de {base} passaram de 24 h' + (f'; {count} delas em {plant}.' if count >= 2 else '.')
    result['resolucao_24h'] = {'value': round(len(ok) / base * 100, 1) if base else None, 'n': base, 'failing': _ids(late), 'reading': reading}

    durations = sorted(((((row['end'] or now) - row['start']).total_seconds() / 86400, row) for row in late), key=lambda pair: -pair[0])
    value = round(statistics.median(d for d, _ in durations), 1) if durations else None
    over = [row for d, row in durations if d > metas['duracao_longas']]
    if durations:
        worst_days, worst = durations[0]
        reading = f'{_plural(len(durations), "passou", "passaram")} de 24 h; a mais demorada: {worst["label"]} ({worst["plant"]}, {worst_days:.0f} dias{"" if worst["end"] else ", ainda aberta"}).'
    else:
        reading = 'Nenhuma ocorrência de manutenção passou de 24 h neste mês.'
    result['duracao_longas'] = {'value': value, 'n': len(durations), 'failing': _ids(over), 'reading': reading}

    opened = [row for row in rows if _aberta_em(row, ref)]
    old = sorted((row for row in opened if (ref - row['start'].date()).days > 30), key=lambda r: r['start'])
    reading = f'Nenhuma das {len(opened)} em acompanhamento passa de 30 dias.' if not old else \
        f'{len(old)} de {len(opened)} em acompanhamento passam de 30 dias; a mais antiga: {old[0]["label"]} ({old[0]["plant"]}, {(ref - old[0]["start"].date()).days} dias).'
    result['backlog_30d'] = {'value': len(old), 'n': len(opened), 'failing': _ids(old), 'reading': reading}

    eligible = [row for row in maintenance if row.get('_keys')]
    repeated = [row for row in eligible if repeats.get(id(row))]
    pairs = Counter((row['plant'], key, row['family']) for row in repeated for key in repeats[id(row)])
    reading = 'Nenhuma ocorrência de manutenção com equipamento identificado neste mês.' if not eligible else \
        'Nenhuma causa voltou no mesmo equipamento em 60 dias.' if not repeated else \
        f'{len(repeated)} de {len(eligible)} repetem uma causa que o mesmo equipamento já teve nos 60 dias anteriores; a que mais volta: {" · ".join(pairs.most_common(1)[0][0])}.'
    result['reincidencia'] = {'value': round(len(repeated) / len(eligible) * 100, 1) if eligible else None, 'n': len(eligible), 'failing': _ids(repeated), 'reading': reading}

    due = [row for row in rows if row['doc_deadline'] and row['doc_deadline'].strftime('%Y-%m') == month and (row['doc_return'] or row['doc_deadline'] < today)]
    missed = [row for row in due if not row['doc_return'] or row['doc_return'] > row['doc_deadline']]
    waiting = sum(1 for row in missed if not row['doc_return'])
    reading = 'Nenhum registro com prazo decidido neste mês.' if not due else f'Os {len(due)} registros voltaram no prazo.' if not missed else \
        f'{len(missed)} de {len(due)} fora do prazo' + (f' ({waiting} ainda sem devolução).' if waiting else '.')
    result['registro_prazo'] = {'value': round((len(due) - len(missed)) / len(due) * 100, 1) if due else None, 'n': len(due), 'failing': _ids(missed), 'reading': reading}

    blank = [row for row in of_month if not row['origin'] and not row.get('pseudo')]
    known = [row for row in of_month if not row.get('pseudo')]
    cls, count = _top(blank, 'classification')
    reading = 'Sem ocorrências neste mês.' if not known else 'Todas com a causa preenchida.' if not blank else \
        f'{len(blank)} de {len(known)} sem causa' + (f'; {count} são de {cls}.' if count >= 2 else '.')
    result['causa_informada'] = {'value': round((len(known) - len(blank)) / len(known) * 100, 1) if known else None, 'n': len(known), 'failing': _ids(blank), 'reading': reading}

    for kpi in KPIS:
        entry = result[kpi['key']]
        entry['status'] = situacao(kpi, entry['value'], entry['n'], metas[kpi['key']])
    return result


def _media_anterior(counter_by_month, month, key=None):
    """Média dos 3 meses anteriores (meses sem ocorrência contam como zero)."""
    previous = [_shift(month, -step) for step in (1, 2, 3)]
    values = [counter_by_month.get(m, Counter())[key] if key is not None else sum(counter_by_month.get(m, Counter()).values()) for m in previous]
    return sum(values) / 3


def _achados(month, rows, scope, repeats, kpis, by_class, by_plant, today):
    ref = min(today, _month_end(month))
    of_month = [row for row in rows if _month_of(row) == month]
    found = []
    # ref identifica o achado (para não abrir duas ações iguais); metric diz o que medir antes e depois da ação.
    add = lambda level, kind, title, detail, action, items=(), ref='', metric=None: found.append(
        {'level': level, 'kind': kind, 'title': title, 'detail': detail, 'action': action, 'items': _ids(items), 'ref': f'{kind}:{ref or month}', 'metric': metric})
    total, baseline = len(of_month), _media_anterior(by_class, month)
    partial = ref < _month_end(month)

    # 1. Volume fora do normal: quem puxou?
    if not partial and baseline >= 5 and total >= baseline * 1.3 and total - baseline >= 10:
        gains = sorted(((count - _media_anterior(by_class, month, name), name, count) for name, count in by_class.get(month, Counter()).items()), reverse=True)
        gain, name, count = gains[0]
        internal = _norm(name) in scope
        add(2, 'volume', f'Volume {round((total / baseline - 1) * 100)}% acima do normal, puxado por {name}',
            f'{total} ocorrências contra a média de {baseline:.0f} dos 3 meses anteriores. {name}: {count} (média {count - gain:.0f}).',
            'Leve para a reunião com as coordenações: o que mudou nas usinas?' if internal else
            'Não é falha de manutenção: separe esse volume ao avaliar as usinas e veja se cabe tratar com a origem (rede, clima, TI).',
            [row for row in of_month if row['classification'] == name],
            metric={'type': 'classe', 'args': [name], 'label': f'ocorrências de {name} em 30 dias', 'better': 'down'})

    # 2. Usina muito acima do próprio padrão.
    spikes = []
    for plant, count in by_plant.get(month, Counter()).items():
        usual = _media_anterior(by_plant, month, plant)
        if count >= 5 and count - usual >= 4 and count >= max(usual, 1) * 2:
            spikes.append((count / max(usual, 1), plant, count, usual))
    for ratio, plant, count, usual in sorted(spikes, reverse=True)[:2]:
        own = [row for row in of_month if row['plant'] == plant]
        cls, cls_count = _top(own, 'classification')
        origin, origin_count = _top(own, 'origin')
        coordination = _top(own, 'coordination')[0]
        add(2, 'usina', f'{plant}: {count} ocorrências, {f"{ratio:.1f}".replace(".", ",")}× o normal dela',
            f'A média dos 3 meses anteriores era {usual:.0f}. Principal classificação: {cls} ({cls_count})' + (f'; principal causa: {origin} ({origin_count}).' if origin else '.'),
            f'Pergunte à coordenação {coordination} o que mudou nessa usina.' if _norm(cls) in scope else
            f'A maior parte não é manutenção ({cls}). Junte datas e horários e leve à origem do problema.', own,
            ref=f'{plant}|{month}', metric={'type': 'usina', 'args': [plant], 'label': f'ocorrências de {plant} em 30 dias', 'better': 'down'})

    # 3. Mesma causa voltando no mesmo equipamento nos últimos 60 dias.
    window = [row for row in rows if _norm(row['classification']) in scope and ref - timedelta(days=RECURRENCE_DAYS) <= row['start'].date() <= ref]
    groups = {}
    for row in window:
        if row['family'] in ('', NO_FAMILY):
            continue
        for key in row.get('_keys') or ():
            groups.setdefault((row['plant'], key, row['family']), []).append(row)
    for (plant, key, family), group in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:3]:
        if len(group) < 2:
            continue
        texts = Counter(row['origin'] for row in group).most_common(3)
        add(3, 'reincidencia', f'{plant} · {key}: {family} voltou {len(group)} vezes em 60 dias',
            'Como foi registrado: ' + ', '.join(f'{name} ({count})' for name, count in texts) + '.',
            f'Peça análise de causa raiz à coordenação {_top(group, "coordination")[0]}: o reparo não está eliminando a causa.', group,
            ref=f'{plant}|{key}|{family}', metric={'type': 'equip', 'args': [plant, key, family], 'label': f'ocorrências de {family} em 60 dias', 'better': 'down'})

    # 4. Ocorrências muito antigas em aberto.
    ancient = sorted((row for row in rows if _aberta_em(row, ref) and (ref - row['start'].date()).days > 60), key=lambda r: r['start'])
    if ancient:
        sample = ', '.join(f'{row["label"]} ({row["plant"]}, {(ref - row["start"].date()).days} dias)' for row in ancient[:3])
        add(3, 'antigas', f'{_plural(len(ancient), "ocorrência aberta", "ocorrências abertas")} há mais de 60 dias',
            f'As mais antigas: {sample}.', 'Cobre a previsão de cada uma, ou encerre as que já normalizaram.', ancient,
            ref='abertas', metric={'type': 'pendentes', 'args': ['abertas', _ids(ancient)], 'label': 'dessas ainda abertas', 'better': 'down'})

    # 5. Do mês: constam como abertas, sem data de fim, mas fora do acompanhamento.
    loose = [row for row in of_month if not row['end'] and row['closed'] and 'abert' in row['status'] and not row.get('pseudo')]
    if loose:
        add(2, 'sem_fim', f'{_plural(len(loose), "ocorrência", "ocorrências")} de {MONTH_NAMES[int(month[5:7]) - 1]} sem data de fim e fora do acompanhamento',
            f'Status "Aberta" na planilha, mas a conclusão já é "Encerrada". Mais comuns: {_top(loose, "classification")[0]} em {_top(loose, "plant")[0]}.',
            'Preencha a data de fim (ou reabra o acompanhamento): sem ela, a duração e a disponibilidade ficam distorcidas.', loose,
            metric={'type': 'pendentes', 'args': ['sem_fim', _ids(loose)], 'label': 'dessas ainda sem data de fim', 'better': 'down'})

    # 6. Registro ainda não enviado com a ocorrência já encerrada.
    unsent = [row for row in of_month if row['end'] and row['word'] not in ('', '-', 'nao se aplica') and 'enviado' not in row['doc_sent'] and not row['doc_return']]
    if unsent:
        add(2, 'registro', f'{_plural(len(unsent), "registro", "registros")} de {MONTH_NAMES[int(month[5:7]) - 1]} ainda não {"enviado" if len(unsent) == 1 else "enviados"} à coordenação',
            'A ocorrência já terminou, mas o Word não foi enviado: ' + ', '.join(row['label'] for row in unsent[:5]) + '.',
            'Gere e envie o registro: o prazo da coordenação só começa a contar no envio.', unsent,
            metric={'type': 'pendentes', 'args': ['registro', _ids(unsent)], 'label': 'desses ainda não enviados', 'better': 'down'})

    # 7. Coordenação bem abaixo das demais (3 meses, para ter base).
    recent = [row for row in rows if _norm(row['classification']) in scope and _shift(month, -2) <= _month_of(row) <= month and row['sla'] in ('ok', 'superior 24h', 'analisar')]
    if len(recent) >= 10:
        overall = sum(1 for row in recent if row['sla'] == 'ok') / len(recent) * 100
        for coordination, group in sorted(_group(recent, 'coordination').items()):
            rate = sum(1 for row in group if row['sla'] == 'ok') / len(group) * 100
            if len(group) >= MIN_BASE and overall - rate >= 15:
                add(2, 'coordenacao', f'Coordenação {coordination}: {rate:.0f}% da manutenção em até 24 h (geral: {overall:.0f}%)',
                    f'{len(group)} ocorrências de manutenção nos últimos 3 meses.', 'Compare com a coordenação de melhor resultado: equipe, peças ou acesso?',
                    [row for row in group if row['sla'] != 'ok'], ref=coordination,
                    metric={'type': 'coord24', 'args': [coordination], 'label': '% da manutenção em até 24 h (90 dias)', 'better': 'up'})

    # 8. Concentração de horário.
    timed = [row for row in of_month if row['has_time']]
    if len(timed) >= 20:
        block, count = Counter(row['start'].hour // 4 for row in timed).most_common(1)[0]
        if count / len(timed) >= 0.35:
            add(1, 'horario', f'{round(count / len(timed) * 100)}% das ocorrências começam entre {BLOCKS[block].replace("–", " e ")}',
                f'{count} de {len(timed)} com hora informada.', 'Se for rede ou clima, é esperado. Se for operação, veja o que acontece nesse turno.',
                [row for row in timed if row['start'].hour // 4 == block])

    # 9. Indicadores fora da meta entram no guia com a leitura deles.
    for kpi in KPIS:
        entry = kpis[kpi['key']]
        if entry['status'] == 'bad' and kpi['key'] == 'causa_informada':
            add(1, 'causa', 'Causa em branco impede aprender com as falhas', entry['reading'], 'Preencha a Origem das ocorrências listadas, começando pelas de manutenção.',
                [row for row in of_month if not row['origin']],
                metric={'type': 'sem_causa', 'args': [], 'label': 'ocorrências sem causa em 30 dias', 'better': 'down'})
    return sorted(found, key=lambda entry: -entry['level'])


def medir(metric, rows, scope, today):
    """Valor atual da medida de uma ação (o mesmo cálculo serve para o "antes" e para o "depois")."""
    if not metric:
        return None
    kind, args = metric.get('type'), metric.get('args') or []
    recent = lambda days: [row for row in rows if today - timedelta(days=days) <= row['start'].date() <= today]
    if kind == 'equip':
        plant, key, family = args
        return sum(1 for row in recent(RECURRENCE_DAYS) if _norm(row['classification']) in scope and row['plant'] == plant
                   and row['family'] == family and key in equipamentos(row['equipment'], row['plant']))
    if kind == 'usina':
        return sum(1 for row in recent(30) if row['plant'] == args[0])
    if kind == 'classe':
        return sum(1 for row in recent(30) if row['classification'] == args[0])
    if kind == 'sem_causa':
        return sum(1 for row in recent(30) if not row['origin'] and not row.get('pseudo'))
    if kind == 'coord24':
        decided = [row for row in recent(90) if row['coordination'] == args[0] and _norm(row['classification']) in scope and row['sla'] in ('ok', 'superior 24h', 'analisar')]
        return round(sum(1 for row in decided if row['sla'] == 'ok') / len(decided) * 100) if decided else None
    if kind == 'pendentes':
        wanted = set(args[1])
        pending = {'abertas': lambda row: _aberta_em(row, today), 'sem_fim': lambda row: not row['end'],
                   'registro': lambda row: 'enviado' not in row['doc_sent'] and not row['doc_return']}[args[0]]
        return sum(1 for row in rows if row['id'] in wanted and pending(row))
    return None


def acompanhar(actions, rows, scope, today):
    """Junta a cada ação o valor de agora, o efeito (melhorou, igual, piorou) e o que revisar."""
    result = []
    for action in actions:
        metric = action.get('metric') or {}
        before, current = metric.get('value'), medir(metric, rows, scope, today)
        effect = None
        if before is not None and current is not None:
            gain = (before - current) if metric.get('better') == 'down' else (current - before)
            effect = 'melhorou' if gain > 0 else 'piorou' if gain < 0 else 'igual'
        days_done = (today - date.fromisoformat(action['done_at'][:10])).days if action.get('done_at') else None
        late = action['status'] == 'aberta' and action['due'] < today.isoformat()
        # 30 dias depois de concluída sem melhora: a ação não resolveu.
        review = 'sem_melhora' if days_done is not None and days_done >= 30 and effect in ('igual', 'piorou') else \
            'aguardando' if days_done is not None and days_done < 30 else None
        result.append({**action, 'current': current, 'effect': effect, 'late': late, 'days_done': days_done, 'review': review})
    return result


def _group(rows, field):
    groups = {}
    for row in rows:
        groups.setdefault(row[field], []).append(row)
    return groups


def _padroes(month, rows, scope, by_class, by_plant, today):
    ref = min(today, _month_end(month))
    of_month = [row for row in rows if _month_of(row) == month]
    months = [_shift(month, -step) for step in range(5, -1, -1)]

    def heat(counter_by_month, limit):
        totals = Counter()
        for m in months:
            totals.update(counter_by_month.get(m, Counter()))
        return [{'name': name, 'counts': [counter_by_month.get(m, Counter())[name] for m in months], 'total': total} for name, total in totals.most_common(limit)]

    # Causas por família; em cada família, os textos da planilha que caíram nela.
    causes = Counter(row['family'] for row in of_month if row['family'])
    texts = {family: Counter(row['origin'] for row in of_month if row['family'] == family).most_common(6) for family in causes}
    coords = []
    for name, group in sorted(_group(of_month, 'coordination').items(), key=lambda pair: -len(pair[1])):
        decided = [row for row in group if _norm(row['classification']) in scope and row['sla'] in ('ok', 'superior 24h', 'analisar')]
        opened = [row for row in rows if row['coordination'] == name and _aberta_em(row, ref)]
        coords.append({'name': name, 'total': len(group), 'maintenance': len(decided),
                       'pct24': round(sum(1 for row in decided if row['sla'] == 'ok') / len(decided) * 100) if decided else None,
                       'open': len(opened), 'open30': sum(1 for row in opened if (ref - row['start'].date()).days > 30)})
    since = ref - timedelta(days=89)
    week = [[0] * len(BLOCKS) for _ in WEEKDAYS]
    for row in rows:
        if row['has_time'] and since <= row['start'].date() <= ref:
            week[row['start'].weekday()][row['start'].hour // 4] += 1
    return {'months': months, 'plants': heat(by_plant, 12), 'classes': heat(by_class, 8),
            'causes': [{'name': name, 'count': count, 'texts': [{'name': text, 'count': n} for text, n in texts[name]]} for name, count in causes.most_common(12)],
            'causes_total': sum(causes.values()),
            'causes_blank': sum(1 for row in of_month if not row['origin']),
            'coordinations': coords, 'week': {'days': list(WEEKDAYS), 'blocks': list(BLOCKS), 'counts': week, 'from': since.isoformat(), 'to': ref.isoformat()}}


def preparar(items, config, issues=(), families=None, today=None):
    """Linhas até hoje e o conjunto de classificações de manutenção (as que exigem registro)."""
    today = today or date.today()
    rows = [row for row in linhas(items, issues, families) if row['start'].date() <= today]
    types = {_norm(t.get('name')): t for t in config.get('flow_types') or ()}
    scope = {_norm(f.get('classification')) for f in config.get('flows') or () if (types.get(_norm(f.get('flow'))) or {}).get('coordinator')}
    return rows, scope


def sem_familia(rows, limit=40):
    """Textos de Origem que ainda não caem em nenhuma família, do mais frequente ao menos."""
    return [{'name': name, 'count': count} for name, count in Counter(row['origin'] for row in rows if row['family'] == NO_FAMILY).most_common(limit)]


def calcular(items, config, metas=None, issues=(), today=None, families=None):
    """Monta os KPIs de todos os meses. `metas` são as metas salvas (as ausentes usam o padrão)."""
    today = today or date.today()
    metas = {**KPI_DEFAULTS, **{k: v for k, v in (metas or {}).items() if k in KPI_DEFAULTS}}
    rows, scope = preparar(items, config, issues, families, today)
    scope_names = [f['classification'] for f in config.get('flows') or () if _norm(f.get('classification')) in scope]
    definitions = [{**{k: kpi[k] for k in ('key', 'name', 'unit', 'better', 'question', 'how', 'min', 'max', 'default')}, 'target': metas[kpi['key']]} for kpi in KPIS]
    current = today.strftime('%Y-%m')
    if not rows:
        return {'targets': metas, 'definitions': definitions, 'scope': scope_names, 'months': [], 'current': current, 'by_month': {}}
    repeats = _repeticoes([row for row in rows if _norm(row['classification']) in scope])
    by_class, by_plant = {}, {}
    for row in rows:
        by_class.setdefault(_month_of(row), Counter())[row['classification']] += 1
        by_plant.setdefault(_month_of(row), Counter())[row['plant']] += 1
    months, month = [], min(_month_of(row) for row in rows)
    while month <= current:
        months.append(month)
        month = _shift(month, 1)
    by_month = {}
    for month in months:
        kpis = _kpis_do_mes(month, rows, scope, repeats, metas, today)
        total = sum(by_class.get(month, Counter()).values())
        judged = [kpis[kpi['key']]['status'] for kpi in KPIS]
        by_month[month] = {
            'volume': {'total': total, 'baseline': round(_media_anterior(by_class, month), 1),
                       'classes': [{'name': name, 'count': count} for name, count in by_class.get(month, Counter()).most_common()],
                       'maintenance': sum(count for name, count in by_class.get(month, Counter()).items() if _norm(name) in scope)},
            'score': {status: judged.count(status) for status in ('ok', 'warn', 'bad', 'none')},
            'kpis': kpis,
            'findings': _achados(month, rows, scope, repeats, kpis, by_class, by_plant, today),
            'patterns': _padroes(month, rows, scope, by_class, by_plant, today),
        }
    return {'targets': metas, 'definitions': definitions, 'scope': scope_names, 'months': months, 'current': current, 'by_month': by_month}
