"""Responses transport and credential tests: no real network, model or user keys."""
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
from slotcapture import vision_api as v
from slotcapture.common import read


def complete_draft():
    return dict(schema_version='slot-rule-extraction/v1',name='测试',symbols=[],features=[],
        base_game=dict(reels=None,rows=None,payline_count=None,win_method='',bet_basis='',wild_rules='',scatter_rules='',rules=[],pending=[],source_images=[]),
        free_game=dict(trigger='',initial_spins=None,extra_spins='',multiplier_rules='',retrigger_rules='',settlement='',end_condition='',pending=[],source_images=[]),
        payouts=[],capture_checklist=[],unknowns=[],notes='')


class VisionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Path(self.temp.name)
        p=patch.dict(v.os.environ,ANTHROPIC_API_KEY='test-only-api-key');p.start();self.addCleanup(p.stop)
        p=patch.object(v,'registry_key',return_value='');p.start();self.addCleanup(p.stop)
        self.settings=dict(base_url='https://example.invalid/openai',selection='automatic')

    def response(self,data):return io.BytesIO(json.dumps(data).encode())

    def test_inventory_is_required_and_fast_selection_only_uses_returned_ids(self):
        v.save(self.store,self.settings)
        with patch.object(v.urllib.request,'urlopen',return_value=self.response({'data':[{'id':'gpt-5.6'},{'id':'gpt-4.1-nano'}]})) as http:
            result=v.models(self.store)
        self.assertEqual(result['settings']['model'],'gpt-4.1-nano')
        self.assertEqual(http.call_args.args[0].full_url,'https://example.invalid/openai/models')
        self.assertIsNone(http.call_args.args[0].data)
        self.assertIsNone(v.choose_model(['not-a-vision-model','gpt-4o-mini-tts']))

    def test_unknown_list_requires_user_selection_instead_of_inventing_support(self):
        v.save(self.store,self.settings)
        with patch.object(v.urllib.request,'urlopen',return_value=self.response({'data':[{'id':'unknown-fast'}]})):
            result=v.models(self.store)
        self.assertTrue(result['selection_required']);self.assertEqual(v.config(self.store)['model'],'')

    def test_key_is_encrypted_and_never_returned_or_written_in_configuration(self):
        codec=lambda value,decode=False:value[7:] if decode else b'cipher:'+value
        with patch.object(v,'protect',side_effect=codec):
            result=v.save(self.store,self.settings,'my-test-secret-value')
            self.assertEqual(v.key_for(self.store,v.config(self.store)),'my-test-secret-value')
            self.assertTrue(result['key_present'])
        self.assertNotIn('my-test-secret-value',json.dumps(result))
        self.assertNotIn('my-test-secret-value',(self.store/'vision-settings.json').read_text())
        self.assertFalse((self.store/'private'/'vision-key.bin').read_bytes().startswith(b'my-test'))
        with self.assertRaises(ValueError):v.save(self.store,self.settings,'crs_')

    def test_responses_images_schema_and_minimum_reasoning_are_sent_only_on_recognize(self):
        v.save(self.store,dict(self.settings,selection='manual',model='gpt-4.1-nano'))
        image=self.store/'rules.png';Image.new('RGB',(2,2),'white').save(image)
        draft=complete_draft()
        result=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(draft))])])
        with patch.object(v.urllib.request,'urlopen',return_value=self.response(result)) as http:
            answer=v.recognize([image],self.store,'测试说明')
        req=http.call_args.args[0];payload=json.loads(req.data)
        self.assertEqual(req.full_url,'https://example.invalid/openai/responses')
        self.assertNotIn('reasoning',payload);self.assertFalse(payload['store'])
        self.assertEqual(payload['text']['format']['type'],'json_schema')
        self.assertTrue(payload['input'][0]['content'][1]['image_url'].startswith('data:image/png;base64,'))
        self.assertTrue(answer['requires_confirmation'])
        manifest=read(Path(answer['job'])/'manifest.json')
        self.assertNotIn('test-only-api-key',json.dumps(manifest))
        self.assertFalse((self.store/'machines').exists())

    def test_windows_secret_roundtrip(self):
        if v.os.name!='nt':self.skipTest('Windows DPAPI only')
        encoded=v.protect(b'local-test-value')
        self.assertNotIn(b'local-test-value',encoded)
        self.assertEqual(v.protect(encoded,True),b'local-test-value')

    def test_relay_response_errors_and_incomplete_results_never_save_draft(self):
        v.save(self.store,dict(self.settings,selection='manual',model='gpt-5-mini'))
        image=self.store/'rules.png';Image.new('RGB',(2,2),'white').save(image)
        with patch.object(v.urllib.request,'urlopen',return_value=self.response(dict(status='incomplete',output=[]))) as http:
            with self.assertRaises(ValueError):v.recognize([image],self.store)
        self.assertEqual(json.loads(http.call_args.args[0].data)['reasoning'],{'effort':'medium'})
        self.assertFalse((self.store/'vision-jobs').exists())

    def test_nonstream_and_relay_sse_completed_response(self):
        body={'status':'completed','output':[]}
        raw=('event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':body})+'\n\n').encode()
        self.assertEqual(v.decode_response(raw,'text/event-stream'),body)
        self.assertEqual(v.decode_response(json.dumps(body).encode()),body)
        for raw in (b'',b'<html>gateway</html>',b'data: [DONE]\n\n'):
            with self.assertRaises(ValueError):v.decode_response(raw)

    def test_preset_schema_covers_next_stage_without_unknown_numbers_becoming_zero(self):
        from slotcapture.rule_prompt import DRAFT_SCHEMA, PROMPT
        self.assertEqual(DRAFT_SCHEMA['properties']['base_game']['properties']['payline_count']['type'],['integer','null'])
        self.assertIn('capture_checklist',DRAFT_SCHEMA['properties'])
        self.assertIn('3连、4连、5连',PROMPT)
        self.assertIn('不猜Symbol协议ID',PROMPT)
        self.assertNotIn('protocol_id',DRAFT_SCHEMA['properties']['symbols']['items']['properties'])

    def test_luna_medium_saved_request_uses_responses_without_inventory(self):
        v.save(self.store,dict(self.settings,selection='manual',model='gpt-6-luna',reasoning_effort='medium'))
        image=self.store/'rules.png';Image.new('RGB',(2,2),'white').save(image)
        result=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(complete_draft()))])])
        with patch.object(v.urllib.request,'urlopen',return_value=self.response(result)) as http:
            answer=v.recognize([image],self.store)
        self.assertEqual(http.call_count,1)
        payload=json.loads(http.call_args.args[0].data)
        self.assertEqual(payload['model'],'gpt-6-luna');self.assertEqual(payload['reasoning'],{'effort':'medium'})
        self.assertTrue(http.call_args.args[0].full_url.endswith('/responses'))
        self.assertEqual(read(Path(answer['job'])/'manifest.json')['prompt_version'],'slot-rule-extraction/v1')

    def test_partial_or_wrong_typed_rule_packages_are_rejected(self):
        from slotcapture.rule_prompt import validate_draft
        validate_draft(complete_draft())
        for field,value in [('schema_version','unknown'),('payouts','not-a-list'),('base_game',{})]:
            draft=complete_draft();draft[field]=value
            with self.assertRaises(ValueError):validate_draft(draft)



if __name__=='__main__':unittest.main()
