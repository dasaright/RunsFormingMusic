import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from relay_agent.shared_clips import blob_sha, sync_clips

class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return self.data

class SyncTests(unittest.TestCase):
    def test_upload_download_conflict_and_ignore_non_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'new.mp3').write_bytes(b'new')
            (folder/'existing.ogg').write_bytes(b'local')
            (folder/'config.json').write_text('private')
            remote = [{'name':'existing.ogg','sha':blob_sha(b'remote'),'size':6},
                      {'name':'download.mp3','sha':blob_sha(b'download'),'size':8}]
            requests = []
            def opening(req, **kwargs):
                requests.append(req)
                self.assertEqual(req.get_header('Authorization'), 'Bearer relay-token')
                if req.method == 'PUT':
                    self.assertTrue(req.full_url.endswith('/new.mp3'))
                    return Response(b'{}')
                if req.full_url.endswith('/download.mp3'):
                    return Response(b'download')
                return Response(json.dumps({'files':remote}).encode())
            with patch('relay_agent.shared_clips.urlopen', side_effect=opening):
                result = sync_clips(folder, {'server_url':'wss://example.com/relay','relay_token':'relay-token'}, {'.mp3','.ogg'}, lambda p: True)
            self.assertEqual(result, (1,1,['existing.ogg']))
            self.assertEqual((folder/'existing.ogg').read_bytes(), b'local')
            self.assertEqual((folder/'download.mp3').read_bytes(), b'download')
            self.assertEqual(len(requests), 4)

    def test_reject_insecure_relay(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                sync_clips(directory, {'server_url':'ws://example.com/relay'}, {'.mp3'}, lambda p:True)
