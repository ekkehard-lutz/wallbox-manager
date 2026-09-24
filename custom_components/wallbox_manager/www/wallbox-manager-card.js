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

function powerStep(value, direction, maximum = null) {
  // Integer decimal arithmetic prevents 9.8 + 0.1 becoming 9.899999… .
  const step = value < 10 || (value === 10 && direction < 0) ? 0.1 : 1;
  let next = Math.round((value + direction * step) * 1e6) / 1e6;
  if ((value < 10 && next > 10) || (value > 10 && next < 10)) next = 10;
  return Math.max(0, maximum === null ? next : Math.min(maximum, next));
}
function parseInput(value, language) {
  const decimal = new Intl.NumberFormat(language).formatToParts(1.1).find(p => p.type === "decimal").value;
  const normalized = String(value).trim().replace(decimal, ".");
  return /^\d+(?:\.\d+)?$/.test(normalized) ? Number(normalized) : NaN;
}
function available(state) { return state && !["unknown", "unavailable", ""].includes(state.state); }
function fresh(state, now = Date.now()) {
  return available(state) && state.attributes.connected !== false &&
    (!state.attributes.observed_at || Date.parse(state.attributes.observed_at) <= now) &&
    Number.isFinite(Date.parse(state.attributes.valid_until)) && Date.parse(state.attributes.valid_until) > now;
}
function liveValues(states, discovery, language, now = Date.now()) {
  const state = role => states[discovery.roles[role]];
  const de = language?.startsWith("de"), empty = "—";
  const number = value => new Intl.NumberFormat(language, {maximumFractionDigits: 1}).format(value);
  const connected = discovery.inventory[discovery.displayed]?.connected;
  const text = (role, labels) => connected && available(state(role)) && state(role).attributes.connected !== false && (!state(role).attributes.valid_until || fresh(state(role),now)) ? (labels[state(role).state] || empty) : empty;
  const connection = text("connector_state", de ? {available:"Frei", occupied:"Belegt", reserved:"Reserviert", faulted:"Störung"} : {available:"Available", occupied:"Occupied", reserved:"Reserved", faulted:"Faulted"});
  const charging = text("charging_state", de ? {idle:"Bereit",connected:"Verbunden",preparing:"Vorbereitung",charging:"Lädt",suspended_vehicle:"Vom Fahrzeug pausiert",suspended_station:"Von Wallbox pausiert",finishing:"Beendet"} : {idle:"Idle",connected:"Connected",preparing:"Preparing",charging:"Charging",suspended_vehicle:"Paused by vehicle",suspended_station:"Paused by wallbox",finishing:"Finishing"});
  const session = role => connected && available(state(role)) && state(role).attributes.connected !== false && state(role).attributes.session_active === true ? Number(state(role).state) : NaN;
  const energy = fresh(state("session_energy"),now) ? session("session_energy") : NaN, duration = state("session_duration")?.attributes.start_known === false ? NaN : session("session_duration");
  const powerState = state("power");
  const power = connected && fresh(powerState, now) ? Number(powerState.state) / (powerState.attributes.unit_of_measurement === "kW" ? 1 : 1000) : NaN;
  const samples = [1,2,3].map(n => state(`current_l${n}`));
  const currents = samples.map(s => connected && fresh(s, now) ? Number(s.state) : NaN);
  let measured = currents;
  if (!currents.every(Number.isFinite)) {
    const attrs = state("charging_profile")?.attributes || {};
    const phases = attrs.physical_phase_mode;
    measured = Date.parse(attrs.physical_phase_valid_until) > now && Array.isArray(phases) && phases.length ? phases.map(p => currents[Number(String(p).replace(/\D/g, "")) - 1]) : [];
  }
  let actual = empty;
  if (measured.length && measured.every(Number.isFinite)) {
    const active = measured.filter(a => a > 0);
    const low = Math.min(...active), high = Math.max(...active);
    const amps = active.length ? (high - low > 0.05 ? `${number(low)}–${number(high)}` : number(high)) : "0";
    actual = `${active.length}${de ? "-phasig" : "-phase"} · ${amps} A`;
  }
  const seconds = Math.max(0, Math.floor(duration));
  return {connection,charging,energy:Number.isFinite(energy) ? `${number(energy)} kWh` : empty,
    power:Number.isFinite(power) ? `${number(power)} kW` : empty,
    duration:Number.isFinite(duration) ? `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}` : empty, actual};
}

class WallboxManagerCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.edits = {};
    if (!this.shadowRoot) this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML = `<style>
      :host {display:block;color:var(--primary-text-color)}
      ha-card {padding:16px;font-family:var(--paper-font-body1_-_font-family,inherit)}
      header {display:flex;align-items:center;gap:12px;margin-bottom:12px}
      ha-icon {color:var(--primary-color);flex:none} .heading {min-width:0;flex:1}
      h2 {font-size:var(--ha-card-header-font-size,20px);font-weight:500;margin:0;line-height:1.3}
      #station {font-size:14px;font-weight:400;color:var(--secondary-text-color);margin-top:3px;overflow-wrap:anywhere}
      .row {display:flex;align-items:center;justify-content:space-between;gap:12px;margin:10px 0;font-size:14px}
      input,select,button {font:inherit;color:var(--primary-text-color);border:1px solid var(--divider-color);background:var(--card-background-color);border-radius:var(--ha-border-radius-sm,8px);box-sizing:border-box;min-height:40px}
      select {padding:6px 8px;max-width:55%} #wallbox-row {margin:0;max-width:48%} #wallbox {max-width:100%;width:100%}
      .numeric {display:flex;align-items:center;gap:4px;flex:none} input {width:5.3em;text-align:center;padding:6px 3px;font-variant-numeric:tabular-nums}
      #reserve {width:4em} .step {width:34px;padding:0;font-size:19px} .unit {color:var(--secondary-text-color);font-size:13px;width:2em}
      button {cursor:pointer} button:hover:not(:disabled) {background:var(--secondary-background-color)}
      :is(input,select,button):focus-visible {outline:2px solid var(--primary-color);outline-offset:2px}
      :disabled {opacity:.5;cursor:default} #permission,#takeover {width:100%;padding:9px 12px;margin-top:6px;font-size:14px}
      #permission {background:var(--primary-color);color:var(--text-primary-color);border-color:var(--primary-color);font-weight:500}
      #permission:hover:not(:disabled) {filter:brightness(.95)}
      .live {border-top:1px solid var(--divider-color);padding-top:12px;margin-top:16px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px 16px}
      .caption {font-size:12px;color:var(--secondary-text-color);margin-bottom:3px} .reading {font-size:14px;line-height:1.4;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
      #actual {align-self:end} .notice {font-size:13px;color:var(--error-color);line-height:1.4;margin:12px 0 0}
      [hidden] {display:none!important}
      @media(max-width:360px) {ha-card {padding:12px} .row {gap:8px} header {gap:8px;flex-wrap:wrap} #wallbox-row {max-width:100%;width:100%} .numeric {gap:2px} input {width:4.5em} .step {width:32px}}
    </style><ha-card>
      <header><ha-icon icon="mdi:ev-station"></ha-icon><div class="heading"><h2 id="title"></h2><div id="station"></div></div><label id="wallbox-row"><select id="wallbox" aria-label="Active wallbox"></select></label></header>
      <button id="takeover"></button>
      <label class="row"><span id="profile-label"></span><select id="profile"></select></label>
      <div class="row"><label id="power-label" for="power"></label><div class="numeric"><button id="power-down" class="step" type="button">−</button><input id="power" type="text" inputmode="decimal" autocomplete="off"><button id="power-up" class="step" type="button">+</button><span class="unit">kW</span></div></div>
      <div class="row" id="reserve-row"><label id="reserve-label" for="reserve"></label><div class="numeric"><button id="reserve-down" class="step" type="button">−</button><input id="reserve" type="text" inputmode="numeric" autocomplete="off"><button id="reserve-up" class="step" type="button">+</button><span class="unit">%</span></div></div>
      <button id="permission"></button>
      <div class="live"><div><div class="caption" id="connection-label"></div><div class="reading" id="connection"></div></div><div><div class="caption" id="charging-label"></div><div class="reading" id="charging"></div></div><div><div class="caption" id="energy-label"></div><div class="reading" id="energy"></div></div><div><div class="caption" id="live-power-label"></div><div class="reading" id="live-power"></div></div><div><div class="caption" id="duration-label"></div><div class="reading" id="duration"></div></div><div class="reading" id="actual"></div></div>
      <p id="status" class="notice" role="status" hidden></p><p id="error" class="notice" role="alert" hidden></p>
    </ha-card>`;
    const get = id => this.shadowRoot.getElementById(id);
    get("wallbox").onchange = e => this.activate(e.target.value);
    get("takeover").onclick = () => this.activate(this.discovery.displayed);
    get("profile").onchange = e => this.call("select", "select_option", {entity_id:this.discovery.roles.charging_profile, option:e.target.value});
    for (const id of ["power", "reserve"]) {
      get(id).onchange = () => this.editNumber(id);
      get(id).onkeydown = e => {
        if (["ArrowUp", "ArrowDown"].includes(e.key)) { e.preventDefault(); this.stepNumber(id, e.key === "ArrowUp" ? 1 : -1); }
        if (e.key === "Enter") { e.preventDefault(); this.editNumber(id); }
      };
      for (const [suffix, direction] of [["down",-1],["up",1]]) get(`${id}-${suffix}`).onclick = () => this.stepNumber(id,direction);
    }
    get("permission").onclick = () => {
      const id = this.discovery.roles.charging_enabled;
      this.call("switch", this._hass.states[id]?.state === "on" ? "turn_off" : "turn_on", {entity_id:id});
    };
    if (this._hass) this.hass = this._hass;
  }
  connectedCallback() {
    this.disconnectedCallback();
    // Expire displayed meter samples even when HA has no new state to push.
    this.timer = setInterval(() => { if (this._hass) this.hass = this._hass; },1000);
  }
  disconnectedCallback() { if (this.timer) clearInterval(this.timer); this.timer = null; }
  format(value) { return new Intl.NumberFormat(this._hass.language, {maximumFractionDigits:6,useGrouping:false}).format(value); }
  maximum(id) {
    if (id === "reserve") return 100;
    const value = this._hass.states[this.discovery.roles.soll_power]?.attributes.technical_max_kw;
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }
  stepNumber(id, direction) {
    const input = this.shadowRoot.getElementById(id);
    if (input.disabled) return;
    const value = parseInput(input.value, this._hass.language);
    if (!Number.isFinite(value)) return;
    const next = id === "power" ? powerStep(value,direction,this.maximum(id)) : Math.min(100,Math.max(0,Math.round(value) + direction));
    if (next === value) return;
    input.value = this.format(next);
    return this.editNumber(id);
  }
  async editNumber(id) {
    const input = this.shadowRoot.getElementById(id), get = key => this.shadowRoot.getElementById(key);
    if (input.disabled) return;
    const value = parseInput(input.value,this._hass.language), maximum = this.maximum(id);
    const de = this._hass.language?.startsWith("de");
    if (!Number.isFinite(value) || value < 0 || (maximum !== null && value > maximum) || (id === "reserve" && !Number.isInteger(value))) {
      get("error").textContent = de ? `Bitte einen gültigen Wert ab 0${maximum === null ? "" : ` bis ${this.format(maximum)}`} eingeben${id === "reserve" ? " (ganze Prozent)" : ""}.` : `Enter a valid value from 0${maximum === null ? "" : ` to ${this.format(maximum)}`}${id === "reserve" ? " (whole percent)" : ""}.`;
      get("error").hidden = false;
      return;
    }
    const entity = this.discovery.roles[id === "power" ? "soll_power" : "min_soc"];
    if ((!this.edits[id] && available(this._hass.states[entity]) && Number(this._hass.states[entity].state) === value) || (this.edits[id]?.entity === entity && this.edits[id].value === value)) return;
    const edit = this.edits[id] = {entity,value};
    input.value = this.format(value);
    get("error").hidden = true;
    this.hass = this._hass;
    try { await this._hass.callService("number","set_value",{entity_id:entity,value}); }
    catch (err) {
      if (this.edits[id] === edit && this.discovery.roles[id === "power" ? "soll_power" : "min_soc"] === entity) {
        delete this.edits[id];
        input.value = this.format(Number(this._hass.states[entity].state));
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
    if (previous !== d.displayed) { this.edits = {}; get("error").hidden = true; }
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
    get("power-label").textContent = de ? "Sollleistung" : "Requested power";
    get("reserve-label").textContent = de ? "Entladereserve" : "Discharge reserve";
    this.options(get("profile"), (state("charging_profile")?.attributes.options || []).map(value => [value,value === "NETZ" ? (de ? "Netz" : "Grid") : value]));
    get("profile").value = state("charging_profile")?.state || "";
    get("profile").disabled = busy || !ready || !available(state("charging_profile"));
    for (const [id,role] of [["power","soll_power"],["reserve","min_soc"]]) {
      const s = state(role), edit = this.edits[id];
      if (edit && (edit.entity !== d.roles[role] || (available(s) && Number(s.state) === edit.value))) delete this.edits[id];
      if (previous !== d.displayed || this.shadowRoot.activeElement !== get(id)) get(id).value = this.edits[id] ? this.format(this.edits[id].value) : available(s) ? this.format(Number(s.state)) : "";
      const disabled = busy || !ready || !available(s);
      get(id).disabled = disabled;
      const value = parseInput(get(id).value,hass.language), maximum = this.maximum(id);
      for (const suffix of ["up","down"]) {
        get(`${id}-${suffix}`).disabled = disabled || !Number.isFinite(value) || (suffix === "down" ? value <= 0 : maximum !== null && value >= maximum);
        get(`${id}-${suffix}`).ariaLabel = `${get(`${id}-label`).textContent} ${suffix === "up" ? (de ? "erhöhen" : "increase") : (de ? "verringern" : "decrease")}`;
      }
    }
    get("permission").disabled = busy || !ready || !available(permission);
    get("permission").textContent = busy ? (de ? "Bitte warten …" : "Please wait …") : enabled ? (de ? "Ladefreigabe deaktivieren" : "Disable charging permission") : (de ? "Laden freigeben" : "Enable charging permission");
    get("reserve-row").hidden = !attrs.battery_configured;
    const labels = de ? {connection:"Anschlussstatus",charging:"Ladezustand",energy:"Energie",power:"Leistung",duration:"Dauer"} : {connection:"Connection status",charging:"Charging state",energy:"Energy",power:"Power",duration:"Duration"};
    for (const [key,value] of Object.entries(liveValues(hass.states,d,hass.language))) {
      const id = key === "power" ? "live-power" : key;
      get(id).textContent = value;
      if (labels[key]) get(`${id}-label`).textContent = labels[key];
    }
    const errors = de ? {previous_off_unconfirmed:"Vorherige Wallbox: Ladefreigabe OFF nicht bestätigt.",previous_wallbox_unavailable:"Vorherige Wallbox nicht erreichbar.",previous_authority_unknown:"Steuerung der vorherigen Wallbox unbekannt.",battery_restore_pending:"Batteriereserve konnte noch nicht wiederhergestellt werden.",takeover_failed:"Steuerungsübernahme fehlgeschlagen.",wallbox_unavailable:"Wallbox nicht erreichbar.",off_unconfirmed:"Ladefreigabe OFF nicht bestätigt.",takeover_stale:"Steuerungsübernahme bitte erneut ausführen.",transition_failed:"Wallbox-Wechsel fehlgeschlagen."} : {previous_off_unconfirmed:"Previous wallbox: charging permission OFF not confirmed.",previous_wallbox_unavailable:"Previous wallbox unavailable.",previous_authority_unknown:"Previous wallbox authority unknown.",battery_restore_pending:"Battery reserve restoration pending.",takeover_failed:"Control takeover failed.",wallbox_unavailable:"Wallbox unavailable.",off_unconfirmed:"Charging permission OFF not confirmed.",takeover_stale:"Please take control again.",transition_failed:"Wallbox switch failed."};
    const blocked = de ? {voltage_unavailable:"Keine aktuellen Spannungswerte. Messdaten der Wallbox prüfen.",capabilities_unavailable:"Technische Grenzen fehlen. Verbindung und Wallbox-Konfiguration prüfen.",direction_unreachable:"Sollleistung mit der gewählten Annäherung nicht erreichbar. Sollleistung oder Annäherung anpassen.",zero_current_unverified:"Nullleistung wird nicht bestätigt unterstützt. Ladefreigabe deaktivieren, um zu stoppen.",electrical_limit:"Kein Ladepunkt innerhalb der Stromgrenzen. Einstellungen prüfen.",no_eligible_mode:"Keine unterstützte Phasenkonfiguration verfügbar. Wallbox-Konfiguration prüfen."} : {voltage_unavailable:"No fresh voltage readings. Check wallbox metering.",capabilities_unavailable:"Technical limits unavailable. Check connection and wallbox configuration.",direction_unreachable:"Requested power cannot meet the selected approximation policy. Adjust power or approximation.",zero_current_unverified:"Zero-power control is unverified. Disable charging permission to stop.",electrical_limit:"No charging point within current limits. Check settings.",no_eligible_mode:"No supported phase configuration available. Check wallbox configuration."};
    const messages = [errors[d.state?.attributes.ownership_status], blocked[attrs.control_status],
      d.displayed && d.inventory[d.displayed]?.connected === false ? (de ? "Wallbox nicht verbunden. Verbindung prüfen." : "Wallbox disconnected. Check its connection.") : "",
      d.active && !d.inventory[d.active] ? (de ? "Aktive Wallbox fehlt. Bitte Verbindung prüfen." : "Active wallbox missing. Check its connection.") : "",
      ["error","write_unconfirmed"].includes(attrs.battery_status) ? (de ? "Batteriereserve konnte nicht gesetzt werden. Batterie prüfen." : "Could not set battery reserve. Check the battery.") : "",
      ["failed","unsupported","temporarily_rejected"].includes(attrs.command_status) && !["phase_lockout","observing"].includes(attrs.profile_status) ? (de ? "Ladeeinstellung nicht angewendet. Verbindung und Wallbox prüfen." : "Charging setting not applied. Check the connection and wallbox.") : "",
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
if (!window.customCards.some(c => c.type === "wallbox-manager-card")) window.customCards.push({type:"wallbox-manager-card",name:"Wallbox Manager",description:"Automatically discovered wallboxes and Grid profile"});
