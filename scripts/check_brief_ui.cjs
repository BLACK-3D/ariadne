"use strict";
// Actual poster callbacks in a minimal controlled DOM. No browser, model,
// owner data, microphone, network or real timers are used by these fixtures.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "ariadne/static/app.js"), "utf8");
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== "--receipt" || !args[1])) throw new Error("Usage: node scripts/check_brief_ui.cjs [--receipt PATH]");
const first = source.indexOf("function clearBriefPoster("), last = source.indexOf("async function saveBriefing(", first);
assert.ok(first >= 0 && last > first);
const callbacks = source.slice(first, last);
const checks = [];
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
function fixture(apiHandler) {
  const elements = new Map(), urls = new Map(), revoked = [], requests = [], downloads = [], images = [], timers = [], toasts = [], printed = [], canvases = [];
  const classes = new Set(); let nextUrl = 0;
  class Element {
    constructor(tag="div", text="") { this.tag=tag; this.children=[]; this._text=text; this.hidden=false; this.disabled=false; }
    get textContent() { return this._text+this.children.map(x => x.textContent || "").join(""); }
    set textContent(value) { this._text=String(value); this.children=[]; }
    set innerHTML(_value) { throw new Error("Poster data must not enter HTML markup"); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children=children; this._text=""; }
    removeAttribute(name) { delete this[name]; }
    click() { if(this.tag === "a") downloads.push({url:this.href,filename:this.download,blob:urls.get(this.href)}); else this.action?.(); }
    remove() { this.removed=true; }
  }
  for (const id of ["brief-poster-image","brief-poster-frame","brief-poster-note","brief-report-title","brief-report","brief-receipt","brief-artifacts"]) elements.set(id,new Element());
  const state = {authenticated:true,token:"fixture-session"};
  const sandbox = vm.createContext({state,Blob,
    briefViewRequest:0,briefLoadRequest:0,briefPosterUrl:null,briefPoster:null,
    $: selector => elements.get(selector.replace(/^#/,"")),
    clear: element => element.replaceChildren(),
    node: (tag,_class,text) => new Element(tag,text || ""),
    button: (label,_class,action) => {const element=new Element("button",label);element.action=action;return element;},
    humanize: value => String(value),
    toast: (...values) => toasts.push(values),
    downloadArtifact: value => downloads.push({artifact:value}),
    api: async route => { requests.push(route); return apiHandler ? apiHandler(route) : response(); },
    Image: class {constructor(){images.push(this);}},
    URL: {createObjectURL(blob){const id="blob:fixture-"+(++nextUrl);urls.set(id,blob);return id;},revokeObjectURL(url){revoked.push(url);urls.delete(url);}},
    document: {body:{append(){},classList:{add(name){classes.add(name);},remove(name){classes.delete(name);}}},
      createTextNode: text => new Element("text",text),
      createElement(tag){if(tag!=="canvas") throw new Error("Unexpected element"); const canvas={getContext(){return {drawImage(...values){canvas.drawn=values;}};},toBlob(callback,mime){canvas.mime=mime;canvas.callback=callback;}};canvases.push(canvas);return canvas;}},
    window:{print(){printed.push(classes.has("brief-printing"));}},
    setTimeout: callback => {timers.push(callback);return timers.length;},
  });
  vm.runInContext(callbacks,sandbox,{timeout:2000});
  return {sandbox,state,elements,urls,revoked,requests,downloads,images,timers,toasts,printed,canvases,classes};
}
function receipt(id="daily-2026-10-29") { return {request_id:id,status:"completed",day:"2026-10-29",artifacts:[{path:"briefings/"+id+"/brief.md"},{path:"briefings/"+id+"/brief.json"},{path:"briefings/"+id+"/brief.svg"}]}; }
function response() { return {content:"Saved report <literal>",poster:{mime:"image/svg+xml",width:1080,height:1350,svg:'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350"/>',accessible_text:"Literal <source> brief",note:"Saved evidence; not an all-clear."}}; }
function action(f,label){return f.elements.get("brief-artifacts").children.find(element=>element.textContent===label);}
async function ready(f){await f.sandbox.showBriefReceipt(receipt());}
async function tick(){await Promise.resolve();await Promise.resolve();}
async function main(){
  {
    const f=fixture();await ready(f);
    assert.equal(f.requests.length,1);assert.ok(f.requests[0].startsWith("/briefing/report?request_id="));assert.ok(!f.requests[0].includes(f.state.token));
    assert.equal(f.elements.get("brief-report").textContent,"Saved report <literal>");
    assert.equal(f.elements.get("brief-poster-frame").hidden,false);assert.ok(f.elements.get("brief-poster-image").src.startsWith("blob:"));
    assert.equal(f.elements.get("brief-poster-image").alt,"Literal <source> brief");
    assert.deepEqual(f.elements.get("brief-artifacts").children.map(x=>x.textContent),["Download PNG","Download SVG","Print one page","Full report","Evidence JSON"]);
    checks.push("verified poster is displayed through an inert blob image, literal text, and one authenticated JSON request without a credential URL");
    const prior=f.elements.get("brief-poster-image").src;f.sandbox.clearBriefPoster(true);
    assert.ok(f.revoked.includes(prior));assert.equal(f.elements.get("brief-poster-frame").hidden,true);assert.equal(f.elements.get("brief-poster-image").src,undefined);assert.equal(f.elements.get("brief-poster-image").alt,"One-page Ariadne daily brief");assert.equal(f.elements.get("brief-poster-note").textContent,"");assert.equal(f.sandbox.briefPoster,null);assert.equal(f.sandbox.briefLoadRequest,1);assert.equal(f.elements.get("brief-artifacts").children.length,0);
    checks.push("locking clears private poster bytes/URL and controls while invalidating pending load generation");
  }
  {
    const waiting=deferred();let count=0;const f=fixture(()=>++count===1?waiting.promise:response());
    const old=f.sandbox.showBriefReceipt(receipt("old"));await f.sandbox.showBriefReceipt(receipt("new"));const current=f.elements.get("brief-poster-image").src;
    waiting.resolve({...response(),content:"Old private brief"});await old;
    assert.equal(f.elements.get("brief-poster-image").src,current);assert.equal(f.elements.get("brief-report").textContent,"Saved report <literal>");
    checks.push("a late old report cannot replace a more recently selected brief");
  }
  {
    const waiting=deferred(),f=fixture(()=>waiting.promise);const pending=f.sandbox.showBriefReceipt(receipt());
    f.state.authenticated=false;f.sandbox.clearBriefPoster(true);f.state.authenticated=true;
    waiting.resolve(response());await pending;assert.equal(f.sandbox.briefPoster,null);assert.equal(f.elements.get("brief-poster-frame").hidden,true);
    checks.push("a report completing after lock and re-pair cannot restore private poster content even with the same pairing key");
  }
  {
    const f=fixture();await ready(f);await f.sandbox.showBriefReceipt(null);assert.equal(f.sandbox.briefPoster,null);assert.equal(f.elements.get("brief-report-title").textContent,"Awaiting the first brief");
    checks.push("an absent receipt clears the previous report instead of presenting stale content as the latest brief");
  }
  {
    const f=fixture(()=>Promise.reject(new Error("Saved evidence changed")));await ready(f);
    assert.equal(f.sandbox.briefPoster,null);assert.equal(f.toasts.length,1);assert.ok(f.elements.get("brief-report").textContent.includes("Saved evidence changed"));
    checks.push("changed or unreadable evidence produces an explicit error without a stale poster or unhandled rejection");
  }
  {
    const f=fixture();const value=receipt();value.status="failed";await f.sandbox.showBriefReceipt(value);assert.equal(f.requests.length,0);assert.equal(f.sandbox.briefPoster,null);
    checks.push("failed partial reports are not exported as completed briefs");
  }
  {
    const f=fixture(()=>{const value=response();value.poster.width=20000;return value;});await ready(f);assert.equal(f.sandbox.briefPoster,null);assert.equal(action(f,"Download PNG"),undefined);
    checks.push("invalid poster dimensions leave original evidence available without enabling poster export");
  }
  {
    const f=fixture();await ready(f);await f.sandbox.downloadBriefPoster("svg",action(f,"Download SVG"));assert.equal(f.downloads.length,1);assert.equal(f.downloads[0].blob.type,"image/svg+xml");assert.equal(f.downloads[0].filename,"ariadne-brief-2026-10-29.svg");f.timers.forEach(fn=>fn());
    checks.push("SVG download uses only the local bounded poster blob with a safe filename and releases its temporary URL");
  }
  {
    const f=fixture();await ready(f);const control=action(f,"Download PNG"),pending=f.sandbox.downloadBriefPoster("png",control);assert.equal(control.disabled,true);f.images[0].onload();await tick();
    const canvas=f.canvases[0];assert.equal(canvas.width,1080);assert.equal(canvas.height,1350);assert.equal(canvas.mime,"image/png");canvas.callback(new Blob(["fixture PNG"],{type:"image/png"}));await pending;
    assert.equal(f.downloads[0].filename,"ariadne-brief-2026-10-29.png");assert.equal(control.disabled,false);
    checks.push("PNG export decodes locally and rasterizes to the exact 1080 by 1350 canvas without a server or model call");
  }
  {
    const f=fixture();await ready(f);const pending=f.sandbox.downloadBriefPoster("png",action(f,"Download PNG"));f.images[0].onload();await tick();f.sandbox.clearBriefPoster(true);f.canvases[0].callback(new Blob(["late"],{type:"image/png"}));await pending;
    assert.equal(f.downloads.length,0);assert.equal(f.toasts.length,0);
    checks.push("a late PNG callback after lock cannot download private content or report over a new session");
  }
  {
    const f=fixture();await ready(f);const control=action(f,"Download PNG"),pending=f.sandbox.downloadBriefPoster("png",control);f.images[0].onerror();await pending;
    assert.equal(f.downloads.length,0);assert.equal(f.toasts.length,1);assert.equal(control.disabled,false);assert.ok(f.toasts[0][0].includes("Download SVG"));
    checks.push("a local image decoding failure releases its URL/control and offers SVG rather than claiming PNG success");
  }
  {
    const f=fixture();await ready(f);f.sandbox.printBriefPoster();assert.deepEqual(f.printed,[true]);assert.equal(f.classes.size,0);
    f.state.authenticated=false;f.sandbox.printBriefPoster();assert.equal(f.printed.length,1);
    checks.push("print is an explicit authenticated action scoped to the one-page CSS mode and always restores the display");
  }
  assert.ok(source.includes("clearBriefPoster(true);"));
  checks.push("the maintained lock handler calls poster cleanup with load invalidation");
  const paths=["ariadne/static/app.js","ariadne/static/index.html","ariadne/static/command.css","scripts/check_brief_ui.cjs"];
  const uiReceipt={created_at_utc:new Date().toISOString(),passed:checks.length,failures:0,checks,
    source_sha256:Object.fromEntries(paths.map(name=>[name,crypto.createHash("sha256").update(fs.readFileSync(path.join(root,name))).digest("hex")])),
    scope:"Actual callbacks in controlled DOM/async fixtures. PNG encoding/image decoding and print layout are simulated here; native browser visual/PNG/print QA remains separate."};
  if(args.length) fs.writeFileSync(path.resolve(args[1]),JSON.stringify(uiReceipt,null,2)+"\n");
  console.log(JSON.stringify({passed:checks.length,failures:0}));
}
main().catch(error=>{console.error(error);process.exit(1);});
