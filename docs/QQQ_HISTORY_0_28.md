# QQQ source-history review and recovery

The daily recovery workflow retains an immutable failed request when newly collected economic history disagrees with accepted history. Version 0.28 adds an offline review and a separately recorded resolution for changes confined to diagnostic fields. It does not replay, overwrite or delete the failed request, reset an account, infer a fill or add simulated funding.

## What can change

For the exact reviewed QQQ recovery rule and implementation, volume and adjusted close are diagnostic fields. Signals, next-open fills, cash distributions, splits and equity use the preserved dates and unadjusted economic fields. The policy checks both the frozen rule digest and the actual recovery implementation's source digest. A changed implementation must requalify this claim.

Overlapping dates, open/high/low/close prices, cash dividends, split ratios and dataset basis remain exact. No numeric epsilon, rounding allowance, forward fill or price-revision acceptance is introduced. A protected change, truncated history, missing session, stale/future source, changed permission epoch or unrelated uncertain request still blocks acceptance. Future eligible volume revisions are recorded with before/after values and a digest; larger records use quota-charged immutable evidence.

## Review a retained candidate

The candidate must be a complete saved bundle from the fixed QQQ/SPY collector. Its normalized values are rebuilt offline from both retained raw chart responses. Exact source URLs, clocks, hashes, calendar, complete history and source-quality metadata must agree. Collection must fit the existing 70-second worker window, and accepted completed bars cannot have source timestamps after the fixed request cutoff. Hashing unrelated raw bytes is insufficient. These checks establish internal consistency, not independent authentication of provider data.

Copy the verified candidate into Ariadne Workspace, then use the bundled runtime. These placeholders must be replaced with actual paths and digests:

```powershell
runtime\python\python.exe -I -B scripts\qqq_history.py review --candidate review/qqq/candidate-bundle.json --candidate-sha256 EXACT_BUNDLE_SHA256 --failed-request-id EXACT_FAILED_REQUEST_ID --output staging/qqq-review-001.json
```

Review opens only existing databases in read-only mode and does not initialize settings or run a collector. It returns the exact accepted-data, failure, account, event, authority, comparison and code identities needed by a new resolution request. Economic eligibility does not override a pause or grant permission to mutate state.

## Resolve explicitly

After inspecting a complete eligible review, use its exact file digest and a new request ID:

```powershell
runtime\python\python.exe -I -B scripts\qqq_history.py resolve --review staging/qqq-review-001.json --review-sha256 EXACT_REVIEW_SHA256 --request-id NEW_UNIQUE_RESOLUTION_ID --output staging/qqq-resolution-001.json
```

The corresponding owner-authenticated API is `POST /api/qqq/resolve-history`. It accepts the complete source-bound body produced by review; it does not fetch a candidate or supply missing approval fields. Standard host, authentication, same-origin and pause controls apply.

Resolution validates every current identity again, reserves a new receipt, retains the accepted-before dataset, full candidate bundle, comparison, failed receipt and account/authority binding, and rechecks every published artifact before the final commit. A separate resolution link releases only the matching failed request. Its original status and bytes remain unchanged. Missing or changed resolution evidence restores a visible hold; it is never regenerated automatically.

The operation updates accepted source data only. It leaves the paper account ID, state, policy and events unchanged. A newly accepted completed session can be processed later by the ordinary permitted forward workflow. The failed daily ID continues to own its original day; resolution does not replay that day. The unrelated Ford options account and its shared $500 monthly commitment cap are untouched.

The QQQ panel shows the completed review and retained evidence. The existing daily brief reads the current workflow status on its next normal run; an already completed brief remains immutable.

## Failure handling and offline use

The CLI blocks network, model connections and child processes. A review or resolution output path is exclusive. If execution or delivery is interrupted, inspect the durable service receipt and retained artifacts before considering another operation; never rerun an uncertain request to recover a missing console response. An exhausted evidence quota or tampered artifact holds work without deleting history.

Saved charts and source metadata support private research. Structural verification does not authenticate Yahoo's prices, guarantee its availability or establish future profitability. No brokerage operation is added.
