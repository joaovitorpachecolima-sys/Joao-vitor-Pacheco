"""Etapa 1 do editor BPMN: o fluxo como dado precisa se comportar exatamente como o motor atual."""
import copy
import random
import unittest
from datetime import datetime, timedelta

from fluxo import CONDICOES, PRAZOS, caminho, executar, fluxo_padrao, validar
from workflow import derive_codigo as derive

CONFIG = {'sla_hours': 24, 'followup_hour': 9, 'reminder_days': 7, 'document_days': 7,
          'flows': [{'classification': 'Manutenção', 'flow': 'COMPLETO', 'followup': True, 'coordinator': True},
                    {'classification': 'Distribuidora', 'flow': 'SEM_COORD', 'followup': True, 'coordinator': False},
                    {'classification': 'Informativo', 'flow': 'INFORMATIVO', 'followup': False, 'coordinator': False}]}
CHAVES = ('sla', 'hours', 'can_act', 'due_at', 'category', 'action', 'action_kind', 'calculated_conclusion', 'conclusion')
CLOCK = datetime(2026, 11, 13, 10, 0)


def sorteio(rng):
    """Uma ocorrência com valores sorteados entre os que mudam o caminho no motor."""
    dia = lambda delta: (CLOCK + timedelta(days=delta)).strftime('%Y-%m-%d')
    hora = lambda: rng.choice(['', '00:00', '08:30', '13:45', '23:59', '25:00'])
    start_delta = rng.choice([-90, -30, -10, -3, -2, -1, 0, 1])
    values = {
        'classification': rng.choice(['Manutenção', 'Distribuidora', 'Informativo', 'Civil', '']),
        'start_date': rng.choice([dia(start_delta), dia(start_delta), '']), 'start_time': hora(),
        'end_date': rng.choice(['', '', dia(start_delta), dia(start_delta + 1), dia(start_delta + 5)]), 'end_time': hora(),
        'maintenance_status': rng.choice(['', 'Finalizada', 'Em Aberto', 'aberta', 'Encerrado', '46107', '-']),
        'forecast_date': rng.choice(['', dia(-5), dia(-1), dia(0), dia(3)]),
        'forms_sent': rng.choice(['', 'Enviado', 'Pessoa B', 'Em fila', '-']),
        'forms_sent_at': rng.choice(['', (CLOCK - timedelta(days=rng.choice([1, 8, 20]))).strftime('%Y-%m-%dT%H:%M')]),
        'forms_due_at': rng.choice(['', (CLOCK + timedelta(days=rng.choice([-3, 0, 2]))).strftime('%Y-%m-%dT09:00')]),
        'document_sent': rng.choice(['', 'Enviar Email', 'Não se aplica', 'Email enviado (01/11/2026 10:00)', 'Email enviado (06/11/2026 16:05)']),
        'document_return_date': rng.choice(['', '', dia(-2)]),
        'document_deadline_date': rng.choice(['', dia(-4), dia(0), dia(5), '2026-11-08', '2026-11-13']),
        'document_next_date': rng.choice(['', dia(-1), dia(0), dia(2)]),
        'word_status': rng.choice(['', 'Adiantar Word', 'Word gerado (05/11/2026 09:00)', 'Não se aplica']),
    }
    return {'values': values, 'reviewed': rng.random() < 0.8}


class Equivalencia(unittest.TestCase):
    def test_fluxo_padrao_e_valido(self):
        self.assertEqual(validar(fluxo_padrao()), [])

    def test_mesmo_resultado_do_motor_em_milhares_de_casos(self):
        rng, fluxo, vistos = random.Random(20261113), fluxo_padrao(), set()
        for caso in range(30000):
            item = sorteio(rng)
            config = {**CONFIG, 'sla_hours': rng.choice([12, 24, 48]), 'followup_hour': rng.choice([9, 14])}
            clock = CLOCK + timedelta(hours=rng.choice([0, 5, 30]))
            esperado, obtido = derive(copy.deepcopy(item), config, clock), executar(fluxo, item, config, clock)
            for chave in CHAVES:
                self.assertEqual(obtido[chave], esperado[chave], f'caso {caso}, campo {chave}: {item}')
            vistos.add(obtido['path'][-1])
        # Todas as situações do fluxo foram alcançadas pelo sorteio: nenhuma ficou sem teste.
        situacoes = {k for k, el in fluxo['elementos'].items() if el['tipo'] == 'situacao'}
        self.assertEqual(situacoes - vistos, set())

    def test_caminho_registra_as_decisoes(self):
        item = {'values': {'classification': 'Informativo', 'start_date': '2026-11-12', 'start_time': '08:00'}, 'reviewed': True}
        passos, _ = caminho(fluxo_padrao(), item, CONFIG, CLOCK)
        self.assertEqual(passos, ['q_classificacao', 'q_revisada', 'q_acompanha', 's_encerrada'])


class Validacao(unittest.TestCase):
    def test_recusa_o_que_nao_executa(self):
        fluxo = fluxo_padrao()
        quebrado = copy.deepcopy(fluxo); quebrado['elementos']['q_word']['sim'] = 'nao_existe'
        self.assertIn('"Word gerado?": o caminho "Sim" não leva a nenhum elemento.', validar(quebrado))
        quebrado = copy.deepcopy(fluxo); quebrado['elementos']['q_word']['condicao'] = 'chuta'
        self.assertIn('"Word gerado?": a decisão não tem uma pergunta válida.', validar(quebrado))
        quebrado = copy.deepcopy(fluxo); quebrado['elementos']['s_encerrada']['fase'] = 'limbo'
        self.assertIn('"Encerrada": a fase não existe.', validar(quebrado))
        quebrado = copy.deepcopy(fluxo); quebrado['elementos']['s_forms_fila']['prazo'] = 'amanha'
        self.assertIn('"Forms em fila": o prazo não existe.', validar(quebrado))
        quebrado = copy.deepcopy(fluxo); quebrado['elementos']['q_word']['nao'] = 'q_registro_enviado'
        self.assertTrue(any(p.startswith('Laço') for p in validar(quebrado)), validar(quebrado))
        quebrado = copy.deepcopy(fluxo); quebrado['elementos']['s_sobra'] = {'tipo': 'situacao', 'nome': 'Sobra', 'conclusao': 'X', 'fase': 'forms', 'acao': 'Y', 'tipo_acao': 'details', 'prazo': ''}
        self.assertIn('"Sobra" está solto: nenhum caminho chega até ele.', validar(quebrado))
        self.assertEqual(validar({'elementos': {}}), ['O fluxo não tem elementos.'])

    def test_catalogos_tem_nome_para_a_tela(self):
        for catalogo in (CONDICOES, PRAZOS):
            for chave, (nome, funcao) in catalogo.items():
                self.assertTrue(nome and callable(funcao), chave)


class Subprocessos(unittest.TestCase):
    """BPMN, etapa 1: cada elemento pertence a um subprocesso ou ao nível de cima; o motor não muda."""

    def test_rotulos_antigos_mantem_destinos_e_personalizacoes(self):
        from fluxo import atualizar_fluxo_salvo
        padrao = fluxo_padrao()
        antigo = copy.deepcopy(padrao)
        anteriores = {'q_classificacao': 'Classificada?', 'q_revisada': 'Revisada?',
                      'q_acompanha': 'Acompanha o retorno?', 'q_exige_registro': 'Exige registro Word?'}
        for chave, nome in anteriores.items():
            antigo['elementos'][chave]['nome'] = nome
            self.assertNotEqual(padrao['elementos'][chave]['nome'], nome)
        self.assertEqual(atualizar_fluxo_salvo(copy.deepcopy(antigo)), padrao)
        antigo['elementos']['q_acompanha']['nome'] = 'Pergunta da equipe'
        antigo['elementos']['q_exige_registro']['condicao'] = 'registro_enviado'
        antigo['elementos']['q_revisada']['sim'] = 's_encerrada'
        novo = atualizar_fluxo_salvo(antigo)['elementos']
        self.assertEqual(novo['q_acompanha']['nome'], 'Pergunta da equipe')
        self.assertEqual(novo['q_exige_registro']['nome'], 'Exige registro Word?')
        self.assertEqual(novo['q_revisada']['sim'], 's_encerrada')

    def test_padrao_organizado_em_quatro_subprocessos(self):
        fluxo = fluxo_padrao()
        self.assertEqual([s['nome'] for s in fluxo['subprocessos']], ['Triagem', 'Monitoramento 24h', 'Retorno da manutenção', 'Registro da ocorrência'])
        topo = sorted(k for k, el in fluxo['elementos'].items() if el['subprocesso'] == '')
        self.assertEqual(topo, ['q_acompanha', 'q_exige_registro', 's_encerrada'])
        contagem = {s['id']: sum(1 for el in fluxo['elementos'].values() if el['subprocesso'] == s['id']) for s in fluxo['subprocessos']}
        self.assertEqual(contagem, {'triagem': 4, 'monitoramento': 5, 'retorno': 17, 'registro': 12})

    def test_validador_exige_subprocessos_coerentes(self):
        fluxo = fluxo_padrao()
        errado = copy.deepcopy(fluxo); errado['elementos']['q_word']['subprocesso'] = 'nenhum'
        self.assertIn('"Word gerado?": o subprocesso não existe.', validar(errado))
        errado = copy.deepcopy(fluxo); errado['subprocessos'].append({'id': 'triagem', 'nome': 'Outra'})
        self.assertIn('Subprocesso repetido: "Outra".', validar(errado))
        errado = copy.deepcopy(fluxo); errado['subprocessos'].append({'id': 'vazio', 'nome': 'Sem nada'})
        self.assertIn('O subprocesso "Sem nada" está vazio.', validar(errado))
        errado = copy.deepcopy(fluxo); errado['subprocessos'][1]['nome'] = 'triagem'
        self.assertIn('Dois subprocessos têm o mesmo nome.', validar(errado))
        errado = copy.deepcopy(fluxo); del errado['subprocessos']
        self.assertIn('O fluxo não tem a lista de subprocessos.', validar(errado))

    def test_fluxo_de_antes_ganha_a_organizacao_padrao(self):
        from fluxo import com_subprocessos
        antigo = fluxo_padrao(); del antigo['subprocessos']
        for el in antigo['elementos'].values():
            del el['subprocesso']
        antigo['elementos']['s_nova_1'] = {'tipo': 'situacao', 'nome': 'X', 'conclusao': 'X', 'fase': 'forms', 'acao': 'Y', 'tipo_acao': 'details', 'prazo': ''}
        novo = com_subprocessos(antigo)
        self.assertEqual(novo['elementos']['s_nova_1']['subprocesso'], '')
        del novo['elementos']['s_nova_1']; novo['versao_formato'] = 2
        self.assertEqual(novo, fluxo_padrao())
        self.assertIs(com_subprocessos(fluxo_padrao()) is not None, True)


class FluxoGuardado(unittest.TestCase):
    """Etapa 2: o motor percorre o fluxo publicado no banco, com rascunho, versão e histórico."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from core import Store
        from operations import Operations
        from test_core import snapshot
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.temp.name) / 'fluxo.sqlite3')
        self.store.ingest(snapshot())
        self.ops = Operations(self.store, Path(self.temp.name) / 'sem_modelo.docx')

    def tearDown(self):
        self.temp.cleanup()

    def informativa(self):
        item = self.store.list_items()[0]
        item['values'].update(classification='Informativo', start_date='2026-11-13', start_time='08:00', end_date='', end_time='')  # 2 h antes do CLOCK
        item['reviewed'] = True
        return item

    def test_sem_nada_salvo_vale_o_fluxo_padrao_e_o_motor_o_percorre(self):
        from workflow import describe
        config = self.ops.config()
        self.assertEqual((self.ops.flow()['version'], config['fluxo']), (0, fluxo_padrao()))
        resultado = describe(self.informativa(), config, CLOCK)
        self.assertEqual((resultado['conclusion'], resultado['path'][-1]), ('Encerrada', 's_encerrada'))

    def test_rascunho_simula_e_so_vale_depois_de_publicar(self):
        from core import Conflict
        from workflow import describe
        fluxo = fluxo_padrao()
        # Mudança de comportamento: o tipo sem acompanhamento passa a ir para o monitoramento.
        fluxo['elementos']['q_acompanha']['sim'] = 'q_no_limite'
        resposta = self.ops.save_flow_draft({'flow': fluxo})
        self.assertEqual(resposta['draft']['base_version'], 0)
        self.assertIn('changed_total', resposta['simulation'])
        self.assertEqual(describe(self.informativa(), self.ops.config(), CLOCK)['conclusion'], 'Encerrada')   # rascunho não vale
        with self.assertRaises(Conflict):
            self.ops.publish_flow({'version': 5})
        self.ops.publish_flow({'version': 0, 'note': 'Informativo monitorado'})
        self.assertEqual(self.ops.flow()['version'], 1)
        self.assertEqual(describe(self.informativa(), self.ops.config(), CLOCK)['conclusion'], 'Em monitoramento 24h')
        estado = self.ops.flow_state()
        self.assertEqual((estado['draft'], [v['version'] for v in estado['versions']]), (None, [0]))
        self.ops.restore_flow({'version': 0})                                       # a versão 0 volta como rascunho
        self.assertEqual(self.ops.flow_state()['draft']['flow'], fluxo_padrao())
        self.ops.discard_flow_draft()
        self.assertIsNone(self.ops.flow_state()['draft'])

    def test_fluxo_invalido_nao_entra_e_o_salvo_corrompido_cai_no_padrao(self):
        with self.assertRaises(ValueError):
            self.ops.publish_flow({'version': 0})                                   # não há rascunho
        fluxo = fluxo_padrao(); fluxo['elementos']['q_word']['sim'] = 'nao_existe'
        resposta = self.ops.save_flow_draft({'flow': fluxo})                        # rascunho incompleto é guardado...
        self.assertEqual((resposta['simulation'], len(resposta['draft']['problems'])), (None, 1))
        with self.assertRaises(ValueError):
            self.ops.publish_flow({'version': 0})                                   # ...mas não é publicado
        self.assertEqual(self.ops.flow()['version'], 0)
        for errado in ({'flow': 'x'}, {'flow': {'elementos': []}}, {'flow': {'elementos': {'': {}}}}, {'flow': {'elementos': {}, 'layout': []}}):
            with self.assertRaises(ValueError):
                self.ops.save_flow_draft(errado)
        with self.store.lock, self.store.connect() as db:                           # banco adulterado por fora
            self.store._meta(db, 'flow_published', {'version': 3, 'flow': fluxo})
        salvo = self.ops.flow()
        self.assertEqual((salvo['version'], salvo['fallback'], salvo['flow']), (0, True, fluxo_padrao()))
        antigo = fluxo_padrao(); del antigo['subprocessos']                          # publicado antes dos subprocessos
        for el in antigo['elementos'].values():
            del el['subprocesso']
        with self.store.lock, self.store.connect() as db:
            self.store._meta(db, 'flow_published', {'version': 4, 'flow': antigo})
        salvo = self.ops.flow()
        self.assertEqual((salvo['version'], salvo.get('fallback', False), len(salvo['flow']['subprocessos'])), (4, False, 4))

    def test_simulacao_conta_o_que_muda(self):
        from workflow import describe
        for item in self.store.list_items():
            self.store.update(item['id'], {'version': item['version'], 'reviewed': True, 'values': {'classification': 'Informativo'}})
        fluxo = fluxo_padrao(); fluxo['elementos']['s_encerrada']['fase'] = 'documents'
        simulacao = self.ops.simulate_flow(fluxo)
        abertas = sum(1 for item in self.store.list_items() if describe(item, self.ops.config())['phase'] == 'closed')
        self.assertEqual(simulacao['changed_total'], abertas)
        self.assertGreater(simulacao['changed_total'], 0)


class PecasAutomaticas(FluxoGuardado):
    """Etapa 4: peças que executam. Só "Gerar o Word" pode ser automática; envios são sempre manuais."""

    def modelo(self):
        from pathlib import Path
        from zipfile import ZipFile
        caminho = Path(self.temp.name) / 'modelo.docx'
        with ZipFile(caminho, 'w') as docx:
            docx.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            docx.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{{USINA}}</w:t></w:r></w:p></w:body></w:document>')
        return caminho

    def preparar(self, revisada=True):
        from test_core import snapshot
        dados = snapshot(); dados['records'][0]['source_values'].update(plant='Usina A')   # a usina vem da planilha
        self.store.ingest(dados)
        item = self.store.list_items()[0]
        self.store.update(item['id'], {'version': item['version'], 'reviewed': revisada, 'values': {
            'classification': 'Manutenção', 'equipment': 'GG01', 'start_date': '2026-11-01', 'start_time': '08:00',
            'end_date': '2026-11-03', 'end_time': '08:00', 'document_return_date': ''}})
        atual = self.store.get(item['id'])['values']
        self.assertTrue(atual.get('plant'))                                         # usina vem da origem (o Word exige)
        self.assertNotIn('enviado', atual.get('document_sent', '').lower())
        fluxo = fluxo_padrao(); fluxo['elementos']['s_aguardando_geracao_registro']['automatico'] = True
        self.ops.save_flow_draft({'flow': fluxo}); self.ops.publish_flow({'version': 0, 'note': 'Word automático'})
        return item['id']

    def test_envio_nunca_pode_ser_automatico(self):
        fluxo = fluxo_padrao(); fluxo['elementos']['s_resposta_cobrar']['automatico'] = True
        self.assertIn('"Aguardando resposta do Forms": só "Gerar o Word do registro" pode ser automática; envios de e-mail são sempre manuais.', validar(fluxo))
        fluxo = fluxo_padrao(); fluxo['elementos']['s_aguardando_geracao_registro']['automatico'] = 'sim'
        self.assertIn('"Aguardando geração do registro": a opção automática é inválida.', validar(fluxo))

    def test_word_automatico_so_na_revisada_e_uma_vez(self):
        self.ops.template = self.modelo()
        item_id = self.preparar()
        from operations import parse, now
        primeira = self.ops.run_automations(CLOCK)
        self.assertEqual([d['id'] for d in primeira['done']], [item_id])
        self.assertEqual(len(self.ops.documents(item_id)), 1)
        self.assertEqual(self.ops.run_automations(CLOCK)['done'], [])               # já tem documento: não repete
        self.assertEqual(self.ops.flow_state()['automation']['done'], [])

    def test_nao_revisada_nao_executa_e_sem_modelo_fica_bloqueada(self):
        item_id = self.preparar(revisada=False)
        self.assertEqual(self.ops.run_automations(CLOCK), {**self.ops.last_automation, 'done': [], 'blocked': []})
        item = self.store.get(item_id)
        self.store.update(item_id, {'version': item['version'], 'reviewed': True})
        bloqueada = self.ops.run_automations(CLOCK)                                 # o teste não tem modelo Word
        self.assertEqual((bloqueada['done'], [b['reason'] for b in bloqueada['blocked']]), ([], ['Modelo Word não localizado.']))
        self.assertEqual(self.ops.documents(item_id), [])

    def test_fluxo_padrao_nao_executa_nada(self):
        self.ops.template = self.modelo()
        self.assertEqual(self.ops.run_automations(CLOCK)['done'], [])


if __name__ == '__main__':
    unittest.main()
