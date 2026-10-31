"""Generated-only source integrity/quarantine checks; no historical ROI tests."""
from copy import deepcopy
from contextlib import closing
from datetime import date, timedelta
from decimal import Inexact, ROUND_UP, localcontext
import hashlib
import io
import json
import struct
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
import unittest
import zipfile

from ariadne import options_samples as samples
from ariadne.options_data import OptionsDataError, canonical, contract_id, load_dataset
from ariadne.qqq_data import session_dates

NOW = '2026-10-31T13:20:00+00:00'
DAYS = session_dates(samples.START, samples.END)
STOCK_NAMES = ['day_by_symbol/'+key+'_day.csv' for key in ('TSLA','KO','SPY')]+[
    'day_by_symbol/TWTR_day_delisted_2022-10-31.csv']+[
    'minute_by_symbol/'+key+'_minute.csv' for key in ('TSLA','KO','SPY')]+[
    'minute_by_symbol/TWTR_minute_delisted_2022-10-31.csv']


def source_row(day, *, kind='call', strike='60'):
    expiry=(day+timedelta(days=14)).isoformat()
    fields=dict.fromkeys(samples.OPTION_HEADER,'')
    fields.update(contract=contract_id('KO',expiry,kind,strike),underlying='KO',expiration=expiry,
        type=kind,strike=strike,style='A',quote_date=day.isoformat(),bid='0.4',bid_size='2',ask='0.5',
        ask_size='3',volume='1',open_interest='20',underlying_close='60',settlement_time='PM')
    return fields


def csv_bytes(header, rows):
    import csv
    output=io.StringIO(newline='')
    writer=csv.writer(output,lineterminator='\n');writer.writerow(header)
    for row in rows:writer.writerow([row[key] for key in header])
    return output.getvalue().encode()


def zipped(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED)as archive:
        for name,raw in files.items():archive.writestr(name,raw)
    return output.getvalue()


def fixture(*, extra=None, duplicate=False, bad_hash=False, missing_day=False, raw_close='60', unsafe=False, dividend=''):
    files={};records=[]
    for index,day in enumerate(DAYS):
        name=day.isoformat()+'_options.csv'
        rows=[source_row(day),source_row(day,kind='put')]
        if extra and index==20:rows.append(extra(day))
        if duplicate and index==126:rows.append(deepcopy(rows[0]))
        raw=csv_bytes(samples.OPTION_HEADER,rows)
        files['day_by_date/'+name]=raw
        records.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'rows':len(rows)})
    if bad_hash:records[-1]['sha256']='0'*64
    manifest={'product':'options_sample_2022H2','scope':'Generated integrity fixture; no market observations',
        'first_day':samples.START.isoformat(),'last_day':samples.END.isoformat(),'trading_days_expected':127,
        'trading_days_present':127,'trading_days_missing':[],'rows_total':sum(row['rows']for row in records),
        'files':records,'rates':{},'rates_carried_forward':{}}
    files['day_by_date/manifest.json']=canonical(manifest)
    if unsafe:files['../escape.csv']=b'unsafe\n'
    option_raw=zipped(files)
    stock_files={}
    for name in STOCK_NAMES:
        fields=dict.fromkeys(samples.STOCK_HEADER,'')
        rows=[]
        if name=='day_by_symbol/KO_day.csv':
            for day in DAYS[:-1]if missing_day else DAYS:
                row={**fields,'date':day.isoformat(),'open':'60','high':'61','low':'59','close':raw_close,
                     'volume':'100','vwap':'60','transactions':'1','dividend':dividend}
                rows.append(row)
        stock_files[name]=csv_bytes(samples.STOCK_HEADER,rows)
    stock_manifest={'product':'stock_sample_2022H2','files_count':8,
        'bytes_total':sum(len(raw)for raw in stock_files.values()),'first_day':'2022-07-01',
        'last_day':'2022-12-31','trading_days_missing':[],
        'files':[{'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}for name,raw in stock_files.items()]}
    stock_files['manifest.json']=canonical(stock_manifest)
    return option_raw,zipped(stock_files)


def convert(pair,**kwargs):
    options,stocks=pair
    return samples.convert_samples(io.BytesIO(options),io.BytesIO(stocks),
        options_sha256=hashlib.sha256(options).hexdigest(),stock_sha256=hashlib.sha256(stocks).hexdigest(),
        retrieved_at=NOW,as_of=NOW,standard100_assumption=True,**kwargs)


class SampleTests(unittest.TestCase):
    def test_complete_integrity_archive_and_counterfactual_clocks(self):
        result=convert(fixture())
        receipt=result['receipt'];manifest=json.loads(result['manifest'])
        self.assertEqual(receipt['sessions'],127)
        self.assertEqual(receipt['source_rows'],254)
        self.assertEqual(receipt['diagnostics']['accepted_quotes'],254)
        self.assertTrue(receipt['all_original_CSV_CRC_SHA_bytes_verified'])
        self.assertFalse(receipt['standard100_terms_independently_verified'])
        self.assertFalse(receipt['execution_qualified'])
        self.assertTrue(receipt['provider_zero_size_to_blank_preprocessing'])
        self.assertEqual(len(result['chunks']),6)
        days=[]
        for chunk in result['chunks']:
            data=load_dataset(chunk['raw'],NOW)
            self.assertEqual(data['source']['quote_time_basis'],'assumed_eod')
            self.assertFalse(data['source']['independently_verified'])
            for row in data['snapshots']:
                days.append(row['observed_at'][:10])
                from datetime import datetime
                self.assertEqual(datetime.fromisoformat(row['available_at'])-datetime.fromisoformat(row['observed_at']),timedelta(minutes=15))
                self.assertEqual(row['underlying_price'],'60')
        self.assertEqual(days,[day.isoformat()for day in DAYS])
        self.assertEqual(manifest['selection']['max_dte'],35)
        self.assertEqual(result['reject_ledger'],b'')

    def test_exact_quarantine_fields_and_zero_liquidity_retention(self):
        variants=[('missing_bid_or_ask',{'bid':''}),('crossed_bid_ask',{'bid':'0.6'}),
                  ('bid_above_economic_upper_bound',{'bid':'61','ask':'62'}),
                  ('nonrepresentable_price_size_or_underlying',{'bid':'NaN'}),
                  ('unsupported_style_or_settlement',{'style':'E'}),
                  ('unexpected_quote_time_in_date_only_scope',{'quote_time':'2022-08-01T20:00:00Z'})]
        for expected,delta in variants:
            def extra(day):return {**source_row(day,strike='61'),**delta}
            with self.subTest(expected=expected):
                result=convert(fixture(extra=extra))
                records=[json.loads(line)for line in result['reject_ledger'].splitlines()]
                self.assertEqual(len(records),1)
                self.assertEqual(records[0]['reason'],expected)
                self.assertEqual(records[0]['row_number'],3)
                self.assertEqual(set(records[0]['original_fields']),set(samples.OPTION_HEADER))
                self.assertEqual(result['receipt']['diagnostics']['accepted_quotes'],254)
        def zeros(day):return {**source_row(day,strike='61'),'bid':'0','ask':'0','bid_size':'0','ask_size':''}
        result=convert(fixture(extra=zeros))
        self.assertEqual(result['reject_ledger'],b'')
        self.assertEqual(result['receipt']['diagnostics']['zero_bid_quotes'],1)
        self.assertEqual(result['receipt']['diagnostics']['zero_bid_size'],1)
        self.assertEqual(result['receipt']['diagnostics']['missing_ask_size'],1)

    def test_spot_disagreement_retained_with_exact_issue_not_silent_replacement(self):
        def extra(day):return {**source_row(day,strike='61'),'underlying_close':'59.99'}
        result=convert(fixture(extra=extra))
        issue=json.loads(result['reject_ledger'].splitlines()[0])
        self.assertEqual(issue['disposition'],'retained_with_issue')
        self.assertEqual(issue['original_fields']['underlying_close'],'59.99')
        self.assertEqual(result['receipt']['diagnostics']['accepted_quotes'],255)

    def test_fixed_dte_exclusion_and_adjusted_roots_have_explicit_counts(self):
        def far(day):
            expiry=(day+timedelta(days=36)).isoformat()
            return {**source_row(day,strike='61'),'expiration':expiry,'contract':contract_id('KO',expiry,'call','61')}
        result=convert(fixture(extra=far))
        self.assertEqual(result['receipt']['diagnostics']['outside_fixed_0_35_DTE'],1)
        self.assertEqual(result['receipt']['diagnostics']['accepted_quotes'],254)
        def adjusted(day):
            row=source_row(day,strike='61');row['contract']=row['contract'].replace('KO','KO1',1);return row
        result=convert(fixture(extra=adjusted))
        self.assertEqual(json.loads(result['reject_ledger'].splitlines()[0])['reason'],'unsupported_or_adjusted_contract_root')

    def test_late_hash_failure_invalidates_every_chunk(self):
        with self.assertRaisesRegex(OptionsDataError,'hash mismatch'):convert(fixture(bad_hash=True))

    def test_duplicate_ids_are_substantive_stop(self):
        with self.assertRaisesRegex(OptionsDataError,'Duplicate selected source ID'):convert(fixture(duplicate=True))

    def test_every_stock_day_and_under100_gate_required(self):
        with self.assertRaisesRegex(OptionsDataError,'every selected session'):convert(fixture(missing_day=True))
        with self.assertRaisesRegex(OptionsDataError,'below100'):convert(fixture(raw_close='100'))

    def test_unsafe_zip_and_bad_source_hash_stop(self):
        with self.assertRaisesRegex(OptionsDataError,'Unsafe'):convert(fixture(unsafe=True))
        options,stocks=fixture()
        with self.assertRaisesRegex(OptionsDataError,'ZIP hash differs'):
            samples.convert_samples(io.BytesIO(options),io.BytesIO(stocks),options_sha256='0'*64,
                stock_sha256=hashlib.sha256(stocks).hexdigest(),retrieved_at=NOW,as_of=NOW,standard100_assumption=True)

    def test_limits_and_checkpoint_abort_without_partial_result(self):
        pair=fixture()
        with patch.object(samples,'MAX_REJECTIONS',1):
            with self.assertRaisesRegex(OptionsDataError,'ledger exceeds'):
                convert(fixture(extra=lambda day:{**source_row(day,strike='61'),'bid':''}))
        with patch.object(samples,'MAX_CANONICAL',1):
            with self.assertRaisesRegex(OptionsDataError,'aggregate bounds'):convert(pair)
        def stop():raise ValueError('owner paused')
        with self.assertRaisesRegex(ValueError,'owner paused'):convert(pair,checkpoint=stop)

    def test_caller_decimal_context_does_not_change_prices_or_classification(self):
        pair=fixture(extra=lambda day:{**source_row(day,strike='61.125'),'bid':'0.12345678','ask':'0.15'})
        expected=convert(pair)
        with localcontext()as context:
            context.prec=4;context.rounding=ROUND_UP;context.traps[Inexact]=True
            self.assertEqual(convert(pair),expected)
            self.assertEqual(context.prec,4)
            self.assertTrue(context.traps[Inexact])

    def test_directory_preflight_precedes_zipinfo_allocation(self):
        options,stocks=fixture()
        modified=bytearray(options)
        end=modified.rfind(b'PK\x05\x06')
        struct.pack_into('<HH',modified,end+8,513,513)
        with patch.object(samples.zipfile,'ZipFile',side_effect=AssertionError('Must not allocate ZipInfo'))as constructor:
            with self.assertRaisesRegex(OptionsDataError,'directory count'):convert((bytes(modified),stocks))
            constructor.assert_not_called()

    def test_nul_truncated_name_rejected_even_without_extraction(self):
        from types import SimpleNamespace
        item=zipfile.ZipInfo('safe\x00alias.csv')
        archive=SimpleNamespace(infolist=lambda:[item])
        with self.assertRaisesRegex(OptionsDataError,'Unsafe'):samples._inventory(archive)

    def test_zero_formatted_dividends_are_not_events(self):
        result=convert(fixture(dividend='0.00'))
        self.assertEqual(result['receipt']['stock_events'],[])


class SampleCLITests(unittest.TestCase):
    def setUp(self):
        from scripts import options_sample
        self.cli=options_sample
        staging=Path(__file__).resolve().parents[1]/'staging'
        self.temporary=tempfile.TemporaryDirectory(prefix='sample-cli-',dir=staging)
        self.root=Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(staging.resolve()))
        (self.root/'workspace').mkdir()
        self.options,self.stocks=fixture()
        (self.root/'options.zip').write_bytes(self.options)
        (self.root/'stocks.zip').write_bytes(self.stocks)
        self.args=self.cli.parser().parse_args(['--options','options.zip','--stocks','stocks.zip',
            '--options-sha256',hashlib.sha256(self.options).hexdigest(),
            '--stocks-sha256',hashlib.sha256(self.stocks).hexdigest(),
            '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace',
            '--output','archives/KO','--detached-research'])
    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(Path(__file__).resolve().parents[1]/'staging'))
        self.temporary.cleanup()
    def owner(self,paused):
        data=self.root/'data';data.mkdir()
        with closing(sqlite3.connect(data/'ariadne.sqlite3'))as db:
            db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO settings(key,value)VALUES(?,?)',('paused',json.dumps(paused)))
            db.commit()

    def test_exclusive_completed_publication_with_all_artifacts_bound(self):
        completed=self.cli.run(self.args,root=self.root,now=NOW)
        output=self.root/'workspace/archives/KO'
        self.assertTrue(completed['all_sources_and_outputs_reverified'])
        self.assertFalse(completed['owner_authority']['owner_database_opened'])
        for name,item in completed['artifacts'].items():
            raw=(output/name).read_bytes()
            self.assertEqual(len(raw),item['bytes'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
        manifest=json.loads((output/'catalog.json').read_bytes())
        self.assertTrue(all(item['path'].startswith('archives/KO/')for item in manifest['chunks']))
        previous=(output/'completed.json').read_bytes()
        with self.assertRaises(FileExistsError):self.cli.run(self.args,root=self.root,now=NOW)
        self.assertEqual((output/'completed.json').read_bytes(),previous)

    def test_late_integrity_failure_preserves_failed_folder_without_catalog(self):
        options,stocks=fixture(bad_hash=True)
        (self.root/'options.zip').write_bytes(options)
        self.args.options_sha256=hashlib.sha256(options).hexdigest()
        with self.assertRaisesRegex(OptionsDataError,'hash mismatch'):self.cli.run(self.args,root=self.root,now=NOW)
        output=self.root/'workspace/archives/KO'
        failure=json.loads((output/'failed.json').read_bytes())
        self.assertFalse(failure['replay_allowed'])
        self.assertFalse(failure['adopted'])
        self.assertTrue((output/'intent.json').exists())
        self.assertFalse((output/'catalog.json').exists())
        with self.assertRaises(FileExistsError):self.cli.run(self.args,root=self.root,now=NOW)

    def test_default_pause_check_refuses_global_pause_without_owner_changes(self):
        self.owner(True);self.args.detached_research=False
        with self.assertRaisesRegex(ValueError,'paused'):self.cli.run(self.args,root=self.root,now=NOW)
        with closing(sqlite3.connect(self.root/'data/ariadne.sqlite3'))as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0],'true')
        failure=json.loads((self.root/'workspace/archives/KO/failed.json').read_bytes())
        self.assertFalse(failure['owner_settings_written'])
        self.assertEqual(failure['paper_operations'],0)

    def test_detached_mode_cannot_bypass_present_owner_database(self):
        self.owner(True)
        with self.assertRaisesRegex(ValueError,'isolated checkout'):self.cli.run(self.args,root=self.root,now=NOW)
        self.assertFalse((self.root/'workspace/archives/KO').exists())
        self.args.owner_data='unused'
        with self.assertRaisesRegex(ValueError,'isolated checkout'):self.cli.run(self.args,root=self.root,now=NOW)

    def test_assumption_and_live_source_changes_block_publication(self):
        self.args.assume_standard_100=False
        with self.assertRaisesRegex(ValueError,'assume-standard'):self.cli.run(self.args,root=self.root,now=NOW)
        self.assertFalse((self.root/'workspace/archives/KO').exists())
        self.args.assume_standard_100=True
        baseline=self.cli._sources()
        with patch.object(self.cli,'_sources',side_effect=[baseline,{**baseline,'tampered':'changed'}]):
            with self.assertRaisesRegex(ValueError,'source changed'):self.cli.run(self.args,root=self.root,now=NOW)
        self.assertFalse((self.root/'workspace/archives/KO/catalog.json').exists())

    def test_main_installs_full_offline_guards_before_parser_and_converter(self):
        from scripts import options_campaign
        events=[]
        with patch.object(options_campaign,'install_offline_guards',side_effect=lambda:events.append('guards'))as guards, \
             patch.object(self.cli,'parser')as factory, \
             patch.object(self.cli,'run',side_effect=lambda args:events.append('run')or{'status':'completed'})as run, \
             patch('builtins.print'):
            factory.return_value.parse_args.side_effect=lambda argv:events.append('parse')or self.args
            self.assertEqual(self.cli.main(['generated-only']),0)
            self.assertEqual(events,['guards','parse','run'])
            guards.assert_called_once_with()
            run.assert_called_once_with(self.args)
        with patch.object(options_campaign,'install_offline_guards',side_effect=PermissionError('guard failed')), \
             patch.object(self.cli,'parser')as factory,patch.object(self.cli,'run')as run:
            with self.assertRaisesRegex(PermissionError,'guard failed'):self.cli.main([])
            factory.assert_not_called()
            run.assert_not_called()


if __name__=='__main__':unittest.main()
