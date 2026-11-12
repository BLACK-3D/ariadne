"""Build a clean offline core from a prepared starter; never export owner state."""
from __future__ import annotations
import argparse,hashlib,json,os,re,stat,sys,time,zipfile
from pathlib import Path
ROOT=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(ROOT))
from ariadne.atomic import commit_staged,publish_bytes
from ariadne.box_profiles import regular
from ariadne.config import _allowed_path
from ariadne.tools import ToolRegistry
from scripts import core_runtime,provision
from scripts.export_source import _member
from scripts.refresh_offline_source import member_name, PUBLIC_CATALOG_SHA256


def source_files(root):
    value=core_runtime.object_json(root/"SOURCE-MANIFEST.json")
    if set(value)!={"version","sha256"} or type(value["sha256"]) is not dict or not 1<=len(value["sha256"])<=10000:
        raise ValueError("Use the published starter with its complete source manifest.")
    files={};seen=set()
    for name,digest in value["sha256"].items():
        _member(name)
        if name.casefold() in seen or not re.fullmatch(r"[a-f0-9]{64}",digest):raise ValueError("Source manifest alias or invalid hash.")
        seen.add(name.casefold());files[name]=(root/name,digest)
    # Factory core has no optional engines, owner selections or flow service.
    factory=core_runtime.factory_profile()
    required={"config/box-profiles.json","config/public-models.json",*core_runtime.REQUIREMENTS}
    if not required <= set(files):raise ValueError("Factory profiles, public model catalog and every core requirement must be source-bound.")
    if files["config/public-models.json"][1] != PUBLIC_CATALOG_SHA256:raise ValueError("Public model catalog differs from the reviewed pin.")
    for name,(path,digest) in files.items():
        actual=core_runtime.sha(path)
        if actual!=digest and not (name=="config/box-profiles.json" and regular(path,262144).read_bytes()==factory):
            raise ValueError("Source differs from the reviewed starter: "+name)
    files["config/box-profiles.json"]=(factory,hashlib.sha256(factory).hexdigest())
    value["sha256"]["config/box-profiles.json"]=files["config/box-profiles.json"][1]
    payload=(json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
    files["SOURCE-MANIFEST.json"]=(payload,hashlib.sha256(payload).hexdigest())
    return files


def model_files(root):
    if core_runtime.sha(root/"config/public-models.json") != PUBLIC_CATALOG_SHA256:raise ValueError("Public model catalog pin mismatch.")
    catalog=core_runtime.object_json(root/"config/public-models.json")
    choices=[item for item in catalog["models"] if item["tag"]=="qwen3.5:9b"]
    if len(choices)!=1:raise ValueError("Default public model pin missing.")
    model=choices[0];name="data/models/manifests/registry.ollama.ai/library/qwen3.5/9b"
    digest=core_runtime.sha(root/name)
    if digest not in {model["manifest"]["sha256"],provision.MODEL_PINS["qwen3.5:9b"]}:raise ValueError("Default model manifest is not reviewed.")
    manifest=core_runtime.object_json(root/name)
    if manifest.get("schemaVersion")!=2 or type(manifest.get("layers")) is not list or not 1<=len(manifest["layers"])<=32:raise ValueError("Invalid model manifest.")
    approved={}
    for item in model["blobs"]:
        key=item["sha256"];size=item["bytes"]
        if type(key) is not str or not re.fullmatch(r"[a-f0-9]{64}",key) or key in approved or type(size) is not int or not 0<size<=16*1024**3:
            raise ValueError("Invalid public model blob pin.")
        approved[key]=size
    files={name:(root/name,digest)}
    for item in [manifest["config"],*manifest["layers"]]:
        raw=item.get("digest")
        if type(raw) is not str or not re.fullmatch(r"sha256:[a-f0-9]{64}",raw) or type(item.get("size")) is not int:
            raise ValueError("Invalid model blob reference.")
        key=raw[7:]
        if key not in approved or approved[key]!=item["size"]:raise ValueError("Unreviewed public model blob.")
        name="data/models/blobs/sha256-"+key
        if regular(_allowed_path(root/name),16*1024**3).stat().st_size!=approved[key]:raise ValueError("Model blob size mismatch.")
        files[name]=(root/name,key)
    return files


def ollama_files(root):
    package=_allowed_path(root/"runtime/downloads"/f"ollama-{provision.VERSION}.zip")
    if core_runtime.sha(package)!=provision.SHA256:raise ValueError("Original pinned Ollama download is missing or changed. Build from the original prepared starter.")
    files={};seen=set()
    with zipfile.ZipFile(package) as archive:
        if len(archive.infolist())>10000 or sum(i.file_size for i in archive.infolist())>15_000_000_000:raise ValueError("Ollama archive bounds exceeded.")
        for item in archive.infolist():
            if item.is_dir():continue
            name=item.filename;member_name(name)
            if name.casefold() in seen or stat.S_ISLNK(item.external_attr>>16):raise ValueError("Unsafe runtime archive member.")
            seen.add(name.casefold())
            with archive.open(item) as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
            key="runtime/ollama/"+name
            if regular(_allowed_path(root/key),16*1024**3).stat().st_size!=item.file_size:raise ValueError("Installed Ollama file size mismatch.")
            files[key]=(root/key,digest)
    if "runtime/ollama/ollama.exe" not in files:raise ValueError("Model runtime executable missing.")
    marker=provision.SHA256.encode()+b"\n"
    files["runtime/ollama/.archive-sha256"]=(marker,hashlib.sha256(marker).hexdigest())
    return files


def inventory(root, *, with_vision=False):
    root=_allowed_path(root)
    files=source_files(root)
    for name,digest in core_runtime.sealed_files(root).items():files[name]=(root/name,digest)
    files.update(ollama_files(root));files.update(model_files(root))
    if with_vision:
        from scripts.provision_vision import verified_files
        files.update(verified_files(root))
    # No install cache, databases, tokens, schedules, logs or owner profiles.
    return files


def export(destination,*,root=ROOT,with_vision=False):
    root=_allowed_path(root);destination=_allowed_path(destination)
    if not destination.is_relative_to(root) or destination.suffix.lower()!=".zip":raise ValueError("Export must stay inside this Ariadne folder.")
    partial=_allowed_path(destination.with_suffix(".zip.partial"))
    sidecars=[destination,destination.with_suffix(".zip.sha256"),destination.with_suffix(".zip.receipt.json"),partial]
    if any(path.exists() for path in sidecars):raise FileExistsError("An export/partial/receipt exists. Inspect it; choose a new output name.")
    files=inventory(root,with_vision=with_vision)
    destination.parent.mkdir(parents=True,exist_ok=True)
    guard=object.__new__(ToolRegistry);rows=[]
    with guard._directory(destination.parent):
        with partial.open("xb") as outer,zipfile.ZipFile(outer,"w",compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for name,(source,expected) in sorted(files.items()):
                member_name(name);digest=hashlib.sha256();size=0
                info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_STORED;info.external_attr=(stat.S_IFREG|0o644)<<16
                with archive.open(info,"w",force_zip64=True) as target:
                    if type(source) is bytes:
                        target.write(source);digest.update(source);size=len(source)
                    else:
                        with guard._directory(source.parent) as directory:
                            with os.fdopen(guard._open_read(source,directory),"rb") as stream:
                                before=os.fstat(stream.fileno())
                                while chunk:=stream.read(1024*1024):target.write(chunk);digest.update(chunk);size+=len(chunk)
                                after=os.fstat(stream.fileno())
                                if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns) or size!=before.st_size:raise ValueError("Export source changed while streaming.")
                if digest.hexdigest()!=expected:raise ValueError("Export file hash mismatch: "+name)
                rows.append({"path":name,"bytes":size,"sha256":expected})
            manifest={"schema":1,"profile":"ariadne-offline-core-v1","default_model":"qwen3.5:9b","private_state_included":False,"optional_packs":["vision"] if with_vision else [],"files":rows}
            archive.writestr("CORE-MANIFEST.json",json.dumps(manifest,sort_keys=True,indent=2)+"\n")
            archive.close();outer.flush();os.fsync(outer.fileno())
        commit_staged(partial,destination)
        digest=core_runtime.sha(destination)
        receipt={"schema":1,"status":"completed","archive":destination.name,"profile":"ariadne-offline-core-v1","optional_packs":["vision"] if with_vision else [],"bytes":destination.stat().st_size,"sha256":digest,"files":len(rows)+1,"private_state_included":False,"launch":"Start Ariadne.cmd","setup_required":False,"internet_required_for_local_use":False}
        publish_bytes(destination.with_suffix(".zip.sha256"),(digest+"  "+destination.name+"\n").encode())
        publish_bytes(destination.with_suffix(".zip.receipt.json"),(json.dumps(receipt,indent=2)+"\n").encode())
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path)
    parser.add_argument("--with-vision",action="store_true",help="Include the fully verified optional local CPU vision pack")
    args=parser.parse_args()
    path=args.output or ROOT/"dist"/("ariadne-offline-core-"+time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())+".zip")
    try:print(json.dumps(export(path,with_vision=args.with_vision),indent=2));return 0
    except (OSError,ValueError,KeyError,zipfile.BadZipFile) as error:
        print("Offline copy stopped: "+str(error)+". No automatic retry; inspect any partial archive.",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
