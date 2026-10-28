"""Start the fixed local flow runtime. Editor, palette and arbitrary flow deployment are off."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path


def factory_payloads():
    """Return the fixed local routes without touching state or starting services."""
    settings = """const crypto=require('crypto');
module.exports={uiHost:'127.0.0.1',uiPort:1880,httpAdminRoot:false,disableEditor:true,
 flowFile:'flows.json',flowFilePretty:false,credentialSecret:false,
 functionExternalModules:false,externalModules:{autoInstall:false,palette:{allowInstall:false},modules:{allowInstall:false}},
 editorTheme:{projects:{enabled:false},palette:{editable:false}},
 logging:{console:{level:'warn',metrics:false,audit:false}},
 httpNodeMiddleware:function(req,res,next){
  const actual=Buffer.from(req.headers.authorization||'');
  const expected=Buffer.from('Bearer '+process.env.ARIADNE_FLOW_TOKEN);
  if(actual.length!==expected.length||!crypto.timingSafeEqual(actual,expected)){res.status(401).end();return;}
  next();
 }};
"""
    flows = [
        {"id": "ariadne_fixed", "type": "tab", "label": "Ariadne fixed local routes", "disabled": False},
        {"id": "health_in", "z": "ariadne_fixed", "type": "http in", "url": "/ariadne/health", "method": "get", "wires": [["health_payload"]]},
        {"id": "health_payload", "z": "ariadne_fixed", "type": "change", "rules": [{"t": "set", "p": "payload", "pt": "msg", "to": '{"status":"ok","service":"ariadne-fixed-flows"}', "tot": "json"}], "wires": [["health_out"]]},
        {"id": "health_out", "z": "ariadne_fixed", "type": "http response", "statusCode": "200", "headers": {"Content-Type": "application/json"}, "wires": []},
        {"id": "event_in", "z": "ariadne_fixed", "type": "http in", "url": "/ariadne/event", "method": "post", "wires": [["event_payload"]]},
        {"id": "event_payload", "z": "ariadne_fixed", "type": "change", "rules": [{"t": "set", "p": "payload.accepted", "pt": "msg", "to": "true", "tot": "bool"}], "wires": [["event_out"]]},
        {"id": "event_out", "z": "ariadne_fixed", "type": "http response", "statusCode": "200", "headers": {"Content-Type": "application/json"}, "wires": []}]
    # Match the existing platform-native write_text format without rewriting old files.
    return {"settings.js": settings.replace("\n", os.linesep).encode("utf-8"), "flows.json": json.dumps(flows).encode("utf-8")}


def prepare():
    from ariadne.atomic import publish_bytes
    from ariadne.box_profiles import regular
    from ariadne.tools import ToolRegistry
    base = _allowed_path(ROOT / "runtime/automation")
    node = _allowed_path(base / "node-v24.22.1-win-x64/node.exe")
    red = _allowed_path(base / "node-red/node_modules/node-red/red.js")
    if not node.is_file() or not red.is_file():
        raise ValueError("Run scripts/provision_automation.py --pack node-red first.")
    data = _allowed_path(ROOT / "data/flows")
    data.mkdir(parents=True, exist_ok=True)
    token_path = _allowed_path(ROOT / "data/flows.token")
    guard = object.__new__(ToolRegistry)
    with guard._directory(data.parent):
        try:
            publish_bytes(token_path, secrets.token_urlsafe(32).encode("ascii"))
        except FileExistsError:
            pass
        token = regular(token_path, 256).read_text(encoding="ascii").strip()
        if len(token) < 40 or not all(c.isalnum() or c in "-_" for c in token):
            raise ValueError("Invalid private flow token.")
    with guard._directory(data):
        for name, payload in factory_payloads().items():
            target = _allowed_path(data / name)
            try:
                publish_bytes(target, payload)
            except FileExistsError:
                if regular(target).read_bytes() != payload:
                    raise ValueError("Existing flow files differ from the fixed factory routes; no files were replaced.") from None
    return node, red, data, token


def start():
    node, red, data, token = prepare()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 1880)) == 0:
            print("Port 1880 is occupied. No process was changed; inspect existing flow service.")
            return
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC", "LANG") if key in os.environ}
    env.update({"ARIADNE_FLOW_TOKEN": token, "PATH": str(node.parent), "HOME": str(data), "USERPROFILE": str(data), "APPDATA": str(data), "LOCALAPPDATA": str(data), "TEMP": str(data), "TMP": str(data), "NO_UPDATE_NOTIFIER": "1"})
    with (data / "service.log").open("ab") as log:
        child = subprocess.Popen([str(node), str(red), "--userDir", str(data), "--settings", str(data / "settings.js"), "flows.json"], cwd=data, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(60):
        if child.poll() is not None:
            raise ValueError("Local flow runtime exited; inspect data/flows/service.log.")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 1880)) == 0:
                (data / "service.pid").write_text(str(child.pid), encoding="ascii")
                print("Fixed local flow service ready at 127.0.0.1:1880; editor disabled.")
                return
        time.sleep(.25)
    child.terminate()
    raise ValueError("Flow runtime did not become ready.")


def register():
    from ariadne.config import load_settings
    from ariadne.tools import ToolRegistry
    tools = ToolRegistry(load_settings())
    data = tools.automation.config()
    for identifier, method, path in [("flows_health", "GET", "health"), ("local_event", "POST", "event")]:
        data["flows"] = [x for x in data["flows"] if x["id"] != identifier]
        data["flows"].append({"id": identifier, "method": method, "url": "http://127.0.0.1:1880/ariadne/" + path, "token_file": "flows.token", "description": "Fixed local runtime " + path + "; no external effect"})
    tools.automation.configure(data)
    print("Fixed local routes registered; future writes still require per-action approval.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "register", "prepare"])
    choice = parser.parse_args().command
    if choice == "start":
        start()
    elif choice == "register":
        prepare()
        register()
    else:
        prepare()
