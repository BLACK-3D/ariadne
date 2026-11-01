"""Generated CSV observations only; these are not investment-return evidence."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
import json
import unittest
from unittest.mock import patch

from ariadne import options_paired_review as review


def cohort(symbol="AAA", *, preset="conventional_regime", profile="unknown_size_price_proxy", mode="individual",
           start="2023-11-01", end="2023-11-30", source_rows=22):
    result=[]
    for candidate in review.PRESETS[preset]:
        value={"source_window":"generated-November","mode":mode,"portfolio":symbol,"preset":preset,"profile":profile,
            "candidate":candidate,"status":"no_trade","pnl":"0","fees":"0","entries":"0","closed":"0",
            "max_commitment":"0","start":start,"source_end":end,"accepted_end":end,"processed_rows":str(source_rows),
            "source_rows":str(source_rows),"complete":"True","finished_flat":"True",
            "context_required":str(candidate.startswith(("growth_","stress_")) or candidate=="regime_rotating"),
            "context_complete":"True","hold":"","research_gate":"cash_control" if candidate=="cash" else "no_completed_trades",
            "evidence_kind":"historical_quotes","quote_time_basis":"assumed_eod","case_id":"generated-"+symbol.replace(",","-")+"-"+preset}
        result.append(value)
    return result


def leg(rows, candidate=review.REFERENCE, *, pnl="10", state="complete", context=True):
    value=next(r for r in rows if r["candidate"]==candidate)
    value.update(pnl=pnl,entries="1",closed="1",fees="1.30",max_commitment="40",research_gate="exploratory_only",
                 status="gain" if float(pnl)>0 else "loss" if float(pnl)<0 else "zero",context_complete=str(context))
    if state in ("held","open"):
        value.update(closed="0",fees="0.65",complete="False",finished_flat="False",status="held" if state=="held" else "incomplete",
            hold="missing_or_unexecutable_position_quote" if state=="held" else "",
            research_gate="held_incomplete_evidence" if state=="held" else "open_at_input_end")
        if state=="held":value.update(accepted_end="2023-11-29",processed_rows="21")
    return value


class PairedReviewTests(unittest.TestCase):
    def result(self, rows): return review.compare_rows(iter(rows))["strata"][0]
    def rejects(self, rows):
        with self.assertRaises(review.PairedReviewError):review.compare_rows(rows)

    def test_exact_26_column_csv_schema_and_immutable_input(self):
        rows=cohort(); before=deepcopy(rows); result=review.compare_rows(rows)
        self.assertEqual(len(review.COLUMNS),26)
        self.assertEqual(result["columns"],list(review.COLUMNS))
        self.assertEqual(result["input_rows"],8)
        self.assertEqual(rows,before)
        self.assertFalse(result["automatic_adoption"])
        self.assertFalse(result["source_authentication_established"])
        self.assertFalse(result["individual_entry_cap_verified_from_csv"])
        self.assertFalse(result["capital_or_pnl_summed"])
        self.assertFalse(result["independent_win_probability_estimated"])
        self.assertEqual(result["backtests_run"],0)

    def test_all_zero_cases_remain_counted_without_a_winner(self):
        result=self.result(cohort()); pair=result["recovery_pairs"]["cash"]
        self.assertEqual(pair["eligibility_matrix"]["both_eligible"],1)
        self.assertEqual(pair["all_eligible_pairs"]["equal"],1)
        self.assertEqual(pair["both_traded_pairs"]["competitor_minus_reference_net_pnl"]["count"],0)
        self.assertNotIn("winner",result)
        self.assertEqual(result["families"][review.REFERENCE]["status_counts"]["no_trade"],1)

    def test_paired_medians_and_both_traded_subset_not_summed_capital(self):
        a,b,c=cohort("AAA"),cohort("BBB"),cohort("CCC")
        leg(a,pnl="10");leg(a,"raw_breakout_call",pnl="20")
        leg(b,pnl="-20");leg(b,"raw_breakout_call",pnl="-5")
        leg(c,"raw_breakout_call",pnl="-1")
        p=self.result(a+b+c)["recovery_pairs"]["raw_breakout_call"]
        self.assertEqual(p["matched_cohorts"],3)
        self.assertEqual(p["all_eligible_pairs"]["competitor_minus_reference_net_pnl"],
                         {"count":3,"minimum":"-1","median":"10","maximum":"15"})
        self.assertEqual(p["all_eligible_pairs"]["competitor_higher"],2)
        self.assertEqual(p["all_eligible_pairs"]["reference_higher"],1)
        self.assertEqual(p["both_traded_pairs"]["competitor_minus_reference_net_pnl"]["median"],"12.5")
        self.assertEqual(p["matched_reference_fees"]["median"],"1.3")

    def test_hold_prefix_is_censored_not_zero_or_an_unfair_window_error(self):
        rows=cohort();leg(rows,pnl="-20",state="held");leg(rows,"growth_recovery_call",pnl="5")
        p=self.result(rows)["recovery_pairs"]["growth_recovery_call"]
        self.assertEqual(p["eligibility_matrix"]["competitor_only_eligible"],1)
        self.assertEqual(p["all_eligible_pairs"]["competitor_minus_reference_net_pnl"]["count"],0)
        self.assertEqual(p["exclusion_counts_may_overlap"]["reference_held"],1)

    def test_four_censoring_matrix_cells_and_context_exclusion(self):
        a,b,c,d=cohort("AAA"),cohort("BBB"),cohort("CCC"),cohort("DDD")
        leg(b,"growth_recovery_call",context=False)
        leg(c,context=False)
        leg(d,state="held");leg(d,"growth_recovery_call",state="open")
        p=self.result(a+b+c+d)["recovery_pairs"]["growth_recovery_call"]
        self.assertEqual(p["eligibility_matrix"],{"both_eligible":1,"reference_only_eligible":1,
            "competitor_only_eligible":1,"neither_eligible":1})
        self.assertEqual(p["exclusion_counts_may_overlap"]["reference_context_incomplete"],1)

    def test_pending_buy_with_no_position_is_a_valid_nonflat_censored_endpoint(self):
        rows=cohort();v=next(r for r in rows if r["candidate"]==review.REFERENCE)
        v.update(complete="False",finished_flat="False",status="incomplete",research_gate="open_at_input_end")
        result=self.result(rows)
        self.assertEqual(result["families"][review.REFERENCE]["status_counts"]["incomplete"],1)
        self.assertEqual(result["recovery_pairs"]["cash"]["eligibility_matrix"]["competitor_only_eligible"],1)

    def test_open_position_cannot_claim_flat_or_complete(self):
        rows=cohort();v=leg(rows,state="open");v["finished_flat"]="True";self.rejects(rows)
        rows=cohort();v=leg(rows,state="open");v["complete"]="True";self.rejects(rows)

    def test_volatility_is_summary_only_and_rotation_can_start_at23(self):
        rows=cohort(preset="volatility_regime",source_rows=23,start="2023-10-01",end="2023-11-30")
        leg(rows,"regime_rotating",pnl="5")
        result=self.result(rows);f=result["families"]["regime_rotating"]
        self.assertEqual(result["recovery_pairs"],{})
        self.assertEqual(f["source_rows_below_registered_warmup"],0)
        self.assertEqual(f["volatility_rotation_not_all_components_warmed"],1)
        self.assertEqual(f["eligible_traded_net_pnl"]["median"],"5")
        self.assertEqual(result["families"]["raw_pullback_call"]["source_rows_below_registered_warmup"],1)

    def test_short_warmup_no_trades_are_retained_but_not_compared(self):
        rows=cohort(source_rows=10)
        result=self.result(rows); f=result["families"][review.REFERENCE]
        self.assertEqual(f["status_counts"]["no_trade"],1)
        self.assertEqual(f["source_rows_below_registered_warmup"],1)
        self.assertEqual(result["recovery_pairs"]["cash"]["eligibility_matrix"]["competitor_only_eligible"],1)

    def test_synthetic_evidence_stays_in_its_own_stratum(self):
        rows=cohort()
        for row in rows:row.update(evidence_kind="synthetic_fixture",research_gate="synthetic_fixture_only")
        result=self.result(rows)
        self.assertEqual(result["stratum"]["evidence_kind"],"synthetic_fixture")

    def test_same_portfolio_different_window_profile_and_mode_do_not_mix(self):
        first=cohort();second=cohort(profile="strict_sizes");third=cohort(mode="pooled")
        fourth=cohort(start="2023-10-01")
        result=review.compare_rows(first+second+third+fourth)
        self.assertEqual(len(result["strata"]),4)
        self.assertTrue(all(s["cohorts"]==1 for s in result["strata"]))

    def test_unfair_candidate_window_profile_and_source_count_fail_closed(self):
        for key,value in (("start","2023-10-01"),("source_end","2023-12-01"),("profile","strict_sizes"),
                          ("source_rows","23"),("case_id","another-case")):
            rows=cohort();rows[1][key]=value
            if key=="source_end":rows[1]["accepted_end"]=value
            self.rejects(rows)

    def test_duplicate_cohort_even_renamed_case_is_rejected(self):
        rows=cohort();other=cohort()
        for row in other:row["case_id"]="renamed-but-same-cohort"
        self.rejects(rows+other)
        rows=cohort();self.rejects(rows+[deepcopy(rows[0])])

    def test_fixed_candidate_set_cash_and_recovery_cannot_be_omitted(self):
        for candidate in ("cash",review.REFERENCE,"raw_breakout_call"):
            self.rejects([v for v in cohort() if v["candidate"]!=candidate])
        rows=cohort();rows[1]["candidate"]="rsi_unregistered";self.rejects(rows)
        self.rejects([])

    def test_exact_schema_string_types_and_canonical_booleans(self):
        for mutate in (lambda r:r.pop("fees"),lambda r:r.update(extra="0"),lambda r:r.update(pnl=1),
                       lambda r:r.update(complete="true"),lambda r:r.update(case_id="bad\ncase")):
            rows=cohort();mutate(rows[1]);self.rejects(rows)

    def test_finite_plain_decimal_bounds_and_exact_counts(self):
        for value in ("NaN","Infinity","1e6","01","1000000000000","0.0000000000001","-1"):
            rows=cohort();rows[1]["fees"]=value;self.rejects(rows)
        for value in ("-1","1.0","01","10001"):
            rows=cohort();rows[1]["entries"]=value;self.rejects(rows)

    def test_cash_and_no_entry_funding_cannot_be_reported_as_profit(self):
        rows=cohort();rows[0].update(pnl="500",status="gain");self.rejects(rows)
        rows=cohort();rows[1].update(pnl="500",status="gain");self.rejects(rows)
        rows=cohort();leg(rows,"cash");self.rejects(rows)

    def test_fees_risk_limit_context_and_status_consistency(self):
        for key,value in (("fees","0"),("max_commitment","500.01"),("max_commitment","101"),
                          ("pnl","-100.01"),
                          ("closed","2"),("context_required","True"),("status","loss"),
                          ("research_gate","cash_control"),("processed_rows","23")):
            rows=cohort();v=leg(rows);v[key]=value;self.rejects(rows)

    def test_portfolio_membership_is_bounded_sorted_and_not_pooled_with_individuals(self):
        for portfolio in ("BBB,AAA","AAA,AAA","aaa","AAA/BBB",",".join("A"+str(i) for i in range(65))):
            self.rejects(cohort(portfolio,mode="pooled"))
        self.rejects(cohort("AAA,BBB",mode="individual"))

    def test_decimal_host_context_does_not_change_comparisons(self):
        rows=cohort();leg(rows,pnl="0.000000000001");leg(rows,"raw_breakout_call",pnl="0.000000000002")
        normal=review.compare_rows(rows)
        with localcontext() as context:
            context.prec=1;context.rounding=ROUND_UP;context.traps[Inexact]=True
            self.assertEqual(review.compare_rows(rows),normal)

    def test_row_limit_does_not_silently_truncate(self):
        with patch.object(review,"MAX_ROWS",8):self.rejects(cohort()+cohort("BBB"))

    def test_output_limit_is_enforced_without_truncation(self):
        with patch.object(review,"MAX_OUTPUT_BYTES",128):self.rejects(cohort())

    def test_arbitrary_row_order_has_identical_result(self):
        rows=cohort()+cohort("BBB");leg(rows,pnl="-10")
        self.assertEqual(review.compare_rows(rows),review.compare_rows(reversed(rows)))
        self.assertLess(len(json.dumps(review.compare_rows(rows)).encode()),review.MAX_OUTPUT_BYTES)

    def test_fixed_rule_and_aliased_registry_mutation_fail_closed(self):
        with patch.dict(review.RULE,{"capital":"changed"}):self.rejects(cohort())
        with patch.dict(review.PRESETS,{"conventional_regime":review.PRESETS["conventional_regime"][:-1]}):
            self.rejects(cohort())

    def test_rule_mutation_during_input_iteration_is_detected(self):
        def changed():
            yield from cohort()
            review.RULE["capital"]="changed at source EOF"
        with patch.dict(review.RULE):self.rejects(changed())


if __name__=="__main__":unittest.main()
