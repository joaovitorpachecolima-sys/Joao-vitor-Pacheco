"""Ponte BPMN: o fluxo vira BPMN 2.0 e volta igual; o que o desenho muda chega ao motor; o que está errado é apontado."""
import copy
import random
import unittest
import xml.etree.ElementTree as ET

from bpmn import NS, gerar_bpmn, ler_bpmn
from fluxo import executar, fluxo_padrao, validar
from test_fluxo import CHAVES, CLOCK, CONFIG, sorteio
from workflow import derive_codigo


def sem_formato(fluxo):
    return {k: v for k, v in fluxo.items() if k not in ('versao_formato', 'layout')}


class IdaEVolta(unittest.TestCase):
    def test_fluxo_padrao_volta_identico(self):
        fluxo, problemas = ler_bpmn(gerar_bpmn(fluxo_padrao()))
        self.assertEqual(problemas, [])
        self.assertEqual(sem_formato(fluxo), sem_formato(fluxo_padrao()))
        self.assertEqual(validar(fluxo), [])

    def test_motor_igual_lendo_o_bpmn(self):
        fluxo, _ = ler_bpmn(gerar_bpmn(fluxo_padrao()))
        rng = random.Random(7)
        for caso in range(8000):
            item = sorteio(rng)
            esperado, obtido = derive_codigo(copy.deepcopy(item), CONFIG, CLOCK), executar(fluxo, item, CONFIG, CLOCK)
            for chave in CHAVES:
                self.assertEqual(obtido[chave], esperado[chave], f'caso {caso}, {chave}')

    def test_arquivo_e_bpmn_padrao_com_subprocessos_e_piscinas(self):
        raiz = ET.fromstring(gerar_bpmn(fluxo_padrao()))
        processo = raiz.find('bpmn:process', NS)
        self.assertEqual([s.get('name') for s in processo.findall('bpmn:subProcess', NS)], ['Triagem', 'Monitoramento 24h', 'Retorno da manutenção', 'Registro da ocorrência'])
        self.assertEqual(len(raiz.findall('bpmn:collaboration/bpmn:participant', NS)), 2)
        self.assertEqual(len(raiz.findall('bpmn:collaboration/bpmn:messageFlow', NS)), 4)
        self.assertEqual(len(raiz.findall('bpmndi:BPMNDiagram', NS)), 5)                 # nível de cima + 4 subprocessos
        self.assertEqual(len(processo.findall('.//bpmn:exclusiveGateway', NS)), 22)
        self.assertEqual(len(processo.findall('.//bpmn:userTask', NS)) + len(processo.findall('.//bpmn:sendTask', NS)) + len(processo.findall('.//bpmn:serviceTask', NS)), 18)
        ids = [e.get('id') for e in raiz.iter() if e.get('id')]
        self.assertEqual(len(ids), len(set(ids)))                                        # nenhum identificador repetido
        desenhados = {e.get('bpmnElement') for e in raiz.iter() if e.tag.endswith('BPMNShape') or e.tag.endswith('BPMNEdge')}
        com_desenho = {e.get('id') for e in processo.iter() if e.get('id') and not e.tag.endswith('Definition')} - {'processo'}  # o processo é a piscina
        self.assertEqual(com_desenho - desenhados, set())                                # tudo tem posição no desenho
        primeiro = raiz.find('bpmndi:BPMNDiagram/bpmndi:BPMNPlane', NS)
        self.assertEqual(primeiro.get('bpmnElement'), 'colaboracao')                    # os editores abrem o primeiro desenho


class EdicaoNoDesenho(unittest.TestCase):
    def editar(self, troca):
        xml = gerar_bpmn(fluxo_padrao())
        for antes, depois in troca:
            self.assertIn(antes, xml)
            xml = xml.replace(antes, depois, 1)
        return ler_bpmn(xml)

    def test_mudanca_no_desenho_chega_ao_motor(self):
        fluxo, problemas = self.editar([('sisco:fase="closed"', 'sisco:fase="documents"')])
        self.assertEqual(problemas, [])
        self.assertEqual(fluxo['elementos']['s_encerrada']['fase'], 'documents')
        fluxo, _ = self.editar([('sisco:pergunta="nao_revisada"', 'sisco:pergunta="tipo_sem_acompanhamento"')])
        self.assertEqual(fluxo['elementos']['q_revisada']['condicao'], 'tipo_sem_acompanhamento')

    def test_problemas_do_desenho_sao_apontados(self):
        # Ligação sem o atributo do ramo e sem o nome Sim/Não: o leitor não adivinha.
        _, problemas = self.editar([(' name="Sim" sourceRef="q_classificacao"', ' sourceRef="q_classificacao"'),
                                    ('targetRef="s_aguardando_classificacao" sisco:ramo="sim"', 'targetRef="s_aguardando_classificacao"')])
        self.assertTrue(any('não diz se é o caminho Sim ou Não' in p for p in problemas), problemas)
        _, problemas = self.editar([('<bpmn:startEvent id="triagem__entrada" name="Entrada" />', '')])
        self.assertTrue(any('exatamente um evento de início' in p for p in problemas), problemas)

    def test_elemento_novo_sem_configuracao_e_recusado_pelo_validador(self):
        fluxo, _ = self.editar([('<bpmn:subProcess id="triagem"', '<bpmn:exclusiveGateway id="Gateway_novo" name="Nova" />\n<bpmn:subProcess id="triagem"')])
        self.assertIn('Gateway_novo', fluxo['elementos'])
        self.assertTrue(any('Nova' in p for p in validar(fluxo)))

    def test_arquivo_perigoso_ou_quebrado_e_recusado(self):
        for xml in ('<!DOCTYPE x [<!ENTITY a "b">]><x/>', '<bpmn:definitions', 'nada', 5):
            with self.assertRaises(ValueError):
                ler_bpmn(xml)


class PeloServidor(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from core import Store
        from operations import Operations
        from test_core import snapshot
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        store = Store(Path(self.temp.name) / 'bpmn.sqlite3')
        store.ingest(snapshot())
        self.ops = Operations(store, Path(self.temp.name) / 'sem_modelo.docx')

    def tearDown(self):
        self.temp.cleanup()

    def test_rascunho_do_desenho_publicado_e_restaurado(self):
        inicial = self.ops.flow_bpmn()
        self.assertEqual((inicial['version'], inicial['draft']), (0, None))
        self.assertEqual(sem_formato(ler_bpmn(inicial['published'])[0]), sem_formato(fluxo_padrao()))
        editado = inicial['published'].replace('sisco:fase="closed"', 'sisco:fase="documents"', 1)
        resposta = self.ops.save_flow_draft({'bpmn': editado})
        self.assertEqual(resposta['draft']['problems'], [])
        self.assertNotIn('bpmn', resposta['draft'])                                     # o arquivo não vai no estado
        self.assertEqual(self.ops.flow_bpmn()['draft'], editado)
        self.ops.publish_flow({'version': 0, 'note': 'pelo desenho'})
        self.assertEqual(self.ops.config()['fluxo']['elementos']['s_encerrada']['fase'], 'documents')   # o motor segue o desenho
        self.assertEqual(self.ops.flow_bpmn()['published'], editado)                    # o desenho publicado é o editado
        self.ops.restore_flow({'version': 0})
        self.assertEqual(sem_formato(self.ops.flow_state()['draft']['flow']), sem_formato(fluxo_padrao()))

    def test_organizar_redesenha_o_rascunho_sem_mudar_as_regras(self):
        editado = self.ops.flow_bpmn()['published'].replace('sisco:fase="closed"', 'sisco:fase="documents"', 1)
        editado = editado.replace('</bpmn:process>', '<bpmn:textAnnotation id="nota"><bpmn:text>feito à mão</bpmn:text></bpmn:textAnnotation></bpmn:process>', 1)
        self.ops.save_flow_draft({'bpmn': editado})
        organizado = self.ops.flow_bpmn()['organized']
        self.assertNotIn('feito à mão', organizado)                                       # o desenho é refeito
        fluxo, problemas = ler_bpmn(organizado)
        self.assertEqual(problemas, [])
        self.assertEqual(fluxo['elementos']['s_encerrada']['fase'], 'documents')          # as regras do rascunho ficam

    def test_desenho_com_problema_nao_publica(self):
        quebrado = self.ops.flow_bpmn()['published'].replace('<bpmn:startEvent id="triagem__entrada" name="Entrada" />', '')
        resposta = self.ops.save_flow_draft({'bpmn': quebrado})
        self.assertTrue(resposta['draft']['problems'])
        self.assertIsNone(resposta['simulation'])
        with self.assertRaises(ValueError):
            self.ops.publish_flow({'version': 0})
        with self.assertRaises(ValueError):
            self.ops.save_flow_draft({'bpmn': '<!DOCTYPE x><x/>'})


if __name__ == '__main__':
    unittest.main()
