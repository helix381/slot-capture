import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from slotcapture.common import normalize, Journal, jsonl, write, read
from slotcapture.cli import replay, init_run
from slotcapture.audit import analyze
from slotcapture.runtime import ADB, worker

HISTORY = Path('E:/AI/reverse/mia-tavern/runs')
ADAPTER = 'mia-bole-v1'

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_rejects_invalid_budget_and_adapter(self):
        for obj in [dict(bet=True,target_paid=100),dict(bet=10,target_paid=0),
                    dict(bet=10,target_paid=100,max_wager=999),
                    dict(bet=10,target_paid=100,adapter='unknown')]:
            plan=dict(schema_version='slotcapture/v1',adapter=ADAPTER,**{k:v for k,v in obj.items() if k!='adapter'})
            if 'adapter' in obj:plan['adapter']=obj['adapter']
            with self.assertRaises(ValueError):normalize(plan)

    def test_event_cursor_deduplicates_after_restart(self):
        j=Journal(self.root);j.emit('test',{'value':1});j.emit('test',{'value':1})
        Journal(self.root).emit('test',{'value':1});Journal(self.root).emit('test',{'value':2})
        es=jsonl(self.root/'events.jsonl');self.assertEqual([e['cursor'] for e in es],[1,2])

    def test_remote_pid_mismatch_never_signals(self):
        adb=ADB({'adb':'not-used','device':'not-used'})
        with patch.object(adb,'remote_identity',return_value=['unrelated-process']),patch.object(adb,'run') as run:
            with self.assertRaises(RuntimeError):adb.stop_owned(999,['tcpdump','owned-prefix'])
            run.assert_not_called()

    def test_partial_last_json_line_is_not_parsed(self):
        p=self.root/'events.jsonl';p.write_bytes(b'{"ok":true}\n{"partial":')
        self.assertEqual(jsonl(p),[{'ok':True}])
        p.write_bytes(b'bad complete line\n')
        with self.assertRaises(ValueError):jsonl(p)

    def test_cli_bad_input_returns_json(self):
        p=subprocess.run([sys.executable,'-m','slotcapture.cli','init'],capture_output=True,text=True,encoding='utf8')
        self.assertEqual(p.returncode,2);self.assertFalse(json.loads(p.stdout)['ok'])

    def history(self):
        pilot=HISTORY/'pilot-100-13500000-001'
        if not pilot.exists():self.skipTest('Historical fixtures are local and not shipped')
        yield pilot,100,10,1962369453
        base=HISTORY/'bet-crossover-002'
        for b,arm,bet,free,ret in [(1,'B',18000000,10,1649483264),(2,'A',13500000,0,419670000),
                                   (3,'A',13500000,10,4973499589),(4,'B',18000000,0,709215555)]:
            yield base/f'block-{b:02d}-{arm}-{bet}',100,free,ret

    def test_all_500_cycles_from_messages_and_raw_pcaps(self):
        total_paid=total_free=total_return=0
        for i,(src,paid,free,ret) in enumerate(self.history()):
            # The pilot's audited message stream includes its corrected mode attribution.
            if i==0:
                imported=self.root/'pilot-input';imported.mkdir()
                import shutil
                shutil.copyfile(src/'audited-messages.jsonl',imported/'messages.jsonl')
                shutil.copyfile(src/'audited-messages.jsonl.spins.jsonl',imported/'messages.jsonl.spins.jsonl')
                inputs=imported
            else:inputs=src
            for kind in ['messages','pcap']:
                args=argparse.Namespace(plan=str(src/'sampling-plan.json'),out=str(self.root/f'run-{i}-{kind}'),
                         input_run=str(inputs) if kind=='messages' else None,
                         pcap=str(src/'raw-final.pcap') if kind=='pcap' else None,game_ip='198.18.16.10')
                with contextlib.redirect_stdout(io.StringIO()):result=replay(args)
                self.assertEqual(result['paid'],paid,(src,kind,result))
                self.assertEqual(result['free'],free,(src,kind,result))
                self.assertEqual(result['expected_return'],ret,(src,kind,result))
                self.assertTrue(result['complete'],(src,kind,result))
                if i==3:
                    audit=read(Path(args.out)/'audit.json')
                    finals=[f for f in audit['features'] if f['feature']=='beer_final']
                    self.assertEqual(finals[0]['award'],4299224274)
                    self.assertTrue(finals[0]['confirmed'])
            total_paid+=paid;total_free+=free;total_return+=ret
        self.assertEqual((total_paid,total_free,total_return),(500,30,9714237861))

    def fixture(self):
        src=HISTORY/'bet-crossover-002'/'block-03-A-13500000'
        if not src.exists():self.skipTest('Historical fixture unavailable')
        args=argparse.Namespace(plan=str(src/'sampling-plan.json'),out=str(self.root/'run'),input_run=str(src),pcap=None,game_ip=None)
        replay(args)
        return Path(args.out),normalize(read(args.plan))

    def test_missing_final_receipt_blocks_completion(self):
        root,plan=self.fixture();es=jsonl(root/'messages.jsonl')
        es=[e for e in es if not(e.get('command')=='collect_coins' and e.get('direction')=='response' and e['message'][1].get('collect')==4299224274)]
        (root/'messages.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in es),encoding='utf8')
        a=analyze(root,plan,final=True);self.assertFalse(a['complete']);self.assertEqual(a['pending_rewards'],1)

    def test_bet_change_and_target_overrun_are_errors(self):
        root,plan=self.fixture();ss=jsonl(root/'messages.jsonl.spins.jsonl');ss[0]['bet']=18000000
        (root/'messages.jsonl.spins.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in ss),encoding='utf8')
        plan['target_paid']=99
        a=analyze(root,plan,final=True);kinds={i['kind'] for i in a['issues']}
        self.assertIn('bet_mismatch',kinds);self.assertIn('paid_or_wager_limit_exceeded',kinds);self.assertFalse(a['complete'])

    def test_configured_net_loss_stop_is_not_silently_ignored(self):
        root,plan=self.fixture();plan['max_net_loss']=1
        report=analyze(root,plan,final=True)
        self.assertEqual(report['net_loss_stop_at_paid'],1)
        self.assertIn('paid_after_net_loss_stop',{i['kind'] for i in report['issues']})

    def test_live_worker_lifecycle_with_fake_adb_and_continuous_packet_activity(self):
        src=HISTORY/'bet-crossover-002'/'block-02-A-13500000'
        if not src.exists():self.skipTest('Historical fixture unavailable')
        raw=(src/'raw-final.pcap').read_bytes()
        import struct
        size=struct.unpack('<I',raw[32:36])[0];duplicate=raw[24:24+16+size]
        token='a'*32;prefix='/data/local/tmp/slotcap-'+token
        plan=dict(schema_version='slotcapture/v1',run_id='fake-live',adapter=ADAPTER,bet=13500000,target_paid=100,
                  capture=dict(adb='fake',device='fake',game_ip='198.18.16.10',package='example.game',
                               seconds=8,tail_seconds=4,quiet_seconds=1,poll_seconds=.2))
        root=init_run(self.root/'fake-live',plan);(root/'owner.lock').write_text(token)
        signs=[]
        class FakeADB:
            n=0
            argv=['fake','-s','fake']
            def __init__(self,cap):pass
            def remote_identity(self,pid):return ['tcpdump','-w',prefix+'.pcap','tcp','and','host','198.18.16.10']
            def stop_owned(self,pid,argv):signs.append((pid,argv))
            def run(self,*args,**kw):
                if args[0]=='exec-out':
                    self.n+=1;return subprocess.CompletedProcess(args,0,raw+duplicate*self.n,b'')
                if 'test -e' in args[-1]:return subprocess.CompletedProcess(args,1,b'',b'')
                return subprocess.CompletedProcess(args,0,b'123456\n',b'')
        class FakeChild:
            pid=123456
            def poll(self):return None
        with patch('slotcapture.runtime.ADB',FakeADB),patch('slotcapture.runtime.doctor',return_value={}),\
             patch('slotcapture.runtime.subprocess.Popen',return_value=FakeChild()),\
             patch('slotcapture.runtime.process_identity',return_value=None):
            with contextlib.redirect_stdout(io.StringIO()):result=worker(root,token)
        self.assertEqual(result['state'],'COMPLETE',result)
        self.assertEqual(len(signs),1);self.assertEqual(signs[0][0],123456)
        self.assertTrue((root/'raw-final.pcap').exists());self.assertFalse((root/'owner.lock').exists())

if __name__=='__main__':unittest.main()
