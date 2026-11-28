"""Gestão local: famílias de causa, ações com antes/depois, linha do tempo, motivo da espera e histórico das UGs."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import indicadores
from core import Conflict, Store
from gestao import CAUSE_DEFAULTS, NO_FAMILY, Gestao, etapas_por_mes, familia, linha_do_tempo, validar_familias
from test_core import snapshot
from test_indicadores import CONFIG, linha

HOJE = date(2026, 11, 10)


class Causas(unittest.TestCase):
    def test_textos_diferentes_caem_na_mesma_familia(self):
        for texto in ('Mancal', 'Aquecimento Mancal', 'Temperatura Elevada Mancal'):
            self.assertEqual(familia(texto, CAUSE_DEFAULTS), 'Mancal', texto)      # Mancal vem antes de temperatura
        self.assertEqual(familia('Oscilação de Sistema', CAUSE_DEFAULTS), 'Rede / distribuidora')
        self.assertEqual(familia('Revisão Geral eletromecânica  Preventiva', CAUSE_DEFAULTS), 'Manutenção programada')
        self.assertEqual(familia('Troca do DJ', CAUSE_DEFAULTS), 'Elétrico e proteção')
        self.assertEqual(familia('Adjacente', [{'name': 'X', 'keywords': ['dj']}]), NO_FAMILY)  # palavra curta só vale inteira
        self.assertEqual(familia('Deslocamento', CAUSE_DEFAULTS), NO_FAMILY)
        self.assertEqual([familia(t, CAUSE_DEFAULTS) for t in ('', '-', None)], ['', '', ''])

    def test_familias_invalidas_sao_recusadas(self):
        ok = validar_familias([{'name': ' Mancal ', 'keywords': ['Mancal', 'MANCAL', 'Bucha']}])
        self.assertEqual(ok, [{'name': 'Mancal', 'keywords': ['mancal', 'bucha']}])
        for errado in ([], [{'name': '', 'keywords': ['a']}], [{'name': 'A', 'keywords': []}], [{'name': NO_FAMILY, 'keywords': ['a']}],
                       [{'name': 'A', 'keywords': ['a']}, {'name': 'a', 'keywords': ['b']}], [{'name': 'A'}], 'x'):
            with self.assertRaises(ValueError):
                validar_familias(errado)


class Banco(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'gestao.sqlite3')
        self.store.ingest(snapshot())
        self.gestao = Gestao(self.store)
        self.item = self.store.list_items()[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_causas_salvas_com_versao(self):
        self.assertEqual(self.gestao.causes()['version'], 0)
        saved = self.gestao.save_causes({'version': 0, 'families': [{'name': 'Mancal', 'keywords': ['mancal']}]})
        self.assertEqual((saved['version'], len(saved['families'])), (1, 1))
        with self.assertRaises(Conflict):
            self.gestao.save_causes({'version': 0, 'families': [{'name': 'Outra', 'keywords': ['x']}]})

    def test_primeiro_envio_do_forms_nao_e_sobrescrito(self):
        item = {'id': self.item['id'], 'values': {'forms_sent_at': '2026-11-09T10:00'}}
        self.assertEqual(self.gestao.first_forms([item], HOJE)[item['id']], ('2026-11-09T10:00', False))
        item['values']['forms_sent_at'] = '2026-11-20T09:00'                         # a planilha sobrescreveu com a cobrança
        self.assertEqual(self.gestao.first_forms([item], date(2026, 11, 20))[item['id']], ('2026-11-09T10:00', False))
        antigo = {'id': self.item['id'] + 1000, 'values': {'forms_sent_at': '2026-10-01T10:00'}}
        self.assertEqual(self.gestao.first_forms([antigo], HOJE)[antigo['id']], ('2026-10-01T10:00', True))  # visto tarde: estimado

    def test_motivo_da_espera(self):
        saved = self.gestao.save_wait(self.item['id'], {'reason': 'Peça', 'note': ' rolamento  encomendado '})
        self.assertEqual((saved['reason'], saved['note']), ('Peça', 'rolamento  encomendado'))
        self.assertIsNone(self.gestao.save_wait(self.item['id'], {'reason': ''}))
        self.assertEqual(self.gestao.waits(), {})
        for errado in ({'reason': 'Preguiça'}, {'note': 'x'}, {'reason': 'Peça', 'extra': 1}):
            with self.assertRaises(ValueError):
                self.gestao.save_wait(self.item['id'], errado)
        with self.assertRaises(KeyError):
            self.gestao.save_wait(999999, {'reason': 'Peça'})

    def test_acao_com_antes_e_depois(self):
        items = [linha('2026-09-20', end='2026-09-20'), linha('2026-10-05', end='2026-10-05'), linha('2026-11-01', end='2026-11-01')]
        rows, scope = indicadores.preparar(items, CONFIG, today=HOJE)
        metric = {'type': 'equip', 'args': ['Usina A', 'GG01', 'Mancal'], 'label': 'x', 'better': 'down'}
        metric['value'] = indicadores.medir(metric, rows, scope, HOJE)
        self.assertEqual(metric['value'], 3)
        body = {'ref': 'reincidencia:Usina A|GG01|Mancal', 'title': 'Causa raiz do mancal', 'todo': 'Pedir análise', 'owner': 'Pessoa A', 'due': '2026-11-30'}
        action_id = self.gestao.create_action(dict(body), metric)
        with self.assertRaises(Conflict):                                            # o mesmo achado não abre duas ações
            self.gestao.create_action(dict(body), metric)
        for errado in ({**body, 'ref': 'x', 'owner': ''}, {**body, 'ref': 'x', 'due': 'amanhã'}, {**body, 'ref': 'x', 'extra': 1}):
            with self.assertRaises(ValueError):
                self.gestao.create_action(errado)
        aberta = indicadores.acompanhar(self.gestao.actions(), rows, scope, HOJE)[0]
        self.assertEqual((aberta['current'], aberta['effect'], aberta['late'], aberta['review']), (3, 'igual', False, None))
        self.assertTrue(indicadores.acompanhar(self.gestao.actions(), rows, scope, date(2026, 12, 5))[0]['late'])
        self.gestao.update_action(action_id, {'status': 'concluida', 'note': 'trocado o mancal'})
        depois = date(2026, 12, 20)                                                  # 60 dias atrás = 21/10: sobra só a de 01/11
        rows, scope = indicadores.preparar(items, CONFIG, today=depois)
        feita = indicadores.acompanhar(self.gestao.actions(), rows, scope, depois)[0]
        self.assertEqual((feita['status'], feita['current'], feita['effect'], feita['late']), ('concluida', 1, 'melhorou', False))
        self.gestao.update_action(action_id, {'status': 'cancelada'})
        self.assertEqual(self.gestao.actions(), [])
        with self.assertRaises(KeyError):
            self.gestao.update_action(999, {'status': 'aberta'})

    def test_historico_das_ugs(self):
        dia = lambda day, cor: {'usinas': [{'chave': 'usina a', 'recebido': f'{day}T07:00:00', 'unidades': [{'nome': 'UG 01', 'cor': cor, 'mw': 1.5}]}]}
        for day, cor in (('2026-11-07', 'verde'), ('2026-11-08', 'amarelo'), ('2026-11-09', 'vermelho'), ('2026-11-10', 'azul'), ('2026-11-10', 'verde')):
            self.gestao.record_units(dia(day, cor))
        resumo = self.gestao.unit_history(HOJE)[('usina a', 'UG 01')]
        self.assertEqual([d['color'] for d in resumo['days']], ['verde', 'amarelo', 'vermelho', 'verde'])  # o relatório mais novo do dia vale
        self.assertEqual((resumo['known'], resumo['stopped'], resumo['unexplained'], resumo['availability']), (4, 2, 1, 50))
        self.assertEqual(self.gestao.unit_history(date(2027, 1, 1)), {})


class LinhaDoTempo(unittest.TestCase):
    def item(self, item_id=1, **values):
        base = {'start_date': '2026-10-01', 'start_time': '08:00', 'end_date': '2026-10-04', 'end_time': '08:00', 'sla': 'Superior 24h',
                'word_status': 'Word gerado (05/10/2026 09:00)', 'document_sent': 'Email enviado (06/10/2026 16:05)', 'document_return_date': '2026-10-16'}
        return {'id': item_id, 'control_row': True, 'values': {**base, **values}, 'workflow': {'category': 'closed'}}

    def test_datas_de_cada_etapa(self):
        timeline = linha_do_tempo(self.item(), ('2026-10-02T10:00', False), [{'submitted_at': '2026-10-05T12:00'}, {'submitted_at': '2026-10-03T12:00'}])
        times = {step['key']: step['at'] for step in timeline['steps']}
        self.assertEqual(times, {'inicio': '2026-10-01T08:00', 'fim': '2026-10-04T08:00', 'forms': '2026-10-02T10:00', 'retorno': '2026-10-03T12:00',
                                 'word': '2026-10-05T09:00', 'doc_enviado': '2026-10-06T16:05', 'doc_devolvido': '2026-10-16T00:00'})
        self.assertEqual(timeline['limit'], '2026-10-02T08:00')
        self.assertEqual(linha_do_tempo(self.item(word_status='Adiantar Word'))['steps'][4]['at'], '')

    def test_onde_o_tempo_fica(self):
        first = self.item()
        aberta = self.item(2, end_date='', end_time='', sla='Analisar', word_status='Adiantar Word', document_sent='Enviar Email', document_return_date='')
        aberta['workflow'] = {'category': 'forms'}
        timelines = {1: linha_do_tempo(first, ('2026-10-02T10:00', False), [{'submitted_at': '2026-10-03T10:00'}]),
                     2: linha_do_tempo(aberta, ('2026-10-02T08:00', False))}
        stages = {stage['key']: stage for stage in etapas_por_mes([first, aberta], timelines, date(2026, 10, 11))['2026-10']}
        self.assertEqual((stages['reparo']['median'], stages['reparo']['n'], stages['reparo']['running']), (6.8, 2, 1))  # 3 dias e 10,67 (aberta, até hoje)
        self.assertEqual(stages['solicitacao']['median'], 0.0)                       # 2 h e 0 h depois do limite de 24 h
        self.assertEqual((stages['retorno']['n'], stages['retorno']['running']), (2, 1))
        self.assertEqual((stages['emitir']['median'], stages['devolver']['median'], stages['devolver']['owner']), (2.3, 9.3, 'Coordenação'))


if __name__ == '__main__':
    unittest.main()
