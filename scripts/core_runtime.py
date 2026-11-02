"""Seal only upstream Python and pinned wheel members for clean offline copies."""
from __future__ import annotations
import hashlib,json,re,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(ROOT))
from ariadne.atomic import publish_bytes
from ariadne.box_profiles import regular
from ariadne.config import _allowed_path
from scripts.refresh_offline_source import member_name

PYTHON_VERSION="3.13.17"
PYTHON_SHA256="dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5"
REQUIREMENTS=("requirements-bootstrap.txt","requirements-server.txt","requirements-documents.txt")
PROFILE="ariadne-core-v1"



def factory_profile():
    return (json.dumps({"schema":1,"default_model":"qwen3.5:9b","factory_local_flows":False,"profiles":{}},sort_keys=True,indent=2)+"\n").encode()


def sha(path):
    path=regular(_allowed_path(path),64*1024**3)
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()


def object_json(path,cap=8*1024**2):
    def pairs(rows):
        result={}
        for key,value in rows:
            if key in result:raise ValueError("Duplicate manifest key.")
            result[key]=value
        return result
    return json.loads(regular(_allowed_path(path),cap).read_text(encoding="utf-8"),object_pairs_hook=pairs)


def _unique(name,seen):
    member_name(name)
    key=name.casefold()
    if key in seen:raise ValueError("Archive member aliases are not supported.")
    seen.add(key)


def trusted_members(root):
    """Derive the allowlist from pinned upstream bytes, never a runtime walk."""
    root=_allowed_path(root)
    archive=root/"runtime/downloads/python.3.13.17.nupkg"
    if sha(archive)!=PYTHON_SHA256:raise ValueError("Pinned Python archive differs; no portable seal created.")
    bootstrap=object_json(root/"runtime/python/BOOTSTRAP-MANIFEST.json")
    if (set(bootstrap)!={"schema","version","archive_sha256","files"} or bootstrap["schema"]!=1
        or bootstrap["version"]!=PYTHON_VERSION or bootstrap["archive_sha256"]!=PYTHON_SHA256):
        raise ValueError("Python bootstrap provenance differs from the reviewed recipe.")
    upstream={};seen=set()
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist())>10000 or sum(i.file_size for i in package.infolist())>256*1024**2:
            raise ValueError("Python archive exceeds bounds.")
        for item in package.infolist():
            if not item.filename.startswith("tools/") or item.is_dir():continue
            name=item.filename[6:];_unique(name,seen)
            upstream[name]=hashlib.sha256(package.read(item)).hexdigest()
    if bootstrap["files"]!=upstream:raise ValueError("Bootstrap file inventory differs from the pinned archive.")
    files={name:value for name,value in upstream.items() if not name.startswith(("Scripts/","Lib/site-packages/"))}
    requirements={}
    for source in REQUIREMENTS:
        requirements[source]=sha(root/source)
        for row in regular(root/source,65536).read_text(encoding="utf-8").splitlines():
            if not row.strip() or row.lstrip().startswith("#"):continue
            match=re.fullmatch(r"([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)\s+--hash=sha256:([a-f0-9]{64})\s*",row)
            if not match:raise ValueError("Unsupported core wheel requirement.")
            name,version,digest=match.groups()
            choices=list((root/"runtime/wheels").glob(name.replace("-","_")+"-"+version+"-*.whl"))
            if len(choices)!=1 or sha(choices[0])!=digest:raise ValueError("Pinned core wheel missing or changed.")
            seen=set()
            with zipfile.ZipFile(choices[0]) as wheel:
                if len(wheel.infolist())>10000 or sum(i.file_size for i in wheel.infolist())>128*1024**2:raise ValueError("Wheel bounds exceeded.")
                for item in wheel.infolist():
                    if item.is_dir():continue
                    name=item.filename;_unique(name,seen)
                    if ".data/" in name:raise ValueError("This core wheel layout needs separate qualification.")
                    if name.endswith(".dist-info/RECORD"):continue
                    target="Lib/site-packages/"+name
                    value=hashlib.sha256(wheel.read(item)).hexdigest()
                    if target in files and files[target]!=value:raise ValueError("Conflicting wheel file.")
                    files[target]=value
    files["BOOTSTRAP-MANIFEST.json"]=sha(root/"runtime/python/BOOTSTRAP-MANIFEST.json")
    for name,value in files.items():
        if sha(root/"runtime/python"/name)!=value:raise ValueError("Installed core file differs from its upstream bytes: "+name)
    return files,requirements


def seal(root=ROOT):
    root=_allowed_path(root);runtime=root/"runtime/python"
    if any(_allowed_path(root/name).exists() for name in ("data/server.token","data/ariadne.sqlite3","data/host/status.json")):
        raise ValueError("Never reseal an existing private installation.")
    if Path(sys.prefix).resolve()!=runtime or Path(sys.base_prefix).resolve()!=runtime or Path(sys.executable).resolve()!=runtime/"python.exe":
        raise ValueError("Sealing requires this folder's private portable interpreter.")
    files,requirements=trusted_members(root)
    value={"schema":1,"profile":PROFILE,"python":PYTHON_VERSION,"archive_sha256":PYTHON_SHA256,
           "requirements_sha256":requirements,"sha256":files,"origin":"Pinned official NuGet Python and hash-pinned core wheel members only; no owner files."}
    path=_allowed_path(runtime/"PORTABLE-MANIFEST.json")
    payload=(json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
    if path.exists():
        if regular(path,8*1024**2).read_bytes()!=payload:raise ValueError("A different portable seal exists; inspect it before retrying.")
    else:publish_bytes(path,payload)
    return {"schema":1,"status":"sealed","profile":PROFILE,"files":len(files),"runtime":"runtime/python","downloads":False}


def sealed_files(root):
    root=_allowed_path(root);path=root/"runtime/python/PORTABLE-MANIFEST.json"
    value=object_json(path)
    if value.get("schema")!=1 or value.get("profile")!=PROFILE or value.get("archive_sha256")!=PYTHON_SHA256:
        raise ValueError("Prepare the new self-contained starter before exporting its core runtime.")
    expected,requirements=trusted_members(root)
    if value.get("sha256")!=expected or value.get("requirements_sha256")!=requirements:
        raise ValueError("Portable seal differs from its qualified upstream inventory.")
    result={"runtime/python/"+name:digest for name,digest in expected.items()}
    result["runtime/python/PORTABLE-MANIFEST.json"]=sha(path)
    return result
