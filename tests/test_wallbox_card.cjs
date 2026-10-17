// Fresh focused tests for the passive card, not the beta.18 frontend suite.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js', 'utf8');

function load() {
  const registry = new Map();
  class Element {
    constructor() { this.style = {}; this.value = ''; this.validity = {}; }
    replaceChildren(...children) { this.children = children; }
    checkValidity() { return true; }
    focus() {}
  }
  class Host {
    attachShadow() {
      const nodes = new Map();
      this.shadowRoot = {activeElement: null,
        set innerHTML(html) { this.html = html; for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Element()); },
        getElementById(id) { assert.ok(nodes.has(id), `Real rendered element missing: ${id}`); return nodes.get(id); }};
    }
  }
  const context = {HTMLElement:Host, customElements:{get:k=>registry.get(k), define:(k,v)=>registry.set(k,v)},
    window:{addEventListener(){},removeEventListener(){}}, document:{createElement:()=>new Element()},
    setTimeout,clearTimeout,setInterval(){assert.fail('Passive card must not start an idle interval');}};
  vm.runInNewContext(source, context);
  return registry.get('wallbox-manager-card');
}
function states(entry='E') {
  const result = {};
  for (const [role,value] of Object.entries({connected:'on',charging_profile:'NETZ',soll_power:'7.2',min_soc:'20',soll_soc_speicher:'95',pv_approximation:'down',
    charging_enabled:'on',connector_state:'available',charging_state:'idle',power:'1500',session_energy:'1.2',session_duration:'60',
    grid_start_delay:'',grid_duration:'02:30',optimum_lower_soc:'20',optimum_upper_soc:'80',optimum_max_discharge_w:'3000',estimated_daily_house_consumption_kwh:'12'})) {
    result[`${entry}.${role}`] = {state:value,attributes:{wallbox_manager_entry:entry,wallbox_manager_role:role,wallbox_manager_scope:'connector',station_id:`station-${entry}`,
      options:role==='charging_profile'?['NETZ','PV_SURPLUS','PV_OPTIMUM','PV_MAXIMUM']:undefined,min:0,max:100}};
  }
  return result;
}
function card(data=states(), config={}, language='en') {
  const Card = load(), card = new Card(), calls=[];
  card.setConfig({type:'custom:wallbox-manager-card',...config});
  card.hass={states:data,language,callService:async(...args)=>calls.push(args)};
  return {card,calls,get:id=>card.shadowRoot.getElementById(id)};
}

test('familiar layout, read-only permission and no multi-wallbox UI',()=>{
  const {card:c,get,calls}=card();
  assert.match(c.shadowRoot.html,/ha-card/);
  assert.match(c.shadowRoot.html,/class="numeric"/);
  assert.doesNotMatch(c.shadowRoot.html,/id="wallbox"/);
  assert.equal(get('takeover').disabled,true);
  assert.equal(get('permission').disabled,true);
  assert.equal(get('takeover').onclick,undefined);
  assert.equal(get('permission').onclick,undefined);
  assert.match(get('permission').textContent,/enabled/);
  assert.match(get('status').textContent,/not implemented/);
  assert.equal(get('connection').textContent,'Available');
  assert.equal(get('live-power').textContent,'1.5 kW');
  assert.equal(get('energy').textContent,'1.2 kWh');
  assert.equal(calls.length,0);
  c.connectedCallback(); c.disconnectedCallback();
});

test('multiple instances require explicit entry; two cards never cross entries',async()=>{
  const data={...states('A'),...states('B')};
  const missing=card(data);
  assert.equal(Object.keys(missing.card.discovery.roles).length,0);
  assert.match(missing.get('status').textContent,/entry_id/);
  const a=card(data,{entry_id:'A'}),b=card(data,{entry_id:'B'});
  assert.equal(a.get('station').textContent,'station-A');
  assert.equal(b.get('station').textContent,'station-B');
  await a.get('profile').onchange({target:{value:'PV_SURPLUS'}});
  await b.get('profile').onchange({target:{value:'PV_OPTIMUM'}});
  assert.equal(a.calls[0][2].entity_id,'A.charging_profile');
  assert.equal(b.calls[0][2].entity_id,'B.charging_profile');
});

test('existing configured entity can bind a card to its entry',()=>{
  const {card:c}=card({...states('A'),...states('B')},{entity:'B.soll_power'});
  assert.equal(c.discovery.entry,'B');
});

test('number and duration edits keep local service interactions',async()=>{
  const {card:c,get,calls}=card();
  get('power').value='8.4'; await c.editNumber('power');
  get('grid_duration-hours').value='03'; get('grid_duration-minutes').value='15';
  await c.editDuration('grid_duration');
  assert.equal(calls[0][0],'number'); assert.equal(calls[0][2].value,8.4);
  assert.equal(calls[1][0],'text'); assert.equal(calls[1][2].value,'03:15');
  assert.equal(calls[1][2].entity_id,'E.grid_duration');
});

test('German controls stay disabled and physical OFF stays visible',()=>{
  const data=states(); data['E.charging_enabled'].state='off';
  const {get}=card(data,{},'de');
  assert.equal(get('takeover').textContent,'Steuerung übernehmen');
  assert.match(get('permission').textContent,/inaktiv/);
  assert.equal(get('permission').disabled,true);
  assert.equal(get('connection').textContent,'Frei');
  assert.match(get('status').textContent,/ausschließlich lokal/);
});

test('card action boundary refuses charging domains and foreign entity IDs',async()=>{
  const {card:c,calls}=card();
  await c.call('switch','turn_on',{entity_id:'E.charging_enabled'});
  await c.call('button','press',{entity_id:'E.take_control'});
  await c.call('select','select_option',{entity_id:'FOREIGN.charging_profile',option:'NETZ'});
  assert.equal(calls.length,0);
});

test('unavailable measurement and permission are never fabricated as zero or OFF',()=>{
  const data=states(); data['E.power'].state='unavailable'; data['E.charging_enabled'].state='unknown';
  const {get}=card(data);
  assert.equal(get('live-power').textContent,'—');
  assert.match(get('permission').textContent,/unknown/);
});
