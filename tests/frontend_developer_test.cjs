// Usage: node tests/frontend_developer_test.cjs /path/to/rendered-developer.html
// Development-only dependency: jsdom.
const fs=require('fs'), path=require('path'), assert=require('assert'), {JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,20));
(async()=>{
 const dom=new JSDOM(fs.readFileSync(process.argv[2],'utf8'),{url:'http://localhost/developer',runScripts:'outside-only'});
 const w=dom.window,d=w.document,$=id=>d.getElementById(id),errors=[],mutations=[];
 w.addEventListener('error',e=>errors.push(e.message));w.confirm=()=>true;
 let poll; w.setTimeout=fn=>{poll=fn;return 1}; w.clearTimeout=()=>{};
 let state={active:false,categories:['Very Cloudy','Cloudy','Slightly Cloudy','Clear','Very Clear'],
   profile:{ph:null,turbidity:null},defaults:[{min:0,max:1023,label:'Clear',category:'Clear'}],
   live:{fresh:true,phADC:850,turbidityADC:700,turbidityNTU:null,turbidityNTUState:'not_calibrated'}};
 w.fetch=async(url,options={})=>{
  if(options.body){const data=JSON.parse(options.body);mutations.push(data);
   if(options.method==='POST')state.active=data.active;
   if(options.method==='PUT'){state.profile=data;state.live.turbidityNTU=100;state.live.turbidityNTUState='calibrated';}
  }
  return{ok:true,status:200,json:async()=>({ok:true,calibration:structuredClone(state)})};
 };
 w.eval(fs.readFileSync(path.join(__dirname,'../static/developer.js'),'utf8'));await tick();
 assert.equal($('live-turbidity-adc').textContent,'700');
 assert.equal($('live-turbidity-ntu').textContent,'Not calibrated');
 assert($('calibration-fields').disabled);
 $('calibration-start').click();await tick();assert(!$('calibration-fields').disabled);
 const changeUnit=unit=>{$('turbidity-unit').value=unit;$('turbidity-unit').dispatchEvent(new w.Event('change'));};
 changeUnit('NTU');assert($('ntu-calibrated').checked && $('turbidity-custom').checked);assert(!$('ntu-calibration-panel').hidden);
 const point=(i,key)=>$('ntu-points').children[i].querySelector(`[data-field="${key}"]`);
 point(0,'adc').value='900';point(0,'ntu').value='0';
 $('ntu-points').children[1].querySelector('button').click();assert.equal(point(1,'adc').value,'700');
 point(1,'adc').value='100';point(1,'ntu').value='1000';
 $('add-ntu-point').click();assert.equal($('ntu-points').children.length,3);
 $('ntu-points').lastElementChild.querySelectorAll('button')[1].click();assert.equal($('ntu-points').children.length,2);
 const range=(i,key)=>$('turbidity-ranges').children[i].querySelector(`[data-field="${key}"]`);
 range(0,'max').value='100.25';range(0,'label').value='Clear <b>water</b>';
 range(1,'min').value='100.25';range(1,'label').value='Cloudy';
 changeUnit('ADC');assert.equal(range(0,'max').value,'1023');
 changeUnit('NTU');assert.equal(range(0,'max').value,'100.25');
 await poll();assert.equal(range(0,'max').value,'100.25');
 $('calibration-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 const saved=mutations.at(-1);assert.equal(saved.turbidityUnit,'NTU');assert.equal(saved.turbidityPoints.length,2);
 assert.equal(saved.turbidity[0].max,100.25);assert.equal(saved.turbidity[0].label,'Clear <b>water</b>');
 assert.equal($('turbidity-ranges').querySelectorAll('b').length,0);
 assert.equal($('live-turbidity-ntu').textContent,'100.00 NTU');
 state.live={fresh:false,turbidityADC:null,turbidityNTU:null,turbidityNTUState:'unavailable'};await poll();
 assert.equal($('live-turbidity-adc').textContent,'—');assert.equal($('live-turbidity-ntu').textContent,'—');
 const oldADC=point(0,'adc').value;$('ntu-points').children[0].querySelector('button').click();
 assert.equal(point(0,'adc').value,oldADC);assert($('calibration-message').textContent.includes('fresh'));
 $('calibration-finish').click();await tick();assert($('calibration-fields').disabled);
 assert.deepEqual(errors,[]);dom.window.close();
 console.log('Developer DOM checks passed: readings, mode choice, draft retention, references, capture, save, stale data, safe labels, finish.');
})().catch(e=>{console.error(e);process.exit(1)});
