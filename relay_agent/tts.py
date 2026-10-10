"""Offline Piper speech, with a voice downloaded once into the portable Files folder."""
import hashlib
import json
import ssl
import threading
import urllib.request
import wave
from pathlib import Path

import certifi

VOICE = 'en_US-lessac-medium'
BASE_URL = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/'
MAX_TEXT = 1000


class PiperSpeech:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.voice = None
        self.lock = threading.Lock()

    def ensure_voice(self):
        folder = self.directory / 'voice'
        folder.mkdir(parents=True, exist_ok=True)
        for name in (VOICE + '.onnx', VOICE + '.onnx.json', 'MODEL_CARD'):
            target = folder / name
            if target.is_file() and target.stat().st_size:
                continue
            temporary = target.with_suffix(target.suffix + '.download')
            try:
                with urllib.request.urlopen(BASE_URL + name, timeout=60,
                                            context=ssl.create_default_context(cafile=certifi.where())) as response:
                    with temporary.open('wb') as output:
                        total = 0
                        while chunk := response.read(65536):
                            total += len(chunk)
                            if total > 150 * 1024 * 1024:
                                raise ValueError('Voice download exceeds the supported size.')
                            output.write(chunk)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return folder / (VOICE + '.onnx')

    def synthesize(self, text):
        text = text.strip()
        if not text or len(text) > MAX_TEXT:
            raise ValueError(f'Enter between 1 and {MAX_TEXT} characters.')
        with self.lock:
            cache = self.directory / 'audio'
            cache.mkdir(parents=True, exist_ok=True)
            target = cache / (hashlib.sha256((VOICE + text).encode()).hexdigest() + '.wav')
            if target.is_file():
                return target
            if self.voice is None:
                from piper import PiperVoice
                from piper.config import PiperConfig
                import onnxruntime
                model = self.ensure_voice()
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                self.voice = PiperVoice(
                    config=PiperConfig.from_dict(json.loads(Path(str(model) + '.json').read_text(encoding='utf-8'))),
                    session=onnxruntime.InferenceSession(str(model), sess_options=options,
                                                         providers=['CPUExecutionProvider']))
            temporary = target.with_suffix('.tmp')
            try:
                with wave.open(str(temporary), 'wb') as audio:
                    self.voice.synthesize_wav(text, audio)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            # Keep disk use bounded, preserving the newest reusable messages.
            for old in sorted(cache.glob('*.wav'), key=lambda p: p.stat().st_mtime, reverse=True)[100:]:
                old.unlink(missing_ok=True)
            return target
