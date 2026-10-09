"""Additive clip sync. Credentials stay on the bot; only relay authentication is sent."""
import hashlib
import ssl
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import json
import certifi

MAX_CLIP_BYTES = 20 * 1024 * 1024


def blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def sync_clips(folder, config, extensions, validate):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    server = urlsplit(config['server_url'])
    if server.scheme not in ('wss', 'https'):
        raise ValueError('Shared sync requires a secure relay URL.')
    base = 'https://' + server.netloc + '/sharedclips'
    def request(name='', data=None):
        url = base + ('/' + quote(name, safe='') if name else '')
        req = Request(url, data=data, method='PUT' if data is not None else 'GET',
                      headers={'Authorization': 'Bearer ' + config['relay_token'], 'Content-Type': 'application/octet-stream'})
        with urlopen(req, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
            content = response.read(MAX_CLIP_BYTES + 1)
            if len(content) > MAX_CLIP_BYTES:
                raise ValueError('Shared clip exceeds 20 MB.')
            return content
    remote = {f['name']: f for f in json.loads(request())['files']}
    uploaded = downloaded = 0
    conflicts = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if not 0 < path.stat().st_size <= MAX_CLIP_BYTES:
            conflicts.append(path.name + ' (size limit)')
            continue
        if path.name in remote:
            if blob_sha(path.read_bytes()) != remote[path.name]['sha']:
                conflicts.append(path.name)
            continue
        if validate(path):
            request(path.name, path.read_bytes())
            uploaded += 1
    # Re-list to include clips uploaded concurrently by another user.
    for item in json.loads(request())['files']:
        name = item['name']
        if Path(name).name != name or any(c in name for c in '/\\\\:') or Path(name).suffix.lower() not in extensions:
            continue
        path = folder / name
        if path.exists():
            continue
        content = request(name)
        if blob_sha(content) != item['sha']:
            raise ValueError('Shared clip checksum mismatch: ' + name)
        temporary = folder / ('.' + name + '.download')
        try:
            temporary.write_bytes(content)
            # Validate using the original audio extension.
            candidate = folder / ('.sync-' + name)
            temporary.replace(candidate)
            try:
                if not validate(candidate):
                    raise ValueError('Shared file is not playable audio: ' + name)
                with path.open('xb') as output:
                    output.write(content)
                downloaded += 1
            finally:
                candidate.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
    return uploaded, downloaded, conflicts
