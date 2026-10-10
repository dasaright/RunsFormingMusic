import unittest
from relay_agent.main import render_button_icon

class ButtonIconTests(unittest.TestCase):
    def test_icons_are_antialiased_and_scale_to_display_resolution(self):
        for name in ("refresh", "check", "sync", "stop", "folder", "download", "audio", "previous", "next", "soundboard", "play"):
            with self.subTest(name=name):
                for size in (28, 42, 56):
                    icon = render_button_icon(name, size)
                    self.assertEqual(icon.size, (size, size))
                    alpha = set(icon.getchannel("A").tobytes())
                    self.assertIn(0, alpha)
                    self.assertIn(255, alpha)
                    self.assertTrue(any(0 < value < 255 for value in alpha))
    def test_check_updates_has_distinct_icon(self):
        self.assertNotEqual(render_button_icon("refresh").tobytes(), render_button_icon("check").tobytes())
