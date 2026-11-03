// Render pages with tests/render_pages.py first. Development dependency: jsdom.
const fs=require('fs'),path=require('path'),assert=require('assert'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 const dom=new JSDOM(fs.readFileSync('/tmp/fish-controls.html','utf8'),{url:'http://localhost/controls',runScripts:'outside-only'});
 const w=dom.window,d=w.document,$=id=>d.getElementById(id),calls=[],errors=[];
 w.addEventListener('error',e=>errors.push(e.message));w.confirm=()=>true;
 let poll;w.setTimeout=fn=>{poll=fn;return 1};w.clearTimeout=()=>{};
 let state={ok:true,timezone:'Asia/Manila',ready:true,nextRun:null,active:null,last:null,schedules:[]};
 w.fetch=async(url,o={})=>{
  calls.push([url,o]);const body=o.body?JSON.parse(o.body):{};
  if(o.method==='POST'&&url==='/api/water-schedules')state.schedules=[{id:1,enabled:true,dayName:'Every day (Monday–Sunday)',lastStatus:null,...body}];
  if(o.method==='PUT')Object.assign(state.schedules[0],body);
  if(o.method==='DELETE')state.schedules=[];
  if(url.endsWith('/stop'))state.last={status:'stopping',reason:'Stopped by operator'};
  return{ok:true,json:async()=>structuredClone(state)};
 };
 w.eval(fs.readFileSync(path.join(__dirname,'../static/water_schedule.js'),'utf8'));await tick();
 assert($('water-schedule-form').hidden);assert($('water-change-stop').hidden);
 d.body.classList.add('admin-mode');await tick();assert(!$('water-schedule-form').hidden);
 $('water-day').value='7';$('water-time').value='08:00';$('water-drain').value='30';$('water-refill').value='120';
 $('water-schedule-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 assert.deepEqual(JSON.parse(calls.find(([u,o])=>o.method==='POST')[1].body),{day:7,time:'08:00',drainSeconds:30,refillSeconds:120});
 assert($('water-schedule-list').textContent.includes('Drain 30s'));
 let actions=()=>$('water-schedule-list').querySelectorAll('button');
 actions()[0].click();assert.equal($('water-time').value,'08:00');
 $('water-drain').value='25';await poll();assert.equal($('water-drain').value,'25');
 $('water-schedule-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 assert.equal(state.schedules[0].drainSeconds,25);
 actions()[1].click();await tick();assert.equal(state.schedules[0].enabled,false);
 $('water-change-stop').click();await tick();assert($('water-change-state').textContent.includes('stopping'));
 actions()[2].click();await tick();assert.equal(state.schedules.length,0);
 d.body.classList.remove('admin-mode');await tick();const before=calls.length;
 $('water-change-stop').click();await tick();assert.equal(calls.length,before);
 assert.deepEqual(errors,[]);dom.window.close();
 console.log('Water schedule DOM checks passed: viewer/admin, create/edit, polling drafts, pause, delete and stop.');
})().catch(e=>{console.error(e);process.exit(1)});
