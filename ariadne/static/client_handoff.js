"use strict";

// Owner-operated client records. Reads never create clients, drafts or evidence.
(() => {
  let app, data = null, attached = null, generation = 0, loading = false, busy = false;
  let stale = false, uncertain = null, inspected = false, notice = "";
  let selectedClient = null, selectedIntake = null, selectedDocument = null;
  let client = null, intake = null, document = null, buttons = [], inputs = [], localButtons = [];
  let drafts = new Map(), profileOpen = false, intakeOpen = false;
  let pages = {client_offset:0, intake_offset:0, document_offset:0};
  const $ = id => window.document.getElementById(id);
  const string = value => typeof value === "string" ? value : "";
  const trim = value => string(value).trim();
  const idPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const shaPattern = /^[a-f0-9]{64}$/;
  const newId = prefix => prefix + crypto.randomUUID().replaceAll("-", "");
  const current = (marker, token) => generation === marker && app?.state.authenticated && app.state.token === token;
  const e = (tag, cls = "", text) => app.node(tag, cls, text);
  const p = (parent, text, cls = "") => parent.append(e("p", cls, text));
  const pretty = value => string(value).replaceAll("_", " ");
  const stamp = value => { const date = new Date(value); return value && Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Not recorded"; };

  function money(value) {
    if (typeof value !== "string" || !/^\d{1,16}$/.test(value)) return "Awaiting input";
    const cents = BigInt(value);
    return `$${(cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${(cents % 100n).toString().padStart(2,"0")}`;
  }
  function relativePath(value) {
    const path = string(value).replaceAll("\\", "/");
    if (!path || path.length > 1000 || path.startsWith("/") || path.includes(":") || path.split("/").some(part => !part || part === "." || part === "..")) throw new Error("Choose an artifact inside Ariadne Workspace.");
    return path;
  }
  function required(value, label, max = 200) {
    const result = trim(value);
    if (!result || result.length > max) throw new Error(`${label} needs 1–${max} characters.`);
    return result;
  }
  function nullable(value) { return trim(value) || null; }
  function meta(revision, requestId) {
    if (!Number.isInteger(revision) || revision < 0 || !idPattern.test(requestId)) throw new Error("Refresh the saved workspace before another action.");
    return {request_id:requestId, expected_revision:revision};
  }
  function documentMeta(row, revision, requestId) {
    if (!idPattern.test(row?.id || "") || !shaPattern.test(row?.content_sha256 || "")) throw new Error("Refresh and choose a saved document.");
    return {...meta(revision,requestId), document_id:row.id, content_sha256:row.content_sha256};
  }
  function taxBps(value) {
    const raw = trim(value); if (!raw) return null;
    const match = /^(\d{1,3})(?:\.(\d{1,2}))?$/.exec(raw);
    if (!match) throw new Error("Use a supplied tax percentage with at most two decimal places, or leave it unknown.");
    const result = Number(match[1]) * 100 + Number((match[2] || "").padEnd(2,"0"));
    if (result > 10000) throw new Error("The supplied tax percentage must be between 0 and 100.");
    return result;
  }
  function linePayload(row) {
    const quantity = required(row.quantity,"Quantity",16), price = trim(row.unit_price);
    if (!/^(?:0|[1-9]\d{0,4})(?:\.\d{1,3})?$/.test(quantity) || !/[1-9]/.test(quantity)) throw new Error("Use a positive quantity with at most three decimal places, such as 2.5 hours.");
    const [whole,fraction=""] = quantity.split(".");
    if (BigInt(whole)*1000n+BigInt(fraction.padEnd(3,"0"))>10000000n) throw new Error("Quantity cannot exceed 10000 units.");
    if (price && !/^\d{1,13}(?:\.\d{1,2})?$/.test(price)) throw new Error("Use an exact dollar rate with at most two decimal places, or leave it unknown.");
    return {description:required(row.description,"Line description",500), quantity, unit:required(row.unit || "unit","Unit",40),
      unit_price:price || null, price_approved:row.price_approved === true, source_ref:nullable(row.source_ref)};
  }
  function recordFields(fields) {
    if (!Array.isArray(fields.lines) || fields.lines.length < 1 || fields.lines.length > 50) throw new Error("Use one to fifty service lines.");
    return {title:required(fields.title,"Job title",300), customer:{name:nullable(fields.customer),email:nullable(fields.email),address:nullable(fields.address)},
      source_notes:required(fields.source_notes,"Source notes",12000), service_date:nullable(fields.service_date),
      document_date:nullable(fields.document_date), due_date:nullable(fields.due_date), lines:fields.lines.map(linePayload),
      tax:{bps:taxBps(fields.tax),approved:fields.tax_approved === true,source_ref:nullable(fields.tax_source)},
      permission:{confirmed:fields.permission_confirmed === true,source_ref:nullable(fields.permission_source)}};
  }
  function clientPayload(fields, revision, requestId, identifier) {
    if (!idPattern.test(identifier)) throw new Error("Choose a valid client record.");
    return {...meta(revision,requestId),client:{id:identifier,name:required(fields.name,"Service client business name",300),
      address:nullable(fields.address),email:nullable(fields.email),contact:nullable(fields.contact),notes:nullable(fields.notes),archived:fields.archived === true}};
  }
  function intakePayload(fields, revision, requestId, identifier, clientId) {
    if (!idPattern.test(identifier) || !idPattern.test(clientId)) throw new Error("Choose a saved service client first.");
    return {...meta(revision,requestId),intake:{id:identifier,client_id:clientId,...recordFields(fields)}};
  }
  function acceptancePayload(row, fields, revision, requestId, now = Date.now()) {
    if (!["accepted","declined"].includes(fields.response)) throw new Error("Choose the customer's actual response.");
    const date = new Date(fields.observed_at);
    if (!fields.observed_at || !Number.isFinite(date.valueOf()) || date.valueOf() > now) throw new Error("Enter the actual customer response time; future evidence is not accepted.");
    return {...documentMeta(row,revision,requestId),accepted:fields.response === "accepted", observed_at:date.toISOString(),
      source_ref:required(fields.source_ref,"Acceptance evidence reference",1000), contact:required(fields.contact,"Customer contact",300),note:nullable(fields.note)};
  }
  function invoicePayload(row, fields, revision, requestId) {
    if (fields.completed !== true || fields.work_approved !== true) throw new Error("Explicitly confirm the supplied completion evidence before generating an invoice draft.");
    const exact=documentMeta(row,revision,requestId);
    if(row.kind!=="quote")throw new Error("Choose the accepted quote before creating an invoice.");
    return {request_id:exact.request_id,expected_revision:exact.expected_revision,quote_id:exact.document_id,content_sha256:exact.content_sha256,
      service_date:required(fields.service_date,"Completed service date",10),document_date:required(fields.document_date,"Invoice date",10),
      due_date:required(fields.due_date,"Due date",10),work:{completed:true,approved:true,source_ref:required(fields.work_source,"Completion evidence reference",1000)}};
  }
  function fieldLabel(value) {
    const line = /^lines\.(\d+)\.(\w+)$/.exec(string(value));
    const names = {unit_price:"approved rate",quantity:"quantity",source_ref:"price source",price_approved:"price approval",description:"description",unit:"unit"};
    if (line && names[line[2]]) return `Line ${Number(line[1])+1} · ${names[line[2]]}`;
    const labels = {tax:"Tax instruction",permission:"Permission to handle records",provider:"Service client details","provider.name":"Service client business name","provider.address":"Service client business address",work:"Completed-work evidence",acceptance:"Customer acceptance",customer:"Customer details","customer.name":"Customer name","customer.address":"Customer billing address",document_date:"Document date",service_date:"Service date",due_date:"Due or valid-until date",source_notes:"Source notes"};
    return labels[value] || "Owner input required";
  }
  function canWrite() { return Boolean(app?.state.authenticated && data?.demo_only === false && !busy && !loading && !stale && !uncertain && !app.state.status?.paused); }
  function canReview(row,decision) {
    if(!row||row.voided||row.invoice_id||!idPattern.test(row.id||"")||!shaPattern.test(row.content_sha256||""))return false;
    return decision==="approve"?(!row.exceptions?.length&&["ready_for_review","rejected"].includes(row.status)):decision==="reject"&&row.status!=="rejected";
  }
  function controls() {
    const refresh = $("refresh-client-handoff"); if (refresh) refresh.disabled = busy || loading || !app?.state.authenticated;
    for (const item of buttons) item.button.disabled = !canWrite() || !item.allowed();
    for (const item of inputs) item.disabled = busy || loading || !app?.state.authenticated;
    if(busy||loading||!app?.state.authenticated)for(const button of localButtons)button.disabled=true;
  }
  function action(label, callback, allowed = () => true, cls = "button-secondary") {
    const button = app.button(label,cls,async()=>{if(!canWrite()||!allowed())return;await callback();}); buttons.push({button,allowed}); return button;
  }
  function localAction(label, callback, cls = "button-secondary") { const button = app.button(label,cls,async()=>{if(!app?.state.authenticated||busy||loading)return;await callback();}); button.type="button"; localButtons.push(button); return button; }
  function badge(parent, text, state="") { parent.append(e("span",`ch-badge ch-badge-${state}`,text)); }
  function draft(key, initial) { if (!drafts.has(key)) drafts.set(key,initial); return drafts.get(key); }
  function field(parent, label, object, name, {kind="text",wide=false,max=4000,choices=null} = {}) {
    const wrapper = e("label",`ch-field${wide?" ch-field-wide":""}${kind==="checkbox"?" ch-field-check":""}`,label);
    const input = e(choices?"select":kind==="textarea"?"textarea":"input"); input.name=name;
    if (choices) for (const [value,label] of choices) { const option=e("option","",label);option.value=value;input.append(option); }
    else if (kind!=="textarea") input.type=kind;
    input.maxLength=max; if (kind==="checkbox") input.checked=object[name] === true; else input.value=object[name] ?? "";
    input.addEventListener("input",()=>{object[name]=kind==="checkbox"?input.checked:input.value;});
    input.addEventListener("change",()=>{object[name]=kind==="checkbox"?input.checked:input.value;});
    wrapper.append(input);parent.append(wrapper);inputs.push(input);return input;
  }
  function submit(form,label,callback,allowed=()=>true) {
    const button=action(label,()=>{},allowed,"button-primary");button.type="submit";form.append(e("div","ch-actions"));form.childNodes[form.childNodes.length-1].append(button);
    form.addEventListener("submit",async event=>{event.preventDefault();if(!canWrite()||!allowed())return;try{await callback();}catch(error){notice=error.message;render();}});
  }
  function notePanel(parent) {
    if (!notice && !stale && !uncertain && !app.state.status?.paused) return;
    const box=e("section","ch-notice");box.setAttribute("role","status");
    if(notice)p(box,notice);
    if(stale)p(box,"Saved records changed or could not be read completely. Refresh before another action.");
    if(app.state.status?.paused)p(box,"Autonomy is paused. Saved records remain readable; changes and approvals are held.");
    if(uncertain){p(box,`The ${uncertain.kind} response was interrupted. Request ${uncertain.id}. Inspect the saved records and audit trail; nothing is retried automatically.`);
      const button=localAction("I inspected the saved state",()=>{if(!inspected)return;uncertain=null;notice="";render();});button.disabled=!inspected||busy||loading;box.append(button);}
    parent.append(box);
  }
  function overview(parent) {
    const hero=e("section","ch-intro"),copy=e("div");copy.append(e("span","eyebrow","PRIVATE CLIENT WORKSPACE"),e("h2","","From client intake to an approved handoff."));
    p(copy,"Your service is preparing the paperwork. Save the business that delivers the work, capture its customer's job, and prepare a quote. Record customer acceptance and completion separately before creating an invoice.","ch-muted");
    const scope=e("div","ch-scope");badge(scope,"LOCAL / OWNER CONTROLLED");p(scope,"Documents remain drafts. Approval does not send them, post a ledger entry or record payment.","ch-small ch-muted");hero.append(copy,scope);parent.append(hero);
    const totals=data.totals||{},metrics=e("section","ch-metrics");
    for(const [label,value] of [["Service clients",totals.clients??data.clients.length],["Job intakes",totals.intakes??data.intakes.length],["Documents",totals.documents??data.documents.length],["Ready on this page",data.summary?.ready_for_review??0]]){const card=e("div");card.append(e("span","",label),e("strong","",String(value)));metrics.append(card);}parent.append(metrics);
  }
  function pager(parent, category, parameter) {
    const page=data.pagination?.[category];if(!page || page.total <= (data.pagination.limit||50))return;
    const limit=data.pagination.limit||50,bar=e("div","ch-actions");
    const previous=localAction("Previous "+category,()=>{if(loading||busy)return;pages[parameter]=Math.max(0,page.offset-limit);load();});previous.disabled=loading||busy||page.offset===0;
    const next=localAction("Next "+category,()=>{if(loading||busy)return;pages[parameter]=page.offset+limit;load();});next.disabled=loading||busy||!page.has_more;
    bar.append(previous,e("span","ch-small ch-muted",`Showing ${page.offset+1}–${Math.min(page.offset+limit,page.total)} of ${page.total}`),next);parent.append(bar);
  }
  function profileForm(parent) {
    const details=e("details","ch-card");details.open=profileOpen||!data.clients.length;
    details.append(e("summary","",client?"Edit selected service client":"Add your first service client"));
    p(details,"This is the business whose paperwork you prepare. Its issuer details are copied into each intake. Later profile changes do not silently rewrite earlier documents.","ch-muted");
    const key=client?`profile-${client.id}`:"profile-new",value=draft(key,{id:client?.id||null,base_revision:client?.revision??null,name:client?.name||"",address:client?.address||"",email:client?.email||"",contact:client?.contact||"",notes:client?.notes||"",archived:client?.archived===true});
    const form=e("form"),fields=e("div","ch-fields");form.append(fields);
    field(fields,"Service client business name",value,"name",{max:300});field(fields,"Business contact",value,"contact",{max:500});
    field(fields,"Business email",value,"email",{kind:"email",max:500});field(fields,"Business address",value,"address",{max:500});
    field(fields,"Private client notes",value,"notes",{kind:"textarea",wide:true,max:2000});
    if(client)field(fields,"Archive this client for new intake",value,"archived",{kind:"checkbox",wide:true});
    if(client&&value.base_revision!==client.revision){p(form,"The saved client profile changed while these local edits were open. Discard them before changing the current profile.","ch-small");form.append(localAction("Discard local client edits",()=>{drafts.delete(key);render();}));}
    submit(form,client?"Save client profile":"Save new service client",async()=>{
      const identifier=value.id||newId("client-");await mutate("client profile","/service-desk/clients",clientPayload(value,data.revision,newId("client-save-"),identifier),result=>{selectedClient=result.entity_id||identifier;selectedIntake=null;selectedDocument=null;drafts.delete(key);profileOpen=false;},client?"PUT":"POST");
    },()=>!client||value.base_revision===client.revision);details.append(form);parent.append(details);
  }
  function recordForm(parent, value, key, {invoice=false,refreshClient=false}={}) {
    const fields=e("div","ch-fields");parent.append(fields);
    field(fields,"Job title",value,"title",{max:300});field(fields,"Document date",value,"document_date",{kind:"date"});
    field(fields,"Service date",value,"service_date",{kind:"date"});field(fields,invoice?"Invoice due date":"Quote valid-until date",value,"due_date",{kind:"date"});
    if(!invoice){field(fields,"Their customer name",value,"customer",{max:500});field(fields,"Their customer email",value,"email",{kind:"email",max:500});field(fields,"Their customer billing address",value,"address",{wide:true,max:500});}
    field(fields,"Original job notes and supplied instructions",value,"source_notes",{kind:"textarea",wide:true,max:12000});
    if(invoice){field(fields,"Completed-work evidence reference",value,"work_source",{wide:true,max:1000});field(fields,"Completion was supplied",value,"completed",{kind:"checkbox"});field(fields,"Completion evidence was checked",value,"work_approved",{kind:"checkbox"});return;}
    for(const [index,line]of value.lines.entries()){
      const section=e("section","ch-line-editor"),heading=e("div","ch-line-heading");heading.append(e("h4","",`Line ${index+1}`));
      const remove=localAction("Remove line "+(index+1),()=>{if(value.lines.length<=1)return;value.lines.splice(index,1);render();});remove.disabled=value.lines.length<=1||busy||loading;heading.append(remove);section.append(heading);
      const row=e("div","ch-fields");section.append(row);field(row,"Service description",line,"description",{wide:true,max:500});
      field(row,"Quantity, including hours such as 2.5",line,"quantity",{max:16}).inputMode="decimal";field(row,"Unit",line,"unit",{max:40});
      field(row,"Supplied unit price (USD, blank = unknown)",line,"unit_price",{max:16}).inputMode="decimal";field(row,"Approved price source reference",line,"source_ref",{max:1000});
      field(row,"Client approved this rate",line,"price_approved",{kind:"checkbox",wide:true});parent.append(section);
    }
    const add=localAction("Add service line",()=>{if(value.lines.length>=50)return;value.lines.push({description:"",quantity:"1",unit:"unit",unit_price:"",price_approved:false,source_ref:""});render();});add.disabled=value.lines.length>=50||busy||loading;parent.append(add);
    const evidence=e("div","ch-fields ch-line-editor");parent.append(evidence);
    field(evidence,"Supplied tax percentage (blank = unknown)",value,"tax",{max:8}).inputMode="decimal";field(evidence,"Tax instruction source reference",value,"tax_source",{max:1000});
    field(evidence,"Client supplied the tax instruction",value,"tax_approved",{kind:"checkbox",wide:true});
    field(evidence,"Permission or engagement reference",value,"permission_source",{wide:true,max:1000});field(evidence,"Permission to handle these customer records is recorded",value,"permission_confirmed",{kind:"checkbox",wide:true});
    if(refreshClient)field(evidence,"Refresh issuer details from the current client profile; a new review will be required",value,"refresh_client",{kind:"checkbox",wide:true});
    p(parent,"Only supplied facts are used. Missing prices, permission, dates or tax instructions stay unresolved. Quantities and prices use exact decimal text.","ch-form-help ch-small");
  }
  function fromRecord(row) {
    return {title:row?.title||"",customer:row?.customer?.name||"",email:row?.customer?.email||"",address:row?.customer?.address||"",source_notes:row?.source_notes||"",
      service_date:row?.service_date||"",document_date:row?.document_date||"",due_date:row?.due_date||"",
      lines:(row?.lines||[{description:"",quantity:"1",unit:"unit",unit_price:null,price_approved:false,source_ref:null}]).map(line=>({...line,quantity:string(line.quantity)||String(line.quantity??"1"),unit_price:line.unit_price??"",source_ref:line.source_ref??""})),
      tax:Number.isInteger(row?.tax?.bps)?(row.tax.bps/100).toFixed(2):"",tax_source:row?.tax?.source_ref||"",tax_approved:row?.tax?.approved===true,
      permission_source:row?.permission?.source_ref||"",permission_confirmed:row?.permission?.confirmed===true,
      work_source:row?.work?.source_ref||"",completed:row?.work?.completed===true,work_approved:row?.work?.approved===true,refresh_client:false};
  }
  function intakeForm(parent) {
    if(!client)return;
    const details=e("details","ch-card");details.open=intakeOpen;details.append(e("summary","","Capture a new customer job"));
    p(details,`Issuer: ${client.name}. Save the client's original notes and approved inputs. This captures a private intake; it does not send a quote or assert a sale.` ,"ch-muted");
    if(client.archived){p(details,"This client is archived. Unarchive its profile before adding a job.");parent.append(details);return;}
    const key="intake-new-"+client.id,value=draft(key,fromRecord(null)),form=e("form");recordForm(form,value,key);
    submit(form,"Save job intake",async()=>{
      const identifier=newId("job-");await mutate("job intake","/service-desk/intakes",intakePayload(value,data.revision,newId("intake-save-"),identifier,client.id),result=>{selectedIntake=result.entity_id||identifier;selectedDocument=null;drafts.delete(key);intakeOpen=false;});
    });details.append(form);parent.append(details);
  }
  function workspaceTools(parent) {
    const toolbar=e("div","ch-toolbar"),label=e("label","ch-field","Service client"),select=e("select");
    const clients=[...data.clients];if(client&&!clients.some(row=>row.id===client.id))clients.unshift(client);
    if(!clients.length){const option=e("option","","No clients saved yet");option.value="";select.append(option);}
    for(const item of clients){const option=e("option","",item.name+(item.archived?" · archived":""));option.value=item.id;select.append(option);}select.value=selectedClient||"";inputs.push(select);
    select.addEventListener("change",async()=>{if(busy||loading)return;selectedClient=select.value||null;selectedIntake=null;selectedDocument=null;pages.intake_offset=0;pages.document_offset=0;profileOpen=false;await load();});label.append(select);toolbar.append(label);
    toolbar.append(localAction("Add another service client",async()=>{selectedClient=null;selectedIntake=null;selectedDocument=null;client=null;intake=null;document=null;profileOpen=true;pages.intake_offset=0;pages.document_offset=0;render();await load();}));
    if(client)toolbar.append(localAction("New customer job",()=>{intakeOpen=true;render();}));parent.append(toolbar);pager(parent,"clients","client_offset");
    profileForm(parent);intakeForm(parent);
  }
  function queues(parent) {
    const section=e("section","ch-queue");section.append(e("span","eyebrow","WORK QUEUE"),e("h3","","Saved job intakes"));p(section,selectedClient?`Jobs and documents for ${client?.name||"the selected service client"}.`:"Jobs and documents across all saved service clients.","ch-small ch-muted");
    for(const row of data.intakes){const button=localAction("",async()=>{if(busy||loading)return;selectedIntake=row.id;selectedDocument=null;selectedClient=row.client_id;await load();},"ch-queue-item");button.setAttribute("aria-label",`Open job ${row.title}`);button.setAttribute("aria-pressed",String(row.id===selectedIntake));button.append(e("span","",row.id),e("strong","",row.title),e("span","",row.can_generate_quote?"Ready to prepare quote":"Quote already prepared"));section.append(button);}
    if(!data.intakes.length)p(section,"No jobs are saved. Add a service client, then capture the first job.","ch-muted");pager(section,"intakes","intake_offset");
    section.append(e("h3","","Quote and invoice drafts"));
    for(const row of data.documents){const button=localAction("",async()=>{if(busy||loading)return;selectedDocument=row.id;selectedIntake=row.intake_id;selectedClient=row.client_id;await load();},"ch-queue-item");button.setAttribute("aria-label",`Open ${row.reference||row.kind} ${row.title}`);button.setAttribute("aria-pressed",String(row.id===selectedDocument));button.append(e("span","",`${row.reference||row.id} · ${row.kind}`),e("strong","",row.title),e("span","",row.provider_name||row.client_id));badge(button,row.voided?"Voided":pretty(row.status),row.status);section.append(button);}
    if(!data.documents.length)p(section,"No document drafts yet. Select a saved intake to generate its quote.","ch-muted");pager(section,"documents","document_offset");parent.append(section);
  }
  function intakePreview(parent) {
    if(!intake){const box=e("section","ch-empty");box.append(e("h3","","Your client work starts here."));p(box,"Save a real service client's issuer details, then capture a job using records you are permitted to handle. The fictional demonstration stays in its own workspace.","ch-muted");parent.append(box);return;}
    const box=e("section","ch-card");box.append(e("span","eyebrow","SAVED INTAKE"),e("h3","",intake.title));
    p(box,`For ${intake.customer?.name||"customer details awaiting input"} · Issuer ${intake.provider?.name||client?.name||"not supplied"}.`,"ch-muted");p(box,intake.source_notes,"ch-source-note");
    const list=e("ul","ch-small ch-muted");for(const line of intake.lines||[])list.append(e("li","",`${line.description}: ${line.quantity} ${line.unit||"unit"} at ${line.unit_price===null?"unknown rate":"$"+line.unit_price}; ${line.price_approved?"rate approval supplied":"rate approval missing"}.`));box.append(list);
    p(box,"An intake is an immutable record of supplied information. Corrections are made to its generated quote and require a new review.","ch-small ch-muted");
    box.append(action("Generate quote draft",async()=>{await mutate("quote generation","/service-desk/quotes",{...meta(data.revision,newId("quote-generate-")),intake_id:intake.id,content_sha256:intake.content_sha256},result=>{selectedDocument=result.entity_id;});},()=>intake.can_generate_quote!==false&&!intake.quote_id,"button-primary"));parent.append(box);
  }
  function documentPreview(parent,row) {
    const sheet=e("article","ch-preview");sheet.setAttribute("aria-label",`${row.kind} draft preview`);
    const header=e("header","ch-preview-header"),provider=e("div");provider.append(e("strong","",row.provider?.name||"Issuer details missing"),e("span","",row.provider?.address||"Address not supplied"));
    const heading=e("div");heading.append(e("h3","",row.voided?"VOIDED DRAFT":row.kind==="invoice"?"INVOICE DRAFT":"QUOTE DRAFT"),e("span","",row.reference||row.id));header.append(provider,heading);sheet.append(header);
    const metadata=e("div","ch-preview-meta"),recipient=e("div");recipient.append(e("span","",row.kind==="invoice"?"BILL TO":"PREPARED FOR"),e("strong","",row.customer?.name||"Customer name missing"),e("span","",row.customer?.address||"Billing address missing"));if(row.customer?.email)recipient.append(e("span","",row.customer.email));
    const dates=e("dl");for(const [label,value]of [["Document date",row.document_date],["Service date",row.service_date],[row.kind==="invoice"?"Due date":"Valid until",row.due_date]])dates.append(e("dt","",label),e("dd","",value||"Not supplied"));metadata.append(recipient,dates);sheet.append(metadata,e("h4","",row.title));
    const scroll=e("div","ch-table-scroll"),table=e("table","ch-lines"),head=e("thead"),thead=e("tr");for(const name of ["Description","Quantity","Rate","Amount"])thead.append(e("th","",name));head.append(thead);table.append(head);const body=e("tbody");
    for(const line of row.lines||[]){const tr=e("tr");tr.append(e("td","",line.description),e("td","",`${line.quantity} ${line.unit||"unit"}`),e("td","",line.unit_price===null?"Unknown":"$"+line.unit_price),e("td","",money(line.line_total_cents)));body.append(tr);}table.append(body);scroll.append(table);sheet.append(scroll);
    const totals=e("dl","ch-totals");for(const [label,value]of [["Subtotal",money(row.subtotal_cents)],["Supplied tax",money(row.tax_cents)],["Draft total",row.total_cents===null?"HELD":money(row.total_cents)]])totals.append(e("dt","",label),e("dd","",value));sheet.append(totals);
    p(sheet,row.voided?"VOIDED · retained for history. This record is not eligible for handoff.":"DRAFT / NOT SENT · No ledger posting or payment is recorded by this workspace. Document face value belongs to the service business; it is not your paperwork service fee.","ch-preview-foot");parent.append(sheet);
  }
  function checks(parent,row) {
    const card=e("section","ch-card");card.append(e("span","eyebrow","SUPPLIED EVIDENCE"),e("h3","","Source notes and unresolved inputs"));p(card,row.source_notes,"ch-source-note ch-muted");
    if(row.exceptions?.length)for(const item of row.exceptions){const gap=e("div","ch-exception");gap.append(e("strong","",fieldLabel(item.field)),e("p","",item.message||"Owner input required."));card.append(gap);}
    else p(card,"Required supplied fields and arithmetic pass local checks. This does not independently verify customer consent, tax treatment or completed work.","ch-muted");
    const refs=e("ul","ch-small ch-muted");for(const line of row.lines||[])refs.append(e("li","",`${line.description}: ${line.source_ref||"price source missing"}`));refs.append(e("li","",`Tax: ${row.tax?.source_ref||"instruction source missing"}`),e("li","",`Permission: ${row.permission?.source_ref||"reference missing"}`));if(row.kind==="invoice")refs.append(e("li","",`Completion: ${row.work?.source_ref||"reference missing"}`));card.append(refs);parent.append(card);
  }
  function editDocument(parent,row) {
    if(row.can_edit===false||row.voided)return;
    const details=e("details","ch-card");details.append(e("summary","",row.kind==="invoice"?"Correct invoice dates or work evidence":"Correct quote inputs"));
    p(details,row.kind==="invoice"?"Invoice pricing, issuer, customer, permission and tax remain the accepted quote snapshot. To correct those, void the unsent invoice and revise the quote before generating another invoice.":"Saving corrections clears the internal review and any recorded customer acceptance. Previously exported packets keep their original snapshots.","ch-muted");
    const key="document-edit-"+row.id,value=draft(key,{...fromRecord(row),base_hash:row.content_sha256}),form=e("form");recordForm(form,value,key,{invoice:row.kind==="invoice",refreshClient:row.kind==="quote"});
    if(value.base_hash!==row.content_sha256){p(form,"The saved document changed while these local edits were open. Discard them before editing the new version.","ch-small");form.append(localAction("Discard local edits",()=>{drafts.delete(key);render();}));}
    submit(form,"Save corrections and require new review",async()=>{
      const changes=row.kind==="invoice"?{title:required(value.title,"Job title",300),source_notes:required(value.source_notes,"Source notes",12000),service_date:nullable(value.service_date),document_date:nullable(value.document_date),due_date:nullable(value.due_date),work:{completed:value.completed===true,approved:value.work_approved===true,source_ref:nullable(value.work_source)}}:recordFields(value);
      const body={...documentMeta(row,data.revision,newId("document-edit-")),changes};if(row.kind==="quote")body.refresh_client=value.refresh_client===true;
      await mutate("document correction","/service-desk/documents",body,()=>drafts.delete(key),"PUT");
    },()=>value.base_hash===document?.content_sha256);details.append(form);parent.append(details);
  }
  function lineage(parent,row) {
    if(!row.parent_quote_id&&!row.voided&&!row.history?.length)return;
    const box=e("details","ch-card");box.append(e("summary","","Source snapshot and revision history"));
    if(row.parent_quote_id){p(box,`Accepted source quote: ${row.parent_quote_id}. The invoice retains that quote's issuer, customer, prices, tax and permission snapshot.`,"ch-muted");if(row.parent?.content_sha256)p(box,`Accepted source fingerprint: ${row.parent.content_sha256}`,"ch-hash");const response=row.parent?.acceptance;if(response)p(box,`Customer acceptance reported by owner: ${response.contact||"contact not supplied"} · ${stamp(response.observed_at)} · ${response.source_ref||"reference not supplied"}. No independent customer verification is established.`,"ch-small ch-muted");}
    if(row.voided)p(box,`Voided unsent draft: ${row.voided.reason||"reason recorded"} · ${stamp(row.voided.voided_at)}. Earlier exports remain historical snapshots.`,"ch-muted");
    if(row.history?.length){p(box,`Showing ${row.history.length} of ${row.history_retained??row.history.length} retained content revisions. Internal reviews and customer responses are separately recorded observations.`,"ch-small ch-muted");const list=e("ol","ch-revisions");for(const item of row.history){const li=e("li");li.append(e("strong","",`Revision ${item.revision}${item.current?" · current":""}`),e("span","ch-hash",item.content_sha256));list.append(li);}box.append(list);}parent.append(box);
  }
  function reviewForm(parent,row) {
    const card=e("section","ch-card");card.append(e("span","eyebrow","1 / INTERNAL DOCUMENT REVIEW"),e("h3","",row.status==="approved"?"Internal approval recorded":"Check this exact draft"));
    p(card,"This is your paperwork review, separate from the customer's acceptance of the job. Edits invalidate it. Approval does not send or issue the document.","ch-muted");
    if(row.review)p(card,`${pretty(row.review.decision)} · ${stamp(row.review.reviewed_at)}${row.review.note?" · "+row.review.note:""}`,"ch-small");
    const key="review-"+row.id,value=draft(key,{note:""});field(card,"Review note (required to reject)",value,"note",{kind:"textarea",max:2000});
    const actions=e("div","ch-actions");actions.append(action("Approve exact draft",()=>review(row,"approve",value.note),()=>canReview(row,"approve"),"button-primary"),action("Reject draft with note",()=>review(row,"reject",value.note),()=>canReview(row,"reject")));card.append(actions);if(row.invoice_id)p(card,"This quote is locked by its active invoice. Its approved source snapshot remains unchanged.","ch-small ch-muted");p(card,`Record fingerprint: ${row.content_sha256}`,"ch-hash");parent.append(card);
  }
  function acceptanceForm(parent,row) {
    if(row.kind!=="quote")return;
    const card=e("section","ch-card");card.append(e("span","eyebrow","2 / CUSTOMER RESPONSE"),e("h3","","Record customer acceptance"));
    p(card,"Record the response from your service client's customer. Supply who responded, when, and the evidence reference. This remains owner-reported; Ariadne does not contact or authenticate that person.","ch-muted");
    if(row.acceptance)p(card,`${row.acceptance.accepted?"Accepted":"Declined"} · ${row.acceptance.contact||"contact recorded"} · ${stamp(row.acceptance.observed_at)} · ${row.acceptance.source_ref||"evidence recorded"}`,"ch-small");
    const key="acceptance-"+row.id,value=draft(key,{response:"",contact:"",observed_at:"",source_ref:"",note:""}),form=e("form"),fields=e("div","ch-fields");form.append(fields);
    field(fields,"Customer's actual response",value,"response",{choices:[["","Choose a recorded response"],["accepted","Accepted the quote"],["declined","Declined the quote"]]});field(fields,"Customer contact who responded",value,"contact",{max:300});
    field(fields,"When the customer responded (this device's local time)",value,"observed_at",{kind:"datetime-local"});field(fields,"Response evidence reference",value,"source_ref",{max:1000});field(fields,"Response note",value,"note",{kind:"textarea",wide:true,max:2000});p(form,`Quote date checks use ${data.workflow_timezone||"America/Chicago"}. Response time is saved with its UTC offset.`,"ch-small ch-muted");
    submit(form,"Record customer response",async()=>await mutate("customer response","/service-desk/acceptance",acceptancePayload(row,value,data.revision,newId("customer-response-")),()=>drafts.delete(key)),()=>row.can_accept===true);card.append(form);if(!row.can_accept)p(card,"An internally approved current quote is required. A quote with an active invoice cannot be changed here.","ch-small ch-muted");parent.append(card);
  }
  function invoiceForm(parent,row) {
    if(row.kind!=="quote")return;
    const card=e("section","ch-card");card.append(e("span","eyebrow","3 / COMPLETED WORK"),e("h3","","Generate the invoice draft"));
    p(card,"The invoice copies the accepted quote's prices, customer, issuer and tax snapshot. Record completed-work evidence and invoice dates separately. Only one active invoice is allowed for a quote.","ch-muted");
    if(row.invoice_id){p(card,"An active invoice already exists for this quote.","ch-small");card.append(localAction("Open linked invoice",async()=>{selectedDocument=row.invoice_id;await load();}));parent.append(card);return;}
    const key="invoice-"+row.id,value=draft(key,{service_date:"",document_date:"",due_date:"",work_source:"",completed:false,work_approved:false}),form=e("form"),fields=e("div","ch-fields");form.append(fields);
    field(fields,"Actual completed service date",value,"service_date",{kind:"date"});field(fields,"Invoice document date",value,"document_date",{kind:"date"});field(fields,"Invoice due date",value,"due_date",{kind:"date"});field(fields,"Completed-work evidence reference",value,"work_source",{max:1000});field(fields,"The work was reported completed",value,"completed",{kind:"checkbox",wide:true});field(fields,"I checked the supplied completion evidence",value,"work_approved",{kind:"checkbox",wide:true});
    submit(form,"Generate invoice draft from accepted quote",async()=>await mutate("invoice generation","/service-desk/invoices",invoicePayload(row,value,data.revision,newId("invoice-generate-")),result=>{selectedDocument=result.entity_id;drafts.delete(key);}),()=>row.can_invoice===true);card.append(form);if(!row.can_invoice)p(card,"Current internal approval and recorded customer acceptance are required before invoice generation.","ch-small ch-muted");parent.append(card);
  }
  function exportAndVoid(parent,row) {
    const card=e("section","ch-card");card.append(e("span","eyebrow","4 / OWNER HANDOFF"),e("h3","","Export an approved draft"));
    p(card,"Save an HTML customer draft and a private JSON evidence file for this exact approved document. JSON retains source notes and the issuer snapshot, including private client notes. Review it before sharing. The printable draft is not sent, posted to accounting or marked paid.","ch-muted");
    card.append(action("Export approved draft",async()=>await mutate("document export","/service-desk/export",documentMeta(row,data.revision,newId("client-export-"))),()=>row.status==="approved"&&!row.voided&&!data.export_hold,"button-primary"));
    if(data.export_hold){p(card,data.export_hold.reason||"An earlier export is incomplete and needs inspection.","ch-small");card.append(action("Acknowledge inspected export hold",acknowledgeExport));}
    const saved=data.latest_export;
    if(saved?.status==="completed"&&(saved.document_id||saved.entity_id)===row.id&&saved.content_sha256===row.content_sha256&&Array.isArray(saved.artifacts)){
      const box=e("div","ch-artifacts");p(box,"Latest saved export retains its original approved snapshot.","ch-small ch-muted");
      for(const item of saved.artifacts){let path;try{path=relativePath(item.path);}catch{continue;}box.append(localAction(path.endsWith(".html")?"Download printable draft":"Inspect handoff data",()=>path.endsWith(".html")&&app.downloadArtifact?app.downloadArtifact(path):app.openArtifact(path)));p(box,path,"ch-hash");}card.append(box);
    }
    if(row.can_void){const value=draft("void-"+row.id,{reason:""});field(card,"Reason for voiding this unsent draft",value,"reason",{kind:"textarea",max:2000});card.append(action("Void unsent draft",async()=>{
      let reason;try{reason=required(value.reason,"Void reason",2000);}catch(error){notice=error.message;render();return;}
      const marker=generation,token=app.state.token,revision=data.revision,hash=row.content_sha256;
      const accepted=await app.confirmAction("Void this unsent draft?","The record and previous exports remain in history. This does not send a cancellation, issue a credit note, reverse a payment or delete data.","Void unsent draft","Keep draft");
      if(!accepted||!current(marker,token)||data.revision!==revision||document?.content_sha256!==hash)return;
      await mutate("void draft","/service-desk/void",{...documentMeta(row,revision,newId("draft-void-")),reason},()=>drafts.delete("void-"+row.id));
    }));}parent.append(card);
  }
  function activity(parent) {
    if(!data.audit?.length)return;const section=e("section","ch-audit");section.append(e("span","eyebrow","PRIVATE ACTIVITY"),e("h3","","Recorded local changes"));const list=e("ol");
    for(const row of data.audit.slice(0,8)){const li=e("li");li.append(e("strong","",pretty(row.action||"Local action")),e("span","",`${stamp(row.created_at)} · ${pretty(row.status||"saved")} · ${row.entity_id||row.request_id||"receipt saved"}`));list.append(li);}section.append(list);parent.append(section);
  }
  function render() {
    const root=$("client-handoff-content");if(!root||!app?.state.authenticated)return;root.replaceChildren();buttons=[];inputs=[];localButtons=[];
    if(!data){p(root,loading?"Reading the private client workspace…":notice||"Open this workspace to read saved client records.","ch-muted");controls();return;}
    overview(root);notePanel(root);workspaceTools(root);
    const layout=e("div","ch-layout"),queue=e("div"),work=e("div");queues(queue);layout.append(queue,work);
    if(document){documentPreview(work,document);checks(work,document);lineage(work,document);editDocument(work,document);const stages=e("div","ch-workflow");reviewForm(stages,document);acceptanceForm(stages,document);invoiceForm(stages,document);exportAndVoid(stages,document);work.append(stages);}
    else intakePreview(work);root.append(layout);activity(root);controls();
  }
  function statusPath() {const values=Object.entries(pages).filter(([,value])=>value>0).map(([name,value])=>`${name}=${value}`);if(selectedClient)values.push("client_id="+encodeURIComponent(selectedClient));return "/service-desk"+(values.length?"?"+values.join("&"):"");}
  async function load() {
    if(!app?.state.authenticated||loading||busy)return;loading=true;const marker=generation,token=app.state.token;controls();
    try{
      let next=await app.api(statusPath());if(!current(marker,token))return;
      if(next?.demo_only!==false||!Number.isInteger(next.revision)||!Array.isArray(next.clients)||!Array.isArray(next.intakes)||!Array.isArray(next.documents))throw new Error("The private workspace response could not be verified.");
      if(!selectedClient&&next.clients.length&&!profileOpen){selectedClient=next.clients[0].id;next=await app.api(statusPath());if(!current(marker,token))return;if(next?.demo_only!==false||!Number.isInteger(next.revision)||!Array.isArray(next.clients)||!Array.isArray(next.intakes)||!Array.isArray(next.documents))throw new Error("The selected client workspace response could not be verified.");}
      data=next;client=null;intake=null;document=null;
      const reads=[];if(selectedClient)reads.push(["client","clients",selectedClient]);if(selectedIntake)reads.push(["intake","intakes",selectedIntake]);if(selectedDocument)reads.push(["document","documents",selectedDocument]);
      const details=await Promise.all(reads.map(async([kind,path,id])=>({kind,value:await app.api(`/service-desk/${path}/${encodeURIComponent(id)}`)})));
      if(!current(marker,token))return;
      for(const item of details){if(item.value.revision!==data.revision)throw new Error("The workspace changed while loading document details. Refresh before acting.");if(item.kind==="client")client=item.value.client;if(item.kind==="intake")intake=item.value.intake;if(item.kind==="document")document=item.value.document;}
      if(client&&client.id!==selectedClient||document&&document.client_id!==selectedClient||intake&&intake.client_id!==selectedClient)throw new Error("Selected records belong to a different service client. Refresh and choose them again.");
      if(selectedClient&&(data.intakes.some(row=>row.client_id!==selectedClient)||data.documents.some(row=>row.client_id!==selectedClient)))throw new Error("The selected client list could not be verified. Refresh before another action.");
      stale=false;notice="";if(uncertain)inspected=true;
    }catch(error){if(current(marker,token)){notice=error.message;stale=true;}}
    finally{if(current(marker,token)){loading=false;render();}}
  }
  async function mutate(kind,path,body,onSuccess,method="POST") {
    if(!canWrite())return;busy=true;notice="";const marker=generation,token=app.state.token;controls();
    try{
      const result=await app.api(path,{method,body});if(!current(marker,token))return;
      const successStatus=path==="/service-desk/export/acknowledge"?"acknowledged":"completed";
      if(result?.status!==successStatus)throw Object.assign(new Error(result?.error||result?.reason||"The local change did not complete. Inspect saved state before continuing."),{status:409});
      if(onSuccess)onSuccess(result);app.toast("Saved locally. Nothing sent, posted or paid.");
    }catch(error){if(!current(marker,token))return;notice=error.message;if(error.status===409)stale=true;else if(!error.status||error.status>=500)uncertain={kind,id:body.request_id};}
    finally{if(current(marker,token)){busy=false;render();if(!stale&&!uncertain)await load();}}
  }
  async function review(row,decision,note) {
    if(!canWrite()||!canReview(row,decision))return;
    try{if(decision==="reject")required(note,"Rejection note",2000);const body={...documentMeta(row,data.revision,newId("document-review-")),decision,note:nullable(note)};await mutate("document review","/service-desk/review",body);}catch(error){notice=error.message;render();}
  }
  async function acknowledgeExport() {
    if(!canWrite()||!data.export_hold||!app.confirmAction)return;const marker=generation,token=app.state.token,revision=data.revision,hold={...data.export_hold};
    const accepted=await app.confirmAction("Acknowledge the inspected export hold?",`${hold.reason||"Earlier export completion is uncertain."} This does not adopt or replay any partial files. A later export is a separately requested new packet.` ,"Acknowledge reviewed hold","Keep held");
    if(!accepted||!current(marker,token)||data.revision!==revision||data.export_hold?.request_id!==hold.request_id)return;
    await mutate("export acknowledgement","/service-desk/export/acknowledge",{request_id:hold.request_id,expected_revision:revision});
  }
  function status(){controls();}
  function locked(){generation++;data=null;client=null;intake=null;document=null;selectedClient=null;selectedIntake=null;selectedDocument=null;loading=false;busy=false;stale=false;uncertain=null;inspected=false;notice="";drafts=new Map();profileOpen=false;intakeOpen=false;pages={client_offset:0,intake_offset:0,document_offset:0};buttons=[];inputs=[];localButtons=[];$("client-handoff-content")?.replaceChildren();controls();}
  function attach(value){app=value;const root=$("client-handoff-content");if(!root||root===attached)return;attached=root;$("refresh-client-handoff")?.addEventListener("click",load);controls();}
  window.ariadneClientHandoff={attach,load,status,locked,money,relativePath,taxBps,linePayload,recordFields,clientPayload,intakePayload,acceptancePayload,invoicePayload,documentMeta,fieldLabel,canReview,review,acknowledgeExport};
})();
