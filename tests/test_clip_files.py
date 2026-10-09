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

class ActiveClipEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_rename_waits_for_ffmpeg_exit_and_leaves_other_clips_running(self):
        import asyncio
        import os
        import shutil
        import wave
        from unittest.mock import AsyncMock, patch
        from relay_agent.main import RelayAgent
        ffmpeg=os.getenv('TEST_FFMPEG_PATH') or shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg is required; packaging CI runs this with bundled FFmpeg')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'playing.wav'
            with wave.open(str(path),'wb') as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(48000)
                output.writeframes(b'\0'*48000*4*10)
            agent=RelayAgent({})
            agent.local_files={'clip':path}
            agent.send_json=AsyncMock()
            started=asyncio.Event()
            async def send(frame): started.set()
            agent.send_binary=send
            other=asyncio.create_task(asyncio.sleep(30))
            agent.clip_tasks['other']=other
            agent.clip_file_ids['other']='other-file'
            with patch('relay_agent.main.FFMPEG_PATH',Path(ffmpeg)):
                task=asyncio.create_task(agent.stream('request','local:clip'))
                agent.clip_tasks['request']=task
                agent.clip_file_ids['request']='clip'
                try:
                    await asyncio.wait_for(started.wait(),10)
                    await asyncio.wait_for(agent.edit_clip_file('clip',path,'renamed'),10)
                    self.assertTrue(task.done())
                    self.assertTrue((Path(directory)/'renamed.wav').is_file())
                    self.assertFalse(path.exists())
                    self.assertFalse(other.done())
                finally:
                    task.cancel(); other.cancel()
                    await asyncio.gather(task,other,return_exceptions=True)
