"""Headless controller checks; never create a Tk window or invoke a device."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from slotcapture.gui_workflow import WorkflowApp
from slotcapture.workflow import default_store


class Value:
    def __init__(self, value=''):self.value=value
    def set(self,value):self.value=value
    def get(self):return self.value


class DiscoveryControllerTests(unittest.TestCase):
    def app(self):
        app=WorkflowApp.__new__(WorkflowApp)
        app.busy=False;app.active=None;app.connection='old-proof'
        app.fields={key:Value(value) for key,value in dict(adb='old-adb',device='old-device',package='old.package',game_ip='198.51.100.10').items()}
        app.notice=Value();app.connection_text=Value();app.detected_summary=Value()
        return app

    def test_auto_detection_never_supplies_cached_package_device_or_ip(self):
        app=self.app();calls=[]
        app.cli=lambda *args:calls.append(args) or {}
        app.task=lambda work,done,label:work()
        app.detect_connection()
        self.assertIsNone(app.connection)
        self.assertEqual(app.fields['game_ip'].get(),'')
        self.assertEqual(calls,[('discover','--adb','old-adb')])

    def test_unique_match_fills_details_and_starts_connection_check(self):
        app=self.app();calls=[];app.launch=lambda purpose:calls.append(purpose)
        cap=dict(adb='new-adb',device='new-device',package='com.game',game_ip='203.0.113.10')
        app.discovered(dict(stage='detected',capture=cap,message='匹配成功'))
        self.assertEqual(calls,['connection'])
        self.assertEqual({k:v.get() for k,v in app.fields.items()},cap)
        self.assertIn('com.game',app.detected_summary.get())

    def test_multiple_devices_only_request_a_choice(self):
        app=self.app();requests=[]
        app.choose_candidate=lambda title,rows,callback:requests.append((title,rows))
        app.discovered(dict(stage='choose_device',devices=[dict(model='One',device='one',adb='adb'),dict(model='Two',device='two',adb='adb')]))
        self.assertEqual(len(requests),1);self.assertEqual(len(requests[0][1]),2)

    def test_uid_failure_preserves_partial_results_and_exits_detecting(self):
        app=self.app();app.detecting=True;app.details_open=Value(False);app.toggle_details=lambda:None
        app.discovered(dict(stage='uid_missing',capture=dict(adb='adb',device='emulator-5554',package='com.game'),
                            message='UID解析失败',diagnostic_path='diagnostic.json'))
        self.assertFalse(app.detecting)
        self.assertEqual(app.fields['package'].get(),'com.game')
        self.assertIn('检测未通过',app.detected_summary.get())
        self.assertNotIn('检测中',app.detected_summary.get())
        self.assertEqual(app.last_diagnostic,'diagnostic.json')

    def test_probe_failure_never_claims_connection_waiting_for_selection(self):
        app=self.app();app.details_open=Value(False);app.toggle_details=lambda:None
        app.discovered(dict(stage='probe_cleanup_required',capture=dict(device='emulator-5554',package='com.game'),
                            message='未确认安全停止'))
        self.assertIn('探测未安全结束',app.detected_summary.get())
        self.assertNotIn('待选择',app.detected_summary.get())
        self.assertTrue(app.details_open.get())
        app.discovered(dict(stage='heartbeat_missing',capture=dict(device='emulator-5554',package='com.game'),
                            message='尚未匹配到心跳'))
        self.assertIn('未检测到游戏心跳',app.detected_summary.get())

    def test_new_version_reuses_existing_library_without_copying(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/'dist';old=base/'v0.2.0'/'data'/'machines';old.mkdir(parents=True)
            (old/'mia.json').write_text('{}')
            exe=base/'v0.2.1'/'slot-capture.exe'
            with patch('slotcapture.workflow.sys.frozen',True,create=True),patch('slotcapture.workflow.sys.executable',str(exe)):
                self.assertEqual(default_store(),old.parent)
                self.assertFalse((base/'data').exists())

    def test_flat_daily_build_reuses_existing_library(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/'dist';old=base/'v0.2.0'/'data'/'machines';old.mkdir(parents=True)
            (old/'mia.json').write_text('{}')
            with patch('slotcapture.workflow.sys.frozen',True,create=True),patch('slotcapture.workflow.sys.executable',str(base/'slot-capture.exe')):
                self.assertEqual(default_store(),old.parent)
