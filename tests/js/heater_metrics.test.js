"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const test=require("node:test");
const vm=require("node:vm");
const web=path.join(__dirname,"../../web");

function makeNode(){
  return {dataset:{},textContent:"",className:"",replaceChildren(){},append(){}};
}

test("diagnostics groups heater metrics immediately below the system overview",()=>{
  const html=fs.readFileSync(path.join(web,"diagnostics.html"),"utf8");
  assert.match(html,/<div class="diagnostic-system-stack">\s*<article[^\n]+id="diagnostics-system"[^\n]+\n\s*<article[^\n]+id="diagnostics-heater-metrics"/);
});

test("heater metrics require valid diagnostic data, preserve raw zero, and mark stale values",async()=>{
  const nodes=new Map(),calls=[];
  const element=id=>{
    if(!nodes.has(id))nodes.set(id,makeNode());
    return nodes.get(id);
  };
  let latest=null,error=false;
  const L={$:element,state:{language:"de",busy:false},modules:{},
    el:makeNode,boolean:value=>String(value),text:(node,value)=>{node.textContent=value},
    t:key=>context.window.LANDY_I18N[L.state.language][key]||key,
    definition:(node,rows)=>{node.rows=rows},
    request:async url=>{
      calls.push(url);
      if(error)throw new Error("Offline");
      return {live:{status:{heater:{actual:latest}},uptime_ms:23125}};
    },
  };
  const context={window:{Landy:L},location:{hash:"#diagnostics"},document:{hidden:false}};
  for(const source of ["i18n.js","diagnostics.js"]){
    vm.runInNewContext(fs.readFileSync(path.join(web,source),"utf8"),context,{filename:source});
  }
  const diagnostics=L.modules.diagnostics;
  const status=()=>element("heater-metrics-status").textContent;
  const rows=()=>Object.fromEntries(element("diagnostics-heater-metrics").rows);

  const healthy={communication:"ok",heater_state:"running",voltage:12.6,
    glow_plug_raw:0,fan_raw:91,heater_state_raw:4,status_age_ms:250};
  latest=healthy;
  await diagnostics.load();
  assert.deepEqual(rows(),{"Versorgungsspannung":"12,6 V","Glühkerze (Rohwert)":"0",
    "Lüfter (Rohwert)":"91","Heizungszustand (Rohwert)":"0x04","Alter der letzten Antwort":"0,3 s"});
  assert.equal(status(),"Aktuell");

  latest={...healthy,status_age_ms:3000};
  await diagnostics.load();
  assert.match(status(),/nicht aktuell/);
  assert.equal(rows()["Lüfter (Rohwert)"],"91");

  latest={...healthy,communication:"error"};
  await diagnostics.load();
  assert.match(status(),/nicht aktuell/);
  assert.equal(rows()["Glühkerze (Rohwert)"],"0");

  error=true;
  await diagnostics.load();
  assert.match(status(),/nicht aktuell/);
  assert.equal(rows()["Versorgungsspannung"],"12,6 V");
  assert.equal(rows()["Alter der letzten Antwort"],"–");

  error=false;
  latest={communication:"unknown",voltage:null,glow_plug_raw:-1,fan_raw:256,heater_state_raw:"0",status_age_ms:null};
  await diagnostics.load();
  assert.equal(status(),"Keine Messwerte");
  assert(Object.values(rows()).every(value=>value==="–"));

  latest={...healthy,heater_state:"off",fan_raw:0,heater_state_raw:0};
  L.state.language="en";
  await diagnostics.load();
  assert.equal(status(),"Current");
  assert.equal(rows()["Supply voltage"],"12.6 V");
  assert.equal(rows()["Fan (raw)"],"0");
  assert.equal(rows()["Heater state (raw)"],"0x00");
  assert.equal(element("heater-metrics-status").dataset.i18n,"heaterMetricsCurrent");
  assert.equal(calls.length,6);
  assert(calls.every(route=>route.startsWith("/api/v1/diagnostics?")),"metrics need no extra request");
});
