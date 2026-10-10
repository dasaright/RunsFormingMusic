"""Local CTranslate2 translation using downloadable Argos language packages."""
import json
import shutil
import ssl
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

import certifi

LANGUAGES = {'English': 'en', 'Dutch': 'nl', 'German': 'de', 'French': 'fr', 'Bulgarian': 'bg'}
MODELS = {
    ('bg','en'): 'translate-bg_en-1_9', ('en','bg'): 'translate-en_bg-1_9',
    ('nl','en'): 'translate-nl_en-1_8', ('en','nl'): 'translate-en_nl-1_8',
    ('fr','en'): 'translate-fr_en-1_9', ('en','fr'): 'translate-en_fr-1_9',
    ('de','en'): 'translate-de_en-1_3', ('en','de'): 'translate-en_de-1_3',
}


def route(source, target):
    if source not in LANGUAGES.values() or target not in LANGUAGES.values():
        raise ValueError('Choose a supported translation language.')
    if source == target:
        return []
    return [(source,target)] if 'en' in (source,target) else [(source,'en'),('en',target)]


class LocalTranslator:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.sessions = {}

    def ensure_model(self, pair):
        target = self.directory / MODELS[pair]
        if (target / 'model' / 'model.bin').is_file():
            return target
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
            archive = Path(temporary) / 'model.zip'
            url = 'https://argos-net.com/v1/' + MODELS[pair] + '.argosmodel'
            with urllib.request.urlopen(url, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
                with archive.open('wb') as output:
                    size = 0
                    while chunk := response.read(65536):
                        size += len(chunk)
                        if size > 300*1024*1024:
                            raise ValueError('Translation model exceeds the supported size.')
                        output.write(chunk)
            extracted = Path(temporary) / 'unpacked'
            extracted.mkdir()
            with zipfile.ZipFile(archive) as bundle:
                total = 0
                for entry in bundle.infolist():
                    total += entry.file_size
                    path = (extracted / entry.filename).resolve()
                    if not path.is_relative_to(extracted.resolve()) or total > 800*1024*1024:
                        raise ValueError('Invalid translation package.')
                    bundle.extract(entry, extracted)
            model = next(extracted.rglob('model.bin'), None)
            if model is None:
                raise ValueError('Translation model is missing.')
            root = model.parent.parent
            if not (root / 'sentencepiece.model').is_file():
                raise ValueError('This translation package has an unsupported tokenizer.')
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(root), target)
        return target

    def translate_pair(self, text, pair):
        if pair not in self.sessions:
            import ctranslate2
            import sentencepiece
            folder = self.ensure_model(pair)
            metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
            session = ctranslate2.Translator(str(folder / 'model'), device='cpu',
                                            inter_threads=1, intra_threads=2)
            tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / 'sentencepiece.model'))
            if len(self.sessions) >= 2:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[pair] = (session,tokenizer,metadata.get('target_prefix',''))
        session,tokenizer,prefix = self.sessions[pair]
        # Bound each inference while preserving the full submitted text.
        chunks = []
        current = ''
        for word in text.split():
            if current and len(current)+len(word) > 350:
                chunks.append(current);current=''
            current += (' ' if current else '') + word
        if current:
            chunks.append(current)
        tokens = [tokenizer.encode(chunk,out_type=str) for chunk in chunks]
        results = session.translate_batch(tokens, target_prefix=[[prefix]]*len(tokens) if prefix else None,
                                          beam_size=4, max_decoding_length=512)
        output = []
        for result in results:
            translated = tokenizer.decode(result.hypotheses[0]).replace('▁',' ')
            if prefix and translated.startswith(prefix):
                translated = translated[len(prefix):]
            output.append(translated.strip())
        return ' '.join(output)

    def translate(self, text, source, target):
        text = text.strip()
        if not text or len(text)>1000:
            raise ValueError('Enter between 1 and 1000 characters.')
        with self.lock:
            for pair in route(source,target):
                text = self.translate_pair(text,pair)
        return text
