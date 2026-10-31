"""Exact scenario arithmetic; fixtures are assumptions, never market prices."""
from copy import deepcopy
from decimal import localcontext
import json
import unittest
from unittest.mock import patch

from ariadne import revenue_planner as planner


def offer(kind='pod'):
    return {'id':'illustrative-pod','name':'Unvalidated illustrative apparel assumptions','kind':kind,
        'unit_price':'29.99','units_assumed':300,'owner_hours_per_unit':'0.1',
        'costs':{'production_per_unit':'12.00','shipping_per_unit':'5.00','other_variable_per_unit':'0.00',
            'payment_fee_bps':300,'payment_fee_fixed_per_unit':'0.30','refund_reserve_bps':200,'acquisition_per_unit':'3.00'},
        'funding':{'mode':'merchant_fronts','payout_lag_days':14},'sources':[]}


def fixture():
    return {'schema':1,'label':'Unvalidated hypothetical revenue comparison','system_budget_monthly':'100.00',
        'actual_system_cost_monthly':None,'other_fixed_cost_monthly':'0.00','monthly_profit_target':'2000.00',
        'owner_hourly_value':'35.00','weekly_hours_profiles':[10,20],'fixed_nonbillable_hours_monthly':'12',
        'offers':[offer()]}


class RevenuePlannerTests(unittest.TestCase):
    def calculate(self,value=None):return planner.calculate(fixture() if value is None else value)

    def test_exact_pod_contribution_target_capacity_and_working_capital(self):
        result=self.calculate();row=result['offers'][0]
        self.assertEqual(result['required_monthly_cash_contribution'],'2100.00')
        self.assertEqual(row['per_unit'],{'production':'12.00','shipping':'5.00','other_variable':'0.00',
            'payment_fee':'1.20','refund_reserve':'0.60','acquisition':'3.00','total_variable':'21.80',
            'cash_contribution':'8.19','economic_contribution':'4.69'})
        self.assertEqual(row['monthly']['gross_sales_assumed'],'8997.00')
        self.assertEqual(row['monthly']['cash_surplus_before_owner_compensation_and_personal_taxes'],'2357.00')
        self.assertEqual(row['monthly']['owner_hours'],'42')
        self.assertEqual(row['monthly']['owner_time_value'],'1470.00')
        self.assertEqual(row['monthly']['economic_surplus_before_personal_taxes'],'887.00')
        self.assertEqual(row['target']['minimum_units_cash'],257)
        self.assertEqual(row['target']['minimum_units_economic'],538)
        self.assertTrue(row['capacity'][0]['cash_target_units_fit'])
        self.assertFalse(row['capacity'][0]['economic_target_units_fit'])
        self.assertEqual(row['capacity'][0]['maximum_units_under_hours'],313)
        self.assertEqual(row['capacity'][1]['monthly_hours'],'86.67')
        self.assertEqual(row['funding']['units_during_payout_gap'],140)
        self.assertEqual(row['funding']['working_capital_estimate'],'2800.00')

    def test_customer_funded_fulfillment_keeps_marketing_and_other_front_costs(self):
        value=fixture();value['offers'][0]['funding']['mode']='deducted_from_customer_payment'
        row=self.calculate(value)['offers'][0]
        self.assertEqual(row['funding']['front_cost_per_unit'],'3.00')
        self.assertEqual(row['funding']['working_capital_estimate'],'420.00')
        self.assertTrue(row['funding']['customer_payment_covers_deducted_costs'])
        self.assertEqual(row['per_unit']['cash_contribution'],'8.19')
        value['offers'][0]['costs']['other_variable_per_unit']='2.00'
        self.assertEqual(self.calculate(value)['offers'][0]['funding']['working_capital_estimate'],'700.00')

    def test_same_day_payout_has_first_unit_floor_and_zero_sales_have_no_gap_units(self):
        value=fixture();value['offers'][0]['funding']['payout_lag_days']=0
        row=self.calculate(value)['offers'][0]
        self.assertEqual(row['funding']['units_during_payout_gap'],1)
        self.assertEqual(row['funding']['working_capital_estimate'],'20.00')
        value['offers'][0]['units_assumed']=0
        row=self.calculate(value)['offers'][0]
        self.assertEqual(row['funding']['working_capital_estimate'],'0.00')
        self.assertEqual(row['monthly']['gross_sales_assumed'],'0.00')
        self.assertEqual(row['monthly']['cash_surplus_before_owner_compensation_and_personal_taxes'],'-100.00')

    def test_underfunded_customer_payment_is_not_presented_as_zero_required_cash(self):
        value=fixture();item=value['offers'][0];item['unit_price']='10.00';item['funding']['mode']='deducted_from_customer_payment'
        row=self.calculate(value)['offers'][0]
        self.assertFalse(row['funding']['customer_payment_covers_deducted_costs'])
        self.assertIsNone(row['funding']['working_capital_estimate'])
        self.assertIsNone(row['target']['minimum_units_cash'])
        self.assertFalse(row['target']['cash_target_met_under_assumptions'])

    def test_unknown_unit_cost_never_becomes_a_zero_margin_input(self):
        for field in fixture()['offers'][0]['costs']:
            value=fixture();value['offers'][0]['costs'][field]=None
            row=self.calculate(value)['offers'][0]
            with self.subTest(field=field):
                self.assertFalse(row['unit_costs_complete'])
                self.assertIn(field,row['unknown_unit_cost_fields'])
                self.assertIsNone(row['per_unit']['cash_contribution'])
                self.assertIsNone(row['monthly']['cash_surplus_before_owner_compensation_and_personal_taxes'])
                self.assertIsNone(row['target']['minimum_units_cash'])
                self.assertIsNone(row['target']['cash_target_met_under_assumptions'])

    def test_unknown_fixed_cost_suppresses_net_profit_and_count_but_not_known_unit_margin(self):
        value=fixture();value['other_fixed_cost_monthly']=None
        result=self.calculate(value);row=result['offers'][0]
        self.assertEqual(row['per_unit']['cash_contribution'],'8.19')
        self.assertIsNone(result['fixed_cash_cost_used'])
        self.assertIsNone(result['required_monthly_cash_contribution'])
        self.assertIsNone(row['monthly']['cash_surplus_before_owner_compensation_and_personal_taxes'])
        self.assertIsNone(row['target']['minimum_units_cash'])
        self.assertEqual(result['unknown_fixed_cost_fields'],['other_fixed_cost_monthly'])

    def test_actual_system_cost_replaces_budget_once(self):
        value=fixture();value['actual_system_cost_monthly']='75.00'
        result=self.calculate(value)
        self.assertEqual(result['system_cost_used'],'75.00')
        self.assertEqual(result['fixed_cash_cost_used'],'75.00')
        self.assertEqual(result['required_monthly_cash_contribution'],'2075.00')
        self.assertEqual(result['system_cost_basis'],'owner_supplied_actual')
        self.assertFalse(result['actual_system_cost_unknown'])
        self.assertEqual(self.calculate()['system_cost_basis'],'budget_assumption_not_measured')

    def test_service_client_billing_cycle_is_distinct_from_owner_compensation(self):
        value=fixture();item=offer('recurring_service');item.update(id='service',name='Unvalidated service assumptions',unit_price='500.00',units_assumed=5,owner_hours_per_unit='4')
        item['costs'].update(production_per_unit='0.00',shipping_per_unit='0.00',other_variable_per_unit='10.00',
            payment_fee_fixed_per_unit='0.00',refund_reserve_bps=0,acquisition_per_unit='20.00')
        value['offers']=[item];row=self.calculate(value)['offers'][0]
        self.assertEqual(row['per_unit']['cash_contribution'],'455.00')
        self.assertEqual(row['monthly']['cash_surplus_before_owner_compensation_and_personal_taxes'],'2175.00')
        self.assertEqual(row['monthly']['economic_surplus_before_personal_taxes'],'1055.00')
        self.assertEqual(row['target']['minimum_units_cash'],5)
        self.assertEqual(row['target']['minimum_units_economic'],8)
        self.assertFalse(row['capacity'][0]['economic_target_units_fit'])

    def test_fee_reserve_and_owner_time_round_up_per_unit_consistently(self):
        value=fixture();item=value['offers'][0]
        item.update(unit_price='0.01',units_assumed=3,owner_hours_per_unit='0.000001')
        for key in item['costs']:item['costs'][key]=0 if key.endswith('_bps') else '0.00'
        item['costs'].update(payment_fee_bps=1,refund_reserve_bps=1)
        row=self.calculate(value)['offers'][0]
        self.assertEqual(row['per_unit']['payment_fee'],'0.01')
        self.assertEqual(row['per_unit']['refund_reserve'],'0.01')
        self.assertEqual(row['per_unit']['cash_contribution'],'-0.01')
        self.assertEqual(row['per_unit']['economic_contribution'],'-0.02')
        self.assertEqual(row['monthly']['owner_time_value'],'420.03')

    def test_capacity_uses_exact_week_ratio_and_separates_zero_hours_from_unknown(self):
        value=fixture();item=value['offers'][0]
        item['owner_hours_per_unit']='0'
        row=self.calculate(value)['offers'][0]
        self.assertTrue(row['capacity'][0]['zero_unit_hours_unconstrained'])
        self.assertIsNone(row['capacity'][0]['maximum_units_under_hours'])
        self.assertTrue(row['capacity'][0]['assumed_units_fit'])
        item['owner_hours_per_unit']=None
        row=self.calculate(value)['offers'][0]
        self.assertFalse(row['capacity'][0]['zero_unit_hours_unconstrained'])
        self.assertIsNone(row['capacity'][0]['assumed_units_fit'])
        self.assertIsNone(row['monthly']['economic_surplus_before_personal_taxes'])
        value['fixed_nonbillable_hours_monthly']='44';item['owner_hours_per_unit']='0'
        self.assertFalse(self.calculate(value)['offers'][0]['capacity'][0]['assumed_units_fit'])

    def test_unknown_payout_lag_or_front_cost_does_not_create_cash_buffer_claim(self):
        for change in ('lag','front'):
            value=fixture()
            if change=='lag':value['offers'][0]['funding']['payout_lag_days']=None
            else:value['offers'][0]['costs']['production_per_unit']=None
            self.assertIsNone(self.calculate(value)['offers'][0]['funding']['working_capital_estimate'])

    def test_nonpositive_contribution_and_large_target_counts_are_explicit(self):
        value=fixture();item=value['offers'][0];item['unit_price']='0.00'
        row=self.calculate(value)['offers'][0]
        self.assertIsNone(row['target']['minimum_units_cash'])
        for key in item['costs']:item['costs'][key]=0 if key.endswith('_bps') else '0.00'
        item.update(unit_price='0.01',owner_hours_per_unit='0')
        row=self.calculate(value)['offers'][0]
        self.assertEqual(row['target']['minimum_units_cash'],210000)
        self.assertTrue(row['target']['counts_above_planning_limit'])

    def test_canonical_parse_detaches_input_and_requires_strict_numeric_types(self):
        value=fixture();before=deepcopy(value)
        parsed=planner.parse_plan(planner.canonical(value));self.assertEqual(value,before)
        parsed['offers'][0]['name']='Changed';self.assertEqual(value,before)
        for mutate in (lambda x:x.update(schema=True),lambda x:x.update(system_budget_monthly=100.0),
                       lambda x:x['offers'][0].update(units_assumed=True),lambda x:x['offers'][0]['costs'].update(payment_fee_bps=1.5),
                       lambda x:x['offers'][0].update(owner_hours_per_unit='1e-2'),lambda x:x.update(extra=True)):
            invalid=fixture();mutate(invalid)
            with self.assertRaises(ValueError):planner.validate(invalid)
        for raw in (b'{"schema":1,"schema":1}',b'{"a":NaN}',b'{"a":1.0}',b'['*2000+b'0'+b']'*2000,b'x'*(planner.MAX_INPUT_BYTES+1)):
            with self.assertRaises(ValueError):planner.parse_plan(raw)

    def test_money_bounds_surrogates_and_reference_urls_are_guarded_without_fetching(self):
        for value in (True,1,1.0,'NaN','Infinity','-0.01','1e2','01.00','1.001','1000000.01',' 1.00'):
            with self.subTest(value=value),self.assertRaises(ValueError):planner.money_cents(value)
        self.assertEqual(planner.money_cents('1000000.00'),100000000)
        self.assertEqual(planner.format_money(-1),'-0.01')
        for url in ('http://example.com/x','https://localhost/x','https://127.0.0.1/x','https://10.0.0.1/x',
                    'https://127.1/x','https://0177.0.0.1/x','https://2130706433/x','https://0x7f.0.0.1/x','https://bad-.example/x',
                    'https://user:secret@example.com/x','https://example.com/x?key=secret','https://example.com/x#fragment'):
            value=fixture();value['offers'][0]['sources']=[{'label':'Owner cost note','url':url,'as_of':'2026-10-30'}]
            with self.assertRaises(ValueError):planner.validate(value)
        for label in ('bad\x00label','bad\ud800label'):
            value=fixture();value['label']=label
            with self.assertRaises(ValueError):planner.validate(value)
        value=fixture();value['offers'][0]['sources']=[{'label':'Owner-supplied dated reference','url':'https://www.printify.com/pricing/','as_of':'2026-10-30'}]
        with patch('socket.socket',side_effect=AssertionError('No network')),patch('subprocess.Popen',side_effect=AssertionError('No process')):
            self.assertEqual(self.calculate(value)['offers'][0]['source_status'],'owner_supplied_references_unverified')

    def test_global_decimal_context_does_not_change_exact_integer_arithmetic(self):
        expected=self.calculate()
        with localcontext() as context:
            context.prec=2
            self.assertEqual(self.calculate(),expected)
        self.assertEqual(expected['trading_income_budgeted'],'0.00')
        self.assertTrue(expected['planning_only'])
        self.assertEqual(expected['model_calls'],expected['network_calls'])

    def test_maximum_plan_is_bounded_and_does_not_claim_actual_or_verified_results(self):
        value=fixture();value['offers']=[]
        for index in range(12):
            item=offer();item.update(id='offer-'+str(index),units_assumed=10000)
            value['offers'].append(item)
        report=self.calculate(value)
        self.assertEqual(len(report['offers']),12)
        self.assertLess(len(planner.canonical(report)),planner.MAX_OUTPUT_BYTES)
        self.assertEqual(report['status'],'hypothetical_scenario_only')
        with self.assertRaises(ValueError):
            value['offers'].append(offer());planner.validate(value)


if __name__=='__main__':unittest.main()
