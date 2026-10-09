import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from relay_agent.main import RelayWindow


class ClipClickTests(unittest.TestCase):
    def window(self):
        window = RelayWindow.__new__(RelayWindow)
        window.listbox = Mock()
        window.listbox.get_children.return_value = []
        window.listbox.identify_row.return_value = "clip"
        window.listbox.identify_region.return_value = "cell"
        window.listbox.identify_column.return_value = "#1"
        window.listbox.bbox.return_value = (0, 0, 150, 20)
        window.listbox.winfo_width.return_value = 200
        window.file_ids = ['clip']
        window.clip_pressed_index = "clip"
        window.target_id = Mock(return_value='1')
        window.destination = Mock()
        window.destination.current.return_value = 0
        window.targets = [{'id':'1'}]
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
        window.listbox.identify_row.return_value = ""
        window.play_selected(SimpleNamespace(widget=window.listbox, x=30, y=100))
        window.send.assert_not_called()

    def test_click_in_blank_space_right_of_filename_does_not_play(self):
        window = self.window()
        window.listbox.identify_column.return_value = "#2"
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


class SortingTests(unittest.TestCase):
    def test_shared_sort_toggles_group_and_keeps_names_alphabetical(self):
        window = RelayWindow.__new__(RelayWindow)
        window.listbox = Mock()
        window.listbox.get_children.return_value = []
        window.agent = SimpleNamespace(local_files={'1':Path('Z.mp3'), '2':Path('A.mp3'), '3':Path('B.ogg')})
        window.clip_origins = {'1':'Local','2':'Local','3':'Shared'}
        window.sort_column = 'name'
        window.sort_reverse = False
        window.sort_clips('shared')
        self.assertEqual(window.file_ids, ['2','1','3'])
        window.sort_clips('shared')
        self.assertEqual(window.file_ids, ['3','2','1'])
