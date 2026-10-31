"""Stream a fixed free monthly sample into explicitly unverified research data.

Only caller-owned streams/callbacks are used. Source byte identity, CSV integrity
and fixed coverage filters do not authenticate prices, security types, contract
deliverables, quote ages or historical publication. No account or strategy work.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from functools import wraps
import hashlib
import io
from pathlib import PurePosixPath
import re
import stat
import struct
import zipfile

from .options_data import (MAX_DATA, MAX_QUOTES, OptionsDataError, canonical,
    clock, contract_id, decimal_string, number, load_dataset)
from .options_archive import parse_manifest
from .qqq_data import session_dates, session_close_utc

START, END = date(2019, 8, 1), date(2019, 8, 30)
MAX_ZIP = 640 * 1024**2
MAX_EXPANDED = 8 * 1024**3
MAX_MEMBER = 512 * 1024**2
MAX_OUTPUT = 2 * 1024**3
MAX_ARCHIVE = 512 * 1024**2
MAX_REJECTIONS = 256 * 1024**2
MAX_ENCODED_SNAPSHOTS = 512 * 1024**2
MAX_ROWS = 32_000_000
MAX_SYMBOLS = 10_000
MAX_BATCH = 64
INTENT_SHA256 = '406f870f5eda83d81ed641cb3ff780ec077259b6ecdf462197d2c3dcecc5acfd'
URL = 'https://www.dropbox.com/scl/fi/1a0tlv4do2ejx2v7cumhe/Sample_L2_2019_August.zip?dl=1&rlkey=ibxlrbngoirxke47w0bfx1r3y'
OPTION_HEADER = 'UnderlyingSymbol UnderlyingPrice Exchange OptionSymbol OptionExt Type Expiration DataDate Strike Last Bid Ask Volume OpenInterest IV Delta Gamma Theta Vega AKA'.split()
STOCK_HEADER = 'symbol quotedate open high low close volume adjustedclose'.split()
STATS_HEADER = 'symbol quotedate iv30call iv30put iv30mean callvol putvol totalvol calloi putoi comment'.split()
# Provider's2019 index list plus its American OEX and explicit index aliases.
BLOCKED_INDICES = frozenset('BKX DJX HGX MNX MXEA MXEF NDX OSX RLG RLV RUI RUT SIXB SIXC SIXE SIXI SIXM SIXR SIXRE SIXT SIXU SIXV SIXY SPX SPXPM UKS UTY VIX XDA XDB XDC XDZ XEO OEX SPXW XSP XND'.split())
INDEX_SOURCE = 'https://historicaloptiondata.com/data-structure/'
_ROOT = re.compile(r'[A-Z]{1,6}\Z')
_OCC = re.compile(r'([A-Z]{1,6})([0-9]{6})([CP])([0-9]{8})\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
LIMITATIONS = [
    'Free sample private evaluation only. No redistribution permission or broader commercial license inferred from the provider offer.',
    'Stock-table membership is not independently authenticated US-stock/ETF classification; known provider-listed indices are blocked but the remaining security types are unverified.',
    'All standard100 American physical-delivery terms are explicit unverified model assumptions. No per-contract multiplier/deliverable evidence or corporate-action adjustment is supplied.',
    'Bid/ask sizes are absent and remain null. Only an explicit unknown-size price proxy can study this source; no strict executable liquidity qualification.',
    'Actual capture is2026. Source date-only values do not authenticate event time or historical publication; session-close/+15min are retrospective assumptions.',
    'Raw stock close is the reference spot; source option UnderlyingPrice remains distinct and every retained disagreement has exact original fields in the issue ledger.',
    'Fixed0..35DTE and full-window[0.95*min(rawclose),1.05*max(rawclose)] strike retention conservatively bounds coverage only; full-window extrema never grant decisions or signals.',
    'Every qualified symbol retains all22sessions, including above100 prices needed for warmup/exits. Entry eligibility below100 must be enforced at decision and fill by the research host.',
    'Only22sessions exist; longer41/65-session warmups cannot be manufactured. No current-universe survivor or profitable-result filtering.',
    'Post-quarantine chains and held-contract exit continuity are not complete or repaired. All source gaps can stop a research ledger. No automatic adoption or owner/account authority.',
]


def _require(condition, message):
    if not condition:
        raise OptionsDataError(message)


def _precision(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
                return function(*args, **kwargs)
        except (zipfile.BadZipFile, csv.Error, UnicodeError) as error:
            raise OptionsDataError('Invalid full source ZIP/CSV integrity: '+str(error)[:160]) from None
    return guarded


def _tick(checkpoint):
    if checkpoint is not None:
        checkpoint()


def source_hash(stream, expected, checkpoint=None):
    _require(not isinstance(stream, (str, bytes)) and hasattr(stream, 'seek') and hasattr(stream, 'read'),
        'Supply a caller-owned seekable binary ZIP stream.')
    _require(type(expected) is str and _HASH.fullmatch(expected), 'Exact lowercase ZIP SHA256 required.')
    stream.seek(0)
    digest, size = hashlib.sha256(), 0
    while block := stream.read(65536):
        _tick(checkpoint)
        _require(type(block) is bytes, 'A binary source stream is required.')
        size += len(block)
        _require(size <= MAX_ZIP, 'ZIP exceeds640MiB acquisition cap.')
        digest.update(block)
    _require(size > 0 and digest.hexdigest() == expected, 'Full source ZIP SHA256 mismatch; no adoption.')
    stream.seek(0)
    return size


def _preflight(stream):
    stream.seek(0, 2)
    size = stream.tell()
    _require(size >= 22, 'Truncated ZIP end record.')
    count = min(size, 65535+22)
    stream.seek(size-count)
    tail = stream.read(count)
    at = tail.rfind(b'PK\x05\x06')
    _require(at >= 0 and at+22 <= len(tail), 'ZIP end record missing.')
    _, disk, directory_disk, disk_count, members, length, offset, comment = struct.unpack_from('<4s4H2LH', tail, at)
    _require(at+22+comment == len(tail) and disk == directory_disk == 0 and disk_count == members
        and 1 <= members <= 256 and 1 <= length <= 256*1024
        and offset != 0xffffffff and offset+length == size-count+at,
        'ZIP directory exceeds fixed non-ZIP64/multidisk/count/byte/layout bounds.')
    stream.seek(offset)
    directory = stream.read(length)
    position = found = 0
    while position < length:
        _require(position+46 <= length and directory[position:position+4] == b'PK\x01\x02', 'Invalid ZIP directory record.')
        name_size, extra, note = struct.unpack_from('<3H', directory, position+28)
        step = 46+name_size+extra+note
        found += 1
        _require(found <= 256 and 1 <= name_size <= 512 and position+step <= length
            and b'\x00' not in directory[position+46:position+46+name_size], 'Unsafe ZIP directory name.')
        position += step
    _require(position == length and found == members, 'ZIP directory/end count mismatch.')
    stream.seek(0)


def _inventory(archive, days):
    expected = {f'L2_{family}_{day:%Y%m%d}.csv' for family in ('options', 'stockquotes', 'optionstats') for day in days}
    found, expanded = set(), 0
    for item in archive.infolist():
        name, path = item.filename, PurePosixPath(item.filename)
        mode = (item.external_attr >> 16) & 0xffff
        _require(item.orig_filename == name and name in expected and name.casefold() not in found
            and not path.is_absolute() and '\\' not in name and ':' not in name and '\x00' not in name
            and all(part not in ('.', '..') for part in path.parts), 'Unexpected, unsafe or duplicate sample member.')
        found.add(name.casefold())
        _require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG)
            and not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
            'Unsupported nonregular/encrypted/compressed ZIP member.')
        _require(1 <= item.file_size <= MAX_MEMBER and item.file_size <= max(1, item.compress_size)*200,
            'Sample member exceeds512MiB/expansion bound.')
        expanded += item.file_size
    _require(len(found) == len(expected) == 66 and expanded <= MAX_EXPANDED, 'All66 fixed CSVs within8GiB required.')
    return expanded


class _Reader(io.RawIOBase):
    def __init__(self, stream, size, expanded, checkpoint):
        self.stream, self.size, self.expanded, self.checkpoint = stream, size, expanded, checkpoint
        self.count, self.digest = 0, hashlib.sha256()
    def readable(self):
        return True
    def readinto(self, buffer):
        _tick(self.checkpoint)
        block = self.stream.read(min(len(buffer), 65536))
        self.count += len(block)
        self.expanded[0] += len(block)
        _require(self.count <= self.size and self.expanded[0] <= MAX_EXPANDED, 'Actual expanded source exceeds fixed bounds.')
        self.digest.update(block)
        buffer[:len(block)] = block
        return len(block)


def _rows(archive, name, header, expanded, proofs, checkpoint):
    info = archive.getinfo(name)
    with archive.open(info) as member:
        digest = _Reader(member, info.file_size, expanded, checkpoint)
        with io.TextIOWrapper(io.BufferedReader(digest, buffer_size=65536), encoding='utf-8-sig', newline='') as text:
            def lines():
                while line := text.readline(16385):
                    _require(len(line) <= 16384, 'CSV physical line exceeds16KiB.')
                    yield line
            reader = csv.reader(lines(), strict=True)
            _require(next(reader, None) == header, 'Exact fixed source CSV header required.')
            count = 0
            for count, row in enumerate(reader, 1):
                _require(count <= MAX_ROWS and len(row) == len(header)
                    and all(len(value) <= 256 and (not value or value.isprintable()) for value in row),
                    'CSV row count/width/printable field bounds violated.')
                yield count, dict(zip(header, row))
            _require(digest.count == info.file_size, 'Full source CSV byte count mismatch.')
            proofs[name] = {'bytes': digest.count, 'sha256': digest.digest.hexdigest(),
                'rows': count, 'ZIP_CRC32': f'{info.CRC:08x}', 'ZIP_CRC_verified': True}


def _day(value):
    _require(type(value) is str and re.fullmatch(r'[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}', value),
        'Actual sample requires date-only M/D/YYYY fields; no inferred quote time.')
    try:
        month, day, year = map(int, value.split('/'))
        return date(year, month, day)
    except ValueError:
        raise OptionsDataError('Invalid source calendar date.') from None


def _integer(value):
    if value == '':
        return None
    _require(bool(re.fullmatch(r'[0-9]{1,10}', value)) and int(value) <= 1_000_000_000,
        'Invalid volume/open interest; no rounding or repair.')
    return int(value)


@_precision
def convert_monthly(stream, *, zip_sha256, retrieved_at, standard100_assumption=False,
                    as_of=None, checkpoint=None, emit=None, output_prefix='market-data/options/archives/monthly2019aug'):
    """Emit tentative files; return catalogs only after every original CSV verifies.

    emit(relative_name, bytes) is an optional trusted caller-owned artifact sink.
    Without it, bytes are returned in files for small generated tests. Any error
    invalidates every emitted file. Up to512MiB aggregate canonical snapshot
    bytes are buffered; monthly chunks are emitted only after all source CSVs
    and the original ZIP verify. The host must reserve a fresh folder, verify
    all bytes and source identities, and publish its completed receipt last.
    """
    _require(standard100_assumption is True, 'Explicit unverified standard100 research assumption required.')
    _require(checkpoint is None or callable(checkpoint), 'Checkpoint must be callable.')
    _require(emit is None or callable(emit), 'Artifact sink must be callable.')
    path = PurePosixPath(output_prefix)
    _require(type(output_prefix) is str and output_prefix and not path.is_absolute()
        and '\\' not in output_prefix and ':' not in output_prefix
        and all(part not in ('.', '..') for part in path.parts), 'A bounded safe workspace-relative output prefix is required.')
    current, received = clock(as_of), clock(retrieved_at)
    _require(received <= current, 'Actual receipt cannot be future-dated.')
    source_bytes = source_hash(stream, zip_sha256, checkpoint)
    _preflight(stream)
    days = session_dates(START, END)
    _require(len(days) == 22, 'Reviewed sample calendar must have22sessions.')
    source = {'provider': 'HistoricalOptionData fixed free August2019 sample', 'url': URL,
        'sha256': zip_sha256, 'retrieved_at': received.isoformat(), 'quote_time_basis': 'assumed_eod',
        'independently_verified': False,
        'usage': 'Private free-sample evaluation; no redistribution inferred. Explicit unverified standard100 American physical model; source security types unverified, known indices blocked. Bid/ask sizes absent/null. Raw stock close reference; original option spot disagreements retained. Date-only source: assumedEOD/+15min, actual2026receipt. Fixed0..35DTE/fullwindowstrikecoverage only; no repaired gaps or automatic adoption.'}
    artifacts, files = {}, {}
    output_bytes = 0
    def publish(name, raw):
        nonlocal output_bytes
        _tick(checkpoint)
        _require(name not in artifacts and type(raw) is bytes, 'Unique binary tentative artifacts required.')
        output_bytes += len(raw)
        _require(output_bytes <= MAX_OUTPUT, 'Whole import exceeds2GiB; no truncation/adoption.')
        if emit is None:
            files[name] = raw
        else:
            emit(name, raw)
        artifacts[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        return {'path': output_prefix+'/'+name, **artifacts[name]}
    issues = bytearray()
    issue_count = 0
    diagnostics = Counter()
    # 20.3M measured rows repeat only22source dates and a small expiry grid.
    # Per-conversion exact-string caching reuses literal validation; it never
    # infers a date from a filename or skips source fields/hash/CRC checking.
    date_cache = {}
    def source_day(value):
        if value not in date_cache:
            _require(len(date_cache) < 4096, 'Source date cache exceeds4096distinct literals.')
            date_cache[value] = _day(value)
        return date_cache[value]
    def issue(name, row_number, fields, reason, disposition='quarantined'):
        nonlocal issue_count
        raw = canonical({'file': name, 'row_number': row_number, 'contract_id': fields.get('OptionSymbol', ''),
            'reason': reason, 'disposition': disposition, 'original_fields': fields})+b'\n'
        _require(len(issues)+len(raw) <= MAX_REJECTIONS, 'Complete reject ledger exceeds256MiB; no truncation/adoption.')
        issues.extend(raw)
        issue_count += 1
        diagnostics[disposition+':'+reason] += 1
    expanded, proofs = [0], {}
    stocks, stock_seen, stock_invalid = {}, set(), Counter()
    per_symbol, intervals = {}, {}
    snapshot_buffers, encoded_bytes = {}, 0
    option_underlyings = set()
    with zipfile.ZipFile(stream) as archive:
        declared = _inventory(archive, days)
        for day in days:
            name = f'L2_stockquotes_{day:%Y%m%d}.csv'
            for row_number, fields in _rows(archive, name, STOCK_HEADER, expanded, proofs, checkpoint):
                _require(source_day(fields['quotedate']) == day, 'Stock row date differs from its fixed file date.')
                symbol = fields['symbol']
                _require((day, symbol) not in stock_seen, 'Duplicate stock symbol/session is ambiguous; whole import stops.')
                stock_seen.add((day, symbol))
                _require(len(stocks) < MAX_SYMBOLS or symbol in stocks, 'Source stock symbol count exceeds10000.')
                prices = stocks.setdefault(symbol, {})
                try:
                    close = number(fields['close'], positive=True)
                except OptionsDataError:
                    stock_invalid[symbol] += 1
                    issue(name, row_number, fields, 'invalid_raw_stock_close')
                    continue
                prices[day] = decimal_string(close)
        for symbol in sorted(stocks):
            prices = stocks[symbol]
            reasons = []
            if not _ROOT.fullmatch(symbol): reasons.append('unsupported_ASCII_root')
            if symbol in BLOCKED_INDICES: reasons.append('known_index')
            if set(prices) != set(days) or stock_invalid[symbol]: reasons.append('missing_or_invalid_raw_stock_calendar')
            if prices and min(map(number, prices.values())) >= 100: reasons.append('never_raw_close_below100')
            per_symbol[symbol] = {'symbol': symbol, 'stock_sessions': len(prices), 'source_option_rows': 0,
                'below100_sessions': sum(number(value)<100 for value in prices.values()),
                'raw_close_min': decimal_string(min(map(number, prices.values()))) if prices else None,
                'raw_close_max': decimal_string(max(map(number, prices.values()))) if prices else None,
                'exclusion_reasons': reasons, 'daily': [], 'tentative_chunks': [], 'canonical_bytes': 0}
            if not reasons:
                intervals[symbol] = (min(map(number,prices.values()))*Decimal('0.95'), max(map(number,prices.values()))*Decimal('1.05'))
                snapshot_buffers[symbol] = []
        for day in days:
            name = f'L2_options_{day:%Y%m%d}.csv'
            quotes_by_symbol = {symbol: [] for symbol in intervals}
            seen_ids, seen_terms = set(), set()
            day_counts = {symbol: Counter() for symbol in intervals}
            for row_number, fields in _rows(archive, name, OPTION_HEADER, expanded, proofs, checkpoint):
                diagnostics['source_option_rows'] += 1
                _require(diagnostics['source_option_rows'] <= MAX_ROWS, 'Total source options rows exceed32000000.')
                _require(source_day(fields['DataDate']) == day, 'Option row date differs from its fixed file date.')
                symbol = fields['UnderlyingSymbol']
                option_underlyings.add(symbol)
                _require(len(option_underlyings) <= MAX_SYMBOLS, 'Source option underlying count exceeds10000.')
                if symbol not in per_symbol:
                    _require(len(per_symbol) < MAX_SYMBOLS, 'Full source universe exceeds10000symbols.')
                    per_symbol[symbol] = {'symbol': symbol, 'stock_sessions': 0, 'source_option_rows': 0,
                        'below100_sessions': 0, 'raw_close_min': None, 'raw_close_max': None,
                        'exclusion_reasons': ['missing_raw_stock_history'], 'daily': [], 'tentative_chunks': [], 'canonical_bytes': 0}
                per_symbol[symbol]['source_option_rows'] += 1
                if symbol not in intervals:
                    diagnostics['outside_predeclared_stock_universe'] += 1
                    continue
                counts = day_counts[symbol]
                counts['source_rows'] += 1
                identity = fields['OptionSymbol']
                _require(identity not in seen_ids, 'Duplicate selected source ID is ambiguous; whole import stops.')
                seen_ids.add(identity)
                match = _OCC.fullmatch(identity)
                if match is None or match[1] != symbol or fields['OptionExt'] != '' or fields['AKA'] not in ('', identity):
                    issue(name, row_number, fields, 'unsupported_adjusted_root_or_alias')
                    counts['quarantined'] += 1
                    continue
                try:
                    expiry = source_day(fields['Expiration'])
                    strike = number(fields['Strike'], positive=True, limit='99999.999')
                    kind = fields['Type']
                    expected = contract_id(symbol, expiry.isoformat(), kind, decimal_string(strike), research_symbols=(symbol,))
                    _require(identity == expected, 'ID_terms_mismatch')
                    terms = (symbol, expiry, kind, strike)
                    _require(terms not in seen_terms, 'duplicate_economic_terms')
                    seen_terms.add(terms)
                except OptionsDataError as error:
                    if str(error) == 'duplicate_economic_terms':
                        raise OptionsDataError('Duplicate selected economic terms; whole import stops.') from None
                    issue(name, row_number, fields, 'invalid_or_contradictory_contract_terms')
                    counts['quarantined'] += 1
                    continue
                if not 0 <= (expiry-day).days <= 35:
                    counts['outside_fixed_0_35_DTE'] += 1
                    continue
                lower, upper_strike = intervals[symbol]
                if not lower <= strike <= upper_strike:
                    counts['outside_full_window_strike_interval'] += 1
                    continue
                counts['within_fixed_retention'] += 1
                if fields['Bid'] == '' or fields['Ask'] == '':
                    issue(name, row_number, fields, 'missing_bid_or_ask'); counts['quarantined'] += 1
                    continue
                try:
                    bid, ask = number(fields['Bid']), number(fields['Ask'])
                    spot = number(fields['UnderlyingPrice'], positive=True)
                    volume, interest = _integer(fields['Volume']), _integer(fields['OpenInterest'])
                except OptionsDataError:
                    issue(name, row_number, fields, 'invalid_price_volume_interest_or_option_spot'); counts['quarantined'] += 1
                    continue
                if bid > ask:
                    issue(name, row_number, fields, 'crossed_bid_ask'); counts['quarantined'] += 1
                    continue
                reference = number(stocks[symbol][day])
                if bid > (reference if kind == 'call' else strike):
                    issue(name, row_number, fields, 'bid_above_reference_economic_upper'); counts['quarantined'] += 1
                    continue
                if spot != reference:
                    issue(name, row_number, fields, 'option_spot_differs_from_raw_stock_reference', 'retained_with_issue')
                    counts['option_spot_mismatch'] += 1
                quotes_by_symbol[symbol].append({'contract_id': identity, 'type': kind, 'expiry': expiry.isoformat(),
                    'strike': decimal_string(strike), 'multiplier': 100, 'bid': decimal_string(bid), 'ask': decimal_string(ask),
                    'bid_size': None, 'ask_size': None, 'volume': volume, 'open_interest': interest})
                counts['accepted_quotes'] += 1
                counts['zero_bid'] += bid == 0
                counts['zero_ask'] += ask == 0
                _require(len(quotes_by_symbol[symbol]) <= 25000, 'One selected symbol/session exceeds25000quotes.')
            for symbol in sorted(intervals):
                record = per_symbol[symbol]
                quotes = quotes_by_symbol[symbol]
                record['daily'].append({'date': day.isoformat(), **dict(day_counts[symbol])})
                if not quotes:
                    continue
                observed = session_close_utc(day)
                snapshot = {'observed_at': observed.isoformat(), 'available_at': (observed+timedelta(minutes=15)).isoformat(),
                    'underlying_symbol': symbol, 'underlying_price': stocks[symbol][day],
                    'quotes': sorted(quotes, key=lambda quote: quote['contract_id'])}
                raw = canonical(snapshot)
                encoded_bytes += len(raw)
                _require(encoded_bytes <= MAX_ENCODED_SNAPSHOTS,
                    'Aggregate encoded snapshots exceed512MiB; no truncation/adoption.')
                snapshot_buffers[symbol].append({'raw':raw, 'quotes':len(quotes),
                    'observed_at':observed.isoformat()})
        # IVStats are integrity/date checked, not used as invented trade or size evidence.
        for day in days:
            name = f'L2_optionstats_{day:%Y%m%d}.csv'
            for _, fields in _rows(archive, name, STATS_HEADER, expanded, proofs, checkpoint):
                _require(source_day(fields['quotedate']) == day, 'IVStats row date differs from its fixed file date.')
        _require(len(proofs) == 66 and expanded[0] == declared, 'All66 original CSV bytes must verify before catalogs.')
    source_hash(stream, zip_sha256, checkpoint)
    catalogs = []
    for symbol in sorted(per_symbol):
        record = per_symbol[symbol]
        buffered = snapshot_buffers.get(symbol, [])
        record['buffered_snapshot_count'] = len(buffered)
        record['buffered_snapshot_bytes'] = sum(len(item['raw']) for item in buffered)
        if not record['source_option_rows']:
            record['exclusion_reasons'].append('no_source_options')
        if symbol in intervals and len(buffered) != 22:
            record['exclusion_reasons'].append('missing_qualified_quote_session')
        record['qualified'] = not record['exclusion_reasons']
        record['standard100_verified'] = False
        record['security_type_verified'] = False
        record['strict_size_profile_qualified'] = False
        if symbol in intervals:
            lower, upper = intervals[symbol]
            record['retention_strike_interval'] = [decimal_string(lower), decimal_string(upper)]
        if not record['qualified']:
            record['tentative_artifacts_adopted'] = False
            record['encoded_snapshot_buffers_adopted'] = False
            record['excluded_records_retained_in_original_source_ZIP'] = True
            continue
        # Immutable encoded snapshots avoid keeping full-market quote-object
        # graphs alive. Only one bounded canonical chunk is parsed/validated at
        # a time, after all original66CSV CRC/SHA and the ZIP have verified.
        empty = canonical({'schema':1, 'evidence_kind':'historical_quotes', 'currency':'USD',
            'as_of':current.isoformat(), 'source':source, 'snapshots':[]})
        needle = b'"snapshots":[]'
        _require(empty.count(needle)==1, 'Canonical snapshot envelope changed.')
        before, after = empty.split(needle)
        pending, pending_bytes, pending_quotes = [], 0, 0
        def flush():
            nonlocal pending, pending_bytes, pending_quotes
            if not pending:
                return
            raw = before+b'"snapshots":['+b','.join(item['raw']for item in pending)+b']'+after
            _require(len(raw)<=MAX_DATA and pending_quotes<=MAX_QUOTES and len(pending)<=4000,
                'Packed dataset exceeds existing32MiB/200kquote/4000snapshot limits.')
            load_dataset(raw, current, research_symbols=(symbol,))
            record['canonical_bytes'] += len(raw)
            _require(record['canonical_bytes']<=MAX_ARCHIVE and len(record['tentative_chunks'])<96,
                'One stock archive exceeds512MiB/96chunks; no partial adoption.')
            # PRN is an actual ETF and a Windows device name. Keep its root.
            name=f'symbols/ticker-{symbol}/chunk-{len(record["tentative_chunks"])+1:03d}.json'
            item=publish(name,raw)
            record['tentative_chunks'].append({**item,'snapshots':len(pending),'quotes':pending_quotes,
                'first_observed_at':pending[0]['observed_at'],'last_observed_at':pending[-1]['observed_at']})
            pending, pending_bytes, pending_quotes = [], 0, 0
        for item in buffered:
            prospective=len(empty)+pending_bytes+len(item['raw'])+len(pending)
            if pending and (prospective>MAX_DATA or pending_quotes+item['quotes']>MAX_QUOTES or len(pending)>=4000):
                flush()
            _require(len(empty)+len(item['raw'])<=MAX_DATA and item['quotes']<=MAX_QUOTES,
                'One encoded snapshot exceeds existing dataset limits.')
            pending.append(item);pending_bytes+=len(item['raw']);pending_quotes+=item['quotes']
        flush()
        _require(sum(item['snapshots']for item in record['tentative_chunks'])==22,
            'Every packed qualified stock must retain all22snapshots.')
        manifest = {'schema': 1, 'as_of': current.isoformat(), 'evidence_kind': 'historical_quotes',
            'symbol': symbol, 'source': source, 'selection': {'start': START.isoformat(), 'end': END.isoformat(),
                'max_dte': 35, 'reason': 'Fixed free August2019 full22sessions. Source stock membership/standard root; at least one rawclose<100. Fixed0..35DTE and inclusive full-window[0.95*min,1.05*max] strike coverage only, never signals. Rawclose reference; all source issues retained; no outcome selection.'},
            'limitations': LIMITATIONS, 'chunks': record['tentative_chunks']}
        raw = canonical(manifest)
        parse_manifest(raw, research_symbols=(symbol,))
        item = publish(f'symbols/ticker-{symbol}/catalog.json', raw)
        catalogs.append({'symbol': symbol, **item, 'sessions': 22,
            'quotes': sum(value['quotes'] for value in record['tentative_chunks']), 'canonical_bytes': record['canonical_bytes']})
        record['tentative_artifacts_adopted'] = True
        record['encoded_snapshot_buffers_adopted'] = True
    ledger = {'schema': 1, 'source_sha256': zip_sha256, 'intent_sha256': INTENT_SHA256,
        'universe_selection_not_outcome_based': True, 'index_blocklist_source': INDEX_SOURCE,
        'known_index_roots': sorted(BLOCKED_INDICES), 'security_type_verified': False,
        'all_symbols': [per_symbol[symbol] for symbol in sorted(per_symbol)]}
    ledger_item = publish('universe-ledger.json', canonical(ledger))
    issue_item = publish('reject-issues.jsonl', bytes(issues))
    proof_item = publish('source-files.json', canonical({'schema': 1, 'ZIP_sha256': zip_sha256,
        'CSV_files': proofs, 'expanded_bytes': expanded[0], 'publisher_per_file_hashes_supplied': False,
        'all_original_CSV_CRC_SHA_bytes_verified': True}))
    symbols = [item['symbol'] for item in catalogs]
    batches = []
    for offset in range(0, len(catalogs), MAX_BATCH):
        batch = catalogs[offset:offset+MAX_BATCH]
        _require(sum(item['canonical_bytes'] for item in batch) <= MAX_ARCHIVE,
            'A fixed ASCII64-symbol batch exceeds512MiB; whole qualification fails.')
        batches.append({'index': len(batches)+1, 'symbols': [item['symbol'] for item in batch],
            'canonical_bytes': sum(item['canonical_bytes'] for item in batch)})
    index = {'schema': 1, 'kind': 'monthly_options_universe', 'research_only': True, 'execution_qualified': False,
        'source': source, 'intent_sha256': INTENT_SHA256, 'as_of': current.isoformat(),
        'first_date': START.isoformat(), 'last_date': END.isoformat(), 'sessions': 22,
        'archives': catalogs, 'qualified_symbols': symbols, 'batches': batches,
        'universe_ledger': ledger_item, 'reject_issues': issue_item, 'source_files': proof_item,
        'limitations': LIMITATIONS, 'owner_settings_written': False, 'paper_operations': 0, 'backtests_run': 0}
    index_raw = canonical(index)
    publish('universe.json', index_raw)
    source_hash(stream, zip_sha256, checkpoint)
    receipt = {'schema': 1, 'status': 'qualified_research_only', 'source_ZIP': {'bytes': source_bytes, 'sha256': zip_sha256},
        'source_option_rows': diagnostics['source_option_rows'], 'expanded_CSV_bytes': expanded[0],
        'all66_CSV_CRC_SHA_verified': True, 'source_stock_symbols': len(stocks),
        'source_option_underlyings': len(option_underlyings), 'all_coverage_symbols': len(per_symbol),
        'encoded_snapshot_bytes':encoded_bytes,'encoded_snapshot_byte_limit':MAX_ENCODED_SNAPSHOTS,
        'storage_layout':'contiguous_monthly_chunks_after_full_source_verification',
        'validated_distinct_date_literals': len(date_cache),
        'qualified_symbols': len(catalogs), 'excluded_symbols': len(per_symbol)-len(catalogs),
        'batches': len(batches), 'reject_issue_records': issue_count, 'reject_issue_bytes': len(issues),
        'output_bytes_before_receipt': output_bytes, 'diagnostics': dict(diagnostics),
        'standard100_verified': False, 'security_types_verified': False, 'sizes_available': False,
        'historical_publication_verified': False, 'execution_qualified': False, 'automatic_adoption': False,
        'owner_settings_written': False, 'network_calls': 0, 'model_calls': 0, 'backtests_run': 0}
    return {'index': index_raw, 'receipt': receipt, 'artifacts': artifacts, 'files': files}
