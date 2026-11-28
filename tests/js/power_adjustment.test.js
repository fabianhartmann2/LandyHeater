"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const web=path.join(__dirname,"../../web");
const nodes=new Map();

class Element {
  constructor(tag="div",className="",textContent=""){
    this.tagName=tag;
    this.className=className;
    this.textContent=textContent;
    this.children=[];
    this.handlers={};
    this.attributes={};
    const get=()=>new Set(this.className.split(/\s+/).filter(Boolean));
    this.classList={contains:key=>get().has(key),add:key=>this.classList.toggle(key,true),toggle:(key,force)=>{
      const classes=get();
      if(force??!classes.has(key))classes.add(key);else classes.delete(key);
      this.className=[...classes].join(" ");
    }};
  }
  setAttribute(key,value){this.attributes[key]=String(value)}
  insertAdjacentHTML(_position,markup){importElements(markup)}
  replaceChildren(...children){this.children=children}
  append(...children){this.children.push(...children)}
  addEventListener(event,callback){this.handlers[event]=callback}
  focus(){document.activeElement=this}
}

function importElements(markup){
  for(const [,tag,id,attributes] of markup.matchAll(/<(\w+) id="([^"]+)"([^>]*)>/g)){
    const classes=attributes.match(/ class="([^"]*)"/);
    const value=attributes.match(/ value="([^"]*)"/);
    const node=new Element(tag,classes?.[1]);
    if(value)node.value=value[1];
    nodes.set(id,node);
  }
}

importElements(fs.readFileSync(path.join(web,"index.html"),"utf8"));
global.document={activeElement:null,querySelectorAll:()=>[],createElement:tag=>new Element(tag)};
global.location={origin:"http://192.168.4.1"};
const requests=[],toasts=[];
let deferred;
const L={
  $:id=>nodes.get(id),
  el:(tag,classes,text)=>new Element(tag,classes,text),
  text:(node,value)=>{node.textContent=value},
  t:key=>window.LANDY_I18N[L.state.language][key]||key,
  mode:value=>value,
  temperature:()=>"–",
  minutes:value=>String(value),
  health:()=>"–",
  toast:(message,error)=>toasts.push({message,error}),
  setConnection:()=>{},
  state:{busy:false,language:"de",status:null},
  modules:{},
  loadStatus:async()=>L.state.status,
  controlMutate:(route,method,body,useEtag)=>{
    requests.push({route,method,body,useEtag});
    if(route.endsWith("/stop")){
      show(powerStatus({on:false,session:null,state:"off",revision:9}));
      return Promise.resolve({changed:true});
    }
    return new Promise(resolve=>{deferred=resolve});
  },
};
global.window={Landy:L};
for(const source of ["i18n.js","home.js","api-console.js"]){
  vm.runInThisContext(fs.readFileSync(path.join(web,source),"utf8"),{filename:source});
}
const home=L.modules.home;
home.bind();

function powerStatus({on=true,session,mode="power",state="running",revision=5,level=9,supported=true,update=null,synchronized=true}={}){
  return {heater:{requested:{on,mode,power_level:level},actual:{heater_state:state,communication:"ok",initialized:true,synchronized},
    session:session===undefined?{id:1,mode,target:mode==="power"?level:20,confirmed_active:true,expired:false}:session,
    request_revision:revision,...(supported?{power_update:update}:{})},configuration:{timer_start_allowed:true}};
}
function show(status){L.state.status=status;home.render(status)}
const hidden=id=>L.$(id).classList.contains("hidden");
const click=id=>{const node=L.$(id);assert(!node.disabled,id+" disabled");return node.handlers.click()};
const setRange=value=>{const node=L.$("adjust-power-range");node.value=String(value);node.handlers.input()};
const pending={state:"pending",power_level:4,reason:null};

async function testHome(){
  for(const status of [powerStatus({supported:false}),powerStatus({mode:"cabin_temperature"}),powerStatus({state:"starting"}),powerStatus({synchronized:false}),powerStatus({on:false}),powerStatus({session:null})]){
    show(status);
    assert(hidden("adjust-power"));
    assert(hidden("power-adjust-panel"));
  }
  show(powerStatus());
  assert.equal(L.$("target-label").textContent,"Sollleistung");
  assert(!hidden("adjust-power"));
  assert(hidden("adjust-target"));
  click("adjust-power");
  assert.equal(L.$("adjust-power").attributes["aria-expanded"],"true");
  assert.equal(L.$("adjust-power-output").textContent,"9 / 9");
  assert(L.$("apply-power").disabled);
  setRange(4);
  assert.equal(L.$("adjust-power-output").textContent,"4 / 9");
  show(powerStatus());
  assert(!hidden("power-adjust-panel"),"unchanged polling must not reset the draft");
  show(powerStatus({revision:6}));
  assert(hidden("power-adjust-panel"),"changed request revision must invalidate the draft");
  click("adjust-power");
  setRange(4);
  const submitted=click("apply-power");
  assert.deepEqual(requests[0],{route:"/api/v1/heater/session",method:"PATCH",body:{expected_request_revision:6,target_temperature:null,extend_minutes:0,power_level:4},useEtag:undefined});
  assert.match(L.$("power-update-status").textContent,/angefragt/);
  deferred({updated:true,heater:{request_revision:6,power_update:pending}});
  await submitted;
  assert.equal(L.$("active-target").textContent,"9 / 9", "staging must not change the displayed setting");
  assert.match(L.$("power-update-status").textContent,/Warte auf Rückmeldung/);
  assert(L.$("adjust-power").disabled);
  assert(!L.$("stop-heater").disabled);

  show(powerStatus({level:4,revision:7,update:{...pending,state:"applied"}}));
  assert.equal(L.$("active-target").textContent,"4 / 9");
  assert.match(L.$("power-update-status").textContent,/bestätigt/);
  assert(!L.$("adjust-power").disabled);
  show(powerStatus({level:9,revision:7,update:{...pending,state:"failed",reason:"timeout"}}));
  assert.match(L.$("power-update-status").textContent,/konnte nicht bestätigt/);
  click("adjust-power");
  assert(!L.$("apply-power").disabled,"same-level retry is available after an uncertain write");
  setRange(4);
  const inFlight=click("apply-power");
  await click("stop-heater");
  assert.equal(requests[2].route,"/api/v1/heater/stop","in-flight power request must not swallow stop");
  deferred({updated:true,heater:{request_revision:7,power_update:pending}});
  await inFlight;
  assert(hidden("adjust-power"));
  assert(hidden("power-update-status"),"a stale PATCH response must not re-open power UI after stop");
  assert.equal(toasts.filter(value=>value.error).length,0);
}

function testConsole(){
  const spec=JSON.parse(fs.readFileSync(path.join(web,"openapi.json"),"utf8"));
  const route="/api/v1/heater/session",pathItem=spec.paths[route],operation=pathItem.patch;
  const host=new Element();
  L.modules.apiConsole.render(host,{spec,path:route,method:"patch",pathItem,operation});
  function all(node){return [node,...node.children.flatMap(all)]}
  const container=all(host);
  const body=container.find(node=>node.tagName==="textarea");
  const curl=container.find(node=>node.className==="api-curl");
  assert.equal(JSON.parse(body.value).power_level,4);
  assert.match(curl.textContent,/"power_level": 4/);
  assert.match(curl.textContent,/heater\.power_update/);
  assert.equal(requests.length,3,"rendering cURL must never submit requests");
}

function testWarnings(){
  const warnings=L.$("warnings"), since=Date.UTC(2026,10,29,12,30)/1000;
  const status=powerStatus();
  status.warnings=["clock_invalid","sensor_cabin_stale"];
  status.warning_details={
    clock_invalid:{since_unix:null,age_seconds:123},
    sensor_cabin_stale:{since_unix:since,age_seconds:65},
    heater_control_fault:{since_unix:since,age_seconds:10},
  };
  show(status);
  assert.equal(L.$("warning-count").textContent,2);
  assert.equal(warnings.children.length,2,"inactive details must not render");
  assert.equal(warnings.children[0].children[1].textContent,"vor 2 Minuten");
  const stamp=warnings.children[1].children[1];
  assert.equal(stamp.tagName,"time");
  assert.equal(stamp.attributes.datetime,"2026-11-29T12:30:00.000Z");
  assert.match(stamp.textContent,/^Seit /);

  L.state.language="en";
  status.warning_details.clock_invalid.age_seconds=0;
  show(status);
  assert.equal(warnings.children[0].children[1].textContent,"Just now");
  assert.match(warnings.children[1].children[1].textContent,/^Since /);
  assert.equal(warnings.children[1].children[1].attributes.datetime,stamp.attributes.datetime);

  show({...status,warning_details:undefined});
  assert.equal(warnings.children[0].textContent,"clock invalid");
  assert.equal(warnings.children[0].children.length,0,"do not invent a time for older responses");
  show({...status,warnings:[]});
  assert.equal(L.$("warning-count").textContent,0);
  assert.equal(warnings.children[0].className,"empty");

  status.configuration.restart_required=true;
  status.warnings=["runtime_restart_required"];
  status.warning_details.runtime_restart_required={since_unix:since,age_seconds:65};
  show(status);
  assert.equal(warnings.children[0].textContent,"Restart required. Until then, sensors keep using the previous settings.");
  assert.equal(warnings.children[0].children[1].attributes.datetime,stamp.attributes.datetime);
  L.state.language="de";
  show(status);
  assert.match(warnings.children[0].textContent,/^Neustart erforderlich\..*bisherigen Einstellungen/);
}

testHome().then(testConsole).then(testWarnings).catch(error=>{console.error(error);process.exitCode=1});
