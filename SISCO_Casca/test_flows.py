"""Tipos de fluxo editáveis e destinatário por classificação, a partir das abas Classificacoes e Contatos."""
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from core import Store, TEST_EMAIL
from importer import ler_conteudo
from operations import Operations, normalize_config, routing_for
from planilha import escrever
from test_core import snapshot


def editable(config):
    return {k: v for k, v in config.items() if k in {'version', 'flow_types', 'flows', 'routes', 'class_routes',
                                                     'signature', 'document_days', 'reminder_days'}}


CLASSIFICACOES = [['Classificação', 'Tipo de fluxo', 'Acompanha', 'Registro formal', 'E-mail vai para'],
                  ['Manutenção', 'COMPLETO', 'Sim', 'Sim', 'Coordenação'],
                  ['TI', 'TI', 'Sim', 'Não', 'Classificação'],
                  ['Distribuidora', 'SEM_COORD', 'Sim', 'Não', 'Coordenação'],
                  ['Civil', 'INFORMATIVO', 'Não', 'Não', 'Coordenação']]
CONTATOS = [['Nome', 'Tipo', 'Para', 'CC', 'CCO'],
            ['Coordenação Norte', 'Coordenação', 'pessoa.r@x.com.br', 'pessoa.k@x.com.br', 'pessoa.j@x.com.br'],
            ['Manutenção', 'Classificação', '', 'pessoa.k@x.com.br', 'pessoa.j@x.com.br'],
            ['TI', 'Classificação', 'suporte@x.com.br', 'pessoa.b@x.com.br;cog@x.com.br', 'pessoa.j@x.com.br'],
            ['Civil', '', '', 'pessoa.j@x.com.br', 'pessoa.j@x.com.br']]


def ler(classificacoes=CLASSIFICACOES, contatos=CONTATOS):
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as pasta:
        caminho = Path(pasta) / 'dados.xlsx'
        abas = [('Ocorrencias', [['ID', 'Usina'], [1, 'Usina A']], None, ()),
                ('Usinas', [['Usina', 'Coordenação'], ['Usina A', 'Coordenação Norte']], None, ())]
        if classificacoes:
            abas.append(('Classificacoes', classificacoes, None, ()))
        if contatos:
            abas.append(('Contatos', contatos, None, ()))
        escrever(caminho, abas)
        return ler_conteudo(caminho.read_bytes(), caminho.name)


def configuracao():
    return ler()['configuration']


class ImportacaoDaConfig(unittest.TestCase):
    def test_contatos_por_classificacao_e_por_coordenacao(self):
        c = configuracao()
        self.assertEqual([r['classification'] for r in c['class_routes']], ['Manutenção', 'TI', 'Civil'])
        ti = next(r for r in c['class_routes'] if r['classification'] == 'TI')
        self.assertEqual((ti['to'], ti['cc'], ti['bcc']), ('suporte@x.com.br', 'pessoa.b@x.com.br;cog@x.com.br', 'pessoa.j@x.com.br'))
        manut = next(r for r in c['class_routes'] if r['classification'] == 'Manutenção')
        self.assertEqual((manut['to'], manut['cc'], manut['bcc']), ('', 'pessoa.k@x.com.br', 'pessoa.j@x.com.br'))
        self.assertEqual(c['routes'], [{'coordination': 'Coordenação Norte', 'to': 'pessoa.r@x.com.br',
                                        'cc': 'pessoa.k@x.com.br', 'bcc': 'pessoa.j@x.com.br'}])

    def test_tipos_de_fluxo_vem_da_tabela(self):
        tipos = {t['name']: t for t in configuracao()['flow_types']}
        self.assertEqual(set(tipos), {'COMPLETO', 'TI', 'SEM_COORD', 'INFORMATIVO'})
        self.assertEqual(tipos['TI'], {'name': 'TI', 'followup': True, 'coordinator': False, 'recipient': 'classificacao'})
        self.assertEqual(tipos['COMPLETO']['recipient'], 'coordenacao')
        self.assertTrue(tipos['COMPLETO']['coordinator'])

    def test_mesmo_tipo_com_comportamentos_diferentes_e_apontado(self):
        lido = ler(CLASSIFICACOES + [['Operacional', 'SEM_COORD', 'Não', 'Não', 'Coordenação']])
        self.assertTrue(any(i['kind'] == 'flow_type_mismatch' for i in lido['issues']))
        operacional = next(f for f in lido['configuration']['flows'] if f['classification'] == 'Operacional')
        self.assertTrue(operacional['followup'])                                   # vale a primeira linha do tipo

    def test_contato_sem_tipo_desconhecido_e_apontado(self):
        lido = ler(contatos=CONTATOS + [['Alguém', '', 'a@x.com.br', '', '']])
        self.assertTrue(any(i['kind'] == 'contact_kind' for i in lido['issues']))

    def test_sem_classificacoes_valem_as_padrao(self):
        c = ler(classificacoes=None)['configuration']
        self.assertIn('Manutenção', {f['classification'] for f in c['flows']})


class Roteamento(unittest.TestCase):
    def setUp(self):
        self.config = normalize_config(configuracao())

    def test_ti_vai_para_a_ti_com_cc_da_ti_sem_coordenacao(self):
        r = routing_for(self.config, {'classification': 'TI', 'coordination': 'Coordenação Norte'}, 'request')
        self.assertEqual((r['to'], r['cc'], r['bcc'], r['mode']),
                         ('suporte@x.com.br', 'pessoa.b@x.com.br;cog@x.com.br', 'pessoa.j@x.com.br', 'classificacao'))
        self.assertNotIn('pessoa.r@x.com.br', ' '.join([r['to'], r['cc'], r['bcc']]))

    def test_manutencao_continua_na_coordenacao(self):
        status = routing_for(self.config, {'classification': 'Manutenção', 'coordination': 'Coordenação Norte'}, 'reminder')
        self.assertEqual((status['to'], status['cc']), ('pessoa.r@x.com.br', 'pessoa.k@x.com.br'))
        word = routing_for(self.config, {'classification': 'Manutenção', 'coordination': 'Coordenação Norte'}, 'document')
        self.assertEqual((word['to'], word['bcc']), ('pessoa.r@x.com.br', 'pessoa.j@x.com.br'))

    def test_comportamento_vem_do_tipo(self):
        flows = {f['classification']: f for f in self.config['flows']}
        self.assertEqual((flows['TI']['followup'], flows['TI']['coordinator'], flows['TI']['recipient']), (True, False, 'classificacao'))
        self.assertEqual((flows['Civil']['followup'], flows['Civil']['coordinator']), (False, False))

    def test_configuracao_antiga_sem_tipos_e_convertida(self):
        antiga = {'flows': [{'classification': 'Manutenção', 'flow': 'COMPLETO', 'coordinator': True, 'followup': True},
                            {'classification': 'Especial', 'flow': 'PROPRIO', 'coordinator': False, 'followup': True}],
                  'routes': [], 'class_routes': [{'classification': 'Especial', 'cc': 'a@x.com.br', 'bcc': ''}]}
        nova = normalize_config(antiga)
        tipos = {t['name']: t for t in nova['flow_types']}
        self.assertIn('COMPLETO', tipos)
        self.assertEqual(tipos['PROPRIO'], {'name': 'PROPRIO', 'followup': True, 'coordinator': False, 'recipient': 'coordenacao'})
        self.assertEqual(nova['class_routes'][0]['to'], '')


class SalvarConfiguracao(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'f.sqlite3')
        data = snapshot()
        data['configuration'] = configuracao()
        self.store.ingest(data)
        self.ops = Operations(self.store, Path(self.temp.name) / 'missing.docx')

    def tearDown(self):
        self.temp.cleanup()

    def test_novo_tipo_de_fluxo_e_mudanca_de_classificacao(self):
        c = editable(self.ops.config())
        c['flow_types'].append({'name': 'Somente TI e cópia', 'followup': True, 'coordinator': True, 'recipient': 'classificacao'})
        c['flows'].append({'classification': 'Telecom', 'flow': 'somente ti e cópia'})
        c['class_routes'].append({'classification': 'Telecom', 'to': 'telecom@x.com.br', 'cc': '', 'bcc': ''})
        next(f for f in c['flows'] if f['classification'] == 'Distribuidora')['flow'] = 'INFORMATIVO'
        salvo = self.ops.save_config(c)
        flows = {f['classification']: f for f in salvo['flows']}
        self.assertEqual(flows['Telecom']['flow'], 'Somente TI e cópia')
        self.assertEqual((flows['Telecom']['coordinator'], flows['Telecom']['recipient']), (True, 'classificacao'))
        self.assertFalse(flows['Distribuidora']['followup'])
        r = routing_for(salvo, {'classification': 'Telecom'}, 'document')
        self.assertEqual((r['to'], r['cc']), ('telecom@x.com.br', ''))

    def test_alterar_o_tipo_muda_todas_as_classificacoes_dele(self):
        c = editable(self.ops.config())
        next(t for t in c['flow_types'] if t['name'] == 'SEM_COORD')['followup'] = False
        salvo = self.ops.save_config(c)
        self.assertFalse(next(f for f in salvo['flows'] if f['classification'] == 'Distribuidora')['followup'])

    def test_recusas(self):
        casos = {
            'tipo inexistente': lambda c: c['flows'].append({'classification': 'X', 'flow': 'NAO_EXISTE'}),
            'tipo repetido': lambda c: c['flow_types'].append({**c['flow_types'][0], 'name': c['flow_types'][0]['name'].lower()}),
            'destinatário inválido': lambda c: c['flow_types'][0].update(recipient='todos'),
            'TI sem e-mail': lambda c: next(r for r in c['class_routes'] if r['classification'] == 'TI').update(to=''),
            'tipo em uso removido': lambda c: c.__setitem__('flow_types', [t for t in c['flow_types'] if t['name'] != 'TI']),
            'nome vazio': lambda c: c['flow_types'].append({'name': '  ', 'followup': True, 'coordinator': False, 'recipient': 'coordenacao'}),
        }
        for nome, mudar in casos.items():
            with self.subTest(nome):
                c = editable(self.ops.config())
                mudar(c)
                with self.assertRaises(ValueError):
                    self.ops.save_config(c)
        self.assertEqual(self.ops.config()['version'], 0)

    def test_previa_da_ti_mostra_so_a_ti_e_envio_continua_de_teste(self):
        item = self.store.get(1)
        self.store.update(1, {'version': item['version'], 'values': {'classification': 'TI'}, 'reviewed': True})
        item = self.store.get(1)
        from unittest.mock import patch
        with patch('operations.derive', return_value={'sla': 'Analisar'}):
            p = self.ops.preview(1, {'version': item['version'], 'kind': 'request'}, 8780)
        self.assertEqual((p['to'], p['cc'], p['bcc']), (TEST_EMAIL, '', ''))
        self.assertEqual((p['original_routing']['to'], p['original_routing']['cc']), ('suporte@x.com.br', 'pessoa.b@x.com.br;cog@x.com.br'))


if __name__ == '__main__':
    unittest.main()
