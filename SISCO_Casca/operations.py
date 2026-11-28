"""Documentos e comunicações manuais do piloto, sempre destinados ao e-mail de teste."""
from __future__ import annotations
import base64
import copy
import hashlib
import html
import json
import re
import subprocess
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
from core import Conflict, TEST_EMAIL, dumps, now
from workflow import DEFAULT_FLOWS, DEFAULT_FLOW_TYPES, RECIPIENT_MODES, RULE_DEFAULTS, RULE_KEYS, describe, derive, parse, full_end, document_deadline, forms_deadline
from importer import _normalize
from indicadores import KPI_DEFAULTS, validar_metas
from fluxo import ACOES, CONDICOES, FASES, PRAZOS, TIPOS_ACAO, atualizar_fluxo_salvo, fluxo_padrao, validar as validar_fluxo
from bpmn import gerar_bpmn, ler_bpmn

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def _emails(text):
    """Endereços válidos de uma lista separada por ; ou ,."""
    return [p.strip() for p in re.split(r'[;,\n]', str(text or '')) if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', p.strip())]


def normalize_config(config):
    """
    Garante o formato com tipos de fluxo. Configurações antigas (sem flow_types)
    recebem os tipos padrão mais os nomes já usados; o comportamento de cada
    classificação (acompanhamento/coordenador) passa a vir do seu tipo.
    """
    config = copy.deepcopy(config)
    class_routes = [{'to': '', **r} for r in config.get('class_routes', [])]
    types = {_normalize(t['name']): dict(t) for t in config.get('flow_types') or []}
    if not types:
        types = {_normalize(t['name']): dict(t) for t in copy.deepcopy(DEFAULT_FLOW_TYPES)}
        with_to = {_normalize(r['classification']) for r in class_routes if r['to']}
        for f in config.get('flows', []):
            key = _normalize(f.get('flow'))
            if key and key not in types:
                types[key] = {'name': f['flow'], 'followup': bool(f.get('followup')), 'coordinator': bool(f.get('coordinator')),
                              'recipient': 'classificacao' if _normalize(f['classification']) in with_to else 'coordenacao'}
    flows = []
    for f in config.get('flows', []):
        t = types.get(_normalize(f.get('flow')))
        flows.append({'classification': f['classification'], 'flow': t['name'] if t else f.get('flow', ''),
                      'coordinator': t['coordinator'] if t else bool(f.get('coordinator')),
                      'followup': t['followup'] if t else bool(f.get('followup')),
                      'recipient': t['recipient'] if t else 'coordenacao'})
    return {**config, 'flow_types': list(types.values()), 'flows': flows, 'class_routes': class_routes}


def routing_for(config, values, kind):
    """
    Destinatários reais (de referência no ambiente de testes) de uma ocorrência.
    Fluxo que envia para a classificação: Para, CC e CCO da própria classificação,
    sem a coordenação. Caso contrário: Para = coordenação; CC/CCO da classificação
    nas solicitações de status e da coordenação no registro Word.
    """
    flow = next((f for f in config['flows'] if _normalize(f['classification']) == _normalize(values.get('classification'))), {})
    coordination = next((r for r in config['routes'] if _normalize(r['coordination']) == _normalize(values.get('coordination'))), {})
    class_route = next((r for r in config['class_routes'] if _normalize(r['classification']) == _normalize(values.get('classification'))), {})
    if flow.get('recipient') == 'classificacao':
        return {'to': class_route.get('to', ''), 'cc': class_route.get('cc', ''), 'bcc': class_route.get('bcc', ''), 'mode': 'classificacao'}
    source = coordination if kind.startswith('document') else class_route
    return {'to': coordination.get('to', ''), 'cc': source.get('cc', ''), 'bcc': source.get('bcc', ''), 'mode': 'coordenacao'}

class NotSubmitted(RuntimeError):
    pass

def _replace_xml(data, replacements):
    # Replace only text-node contents; preserve all other XML bytes, namespaces,
    # drawings and relationships. Tokens may be split across multiple Word runs.
    text = data.decode('utf-8')
    matches = list(re.finditer(r'<w:t\b[^>]*>(.*?)</w:t>', text, re.S))
    parts = [html.unescape(m.group(1)) for m in matches]
    tokens = list(re.finditer('|'.join(re.escape(k) for k in replacements), ''.join(parts)))
    for token in reversed(tokens):
        start,end=token.span(); value=replacements[token.group()]; offset=0
        for i,part in enumerate(parts):
            a,b=offset,offset+len(part); offset=b
            if a < end and b > start:
                left=max(0,start-a); right=min(len(part),end-a)
                parts[i]=part[:left]+(str(value) if a<=start<b else '')+part[right:]
    for match,part in reversed(list(zip(matches,parts))):
        opening=text[match.start():match.start(1)]
        if part != part.strip() and 'xml:space=' not in opening:
            opening=opening[:-1]+' xml:space="preserve">'
        text=text[:match.start()]+opening+html.escape(part,quote=False)+text[match.end(1):]
    return text.encode('utf-8')

def validate_docx(data):
    if not data or len(data)>8_000_000: raise ValueError('Use um DOCX de até 8 MB.')
    try:
        with ZipFile(BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist())>40_000_000: raise ValueError('Documento expandido excede o limite de 40 MB.')
            if 'word/document.xml' not in z.namelist() or '[Content_Types].xml' not in z.namelist(): raise ValueError('Arquivo DOCX inválido.')
            if any('vbaproject' in n.casefold() for n in z.namelist()): raise ValueError('Documentos com macros não são aceitos.')
            ET.fromstring(z.read('word/document.xml'))
    except (BadZipFile,ET.ParseError) as exc: raise ValueError('Arquivo DOCX inválido.') from exc

class Operations:
    def __init__(self, store, template):
        self.store=store
        self.template=Path(template)
        self.directory=store.path.parent/'documentos_teste'
        self.directory.mkdir(exist_ok=True)

    def config(self):
        with self.store.connect() as db:
            saved=db.execute("SELECT value FROM metadata WHERE key='configuration'").fetchone()
            source=db.execute("SELECT value FROM metadata WHERE key='source_configuration'").fetchone()
        base=normalize_config(json.loads(source[0]) if source else {'flows':copy.deepcopy(DEFAULT_FLOWS),'routes':[],'class_routes':[]})
        config=normalize_config(json.loads(saved[0])) if saved else {**base,'version':0,'signature':'Equipe de Operação · SISCO', 'document_days':7,'reminder_days':7}
        config={**copy.deepcopy(RULE_DEFAULTS),**config}
        config['phase_targets']={**RULE_DEFAULTS['phase_targets'],**config.get('phase_targets',{})}
        # O fluxo publicado (etapa 2 do editor BPMN) segue junto: é ele que o motor percorre.
        return {**config,'fluxo':self.flow()['flow'],'test_email':TEST_EMAIL,'template_available':self.template.is_file(),'source_configuration':base}

    # ---------- Fluxo das ocorrências (o grafo que o motor percorre): rascunho → validar → simular → publicar ----------
    def flow(self):
        """Fluxo publicado. Sem nada salvo, ou se o salvo não for válido, vale o fluxo padrão (= regras de antes)."""
        with self.store.connect() as db:
            saved=db.execute("SELECT value FROM metadata WHERE key='flow_published'").fetchone()
        saved=json.loads(saved[0]) if saved else None
        if saved:
            saved['flow']=atualizar_fluxo_salvo(saved.get('flow'))
        if saved and not validar_fluxo(saved.get('flow')):
            return saved
        return {'version':0,'flow':fluxo_padrao(),'at':'','note':'Fluxo padrão','fallback':bool(saved)}

    def flow_state(self):
        """O que a tela do Flow Studio precisa: publicado, rascunho, histórico e os catálogos para editar."""
        with self.store.connect() as db:
            draft=db.execute("SELECT value FROM metadata WHERE key='flow_draft'").fetchone()
            versions=db.execute("SELECT value FROM metadata WHERE key='flow_versions'").fetchone()
        published=self.flow()
        draft=self._without_bpmn(json.loads(draft[0])) if draft else None              # o arquivo vai por /api/flow/bpmn
        if draft:
            draft['flow']=atualizar_fluxo_salvo(draft['flow'])
        return {'version':published['version'],'published':published['flow'],'fallback':published.get('fallback',False),
                'draft':draft,
                'versions':[{k:v[k] for k in ('version','at','note')} for v in (json.loads(versions[0]) if versions else [])][::-1],
                'conditions':{k:nome for k,(nome,_) in CONDICOES.items()},'deadlines':{k:nome for k,(nome,_) in PRAZOS.items()},'phases':FASES,'action_kinds':list(TIPOS_ACAO),
                'actions':{k:{'name':nome,'type':tipo,'automatic':auto} for k,(nome,tipo,auto) in ACOES.items()},
                'automation':getattr(self,'last_automation',None)}

    def run_automations(self, clock=None):
        """Executa as peças automáticas do fluxo publicado (hoje só "Gerar o Word do registro").

        Só em ocorrências revisadas que estão nessa situação e ainda não têm documento. Nada é enviado: o Word
        fica nos documentos de teste do SISCO e o envio continua manual, com prévia."""
        config=self.config(); flow=config['fluxo']; clock=clock or parse(now())
        auto={k for k,el in flow['elementos'].items() if el['tipo']=='situacao' and el.get('automatico') and el.get('tipo_acao')=='word'}
        result={'at':now(),'done':[],'blocked':[]}
        if auto:
            for item in self.store.list_items():
                if not item['reviewed'] or (describe(item,config,clock).get('path') or [None])[-1] not in auto or self.documents(item['id']): continue
                label=item.get('legacy_id') or f'Forms {item.get("forms_id") or item["id"]}'
                try:
                    self.create_document(item['id'],{'version':item['version']})
                    with self.store.lock, self.store.connect() as db:
                        db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',(item['id'],now(),'Peça automática do fluxo',dumps({'acao':'word','fluxo':self.flow()['version']})))
                    result['done'].append({'id':item['id'],'label':label})
                except (ValueError,Conflict) as exc:
                    result['blocked'].append({'id':item['id'],'label':label,'reason':str(exc)})
        self.last_automation=result
        return result

    @staticmethod
    def _checked_flow(flow):
        if not isinstance(flow,dict) or len(json.dumps(flow))>200_000: raise ValueError('Fluxo inválido.')
        problems=validar_fluxo(flow)
        if problems: raise ValueError('O fluxo não pode ser salvo: '+' '.join(problems[:5]))
        return copy.deepcopy(flow)

    def save_flow_draft(self, body):
        """O rascunho pode ficar incompleto enquanto é editado (ex.: elemento novo ainda solto): ele é salvo com
        a lista de problemas, mas só é simulado e publicado quando não houver nenhum.

        Vem do editor BPMN (`bpmn`: o arquivo editado, lido por bpmn.ler_bpmn) ou já como fluxo (`flow`)."""
        if not isinstance(body,dict): raise ValueError('Rascunho de fluxo inválido.')
        if 'bpmn' in body:
            flow,drawing_problems=ler_bpmn(body['bpmn'])
            problems=list(dict.fromkeys(drawing_problems+validar_fluxo(flow)))
            draft={'base_version':self.flow()['version'],'updated_at':now(),'flow':flow,'bpmn':body['bpmn'],'problems':problems}
            with self.store.lock, self.store.connect() as db:
                self.store._meta(db,'flow_draft',draft)
            return {'draft':self._without_bpmn(draft),'simulation':None if problems else self.simulate_flow(flow)}
        if not isinstance(body.get('flow'),dict): raise ValueError('Rascunho de fluxo inválido.')
        flow=body['flow']
        if not isinstance(flow.get('elementos'),dict) or len(flow['elementos'])>300 or len(json.dumps(flow))>200_000: raise ValueError('Fluxo inválido.')
        if any(not isinstance(k,str) or not 0<len(k)<=60 for k in flow['elementos']): raise ValueError('Identificador de elemento inválido.')
        if 'layout' in flow and not isinstance(flow['layout'],dict): raise ValueError('Posições do desenho inválidas.')
        problems=validar_fluxo(flow)
        draft={'base_version':self.flow()['version'],'updated_at':now(),'flow':copy.deepcopy(flow),'problems':problems}
        with self.store.lock, self.store.connect() as db:
            self.store._meta(db,'flow_draft',draft)
        return {'draft':draft,'simulation':None if problems else self.simulate_flow(flow)}

    def discard_flow_draft(self):
        with self.store.lock, self.store.connect() as db:
            db.execute("DELETE FROM metadata WHERE key='flow_draft'")

    def publish_flow(self, body):
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='flow_draft'").fetchone()
        if not row: raise ValueError('Não há rascunho de fluxo para publicar.')
        draft=json.loads(row[0])
        if not isinstance(body,dict) or type(body.get('version')) is not int: raise ValueError('Versão inválida.')
        if draft.get('problems'): raise ValueError('O rascunho tem problemas: '+' '.join(draft['problems'][:5]))
        flow=self._checked_flow(draft['flow'])
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.flow()
            if body['version']!=current['version'] or draft['base_version']!=current['version']:
                raise Conflict('O fluxo publicado mudou depois do rascunho. Confira o rascunho antes de publicar.')
            note=str(body.get('note') or 'Fluxo publicado')[:200]
            history=db.execute("SELECT value FROM metadata WHERE key='flow_versions'").fetchone()
            versions=(json.loads(history[0]) if history else [])[-19:]
            versions.append({'version':current['version'],'at':now(),'note':note,'flow':current['flow'],**({'bpmn':current['bpmn']} if current.get('bpmn') else {})})
            self.store._meta(db,'flow_versions',versions)
            self.store._meta(db,'flow_published',{'version':current['version']+1,'flow':flow,'at':now(),'note':note,**({'bpmn':draft['bpmn']} if draft.get('bpmn') else {})})
            db.execute("DELETE FROM metadata WHERE key='flow_draft'")
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)',(now(),'Fluxo publicado',dumps({'version':current['version']+1,'note':note})))
        return self.flow()

    def restore_flow(self, body):
        """Põe o fluxo de uma versão anterior no rascunho (para conferir e publicar de novo)."""
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='flow_versions'").fetchone()
        found=next((v for v in (json.loads(row[0]) if row else []) if isinstance(body,dict) and v['version']==body.get('version')),None)
        if not found: raise ValueError('Versão do fluxo não encontrada no histórico.')
        if found.get('bpmn'):
            return self.save_flow_draft({'bpmn':found['bpmn']})
        return self.save_flow_draft({'flow':atualizar_fluxo_salvo(found['flow'])})

    @staticmethod
    def _without_bpmn(draft):
        return {k:v for k,v in draft.items() if k!='bpmn'} if draft else draft

    def flow_bpmn(self):
        """Os arquivos BPMN para o editor: o publicado e o do rascunho (gerados do fluxo quando ainda não há desenho)."""
        published=self.flow()
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='flow_draft'").fetchone()
        draft=json.loads(row[0]) if row else None
        atual=atualizar_fluxo_salvo(draft['flow']) if draft else published['flow']
        try:
            organized=gerar_bpmn(atual)        # "Organizar": o mesmo fluxo, redesenhado pelo SISCO
        except Exception:
            organized=None                     # rascunho incompleto demais para redesenhar
        return {'version':published['version'],'published':published.get('bpmn') or gerar_bpmn(published['flow']),
                'draft':(draft.get('bpmn') or gerar_bpmn(atual)) if draft else None,'organized':organized}

    def simulate_flow(self, flow):
        """Compara o fluxo publicado com outro fluxo em todas as ocorrências (nada é gravado)."""
        published=self.config()
        return self._compare(published,{**published,'fluxo':self._checked_flow(flow)})

    @staticmethod
    def _rule_values(body, current):
        """Parâmetros das regras: valida os enviados; os ausentes ficam como estão publicados."""
        limits={'sla_hours':(1,168,'Limite do monitoramento entre 1 e 168 horas.'),'followup_hour':(0,23,'Hora da cobrança entre 0 e 23.'),
                'ug_occurrence_days':(1,730,'Janela das ocorrências das UGs entre 1 e 730 dias.')}
        values={}
        for key,(low,high,message) in limits.items():
            value=body.get(key,current.get(key,RULE_DEFAULTS[key]))
            if type(value) is not int or not low<=value<=high: raise ValueError(message)
            values[key]=value
        targets=body.get('phase_targets',current.get('phase_targets',RULE_DEFAULTS['phase_targets']))
        if not isinstance(targets,dict) or set(targets)-set(RULE_DEFAULTS['phase_targets']): raise ValueError('Metas de fase inválidas.')
        values['phase_targets']={**RULE_DEFAULTS['phase_targets'],**targets}
        if any(type(v) is not int or not 1<=v<=365 for v in values['phase_targets'].values()): raise ValueError('Metas de fase entre 1 e 365 dias.')
        return values

    def save_config(self, body, note='Configuração local atualizada', dry_run=False):
        required={'version','flows','routes','class_routes','signature','document_days','reminder_days'}
        if not isinstance(body,dict) or not required<=set(body) or set(body)-required-{'flow_types',*RULE_DEFAULTS}: raise ValueError('Configuração incompleta ou campo não permitido.')
        if type(body['version']) is not int: raise ValueError('Versão de configuração inválida.')
        for field in ('document_days','reminder_days'):
            if type(body[field]) is not int or not 1<=body[field]<=365: raise ValueError('Informe prazos entre 1 e 365 dias.')
        rule_values=self._rule_values(body,self.config())
        if not isinstance(body['signature'],str) or len(body['signature'])>1000: raise ValueError('Assinatura inválida.')
        flow_types=body.get('flow_types',copy.deepcopy(DEFAULT_FLOW_TYPES))
        if not isinstance(flow_types,list) or not 1<=len(flow_types)<=30: raise ValueError('Informe entre 1 e 30 tipos de fluxo.')
        types={}
        for t in flow_types:
            if not isinstance(t,dict) or set(t)!={'name','followup','coordinator','recipient'}: raise ValueError('Tipo de fluxo inválido.')
            if not isinstance(t['name'],str) or not _normalize(t['name']) or len(t['name'].strip())>40: raise ValueError('Informe o nome do tipo de fluxo (até 40 caracteres).')
            if _normalize(t['name']) in types: raise ValueError(f'Tipo de fluxo repetido: {t["name"].strip()}.')
            if type(t['followup']) is not bool or type(t['coordinator']) is not bool or t['recipient'] not in RECIPIENT_MODES: raise ValueError('Opções do tipo de fluxo inválidas.')
            types[_normalize(t['name'])]={**t,'name':t['name'].strip()}
        if not isinstance(body['flows'],list) or not 1<=len(body['flows'])<=50: raise ValueError('Informe entre 1 e 50 classificações.')
        names,flows=set(),[]
        for f in body['flows']:
            # coordinator/followup/recipient vêm do tipo de fluxo; aceitos na entrada só por compatibilidade e ignorados.
            if not isinstance(f,dict) or not {'classification','flow'}<=set(f) or set(f)-{'classification','flow','coordinator','followup','recipient'}: raise ValueError('Regra de fluxo inválida.')
            if not isinstance(f['classification'],str) or not isinstance(f['flow'],str): raise ValueError('Nome de classificação inválido.')
            name=_normalize(f['classification'])
            if not name or name in names or len(f['classification'].strip())>100: raise ValueError('Classificação vazia ou repetida.')
            names.add(name)
            if _normalize(f['flow']) not in types: raise ValueError(f'Tipo de fluxo inexistente para {f["classification"].strip()}: {f["flow"]}.')
            flows.append({'classification':f['classification'].strip(),'flow':types[_normalize(f['flow'])]['name']})
        routes={}
        for field,keys,optional in (('routes',{'coordination','to','cc','bcc'},set()),('class_routes',{'classification','cc','bcc'},{'to'})):
            if not isinstance(body[field],list) or len(body[field])>50: raise ValueError('Roteamento inválido.')
            for r in body[field]:
                if not isinstance(r,dict) or not keys<=set(r) or set(r)-keys-optional or any(not isinstance(v,str) or len(v)>2000 for v in r.values()): raise ValueError('Roteamento inválido.')
            routes[field]=[{'to':'',**r} for r in body[field]] if optional else body[field]
        class_to={_normalize(r['classification']):r['to'].strip() for r in routes['class_routes']}
        for f in flows:
            if types[_normalize(f['flow'])]['recipient']=='classificacao' and not _emails(class_to.get(_normalize(f['classification']),'')):
                raise ValueError(f'O fluxo de {f["classification"]} envia para o e-mail da classificação. Informe esse e-mail (Para) em E-mails por classificação.')
        clean={'version':body['version'],'flow_types':list(types.values()),'flows':flows,'routes':routes['routes'],'class_routes':routes['class_routes'],
               'signature':body['signature'],'document_days':body['document_days'],'reminder_days':body['reminder_days'],**rule_values}
        if dry_run:
            return clean
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.config()
            if body['version']!=current['version']: raise Conflict('A configuração mudou. Recarregue antes de salvar.')
            # Histórico de versões publicadas (as 20 mais recentes), para voltar a uma anterior.
            row=db.execute("SELECT value FROM metadata WHERE key='configuration_versions'").fetchone()
            versions=(json.loads(row[0]) if row else [])[-19:]
            versions.append({'version':current['version'],'at':now(),'note':note,'rules':{k:current.get(k) for k in RULE_KEYS}})
            self.store._meta(db,'configuration_versions',versions)
            self.store._meta(db,'configuration',{**clean,'version':body['version']+1})
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)',(now(),note,dumps(clean)))
        return self.config()

    # ---------- Regras do fluxo: rascunho → testar → publicar ----------
    def _published_body(self, rules=None):
        current=self.config()
        body={k:current[k] for k in ('version','routes','class_routes','signature')}
        body.update({k:copy.deepcopy(current[k]) for k in RULE_KEYS})
        body['flows']=[{'classification':f['classification'],'flow':f['flow']} for f in current['flows']]
        if rules:
            if not isinstance(rules,dict) or set(rules)-set(RULE_KEYS): raise ValueError('Regra desconhecida no rascunho.')
            body.update(copy.deepcopy(rules))
        return body

    def rules(self):
        with self.store.connect() as db:
            draft=db.execute("SELECT value FROM metadata WHERE key='rules_draft'").fetchone()
            versions=db.execute("SELECT value FROM metadata WHERE key='configuration_versions'").fetchone()
        return {'draft':json.loads(draft[0]) if draft else None,
                'versions':[{k:v[k] for k in ('version','at','note')} for v in (json.loads(versions[0]) if versions else [])][::-1]}

    def save_draft(self, body):
        if not isinstance(body,dict) or not isinstance(body.get('rules'),dict): raise ValueError('Rascunho inválido.')
        rules=self.validate_rules(body['rules'])
        draft={'base_version':self.config()['version'],'updated_at':now(),'rules':rules}
        with self.store.lock, self.store.connect() as db:
            self.store._meta(db,'rules_draft',draft)
        return {'draft':draft,'simulation':self.simulate(rules)}

    def validate_rules(self, rules):
        """Mesma validação da publicação (regras + e-mails publicados), sem gravar nada."""
        clean=self.save_config(self._published_body(rules),dry_run=True)
        return {k:clean[k] for k in RULE_KEYS}

    def simulate(self, rules):
        """Compara, ocorrência por ocorrência, a regra publicada com a do rascunho (nada é gravado)."""
        published=self.config()
        draft_config={**published,**self.validate_rules(rules)}
        draft_config=normalize_config(draft_config)
        return self._compare(published,draft_config)

    def _compare(self, published, draft_config):
        before_counts,after_counts,changed={},{},[]
        lanes={'before':{},'after':{}}
        flow_of=lambda config,item:next((f['flow'] for f in config['flows'] if _normalize(f['classification'])==_normalize(item['values'].get('classification'))),'')
        clock=parse(now())
        for item in self.store.list_items():
            before=describe(item,published,clock); after=describe(item,draft_config,clock)
            before_counts[before['phase']]=before_counts.get(before['phase'],0)+1
            after_counts[after['phase']]=after_counts.get(after['phase'],0)+1
            # Por tipo de fluxo e fase, para cada bloco do mapa mostrar "agora → com o rascunho".
            for side,config,wf in (('before',published,before),('after',draft_config,after)):
                key=f"{flow_of(config,item)}|{wf['phase']}"; lanes[side][key]=lanes[side].get(key,0)+1
            if (before['phase'],before['conclusion'],before['due_at'],before['overdue'])!=(after['phase'],after['conclusion'],after['due_at'],after['overdue']):
                changed.append({'id':item['id'],'legacy_id':item.get('legacy_id',''),'forms_id':item.get('forms_id',''),'plant':item['values'].get('plant',''),
                                'before':{k:before[k] for k in ('phase','conclusion','due_at','overdue')},'after':{k:after[k] for k in ('phase','conclusion','due_at','overdue')}})
        return {'before':before_counts,'after':after_counts,'lanes':lanes,'changed_total':len(changed),'changed':changed[:100]}

    def publish_draft(self, body):
        draft=self.rules()['draft']
        if not draft: raise ValueError('Não há rascunho para publicar.')
        if not isinstance(body,dict) or type(body.get('version')) is not int: raise ValueError('Versão inválida.')
        if body['version']!=self.config()['version'] or draft['base_version']!=body['version']:
            raise Conflict('As regras publicadas mudaram depois do rascunho. Confira o rascunho antes de publicar.')
        published=self.save_config(self._published_body(draft['rules']),note=str(body.get('note') or 'Regras do fluxo publicadas')[:200])
        with self.store.lock, self.store.connect() as db:
            db.execute("DELETE FROM metadata WHERE key='rules_draft'")
        return published

    def discard_draft(self):
        with self.store.lock, self.store.connect() as db:
            db.execute("DELETE FROM metadata WHERE key='rules_draft'")
        return self.rules()

    def restore_version(self, body):
        """Coloca as regras de uma versão anterior no rascunho (para testar e publicar de novo)."""
        with self.store.connect() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='configuration_versions'").fetchone()
        versions=json.loads(row[0]) if row else []
        found=next((v for v in versions if v['version']==body.get('version')),None) if isinstance(body,dict) else None
        if not found: raise ValueError('Versão não encontrada no histórico.')
        rules={k:v for k,v in found['rules'].items() if v is not None}
        return self.save_draft({'rules':rules})

    # ---------- Metas dos KPIs (página KPIs) ----------
    def kpi_targets(self):
        with self.store.connect() as db:
            saved=db.execute("SELECT value FROM metadata WHERE key='kpi_targets'").fetchone()
        saved=json.loads(saved[0]) if saved else {}
        return {'version':saved.get('version',0),'targets':{**KPI_DEFAULTS,**saved.get('targets',{})}}

    def save_kpi_targets(self, body):
        if not isinstance(body,dict) or set(body)!={'version','targets'} or type(body['version']) is not int: raise ValueError('Pedido de metas inválido.')
        targets=validar_metas(body['targets'])
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if body['version']!=self.kpi_targets()['version']: raise Conflict('As metas mudaram. Recarregue antes de salvar.')
            self.store._meta(db,'kpi_targets',{'version':body['version']+1,'targets':targets})
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)',(now(),'Metas dos KPIs atualizadas',dumps(targets)))
        return self.kpi_targets()

    def analytics(self):
        with self.store.connect() as db:
            saved=db.execute("SELECT value FROM metadata WHERE key='analytics'").fetchone()
        return {'powerbi_url':'','charts_url':'','version':0,**(json.loads(saved[0]) if saved else {})}

    @staticmethod
    def _powerbi_link(url):
        if not isinstance(url,str) or len(url)>2000: raise ValueError('Link do Power BI inválido.')
        url=url.strip()
        if url:
            parts=urlsplit(url)
            if parts.scheme!='https' or parts.netloc.lower()!='app.powerbi.com' or parts.path not in ('/reportEmbed','/view') or not parts.query:
                raise ValueError('Use o link de Inserir → Site ou portal do Power BI (https://app.powerbi.com/reportEmbed?...).')
        return url

    def save_analytics(self, body):
        # Somente links de inserção do Power BI (painel do Início e gráficos de Ocorrências); os relatórios
        # são exibidos pelo navegador, sem passar dados pelo servidor. Campo omitido mantém o valor salvo.
        if not isinstance(body,dict) or 'version' not in body or set(body)-{'version','powerbi_url','charts_url'} or len(body)<2 or type(body['version']) is not int: raise ValueError('Pedido de painel inválido.')
        links={field:self._powerbi_link(body[field]) for field in ('powerbi_url','charts_url') if field in body}
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.analytics()
            if body['version']!=current['version']: raise Conflict('O link do painel mudou. Recarregue antes de salvar.')
            self.store._meta(db,'analytics',{'powerbi_url':current['powerbi_url'],'charts_url':current['charts_url'],**links,'version':body['version']+1})
            db.execute('INSERT INTO history(item_id,at,action,details) VALUES (0,?,?,?)',(now(),'Painel Power BI atualizado',dumps(links)))
        return self.analytics()

    def _authorized(self,item_id,version):
        item=self.store.get(item_id)
        if type(version) is not int or item['version']!=version: raise Conflict('A ocorrência mudou. Atualize e confira antes de agir.')
        if not item['reviewed']: raise ValueError('Marque Revisado e salve para autorizar as tratativas.')
        return item

    def _record(self, db, item_id, changes, action, details):
        row=db.execute('SELECT edits FROM items WHERE id=?',(item_id,)).fetchone()
        edits={**json.loads(row[0]),**changes}
        db.execute('UPDATE items SET edits=?,version=version+1,updated_at=? WHERE id=?',(dumps(edits),now(),item_id))
        db.execute('INSERT INTO history(item_id,at,action,details) VALUES (?,?,?,?)',(item_id,now(),action,dumps(details)))

    def documents(self,item_id):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,filename,created_at,kind FROM documents WHERE item_id=? ORDER BY created_at DESC,rowid DESC',(item_id,))]

    def document(self,doc_id):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM documents WHERE id=?',(doc_id,)).fetchone()
        if row is None: raise KeyError(doc_id)
        return dict(row)

    def create_document(self,item_id,body):
        with self.store.lock:
            item=self._authorized(item_id,body.get('version')); v=item['values']
            if body.get('content'):
                try: data=base64.b64decode(body['content'],validate=True)
                except Exception: raise ValueError('Conteúdo do anexo inválido.') from None
                kind='Revisão anexada'
            else:
                if not self.template.is_file(): raise ValueError('Modelo Word não localizado.')
                if not all(v.get(k) for k in ('plant','equipment','start_date','start_time')): raise ValueError('Preencha usina, equipamento, data e hora inicial antes de gerar Word.')
                replacements={'{{ID_OCORRENCIA}}':item.get('legacy_id') or 'TESTE-'+str(item_id),'{{ANO}}':v['start_date'][:4],
                    '{{USINA}}':v['plant'],'{{EQUIPAMENTO}}':v['equipment'],'{{DATA_OBSERVACAO}}':datetime.strptime(v['start_date'],'%Y-%m-%d').strftime('%d/%m/%Y'), '{{HORA_OBSERVACAO}}':' '+v['start_time']}
                out=BytesIO()
                with ZipFile(self.template) as source, ZipFile(out,'w') as target:
                    for info in source.infolist():
                        data=source.read(info.filename)
                        if info.filename.startswith('word/') and info.filename.endswith('.xml'):
                            data=_replace_xml(data,replacements)
                        target.writestr(info,data)
                data=out.getvalue(); kind='Gerado pelo modelo'
            validate_docx(data)
            doc_id=uuid.uuid4().hex
            filename=f'Registro_TESTE_{item.get("legacy_id") or item_id}_{doc_id[:8]}.docx'
            path=self.directory/filename; path.write_bytes(data)
            with self.store.connect() as db:
                db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',(doc_id,item_id,filename,str(path),now(),kind))
                self._record(db,item_id,{'word_status':'Word gerado'},kind,{'document_id':doc_id})
            return {'document':self.document(doc_id),'item':self.store.get(item_id)}

    def enrich(self,item,config=None):
        item['workflow']=describe(item,config or self.config(),parse(now()))
        item['documents']=self.documents(item['id'])
        with self.store.connect() as db:
            item['deliveries']=[dict(r) for r in db.execute('SELECT id,kind,status,created_at,sent_at,error FROM deliveries WHERE item_id=? ORDER BY created_at DESC LIMIT 15',(item['id'],))]
        return item

    def preview(self,item_id,body,port):
        item=self._authorized(item_id,body.get('version')); config=self.config(); v=item['values']
        kind=body.get('kind')
        clock=parse(now())
        flow=next((f for f in config['flows'] if _normalize(f['classification'])==_normalize(v.get('classification'))),None)
        if not flow: raise ValueError('Configure o fluxo da classificação antes de enviar.')
        titles={'request':'Solicitação de atualização da ocorrência','reminder':'Solicitação de retorno sobre a ocorrência',
                'document':'Registro de ocorrência para preenchimento','document_reminder':'Retorno do registro de ocorrência pendente'}
        if kind not in titles: raise ValueError('Tipo de mensagem inválido.')
        document=self.documents(item_id)[0] if kind.startswith('document') and self.documents(item_id) else None
        sent_payload=None
        if kind=='document_reminder':
            with self.store.connect() as db:
                sent=db.execute("SELECT payload FROM deliveries WHERE item_id=? AND kind IN ('document','document_reminder') AND status='SUBMETIDO_OUTLOOK' ORDER BY created_at LIMIT 1",(item_id,)).fetchone()
            if sent:
                sent_payload=json.loads(sent[0]); document=self.document(sent_payload['document_id'])
                current_hash=hashlib.sha256(Path(document['path']).read_bytes()).hexdigest()
                if current_hash!=sent_payload.get('attachment_sha256'): raise ValueError('O anexo arquivado foi alterado. Confira o envio original antes de cobrar.')
            elif document and document['kind']!='Revisão anexada':
                raise ValueError('Anexe o registro já enviado pelo sistema atual para cobrar o mesmo documento.')
        if kind.startswith('document') and not document: raise ValueError('Crie o Word ou anexe a versão que será enviada.')
        if kind.startswith('document') and not flow['coordinator']: raise ValueError('Esta classificação não exige registro para a coordenação. Confira a configuração do fluxo.')
        if kind=='document' and 'enviado' in _normalize(v.get('document_sent')): raise ValueError('O registro já consta como enviado. Use cobrança documental.')
        if kind=='request' and ('enviado' in _normalize(v.get('forms_sent')) or 'fila' in _normalize(v.get('forms_sent'))): raise ValueError('Já existe solicitação ou fila na origem. Consulte o retorno ou prepare uma cobrança.')
        if kind in ('request','reminder') and full_end(v): raise ValueError('O encerramento já está preenchido. Confira a etapa documental.')
        if kind in ('request','reminder') and not flow['followup']: raise ValueError('Esta classificação não possui acompanhamento de status.')
        if kind=='request':
            if _normalize(v.get('status'))!='aberta' or derive(item,config,clock)['sla']!='Analisar': raise ValueError('A solicitação inicial exige ocorrência aberta há mais de 24 horas.')
            if v.get('forms_sent') or v.get('forms_sent_at'): raise ValueError('Há informação anterior de envio. Confira a origem antes de criar outra solicitação.')
        if kind=='reminder':
            sent_at=parse(v.get('forms_sent_at')); next_at=parse(v.get('forms_due_at')); forecast=parse(v.get('forecast_date'))
            if _normalize(v.get('forms_sent'))!='enviado' or not sent_at: raise ValueError('A cobrança exige solicitação inicial com data de envio válida.')
            if any(w in _normalize(v.get('maintenance_status')) for w in ('finaliz','encerr','conclu')): raise ValueError('O responsável já informou finalização. Complete os dados de encerramento.')
            due=forms_deadline(v,config)
            if clock<due: raise ValueError('A próxima cobrança de status está prevista para '+due.strftime('%d/%m/%Y %H:%M')+'.')
        raw_return=next((value for key,value in item.get('raw_fields',{}).get('controle',{}).items() if key.startswith('AD ·')),None)
        if kind=='document_reminder' and (v.get('document_return_date') or raw_return not in (None,'')):
            raise ValueError('Há informação de retorno do registro. Confira o recebimento antes de cobrar.')
        if kind=='document_reminder':
            if 'enviado' not in _normalize(v.get('document_sent')): raise ValueError('A cobrança documental exige envio inicial registrado.')
            deadline=parse(sent_payload['document_deadline']) if sent_payload else document_deadline(v)
            next_at=parse(v.get('document_next_date'))
            if not deadline or not next_at: raise ValueError('Confira o prazo original e a próxima cobrança na origem antes de reenviar.')
            if clock.date()<=deadline.date() or clock.date()<next_at.date(): raise ValueError('A cobrança documental ainda não venceu. Próxima cobrança: '+next_at.strftime('%d/%m/%Y')+'.')
        routing=routing_for(config,v,kind)
        if routing['mode']=='classificacao' and not _emails(routing['to']):
            # Como no SISCO oficial: sem e-mail da classificação, não cai para a coordenação.
            raise ValueError('O fluxo desta classificação envia para o e-mail da classificação, que não está configurado. Informe-o em Configurações.')
        heading=f'{titles[kind]} · ID {item.get("legacy_id") or "Forms "+item.get("forms_id",str(item_id))} · {v.get("plant","")}'
        deadline=(sent_payload['document_deadline'] if sent_payload else document_deadline(v).date().isoformat()) if kind=='document_reminder' else (clock+timedelta(days=config['document_days'])).date().isoformat() if kind=='document' else ''
        deadline_text=datetime.fromisoformat(deadline).strftime('%d/%m/%Y') if deadline else 'não informado'
        content=['AMBIENTE DE TESTES SISCO — mensagem enviada só para o e-mail de teste.',heading,'',f'Usina: {v.get("plant","")}',f'Equipamento: {v.get("equipment","")}',f'Classificação: {v.get("classification","")}',f'Início: {v.get("start_date","")} {v.get("start_time","")}', '',v.get('description','')]
        if kind.startswith('document'):
            content+=['',f'Prazo para devolução do registro: {deadline_text}.','Preencha e confira as partes operacionais e técnicas do documento anexo.','O reenvio conserva o prazo original.']
        else:
            content+=['','Informe o status atual, a previsão de encerramento ou a data e hora finais.','Nesta fase de testes, registre o retorno no SISCO; nenhum formulário externo é acionado.']
        content+=['',f'Abrir no computador do SISCO: http://127.0.0.1:{port}/?item={item_id}&view=followup','',config['signature']]
        text='\n'.join(content)
        payload={'item_id':item_id,'version':item['version'],'config_version':config['version'],'kind':kind,'to':TEST_EMAIL,'cc':'','bcc':'','subject':'[TESTE SISCO] '+heading,
                 'body':text,'document_id':document['id'] if document else None,'document_deadline':deadline,'original_routing':routing}
        payload['document_reminder_days']=(sent_payload.get('document_reminder_days',7) if sent_payload else 7) if kind=='document_reminder' else config['reminder_days']
        if sent_payload: payload['subject']=sent_payload['subject']
        if document:
            payload['attachment_sha256']=hashlib.sha256(Path(self.document(document['id'])['path']).read_bytes()).hexdigest()
        token=hashlib.sha256(dumps(payload).encode()).hexdigest()
        return {**payload,'preview_token':token,'attachment':document['filename'] if document else ''}

    def send_test(self,item_id,body,port,sender=None):
        # Lock spans authorization, persistent reservation and Outlook submission.
        # UI double clicks and repeated requests cannot submit the same preview twice.
        with self.store.lock:
            preview=self.preview(item_id,body,port)
            if body.get('preview_token')!=preview['preview_token']: raise Conflict('A prévia mudou. Abra e confira a mensagem novamente.')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                # Revalidate after obtaining the database write reservation too;
                # another local process may have edited this same record.
                self._authorized(item_id,body.get('version'))
                uncertain=db.execute("SELECT id FROM deliveries WHERE item_id=? AND status IN ('RESERVADO','ENVIO_INCERTO')",(item_id,)).fetchone()
                if uncertain: raise Conflict('Há envio com resultado incerto. Confira o Outlook antes de qualquer nova tentativa.')
                prior=db.execute('SELECT id FROM deliveries WHERE id=?',(preview['preview_token'],)).fetchone()
                if prior: raise Conflict('Esta mensagem já foi processada. Confira o histórico.')
                db.execute('INSERT INTO deliveries(id,item_id,kind,status,created_at,payload) VALUES (?,?,?,?,?,?)',(preview['preview_token'],item_id,preview['kind'],'RESERVADO',now(),dumps(preview)))
            try:
                if sender: sender(preview)
                else:
                    attachment=self.document(preview['document_id'])['path'] if preview['document_id'] else ''
                    request={'to':TEST_EMAIL,'subject':preview['subject'],'body':preview['body'],'attachment':attachment}
                    proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(Path(__file__).parent/'enviar_teste.ps1')],
                        input=dumps(request),encoding='utf-8',capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if 'SISCO_NOT_SUBMITTED' in proc.stdout: raise NotSubmitted('O Outlook recusou antes do envio. Confira se a conta do e-mail de teste está no Outlook clássico e o anexo.')
                    if proc.returncode!=0 or 'SISCO_SUBMETIDO' not in proc.stdout: raise RuntimeError('O Outlook não confirmou a submissão. Confira a Caixa de Saída e os Itens Enviados.')
                stamp=now(); dt=parse(stamp); config=self.config(); kind=preview['kind']
                changes={}
                if kind=='request': changes={'forms_sent':'Enviado','forms_sent_at':stamp,'forms_due_at':(dt+timedelta(days=config['reminder_days'])).replace(hour=config['followup_hour'],minute=0,second=0).isoformat()}
                elif kind=='reminder': changes={'forms_due_at':(dt+timedelta(days=config['reminder_days'])).replace(hour=config['followup_hour'],minute=0,second=0).isoformat()}
                elif kind=='document': changes={'document_sent':f'Email enviado ({stamp})','document_deadline_date':preview['document_deadline'],'document_next_date':(dt+timedelta(days=config['document_days']+1)).date().isoformat()}
                elif kind=='document_reminder': changes={'document_next_date':(dt+timedelta(days=preview['document_reminder_days'])).date().isoformat(),'document_deadline_date':preview['document_deadline']}
                with self.store.connect() as db:
                    db.execute("UPDATE deliveries SET status='SUBMETIDO_OUTLOOK',sent_at=? WHERE id=?",(stamp,preview['preview_token']))
                    self._record(db,item_id,changes,'Mensagem de teste submetida ao Outlook',{'kind':kind,'to':TEST_EMAIL,'delivery_id':preview['preview_token'],'prior_document_deadline':self.store.get(item_id)['values'].get('document_deadline_date')})
            except Exception as exc:
                with self.store.connect() as db:
                    db.execute('UPDATE deliveries SET status=?,error=? WHERE id=?',('ERRO_ANTES_ENVIO' if isinstance(exc,NotSubmitted) else 'ENVIO_INCERTO',str(exc),preview['preview_token']))
                    if isinstance(exc,NotSubmitted): self._record(db,item_id,{},'Tentativa recusada antes do envio',{'error':str(exc)})
                if isinstance(exc,NotSubmitted): raise ValueError(str(exc)+' Nenhuma mensagem submetida; corrija o problema e gere outra prévia.') from exc
                raise ValueError('Resultado do envio incerto. Não houve repetição automática. Confira o Outlook; os prazos não foram avançados.') from exc
        return self.store.get(item_id)
