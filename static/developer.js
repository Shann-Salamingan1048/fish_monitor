/* Calibration values never enter innerHTML; display names are plain text. */
(() => {
    const $ = id => document.getElementById(id);
    let categories = [], active = false, loaded = false, busy = false, timer, generation = 0;
    let rangeUnit = 'ADC', rangeDrafts = {}, liveADC = null, defaults = [];
    const message = (text, error = false) => {
        $('calibration-message').textContent = text;
        $('calibration-message').classList.toggle('error', error);
    };
    async function request(method = 'GET', payload) {
        const response = await fetch('/api/developer/calibration', {
            method, headers: {'Content-Type': 'application/json'},
            ...(payload === undefined ? {} : {body: JSON.stringify(payload)})
        });
        if (response.status === 401 || response.status === 403) throw new Error('Sign in as Admin to calibrate.');
        if (response.status === 404) throw new Error('Developer option is disabled.');
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to save calibration.');
        return data.calibration;
    }
    function addRow(row = {min: '', max: '', label: '', category: 'Clear'}) {
        if ($('turbidity-ranges').children.length >= 20) { message('Maximum 20 ranges.', true); return; }
        const entry = document.createElement('div'); entry.className = 'calibration-row';
        for (const [key, title] of [['min', `Minimum ${rangeUnit}`], ['max', `Maximum ${rangeUnit}`], ['label', 'Display name'], ['category', 'Category']]) {
            const label = document.createElement('label'); label.textContent = title;
            const input = document.createElement(key === 'category' ? 'select' : 'input');
            input.dataset.field = key;
            if (key === 'category') {
                for (const name of categories) { const option = document.createElement('option'); option.value = name; option.textContent = name; input.append(option); }
            } else if (key === 'label') { input.type = 'text'; input.maxLength = 32; }
            else { input.type = 'number'; input.min = 0; input.max = rangeUnit === 'ADC' ? 1023 : 1000; input.step = rangeUnit === 'ADC' ? 1 : 'any'; input.inputMode = 'decimal'; }
            input.value = row[key] ?? ''; label.append(input); entry.append(label);
        }
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
        remove.addEventListener('click', () => entry.remove()); entry.append(remove); $('turbidity-ranges').append(entry);
    }
    function readRows(container) {
        return Array.from($(container).children, row => {
            const value = {};
            for (const input of row.querySelectorAll('[data-field]')) {
                value[input.dataset.field] = input.type === 'number'
                    ? (input.value === '' ? null : Number(input.value)) : input.value;
            }
            return value;
        });
    }
    function showRanges(rows) {
        $('turbidity-ranges').replaceChildren();
        for (const row of rows) addRow(row);
        $('range-unit-note').textContent = rangeUnit === 'ADC'
            ? 'ADC range 0–1023 · Lower means cloudier.'
            : 'NTU range 0–1000 · Higher means cloudier.';
    }
    function addPoint(point = {adc: '', ntu: ''}) {
        if ($('ntu-points').children.length >= 8) { message('Maximum 8 reference points.', true); return; }
        const entry = document.createElement('div'); entry.className = 'calibration-row';
        for (const [key, title] of [['adc', 'Measured ADC'], ['ntu', 'Known NTU']]) {
            const label = document.createElement('label'); label.textContent = title;
            const input = document.createElement('input'); input.type = 'number';
            input.min = 0; input.max = key === 'adc' ? 1023 : 1000; input.step = 'any';
            input.inputMode = 'decimal'; input.dataset.field = key; input.value = point[key] ?? '';
            label.append(input); entry.append(label);
        }
        const capture = document.createElement('button'); capture.type = 'button'; capture.textContent = 'Use live ADC';
        capture.addEventListener('click', () => {
            if (liveADC === null) { message('Wait for a fresh turbidity ADC reading.', true); return; }
            entry.querySelector('[data-field="adc"]').value = liveADC;
        }); entry.append(capture);
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
        remove.addEventListener('click', () => entry.remove()); entry.append(remove);
        $('ntu-points').append(entry);
    }
    function showPH() {
        const three = $('ph-points').value === '3';
        const pair = $('ph-pair').value;
        $('ph-pair-field').hidden = three;
        $('ph-pair').disabled = three;
        for (const [buffer, field] of [['401', 'ph-acid-field'], ['686', 'ph-neutral-field'], ['918', 'ph-basic-field']]) {
            const selected = three || pair.split('_').includes(buffer);
            $(field).hidden = !selected;
            $('adc' + buffer).disabled = !selected;
        }
    }
    function showNTU() {
        $('ntu-calibration-panel').hidden = !$('ntu-calibrated').checked;
    }
    function render(state, fill = false) {
        active = state.active; categories = state.categories;
        $('calibration-start').disabled = active || busy;
        $('calibration-finish').disabled = !active || busy;
        $('calibration-fields').disabled = !active || busy;
        $('calibration-mode').textContent = active ? 'Calibration mode is active.' : 'Normal monitoring.';
        const live = state.live;
        $('live-ph-adc').textContent = live.phADC ?? '—';
        liveADC = live.turbidityADC ?? null;
        $('live-turbidity-adc').textContent = liveADC ?? '—';
        let value = '—', note = 'Waiting for a fresh turbidity ADC reading.';
        if (live.turbidityNTU != null) {
            value = `${live.turbidityNTU.toFixed(2)} NTU`;
            note = 'Interpolated from your saved NTU references.';
        } else if (live.estimatedNTU != null) {
            const volts = live.turbidityVoltage.toFixed(2);
            if (live.estimateState === 'curve') {
                value = `≈ ${live.estimatedNTU.toFixed(1)} NTU`;
                note = `5 V sensor curve at ${volts} V. Add known-NTU references to calibrate this probe.`;
            } else {
                value = live.estimateState === 'cloudy_limit' ? '3000 NTU (capped estimate)' : '0 NTU (clear-end estimate)';
                note = `${volts} V is outside the 2.5–4.2 V curve. This is a display limit.`;
            }
            if (live.turbidityNTUState === 'out_of_range') note = 'Outside your saved reference range. ' + note;
        }
        $('live-turbidity-ntu').textContent = value;
        $('adc-note').textContent = note;
        if (fill) {
            $('ph-custom').checked = Boolean(state.profile.ph);
            $('ph-points').value = String(state.profile.ph?.points ?? 2);
            $('ph-pair').value = state.profile.ph?.pair ?? '686_918';
            $('adc401').value = state.profile.ph?.adc401 ?? '';
            showPH();
            $('adc686').value = state.profile.ph?.adc686 ?? '';
            $('adc918').value = state.profile.ph?.adc918 ?? '';
            $('turbidity-custom').checked = state.profile.turbidity !== null;
            defaults = state.defaults;
            rangeUnit = state.profile.turbidityUnit ?? 'ADC';
            $('turbidity-unit').value = rangeUnit;
            rangeDrafts = {};
            showRanges(state.profile.turbidity ?? defaults);
            $('ntu-calibrated').checked = Boolean(state.profile.turbidityPoints);
            $('ntu-points').replaceChildren();
            for (const point of state.profile.turbidityPoints ?? [{adc: '', ntu: ''}, {adc: '', ntu: ''}]) addPoint(point);
            showNTU();
        }
    }
    async function poll() {
        if (!busy) {
            try { const stamp = generation; const state = await request(); if (stamp === generation && !busy) { render(state, !loaded); loaded = true; } }
            catch (error) {
                $('calibration-fields').disabled = true;
                $('calibration-start').disabled = $('calibration-finish').disabled = true;
                liveADC = null;
                $('live-ph-adc').textContent = $('live-turbidity-adc').textContent = $('live-turbidity-ntu').textContent = '—';
                $('adc-note').textContent = 'Waiting for a fresh turbidity ADC reading.';
                message(error.message, true);
            }
        }
        timer = setTimeout(poll, 1500);
    }
    async function mutate(method, payload, success, fill = false) {
        if (busy) return;
        generation += 1;
        busy = true; $('calibration-fields').disabled = true;
        $('calibration-start').disabled = $('calibration-finish').disabled = true;
        try { const state = await request(method, payload); busy = false; render(state, fill); message(success); window.updateDashboard?.(); }
        catch (error) { message(error.message, true); }
        finally { busy = false; }
    }
    $('calibration-start').addEventListener('click', () => mutate('POST', {active: true}, 'Calibration started. Wait for stable ADC readings.'));
    $('calibration-finish').addEventListener('click', () => {
        if (!window.confirm('Have you saved your changes and returned both probes to the aquarium?')) return;
        mutate('POST', {active: false}, 'Calibration finished. Monitoring resumed.');
    });
    $('ph-points').addEventListener('change', showPH);
    $('ph-pair').addEventListener('change', showPH);
    $('ntu-calibrated').addEventListener('change', showNTU);
    $('add-ntu-point').addEventListener('click', () => addPoint());
    $('turbidity-unit').addEventListener('change', () => {
        rangeDrafts[rangeUnit] = readRows('turbidity-ranges');
        rangeUnit = $('turbidity-unit').value;
        showRanges(rangeDrafts[rangeUnit] ?? (rangeUnit === 'ADC' ? defaults :
            [{min: 0, max: '', label: '', category: 'Very Clear'},
             {min: '', max: 1000, label: '', category: 'Very Cloudy'}]));
        if (rangeUnit === 'NTU') {
            $('ntu-calibrated').checked = $('turbidity-custom').checked = true; showNTU();
        }
    });
    $('add-range').addEventListener('click', () => addRow());
    $('calibration-form').addEventListener('submit', event => {
        event.preventDefault();
        const ph = $('ph-custom').checked ? {points: Number($('ph-points').value)} : null;
        if (ph) {
            if (ph.points === 2) ph.pair = $('ph-pair').value;
            const buffers = ph.points === 3 ? ['401', '686', '918'] : ph.pair.split('_');
            for (const buffer of buffers) ph['adc' + buffer] = $('adc' + buffer).value === '' ? null : Number($('adc' + buffer).value);
        }
        const turbidity = $('turbidity-custom').checked ? readRows('turbidity-ranges') : null;
        const turbidityPoints = $('ntu-calibrated').checked ? readRows('ntu-points') : null;
        mutate('PUT', {ph, turbidity, turbidityUnit: rangeUnit, turbidityPoints},
            'Saved. Verify the readings, return the probes to the aquarium, then finish calibration.', true);
    });
    window.addEventListener('pagehide', () => clearTimeout(timer));
    poll();
})();
