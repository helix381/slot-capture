import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from slotcapture.common import read,write
from slotcapture.workflow import save_profile,list_profiles,delete_profile,restore_profile,prepare_session,default_store
from slotcapture.cli import parser,run
from slotcapture.gui_workflow import WorkflowApp


class MachineDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Path(self.temp.name)/'store'
        self.profile=save_profile(self.store,dict(id='mia',name='米亚',adapter='mia-bole-v1',
                      symbols=[dict(name='女郎')],features=[],capture={},rules_confirmed=True))
    def tearDown(self):self.temp.cleanup()

    def test_delete_is_recoverable_and_preserves_history_and_images(self):
        run_dir=self.store/'runs'/'mia'/'old';run_dir.mkdir(parents=True)
        history=run_dir/'raw.pcap';history.write_bytes(b'raw evidence')
        images=self.store/'rule-images'/'mia';images.mkdir(parents=True)
        image=images/'rules.png';image.write_bytes(b'original screenshot')
        deleted=delete_profile(self.store,'mia')
        self.assertEqual(list_profiles(self.store),[])
        self.assertEqual(read(deleted['archived_path'])['name'],'米亚')
        self.assertEqual(history.read_bytes(),b'raw evidence')
        self.assertEqual(image.read_bytes(),b'original screenshot')
        restored=restore_profile(self.store,'mia',deleted['deleted'])
        self.assertEqual(restored['symbols'],self.profile['symbols'])
        self.assertEqual(len(list_profiles(self.store)),1)

    def test_active_external_run_blocks_delete(self):
        external=Path(self.temp.name)/'external-run'
        prepare_session(self.store,'mia','connection',external)
        self.assertIn(str(external.resolve()),read(self.store/'machine-runs'/'mia.json')['runs'])
        with patch('slotcapture.runtime.status',return_value=dict(state='CAPTURING',worker_alive=True)):
            with self.assertRaises(ValueError):delete_profile(self.store,'mia')
        self.assertEqual(len(list_profiles(self.store)),1)

    def test_restore_never_overwrites_recreated_profile(self):
        deleted=delete_profile(self.store,'mia')
        save_profile(self.store,dict(self.profile,name='新档案'))
        with self.assertRaises(FileExistsError):restore_profile(self.store,'mia',deleted['deleted'])
        self.assertEqual(list_profiles(self.store)[0]['name'],'新档案')
        self.assertTrue(Path(deleted['archived_path']).exists())

    def test_invalid_ids_and_tokens_are_rejected(self):
        with self.assertRaises(ValueError):delete_profile(self.store,'../escape')
        with self.assertRaises(ValueError):restore_profile(self.store,'mia','../../escape')

    def test_deleting_last_legacy_profile_does_not_abandon_its_recycle_directory(self):
        base=Path(self.temp.name)/'dist'
        legacy=base/'v0.2.0'/'data'
        profile=dict(self.profile,id='legacy')
        save_profile(legacy,profile);deleted=delete_profile(legacy,'legacy')
        with patch('slotcapture.workflow.sys.frozen',True,create=True),patch('slotcapture.workflow.sys.executable',str(base/'slot-capture.exe')):
            self.assertEqual(default_store(),legacy)
        restored=restore_profile(legacy,'legacy',deleted['deleted'])
        self.assertEqual(restored['id'],'legacy')

    def test_cli_delete_and_restore(self):
        deleted=run(parser().parse_args(['machine-delete','--id','mia','--store',str(self.store)]))
        restored=run(parser().parse_args(['machine-restore','--id','mia','--deleted',deleted['deleted'],'--store',str(self.store)]))
        self.assertEqual(restored['id'],'mia')

    def test_gui_cancel_and_active_state_never_delete(self):
        app=WorkflowApp.__new__(WorkflowApp)
        app.busy=False;app.active=None;app.root=Mock();app.notice=Mock()
        app.records={'mia':self.profile};app.machines=Mock();app.machines.curselection.return_value=(0,)
        app.task=Mock()
        with patch('slotcapture.gui_workflow.messagebox.askyesno',return_value=False) as confirm:
            app.delete_machine()
            self.assertIn('米亚',confirm.call_args.args[1])
        app.task.assert_not_called()
        app.active='running'
        with patch('slotcapture.gui_workflow.messagebox.askyesno') as confirm:
            app.delete_machine();confirm.assert_not_called()
        app.task.assert_not_called()
