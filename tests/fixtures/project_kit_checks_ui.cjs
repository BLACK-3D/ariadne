"use strict";
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync(path.join(__dirname, "../../ariadne/static/app.js"), "utf8");
const first = source.indexOf("const checkTemplates = {"), last = source.indexOf("function renderVerification(", first);
assert.ok(first > 0 && last > first);
const context = vm.createContext({});
vm.runInContext(source.slice(first,last) + "\nthis.kitChecks = {parseChecks,checkDefinitions,checkDescription};", context);
const api = context.kitChecks; let passed = 0;
const check = (label, action) => { try { action(); passed += 1; } catch(error) { error.message = label + ": " + error.message; throw error; } };
const ordinary = value => JSON.parse(JSON.stringify(value));
const exact = {kind:"json_value",path:"output/plan.json",pointer:"/source_sha256",equals:"a".repeat(64)};
const sized = {kind:"file_size",path:"output/plan.json",max_bytes:2500};
check("provenance and byte checks survive editing roundtrip", () => {
  const parsed = api.parseChecks(JSON.stringify([exact,sized]));
  assert.deepEqual(ordinary(api.checkDefinitions(parsed)),[exact,sized]);
});
for (const value of [null,true,false,0,Number.MAX_SAFE_INTEGER,Number.MIN_SAFE_INTEGER,"", "é"])
  check("exact typed scalar " + JSON.stringify(value), () => assert.deepEqual(ordinary(api.parseChecks(JSON.stringify([{...exact,equals:value}]))),[{...exact,equals:value}]));
for (const value of [{},[],1.5,Number.MAX_SAFE_INTEGER+1,"x".repeat(4001)])
  check("reject unsafe expectation " + String(value).slice(0,20), () => assert.throws(() => api.parseChecks(JSON.stringify([{...exact,equals:value}]))));
for (const value of [0,10000001,true,"2500",1.5,null])
  check("reject invalid byte budget " + value, () => assert.throws(() => api.parseChecks(JSON.stringify([{...sized,max_bytes:value}]))));
for (const pointer of ["missing","/~2","/x".repeat(33)])
  check("reject invalid pointer " + pointer, () => assert.throws(() => api.parseChecks(JSON.stringify([{...exact,pointer}]))));
check("descriptions show exact type and byte bound", () => {
  assert.match(api.checkDescription({...exact,equals:false}), /false/);
  assert.match(api.checkDescription(sized), /2500 bytes/);
});
check("old decimal checks remain unchanged", () => {
  const old={kind:"json_number",path:"output/summary.json",pointer:"/total",value:"16.00"};
  assert.deepEqual(ordinary(api.checkDefinitions(api.parseChecks(JSON.stringify([old])))),[old]);
});
check("absolute and parent paths are refused", () => {
  for(const target of ["../escape.json","C:/private.json","/root.json"])
    assert.throws(() => api.parseChecks(JSON.stringify([{...exact,path:target}])));
});
check("shape fields roundtrip without dropping checks", () => {
  const shape={kind:"json_shape",path:"output/plan.json",fields:{scope:"string",unknowns:"strings",draft:"boolean"}};
  assert.deepEqual(ordinary(api.checkDefinitions(api.parseChecks(JSON.stringify([shape])))),[shape]);
});
check("shape rejects unsupported executable or recursive schema forms", () => {
  for(const fields of [{},{x:{$ref:"https://example.invalid"}},{x:"object"},{"$ref":"string"}])
    assert.throws(() => api.parseChecks(JSON.stringify([{kind:"json_shape",path:"output/plan.json",fields}])));
});
process.stdout.write(JSON.stringify({passed,failures:0,scope:"Synthetic check-editor roundtrip; no browser, network, owner state or model."}));
