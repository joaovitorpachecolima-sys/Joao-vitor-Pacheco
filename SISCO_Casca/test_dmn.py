"""Ponte DMN: as regras viram tabelas de decisão e voltam iguais; o que a tabela muda chega às regras; erro é apontado."""
import unittest
import xml.etree.ElementTree as ET

from dmn import NS, gerar_dmn, ler_dmn, tratar_pedido
from workflow import DEFAULT_FLOW_TYPES, DEFAULT_FLOWS

TIPOS = [dict(t) for t in DEFAULT_FLOW_TYPES]
FLUXOS = [{'classification': f['classification'], 'flow': f['flow']} for f in DEFAULT_FLOWS]


class IdaEVolta(unittest.TestCase):
    def test_regras_padrao_voltam_identicas(self):
        flows, types, problemas = ler_dmn(gerar_dmn(FLUXOS, TIPOS))
        self.assertEqual(problemas, [])
        self.assertEqual(flows, FLUXOS)
        self.assertEqual(types, TIPOS)

    def test_aspas_e_acentos_sobrevivem(self):
        fluxos = [{'classification': 'Telecom "fibra" \\ Ação', 'flow': TIPOS[0]['name']}]
        flows, _, problemas = ler_dmn(gerar_dmn(fluxos, TIPOS))
        self.assertEqual((flows, problemas), (fluxos, []))

    def test_arquivo_e_dmn_com_duas_decisoes_ligadas(self):
        raiz = ET.fromstring(gerar_dmn(FLUXOS, TIPOS))
        self.assertEqual([d.get('name') for d in raiz.findall('dmn:decision', NS)], ['Tipo de fluxo', 'Tratamento do tipo de fluxo'])
        self.assertEqual(len(raiz.findall('dmn:decision/dmn:decisionTable/dmn:rule', NS)), len(FLUXOS) + len(TIPOS))
        self.assertIsNotNone(raiz.find('.//dmn:requiredDecision[@href="#tipo_de_fluxo"]', NS))
        ids = [e.get('id') for e in raiz.iter() if e.get('id')]
        self.assertEqual(len(ids), len(set(ids)))
        desenhados = {e.get('dmnElementRef') for e in raiz.iter() if e.get('dmnElementRef')}
        self.assertTrue({'classificacao', 'tipo_de_fluxo', 'tratamento', 'req_classificacao', 'req_tipo'} <= desenhados)


class EdicaoNaTabela(unittest.TestCase):
    def editar(self, *trocas):
        xml = gerar_dmn(FLUXOS, TIPOS)
        for antes, depois in trocas:
            self.assertIn(antes, xml)
            xml = xml.replace(antes, depois, 1)
        return ler_dmn(xml)

    def test_trocar_o_tipo_e_varias_classificacoes_numa_linha(self):
        primeira, outro = FLUXOS[0], next(t['name'] for t in TIPOS if t['name'] != FLUXOS[0]['flow'])
        flows, _, problemas = self.editar((f'<outputEntry id="tipo_r1_s"><text>"{primeira["flow"]}"</text>',
                                           f'<outputEntry id="tipo_r1_s"><text>"{outro}"</text>'),
                                          (f'<inputEntry id="tipo_r1_e"><text>"{primeira["classification"]}"</text>',
                                           f'<inputEntry id="tipo_r1_e"><text>"{primeira["classification"]}", "Nova classe"</text>'))
        self.assertEqual(problemas, [])
        self.assertEqual(flows[0], {'classification': primeira['classification'], 'flow': outro})
        self.assertEqual(flows[1], {'classification': 'Nova classe', 'flow': outro})

    def test_mudar_o_tratamento_de_um_tipo(self):
        _, types, problemas = self.editar(('<outputEntry id="trat_r1_acompanha"><text>true</text>', '<outputEntry id="trat_r1_acompanha"><text>false</text>'))
        self.assertEqual(problemas, [])
        self.assertFalse(types[0]['followup'])

    def test_problemas_sao_apontados(self):
        casos = [
            (('<inputEntry id="tipo_r1_e"><text>', '<inputEntry id="tipo_r1_e"><text>-'), 'entre aspas'),
            (('<outputEntry id="tipo_r1_s"><text>"', '<outputEntry id="tipo_r1_s"><text>"NAO_EXISTE'), 'não existe na tabela Tratamento'),
            (('<outputEntry id="trat_r1_acompanha"><text>true', '<outputEntry id="trat_r1_acompanha"><text>talvez'), 'true ou false'),
            (('<outputEntry id="trat_r1_destino"><text>"coordenacao"', '<outputEntry id="trat_r1_destino"><text>"todos"'), 'coordenacao'),
            (('hitPolicy="UNIQUE"', 'hitPolicy="FIRST"'), 'UNIQUE'),
        ]
        for troca, esperado in casos:
            _, _, problemas = self.editar(troca)
            self.assertTrue(any(esperado in p for p in problemas), (troca, problemas))
        repetida = FLUXOS[1]['classification']
        _, _, problemas = self.editar((f'<inputEntry id="tipo_r1_e"><text>"{FLUXOS[0]["classification"]}"', f'<inputEntry id="tipo_r1_e"><text>"{FLUXOS[0]["classification"]}", "{repetida}"'))
        self.assertTrue(any('já está em outra linha' in p for p in problemas), problemas)

    def test_arquivo_perigoso_ou_sem_as_decisoes_e_recusado(self):
        for xml in ('<!DOCTYPE x [<!ENTITY a "b">]><x/>', '<definitions', 'nada', 5,
                    f'<definitions xmlns="{NS["dmn"]}" id="d" name="d" namespace="x"/>'):
            with self.assertRaises(ValueError):
                ler_dmn(xml)

    def test_rota_valida_o_pedido(self):
        self.assertIn('<decisionTable', tratar_pedido({'flows': FLUXOS, 'flow_types': TIPOS})['dmn'])
        self.assertEqual(tratar_pedido({'dmn': gerar_dmn(FLUXOS, TIPOS)})['problems'], [])
        with self.assertRaises(ValueError):
            tratar_pedido({'flows': 'x', 'flow_types': TIPOS})


if __name__ == '__main__':
    unittest.main()
