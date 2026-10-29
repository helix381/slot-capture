import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from slotcapture.vision import find_codex,codex_status
class DiscoveryTests(unittest.TestCase):
 def test_desktop_without_path(self):
  with tempfile.TemporaryDirectory() as d:
   exe=Path(d)/'OpenAI/Codex/bin/version/codex.exe';exe.parent.mkdir(parents=True);exe.touch()
   with patch.dict(os.environ,{'LOCALAPPDATA':d}),patch('slotcapture.vision.shutil.which',return_value=None):
    self.assertEqual(find_codex(),str(exe.resolve()))
 def test_invalid_explicit_path(self):
  with patch('slotcapture.vision.subprocess.run',side_effect=FileNotFoundError('missing')):
   self.assertFalse(codex_status('missing.exe')['available'])
if __name__=='__main__':unittest.main()
