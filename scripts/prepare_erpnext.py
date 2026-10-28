"""Prepare the optional ERPNext stack; never install Docker or change its storage."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import _allowed_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-images", action="store_true", help="Explicit online Docker pull/save; requires Docker storage already under this box.")
    args = parser.parse_args()
    data = _allowed_path(ROOT / "data/erpnext")
    data.mkdir(parents=True, exist_ok=True)
    for name in ("db", "sites", "logs", "redis"):
        _allowed_path(data / name).mkdir(exist_ok=True)
    envfile = _allowed_path(data / "bootstrap.env")
    if not envfile.exists():
        fd = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write("ARIADNE_ERP_DATA=" + data.as_posix() + "\n")
            target.write("ARIADNE_ERP_DB_PASSWORD=" + secrets.token_hex(32) + "\n")
            target.write("ARIADNE_ERP_ADMIN_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
    if args.cache_images:
        executable = shutil.which("docker")
        if not executable:
            raise SystemExit("Docker is absent. The optional ERP stack was prepared but not started.")
        executable = _allowed_path(executable)
        actual = subprocess.check_output([str(executable), "info", "--format", "{{.DockerRootDir}}"], text=True, timeout=15).strip()
        try:
            _allowed_path(actual).relative_to(ROOT)
        except ValueError:
            raise SystemExit("Docker storage is not verified inside Yikes. No images were downloaded; configure an allowed local container host separately.")
        pins = json.loads((ROOT / "config/erpnext/images.json").read_text(encoding="utf-8"))["images"]
        for image in pins.values():
            subprocess.run([str(executable), "pull", image], check=True, timeout=900)
        cache = _allowed_path(ROOT / "runtime/erpnext")
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(executable), "image", "save", "-o", str(cache / "images.tar"), *pins.values()], check=True, timeout=900)
        print("Pinned container images saved for offline loading; no ERP containers were started.")
    print("ERP recipe and private bootstrap credentials prepared. Docker/Linux runtime remains a separate prerequisite.")
    print("Start only after verifying container storage: docker compose --env-file data/erpnext/bootstrap.env -f config/erpnext/compose.yaml up -d --pull never")


if __name__ == "__main__":
    main()
