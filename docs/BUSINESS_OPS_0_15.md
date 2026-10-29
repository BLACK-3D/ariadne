# Offline business operations · 0.15

`business_ops` adds three fixed, zero-model workflows: review current local stock/journal/cash records, age supplied ERPNext sales invoices, and turn local catalog prices into a checked quote and **draft-only** ERPNext-shaped invoice. It uses the bundled Python standard library and existing guarded workspace/database access. It adds no always-on ERP, library download, API charge or account connection.

This implements the scan's read-first ERP adapter and quote-to-draft direction. ERPNext itself is **not installed** by this feature. The adapter processes exported records offline and emits a review draft; it does not claim end-to-end compatibility with every ERPNext site's custom fields or accounting settings. Dolibarr and other ERP formats are not accepted by this version.

## Calls and output

All calls require `action` and `request_id` (1–64 letters, digits, underscores or hyphens). Reports are immutable single JSON artifacts at `business-ops/<request_id>.json`, containing input identities, calculation/result digest, generation time, assumptions and primary-contract links. The response returns artifact path, byte count and SHA-256 plus a compact summary. Use a new ID for new inputs. Reusing a completed ID only retrieves the same result if source bytes, arguments and local-record snapshot still match. Changed inputs or damaged reports are refused; existing files are never replaced.

Input files must be workspace-relative UTF-8 JSON, at most 256 KiB. Duplicate keys, nonfinite numbers, ambiguous paths and linked files are rejected. The maximum generated report is 100,000 UTF-8 bytes; narrow large exports when the limit is reached. No supplied content becomes code, a URL, a command or a network operation.

### Current local snapshot

```json
{"action":"snapshot","request_id":"business-review-001","cash_accounts":["Cash","Bank"]}
```

`cash_accounts` is optional, accepts up to twenty unique existing account IDs, and is never inferred from names. Without it, the cash result is unknown. The optional `as_of` snapshot label must be the current UTC date; the tool does not reconstruct historical or future local balances. The report includes exact trial-balance totals, recorded stock quantities, zero-stock items and an indicative stock value using current **selling prices**, not cost or profit. A single local-business read transaction binds contacts, items, stock and journal balances to a revision and digest. Currency remains unspecified because the pre-existing local ledger does not record currency metadata. These are operational records, not a bank reconciliation or assurance that money is available to spend.

### ERPNext exported-invoice ageing

```json
{"action":"audit_erp","request_id":"receivables-review-001","path":"imports/erpnext.json","as_of":"2026-10-29"}
```

Use a local envelope shaped like `config/examples/business-erpnext-demo.json`:

```json
{"schema":1,"provider":"erpnext","captured_at":"2026-10-29T00:00:00Z","currency":"USD","invoices":{"data":[{"name":"INV-001","customer":"Customer-001","posting_date":"2026-09-01","due_date":"2026-10-01","currency":"USD","docstatus":1,"grand_total":"100.01","outstanding_amount":"80.01"}]}}
```

The `data` wrapper and document fields follow Frappe REST exports. An operator must collect all intended pages with the required fields and preserve the captured records. The envelope, capture time and completeness are operator assertions; a file hash identifies bytes, not server authenticity. The supplied capture time must have a timezone and cannot be more than five minutes into the future. Every amount is a nonnegative exact decimal with at most two places; mixed currencies, returns/negative invoices, inconsistent outstanding amounts, duplicate invoice names and unsupported statuses are rejected. Up to 500 invoice rows are accepted, subject to the artifact-size bound.

Only `docstatus:1` invoices contribute to receivables and ageing; drafts (`0`) and cancellations (`2`) are counted separately. Age buckets use the explicit `as_of` date and due dates, with due-today invoices not overdue. The report preserves the snapshot capture time and age. Outstanding balances are the supplied snapshot's observations; changing `as_of` ages those same amounts and does not produce historical or future payment facts. Unrecognized native invoice fields remain inert and are omitted from the report.

The demonstration fixture contains four clearly named fictional invoices. At `as_of:2026-10-29`, expected submitted receivables are **8,030 cents**, of which **8,001 cents** are overdue in the 31–60 day bucket. The draft and cancelled invoices are excluded. Running the example creates no local customers, invoices, journal entries or stock movements.

### Quote to draft invoice

```json
{"action":"draft_quote","request_id":"quote-review-001","path":"quotes/request.json"}
```

The input format is shown in `config/examples/business-quote-demo.json`. Set `customer_id` and each `item_id` to actual existing **local** records. `company`, posting/due dates, `currency:"USD"`, tax basis points and (when tax applies) the tax-account ID must be explicitly supplied. `tax_bps:825` means 8.25%; this is a calculation instruction, not a tax-law determination. The optional per-line `discount_bps` uses the same scale. Lines require unique item IDs and positive integer quantities in their existing named stock units. At most 100 lines are accepted.

Prices come from the local catalog. The input's USD declaration is an explicit assumption that those cent prices are USD; there is no currency conversion. Discounts round half up to cents **per unit**, then multiply by quantity. Tax rounds half up once on the discounted subtotal. All money calculations use integers; monetary cent totals are returned as decimal strings so JavaScript cannot silently lose integer precision. A single line or final total may not exceed 10^15 cents.

The draft carries already-discounted, exact two-decimal unit rates and quantities. It does not also attach a discount percentage that could be applied twice. The report keeps the original price, requested discount and calculation ledger. The draft uses `doctype:"Sales Invoice"`, `docstatus:0` and `update_stock:0`. Stock shortages are explicit exceptions; no stock is reserved, deducted or promised. Match the target ERP's company/customer/item/UOM/account identifiers and reconcile its price, tax and precision settings before any separately approved import. Nothing here submits, sends, posts, pays or changes ERP/local ledger records.

## Source contracts and validation

Official contracts checked on 29 October 2026:

- [Frappe REST API](https://docs.frappe.io/framework/user/en/api/rest): resource/document representation and `data` response wrapper. This implementation consumes saved data; it contains no REST client or credentials.
- [Frappe document status](https://docs.frappe.io/framework/doctypes/docstatus): the draft/submitted/cancelled distinction used by the audit and draft.
- [ERPNext sales invoice](https://docs.frappe.io/erpnext/sales-invoice): customer/company, dates, quantity/UOM/rate, tax-account and stock-update concepts. Site rules can change calculated values, which is why generated output stays a review draft.

Tests cover exact rounding/reconciliation, ageing boundaries, excluded drafts/cancellations, source/ledger input binding, immutable replay, damaged-output refusal, cancellation, file/link boundaries and operation with DNS, sockets and subprocess creation denied. There is no live ERP connection or import claim in those tests.
