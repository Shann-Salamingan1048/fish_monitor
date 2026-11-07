(() => {
    const reef = document.querySelector('.reef');
    const light = document.getElementById('reef-light');
    const motion = document.getElementById('reef-motion');
    const rippleButton = document.getElementById('reef-ripple');
    const ring = reef.querySelector('.reef-ripple');
    let ripple = null;
    const clock = new Intl.DateTimeFormat('en-GB', {
        timeZone: reef.dataset.zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    });
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    function read(key) { try { return localStorage.getItem(key); } catch { return null; } }
    function save(key, value) { try { localStorage.setItem(key, value); } catch {} }
    const savedLight = read('aquapi-scene-light');
    if (Array.from(light.options).some(option => option.value === savedLight)) light.value = savedLight;
    let paused = read('aquapi-scene-paused') === 'yes';
    function updateScene() {
        const time = clock.format(new Date());
        const hour = Number(time.slice(0, 2));
        const period = hour < 5 ? 'night' : hour < 7 ? 'dawn' : hour < 17 ? 'day' : hour < 19 ? 'dusk' : 'night';
        reef.dataset.light = light.value === 'auto' ? period : light.value;
        document.getElementById('reef-period').textContent = light.value === 'auto' ? period[0].toUpperCase() + period.slice(1) : '';
        document.getElementById('time-val').textContent = time;
    }
    function updateMotion() {
        reef.dataset.paused = String(paused || reduced.matches || document.hidden);
        rippleButton.disabled = paused || reduced.matches;
        if (reef.dataset.paused === 'true' && ripple) ripple.cancel();
        motion.disabled = reduced.matches;
        motion.textContent = reduced.matches ? 'Motion off' : paused ? 'Resume motion' : 'Pause motion';
        motion.setAttribute('aria-label', motion.textContent);
        motion.setAttribute('aria-pressed', String(paused || reduced.matches));
    }
    reef.addEventListener('click', event => {
        const target = event.target;
        if (reef.dataset.paused === 'true' || (target !== rippleButton && target.closest('.reef-top, .reef-dock, .reef-instruments'))) return;
        const rect = reef.getBoundingClientRect();
        const fromButton = target === rippleButton;
        ring.style.left = (fromButton ? rect.width * .67 : event.clientX - rect.left) + 'px';
        ring.style.top = (fromButton ? rect.height * .45 : event.clientY - rect.top) + 'px';
        if (ripple) ripple.cancel();
        ripple = ring.animate([{transform:'scale(.2)', opacity:.9}, {transform:'scale(3)', opacity:0}],
                              {duration:850, easing:'ease-out'});
    });
    light.addEventListener('change', () => { save('aquapi-scene-light', light.value); updateScene(); });
    motion.addEventListener('click', () => { paused = !paused; save('aquapi-scene-paused', paused ? 'yes' : 'no'); updateMotion(); });
    reduced.addEventListener('change', updateMotion);
    document.addEventListener('visibilitychange', () => { updateMotion(); if (!document.hidden) updateScene(); });
    updateMotion();
    updateScene();
    setInterval(() => { if (!document.hidden) updateScene(); }, 30000);
})();

(() => {
    const names = {temp:'Temperature', ph:'pH Level', water:'Water Level', clarity:'Turbidity'};
    const probes = Array.from(document.querySelectorAll('[data-probe]'), node => ({
        node, key:node.dataset.probe, samples:[], value:node.querySelector('strong'),
        status:node.querySelector('.probe-state'), detail:node.querySelector('.probe-detail span'),
        marker:node.querySelector('.probe-scale i'), ends:node.querySelector('.probe-ends').children,
        canvas:node.querySelector('canvas'), color:getComputedStyle(node).getPropertyValue('--ink').trim()
    }));
    let lastSample = 0;
    function trace(probe, valid) {
        const ctx = probe.canvas.getContext('2d');
        if (!ctx) return;
        const {width,height} = probe.canvas;
        ctx.clearRect(0,0,width,height);
        if (!valid.length) return;
        const lo = Math.min(...valid), hi = Math.max(...valid);
        ctx.lineWidth = 3;
        ctx.strokeStyle = ctx.fillStyle = probe.color;
        let previous = null;
        ctx.beginPath();
        probe.samples.forEach((v,i) => {
            if (v === null) { previous = null; return; }
            const x = width - 5 - (probe.samples.length - 1 - i) * (width - 10) / 29;
            const y = hi === lo ? height / 2 : height - 5 - (v - lo) / (hi - lo) * (height - 10);
            if (previous === null) ctx.moveTo(x,y);
            else {
                if (probe.key === 'water' || probe.key === 'clarity') ctx.lineTo(x,previous);
                ctx.lineTo(x,y);
            }
            previous = y;
        });
        ctx.stroke();
        if (previous !== null) {
            ctx.beginPath(); ctx.arc(width-5,previous,3.5,0,Math.PI*2); ctx.fill();
        }
    }
    window.addEventListener('aquapi-readings', event => {
        const {data, values, scaleChanged} = event.detail;
        const offline = Boolean(data.connection?.stale);
        const fields = {temp:data.temp, ph:data.ph, water:data.float_stat, clarity:data.turbidity};
        const now = Date.now();
        for (const probe of probes) {
            const key = probe.key, v = offline ? null : values[key];
            const alarm = (data.alerts || []).find(a => a.sensor === names[key] && a.type === 'limit');
            const severity = v === null ? 'idle' : alarm?.severity || 'live';
            const raw = String(fields[key] ?? '');
            const unavailable = {DISABLED:'Disabled', PAUSED:'Paused', ERROR:'Error', PARTIAL:'Partial'};
            probe.node.dataset.state = severity;
            probe.status.textContent = severity === 'idle' ? (offline ? 'Offline' : unavailable[raw.toUpperCase()] || 'Waiting')
                                                           : severity[0].toUpperCase() + severity.slice(1);
            let ends, maximum, text, categories;
            switch (key) {
                case 'temp':
                    maximum = data.limits?.temperature?.overheatLimit;
                    ends = Number.isFinite(maximum) ? [temperatureText(0), 'Critical ' + temperatureText(maximum)] : ['', ''];
                    text = v === null ? '—' : temperatureText(v);
                    break;
                case 'ph': maximum=14; ends=['0 · Acidic','14 · Alkaline']; text=raw; break;
                case 'water':
                    categories=['Very low','Low','Normal','Overflow'];
                    maximum=3; ends=['Very low','Overflow']; text=raw.replace(/^Water level (is )?/i,''); break;
                case 'clarity':
                    categories=clarityLevels;
                    maximum = categories.length - 1;
                    ends = [categories[0] || '', maximum > 0 ? categories[maximum] : ''];
                    text = data.turbidity;
                    if (scaleChanged) probe.samples.length = 0;
                    break;
            }
            probe.value.textContent = v === null ? '—' : text;
            probe.ends[0].textContent = ends[0];
            probe.ends[1].textContent = ends[1];
            probe.marker.hidden = v === null || !(maximum > 0);
            if (!probe.marker.hidden) probe.marker.style.left = Math.max(0, Math.min(100,100*v/maximum)) + '%';
            if (now - lastSample > 7500) probe.samples.length = 0;
            probe.samples.push(v);
            if (probe.samples.length > 30) probe.samples.shift();
            const valid = probe.samples.filter(Number.isFinite);
            trace(probe, valid);
            if (!valid.length) probe.detail.textContent = 'Waiting for readings';
            else {
                const format = value => categories ? categories[value] : key === 'temp' ? temperatureText(value) : value.toFixed(2);
                const low = Math.min(...valid), high = Math.max(...valid);
                probe.detail.textContent = 'Recent · ' + format(low) + (low === high ? '' : ' – ' + format(high));
            }
        }
        lastSample = now;
    });
})();
