"use strict";

// A local demonstration desk. Loading the view never seeds, approves or exports.
(() => {
  let app, data = null, selected = null, attached = null;
  let busy = false, loading = false, generation = 0, stale = false;
  let notice = "", uncertain = null, inspected = false, exported = null;
  let buttons = [], editDrafts = new Map();
  const $ = id => document.getElementById(id);
  const current = (marker, token) => marker === generation && app?.state.authenticated && app.state.token === token;
  const text = value => typeof value === "string" ? value : "";
  const title = value => text(value).replaceAll("_", " ");
  const newId = prefix => prefix + crypto.randomUUID().replaceAll("-", "");
  const e = (tag, cls = "", value) => app.node(tag, cls, value);
  const p = (parent, value, cls = "") => parent.append(e("p", cls, value));

  function money(value) {
    const raw = typeof value === "number" && Number.isSafeInteger(value) ? String(value) : value;
    if (typeof raw !== "string" || !/^\d{1,16}$/.test(raw)) return "Awaiting input";
    const cents = BigInt(raw);
    return `$${(cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${(cents % 100n).toString().padStart(2, "0")}`;
  }
  function relativePath(value) {
    const path = text(value).replaceAll("\\", "/");
    if (!path || path.length > 1000 || path.startsWith("/") || path.includes(":") || path.split("/").some(part => !part || part === "." || part === "..")) throw new Error("The handoff artifact must remain inside Ariadne Workspace.");
    return path;
  }
  function exceptionLabel(field) {
    const line = /^lines\.(\d{1,4})\.(unit_price|source_ref|price_approved|description|quantity)$/.exec(text(field));
    if (line) {
      const labels = {unit_price:"approved price", source_ref:"price source", price_approved:"price approval", description:"service description", quantity:"quantity"};
      return `Line ${Number(line[1]) + 1} · ${labels[line[2]]}`;
    }
    const labels = {tax:"Tax instruction", "tax.bps":"Tax instruction", "tax.approved":"Tax approval", "tax.source_ref":"Tax instruction source", work:"Completed-work evidence", "work.completed":"Completed-work evidence", "work.approved":"Completed-work approval", "work.source_ref":"Completed-work source", customer:"Customer details", "customer.name":"Customer name", "customer.email":"Customer email", "customer.address":"Customer address", document_date:"Document date", service_date:"Service date", due_date:"Due or valid-until date", source_notes:"Source notes", title:"Job title", unit_price:"Approved price", source_ref:"Price source"};
    return Object.prototype.hasOwnProperty.call(labels, field) ? labels[field] : "Owner input required";
  }
  function canWrite() {
    return Boolean(app?.state.authenticated && data?.demo_only === true && !busy && !loading && !stale && !uncertain && !app.state.status?.paused);
  }
  function canReview(record, board = data, paused = Boolean(app?.state.status?.paused)) {
    return Boolean(board?.demo_only === true && board.initialized && !paused && record?.status === "ready_for_review" && Array.isArray(record.exceptions) && record.exceptions.length === 0 && /^[a-f0-9]{64}$/.test(record.content_sha256 || ""));
  }
  function reviewPayload(record, revision, requestId) {
    if (!Number.isInteger(revision) || revision < 0 || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(requestId) || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(record?.id || "") || !/^[a-f0-9]{64}$/.test(record.content_sha256 || "")) throw new Error("Refresh the desk before reviewing this draft.");
    return {request_id:requestId, expected_revision:revision, record_id:record.id, content_sha256:record.content_sha256, decision:"approve"};
  }
  function taxBps(value) {
    if (!text(value).trim()) return null;
    const match = /^(\d{1,3})(?:\.(\d{1,2}))?$/.exec(text(value).trim());
    if (!match) throw new Error("Enter the supplied tax percentage with up to two decimal places, or leave it blank.");
    const result = Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
    if (result > 10000) throw new Error("The supplied tax percentage must be between 0 and 100.");
    return result;
  }
  function editPayload(row, fields, revision, requestId) {
    const payload = reviewPayload(row, revision, requestId); delete payload.decision;
    const value = name => text(fields[name]).trim(), flag = name => fields[name] === true;
    const lines = (row.lines || []).map((unused, index) => {
      const quantity = value(`quantity-${index}`), rate = value(`rate-${index}`);
      if (!/^[1-9]\d{0,8}$/.test(quantity)) throw new Error("Each line needs a positive whole quantity.");
      if (rate && !/^\d{1,13}(\.\d{1,2})?$/.test(rate)) throw new Error("Use an exact nonnegative dollar rate with up to two decimal places, or leave it blank.");
      return {description:value(`description-${index}`), quantity:Number(quantity), unit_price:rate || null,
        price_approved:flag(`approved-${index}`), source_ref:value(`source-${index}`) || null};
    });
    payload.changes = {title:value("title"), customer:{name:value("customer") || null,email:value("email") || null,address:value("address") || null},
      service_date:value("service-date") || null, document_date:value("document-date") || null, due_date:value("due-date") || null,
      lines, tax:{bps:taxBps(value("tax")),approved:flag("tax-approved"),source_ref:value("tax-source") || null},
      work:{completed:flag("completed"),approved:flag("work-approved"),source_ref:value("work-source") || null}};
    return payload;
  }
  function action(label, callback, allowed = () => true, cls = "button-secondary") {
    const button = app.button(label, cls, callback); buttons.push({button, allowed}); return button;
  }
  function controls() {
    const refresh = $("refresh-handoff");
    if (refresh) refresh.disabled = busy || loading || !app?.state.authenticated;
    for (const row of buttons) row.button.disabled = !canWrite() || !row.allowed();
  }
  function record() { return (data?.records || []).find(row => row.id === selected) || null; }
  function stamp(value) {
    const date = new Date(value);
    return value && Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Time not supplied";
  }
  function badge(parent, label, state) { parent.append(e("span", `handoff-badge handoff-badge-${state || "neutral"}`, label)); }

  function noticePanel(parent) {
    if (!notice && !stale && !uncertain && !app.state.status?.paused) return;
    const box = e("section", "handoff-notice"); box.setAttribute("role", "status");
    if (notice) p(box, notice);
    if (app.state.status?.paused) p(box, "Autonomy is paused. The examples remain readable; local review and export are held.");
    if (stale) p(box, "The desk changed. Refresh its saved state before another action.");
    if (uncertain) {
      p(box, `The ${uncertain.kind} response was interrupted. Request: ${uncertain.id}. Refresh and inspect saved records and audit history. Nothing is retried automatically.`);
      const acknowledge = app.button("I inspected the saved state", "button-secondary", () => { if (!inspected) return; uncertain = null; notice = ""; render(); });
      acknowledge.disabled = !inspected || busy || loading; box.append(acknowledge);
    }
    parent.append(box);
  }
  function overview(parent) {
    const section = e("section", "handoff-hero"), content = e("div", "handoff-hero-copy");
    content.append(e("span", "eyebrow", "SERVICE DEMONSTRATOR / FICTIONAL RECORDS"), e("h2", "", "From job notes to paperwork ready for review."));
    p(content, "Your client supplies the job details and approved prices. Ariadne checks the draft and holds missing evidence. The business owner approves, sends and posts it.", "handoff-muted");
    section.append(content);
    const scope = e("div", "handoff-scope"); badge(scope, "DEMO ONLY", "neutral"); p(scope, "No client contacted. No document sent. No money collected.");
    if (!data.initialized) scope.append(action("Load fictional examples", seed, () => !data.initialized, "button-primary"));
    else p(scope, "Your local demo changes persist independently of operational business records.", "handoff-muted");
    section.append(scope); parent.append(section);
    const steps = e("ol", "handoff-steps");
    for (const [index, label] of ["Receive approved inputs", "Check prices and totals", "Hold missing details", "Review the draft", "Return a handoff packet"].entries()) {
      const item = e("li"); item.append(e("span", "", String(index + 1).padStart(2, "0")), e("strong", "", label)); steps.append(item);
    }
    parent.append(steps);
    const model = e("details", "handoff-service-model"), roles = e("div", "handoff-roles");
    model.append(e("summary", "", "How this service works"));
    for (const [label, heading, description] of [["YOU", "Run the paperwork service", "Prepare and check drafts. Your fee pays for this administrative work."], ["YOUR CLIENT", "Runs the service business", "Owns the job, approved rates and customer relationship. Their workers perform the service."], ["THEIR CUSTOMER", "Receives approved paperwork", "Receives the quote or invoice after the business owner approves and sends it."]]) {
      const item = e("div"); item.append(e("span", "eyebrow", label), e("strong", "", heading), e("p", "handoff-muted", description)); roles.append(item);
    }
    model.append(roles); p(model, "Pricing hypothesis for your service: $150 for a capped pilot, or $500/month for a capped ongoing batch service. These prices are untested. The $500 quote and other draft amounts below belong to fictional client jobs; they are not your service revenue.", "handoff-fee-note"); parent.append(model);
    const summary = data.summary || {}, metrics = e("section", "handoff-metrics");
    for (const [label, value] of [["Fictional records", summary.records || 0], ["Ready for review", summary.ready_for_review || 0], ["Held for input", summary.held || 0], ["Demo reviewed", summary.approved || 0]]) {
      const item = e("div"); item.append(e("span", "", label), e("strong", "", String(value))); metrics.append(item);
    }
    parent.append(metrics);
  }
  function queue(parent) {
    const section = e("section", "handoff-queue"); section.append(e("span", "eyebrow", "01 / WORK QUEUE"), e("h3", "", "Choose a draft"));
    for (const row of data.records || []) {
      const button = app.button("", "handoff-record", () => { selected = row.id; render(); });
      button.setAttribute("aria-label", `${row.title || row.id}, ${title(row.status)}`);
      button.setAttribute("aria-pressed", String(row.id === selected));
      button.append(e("span", "handoff-record-kind", `${row.kind === "invoice" ? "INVOICE" : "QUOTE"} / ${row.id}`), e("strong", "", row.title || row.id), e("span", "handoff-muted", row.customer?.name || "Customer missing"));
      badge(button, row.status === "approved" ? "Demo reviewed" : row.status === "held" ? "Needs input" : title(row.status), row.status);
      section.append(button);
    }
    p(section, "These examples are fictional. Their face values are not sales, receivables or earned revenue.", "handoff-small handoff-muted"); parent.append(section);
  }
  function preview(parent, row) {
    const frame = e("section", "handoff-preview-frame"), top = e("div", "handoff-preview-top");
    top.append(e("span", "eyebrow", "02 / DOCUMENT PREVIEW")); badge(top, row.status === "held" ? "HELD — INCOMPLETE" : row.status === "approved" ? "DEMO REVIEW RECORDED" : "AWAITING DEMO REVIEW", row.status); frame.append(top);
    const sheet = e("article", "handoff-paper"); sheet.setAttribute("aria-label", `${row.kind === "invoice" ? "Invoice" : "Quote"} draft preview`);
    const header = e("header", "handoff-paper-header"), brand = e("div"); brand.append(e("strong", "handoff-paper-brand", row.company || (typeof data.provider === "string" ? data.provider : data.provider?.name) || "Fictional service business"), e("span", "", "Demonstration document · not sent"));
    const heading = e("div"); heading.append(e("h3", "", row.kind === "invoice" ? "INVOICE DRAFT" : "QUOTE DRAFT"), e("span", "", row.id)); header.append(brand, heading); sheet.append(header);
    const meta = e("div", "handoff-paper-meta"), recipient = e("div"); recipient.append(e("span", "handoff-paper-label", row.kind === "invoice" ? "BILL TO / FICTIONAL" : "PREPARED FOR / FICTIONAL"), e("strong", "", row.customer?.name || "Customer not supplied"));
    if (row.customer?.address) recipient.append(e("span", "", row.customer.address));
    if (row.customer?.email) recipient.append(e("span", "", row.customer.email));
    const dates = e("dl");
    for (const [label, value] of [["Document date", row.document_date], ["Service date", row.service_date], [row.kind === "invoice" ? "Due date" : "Valid until", row.due_date]]) { dates.append(e("dt", "", label), e("dd", "", value || "Not supplied")); }
    meta.append(recipient, dates); sheet.append(meta, e("h4", "", row.title || "Service scope"));
    const scroll = e("div", "handoff-table-scroll"), table = e("table", "handoff-lines"), head = e("thead"), line = e("tr");
    for (const label of ["Description", "Qty", "Approved rate", "Amount"]) line.append(e("th", "", label)); head.append(line); table.append(head);
    const body = e("tbody");
    for (const item of row.lines || []) {
      const tr = e("tr"), description = e("td", "", item.description || "Description missing");
      if (!item.price_approved) description.append(e("small", "handoff-paper-warning", "Rate approval missing"));
      tr.append(description, e("td", "", item.quantity === null || item.quantity === undefined ? "Missing" : String(item.quantity)), e("td", "", typeof item.unit_price === "string" && /^\d{1,13}(\.\d{1,2})?$/.test(item.unit_price) ? `$${item.unit_price}` : "Missing"), e("td", "", money(item.line_total_cents))); body.append(tr);
    }
    table.append(body); scroll.append(table); sheet.append(scroll);
    const totals = e("dl", "handoff-totals");
    for (const [label, value] of [["Subtotal", money(row.subtotal_cents)], [row.tax?.bps === null || row.tax?.bps === undefined ? "Tax — input required" : "Tax — supplied policy", money(row.tax_cents)], ["Draft total", row.total_cents === null || row.total_cents === undefined ? "HELD" : money(row.total_cents)]]) totals.append(e("dt", "", label), e("dd", "", value));
    sheet.append(totals);
    if (row.kind === "invoice") p(sheet, row.work?.completed === true && row.work?.approved === true ? "Completion and approval were supplied in the fictional source. This draft has not been issued or posted." : "Completion or approval evidence is missing. An estimate cannot be treated as completed work.", "handoff-paper-note");
    else p(sheet, "This is a proposal draft. Acceptance, performed work and payment are separate events.", "handoff-paper-note");
    p(sheet, "FICTIONAL DEMONSTRATION · NOT A PAYMENT REQUEST", "handoff-paper-watermark"); frame.append(sheet); parent.append(frame);
  }
  function checks(parent, row) {
    const aside = e("aside", "handoff-checks"), details = e("section", "handoff-card"); details.append(e("span", "eyebrow", "03 / SOURCE CHECKS"), e("h3", "", "What supports this draft?"));
    const notes = Array.isArray(row.source_notes) ? row.source_notes : [row.source_notes];
    for (const note of notes) {
      const value = typeof note === "string" ? note : note && typeof note === "object" ? [note.ref || note.id, note.text || note.note].filter(Boolean).join(" · ") : "";
      if (value) p(details, value, "handoff-source-note");
    }
    const refs = e("ul", "handoff-source-refs");
    for (const item of row.lines || []) refs.append(e("li", "", `${item.description || "Line item"}: ${item.source_ref || "source reference missing"} · ${item.price_approved ? "price approval supplied" : "price approval missing"}`));
    refs.append(e("li", "", `Tax: ${row.tax?.source_ref || "source reference missing"} · ${row.tax?.approved ? "policy approval supplied" : "policy approval missing"}`));
    if (row.kind === "invoice") refs.append(e("li", "", `Work: ${row.work?.source_ref || "source reference missing"} · ${row.work?.completed && row.work?.approved ? "completion approved" : "completion/approval missing"}`));
    details.append(refs); aside.append(details);
    const exceptions = e("section", "handoff-card"); exceptions.append(e("span", "eyebrow", "EXCEPTION QUEUE"), e("h3", "", row.exceptions?.length ? "Hold these gaps" : "Required checks passed"));
    if (row.exceptions?.length) {
      for (const problem of row.exceptions) { const item = e("div", "handoff-exception"); item.append(e("strong", "", exceptionLabel(problem.field)), e("p", "", problem.message || "Owner input required.")); exceptions.append(item); }
      p(exceptions, "Missing prices, tax policy and completed work stay missing until the owner supplies reviewed evidence. Ariadne does not guess them.", "handoff-small handoff-muted");
    } else p(exceptions, "Arithmetic and required supplied fields passed local checks. This does not verify a real customer, legal tax treatment or work performed.", "handoff-muted");
    aside.append(exceptions); editForm(aside, row);
    const review = e("section", "handoff-card handoff-review"); review.append(e("span", "eyebrow", "04 / LOCAL REVIEW"), e("h3", "", row.status === "approved" ? "Demo review recorded" : "Approve the exact draft"));
    p(review, "Review applies to this fictional content snapshot only. Editing it invalidates the review. The business owner still controls actual sending and posting.", "handoff-muted");
    if (row.status === "approved") p(review, "This record is eligible for the demonstration handoff packet.", "handoff-review-ready");
    else review.append(action("Approve this demo draft", () => approve(row), () => canReview(row), "button-primary"));
    p(review, `Content SHA-256: ${row.content_sha256 || "Unavailable"}`, "handoff-hash"); aside.append(review); parent.append(aside);
  }
  function editForm(parent, row) {
    const details = e("details", "handoff-card handoff-editor"); details.append(e("summary", "", "Edit fictional source inputs"));
    p(details, "Supply the missing fictional evidence and recheck. Blank rates or tax stay unknown. Saving edits clears the previous demo review.", "handoff-muted");
    const form = e("form"), fields = {}, revision = data.revision, sourceHash = row.content_sha256;
    const draft = editDrafts.get(row.id), saved = draft?.hash === sourceHash ? draft.fields : {};
    function field(label, name, value, kind = "text") {
      const wrapper = e("label", `handoff-edit-field${kind === "checkbox" ? " handoff-edit-check" : ""}`, label), input = e("input"); input.name = name; input.type = kind;
      if (kind === "checkbox") input.checked = Object.prototype.hasOwnProperty.call(saved, name) ? saved[name] === true : value === true;
      else { input.value = Object.prototype.hasOwnProperty.call(saved, name) ? text(saved[name]) : value ?? ""; input.maxLength = name === "title" ? 200 : 500; }
      input.addEventListener("input", remember); input.addEventListener("change", remember); fields[name] = input; wrapper.append(input); form.append(wrapper); return input;
    }
    const read = () => Object.fromEntries(Object.entries(fields).map(([name, input]) => [name,input.type === "checkbox" ? input.checked : input.value]));
    function remember() { editDrafts.set(row.id, {hash:sourceHash, fields:read()}); }
    field("Job title", "title", row.title); field("Fictional customer name", "customer", row.customer?.name); field("Fictional customer email", "email", row.customer?.email); field("Fictional customer address", "address", row.customer?.address);
    field("Document date", "document-date", row.document_date, "date"); field("Service date", "service-date", row.service_date, "date"); field(row.kind === "invoice" ? "Due date" : "Valid-until date", "due-date", row.due_date, "date");
    for (const [index, item] of (row.lines || []).entries()) {
      const heading = e("h4", "", `Line ${index + 1}`); form.append(heading);
      field("Description", `description-${index}`, item.description); const quantity = field("Quantity", `quantity-${index}`, String(item.quantity ?? "")); quantity.inputMode = "numeric";
      const rate = field("Supplied unit price (USD)", `rate-${index}`, item.unit_price); rate.inputMode = "decimal";
      field("Fictional rate source reference", `source-${index}`, item.source_ref); field("Rate approval supplied", `approved-${index}`, item.price_approved, "checkbox");
    }
    form.append(e("h4", "", "Supplied tax and work evidence"));
    field("Supplied tax percentage (blank = unknown)", "tax", Number.isInteger(row.tax?.bps) ? (row.tax.bps / 100).toFixed(2) : "").inputMode = "decimal";
    field("Fictional tax source reference", "tax-source", row.tax?.source_ref); field("Tax policy approval supplied", "tax-approved", row.tax?.approved, "checkbox");
    field("Fictional completion source reference", "work-source", row.work?.source_ref); field("Completion supplied", "completed", row.work?.completed, "checkbox"); field("Completion approval supplied", "work-approved", row.work?.approved, "checkbox");
    const submit = action("Save fictional inputs and recheck", () => {}, () => sourceHash === record()?.content_sha256); submit.type = "submit"; form.append(submit);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (!canWrite()) return; remember();
      const id = newId("handoff-edit-");
      try { const payload = editPayload(row, read(), revision, id); await mutate("edit", "/handoff/record", payload, id, "PUT"); }
      catch (error) { notice = error.message; render(); }
    });
    details.append(form); parent.append(details);
  }
  function handoff(parent) {
    const section = e("section", "handoff-export"), copy = e("div"); copy.append(e("span", "eyebrow", "05 / RETURN THE WORK"), e("h3", "", "A packet the owner can inspect."));
    p(copy, "Export currently reviewed fictional records and their source checks. Incomplete or unreviewed records are excluded. Exporting does not send or issue an invoice.", "handoff-muted"); section.append(copy, action("Create reviewed handoff packet", exportPacket, () => (data.summary?.approved || 0) > 0 && !data.export_hold, "button-primary")); parent.append(section);
    if (data.export_hold) {
      const hold = e("section", "handoff-notice"); hold.append(e("h3", "", "A previous export needs review"));
      p(hold, `${data.export_hold.reason || "Earlier export did not complete durably."} Request: ${data.export_hold.request_id}.`);
      p(hold, "No partial output will be adopted or retried. Acknowledge only after inspecting the saved state; a later export requires a new manual request.");
      hold.append(action("Acknowledge inspected export hold", acknowledgeHold)); parent.append(hold);
    }
    const result = exported || data.latest_export;
    if (result?.status === "completed" && result?.artifacts?.length) {
      const artifacts = e("section", "handoff-card handoff-artifacts"); artifacts.append(e("h3", "", "Saved local handoff"));
      for (const item of result.artifacts) {
        let path; try { path = relativePath(item.path); } catch { continue; }
        artifacts.append(app.button(path.endsWith(".html") ? "Download printable handoff" : path.endsWith(".json") ? "Inspect handoff data" : "Open saved artifact", "button-secondary", () => path.endsWith(".html") && app.downloadArtifact ? app.downloadArtifact(path) : app.openArtifact(path), "file"));
        p(artifacts, `${path} · SHA-256 ${item.sha256 || "unavailable"}`, "handoff-hash");
      }
      p(artifacts, "A saved packet retains its reviewed snapshot. Later edits require a new review and a new packet.", "handoff-muted"); parent.append(artifacts);
    }
  }
  function audit(parent) {
    if (!(data.audit || []).length) return;
    const section = e("section", "handoff-audit"); section.append(e("span", "eyebrow", "LOCAL DEMO ACTIVITY"), e("h3", "", "Recorded, without external actions"));
    const list = e("ol");
    for (const item of data.audit.slice(0, 8)) {
      const li = e("li"); li.append(e("strong", "", title(item.action || item.kind || "Local action")), e("span", "", `${item.record_id || "Demo desk"} · ${stamp(item.created_at || item.created)} · ${item.request_id || "Receipt unavailable"}`)); list.append(li);
    }
    section.append(list); parent.append(section);
  }
  function render() {
    const root = $("handoff-content"); if (!root || !app?.state.authenticated) return;
    root.replaceChildren(); buttons = [];
    if (!data) { p(root, loading ? "Reading the local demonstration desk…" : notice || "Refresh to inspect the local demonstration desk.", "handoff-muted"); controls(); return; }
    overview(root); noticePanel(root);
    if (data.initialized && data.records?.length) {
      if (!record()) selected = data.records[0].id;
      const layout = e("div", "handoff-layout"), queueColumn = e("div"), previewColumn = e("div");
      queue(queueColumn); preview(previewColumn, record()); layout.append(queueColumn, previewColumn); checks(layout, record()); root.append(layout); handoff(root); audit(root);
    } else p(root, "Load the sample only when you choose. It includes a complete quote, an invoice supported by fictional completed work, and held examples with missing evidence.", "handoff-empty");
    controls();
  }
  async function load() {
    if (!app?.state.authenticated || loading || busy) return;
    loading = true; const marker = generation, token = app.state.token; controls();
    try {
      const value = await app.api("/handoff"); if (!current(marker, token)) return;
      if (value?.demo_only !== true || !Number.isInteger(value.revision) || !Array.isArray(value.records)) throw new Error("The local demo response could not be verified.");
      data = value; exported = null; stale = false; if (uncertain) inspected = true;
    } catch (error) { if (current(marker, token)) notice = error.message; }
    finally { if (current(marker, token)) { loading = false; render(); } }
  }
  async function mutate(kind, path, body, id, method = "POST") {
    if (!canWrite()) return;
    busy = true; notice = ""; const marker = generation, token = app.state.token; controls();
    try {
      const result = await app.api(path, {method, body}); if (!current(marker, token)) return;
      if (result.ok === false || ["failed", "interrupted", "held"].includes(result.status)) throw Object.assign(new Error(result.error || result.reason || "The demo operation is held; inspect the saved receipt."), {status:409});
      if (kind === "export") exported = result;
      if (kind === "edit") editDrafts.delete(body.record_id);
      app.toast(kind === "review" ? "Demo draft reviewed. Nothing sent or posted." : kind === "export" ? "Local demonstration packet saved." : kind === "edit" ? "Fictional inputs saved and rechecked; previous review cleared." : kind === "acknowledgement" ? "Export hold acknowledged. Earlier output was not retried or adopted." : "Fictional examples loaded.");
    } catch (error) {
      if (!current(marker, token)) return;
      notice = error.message;
      if (error.status === 409) stale = true;
      else if (!error.status || error.status >= 500) uncertain = {kind, id};
    } finally {
      if (current(marker, token)) { busy = false; render(); if (!uncertain && !stale) await load(); }
    }
  }
  async function seed() {
    if (!canWrite() || data.initialized) return;
    const id = newId("handoff-demo-"); await mutate("seed", "/handoff/demo", {request_id:id, expected_revision:data.revision}, id);
  }
  async function approve(row) {
    if (!canWrite() || !canReview(row)) return;
    const id = newId("handoff-review-"); await mutate("review", "/handoff/review", reviewPayload(row, data.revision, id), id);
  }
  async function exportPacket() {
    if (!canWrite() || !(data.summary?.approved > 0) || data.export_hold) return;
    const id = newId("handoff-export-"); await mutate("export", "/handoff/export", {request_id:id, expected_revision:data.revision}, id);
  }
  async function acknowledgeHold() {
    if (!canWrite() || !data.export_hold || !app.confirmAction) return;
    const marker = generation, token = app.state.token, revision = data.revision, hold = {...data.export_hold};
    const accepted = await app.confirmAction("Acknowledge the inspected export hold?", `${hold.reason || "Export completion was not established."} Request ${hold.request_id}. This only acknowledges your review; it does not adopt, restore or retry partial output.`, "Acknowledge reviewed hold", "Keep held");
    if (!accepted || !current(marker, token) || data.revision !== revision || data.export_hold?.request_id !== hold.request_id) return;
    await mutate("acknowledgement", "/handoff/export/acknowledge", {request_id:hold.request_id, expected_revision:revision}, hold.request_id);
  }
  function status() { controls(); }
  function locked() {
    generation++; data = null; selected = null; busy = false; loading = false; stale = false;
    notice = ""; uncertain = null; inspected = false; exported = null; buttons = []; editDrafts = new Map();
    $("handoff-content")?.replaceChildren(); controls();
  }
  function attach(value) {
    app = value; const root = $("handoff-content"); if (!root || attached === root) return;
    attached = root; $("refresh-handoff")?.addEventListener("click", load); controls();
  }
  window.ariadneHandoff = {attach, load, locked, status, money, relativePath, exceptionLabel, canReview, reviewPayload, taxBps, editPayload, seed, approve, exportPacket, acknowledgeHold};
})();
