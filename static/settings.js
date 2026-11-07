/* Customer preferences, voice playback and access to the staff workspace. */
const PREFERENCE_KEY='udyam_preferences_v1';
let activeRecognition=null,settingsReturnFocus=null,voiceRequestTimer=null,onlineVoiceRetryAt=0;

function randomVoiceGender(){
 try{const value=new Uint8Array(1);crypto.getRandomValues(value);return value[0]%2?'male':'female'}
 catch{return Math.random()<.5?'female':'male'}
}

function readPreferences(){
 let saved={};try{saved=JSON.parse(localStorage.getItem(PREFERENCE_KEY)||'{}')||{}}catch{}
 return {language:Object.hasOwn(VL,saved.language)?saved.language:'en',readAloud:saved.readAloud===true};
}
function savePreferences(){
 const preferences={language:S.lang,readAloud:$('voice').checked};
 try{localStorage.setItem(PREFERENCE_KEY,JSON.stringify(preferences))}catch{toast('Preferences apply here but could not be saved on this device.')}
}
function voiceStatus(message,state='idle'){
 const status=$('voiceStatus');if(status){status.textContent=message;status.dataset.state=state}
}
function clearVoiceAudio(){
 if(voiceAudio){voiceAudio.onended=null;voiceAudio.onerror=null;voiceAudio.pause();voiceAudio.src='';voiceAudio=null}
 if(voiceUrl){URL.revokeObjectURL(voiceUrl);voiceUrl=''}
}
function stopSpeech(){
 voiceToken++;clearTimeout(voiceRequestTimer);voiceRequestTimer=null;
 if(voiceAbort){voiceAbort.abort();voiceAbort=null}clearVoiceAudio();
 if(window.speechSynthesis)window.speechSynthesis.cancel();
 voiceStatus('Voice stopped.');
}
async function deviceVoices(){
 const synth=window.speechSynthesis;if(!synth)return [];
 let voices=synth.getVoices();if(voices.length)return voices;
 await new Promise(resolve=>{let timer;const finish=()=>{clearTimeout(timer);synth.removeEventListener('voiceschanged',finish);resolve()};synth.addEventListener('voiceschanged',finish);timer=setTimeout(finish,1200)});
 return synth.getVoices();
}
async function localVoice(text,token=voiceToken,fallback=false){
 if(!window.speechSynthesis||!window.SpeechSynthesisUtterance){voiceStatus('Voice is unavailable in this browser. You can continue with text.','error');return false}
 voiceStatus('Preparing device voice…','loading');
 const locale=(VL[S.lang]||'en-IN').toLowerCase(),prefix=locale.split('-')[0];
 const voices=(await deviceVoices()).filter(v=>{const l=(v.lang||'').toLowerCase();return l===locale||l===prefix||l.startsWith(prefix+'-')});
 if(token!==voiceToken)return false;
 if(!voices.length){voiceStatus('No device voice is installed for this language. Choose another language or try online voice.','error');return false}
 const score=v=>((v.lang||'').toLowerCase()===locale?20:0)+(/natural|neural/i.test(v.name)?10:0);
 voices.sort((a,b)=>score(b)-score(a));const utterance=new window.SpeechSynthesisUtterance(text);
  utterance.lang=VL[S.lang];utterance.voice=voices[0];utterance.rate=1;
 utterance.onstart=()=>{if(token===voiceToken)voiceStatus(fallback?'Online voice unavailable. Playing the device voice.':'Playing the device voice.','playing')};
 utterance.onend=()=>{if(token===voiceToken)voiceStatus(fallback?'Played with the device voice. Online voice is currently unavailable.':'Device voice finished.','success')};
 utterance.onerror=()=>{if(token===voiceToken)voiceStatus('Device voice could not play. Check sound settings or continue with text.','error')};
 try{window.speechSynthesis.speak(utterance);return true}catch{voiceStatus('Device voice could not start. You can continue with text.','error');return false}
}
async function speakText(text){
 if(!$('voice').checked||!text)return;
 stopSpeech();const token=voiceToken;
 if(Date.now()<onlineVoiceRetryAt)return localVoice(text,token,true);
 voiceStatus('Preparing voice…','loading');const controller=new AbortController();voiceAbort=controller;
 voiceRequestTimer=setTimeout(()=>controller.abort(),12000);
 try{
  const response=await fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,lang:S.lang,gender:S.voiceGender}),signal:controller.signal});
  if(!response.ok||!response.headers.get('content-type')?.startsWith('audio/mpeg'))throw new Error('Voice service unavailable');
  const blob=await response.blob();if(token!==voiceToken)return;if(blob.size<100)throw new Error('Empty voice response');
  clearTimeout(voiceRequestTimer);voiceUrl=URL.createObjectURL(blob);voiceAudio=new Audio(voiceUrl);voiceAudio.playbackRate=1;
  voiceAudio.onended=()=>{if(token===voiceToken){clearVoiceAudio();voiceStatus('Voice finished.','success')}};
  voiceAudio.onerror=()=>{if(token===voiceToken){clearVoiceAudio();localVoice(text,token,true)}};
  await voiceAudio.play();if(token!==voiceToken)return;onlineVoiceRetryAt=0;voiceStatus('Playing voice.','playing');
 }catch(error){
  if(token!==voiceToken)return;clearVoiceAudio();
  if(error.name==='NotAllowedError'){voiceStatus('Your browser blocked sound. Allow audio for this site or continue with text.','error');return}
  onlineVoiceRetryAt=Date.now()+60000;voiceStatus('Online voice unavailable. Checking device voices…','loading');await localVoice(text,token,true);
 }finally{if(token===voiceToken){clearTimeout(voiceRequestTimer);voiceRequestTimer=null;voiceAbort=null}}
}
function stopListening(){
 if(activeRecognition){activeRecognition.abort();activeRecognition=null}
 $('mic')?.setAttribute('aria-pressed','false');
}
function startListening(){
 if(activeRecognition){stopListening();return}
 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!Recognition){toast('Voice input is not supported in this browser. Type your answer instead.');return}
 stopSpeech();const recognition=new Recognition();activeRecognition=recognition;
 recognition.lang=VL[S.lang]||'en-IN';recognition.continuous=false;recognition.interimResults=false;recognition.maxAlternatives=1;
 recognition.onstart=()=>{$('mic').setAttribute('aria-pressed','true');$('inputStatus').textContent=(VC[S.lang]||VC.en).listening};
  recognition.onresult=event=>{$('free').value=event.results[0][0].transcript;$('inputStatus').textContent=tr('loading');sendFree()};
 recognition.onerror=event=>{if(event.error!=='aborted')$('inputStatus').textContent=(VC[S.lang]||VC.en).micError};
 recognition.onend=()=>{if(activeRecognition===recognition)activeRecognition=null;$('mic').setAttribute('aria-pressed','false')};
 try{recognition.start()}catch{stopListening();$('inputStatus').textContent='Could not start the microphone. You can type your answer.'}
}
function testToolsEnabled(){return !!(hasStaffRole(STAFF_ROLE.admin,STAFF_ROLE.activation)&&integrationInfo?.demo&&$('testTools')?.checked)}
function refreshTestTools(){
 document.body.classList.toggle('test-tools',testToolsEnabled());
 if($('testToolsSection'))$('testToolsSection').hidden=!(hasStaffRole(STAFF_ROLE.admin,STAFF_ROLE.activation)&&integrationInfo?.demo);
 if(S.order?.activation)renderOrder(S.order);
}
function openSettings(){
 settingsReturnFocus=document.activeElement;stopListening();$('settingsDialog').showModal();$('closeSettings').focus();
}
function closeSettings(){$('settingsDialog').close()}
function openAccess(panel='customerAccessPanel'){
 stopListening();const customerActive=!!sessionStorage.getItem('udyam_customer_token'),accountActive=customerActive||staffView;
 if(staffView)panel='staffAccessPanel';else if(customerActive)panel='customerAccessPanel';
 $('accessRoleTabs').hidden=accountActive;$('accessTitle').textContent=tr(accountActive?'my_account':'sign_in');
 for(const button of document.querySelectorAll('[data-access-panel]'))button.classList.toggle('on',button.dataset.accessPanel===panel);
 for(const section of document.querySelectorAll('.access-panel'))section.classList.toggle('on',section.id===panel);
 $('accessDialog').showModal();$('closeAccess').focus();
}
function closeAccess(){if($('accessDialog').open)$('accessDialog').close()}
function updateWorkspaceUI(){
 $('staffAccessForm').hidden=staffView;$('staffSession').hidden=!staffView;
 $('connectionSettings').hidden=!hasStaffRole(STAFF_ROLE.admin);$('staffMessage').textContent='';
 if(staffView){const labels=staffIdentity.roles.length?staffIdentity.roles.map(r=>r.replace('udyamone_','').replaceAll('_',' ')).join(' · '):tr('local_demo_role');$('staffAccountSummary').textContent=tr('signed_in_as',{value:labels})+(staffIdentity.partner_code?' · '+tr('partner_code',{value:staffIdentity.partner_code}):'')}
 $('openAccess').textContent=staffView||sessionStorage.getItem('udyam_customer_token')?tr('my_account'):tr('sign_in');
 refreshTestTools();
}
let customerChallenge='';
function updateCustomerSession(){
  const active=!!sessionStorage.getItem('udyam_customer_token');
  $('customerLoginForm').hidden=active;$('customerSession').hidden=!active;
  document.body.classList.toggle('customer-authenticated',active);if(active){const phone=$('customerPhone').value.trim();$('customerAccountSummary').textContent=phone?tr('signed_in_as',{value:phone.replace(/.(?=.{4})/g,'•')}):tr('customer_session_active')}
  if(!staffView)$('openAccess').textContent=active?tr('my_account'):tr('sign_in');updateJourneyNav();
}
async function sendCustomerCode(){
 const phone=$('customerPhone').value.trim();if(!phone){$('customerAuthMessage').textContent='Enter your phone number.';return}
 $('sendCustomerCode').disabled=true;$('customerAuthMessage').textContent='Sending code…';
 try{const d=await api('/api/auth/customer/request-otp',{phone});customerChallenge=d.challenge_id;$('customerCodeForm').hidden=false;$('customerAuthMessage').textContent=d.debug_code?'Local test code: '+d.debug_code:'Code sent. It expires in five minutes.';$('customerCode').focus()}
 catch(e){$('customerAuthMessage').textContent=e.message}finally{$('sendCustomerCode').disabled=false}
}
async function verifyCustomerCode(){
 if(!customerChallenge||!$('customerCode').reportValidity())return;
 try{const d=await api('/api/auth/customer/verify-otp',{challenge_id:customerChallenge,code:$('customerCode').value.trim()});sessionStorage.setItem('udyam_customer_token',d.access_token);$('customerAuthMessage').textContent='Phone verified.';if(!$('phone').value)$('phone').value=$('customerPhone').value;updateCustomerSession();closeAccess();showCustomerDashboard()}
 catch(e){$('customerAuthMessage').textContent=e.message}
}
async function customerLogout(){
 try{await api('/api/auth/customer/logout',{},customerHeaders())}catch{}
 sessionStorage.removeItem('udyam_customer_token');customerChallenge='';$('customerCode').value='';$('customerCodeForm').hidden=true;$('customerAuthMessage').textContent='';closeAccess();$('accessRoleTabs').hidden=false;$('accessTitle').textContent=tr('sign_in');updateCustomerSession();showHome();
}
async function enterStaffWorkspace(){
 const button=$('enterStaff');button.disabled=true;$('staffMessage').textContent='Checking staff access…';
 try{const identity=await api('/api/operator/session',undefined,operatorHeaders());staffIdentity={roles:identity.roles||[],partner_code:identity.partner_code||''};setStaffView(true);updateWorkspaceUI();closeAccess();showApp('bo');configureStaffWorkspace()}
 catch(error){$('adminToken').value='';$('staffMessage').textContent=(error.message.includes('authentication')||error.message.includes('Admin token'))?'Enter a valid authorised access token.':error.message}
 finally{button.disabled=false}
}
function leaveStaffWorkspace(){
 $('adminToken').value='';$('testTools').checked=false;orderLoadSequence++;
 if(S.order&&!sessionStorage.getItem('uo_token_'+S.order.id)&&!sessionStorage.getItem('udyam_customer_token')){
  S.order=null;$('trackRes').textContent='Enter your order reference and private access code to track services.';
  for(const id of ['trackId','kycOrder','orderAccess','udyamNo','f_gst','f_ud','f_sid','f_addr'])$(id).value='';$('kres').textContent='';
  localStorage.removeItem('udyam_order');resetAdvisor();
 }
 $('routingOrders').textContent='';$('boTable').querySelector('tbody').innerHTML='';$('boMeta').textContent='';$('partnerSummary').textContent='';
 staffIdentity={roles:[],partner_code:''};setStaffView(false);updateWorkspaceUI();closeAccess();showHome();
}
function renderConnectionSettings(){
 const providers=integrationInfo?.providers;if(!providers)return;
 const labels={telecom:'Telecom activation',verification:'GST / Udyam verification',payments:'Payments',whatsapp:'WhatsApp'};
 const descriptions={telecom:'Live provisioning is not connected. Activation steps are recorded within UdyamOne.',verification:'Live registry checks are not connected. Local checks use the supplied reference records.',payments:'Online payments are not enabled. No real charge can be made.',whatsapp:'The WhatsApp account is not connected. The conversation preview does not send messages.'};
 $('integrationStatus').innerHTML=Object.entries(providers).map(([key,provider])=>'<article><div class="row"><h4>'+esc(labels[key]||key)+'</h4><span class="connection-state">'+esc(provider.status==='configured_not_verified'?'Configured · unverified':'Not connected')+'</span></div><p>'+esc(provider.status==='configured_not_verified'?provider.detail:descriptions[key]||provider.detail)+'</p></article>').join('');
 refreshTestTools();
}
function setupSettings(){
 const preferences=readPreferences();S.lang=preferences.language;S.voiceGender=randomVoiceGender();
 $('lang').value=S.lang;$('voice').checked=preferences.readAloud;
  applyLang();if($('languageScope'))$('languageScope').hidden=S.lang==='en';
 // Refresh the initial prompt without autoplay when saved preferences are restored.
 const aloud=$('voice').checked;$('voice').checked=false;$('chat').innerHTML='';ask();$('voice').checked=aloud;
   $('openSettings').onclick=openSettings;$('footerSettings').onclick=openSettings;$('closeSettings').onclick=closeSettings;
   $('openAccess').onclick=()=>openAccess(staffView?'staffAccessPanel':'customerAccessPanel');$('closeAccess').onclick=closeAccess;$('landingSignIn').onclick=()=>openAccess('customerAccessPanel');$('openCustomerSignIn').onclick=()=>openAccess('customerAccessPanel');$('openCustomerDashboard').onclick=()=>{closeAccess();showCustomerDashboard()};
  document.querySelectorAll('[data-access-panel]').forEach(button=>button.onclick=()=>{for(const item of document.querySelectorAll('[data-access-panel]'))item.classList.toggle('on',item===button);for(const section of document.querySelectorAll('.access-panel'))section.classList.toggle('on',section.id===button.dataset.accessPanel)});
  $('settingsDialog').addEventListener('close',()=>{stopSpeech();settingsReturnFocus?.focus()});
  $('settingsDialog').addEventListener('click',event=>{if(event.target===$('settingsDialog')){const box=$('settingsDialog').getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)closeSettings()}});
  $('accessDialog').addEventListener('click',event=>{if(event.target===$('accessDialog')){const box=$('accessDialog').getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)closeAccess()}});
  $('lang').onchange=()=>{stopSpeech();stopListening();S.lang=$('lang').value;S.voiceGender=randomVoiceGender();applyLang();updateCustomerSession();updateWorkspaceUI();if($('languageScope'))$('languageScope').hidden=S.lang==='en';if(!businessContext){const aloud=$('voice').checked;$('voice').checked=false;$('chat').innerHTML='';ask();$('voice').checked=aloud}refreshLocalizedExperience();savePreferences();voiceStatus($('voice').checked?tr('voice_on'):tr('voice_off'))};
  $('voice').onchange=()=>{stopSpeech();if($('voice').checked)S.voiceGender=randomVoiceGender();savePreferences();voiceStatus($('voice').checked?tr('voice_on'):tr('voice_off'))};
  $('enterStaff').onclick=enterStaffWorkspace;$('adminToken').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();enterStaffWorkspace()}};
  $('sendCustomerCode').onclick=sendCustomerCode;$('verifyCustomerCode').onclick=verifyCustomerCode;$('customerLogout').onclick=customerLogout;$('customerCode').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();verifyCustomerCode()}};updateCustomerSession();
   $('leaveStaff').onclick=leaveStaffWorkspace;$('testTools').onchange=refreshTestTools;
 $('mic').onclick=startListening;$('mic').setAttribute('aria-pressed','false');
 if(!(window.SpeechRecognition||window.webkitSpeechRecognition)){$('mic').disabled=true;$('mic').title=tr('voice_input');$('inputStatus').textContent=tr('input_help')}
 window.addEventListener('pagehide',()=>{stopSpeech();stopListening()});
 updateWorkspaceUI();renderConnectionSettings();
}
