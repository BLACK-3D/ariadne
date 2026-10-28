"""Synthetic, local-only end-to-end model qualification; preserves every attempt."""
from __future__ import annotations

import base64
import csv
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.sources import SourceManager
from ariadne.store import Store


def main():
    from PIL import Image, ImageDraw, ImageFont
    attempt = ROOT / "data/verification-0.3" / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    settings = Settings(data_dir=attempt / "private", model="qwen3:8b", max_steps=12, api_token="synthetic-qualification-only")
    store = Store(settings.data_dir / "state.sqlite3")
    settings.workspace.mkdir()
    shutil.copyfile(ROOT / "data/ocr-config.json", settings.data_dir / "ocr-config.json")
    image = Image.new("RGB", (1500, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 66)
    draw.text((50, 45), "ARIADNE WAREHOUSE", font=font, fill="black")
    draw.text((50, 155), "Service fee 16.00", font=font, fill="black")
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    (attempt / "warehouse-note.png").write_bytes(payload.getvalue())
    document = SourceManager(settings, store).upload("warehouse-note.png", base64.b64encode(payload.getvalue()).decode())
    chunks = store.search("warehouse", knowledge=True)
    if not chunks or "16.00" not in " ".join(chunk["content"] for chunk in chunks):
        raise ValueError("OCR qualification did not recognize the synthetic fee; stop before model execution")
    total = Fraction()
    with (settings.workspace / "orders.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["order", "sku", "quantity"])
        for index in range(6000):
            quantity, sku = index % 5 + 1, index % 12 + 1
            writer.writerow([f"{index:06d}", f"SKU{sku:02d}", quantity])
            total += quantity * Fraction(sku, 4)
    with (settings.workspace / "prices.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sku", "unit_price"])
        for sku in range(1, 13):
            writer.writerow([f"SKU{sku:02d}", f"{sku / 4:.2f}"])
    expected_value = str(total.numerator // total.denominator) if total.denominator == 1 else str(float(total))
    checks = [{"kind": "json_valid", "path": "analysis/summary.json"},
              {"kind": "json_number", "path": "analysis/summary.json", "pointer": "/total_units", "value": "18000"},
              {"kind": "json_number", "path": "analysis/summary.json", "pointer": "/stock_value", "value": expected_value},
              {"kind": "json_number", "path": "analysis/summary.json", "pointer": "/service_fee", "value": "16.00"},
              {"kind": "file_exists", "path": "analysis/report.md"}]
    prompt = """Qualify local warehouse analysis using the actual tools. This is a synthetic fixture.
Search Knowledge for warehouse-note.png and record its service fee, title and OCR provenance.
Analyze orders.csv (6000 rows: order,sku,quantity) joined with prices.csv (sku,unit_price).
Use analytics_query with table aliases orders/prices, numeric_columns orders.quantity/prices.unit_price,
an inner equijoin on orders.sku/prices.sku, and count(*), sum(quantity), sum_product(quantity,unit_price).
Save analysis/summary.json containing total_units,stock_value,service_fee as exact decimal strings,
plus input_hashes and source_title. Save analysis/report.md explaining the fixture, tool result,
source hashes and OCR limitations. Read back both files before finishing. Keep result pages small.
Do not contact external systems, spend money or deploy anything."""
    run = store.new_run(prompt, checks=checks)
    engine = Engine(settings, store)
    store.next_run()
    started = time.monotonic()
    engine.process(run["id"])
    detail = store.details(run["id"])
    output = settings.workspace / "analysis/summary.json"
    actual = json.loads(output.read_text()) if output.is_file() else {}
    from decimal import Decimal
    semantic = all(Decimal(str(actual.get(key, "NaN"))) == Decimal(expected) for key, expected in
                   (("total_units", "18000"), ("stock_value", expected_value), ("service_fee", "16.00")))
    results = [event["data"] for event in detail["events"] if event["kind"] == "tool_result"]
    analytics_called = any(event["tool"] == "analytics_query" and event["result"].get("ok") for event in results)
    knowledge_called = any(event["tool"] == "knowledge_search" and event["result"].get("ok") for event in results)
    hashes = {name: hashlib.sha256((settings.workspace / name).read_bytes()).hexdigest() for name in ("orders.csv", "prices.csv")}
    result = {"run_id": run["id"], "model": settings.model, "elapsed_seconds": round(time.monotonic() - started, 3),
              "status": detail["run"]["status"], "verification_status": detail["run"]["verification_status"],
              "step_count": detail["run"]["step_count"], "semantic_pass": semantic,
              "analytics_called_successfully": analytics_called, "knowledge_called_successfully": knowledge_called,
              "input_sha256": hashes, "ocr_document": document["document"], "actual": actual,
              "expected": {"total_units": "18000", "stock_value": expected_value, "service_fee": "16.00"}}
    (attempt / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(attempt / "result.json"), **{key: result[key] for key in
          ("status", "verification_status", "step_count", "elapsed_seconds", "semantic_pass", "analytics_called_successfully", "knowledge_called_successfully")}}))
    return 0 if result["status"] == "completed" and result["verification_status"] == "passed" and semantic and analytics_called and knowledge_called else 1


if __name__ == "__main__":
    raise SystemExit(main())
