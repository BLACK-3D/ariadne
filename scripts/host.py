"""Start/stop Ariadne's fixed Windows host independently of a calling terminal."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.box_profiles import regular
from ariadne.config import _allowed_path
from ariadne.locking import InstanceLock
from scripts.windows_host import StopEvent, ServiceJob, detach, in_any_job, process_identity, same_process

ENVIRONMENT_KEYS = frozenset({"ARIADNE_DATA_DIR", "ARIADNE_HOST", "ARIADNE_PORT", "ARIADNE_MODEL", "ARIADNE_MAX_STEPS",
    "ARIADNE_MODEL_TIMEOUT", "ARIADNE_OFFLINE", "ARIADNE_OLLAMA_URL", "ARIADNE_ENABLE_DOCKER", "ARIADNE_DOCKER_IMAGE"})


def host_directory():
    base = _allowed_path(ROOT / "data/host")
    base.mkdir(parents=True, exist_ok=True)
    return base


def read_json(path):
    return json.loads(regular(path, 32768).read_text(encoding="utf-8"))


def write_json(path, value):
    """Publish within a pinned directory, refusing linked destinations."""
    from ariadne.tools import ToolRegistry
    with object.__new__(ToolRegistry)._directory(path.parent):
        if path.exists():
            regular(path, 32768)
        temporary = _allowed_path(path.with_name(path.name + "." + secrets.token_hex(12) + ".tmp"))
        with temporary.open("x", encoding="utf-8") as target:
            target.write(json.dumps(value, indent=2) + "\n")
        os.replace(temporary, path)


def clean_environment(overrides=None):
    overrides = overrides or {}
    if type(overrides) is not dict or set(overrides) - ENVIRONMENT_KEYS or any(type(value) is not str or len(value) > 2048 or "\x00" in value for value in overrides.values()):
        raise ValueError("Unsupported host environment.")
    if "ARIADNE_DATA_DIR" in overrides:
        selected = _allowed_path(overrides["ARIADNE_DATA_DIR"])
        if not selected.is_relative_to(ROOT):
            raise ValueError("Host data must remain inside the portable box.")
    directory = host_directory() / "environment"
    _allowed_path(directory).mkdir(parents=True, exist_ok=True)
    result = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC", "LANG", "LC_ALL") if key in os.environ}
    result.update({"PATH": str(Path(sys.executable).parent) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
                   "TEMP": str(directory), "TMP": str(directory), "HOME": str(directory), "USERPROFILE": str(directory),
                   "APPDATA": str(directory), "LOCALAPPDATA": str(directory), "PYTHONDONTWRITEBYTECODE": "1"})
    result.update(overrides)
    return result


def verified_record(record):
    if type(record) is not dict or record.get("schema") != 1 or record.get("root") != str(ROOT) or record.get("state") not in {"starting", "running", "stopping"}:
        return False
    identity = record.get("identity", {})
    return (identity.get("executable") == str(Path(sys.executable).resolve()) and
            identity.get("owner_sid") == process_identity(os.getpid())["owner_sid"] and same_process(identity))


def status():
    path = host_directory() / "status.json"
    try:
        record = read_json(path)
    except FileNotFoundError:
        return None
    return record if verified_record(record) else None


def job_free(identity):
    """Bind the job query to the same immutable process on both sides."""
    if not same_process(identity):
        raise ValueError("Host identity changed before independence verification.")
    free = not in_any_job(identity["pid"])
    if not same_process(identity):
        raise ValueError("Host identity changed during independence verification.")
    return free


def start(*, no_browser=False):
    """One serialized launch, one fixed child, bounded readiness; never retry."""
    base = host_directory()
    guard = InstanceLock(base / "launcher.lock")
    guard.acquire()
    try:
        current = status()
        if current:
            if current["state"] != "running":
                raise ValueError("Ariadne is starting/stopping; no duplicate host was created.")
            if not job_free(current["identity"]):
                raise ValueError("The existing Ariadne host remains job-bound. Stop it before starting an independent host.")
            if not no_browser:
                webbrowser.open(current["url"])
            print("Ariadne is already running independently. " + current["url"])
            return current
        # Refuse to replace a foreground/unmanaged instance; root can migrate it
        # only after quiescing its work and stopping its verified services.
        from ariadne.config import load_settings
        from ariadne.box_profiles import bootstrap_profiles
        bootstrap_profiles(ROOT, Path(os.environ.get("ARIADNE_DATA_DIR", ROOT / "data")))
        settings = load_settings()
        server_probe = InstanceLock(settings.data_dir / "server.lock")
        try:
            server_probe.acquire()
        except ValueError:
            raise ValueError("An existing foreground Ariadne owns the box. Stop that instance before switching to the independent host.") from None
        else:
            server_probe.release()
        request_id = secrets.token_hex(24)
        overrides = {key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}
        environment = clean_environment(overrides)
        request_path = base / ("start-" + request_id + ".json")
        write_json(request_path, {"schema": 1, "request_id": request_id, "environment": overrides})
        command = [str(Path(sys.executable).resolve()), "-I", "-B", str(ROOT / "scripts/host.py"), "run", "--request", request_id]
        pid, method = detach(command, str(ROOT), environment)
        identity = process_identity(pid)
        if identity["executable"] != str(Path(sys.executable).resolve()) or identity["owner_sid"] != process_identity(os.getpid())["owner_sid"]:
            raise ValueError("Independent process identity mismatch; no ready host claimed.")
        if not job_free(identity):
            raise ValueError("The broker-created process remains job-bound; no independent host is claimed.")
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            if not same_process(identity):
                raise ValueError("Independent host exited before readiness; inspect data/host/status.json and host.log.")
            try:
                record = read_json(base / "status.json")
            except FileNotFoundError:
                record = {}
            if record.get("request_id") == request_id:
                if record.get("identity") != identity:
                    raise ValueError("Host readiness identity mismatch.")
                if record.get("state") == "failed":
                    raise ValueError("Independent host startup failed; no automatic retry was attempted.")
                if record.get("state") == "running":
                    if record.get("job_free_preflight") is not True or record.get("job_free_ready") is not True or not job_free(identity):
                        raise ValueError("Host readiness did not prove native process independence.")
                    write_json(base / "last-launch.json", {"schema": 1, "request_id": request_id, "method": method, "identity": identity, "ready": True, "native_job_free": True})
                    if not no_browser:
                        webbrowser.open(record["url"])
                    print("Ariadne is running independently. " + record["url"])
                    print("Pair this browser with the key in data/server.token. Use Stop Ariadne.cmd to stop the host.")
                    return record
            time.sleep(.2)
        raise ValueError("Independent host readiness timed out; inspect its status before another launch. No retry was attempted.")
    finally:
        guard.release()


def stop():
    current = status()
    if not current:
        print("No verified independent Ariadne host is running. No process was changed.")
        return False
    if current["state"] == "starting":
        raise ValueError("Ariadne is still starting. Wait for its readiness result before stopping it.")
    event = StopEvent(current["stop_event"])
    try:
        # The immutable process identity is checked again immediately before the
        # signal. No PID-based termination or unrelated service stop is allowed.
        if not same_process(current["identity"]):
            raise ValueError("Host identity changed before stop.")
        event.signal()
    finally:
        event.close()
    deadline = time.monotonic() + 45
    while same_process(current["identity"]) and time.monotonic() < deadline:
        time.sleep(.2)
    if same_process(current["identity"]):
        raise ValueError("Graceful shutdown is still pending. No force kill was attempted.")
    final = read_json(host_directory() / "status.json")
    if final.get("request_id") != current["request_id"] or final.get("state") != "stopped":
        raise ValueError("Host exited without a complete graceful-stop receipt; inspect its local status.")
    print("Ariadne and its owned model/flow services stopped.")
    return True


def _safe_diagnostic(message):
    try:
        print(message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass  # Disk/pipe failure must never disable the stop channel.


def watch_for_stop(event, finished, stop_requested, record, publish):
    while not finished.is_set():
        if event.wait(250):
            # Control first. Persistence is useful evidence, never permission
            # to stop and never a prerequisite for releasing owned resources.
            stop_requested.set()
            record["state"] = "stopping"
            try:
                publish()
            except OSError as error:
                record["stop_metadata_error"] = type(error).__name__
                _safe_diagnostic("Ariadne stop accepted; status publication failed. Shutdown continues.")
            return


def run(request_id):
    import re
    if not re.fullmatch("[a-f0-9]{48}", request_id or ""):
        raise ValueError("Invalid host launch request.")
    base = host_directory()
    request = read_json(base / ("start-" + request_id + ".json"))
    if set(request) != {"schema", "request_id", "environment"} or request["schema"] != 1 or request["request_id"] != request_id:
        raise ValueError("Unsupported host launch request.")
    environment = clean_environment(request["environment"])
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(ROOT)
    lock = InstanceLock(base / "host.lock")
    lock.acquire()
    event = None
    services = None
    finished, stop_requested = threading.Event(), threading.Event()
    record = {"schema": 1, "root": str(ROOT), "request_id": request_id, "identity": process_identity(os.getpid()),
              "stop_event": "Local\\Ariadne-" + secrets.token_hex(24), "state": "starting", "services": []}
    log_path = base / "host.log"
    try:
        if not job_free(record["identity"]):
            record["independence_error"] = "Job-bound host refused before any application or owned service startup."
            raise ValueError("The background host remains associated with a Windows job.")
        record["job_free_preflight"] = True
        event = StopEvent(record["stop_event"], create=True)
        services = ServiceJob()
        write_json(base / "status.json", record)
        if log_path.exists():
            regular(log_path, 32 * 1024**2)
            if log_path.stat().st_size > 2 * 1024**2:
                previous = base / "host.previous.log"
                if previous.exists():
                    regular(previous, 32 * 1024**2)
                os.replace(log_path, previous)
        def service_started(process):
            record["services"].append(process_identity(process.pid))
            write_json(base / "status.json", record)
        def ready():
            if not job_free(record["identity"]):
                raise ValueError("Host became job-bound before readiness.")
            from ariadne.config import load_settings
            settings = load_settings()
            host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
            record.update(state="running", url=f"http://{host}:{settings.port}", job_free_ready=True)
            write_json(base / "status.json", record)
        watcher = threading.Thread(target=watch_for_stop, args=(event, finished, stop_requested, record,
                                   lambda: write_json(base / "status.json", record)), name="ariadne-host-stop", daemon=True)
        watcher.start()
        with log_path.open("a", encoding="utf-8", buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
            from scripts.launch import main as launch
            sys.argv = [str(ROOT / "scripts/launch.py"), "--no-browser"]
            launch(on_ready=ready, stop_event=stop_requested, show_pairing_token=False, on_service_started=service_started,
                   require_new_services=True, service_process_factory=services.popen)
        if not stop_requested.is_set():
            raise ValueError("Host server returned without an explicit stop.")
        record["state"] = "stopped"
    except BaseException as error:
        record.update(state="failed", error_type=type(error).__name__, note="Failure held; inspect the local host log. No automatic restart.")
    finally:
        finished.set()
        if 'watcher' in locals():
            watcher.join(timeout=2)
        try:
            if services:
                services.close()
        except OSError as error:
            record.update(state="failed", cleanup_error=type(error).__name__)
            _safe_diagnostic("Ariadne owned-service cleanup could not confirm complete exit.")
        try:
            write_json(base / "status.json", record)
        except OSError:
            _safe_diagnostic("Ariadne exited; final status could not be written. No complete receipt is claimed.")
        finally:
            if event:
                event.close()
            lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "run"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--request")
    args = parser.parse_args()
    try:
        if os.name != "nt":
            raise ValueError("Use start.sh or the foreground launcher on this platform.")
        if args.action == "start":
            start(no_browser=args.no_browser)
        elif args.action == "stop":
            stop()
        elif args.action == "status":
            current = status()
            print(json.dumps({"running": bool(current), "state": current["state"] if current else "stopped",
                              "independent": bool(current) and job_free(current["identity"])}))
        else:
            run(args.request)
    except (ValueError, OSError) as error:
        print("Ariadne host: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
