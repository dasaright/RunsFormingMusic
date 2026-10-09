import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from relay_agent.main import clip_gain, LABEL_COLORS, RelayWindow

class ClipSettingsTests(unittest.TestCase):
    def test_volume_range_and_saturation(self):
        data = array("h", [1000, -1000, 20000, -20000]).tobytes()
        self.assertEqual(clip_gain(data, 0), data)
        self.assertEqual(clip_gain(data, -100), bytes(len(data)))
        self.assertEqual(list(array("h", clip_gain(data, 100))), [2000, -2000, 32767, -32768])
        self.assertEqual(clip_gain(data, 500), clip_gain(data, 100))

    def test_label_color_updates_shared_label_without_changing_other_labels(self):
        window = RelayWindow.__new__(RelayWindow)
        window.config = {"clip_settings": {"a": {"label": "Funny"}, "b": {"label": "Funny"}}, "clip_labels": {"Funny": "#ffffff", "Other": "#ffffff"}}
        window.context_clip_id = "a"
        window.save = Mock()
        window.position_clip_widgets = Mock()
        window.render_clips = Mock()
        window.listbox = Mock()
        window.change_label_color("Light Pink")
        self.assertEqual(window.config["clip_labels"]["Funny"], LABEL_COLORS["Light Pink"])
        self.assertEqual(window.config["clip_labels"]["Other"], "#ffffff")
        window.set_clip_label("b", "Other")
        self.assertEqual(window.clip_setting("a")["label"], "Funny")

    def test_real_slider_and_label_cells(self):
        import tkinter as tk
        from tkinter import ttk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Requires desktop display; Windows packaging CI runs this")
        try:
            window = RelayWindow.__new__(RelayWindow)
            window.root = root
            window.config = {"clip_labels": {"Test": LABEL_COLORS["Light Green"]}, "clip_settings": {"a": {"label": "Test"}}}
            window.save = Mock()
            window.listbox = ttk.Treeview(root, columns=("name", "volume", "shared", "label"), show="headings")
            window.listbox.pack()
            window.listbox.insert("", "end", iid="a", values=("Clip", "", "Local", ""))
            window.file_ids = ["a"]
            window.clip_widgets = {}
            root.update()
            window.position_clip_widgets()
            root.update()
            frame, label = window.clip_widgets["a"]
            self.assertEqual(label.cget("background"), LABEL_COLORS["Light Green"])
            self.assertEqual(label.cget("text"), "Test")
            scale = next(w for w in frame.winfo_children() if isinstance(w, ttk.Scale))
            self.assertEqual(scale.get(), 0)
            window.listbox.column('name', width=300)
            root.update()
            window.position_clip_widgets()
            root.update()
            self.assertEqual(frame.winfo_x(), window.listbox.bbox('a', 'volume')[0])
            self.assertEqual(label.winfo_x(), window.listbox.bbox('a', 'label')[0])
            scale.set(-100)
            root.update()
            self.assertEqual(window.clip_setting("a")["volume"], -100)
            scale.set(100)
            root.update()
            self.assertEqual(window.clip_setting("a")["volume"], 100)
        finally:
            root.destroy()

    def test_scrolling_keeps_all_preloaded_widgets_and_colors(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Requires Windows desktop")
        try:
            w = RelayWindow.__new__(RelayWindow)
            w.root = root
            w.config = {"clip_labels": {"Test": "#bde8b3"},
                        "clip_settings": {str(i): {"label": "Test"} for i in range(100)}}
            w.file_ids = list(w.config["clip_settings"])
            w.clip_widgets = {}
            w.listbox = ttk.Treeview(root, columns=("name", "volume", "shared", "label"), show="headings")
            w.listbox.pack()
            for key in w.file_ids:
                w.listbox.insert("", "end", iid=key)
            root.update()
            w.preload_clip_widgets()
            original = dict(w.clip_widgets)
            self.assertEqual(len(original), 100)
            self.assertEqual(original['99'][1].cget('background'), '#bde8b3')
            with patch('relay_agent.main.ttk.Frame', side_effect=AssertionError("Scroll created a frame")), \
                 patch('relay_agent.main.tk.Menubutton', side_effect=AssertionError("Scroll created a label")):
                for fraction in (0, .5, 1, 0):
                    w.listbox.yview_moveto(fraction)
                    w.position_clip_widgets()
                    root.update()
                    self.assertEqual(w.clip_widgets, original)
            self.assertTrue(original['0'][0].winfo_ismapped())
            self.assertFalse(original['99'][0].winfo_ismapped())
        finally:
            root.destroy()

    def test_layered_sort_shared_then_label_then_name(self):
        from relay_agent.main import sorted_clip_ids
        files = {k:Path(n+'.mp3') for k,n in [('a','Zulu'),('b','Apple'),('c','Banana'),('d','Local')]}
        origins = {'a':'Shared','b':'Shared','c':'Shared','d':'Local'}
        settings = {'a':{'label':'First'},'b':{'label':'Second'},'c':{'label':'First'}}
        self.assertEqual(sorted_clip_ids(files, origins, settings, [('shared',False),('label',False),('name',False)]), ['c','a','b','d'])
