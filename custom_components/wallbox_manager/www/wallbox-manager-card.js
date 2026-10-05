(() => {
if (customElements.get("wallbox-manager-card")) return;
/* Stable roles and backend scope joins survive entity and device renames. */
function discoverWallboxManager(states) {
  const entries = Object.entries(states || {});
  const selector = entries.find(([, s]) => s.attributes.wallbox_manager_role === "active_wallbox" && s.state !== "unavailable");
  const inventory = selector?.[1].attributes.wallboxes || {};
  const active = selector?.[1].attributes.active_wallbox;
  const keys = Object.keys(inventory);
  const displayed = active && inventory[active] ? active : (!active && keys.length === 1 ? keys[0] : null);
  const roles = {}, ranks = {};
  for (const [id, state] of entries) {
    const a = state.attributes, role = a.wallbox_manager_role;
    const rank = a.wallbox_manager_target === displayed ? 3 : a.wallbox_manager_targets?.includes(displayed) ? (a.wallbox_manager_scope === "evse" ? 2 : 1) : 0;
    if (displayed && role && rank > (ranks[role] || 0)) { roles[role] = id; ranks[role] = rank; }
  }
  return {selector: selector?.[0], state: selector?.[1], inventory, active, displayed, roles, multiple: keys.length > 1};
}

function powerStep(value, direction, maximum = null, minimum = 0) {
  // Integer decimal arithmetic prevents 9.8 + 0.1 becoming 9.899999… .
  const step = value < 10 || (value === 10 && direction < 0) ? 0.1 : 1;
  let next = Math.round((value + direction * step) * 1e6) / 1e6;
  if ((value < 10 && next > 10) || (value > 10 && next < 10)) next = 10;
  return Math.max(minimum, maximum === null ? next : Math.min(maximum, next));
}
function parseInput(value, language) {
  const decimal = new Intl.NumberFormat(language).formatToParts(1.1).find(p => p.type === "decimal").value;
  const normalized = String(value).trim().replace(decimal, ".");
  return /^\d+(?:\.\d+)?$/.test(normalized) ? Number(normalized) : NaN;
}
function available(state) { return state && !["unknown", "unavailable", ""].includes(state.state); }
function durationAvailable(state) { return state && typeof state.state === "string" && (state.state === "" || /^[0-9]{2,}:[0-5][0-9]$/.test(state.state)); }
// Absolute backend deadlines; round up so a live interval never shows zero early.
function gridCountdown(attrs, id, now = Date.now() / 1000) {
  const start = attrs.grid_start_deadline, end = attrs.grid_end_deadline;
  if (attrs.grid_timing_state === "stopping" || (end != null && now >= end)) return "";
  const seconds = id === "grid_start_delay" ? Math.max(0, start - now) :
    end == null ? null : now < start ? attrs.grid_armed_duration_seconds : end - now;
  if (seconds == null || seconds <= 0) return "";
  const minutes = Math.ceil(seconds / 60);
  return `${String(Math.floor(minutes / 60)).padStart(2,"0")}:${String(minutes % 60).padStart(2,"0")}`;
}
const optimumFields = {optimum_lower_soc:["Lower target SoC","Unterer Ziel-SoC","%"], optimum_upper_soc:["Upper target SoC","Oberer Ziel-SoC","%"], optimum_max_discharge_w:["Maximum storage discharge","Maximale Speicher-Entladeleistung","W"], estimated_daily_house_consumption_kwh:["Daily household consumption (without EV)","Täglicher Hausverbrauch (ohne EV)","kWh/d"]};
function numberRole(id) { return id === "power" ? "soll_power" : id === "reserve" ? "min_soc" : id; }
function fresh(state, now = Date.now()) {
  return available(state) && state.attributes.connected !== false &&
    (!state.attributes.observed_at || Date.parse(state.attributes.observed_at) <= now) &&
    Number.isFinite(Date.parse(state.attributes.valid_until)) && Date.parse(state.attributes.valid_until) > now;
}
function sessionDuration(state, connected, now) {
  if (!available(state) || state.attributes.start_known === false) return "—";
  const factors = {s:1,min:60,h:3600,d:86400,ms:0.001,"µs":0.000001};
  let seconds = Number(state.state) * factors[state.attributes.unit_of_measurement];
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (state.attributes.session_active === true) {
    const sampled = Date.parse(state.attributes.duration_sampled_at || state.last_updated);
    const deadline = state.attributes.duration_valid_until ? Date.parse(state.attributes.duration_valid_until) : sampled + 90000;
    if (!connected || state.attributes.connected === false || !Number.isFinite(sampled) || sampled > now || !(now < deadline)) return "—";
    seconds += (now - sampled) / 1000;
  } else if (state.attributes.session_active !== false) return "—";
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2,"0")}`;
}

function liveValues(states, discovery, language, now = Date.now()) {
  const state = role => states[discovery.roles[role]];
  const de = language?.startsWith("de"), empty = "—";
  const number = value => new Intl.NumberFormat(language, {maximumFractionDigits: 1}).format(value);
  const connected = discovery.inventory[discovery.displayed]?.connected;
  const text = (role, labels) => available(state(role)) && (state(role).attributes.state_represents === "last_known_observation" || (connected && state(role).attributes.connected !== false && (!state(role).attributes.valid_until || fresh(state(role),now)))) ? (labels[state(role).state] || empty) : empty;
  const connection = text("connector_state", de ? {available:"Frei", occupied:"Belegt", reserved:"Reserviert", faulted:"Störung"} : {available:"Available", occupied:"Occupied", reserved:"Reserved", faulted:"Faulted"});
  const charging = text("charging_state", de ? {idle:"Bereit",connected:"Verbunden",preparing:"Vorbereitung",charging:"Lädt",suspended_vehicle:"Vom Fahrzeug pausiert",suspended_station:"Von Wallbox pausiert",finishing:"Beendet"} : {idle:"Idle",connected:"Connected",preparing:"Preparing",charging:"Charging",suspended_vehicle:"Paused by vehicle",suspended_station:"Paused by wallbox",finishing:"Finishing"});
  const session = role => connected && available(state(role)) && state(role).attributes.connected !== false && state(role).attributes.session_active === true ? Number(state(role).state) : NaN;
  const energy = fresh(state("session_energy"),now) ? session("session_energy") : NaN;
  const powerState = state("power");
  const power = connected && fresh(powerState, now) ? Number(powerState.state) / (powerState.attributes.unit_of_measurement === "kW" ? 1 : 1000) : NaN;
  const applied = state("charging_profile")?.attributes || {};
  const phases = applied.applied_phase_count, current = applied.applied_current_a;
  let actual = empty;
  if (connected && available(state("charging_profile")) && applied.profile_control_ready && discovery.state?.attributes.profile_control_ready && discovery.active === discovery.displayed && applied.actual_enabled === true && applied.control_authority === "remote" && typeof current === "number" && Number.isFinite(current)) {
    if ([1,2,3].includes(phases) && current > 0) actual = `${phases}${de ? "-phasig" : "-phase"} · ${number(current)} A`;
    else if (phases === null && current === 0) actual = `${de ? "Aus" : "Off"} · 0 A`;
  }
  return {connection,charging,energy:Number.isFinite(energy) ? `${number(energy)} kWh` : empty,
    power:Number.isFinite(power) ? `${number(power)} kW` : empty,
    duration:sessionDuration(state("session_duration"),connected,now), actual};
}

class WallboxManagerCard extends HTMLElement {
  setConfig(config) {
    this.stopHold();
    this.config = config;
    this.edits = {};
    this.durationEdits = {};
    if (!this.shadowRoot) this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML = `<style>
      :host {display:block;color:var(--primary-text-color)}
      ha-card {padding:16px;font-family:var(--paper-font-body1_-_font-family,inherit)}
      header {display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:12px}
      ha-icon {--mdc-icon-size:48px;width:48px;height:48px;color:var(--primary-color);flex:none} .heading {min-width:0;flex:1}
      h2 {overflow-wrap:anywhere;font-size:var(--ha-card-header-font-size,20px);font-weight:500;margin:0;line-height:1.3}
      #station {font-size:14px;font-weight:400;color:var(--secondary-text-color);margin-top:3px;overflow-wrap:anywhere}
      .row {display:flex;align-items:center;justify-content:space-between;gap:12px;margin:10px 0;font-size:14px}
      input,select,button {font:inherit;color:var(--primary-text-color);border:1px solid var(--divider-color);background:var(--card-background-color);border-radius:var(--ha-border-radius-sm,8px);box-sizing:border-box;min-height:40px}
      select {padding:6px 8px;max-width:55%} #wallbox-row {margin:0;max-width:48%} #wallbox {max-width:100%;width:100%}
      .numeric {display:flex;align-items:center;gap:4px;flex:none} input {width:4em;text-align:center;padding:6px 3px;font-variant-numeric:tabular-nums}
      #grid_start_delay-label,#grid_duration-label {min-width:0;overflow-wrap:anywhere}
      .duration-editor {display:flex;align-items:center;gap:4px;flex:none} .duration-editor input {text-align:left;width:4em;padding:6px 4px;appearance:auto} .duration-editor .clear {padding:0 6px} .duration-editor .colon {font-weight:600}
      .step {width:34px;padding:0;font-size:19px;touch-action:none;user-select:none;-webkit-user-select:none} .unit {color:var(--secondary-text-color);font-size:13px;width:2em}
      button {cursor:pointer} button:hover:not(:disabled) {background:var(--secondary-background-color)}
      :is(input,select,button):focus-visible {outline:2px solid var(--primary-color);outline-offset:2px}
      :disabled {opacity:.5;cursor:default} #permission,#takeover {width:100%;padding:9px 12px;margin-top:6px;font-size:14px}
      #permission {background:var(--primary-color);color:var(--text-primary-color);border-color:var(--primary-color);font-weight:500}
      #permission:hover:not(:disabled) {filter:brightness(.95)}
      .live {border-top:1px solid var(--divider-color);padding-top:12px;margin-top:16px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px 16px}
      .caption {font-size:12px;color:var(--secondary-text-color);margin-bottom:3px} .reading {font-size:14px;line-height:1.4;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
      .section {border-top:1px solid var(--divider-color);padding-top:12px;margin-top:16px} h3 {font-size:14px;font-weight:500;margin:0 0 8px} #messages:has(.notice:not([hidden])) #no-messages {display:none} .notice {font-size:13px;color:var(--error-color);line-height:1.4;margin:12px 0 0}
      [hidden] {display:none!important}
      @media(max-width:360px) {ha-card {padding:12px} .row {gap:8px} header {gap:8px;flex-wrap:wrap} #wallbox-row {max-width:100%;width:100%} .numeric {gap:2px} .step {width:32px}}
    </style><ha-card>
      <header><ha-icon icon="mdi:ev-station"></ha-icon><div class="heading"><h2 id="title"></h2><div id="station"></div></div><label id="wallbox-row"><select id="wallbox" aria-label="Active wallbox"></select></label></header>
      <button id="takeover"></button>
      <label class="row" id="profile-row"><span id="profile-label"></span><select id="profile"></select></label>
      <div class="row" id="power-row"><label id="power-label" for="power"></label><div class="numeric"><button id="power-down" class="step" type="button">−</button><input id="power" type="text" inputmode="decimal" autocomplete="off"><button id="power-up" class="step" type="button">+</button><span class="unit">kW</span></div></div>
      <div class="row" id="reserve-row"><label id="reserve-label" for="reserve"></label><div class="numeric"><button id="reserve-down" class="step" type="button">−</button><input id="reserve" type="text" inputmode="numeric" autocomplete="off"><button id="reserve-up" class="step" type="button">+</button><span class="unit">%</span></div></div>
      <label class="row" id="approximation-row"><span id="approximation-label"></span><select id="approximation"></select></label>
      <div class="row" id="soll_soc_speicher-row"><label id="soll_soc_speicher-label" for="soll_soc_speicher"></label><div class="numeric"><button id="soll_soc_speicher-down" class="step" type="button">−</button><input id="soll_soc_speicher" type="text" inputmode="numeric" autocomplete="off"><button id="soll_soc_speicher-up" class="step" type="button">+</button><span class="unit">%</span></div></div>
      ${["grid_start_delay", "grid_duration"].map(id => `<div class="row" id="${id}-row"><span id="${id}-label"></span><div class="duration-editor" role="group" aria-labelledby="${id}-label"><input type="number" id="${id}-hours" min="0" step="1" placeholder="—"><span class="colon">:</span><input type="number" id="${id}-minutes" min="0" max="59" step="1" placeholder="—"><button type="button" class="clear" id="${id}-clear">×</button></div></div>`).join("")}
      ${Object.entries(optimumFields).map(([id,labels]) => `<div class="row" id="${id}-row"><label id="${id}-label" for="${id}"></label><div class="numeric"><input id="${id}" type="number" min="0" step="${id === "estimated_daily_house_consumption_kwh" ? "0.1" : "1"}"><span class="unit">${labels[2]}</span></div></div>`).join("")}
      <div class="row" id="optimum-target-row"><span id="optimum-target-label"></span><span id="optimum-target"></span></div>
      <button id="permission"></button>
      <div class="live"><div><div class="caption" id="connection-label"></div><div class="reading" id="connection"></div></div><div><div class="caption" id="charging-label"></div><div class="reading" id="charging"></div></div><div><div class="caption" id="energy-label"></div><div class="reading" id="energy"></div></div><div><div class="caption" id="live-power-label"></div><div class="reading" id="live-power"></div></div><div><div class="caption" id="duration-label"></div><div class="reading" id="duration"></div></div></div>
      <section class="section" aria-labelledby="parameters-label"><h3 id="parameters-label"></h3><div class="reading" id="actual"></div></section>
      <section class="section" id="messages" aria-labelledby="messages-label"><h3 id="messages-label"></h3><div class="reading" id="no-messages">-</div><p id="status" class="notice" role="status" hidden></p><p id="error" class="notice" role="alert" hidden></p></section>
    </ha-card>`;
    const get = id => this.shadowRoot.getElementById(id);
    get("wallbox").onchange = e => this.activate(e.target.value);
    get("takeover").onclick = () => this.activate(this.discovery.displayed);
    get("profile").onchange = e => this.call("select", "select_option", {entity_id:this.discovery.roles.charging_profile, option:e.target.value});
    get("approximation").onchange = e => this.call("select", "select_option", {entity_id:this.discovery.roles.pv_approximation, option:e.target.value});
    for (const id of ["grid_start_delay", "grid_duration"]) {
      for (const part of ["hours", "minutes"]) {
        get(`${id}-${part}`).onchange = () => this.editDuration(id);
        get(`${id}-${part}`).onkeydown = e => {
          if (e.key === "Enter") { e.preventDefault(); this.editDuration(id); }
        };
      }
      get(`${id}-clear`).onclick = () => {
        if (get(`${id}-clear`).disabled) return;
        get(`${id}-hours`).value = get(`${id}-minutes`).value = "";
        this.editDuration(id);
      };
    }
    for (const id of ["power", "reserve", "soll_soc_speicher"]) {
      get(id).onchange = () => this.editNumber(id);
      get(id).onkeydown = e => {
        if (["ArrowUp", "ArrowDown"].includes(e.key)) { e.preventDefault(); this.stepNumber(id, e.key === "ArrowUp" ? 1 : -1); }
        if (e.key === "Enter") { e.preventDefault(); this.editNumber(id); }
      };
      for (const [suffix, direction] of [["down",-1],["up",1]]) {
        const button = get(`${id}-${suffix}`);
        button.onpointerdown = e => this.startHold(button,id,direction,e);
        for (const event of ["pointerup","pointercancel","pointerleave","lostpointercapture"]) button[`on${event}`] = () => {
          if (this.hold?.button === button) this.stopHold();
        };
        button.onpointermove = e => {
          if (this.hold?.button !== button) return;
          const rect = button.getBoundingClientRect();
          if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) this.stopHold();
        };
        button.oncontextmenu = e => e.preventDefault();
        button.onclick = e => {
          // Native keyboard/assistive clicks have detail 0 and no pointer press.
          const pointer = button._pointerStep && e?.detail !== 0;
          button._pointerStep = false;
          if (!pointer && !button.disabled) this.stepNumber(id,direction);
        };
      }
    }
    for (const id of Object.keys(optimumFields)) {
      get(id).onchange = () => {
        const input = get(id), value = Number(input.value);
        if (input.disabled || input.value === "" || !input.checkValidity() || !Number.isFinite(value)) return;
        this.call("number", "set_value", {entity_id:this.discovery.roles[id], value});
      };
    }
    get("permission").onclick = () => {
      const id = this.discovery.roles.charging_enabled;
      this.call("switch", this._hass.states[id]?.state === "on" ? "turn_off" : "turn_on", {entity_id:id});
    };
    if (this._hass) this.hass = this._hass;
  }
  connectedCallback() {
    this.disconnectedCallback();
    this._blur = () => this.stopHold();
    window.addEventListener("blur",this._blur);
    // Expire displayed meter samples even when HA has no new state to push.
    this.timer = setInterval(() => { if (this._hass) this.hass = this._hass; },1000);
  }
  disconnectedCallback() {
    this.stopHold();
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this._blur) window.removeEventListener("blur",this._blur);
    this._blur = null;
  }
  startHold(button, id, direction, event) {
    if (button.disabled || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault();
    this.stopHold();
    button._pointerStep = true;
    button.focus({preventScroll:true});
    button.setPointerCapture(event.pointerId);
    const hold = this.hold = {button,id,pointerId:event.pointerId,timer:null};
    const repeat = () => {
      if (this.hold !== hold) return;
      if (button.disabled) { this.stopHold(); return; }
      this.stepNumber(id,direction);
      if (this.hold === hold) hold.timer = setTimeout(repeat,150);
    };
    this.stepNumber(id,direction);
    if (this.hold === hold) hold.timer = setTimeout(repeat,450);
  }
  stopHold() {
    const hold = this.hold;
    this.hold = null;
    if (!hold) return;
    clearTimeout(hold.timer);
    if (hold.button.hasPointerCapture(hold.pointerId)) hold.button.releasePointerCapture(hold.pointerId);
  }
  format(value) { return new Intl.NumberFormat(this._hass.language, {maximumFractionDigits:6,useGrouping:false}).format(value); }
  minimum(id) {
    const value = id === "power" ? this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_min_kw : 0;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
  }
  maximum(id) {
    if (id === "reserve") return 100;
    if (id === "soll_soc_speicher") return 99;
    const value = this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_max_kw;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }
  stepNumber(id, direction) {
    const input = this.shadowRoot.getElementById(id);
    if (input.disabled) return;
    const value = parseInput(input.value, this._hass.language);
    if (!Number.isFinite(value) || (id !== "power" && !Number.isInteger(value))) return;
    const next = id === "power" ? powerStep(value,direction,this.maximum(id),this.minimum(id)) : Math.min(this.maximum(id),Math.max(0,Math.round(value) + direction));
    if (next === value) return;
    input.value = this.format(next);
    return this.editNumber(id);
  }
  async editNumber(id) {
    const input = this.shadowRoot.getElementById(id), get = key => this.shadowRoot.getElementById(key);
    if (input.disabled) return;
    const value = parseInput(input.value,this._hass.language), maximum = this.maximum(id);
    const de = this._hass.language?.startsWith("de");
    if (!Number.isFinite(value) || value < this.minimum(id) || (maximum !== null && value > maximum) || (id !== "power" && !Number.isInteger(value))) {
      get("error").textContent = de ? `Bitte einen gültigen Wert ab ${this.format(this.minimum(id))}${maximum === null ? "" : ` bis ${this.format(maximum)}`} eingeben${id !== "power" ? " (ganze Prozent)" : ""}.` : `Enter a valid value from ${this.format(this.minimum(id))}${maximum === null ? "" : ` to ${this.format(maximum)}`}${id !== "power" ? " (whole percent)" : ""}.`;
      get("error").hidden = false;
      if (id === "soll_soc_speicher") input.value = this.format(Math.round(Number(this._hass.states[this.discovery.roles[id]].state)));
      return;
    }
    input.value = this.format(value);
    const entity = this.discovery.roles[numberRole(id)];
    if ((!this.edits[id] && available(this._hass.states[entity]) && Number(this._hass.states[entity].state) === value) || (this.edits[id]?.entity === entity && this.edits[id].value === value)) return;
    const edit = this.edits[id] = {entity,value};
    input.value = this.format(value);
    get("error").hidden = true;
    this.hass = this._hass;
    try { await this._hass.callService("number","set_value",{entity_id:entity,value}); }
    catch (err) {
      if (this.edits[id] === edit && this.discovery.roles[numberRole(id)] === entity) {
        delete this.edits[id];
        input.value = this.format(id === "soll_soc_speicher" ? Math.round(Number(this._hass.states[entity].state)) : Number(this._hass.states[entity].state));
        get("error").textContent = err.message || String(err); get("error").hidden = false;
      }
    }
    finally { this.hass = this._hass; }
  }
  async editDuration(id) {
    const get = key => this.shadowRoot.getElementById(key);
    const hours = get(`${id}-hours`), minutes = get(`${id}-minutes`);
    if (hours.disabled || minutes.disabled) return;
    const valid = (input, max = Number.MAX_SAFE_INTEGER) => !input.validity?.badInput &&
      (input.value === "" || (/^[0-9]+$/.test(input.value) && Number.isSafeInteger(Number(input.value)) && Number(input.value) <= max));
    if (!valid(hours) || !valid(minutes,59)) {
      get("error").textContent = this._hass.language?.startsWith("de") ? "Ganze Stunden ab 0 und Minuten von 0 bis 59 eingeben." : "Enter whole hours from 0 and minutes from 0 to 59.";
      get("error").hidden = false;
      return;
    }
    const value = hours.value === "" && minutes.value === "" ? "" : `${String(Number(hours.value || 0)).padStart(2,"0")}:${String(Number(minutes.value || 0)).padStart(2,"0")}`;
    const entity = this.discovery.roles[id];
    const edit = this.durationEdits[id] = {entity,value};
    get("error").hidden = true;
    this.hass = this._hass;
    try { await this._hass.callService("text","set_value",{entity_id:entity,value}); }
    catch (err) {
      if (this.durationEdits[id] === edit && this.discovery.roles[id] === entity) {
        delete this.durationEdits[id];
        get("error").textContent = err.message || String(err); get("error").hidden = false;
      }
    }
    finally { this.hass = this._hass; }
  }
  activate(option) {
    if (option && this.discovery.selector) return this.call("select", "select_option", {entity_id:this.discovery.selector,option});
  }
  async call(domain, service, data) {
    const error = this.shadowRoot.getElementById("error");
    error.textContent = ""; error.hidden = true;
    this.busy = true; this.hass = this._hass;
    try { await this._hass.callService(domain,service,data); }
    catch (err) { error.textContent = err.message || String(err); error.hidden = false; }
    finally { this.busy = false; this.hass = this._hass; }
  }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const get = id => this.shadowRoot.getElementById(id), de = hass.language?.startsWith("de");
    const previous = this.discovery?.displayed;
    const d = this.discovery = discoverWallboxManager(hass.states);
    if (previous !== d.displayed) { this.stopHold(); this.edits = {}; this.durationEdits = {}; get("error").hidden = true; }
    const state = role => hass.states[d.roles[role]], attrs = state("charging_profile")?.attributes || {};
    const ready = !!(attrs.profile_control_ready && d.state?.attributes.profile_control_ready && d.active === d.displayed);
    const busy = !!(this.busy || d.state?.attributes.transition_pending);
    const permission = state("charging_enabled"), enabled = permission?.state === "on";
    get("title").textContent = this.config.name || "Wallbox Manager";
    get("station").textContent = d.inventory[d.displayed]?.display_name || d.inventory[d.displayed]?.name || "—";
    get("wallbox-row").hidden = !d.multiple;
    get("wallbox").ariaLabel = de ? "Aktive Wallbox" : "Active wallbox";
    this.options(get("wallbox"), [["",de ? "Wallbox auswählen" : "Select wallbox"], ...Object.entries(d.inventory).map(([key,value]) => [key,value.name])]);
    get("wallbox").value = d.active || ""; get("wallbox").disabled = busy;
    get("takeover").textContent = de ? "Steuerung übernehmen" : "Take control";
    get("takeover").hidden = ready || !d.displayed; get("takeover").disabled = busy || !d.inventory[d.displayed]?.connected;
    get("profile-label").textContent = de ? "Ladeprofil" : "Charging profile";
    get("power-label").textContent = de ? "Angeforderte Ladeleistung" : "Requested charging power";
    get("reserve-label").textContent = de ? "Entladereserve" : "Discharge reserve";
    get("soll_soc_speicher-label").textContent = de ? "Speicher-Ziel-SoC" : "Battery target SoC";
    this.options(get("profile"), (state("charging_profile")?.attributes.options || []).map(value => [value,value === "NETZ" ? (de ? "Netz" : "Grid") : value === "PV_SURPLUS" ? (de ? "PV-Überschuss" : "PV Surplus") : value === "PV_OPTIMUM" ? "PV Optimum" : value]));
    get("profile").value = state("charging_profile")?.state || "";
    get("profile-row").hidden = (state("charging_profile")?.attributes.options || []).length <= 1;
    get("profile").disabled = busy || !available(state("charging_profile"));
    for (const [id,role] of [["power","soll_power"],["reserve","min_soc"],["soll_soc_speicher","soll_soc_speicher"]]) {
      const s = state(role), edit = this.edits[id];
      if (edit && (edit.entity !== d.roles[role] || (available(s) && Number(s.state) === edit.value))) delete this.edits[id];
      if (previous !== d.displayed || this.shadowRoot.activeElement !== get(id)) get(id).value = this.edits[id] ? this.format(this.edits[id].value) : available(s) ? this.format(id === "soll_soc_speicher" ? Math.round(Number(s.state)) : Number(s.state)) : "";
      const disabled = busy || !available(s);
      get(id).disabled = disabled;
      const value = parseInput(get(id).value,hass.language), maximum = this.maximum(id);
      for (const suffix of ["up","down"]) {
        get(`${id}-${suffix}`).disabled = disabled || !Number.isFinite(value) || (suffix === "down" ? value <= this.minimum(id) : maximum !== null && value >= maximum);
        get(`${id}-${suffix}`).ariaLabel = `${get(`${id}-label`).textContent} ${suffix === "up" ? (de ? "erhöhen" : "increase") : (de ? "verringern" : "decrease")}`;
      }
    }
    get("permission").disabled = busy || !ready || !available(permission);
    get("permission").textContent = !ready ? (enabled ? (de ? "Ladefreigabe aktiv · keine Steuerung" : "Charging permission enabled · no control") : (de ? "Ladefreigabe inaktiv · keine Steuerung" : "Charging permission disabled · no control")) : busy ? (de ? "Bitte warten …" : "Please wait …") : enabled ? (de ? "Ladefreigabe deaktivieren" : "Disable charging permission") : (de ? "Laden freigeben" : "Enable charging permission");
    const optimum = state("charging_profile")?.state === "PV_OPTIMUM";
    const pv = optimum || state("charging_profile")?.state === "PV_SURPLUS";
    for (const [id,labels] of Object.entries(optimumFields)) {
      get(`${id}-row`).hidden = !optimum;
      get(`${id}-label`).textContent = labels[de ? 1 : 0];
      const s = state(id), input = get(id);
      input.disabled = busy || !available(s);
      input.max = s?.attributes.max ?? (id === "optimum_max_discharge_w" ? 100000 : id === "estimated_daily_house_consumption_kwh" ? 1000 : 100);
      if (previous !== d.displayed || this.shadowRoot.activeElement !== input) input.value = available(s) ? s.state : "";
    }
    get("optimum-target-row").hidden = !optimum;
    get("optimum-target-label").textContent = de ? "Aktueller Ziel-SoC" : "Current target SoC";
    get("optimum-target").textContent = typeof attrs.optimum_target_soc === "number" ? `${this.format(Math.round(attrs.optimum_target_soc * 10) / 10)} %` : "—";
    get("power-row").hidden = pv;
    get("reserve-row").hidden = !(!pv && (attrs.battery_reserve_configured ?? attrs.battery_configured));
    get("approximation-row").hidden = true;
    get("approximation-label").textContent = de ? "Leistungsannäherung" : "Power approximation";
    this.options(get("approximation"), [["up",de ? "Nicht unter Soll" : "Not below target"],["down",de ? "Nicht über Soll" : "Not above target"]]);
    get("approximation").value = state("pv_approximation")?.state || "down";
    get("approximation").disabled = busy || !available(state("pv_approximation"));
    for (const [id,label] of Object.entries(de ? {grid_start_delay:"Startverzögerung",grid_duration:"Ladedauer"} : {grid_start_delay:"Start delay",grid_duration:"Charging duration"})) {
      get(`${id}-row`).hidden = pv;
      get(`${id}-label`).textContent = label;
      const s = state(id), edit = this.durationEdits[id];
      if (edit && (edit.entity !== d.roles[id] || s?.state === edit.value)) delete this.durationEdits[id];
      const armed = !pv && attrs.grid_request_armed === true;
      if (armed) delete this.durationEdits[id];
      const value = armed ? gridCountdown(attrs,id) : this.durationEdits[id]?.value ?? (durationAvailable(s) ? s.state : "");
      const parts = value === "" ? ["",""] : value.split(":");
      const focused = [get(`${id}-hours`),get(`${id}-minutes`)].includes(this.shadowRoot.activeElement);
      for (const [index,part] of ["hours","minutes"].entries()) {
        const input = get(`${id}-${part}`);
        if (armed || previous !== d.displayed || !focused) input.value = parts[index];
        input.disabled = busy || armed || !durationAvailable(s);
        input.ariaLabel = `${label}: ${part === "hours" ? (de ? "Stunden" : "hours") : (de ? "Minuten" : "minutes")}`;
      }
      get(`${id}-clear`).disabled = busy || armed || !durationAvailable(s);
      get(`${id}-clear`).title = get(`${id}-clear`).ariaLabel = de ? `${label} zurücksetzen (nicht gesetzt)` : `Clear ${label} (unset)`;
    }
    get("soll_soc_speicher-row").hidden = optimum || !pv || !attrs.battery_configured;
    if (this.hold && (this.hold.button.disabled || (["reserve","soll_soc_speicher"].includes(this.hold.id) && (!attrs.battery_configured || (this.hold.id === "reserve" ? pv : !pv))))) this.stopHold();
    get("parameters-label").textContent = de ? "Wallboxparameter" : "Wallbox parameters";
    get("messages-label").textContent = de ? "Meldungen" : "Messages";
    get("actual").title = de ? "Bestätigter Betriebspunkt · Stromlimit, kein Messwert" : "Confirmed operating point · current limit, not measured current";
    const labels = de ? {connection:"Anschlussstatus",charging:"Ladezustand",energy:"Energie",power:"Leistung",duration:"Dauer"} : {connection:"Connection status",charging:"Charging state",energy:"Energy",power:"Power",duration:"Duration"};
    for (const [key,value] of Object.entries(liveValues(hass.states,d,hass.language))) {
      const id = key === "power" ? "live-power" : key;
      get(id).textContent = value;
      if (labels[key]) get(`${id}-label`).textContent = labels[key];
    }
    for (const [id, role] of [["connection", "connector_state"], ["charging", "charging_state"]]) {
      const stale = state(role)?.attributes.state_fresh === false;
      get(id).style.opacity = stale ? "0.5" : "1";
      get(id).title = stale ? (de ? "Letzter bekannter Zustand · aktuell nicht beobachtbar" : "Last known state · not currently observable") : "";
    }
    const errors = de ? {previous_off_unconfirmed:"Vorherige Wallbox: Ladefreigabe OFF nicht bestätigt.",previous_wallbox_unavailable:"Vorherige Wallbox nicht erreichbar.",previous_authority_unknown:"Steuerung der vorherigen Wallbox unbekannt.",battery_restore_pending:"Batteriereserve konnte noch nicht wiederhergestellt werden.",takeover_failed:"Steuerungsübernahme fehlgeschlagen.",wallbox_unavailable:"Wallbox nicht erreichbar.",off_unconfirmed:"Ladefreigabe OFF nicht bestätigt.",takeover_stale:"Steuerungsübernahme bitte erneut ausführen.",transition_failed:"Wallbox-Wechsel fehlgeschlagen."} : {previous_off_unconfirmed:"Previous wallbox: charging permission OFF not confirmed.",previous_wallbox_unavailable:"Previous wallbox unavailable.",previous_authority_unknown:"Previous wallbox authority unknown.",battery_restore_pending:"Battery reserve restoration pending.",takeover_failed:"Control takeover failed.",wallbox_unavailable:"Wallbox unavailable.",off_unconfirmed:"Charging permission OFF not confirmed.",takeover_stale:"Please take control again.",transition_failed:"Wallbox switch failed."};
    const blocked = de ? {voltage_unavailable:"Keine aktuellen Spannungswerte. Messdaten der Wallbox prüfen.",capabilities_unavailable:"Technische Grenzen fehlen. Verbindung und Wallbox-Konfiguration prüfen.",direction_unreachable:"Sollleistung mit der gewählten Annäherung nicht erreichbar. Sollleistung oder Annäherung anpassen.",zero_current_unverified:"Nullleistung wird nicht bestätigt unterstützt. Ladefreigabe deaktivieren, um zu stoppen.",electrical_limit:"Kein Ladepunkt innerhalb der Stromgrenzen. Einstellungen prüfen.",no_eligible_mode:"Keine unterstützte Phasenkonfiguration verfügbar. Wallbox-Konfiguration prüfen."} : {voltage_unavailable:"No fresh voltage readings. Check wallbox metering.",capabilities_unavailable:"Technical limits unavailable. Check connection and wallbox configuration.",direction_unreachable:"Requested power cannot meet the selected approximation policy. Adjust power or approximation.",zero_current_unverified:"Zero-power control is unverified. Disable charging permission to stop.",electrical_limit:"No charging point within current limits. Check settings.",no_eligible_mode:"No supported phase configuration available. Check wallbox configuration."};
    const messages = [errors[d.state?.attributes.ownership_status], blocked[attrs.control_status],
      d.displayed && d.inventory[d.displayed]?.connected === false ? (de ? "Wallbox nicht verbunden. Verbindung prüfen." : "Wallbox disconnected. Check its connection.") : "",
      d.active && !d.inventory[d.active] ? (de ? "Aktive Wallbox fehlt. Bitte Verbindung prüfen." : "Active wallbox missing. Check its connection.") : "",
      ["error","write_unconfirmed"].includes(attrs.battery_status) ? (de ? "Batteriereserve konnte nicht gesetzt werden. Batterie prüfen." : "Could not set battery reserve. Check the battery.") : "",
      ["failed","unsupported","temporarily_rejected"].includes(attrs.command_status) && !["phase_lockout","observing"].includes(attrs.profile_status) ? (de ? "Ladeeinstellung nicht angewendet. Verbindung und Wallbox prüfen." : "Charging setting not applied. Check the connection and wallbox.") : "",
      ({grid_waiting:de ? "NETZ-Startverzögerung läuft." : "Waiting for Grid start delay.",grid_expired:de ? "NETZ-Ladedauer abgelaufen." : "Grid charging duration expired.",profile_unavailable:de ? "Profil nicht verfügbar. Warte auf bestätigtes OFF für den Wechsel zu Netz." : "Profile unavailable. Waiting for confirmed OFF before falling back to Grid.",pv_start_delay:de ? "PV-Startverzögerung läuft." : "Waiting for PV start delay.",pv_stop_delay:de ? "PV-Stoppverzögerung: Laden mit Mindestleistung." : "PV stop delay: charging at minimum power.",measurements_unavailable: de ? "PV-Regelung pausiert: Messwerte fehlen, sind ungültig oder veraltet." : "PV regulation paused: readings are missing, invalid or stale.",waiting_battery_soc:de ? "Warte auf Speicher-SoC über dem Zielwert." : "Waiting for battery SoC above target.",stopped_battery_soc:de ? "Laden wegen niedrigem Speicher-SoC gestoppt." : "Charging stopped due to low battery SoC.",optimum_minimum_hold:de ? "PV Optimum hält die erreichbare Mindestladeleistung; Netzbezug ist möglich." : "PV Optimum holds the reachable minimum charging power; grid import may remain.",optimum_pause_pending:de ? "PV Optimum hält die Mindestleistung und prüft anhaltend zu geringe PV-Leistung." : "PV Optimum holds minimum power while checking sustained insufficient PV.",optimum_deliberate_pause:de ? "PV Optimum pausiert bewusst wegen anhaltend zu geringer PV-Leistung." : "PV Optimum deliberately pauses for insufficient PV.",optimum_no_positive_point:de ? "Kein sicherer positiver Ladepunkt innerhalb der elektrischen Grenzen." : "No safe positive charging point within electrical limits.",paused_insufficient_pv:de ? "Laden wegen zu geringer PV-Leistung pausiert." : "Charging paused due to insufficient PV power."})[attrs.profile_status] || "",
      attrs.profile_status === "error" ? (de ? "Ladeprofil fehlgeschlagen. Wallbox prüfen." : "Charging profile failed. Check the wallbox.") : ""].filter(Boolean);
    get("status").textContent = messages.join(" "); get("status").hidden = !messages.length;
  }
  options(select, options) {
    const signature = JSON.stringify(options);
    if (select._signature === signature) return;
    select._signature = signature;
    select.replaceChildren(...options.map(([value,label]) => { const o = document.createElement("option"); o.value = value; o.textContent = label; return o; }));
  }
  getCardSize() { return 5; }
  static getStubConfig() { return {}; }
}
if (!customElements.get("wallbox-manager-card")) customElements.define("wallbox-manager-card",WallboxManagerCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(c => c.type === "wallbox-manager-card")) window.customCards.push({type:"wallbox-manager-card",name:"Wallbox Manager",description:"Automatically discovered wallboxes and primitive charging profiles"});

})();
