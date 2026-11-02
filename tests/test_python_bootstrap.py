"""Native pre-Python bootstrap tests; synthetic archives only, no network/model."""
from pathlib import Path
from contextlib import contextmanager
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/bootstrap_python.ps1"
SHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
REQUIRED = ("python.exe", "python313.dll", "vcruntime140.dll", "vcruntime140_1.dll",
            "DLLs/_ssl.pyd", "DLLs/_sqlite3.pyd", "Lib/site-packages/pip/__init__.py", "LICENSE.txt")
PS = r"""param([string]$Source,[string]$Box,[string]$Mode,[string]$Hash,[long]$Size)
$ErrorActionPreference='Stop'
. $Source
$script:bootstrapRoot=$Box
$script:bootstrapSha256=$Hash
$script:bootstrapBytes=$Size
try {
 if ($Mode -eq 'names') {
  [void][Reflection.Assembly]::LoadWithPartialName('System.IO.Compression')
  $stream=[IO.File]::OpenRead((Join-Path $Box 'input.zip'))
  $archive=[IO.Compression.ZipArchive]::new($stream,[IO.Compression.ZipArchiveMode]::Read)
  try { $items=Get-BootstrapEntries $archive; @{status='accepted';count=$items.Count}|ConvertTo-Json -Compress }
  finally { $archive.Dispose();$stream.Dispose() }
 } elseif ($Mode -eq 'junction') {
  $script:bootstrapNative=New-BootstrapNative
  $target=Join-Path $Box 'ordinary-target';[void][IO.Directory]::CreateDirectory($target)
  $link=Join-Path $Box 'junction'
  [void](New-Item -ItemType Junction -Path $link -Target $target)
  try { [void](Pin-BootstrapDirectory $link) }
  finally { [IO.Directory]::Delete($link) }
 } elseif ($Mode -eq 'excluded') {
  $script:bootstrapNative=New-BootstrapNative
  [void](Pin-BootstrapDirectory 'E:\never-read')
 } elseif ($Mode -eq 'unc') {
  $script:bootstrapNative=New-BootstrapNative
  [void](Pin-BootstrapDirectory '\\nonexistent\never-read\box')
 } else {
  if ($Mode -eq 'mock_probe') { function Test-BootstrapRuntime { param($Directory,$Temporary); return @{mock_only=$true} } }
  if ($Mode -eq 'probe_failure') { function Test-BootstrapRuntime { param($Directory,$Temporary); throw 'synthetic runtime probe failure' } }
  Invoke-PythonBootstrap -CheckOnly:($Mode -eq 'check')|ConvertTo-Json -Depth 5 -Compress
 }
} catch { @{status='rejected';error=$_.Exception.GetType().Name;message=$_.Exception.Message}|ConvertTo-Json -Compress }
finally { if($null -ne $bootstrapNative){for($i=$bootstrapHandles.Count-1;$i -ge 0;$i--){[void]$bootstrapNative.GetMethod('CloseHandle').Invoke($null,[object[]]@($bootstrapHandles[$i]))}} }
"""


def archive_bytes(extra=(), *, missing=None, unsupported_method=False):
    output=io.BytesIO()
    backslash_names=[]
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as bundle:
        for name in REQUIRED:
            if name != missing: bundle.writestr('tools/'+name, ('synthetic '+name).encode())
        for entry in extra:
            if isinstance(entry, zipfile.ZipInfo): bundle.writestr(entry,b'bad')
            else:
                if '\\' in entry: backslash_names.append(entry)
                bundle.writestr(entry,b'bad')
    raw=bytearray(output.getvalue())
    # Python normalizes backslashes while writing on Windows; preserve the
    # malicious raw archive spelling in both local and central ZIP headers.
    for name in backslash_names:
        raw=raw.replace(name.replace('\\','/').encode(),name.encode())
    if unsupported_method:
        # Entry.Open rejects this compression method after complete inventory.
        local=raw.find(b'PK\x03\x04')
        central=raw.find(b'PK\x01\x02')
        struct.pack_into('<H',raw,local+8,99)
        struct.pack_into('<H',raw,central+10,99)
    return bytes(raw)


@unittest.skipUnless(os.name=='nt' and SHELL.is_file(), 'Native Windows PowerShell fixture')
class PythonBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='.tools-test-python-bootstrap-',dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.box=Path(self.temporary.name)
        self.driver=self.box/'driver.ps1';self.driver.write_text(PS,encoding='utf8',newline='\n')
        self.cache=self.box/'runtime/downloads';self.cache.mkdir(parents=True)
        self.payload=archive_bytes()
        self.archive=self.cache/'python.3.13.17.nupkg';self.archive.write_bytes(self.payload)

    def invoke(self,mode,*,raw=None):
        raw=self.payload if raw is None else raw
        env={key:os.environ[key] for key in ('SystemRoot','WINDIR','COMSPEC','PROCESSOR_ARCHITECTURE') if key in os.environ}
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        result=subprocess.run([str(SHELL),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(self.driver),str(SOURCE),str(self.box),mode,hashlib.sha256(raw).hexdigest(),str(len(raw))],cwd=self.box,env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def names(self,raw):
        (self.box/'input.zip').write_bytes(raw)
        return self.invoke('names',raw=raw)

    def assert_rejected(self,result):
        self.assertEqual(result['status'],'rejected',result)
        self.assertFalse((self.box/'runtime/python').exists())

    def test_readonly_check_never_creates_runtime_or_download_partial(self):
        before={p.relative_to(self.box).as_posix():p.read_bytes() for p in self.box.rglob('*') if p.is_file()}
        result=self.invoke('check')
        self.assertEqual(result['status'],'missing')
        self.assertFalse(result['downloads']);self.assertFalse(result['processes_started'])
        self.assertEqual(before,{p.relative_to(self.box).as_posix():p.read_bytes() for p in self.box.rglob('*') if p.is_file()})

    def test_owner_token_is_preserved_and_normal_bootstrap_refused(self):
        path=self.box/'data/server.token';path.parent.mkdir();path.write_bytes(b'private-test-sentinel')
        self.assert_rejected(self.invoke('mock_probe'))
        self.assertEqual(path.read_bytes(),b'private-test-sentinel')
        self.assertEqual(self.invoke('check')['status'],'existing_installation')

    def test_existing_runtime_is_never_overwritten(self):
        path=self.box/'runtime/python/owner.txt';path.parent.mkdir();path.write_bytes(b'keep')
        result=self.invoke('mock_probe')
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(path.read_bytes(),b'keep')

    def test_wrong_checksum_is_held_without_extraction(self):
        self.archive.write_bytes(b'x'*len(self.payload))
        self.assert_rejected(self.invoke('mock_probe'))
        self.assertEqual(self.archive.read_bytes(),b'x'*len(self.payload))
        self.assertFalse((self.box/'runtime/python-bootstrap-3.13.17.partial').exists())

    def test_existing_download_partial_blocks_retry(self):
        path=Path(str(self.archive)+'.partial');path.write_bytes(b'interrupted')
        self.assert_rejected(self.invoke('mock_probe'))
        self.assertEqual(path.read_bytes(),b'interrupted')

    def test_existing_extraction_partial_blocks_retry(self):
        path=self.box/'runtime/python-bootstrap-3.13.17.partial';path.mkdir();(path/'evidence').write_bytes(b'keep')
        self.assert_rejected(self.invoke('mock_probe'))
        self.assertEqual((path/'evidence').read_bytes(),b'keep')

    def test_traversal_and_absolute_names_are_refused(self):
        for name in ('tools/../escape','/tools/escape','tools/./file','tools//file','tools/C:/file'):
            with self.subTest(name=name): self.assert_rejected(self.names(archive_bytes((name,))))

    def test_backslashes_ads_device_names_and_trailing_aliases_are_refused(self):
        for name in ('tools\\escape','tools/file:stream','tools/CON.txt','tools/AUX','tools/name.','tools/name '):
            with self.subTest(name=name):self.assert_rejected(self.names(archive_bytes((name,))))

    def test_duplicate_case_aliases_and_file_directory_collisions_are_refused(self):
        for extra in (('tools/PYTHON.EXE',),('tools/Lib',),('tools/directory','tools/directory/file')):
            with self.subTest(extra=extra):self.assert_rejected(self.names(archive_bytes(extra)))

    def test_archive_symlink_and_reparse_attributes_are_refused(self):
        for attributes in ((0o120777<<16),0x400):
            entry=zipfile.ZipInfo('tools/link');entry.create_system=3;entry.external_attr=attributes
            with self.subTest(attributes=attributes):self.assert_rejected(self.names(archive_bytes((entry,))))

    def test_archive_entry_and_expansion_bounds_are_enforced(self):
        raw=bytearray(self.payload)
        first=raw.find(b'PK\x01\x02')
        struct.pack_into('<I',raw,first+24,33*1024*1024)
        self.assert_rejected(self.names(bytes(raw)))
        raw=bytearray(self.payload);offset=0
        while True:
            offset=raw.find(b'PK\x01\x02',offset)
            if offset<0:break
            struct.pack_into('<I',raw,offset+24,20*1024*1024);offset+=4
        self.assert_rejected(self.names(bytes(raw)))
        self.assert_rejected(self.names(archive_bytes(tuple('tools/extra'+str(i) for i in range(5001)))))

    def test_missing_required_native_or_pip_asset_is_refused(self):
        for missing in ('python.exe','vcruntime140_1.dll','Lib/site-packages/pip/__init__.py'):
            with self.subTest(missing=missing):self.assert_rejected(self.names(archive_bytes(missing=missing)))

    def test_hardlinked_archive_is_refused(self):
        os.link(self.archive,self.box/'archive-alias')
        self.assert_rejected(self.invoke('mock_probe'))

    def test_native_junction_path_is_refused_without_touching_target(self):
        self.assert_rejected(self.invoke('junction'))
        self.assertTrue((self.box/'ordinary-target').is_dir())
        self.assertFalse((self.box/'junction').exists())

    def test_excluded_drive_and_unc_are_refused_before_access(self):
        self.assert_rejected(self.invoke('excluded'))
        self.assert_rejected(self.invoke('unc'))

    def test_native_probe_failure_holds_stage_and_never_publishes_runtime(self):
        self.assert_rejected(self.invoke('probe_failure'))
        self.assertTrue((self.box/'runtime/python-bootstrap-3.13.17.partial').is_dir())
        self.assert_rejected(self.invoke('mock_probe'))

    def test_unsupported_compression_fails_during_extraction_and_holds_stage(self):
        raw=archive_bytes(unsupported_method=True);self.archive.write_bytes(raw)
        self.assert_rejected(self.invoke('mock_probe',raw=raw))
        self.assertTrue((self.box/'runtime/python-bootstrap-3.13.17.partial').is_dir())

    def test_synthetic_qualified_tree_manifest_is_exact_and_relpath_only(self):
        result=self.invoke('mock_probe')
        self.assertEqual(result['status'],'installed')
        manifest=json.loads((self.box/'runtime/python/BOOTSTRAP-MANIFEST.json').read_bytes())
        self.assertEqual(set(manifest),{'schema','version','archive_sha256','files'})
        self.assertEqual(manifest['schema'],1);self.assertEqual(manifest['version'],'3.13.17')
        self.assertEqual(manifest['archive_sha256'],hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(set(manifest['files']),set(REQUIRED))
        for path,digest in manifest['files'].items():
            self.assertEqual(digest,hashlib.sha256((self.box/'runtime/python'/path).read_bytes()).hexdigest())
        self.assertFalse((self.box/'runtime/python-bootstrap-3.13.17.partial').exists())


class BootstrapSourceContractTests(unittest.TestCase):
    def test_pinned_official_runtime_has_no_system_installer_or_python_discovery(self):
        text=SOURCE.read_text(encoding='utf8')
        self.assertIn("$bootstrapVersion = '3.13.17'",text)
        self.assertIn("$bootstrapBytes = [long]14826465",text)
        self.assertIn('dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5',text)
        self.assertIn('https://api.nuget.org/v3-flatcontainer/python/3.13.17/python.3.13.17.nupkg',text)
        self.assertNotIn('Invoke-Expression',text)
        self.assertNotIn('Get-Command python',text)
        self.assertNotIn('Get-Command py.exe',text)
        self.assertNotIn('msiexec',text.lower())
        self.assertIn("$request.AllowAutoRedirect = $false",text)
        self.assertIn("$request.Proxy = $null",text)


if __name__=='__main__':unittest.main()
