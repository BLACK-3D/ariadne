"use strict";

// Saved evidence only. Opening this desk cannot start a producer or a replay.
(() => {
  let app, attached, board=null, detail=null, receiptInspection=null, selected="", importPath="", ruviewPath="", sourcePolicy="esp32_only", notice="";
  let visible=false, busy=false, loading=false, stale=false, uncertain=null, generation=0, selection=0;
  let writes=[], reads=[], fields=[];
  const $=id=>document.getElementById(id);
  const text=(value,max=800)=>typeof value==="string"?value.slice(0,max):"";
  const list=(value,max)=>Array.isArray(value)?value.slice(0,max):[];
  const ident=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const hash=/^[a-f0-9]{64}$/;
  const e=(tag,cls="",value)=>app.node(tag,cls,value);
  const p=(parent,value,cls="perception-note")=>parent.append(e("p",cls,value));
  const active=()=>Boolean(app?.state.authenticated&&visible&&app.state.view==="perception");
  const current=(mark,token)=>mark===generation&&active()&&token===app.state.token;
  const state=()=>board?.status&&typeof board.status==="object"?board.status:board||{};
  const revision=()=>Number.isSafeInteger(board?.revision)?board.revision:state().revision;
  const records=()=>list(board?.recordings||board?.history?.runs||state().recordings||board?.runs,32);
  const paused=()=>app?.state.status?.paused!==false||state().paused!==false;
  const held=()=>Boolean(state().hold);
  const ready=()=>active()&&board&&!busy&&!loading&&!stale&&!uncertain&&!paused()&&!held()&&Number.isSafeInteger(revision())&&revision()>=0;
  const id=()=>"perception-ui-"+crypto.randomUUID().replaceAll("-","");
  const RU_PROFILE="ruview-sensing-094e140-1",RU_FORMAT="ruview_native_jsonl";
  const RU_CLOCK="producer_server_timestamp_not_radio_capture";
  const sourcePolicies=["esp32_only","recorded_any"];
  const operation=kind=>kind==="demo"?"synthetic_demo":kind==="import-ruview"?"import_ruview":kind==="ruview-demo"?"ruview_demo":kind;
  const ruviewCatalog=()=>board?.catalog?.ruview||board?.ruview||state().ruview;
  const ruviewSupported=()=>{const value=ruviewCatalog();return value?.available===true&&value.profile===RU_PROFILE&&value.source_format===RU_FORMAT&&value.source_clock===RU_CLOCK&&value.hardware_qualified===false&&value.live_available===false&&sourcePolicies.every(policy=>value.source_policies?.includes(policy));};
  const cleanId=value=>{if(typeof value!=="string"||value.length>64||!ident.test(value))throw new Error("Choose an ordinary saved recording identifier.");return value;};

  function recordingPath(value,extension) {
    const raw=text(value,1001),valuePath=raw.replaceAll("\\","/");
    if(raw!==raw.trim()||!valuePath||valuePath.length>1000||!valuePath.toLowerCase().endsWith(extension)||valuePath.startsWith("/")||/[\u0000-\u001f\u007f:<>"|?*]/.test(valuePath)||valuePath.split("/").some(part=>!part||part==="."||part===".."||/[. ]$/.test(part)||/^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)/i.test(part)))throw new Error("Enter an ordinary "+extension+" path inside Ariadne Workspace, such as perception/room-session"+extension+".");
    return valuePath;
  }
  const path=value=>recordingPath(value,".json");
  const ruviewRecordingPath=value=>recordingPath(value,typeof value==="string"&&value.toLowerCase().endsWith(".ndjson")?".ndjson":".jsonl");
  function request(kind,extra={},requestId=id()) {
    if(!Number.isSafeInteger(revision())||revision()<0)throw new Error("Refresh saved evidence before creating a receipt.");
    const body={request_id:cleanId(requestId),expected_revision:revision()};
    if(kind==="import")body.path=path(extra.path);
    else if(kind==="import-ruview"){
      if(!ruviewSupported())throw new Error("The supported local RuView recording adapter is unavailable. Refresh its status before importing.");
      body.path=ruviewRecordingPath(extra.path);body.source_policy=extra.source_policy??"esp32_only";
      if(!sourcePolicies.includes(body.source_policy))throw new Error("Choose the ESP32-only or explicit recorded-sources policy.");
    }
    else if(kind==="replay")body.recording_id=cleanId(extra.recording_id);
    else if(kind==="ruview-demo"){if(!ruviewSupported())throw new Error("The supported local RuView recording adapter is unavailable. Refresh its status before creating a fixture.");}
    else if(kind!=="demo")throw new Error("Unsupported awareness action.");
    return body;
  }
  function controls() {
    const refresh=$("refresh-perception");if(refresh)refresh.disabled=!active()||busy||loading;
    for(const {node,allowed} of writes)node.disabled=!ready()||!allowed();
    for(const node of reads)node.disabled=!active()||busy||loading;
    for(const node of fields)node.disabled=!active()||busy||loading;
  }
  function writeButton(label,callback,allowed=()=>true,primary=false) {
    const node=app.button(label,primary?"button button-primary":"button button-secondary",async()=>{if(!ready()||!allowed())return;await callback();});
    writes.push({node,allowed});return node;
  }
  function readButton(label,callback,cls="button button-secondary") {
    const node=app.button(label,cls,async()=>{if(!active()||busy||loading)return;await callback();});reads.push(node);return node;
  }
  function chip(parent,label,kind="") {parent.append(e("span","perception-chip"+(kind?" perception-chip-"+kind:""),label));}
  function metric(parent,label,value,note) {const item=e("div","perception-metric");item.append(e("span","",label),e("strong","",value),e("small","",note));parent.append(item);}
  function row(parent,label,value) {const item=e("div","perception-fact");item.append(e("dt","",label),e("dd","",value||"Unknown"));parent.append(item);}
  const knownMode=value=>value==="simulation"?"SIMULATION":value==="replay"?"REPLAY":"MODE UNKNOWN";
  const originLabel=value=>value==="synthetic"?"SYNTHETIC":value==="live"?"ORIGINAL LIVE CAPTURE · UNQUALIFIED":"ORIGIN UNKNOWN";
  function origins(value) {return list(value,5).map(originLabel).join(" / ")||"ORIGIN UNKNOWN";}
  const count=value=>Number.isSafeInteger(value)&&value>=0?String(value):"—";
  function offset(value) {if(!Number.isFinite(value)||value<0)return "Time unknown";const sec=Math.floor(value/1000);return "+"+String(Math.floor(sec/60)).padStart(2,"0")+":"+String(sec%60).padStart(2,"0")+"."+String(Math.floor(value%1000)).padStart(3,"0");}
  function fresh(zone) {
    const label=zone.physical_freshness==="fresh"?"Fresh at recording":zone.physical_freshness==="stale"?"Stale at recording":"Freshness unknown";
    return label+" · source age "+(Number.isFinite(zone.source_age_seconds)&&zone.source_age_seconds>=0?zone.source_age_seconds.toFixed(1)+" s":"unknown");
  }
  function verifiedDetail(value,recordingId) {
    const receipt=value?.receipt;
    if(!receipt||receipt.request_id!==recordingId||receipt.status!=="completed"||receipt.verified!==true)throw new Error("This recording lacks a matching completed, hash-verified receipt. Evidence is withheld.");
    if(!value.evidence||!["replay","simulation"].includes(value.evidence.processing_mode))throw new Error("This saved evidence has an unsupported mode. Evidence is withheld.");
    if(["import_ruview","ruview_demo"].includes(receipt.operation)||receipt.source_profile||value.adapter||value.evidence.adapter){
      const adapter=value.adapter,compact=value.evidence.adapter;
      if(!adapter||adapter.profile!==RU_PROFILE||receipt.source_profile!==RU_PROFILE||adapter.source_format!==RU_FORMAT||receipt.source_format!==RU_FORMAT||!sourcePolicies.includes(adapter.source_policy)||adapter.source_policy!==receipt.source_policy||adapter.hardware_qualified!==false||receipt.hardware_qualified!==false||adapter.physical_clock_available!==false||adapter.source_clock!==RU_CLOCK||receipt.source_clock!==RU_CLOCK||!hash.test(receipt.source_sha256)||!hash.test(receipt.bundle_sha256)||adapter.source_sha256!==receipt.source_sha256||adapter.bundle_sha256!==receipt.bundle_sha256||!compact||compact.source_profile!==RU_PROFILE||compact.source_format!==RU_FORMAT||compact.source_policy!==adapter.source_policy||compact.source_clock!==RU_CLOCK||compact.hardware_qualified!==false||!Array.isArray(adapter.frames))throw new Error("RuView adapter provenance does not match the verified recording. Evidence is withheld.");
    }
    return value;
  }
  function renderHero(root) {
    const hero=e("section","perception-hero"),copy=e("div","perception-hero-copy"),badges=e("div","perception-chips");
    chip(badges,"RECORDING ONLY");chip(badges,"NO LIVE SENSORS","quiet");
    if(list(detail?.evidence?.capture_origins,5).includes("synthetic"))chip(badges,"SYNTHETIC","synthetic");
    copy.append(badges,e("h2","","Awareness, with evidence."));
    p(copy,"Inspect what a recording supports, what disagrees, and what remains unknown. Physical freshness belongs to the original evidence time.");
    const emblem=e("div","perception-emblem");emblem.setAttribute("aria-hidden","true");emblem.append(e("span","","LOCAL"),e("strong","","EVIDENCE"),e("small","","RECORDING / REPLAY"));
    hero.append(copy,emblem);root.append(hero);
    const metrics=e("div","perception-metrics"),evidence=detail?.evidence;
    metric(metrics,"SAVED RECORDINGS",count(records().length),"Showing up to 32 retained receipts");
    metric(metrics,"SELECTED ZONES",evidence?count(list(evidence.zones,24).length):"—",evidence?"State at the recording reference time":"Select verified evidence below");
    metric(metrics,"PROCESSING",evidence?knownMode(evidence.processing_mode):"IDLE","Local deterministic evidence · no model calls");root.append(metrics);
  }
  function renderControls(parent) {
    const panel=e("section","perception-panel perception-intake");panel.append(e("span","eyebrow","01 / LOCAL RECORDINGS"),e("h3","","Bring a session into view"));
    p(panel,"Import a JSON observation bundle already in Workspace. Import saves and validates it; replay is a separate explicit action. Video files are not decoded here.");
    const form=e("form","perception-import"),label=e("label","perception-field","Workspace JSON path"),input=e("input");
    input.type="text";input.name="perception_path";input.value=importPath;input.maxLength=1000;input.placeholder="perception/room-session.json";input.setAttribute("aria-label","Workspace JSON path");input.spellcheck=false;
    input.addEventListener("input",()=>{importPath=input.value;controls();});fields.push(input);label.append(input);form.append(label,writeButton("Import recording",()=>mutate("import",{path:importPath}),()=>Boolean(importPath.trim()),true));
    form.addEventListener("submit",async event=>{event.preventDefault();if(ready())await mutate("import",{path:importPath});});panel.append(form);
    const demo=e("div","perception-demo");demo.append(e("span","perception-demo-label","SYNTHETIC / FIXED FIXTURE"));p(demo,"Explore known, unknown, stale and conflicting evidence using the same deterministic local demo.");demo.append(writeButton("Create synthetic demo",()=>mutate("demo")));panel.append(demo);parent.append(panel);
  }
  function renderRuViewIntake(parent) {
    const panel=e("section","perception-panel perception-rf-intake"),badges=e("div","perception-chips");
    chip(badges,"LOCAL RECORDING");chip(badges,"NO HARDWARE CONNECTION","quiet");panel.append(badges,e("h3","","Wi-Fi sensing / RuView"));
    p(panel,"Import saved RuView sensing events, then inspect their original claims and clocks. This does not connect to Wi-Fi hardware, start a producer or turn a recording into current room awareness.");
    const profile=e("dl","perception-facts");row(profile,"Supported profile",RU_PROFILE);row(profile,"Protocol pin",text(ruviewCatalog()?.pinned_commit,64)||"Unavailable");row(profile,"Adapter",ruviewSupported()?"Ready for explicit local-file import":"Unavailable or unsupported profile");panel.append(profile);
    const form=e("form","perception-import"),label=e("label","perception-field","Workspace RuView JSONL / NDJSON path"),input=e("input");
    input.type="text";input.name="ruview_path";input.value=ruviewPath;input.maxLength=1000;input.placeholder="perception/ruview-session.jsonl";input.setAttribute("aria-label","Workspace RuView JSONL / NDJSON path");input.spellcheck=false;
    input.addEventListener("input",()=>{ruviewPath=input.value;controls();});fields.push(input);label.append(input);
    const policyLabel=e("label","perception-field","Recorded source policy"),policy=e("select");policy.name="ruview_source_policy";policy.setAttribute("aria-label","Recorded source policy");
    for(const [value,label] of [["esp32_only","ESP32 recordings only — reject fallback"],["recorded_any","Recorded sources including simulation — explicit"]]){const option=e("option","",label);option.value=value;policy.append(option);}policy.value=sourcePolicy;
    policy.addEventListener("change",()=>{sourcePolicy=policy.value;const note=$("ruview-policy-note");if(note)note.textContent=policyWarning();controls();});fields.push(policy);policyLabel.append(policy);
    const warning=e("p","perception-warning",policyWarning());warning.id="ruview-policy-note";
    form.append(label,policyLabel,warning,writeButton("Import RuView recording",()=>mutate("import-ruview",{path:ruviewPath,source_policy:sourcePolicy}),()=>ruviewSupported()&&Boolean(ruviewPath.trim()),true));
    form.addEventListener("submit",async event=>{event.preventDefault();if(ready())await mutate("import-ruview",{path:ruviewPath,source_policy:sourcePolicy});});panel.append(form);
    const demo=e("div","perception-demo");demo.append(e("span","perception-demo-label","SYNTHETIC / RUVIEW FORMAT"));p(demo,"Try a fixed fictional RuView recording without hardware or acquisition. Its synthetic origin remains visible during import and replay.");demo.append(writeButton("Try synthetic RuView demo",()=>mutate("ruview-demo"),()=>ruviewSupported()));panel.append(demo);parent.append(panel);
  }
  const policyWarning=()=>sourcePolicy==="recorded_any"?"Explicit recorded-sources mode can include simulation and other supported recorded sources. It never establishes physical CSI or hardware qualification.":"ESP32-only admission rejects simulation and other source families. It does not authenticate or qualify an ESP32 source claim.";
  function renderRecordings(parent) {
    const panel=e("section","perception-panel"),heading=e("div","perception-panel-heading");heading.append(e("h3","","Saved receipts"),e("small","",count(records().length)+" / 32 shown"));panel.append(heading);
    if(!records().length)p(panel,"No recordings saved. Import a workspace bundle or explicitly create the synthetic demo.","perception-empty");
    const items=e("div","perception-recordings");
    for(const record of records()) {
      if(!record||typeof record!=="object")continue;
      const item=e("article","perception-recording"+(record.request_id===selected?" selected":"")),badges=e("div","perception-chips");
      const valid=record.status==="completed"&&record.verified===true;
      chip(badges,knownMode(record.processing_mode),record.processing_mode==="simulation"?"synthetic":"");chip(badges,valid?"HASH VERIFIED":"UNVERIFIED / "+(text(record.status,30)||"UNKNOWN").toUpperCase(),valid?"verified":"warning");
      item.append(badges,e("h4","",text(record.request_id,96)||"Unknown receipt"));
      p(item,(record.operation==="synthetic_demo"?"Fixed synthetic fixture":record.operation==="ruview_demo"?"Synthetic RuView fixture":record.source_profile===RU_PROFILE?"RuView recording · "+(record.operation==="replay"?"replay":"import"):text(record.operation,30)||"Saved run")+" · "+count(record.observations)+" observations · "+count(record.zones)+" zones");
      p(item,origins(record.capture_origins),"perception-origin");
      if(record.created_at)p(item,"Receipt created "+text(record.created_at,80),"perception-small");
      if(record.error)p(item,text(record.error),"perception-warning");
      if(ident.test(text(record.request_id,96)))item.append(readButton(valid?"Inspect recording":"Inspect receipt",()=>selectRecording(record.request_id)));
      items.append(item);
    }
    panel.append(items);parent.append(panel);
  }
  function renderZones(parent,evidence) {
    const panel=e("section","perception-panel"),heading=e("div","perception-panel-heading");heading.append(e("div","",undefined));heading.childNodes[0].append(e("span","eyebrow","02 / HISTORICAL ZONE EVIDENCE"),e("h3","","What the recording supports"));
    const badges=e("div","perception-chips");chip(badges,knownMode(evidence.processing_mode),evidence.processing_mode==="simulation"?"synthetic":"");heading.append(badges);panel.append(heading);
    p(panel,"These are saved observations, not current room conditions. Missing or disconnected evidence remains unknown.");
    const grid=e("div","perception-zones");
    for(const zone of list(evidence.zones,24)) {
      if(!zone||typeof zone!=="object")continue;
      const stateName=["known","unknown","stale","disagreement"].includes(zone.state)?zone.state:"unknown",card=e("article","perception-zone perception-zone-"+stateName);
      const presence=stateName==="known"&&zone.presence==="present"?"Presence supported":stateName==="known"&&zone.presence==="absent"?"Absence supported":stateName==="disagreement"?"Conflicting evidence":stateName==="stale"?"Evidence is stale":"Presence unknown";
      card.append(e("span","perception-zone-id",text(zone.zone_id,120)||"Unnamed zone"),e("h4","",presence));
      const badges=e("div","perception-chips");chip(badges,stateName==="disagreement"?"CONFLICT":stateName.toUpperCase(),stateName==="known"?"verified":"warning");card.append(badges);
      p(card,"Motion: "+(zone.motion==="detected"?"detected":zone.motion==="not_detected"?"not detected":"unknown"));p(card,fresh(zone),"perception-freshness");
      const reasons=list(zone.reasons,8);if(reasons.length)p(card,reasons.map(item=>text(item,180)).join(" · "));
      p(card,"Supporting IDs: "+(list(zone.supporting_observation_ids,12).map(item=>text(item,96)).join(", ")||"none"),"perception-small");grid.append(card);
    }
    if(!grid.childNodes.length)p(grid,"No zone state is available. This does not establish an empty room.","perception-empty");panel.append(grid);parent.append(panel);
  }
  function renderCurrent(parent,value) {
    const panel=e("section","perception-panel perception-current"),heading=e("div","perception-panel-heading");heading.append(e("h3","","Present awareness"));chip(heading,"NO LIVE SENSORS","warning");panel.append(heading);
    p(panel,"Current presence and motion are unknown. Saved recording results below describe their original timeline, even after replay.");
    const grid=e("div","perception-current-zones"),zones=list(value.current_zones,24);
    for(const zone of zones){if(!zone||typeof zone!=="object")continue;const item=e("div","perception-current-zone");item.append(e("span","",text(zone.zone_id,120)||"Unknown zone"),e("strong","","UNKNOWN NOW"));p(item,"Presence unknown · motion unknown","perception-small");p(item,"Saved source age: "+(Number.isFinite(zone.source_age_seconds)&&zone.source_age_seconds>=0?zone.source_age_seconds.toFixed(1)+" s":"unknown")+" · saved recording is not live","perception-small");grid.append(item);}
    if(!grid.childNodes.length)p(grid,"No present-time sensor evidence is available.","perception-empty");panel.append(grid);parent.append(panel);
  }
  function renderTimeline(parent,evidence) {
    const panel=e("section","perception-panel");panel.append(e("span","eyebrow","03 / SAVED CHANGES"),e("h3","","Evidence timeline"));p(panel,"Offsets follow the recording. Replaying never resets the physical age of an observation.");
    p(panel,count(evidence.timeline?.length)+" retained events · scroll the timeline to inspect.","perception-small");const timeline=e("ol","perception-timeline");
    for(const event of list(evidence.timeline,120)) {
      if(!event||typeof event!=="object")continue;
      const item=e("li"),time=e("span","perception-offset",offset(event.replay_offset_ms)),copy=e("div");copy.append(e("strong","",text(event.zone_id,120)||"Unknown zone"),e("span","perception-timeline-state",text(event.state,40)||"unknown"));
      p(copy,"Presence "+(["present","absent","unknown"].includes(event.presence)?event.presence:"unknown")+" · motion "+(["detected","not_detected","unknown"].includes(event.motion)?event.motion.replaceAll("_"," "):"unknown"));
      if(list(event.reasons,6).length)p(copy,list(event.reasons,6).map(value=>text(value,180)).join(" · "),"perception-small");
      p(copy,"Evidence: "+(list(event.supporting_observation_ids,12).map(value=>text(value,96)).join(", ")||"unknown"),"perception-small");item.append(time,copy);timeline.append(item);
    }
    if(!timeline.childNodes.length)p(panel,"No timeline events are retained.","perception-empty");panel.append(timeline);if((evidence.timeline?.length||0)>120)p(panel,"Showing the first 120 retained events.");parent.append(panel);
  }
  function renderProvenance(parent,value) {
    const panel=e("section","perception-panel perception-provenance"),receipt=value.receipt,evidence=value.evidence;panel.append(e("span","eyebrow","04 / PROVENANCE"),e("h3","","Trace the evidence"));
    const facts=e("dl","perception-facts");row(facts,"Receipt",text(receipt.request_id,96));row(facts,"Integrity","Source and retained artifacts hash verified on this read");row(facts,"Processing",knownMode(evidence.processing_mode));row(facts,"Capture origin",origins(evidence.capture_origins));row(facts,"Source SHA-256",text(receipt.source_sha256||evidence.source_sha256,64));row(facts,"Canonical SHA-256",text(receipt.canonical_sha256,64));panel.append(facts);
    p(panel,"Hash integrity verifies retained bytes. It does not independently qualify a sensor, calibrate a score, or establish current physical conditions.");
    const artifacts=e("details","perception-observations");artifacts.append(e("summary","","Retained artifact hashes"));const hashes=e("dl","perception-facts");for(const artifact of list(receipt.artifacts,8)){if(!artifact||typeof artifact!=="object")continue;row(hashes,text(artifact.name,80)||"Artifact",text(artifact.sha256,64)+" · "+count(artifact.bytes)+" bytes");}artifacts.append(hashes);panel.append(artifacts);
    const observations=e("details","perception-observations");observations.append(e("summary","","Observation provenance · "+count(evidence.observations?.length)+" retained"));
    for(const obs of list(evidence.observations,80)) {
      if(!obs||typeof obs!=="object")continue;
      const item=e("article","perception-observation"),facts=e("dl","perception-facts");item.append(e("h4","",text(obs.event_id,96)||"Unnamed observation"));
      row(facts,"Source",text(obs.registered_sensor_alias,120)||text(obs.sensor_alias,120));row(facts,"Modality",text(obs.modality,40));row(facts,"Capture origin",originLabel(obs.capture_origin));row(facts,"Original observation time",text(obs.observed_at,80)||"Unknown / unavailable");row(facts,"Producer time",text(obs.producer_at,80)||"Unknown / unavailable");row(facts,"Processing",knownMode(obs.processing_mode||evidence.processing_mode));row(facts,"Source artifact hash",text(obs.source_artifact_hash||obs.source_sha256,64)||"Unknown / unavailable");row(facts,"Qualification",text(obs.local_qualification_status,80)||"Unknown / unqualified");
      if(list(obs.quality_flags,8).length)row(facts,"Quality flags",list(obs.quality_flags,8).map(value=>text(value,96)).join(" · "));item.append(facts);observations.append(item);
    }
    if((evidence.observations?.length||0)>80)p(observations,"Showing the first 80 retained observations.");panel.append(observations);parent.append(panel);
  }
  function renderRuViewEvidence(parent,adapter) {
    if(!adapter)return;
    const panel=e("section","perception-panel perception-rf-evidence"),badges=e("div","perception-chips");chip(badges,"RUVIEW / RECORDED CLAIMS");chip(badges,"UNQUALIFIED","warning");chip(badges,"NO PHYSICAL CLOCK","warning");panel.append(badges,e("h3","","RF recording provenance"));
    p(panel,"These are upstream declarations in a saved file. Server timestamps and ticks are not radio capture times. Source labels do not authenticate hardware, and a model score is uncalibrated. Current room presence and motion remain unknown.");
    const facts=e("dl","perception-facts");row(facts,"Adapter profile",text(adapter.profile,80));row(facts,"Supported upstream pin",text(adapter.upstream_revision,64)||"See supported profile");row(facts,"Source policy",adapter.source_policy==="esp32_only"?"ESP32-only recording admission":"Explicit recorded sources, including simulation");row(facts,"Raw JSONL SHA-256",text(adapter.source_sha256,64));row(facts,"Normalized bundle SHA-256",text(adapter.bundle_sha256,64));row(facts,"Physical capture clock","Unavailable / unknown");row(facts,"Source clock",text(adapter.source_clock,100));row(facts,"Hardware qualification","Not established");panel.append(facts);
    p(panel,"No metric radar range, body pose, person count or vital signs are established by this adapter.","perception-warning");
    const frames=list(adapter.frames,32),events=e("div","perception-rf-frames");
    for(const frame of frames){
      if(!frame||typeof frame!=="object")continue;
      const item=e("article","perception-rf-frame"),heading=e("div","perception-panel-heading");heading.append(e("h4","",text(frame.event_id,96)||"Recorded RF event"));chip(heading,originLabel(frame.capture_origin),frame.capture_origin==="synthetic"?"synthetic":"warning");if(list(frame.quality_flags,16).some(flag=>["node_disagreement","room_node_conflict","classification_conflict","motion_disagreement"].includes(flag)))chip(heading,"SOURCE CLAIMS CONFLICT","warning");item.append(heading);
      const meta=e("dl","perception-facts");row(meta,"Raw source line",count(frame.source_line));row(meta,"Declared source family",text(frame.source_family,80));row(meta,"Server timestamp",finiteText(frame.server_timestamp));row(meta,"Producer timestamp",text(frame.producer_at,80));row(meta,"Server tick (not capture)",finiteText(frame.tick));row(meta,"Upstream age (not hardware age)",frame.age_ms===null?"Unknown":finiteText(frame.age_ms)+" ms");row(meta,"Normalized presence / motion",(text(frame.presence,20)||"unknown")+" / "+(text(frame.motion,24)||"unknown"));
      if(frame.room&&typeof frame.room==="object"){row(meta,"Declared room class",text(frame.room.classification,80));row(meta,"Room model score",modelScore(frame.room.confidence));row(meta,"Contributing nodes",count(frame.room.contributing_nodes));}
      if(list(frame.quality_flags,16).length)row(meta,"Quality flags",list(frame.quality_flags,16).map(value=>text(value,96)).join(" · "));item.append(meta);
      const source=e("details","perception-rf-source");source.append(e("summary","","Original declared source (may contain a network name)"));p(source,text(frame.source,160)||"Unknown declared source","perception-small");item.append(source);
      const nodes=e("div","perception-rf-nodes");for(const node of list(frame.nodes,16)){if(!node||typeof node!=="object")continue;const copy=e("p","perception-small");copy.textContent="Node "+count(node.node_id)+" · class "+(text(node.classification,80)||"unknown")+" · "+modelScore(node.confidence)+" · upstream age "+(node.age_ms===null?"unknown":finiteText(node.age_ms)+" ms");nodes.append(copy);}item.append(nodes);if((frame.nodes?.length||0)>16)p(item,"Showing the first 16 recorded node claims.","perception-small");events.append(item);
    }
    if(!events.childNodes.length)p(panel,"No RF frame provenance is retained. This does not establish an empty room.","perception-empty");
    panel.append(events);p(panel,"Showing "+count(frames.length)+" of "+count(adapter.frames.length)+" retained RF events. Saved source claims do not establish current physical conditions.","perception-small");parent.append(panel);
  }
  const finiteText=value=>typeof value==="number"&&Number.isFinite(value)?String(value).slice(0,40):"Unknown";
  const modelScore=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0&&value<=1?String(value)+" (uncalibrated model score)":"Uncalibrated model score unavailable";
  function renderReceipt(parent) {
    if(!receiptInspection||detail)return;
    const receipt=receiptInspection,panel=e("section","perception-panel perception-receipt-review");panel.append(e("span","eyebrow","RETAINED RECEIPT / EVIDENCE WITHHELD"),e("h3","","Inspect the saved outcome"));
    const facts=e("dl","perception-facts");row(facts,"Request",text(receipt.request_id,64));row(facts,"Operation",text(receipt.operation,40));row(facts,"Status",text(receipt.status,40));row(facts,"Integrity",receipt.verified===true?"Hash verified":"Unverified / unavailable");row(facts,"Source SHA-256",text(receipt.source_sha256,64));panel.append(facts);
    p(panel,text(receipt.error)||"This receipt does not provide completed verified evidence.","perception-warning");p(panel,"Inspection cannot clear a failure hold or resume an incomplete request. No action is retried here.");parent.append(panel);
  }
  function render() {
    const root=$("perception-content");if(!root||!active())return;root.replaceChildren();writes=[];reads=[];fields=[];
    renderHero(root);
    const status=e("div","perception-status");status.id="perception-status";status.setAttribute("role","status");
    status.textContent=busy?"Saving a local receipt…":loading?"Reading retained evidence…":paused()?"Global pause is active or authority is unavailable. Saved evidence remains readable.":held()?"Failure hold: "+(text(state().hold_reason)||"Inspect the retained failure receipt before any further work."):stale?"Refresh and inspect saved evidence before another action.":"Local recording desk ready. Every import, replay and demo requires an explicit action.";root.append(status);
    if(notice){const note=e("p","perception-notice",notice);note.setAttribute("role","alert");root.append(note);}
    if(uncertain){const box=e("section","perception-uncertain");box.append(e("strong","","Request outcome needs inspection"));p(box,"Request "+uncertain.id+" · "+uncertain.operation+". No automatic retry. Inspect this request before starting another action.");box.append(readButton("Inspect interrupted request",inspectUncertain));root.append(box);}
    const layout=e("div","perception-layout"),left=e("div","perception-sidebar"),right=e("div","perception-evidence");renderRuViewIntake(left);renderControls(left);renderRecordings(left);renderReceipt(right);
    if(detail){
      const selectedPanel=e("section","perception-selection");selectedPanel.append(e("span","eyebrow","SELECTED RECORDING"),e("h3","",text(detail.evidence.title,160)||"Saved observation recording"),e("strong","",text(selected,96)));p(selectedPanel,"Original timeline starts "+(text(detail.evidence.started_at,80)||"at an unknown time")+" · duration "+(Number.isFinite(detail.evidence.duration_ms)?(detail.evidence.duration_ms/1000).toFixed(1)+" s":"unknown"));
      selectedPanel.append(writeButton("Replay selected recording",()=>mutate("replay",{recording_id:selected}),()=>detail?.receipt?.verified===true,true));p(selectedPanel,"A replay creates a separate saved receipt. Capture origin and original observation time are preserved.");right.append(selectedPanel);
      renderCurrent(right,detail);renderRuViewEvidence(right,detail.adapter);renderZones(right,detail.evidence);renderTimeline(right,detail.evidence);renderProvenance(right,detail);
    }else{const empty=e("section","perception-panel perception-waiting");empty.append(e("span","eyebrow","NO VERIFIED RECORDING SELECTED"),e("h3","","Unknown is a useful answer."));p(empty,"Choose a saved recording to inspect its zone states, timeline and provenance. No evidence selected does not mean no activity.");right.append(empty);}
    layout.append(left,right);root.append(layout);controls();
  }

  async function load() {
    if(!app?.state.authenticated||app.state.view!=="perception"||busy)return;visible=true;
    const mark=++generation,token=app.state.token;loading=true;render();
    try {const value=await app.api("/perception");if(!current(mark,token))return;board=value;stale=false;const keep=selected&&records().find(item=>item?.request_id===selected&&item.status==="completed"&&item.verified===true);if(!keep){detail=null;receiptInspection=null;selected="";}}
    catch(error){if(current(mark,token)){board=null;detail=null;receiptInspection=null;selected="";stale=true;notice="Saved evidence could not be read: "+text(error.message);}}
    finally {if(current(mark,token)){loading=false;render();}}
  }
  async function selectRecording(recordingId,{keepNotice=false}={}) {
    if(!active()||busy||loading)return;
    try{cleanId(recordingId);}catch(error){notice=error.message;render();return;}
    const mark=generation,token=app.state.token,seq=++selection;detail=null;receiptInspection=null;selected=recordingId;if(!keepNotice)notice="";render();
    try{const value=await app.api("/perception/recordings/"+encodeURIComponent(recordingId));if(!current(mark,token)||seq!==selection)return;if(value?.receipt?.request_id===recordingId)receiptInspection=value.receipt;detail=verifiedDetail(value,recordingId);}
    catch(error){if(current(mark,token)&&seq===selection){detail=null;notice="Recording unavailable: "+text(error.message);}}
    finally{if(current(mark,token)&&seq===selection)render();}
  }
  async function inspectUncertain() {
    if(!uncertain||!active()||busy||loading)return;
    const pending=uncertain,mark=generation,token=app.state.token;
    try{const value=await app.api("/perception/recordings/"+encodeURIComponent(pending.id));if(!current(mark,token)||uncertain!==pending)return;const inspected=verifiedDetail(value,pending.id);if(inspected.receipt.operation!==pending.operation)throw new Error("The retained receipt does not match this action.");uncertain=null;notice="Completed request inspected. No action was replayed.";await load();if(active())await selectRecording(pending.id,{keepNotice:true});}
    catch(error){if(current(mark,token)){notice="Request remains uncertain: "+text(error.message)+" No action was retried.";render();}}
  }
  async function mutate(kind,extra={}) {
    if(!ready())return;
    let body;try{body=request(kind,extra);}catch(error){notice=error.message;render();return;}
    const mark=generation,token=app.state.token,pending={id:body.request_id,operation:operation(kind)};busy=true;uncertain=pending;notice="";controls();render();
    try {
      const response=await app.api("/perception/"+kind,{method:"POST",body});if(!current(mark,token))return;
      const receipt=response?.receipt||response;
      if(receipt?.request_id!==pending.id||receipt?.operation!==pending.operation||receipt?.status!=="completed")throw new Error("The action response does not match a completed receipt; its outcome is uncertain.");
      uncertain=null;busy=false;notice=kind==="import-ruview"?"RuView recording imported locally. Inspect its retained source claims before any explicit replay.":kind==="ruview-demo"?"Synthetic RuView fixture saved. Its origin stays synthetic; no hardware was connected.":kind==="import"?"Recording imported. Inspect it, then explicitly replay it when ready.":kind==="demo"?"Synthetic fixture saved. Its provenance stays synthetic.":"Replay completed. Physical freshness remains tied to original evidence.";
      await load();if(active())await selectRecording(pending.id,{keepNotice:true});
    } catch(error) {
      if(!current(mark,token))return;
      if(Number.isInteger(error.status)&&error.status>=400&&error.status<500){uncertain=null;stale=true;notice=text(error.message)+" Refresh and inspect saved receipts before another action.";}
      else {uncertain=pending;notice="Request outcome is uncertain: "+text(error.message)+" No automatic retry.";}
    } finally {if(current(mark,token)){busy=false;render();}}
  }
  function clear() {generation++;selection++;visible=false;busy=false;loading=false;board=null;detail=null;receiptInspection=null;selected="";importPath="";ruviewPath="";sourcePolicy="esp32_only";notice="";stale=false;writes=[];reads=[];fields=[];$("perception-content")?.replaceChildren();}
  window.ariadnePerception={
    attach(value){app=value;if(attached===app)return;attached=app;$("refresh-perception")?.addEventListener("click",()=>load());},
    load,selectRecording,mutate,inspectUncertain,path,ruviewRecordingPath,request,
    status(){if(!active())return;controls();const gate=$("perception-status");if(gate)gate.textContent=paused()?"Global pause is active or authority is unavailable. Saved evidence remains readable.":held()?"Failure hold: "+(text(state().hold_reason)||"Inspect the retained failure receipt before any further work."):stale?"Refresh and inspect saved evidence before another action.":busy?"Saving a local receipt…":loading?"Reading retained evidence…":"Local recording desk ready. Every import, replay and demo requires an explicit action.";},
    leave:clear,
    locked(){clear();},
  };
})();
