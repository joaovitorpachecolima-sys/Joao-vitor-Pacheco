"""Tempo na fase (estilo Pipefy): estimativa inicial e registro das mudanças de fase."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
from core import Store
from workflow import phase_since_estimate


class Estimativa(unittest.TestCase):
    base = {'start_date': '2026-11-01', 'start_time': '22:00'}

    def test_cada_fase_usa_a_data_que_a_marca(self):
        self.assertEqual(phase_since_estimate(self.base, 'review'), '2026-11-01T22:00')
        self.assertEqual(phase_since_estimate({**self.base, 'forms_sent_at': '2026-11-03T09:23'}, 'forms'), '2026-11-03T09:23')
        self.assertEqual(phase_since_estimate({**self.base, 'document_sent': 'Email enviado (10/09/2026 12:08)'}, 'documents'), '2026-09-10T12:08')
        self.assertEqual(phase_since_estimate({**self.base, 'end_date': '2026-11-02', 'end_time': '08:00'}, 'documents'), '2026-11-02T08:00')
        self.assertEqual(phase_since_estimate({**self.base, 'end_date': '2026-11-02', 'end_time': '08:00', 'document_return_date': '2026-11-05'}, 'closed'), '2026-11-05T00:00')

    def test_sem_dado_cai_no_inicio_e_sem_inicio_fica_vazio(self):
        self.assertEqual(phase_since_estimate(self.base, 'forms'), '2026-11-01T22:00')
        self.assertEqual(phase_since_estimate({}, 'review'), '')
        self.assertEqual(phase_since_estimate({'start_date': '2026-11-01'}, 'monitoring'), '2026-11-01T00:00')


class Registro(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'fases.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_primeira_vez_estima_depois_conta_da_mudanca(self):
        with patch.object(core, 'now', return_value='2026-11-10T09:00:00-03:00'):
            primeira = self.store.track_phases({1: 'review', 2: 'forms'}, {1: '2026-11-01T22:00', 2: ''})
        self.assertEqual(primeira, {1: ('2026-11-01T22:00', True), 2: ('2026-11-10T09:00', True)})

        with patch.object(core, 'now', return_value='2026-11-10T10:00:00-03:00'):
            igual = self.store.track_phases({1: 'review', 2: 'forms'}, {1: '2026-01-01T00:00', 2: ''})
        self.assertEqual(igual[1], ('2026-11-01T22:00', True))   # mesma fase: não muda nem reestima

        with patch.object(core, 'now', return_value='2026-11-10T11:30:00-03:00'):
            mudou = self.store.track_phases({1: 'forms'}, {1: '2026-01-01T00:00'})
        self.assertEqual(mudou[1], ('2026-11-10T11:30', False))  # mudou: conta de agora, não é estimativa


if __name__ == '__main__':
    unittest.main()
