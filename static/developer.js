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
            ? 'Whole-number ranges cover 0–1023, including both endpoints. Low ADC is cloudy; high ADC is clear.'
            : 'Ranges cover 0–1000 NTU. Each maximum equals the next minimum; a shared boundary belongs to the higher range. Higher NTU is cloudier. Enter your tested clarity limits.';
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
        $('live-turbidity-ntu').textContent = live.turbidityNTU === null || live.turbidityNTU === undefined
            ? ({not_calibrated: 'Not calibrated', out_of_range: 'Outside reference range'}[live.turbidityNTUState] || '—')
            : `${live.turbidityNTU.toFixed(2)} NTU`;
        $('adc-note').textContent = !live.fresh ? 'Waiting for fresh ADC data. Check the Mega connection.' :
            'Enable the sensors to read ADC. Measurements pause during level-only or manual pumping.';
        if (fill) {
            $('ph-custom').checked = Boolean(state.profile.ph);
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
        mutate('POST', {active: false}, 'Calibration finished. pH correction will wait for the settling period.');
    });
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
        const ph = $('ph-custom').checked ? {adc686: Number($('adc686').value), adc918: Number($('adc918').value)} : null;
        const turbidity = $('turbidity-custom').checked ? readRows('turbidity-ranges') : null;
        const turbidityPoints = $('ntu-calibrated').checked ? readRows('ntu-points') : null;
        mutate('PUT', {ph, turbidity, turbidityUnit: rangeUnit, turbidityPoints},
            'Saved. Verify the readings, return the probes to the aquarium, then finish calibration.', true);
    });
    window.addEventListener('pagehide', () => clearTimeout(timer));
    poll();
})();
