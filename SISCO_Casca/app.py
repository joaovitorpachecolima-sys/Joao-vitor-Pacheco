"""Servidor local do SISCO (casca). Lê a planilha de dados escolhida; envios só manuais e só para o e-mail de teste."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import configuracao
from core import Conflict, Store, TEST_EMAIL, now
from importer import EXTENSOES, ler_conteudo, read_source
from operations import Operations
from maintenance import register
from workflow import phase_since_estimate
from datetime import date, datetime

import checklist
import indicadores
from gestao import Gestao, WAIT_REASONS, etapas_por_mes, linha_do_tempo

ROOT = Path(__file__).resolve().parent
MODELO_WORD = ROOT / 'modelos' / 'registro_modelo.docx'
MODELO_PLANILHA = ROOT / 'modelos' / 'SISCO_dados_modelo.xlsx'
ENTRADA = ROOT / 'entrada'
TAMANHO_MAXIMO = 7_500_000        # em base64 chega perto do limite de 11 MB por pedido

# Modo demonstração (SISCO_DEMO=1): site público com os dados inventados, para hospedar num servidor.
# Escuta em todas as interfaces, aceita o domínio configurado, desliga envio de e-mail e troca de planilha
# e volta a base ao original a cada SISCO_DEMO_RESET_HORAS horas. Sem a variável, nada muda no uso local.
DEMO = os.environ.get('SISCO_DEMO') == '1'
DEMO_HOSTS = {h.strip().lower() for h in os.environ.get('SISCO_HOSTS', '').split(',') if h.strip()}
DEMO_RESET_HORAS = float(os.environ.get('SISCO_DEMO_RESET_HORAS', '6') or 6)
DEMO_BLOQUEADO = 'Desligado na demonstração: aqui não se envia e-mail nem se troca a planilha.'
DEMO_FAIXA = ('<div class="demo-faixa" role="note">Demonstração com dados inventados. '
              'O que você mudar aqui volta ao original de tempos em tempos; e-mail e troca de planilha estão desligados.</div>')


class Application:
    def __init__(self, source, database, interval=60):
        self.source = Path(source).resolve()
        self.database = Path(database).resolve()
        self.interval = interval
        self.token = secrets.token_urlsafe(32)
        self.sync_lock = threading.Lock()
        self.stop = threading.Event()
        # Página Geração: relatórios por usina lidos da aba "Geracao" da planilha de dados.
        self.checklist_path = self.database.parent / 'checklist.json'
        self.config_file = configuracao.ARQUIVO      # onde fica "fonte_de_dados" (os testes usam outro)
        self.entrada = ENTRADA                       # pasta das planilhas recebidas pelo site
        self._abrir_base()
        self.demo_reset_em = datetime.now()

    def reiniciar_demo(self):
        """Demonstração: dados de exemplo com as datas de hoje e uma base nova. A anterior é apagada."""
        import gerar_exemplo
        from planilha import escrever
        with self.sync_lock:
            hoje = date.today()
            self.entrada.mkdir(parents=True, exist_ok=True)
            escrever(self.source, gerar_exemplo._abas(hoje, True) + [gerar_exemplo._aba_instrucoes()])
            antiga = self.database
            self.database = antiga.with_name(f'demo_{datetime.now():%Y%m%d_%H%M%S_%f}.sqlite3')
            self._abrir_base()
            self._ler()
            self.demo_reset_em = datetime.now()
            if antiga.name.startswith('demo_') and antiga != self.database:
                antiga.unlink(missing_ok=True)

    def _abrir_base(self):
        self.store = Store(self.database)
        self.operations = Operations(self.store, MODELO_WORD)
        self.gestao = Gestao(self.store)
        self.units_recorded = None

    def sync(self):
        if not self.sync_lock.acquire(blocking=False):
            raise Conflict('Uma leitura já está em andamento.')
        try:
            return self._ler()
        finally:
            self.sync_lock.release()

    def _ler(self):
        try:
            config = configuracao.ler(self.config_file)
            snapshot = read_source(self.source, config['colunas'], config['abas'])
            info = self.store.ingest(snapshot)
        except Exception as exc:
            self.store.sync_error(str(exc))
            raise
        relatorios = snapshot.get('geracao')
        dados = {'coletado_em': now(), 'situacao': 'ok' if relatorios else 'sem_dados', 'relatorios': relatorios or []}
        temporario = self.checklist_path.with_suffix('.tmp')
        temporario.write_text(json.dumps(dados, ensure_ascii=False), encoding='utf-8')
        temporario.replace(self.checklist_path)
        return info

    def trocar_fonte(self, body):
        """Recebe uma planilha nova pelo site: confere, guarda em entrada/ e passa a ler dela.
        modo "nova_base": a base local atual vai para dados/arquivo/ e começa uma base vazia (nada é apagado)."""
        if not isinstance(body, dict) or set(body) - {'nome', 'conteudo', 'modo'}:
            raise ValueError('Pedido de troca de planilha inválido.')
        nome = Path(str(body.get('nome') or '')).name
        extensao = Path(nome).suffix.lower()
        if extensao not in EXTENSOES:
            raise ValueError('Use uma planilha .xlsx (ou .xlsm) ou um arquivo .csv.')
        if body.get('modo', 'atualizar') not in ('atualizar', 'nova_base'):
            raise ValueError('Escolha entre atualizar a base atual ou começar uma base nova.')
        try:
            payload = base64.b64decode(str(body.get('conteudo') or ''), validate=True)
        except Exception:
            raise ValueError('Conteúdo do arquivo inválido.') from None
        if not payload or len(payload) > TAMANHO_MAXIMO:
            raise ValueError('Use um arquivo de até 7 MB.')
        config = configuracao.ler(self.config_file)
        teste = ler_conteudo(payload, nome, config['colunas'], config['abas'])   # recusa antes de gravar qualquer coisa
        if not teste['records']:
            raise ValueError('Nenhuma ocorrência com ID foi encontrada no arquivo. Confira a aba e os nomes das colunas.')
        seguro = re.sub(r'[^\w.\- ]', '_', Path(nome).stem).strip(' ._') or 'planilha'
        destino = self.entrada / f'{seguro}{extensao}'
        with self.sync_lock:
            self.entrada.mkdir(parents=True, exist_ok=True)
            temporario = destino.with_name(destino.name + '.tmp')
            temporario.write_bytes(payload)
            temporario.replace(destino)
            if body.get('modo') == 'nova_base' and self.database.exists():
                arquivo = self.database.parent / 'arquivo'
                arquivo.mkdir(exist_ok=True)
                self.database.replace(arquivo / f'{self.database.stem}_{datetime.now():%Y%m%d_%H%M%S}{self.database.suffix}')
                self._abrir_base()
            try:
                relativo = destino.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                relativo = str(destino.resolve())
            configuracao.gravar({'fonte_de_dados': relativo}, self.config_file)
            self.source = destino.resolve()
            self._ler()
        return self.state()

    def generation(self, items, config):
        """Situação das UGs agora, gravando o dia de cada uma no histórico (e os relatórios mais antigos, uma vez por coleta)."""
        data = checklist.carregar(self.checklist_path)
        current = checklist.montar(data, items, dias=config['ug_occurrence_days'])
        stamp = data.get('coletado_em', '')
        if stamp != self.units_recorded:
            for report in data.get('relatorios') or ():
                try:
                    moment = datetime.fromisoformat(str(report.get('recebido')))
                except ValueError:
                    continue
                self.gestao.record_units(checklist.montar({'relatorios': [report]}, items, agora=moment, dias=config['ug_occurrence_days']))
            self.units_recorded = stamp
        self.gestao.record_units(current)
        history = self.gestao.unit_history()
        for plant in current['usinas']:
            for unit in plant['unidades']:
                unit['historico'] = history.get((plant.get('chave', ''), unit.get('nome', '')))
        return current

    def create_action(self, body):
        """Ação a partir de um achado: a medida e o valor de "antes" vêm do cálculo do servidor, não do pedido."""
        if not isinstance(body, dict):
            raise ValueError('Pedido de ação inválido.')
        config, sync_info, causes = self.operations.config(), self.store.sync_info(), self.gestao.causes()
        items = [self.operations.enrich(item, config) for item in self.store.list_items()]
        month, ref = body.pop('month', ''), body.get('ref', '')
        kpis = indicadores.calcular(items, config, self.operations.kpi_targets()['targets'], sync_info.get('issues'), families=causes['families'])
        found = next((f for f in (kpis['by_month'].get(month) or {}).get('findings', ()) if f['ref'] == ref), None)
        metric = None
        if found and found.get('metric'):
            rows, scope = indicadores.preparar(items, config, sync_info.get('issues'), causes['families'])
            metric = {**found['metric'], 'value': indicadores.medir(found['metric'], rows, scope, date.today())}
        if found:
            body.setdefault('detail', found['detail'])
        return self.gestao.create_action(body, metric)

    def state(self):
        config = self.operations.config()
        items = [self.operations.enrich(item,config) for item in self.store.list_items()]
        # Tempo na fase (estilo Pipefy): registrado aqui, onde a fase de cada item é calculada.
        phases = self.store.track_phases({i['id']: i['workflow']['phase'] for i in items},
                                         {i['id']: phase_since_estimate(i['values'], i['workflow']['phase']) for i in items})
        for item in items:
            item['workflow']['phase_since'], item['workflow']['phase_since_estimated'] = phases[item['id']]
        sync_info = self.store.sync_info()
        targets = self.operations.kpi_targets()
        causes = self.gestao.causes()
        returns = self.store.returns()
        # Linha do tempo de cada ocorrência (a 1ª data de envio do Forms fica guardada aqui) e motivo da espera.
        first, waits, answered = self.gestao.first_forms(items), self.gestao.waits(), {}
        for entry in returns:
            answered.setdefault(entry.get('item_id'), []).append(entry)
        timelines = {i['id']: linha_do_tempo(i, first.get(i['id']), answered.get(i['id'], ()), config['sla_hours']) for i in items}
        for item in items:
            item['timeline'], item['wait'] = timelines[item['id']], waits.get(item['id'])
        rows, scope = indicadores.preparar(items, config, sync_info.get('issues'), causes['families'])
        try:
            fonte = self.source.relative_to(ROOT).as_posix()
        except ValueError:
            fonte = str(self.source)
        return {'items': items, 'sync': {**sync_info, 'source_path': fonte, 'source_file': self.source.name},
                # KPIs: indicadores com meta, leitura e achados (cálculo puro, nada é gravado).
                'kpis': {**indicadores.calcular(items, config, targets['targets'], sync_info.get('issues'), families=causes['families']), 'version': targets['version']},
                'management': {'causes': {**causes, 'unmatched': indicadores.sem_familia(rows)}, 'wait_reasons': list(WAIT_REASONS),
                               'actions': indicadores.acompanhar(self.gestao.actions(), rows, scope, date.today()),
                               'stages': etapas_por_mes(items, timelines)},
                'generation': self.generation(items, config),
                'rules': self.operations.rules(),
                'flow': self.operations.flow_state(),
                'configuration': config, 'maintenance_returns': returns,
                'analytics': self.operations.analytics(),
                'settings': {'test_email': TEST_EMAIL, 'mode': 'demo' if DEMO else 'test',
                             'sync_seconds': self.interval, 'sending_enabled': not DEMO, 'manual_only': True},
                'csrf_token': self.token}

    def worker(self):
        while not self.stop.is_set():
            if DEMO and (datetime.now() - self.demo_reset_em).total_seconds() >= DEMO_RESET_HORAS * 3600:
                try:
                    self.reiniciar_demo()
                except Exception as exc:
                    print('Reinício da demonstração adiado:', str(exc), flush=True)
            try:
                self.sync()
            except Exception as exc:
                print('Leitura adiada:', str(exc), flush=True)
            try:
                # Peças automáticas do fluxo publicado (só gerar o Word; nada é enviado).
                self.operations.run_automations()
            except Exception as exc:
                print('Peças automáticas adiadas:', str(exc), flush=True)
            self.stop.wait(self.interval)


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Não registrar conteúdos de relatos ou dados pessoais em stdout.
            pass

        def allowed_host(self):
            port = self.server.server_port
            if DEMO:
                # Atrás do servidor de hospedagem (HTTPS na frente). Sem SISCO_HOSTS, aceita qualquer domínio.
                host = (self.headers.get('Host') or '').lower().split(':')[0]
                return not DEMO_HOSTS or host in DEMO_HOSTS or urlsplit(self.path).path == '/api/health'
            return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'}

        def send(self, status, data, content_type='application/json; charset=utf-8', filename=None):
            content = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            if filename:
                self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-src https://app.powerbi.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {'error': 'Acesso permitido apenas neste computador.'})
            path = urlsplit(self.path).path
            try:
                if path == '/api/state':
                    return self.send(200, app.state())
                if path == '/api/flow/bpmn':
                    return self.send(200, app.operations.flow_bpmn())
                if path == '/api/health':
                    return self.send(200, {'ok': True, 'application': 'sisco-casca', 'mode': 'demo' if DEMO else 'test', 'sending_enabled': not DEMO, 'manual_only': True})
                # Planilha modelo (vazia, com instruções) e a planilha de dados em uso, para baixar e preencher.
                if path == '/api/source/modelo':
                    return self.send(200, MODELO_PLANILHA.read_bytes(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', MODELO_PLANILHA.name)
                if path == '/api/source/atual':
                    tipo = 'text/csv; charset=utf-8' if app.source.suffix.lower() == '.csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    return self.send(200, app.source.read_bytes(), tipo, app.source.name)
                if path.startswith('/api/documents/'):
                    doc=app.operations.document(path.rsplit('/',1)[1])
                    return self.send(200,Path(doc['path']).read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document',doc['filename'])
                if path.startswith('/api/items/'):
                    return self.send(200, app.operations.enrich(app.store.get(int(path.rsplit('/', 1)[1]))))
                # Bibliotecas de terceiros guardadas no projeto (bpmn-js, dmn-js): só .js/.css de dentro de static/vendor.
                if path.startswith('/vendor/'):
                    vendor = (ROOT / 'static' / 'vendor').resolve()
                    target = (ROOT / 'static' / path.lstrip('/')).resolve()
                    kinds = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}
                    if vendor in target.parents and target.suffix in kinds and target.is_file():
                        return self.send(200, target.read_bytes(), kinds[target.suffix])
                    return self.send(404, {'error': 'Arquivo não encontrado.'})
                # Marca (logo e símbolo): troque os arquivos de static/marca pelos da sua empresa.
                if path.startswith('/marca/'):
                    marca = (ROOT / 'static' / 'marca').resolve()
                    target = (ROOT / 'static' / path.lstrip('/')).resolve()
                    kinds = {'.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'}
                    if marca in target.parents and target.suffix in kinds and target.is_file():
                        return self.send(200, target.read_bytes(), kinds[target.suffix])
                    return self.send(404, {'error': 'Arquivo não encontrado.'})
                file = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css',
                        '/tema.js': 'tema.js'}.get(path)
                if file:
                    data = (ROOT / 'static' / file).read_bytes()
                    if DEMO and file == 'index.html':
                        data = data.replace(b'<body>', b'<body class="modo-demo">' + DEMO_FAIXA.encode('utf-8'), 1)
                    kind = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'tema.js': 'text/javascript; charset=utf-8',
                            'styles.css': 'text/css; charset=utf-8'}[file]
                    return self.send(200, data, kind)
                return self.send(404, {'error': 'Página não encontrada.'})
            except (KeyError, ValueError):
                return self.send(404, {'error': 'Ocorrência não encontrada.'})
            except Exception:
                return self.send(500, {'error': 'Não foi possível ler os dados locais.'})

        def reject_mutation(self, status, message):
            # Fechar um socket Windows com corpo ainda não lido pode descartar
            # a resposta HTTP. Consumir apenas um corpo limitado, sem interpretá-lo.
            timeout = self.connection.gettimeout()
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if 0 < size <= 11_000_000:
                    self.connection.settimeout(2)
                    self.rfile.read(size)
            except (ValueError, OSError):
                pass
            finally:
                self.connection.settimeout(timeout)
            return self.send(status, {'error': message})

        def mutate(self, method):
            if not self.allowed_host():
                return self.reject_mutation(403, 'Origem inválida.')
            origin = self.headers.get('Origin')
            origens = {'http://' + self.headers.get('Host', '')} | ({'https://' + self.headers.get('Host', '')} if DEMO else set())
            if origin and origin not in origens:
                return self.reject_mutation(403, 'Origem inválida.')
            if not secrets.compare_digest(self.headers.get('X-SISCO-Token', ''), app.token):
                return self.reject_mutation(403, 'Reabra a página para continuar.')
            if self.headers.get_content_type() != 'application/json':
                return self.reject_mutation(415, 'Conteúdo deve ser JSON.')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 11_000_000:
                    return self.send(413, {'error': 'Pedido muito grande.'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body,dict):
                    raise ValueError('Pedido deve ser um objeto JSON.')
                path = urlsplit(self.path).path
                if DEMO and (path == '/api/source/upload' or re.fullmatch(r'/api/items/\d+/send', path)):
                    return self.send(403, {'error': DEMO_BLOQUEADO})
                if method == 'PATCH' and path == '/api/configuration':
                    return self.send(200,app.operations.save_config(body))
                if method == 'PATCH' and path == '/api/analytics':
                    return self.send(200,app.operations.save_analytics(body))
                if method == 'PATCH' and path == '/api/kpis/targets':
                    app.operations.save_kpi_targets(body)
                    return self.send(200,app.state())
                # Gestão: famílias de causa, ações de melhoria e motivo da espera (só no banco local).
                if method == 'PATCH' and path == '/api/causes':
                    app.gestao.save_causes(body)
                    return self.send(200,app.state())
                if method == 'POST' and path == '/api/actions':
                    app.create_action(body)
                    return self.send(200,app.state())
                managed = re.fullmatch(r'/api/(actions|items)/(\d+)(/wait)?',path)
                if method == 'PATCH' and managed and (managed[1] == 'actions') != bool(managed[3]):
                    if managed[1] == 'actions':
                        app.gestao.update_action(int(managed[2]),body)
                    else:
                        app.gestao.save_wait(int(managed[2]),body)
                    return self.send(200,app.state())
                # Regras do fluxo: rascunho → simular → publicar (e voltar a uma versão anterior).
                if method == 'PATCH' and path == '/api/rules/draft':
                    return self.send(200,app.operations.save_draft(body))
                if method == 'POST' and path == '/api/rules/simulate':
                    return self.send(200,app.operations.simulate(body.get('rules') or {}))
                if method == 'POST' and path == '/api/rules/publish':
                    app.operations.publish_draft(body)
                    return self.send(200,app.state())
                if method == 'POST' and path == '/api/rules/discard':
                    app.operations.discard_draft()
                    return self.send(200,app.state())
                if method == 'POST' and path == '/api/rules/restore':
                    return self.send(200,app.operations.restore_version(body))
                # Tabela de decisão (DMN): monta a partir das regras ou lê o que foi editado. Não grava nada.
                if method == 'POST' and path == '/api/rules/dmn':
                    from dmn import tratar_pedido
                    return self.send(200,tratar_pedido(body))
                # Fluxo das ocorrências (o grafo do motor): rascunho → simular → publicar, e voltar a uma versão.
                if method == 'PATCH' and path == '/api/flow/draft':
                    return self.send(200,app.operations.save_flow_draft(body))
                if method == 'POST' and path == '/api/flow/simulate':
                    return self.send(200,app.operations.simulate_flow(body.get('flow')))
                if method == 'POST' and path == '/api/flow/publish':
                    app.operations.publish_flow(body)
                    return self.send(200,app.state())
                if method == 'POST' and path == '/api/flow/discard':
                    app.operations.discard_flow_draft()
                    return self.send(200,app.state())
                if method == 'POST' and path == '/api/flow/restore':
                    return self.send(200,app.operations.restore_flow(body))
                action = re.fullmatch(r'/api/items/(\d+)/(word|preview|send|maintenance-return)',path)
                if method == 'POST' and action:
                    item_id=int(action[1]); operation=action[2]
                    if operation=='maintenance-return':
                        return self.send(200,app.operations.enrich(register(app.store,item_id,body)))
                    if operation=='word':
                        result=app.operations.create_document(item_id,body)
                        result['document'].pop('path',None)
                        result['item']=app.operations.enrich(result['item'])
                        return self.send(200,result)
                    if operation=='preview':
                        return self.send(200,app.operations.preview(item_id,body,self.server.server_port))
                    return self.send(200,app.operations.enrich(app.operations.send_test(item_id,body,self.server.server_port)))
                if method == 'POST' and path in ('/api/sync', '/api/generation/refresh'):
                    app.sync()
                    return self.send(200, app.state())
                # Trocar a planilha de dados pelo site (o arquivo é conferido antes de substituir o atual).
                if method == 'POST' and path == '/api/source/upload':
                    return self.send(200, app.trocar_fonte(body))
                if method == 'PATCH' and path.startswith('/api/items/'):
                    return self.send(200, app.operations.enrich(app.store.update(int(path.rsplit('/', 1)[1]), body)))
                return self.send(404, {'error': 'Operação inexistente.'})
            except Conflict as exc:
                return self.send(409, {'error': str(exc)})
            except KeyError:
                return self.send(404, {'error': 'Ocorrência não encontrada.'})
            except (ValueError, UnicodeDecodeError) as exc:
                return self.send(400, {'error': str(exc)})
            except Exception as exc:
                return self.send(503, {'error': 'Operação não concluída. ' + str(exc)})

        def do_POST(self):
            self.mutate('POST')

        def do_PATCH(self):
            self.mutate('PATCH')
    return Handler


def main():
    config = configuracao.ler()
    parser = argparse.ArgumentParser(description='SISCO (casca): controle de ocorrências lido de uma planilha simples.')
    parser.add_argument('--source', type=Path, default=configuracao.caminho(config['fonte_de_dados']),
                        help='Planilha de dados (.xlsx, .xlsm ou .csv). Padrão: "fonte_de_dados" em configuracao.json.')
    parser.add_argument('--database', type=Path, default=ROOT / 'dados' / 'sisco.sqlite3')
    parser.add_argument('--port', type=int, default=int(config['porta']))
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--once', action='store_true', help='Somente ler a planilha para a base local e sair.')
    args = parser.parse_args()
    if args.interval < 10:
        parser.error('Intervalo mínimo de 10 segundos.')
    if args.database.resolve() == args.source.resolve():
        parser.error('A base local deve ser separada da planilha de dados.')
    if DEMO:
        args.database = ROOT / 'dados' / 'demo_inicial.sqlite3'
        args.port = int(os.environ.get('PORT') or args.port)
    app = Application(args.source, args.database, args.interval)
    if DEMO:
        app.reiniciar_demo()
    if args.once:
        print(json.dumps(app.sync(), ensure_ascii=False, default=str))
        return
    server = ThreadingHTTPServer(('0.0.0.0' if DEMO else '127.0.0.1', args.port), handler_for(app))
    thread = threading.Thread(target=app.worker, daemon=True)
    thread.start()
    print(f'SISCO: http://127.0.0.1:{args.port}' + (' (modo demonstração)' if DEMO else ''), flush=True)
    if DEMO:
        print(f'Dados inventados; base reiniciada a cada {DEMO_RESET_HORAS:g} h. Envio de e-mail e troca de planilha desligados.', flush=True)
    else:
        print(f'Planilha de dados: {app.source.name} (somente leitura). Envios manuais só para {TEST_EMAIL}.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
