"""Pure USD contribution and fulfillment-funding scenarios, never revenue proof.

No provider price is embedded. Money is integer cents; rate-based costs and
imputed time value round up per unit. Capacity uses the exact 52/12 week ratio.
"""
from __future__ import annotations

from datetime import date
import ipaddress
import json
import re
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_UNITS = 10000
MAX_CENTS = 100000000
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?\Z")
_HOURS = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_TOP = {"schema", "label", "system_budget_monthly", "actual_system_cost_monthly", "other_fixed_cost_monthly",
        "monthly_profit_target", "owner_hourly_value", "weekly_hours_profiles", "fixed_nonbillable_hours_monthly", "offers"}
_OFFER = {"id", "name", "kind", "unit_price", "units_assumed", "owner_hours_per_unit", "costs", "funding", "sources"}
_COSTS = {"production_per_unit", "shipping_per_unit", "other_variable_per_unit", "payment_fee_bps",
          "payment_fee_fixed_per_unit", "refund_reserve_bps", "acquisition_per_unit"}


class RevenuePlannerError(ValueError):
    pass


def _ceil(numerator, denominator):
    return -(-numerator // denominator)


def format_money(cents):
    if type(cents) is not int:
        raise RevenuePlannerError("Money output requires integer cents.")
    sign, value = ("-", -cents) if cents < 0 else ("", cents)
    return sign+str(value//100)+"."+str(value%100).zfill(2)


def money_cents(value, name="amount", *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not _MONEY.fullmatch(value):
        raise RevenuePlannerError(name+" requires a nonnegative decimal money string, with at most two decimal places.")
    whole, _, fractional = value.partition(".")
    cents = int(whole)*100+int(fractional.ljust(2, "0") or "0")
    if cents > MAX_CENTS:
        raise RevenuePlannerError(name+" exceeds the USD1,000,000 input bound.")
    return cents


def _money(value, name, *, nullable=False):
    cents = money_cents(value, name, nullable=nullable)
    return None if cents is None else format_money(cents)


def _hours(value, name, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not _HOURS.fullmatch(value):
        raise RevenuePlannerError(name+" requires a nonnegative decimal hours string with at most six places.")
    whole, _, fractional = value.partition(".")
    micros = int(whole)*1000000+int(fractional.ljust(6,"0") or "0")
    if micros > 744000000:
        raise RevenuePlannerError(name+" exceeds744 hours.")
    return micros


def _quantity(micros):
    whole, remainder = divmod(micros,1000000)
    return str(whole)+(('.'+str(remainder).zfill(6).rstrip('0')) if remainder else '')


def _integer(value, name, low, high, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or not low <= value <= high:
        raise RevenuePlannerError(name+" requires a bounded whole number.")
    return value


def _text(value, name, maximum):
    if (type(value) is not str or not 1 <= len(value.strip()) <= maximum
            or any(ord(char)<32 or 0x7f<=ord(char)<=0x9f or 0xd800<=ord(char)<=0xdfff for char in value)):
        raise RevenuePlannerError(name+" requires bounded nonempty text without controls.")
    return value.strip()


def _exact(value, keys, name):
    if type(value) is not dict or set(value) != keys:
        raise RevenuePlannerError(name+" requires exactly its documented fields.")


def _url(value):
    if value is None:
        return None
    if type(value) is not str or len(value)>500 or any(ord(char)<=32 or ord(char)>=127 for char in value):
        raise RevenuePlannerError("Cost references require bounded ordinary HTTPS URLs or null.")
    try:
        parsed=urlsplit(value);host=(parsed.hostname or '').lower()
        if (parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.port not in (None,443)
                or parsed.query or parsed.fragment or not re.fullmatch(r"[a-z0-9.-]+",host)
                or '..' in host or host.startswith('.') or host.endswith('.') or '.' not in host
                or host in {'localhost','localhost.localdomain'} or host.endswith(('.localhost','.local','.lan','.internal','.home'))):
            raise ValueError
        try:
            address=ipaddress.ip_address(host)
        except ValueError:
            address=None
            labels=host.split('.')
            if (all(re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)',label) for label in labels)
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in labels)):
                raise RevenuePlannerError("Numeric aliases and invalid DNS labels are not cost-reference hosts.")
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError:
        raise RevenuePlannerError("Cost references require public HTTPS URLs without credentials, queries or fragments; no URL is fetched.") from None
    return value


def canonical(value, *, maximum=MAX_OUTPUT_BYTES):
    try:
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise RevenuePlannerError("Use bounded ordinary JSON.") from None
    if len(raw)>maximum:
        raise RevenuePlannerError("The revenue JSON exceeds its byte bound.")
    return raw


def validate(value):
    _exact(value,_TOP,"Revenue plan")
    if type(value['schema']) is not int or value['schema']!=1:
        raise RevenuePlannerError("Use revenue schema1.")
    result={'schema':1,'label':_text(value['label'],'label',200)}
    for key in ('system_budget_monthly','monthly_profit_target','owner_hourly_value'):
        result[key]=_money(value[key],key)
    for key in ('actual_system_cost_monthly','other_fixed_cost_monthly'):
        result[key]=_money(value[key],key,nullable=True)
    result['fixed_nonbillable_hours_monthly']=_quantity(_hours(value['fixed_nonbillable_hours_monthly'],'fixed nonbillable hours'))
    profiles=value['weekly_hours_profiles']
    if type(profiles) is not list or not 1<=len(profiles)<=4:
        raise RevenuePlannerError("Choose one to four weekly-hours profiles.")
    result['weekly_hours_profiles']=[_integer(item,'weekly hours',1,168) for item in profiles]
    if len(set(result['weekly_hours_profiles']))!=len(profiles):
        raise RevenuePlannerError("Weekly-hours profiles must differ.")
    offers=value['offers']
    if type(offers) is not list or not 1<=len(offers)<=12:
        raise RevenuePlannerError("Supply one to twelve hypothetical offers.")
    prepared,seen=[],set()
    for value in offers:
        _exact(value,_OFFER,'Offer')
        identifier=_text(value['id'],'offer id',64)
        if not _ID.fullmatch(identifier) or identifier in seen:
            raise RevenuePlannerError("Use unique bounded offer IDs.")
        seen.add(identifier)
        if type(value['kind']) is not str or value['kind'] not in {'pod','recurring_service'}:
            raise RevenuePlannerError("Choose pod or recurring_service.")
        item={'id':identifier,'name':_text(value['name'],'offer name',200),'kind':value['kind'],
              'unit_price':_money(value['unit_price'],'unit price'),'units_assumed':_integer(value['units_assumed'],'units assumed',0,MAX_UNITS)}
        hours=_hours(value['owner_hours_per_unit'],'owner hours per unit',nullable=True)
        item['owner_hours_per_unit']=None if hours is None else _quantity(hours)
        _exact(value['costs'],_COSTS,'Unit costs')
        costs={}
        for key in sorted(_COSTS):
            costs[key]=(_integer(value['costs'][key],key,0,10000,nullable=True) if key.endswith('_bps')
                        else _money(value['costs'][key],key,nullable=True))
        item['costs']=costs
        funding=value['funding'];_exact(funding,{'mode','payout_lag_days'},'Funding')
        if type(funding['mode']) is not str or funding['mode'] not in {'merchant_fronts','deducted_from_customer_payment'}:
            raise RevenuePlannerError("Choose the explicit fulfillment funding mode.")
        item['funding']={'mode':funding['mode'],'payout_lag_days':_integer(funding['payout_lag_days'],'payout lag days',0,90,nullable=True)}
        sources=value['sources']
        if type(sources) is not list or len(sources)>8:
            raise RevenuePlannerError("Use at most eight owner-supplied cost references.")
        item['sources']=[]
        for source in sources:
            _exact(source,{'label','url','as_of'},'Cost reference')
            stamp=source['as_of']
            if stamp is not None:
                try:
                    if type(stamp) is not str or len(stamp)!=10 or date.fromisoformat(stamp).isoformat()!=stamp:raise ValueError
                except ValueError:
                    raise RevenuePlannerError("Reference dates must be ISO calendar dates or null.") from None
            item['sources'].append({'label':_text(source['label'],'reference label',120),'url':_url(source['url']),'as_of':stamp})
        prepared.append(item)
    result['offers']=prepared
    canonical(result,maximum=MAX_INPUT_BYTES)
    return result


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:raise RevenuePlannerError("Duplicate revenue JSON keys are not accepted.")
        result[key]=value
    return result


def parse_plan(raw):
    if type(raw) is not bytes or not 1<=len(raw)<=MAX_INPUT_BYTES:
        raise RevenuePlannerError("A revenue plan requires at most64KiB UTF-8 JSON.")
    def integer(value):
        if len(value)>12:raise RevenuePlannerError("Revenue integer literal exceeds its bound.")
        return int(value)
    def reject(_):raise RevenuePlannerError("Money/hours require strings and counts require integers; floating/nonfinite JSON is not accepted.")
    try:
        return validate(json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_pairs,parse_int=integer,parse_float=reject,parse_constant=reject))
    except (UnicodeError,json.JSONDecodeError,RecursionError):
        raise RevenuePlannerError("Use bounded UTF-8 revenue-plan JSON.") from None


def _minimum(need, contribution):
    if need is None or contribution is None:return None
    if need<=0:return 0
    return None if contribution<=0 else _ceil(need,contribution)


def _cash(value):return None if value is None else format_money(value)


def calculate(value):
    cfg=validate(value)
    cents=lambda key:money_cents(cfg[key],key,nullable=True)
    actual=cents('actual_system_cost_monthly');system=cents('system_budget_monthly') if actual is None else actual
    other=cents('other_fixed_cost_monthly');fixed=None if other is None else system+other
    target=cents('monthly_profit_target');wage=cents('owner_hourly_value')
    fixed_hours=_hours(cfg['fixed_nonbillable_hours_monthly'],'fixed hours');fixed_time_cost=_ceil(fixed_hours*wage,1000000)
    result={'schema':1,'currency':'USD','label':cfg['label'],'status':'hypothetical_scenario_only','planning_only':True,
        'system_cost_basis':'budget_assumption_not_measured' if actual is None else 'owner_supplied_actual',
        'system_budget_monthly':cfg['system_budget_monthly'],'actual_system_cost_monthly':cfg['actual_system_cost_monthly'],
        'system_cost_used':format_money(system),'other_fixed_cost_monthly':cfg['other_fixed_cost_monthly'],
        'fixed_cash_cost_used':_cash(fixed),'monthly_profit_target':cfg['monthly_profit_target'],
        'required_monthly_cash_contribution':_cash(None if fixed is None else fixed+target),
        'owner_hourly_value':cfg['owner_hourly_value'],'fixed_nonbillable_hours_monthly':cfg['fixed_nonbillable_hours_monthly'],
        'weekly_hours_profiles':[str(item) for item in cfg['weekly_hours_profiles']],
        'unknown_fixed_cost_fields':['other_fixed_cost_monthly'] if other is None else [],'actual_system_cost_unknown':actual is None,
        'model_calls':0,'network_calls':0,'trading_income_budgeted':'0.00','offers':[],
        'notes':['All prices, sales volumes, costs, hours and payout terms are owner-supplied scenario assumptions, not verified demand or collected revenue.',
                 'USD unit price is retained gross sale proceeds excluding pass-through sales tax; retained shipping revenue belongs in that explicit price. This is not a tax calculation.',
                 'The system budget replaces unknown actual system cost once; it is not added to an actual bill. Unknown other fixed costs prevent monthly net-profit and target-count claims.',
                 'Payment fees and refund reserves apply to full gross unit price before costs. They round upward per unit to cents; refund reserves are planning deductions, not observed refund payments.',
                 'Owner time rounds upward to cents per delivered unit and separately for fixed monthly time. Cash surplus excludes owner compensation/personal tax; economic surplus subtracts that imputed time value, not a tax deduction.',
                 'Capacity uses exact52/12 weeks per month. Per-unit hours exclude separately supplied fixed sales/admin hours; no conversion, demand, retention or customer-acquisition result is assumed.',
                 'Working capital assumes uniform orders over30 days, with at least one unit of fronted costs for nonzero sales even at zero-day payout lag. It is not a verified peak-cash requirement or financing promise.',
                 'No trade gains, account creation, paid subscription, customer contact, order, provider query or language-model call occurs.']}
    for offer in cfg['offers']:
        price=money_cents(offer['unit_price']);units=offer['units_assumed'];costs=offer['costs']
        unknown=[key for key,value in costs.items() if value is None]
        money=lambda key:money_cents(costs[key],key,nullable=True)
        production,shipping,variable,acquisition=(money(key) for key in ('production_per_unit','shipping_per_unit','other_variable_per_unit','acquisition_per_unit'))
        fee=None if costs['payment_fee_bps'] is None or costs['payment_fee_fixed_per_unit'] is None else _ceil(price*costs['payment_fee_bps'],10000)+money('payment_fee_fixed_per_unit')
        reserve=None if costs['refund_reserve_bps'] is None else _ceil(price*costs['refund_reserve_bps'],10000)
        total=None if unknown else production+shipping+variable+acquisition+fee+reserve
        contribution=None if total is None else price-total
        hours=_hours(offer['owner_hours_per_unit'],'unit hours',nullable=True)
        unit_time=None if hours is None else _ceil(hours*wage,1000000)
        economic_unit=None if contribution is None or unit_time is None else contribution-unit_time
        cash=None if contribution is None or fixed is None else contribution*units-fixed
        total_hours=None if hours is None else fixed_hours+hours*units
        time_value=None if unit_time is None else fixed_time_cost+unit_time*units
        economic=None if cash is None or time_value is None else cash-time_value
        minimum_cash=_minimum(None if fixed is None else fixed+target,contribution)
        minimum_economic=_minimum(None if fixed is None else fixed+fixed_time_cost+target,economic_unit)
        row={'id':offer['id'],'name':offer['name'],'kind':offer['kind'],'units_assumed':units,'unit_price':offer['unit_price'],
             'unit_costs_complete':not unknown,'unknown_unit_cost_fields':unknown,'owner_hours_per_unit':offer['owner_hours_per_unit'],
             'per_unit':{'production':_cash(production),'shipping':_cash(shipping),'other_variable':_cash(variable),'payment_fee':_cash(fee),
                         'refund_reserve':_cash(reserve),'acquisition':_cash(acquisition),'total_variable':_cash(total),
                         'cash_contribution':_cash(contribution),'economic_contribution':_cash(economic_unit)},
             'monthly':{'gross_sales_assumed':format_money(price*units),'variable_cost_assumed':_cash(None if total is None else total*units),
                        'cash_surplus_before_owner_compensation_and_personal_taxes':_cash(cash),
                        'owner_hours':None if total_hours is None else _quantity(total_hours),'owner_time_value':_cash(time_value),
                        'economic_surplus_before_personal_taxes':_cash(economic)},
             'target':{'minimum_units_cash':minimum_cash,'minimum_units_economic':minimum_economic,
                       'cash_target_met_under_assumptions':None if cash is None else cash>=target,
                       'economic_target_met_under_assumptions':None if economic is None else economic>=target,
                       'counts_above_planning_limit':any(item is not None and item>MAX_UNITS for item in (minimum_cash,minimum_economic))},
             'capacity':[],'sources':offer['sources'],
             'source_status':'owner_supplied_references_unverified' if offer['sources'] else 'unsourced_owner_assumptions',
             'limitations':['Every per-unit cost repeats for each assumed order or client billing cycle; acquisition cost must be supplied on that same explicit basis.',
                            'Binomial win rates, trading returns, customer conversion and future sales are not modeled. Unknown values never become free costs.',
                            'Cost-reference links/dates are owner-supplied metadata; no provider price, eligibility, contract or actual expenditure is independently verified.']}
        for weekly in cfg['weekly_hours_profiles']:
            available=weekly*13*1000000
            fit=lambda count:None if count is None or hours is None else (fixed_hours+hours*count)*3<=available
            maximum=None if hours is None or hours==0 else max(0,(available-fixed_hours*3)//(hours*3))
            monthly_hundredths=(weekly*1300+1)//3
            row['capacity'].append({'weekly_hours':str(weekly),'monthly_hours':str(monthly_hundredths//100)+'.'+str(monthly_hundredths%100).zfill(2),
                'maximum_units_under_hours':maximum,'zero_unit_hours_unconstrained':hours==0,
                'assumed_units_fit':fit(units),'cash_target_units_fit':fit(minimum_cash),'economic_target_units_fit':fit(minimum_economic)})
        funding=offer['funding'];mode=funding['mode'];lag=funding['payout_lag_days']
        front=None if variable is None or acquisition is None else variable+acquisition
        if mode=='merchant_fronts':front=None if front is None or production is None or shipping is None else front+production+shipping
        covered=None if production is None or shipping is None or fee is None else price>=production+shipping+fee
        gap_units=None if lag is None else (0 if units==0 else max(1,_ceil(units*lag,30)))
        capital=None if front is None or gap_units is None or mode=='deducted_from_customer_payment' and covered is not True else front*gap_units
        row['funding']={'mode':mode,'payout_lag_days':lag,'front_cost_per_unit':_cash(front),'units_during_payout_gap':gap_units,
            'working_capital_estimate':_cash(capital),'customer_payment_covers_deducted_costs':covered if mode=='deducted_from_customer_payment' else None,
            'scope':'Uniform30-day-flow estimate, not verified peak cash. Customer-funded mode assumes production/shipping deducted from customer payment; other variable costs and acquisition remain seller-fronted. Excludes fixed expenses, payout thresholds/holds, actual refund cash shocks, tax and unmodeled charges. Provider eligibility and payout timing remain unverified.'}
        result['offers'].append(row)
    canonical(result)
    return result
