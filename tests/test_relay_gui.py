import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from relay_agent.main import RelayWindow


class ClipClickTests(unittest.TestCase):
    def window(self):
        window = RelayWindow.__new__(RelayWindow)
        window.listbox = Mock()
        window.listbox.nearest.return_value = 0
        window.listbox.bbox.return_value = (0, 0, 150, 20)
        window.listbox.winfo_width.return_value = 200
        window.file_ids = ['clip']
        window.clip_pressed_index = 0
        window.target_id = Mock(return_value='1')
        window.agent = SimpleNamespace(local_files={'clip': Path('clip.mp3')})
        window.status = Mock()
        window.send = Mock()
        return window

    def test_release_from_drag_drop_does_not_play_clip(self):
        window = self.window()
        window.clip_pressed_index = None
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=10))
        window.send.assert_not_called()

    def test_click_below_rows_does_not_replay_previous_selection(self):
        window = self.window()
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=100))
        window.send.assert_not_called()

    def test_click_in_blank_space_right_of_filename_does_not_play(self):
        window = self.window()
        window.play_selected(SimpleNamespace(widget=window.listbox, x=180, y=10))
        window.send.assert_not_called()

    def test_click_on_another_widget_never_plays_clip(self):
        window = self.window()
        window.play_selected(SimpleNamespace(widget=Mock(), x=30, y=10))
        window.send.assert_not_called()

    def test_file_row_click_plays_exact_file(self):
        window = self.window()
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=10))
        window.send.assert_called_once_with({'type': 'local_play', 'guild_id': '1', 'file_id': 'clip', 'title': 'clip.mp3'})


