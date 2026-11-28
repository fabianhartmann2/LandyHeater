"use strict";
(function(){
const L=window.Landy;
Object.assign(window.LANDY_I18N.de,{adjustPower:"Leistung ändern",applyPower:"Übernehmen",powerSetting:"Sollleistung",powerUpdateSending:"Leistungsänderung wird angefragt…",powerUpdatePending:"Warte auf Rückmeldung der Heizung.",powerUpdateApplied:"Leistungsstufe von der Heizung bestätigt.",powerUpdateFailed:"Leistungsstufe konnte nicht bestätigt werden.",powerUpdateUnavailable:"Die Leistung lässt sich erst bei stabil laufender Heizung ändern."});
Object.assign(window.LANDY_I18N.en,{adjustPower:"Adjust power",applyPower:"Apply",powerSetting:"Power setting",powerUpdateSending:"Requesting power change…",powerUpdatePending:"Waiting for confirmation from the heater.",powerUpdateApplied:"Power setting confirmed by the heater.",powerUpdateFailed:"The power setting could not be confirmed.",powerUpdateUnavailable:"Power can only be adjusted while the heater is running steadily."});
L.$("adjust-target").insertAdjacentHTML("afterend",'<button id="adjust-power" class="button secondary hidden" type="button" aria-controls="power-adjust-panel" aria-expanded="false" data-i18n="adjustPower"></button>');
L.$("stop-heater").insertAdjacentHTML("beforebegin",'<div id="power-adjust-panel" class="inline-adjust hidden"><output id="adjust-power-output" for="adjust-power-range"></output><input id="adjust-power-range" type="range" min="1" max="9" step="1" value="5" data-i18n-aria="powerLevel"><button id="apply-power" class="button primary compact" type="button" data-i18n="applyPower"></button></div><p id="power-update-status" class="muted hidden" role="status"></p>');
L.text(L.$("adjust-power"),L.t("adjustPower"));
L.text(L.$("apply-power"),L.t("applyPower"));
L.$("adjust-power-range").setAttribute("aria-label",L.t("powerLevel"));
let startDialogLoad=null;
let powerDraft=null,powerSaving=false;
const startMessages={de:{restart:"Neustart erforderlich. Starte das Steuergerät neu, bevor du die Heizung startest.",unreachable:"Heizung nicht erreichbar. Prüfe Stromversorgung und Verbindung.",notReady:"Heizung noch nicht startbereit. Bitte kurz warten und erneut versuchen.",blocked:"Die Heizung kann in ihrem aktuellen Zustand nicht gestartet werden."},en:{restart:"Restart required. Restart the controller before starting the heater.",unreachable:"Heater is not reachable. Check its power supply and connection.",notReady:"Heater is not ready to start yet. Wait briefly and try again.",blocked:"The heater cannot start in its current state."}};
const startMessage=key=>(startMessages[L.state.language]||startMessages.de)[key];
const stateLabel=value=>L.t(value||"unknown");
const targetLabel=requested=>requested?.mode==="power"?`${requested.power_level??"–"} / 9`:requested?.target_temperature==null?"–":`${requested.target_temperature} °C`;
const validPower=value=>Number.isInteger(value)&&value>=1&&value<=9;
const powerLabel=value=>`${value} / 9`;
function powerContext(status){
  const heater=status?.heater||{},session=heater.session,actual=heater.actual||{},requested=heater.requested||{};
  const pending=heater.power_update?.state==="pending";
  const canAdjust=Object.hasOwn(heater,"power_update")&&Number.isInteger(heater.request_revision)&&requested.on===true&&requested.mode==="power"&&session?.mode==="power"&&session.confirmed_active===true&&session.expired===false&&validPower(session.target)&&actual.heater_state==="running"&&actual.communication==="ok"&&actual.initialized===true&&actual.synchronized===true&&!heater.control_transition_pending&&!heater.control_faulted&&!heater.restart_blocked&&!status.configuration?.restart_required;
  return {heater,session,pending,canAdjust};
}
function renderPower(status){
  const {heater,session,pending,canAdjust}=powerContext(status);
  const button=L.$("adjust-power"),panel=L.$("power-adjust-panel"),notice=L.$("power-update-status");
  if(!canAdjust||pending||!powerDraft||powerDraft.sessionId!==session.id||powerDraft.revision!==heater.request_revision)powerDraft=null;
  button.classList.toggle("hidden",!canAdjust);
  button.disabled=powerSaving||pending;
  button.setAttribute("aria-expanded",String(Boolean(powerDraft)));
  panel.classList.toggle("hidden",!powerDraft);
  const update=heater.power_update;
  const stateKey={pending:"powerUpdatePending",applied:"powerUpdateApplied",failed:"powerUpdateFailed"}[update?.state];
  const showState=session?.mode==="power"&&stateKey&&validPower(update.power_level);
  const nextNotice=powerSaving&&!pending?L.t("powerUpdateSending"):showState?`${powerLabel(update.power_level)} · ${L.t(stateKey)}`:"";
  if(notice.textContent!==nextNotice)L.text(notice,nextNotice);
  notice.classList.toggle("hidden",!nextNotice);
  L.$("extend-session").disabled=powerSaving||pending;
  renderPowerDraft();
}
function renderPowerDraft(){
  const range=L.$("adjust-power-range"),value=Number(range.value);
  L.text(L.$("adjust-power-output"),powerLabel(value));
  range.setAttribute("aria-valuetext",powerLabel(value));
  L.$("apply-power").disabled=powerSaving||!powerDraft||!validPower(value)||(value===powerDraft.level&&!powerDraft.allowSame);
}
function togglePower(){
  const {heater,session,pending,canAdjust}=powerContext(L.state.status);
  if(!canAdjust||pending||powerSaving)return;
  powerDraft=powerDraft?null:{sessionId:session.id,revision:heater.request_revision,level:session.target,allowSame:heater.power_update?.state==="failed"};
  if(powerDraft)L.$("adjust-power-range").value=session.target;
  renderPower(L.state.status);
  if(powerDraft)L.$("adjust-power-range").focus();
}
async function applyPower(){
  const submittedStatus=L.state.status,{heater,session,pending,canAdjust}=powerContext(submittedStatus);
  const draft=powerDraft,value=Number(L.$("adjust-power-range").value);
  if(L.state.busy||powerSaving||pending||!canAdjust||!draft||draft.sessionId!==session.id||draft.revision!==heater.request_revision||!validPower(value)||(value===draft.level&&!draft.allowSame))return;
  powerDraft=null;
  powerSaving=true;
  renderPower(L.state.status);
  try{
    const result=await L.controlMutate("/api/v1/heater/session","PATCH",{expected_request_revision:draft.revision,target_temperature:null,extend_minutes:0,power_level:value});
    const status=L.state.status,current=status?.heater,reported=result.heater;
    if(status===submittedStatus&&result.updated===true&&reported?.power_update?.state==="pending"){
      L.state.status={...status,heater:{...current,power_update:reported.power_update,request_revision:reported.request_revision}};
      renderPower(L.state.status);
    }
  }catch(error){
    L.toast(error.code==="heater_session_conflict"?L.t("powerUpdateUnavailable"):error.message||L.t("requestFailed"),true);
  }
  try{await L.loadStatus()}catch(error){L.setConnection(false)}finally{
    powerSaving=false;
    renderPower(L.state.status);
    if(canAdjust&&document.activeElement===L.$("apply-power")&&!L.$("adjust-power").disabled)L.$("adjust-power").focus();
  }
}
function renderTemperature(role,key){const item=L.state.status?.temperatures?.[role]||{};L.text(L.$(`temp-${key}`),L.temperature(item.value_c));L.text(L.$(`health-${key}`),L.health(item.health))}
function renderNextTimer(value){const node=L.$("next-timer");node.replaceChildren();if(!value){node.className="empty";node.textContent=L.t("noTimer");return}node.className="";const title=L.el("strong",null,value.start||"–");const detail=L.el("p","muted",`${value.timer_id||""} · ${value.minutes_from_now??"–"} ${L.t("minutes")}`);node.append(title,detail)}
function renderWarnings({warnings,warning_details:details}){
  const list=L.$("warnings"),de=L.state.language==="de",locale=de?"de-CH":"en-GB";
  list.replaceChildren();warnings=Array.isArray(warnings)?warnings:[];
  L.text(L.$("warning-count"),warnings.length);
  if(!warnings.length){list.append(L.el("li","empty",L.t("noWarnings")));return}
  for(const code of warnings){
    const label=code==="runtime_restart_required"?(de?"Neustart erforderlich. Die Sensoren messen bis dahin mit den bisherigen Einstellungen weiter.":"Restart required. Until then, sensors keep using the previous settings."):String(code).replaceAll("_"," ");
    const row=L.el("li",null,label);
    const {since_unix:since,age_seconds:age}=details?.[code]||{};
    const date=new Date(Number.isInteger(since)&&since>=0?since*1000:NaN);
    let stamp;
    if(Number.isFinite(+date)){
      stamp=L.el("time","muted",(de?"Seit ":"Since ")+date.toLocaleString(locale));
      stamp.setAttribute("datetime",date.toISOString());
    }else if(Number.isInteger(age)&&age>=0){
      const [unit,seconds]=age>=86400?["day",86400]:age>=3600?["hour",3600]:age>=60?["minute",60]:["second",1];
      const text=age?new Intl.RelativeTimeFormat(locale).format(-Math.floor(age/seconds),unit):de?"Gerade eben":"Just now";
      stamp=L.el("small","muted",text);
    }
    if(stamp)row.append(L.el("br"),stamp);
    list.append(row);
  }
}
function render(status){if(!status)return;const heater=status.heater||{},actual=heater.actual||{},requested=heater.requested||{},session=heater.session;const actualState=actual.heater_state||"unknown";const active=Boolean(requested.on||session||(actualState!=="off"&&actualState!=="unknown"));const badge=L.$("heater-badge");badge.className=`badge ${actualState==="off"?"neutral":actualState==="running"?"ok":actualState==="unknown"?"danger":"warn"}`;badge.textContent=stateLabel(actualState);L.text(L.$("heater-state"),stateLabel(actualState));L.text(L.$("heater-detail"),heater.control_faulted?L.t("failed"):actual.communication==="error"?L.t("offline"):requested.on?L.mode(requested.mode):L.t("off"));L.text(L.$("remaining"),heater.remaining_seconds==null?"–":L.minutes(Math.ceil(heater.remaining_seconds/60)));L.$("off-actions").classList.toggle("hidden",active);L.$("running-actions").classList.toggle("hidden",!active);L.text(L.$("active-mode"),L.mode(session?.mode||requested.mode));L.text(L.$("active-target"),session?.target!=null?(session.mode==="power"?powerLabel(session.target):`${session.target} °C`):targetLabel(requested));L.text(L.$("active-sensor"),heater.active_sensor?.value_c==null?"–":L.temperature(heater.active_sensor.value_c));const temperatureMode=(session?.mode||requested.mode)!=="power";L.text(L.$("target-label"),L.t(temperatureMode?"target":"powerSetting"));L.$("adjust-target").classList.toggle("hidden",!temperatureMode);L.$("target-adjust-panel").classList.add("hidden");renderPower(status);L.$("quick-start").disabled=!status.configuration?.timer_start_allowed||active;L.$("configure-start").disabled=!status.configuration?.timer_start_allowed||active;renderTemperature("roof_tent","roof");renderTemperature("cabin","cabin");renderTemperature("outside","outside");renderNextTimer(status.scheduler?.next_occurrence);renderWarnings(status)}
function startFailure(error,mode){const status=L.state.status||{},actual=status.heater?.actual||{};if(error.code==="runtime_restart_required"||(error.code==="application_unavailable"&&status.configuration?.restart_required===true))return startMessage("restart");if(error.code!=="heater_start_conflict")return error.message||L.t("requestFailed");if(actual.communication==="error")return startMessage("unreachable");if(actual.communication!=="ok"||actual.initialized!==true||actual.synchronized!==true)return startMessage("notReady");const role=mode==="roof_tent_temperature"?"roof_tent":mode==="cabin_temperature"?"cabin":null,sensor=role?status.temperatures?.[role]:null;if(role&&(!sensor||sensor.health!=="ok"||sensor.present!==true||sensor.usable!==true))return L.t("sensorNotReady");return startMessage("blocked")}
async function act(action,message,failureMessage=null){if(L.state.busy)return;L.state.busy=true;try{await action();L.toast(L.t(message));await L.loadStatus()}catch(error){L.toast(failureMessage?failureMessage(error):error.message||L.t("requestFailed"),true)}finally{L.state.busy=false}}
function prepareStartDialog(){const settings=L.state.settings?.heater||{};const quick=settings.quick_start||{};const mode=quick.mode||"roof_tent_temperature";const radio=document.querySelector(`#start-form input[name=mode][value="${mode}"]`);if(radio)radio.checked=true;L.$("start-target").value=quick.target_temperature??20;L.$("start-power").value=quick.power_level??5;const maximum=settings.maximum_runtime_minutes||120;const duration=L.$("start-duration");Array.from(duration.options).forEach(option=>option.disabled=Number(option.value)>maximum);duration.value=String(Math.min(quick.runtime_minutes||60,maximum));if(!duration.value){const option=document.createElement("option");option.value=String(maximum);option.textContent=`${maximum} min`;duration.append(option);duration.value=option.value}syncStartFields();L.$("start-dialog").showModal()}
async function openStartDialog(){if(startDialogLoad)return startDialogLoad;startDialogLoad=(async()=>{if(!L.state.settings)await L.loadSettings();prepareStartDialog()})();try{return await startDialogLoad}finally{startDialogLoad=null}}
function syncStartFields(){const mode=new FormData(L.$("start-form")).get("mode");L.$("start-target-wrap").classList.toggle("hidden",mode==="power");L.$("start-power-wrap").classList.toggle("hidden",mode!=="power");L.text(L.$("start-target-output"),`${L.$("start-target").value} °C`);L.text(L.$("start-power-output"),`${L.$("start-power").value} / 9`)}
function bind(){L.$("quick-start").addEventListener("click",()=>act(()=>L.controlMutate("/api/v1/heater/quick-start","POST",{expected_request_revision:L.state.status.heater.request_revision}),"started",error=>startFailure(error,L.state.settings?.heater?.quick_start?.mode)));L.$("configure-start").addEventListener("click",()=>openStartDialog().catch(error=>L.toast(error.message||L.t("requestFailed"),true)));document.querySelectorAll("[data-start-cancel]").forEach(n=>n.addEventListener("click",()=>L.$("start-dialog").close()));document.querySelectorAll("#start-form input[name=mode]").forEach(n=>n.addEventListener("change",syncStartFields));L.$("start-target").addEventListener("input",syncStartFields);L.$("start-power").addEventListener("input",syncStartFields);L.$("start-form").addEventListener("submit",event=>{event.preventDefault();if(event.submitter?.value==="cancel"){L.$("start-dialog").close();return}const mode=new FormData(event.currentTarget).get("mode");const body={expected_request_revision:L.state.status.heater.request_revision,mode,target_temperature:mode==="power"?null:Number(L.$("start-target").value),power_level:mode==="power"?Number(L.$("start-power").value):null,runtime_minutes:Number(L.$("start-duration").value)};L.$("start-dialog").close();act(()=>L.controlMutate("/api/v1/heater/start","POST",body),"started",error=>startFailure(error,mode))});L.$("stop-heater").addEventListener("click",()=>act(()=>L.controlMutate("/api/v1/heater/stop","POST",undefined,false),"stopped"));L.$("extend-session").addEventListener("click",()=>act(()=>L.controlMutate("/api/v1/heater/session","PATCH",{expected_request_revision:L.state.status.heater.request_revision,target_temperature:null,extend_minutes:15}),"extended"));L.$("adjust-target").addEventListener("click",()=>{const requested=L.state.status.heater.requested;L.$("adjust-target-range").value=requested.target_temperature??20;L.text(L.$("adjust-target-output"),`${L.$("adjust-target-range").value} °C`);L.$("target-adjust-panel").classList.toggle("hidden")});L.$("adjust-target-range").addEventListener("input",()=>L.text(L.$("adjust-target-output"),`${L.$("adjust-target-range").value} °C`));L.$("apply-target").addEventListener("click",()=>act(()=>L.controlMutate("/api/v1/heater/session","PATCH",{expected_request_revision:L.state.status.heater.request_revision,target_temperature:Number(L.$("adjust-target-range").value),extend_minutes:0}),"targetChanged"))}
function bindHome(){
  bind();
  L.$("adjust-power").addEventListener("click",togglePower);
  L.$("adjust-power-range").addEventListener("input",renderPowerDraft);
  L.$("apply-power").addEventListener("click",applyPower);
}
L.modules.home={render,bind:bindHome};
})();
