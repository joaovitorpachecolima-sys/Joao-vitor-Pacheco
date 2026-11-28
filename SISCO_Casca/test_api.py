import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from app import Application, handler_for
from test_core import snapshot


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.app = Application('unread-test-source.xlsm', Path(self.temp.name) / 'api.sqlite3')
        self.app.store.ingest(snapshot())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app))
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, token=None, origin=None, host=None, method=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-SISCO-Token'] = token
        if origin:
            headers['Origin'] = origin
        if host:
            headers['Host'] = host
        req = urllib.request.Request(self.url + path, headers=headers,
              data=json.dumps(data).encode() if data is not None else None,
              method=method or ('PATCH' if data is not None else 'GET'))
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_local_review_persistence_and_stale_write(self):
        code, state = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertTrue(state['settings']['sending_enabled'])
        self.assertTrue(state['settings']['manual_only'])
        code, item = self.request('/api/items/1', {'version': 1, 'reviewed': True}, state['csrf_token'])
        self.assertEqual(code, 200)
        self.assertTrue(item['reviewed'])
        code, err = self.request('/api/items/1', {'version': 1, 'reviewed': False}, state['csrf_token'])
        self.assertEqual(code, 409)

    def test_reject_external_origin_missing_token_and_host(self):
        self.assertEqual(self.request('/api/items/1', {'version': 1, 'reviewed': True})[0], 403)
        self.assertEqual(self.request('/api/items/1', {'version': 1}, self.app.token, 'https://elsewhere.example')[0], 403)
        self.assertEqual(self.request('/api/state', host='elsewhere.example')[0], 403)
        self.assertFalse(self.app.store.get(1)['reviewed'])

    def test_database_files_not_exposed(self):
        self.assertEqual(self.request('/dados/sisco_teste.sqlite3')[0], 404)
        self.assertEqual(self.request('/../core.py')[0], 404)
        self.assertEqual(self.request('/static/app.py')[0], 404)

    def test_marca_servida_de_static_marca(self):
        for name in ('simbolo.svg', 'logo-branco.svg'):
            with urllib.request.urlopen(f'{self.url}/marca/{name}', timeout=5) as resp:
                self.assertEqual(resp.headers['Content-Type'], 'image/svg+xml')
                self.assertEqual(resp.read(4), b'<svg')
        for path in ('/marca/../app.py', '/marca/nao-existe.svg', '/logo-antigo.png'):
            self.assertEqual(self.request(path)[0], 404)

    def test_troca_de_planilha_exige_token_e_arquivo_valido(self):
        corpo = {'nome': 'dados.xlsx', 'conteudo': 'AAAA'}
        self.assertEqual(self.request('/api/source/upload', corpo, method='POST')[0], 403)
        self.assertEqual(self.request('/api/source/upload', {'nome': 'dados.exe', 'conteudo': 'AAAA'}, self.app.token, method='POST')[0], 400)
        self.assertEqual(self.request('/api/source/upload', corpo, self.app.token, method='POST')[0], 400)

    def test_manual_return_endpoint_requires_review_and_records_once(self):
        path = '/api/items/1/maintenance-return'
        body = {'version': 1, 'request_id': 'api-return-test-123456', 'values': {'maintenance_status': 'Finalizada', 'end_date': '2026-10-12', 'end_time': '10:00'}}
        self.assertEqual(self.request(path, body, method='POST')[0], 403)
        self.assertEqual(self.request(path, body, self.app.token, method='POST')[0], 400)
        _, reviewed = self.request('/api/items/1', {'version': 1, 'reviewed': True}, self.app.token)
        body['version'] = reviewed['version']
        code, item = self.request(path, body, self.app.token, method='POST')
        self.assertEqual(code, 200)
        self.assertEqual(item['workflow']['conclusion'], 'Aguardando geração do registro')
        self.assertEqual(item['deliveries'], [])
        self.assertEqual(self.request(path, body, self.app.token, method='POST')[1]['version'], item['version'])

    def test_powerbi_link_accepts_only_embed_links(self):
        link = 'https://app.powerbi.com/reportEmbed?reportId=abc&autoAuth=true&ctid=xyz'
        self.assertEqual(self.request('/api/analytics', {'version': 0, 'powerbi_url': link})[0], 403)
        for bad in ('http://app.powerbi.com/reportEmbed?reportId=abc', 'https://app.powerbi.com.evil.example/reportEmbed?x=1',
                    'https://app.powerbi.com/groups/me/reports/abc', 'javascript:alert(1)'):
            self.assertEqual(self.request('/api/analytics', {'version': 0, 'powerbi_url': bad}, self.app.token)[0], 400)
        code, saved = self.request('/api/analytics', {'version': 0, 'powerbi_url': link}, self.app.token)
        self.assertEqual((code, saved['powerbi_url'], saved['version']), (200, link, 1))
        self.assertEqual(self.request('/api/analytics', {'version': 0, 'powerbi_url': ''}, self.app.token)[0], 409)
        self.assertEqual(self.request('/api/state')[1]['analytics']['powerbi_url'], link)
        code, cleared = self.request('/api/analytics', {'version': 1, 'powerbi_url': ''}, self.app.token)
        self.assertEqual((code, cleared['powerbi_url']), (200, ''))
        # Link dos gráficos: mesma validação, salvo sem apagar o link do painel.
        self.request('/api/analytics', {'version': 2, 'powerbi_url': link}, self.app.token)
        self.assertEqual(self.request('/api/analytics', {'version': 3, 'charts_url': 'https://evil.example/x?y=1'}, self.app.token)[0], 400)
        charts = 'https://app.powerbi.com/view?r=abc'
        code, saved = self.request('/api/analytics', {'version': 3, 'charts_url': charts}, self.app.token)
        self.assertEqual((code, saved['charts_url'], saved['powerbi_url']), (200, charts, link))
        self.assertEqual(self.request('/api/analytics', {'version': 4}, self.app.token)[0], 400)
        self.assertEqual(self.request('/api/analytics', {'version': 4, 'outro': 'x'}, self.app.token)[0], 400)


if __name__ == '__main__':
    unittest.main()
