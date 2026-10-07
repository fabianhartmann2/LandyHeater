"use strict";
(function(){
function append(tag,configure){return new Promise((resolve,reject)=>{const node=document.createElement(tag);configure(node);node.onload=()=>resolve(node);node.onerror=()=>{node.remove();reject(new Error("asset_load_failed"))};document.head.append(node)})}
async function load(tag,configure){let failure;for(let attempt=0;attempt<3;attempt++){try{return await append(tag,configure)}catch(error){failure=error;if(attempt<2)await new Promise(resolve=>setTimeout(resolve,250*(attempt+1)))}}throw failure}
async function start(){await load("link",node=>{node.rel="stylesheet";node.href="/assets/ui.css"});await load("script",node=>{node.src="/assets/i18n.js"});await load("script",node=>{node.src="/assets/app.js"})}
start().catch(()=>{const status=document.getElementById("connection");if(status){status.classList.add("offline");const label=status.querySelector("span");if(label)label.textContent="Neu laden"}});
})();
