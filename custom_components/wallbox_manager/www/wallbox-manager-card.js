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
function parseInput(value, language) {
  const decimal = new Intl.NumberFormat(language).formatToParts(1.1).find(p => p.type === "decimal").value;
  const normalized = String(value).trim().replace(decimal, ".");
  return /^\d+(?:\.\d+)?$/.test(normalized) ? Number(normalized) : NaN;
}
function available(state) { return state && !["unknown", "unavailable", ""].includes(state.state); }
function durationAvailable(state) { return state && typeof state.state === "string" && (state.state === "" || /^[0-9]{2,}:[0-5][0-9]$/.test(state.state)); }
const optimumFields = {optimum_lower_soc:["Lower target SoC","Unterer Ziel-SoC","%"], optimum_upper_soc:["Upper target SoC","Oberer Ziel-SoC","%"], optimum_max_discharge_w:["Maximum storage discharge","Maximale Speicher-Entladeleistung","W"], estimated_daily_house_consumption_kwh:["Daily household consumption (without EV)","Täglicher Hausverbrauch (ohne EV)","kWh/d"]};
function numberRole(id) { return id === "power" ? "soll_power" : id; }
class WallboxManagerCard extends HTMLElement {
  setConfig(config) {
    if (this.powerQueue) this.powerQueue.cancelled = true;
    if (this.powerStepPending) this.powerStepPending.reject(new Error("Card reconfigured"));
    this.stopHold();
    this.cancelTiming();
    this.localWarnings = new Map();
    this.config = config;
    this.edits = {};
    this.durationEdits = {};
    this.draftWrites = new Set();
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
      :disabled {opacity:.5;cursor:default} #charge-action,#takeover {width:100%;padding:9px 12px;margin-top:6px;font-size:14px}
      #charge-action {background:var(--primary-color);color:var(--text-primary-color);border-color:var(--primary-color);font-weight:500}
      #charge-action:hover:not(:disabled) {filter:brightness(.95)}
      .live {border-top:1px solid var(--divider-color);padding-top:12px;margin-top:16px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px 16px}
      .caption {font-size:12px;color:var(--secondary-text-color);margin-bottom:3px} .reading {font-size:14px;line-height:1.4;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
      .section {border-top:1px solid var(--divider-color);padding-top:12px;margin-top:16px} h3 {font-size:14px;font-weight:500;margin:0 0 8px} .notice {font-size:13px;color:var(--error-color);line-height:1.4;margin:12px 0 0}
      [hidden] {display:none!important}
      @media(max-width:360px) {ha-card {padding:12px} .row {gap:8px} header {gap:8px;flex-wrap:wrap} .numeric {gap:2px} .step {width:32px}}
    </style><ha-card>
      <header><ha-icon icon="mdi:ev-station"></ha-icon><div class="heading"><h2 id="title"></h2><div id="station"></div></div></header>
      <button id="takeover"></button>
      <label class="row" id="profile-row"><span id="profile-label"></span><select id="profile"></select></label>
      <div class="row" id="power-row"><label id="power-label" for="power"></label><div class="numeric"><button id="power-down" class="step" type="button">−</button><input id="power" placeholder="—" type="text" inputmode="decimal" autocomplete="off"><button id="power-up" class="step" type="button">+</button><span class="unit">kW</span></div></div>
      <div class="row" id="soll_soc_speicher-row"><label id="soll_soc_speicher-label" for="soll_soc_speicher"></label><div class="numeric"><button id="soll_soc_speicher-down" class="step" type="button">−</button><input id="soll_soc_speicher" type="text" inputmode="numeric" autocomplete="off"><button id="soll_soc_speicher-up" class="step" type="button">+</button><span class="unit">%</span></div></div>
      ${["grid_start_delay", "grid_duration"].map(id => `<div class="row" id="${id}-row"><span id="${id}-label"></span><div class="duration-editor" role="group" aria-labelledby="${id}-label"><input type="number" id="${id}-hours" min="0" step="1" placeholder="--"><span class="colon">:</span><input type="number" id="${id}-minutes" min="0" max="59" step="1" placeholder="--"><button type="button" class="clear" id="${id}-clear">×</button></div></div>`).join("")}
      ${Object.entries(optimumFields).map(([id,labels]) => `<div class="row" id="${id}-row"><label id="${id}-label" for="${id}"></label><div class="numeric"><input id="${id}" type="number" min="0" step="${id === "estimated_daily_house_consumption_kwh" ? "0.1" : "1"}"><span class="unit">${labels[2]}</span></div></div>`).join("")}
      <button id="charge-action"></button>
      <div class="live"><div><div class="caption" id="connection-label"></div><div class="reading" id="connection"></div></div><div><div class="caption" id="charging-label"></div><div class="reading" id="charging"></div></div><div><div class="caption" id="energy-label"></div><div class="reading" id="energy"></div></div><div><div class="caption" id="live-power-label"></div><div class="reading" id="live-power"></div></div><div><div class="caption" id="duration-label"></div><div class="reading" id="duration"></div></div></div>
      <section class="section" aria-labelledby="parameters-label"><h3 id="parameters-label"></h3><div class="reading" id="actual"></div></section>
      <section class="section" id="messages" aria-labelledby="messages-label"><h3 id="messages-label"></h3><p id="error" class="notice" role="status">—</p></section>
    </ha-card>`;
    const get = id => this.shadowRoot.getElementById(id);
    get("takeover").onclick = () => this.call("button", "press", {entity_id:this.discovery.roles.take_control});
    get("profile").onchange = e => this.call("select", "select_option", {entity_id:this.discovery.roles.charging_profile, option:e.target.value});
    for (const id of ["grid_start_delay", "grid_duration"]) {
      for (const part of ["hours", "minutes"]) {
        get(`${id}-${part}`).onfocus = () => this.beginTiming();
        get(`${id}-${part}`).oninput = () => this.editDuration(id);
        get(`${id}-${part}`).onchange = () => this.editDuration(id);
        get(`${id}-${part}`).onblur = () => Promise.resolve().then(() => {
          if (!this.timingFocused() && this.shadowRoot.activeElement !== get("charge-action")) this.endTiming();
        });
        get(`${id}-${part}`).onkeydown = e => {
          if (e.key === "Enter") { e.preventDefault(); this.editDuration(id); }
        };
      }
      get(`${id}-clear`).onclick = () => {
        if (get(`${id}-clear`).disabled) return;
        get(`${id}-hours`).value = get(`${id}-minutes`).value = "";
        this.editDuration(id).then(() => { if (!this.timingFocused()) this.endTiming(); });
      };
    }
    for (const id of ["power", "soll_soc_speicher"]) {
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
    get("charge-action").onclick = async () => {
      // Moving focus from a time editor onto Apply must keep this first click.
      if (this.timingEditing) await this.endTiming();
      if (this.timingQueue) await this.timingQueue.tail;
      return this.call("button", "press", {entity_id:this.discovery.roles.charge_action});
    };
    if (this._hass) this.hass = this._hass;
  }
  connectedCallback() {
    this._blur = () => { this.stopHold(); this.endTiming(); };
    window.addEventListener("blur", this._blur);
    window.addEventListener("pagehide", this._blur);
  }
  disconnectedCallback() {
    this.stopHold();
    this.endTiming();
    clearTimeout(this.countdownTimer);
    if (this._blur) { window.removeEventListener("blur", this._blur); window.removeEventListener("pagehide", this._blur); }
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
  format(value, digits = null) {
    return new Intl.NumberFormat(this._hass.language, digits === null ? {maximumFractionDigits:6,useGrouping:false} : {minimumFractionDigits:digits,maximumFractionDigits:digits,useGrouping:false}).format(value);
  }
  minimum(id) {
    const value = id === "power" ? this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_min_kw : this._hass.states[this.discovery.roles[id]]?.attributes.min;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
  }
  maximum(id) {
    if (id === "soll_soc_speicher") return this._hass.states[this.discovery.roles[id]]?.attributes.max ?? 98;
    const value = this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_max_kw;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }
  stepNumber(id, direction) {
    if (id === "power") return this.stepTechnical(direction);
    const input = this.shadowRoot.getElementById(id);
    if (input.disabled) return;
    const value = parseInput(input.value, this._hass.language);
    if (!Number.isFinite(value) || (id !== "power" && !Number.isInteger(value))) return;
    const next = Math.min(this.maximum(id),Math.max(this.minimum(id),Math.round(value) + direction));
    if (next === value) return;
    input.value = this.format(next);
    return this.editNumber(id);
  }
  stepTechnical(direction) {
    const entity = this.discovery.roles.soll_power;
    if (this.shadowRoot.getElementById("power").disabled || this.edits.power) return Promise.resolve();
    let queue = this.powerQueue;
    if (!queue || queue.entity !== entity) queue = this.powerQueue = {entity,revision:this._hass.states[entity]?.attributes.grid_draft_revision,tail:Promise.resolve(),count:0,cancelled:false};
    queue.count++;
    queue.tail = queue.tail.then(async () => {
      if (queue.cancelled || entity !== this.discovery.roles.soll_power) return;
      const state = this._hass.states[entity], point = state?.attributes[direction > 0 ? "power_step_up" : "power_step_down"];
      if (!point) return;
      if (!available(state) || state.attributes.grid_draft_revision !== queue.revision) throw new Error("NETZ selection changed; try again");
      let pending;
      const acknowledged = new Promise((resolve,reject) => { pending = {entity,revision:queue.revision,point,incarnation:state.attributes.runtime_incarnation,resolve,reject}; });
      // Attach a handler before the asynchronous service may reject or HA may rebind.
      const outcome = acknowledged.then(() => null, error => error);
      this.powerStepPending = pending;
      this.draftWrites.add(pending);
      this.hass = this._hass;
      try {
        await this._hass.callService("wallbox_manager","step_power",{entity_id:entity,direction,revision:queue.revision,incarnation:state.attributes.runtime_incarnation});
        this.acknowledgePowerStep();
        const error = await outcome;
        if (error) throw error;
        queue.revision++;
      } catch (error) { pending.reject(error); throw error; } finally {
        if (this.powerStepPending === pending) this.powerStepPending = null;
        this.draftWrites.delete(pending);
        this.hass = this._hass;
      }
    }).catch(error => {
      queue.cancelled = true;
      if (entity === this.discovery.roles.soll_power) {
        this.warn("power",error.message || String(error));
      }
    }).finally(() => { if (--queue.count === 0 && this.powerQueue === queue) this.powerQueue = null; });
    return queue.tail;
  }
  acknowledgePowerStep() {
    const pending = this.powerStepPending;
    if (!pending) return;
    const state = this._hass.states[pending.entity];
    if (pending.entity !== this.discovery.roles.soll_power || !available(state) || state.attributes.runtime_incarnation !== pending.incarnation) {
      pending.reject(new Error("NETZ selection unavailable or replaced")); return;
    }
    const revision = state.attributes.grid_draft_revision;
    if (revision > pending.revision) {
      if (revision === pending.revision + 1 && Number(state.state) === pending.point.power_kw && state.attributes.selected_phase_count === pending.point.power_phases) pending.resolve();
      else pending.reject(new Error("NETZ selection changed; try again"));
    }
  }
  async editNumber(id) {
    const input = this.shadowRoot.getElementById(id), get = key => this.shadowRoot.getElementById(key);
    if (input.disabled) return;
    const value = parseInput(input.value,this._hass.language), maximum = this.maximum(id);
    const de = this._hass.language?.startsWith("de");
    if (!Number.isFinite(value) || value < this.minimum(id) || (maximum !== null && value > maximum) || (id !== "power" && !Number.isInteger(value))) {
      this.warn(id, de ? `Bitte einen gültigen Wert ab ${this.format(this.minimum(id))}${maximum === null ? "" : ` bis ${this.format(maximum)}`} eingeben${id !== "power" ? " (ganze Prozent)" : ""}.` : `Enter a valid value from ${this.format(this.minimum(id))}${maximum === null ? "" : ` to ${this.format(maximum)}`}${id !== "power" ? " (whole percent)" : ""}.`);
      if (id === "soll_soc_speicher") input.value = this.format(Math.round(Number(this._hass.states[this.discovery.roles[id]].state)));
      return;
    }
    input.value = this.format(value, id === "power" ? 1 : null);
    const entity = this.discovery.roles[numberRole(id)];
    if ((!this.edits[id] && available(this._hass.states[entity]) && Number(this._hass.states[entity].state) === value && (id !== "power" || !this._hass.states[entity].attributes.draft_phase_hint)) || (this.edits[id]?.entity === entity && this.edits[id].value === value)) return;
    const edit = this.edits[id] = {entity,value,revision:this._hass.states[entity]?.attributes.grid_draft_revision ?? 0};
    if (id === "power") this.draftWrites.add(edit);
    input.value = this.format(value, id === "power" ? 1 : null);
    this.clearWarning(id);
    this.hass = this._hass;
    try { await this._hass.callService("number","set_value",{entity_id:entity,value}); }
    catch (err) {
      if (this.edits[id] === edit && this.discovery.roles[numberRole(id)] === entity) {
        delete this.edits[id];
        input.value = this.format(id === "soll_soc_speicher" ? Math.round(Number(this._hass.states[entity].state)) : Number(this._hass.states[entity].state), id === "power" ? 1 : null);
        this.warn(id,err.message || String(err));
      }
    }
    finally { edit.complete = true; this.draftWrites.delete(edit); this.hass = this._hass; }
  }
  timingAttributes() {
    return this._hass?.states[this.discovery?.roles.grid_duration]?.attributes || {};
  }
  timingFocused() {
    return ["grid_start_delay", "grid_duration"].some(id => ["hours", "minutes", "clear"].some(part => this.shadowRoot.activeElement === this.shadowRoot.getElementById(`${id}-${part}`)));
  }
  cancelTiming() {
    clearTimeout(this.leaseTimer);
    this.leaseTimer = null;
    clearTimeout(this.countdownTimer);
    if (this.timingQueue) this.timingQueue.cancelled = true;
    if (this.timingPending) this.timingPending.reject(new Error("Timing editor replaced"));
    this.timingQueue = null;
    this.timingEditing = false;
  }
  beginTiming() {
    if (this.timingEditing) return this.timingQueue?.tail || Promise.resolve();
    const attrs = this.timingAttributes();
    for (const id of ["grid_start_delay", "grid_duration"]) {
      const state = this._hass.states[this.discovery.roles[id]];
      const parts = (durationAvailable(state) && state.state ? state.state : ":").split(":");
      for (const [index,part] of ["hours","minutes"].entries()) this.shadowRoot.getElementById(`${id}-${part}`).value = parts[index];
    }
    this.timingEditing = true;
    if (!this.timingQueue || this.timingQueue.cancelled || (this.draftWrites.size === 0 && this.timingQueue.revision !== attrs.grid_timing_revision)) this.timingQueue = {owner:`card-${Date.now()}-${Math.random()}`,revision:attrs.grid_timing_revision,incarnation:attrs.runtime_incarnation,tail:Promise.resolve(),cancelled:false};
    this.renderTiming();
    return this.queueTiming("begin", "grid_start_delay");
  }
  endTiming() {
    clearTimeout(this.leaseTimer);
    this.leaseTimer = null;
    if (!this.timingEditing) return Promise.resolve();
    this.timingEditing = false;
    return this.queueTiming("end", "grid_start_delay").finally(() => this.renderTiming());
  }
  scheduleTimingRenewal() {
    const queue = this.timingQueue, edit = this.timingAttributes().grid_timing_edit;
    if (!this.timingEditing || !queue || queue.cancelled || !edit?.active || edit.owner !== queue.owner) {
      clearTimeout(this.leaseTimer); this.leaseTimer = null; return;
    }
    if (this.leaseTimer) return;
    this.leaseTimer = setTimeout(() => {
      this.leaseTimer = null;
      // Serialize with real edits, but renewals need no HA state acknowledgement.
      queue.tail = queue.tail.then(async () => {
        if (!this.timingEditing || queue.cancelled || this.timingQueue !== queue) return;
        const a = this.timingAttributes(), current = a.grid_timing_edit;
        if (a.runtime_incarnation !== queue.incarnation || a.grid_timing_revision !== queue.revision || !current?.active || current.owner !== queue.owner) throw new Error("Timing editor expired or replaced; focus again");
        await this._hass.callService("wallbox_manager","edit_timing",{entity_id:this.discovery.roles.grid_start_delay,action:"renew",owner:queue.owner,revision:queue.revision,incarnation:queue.incarnation});
      }).catch(error => {
        if (!queue.cancelled && this.timingQueue === queue) {
          queue.cancelled = true; this.timingEditing = false;
          this.warn("grid_duration",error.message || String(error));
        }
      }).finally(() => { this.scheduleTimingRenewal(); this.renderTiming(); });
    },20000);
  }
  queueTiming(action, id, value="") {
    const queue = this.timingQueue, entity = this.discovery.roles[id];
    if (!queue) return Promise.resolve();
    const write = {entity};
    this.draftWrites.add(write);
    queue.tail = queue.tail.then(async () => {
      if (queue.cancelled) return;
      const state = this._hass.states[entity];
      if (entity !== this.discovery.roles[id] || state?.attributes.runtime_incarnation !== queue.incarnation || state?.attributes.grid_timing_revision !== queue.revision) throw new Error("Timing selection changed; try again");
      if (action === "update" && state.state === value) return;
      let pending;
      const acknowledged = new Promise((resolve,reject) => { pending = {entity,revision:queue.revision,incarnation:queue.incarnation,value,action,resolve,reject}; });
      const outcome = acknowledged.then(() => null, error => error);
      this.timingPending = pending;
      try {
        await this._hass.callService("wallbox_manager","edit_timing",{entity_id:entity,action,owner:queue.owner,revision:queue.revision,incarnation:queue.incarnation,value});
        this.acknowledgeTiming();
        const error = await outcome;
        if (error) throw error;
        queue.revision++;
        this.clearWarning(id);
      } catch (error) { pending.reject(error); throw error; }
      finally { if (this.timingPending === pending) this.timingPending = null; }
    }).catch(error => {
      if (!queue.cancelled) this.warn(id,error.message || String(error));
      queue.cancelled = true;
      if (this.timingQueue === queue) { this.timingEditing = false; this.durationEdits = {}; }
    }).finally(() => { this.draftWrites.delete(write); this.hass = this._hass; });
    this.hass = this._hass;
    return queue.tail;
  }
  acknowledgeTiming() {
    const pending = this.timingPending;
    if (!pending) return;
    const state = this._hass.states[pending.entity];
    if (!state || state.attributes.runtime_incarnation !== pending.incarnation || !Object.values(this.discovery.roles).includes(pending.entity)) {
      pending.reject(new Error("Timing runtime replaced")); return;
    }
    const revision = state.attributes.grid_timing_revision;
    if (revision > pending.revision) {
      if (revision === pending.revision + 1 && (pending.action !== "update" || state.state === pending.value)) pending.resolve();
      else pending.reject(new Error("Timing selection changed; try again"));
    }
  }
  async editDuration(id) {
    const get = key => this.shadowRoot.getElementById(key);
    const hours = get(`${id}-hours`), minutes = get(`${id}-minutes`);
    if (hours.disabled || minutes.disabled) return;
    const valid = (input, max = Number.MAX_SAFE_INTEGER) => !input.validity?.badInput &&
      (input.value === "" || (/^[0-9]+$/.test(input.value) && Number.isSafeInteger(Number(input.value)) && Number(input.value) <= max));
    if (!valid(hours) || !valid(minutes,59)) {
      this.warn(id,this._hass.language?.startsWith("de") ? "Ganze Stunden ab 0 und Minuten von 0 bis 59 eingeben." : "Enter whole hours from 0 and minutes from 0 to 59.");
      return;
    }
    const value = hours.value === "" && minutes.value === "" ? "" : `${String(Number(hours.value || 0)).padStart(2,"0")}:${String(Number(minutes.value || 0)).padStart(2,"0")}`;
    this.beginTiming();
    const edit = this.durationEdits[id] = {entity:this.discovery.roles[id],value};
    this.clearWarning(id);
    await this.queueTiming("update",id,value);
    if (this.durationEdits[id] === edit) delete this.durationEdits[id];
    this.hass = this._hass;
  }
  renderTiming() {
    if (!this.discovery) return;
    clearTimeout(this.countdownTimer);
    const attrs = this.timingAttributes(), action = this._hass.states[this.discovery.roles.charge_action]?.attributes || {};
    const schedule = attrs.grid_schedule, now = Date.now()/1000;
    const live = action.charge_release && schedule;
    const editing = this.timingEditing || attrs.grid_timing_edit || Object.keys(this.durationEdits).length > 0;
    const text = seconds => { const minutes = Math.floor(Math.max(0,seconds)/60); return `${String(Math.floor(minutes/60)).padStart(2,"0")}:${String(minutes%60).padStart(2,"0")}`; };
    for (const id of ["grid_start_delay", "grid_duration"]) {
      const state = this._hass.states[this.discovery.roles[id]];
      let value = this.durationEdits[id]?.value ?? (durationAvailable(state) ? state.state : "");
      if (live && !editing) {
        if (id === "grid_start_delay") value = schedule.start_at > now && schedule.start_at > schedule.epoch_at ? text(schedule.start_at-now) : "";
        else value = schedule.end_at != null && schedule.end_at > now ? text(schedule.end_at-Math.max(now,schedule.start_at)) : "";
      }
      if (!live && !editing) {
        const approved = attrs[id === "grid_start_delay" ? "grid_approved_start_delay" : "grid_approved_duration"];
        const seconds = value ? value.split(":").reduce((h,m) => Number(h)*3600+Number(m)*60) : null;
        if (approved === seconds) value = "";
      }
      const parts = value === "" ? ["", ""] : value.split(":");
      for (const [index,part] of ["hours","minutes"].entries()) {
        const input = this.shadowRoot.getElementById(`${id}-${part}`);
        if (!this.timingEditing) input.value = parts[index];
      }
    }
    if (live && !editing) this.countdownTimer = setTimeout(() => this.renderTiming(),1000);
  }
  warningSnapshot(id) {
    const entity = this.discovery?.roles[numberRole(id)] || id;
    const state = this._hass?.states[entity];
    return JSON.stringify([state?.state,state?.attributes.command_status,state?.attributes.grid_approved_revision]);
  }
  warn(id,text) {
    this.localWarnings.set(id,{text,raised_at:Date.now()/1000,snapshot:this.warningSnapshot(id)});
    this.renderMessage();
  }
  clearWarning(id) { this.localWarnings.delete(id); this.renderMessage(); }
  renderMessage() {
    if (!this.discovery) return;
    const canonical = this._hass.states[this.discovery.roles.charge_action]?.attributes.active_message;
    const candidates = [...this.localWarnings.values(), ...(canonical ? [canonical] : [])];
    const message = candidates.sort((a,b) => b.raised_at-a.raised_at)[0];
    const field = this.shadowRoot.getElementById("error");
    const german = {connection_lost:"OCPP-Verbindung unterbrochen.",connection_failed:"OCPP-Initialisierung fehlgeschlagen.",control_failed:"Wallbox-Steuerbefehl fehlgeschlagen.",grid_failed:"Ladebefehl fehlgeschlagen.",phase_lockout:"Phasenumschaltung vorübergehend gesperrt.",reenable_lockout:"Wiedereinschalten vorübergehend gesperrt.",capabilities:"NETZ-Fähigkeiten fehlen oder sind ungültig.",power_limited:"Angeforderte Leistung durch den Phasenmodus begrenzt.",settings_save:"Einstellungen konnten nicht gespeichert werden.",settings_value:"Einstellung wurde abgelehnt.",battery_reserve:"Batteriereserve konnte nicht gesetzt werden.",wallbox_fault:"Die Wallbox meldet eine Störung."};
    field.textContent = message ? (this._hass.language?.startsWith("de") && german[message.code] || message.text) : "—";
    field.hidden = false;
    field.style.color = message ? "var(--error-color)" : "var(--secondary-text-color)";
  }
  async call(domain, service, data) {
    const action = domain === "button" && service === "press" ? (data.entity_id === this.discovery.roles.charge_action ? "charge_action" : "take_control") : null;
    if (action) {
      const state = this._hass.states[this.discovery.roles[action]];
      if (this.busy || !state || state.state === "unavailable" || (action === "charge_action" ? (state.attributes.charge_action_ready !== true || this.draftWrites.size > 0 || this.edits.power || Object.keys(this.durationEdits).length > 0) : state.attributes.execution_ready !== true) || data.entity_id !== this.discovery.roles[action]) return;
    } else if (!["number", "select", "text"].includes(domain) || !Object.values(this.discovery.roles).includes(data.entity_id)) return;
    const warningId = data.entity_id;
    const binding = this.discovery.entry, runtime = this.runtime;
    this.clearWarning(warningId);
    this.busy = true; this.hass = this._hass;
    try { await this._hass.callService(domain,service,data); }
    catch (err) { if (this.discovery.entry === binding && this.runtime === runtime) this.warn(warningId,err.message || String(err)); }
    finally { this.busy = false; this.hass = this._hass; }
  }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const get = id => this.shadowRoot.getElementById(id), de = hass.language?.startsWith("de");
    const previous = this.discovery?.entry;
    const d = this.discovery = discoverWallboxManager(hass.states, this.config);
    if (previous !== d.entry || (this.runtime && this.runtime !== this.timingAttributes().runtime_incarnation)) { this.stopHold(); this.cancelTiming(); this.edits = {}; this.durationEdits = {}; this.localWarnings.clear(); }
    this.runtime = this.timingAttributes().runtime_incarnation;
    for (const [id,warning] of this.localWarnings) if (warning.snapshot && warning.snapshot !== this.warningSnapshot(id)) this.localWarnings.delete(id);
    this.acknowledgeTiming();
    const timing = this.timingAttributes(), editor = timing.grid_timing_edit;
    if (this.timingEditing && this.timingQueue && this.draftWrites.size === 0 && !this.timingPending && (timing.grid_timing_revision !== this.timingQueue.revision || (editor && (!editor.active || editor.owner !== this.timingQueue.owner)))) {
      this.cancelTiming(); this.durationEdits = {};
    }
    this.scheduleTimingRenewal();
    this.acknowledgePowerStep();
    const state = role => hass.states[d.roles[role]], busy = !!this.busy;
    const connected = state("connected")?.state === "on";
    const authority = connected && available(state("control_authority")) ? state("control_authority").state : null;
    get("title").textContent = this.config.name || "Wallbox Manager";
    get("station").textContent = state("connected")?.attributes.station_id || "—";
    get("takeover").textContent = de ? "Steuerung übernehmen" : "Take control";
    get("takeover").hidden = authority === "remote";
    for (const [id,role] of [["takeover","take_control"]]) {
      const s = state(role);
      get(id).disabled = busy || !connected || !s || s.state === "unavailable" || s.attributes.execution_ready !== true;
    }
    get("takeover").disabled ||= !["local", "remote"].includes(authority);
    get("profile-label").textContent = de ? "Ladeprofil" : "Charging profile";
    get("power-label").textContent = de ? "Angeforderte Ladeleistung" : "Requested charging power";
    get("soll_soc_speicher-label").textContent = de ? "Speicher-Ziel-SoC" : "Battery target SoC";
    this.options(get("profile"), (state("charging_profile")?.attributes.options || []).map(v => [v, {NETZ:de ? "Netz" : "Grid", PV_SURPLUS:de ? "PV-Überschuss" : "PV Surplus",PV_OPTIMUM:"PV Optimum",PV_MAXIMUM:"PV Maximum"}[v] || v]));
    get("profile").value = state("charging_profile")?.state || "";
    get("profile").disabled = busy || !available(state("charging_profile"));
    for (const [id,role] of [["power","soll_power"],["soll_soc_speicher","soll_soc_speicher"]]) {
      const s = state(role), edit = this.edits[id];
      if (edit && (edit.entity !== d.roles[role] || (available(s) && (Number(s.state) === edit.value || (edit.complete && s.attributes.grid_draft_revision > edit.revision))))) delete this.edits[id];
      if (previous !== d.entry || this.shadowRoot.activeElement !== get(id)) get(id).value = this.edits[id] ? this.format(this.edits[id].value, id === "power" ? 1 : null) : available(s) ? this.format(Number(s.state), id === "power" ? 1 : null) : "";
      const disabled = busy || !available(s) || (id === "power" && this.maximum(id) === null), value = parseInput(get(id).value,hass.language), maximum = this.maximum(id);
      get(id).disabled = disabled;
      for (const suffix of ["up","down"]) get(`${id}-${suffix}`).disabled = disabled || !Number.isFinite(value) || (suffix === "down" ? value <= this.minimum(id) : maximum !== null && value >= maximum) || (id === "power" && !s?.attributes[suffix === "down" ? "power_step_down" : "power_step_up"]);
    }
    const profile = state("charging_profile")?.state;
    const optimum = profile === "PV_OPTIMUM", pv = ["PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"].includes(profile);
    get("power-row").hidden = pv;
    get("soll_soc_speicher-row").hidden = profile !== "PV_SURPLUS";
    for (const [id,labels] of Object.entries(optimumFields)) {
      get(`${id}-row`).hidden = id === "optimum_max_discharge_w" ? !pv : !optimum;
      get(`${id}-label`).textContent = labels[de ? 1 : 0];
      const s = state(id), input = get(id);
      input.disabled = busy || !available(s);
      input.min = s?.attributes.min ?? 0; input.max = s?.attributes.max ?? 100;
      if (this.shadowRoot.activeElement !== input) input.value = available(s) ? s.state : "";
    }
    for (const [id,label] of Object.entries(de ? {grid_start_delay:"Startverzögerung",grid_duration:"Ladedauer"} : {grid_start_delay:"Start delay",grid_duration:"Charging duration"})) {
      get(`${id}-row`).hidden = pv;
      get(`${id}-label`).textContent = label;
      const s = state(id);
      for (const part of ["hours","minutes","clear"]) get(`${id}-${part}`).disabled = busy || !durationAvailable(s);
    }
    this.renderTiming();
    const actionState = state("charge_action"), action = actionState?.attributes.charge_action;
    get("charge-action").textContent = ({start: de ? "Ladung starten" : "Start charging", stop: de ? "Ladung beenden" : "Stop charging", apply: de ? "Ladeparameter übernehmen" : "Apply charging parameters"})[action] || (de ? "Ladung starten" : "Start charging");
    get("charge-action").disabled = busy || !actionState || actionState.state === "unavailable" || actionState.attributes.charge_action_ready !== true || this.draftWrites.size > 0 || !!this.edits.power || Object.keys(this.durationEdits).length > 0;
    get("charge-action").title = actionState?.attributes.charge_action_blocked_reason || (action === "stop" && authority !== "remote" ? (de ? "Lokale Ladung kann durch WBM nicht beendet werden" : "WBM cannot stop locally controlled charging") : "");
    get("parameters-label").textContent = de ? "Wallboxparameter" : "Wallbox parameters";
    get("messages-label").textContent = de ? "Meldungen" : "Messages";
    const point = state("soll_power")?.attributes;
    const formatPoint = (phases, amps) => {
      if (![1,2,3].includes(phases) || !Number.isInteger(amps) || amps < 0) return "—";
      return `${de ? `${phases}-phasig` : `${phases}-phase`} ${amps}A`;
    };
    const actualPhases = point?.applied_phase_count;
    const actualAmps = point?.applied_current_a;
    const wantedPhases = point?.requested_phase_count ?? actualPhases;
    const wantedAmps = point?.requested_current_a;
    const actualPoint = connected ? formatPoint(actualPhases, actualAmps) : "—";
    const wantedPoint = connected ? formatPoint(wantedPhases, wantedAmps) : "—";
    get("actual").textContent = profile === "NETZ" && wantedAmps != null && actualPoint !== wantedPoint
      ? `${actualPoint} → ${wantedPoint}` : actualPoint;
    const labels = de ? {connection:"Anschlussstatus",charging:"Ladezustand",energy:"Sitzungsenergie",power:"Leistung",duration:"Dauer"} : {connection:"Connection status",charging:"Charging state",energy:"Session energy",power:"Power",duration:"Duration"};
    const connectorLabels = de ? {available:"Frei",occupied:"Belegt",reserved:"Reserviert",faulted:"Störung"} : {available:"Available",occupied:"Occupied",reserved:"Reserved",faulted:"Faulted"};
    const chargingLabels = de ? {idle:"Bereit",connected:"Verbunden",charging:"Lädt",suspended_vehicle:"Vom Fahrzeug pausiert",suspended_station:"Von Wallbox pausiert"} : {idle:"Idle",connected:"Connected",charging:"Charging",suspended_vehicle:"Paused by vehicle",suspended_station:"Paused by wallbox"};
    for (const [id,role] of [["connection","connector_state"],["charging","charging_state"],["energy","session_energy"],["live-power","power"],["duration","session_duration"]]) {
      const s = state(role); let text="—";
      if (connected && available(s)) {
        if (id === "connection") text = connectorLabels[s.state] || s.state;
        else if (id === "charging") text = chargingLabels[s.state] || s.state;
        else if (id === "duration") { const minutes=Math.floor(Number(s.state)/60); text=`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,"0")}`; }
        else text = `${this.format(id === "live-power" ? Number(s.state)/1000 : Number(s.state), id === "live-power" ? 2 : 1)} ${id === "live-power" ? "kW" : "kWh"}`;
      }
      get(id).textContent = text; get(`${id}-label`).textContent = labels[id === "live-power" ? "power" : id];
    }
    if (d.ambiguous) this.localWarnings.set("binding",{text:de ? "Mehrere Instanzen: entry_id in der Kartenkonfiguration angeben." : "Multiple instances: configure this card's entry_id.",raised_at:0});
    else this.localWarnings.delete("binding");
    this.renderMessage();
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
