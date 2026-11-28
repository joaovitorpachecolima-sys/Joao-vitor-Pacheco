"""KPIs da gestão: definição de cada indicador, julgamento contra a meta, achados e metas salvas."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import indicadores
from core import Conflict, Store
from indicadores import calcular, equipamentos, situacao, validar_metas
from operations import Operations

CONFIG = {'flow_types': [{'name': 'COMPLETO', 'followup': True, 'coordinator': True, 'recipient': 'coordenacao'},
                         {'name': 'SEM_COORD', 'followup': True, 'coordinator': False, 'recipient': 'coordenacao'}],
          'flows': [{'classification': 'Manutenção', 'flow': 'COMPLETO'}, {'classification': 'Distribuidora', 'flow': 'SEM_COORD'}]}
HOJE = date(2026, 11, 10)
_seq = [0]


def linha(start, sla='Ok', end='', classification='Manutenção', plant='Usina A', equipment='GG01', origin='Mancal', closed=None, **extra):
    _seq[0] += 1
    values = {'start_date': start[:10], 'start_time': start[11:] or '08:00', 'end_date': end[:10], 'end_time': end[11:], 'classification': classification,
              'plant': plant, 'equipment': equipment, 'origin': origin, 'coordination': 'Coordenação Norte', 'sla': sla, 'status': 'Contornada' if end else 'Aberta',
              'word_status': 'Não se aplica', 'document_sent': 'Não se aplica', **extra}
    return {'id': _seq[0], 'legacy_id': str(_seq[0]), 'control_row': True, 'values': values,
            'workflow': {'category': 'closed' if (closed if closed is not None else bool(end)) else 'forms'}}


def kpis(items, month, metas=None):
    return calcular(items, CONFIG, metas, today=HOJE)['by_month'][month]['kpis']


class Definicoes(unittest.TestCase):
    def test_equipamento_reconhece_unidades_e_ignora_usina_inteira(self):
        self.assertEqual(equipamentos('Usina - GG01'), {'GG01'})
        self.assertEqual(equipamentos('usina - ggo1'), {'GG01'})
        self.assertEqual(equipamentos('UG-02'), {'GG02'})
        self.assertEqual(equipamentos('GG01 ; GG02'), {'GG01', 'GG02'})
        self.assertEqual(equipamentos('Inversor 23'), {'INVERSOR 23'})
        for texto in ('Usina', 'UFV Usina J', 'PCH Usina A', ''):
            self.assertEqual(equipamentos(texto, 'Usina A'), set(), texto)

    def test_resolucao_24h_so_conta_manutencao_decidida(self):
        items = [linha('2026-10-01', 'Ok', '2026-10-01'), linha('2026-10-02', 'Ok', '2026-10-02'), linha('2026-10-03', 'Ok', '2026-10-03'),
                 linha('2026-10-04', 'Superior 24h', '2026-10-07'), linha('2026-10-05', 'Analisar'), linha('2026-10-31', '...'),
                 linha('2026-10-06', 'Ok', '2026-10-06', classification='Distribuidora'), linha('2026-10-07', 'Ok', '2026-10-07', classification='Distribuidora')]
        entry = kpis(items, '2026-10')['resolucao_24h']
        self.assertEqual((entry['value'], entry['n']), (60.0, 5))        # 3 de 5; "..." e Distribuidora ficam fora
        self.assertEqual(entry['failing'], [items[3]['id'], items[4]['id']])
        self.assertEqual(entry['status'], 'bad')                         # meta padrão 75, tolerância 10

    def test_duracao_das_longas_usa_mediana_e_conta_abertas_ate_hoje(self):
        items = [linha('2026-10-01 00:00', 'Superior 24h', '2026-10-03 00:00'), linha('2026-10-10 00:00', 'Superior 24h', '2026-10-14 00:00'),
                 linha('2026-10-31 23:59', 'Analisar')]                  # aberta: 10 dias até 10/11 23:59
        entry = kpis(items, '2026-10')['duracao_longas']
        self.assertEqual((entry['value'], entry['n']), (4.0, 3))
        self.assertEqual(entry['failing'], [items[2]['id']])             # só a de 10 dias passa da meta de 5
        self.assertIn('ainda aberta', entry['reading'])

    def test_backlog_e_reconstruido_no_fim_do_mes(self):
        items = [linha('2026-08-01', 'Analisar'),                                   # aberta desde agosto
                 linha('2026-08-20', 'Superior 24h', '2026-10-05'),                 # aberta em setembro, fechou em outubro
                 linha('2026-09-25', 'Analisar'),                                   # aberta, mas só 5 dias no fim de setembro
                 linha('2026-08-02', 'Ok', closed=True)]                            # sem data de fim, dada como encerrada: não conta
        setembro, outubro = kpis(items, '2026-09')['backlog_30d'], kpis(items, '2026-10')['backlog_30d']
        self.assertEqual((setembro['value'], setembro['n']), (2, 3))
        self.assertEqual(setembro['failing'], [items[0]['id'], items[1]['id']])     # a mais antiga primeiro
        self.assertEqual((outubro['value'], outubro['n']), (2, 2))                  # a de 25/09 passou de 30 dias; a de 20/08 fechou

    def test_reincidencia_e_a_mesma_causa_no_mesmo_equipamento_em_60_dias(self):
        items = [linha('2026-08-01', end='2026-08-01'), linha('2026-09-05', end='2026-09-05', origin='Aquecimento Mancal'),  # mesma família: Mancal
                 linha('2026-09-20', end='2026-09-20'),                              # Mancal de novo no GG01 de Usina A
                 linha('2026-09-25', end='2026-09-25', origin='Transformador'),      # mesmo equipamento, outra causa
                 linha('2026-09-26', end='2026-09-26', origin=''),                   # sem causa: não dá para dizer que repetiu
                 linha('2026-09-21', end='2026-09-21', equipment='Usina - GG02'),    # outro equipamento
                 linha('2026-09-22', end='2026-09-22', plant='Usina P'),            # outra usina
                 linha('2026-09-23', end='2026-09-23', equipment='Usina'),           # usina inteira: fora da base
                 linha('2026-09-24', end='2026-09-24', classification='Distribuidora')]
        entry = kpis(items, '2026-09')['reincidencia']
        self.assertEqual((entry['value'], entry['n']), (33.3, 6))
        self.assertEqual(entry['failing'], [items[1]['id'], items[2]['id']])
        self.assertIn('Usina A · GG01 · Mancal', entry['reading'])

    def test_registro_no_prazo_ignora_o_que_ainda_nao_venceu(self):
        doc = lambda deadline, returned='': linha('2026-10-01', 'Superior 24h', '2026-10-03', word_status='Word gerado', document_sent='Email enviado',
                                                  document_deadline_date=deadline, document_return_date=returned)
        items = [doc('2026-10-20', '2026-10-10'), doc('2026-10-20', '2026-10-25'), doc('2026-10-28'), doc('2026-11-17')]
        outubro = kpis(items, '2026-10')['registro_prazo']
        self.assertEqual((outubro['value'], outubro['n']), (33.3, 3))
        self.assertEqual(outubro['failing'], [items[1]['id'], items[2]['id']])
        self.assertEqual(kpis(items, '2026-11')['registro_prazo']['n'], 0)           # prazo 17/11 ainda não venceu

    def test_causa_informada(self):
        items = [linha('2026-10-01', end='2026-10-01'), linha('2026-10-02', end='2026-10-02', origin=''), linha('2026-10-03', end='2026-10-03', origin='-'),
                 linha('2026-10-04', end='2026-10-04', classification='Distribuidora', origin='Oscilação de Sistema')]
        entry = kpis(items, '2026-10')['causa_informada']
        self.assertEqual((entry['value'], entry['n'], len(entry['failing'])), (50.0, 4, 2))


class Julgamento(unittest.TestCase):
    def test_meta_atencao_fora_e_poucos_dados(self):
        alta, baixa = indicadores.KPIS[0], indicadores.KPIS[2]                       # % (maior é melhor) e contagem (menor é melhor)
        self.assertEqual([situacao(alta, v, 20, 75) for v in (80, 75, 66, 64)], ['ok', 'ok', 'warn', 'bad'])
        self.assertEqual(situacao(alta, 100, 4, 75), 'none')                         # menos de 5 casos: não julga
        self.assertEqual(situacao(alta, None, 0, 75), 'none')
        self.assertEqual([situacao(baixa, v, 0, 5) for v in (0, 5, 8, 9)], ['ok', 'ok', 'warn', 'bad'])

    def test_meta_personalizada_muda_o_julgamento(self):
        items = [linha(f'2026-10-0{d}', 'Ok', f'2026-10-0{d}') for d in range(1, 4)] + [linha('2026-10-04', 'Superior 24h', '2026-10-07'), linha('2026-10-05', 'Analisar')]
        self.assertEqual(kpis(items, '2026-10')['resolucao_24h']['status'], 'bad')
        self.assertEqual(kpis(items, '2026-10', {'resolucao_24h': 60})['resolucao_24h']['status'], 'ok')

    def test_metas_invalidas_sao_recusadas(self):
        self.assertEqual(validar_metas({'resolucao_24h': 80})['resolucao_24h'], 80)
        self.assertEqual(validar_metas({})['backlog_30d'], indicadores.KPI_DEFAULTS['backlog_30d'])
        for errado in ({'resolucao_24h': 0}, {'resolucao_24h': 101}, {'backlog_30d': 2.5}, {'outra': 1}, {'reincidencia': True}, []):
            with self.assertRaises(ValueError):
                validar_metas(errado)


class Achados(unittest.TestCase):
    def test_reincidencia_e_volume_entram_no_guia(self):
        items = [linha('2026-09-10', end='2026-09-10'), linha('2026-09-20', end='2026-09-20'), linha('2026-10-05', end='2026-10-05')]
        for month, quantity in (('2026-07', 6), ('2026-08', 6), ('2026-09', 6), ('2026-10', 30)):
            items += [linha(f'{month}-{d + 1:02d}', end=f'{month}-{d + 1:02d}', classification='Distribuidora', plant='Usina E', equipment='Usina',
                            origin='Oscilação de Sistema') for d in range(quantity)]
        month = calcular(items, CONFIG, today=HOJE)['by_month']['2026-10']
        titles = [found['title'] for found in month['findings']]
        self.assertIn('Usina A · GG01: Mancal voltou 3 vezes em 60 dias', titles)
        found = next(f for f in month['findings'] if f['kind'] == 'reincidencia')
        self.assertEqual((found['ref'], found['metric']['type'], found['metric']['args']), ('reincidencia:Usina A|GG01|Mancal', 'equip', ['Usina A', 'GG01', 'Mancal']))
        self.assertTrue(any(title.startswith('Volume') and title.endswith('puxado por Distribuidora') for title in titles), titles)
        self.assertTrue(any(title.startswith('Usina E: 30 ocorrências') for title in titles), titles)
        self.assertEqual(month['findings'][0]['level'], 3)                           # o mais grave vem primeiro
        self.assertEqual(month['volume']['total'], 31)
        self.assertEqual(month['patterns']['classes'][0]['name'], 'Distribuidora')

    def test_sem_ocorrencias_nao_quebra(self):
        vazio = calcular([], CONFIG, today=HOJE)
        self.assertEqual((vazio['months'], vazio['by_month']), ([], {}))
        self.assertEqual(len(vazio['definitions']), 6)


class MetasSalvas(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.ops = Operations(Store(Path(self.temp.name) / 'kpis.sqlite3'), Path(self.temp.name) / 'sem_modelo.docx')

    def tearDown(self):
        self.temp.cleanup()

    def test_salva_com_versao_e_recusa_conflito(self):
        self.assertEqual(self.ops.kpi_targets(), {'version': 0, 'targets': indicadores.KPI_DEFAULTS})
        saved = self.ops.save_kpi_targets({'version': 0, 'targets': {'resolucao_24h': 80, 'backlog_30d': 3}})
        self.assertEqual((saved['version'], saved['targets']['resolucao_24h'], saved['targets']['backlog_30d']), (1, 80, 3))
        self.assertEqual(saved['targets']['reincidencia'], indicadores.KPI_DEFAULTS['reincidencia'])
        with self.assertRaises(Conflict):
            self.ops.save_kpi_targets({'version': 0, 'targets': {'resolucao_24h': 70}})
        with self.assertRaises(ValueError):
            self.ops.save_kpi_targets({'version': 1, 'targets': {'resolucao_24h': 500}})
        self.assertEqual(self.ops.kpi_targets()['targets']['resolucao_24h'], 80)


if __name__ == '__main__':
    unittest.main()
