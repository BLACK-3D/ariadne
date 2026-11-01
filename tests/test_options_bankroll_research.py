"""Generated capital/clock/source controls; not market performance evidence."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ariadne import options_bankroll_plan as capital, options_bankroll_research as research
from ariadne.options_data import canonical
from scripts import options_bankroll_research as cli
from test_options_universe import stocks, timeline
from test_options_campaign import chunked
from test_options_universe_data import stock_fixture


def study(data=None, *, context=None, mechanical=True):
    data = data if data is not None else stocks(symbols=("AAA",), sizes=230,
        quote_values=lambda index, day: ("0.60", "0.65"))
    with patch.object(research.signals, "signal", return_value=True) if mechanical else patch.object(research.signals, "signal", wraps=research.signals.signal):
        return research.backtest(data, symbols=tuple(data), benchmark=context)


def report(value, candidate="raw_breakdown_put"):
    return value["candidate_results"][candidate]


def fills(value, side):
    return [item for item in value["events"] if item["type"] == "fill" and item["action"] == side]


def stress():
    value = timeline()
    return replace(value, observations=tuple(replace(row, label="stress_unwind") for row in value.observations))


class BankrollResearchTests(unittest.TestCase):
    def test_fixed_total_capital_and_detached_rule(self):
        rule = research.profile_rule()
        self.assertEqual(rule["initial_total_capital"], "25000.00")
        self.assertEqual(rule["additional_contributions"], "0.00")
        self.assertEqual(rule["capital_policy_sha256"], capital.POLICY_SHA256)
        self.assertEqual(tuple(rule["candidates"]), research.CANDIDATES)
        rule["candidates"].clear()
        self.assertEqual(len(research.profile_rule()["candidates"]), 4)

    def test_month_boundaries_never_add_cash(self):
        value = report(study(), "cash")
        self.assertEqual(value["metrics"]["contributions"], "25000")
        self.assertEqual(value["metrics"]["ending_equity"], "25000")
        resets = [x for x in value["events"] if x["type"] == "month_budget_reset"]
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0]["funding_added"], "0")
        self.assertFalse(any(x["type"] == "contribution" for x in value["events"]))

    def test_shared_sizing_and_per_contract_fees(self):
        value = report(study())
        entry = fills(value, "BUY")[0]
        expected = capital.size(ask="0.65", ask_size=230)
        self.assertEqual(entry["quantity"], expected["quantity"])
        self.assertEqual(entry["budget_committed"], expected["all_in_commitment"])
        self.assertEqual(entry["fee"], "9.75")
        self.assertEqual(entry["reserved_exit_fee"], "9.75")
        self.assertEqual(entry["quantity"], 15)

    def test_integer_quantity_capped_by_actual_ask_size(self):
        data = stocks(symbols=("AAA",), sizes=2, quote_values=lambda i, d: ("0.60", "0.65"))
        self.assertEqual(fills(report(study(data)), "BUY")[0]["quantity"], 2)

    def test_missing_ask_size_is_not_volume_or_open_interest(self):
        data = stocks(symbols=("AAA",), sizes=None)
        value = report(study(data))
        self.assertEqual(value["metrics"]["entries"], 0)

    def test_entry_quantity_is_clipped_to_current_supplied_bid_size(self):
        data = stocks(symbols=("AAA",), sizes=230,
                      quote_values=lambda i, d: ("0.60", "0.65"))
        for row in data["AAA"]["snapshots"]:
            for quote in row["quotes"]: quote["bid_size"] = 1
        value = report(study(data))
        entries = fills(value, "BUY")
        self.assertTrue(entries)
        self.assertTrue(all(entry["quantity"] == 1 for entry in entries))
        self.assertEqual(entries[0]["actual_ask_size"], 230)
        self.assertEqual(entries[0]["actual_bid_size"], 1)

    def test_pending_month_boundary_holds_without_funding_or_reset(self):
        data = stocks(symbols=("AAA",), sizes=230)
        row = data["AAA"]["snapshots"][0]
        state = research._initial(row)
        state.update(month="2023-10", monthly_committed="1000")
        state["pending"] = {"action": "BUY", "underlying_symbol": "AAA"}
        events, curve = [], []
        research._step(state, row, "raw_breakdown_put", allowed=False, selected_quote=None, events=events, curve=curve)
        self.assertEqual(state["hold"]["code"], "unresolved_month_boundary")
        self.assertEqual(state["month"], "2023-10")
        self.assertEqual(state["monthly_committed"], "1000")
        self.assertEqual(state["cash"], "25000")

    def test_full_quantity_bid_is_required_and_no_partial_exit(self):
        data = stocks(symbols=("AAA",), sizes=230, quote_values=lambda i, d: ("0.60", "0.65"))
        for row in data["AAA"]["snapshots"][15:]:
            for quote in row["quotes"]: quote["bid_size"] = 1
        value = report(study(data))
        self.assertEqual(value["hold"]["code"], "missing_or_insufficient_full_quantity_bid")
        self.assertEqual(value["state"]["position"]["quantity"], 15)
        self.assertEqual(fills(value, "SELL"), [])
        self.assertFalse(value["coverage"]["complete"])

    def test_profitable_exit_books_quantity_and_both_fees(self):
        data = stocks(symbols=("AAA",), sizes=230,
            quote_values=lambda i, d: ("3.5", "3.6") if i in (15, 16) else ("0.60", "0.65"))
        value = report(study(data))
        sale = fills(value, "SELL")[0]
        self.assertEqual(sale["quantity"], 15)
        self.assertEqual(sale["gross_credit"], "5250")
        self.assertEqual(sale["fee"], "9.75")
        self.assertEqual(sale["trade_pnl"], "4255.5")

    def test_round_robin_never_redecides_on_exit_frame(self):
        data = stocks(sizes=230,
            quote_values=lambda i, d: ("3.5", "3.6") if i in (15, 16) else ("0.60", "0.65"))
        value = report(study(data))
        self.assertEqual(fills(value, "BUY")[0]["underlying_symbol"], "AAA")
        sale = fills(value, "SELL")[0]
        later = [x for x in value["events"] if x["type"] == "decision" and x["action"] == "BUY" and x["decided_at"] > sale["available_at"]]
        self.assertEqual(later[0]["underlying_symbol"], "BBB")
        self.assertFalse(any(x["type"] == "decision" and x["action"] == "BUY" and x["decided_at"] == sale["available_at"] for x in value["events"]))

    def test_two_roots_do_not_multiply_capital(self):
        value = study(stocks(sizes=230), context=stress())
        for candidate in value["candidate_results"].values():
            self.assertEqual(candidate["metrics"]["contributions"], "25000")
            active = None
            for event in candidate["events"]:
                if event["type"] != "fill": continue
                if event["action"] == "BUY":
                    self.assertIsNone(active)
                    active = event["underlying_symbol"]
                else:
                    self.assertEqual(active, event["underlying_symbol"])
                    active = None

    def test_unavailable_context_denies_filtered_only_and_no_ranking(self):
        value = study()
        self.assertEqual(report(value, "stress_breakdown_put")["metrics"]["entries"], 0)
        self.assertEqual(report(value, "growth_recovery_call")["metrics"]["entries"], 0)
        self.assertGreater(report(value)["metrics"]["entries"], 0)
        self.assertFalse(value["comparison"]["context_complete"])
        self.assertEqual(value["comparison"]["ranked_ids"], [])

    def test_cash_ranks_ahead_of_all_completed_losing_strategies(self):
        # Generated prices under a historical schema label test ranking only;
        # this data remains synthetic and is never investment evidence.
        data = stocks(symbols=("AAA",), sizes=230,
                      quote_values=lambda i, d: ("0.60", "0.65"))
        data["AAA"]["evidence_kind"] = "historical_quotes"
        result = study(data, context=stress())
        self.assertTrue(result["comparison"]["all_ledgers_complete"])
        self.assertTrue(result["comparison"]["context_complete"])
        self.assertLess(Decimal(report(result)["metrics"]["trading_pnl"]), 0)
        self.assertLess(Decimal(report(result,"stress_breakdown_put")["metrics"]["trading_pnl"]), 0)
        self.assertEqual(result["comparison"]["ranked_ids"][0], "cash")
        self.assertNotIn("growth_recovery_call", result["comparison"]["ranked_ids"])

    def test_stress_and_growth_gate_different_candidates(self):
        bearish = study(context=stress())
        bullish = study(context=timeline())
        self.assertGreater(report(bearish, "stress_breakdown_put")["metrics"]["entries"], 0)
        self.assertEqual(report(bearish, "growth_recovery_call")["metrics"]["entries"], 0)
        self.assertEqual(report(bullish, "stress_breakdown_put")["metrics"]["entries"], 0)
        self.assertGreater(report(bullish, "growth_recovery_call")["metrics"]["entries"], 0)

    def test_fill_gate_cannot_use_current_not_yet_published_context(self):
        data = stocks(symbols=("AAA",), sizes=230)
        value = timeline()
        # A stress close at the pending fill day is published after that fill.
        day = data["AAA"]["snapshots"][14]["observed_at"][:10]
        value = replace(value, observations=tuple(replace(row, label="recovery_growth" if row.session < day else "stress_unwind") for row in value.observations))
        result = study(data, context=value)
        filtered = report(result, "stress_breakdown_put")
        first = fills(filtered, "BUY")[0]
        self.assertGreater(first["source_date"], day)

    def test_under100_gate_at_decision_and_fill(self):
        data = stocks(symbols=("AAA",), sizes=230)
        for row in data["AAA"]["snapshots"]: row["underlying_price"] = "100"
        self.assertEqual(report(study(data))["metrics"]["entries"], 0)
        data = stocks(symbols=("AAA",), sizes=230)
        data["AAA"]["snapshots"][14]["underlying_price"] = "100"
        result = report(study(data))
        self.assertTrue(any(x["type"] == "cancelled" and x["reason"] == "entry_gate_blocked" for x in result["events"]))

    def test_gap_and_mismatched_streams_are_never_stitched(self):
        data = stocks(symbols=("AAA",), sizes=230)
        data["AAA"]["snapshots"].pop(16)
        value = report(study(data))
        self.assertEqual(value["hold"]["code"], "historical_session_gap")
        data = stocks()
        data["BBB"]["snapshots"].pop()
        with self.assertRaisesRegex(ValueError, "ended at different"):
            study(data)

    def test_archive_chunking_does_not_change_result(self):
        data = stocks(symbols=("AAA",), sizes=230)
        expected = study(data)
        split = {"AAA": chunked(data["AAA"], 10, 20)}
        actual = study(split)
        self.assertEqual(expected["candidate_results"], actual["candidate_results"])
        self.assertEqual(expected["coverage"], actual["coverage"])

    def test_caller_decimal_traps_do_not_change_money(self):
        data = stocks(symbols=("AAA",), sizes=230, quote_values=lambda i, d: ("0.60", "0.65"))
        expected = study(data)
        with localcontext() as ctx:
            ctx.prec, ctx.rounding, ctx.traps[Inexact] = 2, ROUND_DOWN, True
            self.assertEqual(expected, study(data))

    def test_unknown_root_set_and_timeline_are_refused(self):
        for symbols in (["AAA"], ("AAA", "AAA"), ("../AAA",), (True,)):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                research.backtest({}, symbols=symbols)
        with self.assertRaises(ValueError):
            research.backtest(stocks(), symbols=("AAA", "BBB"), benchmark={"status": "ready"})

    def test_even_held_ledgers_exhaust_later_input_chunks(self):
        data = stocks(symbols=("AAA",), sizes=230)
        for row in data["AAA"]["snapshots"][15:]:
            for quote in row["quotes"]: quote["bid_size"] = 0
        consumed = []
        def stream():
            for index, chunk in enumerate(chunked(data["AAA"], 20)):
                consumed.append(index)
                yield chunk
        result = study({"AAA": stream()})
        self.assertEqual(consumed, [0, 1])
        self.assertTrue(result["source_streams_exhausted"])
        self.assertFalse(report(result)["coverage"]["complete"])


class BankrollMoneyParityTests(unittest.TestCase):
    """Paired neutral money steps, NOT shared source/permission ancestry.

    The two observation envelopes intentionally remain different contracts.
    For these generated samples only, receipt=historical available_at and the
    source date equals observed NY day. This is no forward execution proof.
    """
    def pair(self, *, bid_size=100, ask_size=230, tiny=False, undersized=False):
        from ariadne import options_bankroll_eod as production
        from ariadne.qqq_data import session_close_utc
        from test_options_bankroll_eod import state
        from test_options_root_eod import row as root_row
        pstate, rstate, events, curve = state(), None, [], []
        days=(date(2024,1,4),date(2024,1,5),date(2024,1,8),date(2024,1,9))
        for index, day in enumerate(days):
            bid, ask = ("0.60", "0.65") if index < 2 else ("0.00000001", "0.00000001") if tiny else ("3.25", "3.30") if index == 2 else ("3.20", "3.25")
            current_bid_size = 1 if undersized and index >= 2 else bid_size
            raw = root_row(day,price="59.6",bid=bid,ask=ask,bid_size=current_bid_size,ask_size=ask_size)
            observed=session_close_utc(day)
            available=(observed+research.timedelta(minutes=15)).isoformat()
            raw["received_at"]=available
            row={"observed_at":observed.isoformat(),"available_at":available,"underlying_symbol":"KO",
                 "underlying_price":raw["underlying_close"],"quotes":[{**quote,"multiplier":100,"volume":0,"open_interest":0} for quote in raw["quotes"]]}
            if rstate is None:rstate=research._initial(row)
            result=production._step_capital(raw,pstate,decided_at=available)
            pstate=result["state"]
            research._step(rstate,row,"raw_breakdown_put",allowed=True,selected_quote=row["quotes"][0],events=events,curve=curve)
            for key in ("cash","contributions","monthly_committed","fees"):
                self.assertEqual(Decimal(pstate[key]),Decimal(rstate[key]),(index,key))
            self.assertEqual(pstate["position"] is None,rstate["position"] is None)
            self.assertEqual(pstate["pending"] is None,rstate["pending"] is None)
            self.assertEqual(pstate["hold"] is None,rstate["hold"] is None)
            self.assertEqual(production._equity(pstate),research._equity(rstate))
            if pstate["position"]:
                self.assertEqual(pstate["position"]["quantity"],rstate["position"]["quantity"])
                self.assertEqual(pstate["position"]["forced_exit_session"],rstate["position"]["forced_exit_session"])
            if pstate["hold"]:break
        return pstate,rstate,events

    def test_full_entry_mark_delayed_exit_and_fees_match_schema6(self):
        pstate,rstate,events=self.pair()
        self.assertEqual(pstate["cash"],"28805.5")
        self.assertEqual(rstate["realized_pnl"],"3805.5")
        self.assertEqual(fills({"events":events},"BUY")[0]["quantity"],15)

    def test_current_two_sided_quantity_limits_match_schema6(self):
        for sizes in ((1,230),(100,3),(2,230)):
            with self.subTest(sizes=sizes):self.pair(bid_size=sizes[0],ask_size=sizes[1])

    def test_subcent_bid_liquidation_fees_can_be_negative_position_value(self):
        pstate,rstate,_=self.pair(tiny=True)
        self.assertEqual(pstate["cash"],"24005.5")
        self.assertEqual(rstate["realized_pnl"],"-994.5")

    def test_funded_insufficient_depth_holds_both_without_partial_exit(self):
        pstate,rstate,events=self.pair(undersized=True)
        self.assertIsNotNone(pstate["hold"])
        self.assertIsNotNone(rstate["hold"])
        self.assertEqual(fills({"events":events},"SELL"),[])


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class BankrollResearchCLITests(unittest.TestCase):
    def setUp(self):
        parent = cli.ROOT/"runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="bankroll-research-cli-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in cli.SOURCE_FILES:
            destination = self.root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((cli.ROOT/name).read_bytes())
        self.workspace = self.root/"workspace"
        self.workspace.mkdir()
        data = stock_fixture()
        raw = canonical(data)
        (self.workspace/"part.json").write_bytes(raw)
        rows = data["snapshots"]
        manifest = {"schema": 1, "as_of": data["as_of"], "evidence_kind": data["evidence_kind"], "symbol": "F",
            "source": data["source"], "selection": {"start": "2024-01-02", "end": "2024-01-05", "max_dte": None,
                "reason": "Generated total capital publication fixture."}, "limitations": ["Synthetic fixture."],
            "chunks": [{"path": "part.json", "sha256": digest(raw), "bytes": len(raw), "snapshots": 4, "quotes": 4,
                "first_observed_at": rows[0]["observed_at"], "last_observed_at": rows[-1]["observed_at"]}]}
        raw = canonical(manifest)
        (self.workspace/"catalog.json").write_bytes(raw)
        (self.workspace/"benchmark.json").write_bytes(b'{"test_fixture":true}')
        (self.root/"intent.json").write_bytes(b'{"schema":1,"purpose":"synthetic fixture"}')
        self.args = cli.parser().parse_args(["--manifest", "catalog.json", "--manifest-sha256", digest(raw),
            "--benchmark", "benchmark.json", "--benchmark-sha256", digest((self.workspace/"benchmark.json").read_bytes()),
            "--benchmark-clock-profile", "retained_capture", "--intent", "intent.json", "--intent-sha256", digest((self.root/"intent.json").read_bytes()),
            "--workspace", "workspace", "--detached-research", "--output", "staging/run"])
        self.consume = None
        self.seen = []

    def _study(self, streams, **kwargs):
        self.seen.append(kwargs)
        for stream in streams.values(): list(stream)
        if self.consume: self.consume()
        return {"source_streams_exhausted": True, "research_only": True}

    def _run(self):
        context = SimpleNamespace(describe=lambda: {"test_fixture": True})
        with patch("ariadne.options_regime.build_timeline", return_value=context), patch.object(research, "backtest", side_effect=self._study):
            return cli.run(self.args, root=self.root, now=datetime(2026,11,1,15,tzinfo=timezone.utc))

    def _failed(self):
        with self.assertRaises((ValueError, OSError)): self._run()
        self.assertFalse((self.root/"staging/run/completed.json").exists())
        return json.loads((self.root/"staging/run/failed.json").read_bytes())

    def test_registered_hashes_outputs_and_zero_owner_operations(self):
        result = self._run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["paper_operations"], 0)
        for name, identity in result["artifacts"].items():
            raw = (self.root/"staging/run"/name).read_bytes()
            self.assertEqual((len(raw), digest(raw)), (identity["bytes"], identity["sha256"]))
        registration = json.loads((self.root/"staging/run/registration.json").read_bytes())
        self.assertEqual(registration["rule"]["initial_total_capital"], "25000.00")
        self.assertEqual(registration["owner_authority"]["mode"], "detached_research_owner_unchecked")

    def test_existing_or_interrupted_output_never_replays(self):
        self._run()
        before = (self.root/"staging/run/completed.json").read_bytes()
        with self.assertRaises(FileExistsError): self._run()
        self.assertEqual(before, (self.root/"staging/run/completed.json").read_bytes())

    def test_detached_cannot_skip_real_owner_or_alternate_path(self):
        (self.root/"data").mkdir()
        owner = self.root/"data/ariadne.sqlite3"
        owner.write_bytes(b"Do not initialize or open this file")
        with self.assertRaisesRegex(ValueError, "bypass"): self._run()
        self.assertEqual(owner.read_bytes(), b"Do not initialize or open this file")
        self.assertFalse((self.root/"staging/run").exists())

    def test_wrong_source_hash_stops_before_financial_evaluation(self):
        self.args.benchmark_sha256 = "0"*64
        failure = self._failed()
        self.assertIn("Benchmark", failure["reason"])
        self.assertEqual(self.seen, [])

    def test_same_length_source_tamper_with_restored_mtime_fails(self):
        source = self.workspace/"part.json"
        def change():
            stat, raw = source.stat(), source.read_bytes()
            changed = raw.replace(b'"bid":"0.5"', b'"bid":"0.4"', 1)
            self.assertNotEqual(raw, changed)
            source.write_bytes(changed)
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.consume = change
        failure = self._failed()
        self.assertIn("source bytes changed", failure["reason"])

    def test_incomplete_stream_even_with_true_claim_is_rejected(self):
        with patch.object(self, "_study", return_value={"source_streams_exhausted": True}):
            failure = self._failed()
        self.assertIn("Every registered stock stream", failure["reason"])

    def test_output_is_not_truncated_to_fit_quota(self):
        with patch.object(cli, "MAX_OUTPUT", 8192), patch.object(cli, "FAILURE_RESERVE", 1024):
            failure = self._failed()
        self.assertIn("output exceeds", failure["reason"])


if __name__ == "__main__":
    unittest.main()
