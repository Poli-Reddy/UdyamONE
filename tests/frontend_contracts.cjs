/* Presentation/API contract tests. These do not replace visual browser QA. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const python=process.env.UDYAM_TEST_PYTHON||path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const fixture=JSON.parse(execFileSync(python,[path.join(__dirname,'frontend_fixtures.py')],{encoding:'utf8',cwd:root}));
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const inline=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
const source=fs.readFileSync(path.join(root,'static/decision.js'),'utf8').replace(/^init\(\);\s*enhance\(\);\s*finishExperience\(\);/m,'');
const settings=fs.readFileSync(path.join(root,'static/settings.js'),'utf8');

function harness(){
 const nodes=new Map(),requests=[];
 class Element{
  constructor(id){this.id=id;this.value='';this.checked=false;this.disabled=false;this.dataset={};this.childrenIds=[];this._html='';this.textContent='';this.classList={add(){},remove(){},toggle(){},contains(){return false}}}
  set innerHTML(value){for(const id of this.childrenIds)nodes.delete(id);this.childrenIds=[];this._html=value;for(const [,id] of value.matchAll(/\bid="([^"]+)"/g)){nodes.set(id,new Element(id));this.childrenIds.push(id)}}
  get innerHTML(){return this._html}
  insertAdjacentHTML(position,value){this.innerHTML=this._html+value}
  querySelectorAll(selector){if(selector==='[data-task]')return [...this._html.matchAll(/<button\b([^>]*data-task[^>]*)>/g)].map(m=>{const e=new Element();for(const [,key,value] of m[1].matchAll(/data-([\w-]+)="([^"]*)"/g))e.dataset[key]=value;e.disabled=/\sdisabled\b/.test(m[1]);return e});return []}
  querySelector(){return null} close(){} showModal(){}
  focus(){} scrollIntoView(){} setAttribute(){} reportValidity(){return true} checkValidity(){return true}
 }
 for(const [,id] of html.matchAll(/\bid="([^"]+)"/g))nodes.set(id,new Element(id));
 for(const id of ['orderAccess','kycContext','orderRestore'])nodes.set(id,new Element(id));
 const storage={getItem(){return null},setItem(){},removeItem(){}};
 const context=vm.createContext({document:{body:{classList:{toggle(){}}},getElementById:id=>nodes.get(id)||null,querySelectorAll:()=>[]},window:{addEventListener(){},scrollTo(){}},sessionStorage:storage,localStorage:storage,crypto:{randomUUID:()=> '12345678-event-key'},setTimeout,clearTimeout,AbortController,URLSearchParams,location:{search:''},fixture:structuredClone(fixture),fetch:async(url,request)=>{requests.push({url,request});return {ok:true,status:200,json:async()=>structuredClone(fixture.order)}}});
 vm.runInContext(inline+'\n'+settings+'\n'+source,context);
 return {nodes,context,requests,run:code=>vm.runInContext(code,context)};
}

test('customer tracker keeps internal controls, IDs and unavailable payments out of the view',()=>{
 const h=harness();h.run('renderOrder(fixture.order)');const markup=h.nodes.get('trackRes').innerHTML;
 assert.match(markup,/Main clinic/);assert.match(markup,/Order received/);assert.match(markup,/Continue verification/);
 for(const text of ['data-task=','id="taskEvidence"','main:P01:','Create test checkout','Staff activation controls','Simulate verification'])assert.ok(!markup.includes(text),text);
 assert.ok(!h.nodes.has('paymentLink'));assert.ok(h.nodes.has('trackToKyc'));
});

test('staff view exposes explicit simulation controls without enabling an unconfigured checkout',()=>{
 const h=harness();h.run('staffView=true;$("testTools").checked=true;integrationInfo={demo:true,providers:{payments:{status:"not_configured"}}};renderOrder(fixture.order)');
 const markup=h.nodes.get('trackRes').innerHTML;assert.match(markup,/Staff activation controls/);assert.match(markup,/data-task=/);assert.ok(h.nodes.has('demoVerify'));assert.ok(!h.nodes.has('paymentLink'));
});

test('quote uses actual API totals, names and correctly wires acceptance',()=>{
 const h=harness();h.run('renderQuote(fixture.recommendation)');const markup=h.nodes.get('quoteCard').innerHTML;
 assert.match(markup,/Main clinic/);assert.match(markup,/₹2,011/);assert.equal(typeof h.nodes.get('acceptPlan').onclick,'function');
});

test('nothing-to-buy has no checkout action and existing services show location names',()=>{
 const h=harness();h.run('renderQuote(fixture.nothing_to_buy)');const markup=h.nodes.get('quoteCard').innerHTML;
 assert.match(markup,/already covered/);assert.match(markup,/Main clinic/);assert.ok(!h.nodes.has('acceptPlan'));
});

test('dynamic business names are escaped before being rendered',()=>{
 const h=harness();h.run('fixture.order.business_name="<img src=x onerror=alert(1)>";renderOrder(fixture.order)');
 const markup=h.nodes.get('trackRes').innerHTML;assert.ok(!markup.includes('<img src=x'));assert.match(markup,/&lt;img/);
});

test('API helper sends the required order header and readable validation errors',async()=>{
 const h=harness();await h.run('api("/api/orders/example",undefined,{"x-order-token":"test-code"})');
 assert.equal(h.requests[0].request.headers['x-order-token'],'test-code');
 h.context.fetch=async()=>({ok:false,status:422,json:async()=>({detail:[{msg:'Value error, Enter a valid phone number'}]})});
 await assert.rejects(h.run('api("/api/orders",{})'),/Enter a valid phone number/);
 h.context.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'CASHFREE_CLIENT_SECRET missing'})});
 await assert.rejects(h.run('api("/api/orders/example/registry",{})'),/currently unavailable/);
});

test('repeated staff rendering does not leave operator controls in customer view',()=>{
 const h=harness();h.run('staffView=true;renderOrder(fixture.order);staffView=false;renderOrder(fixture.order)');
 assert.ok(!h.nodes.has('taskEvidence'));assert.ok(!h.nodes.get('trackRes').innerHTML.includes('data-task='));
});

test('staff entry alone does not expose simulated verification',()=>{
 const h=harness();h.run('staffView=true;integrationInfo={demo:true};renderOrder(fixture.order)');
 assert.ok(!h.nodes.has('demoVerify'));
 h.run('$("testTools").checked=true;renderOrder(fixture.order)');assert.ok(h.nodes.has('demoVerify'));
 h.run('integrationInfo.demo=false;renderOrder(fixture.order)');assert.ok(!h.nodes.has('demoVerify'));
});

test('invalid saved preferences fall back safely and language/read-aloud persist',()=>{
 const h=harness();h.context.localStorage.getItem=()=>'{broken';
 assert.equal(h.run('readPreferences().language'),'en');
 h.context.localStorage.getItem=()=>JSON.stringify({language:'te',gender:'male',source:'device',speed:0.85,readAloud:true});
 const prefs=h.run('readPreferences()');assert.equal(prefs.language,'te');assert.equal(prefs.readAloud,true);assert.equal(prefs.gender,undefined);
 h.context.localStorage.getItem=()=>JSON.stringify({language:'__proto__',gender:'invalid',speed:100,readAloud:'yes'});
 assert.equal(h.run('readPreferences().language'),'en');assert.equal(h.run('readPreferences().readAloud'),false);
});

function addDeviceSpeech(h,voices=[{lang:'te-IN',name:'Telugu voice'}]){
 const spoken=[];h.context.window.SpeechSynthesisUtterance=function(text){this.text=text};
 h.context.window.speechSynthesis={getVoices:()=>voices,cancel(){},speak(utterance){spoken.push(utterance);utterance.onstart?.()}};
 h.nodes.get('voice').checked=true;h.run('S.lang="te"');return spoken;
}

test('online failure uses a matching device voice and reports the fallback',async()=>{
 const h=harness(),spoken=addDeviceSpeech(h);h.context.fetch=async()=>({ok:false,status:503});
 await h.run('speakText("తెలుగు పరీక్ష")');assert.equal(spoken.length,1);assert.equal(spoken[0].voice.lang,'te-IN');
 assert.match(h.nodes.get('voiceStatus').textContent,/Online voice unavailable/);
});

test('missing device language is explained without speaking the wrong language',async()=>{
 const h=harness(),spoken=addDeviceSpeech(h,[{lang:'en-US',name:'English'}]);
 await h.run('localVoice("తెలుగు పరీక్ష")');assert.equal(spoken.length,0);assert.match(h.nodes.get('voiceStatus').textContent,/No device voice/);assert.equal(h.requests.length,0);
});

test('stopping voice cancels a late response before playback',async()=>{
 const h=harness(),spoken=addDeviceSpeech(h);let respond;h.context.fetch=()=>new Promise(resolve=>respond=resolve);
 const pending=h.run('speakText("cancel this")');h.run('stopSpeech()');
 respond({ok:true,headers:{get:()=> 'audio/mpeg'},blob:async()=>({size:2000})});await pending;
 assert.equal(spoken.length,0);assert.equal(h.nodes.get('voiceStatus').textContent,'Voice stopped.');
});

test('currency places the minus sign before the rupee symbol',()=>{
 const h=harness();assert.equal(h.run('money(-100)'),'-₹100');assert.equal(h.run('money(100)'),'₹100');
});

test('denied staff access never changes workspace and clears the attempted key',async()=>{
 const h=harness();h.nodes.get('adminToken').value='wrong';h.context.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Admin token required'})});
 await h.run('enterStaffWorkspace()');assert.equal(h.run('staffView'),false);assert.equal(h.nodes.get('adminToken').value,'');assert.match(h.nodes.get('staffMessage').textContent,/valid staff access key/);
});

test('successful staff entry uses server authorisation without persisting the access key',async()=>{
 const h=harness();let saved=0;h.context.localStorage.setItem=()=>saved++;h.context.sessionStorage.setItem=()=>saved++;
 h.nodes.get('adminToken').value='staff-key';h.context.fetch=async(url,request)=>{h.requests.push({url,request});return {ok:true,status:200,json:async()=>({authorized:true})}};
 h.run('go=function(tab){S.lastTab=tab}');await h.run('enterStaffWorkspace()');
 assert.equal(h.requests[0].url,'/api/operator/session');assert.equal(h.requests[0].request.headers['x-admin-token'],'staff-key');assert.equal(h.run('staffView'),true);assert.equal(h.run('S.lastTab'),'bo');assert.equal(saved,0);
});
