import copy
import tempfile
import unittest
from pathlib import Path
from core import Store, Conflict


def snapshot():
    return {'source_path': 'somente-leitura.xlsm', 'sha256': 'abc', 'saved_at': '2026-10-09',
            'records': [{'source_key': 'forms:1', 'forms_id': '1', 'legacy_id': '8', 'source_row': 5,
                         'source_review': 'Revisado', 'source_values': {'description': 'Relato',
                         'classification': 'Manutenção', 'start_date': '2026-10-09', 'start_time': '08:00',
                         'end_date': '', 'end_time': '', 'status': 'Aberta'},
                         'raw_report': 'Relato original', 'raw_fields': {}, 'warnings': []}],
            'issues': [], 'stats': {}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_repeated_import_and_reorder_do_not_duplicate_or_review(self):
        first = snapshot()
        self.store.ingest(first)
        self.store.ingest(first)
        first['records'][0]['source_row'] = 500
        first['records'][0]['forms_row'] = 22
        first['records'][0]['control_row'] = 500
        self.store.ingest(first)
        items = self.store.list_items()
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]['reviewed'])
        self.assertFalse(items[0]['source_changed'])
        self.assertEqual(items[0]['version'], 1)

    def test_source_changes_preserve_local_edits_and_review(self):
        data = snapshot()
        self.store.ingest(data)
        item = self.store.list_items()[0]
        item = self.store.update(item['id'], {'version': item['version'],
                    'values': {'description': 'Correção de Pessoa A'}, 'reviewed': True})
        data['records'][0]['source_values']['description'] = 'Outra edição no Excel'
        data['records'][0]['source_values']['equipment'] = 'UG 2'
        self.store.ingest(data)
        result = self.store.get(item['id'])
        self.assertEqual(result['values']['description'], 'Correção de Pessoa A')
        self.assertEqual(result['values']['equipment'], 'UG 2')
        self.assertTrue(result['reviewed'])
        self.assertTrue(result['source_changed'])
        with self.assertRaises(Conflict):
            self.store.update(item['id'], {'version': item['version'], 'values': {'description': 'antigo'}})

    def test_ambiguous_import_is_atomic(self):
        data = snapshot()
        self.store.ingest(data)
        duplicate = copy.deepcopy(data['records'][0])
        duplicate['legacy_id'] = '9'
        data['records'].append(duplicate)
        with self.assertRaises(ValueError):
            self.store.ingest(data)
        self.assertEqual(len(self.store.list_items()), 1)
        self.assertEqual(self.store.list_items()[0]['version'], 1)

    def test_incomplete_open_record_can_be_reviewed(self):
        self.store.ingest(snapshot())
        result = self.store.update(1, {'version': 1, 'reviewed': True})
        self.assertTrue(result['reviewed'])
        self.assertEqual(result['values']['end_date'], '')

    def test_invalid_date_and_chronology_rejected_atomically(self):
        self.store.ingest(snapshot())
        for fields in ({'end_date': '2026-02-30'}, {'end_time': '25:00'},
                       {'end_date': '2026-10-08', 'end_time': '12:00'}):
            with self.assertRaises(ValueError):
                self.store.update(1, {'version': 1, 'values': fields, 'reviewed': True})
        self.assertEqual(self.store.get(1)['version'], 1)
        self.assertFalse(self.store.get(1)['reviewed'])

    def test_missing_source_record_is_kept(self):
        data = snapshot()
        self.store.ingest(data)
        data['records'] = []
        self.store.ingest(data)
        self.assertFalse(self.store.get(1)['source_present'])
        self.assertTrue(self.store.get(1)['warnings'])

    def test_persistence_and_no_arbitrary_fields(self):
        self.store.ingest(snapshot())
        with self.assertRaises(ValueError):
            self.store.update(1, {'version': 1, 'values': {'email': 'qualquer@exemplo.com'}})
        result = Store(self.store.path).get(1)
        self.assertEqual(result['source_key'], 'forms:1')

    def test_generation_and_maintenance_type_preserved_without_authorizing_actions(self):
        data = snapshot()
        self.store.ingest(data)
        result = self.store.update(1, {'version': 1, 'values': {
            'generation_impact': 'Sim', 'maintenance_type': 'Emergencial'}})
        self.assertFalse(result['reviewed'])
        data['records'][0]['source_values']['equipment'] = 'UG 2'
        self.store.ingest(data)
        result = Store(self.store.path).get(1)
        self.assertEqual(result['values']['generation_impact'], 'Sim')
        self.assertEqual(result['values']['maintenance_type'], 'Emergencial')
        self.assertFalse(result['reviewed'])
        with self.assertRaises(ValueError):
            self.store.update(1, {'version': result['version'], 'values': {'maintenance_status': 'Em Aberto'}})

    def test_invalid_classifications_are_rejected_atomically(self):
        self.store.ingest(snapshot())
        for fields in ({'generation_impact': 'Talvez'}, {'maintenance_type': 'Outro'},
                       {'classification': 'Operacional', 'maintenance_type': 'Corretiva'}):
            with self.assertRaises(ValueError):
                self.store.update(1, {'version': 1, 'values': fields, 'reviewed': True})
            self.assertEqual(self.store.get(1)['version'], 1)
            self.assertFalse(self.store.get(1)['reviewed'])

    def test_leaving_maintenance_clears_subtype_but_preserves_generation(self):
        self.store.ingest(snapshot())
        item = self.store.update(1, {'version': 1, 'values': {
            'generation_impact': 'Não', 'maintenance_type': 'Preventiva'}})
        item = self.store.update(1, {'version': item['version'], 'values': {'classification': 'Informativo'}})
        self.assertEqual(item['values']['maintenance_type'], '')
        self.assertEqual(item['values']['generation_impact'], 'Não')
        item = self.store.update(1, {'version': item['version'], 'values': {'generation_impact': ''}, 'reviewed': True})
        self.assertTrue(item['reviewed'])
        self.assertEqual(item['values']['generation_impact'], '')


if __name__ == '__main__':
    unittest.main()
