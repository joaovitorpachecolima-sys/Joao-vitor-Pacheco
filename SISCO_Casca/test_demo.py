"""Modo demonstração (SISCO_DEMO=1): domínio próprio, HTTPS na frente, envios desligados e base reiniciada."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app as modulo
from app import Application, handler_for


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        pasta = Path(self.temp.name)
        self.patches = [patch.object(modulo, 'DEMO', True), patch.object(modulo, 'DEMO_HOSTS', {'sisco.exemplo.com'})]
        for p in self.patches:
            p.start()
        self.app = Application(pasta / 'entrada' / 'exemplo.xlsx', pasta / 'demo_inicial.sqlite3')
        self.app.entrada = pasta / 'entrada'
        self.app.reiniciar_demo()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app))
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def request(self, path, data=None, token=None, origin=None, host='sisco.exemplo.com', method=None, raw=False):
        headers = {'Content-Type': 'application/json', 'Host': host}
        if token:
            headers['X-SISCO-Token'] = token
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.url + path, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None,
                                     method=method or ('PATCH' if data is not None else 'GET'))
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                corpo = resp.read()
                return resp.status, corpo if raw else json.loads(corpo)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_dominio_configurado_e_https(self):
        code, state = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertEqual(state['settings']['mode'], 'demo')
        self.assertFalse(state['settings']['sending_enabled'])
        self.assertGreater(len(state['items']), 50)
        self.assertEqual(self.request('/api/state', host='outro.exemplo.com')[0], 403)
        self.assertEqual(self.request('/api/health', host='interno:8080')[0], 200)
        item = state['items'][0]
        code, salvo = self.request(f"/api/items/{item['id']}", {'version': item['version'], 'reviewed': True},
                                   state['csrf_token'], 'https://sisco.exemplo.com')
        self.assertEqual(code, 200)
        self.assertTrue(salvo['reviewed'])
        self.assertEqual(self.request(f"/api/items/{item['id']}", {'version': salvo['version']},
                                      state['csrf_token'], 'https://outro.exemplo.com')[0], 403)

    def test_envio_e_troca_de_planilha_desligados(self):
        token = self.request('/api/state')[1]['csrf_token']
        code, erro = self.request('/api/source/upload', {'nome': 'x.csv', 'conteudo': 'SUQK'}, token, method='POST')
        self.assertEqual(code, 403)
        self.assertIn('demonstração', erro['error'])
        self.assertEqual(self.request('/api/items/1/send', {'version': 1}, token, method='POST')[0], 403)

    def test_pagina_mostra_o_aviso(self):
        code, html = self.request('/', raw=True)
        self.assertEqual(code, 200)
        self.assertIn('class="demo-faixa"', html.decode('utf-8'))

    def test_reinicio_volta_ao_original(self):
        state = self.request('/api/state')[1]
        item = state['items'][0]
        self.request(f"/api/items/{item['id']}", {'version': item['version'], 'reviewed': not item['reviewed']}, state['csrf_token'])
        antiga = self.app.database
        self.app.reiniciar_demo()
        self.assertNotEqual(self.app.database, antiga)
        self.assertFalse(antiga.exists())
        depois = next(i for i in self.request('/api/state')[1]['items'] if i['id'] == item['id'])
        self.assertEqual(depois['reviewed'], item['reviewed'])


if __name__ == '__main__':
    unittest.main()
