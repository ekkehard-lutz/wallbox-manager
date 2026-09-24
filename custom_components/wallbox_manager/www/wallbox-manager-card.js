/* Wallbox Manager: backend-owned profile controls, no dashboard configuration of batteries. */
class WallboxManagerCard extends HTMLElement {
  setConfig(config) {
    for (const key of ["profile", "power", "permission"]) {
      if (!config[key]) throw new Error(`wallbox-manager-card: ${key} required`);
    }
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML = `<style>
      ha-card {padding:16px} h2 {font-size:18px;margin:0 0 16px}
      label {display:flex;align-items:center;justify-content:space-between;gap:16px;margin:12px 0}
      input,select,button {font:inherit;padding:8px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
      input {width:90px} button {width:100%;cursor:pointer}
      p {font-size:13px;color:var(--secondary-text-color)} [hidden] {display:none}
    </style><ha-card><h2>Wallbox Manager</h2><label><span id="profile-label"></span><select id="profile"></select></label>
    <label><span id="power-label"></span><input id="power" type="number" min="0" max="100" step="0.1"></label>
    <label id="reserve-row"><span id="reserve-label"></span><input id="reserve" type="number" min="0" max="100" step="1"></label>
    <button id="permission"></button><p id="actual"></p><p id="status" role="status"></p></ha-card>`;
    const get = id => this.shadowRoot.getElementById(id);
    get("profile").onchange = e => this.call("select", "select_option", {entity_id: config.profile, option: e.target.value});
    get("power").onchange = e => this.call("number", "set_value", {entity_id: config.power, value: Number(e.target.value)});
    get("reserve").onchange = e => this.call("number", "set_value", {entity_id: config.reserve, value: Number(e.target.value)});
    get("permission").onclick = () => this.call("switch", this._hass.states[config.permission]?.state === "on" ? "turn_off" : "turn_on", {entity_id: config.permission});
  }
  async call(domain, service, data) {
    try { await this._hass.callService(domain, service, data); }
    catch (error) { this.shadowRoot.getElementById("status").textContent = error.message; }
  }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const get = id => this.shadowRoot.getElementById(id);
    const de = hass.language?.startsWith("de");
    const c = this.config, p = hass.states[c.profile], permission = hass.states[c.permission];
    const attrs = p?.attributes || {};
    const enabled = permission?.state === "on";
    const remote = attrs.control_authority === "remote";
    const available = entity => entity && !["unknown", "unavailable"].includes(entity.state);
    get("profile-label").textContent = de ? "Ladeprofil" : "Charging profile";
    get("power-label").textContent = de ? "Sollleistung (kW)" : "Requested power (kW)";
    get("reserve-label").textContent = de ? "Mindestreserve (%)" : "Minimum reserve (%)";
    const select = get("profile");
    const options = p?.attributes.options || [];
    if (Array.from(select.options).map(o => o.value).join() !== options.join()) {
      select.replaceChildren(...options.map(value => {const o=document.createElement("option");o.value=value;o.textContent=value === "NETZ" ? (de ? "Netz" : "Grid") : value;return o;}));
    }
    select.value = p?.state || "";
    select.disabled = !available(p) || !remote;
    for (const [id, entity] of [["power",c.power],["reserve",c.reserve]]) {
      const state = hass.states[entity];
      if (this.shadowRoot.activeElement !== get(id)) get(id).value = available(state) ? state.state : "";
      get(id).disabled = !remote || !available(state);
    }
    get("permission").disabled = !remote || !available(permission);
    get("permission").textContent = enabled ? (de ? "Ladefreigabe deaktivieren" : "Disable charging permission") : (de ? "Laden freigeben" : "Enable charging permission");
    get("reserve-row").hidden = !attrs.battery_configured;
    get("actual").hidden = !attrs.battery_configured;
    get("actual").textContent = attrs.actual_charging ? (de ? "Fahrzeug lädt" : "Vehicle charging") : (de ? "Fahrzeug lädt nicht" : "Vehicle not charging");
    const labels = de ? {idle:"Bereit",observing:"Fahrzeugbeobachtung (1 Minute)",phase_lockout:"Phasensperre – erneuter Versuch in 1 Minute",complete:"Betriebspunkt angewendet",error:"Fehler"} : {idle:"Ready",observing:"Observing vehicle (1 minute)",phase_lockout:"Phase lockout – retry in 1 minute",complete:"Operating point applied",error:"Error"};
    get("status").textContent = [!remote ? (de ? "Keine Fernsteuerungsberechtigung" : "Remote control unavailable") : "", labels[attrs.profile_status], attrs.control_status === "pending" ? (de ? "Betriebspunktwechsel läuft" : "Applying operating point") : "", attrs.execution_blocked_reason, attrs.command_reason, ["error", "write_unconfirmed"].includes(attrs.battery_status) ? (de ? "Batteriereserve: Fehler" : "Battery reserve: error") : "", attrs.battery_status === "external_change" ? (de ? "Externe Reserveänderung beibehalten" : "External reserve change preserved") : ""].filter(Boolean).join(" · ");
  }
  getCardSize() { return 4; }
}
customElements.define("wallbox-manager-card", WallboxManagerCard);
window.customCards = window.customCards || [];
window.customCards.push({type:"wallbox-manager-card",name:"Wallbox Manager",description:"Grid charging profile"});
