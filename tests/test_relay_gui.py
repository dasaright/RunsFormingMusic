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
        window.send.assert_called_once_with({'type': 'local_play', 'guild_id': None, 'file_id': 'clip', 'title': 'clip.mp3'})


class SortingTests(unittest.TestCase):
    def test_shared_sort_toggles_group_and_keeps_names_alphabetical(self):
        window = RelayWindow.__new__(RelayWindow)
        window.listbox = Mock()
        window.listbox.get_children.return_value = []
        window.agent = SimpleNamespace(local_files={'1':Path('Z.mp3'), '2':Path('A.mp3'), '3':Path('B.ogg')})
        window.clip_origins = {'1':'Local','2':'Local','3':'Shared'}
        window.clip_widgets = {}
        window.root = Mock()
        window.config = {}
        window.sort_keys = [('name', False)]
        window.sort_column = 'name'
        window.sort_reverse = False
        window.sort_clips('shared')
        self.assertEqual(window.file_ids, ['3','2','1'])
        window.sort_clips('shared')
        self.assertEqual(window.file_ids, ['2','1','3'])

class SongClickTests(unittest.TestCase):
    def test_song_click_sends_stable_song_id(self):
        window=RelayWindow.__new__(RelayWindow)
        window.queue_view=Mock()
        window.queue_view.identify_row.return_value='song-id'
        window.queue_view.identify_region.return_value='cell'
        window.queue_pressed='song-id'
        window.last_music={'playlist':[]}
        window.target_id=Mock(return_value='1')
        window.send=Mock()
        window.song_selected(SimpleNamespace(widget=window.queue_view,x=20,y=20))
        window.send.assert_called_once_with({'type':'music_control','guild_id':'1','action':'select','track_id':'song-id'})

class HeaderDragTests(unittest.TestCase):
    def test_saved_order_validation_and_move_past_shared(self):
        from relay_agent.main import clip_column_order, reordered_columns
        order = clip_column_order(None)
        self.assertEqual(reordered_columns(order, 'volume', 'shared'), ['name','shared','volume','label'])
        self.assertEqual(clip_column_order(['name','name','volume','label']), order)

    def test_drag_changes_order_without_sorting_or_playing(self):
        window = RelayWindow.__new__(RelayWindow)
        window.root = Mock(); window.save = Mock(); window.send = Mock(); window.sort_clips = Mock()
        window.listbox = Mock(); window.config = {}; window.column_order = ['name','volume','shared','label']
        window.header_drag = {'column':'volume','x':100,'moved':True}
        window.listbox.identify_column.return_value = '#3'
        window.play_selected(SimpleNamespace(widget=window.listbox,x=250,y=10))
        self.assertEqual(window.config['clip_column_order'], ['name','shared','volume','label'])
        window.sort_clips.assert_not_called(); window.send.assert_not_called()

    def test_reordered_name_is_only_playable_column(self):
        window = ClipClickTests().window()
        window.column_order = ['shared','volume','label','name']
        window.listbox.identify_column.return_value = '#1'
        window.play_selected(SimpleNamespace(widget=window.listbox,x=30,y=10))
        window.send.assert_not_called()
        window.clip_pressed_index = 'clip'
        window.listbox.identify_column.return_value = '#4'
        window.play_selected(SimpleNamespace(widget=window.listbox,x=30,y=10))
        window.send.assert_called_once()

    def test_real_tabs_and_reordered_control_positions(self):
        import tkinter as tk
        from unittest.mock import patch
        from relay_agent.main import create_root
        try:
            root = create_root()
        except tk.TclError:
            self.skipTest('Requires desktop display; Windows packaging CI runs this')
        try:
            with patch('threading.Thread.start'):
                window = RelayWindow(root, {})
            window.save = Mock(); window.send = Mock()
            root.update()
            self.assertEqual(len(window.notebook.tabs()), 2)
            self.assertFalse(hasattr(window, 'destination'))
            self.assertGreater(window.tab_buttons[0].winfo_height(), window.tab_buttons[1].winfo_height())
            window.notebook.select(1)
            window.agent.local_files = {'clip':Path('Test.mp3')}
            window.clip_origins = {'clip':'Shared'}
            window.render_clips(); root.update()
            self.assertGreater(window.tab_buttons[1].winfo_height(), window.tab_buttons[0].winfo_height())
            self.assertFalse(root.tk.getboolean(window.clip_widgets['clip'][1].cget('indicatoron')))
            self.assertEqual(window.toolbar_buttons[0].master, window.tab_buttons[0].master)
            window.header_drag = {'column':'volume','x':100,'moved':True}
            # Drop on the Shared header using its actual on-screen coordinate.
            box = window.listbox.bbox('clip','shared')
            window.play_selected(SimpleNamespace(widget=window.listbox,x=box[0]+box[2]//2,y=5))
            root.update()
            self.assertEqual(window.column_order, ['name','shared','volume','label'])
            frame, label = window.clip_widgets['clip']
            self.assertEqual(frame.winfo_x(), window.listbox.bbox('clip','volume')[0])
            self.assertEqual(label.winfo_x(), window.listbox.bbox('clip','label')[0])
            window.notebook.select(0); root.update()
            window.notebook.select(1); root.update()
            self.assertTrue(window.listbox.winfo_ismapped())
        finally:
            root.destroy()
