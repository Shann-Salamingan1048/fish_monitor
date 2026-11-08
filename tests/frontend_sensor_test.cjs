// Run python tests/render_pages.py first. Development dependency: jsdom.
const fs=require('fs'), assert=require('assert'), path=require('path'), {JSDOM}=require('jsdom');
const script=fs.readFileSync(path.join(__dirname,'../static/script.js'),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 for(const name of ['index','feeding','water-changes','cooling-fan','camera','graphs','settings','access','developer']){
  const dom=new JSDOM(fs.readFileSync('/tmp/fish-'+name+'.html','utf8'),{url:'http://localhost:5000/'+name,runScripts:'outside-only'});
  const w=dom.window,d=w.document,calls=[],errors=[];
  w.addEventListener('error',e=>errors.push(e.message));w.console.error=(...args)=>errors.push(args.join(' '));w.alert=m=>errors.push(m);
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.HTMLCanvasElement.prototype.getContext=()=>new Proxy({},{get:(o,k)=>o[k]||(()=>{}),set:(o,k,v)=>(o[k]=v,true)});
  w.setInterval=()=>1;
  let role='audience',fan={mode:'AUTO',on:false,available:true,confirmed:true},feeder={ready:true,busy:false};
  const sensors=Object.fromEntries(['temp','ph','turbidity','float_low','float_normal','float_upper'].map(k=>[k,{label:k,effect:'Sensor protection',enabled:true,status:'active',reading:k.startsWith('float')?true:'27'}]));
  const state={session:'run1',revision:1,sensors,disabled:[],allDisabled:false,inputApplied:true,coolerApplied:true};
  const limits={temperature:{limit:30,overheatLimit:35},ph:{lowLimit:6.5,highLimit:8.5,criticalLowLimit:6,criticalHighLimit:9},maxWebsiteViewers:5,maxCameraViewers:3,maxAdminSessions:2,predictionDelaySeconds:30,predictionAlertDurationSeconds:30};
  const payload=()=>({ok:true,role,fan,feeder,limits,sensorControls:JSON.parse(JSON.stringify(state)),temp:27,ph:7,turbidity:'Clear',turbidity_adc:0,float_stat:'Normal',alerts:[],schedules:[],timezone:'Asia/Manila',servo:{tilt:90,pan:90},waterControl:{pumpActive:false},connection:{stale:false}});
  w.fetch=async(url,o={})=>{
   calls.push([url,o]);
   if(url==='/api/auth/login')role='admin';if(url==='/api/auth/logout')role='audience';
   if(url==='/api/fan'&&o.method==='POST')fan={...fan,mode:JSON.parse(o.body).mode,confirmed:false};
   if(url==='/api/feeder'&&o.method==='POST')feeder={ready:true,busy:true,active:{status:'sent',cycles:JSON.parse(o.body).cycles}};
   if(url==='/api/sensor-controls'&&o.method==='PUT'){
    for(const[k,v]of Object.entries(JSON.parse(o.body).enabled))Object.assign(sensors[k],{enabled:v,status:v?'waiting':'disabled',reading:null});
    state.revision++;state.disabled=Object.keys(sensors).filter(k=>!sensors[k].enabled);state.allDisabled=state.disabled.length===6;
   }
   return{ok:true,json:async()=>payload()};
  };
  w.eval(script);await tick();
  assert.equal(d.querySelectorAll('.app-nav a').length,7);
  for(const[id,page]of [['manual-feed-cycles','feeding'],['phCriticalHighLimit','settings']])assert.equal(d.querySelectorAll('#'+id).length,name===page?1:0);
  assert(!d.getElementById('turbidity-adc'));assert(!d.getElementById('turbidityLimit'));
  assert([...d.querySelectorAll('.sensor-control-row button,[data-fan-mode]')].every(b=>b.disabled));
  const before=calls.length;await w.feederAction('feed',3);await w.setFanMode('ON');assert.equal(calls.length,before);
  role='admin';w.setRoleUI(role);
  if(['feeding','camera'].includes(name)){
   const input=d.getElementById(name==='feeding'?'manual-feed-cycles':'camera-feed-cycles');
   const button=name==='feeding'?d.getElementById('feed-custom'):d.querySelector("button[onclick=\"sendCommand('feed')\"]");
   input.value='30';if(name==='camera')await w.sendCommand('feed');else await w.feederAction('feed');
   assert.deepEqual(JSON.parse(calls.filter(([u,o])=>u==='/api/feeder'&&o.method==='POST').at(-1)[1].body),{action:'feed',cycles:30});
   assert(button.disabled);assert(!d.body.textContent.includes('Waiting for the Mega'));
   feeder={ready:true,busy:false,last:{status:'complete',cycles:30}};w.renderFeeder(feeder,{pumpActive:false});
   const prefix=name==='feeding'?'':'camera-';assert(d.getElementById(prefix+'feeder-status').textContent.includes('Completed'));assert.equal(d.getElementById(prefix+'feeder-message').textContent,'');assert(!button.disabled);
   w.renderFeeder(feeder,{pumpActive:true});w.setRoleUI('admin');assert(button.disabled);w.renderFeeder(feeder);assert(button.disabled);w.renderFeeder(feeder,{pumpActive:false});assert(!button.disabled);
   for(const value of['','0','-1','1.5','4294967296']){input.value=value;const count=calls.length;if(name==='camera')await w.sendCommand('feed');else await w.feederAction('feed');assert.equal(calls.length,count);}
  }
  if(name==='feeding'){
   assert.equal(d.querySelectorAll('#custom-feeder button').length,1);
   d.getElementById('feeding-day').value='7';d.getElementById('feeding-time').value='08:00';d.getElementById('feeding-cycles').value='4';w.saveFeedingSchedule({preventDefault(){}});await tick();
   assert.deepEqual(JSON.parse(calls.find(([u,o])=>u==='/api/feeding-schedules'&&o.method==='POST')[1].body),{day:7,time:'08:00',cycles:4,enabled:true});
  }
  if(name==='cooling-fan'){
   await w.setFanMode('ON');assert(d.querySelector('[data-fan-status]').textContent.includes('Starting'));fan={...fan,confirmed:true,on:true};w.renderFan(fan);assert(d.querySelector('[data-fan-status]').textContent.includes('Fan ON'));
  }
  if(name==='water-changes'){
   w.sendWaterAction('both_start');await tick();assert.deepEqual(JSON.parse(calls.find(([u,o])=>u==='/api/water-control'&&o.method==='POST')[1].body),{action:'both_start'});
   for(const action of['extract_stop','insert_stop'])assert(d.querySelector(`[onclick="sendWaterAction('${action}')"]`));
  }
  if(name==='developer'){
   assert.equal(d.querySelectorAll('.sensor-control-row').length,6);
   await w.toggleSensor('ph');assert.equal(d.querySelector('#sensor-row-ph button').textContent,'Enable');
   assert(!d.getElementById('sensor-disabled-banner').hidden);
   await w.toggleSensor('ph');assert.equal(d.querySelector('#sensor-row-ph .sensor-state').textContent,'WAITING');
   role='audience';w.setRoleUI(role);assert([...d.querySelectorAll('.sensor-control-row button')].every(b=>b.disabled));
  }
  w.setTemperatureUnit('F');assert.equal(w.temperatureText(0),'32.0 °F');
  if(name==='settings'){
   assert.equal(d.getElementById('maxAdminSessions').value,'2');d.getElementById('maxAdminSessions').value='3';
   assert(d.querySelector('.display-settings #temperature-unit'));
   const theme = d.getElementById('display-theme');
   for (const choice of ['light','dark']) {
    theme.value = choice;
    theme.dispatchEvent(new w.Event('change'));
    assert.equal(d.documentElement.dataset.theme, choice);
    assert.equal(w.localStorage.getItem('aquapi.theme'), choice);
   }
   assert(!d.querySelector('header #temperature-unit'));
   assert(!d.getElementById('prediction-watch-help'));
   assert.equal(d.getElementById('tempLimit').value,'86');d.getElementById('tempLimit').value='89.6';await w.loadAuthStatus(false);assert.equal(d.getElementById('tempLimit').value,'89.6');
   d.getElementById('tempLimit').value='86';w.saveLimits();await tick();const saved=JSON.parse(calls.find(([u,o])=>u==='/api/limits'&&o.method==='PUT')[1].body);assert.equal(saved.maxAdminSessions,'3');assert.equal(saved.tempLimit,30);assert.equal(saved.tempCriticalLimit,35);assert.equal(d.getElementById('mode-message').innerText,'Settings saved.');
  }
  if(name==='index'){w.renderWaterControl({extractionOn:true,insertionOn:true,pumpActive:true});assert.equal(d.getElementById('pump-mode-val').innerText,'Both pumps');w.renderSmartAlerts({alerts:[]});assert.equal(d.getElementById('alert-summary').textContent,'No active alerts');}
  if(name==='graphs')assert(d.getElementById('tempChart').getAttribute('aria-label').includes('°F'));
  if(name==='access'){
   role='audience';w.setRoleUI(role);d.getElementById('admin-pin').value='test';d.getElementById('admin-login-panel').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal(d.getElementById('mode-message').innerText,'Signed in.');assert.equal(d.getElementById('admin-pin').value,'');d.getElementById('logout-admin').click();await tick();assert.equal(d.getElementById('mode-message').innerText,'Signed out.');
  }
  assert.equal(errors.length,0,name+': '+errors.join('\n'));w.close();
 }
 console.log('All 9 pages passed DOM checks: navigation, role protection, settings, units, sensors, fan/pumps, schedules and custom feeding status.');
})().catch(e=>{console.error(e);process.exit(1)});
