"use strict";
(function(){
const L=window.Landy,roles=["roof_tent","cabin","outside"];
let data=null,step=0,required=false,opening=false;
const clone=value=>JSON.parse(JSON.stringify(value));
function labelForRole(role){return L.t({roof_tent:"roofTent",cabin:"cabin",outside:"outside"}[role])}
function rows(node,values){L.definition(node,values)}
function setStep(next){
  step=Math.max(0,Math.min(8,next));
  document.querySelectorAll("[data-setup-step]").forEach(node=>node.classList.toggle("hidden",Number(node.dataset.setupStep)!==step));
  L.text(L.$("setup-progress"),`${step+1} / 9`);L.$("setup-progress-bar").value=step+1;
  L.$("setup-back").disabled=step===0;L.$("setup-next").classList.toggle("hidden",step===8);L.$("setup-finish").classList.toggle("hidden",step!==8);
  if(step===7)renderSummary();
}
function configuredNetwork(profile){
  const card=L.el("div","setup-network");card.dataset.id=profile.id||`wifi-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`;card.dataset.configured=profile.password_configured?"1":"0";
  const ssid=document.createElement("input");ssid.maxLength=32;ssid.required=true;ssid.value=profile.ssid||"";ssid.placeholder="SSID";
  const ssidLabel=document.createElement("label");ssidLabel.append(L.el("span",null,"SSID"),ssid);
  const password=document.createElement("input");password.type="password";password.minLength=8;password.maxLength=64;password.autocomplete="new-password";password.placeholder=profile.password_configured?L.t("keepPassword"):L.t("optionalOpenNetwork");
  const passwordLabel=document.createElement("label");passwordLabel.append(L.el("span",null,L.t("password")),password);
  const remove=L.el("button","button secondary compact","×");remove.type="button";remove.setAttribute("aria-label",L.t("delete"));remove.addEventListener("click",()=>card.remove());
  card.append(ssidLabel,passwordLabel,remove);return card;
}
function renderNetworks(){
  const list=L.$("setup-networks");list.replaceChildren();
  (data.network?.known_networks||[]).forEach(profile=>list.append(configuredNetwork(profile)));
}
function sensorOptions(select,current){
  select.append(new Option("–",""));
  const found=new Set((data.checks?.sensors?.discovered||[]).map(item=>item.rom_id));if(current)found.add(current);
  found.forEach(rom=>select.append(new Option(rom,rom)));select.value=current||"";
}
function renderSensors(){
  const state=data.checks?.sensors||{},found=state.discovered||[];
  L.text(L.$("setup-sensor-state"),found.length?`${found.length} ${L.t("sensorsObserved")}`:L.t("sensorCheckDeferred"));
  const container=L.$("setup-sensors");container.replaceChildren();
  roles.forEach(role=>{const label=L.el("label"),select=document.createElement("select");select.dataset.role=role;sensorOptions(select,data.sensors?.assignments?.[role]);label.append(L.el("span",null,labelForRole(role)),select);container.append(label)});
}
function fill(){
  L.$("setup-language").value=L.state.language;
  const clock=data.checks?.rtc||{};rows(L.$("setup-rtc"),[[L.t("clock"),clock.valid?L.t("valid"):L.t("invalid")],["RTC",clock.rtc_health||"–"],[L.t("source"),clock.source||"–"],[L.t("localTime"),clock.local||"–"],[L.t("timezone"),data.time?.timezone_name||"–"]]);
  renderNetworks();const ap=data.network?.access_point||{};L.$("setup-ap-password").value="";L.$("setup-ap-repeat").value="";L.text(L.$("setup-ap-hint"),ap.password_configured?L.t("passwordKeepHint"):L.t("passwordRequiredHint"));L.$("setup-ap-password").dataset.configured=ap.password_configured?"1":"0";
  renderSensors();const autoterm=data.checks?.autoterm||{};rows(L.$("setup-autoterm"),[[L.t("status"),autoterm.state||"not_run"],[L.t("communication"),autoterm.communication||"–"],[L.t("initialized"),L.boolean(autoterm.initialized)],[L.t("synchronized"),L.boolean(autoterm.synchronized)],[L.t("activeTest"),L.t(autoterm.active_test_performed?"yes":"no")]]);
  const heater=data.heater||{},quick=heater.quick_start||{};L.$("setup-mode").value=quick.mode||"roof_tent_temperature";L.$("setup-target").value=quick.target_temperature??20;L.$("setup-power").value=quick.power_level??5;L.$("setup-runtime").value=quick.runtime_minutes??60;L.$("setup-maximum").value=heater.maximum_runtime_minutes??120;syncMode();L.$("setup-confirm").checked=false;
}
function syncMode(){const power=L.$("setup-mode").value==="power";L.$("setup-target").disabled=power;L.$("setup-power").disabled=!power}
function validatePage(){
  if(step===3){const first=L.$("setup-ap-password").value,repeat=L.$("setup-ap-repeat").value,configured=L.$("setup-ap-password").dataset.configured==="1";if(!first&&configured)return true;if(first.length<8||first.length>63||first!==repeat){L.toast(L.t(first!==repeat?"passwordMismatch":"passwordLength"),true);return false}}
  if(step===2){for(const card of L.$("setup-networks").children){const inputs=card.querySelectorAll("input");if(!inputs[0].value.trim()){L.toast(L.t("ssidRequired"),true);return false}if(inputs[1].value&&inputs[1].value.length<8){L.toast(L.t("passwordLength"),true);return false}}}
  return true;
}
function collectNetworks(){
  return Array.from(L.$("setup-networks").children,card=>{const inputs=card.querySelectorAll("input"),password=inputs[1].value,configured=card.dataset.configured==="1";return{id:card.dataset.id,ssid:inputs[0].value.trim(),password_action:password?"replace":configured?"keep":"open",password:password||null}});
}
function collectSensors(){const sensors=clone(data.sensors),used=new Set();document.querySelectorAll("#setup-sensors select").forEach(select=>{const value=select.value||null;if(value&&used.has(value))throw new Error(L.t("duplicateSensor"));if(value)used.add(value);sensors.assignments[select.dataset.role]=value});return sensors}
function collectHeater(){const mode=L.$("setup-mode").value;return{maximum_runtime_minutes:Number(L.$("setup-maximum").value),quick_start:{mode,target_temperature:mode==="power"?null:Number(L.$("setup-target").value),power_level:mode==="power"?Number(L.$("setup-power").value):null,runtime_minutes:Number(L.$("setup-runtime").value)}}}
function renderSummary(){
  let sensors;try{sensors=collectSensors()}catch(error){sensors=data.sensors}
  const networks=collectNetworks(),apChanged=Boolean(L.$("setup-ap-password").value);rows(L.$("setup-summary"),[[L.t("language"),L.state.language==="de"?"Deutsch":"English"],[L.t("timezone"),data.time?.timezone_name||"–"],[L.t("knownNetworks"),String(networks.length)],[L.t("apPassword"),apChanged?L.t("willChange"):L.t("willKeep")],[L.t("roofTent"),sensors.assignments.roof_tent||"–"],[L.t("cabin"),sensors.assignments.cabin||"–"],[L.t("outside"),sensors.assignments.outside||"–"],[L.t("sensorCheck"),data.checks?.sensors?.active_probe_performed?L.t("reviewed"):L.t("deferred")],[L.t("autotermTest"),data.checks?.autoterm?.active_test_performed?L.t("reviewed"):L.t("deferred")],[L.t("quickMode"),L.mode(L.$("setup-mode").value)],[L.t("defaultRuntime"),L.minutes(Number(L.$("setup-runtime").value))]]);
}
async function finish(event){
  event.preventDefault();if(!L.$("setup-confirm").checked){L.toast(L.t("confirmationRequired"),true);return}
  let sensors;try{sensors=collectSensors()}catch(error){L.toast(error.message,true);return}
  const password=L.$("setup-ap-password").value,configured=L.$("setup-ap-password").dataset.configured==="1";
  const payload={heater:collectHeater(),sensors,time:clone(data.time),network:{access_point:{password_action:password?"replace":configured?"keep":"replace",password:password||null},known_networks:collectNetworks()},checks:{sensors:data.checks?.sensors?.active_probe_performed?"reviewed":"deferred",autoterm:data.checks?.autoterm?.active_test_performed?"reviewed":"deferred"}};
  try{const result=await L.mutate("/api/v1/setup","PUT",payload);L.state.settings=result;L.$("setup-dialog").close();L.modules.settings?.render(result);L.toast(L.t(result.restart_required?"savedRestartRequired":"setupCompleted"));await L.loadStatus().catch(()=>L.setConnection(false))}catch(error){L.toast(error.message||L.t("requestFailed"),true)}
}
async function open(isRequired=false){
  if(opening)return;opening=true;required=isRequired;
  try{data=await L.request("/api/v1/setup");fill();setStep(0);L.$("setup-cancel").classList.toggle("hidden",required);if(!L.$("setup-dialog").open)L.$("setup-dialog").showModal()}catch(error){L.toast(error.message||L.t("requestFailed"),true)}finally{opening=false}
}
function autoOpen(){if(L.state.settings?.system?.setup_complete===false)open(true)}
function bind(){
  L.$("restart-setup").addEventListener("click",()=>open(false));L.$("setup-language").addEventListener("change",event=>L.applyLanguage(event.target.value));L.$("setup-mode").addEventListener("change",syncMode);L.$("setup-add-network").addEventListener("click",()=>{if(L.$("setup-networks").children.length>=8){L.toast(L.t("networkLimit"),true);return}L.$("setup-networks").append(configuredNetwork({}))});L.$("setup-back").addEventListener("click",()=>setStep(step-1));L.$("setup-next").addEventListener("click",()=>{if(validatePage())setStep(step+1)});L.$("setup-cancel").addEventListener("click",()=>L.$("setup-dialog").close());L.$("setup-form").addEventListener("submit",finish);L.$("setup-dialog").addEventListener("cancel",event=>{if(required)event.preventDefault()})
}
L.modules.setup={bind,autoOpen,open};
})();
