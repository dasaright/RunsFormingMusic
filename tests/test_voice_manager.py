import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from relay_agent.tts import PiperSpeech, VOICE
from relay_agent.main import RelayWindow

class VoiceModelTests(unittest.TestCase):
    def test_download_detection_and_removal_releases_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            speech=PiperSpeech(directory)
            self.assertFalse(speech.downloaded(VOICE))
            folder=speech.voice_folder(VOICE);folder.mkdir(parents=True)
            for name in (VOICE+'.onnx',VOICE+'.onnx.json','MODEL_CARD'):
                (folder/name).write_bytes(b'file')
            self.assertTrue(speech.downloaded(VOICE))
            speech.voice=Mock();speech.remove(VOICE)
            self.assertIsNone(speech.voice)
            self.assertFalse(folder.exists())
            with self.assertRaises(ValueError):speech.voice_folder('../outside')

    def test_ctrl_backspace_deletes_previous_word_and_selection(self):
        window=RelayWindow.__new__(RelayWindow)
        window.tts_text=Mock()
        window.tts_text.tag_ranges.return_value=()
        window.tts_text.get.return_value='hello world  '
        self.assertEqual(window.delete_tts_word(),'break')
        window.tts_text.delete.assert_called_once_with('insert-7c','insert')
        window.tts_text.delete.reset_mock()
        window.tts_text.tag_ranges.return_value=('1.0','1.5')
        window.delete_tts_word()
        window.tts_text.delete.assert_called_once_with('1.0','1.5')

    def test_shift_backspace_clears_input(self):
        window=RelayWindow.__new__(RelayWindow);window.tts_text=Mock()
        self.assertEqual(window.clear_tts_text(),'break')
        window.tts_text.delete.assert_called_once_with('1.0','end')
