// Display preference belongs to this browser; all API values remain Celsius.
let temperatureUnit = 'C';
try { if (localStorage.getItem('aquapi.temperatureUnit') === 'F') temperatureUnit = 'F'; } catch (_) {}
let latestDisplayData = null;
function temperatureToDisplay(value, unit = temperatureUnit) {
    return unit === 'F' ? Number(value) * 9 / 5 + 32 : Number(value);
}
function temperatureToCelsius(value, unit = temperatureUnit) {
    if (value === '' || value == null) return value;
    return unit === 'F' ? (Number(value) - 32) * 5 / 9 : Number(value);
}
function temperatureText(value) {
    const raw = String(value ?? '--');
    const number = parseSensorNumber(value);
    return number === null ? raw : temperatureToDisplay(number).toFixed(1) + ' °' + temperatureUnit;
}
function temperatureMessage(text) {
    return String(text).replace(/(-?\d+(?:\.\d+)?)\s*°C/g,
        (_, number) => temperatureToDisplay(number).toFixed(1) + '°' + temperatureUnit);
}
function displayTemperatureAlert(alert) {
    if (alert.unit !== '°C') return alert;
    return {...alert, unit: '°' + temperatureUnit,
        value: alert.value == null ? alert.value : temperatureToDisplay(alert.value),
        limit: alert.limit == null ? alert.limit : temperatureToDisplay(alert.limit),
        message: temperatureMessage(alert.message || ''),
        short_message: temperatureMessage(alert.short_message || '')};
}
function updateTemperatureLabels() {
    document.querySelectorAll('[data-temperature-unit]').forEach(el => el.textContent = '°' + temperatureUnit);
    document.querySelectorAll('[data-cooling-margin]').forEach(el => el.textContent = temperatureUnit === 'F' ? '0.9°F' : '0.5°C');
}
function setTemperatureUnit(unit) {
    if (!['C', 'F'].includes(unit)) return;
    const previous = temperatureUnit;
    temperatureUnit = unit;
    try { localStorage.setItem('aquapi.temperatureUnit', unit); } catch (_) {}
    const select = document.getElementById('temperature-unit');
    if (select) select.value = unit;
    // Preserve unsaved threshold edits when changing units.
    ['tempLimit', 'tempCriticalLimit'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.value !== '') el.value = Number(temperatureToDisplay(temperatureToCelsius(el.value, previous)).toFixed(4));
    });
    updateTemperatureLabels();
    if (latestDisplayData) {
        const card = document.getElementById('temp-val');
        if (card) card.innerText = temperatureText(latestDisplayData.temp);
        renderSmartAlerts(latestDisplayData);
    }
    if (sensorControlsState) renderSensorControls(sensorControlsState);
    Object.values(graphCharts).forEach(chart => { if (chart) chart.update(); });
}
function setupTemperatureSelector() {
    const select = document.getElementById('temperature-unit');
    if (select) {
        select.value = temperatureUnit;
        select.addEventListener('change', () => setTemperatureUnit(select.value));
    }
    updateTemperatureLabels();
}

let graphCharts = {};
let labels = [];
let tempData = [];
let phData = [];
let floatData = [];
const turbidityData = [];
let clarityLevels = [];
let cameraHeartbeatTimer = null;
let cameraViewerActive = false;
let cameraViewerId = null;
let MAX_CAMERA_VIEWERS = 3;
let MAX_WEBSITE_VIEWERS = 5;
let PREDICTION_DELAY_SECONDS = 30;
let PREDICTION_ALERT_DURATION_SECONDS = 30;

// Smooth dashboard refresh state.
// Prevents overlapping /api/sensors requests on slower Raspberry Pi systems.
let dashboardRequestRunning = false;

// -------------------------
// Smart Notification and Prediction Limits
// -------------------------
// Display and form defaults. The Flask backend is the source of all alert and prediction results.
const SENSOR_ALERT_LIMITS = {
    temperature: {
        label: "Temperature",
        unit: "\u00b0C",
        limit: 30,
        criticalLimit: 35,
        noiseTolerance: 0.05,
        minRisePerSecond: 0.005,
        dangerWord: "overheat"
    },
    turbidity: {
        label: "Turbidity",
        unit: " NTU",
        limit: 300,
        criticalLimit: 600,
        noiseTolerance: 1,
        minRisePerSecond: 0.2,
        dangerWord: "become too cloudy"
    },
    ph: {
        label: "pH Level",
        unit: " pH",
        lowLimit: 6.5,
        highLimit: 8.5,
        criticalLowLimit: 6.0,
        criticalHighLimit: 9.0,
        noiseTolerance: 0.01,
        minRisePerSecond: 0.001,
        highDangerWord: "become too alkaline",
        lowDangerWord: "become too acidic"
    }
};

let currentRole = "audience";
let currentServoPosition = { tilt: 90, pan: 110 };
let adminPanelOpen = false;
let limitsPanelOpen = false;
let manualControlsPanelOpen = true;
let feedingSchedules = [];
let feedingTimezone = "Asia/Manila";
let editingFeedingScheduleId = null;

function escapeHtml(value) {
    const element = document.createElement("span");
    element.textContent = String(value ?? "");
    return element.innerHTML;
}

function formatNumber(value) {
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatDuration(seconds) {
    const total = Math.max(1, Math.round(Number(seconds)));
    return total < 60 ? total + " sec" : (total / 60).toFixed(1) + " min";
}

function applyServerLimits(limits) {
    if (!limits) return;

    if (limits.temperature) {
        SENSOR_ALERT_LIMITS.temperature.limit = Number(limits.temperature.limit ?? SENSOR_ALERT_LIMITS.temperature.limit);
        SENSOR_ALERT_LIMITS.temperature.criticalLimit = Number(limits.temperature.overheatLimit ?? SENSOR_ALERT_LIMITS.temperature.criticalLimit);
    }
    if (limits.turbidity) {
        SENSOR_ALERT_LIMITS.turbidity.limit = Number(limits.turbidity.limit ?? SENSOR_ALERT_LIMITS.turbidity.limit);
        SENSOR_ALERT_LIMITS.turbidity.criticalLimit = Number(limits.turbidity.criticalLimit ?? SENSOR_ALERT_LIMITS.turbidity.criticalLimit);
    }
    if (limits.ph) {
        SENSOR_ALERT_LIMITS.ph.lowLimit = Number(limits.ph.lowLimit ?? SENSOR_ALERT_LIMITS.ph.lowLimit);
        SENSOR_ALERT_LIMITS.ph.highLimit = Number(limits.ph.highLimit ?? SENSOR_ALERT_LIMITS.ph.highLimit);
        SENSOR_ALERT_LIMITS.ph.criticalLowLimit = Number(limits.ph.criticalLowLimit ?? SENSOR_ALERT_LIMITS.ph.criticalLowLimit);
        SENSOR_ALERT_LIMITS.ph.criticalHighLimit = Number(limits.ph.criticalHighLimit ?? SENSOR_ALERT_LIMITS.ph.criticalHighLimit);
    }

    if (limits.maxCameraViewers !== undefined && limits.maxCameraViewers !== null) {
        MAX_CAMERA_VIEWERS = Math.max(1, Number(limits.maxCameraViewers) || MAX_CAMERA_VIEWERS);
    }

    if (limits.maxWebsiteViewers !== undefined && limits.maxWebsiteViewers !== null) {
        MAX_WEBSITE_VIEWERS = Math.max(1, Number(limits.maxWebsiteViewers) || MAX_WEBSITE_VIEWERS);
    }

    if (limits.predictionDelaySeconds !== undefined && limits.predictionDelaySeconds !== null) {
        PREDICTION_DELAY_SECONDS = Math.max(1, Number(limits.predictionDelaySeconds) || PREDICTION_DELAY_SECONDS);
    }

    if (limits.predictionAlertDurationSeconds !== undefined && limits.predictionAlertDurationSeconds !== null) {
        PREDICTION_ALERT_DURATION_SECONDS = Math.max(1, Number(limits.predictionAlertDurationSeconds) || PREDICTION_ALERT_DURATION_SECONDS);
    }

}

function setRoleUI(role, servo) {
    const wasAdmin = currentRole === "admin";
    currentRole = role || "audience";
    if (!wasAdmin && currentRole === "admin") manualControlsPanelOpen = true;
    if (servo) currentServoPosition = servo;

    const isAdmin = currentRole === "admin";
    if (!isAdmin) document.getElementById("developer-nav")?.remove();

    document.body.classList.toggle("admin-mode", isAdmin);
    document.body.classList.toggle("audience-mode", !isAdmin);

    const roleText = document.getElementById("role-text");
    if (roleText) roleText.innerText = isAdmin ? "Admin" : "Viewer · Sign in";

    const servoText = document.getElementById("servo-position-text");
    if (servoText) {
        servoText.innerText = "Tilt: " + currentServoPosition.tilt + "°, Pan: " + currentServoPosition.pan + "°";
    }

    document.querySelectorAll("button[onclick*='sendCommand']").forEach(button => {
        button.disabled = !isAdmin;
        button.title = !isAdmin ? "Admin mode required" : "";
    });
    const cameraCycles = document.getElementById('camera-feed-cycles');
    if (cameraCycles) cameraCycles.disabled = !isAdmin;

    const loginPanel = document.getElementById("admin-login-panel");
    if (loginPanel) loginPanel.style.display = isAdmin ? "none" : "grid";

    const adminControls = document.getElementById("admin-control-panel");
    if (adminControls) adminControls.style.display = isAdmin ? "grid" : "none";

    // Clearly labelled operator sections.
    const manualPanel = document.getElementById("manual-override-panel");
    if (manualPanel) {
        manualPanel.style.display =
            isAdmin ? "grid" : "none";
    }

    const manualToggle = document.getElementById("manual-controls-toggle");
    if (manualToggle) manualToggle.hidden = !isAdmin;

    // Advanced settings expand on demand.
    const limitPanel = document.getElementById("admin-limit-panel");
    if (limitPanel) {
        limitPanel.style.display = isAdmin ? "grid" : "none";
    }

    const limitsToggle = document.getElementById("custom-limits-toggle");
    if (limitsToggle) limitsToggle.hidden = !isAdmin;

    const rolePanel = document.getElementById("role-panel");
    if (rolePanel) rolePanel.hidden = false;
    document.querySelectorAll('[data-admin-hint]').forEach(el => el.hidden = isAdmin);
    const logout = document.getElementById('logout-admin');
    if (logout) logout.hidden = !isAdmin;
    const feederAccess = document.getElementById("feeder-access-hint");
    if (feederAccess) feederAccess.hidden = isAdmin;

    const fanPanel = document.getElementById('fan-mode-actions');
    if (fanPanel) fanPanel.hidden = !isAdmin;
    renderFan(fanState);
    if (latestFeeder) renderFeeder(latestFeeder);
    const feederPanel = document.getElementById("custom-feeder");
    if (feederPanel) feederPanel.hidden = !isAdmin;
    const scheduleForm = document.getElementById("feeding-schedule-form");
    if (scheduleForm) scheduleForm.hidden = !isAdmin;

    document.querySelectorAll(".schedule-admin-actions").forEach(element => {
        element.hidden = !isAdmin;
    });

    if (sensorControlsState) renderSensorControls(sensorControlsState);
    if (!isAdmin) {
        limitsPanelOpen = false;
        manualControlsPanelOpen = false;
    }
}

function fillLimitInputs(limits) {
    if (!limits) return;
    const values = {
        tempLimit: limits.temperature?.limit == null ? undefined : temperatureToDisplay(limits.temperature.limit),
        tempCriticalLimit: limits.temperature?.overheatLimit == null ? undefined : temperatureToDisplay(limits.temperature.overheatLimit),
        phLowLimit: limits.ph?.lowLimit,
        phHighLimit: limits.ph?.highLimit,
        phCriticalLowLimit: limits.ph?.criticalLowLimit,
        phCriticalHighLimit: limits.ph?.criticalHighLimit,
        maxCameraViewers: limits.maxCameraViewers,
        maxWebsiteViewers: limits.maxWebsiteViewers,
        maxAdminSessions: limits.maxAdminSessions,
        predictionDelaySeconds: limits.predictionDelaySeconds,
        predictionAlertDurationSeconds: limits.predictionAlertDurationSeconds
    };

    Object.entries(values).forEach(([id, value]) => {
        const input = document.getElementById(id);
        if (input && value !== undefined && value !== null) input.value = value;
    });
}

function injectRolePanel() {
    document.getElementById('admin-login-panel')?.addEventListener('submit', event => { event.preventDefault(); loginAdmin(); });
    document.getElementById('logout-admin')?.addEventListener('click', logoutAdmin);
    document.getElementById('save-limits')?.addEventListener('click', saveLimits);
}

function showModeMessage(message) {
    const msg = document.getElementById("mode-message");
    if (msg) msg.innerText = message;
}

function loginAdmin() {
    const pin = document.getElementById("admin-pin")?.value || "";
    fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin })
    })
        .then(async res => {
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Login failed");
            return data;
        })
        .then(() => {
            showModeMessage("Signed in.");
            document.getElementById("admin-pin").value = "";
            adminPanelOpen = true;
            return loadAuthStatus();
        })
        .catch(err => showModeMessage(err.message));
}

function logoutAdmin() {
    fetch("/api/auth/logout", { method: "POST" })
        .then(() => { showModeMessage("Signed out."); return loadAuthStatus(); })
        .catch(err => console.error("Logout failed:", err));
}

function saveLimits() {
    const payload = {
        tempLimit: temperatureToCelsius(document.getElementById("tempLimit")?.value),
        tempCriticalLimit: temperatureToCelsius(document.getElementById("tempCriticalLimit")?.value),
        phLowLimit: document.getElementById("phLowLimit")?.value,
        phHighLimit: document.getElementById("phHighLimit")?.value,
        phCriticalLowLimit: document.getElementById("phCriticalLowLimit")?.value,
        phCriticalHighLimit: document.getElementById("phCriticalHighLimit")?.value,
        maxWebsiteViewers: document.getElementById("maxWebsiteViewers")?.value,
        maxAdminSessions: document.getElementById("maxAdminSessions")?.value,
        maxCameraViewers: document.getElementById("maxCameraViewers")?.value,
        predictionDelaySeconds: document.getElementById("predictionDelaySeconds")?.value,
        predictionAlertDurationSeconds: document.getElementById("predictionAlertDurationSeconds")?.value
    };

    fetch("/api/limits", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
        .then(async res => {
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Could not save limits");
            return data;
        })
        .then(data => {
            applyServerLimits(data.limits);
            fillLimitInputs(data.limits);
            showModeMessage("Settings saved.");
            updateDashboard();
        })
        .catch(err => showModeMessage(err.message));
}

function showFeedingScheduleMessage(message, isError = false) {
    const element = document.getElementById("feeding-schedule-message");
    if (!element) return;
    element.innerText = message || "";
    element.classList.toggle("error", Boolean(isError));
}

function formatFeedingTime(timeValue) {
    const [hour, minute] = String(timeValue).split(":").map(Number);
    const value = new Date(2000, 0, 1, hour, minute);
    return value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatFeedingDateTime(isoValue) {
    if (!isoValue) return "No automatic feeding scheduled";
    const value = new Date(isoValue);
    const options = {
        weekday: "long",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZone: feedingTimezone
    };
    try {
        return "Next: " + value.toLocaleString([], options);
    } catch (_error) {
        delete options.timeZone;
        return "Next: " + value.toLocaleString([], options);
    }
}

function formatLastFeeding(schedule) {
    if (!schedule.lastTriggeredAt) return "No serving yet";
    if (!["complete", "success"].includes(schedule.lastStatus)) return "Last attempt: " + (schedule.lastStatus || "pending");
    const value = new Date(Number(schedule.lastTriggeredAt) * 1000);
    const options = {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZone: feedingTimezone
    };
    try {
        return "Last fed " + value.toLocaleString([], options);
    } catch (_error) {
        delete options.timeZone;
        return "Last fed " + value.toLocaleString([], options);
    }
}

function renderFeedingSchedules() {
    const list = document.getElementById("feeding-schedule-list");
    if (!list) return;

    if (!feedingSchedules.length) {
        list.innerHTML = `
            <div class="schedule-empty">
                <i class="fas fa-calendar-plus"></i>
                <span>No schedules yet.</span>
            </div>
        `;
        return;
    }

    list.innerHTML = feedingSchedules.map(schedule => {
        const enabledClass = schedule.enabled ? "enabled" : "paused";
        const enabledText = schedule.enabled ? "Active" : "Paused";
        const toggleText = schedule.enabled ? "Pause" : "Enable";
        const actionsHidden = currentRole === "admin" ? "" : " hidden";

        return `
            <article class="feeding-schedule-item ${enabledClass}" data-schedule-id="${Number(schedule.id)}">
                <div class="schedule-day-icon"><i class="fas fa-fish"></i></div>
                <div class="schedule-details">
                    <strong>${escapeHtml(schedule.dayName)} at ${escapeHtml(formatFeedingTime(schedule.time))}</strong>
                    <span>${Number(schedule.cycles)} cycles per serving • ${escapeHtml(formatLastFeeding(schedule))}</span>
                </div>
                <span class="schedule-state ${enabledClass}">${enabledText}</span>
                <div class="schedule-admin-actions"${actionsHidden}>
                    <button type="button" data-schedule-action="edit" title="Edit schedule">Edit</button>
                    <button type="button" data-schedule-action="toggle" title="${toggleText} schedule">${toggleText}</button>
                    <button type="button" data-schedule-action="delete" class="danger" title="Delete schedule">Delete</button>
                </div>
            </article>
        `;
    }).join("");
}

function applyFeedingScheduleResponse(data) {
    feedingSchedules = Array.isArray(data.schedules) ? data.schedules : [];
    feedingTimezone = data.timezone || feedingTimezone;
    renderFeeder(data.feeder);

    const activeScheduleCount = feedingSchedules.filter(schedule => schedule.enabled).length;
    const feedMode = document.getElementById("feed-mode-val");
    if (feedMode) {
        feedMode.innerText = activeScheduleCount > 0
            ? activeScheduleCount + " Scheduled"
            : "Manual";
    }

    const timezoneText = document.getElementById("feeding-timezone-text");
    if (timezoneText) timezoneText.innerText = "Timezone: " + feedingTimezone;

    const nextText = document.getElementById("next-feeding-text");
    if (nextText) {
        if (data.feeder?.paused) {
            nextText.innerText = "Schedule paused";
        } else if (Number(data.postponed || 0) > 0) {
            nextText.innerText = data.postponed + " serving(s) waiting for pumps / feeder connection";
        } else {
            nextText.innerText = formatFeedingDateTime(data.nextRun?.dateTime);
        }
    }

    renderFeedingSchedules();
}

function loadFeedingSchedules() {
    if (!["index", "feeding"].includes(document.body.dataset.page)) return Promise.resolve();
    return fetch("/api/feeding-schedules")
        .then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not load feeding schedules");
            return data;
        })
        .then(data => applyFeedingScheduleResponse(data))
        .catch(error => showFeedingScheduleMessage(error.message, true));
}

function resetFeedingScheduleForm() {
    editingFeedingScheduleId = null;
    const form = document.getElementById("feeding-schedule-form");
    if (form) form.reset();
    const timeInput = document.getElementById("feeding-time");
    if (timeInput) timeInput.value = "08:00";
    const saveButton = document.getElementById("save-feeding-schedule");
    if (saveButton) saveButton.innerHTML = '<i class="fas fa-plus"></i> Add Schedule';
    const cancelButton = document.getElementById("cancel-feeding-edit");
    if (cancelButton) cancelButton.hidden = true;
}

function saveFeedingSchedule(event) {
    event.preventDefault();
    if (currentRole !== "admin") {
        showFeedingScheduleMessage("Admin mode is required to change schedules.", true);
        return;
    }

    const payload = {
        day: Number(document.getElementById("feeding-day")?.value),
        time: document.getElementById("feeding-time")?.value,
        cycles: Number(document.getElementById("feeding-cycles")?.value),
        enabled: true
    };
    const isEditing = editingFeedingScheduleId !== null;
    const url = isEditing
        ? "/api/feeding-schedules/" + editingFeedingScheduleId
        : "/api/feeding-schedules";

    if (isEditing) {
        const existing = feedingSchedules.find(item => item.id === editingFeedingScheduleId);
        payload.enabled = existing ? existing.enabled : true;
    }

    fetch(url, {
        method: isEditing ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not save feeding schedule");
            return data;
        })
        .then(data => {
            applyFeedingScheduleResponse(data);
            showFeedingScheduleMessage(isEditing ? "Feeding schedule updated." : "Feeding schedule added.");
            resetFeedingScheduleForm();
        })
        .catch(error => showFeedingScheduleMessage(error.message, true));
}

function editFeedingSchedule(schedule) {
    editingFeedingScheduleId = schedule.id;
    document.getElementById("feeding-day").value = String(schedule.day);
    document.getElementById("feeding-time").value = schedule.time;
    document.getElementById("feeding-cycles").value = schedule.cycles;
    document.getElementById("save-feeding-schedule").innerHTML = '<i class="fas fa-save"></i> Save Changes';
    document.getElementById("cancel-feeding-edit").hidden = false;
    document.getElementById("feeding-day").focus();
}

function updateFeedingScheduleEnabled(schedule) {
    fetch("/api/feeding-schedules/" + schedule.id, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !schedule.enabled })
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not update feeding schedule");
            return data;
        })
        .then(data => {
            applyFeedingScheduleResponse(data);
            showFeedingScheduleMessage(schedule.enabled ? "Schedule paused." : "Schedule enabled.");
        })
        .catch(error => showFeedingScheduleMessage(error.message, true));
}

function deleteFeedingSchedule(schedule) {
    if (!window.confirm("Delete the " + schedule.dayName + " " + formatFeedingTime(schedule.time) + " schedule?")) return;

    fetch("/api/feeding-schedules/" + schedule.id, { method: "DELETE" })
        .then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not delete feeding schedule");
            return data;
        })
        .then(data => {
            applyFeedingScheduleResponse(data);
            showFeedingScheduleMessage("Feeding schedule deleted.");
            if (editingFeedingScheduleId === schedule.id) resetFeedingScheduleForm();
        })
        .catch(error => showFeedingScheduleMessage(error.message, true));
}

function handleFeedingScheduleAction(event) {
    const button = event.target.closest("button[data-schedule-action]");
    if (!button || currentRole !== "admin") return;
    const item = button.closest("[data-schedule-id]");
    const scheduleId = Number(item?.dataset.scheduleId);
    const schedule = feedingSchedules.find(candidate => candidate.id === scheduleId);
    if (!schedule) return;

    const action = button.dataset.scheduleAction;
    if (action === "edit") editFeedingSchedule(schedule);
    if (action === "toggle") updateFeedingScheduleEnabled(schedule);
    if (action === "delete") deleteFeedingSchedule(schedule);
}

function renderWaterControl(status) {
    if (!status) return;

    const statusElement = document.getElementById("water-control-status");

    let text = "Automatic control ready";

    if (status.mode === "manual_extract") text = "MANUAL: extraction pump ON — no sensor limit";
    else if (status.mode === "manual_insert") text = "MANUAL: insertion pump ON — no sensor limit";
    else if (status.mode === "manual_both") text = "MANUAL: both pumps ON — no sensor limit";
    else if (status.mode === "manual_idle") text = "Manual override active — pumps stopped";
    else if (status.mode === "auto_extract_turbidity") text = "AUTO: replacing cloudy water";
    else if (status.mode === "auto_extract_ph") {
        text = status.phCondition === "acidic"
            ? "AUTO: correcting acidic water"
            : "AUTO: correcting alkaline water";
    }
    else if (status.mode === "auto_extract_overflow") text = "AUTO: extracting until water level is Normal";
    else if (status.mode === "auto_insert") text = "AUTO: inserting until water level is Normal";

    if (!status.manualOverride && status.levelCorrection === "overflow") text += " • Overflow: drain only until Normal";
    else if (!status.manualOverride && status.levelCorrection === "low") text += " • Low water: refill only until Normal";

    const pending = status.pendingQuality || [];
    const pendingText = pending.length ? 'Checking ' + pending.map(item =>
        (item.sensor === 'ph' ? 'pH' : 'water clarity') + ' (' + item.seconds + ' / ' + status.confirmationSeconds + ' s)').join(', ') : '';
    if (pendingText && !status.pumpActive && !status.manualOverride) text = pendingText;
    const note = document.getElementById('quality-confirmation-status');
    if (note) { note.textContent = pendingText; note.hidden = !pendingText; }
    if (statusElement) {
        statusElement.innerText = text;
        statusElement.classList.toggle("active", Boolean(status.pumpActive));
        statusElement.classList.toggle("manual", Boolean(status.manualOverride));
    }

    const pumpMode = document.getElementById("pump-mode-val");
    if (pumpMode) {
        if (status.extractionOn && status.insertionOn) pumpMode.innerText = "Both pumps";
        else if (status.extractionOn) pumpMode.innerText = "Extracting";
        else if (status.insertionOn) pumpMode.innerText = "Inserting";
        else if (status.manualOverride) pumpMode.innerText = "Manual Stop";
        else pumpMode.innerText = pending.length ? "Checking readings" : "Auto Ready";
    }

    document.body.classList.toggle("water-pump-active", Boolean(status.pumpActive));
}

function sendWaterAction(action) {
    if (currentRole !== "admin") {
        alert("Admin Mode is required for manual water-pump control.");
        return;
    }

    fetch("/api/water-control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action })
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Water-control command failed");
            return data;
        })
        .then(data => {
            renderWaterControl(data.waterControl);
            updateDashboard();
        })
        .catch(error => alert(error.message));
}

function renderAdminSessions(state) {
    const label = document.getElementById("admin-session-text");
    if (label && state) label.textContent = "Admin sessions: " + state.active + " / " + state.max;
}

function loadAuthStatus(fillForm = true) {
    return fetch("/api/auth/status", {cache: "no-store"})
        .then(res => res.json())
        .then(data => {
            applyServerLimits(data.limits);
            if (fillForm) fillLimitInputs(data.limits);
            setRoleUI(data.role, data.servo);
            if (data.developerOptions && data.role === 'admin' && !document.getElementById('developer-nav')) {
                const link = document.createElement('a'); link.id = 'developer-nav';
                link.href = '/developer'; link.innerHTML = '<i class="ico ico-developer" aria-hidden="true"></i> Developer option';
                document.querySelector('.app-nav')?.append(link);
            }
            renderAdminSessions(data.adminSessions);
            renderWaterControl(data.waterControl);
        })
        .catch(err => console.error("Auth status failed:", err));
}

function sendCommand(command) {
    if (currentRole !== "admin") {
        alert("Audience mode is view-only. Enter Admin Mode to use camera, feeder, or pump controls.");
        return;
    }

    if (command === "feed") {
        const input = document.getElementById('camera-feed-cycles');
        return feederAction("feed", input ? Number(input.value) : 1);
    }
    fetch("/api/control", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ command: command })
    })
    .then(async res => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Command failed");
        return data;
    })
    .then(data => {
        console.log("Command response:", data);
        setRoleUI(currentRole, data.servo);
    })
    .catch(err => alert(err.message));
}

function reloadCamera() {
    const img = document.getElementById("camera-stream");
    if (img) {
        img.src = "/camera_feed?t=" + new Date().getTime();
    }
}

function getCameraViewerId() {
    if (cameraViewerId) return cameraViewerId;

    cameraViewerId =
        "viewer-" +
        Date.now() +
        "-" +
        Math.random().toString(16).slice(2);

    return cameraViewerId;
}

function getCameraFeedUrl() {
    return "/camera_feed?viewerId=" +
        encodeURIComponent(getCameraViewerId()) +
        "&t=" +
        new Date().getTime();
}

function postCameraViewerEvent(url) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            viewerId: getCameraViewerId()
        })
    });
}

function cameraViewerStart() {
    if (cameraViewerActive) return;

    postCameraViewerEvent("/api/camera/viewer/start")
        .then(async res => {
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Could not start camera stream");
            return data;
        })
        .then(data => {
            cameraViewerActive = true;
            const img = document.getElementById("camera-stream");
            const loading = document.getElementById("camera-loading");
            if (loading) loading.classList.remove("hidden");
            if (img) {
                img.onload = () => loading?.classList.add("hidden");
                img.onerror = () => {
                    if (loading) loading.innerHTML = '<i class="fas fa-triangle-exclamation"></i> Camera reconnecting…';
                };
                img.src = getCameraFeedUrl();
            }
            console.log("Camera viewer started. Active viewers:", data.viewers);

            cameraHeartbeatTimer = setInterval(() => {
                postCameraViewerEvent("/api/camera/viewer/heartbeat")
                    .then(async res => {
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.error || "Camera stream ended");
                        return data;
                    })
                    .catch(err => {
                        console.error("Camera heartbeat failed:", err);
                        cameraViewerStop();
                    });
            }, 3000);
        })
        .catch(err => showModeMessage(err.message));
}

function cameraViewerStop() {
    if (!cameraViewerActive) return;

    cameraViewerActive = false;

    if (cameraHeartbeatTimer) {
        clearInterval(cameraHeartbeatTimer);
        cameraHeartbeatTimer = null;
    }

    const img = document.getElementById("camera-stream");
    if (img) {
        img.removeAttribute("src");
    }

    const payload = JSON.stringify({
        viewerId: getCameraViewerId()
    });

    navigator.sendBeacon(
        "/api/camera/viewer/stop",
        new Blob([payload], {
            type: "application/json"
        })
    );
}

function reloadCamera() {
    const img = document.getElementById("camera-stream");
    const loading = document.getElementById("camera-loading");
    if (loading) {
        loading.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Reconnecting to camera…';
        loading.classList.remove("hidden");
    }
    if (img) img.src = getCameraFeedUrl();
}

function initCameraViewerTracking() {
    const cameraStream = document.getElementById("camera-stream");
    if (!cameraStream) return;

    cameraViewerStart();

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            cameraViewerStop();
        } else {
            cameraViewerStart();
        }
    });

    window.addEventListener("beforeunload", cameraViewerStop);
    window.addEventListener("pagehide", cameraViewerStop);
}

function releaseWebsiteViewerSlot() {
    try {
        navigator.sendBeacon(
            "/api/viewers/stop",
            new Blob(["{}"], { type: "application/json" })
        );
    } catch (err) {
        console.debug("Could not release website viewer slot:", err);
    }
}

function makeChart(canvasId, label, dataArray) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    // Small native Canvas chart; no CDN or third-party script needed offline.
    const colors = {tempChart: '#e68136', phChart: '#169b74', floatChart: '#3877dc', turbidityChart: '#7757c5'};
    const chart = {update() {
        const rect = canvas.getBoundingClientRect();
        const width = Math.max(240, rect.width), height = 240;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const pixelWidth = Math.round(width * ratio), pixelHeight = Math.round(height * ratio);
        if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
            canvas.width = pixelWidth;
            canvas.height = pixelHeight;
        }
        const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);
        const style = getComputedStyle(canvas);
        const labelColor = style.getPropertyValue('--text');
        const clarity = canvasId === 'turbidityChart';
        const categories = canvasId === 'floatChart' ? ['Very low','Low','Normal','Overflow'] : clarity ? clarityLevels : null;
        const displayData = canvasId === 'tempChart' ? dataArray.map(v => v === null ? null : temperatureToDisplay(v)) : dataArray;
        const valid = displayData.filter(v => v !== null && Number.isFinite(v));
        ctx.font = '12px Arial'; ctx.textAlign = 'left'; ctx.fillStyle = labelColor;
        if (canvasId === 'tempChart') ctx.fillText('Temperature (°' + temperatureUnit + ')', 55, 12);
        if (clarity) {
            const latest = displayData[displayData.length - 1];
            const reading = clarityLevels[latest] || 'Waiting for clarity';
            document.getElementById('clarity-plot-status').textContent = reading;
            canvas.setAttribute('aria-label', 'Turbidity history, ' + reading);
        }
        if (!valid.length) { ctx.fillText('Waiting for readings…', 20, 40); return; }
        let lo = categories ? 0 : Math.min(...valid), hi = categories ? categories.length - 1 : Math.max(...valid);
        const pad = Math.max((hi - lo) * 0.15, 0.1);
        if (!categories) { lo -= pad; hi += pad; }
        const left = categories ? 88 : 55, right = width - 14, top = 20, bottom = 204;
        const y = v => hi === lo ? (bottom + top) / 2 : bottom - (v - lo) / (hi - lo) * (bottom - top);
        const x = i => left + i / Math.max(dataArray.length - 1, 1) * (right - left);
        const ticks = categories ? categories.length - 1 : 4;
        for (let i = 0; i <= ticks; i++) {
            const value = categories ? i : lo + (hi - lo) * i / ticks;
            if (clarity && categories) {
                const band = (bottom - top) / Math.max(ticks, 1);
                const bandTop = Math.max(top, y(value) - band / 2);
                const bands = ['--raised', '--good-soft', '--warn-soft', '--prediction-soft', '--danger-soft'];
                ctx.fillStyle = style.getPropertyValue(bands[Math.round(i * 4 / Math.max(ticks, 1))]);
                ctx.fillRect(left, bandTop, right - left, Math.min(bottom, y(value) + band / 2) - bandTop);
            }
            ctx.strokeStyle = style.getPropertyValue('--line'); ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
            if (i === ticks || i % Math.max(1, Math.ceil(ticks / 8)) === 0) {
                ctx.fillStyle = labelColor;
                ctx.fillText(categories ? categories[i] : value.toFixed(1), 3, y(value) + 4, left - 9);
            }
        }
        ctx.strokeStyle = colors[canvasId]; ctx.lineWidth = 2; ctx.beginPath();
        let previous = null;
        displayData.forEach((v, i) => {
            if (v === null || !Number.isFinite(v)) { previous = null; return; }
            if (previous === null) ctx.moveTo(x(i), y(v));
            else { if (categories) ctx.lineTo(x(i), y(previous)); ctx.lineTo(x(i), y(v)); }
            previous = v;
        }); ctx.stroke();
        displayData.forEach((v, i) => { if (v !== null && Number.isFinite(v)) {ctx.fillStyle = colors[canvasId]; ctx.beginPath(); ctx.arc(x(i),y(v),2,0,Math.PI*2);ctx.fill();} });
        ctx.fillStyle = labelColor;
        ctx.fillText(labels[0] || '', left, 229);
        ctx.textAlign = 'right'; ctx.fillText(labels[labels.length-1] || '', right, 229);
        if (!clarity) canvas.setAttribute('aria-label', label + (canvasId === 'tempChart' ? ' °' + temperatureUnit : '') + ', latest reading: ' + valid[valid.length-1]);
    }};
    window.addEventListener('resize', () => chart.update());
    chart.update();
    return chart;
}

function setupCharts() {
    graphCharts.temp = makeChart("tempChart", "Temperature", tempData);
    graphCharts.ph = makeChart("phChart", "pH Level", phData);
    graphCharts.float = makeChart("floatChart", "Water Level", floatData);
    graphCharts.turbidity = makeChart("turbidityChart", "Turbidity", turbidityData);
}

function formatWaterLevelText(value) {
    if (value === undefined || value === null) return "--";

    const rawText = String(value).trim();
    if (!rawText || rawText === "--") return "--";

    const text = rawText.toLowerCase().replace(/[_-]/g, " ").trim();
    if (text.includes("overflow")) return "Water Level is Overflowing";
    if (text.includes("very low")) return "Water Level is Very Low";
    if (text.includes("normal")) return "Water Level is Normal";
    if (text === "low" || text.includes("water level is low")) return "Water Level is Low";
    if (["on", "1", "true", "yes", "detected", "water detected"].includes(text)) return "Water Level Detected";
    if (["off", "0", "false", "no", "empty"].includes(text)) return "Water Level is Very Low";
    return rawText;
}

function parseWaterLevelState(value) {
    const text = String(value ?? "").toLowerCase();
    if (text.includes("overflow")) return 3;
    if (text.includes("normal")) return 2;
    if (text.includes("very low")) return 0;
    if (text.includes("low")) return 1;
    if (text.includes("detected") || ["on", "1", "true", "yes"].includes(text.trim())) return 2;
    if (["off", "0", "false", "no"].includes(text.trim())) return 0;
    return null;
}

function parseSensorNumber(value) {
    if (value === undefined || value === null) return null;

    const text = String(value).toLowerCase().trim();

    if (text.includes("below") && text.includes("cm")) return 0;
    if ((text.includes("in") || text.includes("at") || text.includes("above")) && text.includes("cm")) return 1;
    if (["on", "1", "true", "yes"].includes(text)) return 1;
    if (["off", "0", "false", "no"].includes(text)) return 0;

    if (text.includes("full") || text.includes("high") || text.includes("ok")) return 1;
    if (text.includes("empty") || text.includes("low")) return 0;
    if (text.includes("clear") || text.includes("clean")) return 0;
    if (text.includes("dirty") || text.includes("cloudy") || text.includes("muddy")) return 1;

    const match = text.match(/-?\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : null;
}

function normalizeTurbidityForAlerts(value) {
    if (value === undefined || value === null) return null;

    const text = String(value).toLowerCase().trim();

    if (text.includes("dirty") || text.includes("cloudy") || text.includes("muddy")) {
        return SENSOR_ALERT_LIMITS.turbidity.limit;
    }

    if (text.includes("clear") || text.includes("clean")) return 0;

    return parseSensorNumber(value);
}

function getAlertIconClass(alert) {
    const sensor = String(alert.sensor || "").toLowerCase();

    if (sensor.includes("temp")) return "fa-temperature-high";
    if (sensor.includes("turbidity")) return "fa-water";
    if (sensor.includes("ph")) return "fa-vial";

    return "fa-triangle-exclamation";
}

function getSafeSeverity(severity) {
    const normalized = String(severity || "warning").toLowerCase();

    // "danger" is accepted only for backward compatibility with older cached data.
    if (normalized === "danger" || normalized === "critical") return "critical";
    if (normalized === "prediction" || normalized === "info") return "prediction";
    if (normalized === "safe" || normalized === "normal") return "normal";

    return "warning";
}

function injectStatusLegend() {
    if (document.getElementById("status-legend")) return;

    const alertPanel = document.querySelector(".smart-alert-panel");
    if (!alertPanel) return;

    const legend = document.createElement("div");
    legend.id = "status-legend";
    legend.className = "status-legend";
    legend.setAttribute("aria-label", "Sensor status guide");
    legend.innerHTML = `
        <div class="status-guide normal">
            <strong><i class="fas fa-circle-check"></i> Normal</strong>
            <span>Within the safe range</span>
        </div>
        <div class="status-guide warning">
            <strong><i class="fas fa-triangle-exclamation"></i> Warning</strong>
            <span>Needs attention</span>
        </div>
        <div class="status-guide critical">
            <strong><i class="fas fa-circle-exclamation"></i> Critical</strong>
            <span>Needs immediate action</span>
        </div>
        <div class="status-guide prediction">
            <strong><i class="fas fa-chart-line"></i> Prediction</strong>
            <span>Trend may reach a limit</span>
        </div>
    `;

    const head = alertPanel.querySelector(".smart-alert-head");
    if (head) {
        head.insertAdjacentElement("afterend", legend);
    } else {
        alertPanel.prepend(legend);
    }
}

function renderSmartAlerts(data) {
    const alertList = document.getElementById("alert-list");
    const alertSummary = document.getElementById("alert-summary");
    const alertUpdated = document.getElementById("alert-updated");

    if (!alertList || !alertSummary) return;

    // Alerts, including trend predictions, are calculated by Python and returned by /api/sensors.
    const alerts = Array.isArray(data.alerts) ? data.alerts.map(displayTemperatureAlert) : [];
    const criticalCount = alerts.filter(alert => getSafeSeverity(alert.severity) === "critical").length;
    const predictionCount = alerts.filter(alert => getSafeSeverity(alert.severity) === "prediction").length;

    if (data.sensorControls?.allDisabled) {
        alertSummary.className = "alert-summary";
        alertSummary.textContent = "—";
        alertList.innerHTML = "";
    } else if (data.connection?.stale) {
        alertSummary.className = "alert-summary warning";
        alertSummary.textContent = "—";
        alertList.innerHTML = "";
    } else if (alerts.length === 0) {
        alertSummary.className = "alert-summary normal";
        alertSummary.textContent = "No active alerts";
        alertList.innerHTML = "";
    } else {
        const summaryClass = criticalCount > 0 ? "critical" : predictionCount > 0 ? "prediction" : "warning";
        const notificationWord = alerts.length === 1 ? "notification" : "notifications";
        const predictionText = predictionCount > 0 ? " including " + predictionCount + " prediction" + (predictionCount > 1 ? "s" : "") : "";

        alertSummary.className = "alert-summary " + summaryClass;
        alertSummary.innerHTML = "<i class=\"fas fa-bell\"></i><span>" + alerts.length + " active " + notificationWord + predictionText + ".</span>";

        alertList.innerHTML = alerts.map(alert => {
            const severity = getSafeSeverity(alert.severity);
            const valueText = alert.sensor !== "Turbidity" && Number.isFinite(Number(alert.value))
                ? "<span><i class=\"fas fa-gauge-high\"></i> " + escapeHtml((['°C', '°F'].includes(alert.unit) ? Number(alert.value).toFixed(1) : formatNumber(Number(alert.value)))) + escapeHtml(alert.unit || "") + "</span>"
                : "";
            const limitText = alert.sensor !== "Turbidity" && Number.isFinite(Number(alert.limit))
                ? "<span><i class=\"fas fa-sliders\"></i> Limit: " + escapeHtml((['°C', '°F'].includes(alert.unit) ? Number(alert.limit).toFixed(1) : formatNumber(Number(alert.limit)))) + escapeHtml(alert.unit || "") + "</span>"
                : "";
            const lastsSeconds = alert.lasts_seconds ?? alert.lastsSeconds;
            const durationText = Number.isFinite(Number(lastsSeconds))
                ? "<span><i class=\"fas fa-clock\"></i> Lasts: " + escapeHtml(formatDuration(Number(lastsSeconds))) + "</span>"
                : "";

            return "<article class=\"sensor-alert " + severity + "\">" +
                "<div class=\"sensor-alert-icon\"><i class=\"fas " + getAlertIconClass(alert) + "\"></i></div>" +
                "<div class=\"sensor-alert-body\">" +
                    "<div class=\"sensor-alert-top\">" +
                        "<strong>" + escapeHtml(alert.title || alert.sensor || "Sensor Alert") + "</strong>" +
                        "<span class=\"alert-pill\">" + escapeHtml(severity) + "</span>" +
                    "</div>" +
                    "<p>" + escapeHtml(alert.short_message || alert.message || "Sensor value needs attention.") + "</p>" +
                    "<div class=\"alert-meta\">" + valueText + limitText + durationText + "</div>" +
                "</div>" +
            "</article>";
        }).join("");
    }

    if (alertUpdated) {
        alertUpdated.innerText = "Updated " + new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        });
    }
}

async function updateDashboard() {
    // Do not stack requests if the Raspberry Pi or browser is still processing
    // the previous dashboard refresh.
    if (dashboardRequestRunning) return;

    dashboardRequestRunning = true;

    try {
        const response = await fetch("/api/sensors", {
            cache: "no-store"
        });

        if (!response.ok) {
            throw new Error("Sensor API returned " + response.status);
        }

        const data = await response.json();
        if (sensorControlsState && data.sensorControls &&
            data.sensorControls.session === sensorControlsState.session &&
            data.sensorControls.revision < sensorControlsState.revision) return;

        applyServerLimits(data.limits);

        if (data.role) {
            setRoleUI(data.role, data.servo);
            renderAdminSessions(data.adminSessions);
        }
        renderSensorControls(data.sensorControls);
        renderWaterControl(data.waterControl);
        renderFan(data.fan);
        renderFeeder(data.feeder, data.waterControl);

        const viewerText = document.getElementById("website-viewer-text");
        if (viewerText && data.websiteViewers) {
            const viewerValue =
                "Website viewers: " +
                data.websiteViewers.active +
                " / " +
                data.websiteViewers.max;

            if (viewerText.innerText !== viewerValue) {
                viewerText.innerText = viewerValue;
            }
        }

        latestDisplayData = data;
        const waterLevelText = formatWaterLevelText(data.float_stat);
        const signal = document.getElementById('reef-signal');
        if (signal) {
            const live = !data.connection?.stale && !data.sensorControls?.allDisabled;
            signal.textContent = data.sensorControls?.allDisabled ? 'Sensors off' : live ? 'Live' : 'Offline';
            signal.dataset.live = String(live);
        }

        const tempNumber = data.connection?.stale ? null : parseSensorNumber(data.temp);
        const phNumber = data.connection?.stale ? null : parseSensorNumber(data.ph);
        const floatNumber = data.connection?.stale ? null : parseWaterLevelState(waterLevelText);
        const levels = data.clarityLabels || [];
        const scaleChanged = levels.length !== clarityLevels.length || levels.some((name, i) => name !== clarityLevels[i]);
        if (scaleChanged) {
            clarityLevels = levels;
            turbidityData.fill(null);
        }
        const categoryIndex = clarityLevels.indexOf(data.turbidity);
        const turbidityNumber = data.connection?.stale || categoryIndex < 0 ? null : categoryIndex;

        if (signal) window.dispatchEvent(new CustomEvent('aquapi-readings', {detail: {
            data, scaleChanged, values: {temp:tempNumber, ph:phNumber, water:floatNumber, clarity:turbidityNumber}
        }}));
        renderSmartAlerts(data);

        const hasGraphPage =
            Object.values(graphCharts).some(chart => chart !== null);

        if (!hasGraphPage) return;

        const label = new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        });

        labels.push(label);
        tempData.push(tempNumber);
        phData.push(phNumber);
        floatData.push(floatNumber);
        turbidityData.push(turbidityNumber);

        const MAX_GRAPH_POINTS = 30;

        while (labels.length > MAX_GRAPH_POINTS) {
            labels.shift();
            tempData.shift();
            phData.shift();
            floatData.shift();
            turbidityData.shift();
        }

        Object.values(graphCharts).forEach(chart => {
            if (chart) chart.update();
        });
    } catch (err) {
        const signal = document.getElementById('reef-signal');
        if (signal) {
            signal.textContent = 'Offline'; signal.dataset.live = 'false';
            window.dispatchEvent(new CustomEvent('aquapi-readings', {detail: {
                data:{connection:{stale:true}}, values:{temp:null, ph:null, water:null, clarity:null}
            }}));
        }
        console.error("Sensor update failed:", err);
    } finally {
        dashboardRequestRunning = false;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    injectRolePanel();
    const themeSelect = document.getElementById('display-theme');
    if (themeSelect) {
        themeSelect.value = document.documentElement.dataset.theme || 'dark';
        themeSelect.addEventListener('change', () => {
            document.documentElement.dataset.theme = themeSelect.value;
            try { localStorage.setItem('aquapi.theme', themeSelect.value); } catch (_) {}
        });
    }
    setupTemperatureSelector();
    injectSensorControls();
    injectStatusLegend();
    setupCharts();
    initCameraViewerTracking();
    document.getElementById("feeding-schedule-form")?.addEventListener("submit", saveFeedingSchedule);
    document.getElementById("cancel-feeding-edit")?.addEventListener("click", resetFeedingScheduleForm);
    document.getElementById("feeding-schedule-list")?.addEventListener("click", handleFeedingScheduleAction);
    resetFeedingScheduleForm();
    loadFeedingSchedules();
    loadAuthStatus();
    updateDashboard();
    setInterval(() => { if (!document.hidden) updateDashboard(); }, 2000);
    setInterval(loadFeedingSchedules, 30000);
    // Keep a signed-in background tab alive without depending on sensor polling.
    setInterval(() => loadAuthStatus(false), 30000);
    window.addEventListener("pagehide", releaseWebsiteViewerSlot);
});


let feederRequestRunning = false;
function showFeederMessage(text) {
    for (const id of ['feeder-message', 'camera-feeder-message']) {
        const element = document.getElementById(id);
        if (element) element.textContent = text;
    }
}
async function feederAction(action, count) {
    if (currentRole !== 'admin' || feederRequestRunning) return;
    const cycles = count ?? Number(document.getElementById('manual-feed-cycles')?.value);
    if (action === 'feed' && (!Number.isSafeInteger(cycles) || cycles < 1 || cycles > 4294967295)) {
        showFeederMessage('Enter a whole number of cycles from 1 to 4294967295.');
        return;
    }
    feederRequestRunning = true;
    try {
        const response = await fetch('/api/feeder', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, cycles})});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Feeder request failed');
        showFeederMessage(action === 'feed' ? 'Feeding request sent.' : 'Request processed.');
        renderFeeder(data.feeder);
        loadFeedingSchedules();
    } catch (error) {
        showFeederMessage(error.message);
    } finally { feederRequestRunning = false; }
}

let latestFeeder = null;
let latestWater = null;
function renderFeeder(state, water) {
    if (!state) return;
    latestFeeder = state;
    if (water) latestWater = water;
    const job = state.active || state.last;
    const statusNames = {sent:'Starting', running:'Feeding', complete:'Completed', cancelled:'Stopped', unknown:'Check feeder', failed:'Failed'};
    const text = !state.ready ? 'Feeder disconnected' : job
        ? (statusNames[job.status] || job.status) + ' · ' + job.cycles + ' cycle' + (job.cycles === 1 ? '' : 's')
        : 'Ready';
    for (const id of ['feeder-status','camera-feeder-status']) {
        const el = document.getElementById(id);
        if (el) el.textContent = text + (state.paused ? ' · Schedule paused' : '');
    }
    if (job && ['running','complete','cancelled','unknown','failed'].includes(job.status)) showFeederMessage('');
    const disabled = currentRole !== 'admin' || !state.ready || state.busy || Boolean(latestWater?.pumpActive);
    const feed = document.getElementById('feed-custom');
    if (feed) feed.disabled = disabled;
    document.querySelectorAll(`button[onclick*="sendCommand('feed')"]`).forEach(b => b.disabled = disabled);
}

// One shared panel on localhost, LAN, remote dashboard, camera and graph pages.
let sensorControlsState = null;
let sensorControlBusy = false;
function injectSensorControls() {
    if (document.getElementById('sensor-controls')) return;
    const mount = document.getElementById('sensor-controls-mount');
    if (!mount) return;
    const panel = document.createElement('section');
    panel.id = 'sensor-controls';
    panel.className = 'card sensor-controls';
    panel.innerHTML = `<h2>Sensors</h2><p id="sensor-disabled-count"></p>
        <p id="sensor-controls-access">Sign in as Admin to change sensors.</p>
        <div id="sensor-control-rows" class="sensor-control-grid"></div>
        <p id="sensor-sync-status"></p><p id="sensor-controls-message" role="status" aria-live="polite"></p>
        `;
    mount.appendChild(panel);
    const banner = document.createElement('p');
    banner.id = 'sensor-disabled-banner';
    banner.setAttribute('role', 'status');
    banner.hidden = true;
    panel.parentElement.appendChild(banner);
}
function renderSensorControls(state) {
    if (!state || (sensorControlsState && state.session === sensorControlsState.session && state.revision < sensorControlsState.revision)) return;
    sensorControlsState = state;
    const rows = document.getElementById('sensor-control-rows');
    if (!rows) return;
    Object.entries(state.sensors).forEach(([key, item]) => {
        let row = document.getElementById('sensor-row-' + key);
        if (!row) {
            row = document.createElement('div');
            row.id = 'sensor-row-' + key;
            row.className = 'sensor-control-row';
            row.innerHTML = '<strong></strong><span class="sensor-state"></span><p></p><button type="button" class="mode-btn"></button>';
            row.querySelector('button').addEventListener('click', () => toggleSensor(key));
            rows.appendChild(row);
        }
        row.querySelector('strong').textContent = item.label;
        const value = key === 'temp' ? temperatureText(item.reading) : typeof item.reading === 'boolean' ? (item.reading ? 'Water detected' : 'No water detected') : item.reading;
        row.querySelector('.sensor-state').textContent = item.status.toUpperCase() + (item.status === 'active' ? ' • ' + value : '');
        row.querySelector('p').textContent = '';
        row.title = item.effect;
        row.classList.toggle('sensor-disabled', !item.enabled);
        const button = row.querySelector('button');
        button.textContent = item.enabled ? 'Disable' : 'Enable';
        button.setAttribute('aria-label', button.textContent + ' ' + item.label);
        button.disabled = currentRole !== 'admin' || sensorControlBusy;
    });
    const disabled = state.disabled.map(key => state.sensors[key].label);
    document.getElementById('sensor-disabled-count').textContent = disabled.length ? '— ' + disabled.length + ' disabled' : '— all enabled';
    const banner = document.getElementById('sensor-disabled-banner');
    banner.hidden = !disabled.length;
    banner.textContent = 'Disabled: ' + disabled.join(', ') + '.';
    document.getElementById('sensor-controls-access').textContent = currentRole === 'admin' ? '' : 'Sign in as Admin to change sensors.';
    document.getElementById('sensor-sync-status').textContent = (state.inputApplied ? '' : 'Sensor settings syncing…') +
        (state.coolerApplied ? '' : ' Temperature control syncing…');
}
async function toggleSensor(key) {
    if (currentRole !== 'admin' || sensorControlBusy || !sensorControlsState) return;
    const enabled = !sensorControlsState.sensors[key].enabled;
    sensorControlBusy = true;
    renderSensorControls(sensorControlsState);
    const message = document.getElementById('sensor-controls-message');
    try {
        const response = await fetch('/api/sensor-controls', {method: 'PUT',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: {[key]: enabled}})});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Could not save sensor setting.');
        renderSensorControls(data.sensorControls);
        message.textContent = data.sensorControls.sensors[key].label + (enabled ? ' enabled.' : ' disabled.');
        await updateDashboard();
    } catch (error) { message.textContent = error.message; }
    finally { sensorControlBusy = false; renderSensorControls(sensorControlsState); }
}


let fanState = null;
let fanRequestRunning = false;
function renderFan(state) {
    fanState = state || fanState;
    const current = fanState;
    let text = 'Fan disconnected';
    if (current?.available) {
        text = current.mode + ' • Fan ' + (current.on ? 'ON' : 'OFF');
        if (!current.confirmed) text += ' · Starting';
        else if (current.pending && current.pending !== 'NONE') text += ' • Checking temperature for 5 seconds';
    }
    document.querySelectorAll('[data-fan-status]').forEach(el => el.textContent = text);
    document.querySelectorAll('[data-fan-mode]').forEach(button => {
        button.disabled = currentRole !== 'admin' || !current?.available || fanRequestRunning;
        button.setAttribute('aria-pressed', String(current?.mode === button.dataset.fanMode));
    });
}
async function setFanMode(mode) {
    if (currentRole !== 'admin' || fanRequestRunning) return;
    fanRequestRunning = true;
    renderFan(fanState);
    const message = document.getElementById('fan-message');
    try {
        const response = await fetch('/api/fan', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not change fan mode');
        renderFan(data.fan);
        if (message) message.textContent = 'Request sent.';
    } catch (err) {
        if (message) message.textContent = err.message;
    } finally { fanRequestRunning = false; renderFan(fanState); }
}

(() => {
    const button = document.getElementById('nav-toggle');
    const nav = document.getElementById('main-nav');
    if (!button || !nav) return;
    document.body.classList.add('nav-ready');
    function toggle(open) {
        button.setAttribute('aria-expanded', String(open));
        nav.classList.toggle('menu-open', open);
    }
    button.addEventListener('click', () => toggle(button.getAttribute('aria-expanded') === 'false'));
    document.addEventListener('click', event => {
        if (!nav.contains(event.target) && !button.contains(event.target)) toggle(false);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') {
            toggle(false);
            button.focus();
        }
    });
})();
