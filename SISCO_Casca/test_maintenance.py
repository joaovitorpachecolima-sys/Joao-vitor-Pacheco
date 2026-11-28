import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from core import Store, Conflict, TEST_EMAIL
from maintenance import read_returns, register
from operations import Operations
from test_core import snapshot
from workflow import derive


def row(values):
    return {col: {'value': value, 'error': False, 'uncached': False} for col, value in values.items()}


def response(rid=1, occurrence='8', **values):
    return {'response_id': str(rid), 'occurrence_id': occurrence, 'errors': [],
            'values': {'maintenance_status': 'Em Aberto', 'forecast_date': '2026-11-01', **values}}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.data = snapshot()
        self.data['maintenance_returns'] = []
        self.store.ingest(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def review(self, **values):
        return self.store.update(1, {'version': self.store.get(1)['version'], 'reviewed': True, 'values': values})

    def sync(self, *responses):
        self.data['maintenance_returns'] = list(responses)
        self.store.ingest(self.data)
        return self.store.get(1)

    def manual(self, **values):
        return {'version': self.store.get(1)['version'], 'request_id': 'manual-test-123456789', 'values': values}

    def test_baseline_is_reference_even_when_reviewed(self):
        other = Store(Path(self.temp.name) / 'baseline.sqlite3')
        data = snapshot(); data['maintenance_returns'] = [response()]
        other.ingest(data)
        other.update(1, {'version': 1, 'reviewed': True})
        other.ingest(data)
        self.assertNotIn('maintenance_status', other.get(1)['values'])
        self.assertEqual(other.returns()[0]['status'], 'historical')

    def test_new_response_waits_review_then_applies_once(self):
        self.sync(response())
        self.assertNotIn('forecast_date', self.store.get(1)['values'])
        item = self.review()
        self.assertEqual(item['values']['forms_due_at'], '2026-11-02T09:00')
        self.assertEqual(item['maintenance_returns'][0]['status'], 'applied')
        self.sync(response())
        self.assertEqual(item['version'], self.store.get(1)['version'])

    def test_latest_response_wins_and_old_arrival_cannot_reopen(self):
        self.sync(response(2, maintenance_status='Finalizada', end_date='2026-10-12', end_time='09:00'), response(1))
        item = self.review()
        self.assertEqual(item['values']['maintenance_status'], 'Finalizada')
        self.assertEqual({r['status'] for r in item['maintenance_returns']}, {'applied', 'superseded'})
        self.sync(response(2, maintenance_status='Finalizada', end_date='2026-10-12', end_time='09:00'), response(1), response(3))
        self.assertEqual(self.store.get(1)['values']['maintenance_status'], 'Finalizada')
        self.assertEqual(next(r for r in self.store.returns() if r['response_id'] == 3)['status'], 'ignored')

    def test_invalid_date_and_chronology_are_atomic(self):
        self.review()
        before = self.store.get(1)
        for number, end in enumerate(('2026-02-31', '2026-10-08'), 1):
            self.sync(response(number, maintenance_status='Finalizada', end_date=end, end_time='09:00'))
            self.assertEqual(before['values'], self.store.get(1)['values'])
            self.assertEqual(before['version'], self.store.get(1)['version'])
        self.assertTrue(all(r['status'] == 'conflict' for r in self.store.returns()))

    def test_duplicate_or_missing_occurrence_does_not_link_by_forms_id(self):
        self.review()
        self.sync(response(1, occurrence='1'))  # This is the initial Forms ID, not occurrence 8.
        self.assertIsNone(self.store.returns()[0]['item_id'])
        self.data['issues'] = [{'kind': 'duplicate_legacy_id', 'legacy_id': '8'}]
        self.sync(response(1, occurrence='1'), response(2))
        self.assertTrue(all(r['item_id'] is None for r in self.store.returns()))
        self.assertNotIn('forecast_date', self.store.get(1)['values'])

    def test_local_correction_is_preserved_and_new_managed_forecast_can_advance(self):
        self.review()
        self.sync(response(1))
        self.sync(response(1), response(2, forecast_date='2026-11-03'))
        self.assertEqual(self.store.get(1)['values']['forecast_date'], '2026-11-03')
        self.review(forecast_date='2026-11-10')
        self.sync(response(1), response(2, forecast_date='2026-11-03'), response(3, forecast_date='2026-11-05'))
        self.assertEqual(self.store.get(1)['values']['forecast_date'], '2026-11-10')
        self.assertEqual(next(r for r in self.store.returns() if r['response_id'] == 3)['status'], 'conflict')

    def test_changed_response_is_not_reapplied(self):
        self.review(); self.sync(response())
        item = self.store.get(1)
        self.sync(response(forecast_date='2026-11-20'))
        self.assertEqual(item['values'], self.store.get(1)['values'])
        self.assertEqual(self.store.returns()[0]['status'], 'conflict')

    def test_pending_response_removed_from_source_cannot_apply_at_review(self):
        self.sync(response())
        self.sync()
        item = self.review()
        self.assertNotIn('maintenance_status', item['values'])
        self.assertEqual(self.store.returns()[0]['status'], 'conflict')

    def test_manual_resolution_preserves_changed_response_versions(self):
        self.review(); self.sync(response())
        changed = response(forecast_date='2026-11-20')
        self.sync(changed)
        register(self.store, 1, self.manual(maintenance_status='Em Aberto', forecast_date='2026-11-20'))
        self.sync(changed)
        self.assertEqual(next(r for r in self.store.returns() if r['response_id'])['status'], 'resolved')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM maintenance_return_versions').fetchone()[0], 2)

    def test_changed_occurrence_id_cannot_redirect_a_known_return(self):
        self.review(); self.sync(response())
        before = self.store.get(1)['values']
        self.sync(response(occurrence='9'))
        self.assertIsNone(self.store.returns()[0]['item_id'])
        self.assertEqual(self.store.returns()[0]['status'], 'conflict')
        self.assertEqual(self.store.get(1)['values'], before)

    def test_manual_return_review_version_and_idempotency(self):
        body = self.manual(maintenance_status='Finalizada')
        with self.assertRaisesRegex(ValueError, 'Revise'):
            register(self.store, 1, body)
        self.review()
        with self.assertRaises(Conflict):
            register(self.store, 1, body)
        body['version'] = self.store.get(1)['version']
        result = register(self.store, 1, body)
        repeat = register(self.store, 1, body)
        self.assertEqual(result['version'], repeat['version'])
        self.assertEqual(result['values']['end_date'], '')
        self.assertEqual(len(result['maintenance_returns']), 1)
        with self.assertRaises(Conflict):
            register(self.store, 1, {**body, 'values': {'maintenance_status': 'Em Aberto'}})

    def test_return_headers_types_duplicate_ids_and_raw_evidence(self):
        headers = row(dict(zip('AFJKLMNO', ['Id', 'ID da ocorrência', 'Data de início da ocorrência', 'Hora de início da ocorrência', 'Status da Ocorrência', 'Data prevista para finalização', 'Data Fim', 'Hora Fim\xa0'])))
        rows = {1: headers, 2: row({'A': 7, 'F': '0008.0', 'L': 'Finalizada\xa0', 'N': '31/02/2026', 'O': '09h00'})}
        entries, issues = read_returns(rows)
        self.assertFalse(issues)
        self.assertEqual(entries[0]['occurrence_id'], '8')
        self.assertTrue(entries[0]['errors'])
        self.assertIn('N · Data Fim', entries[0]['raw_fields'])
        rows[3] = copy.deepcopy(rows[2])
        self.assertIn('duplicado', ' '.join(read_returns(rows)[0][0]['errors']))
        rows[1]['F']['value'] = 'Id inicial do Forms'
        self.assertIsNone(read_returns(rows)[0])

    def test_complete_synthetic_cycle_has_no_real_sender(self):
        self.data['records'][0]['source_values'].update(plant='Usina fictícia', equipment='UG teste', coordination='Coordenação Norte')
        self.store.ingest(self.data)
        self.review()
        template = Path(self.temp.name) / 'template.docx'
        with ZipFile(template, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>')
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{{USINA}} {{ID_OCORRENCIA}}</w:t></w:r></w:p></w:body></w:document>')
        ops = Operations(self.store, template)
        submissions = []
        with patch('operations.now', return_value='2026-10-22T10:00:00-03:00'):
            preview = ops.preview(1, {'version': self.store.get(1)['version'], 'kind': 'request'}, 8767)
            ops.send_test(1, preview, 8767, sender=submissions.append)
            self.sync(response(1))
            self.assertEqual(ops.enrich(self.store.get(1))['workflow']['conclusion'], 'Aguardando prazo informado')
            self.sync(response(1), response(2, maintenance_status='Finalizada', end_date='2026-10-21', end_time='10:00'))
            self.assertEqual(ops.enrich(self.store.get(1))['workflow']['conclusion'], 'Aguardando geração do registro')
            document = ops.create_document(1, {'version': self.store.get(1)['version']})
            with ZipFile(document['document']['path']) as z:
                self.assertIn('Usina fictícia', z.read('word/document.xml').decode())
            preview = ops.preview(1, {'version': self.store.get(1)['version'], 'kind': 'document'}, 8767)
            ops.send_test(1, preview, 8767, sender=submissions.append)
            self.assertEqual(ops.enrich(self.store.get(1))['workflow']['conclusion'], 'Aguardando retorno do registro')
            final = self.review(document_return_date='2026-10-22')
            self.assertEqual(ops.enrich(final)['workflow']['conclusion'], 'Encerrada')
        self.assertEqual(len(submissions), 2)
        self.assertTrue(all(p['to'] == TEST_EMAIL and not p['cc'] and not p['bcc'] for p in submissions))
        self.assertTrue(all(p['subject'].startswith('[TESTE SISCO]') for p in submissions))


if __name__ == '__main__':
    unittest.main()
