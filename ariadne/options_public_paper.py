"""Public market-data paper workflow over the one retained options account.

No credentials/network implementation lives here. Only a same-invocation
Public quote capture may enter execute; saved files are not replayed as live.
"""
from copy import deepcopy
from datetime import datetime

from . import options_public_engine as engine
from . import public_market_data as source
from .handoff import HandoffConflict, _load, _identifier
from .locking import InstanceLock
from .options_workflow import _json, _digest, _now, MAX_EVENTS

BINDINGS = {'expected_account_id','expected_state_sha256','expected_source_sha256','expected_owner_epoch'}
MIGRATION_FILES = {'previous-state.json','initial-state.json','source-cutoff.json','activation.json'}
FRAME_FILES = {'before-state.json','capture.json','input.json','paper.json'}
MAX_FRAME_BYTES = 4*1024*1024


def raw(value):
    return _json(value).encode()


def _basis(account):
    return {k:account[k] for k in ('id','state','started_at','policy')}


class OptionsPublicPaper:
    def __init__(self, owner):
        self.owner = owner

    @staticmethod
    def _exists(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opt_public_migrations'").fetchone() is not None

    @staticmethod
    def _schema(db):
        db.execute('CREATE TABLE IF NOT EXISTS opt_public_migrations(account_id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,previous_state TEXT NOT NULL,previous_state_sha256 TEXT NOT NULL,initial_state TEXT NOT NULL,source_cutoff TEXT NOT NULL,policy TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS opt_public_state(account_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,state_sha256 TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS opt_public_frames(account_id TEXT NOT NULL,frame_number INTEGER NOT NULL,request_id TEXT UNIQUE NOT NULL,state_sha256 TEXT NOT NULL,PRIMARY KEY(account_id,frame_number))')

    def cutoff(self, db, account_id):
        rows = db.execute('SELECT source_date,digest,bytes,request_id FROM opt_bankroll_data ORDER BY source_date LIMIT 101').fetchall()
        if len(rows) > 100:
            raise ValueError('Original bankroll source cutoff exceeds its retention bound.')
        anchor = db.execute('SELECT * FROM opt_bankroll_state WHERE account_id=?',(account_id,)).fetchone()
        if anchor is None:
            raise ValueError('Original bankroll anchor is missing.')
        value = {'schema':1,'account_id':account_id,'old_cutoff':self.owner.bankroll.cutoff(db,account_id),
            'bankroll_sources':[dict(r) for r in rows],'bankroll_anchor':dict(anchor),
            'root_config':self.owner.root_eod.config(db),'entry_controls':self.owner.bankroll._entry_controls(db,account_id)}
        if len(raw(value)) > 512*1024:
            raise ValueError('Original source cutoff exceeds its byte bound.')
        return value

    def binding(self, db, account):
        if not account or account['state'].get('schema') not in (6,7):
            raise HandoffConflict('Use the one existing25000 bankroll; no new account is created.')
        row = db.execute('SELECT state FROM opt_accounts WHERE id=? AND closed_at IS NULL',(account['id'],)).fetchone()
        if not row or _load(row[0]) != account['state']:
            raise HandoffConflict('The shared account changed before approval binding.')
        cfg, brief = self.owner._policy(db)
        return {'expected_account_id':account['id'],'expected_state_sha256':_digest(row[0].encode()),
            'expected_source_sha256':_digest(raw(self.cutoff(db,account['id']))),
            'expected_owner_epoch':self.owner._epoch(db,cfg,brief)}

    def _bind(self, db, body, account, cfg, brief):
        epoch = body['expected_owner_epoch']
        if (type(epoch) is not dict or set(epoch) != {'global_pause_rowid','options_revision','brief_revision'}
                or any(type(epoch[k]) is not int or epoch[k] < 0 for k in ('options_revision','brief_revision'))
                or epoch['global_pause_rowid'] is not None and (type(epoch['global_pause_rowid']) is not int or epoch['global_pause_rowid'] < 0)):
            raise ValueError('Use exact owner epoch integer bindings, never booleans.')
        if account['policy'] != self.owner._epoch(db,cfg,brief) or self.binding(db,account) != {k:body[k] for k in BINDINGS}:
            raise HandoffConflict('Approved account, source cutoff or owner permission epoch changed.')

    def preflight(self, body):
        owner = self.owner; owner._arguments(body,BINDINGS|{'requests'})
        if type(body['requests']) is not list or not 1 <= len(body['requests']) <= 128:
            raise ValueError('Use a bounded list of explicit paper requests.')
        ids = set(); at = _now()
        for row in body['requests']:
            if type(row) is not dict or set(row) != {'request_id','contract_id','side','quantity','price_limit','allow_partial'}:
                raise ValueError('Use exact paper request fields; caller decision clocks are refused.')
            engine.gate._request({**row,'source_id':'public','snapshot_id':'preflight','decision_at':at.isoformat()})
            if row['request_id'] in ids:
                raise ValueError('Duplicate paper request identifier.')
            ids.add(row['request_id'])
        with owner.database() as db:
            cfg,brief = owner._policy(db); account = owner._account(db,at)
            if not account or account['state'].get('schema') != 7:
                raise HandoffConflict('Explicit same-account Public migration is required before a live paper collection.')
            self._bind(db,body,account,cfg,brief)
            if db.execute("SELECT 1 FROM opt_runs WHERE status IN ('running','failed') LIMIT 1").fetchone():
                raise HandoffConflict('Uncertain options work requires inspection before a new Public request.')
            self.verify_account(db,account['id'],account['state'],full=True)
        return {'ready':True,'account_id':account['id'],'funding_added':'0'}

    def migrate(self, body, now=None):
        # An older running server retains imported schema6 dispatch. Hold the
        # same lifetime lock as Ariadne through the complete schema change so
        # neither an existing nor concurrently starting server can see schema7.
        guard = InstanceLock(self.owner.registry.settings.data_dir / 'server.lock')
        try:
            guard.acquire()
        except ValueError:
            raise HandoffConflict('Stop Ariadne before migrating the Public paper schema; the server lifetime lock is unavailable. No migration was reserved.') from None
        try:
            return self._migrate_locked(body,now)
        finally:
            guard.release()

    def _migrate_locked(self, body, now=None):
        owner = self.owner
        owner._arguments(body,BINDINGS|{'acknowledge_simulated','acknowledge_no_topups'})
        if body['acknowledge_simulated'] is not True or body['acknowledge_no_topups'] is not True:
            raise ValueError('Explicitly acknowledge simulation and no added capital.')
        at, receipt = _now(now), None
        with owner._ownership():
            try:
                with owner.database() as db:
                    db.execute('BEGIN IMMEDIATE'); cfg,brief = owner._policy(db)
                    replay = owner._reserve(db,'public_migrate',body,None,at)
                    if replay:
                        return replay
                    receipt = {'request_id':body['request_id'],'action':'public_migrate','status':'running','created_at':at.isoformat(),
                        'artifacts':[],'model_calls':0,'replayed':False,'rule_sha256':engine.RULE_SHA256}
                    self._schema(db); account = owner._account(db,at)
                    if not account or account['state'].get('schema') != 6 or db.execute('SELECT 1 FROM opt_public_migrations LIMIT 1').fetchone():
                        raise HandoffConflict('Upgrade only the original healthy flat schema6 account once.')
                    self._bind(db,body,account,cfg,brief)
                    owner.bankroll.verify_account(db,account['id'],account['state'],full=True)
                    previous_raw = db.execute('SELECT state FROM opt_accounts WHERE id=?',(account['id'],)).fetchone()[0].encode()
                    cutoff_raw = raw(self.cutoff(db,account['id']))
                initial = engine.migrate_flat(account['state'],activated_at=at.isoformat(),previous_state_sha256=_digest(previous_raw),source_cutoff_sha256=_digest(cutoff_raw))
                initial_raw = raw(initial)
                activation = {'schema':1,'account_id':account['id'],'activated_at':at.isoformat(),'simulated_funding_added':'0',
                    'previous_state_sha256':_digest(previous_raw),'source_cutoff_sha256':_digest(cutoff_raw),'state_sha256':_digest(initial_raw),
                    'policy':account['policy'],'no_automatic_topups':True,'rule_sha256':engine.RULE_SHA256,'paper_only':True}
                receipt.update(account_id=account['id'],state_sha256=_digest(initial_raw),simulated_funding_added='0',no_automatic_topups=True)
                def final(db):
                    current = owner._account(db,at)
                    if _basis(current) != _basis(account) or raw(self.cutoff(db,account['id'])) != cutoff_raw:
                        raise HandoffConflict('Original shared account/source changed before Public migration.')
                    self._bind(db,body,current,cfg,brief); owner.bankroll.verify_account(db,account['id'],current['state'],full=True)
                    self.verify(receipt)
                    db.execute('INSERT INTO opt_public_migrations VALUES(?,?,?,?,?,?,?)',(account['id'],body['request_id'],previous_raw.decode(),_digest(previous_raw),initial_raw.decode(),cutoff_raw.decode(),_json(account['policy'])))
                    db.execute('INSERT INTO opt_public_state VALUES(?,?,?)',(account['id'],body['request_id'],_digest(initial_raw)))
                    db.execute('UPDATE opt_accounts SET state=? WHERE id=?',(initial_raw.decode(),account['id']))
                owner._publish(cfg,brief,receipt,(('previous-state.json',previous_raw),('initial-state.json',initial_raw),('source-cutoff.json',cutoff_raw),('activation.json',raw(activation))),final=final)
                return receipt
            except Exception as error:
                owner._failed(receipt,error)
                raise

    @staticmethod
    def supplement(normalized, supplement):
        books = deepcopy(normalized['books'])
        if supplement is None:
            return books
        if (type(supplement) is not dict or set(supplement) != {'schema','contracts'} or type(supplement['schema']) is not int
                or supplement['schema'] != 1
                or type(supplement['contracts']) is not list or len(supplement['contracts']) > 128):
            raise ValueError('Supplement must use the documented bounded independent metadata template.')
        lookup = {b['contract_id']:b for b in books}; seen = set()
        for item in supplement['contracts']:
            if type(item) is not dict or set(item) != {'contract_id','underlying_type','standard_deliverable_verified','deliverable_verification_source','underlying_activity','contract_activity','open_interest'}:
                raise ValueError('Use exact documented supplemental evidence fields.')
            contract = item['contract_id']
            if contract not in lookup or contract in seen:
                raise ValueError('Supplement must reference unique actual collected contracts.')
            seen.add(contract); book = lookup[contract]
            observation = next(o for o in normalized['observations'] if o['instrument'] == {'symbol':contract,'type':'OPTION'})
            if item['contract_activity'] is not None and item['contract_activity'].get('volume') != observation['volume']:
                raise ValueError('Supplement cannot replace actual Public contract volume.')
            if item['open_interest'] is not None and item['open_interest'].get('value') != observation['open_interest']:
                raise ValueError('Supplement cannot replace actual Public open interest.')
            book.update({k:deepcopy(v) for k,v in item.items() if k != 'contract_id'})
            engine.gate._book(book)
        return books

    def execute(self, body, *, capture, supplement=None, now=None):
        """Collect-entrypoint only: do not adopt a saved capture as a live clock."""
        owner = self.owner; owner._arguments(body,BINDINGS|{'requests'})
        if type(capture) is not dict or set(capture) != {'payload','received_at','transport'}:
            raise ValueError('Use an actual same-invocation Public client quote capture.')
        if capture['transport'] != {'provider_authenticated':True,'source_id':'public','operation':'quotes'}:
            raise ValueError('A normalized/imported observation cannot assert authenticated transport.')
        payload = capture['payload']
        if type(payload) is not bytes or len(payload) > MAX_FRAME_BYTES:
            raise ValueError('Bounded actual quote bytes required.')
        at,receipt = _now(now),None
        normalized = source.normalize_quotes(payload,received_at=capture['received_at'])
        books = self.supplement(normalized,supplement)
        if engine.gate._clock(normalized['received_at']) > at:
            raise ValueError('Quote receipt clock cannot be later than the actual decision.')
        with owner._ownership():
            try:
                with owner.database() as db:
                    db.execute('BEGIN IMMEDIATE');cfg,brief = owner._policy(db)
                    replay = owner._reserve(db,'public_paper',body,None,at)
                    if replay:
                        return replay
                    receipt = {'request_id':body['request_id'],'action':'public_paper','status':'running','created_at':at.isoformat(),
                        'artifacts':[],'model_calls':0,'replayed':False,'rule_sha256':engine.RULE_SHA256}
                    account = owner._account(db,at)
                    if not account or account['state'].get('schema') != 7:
                        raise HandoffConflict('Explicit flat same-account Public migration is required before paper fills.')
                    self._bind(db,body,account,cfg,brief); self.verify_account(db,account['id'],account['state'],full=True)
                paper = engine.advance(account['state'],books,body['requests'],normalized['observations'],decided_at=at.isoformat())
                after_raw = raw(paper['state']); before_raw = raw(account['state'])
                inputs = {'schema':1,'books':books,'observations':normalized['observations'],'requests':body['requests'],
                    'decided_at':at.isoformat(),'supplement':supplement,'supplement_basis':'Owner-provided separate metadata; Public transport authenticates quote bytes only.'}
                receipt.update(account_id=account['id'],state_sha256=_digest(after_raw),capture_sha256=normalized['raw_sha256'],
                    source_received_at=normalized['received_at'],paper_fills=sum(p['status']=='paper_fill' for p in paper['proposals']),
                    simulated_funding_added='0',no_automatic_topups=True,execution_confirmed=False,provider_authenticated=True,
                    metadata_provider_authenticated=False)
                def final(db):
                    current = owner._account(db,at)
                    if _basis(current) != _basis(account):
                        raise HandoffConflict('Shared account changed before paper commit.')
                    self._bind(db,body,current,cfg,brief);self.verify_account(db,account['id'],current['state'],full=True);self.verify(receipt)
                    if db.execute('SELECT count(*) FROM opt_events').fetchone()[0]+len(paper['events']) > MAX_EVENTS:
                        raise ValueError('The shared event quota is full.')
                    db.execute('UPDATE opt_accounts SET state=? WHERE id=?',(after_raw.decode(),account['id']))
                    db.execute('UPDATE opt_public_state SET request_id=?,state_sha256=? WHERE account_id=?',(body['request_id'],_digest(after_raw),account['id']))
                    db.execute('INSERT INTO opt_public_frames VALUES(?,?,?,?)',(account['id'],paper['state']['processed_count'],body['request_id'],_digest(after_raw)))
                    for event in paper['events']:
                        db.execute('INSERT INTO opt_events(account_id,run_id,value) VALUES(?,?,?)',(account['id'],body['request_id'],_json(event)))
                owner._publish(cfg,brief,receipt,(('before-state.json',before_raw),('capture.json',payload),('input.json',raw(inputs)),('paper.json',raw(paper))),final=final)
                return receipt
            except Exception as error:
                owner._failed(receipt,error)
                raise

    def verify(self, receipt):
        action = receipt.get('action'); names = MIGRATION_FILES if action == 'public_migrate' else FRAME_FILES if action == 'public_paper' else None
        if names is None or receipt.get('rule_sha256') != engine.RULE_SHA256:
            raise ValueError('Unknown Public paper receipt contract.')
        base = 'reports/options-recovery/'+_identifier(receipt['request_id'])+'/'
        artifacts = receipt.get('artifacts')
        if type(artifacts) is not list or len(artifacts) != len(names) or {r.get('path') for r in artifacts} != {base+n for n in names}:
            raise ValueError('Public paper receipt artifact set changed.')
        saved = {}
        for row in artifacts:
            data = self.owner._read(row['path'],min(row['bytes']+1,MAX_FRAME_BYTES+1))
            if len(data) != row['bytes'] or len(data) > MAX_FRAME_BYTES or _digest(data) != row['sha256']:
                raise ValueError('Public paper evidence failed hash/byte validation.')
            saved[row['path'][len(base):]] = data
        if action == 'public_migrate':
            previous = _load(saved['previous-state.json']); activation = _load(saved['activation.json'])
            initial = engine.migrate_flat(previous,activated_at=activation['activated_at'],previous_state_sha256=_digest(saved['previous-state.json']),source_cutoff_sha256=_digest(saved['source-cutoff.json']))
            if raw(initial) != saved['initial-state.json'] or activation['state_sha256'] != _digest(saved['initial-state.json']) or receipt['state_sha256'] != activation['state_sha256']:
                raise ValueError('Public migration cannot reconstruct its original same-account state.')
        else:
            if _digest(saved['capture.json']) != receipt['capture_sha256']:
                raise ValueError('Public frame lost its actual capture.')
            inputs = _load(saved['input.json']); normalized = source.normalize_quotes(saved['capture.json'],received_at=receipt['source_received_at'])
            if inputs['books'] != self.supplement(normalized,inputs['supplement']) or inputs['observations'] != normalized['observations']:
                raise ValueError('Paper inputs differ from normalized actual quote bytes and bound metadata.')
            paper = engine.advance(_load(saved['before-state.json']),inputs['books'],inputs['requests'],inputs['observations'],decided_at=inputs['decided_at'])
            if raw(paper) != saved['paper.json'] or receipt['state_sha256'] != _digest(raw(paper['state'])):
                raise ValueError('Paper frame differs from its sealed deterministic transition.')
        return saved

    def verify_account(self, db, account_id, state, *, full=False):
        state = engine.validate_state(state)
        if not self._exists(db):
            raise ValueError('Public account migration witness is missing.')
        row = db.execute('SELECT * FROM opt_public_migrations WHERE account_id=?',(account_id,)).fetchone()
        identity = db.execute('SELECT started_at,policy FROM opt_accounts WHERE id=? AND closed_at IS NULL',(account_id,)).fetchone()
        if not row or not identity or row['previous_state_sha256'] != _digest(row['previous_state'].encode()) or _load(row['previous_state']) != state['ancestor'] or identity['started_at'] != state['started_at'] or identity['policy'] != row['policy']:
            raise ValueError('Public paper lost its original same-account ancestry.')
        if row['source_cutoff'] != _json(self.cutoff(db,account_id)) or _digest(row['source_cutoff'].encode()) != state['activation']['source_cutoff_sha256']:
            raise ValueError('The sealed original paper source cutoff changed.')
        self.owner.bankroll.verify_account(db,account_id,state['ancestor'],full=full)
        initial = _load(row['initial_state']); engine.validate_state(initial)
        frames = db.execute('SELECT * FROM opt_public_frames WHERE account_id=? ORDER BY frame_number LIMIT 101',(account_id,)).fetchall()
        if len(frames) != state['processed_count'] or len(frames) > engine.MAX_FRAMES:
            raise ValueError('Public paper frame count changed.')
        previous = initial
        migration_receipt = self.owner.eod._recorded(db,row['request_id'])
        migration = self.verify(migration_receipt)
        if migration['previous-state.json'] != row['previous_state'].encode() or migration['initial-state.json'] != row['initial_state'].encode():
            raise ValueError('Migration table differs from its sealed artifacts.')
        for number,frame in enumerate(frames,1):
            if frame['frame_number'] != number:
                raise ValueError('Public frame prefix is discontinuous.')
            receipt = self.owner.eod._recorded(db,frame['request_id'])
            if receipt.get('account_id') != account_id or receipt.get('state_sha256') != frame['state_sha256']:
                raise ValueError('Public frame receipt lost its shared account state.')
            saved = self.verify(receipt)
            if _load(saved['before-state.json']) != previous:
                raise ValueError('Public paper frames do not form one immutable financial chain.')
            previous = _load(saved['paper.json'])['state']
        anchor = db.execute('SELECT * FROM opt_public_state WHERE account_id=?',(account_id,)).fetchone()
        if not anchor or anchor['state_sha256'] != _digest(raw(state)) or previous != state or anchor['request_id'] != (frames[-1]['request_id'] if frames else row['request_id']):
            raise ValueError('Public paper state differs from its completed prefix.')
        return {'frames':len(frames),'contributions_added':'0','same_account':True}

    def snapshot(self, db, account):
        if not account or account['state'].get('schema') != 7:
            return {'active':False,'migration_required':True,'rule':engine.RULE,'rule_sha256':engine.RULE_SHA256,
                'scope':'Public capture is available separately; existing schema6 account is unchanged until explicit safe migration.'}
        return {'active':True,'migration_required':False,'rule':engine.RULE,'rule_sha256':engine.RULE_SHA256,
            'summary':engine.summarize(account['state']),'no_automatic_topups':True,'paper_only':True}
