"""Regras do fluxo configuráveis: parâmetros, rascunho, simulação, publicação e histórico."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import operations
from core import Store, Conflict
from operations import Operations
from workflow import RULE_DEFAULTS, derive, forms_deadline
from test_core import snapshot


class Parametros(unittest.TestCase):
    config = {'flows': [{'classification': 'Manutenção', 'flow': 'COMPLETO', 'coordinator': True, 'followup': True}], 'reminder_days': 7}

    def item(self, **values):
        base = {'classification': 'Manutenção', 'start_date': '2026-11-09', 'start_time': '08:00'}
        return {'values': {**base, **values}, 'reviewed': True}

    def test_padrao_mantem_24h_e_9h(self):
        clock = datetime(2026, 11, 10, 7, 0)  # 23 h depois do início
        wf = derive(self.item(), self.config, clock)
        self.assertEqual(wf['conclusion'], 'Em monitoramento 24h')
        self.assertEqual(wf['due_at'], '2026-11-10T08:00')
        self.assertEqual(forms_deadline({'forecast_date': '2026-11-12'}, self.config), datetime(2026, 11, 13, 9, 0))

    def test_limite_e_hora_configurados(self):
        config = {**self.config, 'sla_hours': 12, 'followup_hour': 14}
        wf = derive(self.item(), config, datetime(2026, 11, 10, 7, 0))
        self.assertNotEqual(wf['conclusion'], 'Em monitoramento 24h')  # 23 h > 12 h: saiu do monitoramento
        self.assertEqual(wf['sla'], 'Analisar')
        self.assertEqual(derive(self.item(), config, datetime(2026, 11, 9, 10, 0))['due_at'], '2026-11-09T20:00')
        self.assertEqual(forms_deadline({'forecast_date': '2026-11-12'}, config), datetime(2026, 11, 13, 14, 0))


class RascunhoEPublicacao(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'regras.sqlite3')
        self.ops = Operations(self.store, Path(self.temp.name) / 'sem_modelo.docx')
        data = snapshot()
        now = datetime.now()
        data['records'][0]['source_values'].update(plant='Usina A', equipment='UG01', start_date=(now - timedelta(hours=10)).strftime('%Y-%m-%d'),
                                                   start_time=(now - timedelta(hours=10)).strftime('%H:%M'), end_date='', end_time='', conclusion='')
        self.store.ingest(data)
        item = self.store.get(1)
        self.store.update(1, {'version': item['version'], 'reviewed': True})
        # Configuração publicada de partida (versão 1), com o e-mail da TI como na base real.
        config = self.ops.config()
        body = {k: config[k] for k in ('version', 'flow_types', 'routes', 'signature', 'document_days', 'reminder_days')}
        body['flows'] = [{'classification': f['classification'], 'flow': f['flow']} for f in config['flows']]
        body['class_routes'] = [{'classification': 'TI', 'to': 'suporte@x.com.br', 'cc': '', 'bcc': ''}]
        self.ops.save_config(body)

    def tearDown(self):
        self.temp.cleanup()

    def test_padrao_aparece_na_configuracao(self):
        config = self.ops.config()
        for key, value in RULE_DEFAULTS.items():
            self.assertEqual(config[key], value)

    def test_rascunho_nao_muda_o_publicado_e_simulacao_mostra_a_diferenca(self):
        self.assertEqual(self.ops.config()['version'], 1)
        result = self.ops.save_draft({'rules': {'sla_hours': 6}})
        self.assertEqual(self.ops.config()['sla_hours'], 24)                     # publicado intacto
        self.assertEqual(self.ops.rules()['draft']['rules']['sla_hours'], 6)
        sim = result['simulation']
        self.assertEqual(sim['changed_total'], 1)                                # 10 h: sai do monitoramento com 6 h
        mudou = sim['changed'][0]
        self.assertEqual(mudou['before']['phase'], 'monitoring')
        self.assertNotEqual(mudou['after']['phase'], 'monitoring')
        self.assertEqual(sim['before'].get('monitoring'), 1)
        self.assertFalse(sim['after'].get('monitoring'))

    def test_valores_invalidos_sao_recusados(self):
        for rules in ({'sla_hours': 0}, {'followup_hour': 24}, {'phase_targets': {'forms': 0}}, {'phase_targets': {'inexistente': 3}},
                      {'ug_occurrence_days': 'muito'}, {'qualquer': 1}, {'flows': [{'classification': 'Manutenção', 'flow': 'NAO_EXISTE'}]}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                self.ops.save_draft({'rules': rules})
        self.assertIsNone(self.ops.rules()['draft'])

    def test_publicar_aplica_guarda_versao_e_limpa_rascunho(self):
        self.ops.save_draft({'rules': {'sla_hours': 6, 'phase_targets': {'forms': 5}}})
        published = self.ops.publish_draft({'version': 1, 'note': 'Teste: 6 h'})
        self.assertEqual((published['version'], published['sla_hours'], published['phase_targets']['forms']), (2, 6, 5))
        self.assertEqual(published['phase_targets']['documents'], 14)
        rules = self.ops.rules()
        self.assertIsNone(rules['draft'])
        self.assertEqual(rules['versions'][0]['version'], 1)
        self.assertEqual(rules['versions'][0]['note'], 'Teste: 6 h')

    def test_publicar_com_versao_desatualizada_e_recusado(self):
        self.ops.save_draft({'rules': {'sla_hours': 6}})
        with self.assertRaises(Conflict):
            self.ops.publish_draft({'version': 3})
        self.ops.save_draft({'rules': {'sla_hours': 6}})
        self.ops.save_draft({'rules': {'sla_hours': 8}})
        self.assertEqual(self.ops.publish_draft({'version': 1})['sla_hours'], 8)

    def test_voltar_a_versao_anterior_passa_pelo_rascunho(self):
        self.ops.save_draft({'rules': {'sla_hours': 6}})
        self.ops.publish_draft({'version': 1})
        restored = self.ops.restore_version({'version': 1})
        self.assertEqual(restored['draft']['rules']['sla_hours'], 24)
        self.assertEqual(self.ops.config()['sla_hours'], 6)                      # só muda ao publicar
        self.assertEqual(self.ops.publish_draft({'version': 2})['sla_hours'], 24)

    def test_descartar(self):
        self.ops.save_draft({'rules': {'sla_hours': 6}})
        self.ops.discard_draft()
        self.assertIsNone(self.ops.rules()['draft'])

    def test_configuracoes_de_email_nao_perdem_as_regras(self):
        self.ops.save_draft({'rules': {'sla_hours': 6}})
        self.ops.publish_draft({'version': 1})
        config = self.ops.config()
        body = {k: config[k] for k in ('version', 'flow_types', 'routes', 'class_routes', 'signature', 'document_days', 'reminder_days')}
        body['flows'] = [{'classification': f['classification'], 'flow': f['flow']} for f in config['flows']]
        body['signature'] = 'Nova assinatura'
        self.assertEqual(self.ops.save_config(body)['sla_hours'], 6)


if __name__ == '__main__':
    unittest.main()
