import unittest
from unittest.mock import Mock
from relay_agent.main import clipboard_youtube_link, RelayWindow

class ClipboardTests(unittest.TestCase):
    def test_link_in_text_is_parsed(self):
        self.assertEqual(clipboard_youtube_link('Play this: https://www.youtube.com/watch?v=abc&list=PL123.'),'https://www.youtube.com/watch?v=abc&list=PL123')
        self.assertEqual(clipboard_youtube_link('youtu.be/abc'),'https://youtu.be/abc')
    def test_ignore_non_youtube_and_lookalike_domains(self):
        for text in ['nothing','https://youtube.com.evil.com/watch?v=x','https://example.com/file']:
            self.assertIsNone(clipboard_youtube_link(text))
    def test_only_first_link_is_selected(self):
        self.assertEqual(clipboard_youtube_link('https://youtu.be/a https://youtu.be/b'),'https://youtu.be/a')
    def test_paste_sends_checkbox_state_and_save_is_local(self):
        window=RelayWindow.__new__(RelayWindow)
        window.root=Mock()
        window.root.clipboard_get.return_value='https://youtu.be/a'
        window.destination=Mock()
        window.destination.current.return_value=0
        window.targets=[{'id':'1'}]
        window.status=Mock()
        window.send=Mock()
        window.paste_playlist=Mock()
        window.paste_playlist.get.return_value=True
        window.config={}
        window.save=Mock()
        window.save_paste_setting()
        self.assertTrue(window.config['paste_playlist'])
        window.paste_youtube_link()
        window.send.assert_called_once_with({'type':'music_enqueue','guild_id':None,'url':'https://youtu.be/a','playlist':True})
