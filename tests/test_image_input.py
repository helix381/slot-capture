from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch, Mock
from PIL import Image
from slotcapture.image_input import ImageInbox, RuleImageDialog


class Value:
    def __init__(self):self.value=''
    def set(self,value):self.value=value
    def get(self):return self.value


class ImageInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.inbox=ImageInbox(self.root)
    def tearDown(self):self.temp.cleanup()

    def dialog(self):
        panel=RuleImageDialog.__new__(RuleImageDialog)
        panel.inbox=self.inbox;panel.items=[];panel.status=Value();panel.refresh=Mock()
        return panel

    def test_file_import_validates_content_and_uses_stable_copy(self):
        source=self.root/'规则截图 中文.png';Image.new('RGBA',(24,32),(10,30,50,90)).save(source)
        first=self.inbox.import_file(source);second=self.inbox.import_file(source)
        self.assertEqual(first,second);self.assertNotEqual(first,str(source))
        source.unlink()
        with Image.open(first) as result:
            self.assertEqual(result.size,(24,32));self.assertEqual(result.mode,'RGBA')

    def test_same_jpeg_with_different_extensions_is_deduplicated(self):
        first=self.root/'one.jpg';second=self.root/'two.jpeg'
        Image.new('RGB',(10,10),'green').save(first);shutil.copyfile(first,second)
        self.assertEqual(self.inbox.import_file(first),self.inbox.import_file(second))

    def test_bitmap_paste_is_deduplicated_without_reading_real_clipboard(self):
        panel=self.dialog();bitmap=Image.new('RGB',(30,20),'green')
        with patch('slotcapture.image_input.ImageGrab.grabclipboard',return_value=bitmap):
            self.assertEqual(panel.paste(),'break');panel.paste()
        self.assertEqual(len(panel.items),1)
        self.assertIn('没有重复添加',panel.status.get())

    def test_copied_files_can_be_pasted_and_text_is_not_imported(self):
        source=self.root/'one.png';Image.new('RGB',(10,10),'green').save(source)
        panel=self.dialog()
        with patch('slotcapture.image_input.ImageGrab.grabclipboard',return_value=[str(source)]):panel.paste()
        self.assertEqual(len(panel.items),1)
        with patch('slotcapture.image_input.ImageGrab.grabclipboard',return_value=None):panel.paste()
        self.assertEqual(len(panel.items),1);self.assertIn('剪贴板中没有图片',panel.status.get())

    def test_drop_uses_tcl_file_list_parser_for_spaces_and_unicode(self):
        panel=self.dialog();panel.add_files=Mock()
        paths=(r'C:\图片 文件\规则一.png',r'C:\图片 文件\规则二.png')
        panel.window=Mock();panel.window.tk.splitlist.return_value=paths
        event=Mock(data='{C:/图片 文件/规则一.png} {C:/图片 文件/规则二.png}')
        panel.drop(event)
        panel.add_files.assert_called_once_with(paths)

    def test_bad_image_and_unsupported_content_are_rejected(self):
        bad=self.root/'bad.png';bad.write_text('not an image')
        with self.assertRaises(ValueError):self.inbox.import_file(bad)
        gif=self.root/'false.png';Image.new('RGB',(10,10)).save(gif,format='GIF')
        with self.assertRaises(ValueError):self.inbox.import_file(gif)

    def test_limit_preserves_existing_images_and_accepts_only_on_confirm(self):
        panel=self.dialog()
        for n in range(12):panel.append(str(n),str(n))
        with self.assertRaises(ValueError):panel.append('extra','extra')
        self.assertFalse(panel.append('0','again'))
        panel.on_accept=Mock();panel.window=Mock()
        panel.accept();panel.on_accept.assert_called_once_with([str(n) for n in range(12)])

    def test_mixed_file_batch_preserves_good_images_and_reports_errors(self):
        source=self.root/'good.png';Image.new('RGB',(10,10)).save(source)
        panel=self.dialog();panel.add_files([str(source),str(self.root/'missing.png')])
        self.assertEqual(len(panel.items),1);self.assertIn('missing.png',panel.status.get())
