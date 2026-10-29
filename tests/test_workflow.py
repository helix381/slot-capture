import contextlib
import io
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import dpkt
from slotcapture.common import read, write, normalize
from slotcapture.cli import init_run
from slotcapture.runtime import session_report, worker
from slotcapture.workflow import (save_profile, load_profile, verified, prepare_session,
                                  connection_ready, record_verification, archive_images)
from slotcapture.vision import recognize, DRAFT_SCHEMA


def encode(value):
    if isinstance(value, int): return b'\x03'+struct.pack('<i', value)
    if isinstance(value, str):
        raw = value.encode(); return b'\x06'+bytes([len(raw)])+raw
    if isinstance(value, list): return b'\x04'+bytes([len(value)])+b''.join(map(encode, value))
    if isinstance(value, dict): return b'\x0d'+bytes([len(value)])+b''.join(encode(k)+encode(v) for k,v in value.items())
    raise TypeError(type(value))


def pcap(messages):
    output = io.BytesIO(); writer = dpkt.pcap.Writer(output)
    seq = [100, 200]
    for i, (command, body, response) in enumerate(messages):
        payload = encode([command, body, {}]); payload = struct.pack('>I', len(payload))+payload
        tcp = dpkt.tcp.TCP(sport=443 if response else 41000, dport=41000 if response else 443,
                          seq=seq[response], flags=dpkt.tcp.TH_ACK, off=5, data=payload)
        seq[response] += len(payload)
        src, dst = ('203.0.113.77', '192.0.2.18') if response else ('192.0.2.18', '203.0.113.77')
        ip = dpkt.ip.IP(src=socket.inet_aton(src), dst=socket.inet_aton(dst), p=6, data=tcp)
        ip.len = len(ip)
        eth = dpkt.ethernet.Ethernet(src=b'\x02'*6, dst=b'\x04'*6, type=0x0800, data=ip)
        writer.writepkt(bytes(eth), 1700000000+i)
    return output.getvalue()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Path(self.temp.name)
        self.cap = dict(adb='fake', device='fake', game_ip='203.0.113.77', package='example.game',
                        seconds=8, tail_seconds=4, quiet_seconds=1, poll_seconds=.2)
        self.profile = save_profile(self.store, dict(id='mia', name='米亚', adapter='mia-bole-v1',
                      symbols=[dict(name='女郎', protocol_id='2')], features=[dict(name='转盘')],
                      capture=self.cap, rules_confirmed=True))

    def tearDown(self): self.temp.cleanup()

    def fake_worker(self, root, raw):
        token='b'*32; prefix='/data/local/tmp/slotcap-'+token
        (root/'owner.lock').write_text(token)
        class FakeADB:
            argv=['fake','-s','fake']
            def __init__(self, cap): pass
            def remote_identity(self, pid): return ['tcpdump','-w',prefix+'.pcap','tcp','and','host','203.0.113.77']
            def stop_owned(self, pid, argv): pass
            def run(self, *args, **kw):
                if args[0]=='exec-out': return subprocess.CompletedProcess(args,0,raw,b'')
                if 'test -e' in args[-1]: return subprocess.CompletedProcess(args,1,b'',b'')
                return subprocess.CompletedProcess(args,0,b'123456\n',b'')
        class FakeChild:
            pid=123456
            def poll(self): return None
        with patch('slotcapture.runtime.ADB', FakeADB), patch('slotcapture.runtime.doctor', return_value={}), \
             patch('slotcapture.runtime.subprocess.Popen', return_value=FakeChild()), \
             patch('slotcapture.runtime.process_identity', return_value=None), contextlib.redirect_stdout(io.StringIO()):
            return worker(root, token)

    def connected(self):
        root = self.store/'connection'
        prepare_session(self.store, 'mia', 'connection', root)
        result = self.fake_worker(root, pcap([('tick',{},False),('tick',{},True)]))
        self.assertEqual(result['state'], 'COMPLETE', result)
        self.assertTrue(connection_ready(self.profile, root))
        return root

    def test_connection_requires_same_command_both_directions_and_fresh_evidence(self):
        root = self.store/'probe'; root.mkdir()
        plan=normalize(dict(schema_version='slotcapture/v1', adapter='mia-bole-v1',
                            bet=None, auto_bet=True, target_paid=1, purpose='connection', capture=self.cap))
        q=dict(heartbeats={'tick':{'request':1,'response':0},'keepalive':{'request':0,'response':1}})
        self.assertFalse(session_report(root,plan,q)['complete'])
        connection = self.connected()
        s=read(connection/'status.json'); s['checked_utc']='2020-01-01T00:00:00+00:00'; write(connection/'status.json',s)
        self.assertFalse(connection_ready(self.profile, connection))

    def test_full_onboarding_auto_bet_and_reuse_verification(self):
        connection = self.connected()
        with self.assertRaises(ValueError):
            prepare_session(self.store,'mia','sampling',self.store/'blocked',10,connection)
        first=self.store/'first'
        prepare_session(self.store,'mia','first_spin',first,connection=connection)
        req=dict(bet=13500000,free_spins=0,lines=40)
        result=dict(base_win=0,total_win=0,item_list=[[2,3,4]]*5,win_lines=[],theme_info={'multi_info':[1,0]})
        raw=pcap([('batch_spin',req,False),('batch_spin',{'list':[result]},True)])
        s=self.fake_worker(first,raw)
        self.assertEqual(s['state'],'COMPLETE',s)
        self.assertEqual(s['detected_bet'],13500000)
        profile=load_profile(self.store,'mia'); self.assertTrue(verified(profile))
        # Unchanged profile edits preserve evidence, including a new session's independent auto Bet.
        profile=save_profile(self.store,profile); self.assertTrue(profile['first_spin_verified'])
        # A changed server address needs a new live connection, not another math verification.
        changed=save_profile(self.store,dict(profile,capture=dict(profile['capture'],game_ip='203.0.113.78')))
        self.assertTrue(changed['first_spin_verified'])
        self.assertFalse(connection_ready(changed,connection))
        profile=save_profile(self.store,profile)
        sample=self.store/'formal'
        prepare_session(self.store,'mia','sampling',sample,10,connection)
        self.assertIsNone(read(sample/'plan.json')['bet'])
        self.assertEqual(read(sample/'plan.json')['target_paid'],10)
        self.assertEqual(read(first/'audit.json')['paid'],1)
        profile['symbols'][0]['name']='新名称'; changed=save_profile(self.store,profile)
        self.assertFalse(verified(changed))

    def test_auto_bet_locks_then_drift_is_detected(self):
        root=self.store/'auto'
        plan=dict(schema_version='slotcapture/v1',run_id='auto',adapter='mia-bole-v1',auto_bet=True,bet=None,target_paid=2,capture=self.cap)
        init_run(root,plan); plan=read(root/'plan.json')
        # Paired raw results with no awards, sufficient to test Bet locking and mismatch.
        rows=[]; messages=[]
        for i,bet in enumerate([13500000,18000000],1):
            sha=str(i)
            rows.append(dict(sample=i,matched_request=True,mode='base',batch_size=1,bet=bet,base_win=0,
                             win_lines=[],response_sha256=sha,response_time_utc=f'2026-01-01T00:00:0{i}+00:00'))
            messages.extend([dict(kind='game_message',command='batch_spin',direction='request'),
                             dict(kind='game_message',command='batch_spin',direction='response',payload_sha256=sha,message=['batch_spin',{'list':[{}]}])])
        # Nonempty raw result avoids conflating the deliberately changed Bet with a missing result.
        for e in messages:
            if e.get('direction')=='response':e['message'][1]['list']=[{'base_win':0}]
        for name,values in [('messages.jsonl',messages),('messages.jsonl.spins.jsonl',rows)]:
            (root/name).write_text(''.join(json.dumps(v)+'\n' for v in values))
        report=session_report(root,plan,{})
        self.assertEqual(plan['bet'],13500000)
        self.assertIn('bet_mismatch',{i['kind'] for i in report['issues']})

    def test_unconfirmed_and_unadapted_profiles_cannot_start(self):
        connection=self.connected()
        p=dict(self.profile,rules_confirmed=False);save_profile(self.store,p)
        with self.assertRaises(ValueError):prepare_session(self.store,'mia','first_spin',self.store/'blocked',connection=connection)
        p.update(adapter='unadapted');save_profile(self.store,p)
        with self.assertRaises(ValueError):prepare_session(self.store,'mia','connection',self.store/'blocked2')

    def test_changed_environment_invalidates_connection(self):
        connection=self.connected()
        changed=dict(self.profile,capture=dict(self.profile['capture'],game_ip='203.0.113.78'))
        changed=save_profile(self.store,changed)
        self.assertFalse(connection_ready(changed,connection))
        with self.assertRaises(ValueError):prepare_session(self.store,'mia','first_spin',self.store/'wrong-ip',connection=connection)

    def test_request_and_response_on_different_connections_do_not_pair(self):
        from slotcapture.vendor.slot_capture import Recorder
        rec=Recorder(self.store/'test.jsonl','203.0.113.77')
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rec.message(1,'request',encode(['tick',{},{}]),('192.0.2.18',41000,'203.0.113.77',443),1)
                rec.message(2,'response',encode(['tick',{},{}]),('203.0.113.77',443,'192.0.2.18',41001),1)
            self.assertEqual(rec.summary()['heartbeat_pairs'],0)
        finally:
            rec.out.close();rec.spins.close()

    def test_screenshot_archive_and_cannot_forge_first_spin(self):
        image=self.store/'rules.png';image.write_bytes(b'fake image')
        p=dict(self.profile,screenshots=[str(image)],verification={'passed':True},first_spin_verified=True)
        saved=save_profile(self.store,p)
        self.assertFalse(saved['first_spin_verified']);self.assertNotIn('verification',read(self.store/'machines'/'mia.json'))
        self.assertTrue(Path(saved['screenshots'][0]).is_file())
        self.assertNotEqual(str(image),saved['screenshots'][0])
        with self.assertRaises(ValueError):archive_images(self.store,'../escape',[str(image)])

    def test_codex_bridge_returns_draft_without_saving_rules_or_using_shell(self):
        image=self.store/'rules.png';image.write_bytes(b'fake image')
        draft=dict(name='米亚',symbols=[dict(name='女郎',role='收集',size='1x1',notes='待确认')],features=[],notes='')
        calls=[]
        class Child:
            returncode=0
            def __init__(self,args,**kw):
                calls.append((args,kw));self.output=Path(args[args.index('--output-last-message')+1])
            def communicate(self,prompt,timeout):
                write(self.output,draft);return '', ''
        with patch('slotcapture.vision.codex_status',return_value=dict(available=True,authenticated=True,executable='codex.exe')), \
             patch('slotcapture.vision.subprocess.Popen',Child):
            response=recognize([image],self.store)
        self.assertTrue(response['requires_confirmation'])
        self.assertNotIn('shell',calls[0][1])
        self.assertIn('read-only',calls[0][0]);self.assertIn('--ephemeral',calls[0][0])
        self.assertEqual(calls[0][0][calls[0][0].index('--model')+1],'gpt-6-luna')
        self.assertIn('model_reasoning_effort="low"',calls[0][0])
        manifest=read(Path(response['job'])/'manifest.json')
        self.assertEqual(manifest['reasoning_effort'],'low')
        self.assertEqual(manifest['model'],'gpt-6-luna')
        self.assertEqual(load_profile(self.store,'mia')['symbols'][0]['name'],'女郎')
        self.assertNotIn('protocol_id',DRAFT_SCHEMA['properties']['symbols']['items']['properties'])
