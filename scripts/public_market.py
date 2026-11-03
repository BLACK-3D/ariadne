"""Personal Public market data and explicit shared-account paper operations.

No order endpoints, account funding, model requests or automatic scheduling.
Run from a local terminal; credentials belong only in the hidden prompt.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_INPUT = 256 * 1024


class PublicCommandError(ValueError):
    """Controlled diagnostic text; contains no provider payload or credential."""


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('action', choices=('setup', 'accounts', 'status', 'collect', 'binding', 'migrate', 'paper'))
    value.add_argument('--data-dir', default='data', help='Existing box-relative private data directory.')
    value.add_argument('--workspace', default='data/workspace', help='Existing box-relative workspace.')
    value.add_argument('--online', action='store_true', help='Explicit network opt-in; an inherited offline policy still blocks access.')
    value.add_argument('--instruments', help='Workspace-relative JSON: schema:1 and instruments:[{symbol,type}].')
    value.add_argument('--request', help='Workspace-relative explicit shared-account migration or paper request JSON.')
    value.add_argument('--supplement', help='Workspace-relative independently dated activity/deliverable evidence. Missing evidence blocks entries.')
    value.add_argument('--output', help='For collect only: a NEW workspace-relative reports/<name> directory.')
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()


def _guards(args, root):
    from scripts.options_market import guard_for
    from ariadne.config import _allowed_path
    from ariadne.tools import ToolRegistry
    root = _allowed_path(root)
    box = object.__new__(ToolRegistry)
    box.root = root
    data = box._path(args.data_dir)
    if not data.is_dir():
        raise PublicCommandError('Start Ariadne once to create the local data directory; no new paper account is created here.')
    data_guard = object.__new__(ToolRegistry)
    data_guard.root = data
    workspace = guard_for(args.workspace, root=root)
    if not workspace.root.is_relative_to(data):
        raise PublicCommandError('The Public workspace must remain inside this box data directory.')
    return data_guard, workspace


def _load(guard, path):
    from scripts.options_market import read, metadata
    return metadata(read(guard, path, MAX_INPUT))


def _readonly(guard):
    from ariadne.config import _allowed_path
    path = guard._path('ariadne.sqlite3')
    for suffix in ('', '-wal', '-shm', '-journal'):
        candidate = _allowed_path(str(path) + suffix)
        if not candidate.exists():
            if not suffix:
                raise PublicCommandError('Existing Ariadne owner database is required; this command never creates a bankroll.')
            continue
        info = candidate.stat()
        if not candidate.is_file() or info.st_nlink != 1:
            raise PublicCommandError('Owner database must be a regular single-link file.')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA trusted_schema=OFF')
    db.execute('PRAGMA query_only=ON')
    return db


def _settings(db):
    from scripts.options_market import metadata
    values = {}
    for key in ('paused', 'internet_access'):
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        values[key] = None if row is None else metadata(row[0].encode())
    if values['paused'] is not None and type(values['paused']) is not bool:
        raise PublicCommandError('Owner pause setting is invalid.')
    return values


def _network_allowed(guard):
    with guard._directory(guard.root), closing(_readonly(guard)) as db:
        state = _settings(db)
        if state['paused'] is True:
            raise PublicCommandError('Global autonomy is paused; Public collection was not started.')
        internet = state['internet_access']
        if type(internet) is not dict or internet.get('enabled') is not True:
            raise PublicCommandError('Owner Internet control is disabled or unconfigured; no setting was changed.')
        return hashlib.sha256(_canonical(state)).hexdigest()


def _hidden(prompt):
    if not sys.stdin.isatty():
        raise PublicCommandError('Use an interactive local terminal for hidden credential entry; piped input is refused.')
    return getpass.getpass(prompt)


def _owner(args, data, workspace):
    # Construct accessors without Store.__init__, which would initialize tables.
    from ariadne.store import Store
    from ariadne.config import Settings
    from ariadne.tools import ToolRegistry
    from ariadne.briefing import BriefingService
    with data._directory(data.root), closing(_readonly(data)):
        pass
    brief_path = data._path('briefing.sqlite3')
    if not brief_path.is_file() or brief_path.stat().st_nlink != 1:
        raise PublicCommandError('Existing initialized briefing controls are required; no controls were created.')
    settings = Settings(data_dir=data.root, workspace=workspace.root)
    store = object.__new__(Store)
    store.path = data._path('ariadne.sqlite3')
    registry = ToolRegistry(settings)
    return BriefingService(settings, store, registry).options


def _status(data):
    from ariadne.public_credentials import configured
    result = {'schema': 1, 'credential_configured': configured(data), 'network_requests': 0,
              'paper_only': True, 'real_orders_supported': False, 'automatic_collection_enabled': False,
              'live_feed_qualified': False, 'bankroll_created': False, 'funding_added': '0'}
    with data._directory(data.root), closing(_readonly(data)) as db:
        controls = _settings(db)
        result['global_paused'] = controls['paused']
        result['internet_enabled'] = type(controls['internet_access']) is dict and controls['internet_access'].get('enabled') is True
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_accounts'").fetchone()
        rows = [] if not exists else db.execute('SELECT state FROM opt_accounts WHERE closed_at IS NULL LIMIT 2').fetchall()
        if len(rows) > 1:
            raise PublicCommandError('Multiple active paper accounts require review; no account was selected.')
        result['paper_account_present'] = bool(rows)
        if rows:
            from scripts.options_market import metadata
            state = metadata(rows[0][0].encode())
            result['paper_account_schema'] = state.get('schema')
            result['saved_state_sha256'] = hashlib.sha256(rows[0][0].encode()).hexdigest()
            result['state_projection_verified'] = False
            result['note'] = 'Saved schema only; binding/migration performs full ledger verification. A Public key does not qualify a feed.'
    return result


def _collect(args, data, workspace, client_factory):
    from ariadne.public_market_data import instruments
    from ariadne.public_credentials import load
    from ariadne.atomic import publish_bytes
    watch = _load(workspace, args.instruments)
    if type(watch) is not dict or set(watch) != {'schema', 'instruments'} or type(watch['schema']) is not int or watch['schema'] != 1:
        raise PublicCommandError('Use exactly schema:1 and instruments:[{symbol,type}].')
    requested = instruments(watch['instruments'])
    policy = _network_allowed(data)
    secret, account_id = load(data)
    client = client_factory(secret, online=True)
    try:
        captured = client.quotes(account_id, requested)
    finally:
        client.close()
        secret = account_id = None
    if _network_allowed(data) != policy:
        raise PublicCommandError('Owner network permission changed during collection; the response was not accepted.')
    return captured, requested


def _publish_capture(workspace, output, capture, requested):
    from ariadne.atomic import publish_bytes
    from ariadne.public_market_data import normalize_quotes
    if type(output) is not str or not output.startswith('reports/') or len(output) > 240:
        raise PublicCommandError('Use a NEW workspace-relative reports/<name> output directory.')
    destination = workspace._path(output)
    if destination.exists():
        raise FileExistsError('Prior Public output exists; inspect it instead of overwriting or replaying it.')
    normalized = normalize_quotes(capture['payload'], received_at=capture['received_at'], expected_instruments=requested)
    files = {'quotes.json': capture['payload'], 'normalized.json': _canonical(normalized)}
    receipt = {'schema': 1, 'kind': 'public_market_capture', 'status': 'completed',
               'received_at': capture['received_at'], 'transport': capture['transport'],
               'paper_fills': 0, 'funding_added': '0', 'live_feed_qualified': False,
               'normalization_authenticates_saved_files': False,
               'artifacts': [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in files.items()]}
    # Directory publication is exclusive; incomplete output has no completed receipt.
    with workspace._directory(destination.parent, create=True):
        destination.mkdir()
        with workspace._directory(destination):
            for name, raw in files.items():
                publish_bytes(destination / name, raw)
                if (destination / name).read_bytes() != raw:
                    raise PublicCommandError('Public capture readback failed; incomplete output was retained for inspection.')
            publish_bytes(destination / 'completed.json', _canonical(receipt))
    return {**receipt, 'output': output}


def run(args, *, root=ROOT, client_factory=None):
    from ariadne.public_credentials import save, load
    from ariadne.public_market_data import PublicMarketDataClient
    data, workspace = _guards(args, root)
    client_factory = PublicMarketDataClient if client_factory is None else client_factory
    fields = (args.instruments, args.request, args.supplement, args.output)
    if args.action in ('setup', 'accounts', 'status', 'binding') and any(v is not None for v in fields):
        raise PublicCommandError('This action does not accept input, request, supplement or output overrides.')
    if args.action in ('collect', 'paper') and not args.online:
        raise PublicCommandError('Public collection requires an explicit --online flag.')
    if args.action == 'setup':
        if args.online:
            raise PublicCommandError('Setup saves only local encrypted credentials and does not use --online.')
        secret = _hidden('Public API secret (hidden): ')
        account_id = _hidden('Public account ID (hidden): ')
        try:
            return save(data, secret, account_id)
        finally:
            secret = account_id = None
    if args.action == 'accounts':
        if not args.online:
            raise PublicCommandError('Account lookup requires --online.')
        policy = _network_allowed(data)
        try:
            secret, account_id = load(data)
        except FileNotFoundError:
            secret = _hidden('Public API secret (hidden, not saved): ')
        client = client_factory(secret, online=True)
        try:
            rows = client.accounts()
        finally:
            client.close()
            secret = None
        if _network_allowed(data) != policy:
            raise PublicCommandError('Owner network permission changed; account lookup was not accepted.')
        return {'accounts': rows, 'local_selection_only': True, 'saved': False}
    if args.online and args.action not in ('collect', 'paper'):
        raise PublicCommandError('--online is only for accounts, collect and paper.')
    if args.action == 'status':
        return _status(data)
    if args.action == 'collect':
        if not args.instruments or not args.output or args.request or args.supplement:
            raise PublicCommandError('Collect requires --instruments and --output only.')
        # Fail output/input preflight before credentials or network access.
        path = workspace._path(args.output)
        if not args.output.startswith('reports/') or len(args.output) > 240 or path.exists():
            raise PublicCommandError('Collect needs a new reports/<name> directory.')
        capture, requested = _collect(args, data, workspace, client_factory)
        return _publish_capture(workspace, args.output, capture, requested)
    if args.output or args.action not in ('binding', 'migrate', 'paper'):
        raise PublicCommandError('Paper operations publish through their protected account receipt; no --output override.')
    owner = _owner(args, data, workspace)
    if args.action == 'binding':
        with owner.database() as db:
            revision, _ = owner._meta(db)
            account = owner._account(db, datetime.now(timezone.utc))
            if not account:
                raise PublicCommandError('An existing verified paper account is required; no bankroll was created.')
            return {'expected_revision': revision, **owner.public_paper.binding(db, account)}
    if not args.request or args.action == 'migrate' and (args.instruments or args.supplement):
        raise PublicCommandError('Migration requires --request only; paper requires --request and --instruments.')
    body = _load(workspace, args.request)
    if args.action == 'migrate':
        return owner.public_paper.migrate(body)
    if not args.instruments:
        raise PublicCommandError('Paper requires --instruments for a fresh authenticated collection.')
    supplement = _load(workspace, args.supplement) if args.supplement else None
    # Validate policy, bindings and request before making an external request.
    owner.public_paper.preflight(body)
    capture, _ = _collect(args, data, workspace, client_factory)
    return owner.public_paper.execute(body, capture=capture, supplement=supplement)


def main(argv=None):
    args = parser().parse_args(argv)
    # Explicit online command controls this process only. Never override inherited offline=1.
    if args.online and os.environ.get('ARIADNE_OFFLINE') not in (None, '0'):
        print('Public operation refused: inherited offline policy is active.', file=sys.stderr)
        return 2
    os.environ['ARIADNE_OFFLINE'] = '0' if args.online else '1'
    from ariadne.network_policy import install_network_policy
    install_network_policy()
    try:
        result = run(args)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        from ariadne.public_market_data import PublicDataError
        from ariadne.handoff import HandoffConflict
        # Only controlled messages are printable; never echo raw OS/provider errors.
        reason = str(error)[:300] if isinstance(error, (PublicCommandError, PublicDataError, HandoffConflict)) else 'Check the documented local inputs and credential setup; no automatic retry.'
        print('Public operation refused: ' + reason, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
