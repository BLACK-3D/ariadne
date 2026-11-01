"""Generated completed-report observations; never historical investment evidence."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
import hashlib
import unittest
from unittest.mock import patch

from ariadne import options_paired_review as base, options_stress_review as review
from scripts.options_paired_review import render
from test_options_paired_review import cohort, leg


def rows(symbol="AAA", preset="conventional_regime", **kwargs):
    return cohort(symbol, preset=preset, source_rows=80, start="2023-08-01", end="2023-11-30", **kwargs)


class StressReviewTests(unittest.TestCase):
    def result(self, values):
        return review.compare_rows(iter(values))["strata"][0]

    def test_two_fixed_references_have_all_seven_same_preset_peers(self):
        for preset, reference in review.REFERENCES.items():
            with self.subTest(preset=preset):
                values=rows(preset=preset);before=deepcopy(values);result=self.result(values)
                self.assertEqual(result["reference_candidate"],reference)
                self.assertEqual(set(result["stress_pairs"]),set(base.PRESETS[preset])-{reference})
                self.assertEqual(result["families"],base.compare_rows(values)["strata"][0]["families"])
                self.assertEqual(values,before)

    def test_generated_signed_pairs_activity_fees_and_medians_are_not_added_money(self):
        groups=[rows(s) for s in ("AAA","BBB","CCC","DDD","EEE")]
        for group,pnl in zip(groups[:2],("10","-20")):leg(group,"stress_breakdown_put",pnl=pnl)
        leg(groups[0],"raw_breakdown_put",pnl="20")
        leg(groups[1],"raw_breakdown_put",pnl="-5")
        leg(groups[2],"raw_breakdown_put",pnl="-1")
        leg(groups[3],"stress_breakdown_put",pnl="5")
        report=review.compare_rows([r for g in groups for r in g])
        p=report["strata"][0]["stress_pairs"]["raw_breakdown_put"]
        self.assertEqual(p["all_eligible_pairs"]["competitor_minus_reference_net_pnl"],
            {"count":5,"minimum":"-5","median":"0","maximum":"15"})
        self.assertEqual(p["both_traded_pairs"]["competitor_minus_reference_net_pnl"]["median"],"12.5")
        self.assertEqual(p["all_eligible_pairs"]["competitor_higher"],2)
        self.assertEqual(p["all_eligible_pairs"]["reference_higher"],2)
        self.assertEqual(p["all_eligible_pairs"]["equal"],1)
        self.assertEqual(p["eligible_pair_activity_counts"],{"both_traded":2,"reference_only_traded":1,
            "competitor_only_traded":1,"neither_traded":1})
        self.assertEqual(p["matched_reference_fees"]["maximum"],"1.3")
        self.assertFalse(report["capital_or_pnl_summed"]);self.assertFalse(report["independent_win_probability_estimated"])

    def test_cash_pairs_include_zero_outcomes_but_never_both_traded(self):
        values=rows();leg(values,"stress_breakdown_put",pnl="-20")
        p=self.result(values)["stress_pairs"]["cash"]
        self.assertEqual(p["all_eligible_pairs"]["competitor_minus_reference_net_pnl"]["median"],"20")
        self.assertEqual(p["both_traded_pairs"]["competitor_minus_reference_net_pnl"]["count"],0)
        self.assertEqual(p["eligible_pair_activity_counts"]["reference_only_traded"],1)

    def test_held_prefix_remains_censored_and_not_imputed_as_zero(self):
        values=cohort();leg(values,"stress_breakdown_put",pnl="-20",state="held")
        leg(values,"raw_breakdown_put",pnl="5")
        result=self.result(values);p=result["stress_pairs"]["raw_breakdown_put"]
        self.assertEqual(p["eligibility_matrix"]["competitor_only_eligible"],1)
        self.assertEqual(p["all_eligible_pairs"]["competitor_minus_reference_net_pnl"]["count"],0)
        self.assertEqual(result["families"]["stress_breakdown_put"]["status_counts"]["held"],1)
        self.assertEqual(p["exclusion_counts_may_overlap"]["reference_held"],1)

    def test_all_four_eligibility_cells_and_context_incomplete_complete_rows(self):
        groups=[rows(s) for s in ("AAA","BBB","CCC","DDD")]
        leg(groups[1],"raw_breakdown_put",context=False)
        leg(groups[2],"stress_breakdown_put",context=False)
        leg(groups[3],"stress_breakdown_put",state="open")
        leg(groups[3],"raw_breakdown_put",state="open")
        p=self.result([r for g in groups for r in g])["stress_pairs"]["raw_breakdown_put"]
        self.assertEqual(p["eligibility_matrix"],{"both_eligible":1,"reference_only_eligible":1,
            "competitor_only_eligible":1,"neither_eligible":1})
        self.assertEqual(p["exclusion_counts_may_overlap"]["reference_context_incomplete"],1)
        self.assertEqual(sum(p["eligible_pair_activity_counts"].values()),1)

    def test_pending_buy_zero_entry_flat_position_is_nonflat_censored(self):
        values=rows();value=next(r for r in values if r["candidate"]=="stress_breakdown_put")
        value.update(complete="False",finished_flat="False",status="incomplete",research_gate="open_at_input_end")
        self.assertEqual(self.result(values)["stress_pairs"]["cash"]["eligibility_matrix"]["competitor_only_eligible"],1)

    def test_volatility23_warmup_allows_shock_not_longer_peers(self):
        values=cohort(preset="volatility_regime",source_rows=23,start="2023-10-01",end="2023-11-30")
        leg(values,"stress_shock_put",pnl="5");leg(values,"regime_rotating",pnl="3")
        result=self.result(values)
        self.assertEqual(result["stress_pairs"]["raw_shock_put"]["eligibility_matrix"]["both_eligible"],1)
        for name in ("raw_compression_call","raw_pullback_call"):
            self.assertEqual(result["stress_pairs"][name]["eligibility_matrix"]["reference_only_eligible"],1)
        self.assertEqual(result["families"]["regime_rotating"]["volatility_rotation_not_all_components_warmed"],1)
        self.assertEqual(result["stress_pairs"]["regime_rotating"]["both_traded_pairs"]["reference_higher"],1)

    def test_short_warmup_all_zero_cases_remain_status_counts_not_winners(self):
        result=self.result(cohort(preset="volatility_regime"))
        self.assertEqual(result["families"]["stress_shock_put"]["status_counts"]["no_trade"],1)
        self.assertEqual(result["families"]["stress_shock_put"]["source_rows_below_registered_warmup"],1)
        self.assertEqual(result["stress_pairs"]["cash"]["eligibility_matrix"]["competitor_only_eligible"],1)
        self.assertNotIn("winner",result)

    def test_incomparable_windows_presets_modes_and_profiles_are_separate(self):
        groups=[rows(),rows(profile="strict_sizes"),rows(mode="pooled"),rows(preset="volatility_regime"),
                cohort(start="2023-10-01")]
        report=review.compare_rows([r for g in groups for r in g])
        self.assertEqual(len(report["strata"]),5)
        self.assertTrue(all(x["cohorts"]==1 for x in report["strata"]))
        self.assertFalse(report["cross_preset_or_window_ranking"])

    def test_schema_risk_fee_and_incomplete_candidate_sets_fail_closed(self):
        for mutate in (lambda r:r.pop("fees"),lambda r:r.update(pnl="Infinity"),
                       lambda r:r.update(fees="0.1"),lambda r:r.update(max_commitment="500.01"),
                       lambda r:r.update(case_id="unmatched")):
            values=rows();value=leg(values,"stress_breakdown_put");mutate(value)
            with self.subTest(mutate=mutate),self.assertRaises(review.StressReviewError):review.compare_rows(values)
        for missing in ("cash","stress_breakdown_put","raw_recovery_call"):
            with self.subTest(missing=missing),self.assertRaises(review.StressReviewError):
                review.compare_rows([r for r in rows() if r["candidate"]!=missing])
        with self.assertRaises(review.StressReviewError):review.compare_rows(rows()+rows())

    def test_decimal_context_and_inputs_cannot_change_results(self):
        values=rows();leg(values,"stress_breakdown_put",pnl="1.123456789012");leg(values,"raw_breakdown_put",pnl="-2.25")
        before=deepcopy(values);normal=review.compare_rows(values)
        with localcontext() as settings:
            settings.prec=1;settings.rounding=ROUND_UP;settings.traps[Inexact]=True
            self.assertEqual(review.compare_rows(values),normal)
        self.assertEqual(values,before)

    def test_rules_are_bound_before_and_after_iterator_consumption(self):
        with patch.dict(review.REFERENCES,{"conventional_regime":"cash"}):
            with self.assertRaises(review.StressReviewError):review.compare_rows(rows())
        def changed():
            yield from rows()
            review.RULE["paired_delta"]="changed"
        original=review.RULE["paired_delta"]
        try:
            with self.assertRaises(review.StressReviewError):review.compare_rows(changed())
        finally:review.RULE["paired_delta"]=original

    def test_fixed_limits_reject_instead_of_truncating(self):
        with patch.object(review,"MAX_ROWS",7):
            with self.assertRaises(review.StressReviewError):review.compare_rows(rows())
        with patch.object(review,"MAX_OUTPUT_BYTES",100):
            with self.assertRaises(review.StressReviewError):review.compare_rows(rows())

    def test_default_recovery_rule_and_html_bytes_remain_unchanged(self):
        self.assertEqual(base.RULE_SHA256,"565cf94868d70734ac0df10e4e17f8edc4da676d1aeddd25ab8f4deb52d16d9b")
        result=base.compare_rows(cohort())
        self.assertEqual(hashlib.sha256(render(result)).hexdigest(),"92fc09252f3d05278803e6d79330ff9e06df95aaf68bcd47054b2f9f8daf3370")
        self.assertEqual(result["reference_candidate"],"raw_recovery_call")
        self.assertNotIn("stress_pairs",result["strata"][0])
        self.assertEqual(base.compare_rows(cohort(preset="volatility_regime"))["strata"][0]["recovery_pairs"],{})

    def test_output_discloses_limits_and_operations_without_qualification_claims(self):
        report=review.compare_rows(rows())
        for key in ("source_authentication_established","individual_entry_cap_verified_from_csv","execution_qualified",
                    "automatic_adoption","capital_or_pnl_summed","independent_win_probability_estimated"):
            self.assertIs(report[key],False)
        for key in ("backtests_run","owner_operations","network_calls","model_calls"):self.assertEqual(report[key],0)
        html=render(report).decode()
        self.assertIn("Stress-filtered puts / paired evidence review",html)
        self.assertIn("Stress put higher",html);self.assertNotIn("Recovery higher",html)
        self.assertIn("Fixed reference: stress_breakdown_put",html)


if __name__=="__main__":unittest.main()

