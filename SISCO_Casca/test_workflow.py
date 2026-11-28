import base64
import copy
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile
from unittest.mock import patch
from core import Store, Conflict, TEST_EMAIL
from operations import Operations, NotSubmitted, _replace_xml
from workflow import derive, describe, DEFAULT_FLOWS, document_deadline
from test_core import snapshot

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store=Store(Path(self.temp.name)/'test.sqlite3')
        self.ops=Operations(self.store,Path(self.temp.name)/'missing.docx')
        data=snapshot();data['records'][0]['source_values'].update(plant='Usina A',equipment='UG01',coordination='Coordenação Norte',conclusion='Aguardando envio do Forms')
        self.store.ingest(data)
    def tearDown(self): self.temp.cleanup()
    def review(self):
        item=self.store.get(1)
        return self.store.update(1,{'version':item['version'],'reviewed':True})
    def preview(self,kind='request'):
        return self.ops.preview(1,{'version':self.store.get(1)['version'],'kind':kind},8767)
    def source(self, **values):
        data=snapshot();data['records'][0]['source_values'].update(plant='Usina A',equipment='UG01',**values);self.store.ingest(data)
    def attach(self, text='original'):
        data=BytesIO()
        with ZipFile(data,'w') as z:
            z.writestr('[Content_Types].xml','<Types/>')
            z.writestr('word/document.xml','<document>'+text+'</document>')
        return self.ops.create_document(1,{'version':self.store.get(1)['version'],'content':base64.b64encode(data.getvalue()).decode()})['document']
    def test_refusal_before_send_can_retry_with_fresh_preview(self):
        self.review();p=self.preview()
        def refuse(_): raise NotSubmitted('Conta não disponível')
        with self.assertRaisesRegex(ValueError,'Nenhuma mensagem submetida'): self.ops.send_test(1,p,8767,refuse)
        self.assertNotIn('forms_sent_at',self.store.get(1)['values'])
        fresh=self.preview();self.assertNotEqual(fresh['preview_token'],p['preview_token'])
        self.assertEqual(self.ops.send_test(1,fresh,8767,lambda _:None)['values']['forms_sent'],'Enviado')
    def test_status_reminder_respects_forecast_hour_and_later_next_date(self):
        self.source(forms_sent='Enviado',forms_sent_at='2026-10-10T09:00',maintenance_status='Em Aberto',forecast_date='2026-10-20',forms_due_at='2026-10-19T09:00')
        self.review()
        with patch('operations.now',return_value='2026-10-21T08:59:00-03:00'):
            with self.assertRaises(ValueError):self.preview('reminder')
        self.assertEqual(derive(self.store.get(1),self.ops.config(),datetime(2026,10,21,8,59))['conclusion'],'Aguardando prazo informado')
        with patch('operations.now',return_value='2026-10-21T09:00:00-03:00'):self.preview('reminder')
        self.source(forms_sent='Enviado',forms_sent_at='2026-10-10T09:00',forecast_date='2026-10-20',forms_due_at='2026-10-25T09:00')
        with patch('operations.now',return_value='2026-10-22T09:00:00-03:00'):
            with self.assertRaises(ValueError):self.preview('reminder')
    def test_document_reminder_keeps_original_attachment_deadline_subject_and_interval(self):
        self.source(end_date='2026-10-12',end_time='10:00');self.review();original=self.attach()
        with patch('operations.now',return_value='2026-10-19T09:00:00-03:00'):
            first=self.preview('document');self.ops.send_test(1,first,8767,lambda _:None)
        self.attach('revisao posterior')
        c={k:v for k,v in self.ops.config().items() if k in {'version','flows','routes','class_routes','signature','document_days','reminder_days'}}
        c['class_routes']=c['class_routes']+[{'classification':'TI','to':'suporte@example.com','cc':'','bcc':''}]
        c.update(document_days=30,reminder_days=30);self.ops.save_config(c)
        with patch('operations.now',return_value='2026-10-26T23:00:00-03:00'):
            with self.assertRaises(ValueError):self.preview('document_reminder')
        with patch('operations.now',return_value='2026-10-27T09:00:00-03:00'):
            reminder=self.preview('document_reminder')
            self.assertEqual(reminder['document_id'],original['id']);self.assertEqual(reminder['subject'],first['subject'])
            self.assertEqual(reminder['document_deadline'],'2026-10-26')
            item=self.ops.send_test(1,reminder,8767,lambda _:None)
            self.assertEqual(item['values']['document_next_date'],'2026-11-03')
    def test_historical_deadline_correction_and_nonempty_return_block(self):
        v={'document_sent':'Email enviado (01/10/2026 09:00)','document_deadline_date':'2026-10-15'}
        self.assertEqual(document_deadline(v).date().isoformat(),'2026-10-08')
        v['document_deadline_date']='2026-10-20';self.assertEqual(document_deadline(v).date().isoformat(),'2026-10-20')
        self.source(end_date='2026-10-12',end_time='10:00',document_sent='Email enviado (01/10/2026 09:00)',document_deadline_date='2026-10-15',document_next_date='2026-10-09')
        self.review();self.attach()
        with patch('operations.now',return_value='2026-10-19T09:00:00-03:00'):
            self.assertEqual(self.preview('document_reminder')['document_deadline'],'2026-10-08')
        item=self.store.get(1);item['raw_fields']={'controle':{'AD · Data de retorno':'recebido'}}
        with patch.object(self.store,'get',return_value=item):
            with self.assertRaisesRegex(ValueError,'informação de retorno'):self.preview('document_reminder')
    def test_excel_conclusion_is_visible_but_does_not_authorize_actions(self):
        item=self.store.get(1);wf=describe(item,self.ops.config())
        self.assertEqual(wf['conclusion'],'Aguardando envio do Forms')
        self.assertFalse(wf['can_act'])
        with self.assertRaises(ValueError): self.preview()
        with self.assertRaises(ValueError): self.ops.create_document(1,{'version':1})
        with self.assertRaises(ValueError): self.store.update(1,{'version':1,'values':{'forecast_date':'2026-10-30'}})
    def test_phase_aguardando_envio_reune_forms_e_registro(self):
        item=self.store.get(1)
        for conclusion,phase in [('Aguardando envio do Forms','sending'),('Forms em fila','sending'),('Aguardando envio do registro','sending'),
                                 ('Aguardando resposta do Forms','forms'),('Aguardando retorno do registro','documents'),('Encerrada','closed')]:
            item['source_values']={**item['source_values'],'conclusion':conclusion}
            wf=describe(item,self.ops.config())
            self.assertEqual(wf['phase'],phase,conclusion)
            self.assertIn(wf['category'],('forms','documents','closed'))  # a categoria (filtros, relatórios) não muda
    def test_threshold_end_and_document_priority(self):
        item=self.review();v=item['values'];v['start_date']='2026-10-15';v['start_time']='09:00'
        clock=datetime(2026,10,16,9)
        self.assertEqual(derive(item,self.ops.config(),clock)['conclusion'],'Em monitoramento 24h')
        v['start_time']='08:59';self.assertEqual(derive(item,self.ops.config(),clock)['conclusion'],'Aguardando envio do Forms')
        v.update(end_date='2026-10-16',end_time='10:00',maintenance_status='Em Aberto')
        self.assertEqual(derive(item,self.ops.config(),clock)['conclusion'],'Aguardando geração do registro')
        v['classification']='Distribuidora';self.assertEqual(derive(item,self.ops.config(),clock)['conclusion'],'Encerrada')
    def test_document_deadline_does_not_close_on_maintenance_completion(self):
        item=self.review();item['values'].update(end_date='2026-10-12',end_time='09:00',document_sent='Email enviado',document_deadline_date='2026-10-16',document_next_date='2026-10-17')
        self.assertEqual(derive(item,self.ops.config(),datetime(2026,10,16,23))['conclusion'],'Aguardando retorno do registro')
        self.assertEqual(derive(item,self.ops.config(),datetime(2026,10,17,0))['conclusion'],'Prazo do registro vencido')
        item['values']['document_return_date']='2026-10-17'
        self.assertEqual(derive(item,self.ops.config(),datetime(2026,10,17,1))['conclusion'],'Encerrada')
    def test_preview_routes_all_recipients_to_test_and_send_requires_current_preview(self):
        self.review();p=self.preview();self.assertEqual(p['to'],TEST_EMAIL);self.assertEqual(p['cc'],'');self.assertEqual(p['bcc'],'')
        self.assertTrue(p['subject'].startswith('[TESTE SISCO]'))
        called=[]
        with self.assertRaises(Conflict):self.ops.send_test(1,{**p,'preview_token':'forged'},8767,called.append)
        self.assertEqual(called,[])
        item=self.ops.send_test(1,p,8767,called.append)
        self.assertEqual(len(called),1);self.assertEqual(item['values']['forms_sent'],'Enviado')
        with self.assertRaises(Conflict):self.ops.send_test(1,p,8767,called.append)
        self.assertEqual(len(called),1)
    def test_unknown_submission_is_reserved_and_not_retried_or_advanced(self):
        self.review();p=self.preview();called=[]
        def fail(payload):called.append(payload);raise TimeoutError()
        with self.assertRaises(ValueError):self.ops.send_test(1,p,8767,fail)
        self.assertNotIn('forms_sent_at',self.store.get(1)['values'])
        with self.assertRaises(Conflict):self.ops.send_test(1,p,8767,fail)
        self.assertEqual(len(called),1)
    def test_config_version_validation_and_persistence(self):
        c={k:v for k,v in self.ops.config().items() if k in {'version','flows','routes','class_routes','signature','document_days','reminder_days'}}
        c['class_routes']=c['class_routes']+[{'classification':'TI','to':'suporte@example.com','cc':'','bcc':''}]
        c['document_days']=9;self.ops.save_config(c)
        self.assertEqual(Operations(self.store,'missing').config()['document_days'],9)
        with self.assertRaises(Conflict):self.ops.save_config(c)
        c['version']=1;c['test_email']='other@example.com'
        with self.assertRaises(ValueError):self.ops.save_config(c)
    def test_split_word_markers_and_escaped_text_preserve_xml(self):
        xml=b'<w:p><w:r><w:t>{{US</w:t></w:r><w:r><w:t>INA}} / {{ANO}}</w:t></w:r></w:p>'
        result=_replace_xml(xml,{'{{USINA}}':'A & B {{USINA}}','{{ANO}}':'2026'})
        self.assertIn(b'A &amp; B {{USINA}}',result)
        self.assertIn(b' / 2026',result)
        self.assertEqual(result.count(b'<w:r>'),2)
    def test_import_upgrade_does_not_flag_all_items_as_source_changes(self):
        data=snapshot();data['records'][0]['source_values'].pop('conclusion',None)
        store=Store(Path(self.temp.name)/'upgrade.sqlite3');store.ingest(data)
        data['records'][0]['source_values']['conclusion']='Encerrada';store.ingest(data)
        self.assertFalse(store.get(1)['source_changed']);self.assertEqual(store.get(1)['version'],1)

if __name__=='__main__':unittest.main()
