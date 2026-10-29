import subprocess
import unittest
from slotcapture.android import resolve_package_uid, UIDResolutionError, foreground_candidates, package_uid_from_list


class FakeADB:
    def __init__(self, replies):self.replies=replies;self.calls=[]
    def run(self,*args,**kw):
        command=args[-1];self.calls.append(command)
        value=self.replies.get(command,(1,'','unsupported'))
        if isinstance(value,str):value=(0,value,'')
        code,text,error=value
        return subprocess.CompletedProcess(args,code,text.encode(),error.encode())


class AndroidTests(unittest.TestCase):
    def test_native_user_id_and_new_app_id_formats(self):
        for body in ('userId=10042','userId = 10042','appId=10042'):
            adb=FakeADB({'dumpsys package com.game':body})
            self.assertEqual(resolve_package_uid(adb,'com.game'),10042)

    def test_cmd_fallback_when_dump_has_no_uid(self):
        adb=FakeADB({'dumpsys package com.game':'Package com.game without UID',
                     'cmd package list packages -U com.game':'package:com.game.extra uid:999\npackage:com.game uid:10042'})
        self.assertEqual(resolve_package_uid(adb,'com.game'),10042)
        self.assertEqual(len(adb.calls),2)

    def test_pm_fallback_when_cmd_is_unsupported(self):
        adb=FakeADB({'dumpsys package com.game':'no numeric field',
                     'pm list packages -U com.game':'package:com.game uid:10042'})
        self.assertEqual(resolve_package_uid(adb,'com.game'),10042)

    def test_wrong_package_never_supplies_uid_and_keeps_diagnostics(self):
        adb=FakeADB({'cmd package list packages -U com.game':'package:com.game.extra uid:10042'})
        trace=[]
        with self.assertRaises(UIDResolutionError) as ctx:resolve_package_uid(adb,'com.game',trace=trace)
        self.assertIn('com.game',str(ctx.exception))
        self.assertEqual(len(trace),3)
        self.assertIn('unsupported',trace[0]['stderr'])

    def test_android_user_profile_uid_is_not_confused_with_app_id(self):
        adb=FakeADB({'dumpsys package com.game':'appId=10042'})
        self.assertEqual(resolve_package_uid(adb,'com.game',10),1010042)
        self.assertIsNone(package_uid_from_list('package:com.game uid:10042','com.game',10))

    def test_foreground_priority_and_user_from_window_record(self):
        text='mResumedActivity: ActivityRecord{old u0 com.old/.Main}\nmCurrentFocus=Window{new u10 com.game/.Main}'
        first=foreground_candidates(text)[0]
        self.assertEqual(first['package'],'com.game');self.assertEqual(first['android_user'],10)
        self.assertEqual(foreground_candidates('unrelated com.fake/.Main'),[])

    def test_invalid_package_and_user_never_reach_shell(self):
        adb=FakeADB({})
        for package,user in [('com.game;id',None),('com.game','0;id'),('com.game',True)]:
            with self.assertRaises(ValueError):resolve_package_uid(adb,package,user)
        self.assertEqual(adb.calls,[])
