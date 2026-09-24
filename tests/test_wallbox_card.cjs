/* Run: node --test tests/test_wallbox_card.cjs */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function runtime() {
  class Node {
    constructor() { this.children = []; this.value = ''; this.textContent = ''; }
    replaceChildren(...nodes) { this.children = nodes; }
  }
  class HTMLElement {
    attachShadow() {
      const nodes = {};
      this.shadowRoot = {getElementById: id => nodes[id] ||= new Node(), activeElement: null};
    }
  }
  const registry = new Map();
  const context = vm.createContext({HTMLElement, document:{createElement: () => new Node()}, window:{}, customElements:{get: key=>registry.get(key),define:(key,value)=>registry.set(key,value)}});
  vm.runInContext(fs.readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js','utf8'), context);
  return {Card:registry.get('wallbox-manager-card'), discover:vm.runInContext('discoverWallboxManager',context)};
}
function state(role, target, value, extra={}) {
  return {state:value,attributes:{wallbox_manager_role:role,wallbox_manager_target:target,...extra}};
}
function states(ready=false) {
  return {
    'select.renamed_owner':state('active_wallbox',null, ready ? 'A' : 'unknown', {active_wallbox:ready ? 'A':null,profile_control_ready:ready,ownership_status:ready?'ready':'take_control_required',wallboxes:{A:{name:'Wallbox01',connected:true}}}),
    'select.anything':state('charging_profile','A','NETZ',{options:['NETZ'],profile_control_ready:ready}),
    'number.renamed_power':state('soll_power','A','11'),
    'switch.another_name':state('charging_enabled','A','off'),
    'number.x':state('min_soc','A','20'),
  };
}
function card(data) {
  const {Card} = runtime();
  const card = new Card();
  card.setConfig({type:'custom:wallbox-manager-card'});
  const calls = [];
  card.hass = {states:data, language:'en',callService:async (...args)=>calls.push(args)};
  return {card,calls,get:id=>card.shadowRoot.getElementById(id)};
}

test('only type required; single wallbox hides selector, shows explicit takeover',async()=>{
  const {card:c,calls,get} = card(states());
  assert.equal(get('wallbox-row').hidden,true);
  assert.equal(get('takeover').hidden,false);
  assert.equal(get('permission').disabled,true);
  await c.activate('A');
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'select');
  assert.equal(calls[0][2].entity_id,'select.renamed_owner');
  assert.equal(calls[0][2].option,'A');
  assert.equal(get('permission').disabled,true); // UI cannot assume successful readiness.
});

test('renamed entity IDs and names are resolved by role and stable target',()=>{
  const data = states(true);
  const {discover} = runtime();
  data['number.totally_different'] = {...data['number.renamed_power'],attributes:{...data['number.renamed_power'].attributes,friendly_name:'Whatever'}};
  delete data['number.renamed_power'];
  assert.equal(discover(data).roles.soll_power,'number.totally_different');
});

test('adding and removing wallboxes requires no card reconfiguration',()=>{
  const data = states(true);
  const {card:c,get} = card(data);
  data['select.renamed_owner'].attributes.wallboxes.B = {name:'Wallbox02',connected:true};
  c.hass = {...c._hass, states:data};
  assert.equal(get('wallbox-row').hidden,false);
  assert.equal(get('wallbox').children.length,3);
  delete data['select.renamed_owner'].attributes.wallboxes.A;
  c.hass = {...c._hass,states:data};
  assert.equal(c.discovery.displayed,null); // Missing active target never silently selects B.
  assert.equal(get('permission').disabled,true);
  assert.match(get('status').textContent,/missing/);
});

test('switching active target changes controls to B stored values',()=>{
  const data=states(true);
  data['select.renamed_owner'].attributes.wallboxes.B={name:'B',connected:true};
  data['select.b']=state('charging_profile','B','NETZ',{profile_control_ready:true,options:['NETZ']});
  data['number.b']=state('soll_power','B','7');
  data['switch.b']=state('charging_enabled','B','off');
  data['select.renamed_owner'].attributes.active_wallbox='B';
  const {get}=card(data);
  assert.equal(get('power').value,'7');
  assert.equal(get('permission').disabled,false);
  assert.match(get('permission').textContent,/Enable/);
});

test('pending transition inhibits every command and no automatic service runs',()=>{
  const data=states(true);
  data['select.renamed_owner'].attributes.transition_pending=true;
  const {get,calls}=card(data);
  for (const id of ['permission','power','profile','wallbox']) assert.equal(get(id).disabled,true);
  assert.equal(calls.length,0);
});

test('battery UI appears only with backend battery configuration',()=>{
  const data=states(true);
  const {card:c,get}=card(data);
  assert.equal(get('reserve-row').hidden,true);
  data['select.anything'].attributes.battery_configured=true;
  c.hass={...c._hass,states:data,language:'de'};
  assert.equal(get('reserve-row').hidden,false);
  assert.equal(get('actual').hidden,false);
  assert.equal(get('profile-label').textContent,'Ladeprofil');
});
