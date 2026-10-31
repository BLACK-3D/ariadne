"""Research allowlists must not widen paper engines or weaken archive integrity."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from ariadne.options_archive import inspect_archive, iter_archive, parse_manifest
from ariadne.options_data import (OptionsDataError, canonical, contract_id,
    load_dataset, research_symbol_set, validate_dataset)
from ariadne.options_recovery import OptionsRecoveryError, initial_state
from ariadne.tools import ToolRegistry
from test_options_recovery import fixture


def stock_fixture(symbol="F"):
    value = deepcopy(fixture(4))
    for row in value["snapshots"]:
        row.update(underlying_symbol=symbol, underlying_price="9.9")
        for quote in row["quotes"]:
            quote.update(strike="10", bid="0.5", ask="0.6")
            quote["contract_id"] = contract_id(symbol, quote["expiry"], quote["type"], "10", research_symbols=(symbol,))
    return value


class UniverseDataTests(unittest.TestCase):
    def test_explicit_import_does_not_change_default_or_paper_universe(self):
        value = stock_fixture()
        raw = canonical(value)
        with self.assertRaises(OptionsDataError): load_dataset(raw)
        self.assertEqual(load_dataset(raw, research_symbols=("F",)), value)
        self.assertEqual(validate_dataset(value, research_symbols=("F",))["symbols"], ["F"])
        with self.assertRaises(OptionsDataError): load_dataset(raw)
        with self.assertRaises(OptionsRecoveryError):
            initial_state(value, symbol="F", started_at=value["as_of"])

    def test_allowlist_rejects_aliases_mutability_and_unbounded_input(self):
        for symbols in ((), [], ["F"], "F", True, ("F", "F"), ("f",), ("F1",),
                        ("F/../",), ("A\x00",), ("Ａ",), ([],), tuple("A"*7 for _ in range(65))):
            with self.subTest(symbols=repr(symbols)), self.assertRaises(OptionsDataError):
                research_symbol_set(symbols)
        self.assertEqual(research_symbol_set(("F", "BAC")), frozenset(("BAC", "F")))

    def test_identity_and_economic_bounds_remain_strict(self):
        for mutation in (lambda d: d["snapshots"][0]["quotes"][0].update(contract_id="BAC240131C00010000"),
                         lambda d: d["snapshots"][0]["quotes"][0].update(multiplier=10),
                         lambda d: d["snapshots"][0]["quotes"][0].update(bid="11", ask="12"),
                         lambda d: d["snapshots"][0]["quotes"][0].update(bid_size=True)):
            value = stock_fixture(); mutation(value)
            with self.assertRaises(OptionsDataError): validate_dataset(value, research_symbols=("F",))

    def test_parser_keeps_over_100_rows_for_exit_and_does_not_supply_sizes(self):
        value = stock_fixture()
        value["snapshots"][-1]["underlying_price"] = "101"
        value["snapshots"][-1]["quotes"][0]["bid_size"] = None
        result = load_dataset(canonical(value), research_symbols=("F",))
        self.assertEqual(result, value)
        self.assertIsNone(result["snapshots"][-1]["quotes"][0]["bid_size"])

    def _archive(self, *, omit_last=False):
        parent = Path(__file__).resolve().parents[1] / "runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="universe-data-", dir=parent)
        self.addCleanup(temporary.cleanup)
        registry = object.__new__(ToolRegistry); registry.root = Path(temporary.name)
        data = stock_fixture()
        value = deepcopy(data)
        if omit_last: value["snapshots"].pop()
        body = canonical(value)
        registry._path("part.json").write_bytes(body)
        rows = value["snapshots"]
        manifest = {"schema": 1, "as_of": data["as_of"], "evidence_kind": data["evidence_kind"],
            "symbol": "F", "source": data["source"],
            "selection": {"start": "2024-01-02", "end": "2024-01-05", "max_dte": None,
                "reason": "Synthetic coverage test; not market evidence."},
            "limitations": ["Synthetic fixtures only."], "chunks": [{"path": "part.json",
                "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "snapshots": len(rows),
                "quotes": len(rows), "first_observed_at": rows[0]["observed_at"], "last_observed_at": rows[-1]["observed_at"]}]}
        raw = canonical(manifest)
        registry._path("catalog.json").write_bytes(raw)
        return registry, raw

    def test_archive_opt_in_propagates_without_relaxing_default(self):
        registry, raw = self._archive()
        with self.assertRaises(OptionsDataError): parse_manifest(raw)
        result = inspect_archive(registry, "catalog.json", research_symbols=("F",),
            expected_manifest_sha256=hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["symbol"], "F")
        self.assertEqual(result["snapshots"], 4)
        self.assertFalse(result["execution_qualified"])
        with self.assertRaises(OptionsDataError): list(iter_archive(registry, "catalog.json"))

    def test_opted_in_archive_still_rejects_gaps_and_changed_chunk_bytes(self):
        registry, _ = self._archive(omit_last=True)
        with self.assertRaisesRegex(OptionsDataError, "missing selected"):
            list(iter_archive(registry, "catalog.json", research_symbols=("F",)))
        registry, _ = self._archive()
        with registry._path("part.json").open("ab") as target: target.write(b" ")
        with self.assertRaises(OptionsDataError):
            list(iter_archive(registry, "catalog.json", research_symbols=("F",)))


if __name__ == "__main__": unittest.main()
