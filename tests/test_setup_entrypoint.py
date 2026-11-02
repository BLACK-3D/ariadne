"""Actual cached CMD/PowerShell setup; only the model module is synthetic.

Requires the official NuGet archive in runtime/downloads or the project-local
ARIADNE_TEST_PYTHON_ARCHIVE path, plus the three cached core wheels. No Internet
or model download is performed; production data/services are never used.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ariadne.config import _allowed_path
from scripts.windows_host import same_process

ARCHIVE_SHA256='dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5'
MODEL_DIGEST='56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90'
WHEELS=('pip-26.2.2-py3-none-any.whl','waitress-3.0.2-py3-none-any.whl','pypdf-6.21.3-py3-none-any.whl')
TRACKED=('Setup Ariadne.cmd','scripts/setup_windows.ps1','scripts/bootstrap_python.ps1','scripts/setup_windows.py','scripts/core_runtime.py')
CMD=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/cmd.exe'
SYNTHETIC_MODEL = r'''"""Synthetic model module: no Ollama, inference or owner-port contact."""
from pathlib import Path
import json,os,socket,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
MODEL_PINS={'qwen3.5:9b':'56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90'}
VERSION='synthetic-no-model';SHA256='0'*64
ORIGINAL_SOCKET=socket.socket
class SyntheticFactoryAdmission(ORIGINAL_SOCKET):
 def connect_ex(self,address):
  if address==('127.0.0.1',11434):return 10061
  return super().connect_ex(address)
socket.socket=SyntheticFactoryAdmission
def deny_external(event,args):
 if event in ('socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr','socket.getnameinfo'):
  if args[0] not in ('127.0.0.1','::1','localhost',None):raise AssertionError('Fixture forbids external DNS')
 if event=='socket.connect':
  address=args[1]
  if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):raise AssertionError('Fixture forbids external connections')
sys.addaudithook(deny_external)
def main(argv=None,*,on_process=None,allow_owned=True):
 assert argv==['--model','qwen3.5:9b'] and allow_owned is False
 from scripts.windows_host import process_identity
 path=ROOT/'runtime/synthetic-model-receipt.json'
 value={'synthetic_model':True,'synthetic_factory_port_absence':True,'actual_model_calls':0,'factory_port_contacted':False}
 environment={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC') if k in os.environ}
 environment.update(PATH=str(Path(os.environ['SystemRoot'])/'System32'),TEMP=str(ROOT/'runtime/tmp'),TMP=str(ROOT/'runtime/tmp'))
 child=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(30)'],cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 value.update(identity=process_identity(child.pid),created_pid=child.pid,alive_at_callback=child.poll() is None)
 original_wait=child.wait
 def recorded_wait(*args,**kwargs):
  result=original_wait(*args,**kwargs)
  value.update(wait_completed=True,exit_code=result,wait_completed_at_ns=time.time_ns())
  path.write_text(json.dumps(value,indent=2),encoding='utf8')
  return result
 child.wait=recorded_wait
 path.write_text(json.dumps(value,indent=2),encoding='utf8')
 try:on_process(child)
 except BaseException:
  child.terminate();child.wait(timeout=5);raise
'''

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def fingerprint(root):
    return {p.relative_to(root).as_posix():digest(p) for p in root.rglob('*') if p.is_file()}

def copy_regular_tree(source,destination):
    for base,folders,files in os.walk(source,followlinks=False):
        parent=Path(base)
        for name in folders[:]:
            path=parent/name
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('Fixture source contains a link.')
            if name=='__pycache__':folders.remove(name)
        for name in files:
            if name.endswith(('.pyc','.pyo')):continue
            path=parent/name
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('Fixture source contains a link.')
            target=destination/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,target)

def prepare_box(box,archive,*,owner=False,corrupt_wheel=False):
    box.mkdir(parents=True)
    for name in ('ariadne','scripts','config'):copy_regular_tree(ROOT/name,box/name)
    for source in ROOT.glob('requirements-*.txt'):shutil.copyfile(source,box/source.name)
    for name in ('Setup Ariadne.cmd','Start Ariadne.cmd','Stop Ariadne.cmd','start.ps1','pyproject.toml','LICENSE'):
        shutil.copyfile(ROOT/name,box/name)
    (box/'scripts/provision.py').write_text(SYNTHETIC_MODEL,encoding='utf8',newline='\n')
    cache=box/'runtime/downloads';cache.mkdir(parents=True)
    shutil.copyfile(archive,cache/'python.3.13.17.nupkg')
    wheels=box/'runtime/wheels';wheels.mkdir()
    for name in WHEELS:shutil.copyfile(ROOT/'runtime/wheels'/name,wheels/name)
    if corrupt_wheel:
        path=wheels/WHEELS[1];raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
    if owner:
        (box/'data').mkdir();(box/'data/server.token').write_bytes(b'explicit-private-fixture-owner')
    return box

def run_entrypoint(box,*arguments):
    environment={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC','PROCESSOR_ARCHITECTURE','PROCESSOR_IDENTIFIER') if k in os.environ}
    windows=Path(os.environ['SystemRoot'])
    environment['PATH']=os.pathsep.join(str(x) for x in (windows/'System32',windows/'System32/WindowsPowerShell/v1.0',windows))
    command='""'+str(box/'Setup Ariadne.cmd')+'" '+ ' '.join(arguments)+'"'
    return subprocess.run(subprocess.list2cmdline([str(CMD)])+' /d /s /c '+command,cwd=box,env=environment,input='\r\n',capture_output=True,text=True,encoding='utf8',errors='replace',timeout=150)

@unittest.skipUnless(os.name=='nt' and CMD.is_file(),'Native Windows qualification')
class SetupEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive=_allowed_path(os.environ.get('ARIADNE_TEST_PYTHON_ARCHIVE',str(ROOT/'runtime/downloads/python.3.13.17.nupkg')))
        if not cls.archive.is_file() or digest(cls.archive)!=ARCHIVE_SHA256:raise unittest.SkipTest('Retain the pinned official NuGet archive inside this checkout.')
        if not all((ROOT/'runtime/wheels'/name).is_file() for name in WHEELS):raise unittest.SkipTest('Retain the three pinned core wheels.')
        cls.source_sha256={name:digest(ROOT/name) for name in TRACKED}

    @contextmanager
    def box(self,**options):
        with tempfile.TemporaryDirectory(prefix='.tools-test-entrypoint-',dir=ROOT) as temporary:
            yield prepare_box(Path(temporary)/'fresh setup Ω',self.archive,**options)

    def test_real_cached_setup_seals_and_publishes_ready_after_own_child_cleanup(self):
        with self.box() as box:
            result=run_entrypoint(box,'-NoPause')
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            self.assertIn('no package-index request',result.stdout)
            ready=json.loads((box/'runtime/SETUP-READY.json').read_bytes())
            self.assertEqual(ready['status'],'ready');self.assertEqual(ready['python_mode'],'bundled')
            self.assertEqual(ready['python'],'runtime/python/python.exe');self.assertEqual(ready['model_digest'],MODEL_DIGEST)
            self.assertFalse(ready['microphone_enabled']);self.assertFalse(ready['schedules_enabled']);self.assertFalse(ready['services_left_running'])
            self.assertNotIn(str(box),json.dumps(ready));self.assertNotIn(':\\',json.dumps(ready));self.assertNotIn('owner_sid',json.dumps(ready))
            child=json.loads((box/'runtime/synthetic-model-receipt.json').read_bytes())
            self.assertTrue(child['synthetic_model']);self.assertTrue(child['synthetic_factory_port_absence'])
            self.assertFalse(child['factory_port_contacted']);self.assertEqual(child['actual_model_calls'],0)
            self.assertTrue(child['alive_at_callback']);self.assertTrue(child['wait_completed']);self.assertFalse(same_process(child['identity']))
            self.assertLessEqual(child['wait_completed_at_ns'],(box/'runtime/SETUP-READY.json').stat().st_mtime_ns)
            seal=json.loads((box/'runtime/python/PORTABLE-MANIFEST.json').read_bytes())
            self.assertEqual(seal['profile'],'ariadne-core-v1')
            self.assertEqual(set(seal['requirements_sha256']),{'requirements-bootstrap.txt','requirements-server.txt','requirements-documents.txt'})
            self.assertFalse((box/'.venv').exists());self.assertFalse((box/'runtime/SETUP-FAILED.json').exists())
            before=fingerprint(box);second=run_entrypoint(box,'-NoPause')
            self.assertEqual(second.returncode,0,second.stderr);self.assertIn('Setup already completed',second.stdout)
            self.assertEqual(before,fingerprint(box))
        self.assertEqual(self.source_sha256,{name:digest(ROOT/name) for name in TRACKED})

    def test_missing_runtime_check_is_readonly_and_cmd_keeps_nonzero_exit(self):
        with self.box() as box:
            before=fingerprint(box);result=run_entrypoint(box,'-Check','-NoPause')
            self.assertNotEqual(result.returncode,0);self.assertIn('runtime_missing',result.stdout)
            self.assertEqual(before,fingerprint(box));self.assertFalse((box/'runtime/python').exists())

    def test_owner_refusal_precedes_bootstrap_and_changes_no_files(self):
        with self.box(owner=True) as box:
            before=fingerprint(box);result=run_entrypoint(box,'-NoPause')
            self.assertNotEqual(result.returncode,0);self.assertIn('existing private installation',result.stderr)
            self.assertEqual(before,fingerprint(box));self.assertFalse((box/'runtime/python').exists())

    def test_corrupt_cached_wheel_is_held_without_replacement_or_retry(self):
        with self.box(corrupt_wheel=True) as box:
            wheel=box/'runtime/wheels'/WHEELS[1];original=wheel.read_bytes()
            result=run_entrypoint(box,'-NoPause')
            self.assertNotEqual(result.returncode,0);self.assertEqual(wheel.read_bytes(),original)
            failure=json.loads((box/'runtime/SETUP-FAILED.json').read_bytes())
            self.assertEqual(failure['stage'],'core_dependencies');self.assertFalse(failure['automatic_retry'])
            self.assertFalse((box/'runtime/SETUP-READY.json').exists());self.assertFalse((box/'runtime/synthetic-model-receipt.json').exists())
            before=fingerprint(box);again=run_entrypoint(box,'-NoPause')
            self.assertNotEqual(again.returncode,0);self.assertIn('previous setup failed',again.stderr)
            self.assertEqual(before,fingerprint(box))

if __name__=='__main__':unittest.main()
