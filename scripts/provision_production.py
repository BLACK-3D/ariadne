"""Provision fixed production engines from an explicitly cached, hash-locked wheelhouse.

Runtime calls never fetch models, packages or executables. Optional native binary
registration requires a path inside Yikes, a version, and its exact SHA-256.
This provisioner never searches PATH, system drives, or E:.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import secrets
import struct
import subprocess
import sys
import stat
import urllib.request
import zipfile
from pathlib import Path
from pathlib import PurePosixPath

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ariadne.config import _allowed_path
from ariadne.production_tools import PINNED,NATIVE,_local,MANIFEST,VENDOR


def _write(path,data):
    path=_allowed_path(path); path.relative_to(ROOT)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): _local(path)
    temporary=path.with_name(path.name+'.part-'+secrets.token_hex(8))
    try:
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists(): temporary.unlink()


def verify_wheels():
    lock_path=_local(ROOT/'config/production-assets.json',100000)
    lock=json.loads(lock_path.read_text(encoding='utf-8'))
    if lock.get('schema')!=1 or lock.get('python')!='3.13' or lock.get('platform')!='win_amd64':
        raise ValueError('The cached production lock targets Windows x64 Python 3.13.')
    paths=[]; names={}
    for item in lock['packages']:
        name=item['name'].lower().replace('_','-'); filename=item['filename']
        if name in names or Path(filename).name!=filename or not filename.endswith('.whl'):
            raise ValueError('Invalid wheel lock entry.')
        path=_local(ROOT/'runtime/production/wheels'/filename,100000000)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']:
                raise ValueError('A cached wheel differs from its recorded PyPI SHA-256.')
        names[name]=item['version']; paths.append(path)
    if any(names.get(name)!=version for name,version in PINNED.items()):
        raise ValueError('Production engine pins differ from the reviewed profile.')
    return paths,hashlib.sha256(lock_path.read_bytes()).hexdigest()


def _fetch(item,target):
    target=_allowed_path(target); target.relative_to(ROOT/'runtime/production')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        with _local(target,600_000_000).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256']: return target
        raise ValueError('A cached production asset has the wrong digest; move it aside before retrying.')
    temporary=target.with_name(target.name+'.part-'+secrets.token_hex(8))
    try:
        total=0; digest=hashlib.sha256()
        with urllib.request.urlopen(item['url'],timeout=90) as source, temporary.open('xb') as output:
            while chunk:=source.read(1024*1024):
                total+=len(chunk)
                if total>600_000_000: raise ValueError('Production download exceeded 600 MB.')
                digest.update(chunk); output.write(chunk)
        if digest.hexdigest()!=item['sha256']: raise ValueError('Downloaded production asset differs from its published SHA-256.')
        os.rename(temporary,target)
    finally:
        if temporary.exists(): temporary.unlink()
    return target


def fetch_wheels():
    lock=json.loads(_local(ROOT/'config/production-assets.json',100000).read_text(encoding='utf-8'))
    for item in lock['packages']:
        if Path(item['filename']).name!=item['filename'] or not item['url'].startswith('https://files.pythonhosted.org/'):
            raise ValueError('Wheel source must be the pinned PyPI file host.')
        _fetch(item,ROOT/'runtime/production/wheels'/item['filename'])
    return {'downloaded_wheels':len(lock['packages'])}


def blender_python(fetch=False,install=False):
    lock=_local(ROOT/'config/production-blender-assets.json',100000)
    packages=json.loads(lock.read_text(encoding='utf-8'))
    if type(packages) is not list or not any(item.get('name')=='bpy' and item.get('version')=='5.2.3' for item in packages):
        raise ValueError('The Blender wheel profile must contain exact official bpy 5.2.3.')
    paths=[]
    for item in packages:
        filename=item['filename']
        if Path(filename).name!=filename or not filename.endswith('.whl') or not item['url'].startswith('https://files.pythonhosted.org/'):
            raise ValueError('Invalid Blender PyPI wheel source.')
        path=ROOT/'runtime/production/blender-wheels'/filename
        if fetch: _fetch(item,path)
        with _local(path,600000000).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']: raise ValueError('A Blender wheel differs from the official PyPI digest.')
        paths.append(path)
    vendor=_allowed_path(ROOT/'runtime/production/blender-python')
    if install:
        vendor.mkdir(parents=True,exist_ok=True)
        temporary=_allowed_path(ROOT/'runtime/tmp'); temporary.mkdir(parents=True,exist_ok=True)
        environment=dict(os.environ,TEMP=str(temporary),TMP=str(temporary),PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile','--upgrade','--target',str(vendor),*[str(p) for p in paths]],check=True,env=environment,cwd=ROOT)
    installed={d.metadata['Name'].lower().replace('_','-'):d.version for d in importlib.metadata.distributions(path=[str(vendor)])}
    if any(installed.get(item['name'].lower().replace('_','-'))!=item['version'] for item in packages):
        raise ValueError('Install the complete cached Blender wheel profile before enabling the adapter.')
    _write(ROOT/'runtime/production/blender-wheels-lock.json',lock.read_bytes())
    return {'version':'5.2.3','lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}


def _archive_path(name):
    parts=PurePosixPath(name).parts
    if name.startswith(('/', '\\')) or any(p in {'.','..'} or ':' in p or '\\' in p for p in parts):
        raise ValueError('Archive contains an unsafe path.')


def _extract_zip(archive,destination):
    destination=_allowed_path(destination); destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        entries=source.infolist()
        if len(entries)>50000 or sum(x.file_size for x in entries)>3_000_000_000:
            raise ValueError('Production archive exceeds extraction limits.')
        for item in entries:
            _archive_path(item.filename)
            if stat.S_ISLNK(item.external_attr>>16): raise ValueError('Archive links are not accepted.')
        for item in entries:
            _allowed_path(destination/item.filename)
            source.extract(item,destination)


def fetch_native(name):
    lock=json.loads(_local(ROOT/'config/production-assets.json',100000).read_text(encoding='utf-8'))
    item=lock['native'][name]; archive=_fetch(item,ROOT/'runtime/production'/item['archive'])
    destination=_allowed_path(ROOT/'runtime/production'/item['directory']); destination.mkdir(parents=True,exist_ok=True)
    expected=_allowed_path(destination/item['relative_executable'])
    if MANIFEST.exists():
        prior=json.loads(_local(MANIFEST,500000).read_text(encoding='utf-8')).get('binaries',{}).get(name,{})
        if prior.get('version')==item['version'] and expected.is_file():
            with _local(expected).open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()==prior.get('sha256'):
                    return name,str(expected),item['version'],prior['sha256']
    if item['format']=='zip': _extract_zip(archive,destination)
    elif item['format']=='7z':
        tar=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/tar.exe'
        if not tar.is_file(): raise ValueError('Portable FreeCAD extraction requires the Windows bundled bsdtar.')
        listing=subprocess.check_output([str(tar),'-tf',str(archive)],text=True,timeout=120)
        names=listing.splitlines()
        if len(names)>50000: raise ValueError('Too many archive entries.')
        for value in names: _archive_path(value)
        subprocess.run([str(tar),'-xf',str(archive),'-C',str(destination)],check=True,timeout=600)
    else:
        extract_item=lock['native']['lessmsi']; extract_archive=_fetch(extract_item,ROOT/'runtime/production'/extract_item['archive'])
        extract_dir=_allowed_path(ROOT/'runtime/production/lessmsi'); _extract_zip(extract_archive,extract_dir)
        extractor=_local(extract_dir/'lessmsi.exe')
        temporary=_allowed_path(ROOT/'runtime/tmp'); temporary.mkdir(parents=True,exist_ok=True)
        environment=dict(os.environ,TEMP=str(temporary),TMP=str(temporary))
        with (destination/'extraction.log').open('wb') as log:
            subprocess.run([str(extractor),'x',str(archive),str(destination)+os.sep],check=True,timeout=600,stdout=log,stderr=subprocess.STDOUT,env=environment)
    executable=_local(expected)
    with executable.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return name,str(executable),item['version'],digest


def provision(install=False,register=None,version=None,digest=None):
    if os.name!='nt' or struct.calcsize('P')!=8 or sys.version_info[:2]!=(3,13):
        raise ValueError('The production wheel profile is qualified for Windows x64 Python 3.13.')
    paths,lock_hash=verify_wheels()
    if install:
        _allowed_path(VENDOR).mkdir(parents=True,exist_ok=True)
        temp=_allowed_path(ROOT/'runtime/tmp'); temp.mkdir(parents=True,exist_ok=True)
        environment=dict(os.environ,TEMP=str(temp),TMP=str(temp),PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile','--upgrade','--target',str(VENDOR),*[str(path) for path in paths]],check=True,env=environment,cwd=ROOT)
    installed={d.metadata['Name'].lower().replace('_','-'):d.version for d in importlib.metadata.distributions(path=[str(VENDOR)])}
    if any(installed.get(name)!=version for name,version in PINNED.items()):
        raise ValueError('Install the verified cached wheel profile first using --install-cached.')
    try:
        manifest=json.loads(_local(MANIFEST,500000).read_text(encoding='utf-8'))
    except FileNotFoundError:
        manifest={'schema':1,'binaries':{}}
    manifest.update(packages=PINNED,wheel_lock_sha256=lock_hash,python='3.13',platform='win_amd64')
    if register:
        name,path=register
        if name not in NATIVE or not version or not digest or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Native registration requires a known engine, version and lowercase SHA-256.')
        path=_local(path)
        path.relative_to(ROOT/'runtime/production')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
                raise ValueError('Native binary differs from the supplied SHA-256.')
        manifest.setdefault('binaries',{})[name]={'path':path.relative_to(ROOT).as_posix(),'version':version,'sha256':digest}
    _write(MANIFEST,(json.dumps(manifest,indent=2)+'\n').encode())
    return {'configured':True,'packages':PINNED,'binaries':sorted(manifest.get('binaries',{})),'wheel_lock_sha256':lock_hash,'runtime_downloads':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-cached',action='store_true',help='Install verified cached wheels into an isolated worker-only directory; never change the core environment.')
    parser.add_argument('--register',nargs=2,metavar=('ENGINE','LOCAL_EXE'))
    parser.add_argument('--version')
    parser.add_argument('--sha256')
    parser.add_argument('--fetch-wheels',action='store_true',help='Explicitly download the exact hash-locked PyPI wheel profile; provisioning only.')
    parser.add_argument('--fetch-native',choices=['ffmpeg','freecad','libreoffice'],help='Explicitly download, hash-check and extract a pinned portable native engine inside Yikes; no MSI install or system registration.')
    parser.add_argument('--fetch-blender-python',action='store_true',help='Explicitly download the pinned official bpy and dependency wheel profile into the isolated production cache.')
    parser.add_argument('--install-cached-blender',action='store_true',help='Install only hash-verified cached Blender wheels into the isolated render worker directory.')
    args=parser.parse_args()
    try:
        if args.fetch_wheels: fetch_wheels()
        register,version,digest=args.register,args.version,args.sha256
        if args.fetch_native:
            name,path,version,digest=fetch_native(args.fetch_native); register=[name,path]
        result=provision(args.install_cached,register,version,digest)
        if args.fetch_blender_python or args.install_cached_blender:
            blender=blender_python(args.fetch_blender_python,args.install_cached_blender)
            manifest=json.loads(_local(MANIFEST,500000).read_text(encoding='utf-8'))
            manifest.setdefault('python_engines',{})['blender']=blender
            _write(MANIFEST,(json.dumps(manifest,indent=2)+'\n').encode())
            result['blender_python']=blender
        print(json.dumps(result,indent=2))
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as error:
        parser.exit(1,'Production provisioning failed: '+str(error)+'\n')


if __name__=='__main__': main()
