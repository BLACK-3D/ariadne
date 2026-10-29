"""Inspectable capabilities and local starter workflows; no plugin auto-install."""
import os
import shutil
from pathlib import Path

from .documents import document_capabilities
from .engine import INTERNAL


STARTERS = [
    {"name": "Business planning kit", "prompt": "Create a local starter planning kit for a small service business. State your assumptions and make this a draft requiring owner review before any spending or public launch. Save business/brief.md (customer, offer, assumptions), business/operations.md (repeatable delivery checklist), and business/budget.csv (columns item,quantity,unit_cost with sensible illustrative values). Use file/table tools to save real files and read them back. Do not contact anyone, create accounts, spend money or deploy anything.",
     "checks": [{"kind": "file_exists", "path": "business/brief.md"}, {"kind": "file_exists", "path": "business/operations.md"}, {"kind": "csv_columns", "path": "business/budget.csv", "columns": ["item", "quantity", "unit_cost"]}]},
    {"name": "System blueprint", "prompt": "Design a practical local inventory tracking system. Save system/architecture.md with components, data flow, constraints and a step-by-step build plan. Save system/backlog.json as a valid JSON array of task objects with id,title,acceptance fields. Use the actual file tools. Keep it deployable on a local server with no mandatory cloud dependency. Report concrete saved paths and remaining implementation work; do not claim that the drafted system is deployed.",
     "checks": [{"kind": "file_exists", "path": "system/architecture.md"}, {"kind": "json_valid", "path": "system/backlog.json"}]},
    {"name": "Local knowledge review", "prompt": "Search local knowledge for business and system operating notes. Summarize useful evidence, contradictions, missing information and next actions in reviews/knowledge-review.md. Cite the source titles and available hashes/versions. If no relevant documents exist, record that limitation instead of inventing evidence. Save and read back the actual file. Do not perform external actions.",
     "checks": [{"kind": "file_exists", "path": "reviews/knowledge-review.md"}]},
]

EXPANDED_STARTERS = [
    {"name": "Workshop document pack", "prompt": "Create an illustrative local workshop planning pack. State that all quantities and prices are assumptions. Use office_create to produce workshop/brief.docx with a practical operations checklist and workshop/budget.xlsx with a typed numeric item/quantity/unit-price table and a fixed sum_product total. Use production_optimize for a small explicit assignment problem and save its actual result to workshop/assignment.json. Inspect the created artifacts. Request the existing approvals for artifact production. Do not post business ledger records, spend money, contact anyone or deploy services.",
     "checks": [{"kind": "file_exists", "path": "workshop/brief.docx"}, {"kind": "file_exists", "path": "workshop/budget.xlsx"}, {"kind": "json_valid", "path": "workshop/assignment.json"}]},
    {"name": "Offline reference brief", "prompt": "Use library_search and library_read to research SQLite transaction handling in the installed offline reference library. Save references/sqlite-transactions.md with a concise practical explanation, archive title/date, article paths and available content hashes. Identify any limits of the dated snapshot. Read the resulting file back. Do not contact an online service or execute examples from retrieved text.",
     "checks": [{"kind": "file_exists", "path": "references/sqlite-transactions.md"}, {"kind": "file_contains", "path": "references/sqlite-transactions.md", "text": "SQLite"}]},
    {"name": "Parametric mounting plate", "prompt": "Use cad_part to create a local four-hole mounting plate with explicit millimeter dimensions length100,width60,height5, exported as engineering/mounting-plate.step. Request the existing approval. Save engineering/mounting-plate.md with actual returned geometry checks and template limitations. This is an illustrative design for review; do not fabricate, purchase or control hardware.",
     "checks": [{"kind": "file_exists", "path": "engineering/mounting-plate.step"}, {"kind": "file_exists", "path": "engineering/mounting-plate.md"}]},
]


SURVIVAL_STARTERS = [
    {"name": "Preparedness evidence review", "prompt": "Review this owner's actual preparedness data using survival_records/list and survival_reference/search/read_reference. Use deterministic survival_plan values for arithmetic. Select at most three relevant dated source sections and cite their titles, exact page/section numbers and SHA-256 identities. Save preparedness/evidence-review.md with observed reserves, assumptions, gaps, contradictions and owner decisions. Save preparedness/evidence.json with the actual calculation and source locators. If no owner book is imported, state that Lewis Dartnell's The Knowledge was not available; do not invent its contents. Inventory dates do not establish safety. Do not execute physical tasks, prescribe treatments, contact anyone or control equipment. Read back the files.",
     "checks": [{"kind": "file_exists", "path": "preparedness/evidence-review.md"}, {"kind": "json_valid", "path": "preparedness/evidence.json"}]},
    {"name": "Continuity tabletop exercise", "prompt": "Create a local tabletop exercise draft for a 24-hour utility outage using the actual survival profile and available reference sections. Save continuity/tabletop.md with clearly fictional scenario injects, role assignments left for owner completion, dependency checks, communications alternatives, and observable exercise acceptance criteria. Save continuity/after-action.csv with columns observation,evidence,owner,next_action. Cite dated sources. Clearly distinguish a draft exercise from an emergency alert, an executed drill, or measured equipment capacity. Never operate utilities or hardware, give wiring instructions, contact people or create purchases. Read back the actual saved artifacts.",
     "checks": [{"kind": "file_exists", "path": "continuity/tabletop.md"}, {"kind": "csv_columns", "path": "continuity/after-action.csv", "columns": ["observation", "evidence", "owner", "next_action"]}]},
    {"name": "Business operations handoff", "prompt": "Inspect the current local business inventory and trial balance using the automatic read actions of business_records. Create operations/handoff.md documenting observed stock, reconciliation questions, essential operating functions, responsibilities left for owner assignment, and a restart checklist. Save operations/observed-records.json containing the actual bounded read results. Mark missing data explicitly. Do not infer facts about contacts, movements or records you did not read. Use authoritative server UTC from the system context for generation time; never invent a timestamp or imply it is a source observation date. Do not invent financial facts, modify ledger records, certify accounts, contact people, spend money or deploy services. Use real file tools and read the output back.",
     "checks": [{"kind": "file_exists", "path": "operations/handoff.md"}, {"kind": "json_valid", "path": "operations/observed-records.json"}]},
]


def seed_workflows(store):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        import json
        from .store import ident, now
        for key, items in (("starter_workflows_0_2", STARTERS), ("starter_workflows_0_4", EXPANDED_STARTERS), ("starter_workflows_0_5", SURVIVAL_STARTERS)):
            if db.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone():
                continue
            for item in items:
                if db.execute("SELECT count(*) FROM workflows").fetchone()[0] >= 100:
                    break
                db.execute("INSERT INTO workflows(id,name,prompt,checks,created_at,agent_id) VALUES (?,?,?,?,?,?)", (ident(), item["name"], item["prompt"], json.dumps(item["checks"]), now(), item.get("agent_id", "general")))
            db.execute("INSERT INTO settings VALUES (?, 'true')", (key,))


def catalog(app):
    engine, settings = app.engine, app.settings
    schemas = engine.tools.schemas() + INTERNAL
    mcp_descriptions = {d["name"]: d for d in engine.tools.mcp.descriptions()}
    tools = []
    for schema in schemas:
        function = schema["function"]
        name = function["name"]
        approval = not mcp_descriptions[name]["read_only"] if name in mcp_descriptions else name not in {s["function"]["name"] for s in INTERNAL} and engine.tools.policy(name, {}) == "approval"
        tools.append({"name": name, "description": function["description"], "parameters": function["parameters"], "approval_required": approval})
    def group(identifier, name, description, category, names, ready=True, offline=True, details=None):
        selected = [t for t in tools if t["name"] in names]
        return {"id": identifier, "name": name, "description": description, "category": category,
                "status": "ready" if ready else "needs_setup", "offline": offline,
                "approval_required": any(t["approval_required"] for t in selected), "tools": selected, "details": details}
    documents = document_capabilities()
    from .ocr import ocr_status
    ocr = ocr_status(settings)
    analytics = engine.tools.analytics.status()
    mcp = engine.tools.mcp.status()
    documents["formats"] += [{"extension": extension, "format": "ocr", "ready": ocr["ready"]} for extension in ("png", "jpg", "jpeg")]
    voice = app.voice.status()
    production = engine.tools.production.status()
    knowledge = engine.tools.knowledge.status()
    media = engine.tools.media.status()
    automation = engine.tools.automation.status()
    capabilities = [
        group("workspace", "Local workspace", "Inspect, search, calculate and create files within the box.", "Files", {"file_list", "file_read", "file_write", "workspace_search", "calculate"}),
        group("tables", "Business tables", "Read/write CSV, TSV and JSON tables; filter and compute exact decimal summaries.", "Data", {"table_read", "table_write", "table_query"}, details="Spreadsheet formula-like text is escaped in CSV/TSV exports; numeric totals use deterministic arithmetic."),
        group("analytics", "Larger table analysis", "Filter, group and join local CSV/JSON tables with exact decimal aggregates.", "Data", {"analytics_query"}, ready=analytics.get("ready", False), details="Up to 100,000 combined rows and 10 MB input. Fixed operations, explicit numeric columns and at most one unique-key join. Loaded only on demand."),
        group("knowledge", "Memory and knowledge", "Search owner notes and explicitly imported document snapshots.", "Knowledge", {"memory_search", "memory_save", "knowledge_search"}, details="Reference text remains untrusted. Imported instructions do not install skills or grant permissions."),
        group("documents", "Document imports", "Preview and import local text, Office documents and optional PDF text.", "Knowledge", set(), details="Operator-only import/refresh. Source folders are read-only; all stored snapshots stay in Yikes."),
        group("pdf", "PDF text extraction", "Extract text from PDFs using a bounded worker. Select OCR for scanned PDFs.", "Knowledge", set(), ready=documents["pdf"]["ready"], details=documents["pdf"]["reason"]),
        group("ocr", "Local image and scan OCR", "Read PNG/JPEG images and scanned PDFs using pinned local CPU models.", "Knowledge", set(), ready=ocr["ready"], details=ocr["reason"] + " Review recognized text and numbers; OCR can make mistakes. Up to 10 PDF pages and 12 million pixels per page."),
        group("mcp", "Local MCP tool connections", "Connect explicitly selected tools from local Streamable HTTP servers.", "Connected system", set(mcp_descriptions), ready=mcp.get("ready", False) and bool(mcp_descriptions), details="Register a server and review discovered tools below. Discovery grants no authority; read-only classification is an owner decision. Each write requires approval. Service availability is checked when invoked."),
        group("speech_input", "Offline voice input", "Push-to-talk transcription with a local speech engine.", "Voice", set(), ready=voice["transcription"], details="Browser microphone permission is required; audio is not sent to cloud speech recognition."),
        group("speech_output", "Offline voice output", "Read responses through a local speech engine.", "Voice", set(), ready=voice["speech"]),
        group("workflows", "Checked workflows", "Run reusable local missions with explicit file expectations and bounded repair.", "Automation", set(), details="Checks verify selected file content, structure and exact numeric expectations. They do not certify overall business quality or deployment. A model's completion claim alone does not pass them."),
        group("python_runner", "Isolated Python runner", "Run workspace scripts in a restricted, networkless Docker snapshot.", "Execution", {"run_python"}, ready=bool(settings.enable_docker and shutil.which("docker")), details="Disabled by default. Requires a separately configured Docker daemon and pre-pulled image; no host shell tool is available."),
    ]
    def extension(identifier, name, description, category, names, ready, details, setup):
        entry = group(identifier, name, description, category, set(names), ready=ready, details=details)
        if setup.startswith("python ") and os.name == "nt" and (Path(__file__).resolve().parents[1] / "runtime/python/python.exe").is_file():
            setup = ".\\runtime\\python\\python.exe " + setup[len("python "):]
        entry["setup_command"] = setup
        capabilities.append(entry)
    features = production["features"]
    extension("office", "Office artifact production", "Create real DOCX reports, XLSX tables, PPTX presentations and optional rendered PDFs.", "Production", {"office_create"}, True,
              "Macro-free templates create new files only. Spreadsheet strings are not formulas. PDF rendering needs LibreOffice.", "python scripts/provision_production.py --help")
    extension("optimization", "Checked planning and optimization", "Solve bounded assignments, schedules, packing and routes using supplied local distances.", "Data", {"production_optimize"}, features["optimization"],
              "Integer units, explicit constraints and actual solver status. A feasible result is distinguished from a proven optimum.", "python scripts/provision_production.py --help")
    extension("symbolic", "Symbolic mathematics", "Differentiate, integrate, simplify, factor and solve through a validated expression grammar.", "Data", {"production_math"}, features["symbolic_math"],
              "No Python evaluation or arbitrary library calls. Results retain symbolic/domain limitations.", "python scripts/provision_production.py --help")
    extension("semantic", "Hybrid document memory", "Combine exact lexical matching with local semantic embeddings and source citations.", "Knowledge", {"hybrid_search", "knowledge_reindex"}, knowledge["semantic"]["ready"],
              "An approved reindex binds imported source versions. A stale or unavailable index falls back to lexical search. Indexed: " + str(knowledge["semantic"].get("indexed", False)), "python scripts/provision_knowledge.py --help")
    extension("docling", "Structured document understanding", "Extract reading order, layout and tables from local documents using Docling.", "Knowledge", {"document_structure"}, knowledge["docling"]["ready"],
              "Model assets stay local; remote image/model fetches are disabled. Extraction runs over a bounded snapshot.", "python scripts/provision_knowledge.py --help")
    extension("monty", "Constrained code transformations", "Run short Python-subset transformations with JSON input and output.", "Execution", {"code_transform"}, knowledge["monty"]["ready"],
              "Approval required. No host functions, file mounts or shell. Interpreter memory/time bounds supplement worker limits.", "python scripts/provision_knowledge.py --help")
    extension("kiwix", "Offline reference library", "Search and read a locally served collection of dated ZIM reference archives.", "Knowledge", {"library_search", "library_read"}, knowledge["kiwix"]["ready"],
              "Preload content and register the local service. Archive licenses and snapshot dates remain separate from software licenses.", "python scripts/provision_knowledge.py --help")
    backends = media.get("backends", {})
    for key, title, description, names in [
        ("kokoro", "Natural speech synthesis", "Generate local WAV speech using a selected preset voice.", {"media_speak"}),
        ("sherpa", "Streaming-capable speech recognition", "Transcribe supplied recordings with a compact local ONNX speech engine.", {"media_transcribe"}),
        ("silero", "Speech activity detection", "Find speech segments in supplied audio without ambient recording.", {"media_vad"}),
        ("diarization", "Meeting speaker labels", "Group supplied recording turns by anonymous speaker labels.", {"media_diarize"}),
        ("flux", "Local image generation", "Create images through the optional local FLUX.2-klein-4B worker.", {"media_generate_image"}),
    ]:
        state = backends.get(key, {})
        extension(key, title, description, "Media", names, state.get("ready", False), state.get("reason", "Fixed local assets; resource bounds are not an OS sandbox."), "python scripts/provision_media.py --help")
    selected = app.store.setting("model", settings.model)
    # Detect provisioned vision models from the model's API, never its name alone.
    try:
        vision_ready = "vision" in engine.model.capabilities(selected) if hasattr(engine.model, "capabilities") else False
    except Exception:
        vision_ready = False
    extension("vision", "Visual reasoning", "Ask the selected local vision model about workspace images.", "Media", {"media_inspect_image"}, vision_ready,
              "Up to three bounded PNG/JPEG snapshots. Image instructions are untrusted; the vision call receives no tools.", "python scripts/provision.py --model qwen3.5:9b")
    for key, identifier, title, description, names in [
        ("freecad", "cad", "Parametric CAD", "Generate bounded mechanical parts and export STEP or STL.", {"cad_part"}),
        ("blender", "rendering", "Product rendering", "Render a fixed product scene using local Blender.", {"render_product"}),
        ("kicad", "electronics", "PCB template and exports", "Create a rectangular PCB template, run DRC and export manufacturing files.", {"pcb_manufacture"}),
        ("qgis", "gis", "Local spatial processing", "Buffer local geodata with an optional native QGIS worker.", {"gis_buffer"}),
        ("ffmpeg", "media_utilities", "Local media conversion", "Resample audio, create thumbnails and trim local media using fixed FFmpeg operations.", {"media_transform"}),
    ]:
        extension(identifier, title, description, "Engineering", names, features[key], "Fixed templates and snapshot inputs; no arbitrary scripts or loaded projects. Native assets load on demand.", "python scripts/provision_production.py --help")
    extension("geojson", "Offline map data analysis", "Inspect and filter local GeoJSON coordinates and feature bounds.", "Engineering", {"geo_analyze"}, True, "WGS84 geometry checks without remote maps or geocoding.", "")
    for key, identifier, title, description, names in [
        ("browser", "local_browser", "Local application automation", "Inspect and interact with owner-registered local web applications.", {"browser_task"}),
        ("flows", "local_flows", "Local event workflows", "Invoke fixed owner-registered Node-RED or other loopback routes.", {"local_flow"}),
        ("erpnext", "erpnext", "ERPNext connection", "Read selected records from a separately installed local ERPNext stack.", {"erpnext_read"}),
    ]:
        state = automation[key]
        setup = "python scripts/prepare_erpnext.py --help" if key == "erpnext" else "python scripts/provision_automation.py --offline"
        extension(identifier, title, description, "Connected system", names, state["ready"], state["reason"], setup)
    extension("business_records", "Local business records", "Maintain contacts, item catalogs, stock movements and balanced journals locally.", "Business", {"business_records"}, True,
              "Writes require approval and an idempotency key. Money uses integer cents; journals must balance. Operational records do not certify tax/accounting compliance.", "")
    extension("survival", "Survival & rebuilding", "Plan household reserves and power, track dated supplies and dependency-based projects, and search cited offline references and owner books.", "Preparedness", {"survival_plan", "survival_records", "survival_reference"}, engine.tools.references.status()["ready"],
              "Exact local estimates with explicit assumptions. Inventory is owner-reported; dates do not certify safety. Books require private owner import. Physical work remains an owner decision.", "")
    extension("procedures", "Fixed local workflows", "Generate readiness, stock rotation, power, project, reference, business and knowledge reports without a model call inside the procedure.", "Operations", {"local_procedure"}, True,
              "Explicit local runs, unique output directories, artifact hashes and durable receipts. Interrupted requests stop for review. Optional AI synthesis uses the selected model separately.", "")
    for name, connector in engine.tools._connectors.items():
        credentials_ready = all(not isinstance(v, dict) or bool(os.environ.get(v["env"])) for v in connector["headers"].values())
        capabilities.append(group(name, connector["id"], connector["description"], "Connected system", {name}, ready=credentials_ready, offline=False,
                                  details="Fixed operator-configured endpoint; service health is checked only when invoked. Credential values are not shown."))
    return {"capabilities": capabilities, "formats": documents["formats"], "limits": documents["limits"], "ocr": ocr, "analytics": analytics, "mcp": mcp,
            "packs": {"production": production, "knowledge": knowledge, "media": media, "automation": automation},
            "sources_count": len(app.store.sources()), "workflows_count": len(app.store.workflows())}
