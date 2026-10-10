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
VOICE_OPTIONS = {
    'Lessac — US English': 'en_US-lessac-medium',
    'Amy — US English': 'en_US-amy-medium',
    'Ryan — US English': 'en_US-ryan-medium',
    'Sam — US English': 'en_US-sam-medium',
    'Alan — British English': 'en_GB-alan-medium',
    'Alba — British English': 'en_GB-alba-medium',
}
BASE_URL = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/' 
MAX_TEXT = 1000


class PiperSpeech:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.voice = None
        self.loaded_voice = VOICE
        self.lock = threading.Lock()

    def ensure_voice(self, voice_id=VOICE):
        locale, name, quality = voice_id.split('-')
        base_url = BASE_URL + f'{locale.split("_")[0]}/{locale}/{name}/{quality}/'
        folder = self.directory / 'voice' if voice_id == VOICE else self.directory / 'voices' / voice_id
        folder.mkdir(parents=True, exist_ok=True)
        for name in (voice_id + '.onnx', voice_id + '.onnx.json', 'MODEL_CARD'):
            target = folder / name
            if target.is_file() and target.stat().st_size:
                continue
            temporary = target.with_suffix(target.suffix + '.download')
            try:
                with urllib.request.urlopen(base_url + name, timeout=60,
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
        return folder / (voice_id + '.onnx')

    def synthesize(self, text, voice_id=VOICE):
        if voice_id not in VOICE_OPTIONS.values():
            raise ValueError("Choose a supported Piper voice.")
        text = text.strip()
        if not text or len(text) > MAX_TEXT:
            raise ValueError(f'Enter between 1 and {MAX_TEXT} characters.')
        with self.lock:
            cache = self.directory / 'audio'
            cache.mkdir(parents=True, exist_ok=True)
            target = cache / (hashlib.sha256((voice_id + text).encode()).hexdigest() + '.wav')
            if target.is_file():
                return target
            if self.voice is None or self.loaded_voice != voice_id:
                from piper import PiperVoice
                from piper.config import PiperConfig
                import onnxruntime
                model = self.ensure_voice(voice_id)
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                self.voice = PiperVoice(
                    config=PiperConfig.from_dict(json.loads(Path(str(model) + '.json').read_text(encoding='utf-8'))),
                    session=onnxruntime.InferenceSession(str(model), sess_options=options,
                                                         providers=['CPUExecutionProvider']))
                self.loaded_voice = voice_id
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
