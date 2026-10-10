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

    def test_fractional_wheel_deltas_accumulate_and_animate(self):
        w = self.window()
        for _ in range(3):
            w.smooth_scroll(SimpleNamespace(delta=-10/3), w.listbox)
        w.listbox.yview_scroll.assert_not_called()
        w.smooth_scroll(SimpleNamespace(delta=-10/3), w.listbox)
        w.listbox.yview_scroll.assert_called_once_with(1, 'units')
        w.smooth_scroll(SimpleNamespace(delta=-480), w.listbox)
        self.assertEqual(w.listbox.yview_scroll.call_count, 2)
        self.assertEqual(w.root.after.call_args.args[0], 16)

    def test_wheel_notch_moves_nine_rows_in_three_frames(self):
        w = self.window()
        w.smooth_scroll(SimpleNamespace(delta=-120), w.listbox)
        state = w.scroll_states[w.listbox]
        while abs(state['pending']) >= 1:
            w.scroll_step(w.listbox, state)
        self.assertEqual([c.args for c in w.listbox.yview_scroll.call_args_list],
                         [(3, 'units')] * 3)

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

    def test_resize_coalesces_until_drag_settles(self):
        w = self.window()
        w.clip_last_size = (800, 600)
        w.listbox.winfo_width.return_value = 900
        w.listbox.winfo_height.return_value = 650
        w.defer_clip_resize()
        w.position_clip_widgets.assert_not_called()
        first = w.clip_resize_after
        w.listbox.winfo_width.return_value = 950
        w.defer_clip_resize()
        w.root.after_cancel.assert_called_once_with(first)
        self.assertEqual(w.root.after.call_count, 2)
        self.assertEqual(w.root.after.call_args.args[0], 150)
        w.schedule_clip_position()
        w.root.after_idle.assert_not_called()
        w.finish_clip_resize()
        self.assertIsNone(w.clip_resize_after)
        w.position_clip_widgets.assert_called_once()
