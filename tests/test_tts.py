import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from relay_agent.tts import PiperSpeech
from relay_agent.main import RelayAgent

class SpeechTests(unittest.TestCase):
    def test_validation_cache_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            speech=PiperSpeech(folder)
            voice=Mock()
            def synthesize(text,audio):
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(22050);audio.writeframes(b'\0\0'*2205)
            voice.synthesize_wav.side_effect=synthesize
            speech.voice=voice
            first=speech.synthesize(' hello ')
            self.assertEqual(first,speech.synthesize('hello'))
            voice.synthesize_wav.assert_called_once()
            self.assertGreater(first.stat().st_size,44)
            for text in ['', ' '*5, 'a'*1001]:
                with self.assertRaises(ValueError):speech.synthesize(text)
            self.assertFalse(list(Path(folder).rglob('*.tmp')))

class SpeechPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_uses_bot_overlay_not_direct_or_shared_library(self):
        with tempfile.TemporaryDirectory() as folder,patch('relay_agent.main.FILES_DIR',Path(folder)):
            agent=RelayAgent({})
            agent.websocket=Mock();agent.send_json=AsyncMock()
            agent.speech.synthesize=Mock(return_value=Path(folder)/('a'*64+'.wav'))
            await agent.play_text('Hello')
            payload=agent.send_json.call_args.args[0]
            self.assertEqual(payload['type'],'local_play')
            self.assertIsNone(payload['guild_id'])
            self.assertIn(payload['file_id'],agent.tts_files)
            self.assertEqual(agent.local_files,{})

    async def test_disconnected_and_busy_do_not_generate(self):
        agent=RelayAgent({});agent.speech.synthesize=Mock()
        await agent.play_text('hello')
        agent.speech.synthesize.assert_not_called()
        agent.websocket=Mock()
        async with agent.tts_lock:
            await agent.play_text('hello')
        agent.speech.synthesize.assert_not_called()
