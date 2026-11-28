"""O fluxo das ocorrências como DADO (etapa 1 do editor BPMN).

Hoje as regras vivem no código (workflow.derive). Aqui o mesmo comportamento é descrito como um grafo:
- **decisão**: uma pergunta do catálogo CONDICOES, com um caminho para "sim" e outro para "não";
- **situação**: onde a ocorrência para, com conclusão, fase, próxima ação e prazo (do catálogo PRAZOS).

`executar(fluxo, item, config)` percorre o grafo e devolve o mesmo resultado de workflow.derive.
`validar(fluxo)` recusa o que não executa: pergunta desconhecida, caminho sem destino, laço, elemento solto.

Nesta etapa o fluxo não manda em nada: o motor atual continua valendo, e o teste de equivalência garante que
o fluxo padrão chega à mesma situação em todos os casos. Nenhum efeito externo neste módulo.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from importer import _normalize
from workflow import RULE_DEFAULTS, document_deadline, forms_deadline, full_end, parse

FASES = {'review': 'Triagem', 'monitoring': 'Monitoramento 24h', 'forms': 'Retorno da manutenção', 'documents': 'Registro da ocorrência', 'closed': 'Concluídas'}
# Peças (etapa 4): o que cada situação pede, como tarefa BPMN. (nome, tipo BPMN, pode ser automática?)
#   usuario = tarefa humana · envio = tarefa de envio (e-mail; SEMPRE manual, com prévia) · servico = o SISCO faz sozinho.
ACOES = {
    'review': ('Revisar a ocorrência', 'usuario', False),
    'details': ('Consultar a ocorrência', 'usuario', False),
    'return': ('Registrar o retorno', 'usuario', False),
    'request': ('Pedir status à manutenção (e-mail)', 'envio', False),
    'reminder': ('Cobrar a manutenção (e-mail)', 'envio', False),
    'document': ('Enviar o registro à coordenação (e-mail)', 'envio', False),
    'document_reminder': ('Cobrar o registro (e-mail)', 'envio', False),
    'word': ('Gerar o Word do registro', 'servico', True),
}
TIPOS_ACAO = tuple(ACOES)


class Contexto:
    """Os dados de uma ocorrência que as perguntas e os prazos consultam (mesmo cálculo de workflow.derive)."""

    def __init__(self, item, config, clock=None):
        self.clock = (clock or datetime.now(timezone(timedelta(hours=-3)))).replace(tzinfo=None)
        v = self.v = item['values']
        self.reviewed = bool(item['reviewed'])
        self.flow = next((f for f in config['flows'] if _normalize(f['classification']) == _normalize(v.get('classification'))), None)
        self.state = _normalize(v.get('maintenance_status'))
        self.end = full_end(v)
        self.start = parse(v.get('start_date', '') + 'T' + v.get('start_time', ''))
        finished = parse(v.get('end_date', '') + 'T' + v.get('end_time', ''))
        self.duration = ((finished or self.clock) - self.start).total_seconds() / 3600 if self.start else None
        self.limit = config.get('sla_hours', RULE_DEFAULTS['sla_hours'])
        sla = ('Superior 24h' if self.end else 'Analisar') if self.duration is not None and self.duration > self.limit else ('Ok' if self.end and self.duration is not None else '')
        self.sla = '' if self.duration is None or self.duration < 0 else sla
        self.forecast = parse(v.get('forecast_date'))
        self.forms_due = forms_deadline(v, config)
        self.sent_at = parse(v.get('forms_sent_at'))
        self.deadline = document_deadline(v)
        self.next_doc = parse(v.get('document_next_date'))
        hour = config.get('followup_hour', RULE_DEFAULTS['followup_hour'])
        self.first_due = (self.forecast + timedelta(days=1)).replace(hour=hour, minute=0) if self.forecast else None


# Perguntas que uma decisão pode fazer. (nome na tela, função)
CONDICOES = {
    'sem_classificacao': ('Está sem classificação (ou com classificação sem fluxo)?', lambda c: not c.v.get('classification') or not c.flow),
    'nao_revisada': ('Ainda não foi revisada?', lambda c: not c.reviewed),
    'tipo_sem_acompanhamento': ('O tipo de fluxo dispensa acompanhamento?', lambda c: not c.flow['followup']),
    'encerrou_no_limite': ('Encerrou dentro do limite de horas?', lambda c: c.sla == 'Ok'),
    'dentro_do_limite': ('Ainda não passou do limite de horas?', lambda c: c.sla not in ('Analisar', 'Superior 24h')),
    'tem_fim': ('Tem data e hora de fim?', lambda c: c.end),
    'tipo_sem_registro': ('O tipo de fluxo dispensa o registro (Word)?', lambda c: not c.flow['coordinator']),
    'registro_enviado': ('O registro foi enviado à coordenação?', lambda c: 'enviado' in _normalize(c.v.get('document_sent'))),
    'registro_devolvido': ('O registro foi devolvido?', lambda c: bool(parse(c.v.get('document_return_date')))),
    'prazo_registro_vencido': ('O prazo do registro venceu?', lambda c: bool(c.deadline and c.clock.date() > c.deadline.date())),
    'aguarda_proxima_cobranca_registro': ('Ainda falta chegar a próxima cobrança do registro?', lambda c: bool(c.next_doc and c.clock.date() < c.next_doc.date())),
    'cobranca_registro_chegou': ('Chegou a data de cobrar o registro?', lambda c: bool(c.next_doc and c.clock.date() >= c.next_doc.date())),
    'word_gerado': ('O Word já foi gerado?', lambda c: 'gerado' in _normalize(c.v.get('word_status'))),
    'manutencao_finalizou': ('A manutenção informou que finalizou?', lambda c: any(word in c.state for word in ('finaliz', 'encerr', 'conclu'))),
    'manutencao_em_aberto': ('A manutenção informou que segue em aberto?', lambda c: c.state in ('em aberto', 'aberto', 'aberta')),
    'sem_previsao': ('Está sem previsão de encerramento?', lambda c: not c.forecast),
    'antes_do_prazo_informado': ('Ainda não chegou o dia seguinte à previsão?', lambda c: c.clock < c.first_due),
    'cobranca_vencida_ou_sem_prazo': ('Venceu a cobrança (ou não há prazo)?', lambda c: not c.forms_due or c.clock >= c.forms_due),
    'forms_enviado': ('A solicitação de status (Forms) foi enviada?', lambda c: 'enviado' in _normalize(c.v.get('forms_sent')) and bool(c.sent_at)),
    'cobranca_forms_vencida': ('Venceu o prazo de resposta do Forms?', lambda c: bool(c.forms_due and c.clock >= c.forms_due)),
    'forms_em_fila': ('O Forms está na fila de envio?', lambda c: 'fila' in _normalize(c.v.get('forms_sent'))),
}

# Prazos que uma situação pode mostrar. (nome na tela, função)
PRAZOS = {
    '': ('Sem prazo', lambda c: None),
    'limite_do_monitoramento': ('Início + limite de horas', lambda c: c.start + timedelta(hours=c.limit) if c.start else None),
    'prazo_do_forms': ('Prazo de resposta do Forms', lambda c: c.forms_due),
    'prazo_do_forms_ou_previsao': ('Prazo do Forms (ou dia seguinte à previsão)', lambda c: c.forms_due or c.first_due),
    'prazo_do_registro': ('Prazo do registro', lambda c: c.deadline),
    'proxima_cobranca_do_registro': ('Próxima cobrança do registro', lambda c: c.next_doc),
    'cobranca_ou_prazo_do_registro': ('Próxima cobrança (ou prazo) do registro', lambda c: c.next_doc or c.deadline),
}


# Subprocessos (BPMN): cada elemento pertence a um deles ou ao nível de cima (''), onde ficam os gateways entre
# as fases e o fim. Só organiza o desenho: o motor percorre as mesmas decisões e situações.
SUBPROCESSOS_PADRAO = [
    {'id': 'triagem', 'nome': 'Triagem', 'descricao': 'Classificar e revisar'},
    {'id': 'monitoramento', 'nome': 'Monitoramento 24h', 'descricao': 'Timer do limite de horas'},
    {'id': 'retorno', 'nome': 'Retorno da manutenção', 'descricao': 'Forms e cobranças'},
    {'id': 'registro', 'nome': 'Registro da ocorrência', 'descricao': 'Word, envio e devolução'},
]
_SUBPROCESSO_DE = {
    'triagem': ('q_classificacao', 'q_revisada', 's_aguardando_classificacao', 's_aguardando_revisao'),
    'monitoramento': ('q_no_limite', 'q_dentro', 'q_fim_no_limite', 's_monitoramento', 'q_fim'),
    'retorno': ('q_finalizou', 'q_em_aberto', 'q_sem_previsao', 'q_antes_da_previsao', 'q_cobranca_vencida', 'q_forms_enviado',
                'q_resposta_vencida', 'q_forms_fila', 's_dados_encerramento', 's_aguardando_previsao', 's_aguardando_prazo',
                's_cobrar_atualizacao', 's_aguardando_atualizacao', 's_resposta_cobrar', 's_resposta_aguardar', 's_forms_fila',
                's_aguardando_envio_forms'),
    'registro': ('q_registro_enviado', 'q_registro_devolvido', 'q_registro_vencido', 'q_aguarda_cobranca', 'q_cobranca_registro',
                 'q_word', 's_registro_vencido_aguardar', 's_registro_vencido_cobrar', 's_cobrar_registro',
                 's_aguardando_retorno_registro', 's_aguardando_envio_registro', 's_aguardando_geracao_registro'),
}
SUBPROCESSO_PADRAO_DO_ELEMENTO = {el: sub for sub, els in _SUBPROCESSO_DE.items() for el in els}


def com_subprocessos(fluxo):
    """Fluxos salvos antes dos subprocessos ganham a organização padrão (elemento conhecido → seu subprocesso;
    elemento novo → nível de cima). Não muda nada no comportamento."""
    if not isinstance(fluxo, dict) or not isinstance(fluxo.get('elementos'), dict) or 'subprocessos' in fluxo:
        return fluxo
    fluxo = copy.deepcopy(fluxo)
    fluxo['subprocessos'] = copy.deepcopy(SUBPROCESSOS_PADRAO)
    for chave, el in fluxo['elementos'].items():
        if isinstance(el, dict):
            el.setdefault('subprocesso', SUBPROCESSO_PADRAO_DO_ELEMENTO.get(chave, ''))
    return fluxo


def _decisao(nome, condicao, sim, nao):
    return {'tipo': 'decisao', 'nome': nome, 'condicao': condicao, 'sim': sim, 'nao': nao}


# Rótulos de fábrica que faziam a pergunta inversa à condição avaliada.
PERGUNTAS_CORRIGIDAS = {
    'q_classificacao': ('sem_classificacao', 'Classificada?', 'Falta classificação ou fluxo?'),
    'q_revisada': ('nao_revisada', 'Revisada?', 'Revisão pendente?'),
    'q_acompanha': ('tipo_sem_acompanhamento', 'Acompanha o retorno?', 'Dispensa acompanhamento?'),
    'q_exige_registro': ('tipo_sem_registro', 'Exige registro Word?', 'Dispensa registro Word?'),
}


def atualizar_fluxo_salvo(fluxo):
    """Atualiza a apresentação durante a leitura do JSON, inclusive ao restaurar uma versão."""
    fluxo = com_subprocessos(fluxo)
    if not isinstance(fluxo, dict) or not isinstance(fluxo.get('elementos'), dict):
        return fluxo
    for chave, (condicao, antigo, novo) in PERGUNTAS_CORRIGIDAS.items():
        el = fluxo['elementos'].get(chave)
        if isinstance(el, dict) and el.get('tipo') == 'decisao' and el.get('condicao') == condicao and el.get('nome') == antigo:
            el['nome'] = novo
    return fluxo


def _situacao(conclusao, fase, acao, tipo_acao, prazo=''):
    return {'tipo': 'situacao', 'nome': conclusao, 'conclusao': conclusao, 'fase': fase, 'acao': acao, 'tipo_acao': tipo_acao, 'prazo': prazo}


def fluxo_padrao():
    """O comportamento atual (workflow.derive) descrito como grafo."""
    elementos = {
        # Triagem
        'q_classificacao': _decisao('Falta classificação ou fluxo?', 'sem_classificacao', 's_aguardando_classificacao', 'q_revisada'),
        'q_revisada': _decisao('Revisão pendente?', 'nao_revisada', 's_aguardando_revisao', 'q_acompanha'),
        'q_acompanha': _decisao('Dispensa acompanhamento?', 'tipo_sem_acompanhamento', 's_encerrada', 'q_no_limite'),
        # Monitoramento
        'q_no_limite': _decisao('Encerrou no limite?', 'encerrou_no_limite', 's_encerrada', 'q_dentro'),
        'q_dentro': _decisao('Ainda no limite?', 'dentro_do_limite', 'q_fim_no_limite', 'q_fim'),
        'q_fim_no_limite': _decisao('Já terminou?', 'tem_fim', 's_encerrada', 's_monitoramento'),
        'q_fim': _decisao('Manutenção terminou?', 'tem_fim', 'q_exige_registro', 'q_finalizou'),
        # Registro (Word)
        'q_exige_registro': _decisao('Dispensa registro Word?', 'tipo_sem_registro', 's_encerrada', 'q_registro_enviado'),
        'q_registro_enviado': _decisao('Registro enviado?', 'registro_enviado', 'q_registro_devolvido', 'q_word'),
        'q_registro_devolvido': _decisao('Registro devolvido?', 'registro_devolvido', 's_encerrada', 'q_registro_vencido'),
        'q_registro_vencido': _decisao('Prazo do registro venceu?', 'prazo_registro_vencido', 'q_aguarda_cobranca', 'q_cobranca_registro'),
        'q_aguarda_cobranca': _decisao('Aguardar a próxima cobrança?', 'aguarda_proxima_cobranca_registro', 's_registro_vencido_aguardar', 's_registro_vencido_cobrar'),
        'q_cobranca_registro': _decisao('Hora de cobrar o registro?', 'cobranca_registro_chegou', 's_cobrar_registro', 's_aguardando_retorno_registro'),
        'q_word': _decisao('Word gerado?', 'word_gerado', 's_aguardando_envio_registro', 's_aguardando_geracao_registro'),
        # Retorno da manutenção (Forms)
        'q_finalizou': _decisao('Manutenção finalizou?', 'manutencao_finalizou', 's_dados_encerramento', 'q_em_aberto'),
        'q_em_aberto': _decisao('Manutenção em aberto?', 'manutencao_em_aberto', 'q_sem_previsao', 'q_forms_enviado'),
        'q_sem_previsao': _decisao('Sem previsão?', 'sem_previsao', 's_aguardando_previsao', 'q_antes_da_previsao'),
        'q_antes_da_previsao': _decisao('Antes da previsão?', 'antes_do_prazo_informado', 's_aguardando_prazo', 'q_cobranca_vencida'),
        'q_cobranca_vencida': _decisao('Cobrança venceu?', 'cobranca_vencida_ou_sem_prazo', 's_cobrar_atualizacao', 's_aguardando_atualizacao'),
        'q_forms_enviado': _decisao('Forms enviado?', 'forms_enviado', 'q_resposta_vencida', 'q_forms_fila'),
        'q_resposta_vencida': _decisao('Resposta venceu?', 'cobranca_forms_vencida', 's_resposta_cobrar', 's_resposta_aguardar'),
        'q_forms_fila': _decisao('Forms na fila?', 'forms_em_fila', 's_forms_fila', 's_aguardando_envio_forms'),
        # Situações
        's_aguardando_classificacao': _situacao('Aguardando classificação', 'review', 'Classificar e revisar', 'review'),
        's_aguardando_revisao': _situacao('Aguardando revisão', 'review', 'Revisar ocorrência', 'review'),
        's_encerrada': _situacao('Encerrada', 'closed', 'Consultar ocorrência', 'details'),
        's_monitoramento': _situacao('Em monitoramento 24h', 'monitoring', 'Consultar ocorrência', 'details', 'limite_do_monitoramento'),
        's_registro_vencido_aguardar': _situacao('Prazo do registro vencido', 'documents', 'Aguardar próxima cobrança', 'return', 'cobranca_ou_prazo_do_registro'),
        's_registro_vencido_cobrar': _situacao('Prazo do registro vencido', 'documents', 'Cobrar retorno do registro', 'document_reminder', 'cobranca_ou_prazo_do_registro'),
        's_cobrar_registro': _situacao('Solicitação de retorno do registro pendente', 'documents', 'Conferir cobrança do registro', 'document_reminder', 'proxima_cobranca_do_registro'),
        's_aguardando_retorno_registro': _situacao('Aguardando retorno do registro', 'documents', 'Registrar retorno do documento', 'return', 'prazo_do_registro'),
        's_aguardando_envio_registro': _situacao('Aguardando envio do registro', 'documents', 'Conferir e enviar registro', 'document'),
        's_aguardando_geracao_registro': _situacao('Aguardando geração do registro', 'documents', 'Criar Word', 'word'),
        's_dados_encerramento': _situacao('Aguardando dados de encerramento', 'forms', 'Completar encerramento', 'return'),
        's_aguardando_previsao': _situacao('Aguardando previsão de encerramento', 'forms', 'Registrar previsão ou cobrar retorno', 'return', 'prazo_do_forms'),
        's_aguardando_prazo': _situacao('Aguardando prazo informado', 'forms', 'Consultar previsão', 'return', 'prazo_do_forms_ou_previsao'),
        's_cobrar_atualizacao': _situacao('Solicitação de retorno pendente', 'forms', 'Cobrar atualização', 'reminder', 'prazo_do_forms_ou_previsao'),
        's_aguardando_atualizacao': _situacao('Aguardando atualização do status', 'forms', 'Consultar retorno', 'return', 'prazo_do_forms'),
        's_resposta_cobrar': _situacao('Aguardando resposta do Forms', 'forms', 'Cobrar atualização', 'reminder', 'prazo_do_forms'),
        's_resposta_aguardar': _situacao('Aguardando resposta do Forms', 'forms', 'Consultar retorno', 'return', 'prazo_do_forms'),
        's_forms_fila': _situacao('Forms em fila', 'forms', 'Conferir fila de origem', 'details'),
        's_aguardando_envio_forms': _situacao('Aguardando envio do Forms', 'forms', 'Solicitar atualização', 'request'),
    }
    for chave, el in elementos.items():
        el['subprocesso'] = SUBPROCESSO_PADRAO_DO_ELEMENTO.get(chave, '')
    return {'versao_formato': 2, 'inicio': 'q_classificacao', 'subprocessos': copy.deepcopy(SUBPROCESSOS_PADRAO), 'elementos': copy.deepcopy(elementos)}


def validar(fluxo):
    """Lista de problemas (vazia = o fluxo executa). Recusa o que deixaria uma ocorrência sem situação."""
    problemas = []
    if not isinstance(fluxo, dict) or not isinstance(fluxo.get('elementos'), dict) or not fluxo['elementos']:
        return ['O fluxo não tem elementos.']
    elementos = fluxo['elementos']
    if fluxo.get('inicio') not in elementos:
        problemas.append('O início do fluxo não aponta para nenhum elemento.')
    # Subprocessos: lista com identificador e nome únicos; cada elemento num deles ou no nível de cima ('').
    subprocessos = fluxo.get('subprocessos')
    ids = []
    if not isinstance(subprocessos, list):
        problemas.append('O fluxo não tem a lista de subprocessos.')
        subprocessos = []
    for sub in subprocessos:
        if not isinstance(sub, dict) or not isinstance(sub.get('id'), str) or not sub['id'].strip() or not str(sub.get('nome') or '').strip():
            problemas.append('Há um subprocesso sem identificador ou sem nome.')
            continue
        if sub['id'] in ids:
            problemas.append(f'Subprocesso repetido: "{sub["nome"]}".')
        ids.append(sub['id'])
    nomes = [str(s.get('nome', '')).strip().casefold() for s in subprocessos if isinstance(s, dict)]
    if len(set(nomes)) != len(nomes):
        problemas.append('Dois subprocessos têm o mesmo nome.')
    for sub in subprocessos:
        if isinstance(sub, dict) and sub.get('id') in ids and not any(isinstance(el, dict) and el.get('subprocesso') == sub['id'] for el in elementos.values()):
            problemas.append(f'O subprocesso "{sub.get("nome")}" está vazio.')
    for chave, el in elementos.items():
        nome = el.get('nome') or chave if isinstance(el, dict) else chave
        if not isinstance(el, dict) or el.get('tipo') not in ('decisao', 'situacao'):
            problemas.append(f'"{nome}": tipo de elemento desconhecido.')
            continue
        if el.get('subprocesso', '') != '' and el.get('subprocesso') not in ids:
            problemas.append(f'"{nome}": o subprocesso não existe.')
        if el['tipo'] == 'decisao':
            if el.get('condicao') not in CONDICOES:
                problemas.append(f'"{nome}": a decisão não tem uma pergunta válida.')
            for ramo in ('sim', 'nao'):
                if el.get(ramo) not in elementos:
                    problemas.append(f'"{nome}": o caminho "{"Sim" if ramo == "sim" else "Não"}" não leva a nenhum elemento.')
        else:
            if not str(el.get('conclusao') or '').strip():
                problemas.append(f'"{nome}": a situação não tem conclusão.')
            if el.get('fase') not in FASES:
                problemas.append(f'"{nome}": a fase não existe.')
            if el.get('tipo_acao') not in TIPOS_ACAO:
                problemas.append(f'"{nome}": o tipo de ação não existe.')
            if el.get('prazo', '') not in PRAZOS:
                problemas.append(f'"{nome}": o prazo não existe.')
            if 'automatico' in el and type(el['automatico']) is not bool:
                problemas.append(f'"{nome}": a opção automática é inválida.')
            elif el.get('automatico') and not ACOES.get(el.get('tipo_acao'), ('', '', False))[2]:
                problemas.append(f'"{nome}": só "Gerar o Word do registro" pode ser automática; envios de e-mail são sempre manuais.')
    if problemas:
        return problemas
    # Laços e elementos soltos: percorre a partir do início.
    vistos, emCurso = set(), set()

    def filhos(chave):
        el = elementos[chave]
        return iter((el['sim'], el['nao']) if el['tipo'] == 'decisao' else ())

    pilha = [(fluxo['inicio'], filhos(fluxo['inicio']))]
    emCurso.add(fluxo['inicio'])
    vistos.add(fluxo['inicio'])
    while pilha:
        chave, proximos = pilha[-1]
        seguinte = next(proximos, None)
        if seguinte is None:
            pilha.pop()
            emCurso.discard(chave)
            continue
        if seguinte in emCurso:
            problemas.append(f'Laço: "{elementos[seguinte].get("nome") or seguinte}" volta para trás no fluxo.')
            continue
        if seguinte not in vistos:
            vistos.add(seguinte)
            emCurso.add(seguinte)
            pilha.append((seguinte, filhos(seguinte)))
    for chave in elementos:
        if chave not in vistos:
            problemas.append(f'"{elementos[chave].get("nome") or chave}" está solto: nenhum caminho chega até ele.')
    return problemas


def caminho(fluxo, item, config, clock=None):
    """Os elementos percorridos por uma ocorrência, do início até a situação."""
    ctx = Contexto(item, config, clock)
    elementos, chave, passos = fluxo['elementos'], fluxo['inicio'], []
    for _ in range(len(elementos) + 1):
        el = elementos[chave]
        passos.append(chave)
        if el['tipo'] == 'situacao':
            return passos, ctx
        chave = el['sim'] if CONDICOES[el['condicao']][1](ctx) else el['nao']
    raise ValueError('O fluxo não chegou a uma situação (laço).')


def executar(fluxo, item, config, clock=None):
    """Mesmo formato e mesmo resultado de workflow.derive, mas seguindo o fluxo."""
    passos, ctx = caminho(fluxo, item, config, clock)
    fim = fluxo['elementos'][passos[-1]]
    prazo = PRAZOS[fim.get('prazo', '')][1](ctx)
    return {'sla': ctx.sla, 'hours': round(ctx.duration, 2) if ctx.duration is not None else None, 'can_act': ctx.reviewed,
            'due_at': prazo.isoformat(timespec='minutes') if prazo else '', 'category': fim['fase'],
            'action': fim['acao'], 'action_kind': fim['tipo_acao'], 'calculated_conclusion': fim['conclusao'], 'conclusion': fim['conclusao'],
            'path': passos}
