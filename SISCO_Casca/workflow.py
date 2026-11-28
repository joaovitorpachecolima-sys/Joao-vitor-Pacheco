"""Etapas administrativas e próxima ação. Nenhum efeito externo neste módulo."""
from datetime import datetime, timedelta, timezone
from importer import _normalize
import re

# Tipos de fluxo: o comportamento vem do tipo; cada classificação só escolhe o seu.
# recipient: 'coordenacao' = Para é a coordenação da usina;
#            'classificacao' = Para é o e-mail da própria classificação, sem CC.
RECIPIENT_MODES = ('coordenacao', 'classificacao')
DEFAULT_FLOW_TYPES = [
    {'name': name, 'followup': follow, 'coordinator': coord, 'recipient': recipient}
    for name, follow, coord, recipient in [('COMPLETO', True, True, 'coordenacao'),
        ('SEM_COORD', True, False, 'coordenacao'), ('INFORMATIVO', False, False, 'coordenacao'),
        ('TI', True, False, 'classificacao')]
]

# Parâmetros das regras configuráveis em "Regras do fluxo" (valores padrão = comportamento até 10/11/2026).
#   sla_hours: acima disso a ocorrência é "Superior 24h"/"Analisar" e sai do monitoramento.
#   followup_hour: hora das cobranças de status (dia seguinte à previsão / após o intervalo de cobrança).
#   phase_targets: meta de dias em cada fase do quadro (acima dela o card fica "parado").
#   ug_occurrence_days: idade máxima de uma ocorrência sem encerramento para explicar UG parada.
RULE_DEFAULTS = {'sla_hours': 24, 'followup_hour': 9,
                 'phase_targets': {'review': 1, 'monitoring': 1, 'sending': 1, 'forms': 7, 'documents': 14},
                 'ug_occurrence_days': 120}
RULE_KEYS = ('flow_types', 'flows', 'document_days', 'reminder_days') + tuple(RULE_DEFAULTS)

DEFAULT_FLOWS = [
    {'classification': name, 'flow': flow, 'coordinator': coord, 'followup': follow}
    for name, flow, coord, follow in [('Manutenção','COMPLETO',True,True),
        ('Distribuidora','SEM_COORD',False,True),('Operacional','SEM_COORD',False,True),
        ('TI','TI',False,True),('Civíl','INFORMATIVO',False,False),
        ('Informativo','INFORMATIVO',False,False),('Ambiental','INFORMATIVO',False,False)]
]

def parse(value):
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None) if value else None
    except (ValueError, TypeError):
        return None

def full_end(values):
    return bool(parse(values.get('end_date', '') + 'T' + values.get('end_time', '')))

def document_deadline(values):
    saved=parse(values.get('document_deadline_date'))
    stamp=re.fullmatch(r'\s*Email enviado \((\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})\)\s*',values.get('document_sent',''),re.I)
    if stamp and saved:
        try: sent=datetime.strptime(stamp[1]+' '+stamp[2],'%d/%m/%Y %H:%M')
        except ValueError: return saved
        if (saved.date()-sent.date()).days in (7,14): return (sent+timedelta(days=7)).replace(hour=0,minute=0)
    return saved

def forms_deadline(values, config):
    forecast=parse(values.get('forecast_date'))
    sent=parse(values.get('forms_sent_at'))
    recorded=parse(values.get('forms_due_at'))
    hour=config.get('followup_hour',RULE_DEFAULTS['followup_hour'])
    baseline=((forecast+timedelta(days=1)).replace(hour=hour,minute=0,second=0) if forecast else
              (sent+timedelta(days=config.get('reminder_days',7))).replace(hour=hour,minute=0,second=0) if sent else None)
    return max(recorded,baseline) if recorded and baseline else recorded or baseline

def derive(item, config, clock=None):
    """Situação da ocorrência. Com o fluxo publicado na configuração (`config['fluxo']`), o motor percorre o
    fluxo (fluxo.py); sem ele, usa as regras escritas abaixo. Os dois dão o mesmo resultado (test_fluxo.py)."""
    if config.get('fluxo'):
        from fluxo import executar
        return executar(config['fluxo'], item, config, clock)
    return derive_codigo(item, config, clock)

def derive_codigo(item, config, clock=None):
    clock = (clock or datetime.now(timezone(timedelta(hours=-3)))).replace(tzinfo=None)
    v = item['values']
    flow = next((f for f in config['flows'] if _normalize(f['classification']) == _normalize(v.get('classification'))), None)
    state = _normalize(v.get('maintenance_status'))
    end = full_end(v)
    start = parse(v.get('start_date','') + 'T' + v.get('start_time',''))
    finished = parse(v.get('end_date','') + 'T' + v.get('end_time',''))
    duration = ((finished or clock) - start).total_seconds()/3600 if start else None
    limit = config.get('sla_hours', RULE_DEFAULTS['sla_hours'])
    sla = ('Superior 24h' if end else 'Analisar') if duration is not None and duration > limit else ('Ok' if end and duration is not None else '')
    # Missing/invalid times cannot establish the 24h threshold.
    if duration is None or duration < 0: sla = ''
    forecast = parse(v.get('forecast_date'))
    forms_due = forms_deadline(v,config)
    sent_at = parse(v.get('forms_sent_at'))
    deadline = document_deadline(v)
    next_doc = parse(v.get('document_next_date'))
    result = {'sla':sla, 'hours':round(duration,2) if duration is not None else None,
              'can_act':bool(item['reviewed']), 'due_at':'', 'category':'monitoring',
              'action':'Consultar ocorrência', 'action_kind':'details'}

    def done(conclusion, category='monitoring', action='Consultar ocorrência', kind='details', due=None):
        result.update(calculated_conclusion=conclusion, conclusion=conclusion, category=category,
                      action=action, action_kind=kind, due_at=due.isoformat(timespec='minutes') if due else '')
        return result

    if not v.get('classification') or not flow:
        return done('Aguardando classificação','review','Classificar e revisar','review')
    if not item['reviewed']:
        return done('Aguardando revisão','review','Revisar ocorrência','review')
    if not flow['followup'] or sla == 'Ok':
        return done('Encerrada','closed')
    if sla not in ('Analisar','Superior 24h'):
        return done('Encerrada','closed') if end else done('Em monitoramento 24h',due=start+timedelta(hours=limit) if start else None)
    if end:
        if not flow['coordinator']: return done('Encerrada','closed')
        if 'enviado' in _normalize(v.get('document_sent')):
            if parse(v.get('document_return_date')): return done('Encerrada','closed')
            if deadline and clock.date() > deadline.date():
                waiting=next_doc and clock.date()<next_doc.date()
                return done('Prazo do registro vencido','documents','Aguardar próxima cobrança' if waiting else 'Cobrar retorno do registro','return' if waiting else 'document_reminder',next_doc or deadline)
            if next_doc and clock.date() >= next_doc.date():
                return done('Solicitação de retorno do registro pendente','documents','Conferir cobrança do registro','document_reminder',next_doc)
            return done('Aguardando retorno do registro','documents','Registrar retorno do documento','return',deadline)
        if 'gerado' in _normalize(v.get('word_status')):
            return done('Aguardando envio do registro','documents','Conferir e enviar registro','document')
        return done('Aguardando geração do registro','documents','Criar Word','word')
    if any(word in state for word in ('finaliz','encerr','conclu')):
        return done('Aguardando dados de encerramento','forms','Completar encerramento','return')
    if state in ('em aberto','aberto','aberta'):
        if not forecast:
            due = forms_due
            return done('Aguardando previsão de encerramento','forms','Registrar previsão ou cobrar retorno','return',due)
        first_due = (forecast+timedelta(days=1)).replace(hour=config.get('followup_hour',RULE_DEFAULTS['followup_hour']),minute=0)
        if clock < first_due:
            return done('Aguardando prazo informado','forms','Consultar previsão','return',forms_due or first_due)
        if not forms_due or clock >= forms_due:
            return done('Solicitação de retorno pendente','forms','Cobrar atualização','reminder',forms_due or first_due)
        return done('Aguardando atualização do status','forms','Consultar retorno','return',forms_due)
    if 'enviado' in _normalize(v.get('forms_sent')) and sent_at:
        due = forms_due
        return done('Aguardando resposta do Forms','forms','Cobrar atualização' if due and clock>=due else 'Consultar retorno','reminder' if due and clock>=due else 'return',due)
    if 'fila' in _normalize(v.get('forms_sent')):
        return done('Forms em fila','forms','Conferir fila de origem','details')
    return done('Aguardando envio do Forms','forms','Solicitar atualização','request')

def phase_since_estimate(values, category):
    """
    Melhor estimativa de quando a ocorrência entrou na fase atual, para quem já estava nela
    antes de o SISCO começar a registrar as mudanças de fase. Formato 'AAAA-MM-DDTHH:MM' ou ''.
    """
    start = parse(values.get('start_date','') + 'T' + (values.get('start_time') or '00:00'))
    end = parse(values.get('end_date','') + 'T' + (values.get('end_time') or '00:00'))
    stamp = re.search(r'(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2})', values.get('document_sent','') or '')
    sent_doc = datetime(int(stamp[3]), int(stamp[2]), int(stamp[1]), int(stamp[4]), int(stamp[5])) if stamp else None
    candidates = {'forms': [parse(values.get('forms_sent_at')), start],
                  'documents': [sent_doc, end, start],
                  'closed': [parse(values.get('document_return_date')), end, start]}.get(category, [start])
    found = next((c for c in candidates if c), None)
    return found.isoformat(timespec='minutes') if found else ''

SENDING_CONCLUSIONS = {'aguardando envio do forms', 'forms em fila', 'aguardando envio do registro'}

CONCLUSION_ACTIONS = {
    'aguardando classificacao':('review','Classificar e revisar','review'),
    'aguardando revisao':('review','Revisar ocorrência','review'),
    'aguardando envio do forms':('forms','Solicitar atualização','request'),
    'forms em fila':('forms','Conferir fila de origem','details'),
    'aguardando resposta do forms':('forms','Consultar retorno','return'),
    'aguardando previsao de encerramento':('forms','Registrar previsão','return'),
    'aguardando dados de encerramento':('forms','Completar encerramento','return'),
    'aguardando prazo informado':('forms','Consultar previsão','return'),
    'solicitacao de retorno pendente':('forms','Cobrar atualização','reminder'),
    'aguardando atualizacao do status':('forms','Consultar retorno','return'),
    'aguardando geracao do registro':('documents','Criar Word','word'),
    'aguardando envio do registro':('documents','Conferir e enviar registro','document'),
    'aguardando retorno do registro':('documents','Registrar retorno do documento','return'),
    'solicitacao de retorno do registro pendente':('documents','Conferir cobrança do registro','document_reminder'),
    'prazo do registro vencido':('documents','Cobrar retorno do registro','document_reminder'),
    'encerrada':('closed','Consultar histórico','details'),
    'informativo':('closed','Consultar ocorrência','details'),
    'em monitoramento 24h':('monitoring','Acompanhar duração','details')
}

def describe(item, config, clock=None):
    result = derive(item,config,clock)
    source = item.get('source_values',{}).get('conclusion','')
    # Historical conclusion remains the visible reference until a local workflow
    # edit/action. Local review is an independent authorization, never inherited.
    result['source_conclusion'] = source
    result['basis'] = 'local'
    if source and not item.get('workflow_local') and not item['reviewed']:
        result['conclusion'] = source
        result['basis'] = 'excel'
        category, action, kind = CONCLUSION_ACTIONS.get(_normalize(source),('monitoring','Conferir conclusão','details'))
        result.update(category=category,action=action,action_kind=kind)
        v = item['values']
        due = v.get('document_next_date') if kind=='document_reminder' else v.get('document_deadline_date') if category=='documents' else v.get('forms_due_at') if category=='forms' else ''
        result['due_at'] = due or ''
    # Fase do quadro: igual à categoria, exceto o que está pronto para sair e ainda não foi enviado
    # (Forms ou registro), que fica em "Aguardando envio" entre o monitoramento e o retorno.
    result['phase'] = 'sending' if _normalize(result['conclusion']) in SENDING_CONCLUSIONS else result['category']
    clock=(clock or datetime.now(timezone(timedelta(hours=-3)))).replace(tzinfo=None)
    deadline=document_deadline(item['values'])
    result['document_deadline']=deadline.date().isoformat() if deadline else ''
    if result['category']=='documents':
        result['overdue']=bool(deadline and clock.date()>deadline.date() and not item['values'].get('document_return_date'))
    else:
        result['overdue']=bool(parse(result['due_at']) and clock >= parse(result['due_at']))
    return result
