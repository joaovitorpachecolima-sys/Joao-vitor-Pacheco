"""Ponte entre o fluxo do motor (fluxo.py) e um arquivo BPMN 2.0 editável no bpmn-js (e no Bizagi/Camunda Modeler).

gerar_bpmn(fluxo)  → XML com piscinas, subprocessos fechados (cada um com o próprio desenho), gateways, tarefas,
                     fins, fluxos de mensagem e as posições (DI).
ler_bpmn(xml)      → (fluxo, problemas): o mesmo dicionário que o motor percorre.

Os campos do SISCO ficam em atributos do espaço de nomes "sisco" (pergunta da decisão, ramo Sim/Não, conclusão,
fase, peça, prazo, automático, destino das saídas). O resto é BPMN padrão. Nenhum efeito externo neste módulo.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from fluxo import ACOES

NS = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL', 'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
      'dc': 'http://www.omg.org/spec/DD/20100524/DC', 'di': 'http://www.omg.org/spec/DD/20100524/DI', 'sisco': 'http://sisco.local/bpmn'}
TAREFAS = {'usuario': 'userTask', 'envio': 'sendTask', 'servico': 'serviceTask'}
TIPOS_DE_TAREFA = ('task', 'userTask', 'sendTask', 'serviceTask', 'manualTask', 'scriptTask', 'businessRuleTask', 'receiveTask')
TAMANHO = {'start': (36, 36), 'end': (36, 36), 'exit': (36, 36), 'gateway': (50, 50), 'task': (150, 80), 'sub': (170, 90)}
COLUNA, LINHA, ESQUERDA, TOPO = 210, 150, 120, 150
SITUACAO = ('conclusao', 'fase', 'acao', 'tipo_acao', 'prazo')
ATRIBUTO = {'conclusao': 'conclusao', 'fase': 'fase', 'acao': 'acao', 'tipo_acao': 'tipoAcao', 'prazo': 'prazo'}


def _attr(nome, valor):
    return f' {nome}={quoteattr(str(valor))}'


# ---------------------------------------------------------------- fluxo → BPMN
def _nivel(fluxo, nivel):
    """Nós e ligações de um nível (nível de cima = ''; ou o id de um subprocesso), como no desenho do editor."""
    els, subs = fluxo['elementos'], [s['id'] for s in fluxo.get('subprocessos', [])]
    sub_de = lambda k: els[k].get('subprocesso', '') if els[k].get('subprocesso', '') in subs else ''
    nos, ligacoes, vistos = {}, [], set()

    def ligar(a, b, ramo='', nome=''):
        if (a, b, ramo) not in vistos:
            vistos.add((a, b, ramo))
            ligacoes.append((a, b, ramo, nome))

    def tipo(k):
        el = els[k]
        return 'gateway' if el['tipo'] == 'decisao' else 'end' if el.get('fase') == 'closed' else 'task'

    if nivel == '':
        nos['inicio'] = 'start'
        for s in subs:
            nos[s] = 'sub'
        for k in els:
            if not sub_de(k):
                nos[k] = tipo(k)
        onde = lambda k: (sub_de(k) or k) if k in els else None
        if onde(fluxo['inicio']):
            ligar('inicio', onde(fluxo['inicio']))
        saidas = {}
        for k, el in els.items():
            if el['tipo'] != 'decisao':
                continue
            for ramo in ('sim', 'nao'):
                destino = el.get(ramo)
                if destino not in els or onde(destino) == onde(k):
                    continue
                if sub_de(k):
                    saidas.setdefault((sub_de(k), onde(destino)), _nome_destino(fluxo, destino))
                else:
                    ligar(k, onde(destino), ramo)
        for (s, destino), nome in saidas.items():
            ligar(s, destino, '', nome)
    else:
        entrada = f'{nivel}__entrada'
        nos[entrada] = 'start'
        for k in els:
            if sub_de(k) == nivel:
                nos[k] = tipo(k)
        if sub_de(fluxo['inicio']) == nivel:
            ligar(entrada, fluxo['inicio'])
        for k, el in els.items():
            if el['tipo'] != 'decisao':
                continue
            for ramo in ('sim', 'nao'):
                destino = el.get(ramo)
                if destino not in els:
                    continue
                dentro, destino_dentro = sub_de(k) == nivel, sub_de(destino) == nivel
                if not dentro and destino_dentro:
                    ligar(entrada, destino)
                elif dentro and destino_dentro:
                    ligar(k, destino, ramo)
                elif dentro:
                    saida = f'{nivel}__saida__{destino}'
                    nos[saida] = 'exit'
                    ligar(k, saida, ramo)
    return nos, ligacoes


def _nome_destino(fluxo, destino):
    el = fluxo['elementos'][destino]
    sub = next((s['nome'] for s in fluxo.get('subprocessos', []) if s['id'] == el.get('subprocesso')), '')
    return sub or el.get('nome') or destino


def _arranjo(nos, ligacoes, raiz):
    """Árvore: o caminho com mais passos segue na linha; os desvios descem (mesmo arranjo do editor)."""
    saem = {}
    for a, b, *_ in ligacoes:
        saem.setdefault(a, [])
        if b not in saem[a]:
            saem[a].append(b)
    vagas, maior = {}, [-1]

    def tamanho(n, vistos):
        if n in vistos or n in vagas:
            return 0
        vistos.add(n)
        return 1 + sum(tamanho(m, vistos) for m in saem.get(n, []))

    def colocar(n, col, lin):
        if n in vagas or n not in nos:
            return
        vagas[n] = (col, lin)
        maior[0] = max(maior[0], lin)
        filhos = sorted((m for m in saem.get(n, []) if m not in vagas), key=lambda m: -tamanho(m, set()))
        for i, m in enumerate(filhos):
            if m not in vagas:
                colocar(m, col + 1, lin if i == 0 else maior[0] + 1)

    colocar(raiz, 0, 0)
    soltos = 0
    for n in nos:
        if n not in vagas:
            vagas[n] = (1 + soltos, maior[0] + 1)
            soltos += 1
    return {n: (ESQUERDA + c * COLUNA, TOPO + l * LINHA) for n, (c, l) in vagas.items()}


def _caixa(centro, tipo):
    (x, y), (w, h) = centro, TAMANHO[tipo]
    return x - w / 2, y - h / 2, w, h


def _pontos(pos, nos, a, b, canal):
    """Ponto a ponto de uma ligação: reta, cotovelo para baixo, ou canal por cima quando há algo no caminho."""
    (ax, ay), (bx, by) = pos[a], pos[b]
    aw, ah = TAMANHO[nos[a]]
    bw, bh = TAMANHO[nos[b]]
    if abs(ay - by) < 1 and bx > ax:
        pontos = [(ax + aw / 2, ay), (bx - bw / 2, by)]
    elif by > ay and bx > ax:
        pontos = [(ax, ay + ah / 2), (ax, by), (bx - bw / 2, by)]
    else:
        pontos = None
    if pontos and not _bloqueado(pos, nos, pontos, a, b):
        return pontos
    topo = canal.pop(0)
    return [(ax, ay - ah / 2), (ax, topo), (bx, topo), (bx, by - bh / 2)]


def _bloqueado(pos, nos, pontos, a, b):
    for n, (cx, cy) in pos.items():
        if n in (a, b):
            continue
        x, y, w, h = _caixa((cx, cy), nos[n])
        for (x1, y1), (x2, y2) in zip(pontos, pontos[1:]):
            if min(x1, x2) < x + w and max(x1, x2) > x and min(y1, y2) < y + h and max(y1, y2) > y:
                return True
    return False


def gerar_bpmn(fluxo):
    """O fluxo do motor como BPMN 2.0 com diagrama (piscinas, subprocessos fechados com o próprio desenho)."""
    els, subs = fluxo['elementos'], fluxo.get('subprocessos', [])
    sub_ids = [s['id'] for s in subs]
    sub_de = lambda k: els[k].get('subprocesso', '') if els[k].get('subprocesso', '') in sub_ids else ''
    processo, desenhos = [], []

    def elemento(k, recuo):
        el = els[k]
        if el['tipo'] == 'decisao':
            return f'{recuo}<bpmn:exclusiveGateway id="{k}"{_attr("name", el.get("nome", ""))}{_attr("sisco:pergunta", el.get("condicao", ""))} />'
        extra = ''.join(_attr(f'sisco:{ATRIBUTO[c]}', el.get(c, '')) for c in SITUACAO)
        if el.get('automatico'):
            extra += ' sisco:automatico="true"'
        if el.get('fase') == 'closed':
            return f'{recuo}<bpmn:endEvent id="{k}"{_attr("name", el.get("conclusao", ""))}{extra} />'
        tag = TAREFAS.get(ACOES.get(el.get('tipo_acao'), ('', 'usuario', False))[1], 'task')
        return f'{recuo}<bpmn:{tag} id="{k}"{_attr("name", el.get("conclusao", ""))}{extra} />'

    def nivel_xml(nivel, recuo):
        nos, ligacoes = _nivel(fluxo, nivel)
        linhas = []
        for n, t in nos.items():
            if n in els:
                linhas.append(elemento(n, recuo))
            elif t == 'start' and nivel == '':
                linhas.append(f'{recuo}<bpmn:startEvent id="inicio" name="Ocorrência comunicada"><bpmn:messageEventDefinition id="inicio_mensagem" /></bpmn:startEvent>')
            elif t == 'start':
                linhas.append(f'{recuo}<bpmn:startEvent id="{n}" name="Entrada" />')
            elif t == 'exit':
                destino = n.split('__saida__', 1)[1]
                linhas.append(f'{recuo}<bpmn:endEvent id="{n}"{_attr("name", "Segue: " + _nome_destino(fluxo, destino))}{_attr("sisco:destino", destino)} />')
            elif t == 'sub':
                sub = next(s for s in subs if s['id'] == n)
                linhas.append(f'{recuo}<bpmn:subProcess id="{n}"{_attr("name", sub.get("nome", ""))}{_attr("sisco:descricao", sub.get("descricao", ""))}>')
                linhas.extend(nivel_xml(n, recuo + '  '))
                linhas.append(f'{recuo}</bpmn:subProcess>')
        for a, b, ramo, nome in ligacoes:
            ident = f'f_{a}_{ramo or "x"}_{b}'.replace('__', '_')
            rotulo = {'sim': 'Sim', 'nao': 'Não'}.get(ramo, nome)
            linhas.append(f'{recuo}<bpmn:sequenceFlow id="{ident}"{_attr("name", rotulo) if rotulo else ""} sourceRef="{a}" targetRef="{b}"{_attr("sisco:ramo", ramo) if ramo else ""} />')
        # Desenho deste nível.
        raiz = 'inicio' if nivel == '' else f'{nivel}__entrada'
        pos = _arranjo(nos, ligacoes, raiz)
        topo = min(pos[n][1] - TAMANHO[t][1] / 2 for n, t in nos.items()) - 40
        canal = [topo - i * 12 for i in range(len(ligacoes) + 1)]
        formas = []
        for n, t in nos.items():
            x, y, w, h = _caixa(pos[n], t)
            expandido = ' isExpanded="false"' if t == 'sub' else ''
            marcador = ' isMarkerVisible="true"' if t == 'gateway' else ''
            formas.append(f'      <bpmndi:BPMNShape id="{n}_di" bpmnElement="{n}"{expandido}{marcador}><dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" /></bpmndi:BPMNShape>')
        for a, b, ramo, nome in ligacoes:
            ident = f'f_{a}_{ramo or "x"}_{b}'.replace('__', '_')
            pontos = ''.join(f'<di:waypoint x="{x:.0f}" y="{y:.0f}" />' for x, y in _pontos(pos, nos, a, b, canal))
            formas.append(f'      <bpmndi:BPMNEdge id="{ident}_di" bpmnElement="{ident}">{pontos}</bpmndi:BPMNEdge>')
        desenhos.append((nivel, formas, pos, nos))
        return linhas

    processo = nivel_xml('', '    ')
    # Piscinas e fluxos de mensagem no desenho de cima.
    _, formas_topo, pos, nos = next(d for d in desenhos if d[0] == '')
    xs = [pos[n][0] + TAMANHO[t][0] / 2 for n, t in nos.items()]
    ys = [pos[n][1] + TAMANHO[t][1] / 2 for n, t in nos.items()]
    largura, base = max(xs) + 80, max(ys) + 60
    mensagens = [(s, d, nome, dx) for s, d, nome, dx in (('retorno', 'baixo', 'Pedido de status', -35), ('retorno', 'cima', 'Resposta do Forms', 35),
                                                          ('registro', 'baixo', 'Registro (Word)', -35), ('registro', 'cima', 'Registro devolvido', 35)) if s in nos]
    colaboracao = ['  <bpmn:collaboration id="colaboracao">',
                   '    <bpmn:participant id="p_cog" name="Operação e SISCO" processRef="processo" />']
    if mensagens:
        colaboracao.append('    <bpmn:participant id="p_manutencao" name="Manutenção e Coordenação" />')
    formas_topo.insert(0, f'      <bpmndi:BPMNShape id="p_cog_di" bpmnElement="p_cog" isHorizontal="true"><dc:Bounds x="20" y="20" width="{largura:.0f}" height="{base - 20:.0f}" /></bpmndi:BPMNShape>')
    if mensagens:
        formas_topo.insert(1, f'      <bpmndi:BPMNShape id="p_manutencao_di" bpmnElement="p_manutencao" isHorizontal="true"><dc:Bounds x="20" y="{base + 80:.0f}" width="{largura:.0f}" height="70" /></bpmndi:BPMNShape>')
    for s, direcao, nome, dx in mensagens:
        ident = f'm_{s}_{direcao}'
        origem, alvo = (s, 'p_manutencao') if direcao == 'baixo' else ('p_manutencao', s)
        colaboracao.append(f'    <bpmn:messageFlow id="{ident}"{_attr("name", nome)} sourceRef="{origem}" targetRef="{alvo}" />')
        x, y_sub = pos[s][0] + dx, pos[s][1] + TAMANHO['sub'][1] / 2
        y1, y2 = (y_sub, base + 80) if direcao == 'baixo' else (base + 80, y_sub)
        formas_topo.append(f'      <bpmndi:BPMNEdge id="{ident}_di" bpmnElement="{ident}"><di:waypoint x="{x:.0f}" y="{y1:.0f}" /><di:waypoint x="{x:.0f}" y="{y2:.0f}" /></bpmndi:BPMNEdge>')
    colaboracao.append('  </bpmn:collaboration>')
    partes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<bpmn:definitions xmlns:bpmn="{bpmn}" xmlns:bpmndi="{bpmndi}" xmlns:dc="{dc}" xmlns:di="{di}" xmlns:sisco="{sisco}" '
              'id="sisco_fluxo" targetNamespace="http://sisco.local/bpmn" exporter="SISCO" exporterVersion="3">'.format(**NS),
              *colaboracao, '  <bpmn:process id="processo" name="Gestão de Ocorrências" isExecutable="false">', *processo, '  </bpmn:process>']
    # O desenho do nível de cima vem primeiro: é o que os editores (bpmn-js, Bizagi) abrem.
    for nivel, formas, *_ in sorted(desenhos, key=lambda d: d[0] != ''):
        plano = 'colaboracao' if nivel == '' else nivel
        partes += [f'  <bpmndi:BPMNDiagram id="desenho_{plano}"><bpmndi:BPMNPlane id="plano_{plano}" bpmnElement="{plano}">', *formas, '    </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>']
    partes.append('</bpmn:definitions>')
    return '\n'.join(partes)


# ---------------------------------------------------------------- BPMN → fluxo
def _q(tag):
    prefixo, nome = tag.split(':')
    return f'{{{NS[prefixo]}}}{nome}'


def _s(no, nome, padrao=''):
    return no.get(f'{{{NS["sisco"]}}}{nome}', padrao)


def ler_bpmn(xml):
    """Lê o BPMN editado e devolve o fluxo do motor e a lista de problemas encontrados no desenho."""
    if not isinstance(xml, str) or len(xml) > 2_000_000:
        raise ValueError('Arquivo BPMN inválido ou grande demais.')
    if '<!DOCTYPE' in xml or '<!ENTITY' in xml:
        raise ValueError('O arquivo BPMN não pode ter DOCTYPE nem entidades.')
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as erro:
        raise ValueError(f'O arquivo BPMN não pôde ser lido: {erro}') from None
    processo = raiz.find('bpmn:process', NS)
    if processo is None:
        raise ValueError('O arquivo BPMN não tem processo.')
    problemas, nos, recipiente, fluxos = [], {}, {}, []

    def varrer(pai, sub):
        for no in pai:
            nome_tag = no.tag.split('}')[-1]
            ident = no.get('id')
            if nome_tag == 'sequenceFlow':
                fluxos.append(no)
            elif ident:
                nos[ident] = (nome_tag, no)
                recipiente[ident] = sub
                if nome_tag == 'subProcess':
                    varrer(no, ident)

    varrer(processo, '')
    subprocessos = [{'id': k, 'nome': no.get('name', '') or k, 'descricao': _s(no, 'descricao')} for k, (t, no) in nos.items() if t == 'subProcess']
    saem = {}
    for f in fluxos:
        saem.setdefault(f.get('sourceRef'), []).append(f)

    def entrada_de(sub, visto):
        inicios = [k for k, (t, _) in nos.items() if t == 'startEvent' and recipiente[k] == sub]
        if len(inicios) != 1:
            problemas.append(f'O subprocesso "{nos[sub][1].get("name") or sub}" precisa de exatamente um evento de início.')
            return ''
        seguintes = saem.get(inicios[0], [])
        if len(seguintes) != 1:
            problemas.append(f'O início do subprocesso "{nos[sub][1].get("name") or sub}" precisa de uma ligação.')
            return ''
        return resolver(seguintes[0].get('targetRef'), visto)

    def resolver(alvo, visto=None):
        """O elemento do motor que um destino representa (subprocesso → sua entrada; saída → o destino dela)."""
        visto = visto or set()
        if alvo in visto or alvo not in nos:
            return ''
        visto.add(alvo)
        tipo, no = nos[alvo]
        if tipo == 'exclusiveGateway' or tipo in TIPOS_DE_TAREFA or (tipo == 'endEvent' and not _s(no, 'destino')):
            return alvo
        if tipo == 'subProcess':
            return entrada_de(alvo, visto)
        if tipo == 'endEvent':
            destino = _s(no, 'destino')
            return entrada_de(destino, visto) if nos.get(destino, ('',))[0] == 'subProcess' else resolver(destino, visto)
        problemas.append(f'"{no.get("name") or alvo}": este tipo de elemento ({tipo}) não é usado pelo fluxo.')
        return ''

    elementos = {}
    for k, (tipo, no) in nos.items():
        nome = no.get('name', '')
        if tipo == 'exclusiveGateway':
            el = {'tipo': 'decisao', 'nome': nome or 'Decisão', 'condicao': _s(no, 'pergunta'), 'sim': '', 'nao': ''}
            for f in saem.get(k, []):
                ramo = _s(f, 'ramo') or {'sim': 'sim', 'não': 'nao', 'nao': 'nao'}.get((f.get('name') or '').strip().lower(), '')
                if ramo not in ('sim', 'nao'):
                    problemas.append(f'"{el["nome"]}": uma ligação de saída não diz se é o caminho Sim ou Não.')
                    continue
                if el[ramo]:
                    problemas.append(f'"{el["nome"]}": há mais de um caminho {"Sim" if ramo == "sim" else "Não"}.')
                el[ramo] = resolver(f.get('targetRef'))
            elementos[k] = el
        elif tipo in TIPOS_DE_TAREFA or (tipo == 'endEvent' and not _s(no, 'destino')):
            el = {'tipo': 'situacao', 'nome': _s(no, 'conclusao') or nome or 'Situação'}
            for campo in SITUACAO:
                el[campo] = _s(no, ATRIBUTO[campo])
            el['conclusao'] = el['conclusao'] or nome
            el['nome'] = el['conclusao'] or 'Situação'
            if tipo == 'endEvent' and not el['fase']:
                el['fase'] = 'closed'
            if _s(no, 'automatico') == 'true':
                el['automatico'] = True
            elementos[k] = el
        else:
            continue
        elementos[k]['subprocesso'] = recipiente[k]
    inicio = next((resolver(f.get('targetRef')) for k, (t, _) in nos.items() if t == 'startEvent' and recipiente[k] == '' for f in saem.get(k, [])), '')
    if not inicio:
        problemas.append('O evento de início do fluxo não está ligado a nada.')
    fluxo = {'versao_formato': 3, 'inicio': inicio, 'subprocessos': subprocessos, 'elementos': elementos}
    return fluxo, list(dict.fromkeys(problemas))
