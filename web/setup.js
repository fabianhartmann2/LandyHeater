"use strict";
(function(){
const L=window.Landy,roles=["roof_tent","cabin","outside"],modes=["roof_tent_temperature","cabin_temperature","power"];
let data=null,step=0,required=false,opening=false;
const clone=value=>JSON.parse(JSON.stringify(value));
const bytes=value=>new TextEncoder().encode(value).length;
const integer=value=>value!==""&&Number.isInteger(Number(value))?Number(value):null;
function passwordError(value,station=false){
  if(typeof value!=="string")return "passwordLengthExact";
  if(station&&value.length===64&&/^[0-9a-fA-F]{64}$/.test(value))return null;
  if(value.length<8||value.length>63)return "passwordLengthExact";
  for(let i=0;i<value.length;i++){const code=value.charCodeAt(i);if(code<32||code>126)return "passwordCharacters"}
  return null;
}
function ssidError(value){const normalized=typeof value==="string"?value.trim():"";if(!normalized)return "ssidRequired";if(normalized.includes("\0")||bytes(normalized)>32)return "ssidLength";return null}
function networksError(rows){
  if(!Array.isArray(rows)||rows.length>8)return "networkLimit";
  const seen=new Set();
  for(const row of rows){const error=ssidError(row.ssid);if(error)return error;const ssid=row.ssid.trim();if(seen.has(ssid))return "duplicateNetwork";seen.add(ssid);if(row.action==="keep"&&!row.configured)return "credentialActionInvalid";if(row.action==="replace"){const password=passwordError(row.password,true);if(password)return password}else if(row.action!=="keep"&&row.action!=="open")return "credentialActionInvalid"}
  return null;
}
function apError(value){if(value.action==="keep")return value.configured?null:"credentialActionInvalid";if(value.action!=="replace")return "credentialActionInvalid";const error=passwordError(value.password);if(error)return error;return value.password===value.repeat?null:"passwordMismatch"}
function quickError(value){
  if(!modes.includes(value.mode))return "quickModeInvalid";
  const maximum=integer(value.maximum),runtime=integer(value.runtime),target=integer(value.target),power=integer(value.power);
  if(maximum===null||maximum<1||maximum>120)return "maximumRange";
  if(runtime===null||runtime<1||runtime>maximum)return "runtimeRange";
  if(value.mode==="power")return power!==null&&power>=1&&power<=9?null:"powerRange";
  return target!==null&&target>=5&&target<=30?null:"targetRange";
}
const validation=Object.freeze({passwordError,ssidError,networksError,apError,quickError});
function rows(node,values){L.definition(node,values)}
function translatedOption(value,key){const option=new Option(L.t(key),value);option.dataset.i18n=key;return option}
function labelled(key,node){const label=L.el("label"),span=L.el("span",null,L.t(key));span.dataset.i18n=key;label.append(span,node);return label}
function setStep(next){
  step=Math.max(0,Math.min(8,next));
  document.querySelectorAll("[data-setup-step]").forEach(node=>node.classList.toggle("hidden",Number(node.dataset.setupStep)!==step));
  L.text(L.$("setup-progress"),`${step+1} / 9`);L.$("setup-progress-bar").value=step+1;
  L.$("setup-back").disabled=step===0;L.$("setup-next").classList.toggle("hidden",step===8);L.$("setup-finish").classList.toggle("hidden",step!==8);
  if(step===7)renderSummary();
}
function syncNetworkAction(card){const replace=card.querySelector(".setup-network-action").value==="replace",password=card.querySelector(".setup-network-password");password.disabled=!replace;if(!replace)password.value=""}
function configuredNetwork(profile){
  const card=L.el("div","setup-network");card.dataset.id=profile.id||`wifi-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`;card.dataset.configured=profile.password_configured?"1":"0";
  const ssid=document.createElement("input");ssid.className="setup-network-ssid";ssid.maxLength=32;ssid.required=true;ssid.value=profile.ssid||"";ssid.placeholder="SSID";
  const action=document.createElement("select");action.className="setup-network-action";if(profile.password_configured)action.append(translatedOption("keep","keepExisting"));action.append(translatedOption("replace","wpa2Password"),translatedOption("open","openNetwork"));action.value=profile.password_configured?"keep":profile.ssid?"open":"replace";
  const password=document.createElement("input");password.className="setup-network-password";password.type="password";password.minLength=8;password.maxLength=64;password.autocomplete="new-password";password.placeholder=L.t("passwordRequiredHint");
  const remove=L.el("button","button secondary compact","×");remove.type="button";remove.setAttribute("aria-label",L.t("delete"));remove.addEventListener("click",()=>card.remove());
  action.addEventListener("change",()=>syncNetworkAction(card));card.append(labelled("networkName",ssid),labelled("wirelessSecurity",action),labelled("password",password),remove);syncNetworkAction(card);return card;
}
function renderNetworks(){const list=L.$("setup-networks");list.replaceChildren();(data.network?.known_networks||[]).forEach(profile=>list.append(configuredNetwork(profile)))}
function networkRows(){return Array.from(L.$("setup-networks").children,card=>({card,ssid:card.querySelector(".setup-network-ssid").value,action:card.querySelector(".setup-network-action").value,password:card.querySelector(".setup-network-password").value,configured:card.dataset.configured==="1"}))}
function sensorOptions(select,current){select.append(new Option("–",""));const found=new Set((data.checks?.sensors?.discovered||[]).map(item=>item.rom_id));if(current)found.add(current);found.forEach(rom=>select.append(new Option(rom,rom)));select.value=current||""}
function renderSensors(){const state=data.checks?.sensors||{},found=state.discovered||[];L.text(L.$("setup-sensor-state"),found.length?`${found.length} ${L.t("sensorsObserved")}`:L.t("sensorCheckDeferred"));const container=L.$("setup-sensors");container.replaceChildren();roles.forEach(role=>{const select=document.createElement("select");select.dataset.role=role;sensorOptions(select,data.sensors?.assignments?.[role]);container.append(labelled({roof_tent:"roofTent",cabin:"cabin",outside:"outside"}[role],select))})}
function syncApAction(){const replace=L.$("setup-ap-action").value==="replace";L.$("setup-ap-password").disabled=!replace;L.$("setup-ap-repeat").disabled=!replace;if(!replace){L.$("setup-ap-password").value="";L.$("setup-ap-repeat").value=""}}
function fill(){
  L.$("setup-language").value=L.state.language;
  const clock=data.checks?.rtc||{};rows(L.$("setup-rtc"),[[L.t("clock"),clock.valid?L.t("valid"):L.t("invalid")],["RTC",clock.rtc_health||"–"],[L.t("source"),clock.source||"–"],[L.t("localTime"),clock.local||"–"],[L.t("timezone"),data.time?.timezone_name||"–"]]);
  renderNetworks();const ap=data.network?.access_point||{},keep=L.$("setup-ap-action").querySelector('option[value="keep"]');keep.disabled=!ap.password_configured;keep.hidden=!ap.password_configured;L.$("setup-ap-action").value=ap.password_configured?"keep":"replace";L.$("setup-ap-password").value="";L.$("setup-ap-repeat").value="";L.text(L.$("setup-ap-hint"),ap.password_configured?L.t("passwordChoiceHint"):L.t("passwordRequiredHint"));L.$("setup-ap-password").dataset.configured=ap.password_configured?"1":"0";syncApAction();
  renderSensors();const autoterm=data.checks?.autoterm||{};rows(L.$("setup-autoterm"),[[L.t("status"),autoterm.state||"not_run"],[L.t("communication"),autoterm.communication||"–"],[L.t("initialized"),L.boolean(autoterm.initialized)],[L.t("synchronized"),L.boolean(autoterm.synchronized)],[L.t("activeTest"),L.t(autoterm.active_test_performed?"yes":"no")]]);
  const heater=data.heater||{},quick=heater.quick_start||{};L.$("setup-mode").value=quick.mode||"roof_tent_temperature";L.$("setup-target").value=quick.target_temperature??20;L.$("setup-power").value=quick.power_level??5;L.$("setup-runtime").value=quick.runtime_minutes??60;L.$("setup-maximum").value=heater.maximum_runtime_minutes??120;syncMode();L.$("setup-confirm").checked=false;
}
function syncMode(){const power=L.$("setup-mode").value==="power";L.$("setup-target").disabled=power;L.$("setup-power").disabled=!power}
function showError(key){L.toast(L.t(key),true);return false}
function validatePage(index=step){
  if(index===2){const error=validation.networksError(networkRows());if(error)return showError(error)}
  if(index===3){const error=validation.apError({action:L.$("setup-ap-action").value,password:L.$("setup-ap-password").value,repeat:L.$("setup-ap-repeat").value,configured:L.$("setup-ap-password").dataset.configured==="1"});if(error)return showError(error)}
  if(index===4){try{collectSensors()}catch(error){return showError("duplicateSensor")}}
  if(index===6){const error=validation.quickError(quickValues());if(error)return showError(error)}
  if(index===8&&!L.$("setup-confirm").checked)return showError("confirmationRequired");
  return true;
}
function validateAll(){for(const index of [2,3,4,6,8])if(!validatePage(index))return false;return true}
function collectNetworks(){return networkRows().map(row=>({id:row.card.dataset.id,ssid:row.ssid.trim(),password_action:row.action,password:row.action==="replace"?row.password:null}))}
function collectSensors(){const sensors=clone(data.sensors),used=new Set();document.querySelectorAll("#setup-sensors select").forEach(select=>{const value=select.value||null;if(value&&used.has(value))throw new Error("duplicateSensor");if(value)used.add(value);sensors.assignments[select.dataset.role]=value});return sensors}
function quickValues(){return{mode:L.$("setup-mode").value,target:L.$("setup-target").value,power:L.$("setup-power").value,runtime:L.$("setup-runtime").value,maximum:L.$("setup-maximum").value}}
function collectHeater(){const value=quickValues();return{maximum_runtime_minutes:Number(value.maximum),quick_start:{mode:value.mode,target_temperature:value.mode==="power"?null:Number(value.target),power_level:value.mode==="power"?Number(value.power):null,runtime_minutes:Number(value.runtime)}}}
function renderSummary(){let sensors;try{sensors=collectSensors()}catch(error){sensors=data.sensors}const networks=networkRows(),apAction=L.$("setup-ap-action").value,networkSummary=networks.length?networks.map(row=>`${row.ssid.trim()} · ${L.t(row.action==="open"?"openNetwork":"wpa2Password")}`).join(", "):L.t("noData");rows(L.$("setup-summary"),[[L.t("language"),L.state.language==="de"?"Deutsch":"English"],[L.t("timezone"),data.time?.timezone_name||"–"],[L.t("knownNetworks"),networkSummary],[L.t("apPassword"),L.t(apAction==="replace"?"willChange":"willKeep")],[L.t("roofTent"),sensors.assignments.roof_tent||"–"],[L.t("cabin"),sensors.assignments.cabin||"–"],[L.t("outside"),sensors.assignments.outside||"–"],[L.t("sensorCheck"),data.checks?.sensors?.active_probe_performed?L.t("reviewed"):L.t("deferred")],[L.t("autotermTest"),data.checks?.autoterm?.active_test_performed?L.t("reviewed"):L.t("deferred")],[L.t("quickMode"),L.mode(L.$("setup-mode").value)],[L.t("defaultRuntime"),L.minutes(Number(L.$("setup-runtime").value))]])}
async function finish(event){
  event.preventDefault();if(!validateAll())return;const sensors=collectSensors(),apAction=L.$("setup-ap-action").value,password=L.$("setup-ap-password").value;
  const payload={heater:collectHeater(),sensors,time:clone(data.time),network:{access_point:{password_action:apAction,password:apAction==="replace"?password:null},known_networks:collectNetworks()},checks:{sensors:data.checks?.sensors?.active_probe_performed?"reviewed":"deferred",autoterm:data.checks?.autoterm?.active_test_performed?"reviewed":"deferred"}};
  try{const result=await L.mutate("/api/v1/setup","PUT",payload);L.state.settings=result;L.$("setup-dialog").close();L.modules.settings?.render(result);L.toast(L.t(result.restart_required?"savedRestartRequired":"setupCompleted"));await L.loadStatus().catch(()=>L.setConnection(false))}catch(error){L.toast(error.message||L.t("requestFailed"),true)}
}
async function open(isRequired=false){if(opening)return;opening=true;required=isRequired;try{data=await L.request("/api/v1/setup");fill();setStep(0);L.$("setup-cancel").classList.toggle("hidden",required);if(!L.$("setup-dialog").open)L.$("setup-dialog").showModal()}catch(error){L.toast(error.message||L.t("requestFailed"),true)}finally{opening=false}}
function autoOpen(){if(L.state.settings?.system?.setup_complete===false)open(true)}
function bind(){L.$("restart-setup").addEventListener("click",()=>open(false));L.$("setup-language").addEventListener("change",event=>L.applyLanguage(event.target.value));L.$("setup-mode").addEventListener("change",syncMode);L.$("setup-ap-action").addEventListener("change",syncApAction);L.$("setup-add-network").addEventListener("click",()=>{if(L.$("setup-networks").children.length>=8){L.toast(L.t("networkLimit"),true);return}L.$("setup-networks").append(configuredNetwork({}))});L.$("setup-back").addEventListener("click",()=>setStep(step-1));L.$("setup-next").addEventListener("click",()=>{if(validatePage())setStep(step+1)});L.$("setup-cancel").addEventListener("click",()=>L.$("setup-dialog").close());L.$("setup-form").addEventListener("submit",finish);L.$("setup-dialog").addEventListener("cancel",event=>{if(required)event.preventDefault()})}
L.modules.setup={bind,autoOpen,open,validation};
})();
