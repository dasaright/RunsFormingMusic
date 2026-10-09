import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from relay_agent.main import RelayWindow

class FavoriteTests(unittest.TestCase):
    def window(self):
        w = RelayWindow.__new__(RelayWindow)
        w.config = {}; w.save = Mock(); w.status = Mock(); w.send = Mock()
        w.favorites_view = Mock(); w.favorites_view.get_children.return_value = []
        w.favorites_view.identify_region.return_value = 'cell'
        w.queue_context_id = 'track'
        w.last_music = {'playlist': [{'id': 'track', 'title': 'Song', 'url': 'https://youtu.be/abc'}]}
        return w

    def test_favorite_persists_and_deduplicates_and_double_click_enqueues(self):
        w = self.window()
        w.favorite_song(); w.favorite_song()
        self.assertEqual(w.config['music_favorites'], [{'title': 'Song', 'url': 'https://youtu.be/abc', 'playlist': False}])
        w.save.assert_called_once()
        key = next(iter(w.favorite_items))
        w.favorites_view.identify_row.return_value = key
        w.play_favorite(SimpleNamespace(x=5, y=5))
        w.send.assert_called_once_with({'type': 'music_enqueue', 'guild_id': None,
                                        'url': 'https://youtu.be/abc', 'playlist': False})
        w.favorite_context_id = key; w.remove_favorite()
        self.assertEqual(w.config['music_favorites'], [])

    def test_saved_playlist_queues_as_playlist_and_header_does_not_play(self):
        w = self.window()
        w.save_music_favorite({'title': 'Mix', 'url': 'https://youtube.com/playlist?list=PL1', 'playlist': True})
        w.favorites_view.identify_row.return_value = next(iter(w.favorite_items))
        w.play_favorite(SimpleNamespace(x=5, y=5))
        self.assertTrue(w.send.call_args.args[0]['playlist'])
        w.send.reset_mock(); w.favorites_view.identify_region.return_value = 'heading'
        w.play_favorite(SimpleNamespace(x=5, y=5)); w.send.assert_not_called()

    def test_remove_queue_sends_exact_track_without_touching_favorites(self):
        w = self.window(); w.refresh_music = Mock()
        w.remove_queue_song()
        w.send.assert_called_once_with({'type': 'music_control', 'guild_id': None,
                                        'action': 'remove', 'track_id': 'track'})
        self.assertEqual(w.config, {})
