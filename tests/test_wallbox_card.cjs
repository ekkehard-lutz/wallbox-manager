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
  return {Card:registry.get('wallbox-manager-card'), discover:vm.runInContext('discoverWallboxManager',context), step:vm.runInContext('powerStep',context), live:vm.runInContext('liveValues',context)};
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
  assert.equal(get('reserve-label').textContent,'Entladereserve');
  assert.equal(get('actual').textContent,'—');
  assert.equal(get('profile-label').textContent,'Ladeprofil');
});

test('progressive steps cross 10 exactly in both directions and obey real ceiling',()=>{
  const {step}=runtime();
  let value=9.8;
  for (const expected of [9.9,10,11,12]) { value=step(value,1); assert.equal(value,expected); }
  for (const expected of [11,10,9.9,9.8]) { value=step(value,-1); assert.equal(value,expected); }
  assert.equal(step(0,-1),0);
  assert.equal(step(21.9,1,22.08),22.08);
  assert.equal(step(99,1),100); // No made-up 99 kW station rating.
});

test('rapid steps stay interactive and preserve newest value during slow service responses',async()=>{
  const data=states(true); data['number.renamed_power'].state='9.8';
  const {card:c,get}=card(data), requests=[];
  c._hass.callService=async (...args)=>new Promise(resolve=>requests.push({args,resolve}));
  const first=c.stepNumber('power',1), second=c.stepNumber('power',1), third=c.stepNumber('power',1);
  assert.deepEqual(requests.map(r=>r.args[2].value),[9.9,10,11]);
  assert.equal(get('power').disabled,false);
  assert.equal(get('power').value,'11');
  requests[0].resolve(); await first;
  assert.equal(get('power').value,'11');
  requests[1].resolve(); requests[2].resolve(); await Promise.all([second,third]);
  data['number.renamed_power'].state='11'; c.hass={...c._hass};
  assert.equal(c.edits.power,undefined);
});

test('fractional input above 10 is preserved and localized',async()=>{
  const {card:c,calls,get}=card(states(true));
  c.hass={...c._hass,language:'de'};
  get('power').value='11,25'; await c.editNumber('power');
  assert.equal(calls[0][2].value,11.25);
  assert.equal(get('power').value,'11,25');
  assert.equal(get('power-label').textContent,'Sollleistung');
  assert.equal(get('energy-label').textContent,'Energie');
});

test('technical limit controls stepping and rejects direct overflow without sending',async()=>{
  const data=states(true);
  data['number.renamed_power'].attributes.technical_max_kw=11.04;
  const {card:c,calls,get}=card(data);
  await c.stepNumber('power',1);
  assert.equal(calls[0][2].value,11.04);
  assert.equal(get('power-up').disabled,true);
  get('power').value='11.05'; await c.editNumber('power');
  assert.equal(calls.length,1);
  assert.equal(get('error').hidden,false);
  assert.match(get('error').textContent,/11.04/);
});

test('unknown technical limit ignores generic HA max and surfaces backend rejection',async()=>{
  const data=states(true); data['number.renamed_power'].attributes.max=100;
  const {card:c,get}=card(data); const sent=[];
  c._hass.callService=async (...args)=>{sent.push(args); throw Error('Requested power exceeds available limit');};
  get('power').value='105'; await c.editNumber('power');
  assert.equal(sent[0][2].value,105);
  assert.match(get('error').textContent,/exceeds available limit/);
  assert.equal(get('error').hidden,false);
});

test('reserve is integral, bounded 0..100 and steps by one',async()=>{
  const data=states(true); data['select.anything'].attributes.battery_configured=true;
  const {card:c,calls,get}=card(data);
  assert.equal(get('reserve-label').textContent,'Discharge reserve');
  for (const invalid of ['-1','100.1','50.5','oops']) {get('reserve').value=invalid; await c.editNumber('reserve');}
  assert.equal(calls.length,0);
  get('reserve').value='99'; await c.stepNumber('reserve',1);
  assert.equal(calls[0][2].value,100);
  assert.equal(get('reserve-up').disabled,true);
  get('reserve').value='0'; await c.stepNumber('reserve',-1);
  assert.equal(calls.length,1);
});

test('device subtitle is independent of optional card title, header selector updates dynamically',()=>{
  const data=states(true); data['select.renamed_owner'].attributes.wallboxes.A.display_name='Garage';
  const {card:c,get}=card(data);
  c.setConfig({type:'custom:wallbox-manager-card',name:'My energy'});
  assert.equal(get('title').textContent,'My energy');
  assert.equal(get('station').textContent,'Garage');
  data['select.renamed_owner'].attributes.wallboxes.A.display_name='Renamed garage';
  c.hass={...c._hass};
  assert.equal(get('station').textContent,'Renamed garage');
  assert.match(c.shadowRoot.innerHTML,/<header>[\s\S]*mdi:ev-station[\s\S]*id="wallbox-row"[\s\S]*<\/header>/);
});

function metered() {
  const data=states(true), valid_until=new Date(Date.now()+60000).toISOString();
  for (const [role,value] of Object.entries({connector_state:'occupied',charging_state:'charging',power:'4100',current_l1:'18',current_l2:'0',current_l3:'0',session_energy:'9.3',session_duration:'4980',energy:'10000'}))
    data[`sensor.renamed_${role}`]=state(role,'A',value,{valid_until,connected:true,session_active:true,unit_of_measurement:role==='power'?'W':undefined});
  return data;
}

test('live status uses measured state and current session despite permission OFF',()=>{
  const data=metered(), {card:c,get}=card(data);
  c.hass={...c._hass,language:'de'};
  assert.equal(get('connection').textContent,'Belegt');
  assert.equal(get('charging').textContent,'Lädt');
  assert.equal(get('energy').textContent,'9,3 kWh');
  assert.equal(get('live-power').textContent,'4,1 kW');
  assert.equal(get('duration').textContent,'1:23:00');
  assert.equal(get('actual').textContent,'1-phasig · 18 A');
  assert.equal(get('status').hidden,true);
  data['switch.another_name'].state='on';
  data['sensor.renamed_charging_state'].state='suspended_vehicle';
  c.hass={...c._hass};
  assert.equal(get('charging').textContent,'Vom Fahrzeug pausiert');
});

test('unbalanced phase currents display a range, not the requested current',()=>{
  const data=metered();
  data['sensor.renamed_current_l2'].state='16'; data['sensor.renamed_current_l3'].state='17';
  data['select.anything'].attributes.applied_current_a=32;
  const {get}=card(data);
  assert.equal(get('actual').textContent,'3-phase · 16–18 A');
});

test('stale, unavailable, completed-session and disconnected data use placeholders',()=>{
  const data=metered(), {card:c,get}=card(data);
  data['sensor.renamed_power'].attributes.valid_until=new Date(Date.now()-1).toISOString();
  data['sensor.renamed_current_l1'].state='unavailable';
  data['sensor.renamed_session_energy'].attributes.session_active=false;
  data['sensor.renamed_session_duration'].state='unknown';
  c.hass={...c._hass};
  for (const id of ['live-power','actual','energy','duration']) assert.equal(get(id).textContent,'—');
  data['select.renamed_owner'].attributes.wallboxes.A.connected=false; c.hass={...c._hass};
  for (const id of ['connection','charging','live-power','actual','energy','duration']) assert.equal(get(id).textContent,'—');
});

test('only explicit unambiguous scope metadata permits broader telemetry joins',()=>{
  const data=states(true), {discover}=runtime();
  data['sensor.evse']=state('power',undefined,'4000',{wallbox_manager_targets:['A'],wallbox_manager_scope:'evse'});
  data['sensor.station']=state('power',undefined,'12000',{wallbox_manager_targets:['A'],wallbox_manager_scope:'station'});
  data['sensor.other']=state('power','B','7000');
  assert.equal(discover(data).roles.power,'sensor.evse');
  data['sensor.connector']=state('power','A','4200');
  assert.equal(discover(data).roles.power,'sensor.connector');
  delete data['sensor.connector']; delete data['sensor.evse'].attributes.wallbox_manager_targets; delete data['sensor.station'].attributes.wallbox_manager_targets;
  assert.equal(discover(data).roles.power,undefined);
});

test('fresh physical feedback can qualify a single available current sample',()=>{
  const data=metered(); delete data['sensor.renamed_current_l2']; delete data['sensor.renamed_current_l3'];
  const {card:c,get}=card(data);
  assert.equal(get('actual').textContent,'—');
  Object.assign(data['select.anything'].attributes,{physical_phase_mode:['l1'],physical_phase_valid_until:new Date(Date.now()+60000).toISOString()});
  c.hass={...c._hass}; assert.equal(get('actual').textContent,'1-phase · 18 A');
  data['select.anything'].attributes.physical_phase_valid_until=new Date(Date.now()-1).toISOString();
  c.hass={...c._hass}; assert.equal(get('actual').textContent,'—');
});

test('responsive two-column layout uses theme variables and contains no old diagnostics',()=>{
  const {card:c,get}=card(states(true));
  assert.match(c.shadowRoot.innerHTML,/grid-template-columns:minmax\(0,1fr\) minmax\(0,1fr\)/);
  assert.match(c.shadowRoot.innerHTML,/@media\(max-width:360px\)/);
  assert.match(c.shadowRoot.innerHTML,/var\(--primary-color\)/);
  assert.match(c.shadowRoot.innerHTML,/:focus-visible/);
  assert.equal(get('status').textContent,'');
  const js=fs.readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js','utf8');
  assert.doesNotMatch(js,/Vehicle not charging|Operating point applied|Active wallbox selected|Fahrzeug lädt nicht/);
});

test('actionable backend failure is readable while normal internal statuses stay hidden',()=>{
  const data=states(true), {card:c,get}=card(data);
  for (const status of ['idle','observing','complete','phase_lockout']) {
    data['select.anything'].attributes.profile_status=status;
    c.hass={...c._hass}; assert.equal(get('status').hidden,true);
  }
  data['select.anything'].attributes.control_status='voltage_unavailable';
  c.hass={...c._hass};
  assert.match(get('status').textContent,/fresh voltage readings/);
  assert.doesNotMatch(get('status').textContent,/voltage_unavailable/);
  assert.equal(get('status').hidden,false);
});

test('keyboard arrows and enter use the same validated numeric service path',async()=>{
  const data=states(true); data['number.renamed_power'].state='9.9';
  const {card:c,calls,get}=card(data);
  let prevented=0;
  get('power').onkeydown({key:'ArrowUp',preventDefault(){prevented++;}});
  await Promise.resolve();
  assert.equal(calls[0][2].value,10);
  get('power').value='11.25';
  get('power').onkeydown({key:'Enter',preventDefault(){prevented++;}});
  await Promise.resolve();
  assert.equal(calls[1][2].value,11.25);
  data['number.renamed_power'].state='11.25'; c.hass={...c._hass};
  await get('power').onchange(); // Enter followed by blur must not dispatch twice.
  assert.equal(calls.length,2);
  assert.equal(prevented,2);
});

test('expiry timer only rerenders and stops on removal; stale session energy is hidden',()=>{
  const data=metered(), {card:c,get}=card(data);
  data['sensor.renamed_session_energy'].attributes.valid_until=new Date(Date.now()-1).toISOString();
  data['sensor.renamed_session_duration'].attributes.start_known=false;
  c.hass={...c._hass};
  assert.equal(get('energy').textContent,'—');
  assert.equal(get('duration').textContent,'—');
  const js=fs.readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js','utf8');
  assert.match(js,/setInterval\(\(\) => \{ if \(this\._hass\) this\.hass = this\._hass; \},1000\)/);
  assert.match(js,/disconnectedCallback\(\).*clearInterval/);
});
