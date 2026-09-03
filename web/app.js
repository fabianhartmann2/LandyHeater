"use strict";
(function(){
const L=window.Landy={state:{language:"de",csrf:null,etag:null,status:null,settings:null,timers:null,online:false,busy:false},modules:{}};
L.$=id=>document.getElementById(id);
L.t=key=>(window.LANDY_I18N[L.state.language]||window.LANDY_I18N.de)[key]||key;
L.text=(node,value)=>{node.textContent=value==null?"–":String(value)};
L.el=(tag,className,text)=>{const n=document.createElement(tag);if(className)n.className=className;if(text!=null)n.textContent=text;return n};
L.mode=value=>({roof_tent_temperature:L.t("roofTent"),cabin_temperature:L.t("cabin"),power:L.t("power")})[value]||value||"–";
L.health=value=>({ok:L.t("healthy"),stale:L.t("stale"),failed:L.t("failed"),missing:L.t("missing")})[value]||L.t("unknown");
L.minutes=value=>value==null?"–":`${value} ${L.t("minutes")}`;
L.temperature=value=>typeof value==="number"?`${value.toFixed(1)} °C`:"–";
L.boolean=value=>value?L.t("yes"):L.t("no");
L.timerPath=id=>"/api/v1/timers/~id/"+Array.from(new TextEncoder().encode(id),b=>b.toString(16).padStart(2,"0")).join("");
L.toast=(message,error=false)=>{const n=L.$("toast");n.textContent=message;n.dataset.error=error?"1":"0";n.classList.add("show");clearTimeout(L.toast.timer);L.toast.timer=setTimeout(()=>n.classList.remove("show"),2800)};
L.setConnection=online=>{L.state.online=online;const n=L.$("connection");n.classList.toggle("online",online);n.classList.toggle("offline",!online);n.querySelector("span").textContent=L.t(online?"online":"offline")};
L.request=async(path,options={})=>{let response;try{response=await fetch(path,{cache:"no-store",...options})}catch(error){L.setConnection(false);throw error}let data;try{data=await response.json()}catch(error){L.setConnection(false);throw error}L.setConnection(true);const etag=response.headers.get("ETag");if(etag)L.state.etag=etag;if(!response.ok){const failure=new Error(data.error?.message||L.t("requestFailed"));failure.code=data.error?.code;failure.status=response.status;failure.data=data;throw failure}return data};
L.security=async()=>{const data=await L.request("/api/v1/security-context");L.state.csrf=data.csrf_token;return data};
L.mutate=async(path,method,payload,useEtag=true)=>{if(!L.state.csrf)await L.security();const headers={"X-Landy-CSRF":L.state.csrf},options={method,headers};if(payload!==undefined){headers["Content-Type"]="application/json";options.body=JSON.stringify(payload)}if(useEtag){if(!L.state.etag)await L.loadSettings();headers["If-Match"]=L.state.etag}try{return await L.request(path,options)}catch(error){if(error.status===403||error.status===503){L.state.csrf=null}throw error}};
L.loadStatus=async()=>{const data=await L.request("/api/v1/status");L.state.status=data;L.modules.home?.render(data);L.modules.settings?.renderStatus(data);L.text(L.$("last-update"),`${L.t("updated")} ${new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}`);return data};
L.loadSettings=async()=>{const data=await L.request("/api/v1/settings");L.state.settings=data;L.modules.settings?.render(data);return data};
L.loadTimers=async(offset=0)=>{const data=await L.request(`/api/v1/timers?offset=${offset}&limit=8`);L.state.timers=data;L.modules.timers?.render(data);return data};
L.refresh=async()=>{if(L.state.busy)return;L.state.busy=true;try{await Promise.all([L.loadStatus(),L.loadSettings(),L.loadTimers(L.state.timers?.offset||0)])}catch(error){L.toast(error.message||L.t("requestFailed"),true)}finally{L.state.busy=false}};
L.applyLanguage=language=>{L.state.language=language==="en"?"en":"de";document.documentElement.lang=L.state.language;localStorage.setItem("landy-language",L.state.language);document.querySelectorAll("[data-i18n]").forEach(n=>{n.textContent=L.t(n.dataset.i18n)});document.querySelectorAll("[data-i18n-aria]").forEach(n=>n.setAttribute("aria-label",L.t(n.dataset.i18nAria)));if(L.$("language"))L.$("language").value=L.state.language;L.modules.home?.render(L.state.status);L.modules.timers?.render(L.state.timers);L.modules.settings?.render(L.state.settings);L.modules.settings?.renderStatus(L.state.status);L.setConnection(L.state.online)};
L.showView=name=>{document.querySelectorAll(".view").forEach(n=>n.classList.toggle("active",n.id===`view-${name}`));document.querySelectorAll("[data-view]").forEach(n=>n.classList.toggle("active",n.dataset.view===name));history.replaceState(null,"",`#${name}`);window.scrollTo({top:0});L.$(`view-${name}`)?.focus?.()};
L.definition=(node,rows)=>{node.replaceChildren();for(const [name,value] of rows){node.append(L.el("dt",null,name),L.el("dd",null,value??"–"))}};
async function boot(){const stored=localStorage.getItem("landy-language");L.applyLanguage(stored||"de");document.querySelectorAll("[data-view]").forEach(n=>n.addEventListener("click",()=>L.showView(n.dataset.view)));document.querySelectorAll("[data-view-target]").forEach(n=>n.addEventListener("click",()=>L.showView(n.dataset.viewTarget)));L.$("refresh").addEventListener("click",L.refresh);L.modules.home?.bind();L.modules.timers?.bind();L.modules.settings?.bind();L.modules.setup?.bind();L.showView(["home","timers","settings"].includes(location.hash.slice(1))?location.hash.slice(1):"home");try{await L.refresh();L.modules.setup?.autoOpen()}catch(error){L.setConnection(false);L.toast(error.message||L.t("requestFailed"),true)}setInterval(()=>{if(!document.hidden)L.loadStatus().catch(()=>L.setConnection(false))},5000)}
window.addEventListener("DOMContentLoaded",boot);
})();
