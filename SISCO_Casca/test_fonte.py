"""Fonte de dados da casca: a planilha simples (ou .csv) vira ocorrências, configuração e geração; e pode ser trocada pelo site."""
import base64
import json
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

from app import Application
from core import Store
from gerar_exemplo import _abas, _aba_instrucoes
from importer import ler_conteudo, read_source
from operations import Operations
from planilha import escrever

PASTA = Path(__file__).parent


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PASTA)
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def planilha(self, linhas, nome='dados.xlsx', aba='Ocorrencias', extras=()):
        caminho = self.dir / nome
        escrever(caminho, [(aba, linhas, None, ())] + list(extras))
        return caminho


class LeituraDoExemplo(Base):
    def test_exemplo_completo_sem_pontos_de_atencao(self):
        caminho = self.dir / 'exemplo.xlsx'
        escrever(caminho, _abas(date(2026, 11, 25), True) + [_aba_instrucoes()])
        lido = read_source(caminho)
        from gerar_exemplo import ocorrencias
        self.assertEqual((len(lido['records']), lido['issues']), (len(ocorrencias(date(2026, 11, 25))), []))
        self.assertEqual(lido['sheet'], 'Ocorrencias')
        self.assertEqual(len(lido['columns']), 23)                                # todas as colunas do modelo reconhecidas
        self.assertEqual({r['coordination'] for r in lido['configuration']['routes']},
                         {'Coordenação Norte', 'Coordenação Sul', 'Coordenação Leste'})
        self.assertTrue(lido['geracao'])
        nomes = {r['source_values']['plant'] for r in lido['records']}
        self.assertTrue(nomes <= {f'Usina {letra}' for letra in 'ABCDEFGH'})      # só nomes inventados

    def test_modelo_vazio_le_sem_erro_e_sem_ocorrencias(self):
        caminho = self.dir / 'modelo.xlsx'
        escrever(caminho, [_aba_instrucoes()] + _abas(date(2026, 11, 25), False))
        lido = read_source(caminho)
        self.assertEqual((lido['records'], lido['issues']), ([], []))


class Variacoes(Base):
    def test_ordem_nomes_alternativos_titulo_acima_e_colunas_a_mais(self):
        caminho = self.planilha([['Controle de ocorrências da minha empresa'], [],
                                 ['Usina', 'Coluna minha', 'Número', 'Data de início', 'Horário', 'Tipo', 'Relato'],
                                 ['Usina A', 'x', 15, date(2026, 11, 20), time(8, 30), 'Manutenção', 'Desarme']])
        registro = read_source(caminho)['records'][0]
        v = registro['source_values']
        self.assertEqual((registro['legacy_id'], v['plant'], v['start_date'], v['start_time'], v['classification'], v['description']),
                         ('15', 'Usina A', '2026-11-20', '08:30', 'Manutenção', 'Desarme'))

    def test_data_e_hora_na_mesma_celula_e_textos(self):
        caminho = self.planilha([['ID', 'Data início', 'Hora início', 'Data fim', 'Hora fim', 'Pedido de status enviado em', 'Registro enviado em'],
                                 [1, datetime(2026, 11, 20, 8, 30), datetime(2026, 11, 20, 8, 30), '21/11/2026', '9h15', '21/11/2026 10:00', datetime(2026, 11, 22, 9, 0)]])
        v = read_source(caminho)['records'][0]['source_values']
        self.assertEqual((v['start_date'], v['start_time'], v['end_date'], v['end_time']), ('2026-11-20', '08:30', '2026-11-21', '09:15'))
        self.assertEqual((v['forms_sent'], v['forms_sent_at']), ('Enviado', '2026-11-21T10:00'))
        self.assertEqual(v['document_sent'], 'Email enviado (22/11/2026 09:00)')
        self.assertEqual((v['document_deadline_date'], v['document_next_date']), ('2026-11-29', '2026-11-30'))   # 7 dias por padrão

    def test_ids_repetidos_e_linha_sem_id_viram_pontos_de_atencao(self):
        caminho = self.planilha([['ID', 'Usina'], [7, 'Usina A'], [7, 'Usina B'], ['', 'Usina C'], [8, 'Usina D']])
        lido = read_source(caminho)
        self.assertEqual([r['legacy_id'] for r in lido['records']], ['8'])
        self.assertEqual(sorted(i['kind'] for i in lido['issues']), ['duplicate_legacy_id', 'invalid_control_id'])

    def test_valores_nao_reconhecidos_geram_aviso_e_coordenacao_vem_da_usina(self):
        caminho = self.planilha([['ID', 'Usina', 'Data início', 'Status da manutenção'], [1, 'Usina A', 'ontem', 'mais ou menos']],
                                extras=[('Usinas', [['Usina', 'Coordenação'], ['usina a', 'Coordenação Norte']], None, ())])
        registro = read_source(caminho)['records'][0]
        self.assertEqual(registro['source_values']['coordination'], 'Coordenação Norte')
        self.assertTrue(any('não reconhecida' in w for w in registro['warnings']))
        self.assertTrue(any('Status da manutenção' in w for w in registro['warnings']))

    def test_nomes_de_colunas_e_aba_pelo_configuracao(self):
        caminho = self.planilha([['Cód.', 'Planta X', 'Quando'], [3, 'Usina A', '20/11/2026']], aba='Plan1',
                                extras=[('Outra', [['ID'], [99]], None, ())])
        lido = read_source(caminho, colunas={'ID': 'Cód.', 'Usina': 'Planta X', 'start_date': 'Quando'}, abas={'Ocorrencias': 'Plan1'})
        v = lido['records'][0]
        self.assertEqual((v['legacy_id'], v['source_values']['plant'], v['source_values']['start_date']), ('3', 'Usina A', '2026-11-20'))
        with self.assertRaises(ValueError):
            read_source(caminho, abas={'Ocorrencias': 'Não existe'})

    def test_csv_com_ponto_e_virgula_em_windows_1252(self):
        caminho = self.dir / 'dados.csv'
        caminho.write_bytes('ID;Usina;Data início;Hora início;Classificação\n5;Usina A;20/11/2026;08:30;Manutenção\n'.encode('cp1252'))
        registro = read_source(caminho)['records'][0]
        self.assertEqual((registro['legacy_id'], registro['source_values']['start_date'], registro['source_values']['classification']),
                         ('5', '2026-11-20', 'Manutenção'))

    def test_arquivos_recusados(self):
        with self.assertRaises(ValueError):
            read_source(self.dir / 'nao_existe.xlsx')
        with self.assertRaises(ValueError):
            ler_conteudo(b'qualquer coisa', 'dados.txt')
        with self.assertRaises(ValueError):
            ler_conteudo(b'nao e zip', 'dados.xlsx')
        with self.assertRaises(ValueError):
            read_source(self.planilha([['Usina', 'Data'], ['Usina A', '20/11/2026']]))   # sem coluna ID


class RevisaoInicial(Base):
    def test_revisada_na_planilha_entra_revisada_e_depois_vale_o_sisco(self):
        caminho = self.planilha([['ID', 'Usina', 'Revisada'], [1, 'Usina A', 'Sim'], [2, 'Usina B', '']])
        store = Store(self.dir / 'base.sqlite3')
        store.ingest(read_source(caminho))
        itens = {i['legacy_id']: i for i in store.list_items()}
        self.assertTrue(itens['1']['reviewed'])
        self.assertFalse(itens['2']['reviewed'])
        store.update(itens['1']['id'], {'version': itens['1']['version'], 'reviewed': False})
        store.ingest(read_source(caminho))                                         # reler não revisa de novo
        self.assertFalse(store.get(itens['1']['id'])['reviewed'])


class TrocaPeloSite(Base):
    def setUp(self):
        super().setUp()
        primeira = self.planilha([['ID', 'Usina'], [1, 'Usina A'], [2, 'Usina B']], nome='primeira.xlsx')
        self.app = Application(primeira, self.dir / 'dados' / 'base.sqlite3')
        self.app.config_file = self.dir / 'configuracao.json'
        self.app.entrada = self.dir / 'entrada'
        self.app.sync()

    def enviar(self, caminho, modo='atualizar', nome=None):
        return self.app.trocar_fonte({'nome': nome or caminho.name, 'conteudo': base64.b64encode(caminho.read_bytes()).decode(), 'modo': modo})

    def test_atualizar_mantem_a_base_e_troca_a_fonte(self):
        segunda = self.planilha([['ID', 'Usina'], [1, 'Usina A'], [3, 'Usina C']], nome='segunda.xlsx')
        estado = self.enviar(segunda)
        self.assertEqual(Path(estado['sync']['source_file']).name, 'segunda.xlsx')
        presentes = {i['legacy_id'] for i in estado['items'] if i['source_present']}
        self.assertEqual(presentes, {'1', '3'})                                     # 2 fica na base, marcado como ausente
        self.assertEqual(len(estado['items']), 3)
        self.assertTrue((self.dir / 'entrada' / 'segunda.xlsx').is_file())
        self.assertEqual(json.loads(self.app.config_file.read_text(encoding='utf-8'))['fonte_de_dados'],
                         (self.dir / 'entrada' / 'segunda.xlsx').resolve().relative_to(PASTA).as_posix())

    def test_nova_base_guarda_a_antiga(self):
        outra = self.planilha([['ID', 'Usina'], [10, 'Usina Z']], nome='outra.xlsx')
        estado = self.enviar(outra, modo='nova_base')
        self.assertEqual([i['legacy_id'] for i in estado['items']], ['10'])
        arquivados = list((self.dir / 'dados' / 'arquivo').glob('base_*.sqlite3'))
        self.assertEqual(len(arquivados), 1)
        self.assertEqual(len(Store(arquivados[0]).list_items()), 2)                # nada foi apagado

    def test_arquivo_ruim_e_recusado_sem_mexer_na_fonte(self):
        ruim = self.planilha([['Usina'], ['Usina A']], nome='ruim.xlsx')
        antes = self.app.source
        for corpo in ({'nome': 'ruim.xlsx', 'conteudo': base64.b64encode(ruim.read_bytes()).decode()},
                      {'nome': 'virus.exe', 'conteudo': 'AAAA'},
                      {'nome': 'x.xlsx', 'conteudo': 'não é base64'},
                      {'nome': 'x.xlsx', 'conteudo': 'AAAA', 'modo': 'apagar tudo'}):
            with self.subTest(corpo['nome']), self.assertRaises(ValueError):
                self.app.trocar_fonte(corpo)
        self.assertEqual(self.app.source, antes)
        self.assertFalse((self.dir / 'entrada').exists())
        self.assertFalse(self.app.config_file.exists())


class ModeloWord(Base):
    def test_modelo_do_registro_tem_os_campos_e_gera_documento(self):
        from gerar_exemplo import documento_modelo
        modelo = self.dir / 'registro.docx'
        documento_modelo(modelo)
        caminho = self.planilha([['ID', 'Usina', 'Equipamento', 'Data início', 'Hora início', 'Revisada'],
                                 [42, 'Usina A', 'UG01', '20/11/2026', '08:30', 'Sim']])
        store = Store(self.dir / 'base.sqlite3')
        store.ingest(read_source(caminho))
        ops = Operations(store, modelo)
        item = store.list_items()[0]
        resultado = ops.create_document(item['id'], {'version': item['version']})
        from zipfile import ZipFile
        with ZipFile(resultado['document']['path']) as z:
            texto = z.read('word/document.xml').decode('utf-8')
        self.assertIn('42/2026', texto)
        self.assertIn('Usina A', texto)
        self.assertNotIn('{{', texto)


if __name__ == '__main__':
    unittest.main()
