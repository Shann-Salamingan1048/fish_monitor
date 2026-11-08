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
    const panel = reef.querySelector('.reef-instruments');
    const panelPreference = read('aquapi-scene-panel');
    panel.open = panelPreference === null ? window.innerWidth > 700 : panelPreference === 'open';
    panel.addEventListener('toggle', () => save('aquapi-scene-panel', panel.open ? 'open' : 'closed'));

    const school = reef.querySelector('.reef-school');
    const laneTemplate = school.firstElementChild.cloneNode(true);
    const defaultSpecies = Array.from(school.children).slice(0, 3).map(lane => lane.dataset.species);
    const picker = document.getElementById('fish-picker');
    const speciesImages = new Map(Array.from(picker.querySelectorAll('[data-species]'), choice =>
        [choice.dataset.species, choice.querySelector('img').src]));
    const addFish = document.getElementById('fish-add');
    const removeFish = document.getElementById('fish-remove');
    const fishCount = document.getElementById('fish-count');
    const quantity = document.getElementById('fish-quantity');
    const space = document.getElementById('fish-space');
    const maximumFish = 24;
    function positionFish(lane, index) {
        const seconds = 31 + (index * 7) % 24;
        lane.style.setProperty('--depth', (8 + (index * 23) % 72) + '%');
        lane.style.setProperty('--lap', seconds + 's');
        lane.style.setProperty('--start', -seconds * ((.28 + index * .618) % 1) + 's');
        lane.style.setProperty('--size', (4.2 + (index * 1.3) % 3.8).toFixed(1) + 'vw');
    }
    function applySpecies(lane, species) {
        if (lane.dataset.species !== species) {
            lane.dataset.species = species;
            lane.querySelector('img').src = speciesImages.get(species);
        }
    }
    function appendFish(species) {
        const lane = laneTemplate.cloneNode(true);
        applySpecies(lane, species);
        positionFish(lane, school.childElementCount);
        school.append(lane);
    }
    function updateFishControls() {
        const count = school.childElementCount;
        fishCount.textContent = String(count);
        addFish.disabled = count >= maximumFish;
        removeFish.disabled = count === 0;
    }
    function restoreSchool() {
        let roster;
        try { roster = JSON.parse(read('aquapi-scene-fish-roster')); } catch {}
        if (!Array.isArray(roster) || roster.length > maximumFish || !roster.every(id => speciesImages.has(id))) {
            const previous = read('aquapi-scene-fish-count');
            const n = previous === null ? 10 : Number(previous);
            const count = Number.isInteger(n) && n >= 0 && n <= maximumFish ? n : 10;
            roster = Array.from({length: count}, (_, i) => defaultSpecies[i % defaultSpecies.length]);
        }
        while (school.childElementCount > roster.length) school.lastElementChild.remove();
        roster.forEach((species, index) => {
            const lane = school.children[index];
            if (lane) {
                applySpecies(lane, species);
                positionFish(lane, index);
            } else appendFish(species);
        });
        updateFishControls();
    }
    function saveSchool() {
        save('aquapi-scene-fish-roster', JSON.stringify(Array.from(school.children, lane => lane.dataset.species)));
        updateFishControls();
    }
    restoreSchool();
    addFish.addEventListener('click', () => {
        const remaining = maximumFish - school.childElementCount;
        quantity.replaceChildren();
        for (let n = 1; n <= remaining; n++) quantity.add(new Option(String(n), String(n)));
        space.textContent = remaining === 1 ? '1 space left' : remaining + ' spaces left';
        picker.showModal();
    });
    document.getElementById('fish-picker-close').addEventListener('click', () => picker.close());
    picker.addEventListener('click', event => {
        const choice = event.target.closest('button[data-species]');
        const count = Number(quantity.value);
        if (choice && Number.isInteger(count) && count >= 1 && count <= maximumFish - school.childElementCount) {
            for (let i = 0; i < count; i++) appendFish(choice.dataset.species);
            picker.close();
            saveSchool();
            if (addFish.disabled) removeFish.focus();
        }
    });
    removeFish.addEventListener('click', () => {
        if (school.lastElementChild) school.lastElementChild.remove();
        saveSchool();
    });
    let inView = true;
    if ('IntersectionObserver' in window) {
        new IntersectionObserver(([entry]) => {
            inView = entry.isIntersecting;
            updateMotion();
        }).observe(reef);
    }
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
        reef.dataset.paused = String(paused || reduced.matches || document.hidden || !inView);
        rippleButton.disabled = paused || reduced.matches;
        if (reef.dataset.paused === 'true' && ripple) ripple.cancel();
        motion.disabled = reduced.matches;
        const caption = reduced.matches ? 'Motion off' : paused ? 'Resume motion' : 'Pause motion';
        motion.querySelector('span').textContent = caption;
        motion.querySelector('i').className = 'ico ' + (paused ? 'ico-play' : 'ico-motion');
        motion.setAttribute('aria-label', caption);
        motion.setAttribute('aria-pressed', String(paused || reduced.matches));
    }
    reef.addEventListener('click', event => {
        const target = event.target;
        const fromButton = rippleButton.contains(target);
        if (reef.dataset.paused === 'true' || (!fromButton && target.closest('.reef-top, .reef-dock, .reef-instruments'))) return;
        const rect = reef.getBoundingClientRect();
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
