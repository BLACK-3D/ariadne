"""Start the owned offline model runtime and local application."""
import argparse
import os
import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from ariadne.config import load_settings, _allowed_path, local_service_port
from ariadne.locking import InstanceLock
from scripts.provision import local_env, start_model_server


def factory_flow_requested(settings):
    """Recognize the exact factory route without changing owner configuration."""
    port = local_service_port("ARIADNE_FLOW_PORT", 1880)
    automation = _allowed_path(settings.data_dir / "automation-config.json")
    if not automation.is_file() or automation.stat().st_size > 32_768:
        return False
    value = json.loads(automation.read_text(encoding="utf-8"))
    entries = value.get("flows", []) if type(value) is dict else None
    if type(entries) is not list or any(type(entry) is not dict for entry in entries):
        raise ValueError("Local flow configuration requires owner review.")
    selected = [entry for entry in entries if entry.get("id") == "flows_health" and entry.get("token_file") == "flows.token"]
    expected = f"http://127.0.0.1:{port}/ariadne/health"
    if "ARIADNE_FLOW_PORT" in os.environ and any(entry.get("url") != expected for entry in selected):
        raise ValueError("The selected flow port differs from existing owner routes; configuration was preserved.")
    return any(entry.get("url") == expected for entry in selected)


def main(*, on_ready=None, stop_event=None, show_pairing_token=True, on_service_started=None, require_new_services=False, service_process_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    from ariadne.box_profiles import bootstrap_profiles
    bootstrap_profiles(ROOT, Path(os.environ.get("ARIADNE_DATA_DIR", ROOT / "data")))
    settings = load_settings()  # Validate storage before starting anything.
    flows_requested = factory_flow_requested(settings)
    executable = _allowed_path(ROOT / "runtime" / "ollama" / "ollama.exe")
    if not os.environ.get("ARIADNE_OLLAMA_URL"):
        if not executable.is_file():
            raise SystemExit("The bundled Ollama runtime is missing. Run scripts/provision.py or explicitly configure ARIADNE_OLLAMA_URL for your own server.")
        start_model_server(executable, local_env(), allow_owned=not require_new_services, on_process=on_service_started, create_process=service_process_factory)
    # Only the provisioned factory routes opt into the fixed local service.
    # Other owner-managed services retain their own lifecycle.
    if flows_requested:
        from scripts.local_services import start as start_flows
        start_flows(on_process=on_service_started, require_new=require_new_services, create_process=service_process_factory)
    browser_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    url = f"http://{browser_host}:{settings.port}"
    # Check local worker ownership without transmitting credentials to a listener.
    probe = InstanceLock(settings.data_dir / "server.lock")
    try:
        probe.acquire()
    except ValueError:
        print(f"An Ariadne worker already owns this data directory. Open {url}")
        print(f"The local pairing key is in {settings.data_dir / 'server.token'}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    else:
        probe.release()
    from ariadne.__main__ import main as serve
    sys.argv = [sys.argv[0], "serve"]
    def ready():
        if on_ready:
            on_ready()
        if not args.no_browser:
            webbrowser.open(url)
    serve(on_ready=ready, stop_event=stop_event, show_pairing_token=show_pairing_token)


if __name__ == "__main__":
    main()
