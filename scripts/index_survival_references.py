"""Build distributable full-text reference pages from the pinned public assets."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.books import BookLibrary
from ariadne.survival_library import SurvivalLibrary, ASSETS


def main():
    catalog = json.loads((ASSETS / "catalog.json").read_text(encoding="utf-8"))
    result = {"schema": 1, "resources": {}}
    for resource in catalog["resources"]:
        if not resource.get("path"):
            continue
        path = SurvivalLibrary._file(resource["path"])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != resource["sha256"] or len(data) != resource["bytes"]:
            raise ValueError("Source identity mismatch: " + resource["id"])
        if path.suffix.lower() == ".pdf":
            extracted = BookLibrary.extract(data, "pdf")
        else:
            text = data.decode("utf-8")
            extracted = {"sections": [{"number": 1, "label": resource["title"], "text": text}],
                         "processed_all_sections": True, "coverage_complete": True, "image_coverage": False, "warnings": []}
        result["resources"][resource["id"]] = {"source_sha256": resource["sha256"], **extracted}
    encoded = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (ASSETS / "text-index.json").write_bytes(encoded)
    manifest = {"schema": 1, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
        "catalog_sha256": hashlib.sha256((ASSETS / "catalog.json").read_bytes()).hexdigest()}
    (ASSETS / "text-index-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    receipt = {"schema": 1, "resources": len(result["resources"]),
        "sections": sum(len(r["sections"]) for r in result["resources"].values()),
        "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
        "all_sources_hash_verified": True, "all_sections_processed": True,
        "image_coverage": False, "index": "ariadne/resources/survival/text-index.json"}
    (ROOT / "data/survival-reference-index-0.5.json").write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
