(() => {
if (customElements.get("wallbox-manager-card")) return;
// No global first-wallbox choice: an ambiguous card requires entry_id.
function discoverWallboxManager(states, config = {}) {
  const entries = Object.entries(states || {}).filter(([,s]) => s.attributes.wallbox_manager_entry);
  const ids = [...new Set(entries.map(([,s]) => s.attributes.wallbox_manager_entry))];
  const specified = config.entry_id || (config.entity && states[config.entity]?.attributes.wallbox_manager_entry);
  const entry = specified || (!config.entry_id && !config.entity && ids.length === 1 ? ids[0] : null);
  const roles = {}, ranks = {};
  for (const [id,s] of entries) {
    const a = s.attributes;
    if (a.wallbox_manager_entry !== entry) continue;
    const rank = a.wallbox_manager_scope === "connector" ? 3 : a.wallbox_manager_scope === "evse" ? 2 : 1;
    if (rank > (ranks[a.wallbox_manager_role] || 0)) {
      roles[a.wallbox_manager_role] = id; ranks[a.wallbox_manager_role] = rank;
    }
  }
  return {entry, roles, ambiguous: !entry && ids.length > 1};
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
const optimumFields = {optimum_lower_soc:["Lower target SoC","Unterer Ziel-SoC","%"], optimum_upper_soc:["Upper target SoC","Oberer Ziel-SoC","%"], optimum_max_discharge_w:["Maximum storage discharge","Maximale Speicher-Entladeleistung","W"], estimated_daily_house_consumption_kwh:["Daily household consumption (without EV)","Täglicher Hausverbrauch (ohne EV)","kWh/d"]};
function numberRole(id) { return id === "power" ? "soll_power" : id === "reserve" ? "min_soc" : id; }
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
      select {padding:6px 8px;max-width:55%}
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
      @media(max-width:360px) {ha-card {padding:12px} .row {gap:8px} header {gap:8px;flex-wrap:wrap} .numeric {gap:2px} .step {width:32px}}
    </style><ha-card>
      <header><ha-icon icon="mdi:ev-station"></ha-icon><div class="heading"><h2 id="title"></h2><div id="station"></div></div></header>
      <button id="takeover"></button>
      <label class="row" id="profile-row"><span id="profile-label"></span><select id="profile"></select></label>
      <div class="row" id="power-row"><label id="power-label" for="power"></label><div class="numeric"><button id="power-down" class="step" type="button">−</button><input id="power" type="text" inputmode="decimal" autocomplete="off"><button id="power-up" class="step" type="button">+</button><span class="unit">kW</span></div></div>
      <div class="row" id="reserve-row"><label id="reserve-label" for="reserve"></label><div class="numeric"><button id="reserve-down" class="step" type="button">−</button><input id="reserve" type="text" inputmode="numeric" autocomplete="off"><button id="reserve-up" class="step" type="button">+</button><span class="unit">%</span></div></div>
      <label class="row" id="approximation-row"><span id="approximation-label"></span><select id="approximation"></select></label>
      <div class="row" id="soll_soc_speicher-row"><label id="soll_soc_speicher-label" for="soll_soc_speicher"></label><div class="numeric"><button id="soll_soc_speicher-down" class="step" type="button">−</button><input id="soll_soc_speicher" type="text" inputmode="numeric" autocomplete="off"><button id="soll_soc_speicher-up" class="step" type="button">+</button><span class="unit">%</span></div></div>
      ${["grid_start_delay", "grid_duration"].map(id => `<div class="row" id="${id}-row"><span id="${id}-label"></span><div class="duration-editor" role="group" aria-labelledby="${id}-label"><input type="number" id="${id}-hours" min="0" step="1" placeholder="—"><span class="colon">:</span><input type="number" id="${id}-minutes" min="0" max="59" step="1" placeholder="—"><button type="button" class="clear" id="${id}-clear">×</button></div></div>`).join("")}
      ${Object.entries(optimumFields).map(([id,labels]) => `<div class="row" id="${id}-row"><label id="${id}-label" for="${id}"></label><div class="numeric"><input id="${id}" type="number" min="0" step="${id === "estimated_daily_house_consumption_kwh" ? "0.1" : "1"}"><span class="unit">${labels[2]}</span></div></div>`).join("")}
      <button id="permission"></button>
      <div class="live"><div><div class="caption" id="connection-label"></div><div class="reading" id="connection"></div></div><div><div class="caption" id="charging-label"></div><div class="reading" id="charging"></div></div><div><div class="caption" id="energy-label"></div><div class="reading" id="energy"></div></div><div><div class="caption" id="live-power-label"></div><div class="reading" id="live-power"></div></div><div><div class="caption" id="duration-label"></div><div class="reading" id="duration"></div></div></div>
      <section class="section" aria-labelledby="parameters-label"><h3 id="parameters-label"></h3><div class="reading" id="actual"></div></section>
      <section class="section" id="messages" aria-labelledby="messages-label"><h3 id="messages-label"></h3><div class="reading" id="no-messages">-</div><p id="status" class="notice" role="status" hidden></p><p id="error" class="notice" role="alert" hidden></p></section>
    </ha-card>`;
    const get = id => this.shadowRoot.getElementById(id);
    get("takeover").onclick = () => this.call("button", "press", {entity_id:this.discovery.roles.take_control});
    get("profile").onchange = e => this.call("select", "select_option", {entity_id:this.discovery.roles.charging_profile, option:e.target.value});
    get("approximation").onchange = e => this.call("select", "select_option", {entity_id:this.discovery.roles[this._hass.states[this.discovery.roles.charging_profile]?.state === "NETZ" ? "power_approximation" : "pv_approximation"], option:e.target.value});
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
    get("permission").onclick = () => this.call("switch", this._hass.states[this.discovery.roles.charging_enabled]?.state === "on" ? "turn_off" : "turn_on", {entity_id:this.discovery.roles.charging_enabled});
    if (this._hass) this.hass = this._hass;
  }
  connectedCallback() {
    this._blur = () => this.stopHold();
    window.addEventListener("blur", this._blur);
  }
  disconnectedCallback() {
    this.stopHold();
    if (this._blur) window.removeEventListener("blur", this._blur);
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
    const value = id === "power" ? this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_min_kw : this._hass.states[this.discovery.roles[id]]?.attributes.min;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
  }
  maximum(id) {
    if (id === "reserve") return 100;
    if (id === "soll_soc_speicher") return this._hass.states[this.discovery.roles[id]]?.attributes.max ?? 98;
    const value = this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_max_kw;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }
  stepNumber(id, direction) {
    const input = this.shadowRoot.getElementById(id);
    if (input.disabled) return;
    const value = parseInput(input.value, this._hass.language);
    if (!Number.isFinite(value) || (id !== "power" && !Number.isInteger(value))) return;
    const next = id === "power" ? powerStep(value,direction,this.maximum(id),this.minimum(id)) : Math.min(this.maximum(id),Math.max(this.minimum(id),Math.round(value) + direction));
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
  async call(domain, service, data) {
    const action = domain === "button" && service === "press" ? "take_control" : domain === "switch" && ["turn_on", "turn_off"].includes(service) ? "charging_enabled" : null;
    if (action) {
      const state = this._hass.states[this.discovery.roles[action]];
      if (this.busy || !state || state.state === "unavailable" || state.attributes.execution_ready !== true || data.entity_id !== this.discovery.roles[action]) return;
    } else if (!["number", "select", "text"].includes(domain) || !Object.values(this.discovery.roles).includes(data.entity_id)) return;
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
    const previous = this.discovery?.entry;
    const d = this.discovery = discoverWallboxManager(hass.states, this.config);
    if (previous !== d.entry) { this.stopHold(); this.edits = {}; this.durationEdits = {}; get("error").hidden = true; }
    const state = role => hass.states[d.roles[role]], busy = !!this.busy;
    const connected = state("connected")?.state === "on";
    const authority = connected && available(state("control_authority")) ? state("control_authority").state : null;
    get("title").textContent = this.config.name || "Wallbox Manager";
    get("station").textContent = state("connected")?.attributes.station_id || "—";
    get("takeover").textContent = de ? "Steuerung übernehmen" : "Take control";
    get("takeover").hidden = authority === "remote";
    for (const [id,role] of [["takeover","take_control"],["permission","charging_enabled"]]) {
      const s = state(role);
      get(id).disabled = busy || !connected || !s || s.state === "unavailable" || s.attributes.execution_ready !== true;
    }
    get("takeover").disabled ||= !["local", "remote"].includes(authority);
    const permission = state("charging_enabled");
    const permissionText = permission?.state === "on" ? (de ? "aktiv" : "enabled") : permission?.state === "off" ? (de ? "inaktiv" : "disabled") : (de ? "unbekannt" : "unknown");
    get("permission").textContent = `${de ? "Ladefreigabe" : "Charging permission"}: ${permissionText}`;
    get("profile-label").textContent = de ? "Ladeprofil" : "Charging profile";
    get("power-label").textContent = de ? "Angeforderte Ladeleistung" : "Requested charging power";
    get("reserve-label").textContent = de ? "Entladereserve" : "Discharge reserve";
    get("soll_soc_speicher-label").textContent = de ? "Speicher-Ziel-SoC" : "Battery target SoC";
    this.options(get("profile"), (state("charging_profile")?.attributes.options || []).map(v => [v, {NETZ:de ? "Netz" : "Grid", PV_SURPLUS:de ? "PV-Überschuss" : "PV Surplus",PV_OPTIMUM:"PV Optimum",PV_MAXIMUM:"PV Maximum"}[v] || v]));
    get("profile").value = state("charging_profile")?.state || "";
    get("profile").disabled = busy || !available(state("charging_profile"));
    for (const [id,role] of [["power","soll_power"],["reserve","min_soc"],["soll_soc_speicher","soll_soc_speicher"]]) {
      const s = state(role), edit = this.edits[id];
      if (edit && (edit.entity !== d.roles[role] || (available(s) && Number(s.state) === edit.value))) delete this.edits[id];
      if (previous !== d.entry || this.shadowRoot.activeElement !== get(id)) get(id).value = this.edits[id] ? this.format(this.edits[id].value) : available(s) ? this.format(Number(s.state)) : "";
      const disabled = busy || !available(s), value = parseInput(get(id).value,hass.language), maximum = this.maximum(id);
      get(id).disabled = disabled;
      for (const suffix of ["up","down"]) get(`${id}-${suffix}`).disabled = disabled || !Number.isFinite(value) || (suffix === "down" ? value <= this.minimum(id) : maximum !== null && value >= maximum);
    }
    const profile = state("charging_profile")?.state;
    const optimum = profile === "PV_OPTIMUM", pv = ["PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"].includes(profile);
    get("power-row").hidden = pv; get("reserve-row").hidden = pv;
    get("soll_soc_speicher-row").hidden = profile !== "PV_SURPLUS";
    get("approximation-row").hidden = false;
    for (const [id,labels] of Object.entries(optimumFields)) {
      get(`${id}-row`).hidden = id === "optimum_max_discharge_w" ? !pv : !optimum;
      get(`${id}-label`).textContent = labels[de ? 1 : 0];
      const s = state(id), input = get(id);
      input.disabled = busy || !available(s);
      input.min = s?.attributes.min ?? 0; input.max = s?.attributes.max ?? 100;
      if (this.shadowRoot.activeElement !== input) input.value = available(s) ? s.state : "";
    }
    get("approximation-label").textContent = de ? "Leistungsannäherung" : "Power approximation";
    const approximationRole = pv ? "pv_approximation" : "power_approximation";
    this.options(get("approximation"), [["up",de ? "Nicht unter Soll" : "Not below target"],["down",de ? "Nicht über Soll" : "Not above target"], ...(!pv ? [["nearest",de ? "Nächster Wert" : "Nearest"]] : [])]);
    get("approximation").value = state(approximationRole)?.state || (pv ? "down" : "nearest");
    get("approximation").disabled = busy || !available(state(approximationRole));
    for (const [id,label] of Object.entries(de ? {grid_start_delay:"Startverzögerung",grid_duration:"Ladedauer"} : {grid_start_delay:"Start delay",grid_duration:"Charging duration"})) {
      get(`${id}-row`).hidden = pv;
      get(`${id}-label`).textContent = label;
      const s = state(id), edit = this.durationEdits[id];
      if (edit && (edit.entity !== d.roles[id] || s?.state === edit.value)) delete this.durationEdits[id];
      const value = this.durationEdits[id]?.value ?? (durationAvailable(s) ? s.state : "");
      const parts = value === "" ? ["",""] : value.split(":");
      for (const [index,part] of ["hours","minutes"].entries()) {
        const input = get(`${id}-${part}`);
        if (previous !== d.entry || this.shadowRoot.activeElement !== input) input.value = parts[index];
        input.disabled = busy || !durationAvailable(s);
      }
      get(`${id}-clear`).disabled = busy || !durationAvailable(s);
    }
    get("parameters-label").textContent = de ? "Wallboxparameter" : "Wallbox parameters";
    get("messages-label").textContent = de ? "Meldungen" : "Messages";
    get("actual").textContent = [["voltage_l1","U L1"],["voltage_l2","U L2"],["voltage_l3","U L3"],["current_l1","I L1"],["current_l2","I L2"],["current_l3","I L3"],["control_authority",de ? "Beobachtete Steuerung" : "Observed authority"]].map(([role,label]) => {
      const s=state(role); const value = role === "control_authority" ? (connected && available(s) ? ({local:de ? "Lokal" : "Local",remote:"OCPP",unknown:"—"}[s?.state] || "—") : "—") : available(s) ? s.state + " " + (s.attributes.unit_of_measurement || "") : "—";
      return `${label}: ${value}`;
    }).join(" · ");
    const point = state("soll_power")?.attributes;
    if (profile === "NETZ" && point) get("actual").textContent += ` · ${de ? "Bestätigtes Angebot" : "Confirmed offer"}: ${connected && point.applied_current_a != null ? `${point.applied_phase_count}p × ${point.applied_current_a} A (${this.format(point.applied_offered_power_w / 1000)} kW)` : "—"}`;
    const labels = de ? {connection:"Anschlussstatus",charging:"Ladezustand",energy:"Sitzungsenergie",power:"Leistung",duration:"Dauer"} : {connection:"Connection status",charging:"Charging state",energy:"Session energy",power:"Power",duration:"Duration"};
    const connectorLabels = de ? {available:"Frei",occupied:"Belegt",reserved:"Reserviert",faulted:"Störung"} : {available:"Available",occupied:"Occupied",reserved:"Reserved",faulted:"Faulted"};
    const chargingLabels = de ? {idle:"Bereit",connected:"Verbunden",charging:"Lädt",suspended_vehicle:"Vom Fahrzeug pausiert",suspended_station:"Von Wallbox pausiert"} : {idle:"Idle",connected:"Connected",charging:"Charging",suspended_vehicle:"Paused by vehicle",suspended_station:"Paused by wallbox"};
    for (const [id,role] of [["connection","connector_state"],["charging","charging_state"],["energy","session_energy"],["live-power","power"],["duration","session_duration"]]) {
      const s = state(role); let text="—";
      if (connected && available(s)) {
        if (id === "connection") text = connectorLabels[s.state] || s.state;
        else if (id === "charging") text = chargingLabels[s.state] || s.state;
        else if (id === "duration") { const minutes=Math.floor(Number(s.state)/60); text=`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,"0")}`; }
        else text = `${this.format(id === "live-power" ? Number(s.state)/1000 : Number(s.state))} ${id === "live-power" ? "kW" : "kWh"}`;
      }
      get(id).textContent = text; get(`${id}-label`).textContent = labels[id === "live-power" ? "power" : id];
    }
    get("status").hidden = false;
    get("status").textContent = (d.ambiguous ? (de ? "Mehrere Instanzen: entry_id in der Kartenkonfiguration angeben. " : "Multiple instances: configure this card's entry_id. ") : "") +
      (de ? "Übernahme und Ladefreigabe sind explizite Aktionen. Netzprofil: festes Leistungsangebot bei Ladefreigabe." : "Takeover and charging permission require explicit actions. Grid applies a fixed offered point while charging is permitted.") +
      (profile === "NETZ" && point ? ` NETZ: ${point.profile_status || "—"}${point.command_reason ? ` (${point.command_reason})` : ""}.` : "") +
      ["take_control","charging_enabled"].map(role => {
        const a = state(role)?.attributes;
        return a ? ` ${role === "take_control" ? (de ? "Letzter Übernahmebefehl" : "Last takeover command") : (de ? "Letzter Freigabebefehl" : "Last permission command")}: ${a.command_status || "—"}${a.command_reason ? ` (${a.command_reason})` : ""}. ${de ? "Steuerung" : "Control"}: ${a.execution_blocked_reason || (de ? "bereit" : "ready")}.` : "";
      }).join("");
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
if (!window.customCards.some(c => c.type === "wallbox-manager-card")) window.customCards.push({type:"wallbox-manager-card",name:"Wallbox Manager",description:"Single-wallbox observations, explicit control and Grid profile"});

})();
