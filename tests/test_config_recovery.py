import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from relay_agent.main import load_config, save_config


class ConfigRecoveryTests(unittest.TestCase):
    def test_corrupt_primary_restores_backup_and_preserves_bad_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'relay-config.json'
            with patch('relay_agent.main.CONFIG_PATH', path):
                save_config({'relay_token': 'test', 'clip_settings': {'a': {'volume': -30}}})
                save_config({'relay_token': 'test', 'clip_settings': {'a': {'volume': -20}}})
                path.write_text('')
                recovered = load_config()
                self.assertEqual(recovered['clip_settings']['a']['volume'], -30)
                self.assertEqual(json.loads(path.read_text())['relay_token'], 'test')
                self.assertEqual(len(list(Path(folder).glob('relay-config.corrupt-*.json'))), 1)

    def test_invalid_configs_without_backup_allow_token_setup(self):
        for content in ('', '{broken', 'null', '[]'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'relay-config.json'
                path.write_text(content)
                with patch('relay_agent.main.CONFIG_PATH', path):
                    self.assertNotIn('relay_token', load_config())
                    self.assertIsInstance(json.loads(path.read_text()), dict)

    def test_interrupted_replace_keeps_valid_primary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'relay-config.json'
            with patch('relay_agent.main.CONFIG_PATH', path):
                save_config({'relay_token': 'original'})
                with patch('relay_agent.main.os.replace', side_effect=OSError('interrupted')):
                    with self.assertRaises(OSError):
                        save_config({'relay_token': 'changed'})
                self.assertEqual(json.loads(path.read_text())['relay_token'], 'original')
                self.assertFalse(list(Path(folder).glob('*.tmp')))
