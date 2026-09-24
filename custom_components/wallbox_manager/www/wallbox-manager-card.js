/* Stable semantic roles are emitted by the integration, independent of entity IDs. */
function discoverWallboxManager(states) {
  const entries = Object.entries(states || {});
  const selectors = entries.filter(([,s]) => s.attributes.wallbox_manager_role === "active_wallbox");
  const selector = selectors.find(([,s]) => s.state !== "unavailable");
  const inventory = selector?.[1].attributes.wallboxes || {};
  const active = selector?.[1].attributes.active_wallbox;
  const keys = Object.keys(inventory);
  const displayed = active && inventory[active] ? active : (!active && keys.length === 1 ? keys[0] : null);
  const roles = {};
  for (const [id, state] of entries) {
    const a = state.attributes;
    if (displayed && a.wallbox_manager_target === displayed) roles[a.wallbox_manager_role] = id;
  }
  return {selector: selector?.[0], state: selector?.[1], inventory, active, displayed, roles, multiple: keys.length > 1};
}

class WallboxManagerCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML = `<style>
      ha-card {padding:16px} h2 {font-size:18px;margin:0 0 16px}
      label {display:flex;align-items:center;justify-content:space-between;gap:16px;margin:12px 0}
      input,select,button {font:inherit;padding:8px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
      input {width:90px} button {width:100%;cursor:pointer;margin-top:8px}
      p {font-size:13px;color:var(--secondary-text-color)} [hidden] {display:none}
    </style><ha-card><h2 id="title"></h2>
    <label id="wallbox-row"><span id="wallbox-label"></span><select id="wallbox"></select></label>
    <button id="takeover"></button>
    <label><span id="profile-label"></span><select id="profile"></select></label>
    <label><span id="power-label"></span><input id="power" type="number" min="0" max="100" step="0.1"></label>
    <label id="reserve-row"><span id="reserve-label"></span><input id="reserve" type="number" min="0" max="100" step="1"></label>
    <button id="permission"></button><p id="actual"></p><p id="status" role="status"></p><p id="error" role="alert"></p></ha-card>`;
    const get = id => this.shadowRoot.getElementById(id);
    get("wallbox").onchange = e => this.activate(e.target.value);
    get("takeover").onclick = () => this.activate(this.discovery.displayed);
    get("profile").onchange = e => this.call("select", "select_option", {entity_id: this.discovery.roles.charging_profile, option: e.target.value});
    get("power").onchange = e => this.call("number", "set_value", {entity_id: this.discovery.roles.soll_power, value: Number(e.target.value)});
    get("reserve").onchange = e => this.call("number", "set_value", {entity_id: this.discovery.roles.min_soc, value: Number(e.target.value)});
    get("permission").onclick = () => {
      const id = this.discovery.roles.charging_enabled;
      this.call("switch", this._hass.states[id]?.state === "on" ? "turn_off" : "turn_on", {entity_id: id});
    };
    if (this._hass) this.hass = this._hass;
  }
  activate(option) {
    if (option && this.discovery.selector) return this.call("select", "select_option", {entity_id: this.discovery.selector, option});
  }
  async call(domain, service, data) {
    const error = this.shadowRoot.getElementById("error");
    error.textContent = "";
    this.busy = true;
    this.hass = this._hass;
    try { await this._hass.callService(domain, service, data); }
    catch (err) { error.textContent = err.message; }
    finally { this.busy = false; this.hass = this._hass; }
  }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const get = id => this.shadowRoot.getElementById(id);
    const de = hass.language?.startsWith("de");
    const d = this.discovery = discoverWallboxManager(hass.states);
    const state = role => hass.states[d.roles[role]];
    const attrs = state("charging_profile")?.attributes || {};
    const ready = !!(attrs.profile_control_ready && d.state?.attributes.profile_control_ready && d.active === d.displayed);
    const busy = this.busy || d.state?.attributes.transition_pending;
    const available = s => s && !["unknown", "unavailable"].includes(s.state);
    const permission = state("charging_enabled"), enabled = permission?.state === "on";
    get("title").textContent = this.config.name || "Wallbox Manager";
    get("wallbox-label").textContent = de ? "Aktive Wallbox" : "Active wallbox";
    get("wallbox-row").hidden = !d.multiple;
    const options = Object.entries(d.inventory);
    this.options(get("wallbox"), [["", de ? "Wallbox auswählen" : "Select wallbox"], ...options.map(([key, value]) => [key, value.name])]);
    get("wallbox").value = d.active || "";
    get("wallbox").disabled = !!busy;
    get("takeover").textContent = de ? "Steuerung übernehmen" : "Take control";
    get("takeover").hidden = ready || !d.displayed;
    get("takeover").disabled = !!busy || !d.inventory[d.displayed]?.connected;
    get("profile-label").textContent = de ? "Ladeprofil" : "Charging profile";
    get("power-label").textContent = de ? "Sollleistung (kW)" : "Requested power (kW)";
    get("reserve-label").textContent = de ? "Mindestreserve (%)" : "Minimum reserve (%)";
    this.options(get("profile"), (state("charging_profile")?.attributes.options || []).map(value => [value, value === "NETZ" ? (de ? "Netz" : "Grid") : value]));
    get("profile").value = state("charging_profile")?.state || "";
    get("profile").disabled = !!busy || !ready || !available(state("charging_profile"));
    for (const [id, role] of [["power", "soll_power"], ["reserve", "min_soc"]]) {
      const s = state(role);
      if (this.shadowRoot.activeElement !== get(id)) get(id).value = available(s) ? s.state : "";
      get(id).disabled = !!busy || !ready || !available(s);
    }
    get("permission").disabled = !!busy || !ready || !available(permission);
    get("permission").textContent = enabled ? (de ? "Ladefreigabe deaktivieren" : "Disable charging permission") : (de ? "Laden freigeben" : "Enable charging permission");
    get("reserve-row").hidden = !attrs.battery_configured;
    get("actual").hidden = !attrs.battery_configured;
    get("actual").textContent = attrs.actual_charging ? (de ? "Fahrzeug lädt" : "Vehicle charging") : (de ? "Fahrzeug lädt nicht" : "Vehicle not charging");
    const labels = de ? {idle:"Bereit",observing:"Fahrzeugbeobachtung (1 Minute)",phase_lockout:"Phasensperre – erneuter Versuch in 1 Minute",complete:"Betriebspunkt angewendet",error:"Fehler",take_control_required:"Steuerung übernehmen; anschließend Laden separat freigeben",switching:"Wechsel: Ladefreigaben werden deaktiviert und bestätigt",ready:"Aktive Wallbox gewählt – Ladefreigabe ist separat",previous_off_unconfirmed:"Vorherige Wallbox: OFF nicht bestätigt",previous_wallbox_unavailable:"Vorherige Wallbox nicht erreichbar",previous_authority_unknown:"Steuerung der vorherigen Wallbox unbekannt",battery_restore_pending:"Batteriereserve noch nicht wiederhergestellt",takeover_failed:"Steuerungsübernahme fehlgeschlagen"} : {idle:"Ready",observing:"Observing vehicle (1 minute)",phase_lockout:"Phase lockout – retry in 1 minute",complete:"Operating point applied",error:"Error",take_control_required:"Take control, then enable charging separately",switching:"Switching: disabling and confirming charging permission",ready:"Active wallbox selected – charging permission is separate",previous_off_unconfirmed:"Previous wallbox: OFF not confirmed",previous_wallbox_unavailable:"Previous wallbox unavailable",previous_authority_unknown:"Previous wallbox authority unknown",battery_restore_pending:"Battery reserve restoration pending",takeover_failed:"Authority takeover failed"};
    const ownership = d.state?.attributes.ownership_status;
    get("status").textContent = [!d.selector ? (de ? "Wallbox Manager wird gesucht …" : "Discovering Wallbox Manager …") : labels[ownership] || ownership, d.active && !d.inventory[d.active] ? (de ? "Aktive Wallbox fehlt; keine automatische Neuauswahl" : "Active wallbox missing; no automatic replacement") : "", labels[attrs.profile_status], attrs.control_status === "pending" ? (de ? "Betriebspunktwechsel läuft" : "Applying operating point") : "", attrs.execution_blocked_reason, attrs.command_reason, ["error", "write_unconfirmed"].includes(attrs.battery_status) ? (de ? "Batteriereserve: Fehler" : "Battery reserve: error") : "", attrs.battery_status === "external_change" ? (de ? "Externe Reserveänderung beibehalten" : "External reserve change preserved") : ""].filter(Boolean).join(" · ");
  }
  options(select, options) {
    const signature = JSON.stringify(options);
    if (select._signature === signature) return;
    select._signature = signature;
    select.replaceChildren(...options.map(([value, label]) => { const o = document.createElement("option"); o.value = value; o.textContent = label; return o; }));
  }
  getCardSize() { return 5; }
  static getStubConfig() { return {}; }
}
if (!customElements.get("wallbox-manager-card")) customElements.define("wallbox-manager-card", WallboxManagerCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(c => c.type === "wallbox-manager-card")) window.customCards.push({type:"wallbox-manager-card", name:"Wallbox Manager", description:"Automatically discovered wallboxes and Grid profile"});
