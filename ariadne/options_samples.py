"""Bounded offline qualification of the offered HistoricalData.net KO sample.

Streams are supplied by the caller. No paths, network, database, orders, strategy
evaluation or publisher code are opened/executed here. Quotes are quarantined
with original fields; missing values never become prices or liquidity.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, timedelta
from decimal import Context, ROUND_HALF_EVEN, localcontext
from functools import wraps
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import struct
import zipfile

from .options_data import (OptionsDataError, canonical, clock, contract_id,
    decimal_string, number, validate_dataset, MAX_DATA, MAX_QUOTES)
from .options_archive import parse_manifest
from .qqq_data import session_dates, session_close_utc

START, END = date(2022, 7, 1), date(2022, 12, 30)
MAX_EXPANDED = 2 * 1024**3
MAX_CANONICAL = 256 * 1024**2
MAX_REJECTIONS = 32 * 1024**2
MAX_MEMBER = 32 * 1024**2
MAX_METADATA = 4 * 1024**2
MAX_ROWS = 6_000_000
PREFIX = 'market-data/options/archives/ko-2022h2-historicaldata'
OPTIONS_URL = 'https://historicaldata.net/file/options_sample_2022H2.zip'
STOCK_URL = 'https://historicaldata.net/file/stocks_sample_2022H2.zip'
OPTION_HEADER = ('contract underlying expiration type strike style quote_date bid bid_size ask ask_size quote_time volume open_interest open high low close trade_vwap transactions multileg_volume active_minutes last_trade_date underlying_close settlement_time iv_bid iv_ask iv iv_flag delta gamma theta vega rho').split()
STOCK_HEADER = ('date open high low close volume vwap transactions adj_open adj_high adj_low adj_close adj_volume adj_vwap dividend dividend_type split').split()
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'(?:O:)?KO([0-9]{6})([CP])([0-9]{8})\Z')
LIMITATIONS = [
    'HistoricalData.net free sample, personal evaluation; raw records and reconstructable source substitutes must not be redistributed.',
    '2022 quote_time is absent. Session-close and plus15min clocks are counterfactual assumed-EOD inputs, never actual quote events or historical publication records.',
    'Last-standing quotes are asynchronous and quote ages are unknown. File hashes and source sizes do not establish exchange authenticity or executable liquidity.',
    'Standard100 American physical-delivery terms are a research model assumption; per-contract deliverables and multipliers are absent and not independently verified. Adjusted roots are unsupported.',
    'The provider maps reported zero sizes and some unusable values to blanks before publication. These files are not untouched raw exchange records; blank sizes remain unknown.',
    'Fixed KO and0-35DTE scope only. All127 sessions retained; quarantined rows retain exact fields/reasons and can cause later held-contract evidence stops. Post-quarantine chains are not claimed complete.',
    'Previously examined2022 market regime and earlier QQQ development prevent untouched-market or prospective-validation claims. No automatic adoption or owner-account authority.',
]
PRIMARY_TERM_CONTEXT = {
    'OCC_standard_equity_specification': 'https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications',
    'issuer_regular_dividend_context': 'https://investors.coca-colacompany.com/news-events/press-releases/detail/1070/the-coca-cola-company-elects-three-officers-and-declares-quarterly-dividend',
    'scope': 'General standard100 American physical-delivery convention and one issuer regular0.44 dividend announcement; neither proves every sampled contract deliverable/multiplier. No current settlement lag is imputed into2022.'}


def _precision(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
            return function(*args, **kwargs)
    return guarded


def _require(condition, message):
    if not condition:
        raise OptionsDataError(message)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'Duplicate publisher metadata key.')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise OptionsDataError('Publisher manifest must be bounded unique-key finite JSON.') from None


def _tick(checkpoint):
    if checkpoint is not None:
        checkpoint()


def _zip_hash(stream, expected, maximum, checkpoint):
    _require(not isinstance(stream, (str, bytes)) and hasattr(stream, 'read') and hasattr(stream, 'seek'),
             'Supply a caller-owned seekable binary stream, never a filename or ZIP bytes.')
    _require(type(expected) is str and _HASH.fullmatch(expected), 'An exact source ZIP SHA256 is required.')
    stream.seek(0)
    digest, total = hashlib.sha256(), 0
    while True:
        _tick(checkpoint)
        block = stream.read(65536)
        if not block:
            break
        _require(type(block) is bytes, 'Binary source stream required.')
        total += len(block)
        _require(total <= maximum, 'Source ZIP exceeds its fixed acquisition cap.')
        digest.update(block)
    _require(total > 0 and digest.hexdigest() == expected, 'Source ZIP hash differs; no source is adopted.')
    stream.seek(0)
    return total


def _inventory(archive):
    infos = archive.infolist()
    _require(1 <= len(infos) <= 512, 'ZIP member count exceeds fixed bound.')
    names, expanded = set(), 0
    for item in infos:
        name, path = item.filename, PurePosixPath(item.filename)
        mode = (item.external_attr >> 16) & 0xffff
        _require(item.orig_filename == name and name and not path.is_absolute() and '\\' not in name and ':' not in name
                 and '\x00' not in name and all(part not in ('.', '..') for part in path.parts)
                 and name.casefold() not in names, 'Unsafe or duplicate ZIP member.')
        names.add(name.casefold())
        _require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                 'ZIP symlinks/nonregular members are unsupported.')
        _require(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                 'Encrypted or unsupported ZIP compression.')
        _require(item.file_size <= MAX_MEMBER and item.file_size <= max(1, item.compress_size) * 100,
                 'ZIP member exceeds size or expansion-ratio bound.')
        expanded += item.file_size
    return expanded


def _preflight_zip(stream):
    """Bound the directory before ZipFile can allocate any ZipInfo objects."""
    stream.seek(0, 2)
    size = stream.tell()
    _require(size >= 22, 'Truncated ZIP end record.')
    tail_size = min(size, 65535 + 22)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    position = tail.rfind(b'PK\x05\x06')
    _require(position >= 0 and position + 22 <= len(tail), 'ZIP end record missing.')
    fields = struct.unpack_from('<4s4H2LH', tail, position)
    _, disk, directory_disk, disk_count, count, directory_size, offset, comment = fields
    end_position = size - tail_size + position
    _require(position + 22 + comment == len(tail) and disk == directory_disk == 0
             and disk_count == count and 1 <= count <= 512
             and 1 <= directory_size <= 512 * 1024 and offset + directory_size == end_position
             and offset != 0xffffffff, 'ZIP directory count/size/layout exceeds the fixed non-ZIP64 scope.')
    stream.seek(offset)
    directory = stream.read(directory_size)
    _require(len(directory) == directory_size, 'Truncated ZIP directory.')
    cursor, found = 0, 0
    while cursor < directory_size:
        _require(cursor + 46 <= directory_size and directory[cursor:cursor+4] == b'PK\x01\x02',
                 'Unsupported ZIP directory record.')
        name_size, extra_size, comment_size = struct.unpack_from('<3H', directory, cursor + 28)
        step = 46 + name_size + extra_size + comment_size
        found += 1
        _require(found <= 512 and 1 <= name_size <= 512 and cursor + step <= directory_size
                 and b'\x00' not in directory[cursor+46:cursor+46+name_size], 'Unsafe or oversized ZIP directory entry.')
        cursor += step
    _require(found == count, 'ZIP directory count contradicts its bounded end record.')
    stream.seek(0)


def _manifest(archive, name):
    item = archive.getinfo(name)
    _require(item.file_size <= MAX_METADATA, 'Publisher manifest too large.')
    raw = archive.read(item)  # Full metadata CRC is checked by ZipFile.
    value = _json(raw)
    _require(type(value) is dict and type(value.get('files')) is list
             and 1 <= len(value['files']) <= 512, 'Publisher file manifest is invalid.')
    directory = str(PurePosixPath(name).parent)
    entries = {}
    for record in value['files']:
        _require(type(record) is dict and type(record.get('name')) is str, 'Invalid manifest file record.')
        member = (directory + '/' if directory != '.' else '') + record['name']
        _require(member not in entries and type(record.get('bytes')) is int
                 and 1 <= record['bytes'] <= MAX_MEMBER and type(record.get('sha256')) is str
                 and _HASH.fullmatch(record['sha256']), 'Invalid or duplicate manifest identity.')
        info = archive.getinfo(member)
        _require(info.file_size == record['bytes'], 'Manifest and ZIP byte counts differ.')
        if 'rows' in record:
            _require(type(record['rows']) is int and 1 <= record['rows'] <= MAX_ROWS, 'Invalid publisher row count.')
        entries[member] = record
    csv_names = {info.filename for info in archive.infolist() if info.filename.endswith('.csv')}
    _require(set(entries) == csv_names, 'Manifest and complete CSV inventory differ.')
    return value, entries, hashlib.sha256(raw).hexdigest()


class _DigestReader(io.RawIOBase):
    def __init__(self, stream, expected, expanded, checkpoint):
        self.stream, self.expected, self.expanded, self.checkpoint = stream, expected, expanded, checkpoint
        self.digest, self.count = hashlib.sha256(), 0
    def readable(self):
        return True
    def readinto(self, buffer):
        _tick(self.checkpoint)
        raw = self.stream.read(min(len(buffer), 65536))
        self.count += len(raw)
        self.expanded[0] += len(raw)
        _require(self.count <= self.expected['bytes'] and self.expanded[0] <= MAX_EXPANDED,
                 'Expanded source bytes exceed the fixed manifest/aggregate bound.')
        self.digest.update(raw)
        buffer[:len(raw)] = raw
        return len(raw)
    def verify(self):
        _require(self.count == self.expected['bytes'] and self.digest.hexdigest() == self.expected['sha256'],
                 'Full CSV byte/hash mismatch; all attempted output is invalid.')


def _csv_rows(archive, name, record, expanded, checkpoint):
    with archive.open(name) as member:
        digest = _DigestReader(member, record, expanded, checkpoint)
        with io.TextIOWrapper(io.BufferedReader(digest, buffer_size=65536), encoding='utf-8-sig', newline='') as text:
            def lines():
                while True:
                    line = text.readline(16385)
                    if not line:
                        break
                    _require(len(line) <= 16384, 'A source CSV physical line exceeds16KiB.')
                    yield line
            reader = csv.reader(lines(), strict=True)
            try:
                header = next(reader)
            except StopIteration:
                raise OptionsDataError('Source CSV has no header.') from None
            count = 0
            yield None, header
            for count, row in enumerate(reader, 1):
                _require(count <= MAX_ROWS and len(row) == len(header), 'Source CSV row count/width exceeds contract.')
                yield count, row
            digest.verify()
            if 'rows' in record:
                _require(count == record['rows'], 'Publisher CSV row count differs; no partial adoption.')


def _date(value):
    _require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'Invalid source ISO date.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise OptionsDataError('Invalid source calendar date.') from None


def _integer(value):
    if value == '':
        return None
    _require(bool(re.fullmatch(r'\d{1,10}', value)) and int(value) <= 1_000_000_000,
             'Unrepresentable source integer; no rounding or zero filling.')
    return int(value)


@_precision
def convert_samples(options_stream, stock_stream, *, options_sha256, stock_sha256,
                    retrieved_at, standard100_assumption=False, as_of=None, checkpoint=None):
    """Return fully checked chunks/manifest/reject ledger, without writing output.

    The caller publishes only after this returns successfully. Both ZIPs and all
    original daily CSVs must verify, including foreign-underlying rows. A failure
    invalidates the whole attempted conversion; no partial chunk is returned.
    """
    _require(standard100_assumption is True, 'Explicit unverified standard100 research assumption required.')
    _require(checkpoint is None or callable(checkpoint), 'Checkpoint must be callable.')
    current = clock(as_of)
    received = clock(retrieved_at)
    _require(received <= current, 'Actual source receipt cannot be in the future.')
    option_bytes = _zip_hash(options_stream, options_sha256, 300 * 1024**2, checkpoint)
    stock_bytes = _zip_hash(stock_stream, stock_sha256, 16 * 1024**2, checkpoint)
    _preflight_zip(options_stream)
    _preflight_zip(stock_stream)
    days = session_dates(START, END)
    _require(len(days) == 127, 'Reviewed calendar no longer matches fixed127-session sample.')
    diagnostics = Counter()
    ledger, ledger_bytes = [], 0
    expanded = [0]
    chunks, snapshots, chunk_quotes, output_bytes, chunk_snapshot_bytes = [], [], 0, 0, 0
    stocks, stock_events, per_day = {}, [], []

    def issue(file, row_number, fields, reason, disposition='quarantined'):
        nonlocal ledger_bytes
        record = {'file': file, 'row_number': row_number, 'contract_id': fields.get('contract', ''),
                  'reason': reason, 'disposition': disposition, 'original_fields': fields}
        raw = canonical(record) + b'\n'
        ledger_bytes += len(raw)
        _require(ledger_bytes <= MAX_REJECTIONS, 'Complete reject/issue ledger exceeds32MiB; no truncation/adoption.')
        ledger.append(raw)
        diagnostics[disposition + ':' + reason] += 1

    with zipfile.ZipFile(options_stream) as options, zipfile.ZipFile(stock_stream) as stock:
        _require(_inventory(options) + _inventory(stock) <= MAX_EXPANDED, 'Combined declared ZIP expansion exceeds2GiB.')
        om, option_files, option_manifest_sha = _manifest(options, 'day_by_date/manifest.json')
        sm, stock_files, stock_manifest_sha = _manifest(stock, 'manifest.json')
        _require(om.get('product') == 'options_sample_2022H2' and om.get('first_day') == START.isoformat()
                 and om.get('last_day') == END.isoformat() and om.get('trading_days_expected') == 127
                 and om.get('trading_days_present') == 127 and om.get('trading_days_missing') == [],
                 'Publisher options window/calendar contradicts the predeclared scope.')
        expected_names = {'day_by_date/' + day.isoformat() + '_options.csv' for day in days}
        _require(set(option_files) == expected_names and type(om.get('rows_total')) is int
                 and sum(item.get('rows', -1) for item in option_files.values()) == om['rows_total'] <= MAX_ROWS,
                 'Full options manifest session/row inventory differs.')
        _require(sm.get('product') == 'stock_sample_2022H2' and sm.get('files_count') == 8
                 and len(stock_files) == 8 and sm.get('trading_days_missing') == []
                 and sm.get('bytes_total') == sum(item['bytes'] for item in stock_files.values())
                 and 'day_by_symbol/KO_day.csv' in stock_files, 'Complete stock sample inventory required.')
        for name, record in stock_files.items():
            _tick(checkpoint)
            if name != 'day_by_symbol/KO_day.csv':
                with stock.open(name) as member:
                    reader = _DigestReader(member, record, expanded, checkpoint)
                    while reader.read(65536):
                        pass
                    reader.verify()
                continue
            rows = _csv_rows(stock, name, record, expanded, checkpoint)
            _, header = next(rows)
            _require(header == STOCK_HEADER, 'Exact17-column raw stock layout required.')
            for index, row in rows:
                fields = dict(zip(header, row))
                day = _date(fields['date'])
                _require(day not in stocks and day in days, 'Duplicate/out-of-window stock day; no deduplication.')
                close = number(fields['close'], positive=True)
                _require(close < 100, 'Every accepted raw KO close must be below100; window is not filtered.')
                _require(fields['split'] in ('', '1:1'), 'KO split event requires separate term qualification; no automatic adjustment.')
                stocks[day] = decimal_string(close)
                dividend = number(fields['dividend']) if fields['dividend'] else None
                if dividend is not None and dividend > 0 or fields['split']:
                    stock_events.append({'date': fields['date'], 'dividend': fields['dividend'],
                                         'dividend_type': fields['dividend_type'], 'split': fields['split']})
        _require(set(stocks) == set(days), 'Raw KO stock coverage must contain every selected session.')
        source = {'provider': 'HistoricalData.net fixed free KO 2022H2 sample', 'url': OPTIONS_URL,
            'sha256': hashlib.sha256(canonical({'options_zip': options_sha256, 'stock_zip': stock_sha256,
                'options_manifest': option_manifest_sha, 'stock_manifest': stock_manifest_sha})).hexdigest(),
            'retrieved_at': received.isoformat(), 'quote_time_basis': 'assumed_eod', 'independently_verified': False,
            'usage': 'Free sample evaluation; raw data private/no redistribution. KO0-35DTE; raw stock closes; publisher zero-size-to-blank preprocessing. 2022 quote_time absent; EOD/+15min are counterfactual assumptions. Standard100 terms unverified, not supplied. Quarantines retain original fields. No execution qualification or automatic adoption.'}

        def flush():
            nonlocal snapshots, chunk_quotes, output_bytes, chunk_snapshot_bytes
            if not snapshots:
                return
            data = {'schema': 1, 'evidence_kind': 'historical_quotes', 'as_of': current.isoformat(),
                    'currency': 'USD', 'source': source, 'snapshots': snapshots}
            quality = validate_dataset(data, current)
            raw = canonical(data)
            output_bytes += len(raw)
            _require(len(raw) <= MAX_DATA and output_bytes <= MAX_CANONICAL and len(chunks) < 96,
                     'Canonical KO archive exceeds fixed chunk/aggregate bounds; no truncation.')
            name = f'chunk-{len(chunks)+1:03d}.json'
            chunks.append({'name': name, 'raw': raw, 'metadata': {'path': PREFIX + '/' + name,
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'snapshots': quality['snapshots'],
                'quotes': quality['quotes'], 'first_observed_at': quality['first_observed_at'],
                'last_observed_at': quality['last_observed_at']}})
            snapshots, chunk_quotes, chunk_snapshot_bytes = [], 0, 0

        for day in days:
            _tick(checkpoint)
            name = 'day_by_date/' + day.isoformat() + '_options.csv'
            rows = _csv_rows(options, name, option_files[name], expanded, checkpoint)
            _, header = next(rows)
            _require(header == OPTION_HEADER, 'Exact34-column source option layout required.')
            quotes, seen_ids, seen_terms, day_counts = [], set(), set(), Counter()
            for index, row in rows:
                diagnostics['source_option_rows'] += 1
                _require(row[6] == day.isoformat(), 'A full-source quote_date differs from its daily file.')
                if not re.fullmatch(r'KO\d*', row[1]) and _ID.fullmatch(row[0]) is None:
                    diagnostics['other_underlying_rows'] += 1
                    continue
                fields = dict(zip(header, row))
                diagnostics['KO_source_rows'] += 1
                _require(all(len(value) <= 256 and (value == '' or value.isprintable()) for value in row),
                         'Selected source cells exceed bounded printable field contract.')
                identity = fields['contract'].removeprefix('O:')
                _require(identity not in seen_ids, 'Duplicate selected source ID is ambiguous; whole qualification stops.')
                seen_ids.add(identity)
                match = _ID.fullmatch(fields['contract'])
                if fields['underlying'] != 'KO' or match is None:
                    issue(name, index, fields, 'unsupported_or_adjusted_contract_root'); continue
                try:
                    expiry = _date(fields['expiration'])
                    strike = number(fields['strike'], positive=True, limit='99999.999')
                    kind = fields['type']
                    expected_id = contract_id('KO', fields['expiration'], kind, decimal_string(strike))
                    _require(identity == expected_id, 'contract_id_terms_mismatch')
                    terms = (fields['expiration'], kind, strike)
                    _require(terms not in seen_terms, 'duplicate_economic_terms')
                    seen_terms.add(terms)
                except (OptionsDataError, ValueError) as error:
                    if str(error) == 'duplicate_economic_terms':
                        raise OptionsDataError('Duplicate economic terms are ambiguous; whole qualification stops.') from None
                    issue(name, index, fields, 'invalid_or_contradictory_contract_terms'); continue
                if not 0 <= (expiry-day).days <= 35:
                    diagnostics['outside_fixed_0_35_DTE'] += 1; day_counts['outside_fixed_0_35_DTE'] += 1; continue
                day_counts['within_fixed_DTE'] += 1
                if fields['style'] != 'A' or fields['settlement_time'] != 'PM':
                    issue(name, index, fields, 'unsupported_style_or_settlement'); continue
                if fields['quote_time'] != '':
                    issue(name, index, fields, 'unexpected_quote_time_in_date_only_scope'); continue
                if fields['bid'] == '' or fields['ask'] == '':
                    issue(name, index, fields, 'missing_bid_or_ask'); continue
                try:
                    bid, ask = number(fields['bid']), number(fields['ask'])
                    sizes = {key: _integer(fields[key]) for key in ('bid_size', 'ask_size', 'volume', 'open_interest')}
                    option_spot = number(fields['underlying_close'], positive=True)
                except (OptionsDataError, ValueError):
                    issue(name, index, fields, 'nonrepresentable_price_size_or_underlying'); continue
                if bid > ask:
                    issue(name, index, fields, 'crossed_bid_ask'); continue
                upper = number(stocks[day]) if kind == 'call' else strike
                if bid > upper:
                    issue(name, index, fields, 'bid_above_economic_upper_bound'); continue
                if option_spot != number(stocks[day]):
                    issue(name, index, fields, 'option_underlying_differs_from_predeclared_raw_stock_close', 'retained_with_issue')
                quote = {'contract_id': identity, 'type': kind, 'expiry': fields['expiration'],
                         'strike': decimal_string(strike), 'multiplier': 100, 'bid': decimal_string(bid),
                         'ask': decimal_string(ask), **sizes}
                quotes.append(quote)
                diagnostics['accepted_quotes'] += 1; day_counts['accepted_quotes'] += 1
                diagnostics['zero_bid_quotes'] += bid == 0; diagnostics['zero_ask_quotes'] += ask == 0
                for key in ('bid_size', 'ask_size'):
                    diagnostics['missing_' + key] += sizes[key] is None
                    diagnostics['zero_' + key] += sizes[key] == 0
            _require(1 <= len(quotes) <= 25000, 'Every selected session needs1-25000 qualified quotes; no missing-day repair.')
            observed = session_close_utc(day)
            snapshot = {'observed_at': observed.isoformat(), 'available_at': (observed+timedelta(minutes=15)).isoformat(),
                        'underlying_symbol': 'KO', 'underlying_price': stocks[day],
                        'quotes': sorted(quotes, key=lambda quote: quote['contract_id'])}
            snapshot_bytes = len(canonical(snapshot))
            if snapshots and (snapshots[-1]['observed_at'][:7] != observed.isoformat()[:7]
                              or chunk_quotes + len(quotes) > MAX_QUOTES
                              or chunk_snapshot_bytes + snapshot_bytes + 4096 > MAX_DATA):
                flush()
            snapshots.append(snapshot); chunk_quotes += len(quotes); chunk_snapshot_bytes += snapshot_bytes + 1
            per_day.append({'date': day.isoformat(), **dict(day_counts)})
        flush()
        _require(diagnostics['source_option_rows'] == om['rows_total'], 'Complete source row total differs.')
        manifest = {'schema': 1, 'as_of': current.isoformat(), 'evidence_kind': 'historical_quotes', 'symbol': 'KO',
                    'source': source, 'selection': {'start': START.isoformat(), 'end': END.isoformat(), 'max_dte': 35,
                    'reason': 'Predeclared complete free KO2022H2 sample; raw stock closes strictly below100; standard-root0-35DTE; explicit quarantine ledger; no outcome-driven selection.'},
                    'limitations': LIMITATIONS, 'chunks': [item['metadata'] for item in chunks]}
        manifest_raw = canonical(manifest)
        parse_manifest(manifest_raw)
    _zip_hash(options_stream, options_sha256, 300 * 1024**2, checkpoint)
    _zip_hash(stock_stream, stock_sha256, 16 * 1024**2, checkpoint)
    rejects = b''.join(ledger)
    receipt = {'schema': 1, 'status': 'qualified_date_only_research_with_quarantines',
        'symbol': 'KO', 'first_date': START.isoformat(), 'last_date': END.isoformat(), 'sessions': 127,
        'source_ZIPs': {'options': {'bytes': option_bytes, 'sha256': options_sha256},
                        'stocks': {'bytes': stock_bytes, 'sha256': stock_sha256}},
        'publisher_manifests': {'options_sha256': option_manifest_sha, 'stocks_sha256': stock_manifest_sha},
        'all_original_CSV_CRC_SHA_bytes_verified': True, 'all_127_original_option_row_counts_verified': True,
        'source_rows': diagnostics['source_option_rows'], 'expanded_CSV_bytes_checked': expanded[0],
        'diagnostics': dict(diagnostics), 'daily_diagnostics': per_day, 'stock_events': stock_events,
        'reject_issue_records': len(ledger), 'reject_issue_bytes': len(rejects),
        'reject_issue_sha256': hashlib.sha256(rejects).hexdigest(), 'canonical_bytes': output_bytes,
        'chunks': len(chunks), 'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
        'all_raw_KO_closes_below100': True, 'standard100_terms_independently_verified': False,
        'standard100_assumption_explicit': True, 'provider_zero_size_to_blank_preprocessing': True,
        'primary_term_context': PRIMARY_TERM_CONTEXT,
        'quote_age_known': False, 'post_quarantine_chain_complete': False,
        'execution_qualified': False, 'automatic_adoption': False, 'paper_operations': 0,
        'network_calls': 0, 'model_calls': 0, 'backtests_run': 0}
    return {'chunks': chunks, 'manifest': manifest_raw, 'reject_ledger': rejects, 'receipt': receipt}
