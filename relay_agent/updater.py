"""Public GitHub release updates for the portable Windows relay."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

import certifi

REPOSITORY = 'dasaright/RunsFormingMusic'
RELEASES_URL = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=30'
BUNDLE_NAME = 'RunsformingRelay-Windows.zip'
MANIFEST_NAME = 'relay-update.json'
UPDATE_FILES = ('RunsformingRelay.exe', 'README.md', 'Files/ffmpeg.exe', 'Files/yt-dlp.exe')
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024


def open_url(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'RunsformingRelay-Updater'})
    return urllib.request.urlopen(request, timeout=60,
        context=ssl.create_default_context(cafile=certifi.where()))


def read_json(url):
    with open_url(url) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('Update metadata is too large.')
    return json.loads(data.decode("utf-8-sig"))


def trusted_asset_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com'
            or not parsed.path.startswith(f'/{REPOSITORY}/releases/download/')
            or parsed.username or parsed.password):
        raise ValueError('Update asset is not from the relay repository.')
    return url


def find_update(current_build):
    releases = read_json(RELEASES_URL)
    candidates = []
    for release in releases:
        tag = str(release.get('tag_name', ''))
        if release.get('draft') or release.get('prerelease') or not tag.startswith('relay-'):
            continue
        try:
            build = int(tag[6:])
        except ValueError:
            continue
        assets = {asset['name']: asset['browser_download_url'] for asset in release.get('assets', [])}
        if build > current_build and BUNDLE_NAME in assets and MANIFEST_NAME in assets:
            candidates.append((build, assets))
    if not candidates:
        return None
    build, assets = max(candidates, key=lambda item: item[0])
    manifest = read_json(trusted_asset_url(assets[MANIFEST_NAME]))
    digest = str(manifest.get('sha256', ''))
    if (manifest.get('schema') != 1 or manifest.get('build') != build
            or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)):
        raise ValueError('Invalid relay update manifest.')
    return {'build': build, 'sha256': digest, 'url': trusted_asset_url(assets[BUNDLE_NAME])}


def extract_verified_bundle(archive, destination, expected_digest):
    archive = Path(archive)
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise ValueError('Relay update checksum does not match.')
    with zipfile.ZipFile(archive) as bundle:
        files = [entry for entry in bundle.infolist() if not entry.is_dir()]
        if {entry.filename for entry in files} != set(UPDATE_FILES) or len(files) != len(UPDATE_FILES):
            raise ValueError('Unexpected files in relay update.')
        if any(entry.file_size == 0 for entry in files) or sum(entry.file_size for entry in files) > MAX_DOWNLOAD_BYTES:
            raise ValueError('Invalid relay update size.')
        # Extract only the fixed allowlist; configuration and clips cannot be overwritten.
        for name in UPDATE_FILES:
            target = Path(destination) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)


def prepare_update(update):
    stage = Path(tempfile.mkdtemp(prefix='RunsformingRelay-update-'))
    try:
        archive = stage / 'download.zip'
        downloaded = 0
        with open_url(update['url']) as response, archive.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise ValueError('Relay update download is too large.')
                output.write(chunk)
        extract_verified_bundle(archive, stage / 'payload', update['sha256'])
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


INSTALL_SCRIPT = r'''param(
    [string]$Stage,
    [string]$Target,
    [int]$ProcessToWait = 0,
    [switch]$NoRestart,
    [ValidateRange(1,30)][int]$MaxAttempts = 30
)
$ErrorActionPreference = 'Stop'
if ($ProcessToWait -gt 0) { Wait-Process -Id $ProcessToWait -ErrorAction SilentlyContinue }
$files = @('RunsformingRelay.exe', 'README.md', 'Files/ffmpeg.exe', 'Files/yt-dlp.exe')
$installed = $false
try {
$backup = Join-Path $Stage 'backup'
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target 'Files') | Out-Null
$existed = @{}
foreach ($file in $files) {
    $old = Join-Path $Target $file
    $saved = Join-Path $backup $file
    $existed[$file] = Test-Path -LiteralPath $old -PathType Leaf
    if ($existed[$file]) {
        New-Item -ItemType Directory -Force -Path (Split-Path $saved) | Out-Null
        Copy-Item -LiteralPath $old -Destination $saved -Force
    }
}
$installed = $false
for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
    try {
        foreach ($file in $files) {
            Copy-Item -LiteralPath (Join-Path (Join-Path $Stage 'payload') $file) -Destination (Join-Path $Target $file) -Force
        }
        $installed = $true
        break
    } catch {
        foreach ($file in $files) {
            $old = Join-Path $Target $file
            if ($existed[$file]) { Copy-Item -LiteralPath (Join-Path $backup $file) -Destination $old -Force -ErrorAction SilentlyContinue }
            else { Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue }
        }
        if ($attempt -lt ($MaxAttempts - 1)) { Start-Sleep -Seconds 1 }
    }
}
} catch { $installed = $false }
try {
if ($installed) {
    # Clean up obsolete dependency locations after the new bundle is installed.
    foreach ($name in @('ffmpeg.exe', 'yt-dlp.exe')) {
        Remove-Item -LiteralPath (Join-Path $Target $name) -Force -ErrorAction SilentlyContinue
    }
    'Relay update installed successfully.' | Set-Content -LiteralPath (Join-Path $Target 'Files/update-status.txt')
} else {
    'Relay update failed; previous version restored.' | Set-Content -LiteralPath (Join-Path $Target 'Files/update-status.txt')
}
} catch {}
if (-not $NoRestart) {
    Start-Process -FilePath (Join-Path $Target 'RunsformingRelay.exe') -WorkingDirectory $Target
    if ($installed) { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
}
if (-not $installed) { exit 1 }
'''


def write_install_script(stage):
    script = Path(stage) / 'install.ps1'
    script.write_text(INSTALL_SCRIPT, encoding='utf-8-sig')
    return script


def launch_installer(stage, target):
    script = write_install_script(stage)
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(script), '-Stage', str(stage), '-Target', str(target), '-ProcessToWait', str(os.getpid())],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
