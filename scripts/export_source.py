"""Package deployable source without credentials, model weights or local state."""
import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne import __version__
from ariadne.config import _allowed_path


def main():
    paths = []
    for name in ("ariadne", "scripts", "tests", "docs", "config"):
        for directory, folders, files in os.walk(ROOT / name, followlinks=False):
            folders[:] = [folder for folder in folders if folder != "__pycache__" and not (Path(directory, folder).lstat().st_file_attributes & 0x400 if os.name == "nt" else Path(directory, folder).is_symlink())]
            for filename in files:
                file = Path(directory, filename)
                info = file.lstat()
                if file.suffix != ".pyc" and stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                    paths.append(_allowed_path(file))
    paths += [ROOT / name for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "Dockerfile", "compose.yaml", "compose.provision.yaml", ".dockerignore", ".gitignore", "start.ps1", "start.sh", "Start Ariadne.cmd", "Stop Ariadne.cmd")]
    paths += [_allowed_path(path) for path in ROOT.glob("requirements*.txt") if path.is_file() and path.stat().st_nlink == 1]
    destination = _allowed_path(ROOT / "dist" / f"ariadne-box-{__version__}-source.zip")
    destination.parent.mkdir(exist_ok=True)
    manifest = {}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(paths):
            content = file.read_bytes()
            name = file.relative_to(ROOT).as_posix()
            archive.writestr(name, content)
            manifest[name] = hashlib.sha256(content).hexdigest()
        archive.writestr("SOURCE-MANIFEST.json", json.dumps({"version": __version__, "sha256": manifest}, indent=2))
    print(f"Created {destination} ({destination.stat().st_size:,} bytes). No local state or credentials included.")


if __name__ == "__main__":
    main()
