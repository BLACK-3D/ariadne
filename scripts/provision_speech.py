"""Provision the pinned fast offline voice inside this box only.

Default: verify cached assets without network. --download explicitly fetches
public pinned files; --install installs only the two cached wheels into an
isolated optional-package folder. --configure merges only the Piper backend
into the local media profile. Existing owner voice selections are preserved.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path
from ariadne.atomic import publish_bytes


def official_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in {None, 443} and not parsed.fragment
            and (host in {'files.pythonhosted.org', 'raw.githubusercontent.com', 'huggingface.co', 'hf.co'}
                 or host.endswith(('.huggingface.co', '.hf.co'))))


class SpeechRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not official_url(new_url):
            raise ValueError('Speech downloads may redirect only to official HTTPS asset hosts.')
        return super().redirect_request(request, fp, code, message, headers, new_url)


def regular(path):
    path = _allowed_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Speech provisioning requires regular, unlinked files.')
    return path


def confined(name):
    path = _allowed_path(ROOT / name)
    if not path.is_relative_to(ROOT / 'runtime/media/piper'):
        raise ValueError('A speech asset escaped its fixed destination.')
    return path


def catalog():
    value = json.loads(regular(ROOT/'config/speech-assets.json').read_bytes())
    if value.get('schema') != 1 or type(value.get('assets')) is not list or len(value['assets']) != 6:
        raise ValueError('Unexpected speech asset catalog.')
    names = set()
    for item in value['assets']:
        if set(item) != {'path', 'bytes', 'sha256', 'url'} or item['path'] in names:
            raise ValueError('Unexpected or duplicate speech catalog entry.')
        confined(item['path'])
        names.add(item['path'])
        if type(item['bytes']) is not int or not 0 < item['bytes'] < 200_000_000 or not re.fullmatch('[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid speech asset size or checksum.')
        parsed = urlsplit(item['url'])
        if not official_url(item['url']) or parsed.hostname not in {'files.pythonhosted.org', 'huggingface.co', 'raw.githubusercontent.com'}:
            raise ValueError('Unexpected public speech asset origin.')
    return value


def verify(item):
    target = regular(confined(item['path']))
    with target.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if info.st_size != item['bytes'] or hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
            raise ValueError('A cached speech asset differs from its accepted pin.')


def fetch(item):
    target = confined(item['path'])
    if target.exists():
        verify(item)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = confined(item['path']+'.partial')
    if temporary.exists():
        raise ValueError('A previous partial is preserved; inspect it before another download.')
    context = ssl.create_default_context()
    import certifi
    context.load_verify_locations(certifi.where())
    digest, size = hashlib.sha256(), 0
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SpeechRedirects(), urllib.request.HTTPSHandler(context=context))
    with opener.open(item['url'], timeout=45) as source, temporary.open('xb') as output:
        if not official_url(source.geturl()):
            raise ValueError('Speech download returned an unexpected origin.')
        while block := source.read(1024*1024):
            size += len(block)
            if size > item['bytes']:
                raise ValueError('Speech download exceeded its declared size.')
            digest.update(block)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if size != item['bytes'] or digest.hexdigest() != item['sha256']:
        raise ValueError('Speech download failed integrity; the partial is preserved.')
    from ariadne.atomic import commit_staged
    commit_staged(temporary, target)


def package_files(directory):
    result = {}
    for base, folders, files in os.walk(directory, followlinks=False):
        for name in list(folders):
            path = Path(base)/name
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked speech package directory.')
            if name == '__pycache__' or path == directory/'bin':
                folders.remove(name)
        for name in files:
            if name in {'direct_url.json'} or name.endswith('.pyc'):
                continue
            path = regular(Path(base)/name)
            with path.open('rb') as source:
                result[path.relative_to(directory).as_posix()] = {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(source, 'sha256').hexdigest()}
    return result


def verify_installation():
    target = _allowed_path(ROOT/'runtime/media/piper-python')
    manifest = confined('runtime/media/piper/INSTALL-MANIFEST.json')
    if not target.is_dir() or not manifest.is_file():
        raise ValueError('Install the verified cached speech wheels before configuring this voice.')
    saved = json.loads(regular(manifest).read_bytes())
    if (saved.get('schema') != 1 or saved.get('catalog_sha256') != hashlib.sha256((ROOT/'config/speech-assets.json').read_bytes()).hexdigest()
            or saved.get('packages') != {'piper-tts': '1.8.0', 'pathvalidate': '3.3.1'} or saved.get('files') != package_files(target)):
        raise ValueError('Existing optional speech package files differ; no overwrite was attempted.')


def install(value):
    if sys.platform != 'win32':
        raise ValueError('This pinned fast-speech wheel set targets Windows x64.')
    target = _allowed_path(ROOT/'runtime/media/piper-python')
    manifest = confined('runtime/media/piper/INSTALL-MANIFEST.json')
    if target.exists():
        verify_installation()
        return
    from ariadne.worker import _environment
    command = [sys.executable, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
               'install', '--no-input', '--no-index', '--no-deps', '--no-compile', '--require-hashes',
               '--find-links', str(ROOT/'runtime/media/piper/wheels'), '--target', str(target),
               '-r', str(ROOT/'requirements-speech.txt')]
    subprocess.run(command, env=_environment(None), cwd=ROOT, check=True, timeout=120,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    files = package_files(target)
    if not files:
        raise ValueError('The optional speech installation is empty.')
    publish_bytes(manifest, (json.dumps({'schema': 1, 'catalog_sha256': hashlib.sha256((ROOT/'config/speech-assets.json').read_bytes()).hexdigest(),
        'packages': {'piper-tts': '1.8.0', 'pathvalidate': '3.3.1'}, 'files': files}, indent=2)+'\n').encode())


def configure(value):
    verify_installation()
    from ariadne.media_tools import load_profile
    target = _allowed_path(ROOT/'data/media-config.json')
    previous = regular(target).read_bytes() if target.exists() else None
    profile = load_profile(ROOT/'data')
    if (regular(target).read_bytes() if target.exists() else None) != previous:
        raise ValueError('The media profile changed while it was read; no update was prepared.')
    selected = {'assets': value['profile']}
    existing = profile['backends'].get('piper')
    if existing == selected:
        return
    if existing is not None:
        raise ValueError('An existing Piper profile was preserved. Review it before replacing its assets.')
    profile['backends']['piper'] = selected
    backup = _allowed_path(ROOT/'data/media-config.pre-fast-speech.json')
    if previous is not None:
        publish_bytes(backup, previous)
    temporary = _allowed_path(ROOT/'data/media-config.fast-speech.new')
    publish_bytes(temporary, (json.dumps(profile, indent=2)+'\n').encode())
    if (regular(target).read_bytes() if target.exists() else None) != previous:
        raise ValueError('The media profile changed while preparing its update; the new file is preserved.')
    os.replace(temporary, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--configure', action='store_true')
    options = parser.parse_args()
    value = catalog()
    for item in value['assets']:
        if options.download:
            fetch(item)
        verify(item)
    if options.install:
        install(value)
    if options.configure:
        configure(value)
    print('Pinned local speech assets verified; no microphone or playback was used.')


if __name__ == '__main__':
    main()
