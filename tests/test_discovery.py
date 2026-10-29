from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from slotcapture.discovery import discover, device_rows, foreground_package, ipv4_sockets, adb_candidates, probe_ip, probe_cleanup_status
from slotcapture.runtime import SocketUnavailableError


def sockets(ip='C633640A', uid=10042, state='01', port='1F90'):
    return f' sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid\n 0: 0100007F:1234 {ip}:{port} {state} 0:0 00:0 0 {uid} 0 1\n'


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.rows='List of devices attached\nemulator-5554 device product:x model:LD_Player device:x\n'
        self.tcp=sockets()
        self.uid_dump='Package [com.example.game] userId=10042'
        self.uid_list=''

    def tearDown(self): self.tmp.cleanup()

    def run_discovery(self, **options):
        test=self
        class FakeADB:
            def __init__(self, cap): self.cap=cap
            def run(self,*args,**kwargs):
                cmd=args[-1]
                if cmd=='dumpsys activity activities': value='mResumedActivity: ActivityRecord{abc u0 com.example.game/.Main t12}'
                elif cmd=='dumpsys window windows': value='mCurrentFocus=Window{abc u0 com.example.game/.Main}'
                elif cmd=='dumpsys package com.example.game': value=test.uid_dump
                elif cmd.startswith(('cmd package list packages -U','pm list packages -U')): value=test.uid_list
                elif '/proc/net/tcp6' in cmd: value='header\n'
                elif '/proc/net/tcp' in cmd: value=test.tcp
                else: raise AssertionError(args)
                return subprocess.CompletedProcess(args,0,value.encode(),b'')
        with patch('slotcapture.discovery.adb_candidates',return_value=['X:/adb.exe']), \
             patch('slotcapture.discovery.subprocess.run',return_value=subprocess.CompletedProcess([],0,self.rows.encode(),b'')), \
             patch('slotcapture.discovery.ADB',FakeADB):
            return discover(self.root,**options)

    def test_device_and_foreground_parsers(self):
        rows=device_rows(self.rows+'emulator-5556 offline\n* daemon started successfully\n')
        self.assertEqual(rows[0]['model'],'LD Player');self.assertEqual(rows[1]['state'],'offline')
        self.assertEqual(foreground_package('mCurrentFocus=Window{abc u0 com.example.game/com.example.Main}'),'com.example.game')
        self.assertEqual(foreground_package('mResumedActivity: x u0 com.android.launcher/.Main'),'com.android.launcher')
        self.assertIsNone(foreground_package('no foreground activity'))

    def test_socket_parser_filters_uid_state_and_maps_ipv4(self):
        rows=ipv4_sockets(sockets()+sockets(uid=999)+sockets(state='0A'),10042)
        self.assertEqual(rows,[dict(ip='10.100.51.198',ports=[8080])])
        rows=ipv4_sockets(sockets(ip='0000000000000000FFFF0000C633640A'),10042)
        self.assertEqual(rows,[dict(ip='10.100.51.198',ports=[8080])])
        self.assertEqual(ipv4_sockets(sockets(ip='00000000000000000000000001000000'),10042),[])

    def test_multiple_devices_require_choice_and_do_not_probe(self):
        self.rows+='emulator-5556 device model:Second_Player\n'
        with patch('slotcapture.discovery.probe_ip') as probe:
            result=self.run_discovery()
        self.assertEqual(result['stage'],'choose_device');probe.assert_not_called()

    def test_foreground_uid_connection_discovered_without_manual_fields(self):
        with patch('slotcapture.discovery.probe_ip',return_value=dict(ip='10.100.51.198',matched=True,cleanup_verified=True)) as probe:
            result=self.run_discovery()
        self.assertEqual(result['stage'],'detected')
        self.assertEqual(result['capture'],dict(adb='X:/adb.exe',device='emulator-5554',package='com.example.game',game_ip='10.100.51.198',android_user=0))
        self.assertEqual(probe.call_count,1)

    def test_multiple_ips_resolved_by_passive_protocol_probe(self):
        self.tcp+=sockets(ip='CB007108')
        def probe(cap,ip,store):return dict(ip=ip,matched=ip=='10.100.51.198',cleanup_verified=True)
        with patch('slotcapture.discovery.probe_ip',side_effect=probe) as check:
            result=self.run_discovery()
        self.assertEqual(check.call_count,2)
        self.assertEqual(result['capture']['game_ip'],'10.100.51.198')
        with patch('slotcapture.discovery.probe_ip',side_effect=lambda cap,ip,store:dict(ip=ip,matched=True,cleanup_verified=True)):
            result=self.run_discovery()
        self.assertEqual(result['stage'],'choose_server')
        self.assertNotIn('game_ip',result['capture'])

    def test_failed_cleanup_never_starts_another_candidate(self):
        self.tcp+=sockets(ip='CB007108')
        with patch('slotcapture.discovery.probe_ip',return_value=dict(matched=False,cleanup_verified=False)) as check:
            result=self.run_discovery()
        self.assertEqual(result['stage'],'probe_cleanup_required');self.assertEqual(check.call_count,1)

    def test_no_probe_does_not_claim_heartbeat_verified(self):
        with patch('slotcapture.discovery.probe_ip') as check:
            result=self.run_discovery(probe=False)
        self.assertEqual(result['stage'],'candidate');check.assert_not_called()

    def test_no_usable_connection_does_not_reuse_saved_ip(self):
        self.tcp=sockets(uid=999)
        result=self.run_discovery()
        self.assertEqual(result['stage'],'server_missing');self.assertNotIn('game_ip',result['capture'])

    def test_missing_adb_is_actionable(self):
        with patch('slotcapture.discovery.adb_candidates',return_value=[]):
            result=discover(self.root)
        self.assertEqual(result['stage'],'adb_missing')

    def test_uid_fallback_works_through_entire_discovery(self):
        self.uid_dump='Package [com.example.game] without numeric UID'
        self.uid_list='package:com.example.game uid:10042'
        result=self.run_discovery(probe=False)
        self.assertEqual(result['uid'],10042)
        self.assertEqual(result['stage'],'candidate')

    def test_uid_failure_returns_partial_results_and_diagnostic_file(self):
        import json
        self.uid_dump='Unable to find package: com.example.game'
        result=self.run_discovery(probe=False)
        self.assertEqual(result['stage'],'uid_missing')
        self.assertEqual(result['capture']['package'],'com.example.game')
        diagnostic=json.loads(Path(result['diagnostic_path']).read_text(encoding='utf-8'))
        commands=[q['command'] for q in diagnostic['inspection']]
        self.assertIn('pm list packages -U --user 0 com.example.game',commands)
        self.assertTrue(diagnostic['no_game_action_sent'])

    def test_disappeared_candidate_does_not_discard_previous_heartbeat_match(self):
        self.tcp+=sockets(ip='CB007108')
        results=[dict(ip='10.100.51.198',matched=True,cleanup_verified=True),
                 dict(ip='8.113.0.203',matched=False,cleanup_verified=True,cleanup_status='not_started',failure_code='socket_unavailable')]
        with patch('slotcapture.discovery.probe_ip',side_effect=results):
            result=self.run_discovery()
        self.assertEqual(result['stage'],'detected')
        self.assertEqual(result['capture']['game_ip'],'10.100.51.198')

    def test_no_heartbeat_is_not_presented_as_a_connection_choice(self):
        self.tcp+=sockets(ip='CB007108')
        with patch('slotcapture.discovery.probe_ip',return_value=dict(matched=False,cleanup_verified=True,failure_code='heartbeat_timeout')):
            result=self.run_discovery()
        self.assertEqual(result['stage'],'heartbeat_missing')
        with patch('slotcapture.discovery.probe_ip',return_value=dict(matched=False,cleanup_verified=True,cleanup_status='not_started',failure_code='socket_unavailable')):
            result=self.run_discovery()
        self.assertEqual(result['stage'],'server_changed')

    def test_socket_preflight_failure_proves_no_capture_was_started(self):
        cap=dict(adb='fake',device='fake',package='com.example.game')
        with patch('slotcapture.runtime.doctor',side_effect=SocketUnavailableError('socket changed')), \
             patch('slotcapture.runtime.subprocess.Popen') as launch:
            result=probe_ip(cap,'203.0.113.10',self.root)
        launch.assert_not_called()
        self.assertEqual(result['cleanup_status'],'not_started')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['failure_code'],'socket_unavailable')

    def test_unknown_or_unverified_launch_never_counts_as_clean(self):
        self.assertEqual(probe_cleanup_status(dict(remote_pid=None)),'unverified')
        self.assertEqual(probe_cleanup_status(dict(capture_launch_attempted=True,remote_pid=None)),'unverified')
        self.assertEqual(probe_cleanup_status(dict(capture_launch_attempted=False,remote_pid=123)),'unverified')
        self.assertEqual(probe_cleanup_status(dict(remote_stopped_verified=True)),'verified')

    def test_hint_only_accepted_when_file_exists(self):
        hint=self.root/'adb.exe';hint.write_bytes(b'test')
        with patch('slotcapture.discovery.shutil.which',return_value=None),patch('slotcapture.discovery.psutil.process_iter',return_value=[]):
            paths=adb_candidates(str(hint))
        self.assertEqual(paths[0],str(hint.resolve()))
