"""Real-model local document -> table -> checked artifacts qualification."""
import argparse
import csv
import io
import json
import sys
import time
import uuid
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ariadne.config import load_settings
from ariadne.engine import Engine
from ariadne.sources import SourceManager
from ariadne.store import Store
from ariadne.verification import validate_checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:8b")
    args = parser.parse_args()
    base = load_settings()
    identifier = uuid.uuid4().hex
    directory = base.data_dir / "verification-0.2" / identifier
    settings = replace(base, data_dir=directory, workspace=directory / "workspace", connectors_path=directory / "connectors.json", model=args.model, max_steps=12)
    store = Store(directory / "state.sqlite3")
    engine = Engine(settings, store)
    sources = SourceManager(settings, store)
    references = ROOT / "staging" / ("verification-reference-" + identifier)
    references.mkdir(parents=True)
    with zipfile.ZipFile(references / "inventory-brief.docx", "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Northstar local inventory brief. There are exactly two items: Widget, quantity 3, unit_cost 2.50; Cable, quantity 2, unit_cost 4.25. These are illustrative offline planning data, not an actual business transaction. No external actions are authorized.</w:t></w:r></w:p></w:body></w:document>')
    source = sources.register("Qualification reference", str(references))
    preview = sources.preview(source["id"], "inventory-brief.docx")
    imported = sources.import_reference(source["id"], preview["path"], preview["sha256"])
    checks = validate_checks([
        {"kind": "csv_columns", "path": "business/inventory.csv", "columns": ["item", "quantity", "unit_cost"]},
        {"kind": "json_valid", "path": "business/summary.json"},
        {"kind": "json_number", "path": "business/summary.json", "pointer": "/total_units", "value": "5"},
        {"kind": "json_number", "path": "business/summary.json", "pointer": "/total_value", "value": "16.00"},
        {"kind": "file_contains", "path": "business/operations.md", "text": "Northstar"},
    ], engine.tools)
    prompt = "Search local knowledge for the Northstar inventory brief. Use table_write to create business/inventory.csv with exactly the two items and columns item,quantity,unit_cost from that source. Calculate total_units as sum(quantity), and total_value as sum(quantity * unit_cost) using table_query aggregations sum and sum_product with columns [quantity,unit_cost]. Save business/summary.json as a JSON object with exactly total_units and total_value (numbers or decimal strings). Save business/operations.md with a short Northstar stock-review checklist citing the source title. Create real files with tools, read the outputs back, and finish concisely. Do not perform external actions."
    run = store.new_run(prompt, checks=checks)
    store.next_run()
    start = time.monotonic()
    engine.process(run["id"])
    detail = store.details(run["id"])
    result = {"model": args.model, "status": detail["run"]["status"], "verification": detail["verification"], "steps": detail["run"]["step_count"],
              "seconds": round(time.monotonic() - start, 3), "error": detail["run"]["error"], "source_sha256": preview["sha256"],
              "document_id": imported["document"]["id"], "model_usage": [e["data"] for e in detail["events"] if e["kind"] == "model_usage"]}
    try:
        summary = json.loads((settings.workspace / "business/summary.json").read_text())
        with (settings.workspace / "business/inventory.csv").open(encoding="utf-8-sig", newline="") as source_file:
            rows = list(csv.DictReader(source_file))
        expected = {"Widget": (Decimal(3), Decimal("2.50")), "Cable": (Decimal(2), Decimal("4.25"))}
        actual = {row["item"]: (Decimal(row["quantity"]), Decimal(row["unit_cost"])) for row in rows}
        result["semantic_fixture_passed"] = len(rows) == 2 and actual == expected and Decimal(str(summary["total_units"])) == 5 and Decimal(str(summary["total_value"])) == 16
    except (OSError, ValueError, KeyError, TypeError):
        result["semantic_fixture_passed"] = False
    result["passed"] = result["status"] == "completed" and result["verification"]["status"] == "passed" and result["semantic_fixture_passed"]
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (base.data_dir / "verification-0.2-latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
