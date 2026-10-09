import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from relay_agent.main import copy_soundboard_files, scan_audio_folder


class AudioImportTests(unittest.TestCase):
    def test_scanner_accepts_supported_formats_and_rejects_invalid_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ['clip.mp3', 'clip.OGG', 'clip.wav', 'clip.flac', 'broken.ogg', 'text.txt']:
                (folder / name).write_bytes(b'content')
            with patch('relay_agent.main.subprocess.run', side_effect=lambda args, **kwargs: SimpleNamespace(returncode=int('broken' in args[5]))):
                files = scan_audio_folder(folder)
            self.assertEqual({p.name for p in files.values()}, {'clip.mp3','clip.OGG','clip.wav','clip.flac'})

    def test_drop_copies_and_preserves_original_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'source'; source.mkdir()
            target = folder / 'target'; target.mkdir()
            clip = source / 'my clip.ogg'; clip.write_bytes(b'new clip')
            (target / clip.name).write_bytes(b'existing clip')
            with patch('relay_agent.main.is_playable_audio', return_value=True):
                copied, skipped = copy_soundboard_files([str(clip)], target)
            self.assertEqual(copied, [target / 'my clip (2).ogg'])
            self.assertEqual(copied[0].read_bytes(), b'new clip')
            self.assertEqual(clip.read_bytes(), b'new clip')
            self.assertEqual((target / clip.name).read_bytes(), b'existing clip')
            self.assertFalse(skipped)

    def test_same_folder_drop_does_not_duplicate_file(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / 'clip.ogg'; clip.write_bytes(b'audio')
            with patch('relay_agent.main.is_playable_audio', return_value=True):
                copied, skipped = copy_soundboard_files([str(clip)], directory)
            self.assertFalse(copied)
            self.assertEqual(skipped, ['clip.ogg'])

    def test_bad_file_is_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('relay_agent.main.is_playable_audio', return_value=False):
                copied, skipped = copy_soundboard_files(['broken.ogg'], directory)
            self.assertFalse(copied)
            self.assertEqual(skipped, ['broken.ogg'])
