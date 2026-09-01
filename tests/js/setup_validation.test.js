"use strict";
const assert=require("assert");
const fs=require("fs");
const path=require("path");
const vm=require("vm");

global.window={Landy:{modules:{}}};
vm.runInThisContext(fs.readFileSync(path.join(__dirname,"../../web/setup.js"),"utf8"));
const value=window.Landy.modules.setup.validation;

assert.strictEqual(value.passwordError("1234567"),"passwordLengthExact");
assert.strictEqual(value.passwordError("12345678"),null);
assert.strictEqual(value.passwordError("Passwortä"),"passwordCharacters");
assert.strictEqual(value.passwordError("a".repeat(64),true),null);
assert.strictEqual(value.passwordError("z".repeat(64),true),"passwordLengthExact");

assert.strictEqual(value.ssidError(""),"ssidRequired");
assert.strictEqual(value.ssidError("ä".repeat(17)),"ssidLength");
assert.strictEqual(value.ssidError("Camp WiFi"),null);

assert.strictEqual(value.networksError([{ssid:"Camp",action:"replace",password:"",configured:false}]),"passwordLengthExact");
assert.strictEqual(value.networksError([{ssid:"Camp",action:"open",password:"",configured:false}]),null);
assert.strictEqual(value.networksError([{ssid:"Camp",action:"keep",password:"",configured:false}]),"credentialActionInvalid");
assert.strictEqual(value.networksError([{ssid:"Camp",action:"replace",password:"Station92",configured:false},{ssid:"Camp",action:"open",password:"",configured:false}]),"duplicateNetwork");
assert.strictEqual(value.networksError([{ssid:"Camp",action:"replace",password:"Station92",configured:false}]),null);

assert.strictEqual(value.apError({action:"keep",configured:true,password:"",repeat:""}),null);
assert.strictEqual(value.apError({action:"keep",configured:false,password:"",repeat:""}),"credentialActionInvalid");
assert.strictEqual(value.apError({action:"replace",configured:true,password:"NewSecret92",repeat:"different"}),"passwordMismatch");
assert.strictEqual(value.apError({action:"replace",configured:true,password:"NewSecret92",repeat:"NewSecret92"}),null);

assert.strictEqual(value.quickError({mode:"power",power:"5",target:"",runtime:"60",maximum:"120"}),null);
assert.strictEqual(value.quickError({mode:"power",power:"0",target:"",runtime:"60",maximum:"120"}),"powerRange");
assert.strictEqual(value.quickError({mode:"roof_tent_temperature",power:"",target:"20",runtime:"60",maximum:"120"}),null);
assert.strictEqual(value.quickError({mode:"roof_tent_temperature",power:"",target:"31",runtime:"60",maximum:"120"}),"targetRange");
assert.strictEqual(value.quickError({mode:"roof_tent_temperature",power:"",target:"20",runtime:"121",maximum:"120"}),"runtimeRange");
assert.strictEqual(value.quickError({mode:"roof_tent_temperature",power:"",target:"20",runtime:"60",maximum:"0"}),"maximumRange");

console.log("PHASE10_SETUP_VALIDATION_TEST_PASS_V1");
