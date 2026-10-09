import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from relay_agent.main import RelayWindow

class ScrollTests(unittest.TestCase):
    def window(self):
        w = RelayWindow.__new__(RelayWindow)
        w.root = Mock()
        w.listbox = Mock()
        w.listbox.yview.side_effect = [(0, .2), (.01, .21)] * 20
        w.position_clip_widgets = Mock()
        return w

    def test_fractional_wheel_deltas_accumulate_and_one_row_per_frame(self):
        w = self.window()
        for _ in range(3):
            w.smooth_scroll(SimpleNamespace(delta=-10), w.listbox)
        w.listbox.yview_scroll.assert_not_called()
        w.smooth_scroll(SimpleNamespace(delta=-10), w.listbox)
        w.listbox.yview_scroll.assert_called_once_with(1, 'units')
        w.smooth_scroll(SimpleNamespace(delta=-480), w.listbox)
        self.assertEqual(w.listbox.yview_scroll.call_count, 2)
        self.assertEqual(w.root.after.call_args.args[0], 16)

    def test_scrollbar_cancels_pending_wheel_movement(self):
        w = self.window()
        w.smooth_scroll(SimpleNamespace(delta=-480), w.listbox)
        w.scroll_clips('moveto', '.5')
        w.root.after_cancel.assert_called_once()
        self.assertEqual(w.scroll_states[w.listbox]['pending'], 0)

    def test_overlay_layout_requests_coalesced(self):
        w = self.window()
        w.schedule_clip_position()
        w.schedule_clip_position()
        w.root.after_idle.assert_called_once()
        w.flush_clip_position()
        w.position_clip_widgets.assert_called_once()
