"""Generated normalized benchmark/clock fixtures, never option returns."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Inexact, ROUND_UP, localcontext
import unittest
from unittest.mock import patch

from ariadne import options_stress_context as context
from ariadne.qqq_data import session_dates, session_close_utc
from ariadne.qqq_recovery import next_session_open

UTC=timezone.utc


def fixture(last="2023-11-03",pattern="stress",count=60):
    last=date.fromisoformat(last)
    dates=session_dates(last-timedelta(days=200),last)[-count:]
    captured=session_close_utc(dates[-1])+timedelta(hours=1)
    dataset={"schema":1,"as_of":captured.isoformat(),"currency":"USD","price_basis":"split_adjusted_ohlc",
        "dividend_policy":"split_adjusted_cash_on_ex_date","symbols":{}}
    for symbol in ("QQQ","SPY"):
        bars=[]
        for index,day in enumerate(dates):
            price=100+index if pattern=="growth" else 97 if pattern=="stress" and symbol=="QQQ" and index==len(dates)-1 else 100
            bars.append({"date":day.isoformat(),"open":price,"high":price+1,"low":price-1,"close":price,"volume":1000,
                         "adj_close":price,"cash_dividend":0,"split_ratio":1})
        dataset["symbols"][symbol]={"source":{"url":"https://example.com/"+symbol,"sha256":("a" if symbol=="QQQ" else "b")*64,
            "retrieved_at":captured.isoformat()},"bars":bars}
    receipt={"request_id":"generated-accepted-"+last.isoformat(),"status":"completed","action":"refresh","outcome":"updated",
        "created_at":(captured-timedelta(minutes=1)).isoformat(),"data_sha256":context._digest(dataset,context.MAX_DATA_BYTES),
        "source_sha256":{s:v["source"]["sha256"] for s,v in dataset["symbols"].items()},
        "last_sessions":{s:last.isoformat() for s in dataset["symbols"]}}
    return dataset,receipt,captured


def witness(last="2023-11-03",pattern="stress",published=None):
    dataset,receipt,captured=fixture(last,pattern)
    return context.build_witness(dataset,source_receipt=receipt,recorded_at=published or captured+timedelta(minutes=1))


def reseal(value):
    value["witness_sha256"]=context._digest({k:v for k,v in value.items() if k!="witness_sha256"})
    return value


class StressContextTests(unittest.TestCase):
    def rejects(self,fn,*args,**kwargs):
        with self.assertRaises(context.StressContextError):fn(*args,**kwargs)

    def test_normalized_witness_uses_new_publication_not_earlier_capture(self):
        data,receipt,captured=fixture();before=deepcopy((data,receipt))
        publication=datetime(2023,11,4,12,tzinfo=UTC)
        value=context.build_witness(data,source_receipt=receipt,recorded_at=publication)
        self.assertEqual((data,receipt),before)
        self.assertEqual(value["published_at"],publication.isoformat())
        self.assertEqual(value["data_available_at"],publication.isoformat())
        self.assertEqual(len(value["sessions"]),60)
        self.assertEqual(value["classification"]["label"],"stress_unwind")
        self.assertEqual(value["source_receipt_sha256"],context._digest(receipt,context.MAX_RECEIPT_BYTES))
        self.assertEqual(context.validate_witness(value),value)
        self.assertFalse(value["raw_chart_reconstruction_available"])
        self.assertFalse(value["execution_qualified"])

    def test_decision_requires_latest_finalized_stress_not_older_favorable_context(self):
        old=witness("2023-11-03")
        current=witness("2023-11-06","growth")
        gate=context.select_gate((old,current),decision_at="2023-11-07T14:35:00Z")
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["required_source_session"],"2023-11-06")
        self.assertEqual(gate["label"],"recovery_growth")
        self.assertEqual(gate["reason"],"benchmark_not_stress")

    def test_missing_stale_future_unpublished_and_mixed_context_cannot_allow_entry(self):
        now="2023-11-07T14:35:00Z"
        for ring in ((),(witness(),),(witness("2023-11-06",published=datetime(2023,11,7,15,tzinfo=UTC)),),
                     (witness("2023-11-06","flat"),)):
            self.assertFalse(context.select_gate(ring,decision_at=now)["allowed"])

    def test_fill_uses_prior_session_preopen_not_later_current_context(self):
        prior=witness("2023-11-06",published=datetime(2023,11,7,12,tzinfo=UTC))
        later=witness("2023-11-07","growth",published=datetime(2023,11,8,12,tzinfo=UTC))
        fill=context.select_gate((prior,later),decision_at="2023-11-08T14:35:00Z",source_date="2023-11-07")
        decision=context.select_gate((prior,later),decision_at="2023-11-08T14:35:00Z")
        self.assertTrue(fill["allowed"])
        self.assertEqual(fill["required_source_session"],"2023-11-06")
        self.assertEqual(fill["stage"],"fill")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["required_source_session"],"2023-11-07")

    def test_new_late_derivation_cannot_backdate_prior_input_durability(self):
        late=witness("2023-11-06",published=datetime(2023,11,8,12,tzinfo=UTC))
        gate=context.select_gate((late,),decision_at="2023-11-08T14:35:00Z",source_date="2023-11-07")
        self.assertFalse(gate["allowed"])
        self.assertIsNone(gate["witness"])

    def test_exact_open_boundary_and_one_microsecond_after(self):
        opening=next_session_open("2023-11-06")
        at=witness("2023-11-06",published=opening)
        after=witness("2023-11-06",published=opening+timedelta(microseconds=1))
        self.assertTrue(context.select_gate((at,),decision_at="2023-11-08T15:00:00Z",source_date="2023-11-07")["allowed"])
        self.assertFalse(context.select_gate((after,),decision_at="2023-11-08T15:00:00Z",source_date="2023-11-07")["allowed"])

    def test_weekend_dst_and_holiday_use_actual_preceding_regular_session(self):
        friday=witness("2023-11-03")
        gate=context.select_gate((friday,),decision_at="2023-11-07T14:35:00Z",source_date="2023-11-06")
        self.assertEqual(gate["cutoff_at"],"2023-11-06T14:30:00+00:00")
        self.assertTrue(gate["allowed"])
        holiday=witness("2024-07-03")
        gate=context.select_gate((holiday,),decision_at="2024-07-08T14:00:00Z",source_date="2024-07-05")
        self.assertEqual(gate["required_source_session"],"2024-07-03")
        self.assertEqual(gate["cutoff_at"],"2024-07-05T13:30:00+00:00")

    def test_gate_can_be_validated_after_witness_ring_rotates(self):
        gate=context.select_gate((witness(),),decision_at="2023-11-06T14:35:00Z")
        saved=deepcopy(gate)
        self.assertEqual(context.validate_gate_result(gate),saved)
        gate["witness"]["sessions"][0]["QQQ"]=99
        self.rejects(context.validate_gate_result,gate)
        self.assertEqual(context.validate_gate_result(saved),saved)

    def test_retained_gate_roundtrips_existing_exact_money_owner_codec(self):
        from ariadne.handoff import _load
        value=witness()
        self.assertTrue(all(type(r["QQQ"]) is str and type(r["SPY"]) is str for r in value["sessions"]))
        gate=context.select_gate((value,),decision_at="2023-11-06T14:35:00Z")
        self.assertEqual(_load(context._json(gate,context.MAX_GATE_BYTES)),gate)

    def test_gate_hash_fields_and_authority_cannot_be_changed(self):
        good=context.select_gate((),decision_at="2023-11-06T14:35:00Z")
        for key,value in (("allowed",True),("label","stress_unwind"),("execution_qualified",True),
                          ("decision_at","2023-11-07T14:35:00Z"),("extra",False)):
            bad=deepcopy(good);bad[key]=value
            self.rejects(context.validate_gate_result,bad)

    def test_receipt_dataset_hash_status_source_and_session_must_match(self):
        for key,value in (("status","running"),("outcome","stale"),("data_sha256","0"*64),
                          ("source_sha256",{"QQQ":"0"*64,"SPY":"b"*64}),
                          ("last_sessions",{"QQQ":"2023-11-02","SPY":"2023-11-03"}),
                          ("created_at","2023-11-06T14:00:00Z")):
            data,receipt,captured=fixture();receipt[key]=value
            self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_resolution_receipt_needs_no_invented_completed_clock(self):
        data,receipt,captured=fixture()
        receipt.update(action="resolve_history",outcome="diagnostic_history_resolved")
        receipt.pop("source_sha256");receipt.pop("last_sessions")
        receipt["created_at"]=(captured+timedelta(minutes=5)).isoformat()
        value=context.build_witness(data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=10))
        self.assertEqual(context.validate_witness(value),value)
        self.assertNotIn("completed_at",value["source_receipt"])

    def test_large_irrelevant_receipt_fields_are_bound_but_not_repeated_in_gate(self):
        data,receipt,captured=fixture();receipt["history_revision"]={"diagnostic_notes":"x"*4000}
        value=context.build_witness(data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))
        self.assertNotIn("history_revision",value["source_receipt"])
        self.assertEqual(value["source_receipt_original_sha256"],context._digest(receipt,context.MAX_RECEIPT_BYTES))
        self.assertEqual(value["source_receipt_sha256"],context._digest(context.receipt_projection(receipt),context.MAX_RECEIPT_BYTES))
        self.assertNotEqual(value["source_receipt_original_sha256"],value["source_receipt_sha256"])
        self.assertEqual(context.validate_witness(value),value)
        gate=context.select_gate((value,),decision_at=captured+timedelta(minutes=2))
        self.assertLessEqual(len(context._json(gate,context.MAX_GATE_BYTES)),11*1024)

    def test_context_source_url_limit_is_stricter_than_generic_dataset(self):
        data,receipt,captured=fixture()
        data["symbols"]["QQQ"]["source"]["url"]="https://example.com/"+"x"*512
        receipt["data_sha256"]=context._digest(data,context.MAX_DATA_BYTES)
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_data_canonical_hash_distinguishes_exact_original_encoding_values(self):
        data,receipt,captured=fixture()
        data["symbols"]["QQQ"]["bars"][0]["volume"]+=1
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_unused_older_prefix_is_still_bound_and_only_policy_tail_revalidated(self):
        data,receipt,captured=fixture(count=80)
        original=context.validate_dataset;lengths=[]
        def validate(value):
            lengths.append([len(v["bars"]) for v in value["symbols"].values()])
            return original(value)
        with patch.object(context,"validate_dataset",side_effect=validate):
            result=context.build_witness(data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))
        self.assertEqual(lengths,[[60,60]])
        self.assertEqual(result["data_sha256"],context._digest(data,context.MAX_DATA_BYTES))
        data["symbols"]["QQQ"]["bars"][0]["volume"]+=1
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_history_shape_calendar_future_and_8000_bound(self):
        for mutate in (lambda d:d["symbols"]["QQQ"]["bars"].pop(2),
                       lambda d:d["symbols"]["QQQ"]["bars"].reverse(),
                       lambda d:d["symbols"]["QQQ"].update(bars=[]),
                       lambda d:d["symbols"]["QQQ"].update(bars=[{}]*8001),
                       lambda d:d["symbols"]["SPY"]["bars"][-1].update(date="2023-11-06"),
                       lambda d:d["symbols"]["QQQ"]["bars"][-1].update(close=True)):
            data,receipt,captured=fixture();mutate(data)
            self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_encoded_dataset_and_receipt_budgets(self):
        data,receipt,captured=fixture()
        with patch.object(context,"MAX_DATA_BYTES",100):
            self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))
        receipt["artifacts"]=["x"*4096]*5
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_clock_awareness_and_capture_retrieval_order(self):
        data,receipt,captured=fixture()
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at="2023-11-03T22:00:00")
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured-timedelta(seconds=1))
        data["symbols"]["QQQ"]["source"]["retrieved_at"]=(captured+timedelta(seconds=1)).isoformat()
        self.rejects(context.build_witness,data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))

    def test_rehashed_witness_still_recomputes_price_features_dates_and_new_availability(self):
        for mutate in (lambda w:w["classification"].update(label="recovery_growth"),
                       lambda w:w["sessions"][0].update(date=w["sessions"][1]["date"]),
                       lambda w:w.update(data_available_at=w["dataset_as_of"]),
                       lambda w:w.update(execution_qualified=True),
                       lambda w:w.update(sessions=[]),
                       lambda w:w.update(source_receipt_sha256="0"*64)):
            value=witness();mutate(value);reseal(value)
            self.rejects(context.validate_witness,value)

    def test_duplicate_oversize_malformed_and_ambiguous_rings_are_rejected(self):
        value=witness();now="2023-11-06T14:35:00Z"
        for ring in ((value,value),[value]*17,"not a ring",(None,)):
            self.rejects(context.select_gate,ring,decision_at=now)
        other=deepcopy(value);other["source_receipt"]["request_id"]="different-accepted-capture"
        other["source_receipt_sha256"]=context._digest(other["source_receipt"],context.MAX_RECEIPT_BYTES);reseal(other)
        self.rejects(context.select_gate,(value,other),decision_at=now)

    def test_unfinalized_and_non_session_fill_dates_cannot_grant_entry(self):
        for source in ("2023-11-07","2023-11-05","2023-11-06junk"):
            self.rejects(context.select_gate,(),decision_at="2023-11-06T14:35:00Z",source_date=source)

    def test_decimal_context_and_string_close_representation_are_stable(self):
        data,receipt,captured=fixture()
        for entry in data["symbols"].values():
            for row in entry["bars"]:
                for key in ("open","high","low","close","adj_close","cash_dividend","split_ratio"):row[key]=str(row[key])
        receipt["data_sha256"]=context._digest(data,context.MAX_DATA_BYTES)
        normal=context.build_witness(data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1))
        with localcontext() as decimal:
            decimal.prec=1;decimal.rounding=ROUND_UP;decimal.traps[Inexact]=True
            self.assertEqual(context.build_witness(data,source_receipt=receipt,recorded_at=captured+timedelta(minutes=1)),normal)

    def test_rule_mutation_is_rejected(self):
        value=witness()
        with patch.dict(context.RULE,{"publication":"backdate the old source"}):
            self.rejects(context.validate_witness,value)


if __name__=="__main__":unittest.main()
