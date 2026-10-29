import json
import os
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch
from slotcapture.gui import CaptureApp, invoke, event_summary


class GuiTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SLOT_CAPTURE_GUI_TESTS') == '1', 'GUI test is opt-in; normal checks never create windows')
    def test_packaged_cli_bridge_and_historical_dashboard(self):
        exe = Path(__file__).resolve().parents[1] / 'dist' / 'slot-capture.exe'
        run = exe.parent.parent / 'validation' / 'exe-acceptance' / 'block3'
        if not exe.exists() or not run.exists():
            self.skipTest('Local packaged executable and historical fixture required')
        with patch('slotcapture.gui.cli_prefix', return_value=[str(exe)]):
            status = invoke(['status', '--run', str(run)])
            events = invoke(['events', '--run', str(run), '--after', '0', '--limit', '100'])
            root = tk.Tk()
            root.withdraw()
            try:
                app = CaptureApp(root)
                app.select_run(str(run))
                app.update_monitor(app.run, status, events)
                self.assertEqual(app.cards['paid'].get(), '100 / 100')
                self.assertEqual(app.cards['money'].get(), '4,973,499,589')
                self.assertFalse(status['live'])
                self.assertIn('disabled', app.start_button.state())
                count = len(app.events.get_children())
                next_events = invoke(['events', '--run', str(run), '--after', str(app.cursor)])
                app.update_monitor(app.run, status, next_events)
                self.assertEqual(len(app.events.get_children()), count)
                app.events.selection_set(app.events.get_children()[0])
                app.show_event(None)
                self.assertIn('cursor', app.detail.get('1.0', 'end'))
                plan = json.loads((run / 'plan.json').read_text(encoding='utf-8'))
                app.fill_plan(plan)
                self.assertEqual(app.fields['bet'].get(), '13500000')
            finally:
                root.destroy()

    def test_feature_summary_explains_amount_and_cycle(self):
        text = event_summary({'kind': 'feature_confirmed', 'detail': {'feature': 'wheel', 'award': 13500000, 'paid_cycle': 27}})
        self.assertIn('转盘', text)
        self.assertIn('13,500,000', text)
        self.assertIn('第27次付费', text)
