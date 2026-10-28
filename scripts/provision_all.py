"""Inspect the offline appliance, or explicitly repair packs using cached assets."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import load_settings, _allowed_path
from ariadne.tools import ToolRegistry
from ariadne.store import Store


def run(script, *args):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=ROOT, check=True)


def verify():
    registry = ToolRegistry(load_settings())
    registry.knowledge.store = Store(registry.settings.data_dir / "ariadne.sqlite3")
    production = registry.production.status()
    knowledge = registry.knowledge.status()
    media = registry.media.status()
    automation = registry.automation.status()
    checks = {"production." + key: bool(value) for key, value in production["features"].items()}
    checks.update({"knowledge." + key: bool(knowledge[key]["ready"]) for key in ("semantic", "docling", "monty", "kiwix")})
    checks.update({"media." + key: bool(media.get("backends", {}).get(key, {}).get("ready")) for key in ("kokoro", "sherpa", "silero", "diarization", "flux")})
    checks["automation.browser_runtime"] = bool(automation["browser"].get("runtime_cached"))
    checks["automation.flow_runtime"] = _allowed_path(ROOT / "runtime/automation/node-red/node_modules/node-red/red.js").is_file()
    checks["ollama.runtime"] = _allowed_path(ROOT / "runtime/ollama/ollama.exe").is_file()
    result = {"checks": checks, "installed_assets_ready": all(checks.values()), "runtime_downloads": False,
              "qualification": "Asset/configuration checks only. See actual task qualification reports for execution evidence.",
              "owner_setup": {"local_browser_app": automation["browser"]["ready"], "erpnext_server": automation["erpnext"]["ready"]}}
    print(json.dumps(result, indent=2))
    return result["installed_assets_ready"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-cached", action="store_true", help="Reinstall optional worker dependencies using local verified wheels only.")
    args = parser.parse_args()
    if args.install_cached:
        temporary = _allowed_path(ROOT / "runtime/tmp")
        temporary.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary), PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        for name in ("knowledge",):
            subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--upgrade", "--find-links", str(ROOT / "runtime" / name / "wheels"), "--target", str(ROOT / "runtime" / name / "python"), "--require-hashes", "-r", str(ROOT / f"requirements-{name}.txt")], cwd=ROOT, env=env, check=True)
        run("provision_knowledge.py", "--layout")
        run("provision_production.py", "--install-cached")
        run("provision_engineering.py", "--offline")
        run("provision_media.py", "--offline", "--install", "--flux")
        run("provision_automation.py", "--offline")
    raise SystemExit(0 if verify() else 1)


if __name__ == "__main__":
    main()
