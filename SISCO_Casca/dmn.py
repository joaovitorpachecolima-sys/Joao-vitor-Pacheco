"""Ponte DMN: a regra "classificação → tipo de fluxo → tratamento" como tabelas de decisão DMN 1.3.

São duas decisões ligadas (o diagrama DRD mostra a ligação):
  1. Tipo de fluxo (tabela UNIQUE): entrada = classificação da ocorrência; saída = tipo de fluxo.
  2. Tratamento do tipo (tabela UNIQUE): entrada = tipo de fluxo; saídas = acompanha, registro com a
     coordenação e para quem vai o e-mail.
As tabelas são editadas no dmn-js (o editor DMN de código aberto do Camunda). O SISCO não executa DMN:
`ler_dmn` devolve as mesmas listas `flows` e `flow_types` das regras, que seguem o rascunho → simular → publicar.
"""
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from workflow import RECIPIENT_MODES

NS = {'dmn': 'https://www.omg.org/spec/DMN/20191111/MODEL/', 'dmndi': 'https://www.omg.org/spec/DMN/20191111/DMNDI/',
      'dc': 'http://www.omg.org/spec/DMN/20180521/DC/', 'di': 'http://www.omg.org/spec/DMN/20180521/DI/'}
DESTINOS = {'coordenacao': 'coordenação da usina', 'classificacao': 'e-mail da classificação'}


def _norm(texto):
    return re.sub(r'\s+', ' ', str(texto or '')).strip().casefold()


def _feel(texto):
    return '"' + str(texto).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _celula(ident, texto):
    return f'<inputEntry id="{ident}"><text>{escape(texto)}</text></inputEntry>'


def gerar_dmn(flows, flow_types):
    """As regras do SISCO como DMN 1.3 (duas tabelas de decisão e o diagrama que as liga)."""
    tipos = [t['name'] for t in flow_types]
    permitidos = escape(','.join(_feel(t) for t in tipos))
    r1 = []
    for i, f in enumerate(flows, 1):
        r1.append(f'      <rule id="tipo_r{i}">{_celula(f"tipo_r{i}_e", _feel(f["classification"]))}'
                  f'<outputEntry id="tipo_r{i}_s"><text>{escape(_feel(f["flow"]))}</text></outputEntry></rule>')
    r2 = []
    for i, t in enumerate(flow_types, 1):
        saidas = (('acompanha', 'true' if t['followup'] else 'false'), ('coord', 'true' if t['coordinator'] else 'false'),
                  ('destino', _feel(t['recipient'])))
        r2.append(f'      <rule id="trat_r{i}">{_celula(f"trat_r{i}_e", _feel(t["name"]))}'
                  + ''.join(f'<outputEntry id="trat_r{i}_{k}"><text>{escape(v)}</text></outputEntry>' for k, v in saidas) + '</rule>')
    destinos = escape(','.join(_feel(d) for d in RECIPIENT_MODES))
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<definitions xmlns="{NS["dmn"]}" xmlns:dmndi="{NS["dmndi"]}" xmlns:dc="{NS["dc"]}" xmlns:di="{NS["di"]}" '
        'id="sisco_decisoes" name="Decisões do SISCO" namespace="http://sisco.local/dmn">',
        '  <inputData id="classificacao" name="Classificação da ocorrência"><variable id="classificacao_var" name="classificacao" typeRef="string" /></inputData>',
        '  <decision id="tipo_de_fluxo" name="Tipo de fluxo">',
        '    <informationRequirement id="req_classificacao"><requiredInput href="#classificacao" /></informationRequirement>',
        '    <decisionTable id="tabela_tipo" hitPolicy="UNIQUE">',
        '      <input id="tipo_entrada" label="Classificação"><inputExpression id="tipo_entrada_expr" typeRef="string"><text>classificacao</text></inputExpression></input>',
        f'      <output id="tipo_saida" label="Tipo de fluxo" name="tipo_de_fluxo" typeRef="string"><outputValues id="tipo_valores"><text>{permitidos}</text></outputValues></output>',
        *r1,
        '    </decisionTable>',
        '  </decision>',
        '  <decision id="tratamento" name="Tratamento do tipo de fluxo">',
        '    <informationRequirement id="req_tipo"><requiredDecision href="#tipo_de_fluxo" /></informationRequirement>',
        '    <decisionTable id="tabela_tratamento" hitPolicy="UNIQUE">',
        f'      <input id="trat_entrada" label="Tipo de fluxo"><inputExpression id="trat_entrada_expr" typeRef="string"><text>tipo_de_fluxo</text></inputExpression><inputValues id="trat_valores"><text>{permitidos}</text></inputValues></input>',
        '      <output id="trat_acompanha" label="Acompanha (24 h e Forms)" name="acompanha" typeRef="boolean" />',
        '      <output id="trat_coord" label="Registro com a coordenação" name="coordenacao" typeRef="boolean" />',
        f'      <output id="trat_destino" label="E-mail vai para" name="destinatario" typeRef="string"><outputValues id="trat_destinos"><text>{destinos}</text></outputValues></output>',
        *r2,
        '    </decisionTable>',
        '  </decision>',
        '  <textAnnotation id="nota"><text>Classificação nova: acrescente uma linha na tabela "Tipo de fluxo". Tipo novo: acrescente uma linha em "Tratamento".</text></textAnnotation>',
        '  <association id="nota_ligacao"><sourceRef href="#nota" /><targetRef href="#tipo_de_fluxo" /></association>',
        '  <dmndi:DMNDI><dmndi:DMNDiagram id="drd">',
        '    <dmndi:DMNShape id="classificacao_forma" dmnElementRef="classificacao"><dc:Bounds height="45" width="190" x="150" y="330" /></dmndi:DMNShape>',
        '    <dmndi:DMNShape id="tipo_forma" dmnElementRef="tipo_de_fluxo"><dc:Bounds height="80" width="190" x="150" y="160" /></dmndi:DMNShape>',
        '    <dmndi:DMNShape id="tratamento_forma" dmnElementRef="tratamento"><dc:Bounds height="80" width="190" x="150" y="20" /></dmndi:DMNShape>',
        '    <dmndi:DMNShape id="nota_forma" dmnElementRef="nota"><dc:Bounds height="80" width="260" x="420" y="160" /></dmndi:DMNShape>',
        '    <dmndi:DMNEdge id="req_classificacao_linha" dmnElementRef="req_classificacao"><di:waypoint x="245" y="330" /><di:waypoint x="245" y="240" /></dmndi:DMNEdge>',
        '    <dmndi:DMNEdge id="req_tipo_linha" dmnElementRef="req_tipo"><di:waypoint x="245" y="160" /><di:waypoint x="245" y="100" /></dmndi:DMNEdge>',
        '    <dmndi:DMNEdge id="nota_linha" dmnElementRef="nota_ligacao"><di:waypoint x="420" y="200" /><di:waypoint x="340" y="200" /></dmndi:DMNEdge>',
        '  </dmndi:DMNDiagram></dmndi:DMNDI>',
        '</definitions>'])


_TEXTO = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _textos(celula):
    """Lê uma célula FEEL com textos entre aspas, separados por vírgula: "A", "B". Devolve None se tiver outra coisa."""
    celula = (celula or '').strip()
    if not celula:
        return []
    achados = [m.group(1).replace('\\"', '"').replace('\\\\', '\\') for m in _TEXTO.finditer(celula)]
    sobra = _TEXTO.sub('', celula).replace(',', '').strip()
    return None if sobra else achados


def _logico(celula):
    return {'true': True, 'false': False, 'sim': True, 'não': False, 'nao': False}.get((celula or '').strip().strip('"').casefold())


def tratar_pedido(body):
    """Rota /api/rules/dmn: {"flows", "flow_types"} → {"dmn"}; {"dmn"} → {"flows", "flow_types", "problems"}. Não grava nada."""
    if 'dmn' in body:
        flows, flow_types, problemas = ler_dmn(body['dmn'])
        return {'flows': flows, 'flow_types': flow_types, 'problems': problemas}
    flows, flow_types = body.get('flows'), body.get('flow_types')
    ok = (isinstance(flows, list) and isinstance(flow_types, list) and len(flows) <= 200 and len(flow_types) <= 60
          and all(isinstance(f, dict) and isinstance(f.get('classification'), str) and isinstance(f.get('flow'), str) for f in flows)
          and all(isinstance(t, dict) and isinstance(t.get('name'), str) and isinstance(t.get('followup'), bool)
                  and isinstance(t.get('coordinator'), bool) and isinstance(t.get('recipient'), str) for t in flow_types))
    if not ok:
        raise ValueError('Regras inválidas para montar a tabela de decisão.')
    return {'dmn': gerar_dmn(flows, flow_types)}


def ler_dmn(xml):
    """Lê as tabelas editadas e devolve (flows, flow_types, problemas)."""
    if not isinstance(xml, str) or len(xml) > 2_000_000:
        raise ValueError('Arquivo DMN inválido ou grande demais.')
    if '<!DOCTYPE' in xml or '<!ENTITY' in xml:
        raise ValueError('O arquivo DMN não pode ter DOCTYPE nem entidades.')
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as erro:
        raise ValueError(f'O arquivo DMN não pôde ser lido: {erro}') from None
    if raiz.tag != f'{{{NS["dmn"]}}}definitions':
        raise ValueError('O arquivo não é DMN 1.3.')
    tabelas = {d.get('id'): d.find('dmn:decisionTable', NS) for d in raiz.findall('dmn:decision', NS)}
    if tabelas.get('tipo_de_fluxo') is None or tabelas.get('tratamento') is None:
        raise ValueError('As decisões "Tipo de fluxo" e "Tratamento do tipo de fluxo" precisam continuar no arquivo.')
    problemas = []

    def colunas(tabela, nome, entradas, saidas):
        n_in, n_out = len(tabela.findall('dmn:input', NS)), len(tabela.findall('dmn:output', NS))
        if (n_in, n_out) != (entradas, saidas):
            problemas.append(f'A tabela "{nome}" precisa de {entradas} entrada(s) e {saidas} saída(s); está com {n_in} e {n_out}.')
            return False
        if (tabela.get('hitPolicy') or 'UNIQUE') != 'UNIQUE':
            problemas.append(f'A tabela "{nome}" precisa da política U (UNIQUE): cada caso cai em uma única linha.')
        return True

    def linhas(tabela, entradas, saidas):
        for n, regra in enumerate(tabela.findall('dmn:rule', NS), 1):
            ent = [e.findtext('dmn:text', '', NS) for e in regra.findall('dmn:inputEntry', NS)]
            sai = [e.findtext('dmn:text', '', NS) for e in regra.findall('dmn:outputEntry', NS)]
            yield n, (ent + [''] * entradas)[:entradas], (sai + [''] * saidas)[:saidas]

    flow_types, vistos = [], set()
    if colunas(tabelas['tratamento'], 'Tratamento do tipo de fluxo', 1, 3):
        for n, (entrada,), (acompanha, coord, destino) in linhas(tabelas['tratamento'], 1, 3):
            nomes, onde = _textos(entrada), f'Tratamento, linha {n}'
            if not nomes or len(nomes) != 1:
                problemas.append(f'{onde}: escreva um tipo de fluxo entre aspas, ex.: "COMPLETO".')
                continue
            nome = nomes[0].strip()
            if _norm(nome) in vistos:
                problemas.append(f'{onde}: o tipo "{nome}" já tem outra linha.')
                continue
            vistos.add(_norm(nome))
            valores = (_logico(acompanha), _logico(coord))
            alvo = (_textos(destino) or [''])[0] if _textos(destino) is not None else ''
            alvo = {'coordenação': 'coordenacao', 'classificação': 'classificacao'}.get(alvo.strip().casefold(), alvo.strip().casefold())
            if None in valores:
                problemas.append(f'{onde} ("{nome}"): "Acompanha" e "Registro com a coordenação" aceitam só true ou false.')
            if alvo not in RECIPIENT_MODES:
                problemas.append(f'{onde} ("{nome}"): "E-mail vai para" aceita "coordenacao" ou "classificacao".')
            if None not in valores and alvo in RECIPIENT_MODES:
                flow_types.append({'name': nome, 'followup': valores[0], 'coordinator': valores[1], 'recipient': alvo})
    tipos = {_norm(t['name']): t['name'] for t in flow_types}

    flows, classes = [], set()
    if colunas(tabelas['tipo_de_fluxo'], 'Tipo de fluxo', 1, 1):
        for n, (entrada,), (saida,) in linhas(tabelas['tipo_de_fluxo'], 1, 1):
            nomes, tipo, onde = _textos(entrada), _textos(saida), f'Tipo de fluxo, linha {n}'
            if not nomes:
                problemas.append(f'{onde}: escreva a classificação entre aspas, ex.: "Manutenção" (várias: "A", "B"). "-" (qualquer) não é aceito.')
                continue
            if not tipo or len(tipo) != 1:
                problemas.append(f'{onde}: escolha um tipo de fluxo, entre aspas.')
                continue
            if _norm(tipo[0]) not in tipos:
                problemas.append(f'{onde}: o tipo "{tipo[0]}" não existe na tabela Tratamento.')
                continue
            for nome in nomes:
                if _norm(nome) in classes:
                    problemas.append(f'{onde}: a classificação "{nome.strip()}" já está em outra linha.')
                    continue
                classes.add(_norm(nome))
                flows.append({'classification': nome.strip(), 'flow': tipos[_norm(tipo[0])]})
    if not flows and not problemas:
        problemas.append('A tabela "Tipo de fluxo" precisa de pelo menos uma classificação.')
    return flows, flow_types, list(dict.fromkeys(problemas))
