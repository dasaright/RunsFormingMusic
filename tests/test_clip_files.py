import tempfile
import unittest
from pathlib import Path
from relay_agent.main import rename_soundboard_clip

class ClipFileTests(unittest.TestCase):
    def test_rename_preserves_extension_and_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.ogg'
            path.write_bytes(b'audio')
            target=rename_soundboard_clip(path,'New Name')
            self.assertEqual(target.name,'New Name.ogg')
            self.assertEqual(target.read_bytes(),b'audio')
            self.assertFalse(path.exists())
    def test_rename_does_not_overwrite_another_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.mp3'
            other=Path(directory)/'new.mp3'
            path.write_bytes(b'old'); other.write_bytes(b'new')
            with self.assertRaises(ValueError): rename_soundboard_clip(path,'new')
            self.assertEqual(other.read_bytes(),b'new')
            for name in ['../escape', 'CON', '', 'bad:name', 'name.']:
                with self.subTest(name=name),self.assertRaises(ValueError): rename_soundboard_clip(path,name)
