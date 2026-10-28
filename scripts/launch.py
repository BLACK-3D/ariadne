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
from ariadne.config import load_settings, _allowed_path
from ariadne.locking import InstanceLock
from scripts.provision import local_env, start_model_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    from ariadne.box_profiles import bootstrap_profiles
    bootstrap_profiles(ROOT, Path(os.environ.get("ARIADNE_DATA_DIR", ROOT / "data")))
    settings = load_settings()  # Validate storage before starting anything.
    executable = _allowed_path(ROOT / "runtime" / "ollama" / "ollama.exe")
    if not os.environ.get("ARIADNE_OLLAMA_URL"):
        if not executable.is_file():
            raise SystemExit("The bundled Ollama runtime is missing. Run scripts/provision.py or explicitly configure ARIADNE_OLLAMA_URL for your own server.")
        start_model_server(executable, local_env(), allow_owned=True)
    # Only the provisioned factory routes opt into the fixed local service.
    # Other owner-managed services retain their own lifecycle.
    automation = _allowed_path(settings.data_dir / "automation-config.json")
    if automation.is_file() and automation.stat().st_size <= 32_768:
        entries = json.loads(automation.read_text(encoding="utf-8")).get("flows", [])
        if any(entry.get("id") == "flows_health" and entry.get("url") == "http://127.0.0.1:1880/ariadne/health" and entry.get("token_file") == "flows.token" for entry in entries):
            from scripts.local_services import start as start_flows
            start_flows()
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
    serve(on_ready=None if args.no_browser else lambda: webbrowser.open(url))


if __name__ == "__main__":
    main()
