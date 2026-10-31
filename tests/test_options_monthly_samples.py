"""Generated monthly integrity/coverage fixtures; never historical return claims."""
from copy import deepcopy
from datetime import timedelta
from decimal import Inexact, ROUND_UP, localcontext
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from unittest.mock import patch
import unittest
import zipfile

from ariadne import options_monthly_samples as samples
from ariadne.options_data import OptionsDataError, contract_id, load_dataset
from ariadne.qqq_data import session_dates, session_close_utc

NOW = '2026-10-31T15:11:10+00:00'
DAYS = session_dates(samples.START, samples.END)


def daytext(day):
    return f'{day.month}/{day.day}/{day.year}'


def option(day, symbol='AAA', *, strike='50', **values):
    expiry = day+timedelta(days=14)
    identity = contract_id(symbol, expiry.isoformat(), 'call', strike, research_symbols=(symbol,))
    fields = dict.fromkeys(samples.OPTION_HEADER, '')
    fields.update(UnderlyingSymbol=symbol, UnderlyingPrice='50', Exchange='*', OptionSymbol=identity,
        Type='call', Expiration=daytext(expiry), DataDate=daytext(day), Strike=strike,
        Last='0', Bid='0.4', Ask='0.5', Volume='1', OpenInterest='2', AKA=identity)
    fields.update(values)
    return fields


def csv_bytes(header, rows):
    text = io.StringIO(newline='')
    writer = csv.writer(text, lineterminator='\n')
    writer.writerow(header)
    for row in rows:
        writer.writerow([row[key] for key in header])
    return text.getvalue().encode()


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return output.getvalue()


def fixture(*, extra=None, missing_quote=False, missing_stock=False, duplicate=False,
            above100=False, all_above100=False, malformed_close=False, bad_date=False, unsafe=False):
    files = {}
    for i, day in enumerate(DAYS):
        stock = {'symbol':'AAA', 'quotedate':daytext(day), 'open':'50', 'high':'51',
            'low':'49', 'close':'50', 'volume':'100', 'adjustedclose':'50'}
        if above100 and i==21:stock['close']='105'
        if all_above100:stock['close']='105'
        if malformed_close and i==21:stock['close']='NaN'
        stockrows = [] if missing_stock and i==21 else [stock]
        files[f'L2_stockquotes_{day:%Y%m%d}.csv'] = csv_bytes(samples.STOCK_HEADER, stockrows)
        rows = [] if missing_quote and i==21 else [option(day)]
        if extra and i==10:rows.append(extra(day))
        if duplicate and i==21:rows.append(deepcopy(rows[0]))
        if bad_date and i==21:rows[0]['DataDate']=daytext(DAYS[20])
        files[f'L2_options_{day:%Y%m%d}.csv'] = csv_bytes(samples.OPTION_HEADER, rows)
        fields = dict.fromkeys(samples.STATS_HEADER,'0')
        fields.update(symbol='AAA',quotedate=daytext(day),comment='')
        files[f'L2_optionstats_{day:%Y%m%d}.csv'] = csv_bytes(samples.STATS_HEADER,[fields])
    if unsafe:files['../outside.csv']=b'header\n'
    return zipped(files)


def convert(raw, **kwargs):
    return samples.convert_monthly(io.BytesIO(raw), zip_sha256=hashlib.sha256(raw).hexdigest(),
        retrieved_at=NOW, as_of=NOW, standard100_assumption=True, **kwargs)


def flattened(result,symbol='AAA'):
    catalog=json.loads(result['files'][f'symbols/ticker-{symbol}/catalog.json'])
    values=[]
    for chunk in catalog['chunks']:
        path=chunk['path'].removeprefix('market-data/options/archives/monthly2019aug/')
        values.extend(load_dataset(result['files'][path],NOW,research_symbols=(symbol,))['snapshots'])
    return values


def universe_fixture(symbols):
    files={}
    for day in DAYS:
        stockrows=[];optionrows=[];statrows=[]
        for symbol in symbols:
            stockrows.append({'symbol':symbol,'quotedate':daytext(day),'open':'50','high':'51',
                'low':'49','close':'50','volume':'100','adjustedclose':'50'})
            row=option(day)
            row['UnderlyingSymbol']=symbol
            row['OptionSymbol']=symbol+row['OptionSymbol'][3:]
            row['AKA']=row['OptionSymbol']
            optionrows.append(row)
            stat=dict.fromkeys(samples.STATS_HEADER,'0')
            stat.update(symbol=symbol,quotedate=daytext(day),comment='');statrows.append(stat)
        for family,header,rows in (('stockquotes',samples.STOCK_HEADER,stockrows),
                                  ('options',samples.OPTION_HEADER,optionrows),
                                  ('optionstats',samples.STATS_HEADER,statrows)):
            files[f'L2_{family}_{day:%Y%m%d}.csv']=csv_bytes(header,rows)
    return zipped(files)


class MonthlyTests(unittest.TestCase):
    def test_complete_all22_unknown_size_reference_and_all66_integrity(self):
        result=convert(fixture());index=json.loads(result['index']);receipt=result['receipt']
        self.assertEqual(index['qualified_symbols'],['AAA'])
        self.assertEqual(receipt['source_option_rows'],22)
        self.assertEqual(receipt['qualified_symbols'],1)
        self.assertTrue(receipt['all66_CSV_CRC_SHA_verified'])
        proof=json.loads(result['files']['source-files.json'])
        self.assertEqual(len(proof['CSV_files']),66)
        self.assertTrue(all(item['ZIP_CRC_verified'] for item in proof['CSV_files'].values()))
        catalog=json.loads(result['files']['symbols/ticker-AAA/catalog.json'])
        self.assertEqual(len(catalog['chunks']),1)
        self.assertEqual(sum(item['snapshots']for item in catalog['chunks']),22)
        for day,snapshot in zip(DAYS,flattened(result)):
            self.assertEqual(snapshot['observed_at'][:10],day.isoformat())
            self.assertEqual(snapshot['underlying_price'],'50')
            self.assertIsNone(snapshot['quotes'][0]['bid_size'])
            self.assertIsNone(snapshot['quotes'][0]['ask_size'])
        self.assertFalse(index['execution_qualified'])
        self.assertFalse(receipt['standard100_verified'])
        self.assertFalse(receipt['security_types_verified'])
        self.assertEqual(result['files']['reject-issues.jsonl'],b'')

    def test_point_in_time_price_scope_keeps_above100_exits_and_static_bound(self):
        result=convert(fixture(above100=True));index=json.loads(result['index'])
        self.assertEqual(index['qualified_symbols'],['AAA'])
        self.assertEqual(flattened(result)[-1]['underlying_price'],'105')
        ledger=json.loads(result['files']['universe-ledger.json'])['all_symbols'][0]
        self.assertEqual(ledger['retention_strike_interval'],['47.5','110.25'])
        self.assertEqual(ledger['below100_sessions'],21)
        self.assertIn('option_spot_differs_from_raw_stock_reference',result['files']['reject-issues.jsonl'].decode())
        self.assertEqual(convert(fixture(all_above100=True))['receipt']['qualified_symbols'],0)

    def test_missing_quote_day_excludes_whole_symbol_but_preserves_tentative_artifacts(self):
        result=convert(fixture(missing_quote=True));index=json.loads(result['index'])
        self.assertEqual(index['archives'],[])
        ledger=json.loads(result['files']['universe-ledger.json'])['all_symbols'][0]
        self.assertFalse(ledger['qualified'])
        self.assertFalse(ledger['tentative_artifacts_adopted'])
        self.assertIn('missing_qualified_quote_session',ledger['exclusion_reasons'])
        self.assertEqual(len(ledger['tentative_chunks']),0)
        self.assertEqual(ledger['buffered_snapshot_count'],21)
        self.assertFalse(ledger['encoded_snapshot_buffers_adopted'])
        self.assertTrue(ledger['excluded_records_retained_in_original_source_ZIP'])
        self.assertFalse(any(name.startswith('symbols/ticker-AAA/')for name in result['files']))
        self.assertNotIn('symbols/ticker-AAA/catalog.json',result['files'])

    def test_missing_invalid_stock_day_excludes_without_repair(self):
        for kwargs in ({'missing_stock':True},{'malformed_close':True}):
            with self.subTest(kwargs=kwargs):
                result=convert(fixture(**kwargs))
                self.assertEqual(result['receipt']['qualified_symbols'],0)
                ledger=json.loads(result['files']['universe-ledger.json'])['all_symbols'][0]
                self.assertIn('missing_or_invalid_raw_stock_calendar',ledger['exclusion_reasons'])

    def test_invalid_selected_rows_keep_original_fields_and_never_invent_prices(self):
        variants=[('missing_bid_or_ask',{'Bid':''}),('crossed_bid_ask',{'Bid':'0.6'}),
            ('bid_above_reference_economic_upper',{'Bid':'51','Ask':'52'}),
            ('invalid_price_volume_interest_or_option_spot',{'Ask':'NaN'}),
            ('invalid_price_volume_interest_or_option_spot',{'Volume':'1.1'}),
            ('unsupported_adjusted_root_or_alias',{'OptionExt':'1'})]
        for reason,delta in variants:
            with self.subTest(reason=reason,delta=delta):
                result=convert(fixture(extra=lambda day:option(day,strike='51',**delta)))
                rows=[json.loads(line)for line in result['files']['reject-issues.jsonl'].splitlines()]
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['reason'],reason)
                for key,value in delta.items():self.assertEqual(rows[0]['original_fields'][key],value)
                self.assertEqual(result['receipt']['qualified_symbols'],1)

    def test_spot_disagreement_retained_explicitly_and_zero_prices_preserved(self):
        result=convert(fixture(extra=lambda day:option(day,strike='51',UnderlyingPrice='50.00000001',Bid='0',Ask='0')))
        issue=json.loads(result['files']['reject-issues.jsonl'])
        self.assertEqual(issue['disposition'],'retained_with_issue')
        self.assertEqual(issue['original_fields']['UnderlyingPrice'],'50.00000001')
        quote=flattened(result)[10]['quotes'][1]
        self.assertEqual((quote['bid'],quote['ask']),('0','0'))
        self.assertIsNone(quote['bid_size'])

    def test_fixed_DTE_and_strike_retention_are_counted_not_inferred(self):
        for strike,expiry in (('70',None),('51',DAYS[10]+timedelta(days=36))):
            def extra(day):
                value=option(day,strike=strike)
                if expiry:
                    value['Expiration']=daytext(expiry)
                    value['OptionSymbol']=contract_id('AAA',expiry.isoformat(),'call',strike,research_symbols=('AAA',))
                    value['AKA']=value['OptionSymbol']
                return value
            result=convert(fixture(extra=extra))
            row=json.loads(result['files']['universe-ledger.json'])['all_symbols'][0]['daily'][10]
            reason='outside_fixed_0_35_DTE' if expiry else 'outside_full_window_strike_interval'
            self.assertEqual(row[reason],1)
            self.assertEqual(result['files']['reject-issues.jsonl'],b'')

    def test_duplicate_and_any_file_date_contradiction_fail_whole(self):
        for raw in (fixture(duplicate=True),fixture(bad_date=True),fixture(unsafe=True)):
            with self.subTest(size=len(raw)),self.assertRaises(OptionsDataError):convert(raw)

    def test_source_hash_CRC_and_directory_bounds_before_ZipInfo(self):
        raw=fixture()
        with self.assertRaises(OptionsDataError):
            samples.convert_monthly(io.BytesIO(raw),zip_sha256='0'*64,retrieved_at=NOW,as_of=NOW,standard100_assumption=True)
        corrupted=bytearray(raw);start=corrupted.find(b'UnderlyingSymbol')
        corrupted[start+5]^=1
        with self.assertRaises(OptionsDataError):convert(bytes(corrupted))
        oversized=bytearray(raw);at=oversized.rfind(b'PK\x05\x06')
        struct.pack_into('<HH',oversized,at+8,257,257)
        with patch('ariadne.options_monthly_samples.zipfile.ZipFile',side_effect=AssertionError('directory allocated')):
            with self.assertRaises(OptionsDataError):convert(bytes(oversized))

    def test_hard_output_reject_caps_fail_without_truncation(self):
        with patch.object(samples,'MAX_OUTPUT',100):
            with self.assertRaises(OptionsDataError):convert(fixture())
        with patch.object(samples,'MAX_REJECTIONS',10):
            with self.assertRaises(OptionsDataError):convert(fixture(extra=lambda day:option(day,strike='51',Bid='')))
        with patch.object(samples,'MAX_ARCHIVE',100):
            with self.assertRaises(OptionsDataError):convert(fixture())

    def test_context_independence_streaming_sink_and_checkpoint(self):
        raw=fixture();baseline=convert(raw);emitted={}
        result=convert(raw,emit=lambda name,value:emitted.__setitem__(name,value))
        self.assertEqual(result['files'],{})
        self.assertEqual(emitted,baseline['files'])
        with localcontext()as context:
            context.prec=2;context.rounding=ROUND_UP;context.traps[Inexact]=True
            self.assertEqual(convert(raw),baseline)
        class Stop(Exception):pass
        with self.assertRaises(Stop):convert(raw,checkpoint=lambda:(_ for _ in()).throw(Stop()))
        with self.assertRaises(OptionsDataError):convert(raw,output_prefix='../outside')
        with self.assertRaises(OptionsDataError):
            samples.convert_monthly(io.BytesIO(raw),zip_sha256=hashlib.sha256(raw).hexdigest(),retrieved_at=NOW,as_of=NOW)

    def test_packed_flattening_matches_independent_all22_source_snapshots(self):
        result=convert(fixture());expected=[]
        for day in DAYS:
            expiry=day+timedelta(days=14);observed=session_close_utc(day)
            expected.append({'observed_at':observed.isoformat(),'available_at':(observed+timedelta(minutes=15)).isoformat(),
                'underlying_symbol':'AAA','underlying_price':'50','quotes':[{
                    'contract_id':'AAA'+expiry.strftime('%y%m%d')+'C00050000','type':'call','expiry':expiry.isoformat(),
                    'strike':'50','multiplier':100,'bid':'0.4','ask':'0.5','bid_size':None,'ask_size':None,
                    'volume':1,'open_interest':2}]})
        self.assertEqual(flattened(result),expected)
        self.assertEqual(result['receipt']['encoded_snapshot_byte_limit'],512*1024**2)
        self.assertEqual(result['receipt']['encoded_snapshot_bytes'],sum(len(samples.canonical(row))for row in expected))

    def test_contiguous_quote_and_byte_bound_splits_preserve_all_snapshot_fields(self):
        raw=fixture();baseline=convert(raw);expected=flattened(baseline)
        with patch.object(samples,'MAX_QUOTES',5):
            result=convert(raw)
        catalog=json.loads(result['files']['symbols/ticker-AAA/catalog.json'])
        self.assertEqual([row['snapshots']for row in catalog['chunks']],[5,5,5,5,2])
        self.assertEqual(flattened(result),expected)
        chunk=next(value for name,value in baseline['files'].items()if '/chunk-'in name)
        limit=len(chunk)//3
        with patch.object(samples,'MAX_DATA',limit):result=convert(raw)
        catalog=json.loads(result['files']['symbols/ticker-AAA/catalog.json'])
        self.assertGreater(len(catalog['chunks']),1)
        self.assertTrue(all(item['bytes']<=limit for item in catalog['chunks']))
        self.assertEqual(flattened(result),expected)

    def test_encoded_buffer_cap_and_late_source_failure_emit_no_partial_quotes(self):
        emitted=[]
        with patch.object(samples,'MAX_ENCODED_SNAPSHOTS',100):
            with self.assertRaisesRegex(OptionsDataError,'encoded snapshots'):convert(fixture(),emit=lambda name,raw:emitted.append(name))
        self.assertEqual(emitted,[])
        raw=fixture()
        with zipfile.ZipFile(io.BytesIO(raw))as source:
            files={item.filename:source.read(item)for item in source.infolist()}
        name=f'L2_optionstats_{DAYS[-1]:%Y%m%d}.csv'
        files[name]=files[name].replace(b'symbol,quotedate',b'symbol,bad_date',1)
        with self.assertRaises(OptionsDataError):convert(zipped(files),emit=lambda name,raw:emitted.append(name))
        self.assertEqual(emitted,[])

    def test_all_source_roots_known_indices_and_sorted64_batches(self):
        symbols=['A'+chr(65+i//26)+chr(65+i%26) for i in range(65)]
        result=convert(universe_fixture(list(reversed(symbols))+['PRN','VIX','BAD1']))
        index=json.loads(result['index'])
        self.assertEqual(index['qualified_symbols'],sorted(symbols+['PRN']))
        self.assertEqual([len(x['symbols']) for x in index['batches']],[64,2])
        self.assertIn('symbols/ticker-PRN/catalog.json',result['files'])
        records={row['symbol']:row for row in json.loads(result['files']['universe-ledger.json'])['all_symbols']}
        self.assertIn('known_index',records['VIX']['exclusion_reasons'])
        self.assertIn('unsupported_ASCII_root',records['BAD1']['exclusion_reasons'])
        self.assertFalse(records['VIX']['qualified'])
        self.assertEqual(len(records),68)

    def test_date_cache_is_local_exact_and_all_source_rows_still_checked(self):
        raw=fixture();first=convert(raw)
        self.assertLessEqual(first['receipt']['validated_distinct_date_literals'],44)
        self.assertEqual(first,convert(raw))
        with self.assertRaises(OptionsDataError):convert(fixture(bad_date=True))

    def test_mutated_header_and_missing_source_member_fail_whole(self):
        raw=fixture()
        with zipfile.ZipFile(io.BytesIO(raw))as source:
            files={item.filename:source.read(item)for item in source.infolist()}
        name=f'L2_optionstats_{DAYS[0]:%Y%m%d}.csv'
        original=files.pop(name)
        with self.assertRaises(OptionsDataError):convert(zipped(files))
        files[name]=original.replace(b'symbol,quotedate',b'symbol,bad_date',1)
        with self.assertRaises(OptionsDataError):convert(zipped(files))


class MonthlyCLITests(unittest.TestCase):
    def test_exact_artifact_read_bounds_in_both_full_verification_passes(self):
        from scripts import options_monthly_sample as cli
        from scripts import options_market as market
        actual_read=market.read;calls=[]
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'workspace').mkdir();raw=fixture();(root/'input.zip').write_bytes(raw)
            args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256',hashlib.sha256(raw).hexdigest(),
                '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace','--output','exact','--detached-research'])
            def observed_read(guard,name,limit):
                if name.startswith('exact/'):calls.append((name[len('exact/'):],limit))
                return actual_read(guard,name,limit)
            with (patch.object(cli,'_sources',return_value={'generated':'1'*64}),patch.object(market,'read',side_effect=observed_read)):
                receipt=cli.run(args,root=root,now=NOW)
            for name,item in receipt['artifacts'].items():
                self.assertEqual([limit for seen,limit in calls if seen==name],[item['bytes'],item['bytes']])
            self.assertEqual(len(calls),2*len(receipt['artifacts']))
            self.assertIn(('reject-issues.jsonl',0),calls)

    def test_late_commit_growth_truncation_and_same_size_restored_mtime_are_rejected(self):
        from scripts import options_monthly_sample as cli
        from scripts import options_market as market
        from ariadne import atomic
        actual_read,actual_publish=market.read,atomic.publish_bytes
        for mode in('growth','truncate','same_size_restored_mtime'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory()as folder:
                root=Path(folder);(root/'workspace').mkdir();raw=fixture();(root/'input.zip').write_bytes(raw)
                args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256',hashlib.sha256(raw).hexdigest(),
                    '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace','--output','late','--detached-research'])
                victim=root/'workspace/late/symbols/ticker-AAA/chunk-001.json';reads=[];mutated=[]
                def observed_read(guard,name,limit):
                    if name=='late/symbols/ticker-AAA/chunk-001.json':reads.append(limit)
                    return actual_read(guard,name,limit)
                def late_publish(destination,payload,*,checkpoint=None):
                    if Path(destination).name=='completed.json':
                        self.assertEqual(len(reads),1,'First full output pass must already have occurred.')
                        before=victim.stat();original=victim.read_bytes()
                        changed=(original+b'X' if mode=='growth' else original[:-1] if mode=='truncate' else
                            original.replace(b'"bid":"0.4"',b'"bid":"0.5"',1))
                        self.assertNotEqual(changed,original)
                        victim.write_bytes(changed)
                        os.utime(victim,ns=(before.st_atime_ns,before.st_mtime_ns))
                        if mode=='same_size_restored_mtime':
                            self.assertEqual(victim.stat().st_size,before.st_size)
                            self.assertEqual(victim.stat().st_mtime_ns,before.st_mtime_ns)
                        mutated.append(True)
                    return actual_publish(destination,payload,checkpoint=checkpoint)
                with (patch.object(cli,'_sources',return_value={'generated':'1'*64}),
                      patch.object(market,'read',side_effect=observed_read),patch.object(atomic,'publish_bytes',side_effect=late_publish),
                      self.assertRaises(ValueError)):
                    cli.run(args,root=root,now=NOW)
                self.assertEqual(mutated,[True]);self.assertEqual(len(reads),2)
                self.assertFalse((root/'workspace/late/completed.json').exists())
                failure=json.loads((root/'workspace/late/failed.json').read_bytes())
                self.assertFalse(failure['adopted']);self.assertFalse(failure['replay_allowed'])

    def test_stdout_stays_compact_with_large_completed_manifest(self):
        from scripts import options_monthly_sample as cli
        from contextlib import redirect_stdout
        receipt={'status':'completed','universe_index':{'path':'monthly/universe.json','sha256':'a'*64},
            'artifacts':{f'file-{i}':{'bytes':10,'sha256':'1'*64}for i in range(10000)},
            'source_ZIP_sha256':'b'*64,'output_bytes_before_receipt':100000}
        stream=io.StringIO()
        with (patch('scripts.options_campaign.install_offline_guards'),patch.object(cli,'run',return_value=receipt),redirect_stdout(stream)):
            self.assertEqual(cli.main(['--zip','source.zip','--zip-sha256','b'*64,'--retrieved-at',NOW,'--output','monthly']),0)
        raw=stream.getvalue();value=json.loads(raw)
        self.assertLess(len(raw),1024)
        self.assertEqual(value['artifact_count'],10000)
        self.assertNotIn('artifacts',value)

    def test_full_offline_guard_installed_before_parser_or_run(self):
        from scripts import options_monthly_sample as cli
        events=[]
        with (patch('scripts.options_campaign.install_offline_guards',side_effect=lambda:events.append('guard')),
              patch.object(cli,'parser',side_effect=lambda:events.append('parser')or self.fail('stop'))):
            with self.assertRaises(AssertionError):cli.main([])
        self.assertEqual(events,['guard','parser'])

    def test_exclusive_fresh_publication_and_receipt_rehash(self):
        from scripts import options_monthly_sample as cli
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'input.zip').write_bytes(fixture())
            (root/'workspace').mkdir()
            args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256',hashlib.sha256((root/'input.zip').read_bytes()).hexdigest(),
                '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace','--output','monthly','--detached-research'])
            with patch.object(cli,'_sources',return_value={'generated':'1'*64}):result=cli.run(args,root=root,now=NOW)
            dest=root/'workspace/monthly'
            self.assertTrue((dest/'completed.json').exists())
            self.assertFalse((dest/'failed.json').exists())
            self.assertTrue(result['all_source_and_output_bytes_reverified'])
            for name,identity in result['artifacts'].items():
                raw=(dest/name).read_bytes()
                self.assertEqual(len(raw),identity['bytes'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(),identity['sha256'])
            with patch.object(cli,'_sources',return_value={'generated':'1'*64}),self.assertRaises(FileExistsError):cli.run(args,root=root,now=NOW)

    def test_failure_receipt_and_source_mutation_prevent_completed(self):
        from scripts import options_monthly_sample as cli
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'input.zip').write_bytes(fixture())
            (root/'workspace').mkdir()
            args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256','0'*64,'--retrieved-at',NOW,
                '--assume-standard-100','--workspace','workspace','--output','failed','--detached-research'])
            with patch.object(cli,'_sources',return_value={'generated':'1'*64}),self.assertRaises(OptionsDataError):cli.run(args,root=root,now=NOW)
            dest=root/'workspace/failed';failure=json.loads((dest/'failed.json').read_bytes())
            self.assertFalse(failure['replay_allowed'])
            self.assertFalse((dest/'completed.json').exists())

    def test_dependency_drift_and_output_tamper_prevent_completed(self):
        from scripts import options_monthly_sample as cli
        for mode in ('dependencies','output'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory()as folder:
                root=Path(folder);(root/'workspace').mkdir();raw=fixture();(root/'input.zip').write_bytes(raw)
                args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256',hashlib.sha256(raw).hexdigest(),
                    '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace','--output','failed','--detached-research'])
                count=[0]
                def source_hashes(root_arg):
                    count[0]+=1
                    if count[0]>1 and mode=='dependencies':return {'changed':'2'*64}
                    if count[0]>1 and mode=='output':
                        path=root/'workspace/failed/universe.json'
                        if path.exists():path.write_bytes(b'{}')
                    return {'generated':'1'*64}
                with patch.object(cli,'_sources',side_effect=source_hashes),self.assertRaises(ValueError):
                    cli.run(args,root=root,now=NOW)
                self.assertTrue((root/'workspace/failed/failed.json').exists())
                self.assertFalse((root/'workspace/failed/completed.json').exists())

    def test_detached_mode_cannot_ignore_existing_owner_database(self):
        from scripts import options_monthly_sample as cli
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'workspace').mkdir();(root/'data').mkdir();(root/'data/ariadne.sqlite3').write_bytes(b'owner')
            raw=fixture();(root/'input.zip').write_bytes(raw)
            args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256',hashlib.sha256(raw).hexdigest(),
                '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace','--output','blocked','--owner-data','other','--detached-research'])
            with self.assertRaisesRegex(ValueError,'present owner'):cli.run(args,root=root,now=NOW)
            self.assertFalse((root/'workspace/blocked').exists())

    def test_output_quota_preserves_compact_failure_headroom_and_full_manifest_hash(self):
        from scripts import options_monthly_sample as cli
        from ariadne.options_data import canonical
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'workspace').mkdir();raw=fixture();(root/'input.zip').write_bytes(raw)
            args=cli.parser().parse_args(['--zip','input.zip','--zip-sha256',hashlib.sha256(raw).hexdigest(),
                '--retrieved-at',NOW,'--assume-standard-100','--workspace','workspace','--output','quota','--detached-research'])
            cap=cli.FAILURE_RESERVE+5000
            with (patch.object(cli,'_sources',return_value={'generated':'1'*64}),patch.object(cli,'MAX_OUTPUT',cap),
                  self.assertRaisesRegex(ValueError,'failure-reserve')):
                cli.run(args,root=root,now=NOW)
            dest=root/'workspace/quota'
            failure_raw=(dest/'failed.json').read_bytes();failure=json.loads(failure_raw)
            self.assertLessEqual(len(failure_raw),cli.FAILURE_RESERVE)
            self.assertNotIn('partial_artifacts',failure)
            allfiles={p.relative_to(dest).as_posix():p.read_bytes()for p in dest.rglob('*')if p.is_file()}
            self.assertLessEqual(sum(map(len,allfiles.values())),cap)
            manifest={name:{'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}for name,value in allfiles.items()if name!='failed.json'}
            self.assertGreater(failure['retained_artifact_count'],0)
            self.assertEqual(failure['retained_artifact_count'],len(manifest))
            self.assertEqual(failure['written_manifest_sha256'],hashlib.sha256(canonical(manifest)).hexdigest())
            self.assertEqual(failure['retained_artifact_bytes'],sum(item['bytes']for item in manifest.values()))
            self.assertFalse(failure['replay_allowed'])
            self.assertFalse((dest/'completed.json').exists())


if __name__=='__main__':unittest.main()
