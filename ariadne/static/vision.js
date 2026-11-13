"use strict";



// Native backend only: viewing this desk never discovers or opens a device.

(() => {

  let app, attached, board=null, detail=null, devices=[], selected="", visible=false;

  let busy=false, loading=false, stale=false, pending=null, notice="", generation=0, selection=0, timer=null, imageUrl=null, ownedSession=null, leaseToken=null, boardReadAt=0, polling=null;

  let input={source_path:"",source_sha256:"",recording_started_at:"",duration_seconds:"30",device_id:"",thumbnail:false,preview:true,question:"Briefly describe what is visible in this frame."};

  let previewData=null, previewUrl=null, previewReadAt=0, previewFlight=null, previewSequence=0, previewLastFetch=-Infinity, previewAbort=null, previewNotice="", previewExpiryTimer=null;
  let descriptionBoard=null, descriptionDetail=null, descriptionPending=null, descriptionSubmitting=false, descriptionFlight=null, descriptionNotice="", descriptionSelection=0, descriptionSelected="";
  const revokedDescriptions=new Set();

  let writes=[], reads=[], fields=[], recoveries=[];

  const $=id=>document.getElementById(id), str=(value,max=800)=>typeof value==="string"?value.slice(0,max):"";

  const arr=(value,max=64)=>Array.isArray(value)?value.slice(0,max):[];

  const e=(tag,cls="",value)=>app.node(tag,cls,value), p=(parent,value,cls="vision-note")=>parent.append(e("p",cls,value));

  const elapsedClock=()=>performance.now();
  const active=()=>Boolean(app?.state.authenticated&&visible&&app.state.view==="vision");

  const current=(mark,token)=>mark===generation&&active()&&token===app.state.token;

  const paused=()=>app?.state.status?.paused!==false||board?.paused!==false;

  const revision=()=>board?.control_revision;

  const sessions=()=>arr(board?.sessions,32), recordings=()=>arr(board?.recordings,32);

  const isRunning=value=>["starting","running","capturing","waiting_for_frames","stopping"].includes(value?.status);

  const camera=()=>sessions().find(item=>item?.operation==="camera"&&isRunning(item))||sessions().find(item=>item?.capture_origin==="live"&&isRunning(item));

  const ready=()=>active()&&board&&!busy&&!loading&&!stale&&!pending&&!paused()&&!board.hold&&board.ready===true&&Number.isSafeInteger(revision())&&revision()>=0&&!camera();

  const recoveryReady=()=>active()&&board&&!busy&&!loading&&!stale&&!pending&&!paused()&&Number.isSafeInteger(board.revision)&&Number.isSafeInteger(revision())&&["failed","interrupted"].includes(detail?.receipt?.status)&&detail?.receipt?.reviewed!==true&&detail?.receipt?.failure_reviewed!==true&&/^[a-f0-9]{64}$/.test(str(detail?.receipt?.failure_sha256,64));

  const identifier=value=>{if(typeof value!=="string"||!/^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/.test(value))throw new Error("Invalid saved job identifier.");return value;};

  const byteCount=value=>Number.isSafeInteger(value)&&value>=0?String(value):"unknown";

  const requestId=()=>"vision-ui-"+crypto.randomUUID().replaceAll("-","");

  function path(value){const raw=str(value,1001),normal=raw.replaceAll("\\","/");if(!normal||raw!==raw.trim()||normal.length>1000||normal.startsWith("/")||/[\u0000-\u001f\u007f:<>"|?*]/.test(normal)||normal.split("/").some(part=>!part||part==="."||part===".."||/[. ]$/.test(part)||/^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)/i.test(part))||!(/\.(?:mp4|mkv|mov|avi|webm|m4v)$/i.test(normal)))throw new Error("Enter a video path inside Ariadne Workspace, such as vision/room.mp4.");return normal;}

  function hash(value){if(typeof value!=="string"||!/^[a-fA-F0-9]{64}$/.test(value))throw new Error("Enter the file's 64 character SHA-256 or inspect the selected file first.");return value.toLowerCase();}

  function timestamp(value){if(!value)return null;if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)||!Number.isFinite(Date.parse(value)))throw new Error("Use the original capture start in UTC, such as 2026-11-12T14:30:00Z, or leave it unknown.");return value;}

  function body(kind){const result={request_id:requestId(),expected_control_revision:revision(),thumbnail:input.thumbnail};if(kind==="video"){result.source_path=path(input.source_path);result.source_sha256=hash(input.source_sha256);result.recording_started_at=timestamp(input.recording_started_at);result.sample_interval_ms=1000;result.duration_ms=120000;}else if(kind==="camera"){if(!devices.some(item=>item.device_id===input.device_id&&item.available!==false))throw new Error("Discover cameras and select an available device first.");const seconds=Number(input.duration_seconds);if(!Number.isInteger(seconds)||seconds<1||seconds>120)throw new Error("Choose a camera duration from 1 to 120 seconds.");result.device_id=input.device_id;result.duration_ms=seconds*1000;result.preview=input.preview===true;}else throw new Error("Unsupported Vision action.");return result;}

  function chip(parent,value,kind=""){parent.append(e("span","vision-chip"+(kind?" vision-chip-"+kind:""),value));}

  function fact(parent,label,value){const row=e("div","vision-fact");row.append(e("dt","",label),e("dd","",value||"Unknown / unavailable"));parent.append(row);}

  function clearImage(){if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl=null;}}

  function controls(){for(const {node,allowed}of writes)node.disabled=!ready()||!allowed();for(const node of recoveries)node.disabled=!recoveryReady();for(const node of reads)node.disabled=!active()||busy||loading;for(const node of fields)node.disabled=!active()||busy||loading;const refresh=$("refresh-vision");if(refresh)refresh.disabled=!active()||busy||loading;const stop=$("vision-stop");if(stop)stop.disabled=!active()||!camera()||camera()?.status==="stopping";const describe=$("vision-describe");if(describe)describe.disabled=!descriptionReady();const inspect=$("vision-inspect-description");if(inspect)inspect.disabled=!active()||descriptionSubmitting||Boolean(descriptionFlight);const review=$("vision-review-description");if(review)review.disabled=!descriptionReviewReady();}

  function button(label,callback,{write=false,allowed=()=>true,primary=false}={}){const item=app.button(label,primary?"button button-primary":"button button-secondary",async()=>{if(write?!ready()||!allowed():!active()||busy||loading)return;await callback();});if(write)writes.push({node:item,allowed});else reads.push(item);return item;}

  function field(parent,label,name,{placeholder="",type="text",help="",max=1000}={}){const wrapper=e("label","vision-field",label),item=e("input");item.type=type;item.name=name;item.setAttribute("aria-label",label);item.value=input[name];item.maxLength=max;item.placeholder=placeholder;item.spellcheck=false;item.addEventListener("input",()=>{input[name]=item.value;if(name==="source_path"){input.source_sha256="";const hashField=fields.find(field=>field.name==="source_sha256");if(hashField)hashField.value="";}controls();});fields.push(item);wrapper.append(item);if(help)wrapper.append(e("small","",help));parent.append(wrapper);return item;}

  function hero(root){const panel=e("section","vision-hero"),copy=e("div"),badges=e("div","vision-chips");chip(badges,"LOCAL VISION");chip(badges,"MICROPHONE DISABLED","quiet");copy.append(badges,e("h2","","A view, with its evidence."));p(copy,"Analyze a video you choose or start a short native camera session. Objects, motion and source times stay visible together.");const mark=e("div","vision-emblem");mark.setAttribute("aria-hidden","true");mark.append(e("span","","VISUAL EVIDENCE"),e("strong","","VISION"),e("small","","YOU CHOOSE WHEN"));panel.append(copy,mark);root.append(panel);}

  function currentView(root){const value=camera(),panel=e("section","vision-current"+(value?" vision-current-active":"")),heading=e("div","vision-panel-heading");heading.append(e("h3","","Current camera"));const badge=e("strong","vision-camera-state",(stale||(!board)||pending?.kind==="camera")?"CAMERA STATUS UNKNOWN":value?"CAMERA ACTIVE":"CAMERA OFF");badge.id="vision-camera-state";heading.append(badge);panel.append(heading);

    if(value){const countdown=e("p","vision-countdown");countdown.id="vision-countdown";panel.append(countdown);const stop=app.button("Stop camera","button button-danger-subtle",()=>stopCamera());stop.id="vision-stop";panel.append(stop);p(panel,"Native video only · microphone disabled · capture ends at the selected limit.");const live=value.live||{},age=Number.isFinite(live.age_ms)?live.age_ms+Math.max(0,elapsedClock()-boardReadAt):null,fresh=!stale&&!paused()&&live.state==="fresh"&&live.source==="current_session"&&age!==null&&age<=(board?.limits?.freshness_ms||3000);const line=e("strong","vision-current-reading",fresh?(live.presence==="present"?"Presence supported by recent camera samples":"Current presence unknown"):"CURRENT PRESENCE UNKNOWN");panel.append(line);p(panel,fresh?"Motion: "+motion(live.motion)+" · latest frame "+(str(live.observed_at,80)||"time unknown"):"Waiting, stale, disconnected or unavailable evidence cannot establish current room conditions.");p(panel,"Source freshness: "+(fresh?"fresh":live.state==="fresh"?"stale":str(live.state,40)||"unknown")+" · age "+(age!==null&&age>=0?(age/1000).toFixed(1)+" s":"unknown"));if(fresh)objects(panel,live.objects);}

    else{p(panel,"Current presence and motion are unknown. A saved video or completed camera session describes its recorded timeline.");if(sessions().some(item=>item?.error))p(panel,"A camera or processing error is retained below. This does not establish an empty room.","vision-warning");}

    root.append(panel);updateCountdown();

  }

  function intake(root){const layout=e("div","vision-intake");const video=e("section","vision-panel");video.append(e("span","eyebrow","01 / SELECT A VIDEO"),e("h3","","Review a Workspace video"));p(video,"Choose a file already in Ariadne Workspace. Inspection hashes those bytes; analysis begins only when you select Analyze video. Analysis samples the first 120 seconds, or the whole file if shorter. The source file limit is 512 MiB.");field(video,"Workspace video path","source_path",{placeholder:"vision/room.mp4"});field(video,"Source SHA-256","source_sha256",{max:64,placeholder:"Inspect the file or enter its SHA-256"});field(video,"First video frame time (UTC, optional)","recording_started_at",{max:40,placeholder:"Unknown · or 2026-11-12T14:30:00Z",help:"Owner supplied UTC time of the first video frame. Leave unknown when unavailable; processing time is never substituted."});const actions=e("div","vision-actions");actions.append(button("Inspect selected file",inspectFile),button("Analyze video",()=>mutate("video"),{write:true,primary:true,allowed:()=>Boolean(input.source_path&&input.source_sha256)}));video.append(actions);

    const device=e("section","vision-panel");device.append(e("span","eyebrow","02 / BOUNDED CAMERA SESSION"),e("h3","","Choose when to look"));p(device,"Discover devices without opening them, select one, then explicitly start. Camera access never starts on page load, replay or restart.");device.append(button("Discover cameras",discover));const label=e("label","vision-field","Native camera"),select=e("select");select.name="device_id";select.setAttribute("aria-label","Native camera");const empty=e("option","",devices.length?"Select a camera":"Discover cameras first");empty.value="";select.append(empty);for(const item of devices){const option=e("option","",str(item.label,180)||str(item.device_id,180));option.value=item.device_id;option.disabled=item.available===false;select.append(option);}select.value=input.device_id;select.addEventListener("change",()=>{input.device_id=select.value;controls();});fields.push(select);label.append(select);device.append(label);field(device,"Session duration (seconds)","duration_seconds",{type:"number",max:3,help:"Default 30 seconds · maximum 120 seconds."});device.append(button("Start camera",()=>mutate("camera"),{write:true,primary:true,allowed:()=>Boolean(input.device_id)}));layout.append(video,device);root.append(layout);

    const liveOption=e("label","vision-thumbnail-option"),liveCheck=e("input");liveCheck.type="checkbox";liveCheck.name="preview";liveCheck.checked=input.preview;liveCheck.addEventListener("change",()=>{input.preview=liveCheck.checked===true;});fields.push(liveCheck);liveOption.append(liveCheck,e("span","","Live preview for the next camera session (memory only; never saved)."));root.append(liveOption);
    const options=e("label","vision-thumbnail-option"),checkbox=e("input");checkbox.type="checkbox";checkbox.name="thumbnail";checkbox.checked=input.thumbnail;checkbox.addEventListener("change",()=>{input.thumbnail=checkbox.checked===true;});fields.push(checkbox);options.append(checkbox,e("span","","Retain a thumbnail for this next analysis or camera session (optional; off by default)."));root.append(options);p(root,"Object confidence scores are uncalibrated model scores. Motion is measured separately. Vision does not identify people, infer medical conditions or trigger safety actions.","vision-boundary");

  }

  const freshness=()=>Math.min(3000,Number.isFinite(board?.limits?.freshness_ms)?board.limits.freshness_ms:3000);
  const ownedCamera=()=>{const value=camera();return value?.session_id===ownedSession?value:null;};
  const previewAllowed=()=>Boolean(active()&&document.visibilityState!=="hidden"&&!stale&&!paused()&&leaseToken&&ownedCamera()?.preview?.enabled===true&&elapsedClock()-boardReadAt<=freshness());

  function previewRemaining(){
    if(!previewData)return 0;
    const elapsed=Math.max(0,elapsedClock()-previewReadAt),utcAge=Date.now()-Date.parse(previewData.acquired_at);
    // Subtract the whole transfer/decode interval from the server's lease and
    // source-clock TTL; a slow response may never make an old image fresh.
    return Math.min(previewData.freshness_remaining_ms-elapsed,freshness()-previewData.age_ms-elapsed,freshness()-previewData.source_lag_ms-Math.max(0,utcAge));
  }

  function previewFresh(){
    if(!previewData||!previewUrl||!previewAllowed()||previewData.session_id!==ownedSession||previewData.control_revision!==revision())return false;
    const age=previewData.age_ms+Math.max(0,elapsedClock()-previewReadAt),utcAge=Date.now()-Date.parse(previewData.acquired_at);
    return Number.isFinite(age)&&age>=0&&age<=freshness()&&Number.isFinite(utcAge)&&utcAge>=-2000&&previewData.source_lag_ms+Math.max(0,utcAge)<=freshness()&&previewRemaining()>0;
  }

  function clearPreview(reason=""){
    previewSequence++;previewAbort?.abort();previewAbort=null;clearTimeout(previewExpiryTimer);previewExpiryTimer=null;
    if(previewUrl)URL.revokeObjectURL(previewUrl);
    previewData=null;previewUrl=null;previewReadAt=0;previewNotice=reason;
    $("vision-preview-slot")?.replaceChildren();
  }

  function expirePreviewAtDeadline(){
    clearTimeout(previewExpiryTimer);previewExpiryTimer=null;
    if(!previewData)return;
    const mark=generation,token=app.state.token,seq=previewSequence;
    previewExpiryTimer=setTimeout(()=>{previewExpiryTimer=null;if(!current(mark,token)||seq!==previewSequence)return;if(!previewFresh()){clearPreview("Preview expired. Waiting for a newly received frame; no stale image is retained.");refreshPreview();controls();}else expirePreviewAtDeadline();},Math.max(1,Math.ceil(previewRemaining())+1));
  }

  function refreshPreview(){
    const slot=$("vision-preview-slot");if(!slot)return;
    if((previewData||previewUrl)&&!previewFresh())clearPreview("Preview withheld: the frame is stale, stopped or no longer authorized.");
    slot.replaceChildren();slot.append(e("span","eyebrow","MEMORY ONLY / NO AUDIO"),e("h3","","Live camera preview"));
    if(previewFresh()){
      const image=e("img","vision-live-preview");image.src=previewUrl;image.alt="Current owner-started camera frame; transient and not saved";
      const shown=previewUrl;image.addEventListener("error",()=>{if(previewUrl===shown){clearPreview("The preview could not be decoded. No old image is shown.");refreshPreview();controls();}});
      slot.append(image);p(slot,"Frame received "+str(previewData.acquired_at,80)+" · age "+((previewData.age_ms+Math.max(0,elapsedClock()-previewReadAt))/1000).toFixed(1)+" s","vision-preview-time");
      p(slot,"JPEG updates at most once per second. Driver latency is unknown; this is not continuous video or proof of current safety.","vision-small");
    }else{
      const empty=e("div","vision-preview-empty");p(empty,previewNotice||(!camera()?"Camera off. Start an explicit session with Live preview enabled.":!ownedCamera()?"This tab does not own the active camera lease. No preview is requested.":ownedCamera()?.preview?.enabled!==true?"Live preview was not enabled for this session.":"Waiting for a fresh camera frame. Stale images are never held on screen."));slot.append(empty);
    }
    p(slot,"Preview images stay in memory and disappear on Stop, pause, page hiding, locking or session expiry. Retained thumbnails remain a separate opt-in.","vision-small");
  }

  async function loadPreview(){
    if(!previewAllowed()||previewFlight||elapsedClock()-previewLastFetch<1000)return;
    const mark=generation,token=app.state.token,session=ownedSession,seq=previewSequence,flight={},requestStarted=elapsedClock();
    previewFlight=flight;previewLastFetch=elapsedClock();const controller=new AbortController();previewAbort=controller;
    try{
      const value=await app.api("/vision/preview/"+encodeURIComponent(identifier(session)),{signal:controller.signal});
      if(!current(mark,token)||seq!==previewSequence||session!==ownedSession||!previewAllowed())return;
      if(value?.session_id!==session||value.control_revision!==revision()||value.retained!==false||!Number.isSafeInteger(value.sample_index)||value.sample_index<0||!Number.isSafeInteger(value.width)||value.width<1||value.width>640||!Number.isSafeInteger(value.height)||value.height<1||value.height>480||!/^[a-f0-9]{64}$/.test(str(value.frame_id,65))||!/^[a-f0-9]{64}$/.test(str(value.jpeg_sha256,65))||!/^[a-f0-9]{64}$/.test(str(value.source_frame_sha256,65))||!Number.isFinite(value.age_ms)||value.age_ms<0||value.age_ms>freshness()||!Number.isFinite(value.source_lag_ms)||value.source_lag_ms<0||value.source_lag_ms>freshness()||!Number.isSafeInteger(value.freshness_remaining_ms)||value.freshness_remaining_ms<1||value.freshness_remaining_ms>freshness())throw new Error("Preview provenance or bounds are invalid.");
      const acquired=Date.parse(value.acquired_at),utcAge=Date.now()-acquired;
      if(!Number.isFinite(acquired)||utcAge < -2000||value.source_lag_ms+Math.max(0,utcAge)>freshness())throw new Error("Preview source time is stale or unavailable.");
      if(typeof value.jpeg_base64!=="string"||value.jpeg_base64.length>87384||!/^[-A-Za-z0-9+/]*={0,2}$/.test(value.jpeg_base64))throw new Error("Preview image encoding exceeds its bound.");
      const raw=atob(value.jpeg_base64),bytes=Uint8Array.from(raw,char=>char.charCodeAt(0));
      if(bytes.length<4||bytes.length>65536||bytes[0]!==255||bytes[1]!==216||bytes.at(-2)!==255||bytes.at(-1)!==217)throw new Error("Preview is not a bounded JPEG.");
      const actual=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),byte=>byte.toString(16).padStart(2,"0")).join("");
      if(actual!==value.jpeg_sha256)throw new Error("Preview bytes differ from their frame receipt.");
      if(!current(mark,token)||seq!==previewSequence||session!==ownedSession||!previewAllowed())return;
      if(value.freshness_remaining_ms-(elapsedClock()-requestStarted)<=0)throw new Error("Preview expired during transfer or verification.");
      const {jpeg_base64,...metadata}=value;
      if(previewData?.jpeg_sha256!==value.jpeg_sha256||!previewUrl){if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(new Blob([bytes],{type:"image/jpeg"}));}
      previewData=metadata;previewReadAt=requestStarted;previewNotice="";refreshPreview();expirePreviewAtDeadline();controls();
    }catch(error){if(current(mark,token)&&seq===previewSequence){clearPreview(error.status===409?"Waiting for a fresh authorized preview; no old frame is shown.":"Preview unavailable: "+str(error.message));refreshPreview();controls();}}
    finally{if(previewFlight===flight)previewFlight=null;if(previewAbort===controller)previewAbort=null;}
  }

  const descriptionReady=()=>Boolean(previewFresh()&&descriptionBoard?.configured!==false&&descriptionBoard?.paused===false&&descriptionBoard.control_revision===revision()&&!descriptionBoard.hold&&!descriptionSubmitting&&!descriptionPending&&typeof input.question==="string"&&input.question.trim().length>0&&input.question.length<=2000);
  const descriptionReviewReady=()=>Boolean(active()&&!paused()&&!descriptionSubmitting&&!descriptionFlight&&Number.isSafeInteger(descriptionBoard?.revision)&&["failed","interrupted"].includes(descriptionDetail?.receipt?.status)&&/^[a-f0-9]{64}$/.test(str(descriptionDetail?.receipt?.failure_sha256,65)));

  function descriptionPanel(root){
    const panel=e("section","vision-panel vision-description-panel");panel.append(e("span","eyebrow","ASK / LOCAL MODEL ONLY"),e("h3","","Ask Ariadne about this view"));
    p(panel,"Only your explicit question sends the currently shown fresh frame to the installed local vision-capable model. A replaced frame is rejected rather than substituted. No continuous captions, cloud requests, audio or tool actions.");
    const label=e("label","vision-field","Question about the current frame"),question=e("textarea");question.name="question";question.setAttribute("aria-label","Question about the current frame");question.id="vision-question";question.rows=3;question.maxLength=2000;question.value=input.question;question.addEventListener("input",()=>{input.question=question.value;controls();});label.append(question);fields.push(question);panel.append(label);
    const describe=app.button("Describe current frame","button button-primary",startDescription);describe.id="vision-describe";panel.append(describe);
    p(panel,"Start with Live preview enabled, then ask when a fresh frame is visible. A description interprets that captured frame; it may be wrong or already historical.","vision-small");
    const output=e("div");output.id="vision-description-state";panel.append(output);root.append(panel);refreshDescription();
  }

  function refreshDescription(){
    const slot=$("vision-description-state");if(!slot)return;slot.replaceChildren();
    if(descriptionNotice)p(slot,descriptionNotice,"vision-description-notice");
    if(!descriptionBoard)p(slot,"Local description status is unavailable. No image request will be submitted.","vision-warning");
    else if(descriptionBoard.configured===false)p(slot,"Local descriptions are unavailable: "+(str(descriptionBoard.reason)||"the local endpoint or private storage layout is not configured for image descriptions.")+" No image request will be submitted.","vision-warning");
    else if(descriptionBoard.hold)p(slot,"Description failure hold. Inspect and explicitly review its exact failure before another description request.","vision-warning");
    if(descriptionSubmitting)p(slot,"Submitting one explicit local description request…");
    if(descriptionPending){p(slot,"Request "+descriptionPending.id+" · "+(descriptionPending.uncertain?"outcome unknown; no automatic retry":"local description running; camera Stop remains available"));if(descriptionPending.uncertain){const inspect=app.button("Inspect description request","button button-secondary",()=>selectDescription(descriptionPending.id));inspect.id="vision-inspect-description";slot.append(inspect);}}
    const receipt=descriptionDetail?.receipt,answer=descriptionDetail?.description;
    if(receipt){
      const facts=e("dl","vision-facts");fact(facts,"Description request",str(receipt.request_id,96));fact(facts,"Outcome",str(receipt.status,40));slot.append(facts);
      if(revokedDescriptions.has(receipt.request_id)&&receipt.status==="completed")p(slot,"This description was revoked by capture Stop or pause. Its answer is withheld.","vision-warning");
      else if(receipt.status==="completed"&&receipt.verified===true&&answer&&typeof answer.answer==="string"){
        p(slot,"Description of a captured frame · not proof of what is visible now.","vision-historical");p(slot,str(answer.answer,4000),"vision-description-answer");
        const source=answer.source||receipt.source||{},provenance=e("dl","vision-facts");fact(provenance,"Frame received",str(source.acquired_at,80));fact(provenance,"Frame ID",str(source.frame_id,160));fact(provenance,"JPEG SHA-256",str(source.jpeg_sha256,64));fact(provenance,"Model",typeof answer.model==="string"?str(answer.model,200):str(answer.model?.name,200));slot.append(provenance);
      }else if(["failed","interrupted"].includes(receipt.status)){
        p(slot,str(receipt.error)||"The description did not complete. No answer is presented.","vision-warning");
        if(/^[a-f0-9]{64}$/.test(str(receipt.failure_sha256,65))){p(slot,"Exact failure SHA-256: "+receipt.failure_sha256,"vision-small");p(slot,"Review preserves this failure and permits a separate new request. It never retries this request.","vision-small");const review=app.button("Review description failure","button button-secondary",reviewDescriptionFailure);review.id="vision-review-description";slot.append(review);}
      }else if(receipt.status==="reviewed")p(slot,"This failure review is retained. The old request can never be retried.");
    }
    const rows=arr(descriptionBoard?.descriptions,10);if(rows.length){const list=e("div","vision-description-history");p(list,"Recent local description receipts","vision-small");for(const row of rows){if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/.test(str(row.request_id,96)))continue;const item=app.button("Inspect description · "+str(row.status,30)+" · "+str(row.request_id,96),"button button-secondary",()=>selectDescription(row.request_id));list.append(item);}slot.append(list);}
    controls();
  }

  async function descriptionStatus(mark=generation,token=app.state.token){
    const value=await app.api("/vision/descriptions");if(!current(mark,token))return;
    if(!value||!Number.isSafeInteger(value.revision)||!Array.isArray(value.descriptions))throw new Error("Invalid local description status.");
    descriptionBoard=value;refreshDescription();
  }

  async function startDescription(){
    if(!descriptionReady())return;
    const mark=generation,token=app.state.token,request={request_id:requestId(),session_id:ownedSession,expected_control_revision:revision(),expected_frame_id:previewData.frame_id,question:input.question.trim()};
    descriptionSubmitting=true;descriptionNotice="";descriptionPending={id:request.request_id,uncertain:false};descriptionSelected=request.request_id;descriptionSelection++;descriptionDetail=null;refreshDescription();controls();
    try{
      const value=await app.api("/vision/descriptions",{method:"POST",body:request}),receipt=value?.receipt||value;
      if(!current(mark,token))return;
      if(receipt?.request_id!==request.request_id||!["running","completed","failed","interrupted"].includes(receipt.status))throw new Error("Description response did not match this request.");
      descriptionDetail={receipt,description:null};descriptionPending={id:request.request_id,uncertain:false};
    }catch(error){if(current(mark,token)){if(Number.isInteger(error.status)&&error.status>=400&&error.status<500){descriptionPending=null;if(error.status===409){clearPreview("The shown frame or source authority changed, or the local model is busy. Wait for a newly received preview before asking again.");refreshPreview();}descriptionNotice="Description was not accepted: "+str(error.message)+(error.status===409?" Wait for a fresh preview and ask again explicitly.":"")+" No automatic retry.";try{await descriptionStatus(mark,token);}catch{} }else{descriptionPending={id:request.request_id,uncertain:true};descriptionNotice="Description outcome is uncertain: "+str(error.message)+" Inspect the request; no automatic retry.";}}}
    finally{if(current(mark,token)){descriptionSubmitting=false;refreshDescription();controls();schedule();}}
  }

  async function selectDescription(id){
    if(!active()||descriptionSubmitting)return;try{identifier(id);}catch(error){descriptionNotice=error.message;refreshDescription();return;}
    const mark=generation,token=app.state.token,seq=++descriptionSelection;descriptionSelected=id;descriptionDetail=null;refreshDescription();
    try{const value=await app.api("/vision/descriptions/"+encodeURIComponent(id));if(!current(mark,token)||seq!==descriptionSelection)return;if(value?.receipt?.request_id!==id)throw new Error("Description receipt does not match the selection.");descriptionDetail=value;if(descriptionPending?.id===id){descriptionPending=isRunning(value.receipt)?{id,uncertain:false}:null;}if(["failed","interrupted"].includes(value.receipt.status)&&descriptionBoard)descriptionBoard.hold=true;descriptionNotice="";try{await descriptionStatus(mark,token);}catch{descriptionNotice="Description receipt read; the latest description ledger status is unavailable.";}}
    catch(error){if(current(mark,token)&&seq===descriptionSelection)descriptionNotice="Description could not be inspected: "+str(error.message)+" Nothing was retried.";}
    finally{if(current(mark,token)&&seq===descriptionSelection){refreshDescription();controls();schedule();}}
  }

  async function pollDescription(){
    if(!active()||descriptionSubmitting||descriptionFlight||!descriptionPending||descriptionPending.uncertain)return;
    const mark=generation,token=app.state.token,id=descriptionPending.id,flight={};descriptionFlight=flight;
    try{const value=await app.api("/vision/descriptions/"+encodeURIComponent(id));if(!current(mark,token)||descriptionPending?.id!==id)return;if(value?.receipt?.request_id!==id)throw new Error("Description receipt does not match this request.");if(descriptionSelected===id)descriptionDetail=value;if(!isRunning(value.receipt)){descriptionPending=null;if(["failed","interrupted"].includes(value.receipt.status)&&descriptionBoard)descriptionBoard.hold=true;try{await descriptionStatus(mark,token);}catch{descriptionNotice="The terminal receipt is available; the latest description ledger status is unavailable.";}}refreshDescription();controls();}
    catch(error){if(current(mark,token)&&descriptionPending?.id===id){descriptionPending.uncertain=true;descriptionNotice="Description status is uncertain: "+str(error.message)+" Inspect it explicitly; no generation is retried.";refreshDescription();}}
    finally{if(descriptionFlight===flight)descriptionFlight=null;if(current(mark,token))controls();}
  }

  async function reviewDescriptionFailure(){
    if(!descriptionReviewReady())return;
    const mark=generation,token=app.state.token,receipt=descriptionDetail.receipt,request={request_id:receipt.request_id,expected_revision:descriptionBoard.revision,expected_control_revision:revision(),failure_sha256:receipt.failure_sha256};
    descriptionSubmitting=true;controls();
    try{const value=await app.api("/vision/descriptions/acknowledge",{method:"POST",body:request});if(!current(mark,token))return;if(value.request_id!==request.request_id||value.failure_sha256!==request.failure_sha256||value.status!=="reviewed"||value.retry_permitted!==false)throw new Error("Description failure review response did not match.");descriptionNotice="Description failure review saved. Nothing was retried.";descriptionSubmitting=false;await selectDescription(request.request_id);}
    catch(error){if(current(mark,token))descriptionNotice="Description review could not be confirmed: "+str(error.message)+" No automatic retry.";}
    finally{if(current(mark,token)){descriptionSubmitting=false;refreshDescription();controls();}}
  }

  const motion=value=>value==="detected"?"detected":value==="not_detected"?"not detected":"unknown";

  function objects(parent,value){p(parent,"Object labels · uncalibrated model scores","vision-small");const rows=e("div","vision-objects");for(const item of arr(value,40)){if(!item||typeof item!=="object")continue;const score=Number.isFinite(item.confidence)?item.confidence:Number.isFinite(item.score)?item.score:null;rows.append(e("span","vision-object",(str(item.label,100)||"Unknown object")+" · "+(score!==null&&score>=0&&score<=1?"score "+score.toFixed(3):"score unknown")));}if(!rows.childNodes.length)p(rows,"No retained object labels in this sample. Missing detections do not prove absence.");parent.append(rows);}

  function history(root){const panel=e("section","vision-panel");panel.append(e("span","eyebrow","03 / RETAINED RESULTS"),e("h3","","Recorded evidence"));p(panel,"Completed results remain historical. Select a receipt to inspect its original samples and hashes.");const rows=e("div","vision-history"),seen=new Set();for(const item of [...sessions(),...recordings()]){if(!item||typeof item!=="object"||seen.has(item.request_id))continue;seen.add(item.request_id);const row=e("article","vision-history-item"+(selected===item.request_id?" selected":"")),badges=e("div","vision-chips");chip(badges,str(item.operation,30)==="camera"?"CAMERA RECORDING":"VIDEO FILE");chip(badges,(str(item.status,40)||"unknown").toUpperCase(),item.status==="completed"?"quiet":"warning");row.append(badges,e("h4","",str(item.title,160)||str(item.request_id,96)||"Unknown receipt"));p(row,"Created "+(str(item.created_at,80)||"at an unknown time"));if(item.error)p(row,str(item.error),"vision-warning");if(/^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/.test(str(item.request_id,96)))row.append(button("Inspect result",()=>selectRecording(item.request_id)));rows.append(row);}if(!rows.childNodes.length)p(rows,"No retained Vision results. A quiet history does not establish a quiet room.");panel.append(rows);root.append(panel);}

  function result(root){if(!detail)return;const receipt=detail.receipt||{},evidence=detail.evidence,panel=e("section","vision-panel vision-result");panel.append(e("span","eyebrow","SELECTED / HISTORICAL RESULT"),e("h3","",str(receipt.title,160)||"Recorded Vision samples"));const facts=e("dl","vision-facts");fact(facts,"Request",str(receipt.request_id,96));fact(facts,"Outcome",str(receipt.status,40));fact(facts,"Analysis window",receipt.operation==="video"?"First up to 120 seconds of the source file":"Explicit bounded camera session");fact(facts,"Integrity",receipt.verified===true?"Retained bytes hash verified":"Unverified · sample evidence withheld");fact(facts,"Original capture start",str(receipt.operation==="camera"?evidence?.recording?.observations?.[0]?.observed_at:(receipt.recording_started_at||evidence?.source?.recording_started_at),80)||"Unknown · not inferred from processing time");fact(facts,"Source SHA-256",str(receipt.source_sha256,64));fact(facts,"Source device",str(evidence?.source?.device_id,240));fact(facts,"Derived evidence SHA-256",str(receipt.derived_sha256,64));fact(facts,"Recording bundle SHA-256",str(receipt.recording_sha256,64));fact(facts,"Model",str(receipt.model_id||evidence?.model_id,160));fact(facts,"Model SHA-256",str(receipt.model_sha256||evidence?.model_sha256,64));panel.append(facts);

    if(receipt.error)p(panel,str(receipt.error),"vision-warning");if(receipt.failure_sha256){const failure=e("section","vision-uncertain"),facts=e("dl","vision-facts");failure.append(e("strong","","Review the retained failure"));fact(facts,"Exact failure SHA-256",str(receipt.failure_sha256,64));failure.append(facts);p(failure,"Confirming review keeps this failure and permits a separate new request. This failed request is never resumed or retried.");if(["failed","interrupted"].includes(receipt.status)&&receipt.reviewed!==true&&receipt.failure_reviewed!==true){let discard=null;if(receipt.temporary_source){p(failure,"This interrupted job still retains a private temporary video copy ("+byteCount(receipt.temporary_source.bytes)+" bytes). New work stays held until you explicitly discard that copy.","vision-warning");const label=e("label","vision-checkbox");discard=e("input");discard.type="checkbox";discard.name="discard_temporary_source";discard.checked=false;fields.push(discard);label.append(discard,e("span","","Discard this failed job’s temporary input copy (not your original Workspace video)"));failure.append(label);}const confirm=app.button("Confirm review and allow new work","button button-secondary",()=>{if(recoveryReady())return acknowledgeFailure(discard?.checked===true);});recoveries.push(confirm);failure.append(confirm);}else p(failure,"Failure review is retained. This request remains ineligible for retry.");panel.append(failure);}

    if(receipt.status==="completed"&&receipt.verified===true&&evidence){p(panel,"These samples describe the original recording. They do not establish current presence, absence or safety.","vision-historical");const frames=e("div","vision-frames");for(const [index,frame] of arr(evidence.frames,120).entries()){if(!frame||typeof frame!=="object")continue;const card=e("article","vision-frame"),offset=Number.isFinite(frame.timeline_offset_ms)?frame.timeline_offset_ms:Number.isFinite(frame.offset_ms)?frame.offset_ms:Number.isFinite(frame.replay_offset_ms)?frame.replay_offset_ms:null;card.append(e("h4","",offset!==null?"Sample +"+(offset/1000).toFixed(3)+" s":"Sample · offset unknown"));p(card,"Original frame time: "+(str(evidence.recording?.observations?.[index]?.observed_at||frame.observed_at||frame.captured_at,80)||"unknown"));p(card,"Motion: "+motion(frame.motion?.state||frame.motion));objects(card,frame.objects||frame.detections);const provenance=e("details","vision-frame-hashes");provenance.append(e("summary","","Sample provenance"));const hashes=e("dl","vision-facts");fact(hashes,"Decode / capture acquisition time",str(frame.acquired_at,80));fact(hashes,"Timestamp basis",str(frame.timestamp_basis,60));fact(hashes,"Independent motion method",str(frame.motion?.method,100));fact(hashes,"Decoded frame SHA-256",str(frame.decoded_frame_sha256||frame.decoded_sha256||frame.frame_sha256,64));fact(hashes,"Inference input SHA-256",str(frame.inference_input_sha256||frame.inference_sha256,64));provenance.append(hashes);card.append(provenance);frames.append(card);}if(!frames.childNodes.length)p(frames,"No verified frame rows are available.");panel.append(frames);if((evidence.frames?.length||0)>120)p(panel,"Showing the first 120 retained samples.");if(receipt.thumbnail_available===true||receipt.thumbnail===true||receipt.thumbnail_retained===true||receipt.artifacts?.some?.(item=>/thumbnail|snapshot/.test(str(item.name,80))))panel.append(button("Show retained thumbnail",showThumbnail));if(imageUrl){const image=e("img","vision-thumbnail");image.src=imageUrl;image.alt="Opted-in retained frame from the selected historical recording";panel.append(image);}}

    else p(panel,"Completed verified evidence is unavailable. A failed or unfinished request is never presented as an all clear.","vision-warning");root.append(panel);

  }

  function render(){const root=$("vision-content");if(!root||!active())return;root.replaceChildren();writes=[];reads=[];fields=[];recoveries=[];hero(root);const status=e("div","vision-status");status.id="vision-status";status.setAttribute("role","status");root.append(status);const note=e("p","vision-notice",notice);note.id="vision-notice";note.setAttribute("role","alert");root.append(note);if(pending){const box=e("section","vision-uncertain");box.append(e("strong","","Request outcome needs inspection"));p(box,pending.id+" · no automatic retry. Inspect this job before starting another capture or analysis.");box.append(button("Inspect interrupted request",inspectPending));root.append(box);}const currentSlot=e("div");currentSlot.id="vision-current-slot";currentView(currentSlot);root.append(currentSlot);const observation=e("div","vision-observation-grid"),previewSlot=e("section","vision-panel vision-preview-panel");previewSlot.id="vision-preview-slot";observation.append(previewSlot);descriptionPanel(observation);root.append(observation);refreshPreview();refreshDescription();intake(root);const results=e("div","vision-results"),historySlot=e("div");historySlot.id="vision-history-slot";history(historySlot);results.append(historySlot);result(results);root.append(results);statusText();controls();updateCountdown();}

  function statusText(){const item=$("vision-status");if(!item)return;item.textContent=paused()?"Global pause is active or authority is unavailable. Capture and analysis are blocked.":board?.hold?"Failure hold: "+(str(board.hold_reason||board.recovery?.reason||(typeof board.recovery==="string"?board.recovery:""))||"Inspect the retained failure before further work."):stale?"Vision status is unavailable or changed. Refresh before another action.":busy?"Submitting your explicit request…":loading?"Reading local Vision status…":board?.ready===true?"Local Vision backend available. Capture starts only with your explicit action.":"Vision backend unavailable: "+(str(board?.backend?.reason)||"runtime or model readiness has not been confirmed.");}

  function refreshNotice(){const item=$("vision-notice");if(item)item.textContent=notice;}

  function updateCountdown(){const node=$("vision-countdown"),value=camera();if(!node||!value)return;const end=Date.parse(value.expires_at||value.deadline_at||value.ends_at||"");const duration=Number.isFinite(value.duration_ms)?value.duration_ms:null,start=Date.parse(value.started_at||value.created_at||"");const remaining=Number.isFinite(end)?end-Date.now():duration!==null&&Number.isFinite(start)?start+duration-Date.now():null;node.textContent=remaining===null?"Capture limit enforced by the backend · remaining time unavailable":"Capture limit · "+Math.max(0,Math.ceil(remaining/1000))+" s remaining";}

  function schedule(){clearTimeout(timer);timer=null;if(!active()||(![...sessions(),...recordings()].some(isRunning)&&!descriptionSubmitting&&(!descriptionPending||descriptionPending.uncertain)))return;timer=setTimeout(async()=>{if(!active())return;refreshLive();schedule();if(!polling&&[...sessions(),...recordings()].some(isRunning))await poll();if(!descriptionFlight)await pollDescription();},1000);}

  function refreshLive(){const slot=$("vision-current-slot"),historySlot=$("vision-history-slot");if(slot){slot.replaceChildren();currentView(slot);}if(historySlot){historySlot.replaceChildren();history(historySlot);}refreshPreview();statusText();refreshNotice();controls();updateCountdown();}

  async function poll(){
    const mark=generation,token=app.state.token,flight={},owner=ownedSession;let rejectedLease=false;polling=flight;
    try{
      if(owner&&leaseToken&&ownedCamera()&&!paused()&&document.visibilityState!=="hidden"){
        try{await app.api("/vision/camera/lease",{method:"POST",body:{session_id:owner,lease_token:leaseToken}});}
        catch(error){leaseToken=null;clearPreview();if(error.status!==409)throw error;rejectedLease=true;}
      }
      if(!current(mark,token))return;
      const value=await app.api("/vision");if(!current(mark,token))return;
      board=value;boardReadAt=elapsedClock();stale=false;
      const ended=owner?arr(value.sessions,100).find(row=>row.session_id===owner):null;
      if(owner&&ended&&!isRunning(ended)){
        ownedSession=null;leaseToken=null;clearPreview("Camera off. Preview memory was cleared.");
        if(rejectedLease||/^Camera status unavailable\.|^Stop requested\./.test(notice))notice="Camera session ended. Its recorded result remains available below.";
        if(selected===ended.request_id&&isRunning(detail?.receipt))void selectRecording(selected);
      }else if(rejectedLease){stale=true;notice="Camera lease was revoked or expired. Current conditions are unknown; capture is never resumed automatically.";}
      refreshLive();if(previewAllowed())void loadPreview();
    }catch(error){if(current(mark,token)){stale=true;leaseToken=null;clearPreview();notice="Camera status unavailable. Current conditions are unknown. The bounded capture lease remains enforced by the backend.";refreshLive();}}
    finally{if(polling===flight)polling=null;}
  }

  async function load(){
    if(!app?.state.authenticated||app.state.view!=="vision"||busy)return;visible=true;
    if(descriptionSubmitting){descriptionSubmitting=false;if(descriptionPending)descriptionPending.uncertain=true;descriptionNotice="Description response interrupted by refresh. Inspect the retained request; no automatic retry.";}
    const mark=++generation,token=app.state.token;loading=true;clearPreview();render();
    try{const value=await app.api("/vision");if(!current(mark,token))return;board=value;boardReadAt=elapsedClock();stale=false;if(/^Camera status unavailable\.|^Vision status could not be read:|^Stop requested\./.test(notice))notice="";void descriptionStatus(mark,token).catch(error=>{if(current(mark,token)){descriptionBoard=null;descriptionNotice="Local description status unavailable: "+str(error.message);refreshDescription();}});}
    catch(error){if(current(mark,token)){board=null;detail=null;clearImage();clearPreview();stale=true;notice="Vision status could not be read: "+str(error.message);}}
    finally{if(current(mark,token)){loading=false;render();schedule();}}
  }

  async function inspectFile(){if(!active()||busy||loading)return;let source;try{source=path(input.source_path);}catch(error){notice=error.message;render();return;}const mark=generation,token=app.state.token;busy=true;notice="";controls();try{const value=await app.api("/vision/inspect",{method:"POST",body:{source_path:source}});if(!current(mark,token)||input.source_path.replaceAll("\\","/")!==source)return;input.source_sha256=hash(value.source_sha256);notice="Selected file inspected. Review its hash and original capture time, then explicitly analyze.";}catch(error){if(current(mark,token)){input.source_sha256="";notice="File inspection failed: "+str(error.message);}}finally{if(current(mark,token)){busy=false;render();}}}

  async function discover(){if(!active()||busy||loading||camera())return;const mark=generation,token=app.state.token;busy=true;notice="";controls();try{const value=await app.api("/vision/cameras");if(!current(mark,token))return;devices=arr(value.devices,32).filter(item=>item&&typeof item.device_id==="string");input.device_id="";notice=value.supported===false?"Native camera discovery unavailable: "+(str(value.reason)||"the native backend is not ready."):devices.length?"Devices discovered without starting capture. Select a camera, then Start camera.":"No native cameras were found. Current presence and motion remain unknown.";}catch(error){if(current(mark,token)){devices=[];input.device_id="";notice="Camera discovery failed: "+str(error.message);}}finally{if(current(mark,token)){busy=false;render();}}}

  async function mutate(kind){if(!ready())return;let request;try{request=body(kind);}catch(error){notice=error.message;render();return;}const mark=generation,token=app.state.token;pending={id:request.request_id,kind};input.thumbnail=false;busy=true;notice="";render();try{const value=await app.api(kind==="camera"?"/vision/camera/start":"/vision/video",{method:"POST",body:request});if(!current(mark,token))return;const receipt=value.receipt||value;if(receipt.request_id!==request.request_id)throw new Error("The response did not match this request. Its outcome is uncertain.");if(kind==="camera"&&receipt.session_id){ownedSession=receipt.session_id;leaseToken=value.lease_token||receipt.lease_token||null;}pending=null;busy=false;notice=kind==="camera"?"Camera session requested. Its active state and remaining limit appear above.":"Video analysis requested. Inspect the recorded result when complete.";await load();if(active())await selectRecording(request.request_id);}catch(error){if(current(mark,token)){if(Number.isInteger(error.status)&&error.status>=400&&error.status<500){pending=null;stale=true;notice=str(error.message)+" Refresh and inspect before another action.";}else notice="Request outcome is uncertain: "+str(error.message)+" No automatic retry.";}}finally{if(current(mark,token)){busy=false;render();schedule();}}}

  async function stopCamera({bestEffort=false}={}){const value=bestEffort?sessions().find(item=>item?.session_id===ownedSession&&isRunning(item)):camera();leaseToken=null;clearPreview("Stop requested. No preview image is retained.");if(active())refreshLive();if(!value?.session_id||!app?.state.token)return;const mark=generation,token=app.state.token;if(descriptionPending){revokedDescriptions.add(descriptionPending.id);descriptionNotice="Capture stop requested; an in-flight description will be revoked rather than treated as current.";}try{await app.api("/vision/camera/stop",{method:"POST",body:{session_id:value.session_id,expected_control_revision:revision()},keepalive:bestEffort});if(!current(mark,token)||bestEffort)return;notice="Stop requested. Wait for the backend to confirm camera off.";await poll();}catch(error){if(!bestEffort&&current(mark,token)){notice="Stop could not be confirmed: "+str(error.message)+" The backend capture limit still applies. Current conditions remain unknown.";stale=true;render();}}}

  async function selectRecording(value){if(!active()||busy||loading)return;try{identifier(value);}catch(error){notice=error.message;render();return;}const mark=generation,token=app.state.token,seq=++selection;selected=value;detail=null;clearImage();render();try{const result=await app.api("/vision/jobs/"+encodeURIComponent(value));if(!current(mark,token)||seq!==selection)return;if(result?.receipt?.request_id!==value)throw new Error("The retained receipt does not match this selection.");detail=result;}catch(error){if(current(mark,token)&&seq===selection)notice="Recorded result unavailable: "+str(error.message);}finally{if(current(mark,token)&&seq===selection)render();}}

  async function acknowledgeFailure(discardTemporarySource=false){if(!recoveryReady())return;if(detail.receipt.temporary_source&&discardTemporarySource!==true){notice="Choose whether to discard the failed job’s temporary copy before confirming review. Your original Workspace video stays untouched.";p($("vision-content"),notice,"vision-warning");return;}const request={request_id:detail.receipt.request_id,expected_revision:board.revision,expected_control_revision:revision(),failure_sha256:detail.receipt.failure_sha256},mark=generation,token=app.state.token;if(discardTemporarySource===true)request.discard_temporary_source=true;busy=true;notice="";controls();try{const value=await app.api("/vision/acknowledge",{method:"POST",body:request});if(!current(mark,token))return;if(value.request_id!==request.request_id||value.failure_sha256!==request.failure_sha256||value.status!=="reviewed"||value.retry_permitted!==false)throw new Error("Failure review response did not match this exact retained failure.");busy=false;notice="Failure review saved. The failure is retained; only a separate new request may run. Nothing was retried.";await load();if(active())await selectRecording(request.request_id);}catch(error){if(current(mark,token)){stale=true;notice="Failure review could not be confirmed: "+str(error.message)+" Refresh and inspect; no automatic retry.";}}finally{if(current(mark,token)){busy=false;render();}}}

  async function inspectPending(){if(!pending||!active()||busy||loading)return;const request=pending,mark=generation,token=app.state.token;try{const value=await app.api("/vision/jobs/"+encodeURIComponent(identifier(request.id)));if(!current(mark,token)||request!==pending)return;if(value?.receipt?.request_id!==request.id)throw new Error("The retained receipt does not match this request.");pending=null;notice="Request inspected. No action was retried.";await load();await selectRecording(request.id);}catch(error){if(current(mark,token)){notice="Request remains uncertain: "+str(error.message)+" No automatic retry.";render();}}}

  async function showThumbnail(){if(!active()||busy||loading||!selected||detail?.receipt?.verified!==true)return;const mark=generation,token=app.state.token,seq=selection,id=selected;try{const blob=await app.api("/vision/snapshots/"+encodeURIComponent(identifier(id)),{blob:true});if(!current(mark,token)||seq!==selection||selected!==id)return;if(!["image/jpeg","image/png"].includes(blob.type)||blob.size>4*1024*1024)throw new Error("Unsupported thumbnail response.");clearImage();imageUrl=URL.createObjectURL(blob);render();}catch(error){if(current(mark,token)&&seq===selection){notice="Thumbnail unavailable: "+str(error.message);render();}}}

  function suspend(){stopCamera({bestEffort:true});ownedSession=null;leaseToken=null;clearPreview();clearTimeout(timer);timer=null;generation++;descriptionSelection++;descriptionSubmitting=false;if(descriptionPending)descriptionPending.uncertain=true;busy=false;loading=false;stale=true;notice="This page was hidden or closed. Capture is not resumed. Refresh status and explicitly start a new session when ready.";if(active())render();}

  function clear(){clearTimeout(timer);timer=null;generation++;selection++;descriptionSelection++;visible=false;busy=false;loading=false;board=null;detail=null;devices=[];selected="";notice="";stale=false;ownedSession=null;leaseToken=null;boardReadAt=0;polling=null;input={source_path:"",source_sha256:"",recording_started_at:"",duration_seconds:"30",device_id:"",thumbnail:false,preview:true,question:"Briefly describe what is visible in this frame."};clearImage();clearPreview();previewFlight=null;previewLastFetch=-Infinity;descriptionBoard=null;descriptionDetail=null;descriptionPending=null;descriptionSubmitting=false;descriptionFlight=null;descriptionNotice="";revokedDescriptions.clear();writes=[];reads=[];fields=[];recoveries=[];$("vision-content")?.replaceChildren();}

  window.ariadneVision={attach(value){app=value;if(attached===value)return;attached=value;$("refresh-vision")?.addEventListener("click",load);window.addEventListener("pagehide",suspend);document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="hidden")suspend();});},load,discover,inspectFile,mutate,stopCamera,selectRecording,inspectPending,path,body,showThumbnail,acknowledgeFailure,loadPreview,startDescription,selectDescription,reviewDescriptionFailure,

    status(){if(!active())return;if(paused()&&ownedSession){stopCamera({bestEffort:true});ownedSession=null;}refreshLive();},

    locking(){stopCamera({bestEffort:true});},locked:clear,leave(){stopCamera({bestEffort:true});clear();}};

})();

