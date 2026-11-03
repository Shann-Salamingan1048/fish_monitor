(() => {
    const $ = id => document.getElementById(id);
    if (!$('water-schedule')) return;
    let rows = [], editing = null, busy = false, timer, stopped = false;
    const admin = () => document.body.classList.contains('admin-mode');
    const message = (text, error=false) => {
        $('water-schedule-message').textContent=text;
        $('water-schedule-message').classList.toggle('error',error);
    };
    function access() {
        $('water-schedule-form').hidden = !admin();
        $('water-change-stop').hidden = !admin();
        $('water-schedule').querySelectorAll('button,input,select').forEach(el=>el.disabled=busy || !admin());
        $('water-schedule-list').querySelectorAll('[data-actions]').forEach(el=>el.hidden=!admin());
    }
    const observer=new MutationObserver(access);
    observer.observe(document.body,{attributes:true,attributeFilter:['class']});
    async function request(url='/api/water-schedules',method='GET',payload) {
        const response=await fetch(url,{method,headers:{'Content-Type':'application/json'},
            ...(payload===undefined?{}:{body:JSON.stringify(payload)})});
        const data=await response.json();
        if(!response.ok || !data.ok) throw new Error(data.error || 'Unable to update water schedules.');
        return data;
    }
    function render(data) {
        rows=data.schedules;
        const next=data.nextRun ? new Date(data.nextRun.dateTime).toLocaleString(undefined,{timeZone:data.timezone}) : 'None';
        $('water-schedule-next').textContent=`Next: ${next} · ${data.timezone}`;
        const job=data.active || data.last;
        $('water-change-state').textContent=(!data.ready ? 'Connect Mega with the water-change firmware. ' : '')+
            (job ? `${data.active?'Current':'Last'}: ${job.status}${job.reason?' · '+job.reason:''}` : 'No water changes yet.');
        const list=$('water-schedule-list'); list.replaceChildren();
        if(!rows.length) list.textContent='No water-change schedules.';
        for(const row of rows) {
            const item=document.createElement('article'); item.className='feeding-schedule-item';
            const detail=document.createElement('div'); detail.className='schedule-details';
            const title=document.createElement('strong'); title.textContent=`${row.dayName} at ${row.time}`;
            const text=document.createElement('span');
            text.textContent=`Drain ${row.drainSeconds}s · refill timeout ${row.refillSeconds}s · ${row.enabled?'Active':'Paused'} · Last: ${row.lastStatus || 'Not run'}`;
            detail.append(title,text); item.append(detail);
            const actions=document.createElement('div');actions.dataset.actions='';actions.className='schedule-admin-actions';
            for(const [label,fn] of [['Edit',()=>edit(row)], [row.enabled?'Pause':'Enable',()=>mutate('/api/water-schedules/'+row.id,'PUT',{enabled:!row.enabled})],['Delete',()=>{
                if(window.confirm('Delete this water-change schedule?')) mutate('/api/water-schedules/'+row.id,'DELETE');
            }]]) {
                const b=document.createElement('button');b.type='button';b.textContent=label;b.addEventListener('click',fn);actions.append(b);
            }
            item.append(actions);list.append(item);
        }
        access();
    }
    function reset() {
        editing=null;$('water-schedule-form').reset();$('water-save').textContent='Add schedule';$('water-cancel').hidden=true;
    }
    function edit(row) {
        if(!admin() || busy)return;
        editing=row.id;
        for(const [id,value] of [['water-day',row.day],['water-time',row.time],['water-drain',row.drainSeconds],['water-refill',row.refillSeconds]]) $(id).value=value;
        $('water-save').textContent='Save changes';$('water-cancel').hidden=false;
    }
    async function mutate(url,method,payload,clear=false) {
        if(!admin() || busy)return;
        busy=true;access();
        try {
            const data=await request(url,method,payload);
            if(clear || method==='DELETE' && editing && !data.schedules.some(r=>r.id===editing))reset();
            render(data);message(url.endsWith('/stop')?'Stop requested. Automatic water control is paused.':'Schedule saved.');
        } catch(error){message(error.message,true);}
        finally{busy=false;access();}
    }
    async function poll() {
        if(!busy) {
            try{const data=await request();if(!busy)render(data);}
            catch(error){message(error.message,true);}
        }
        if(!stopped)timer=setTimeout(poll,3000);
    }
    $('water-schedule-form').addEventListener('submit',event=>{
        event.preventDefault();
        mutate('/api/water-schedules'+(editing?'/'+editing:''),editing?'PUT':'POST',{
            day:Number($('water-day').value),time:$('water-time').value,
            drainSeconds:Number($('water-drain').value),refillSeconds:Number($('water-refill').value)
        },true);
    });
    $('water-cancel').addEventListener('click',reset);
    $('water-change-stop').addEventListener('click',()=>mutate('/api/water-change/stop','POST',{}));
    window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);observer.disconnect();});
    access();poll();
})();
