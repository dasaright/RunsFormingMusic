import unittest
from relay_agent.main import youtube_stream_args

class StreamBandwidthTests(unittest.TestCase):
    def test_download_is_throttled_and_single_video(self):
        url = "https://www.youtube.com/watch?v=test&list=playlist"
        args = youtube_stream_args({}, url)
        self.assertIn("--no-playlist", args)
        self.assertEqual(args[args.index("--limit-rate")+1], "256K")
        self.assertEqual(args[args.index("--concurrent-fragments")+1], "1")
        self.assertEqual(args[args.index("--format")+1], "bestaudio/best[height<=360]")
        self.assertEqual(args[-1], url)
