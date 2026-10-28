# Survival and rebuilding workspace

The Survival navigation item runs locally using the existing server, with no added dependency or hosted account. The owner can edit household planning assumptions, daily electrical loads, dated supplies and projects. All quantities are owner reports; neither expiry dates nor a coverage calculation certify an item safe. Default calorie values are editable planning assumptions, not personal dietary advice. Water storage uses the dated [CDC baseline](https://www.cdc.gov/water-emergency/about/how-to-create-and-store-an-emergency-water-supply.html).

Water, food energy and electrical energy keep distinct units. The power estimate applies the entered usable fraction and conversion efficiency once. It does not simulate startup surges, battery chemistry, renewable weather, generators or fuel consumption. Unknown demand produces unknown runtime rather than an infinite result. Expired stock is excluded from arithmetic; unknown expiry remains explicitly unverified owner-reported stock.

Projects support explicit prerequisites and reject missing dependencies and cycles. The original 16-domain rebuilding roadmap creates planning records atomically without replacing existing records. A completed project status remains an owner report, not evidence that a physical operation happened. The roadmap is an independent planning aid; it is not a reconstruction of a commercial book's chapter content.

## Lewis Dartnell's book

The requested author is Lewis Dartnell. The correct work is [The Knowledge: How to Rebuild Our World from Scratch](https://the-knowledge.org/en-gb/the-book/), with subtitles and pagination varying by edition. Its full commercial text is **not bundled or claimed implemented**. An owner-supplied local copy is needed to index that edition privately. No DRM removal or third-party book download is performed.

1. In Knowledge, register the folder containing your lawful unencrypted PDF or EPUB. Source-folder guards exclude E:, links, hidden/private directories and credential-like names.
2. In Survival → Library, select that registered folder, enter the relative filename and choose Review file. This reads at most 128 MiB and records its SHA-256; the original is never changed.
3. Review the file identity and import. A changed file is refused. The fixed reader runs with a 1 GiB process memory ceiling, 90 seconds of user CPU and 120 seconds of wall time. It accepts up to 2,000 PDF pages/EPUB text sections, at most 100,000 characters per section and 12 million characters per book. Exceeding a limit fails the whole import rather than saving a truncated book. EPUB archive expansion has additional bounded ZIP/XML limits.
4. Search the private book and open exact numbered sections. All extracted sections are committed in one transaction and cited with book title, section locator and file SHA-256. Private text remains in `data/books.sqlite3`, excluded from factory exports.

`processed_all_sections` records whether every section was processed. The compatibility field `coverage_complete` measures only whether each section contains some alphanumeric text; `coverage_metric` states this explicitly. It **does not establish complete content extraction**. Missing text pages are listed. Images, diagrams, equations in graphical form, layout and embedded media are not interpreted. Scanned or partially scanned books need separate OCR and visual review. A text layer containing only a page number can still hide substantial image content. Do not infer otherwise from the section count.

The importer stores extracted text and provenance, not a replacement for the original book. Keep the original edition and your private data backups available offline. The private book library is bounded to 100 books and 100 million extracted characters. Model reads are paginated to 8,000 characters; the owner reader can inspect a full bounded section. Source text remains untrusted reference data and cannot grant tool authority.

## Official references

The factory corpus contains 16 original planning cards, five unchanged official PDFs (36 pages), and five reference-only source entries. Full text from all 21 bundled files is indexed into 52 sections without startup downloads. Search/read verify the catalog, text-index manifest and actual source hashes. Dates and source locations remain attached; references are snapshots rather than live local alerts. Exact rights, agency notices and third-party exclusions are in [SURVIVAL-SOURCES.md](SURVIVAL-SOURCES.md).

## State and authority

Readiness records use a separate guarded SQLite database with durable idempotency keys and revisions. Owner saves reject stale revisions with HTTP 409. Model writes require the existing approval flow and bind the records revision. Authenticated owner form submissions are direct owner actions. Reference search and pure arithmetic are automatic. No survival feature controls equipment, initiates purchases, contacts people or changes external systems.

The existing launch command still starts the local application and owned services. The complete 0.4 release ZIP remains immutable; 0.5 development additions and their qualification are separate. Tests use generated books and isolated data, never the absent commercial book.
