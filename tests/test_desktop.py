"""Desktop bridge regression, using temporary data and no live app/device/model."""
import base64
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from PIL import Image
from slotcapture.desktop import DesktopAPI
from slotcapture.cli import parser, run
from slotcapture.common import read, write, utc
from slotcapture.workflow import save_profile


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Path(self.temp.name);self.calls=[]
        self.profile=dict(id='mia',name='米亚的小酒馆',adapter='mia-bole-v1',capture={},
                          symbols=[dict(name='女郎',role='收集',size='1x1')],features=[],rules_confirmed=True)
        self.api=DesktopAPI(self.store,self.command)

    def command(self,args):
        self.calls.append(args)
        if args[0]=='start':return dict(state='STARTING')
        return run(parser().parse_args(args))

    def data(self,result):
        self.assertTrue(result['ok'],result);return result['data']

    def test_bootstrap_is_readonly_and_machine_edits_use_cli(self):
        self.assertEqual(self.data(self.api.bootstrap())['machines'],[])
        p=self.data(self.api.save_machine(self.profile))
        self.assertEqual(p['id'],'mia')
        self.assertEqual(list((self.store/'drafts').glob('*')),[])
        selected=self.data(self.api.bootstrap())['selected']
        self.assertEqual(selected['name'],'米亚的小酒馆')
        self.assertFalse(selected['first_spin_verified'])
        self.assertNotIn('start',[c[0] for c in self.calls])

    def test_connection_and_expiry_keep_formal_gate_closed(self):
        result=self.data(self.api.connect(self.profile,{}));root=Path(result['run'])
        self.assertEqual(read(root/'plan.json')['purpose'],'connection')
        write(root/'status.json',dict(state='COMPLETE',checked_utc=utc(),heartbeat_matched=True))
        d=self.data(self.api.poll())
        self.assertTrue(d['connection_ready']);self.assertEqual(d['purpose'],'connection')
        error=self.api.start_session(self.profile,'sampling',100)
        self.assertFalse(error['ok']);self.assertIn('首转',error['error']['message'])
        write(root/'status.json',dict(state='COMPLETE',checked_utc='2020-01-01T00:00:00+00:00',heartbeat_matched=True))
        self.assertFalse(self.data(self.api.poll())['connection_ready'])
        self.assertFalse(self.data(self.api.bootstrap())['connection_ready'])

    def test_first_spin_is_separate_and_exactly_one_paid(self):
        result=self.data(self.api.connect(self.profile,{}));root=Path(result['run'])
        write(root/'status.json',dict(state='COMPLETE',checked_utc=utc(),heartbeat_matched=True))
        self.data(self.api.poll())
        first=self.data(self.api.start_session(self.profile,'first_spin',100))
        plan=read(Path(first['run'])/'plan.json')
        self.assertNotEqual(first['run'],result['run'])
        self.assertEqual(plan['purpose'],'first_spin');self.assertEqual(plan['target_paid'],1)
        self.assertIsNone(plan['bet']);self.assertTrue(plan['auto_bet'])

    def test_selecting_new_draft_clears_previous_run_and_connection(self):
        self.data(self.api.connect(self.profile,{}))
        self.data(self.api.select_machine(None))  # PREPARED never launched by fake start
        d=self.data(self.api.bootstrap())
        self.assertIsNone(d['run']);self.assertFalse(d['connection_ready'])
        self.assertIsNone(self.data(self.api.poll())['run'])

    def test_active_task_cannot_edit_profile_or_start_another(self):
        result=self.data(self.api.connect(self.profile,{}))
        write(Path(result['run'])/'status.json',dict(state='CAPTURING',checked_utc=utc()))
        self.assertFalse(self.api.save_machine(self.profile)['ok'])
        self.assertFalse(self.api.delete_machine('mia')['ok'])
        self.assertFalse(self.api.discover(self.profile)['ok'])
        self.assertFalse(self.api.select_machine(None)['ok'])

    def test_delete_restore_preserves_history(self):
        self.data(self.api.save_machine(self.profile))
        history=self.store/'runs'/'mia'/'kept';history.mkdir(parents=True)
        deleted=self.data(self.api.delete_machine('mia'))
        self.assertTrue(deleted['history_preserved']);self.assertTrue(history.exists())
        restored=self.data(self.api.restore_machine());self.assertEqual(restored['id'],'mia')
        self.assertIsNone(self.data(self.api.bootstrap())['run'])

    def test_image_import_validate_dedupe_and_bounded_preview_without_clipboard(self):
        b=io.BytesIO();Image.new('RGB',(12,12),'#c9472d').save(b,format='PNG')
        item=dict(name='rule.png',base64=base64.b64encode(b.getvalue()).decode())
        paths=self.data(self.api.import_images([item,item]))['paths']
        self.assertEqual(len(paths),1)
        preview=self.data(self.api.preview_images(paths))[0]
        self.assertTrue(preview['preview'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(len(preview['sha256']),64)
        self.assertFalse(self.api.import_images([dict(name='bad.png',base64='aaaa')])['ok'])
        self.assertFalse(self.api.preview_images([str(self.store.parent/'escape.png')])['ok'])

    def test_unrecognized_images_restore_as_draft_and_never_overwrite_approved_profile(self):
        self.data(self.api.save_machine(self.profile))
        original=(self.store/'machines'/'mia.json').read_bytes()
        b=io.BytesIO();Image.new('RGB',(12,12),'white').save(b,format='PNG')
        paths=self.data(self.api.import_images([dict(name='rule.png',base64=base64.b64encode(b.getvalue()).decode())]))['paths']
        p=dict(self.profile,name='尚未确认的新名字',screenshots=paths)
        saved=self.data(self.api.save_machine_draft(p))
        self.assertTrue(saved['_draft_pending'])
        self.assertEqual((self.store/'machines'/'mia.json').read_bytes(),original)
        fresh=DesktopAPI(self.store,self.command)
        restored=self.data(fresh.bootstrap())['selected']
        self.assertEqual(restored['name'],'尚未确认的新名字');self.assertTrue(restored['_draft_pending'])
        self.assertTrue(Path(restored['screenshots'][0]).is_file())
        self.assertIn('rule-images',restored['screenshots'][0])
        approved=self.data(fresh.save_machine(restored))
        self.assertNotIn('_draft_pending',approved)
        self.assertFalse((self.store/'machine-drafts'/'mia.json').exists())

    def test_new_draft_can_be_deleted_and_restored_without_ocr_or_formal_profile(self):
        draft=dict(self.profile,name='',symbols=[],features=[],rules_confirmed=False,screenshots=[])
        self.data(self.api.save_machine_draft(draft))
        self.assertEqual(len(self.data(self.api.bootstrap())['machines']),1)
        self.assertEqual(self.data(self.api.delete_machine('mia'))['kind'],'draft')
        self.assertEqual(self.data(self.api.bootstrap())['machines'],[])
        restored=self.data(self.api.restore_machine())
        self.assertTrue(restored['_draft_pending']);self.assertEqual(restored['symbols'],[])

    def test_vision_only_runs_on_explicit_request_and_forwards_notes(self):
        save_profile(self.store,self.profile)
        with patch('slotcapture.vision_api.recognize',return_value=dict(draft={},requires_confirmation=True)) as mock:
            self.data(self.api.bootstrap());mock.assert_not_called()
            self.assertFalse(self.api.recognize([str(self.store.parent/'escape.png')])['ok'])
            self.data(self.api.recognize([str(self.store/'image.png')],'只提取占格'))
            self.assertEqual(mock.call_args.args[2],'只提取占格')

    def test_session_rejects_invalid_targets_and_unknown_purposes(self):
        for n in (0,-1,True,1.5,1000001):self.assertFalse(self.api.start_session(self.profile,'sampling',n)['ok'])
        self.assertFalse(self.api.start_session(self.profile,'other',1)['ok'])

    def test_window_buttons_only_control_the_window_and_preserve_capture(self):
        window=Mock();self.api._window=window
        self.data(self.api.window_command('minimize'));window.minimize.assert_called_once()
        self.assertTrue(self.data(self.api.window_command('maximize'))['maximized'])
        self.assertFalse(self.data(self.api.window_command('maximize'))['maximized'])
        window.maximize.assert_called_once();window.restore.assert_called_once()
        self.data(self.api.window_command('close'));window.destroy.assert_called_once()
        self.assertEqual(self.calls,[])  # No CLI stop, kill, device or file actions.
        self.assertFalse(self.api.window_command('arbitrary')['ok'])

    def test_window_resize_has_minimum_bounds_and_never_resizes_maximized(self):
        window=Mock();self.api._window=window
        self.data(self.api.resize_window(400,300));window.resize.assert_called_once_with(960,640)
        window.resize.reset_mock();self.api._on_maximized()
        self.data(self.api.resize_window(1200,800));window.resize.assert_not_called()
        self.assertFalse(self.api.resize_window('1200',800)['ok'])


if __name__=='__main__':unittest.main()
