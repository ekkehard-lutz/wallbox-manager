/* Run: node --test tests/test_wallbox_card.cjs */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function runtime() {
  let ms=0, next=0;
  const timers=new Map(), listeners=new Map();
  const schedule=(fn,delay,interval=0)=>{ const id=++next; timers.set(id,{fn,at:ms+delay,interval}); return id; };
  const clock={advance(delta) {
    const end=ms+delta;
    while (true) {
      const due=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if (!due) break;
      const [id,t]=due; ms=t.at; timers.delete(id);
      if(t.interval) timers.set(id,{...t,at:ms+t.interval});
      t.fn();
    }
    ms=end;
  },get pending(){return timers.size;},listeners};
  class Node {
    constructor() { this.style = {}; this.children = []; this.value = ''; this.textContent = ''; }
    replaceChildren(...nodes) { this.children = nodes; }
    focus() {}
    setPointerCapture(id) { this.pointer=id; }
    hasPointerCapture(id) { return this.pointer===id; }
    releasePointerCapture() { this.pointer=null; }
    getBoundingClientRect() { return {left:0,top:0,right:34,bottom:40}; }
  }
  class HTMLElement {
    attachShadow() {
      const nodes = {};
      this.shadowRoot = {getElementById: id => nodes[id] ||= new Node(), activeElement: null};
    }
  }
  const registry = new Map();
  const context = vm.createContext({HTMLElement, document:{createElement: () => new Node()}, setTimeout:(fn,delay)=>schedule(fn,delay),clearTimeout:id=>timers.delete(id),setInterval:(fn,delay)=>schedule(fn,delay,delay),clearInterval:id=>timers.delete(id),window:{addEventListener:(event,fn)=>listeners.set(event,fn),removeEventListener:(event)=>listeners.delete(event)}, customElements:{get: key=>registry.get(key),define:(key,value)=>registry.set(key,value)}});
  const source = fs.readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js','utf8');
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.testHelpers = {sessionDuration,discoverWallboxManager,powerStep,liveValues};})();'), context);
  return {context, source, clock, duration:context.testHelpers.sessionDuration, Card:registry.get('wallbox-manager-card'), discover:context.testHelpers.discoverWallboxManager, step:context.testHelpers.powerStep, live:context.testHelpers.liveValues};
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
  const {Card,clock} = runtime();
  const card = new Card();
  card.setConfig({type:'custom:wallbox-manager-card'});
  const calls = [];
  card.hass = {states:data, language:'en',callService:async (...args)=>calls.push(args)};
  return {card,calls,clock,get:id=>card.shadowRoot.getElementById(id)};
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
    data[`sensor.renamed_${role}`]=state(role,'A',value,{valid_until,connected:true,session_active:true,unit_of_measurement:role==='power'?'W':role==='session_duration'?'s':undefined,duration_sampled_at:new Date().toISOString()});
  return data;
}

test('live status uses measured state and current session despite permission OFF',()=>{
  const data=metered(), {card:c,get}=card(data);
  c.hass={...c._hass,language:'de'};
  assert.equal(get('connection').textContent,'Belegt');
  assert.equal(get('charging').textContent,'Lädt');
  assert.equal(get('energy').textContent,'9,3 kWh');
  assert.equal(get('live-power').textContent,'4,1 kW');
  assert.equal(get('duration').textContent,'1:23');
  assert.equal(get('actual').textContent,'—'); // Permission/meter data do not prove an applied point.
  assert.equal(get('status').hidden,true);
  data['switch.another_name'].state='on';
  data['sensor.renamed_charging_state'].state='suspended_vehicle';
  c.hass={...c._hass};
  assert.equal(get('charging').textContent,'Vom Fahrzeug pausiert');
});

test('applied current replaces measured-current range while power remains measured',()=>{
  const data=metered();
  data['sensor.renamed_current_l2'].state='16'; data['sensor.renamed_current_l3'].state='17';
  Object.assign(data['select.anything'].attributes,{applied_current_a:9,applied_phase_count:3,actual_enabled:true,control_authority:'remote'});
  const {get}=card(data);
  assert.equal(get('actual').textContent,'3-phase · 9 A');
  assert.equal(get('live-power').textContent,'4.1 kW');
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

test('physical feedback and current samples cannot masquerade as applied limits',()=>{
  const data=metered(); delete data['sensor.renamed_current_l2']; delete data['sensor.renamed_current_l3'];
  const {card:c,get}=card(data);
  assert.equal(get('actual').textContent,'—');
  Object.assign(data['select.anything'].attributes,{physical_phase_mode:['l1'],physical_phase_valid_until:new Date(Date.now()+60000).toISOString()});
  c.hass={...c._hass}; assert.equal(get('actual').textContent,'—');
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
  assert.match(js,/disconnectedCallback\(\)[\s\S]*clearInterval/);
});

const press={button:0,isPrimary:true,pointerId:1,preventDefault(){}};
test('icon is 48px and both numeric fields use the former narrow 4em width',()=>{
  const {card:c}=card(states(true)), html=c.shadowRoot.innerHTML;
  assert.match(html,/--mdc-icon-size:48px;width:48px;height:48px/);
  assert.match(html,/input \{width:4em;/);
  assert.doesNotMatch(html,/#reserve \{width:|width:5.3em|width:4.5em/);
});

test('short pointer click changes once, keyboard clicks still work',()=>{
  const {get,calls,clock}=card(states(true));
  get('power-up').onpointerdown(press);
  assert.equal(calls.length,1);
  get('power-up').onpointerup();
  get('power-up').onclick({detail:1});
  clock.advance(2000);
  assert.equal(calls.length,1);
  get('power-up').onclick({detail:0});
  assert.equal(calls.length,2);
  assert.equal(clock.pending,0);
});

test('hold repeats after 450ms at 150ms intervals across 9.9/10 boundary',()=>{
  const data=states(true);data['number.renamed_power'].state='9.8';
  const {get,calls,clock}=card(data);
  get('power-up').onpointerdown(press);
  assert.equal(calls[0][2].value,9.9);
  clock.advance(449);assert.equal(calls.length,1);
  clock.advance(1);assert.equal(calls[1][2].value,10);
  clock.advance(150);assert.equal(calls[2][2].value,11);
  get('power-up').onpointerup();clock.advance(3000);
  assert.equal(calls.length,3);
  get('power-down').onpointerdown(press);clock.advance(600);
  assert.deepEqual(calls.slice(3).map(x=>x[2].value),[10,9.9,9.8]);
  get('power-down').onpointerup();
});

for (const event of ['pointerup','pointercancel','pointerleave','lostpointercapture','outside','disconnect','disabled','wallbox','blur','reconfigure'])
  test(`hold ends and clears timers on ${event}`,()=>{
    const data=states(true), {card:c,get,calls,clock}=card(data);
    c.connectedCallback();get('power-up').onpointerdown(press);
    if(event==='outside')get('power-up').onpointermove({clientX:50,clientY:20});
    else if(event==='disconnect')c.disconnectedCallback();
    else if(event==='disabled'){data['select.renamed_owner'].attributes.transition_pending=true;c.hass={...c._hass};}
    else if(event==='wallbox'){data['select.renamed_owner'].attributes.wallboxes.B={name:'B',connected:true};data['select.renamed_owner'].attributes.active_wallbox='B';c.hass={...c._hass};}
    else if(event==='blur')clock.listeners.get('blur')();
    else if(event==='reconfigure')c.setConfig({type:'custom:wallbox-manager-card'});
    else get('power-up')[`on${event}`]();
    clock.advance(3000);assert.equal(calls.length,1);
    assert.equal(c.hold,null);c.disconnectedCallback();
    assert.equal(clock.pending,0);assert.equal(clock.listeners.size,0);
  });

test('reserve hold stops at 100 and 0; power hold stops at backend ceiling',()=>{
  const data=states(true);data['select.anything'].attributes.battery_configured=true;
  data['number.x'].state='99';data['number.renamed_power'].attributes.technical_max_kw=11.04;
  const {get,calls,clock}=card(data);
  get('reserve-up').onpointerdown(press);clock.advance(3000);
  assert.equal(calls.length,1);assert.equal(calls[0][2].value,100);
  get('reserve').value='1';get('reserve-down').onpointerdown(press);clock.advance(3000);
  assert.equal(calls.length,2);assert.equal(calls[1][2].value,0);
  get('power-up').onpointerdown(press);clock.advance(3000);
  assert.equal(calls.length,3);assert.equal(calls[2][2].value,11.04);
  assert.equal(clock.pending,0);
});

for (const [value,unit,expected] of [[1320,'s','0:22'],[83,'min','1:23'],[23+59/60,'h','23:59'],[25.2,'h','25:12'],[2945,'min','49:05'],[4980000,'ms','1:23'],[1.05,'d','25:12']])
  test(`duration converts ${value} ${unit} into ${expected}`,()=>{
    const {duration}=runtime();const now=Date.now();
    const s=state('session_duration','A',String(value),{unit_of_measurement:unit,session_active:true,connected:true});s.last_updated=new Date(now).toISOString();
    assert.equal(duration(s,true,now),expected);
  });

test('duration advances during pause, freezes at session end and resets on next session',()=>{
  const {duration}=runtime(),now=Date.now();
  const s=state('session_duration','A','1379',{unit_of_measurement:'s',session_active:true,connected:true,session_id:'one',duration_sampled_at:new Date(now).toISOString(),duration_valid_until:new Date(now+90000).toISOString()});
  assert.equal(duration(s,true,now),'0:22');
  assert.equal(duration(s,true,now+1000),'0:23');
  s.attributes.charging_state='suspended_vehicle';
  assert.equal(duration(s,true,now+61000),'0:24');
  s.attributes.session_active=false;s.state='1500';
  assert.equal(duration(s,false,now+3600000),'0:25');
  Object.assign(s.attributes,{session_active:true,session_id:'two',duration_sampled_at:new Date(now+3600000).toISOString(),duration_valid_until:new Date(now+3690000).toISOString()});s.state='0';
  assert.equal(duration(s,true,now+3600000),'0:00');
});

test('active duration never extrapolates unavailable, stale, future or disconnected data',()=>{
  const {duration}=runtime(),now=Date.now();
  const s=state('session_duration','A','4980',{unit_of_measurement:'s',session_active:true,connected:true});
  assert.equal(duration(s,true,now),'—');
  s.last_updated=new Date(now-90000).toISOString();assert.equal(duration(s,true,now),'—');
  s.last_updated=new Date(now+1000).toISOString();assert.equal(duration(s,true,now),'—');
  s.last_updated=new Date(now).toISOString();assert.equal(duration(s,false,now),'—');
  s.state='unavailable';assert.equal(duration(s,true,now),'—');
  s.state='invalid';assert.equal(duration(s,true,now),'—');
  s.state='20';s.attributes.unit_of_measurement='unknown';assert.equal(duration(s,true,now),'—');
});

test('renamed duration role and independent applied snapshots follow wallbox selection',()=>{
  const data=metered(),{card:c,get}=card(data);
  data['sensor.random_name']=data['sensor.renamed_session_duration'];delete data['sensor.renamed_session_duration'];
  Object.assign(data['select.anything'].attributes,{applied_current_a:16,applied_phase_count:1,actual_enabled:true,control_authority:'remote',profile_status:'phase_lockout',requested_phase_count:3});
  c.hass={...c._hass};assert.equal(get('duration').textContent,'1:23');assert.equal(get('actual').textContent,'1-phase · 16 A');
  data['select.renamed_owner'].attributes.wallboxes.B={name:'B',connected:true};
  data['select.renamed_owner'].attributes.active_wallbox='B';
  data['select.renamed_b']=state('charging_profile','B','NETZ',{profile_control_ready:true,applied_current_a:9,applied_phase_count:3,actual_enabled:true,control_authority:'remote'});
  c.hass={...c._hass,language:'de'};assert.equal(get('actual').textContent,'3-phasig · 9 A');assert.equal(get('duration').textContent,'—');
  for(const attrs of [{applied_current_a:null},{applied_current_a:9,actual_enabled:false},{actual_enabled:true,control_authority:'local'}]){
    Object.assign(data['select.renamed_b'].attributes,attrs);c.hass={...c._hass};assert.equal(get('actual').textContent,'—');
  }
});

for (const battery of [false,true]) for (const language of ['en','de']) {
  test(`PV settings, discovery and labels: battery=${battery} language=${language}`,async()=>{
    const data=states(true);
    data['select.anything'].state='PV_SURPLUS';
    Object.assign(data['select.anything'].attributes,{battery_configured:battery,battery_reserve_configured:true,options:['NETZ','PV_SURPLUS']});
    for(const [role,value] of Object.entries({soll_soc_speicher:'95',soc_hysterese:'5',regulation_interval:'5'})) data[`number.random_${role}`]=state(role,'A',value);
    data['select.random_pv']=state('pv_approximation','A','down');
    const {card:c,calls,get}=card(data);c.hass={...c._hass,language};
    assert.equal(get('power-row').hidden,true);
    assert.equal(get('reserve-row').hidden,true);
    assert.equal(get('approximation-row').hidden,true);
    assert.equal(get('soll_soc_speicher-row').hidden,!battery);
    assert.equal(get('grid_start_delay-row').hidden,true);
    assert.equal(get('grid_duration-row').hidden,true);
    assert.equal(get('permission').disabled,false);
    get('soll_soc_speicher').value='90';
    await get('soll_soc_speicher').onchange();
    assert.equal(calls.at(-1)[2].entity_id,'number.random_soll_soc_speicher');
    assert.equal(calls.at(-1)[2].value,90);
  });
}

test('automatic and manual duplicate loads do not execute or define the card twice',()=>{
  const {context,source,Card}=runtime();
  const cards=context.window.customCards;
  context.customElements.define=()=>{throw new Error('duplicate definition');};
  vm.runInContext(source,context,{filename:'automatic-module.js'});
  vm.runInContext(source,context,{filename:'temporary-manual-resource.js'});
  assert.equal(context.customElements.get('wallbox-manager-card'),Card);
  assert.equal(cards.length,1);
});

for(const options of [['NETZ'],['NETZ','PV_SURPLUS']]) {
  test(`profile selector visibility follows backend options: ${options.join(',')}`,()=>{
    const data=states(true);data['select.anything'].attributes.options=options;
    const {get}=card(data);
    assert.equal(get('profile-row').hidden,options.length===1);
  });
}

for(const battery of [false,true]) {
  test(`profile settings remain editable without authority: battery=${battery}`,async()=>{
    const data=states(false);
    data['select.anything'].state='PV_SURPLUS';
    Object.assign(data['select.anything'].attributes,{options:['NETZ','PV_SURPLUS'],battery_configured:battery});
    data['switch.another_name'].state='on';
    for(const [role,value] of Object.entries({soll_soc_speicher:'95',soc_hysterese:'5',regulation_interval:'5',pv_start_delay:'0',pv_stop_delay:'60'})) data[`number.${role}`]=state(role,'A',value);
    data['select.pv']=state('pv_approximation','A','down');
    const {card:c,get,calls}=card(data);
    assert.equal(get('permission').disabled,true);
    assert.match(get('permission').textContent,/enabled.*no control/);
    assert.equal(get('profile').disabled,false);
    assert.equal(get('power').disabled,false);
    assert.equal(get('reserve').disabled,false);
    assert.equal(get('approximation').disabled,false);
    assert.equal(get('approximation-row').hidden,true);
    assert.equal(get('soll_soc_speicher-row').hidden,!battery);
    await get('profile').onchange({target:{value:'NETZ'}});
    get('soll_soc_speicher').value='90';
    await get('soll_soc_speicher').onchange();
    assert.deepEqual(calls.map(call=>call.slice(0,2)),[['select','select_option'],['number','set_value']]);
    assert.equal(calls[1][2].entity_id,'number.soll_soc_speicher');
  });
}

test('profile availability is discovered separately for the displayed connector',()=>{
  const data=states(true);
  data['select.anything'].attributes.options=['NETZ'];
  data['select.renamed_owner'].attributes.wallboxes.B={name:'B',connected:true};
  data['select.b']=state('charging_profile','B','PV_SURPLUS',{options:['NETZ','PV_SURPLUS']});
  const {card:c,get}=card(data);
  assert.equal(get('profile-row').hidden,true);
  data['select.renamed_owner'].attributes.active_wallbox='B';
  c.hass={...c._hass};
  assert.equal(get('profile-row').hidden,false);
  assert.equal(get('profile').value,'PV_SURPLUS');
});

test('last known CP states remain visible and dimmed until fresh observations', () => {
  const data = states(true);
  data['sensor.connector'] = state('connector_state','A','occupied',{state_represents:'last_known_observation',state_fresh:false});
  data['sensor.charging'] = state('charging_state','A','charging',{state_represents:'last_known_observation',state_fresh:false});
  const {card: c,get} = card(data);
  assert.equal(get('connection').textContent,'Occupied');
  assert.equal(get('charging').textContent,'Charging');
  assert.equal(get('connection').style.opacity,'0.5');
  assert.match(get('connection').title,/Last known/);
  assert.match(get('permission').textContent,/Enable/);
  data['sensor.connector'].state = 'available';
  data['sensor.charging'].state = 'idle';
  data['sensor.connector'].attributes.state_fresh = true;
  data['sensor.charging'].attributes.state_fresh = true;
  c.hass = {states:data,language:'en'};
  assert.equal(get('connection').textContent,'Available');
  assert.equal(get('charging').textContent,'Idle');
  assert.equal(get('connection').style.opacity,'1');
});

test('parameters heading precedes confirmed point and permanent messages section',()=>{
  const {card:c,get}=card(states(true));
  const html=c.shadowRoot.innerHTML;
  assert.ok(html.indexOf('id="parameters-label"') < html.indexOf('id="actual"'));
  assert.ok(html.indexOf('id="actual"') < html.indexOf('id="messages"'));
  assert.match(html, /id="no-messages">-<\/div>/);
  assert.equal(get('parameters-label').textContent,'Wallbox parameters');
  assert.equal(get('messages-label').textContent,'Messages');
  assert.equal(get('status').hidden,true);
  assert.match(html, /#messages:has\(\.notice:not\(\[hidden\]\)\) #no-messages \{display:none\}/);
  assert.match(html, /\.notice \{[^}]*color:var\(--error-color\)/);
  c.hass={...c._hass, language:'de'};
  assert.equal(get('parameters-label').textContent,'Wallboxparameter');
  assert.equal(get('messages-label').textContent,'Meldungen');
});

test('existing backend and service errors stay inside messages without duplicates',async()=>{
  const data=states(true);
  data['select.anything'].attributes.control_status='voltage_unavailable';
  const {card:c,get}=card(data);
  const html=c.shadowRoot.innerHTML;
  const section=html.slice(html.indexOf('id="messages"'));
  for(const id of ['status','error']) {
    assert.equal(html.split(`id="${id}"`).length-1,1);
    assert.ok(section.includes(`id="${id}"`));
  }
  assert.match(get('status').textContent,/No fresh voltage/);
  assert.equal(get('status').hidden,false);
  c.hass={...c._hass,callService:async()=>{throw new Error('station error');}};
  await c.call('switch','turn_off',{});
  assert.equal(get('error').textContent,'station error');
  assert.equal(get('error').hidden,false);
  delete data['select.anything'].attributes.control_status;
  c.hass={...c._hass, states:data};
  assert.equal(get('status').hidden,true);
  assert.equal(get('error').hidden,false); // Placeholder stays hidden for service errors too.
});

for (const role of ['grid_start_delay','grid_duration']) for (const value of ['', '00:00','01:30','24:00','120:15']) {
  test(`${role} split round trip ${JSON.stringify(value)} without authority`, async () => {
    const data=states(false);
    data['text.setting']=state(role,'A',value);
    const {card:c,get,calls}=card(data);
    const [hours,minutes]=value ? value.split(':') : ['',''];
    assert.equal(get(`${role}-hours`).value,hours);
    assert.equal(get(`${role}-minutes`).value,minutes);
    assert.equal(get(`${role}-hours`).disabled,false);
    assert.equal(get(`${role}-minutes`).disabled,false);
    await get(`${role}-hours`).onchange();
    assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),['text','set_value',{entity_id:'text.setting',value}]);
    assert.equal(get('permission').disabled,true);
    c.hass={...c._hass};
    assert.equal(get(`${role}-hours`).value,hours);
    get(`${role}-clear`).onclick();
    assert.equal(calls.at(-1)[2].value,'');
    assert.equal(get(`${role}-hours`).value,'');
    assert.equal(get(`${role}-minutes`).value,'');
  });
}
for (const [hours,minutes,valid] of [['1','30',true],['120','15',true],['','5',true],['1','',true],['0','59',true],['0','0',true],['1.5','30',false],['1','30.5',false],['1.0','30',false],['-1','0',false],['0','-1',false],['1','60',false],['1e2','0',false]]) {
  test(`duration integer bounds ${hours}:${minutes}`,async()=>{
    const data=states(true);data['text.time']=state('grid_duration','A','01:30');
    const {get,calls}=card(data);
    get('grid_duration-hours').value=hours;get('grid_duration-minutes').value=minutes;
    await get('grid_duration-minutes').onchange();
    assert.equal(calls.length,valid?1:0);
    if(valid) assert.equal(calls[0][2].value,`${String(Number(hours||0)).padStart(2,'0')}:${String(Number(minutes||0)).padStart(2,'0')}`);
    else assert.equal(get('error').hidden,false);
  });
}
for(const value of ['unknown','unavailable',null]) {
  test(`unusable duration remains disabled: ${value}`,()=>{
    const data=states(false);if(value!==null)data['text.time']=state('grid_duration','A',value);
    const {get}=card(data);assert.equal(get('grid_duration-hours').disabled,true);
    assert.equal(get('grid_duration-minutes').disabled,true);
  });
}
test('split editors use native integer spinners and a visible colon',()=>{
  const {card:c}=card(states());const html=c.shadowRoot.innerHTML;
  for(const role of ['grid_start_delay','grid_duration']) {
    assert.match(html,new RegExp(`<input type="number" id="${role}-hours" min="0" step="1"`));
    assert.match(html,new RegExp(`<input type="number" id="${role}-minutes" min="0" max="59" step="1"`));
    assert.ok(!html.includes(`id="${role}-up"`));
  }
  assert.match(html,/<span class="colon">:<\/span>/);
  assert.match(html,/duration-editor input \{text-align:left/);
});
for(const stored of ['40.0','40','40.5']) {
  test(`PV percentage compact integer rendering from ${stored}`,async()=>{
    const data=states(false);data['select.anything'].state='PV_SURPLUS';
    data['select.anything'].attributes.battery_configured=true;
    data['number.target']=state('soll_soc_speicher','A',stored);
    const {card:c,get,calls}=card(data);
    const initial=Math.round(Number(stored));
    assert.equal(get('soll_soc_speicher').value,String(initial));
    assert.equal(get('soll_soc_speicher-row').hidden,false);
    assert.equal(get('grid_duration-row').hidden,true);
    assert.equal(get('grid_start_delay-row').hidden,true);
    assert.equal(get('reserve-row').hidden,true);
    await c.stepNumber('soll_soc_speicher',-1);
    assert.equal(calls.at(-1)[2].value,initial-1);
    await c.stepNumber('soll_soc_speicher',1);
    assert.equal(calls.at(-1)[2].value,initial);
    get('soll_soc_speicher').value='40.5';
    const count=calls.length;await get('soll_soc_speicher').onchange();
    assert.equal(calls.length,count);
    assert.match(c.shadowRoot.innerHTML,/<input id="soll_soc_speicher" type="text" inputmode="numeric"/);
    assert.ok(calls.every(call=>Number.isInteger(call[2].value)));
  });
}
test('technical regulation fields are absent from card markup',()=>{
  const {source}=runtime();
  for (const field of ['soc_hysterese','regulation_interval','pv_start_delay','pv_stop_delay','power_smoothing_window']) {
    assert.ok(!source.includes(`id="${field}"`));
  }
});

for(const value of [0,99]) {
  test(`target SoC buttons respect ${value} boundary`,async()=>{
    const data=states(true);data['select.anything'].state='PV_SURPLUS';
    data['select.anything'].attributes.battery_configured=true;
    data['number.target']=state('soll_soc_speicher','A',String(value));
    const {card:c,get,calls}=card(data);
    assert.equal(get(`soll_soc_speicher-${value===0?'down':'up'}`).disabled,true);
    await c.stepNumber('soll_soc_speicher',value===0?-1:1);
    assert.equal(calls.length,0);
    get('soll_soc_speicher').value=`${value}.0`;
    await get('soll_soc_speicher').onchange();
    assert.equal(get('soll_soc_speicher').value,String(value));
    assert.equal(calls.length,0);
  });
}
test('native bad-input state cannot accidentally clear a duration',async()=>{
  const data=states(true);data['text.time']=state('grid_duration','A','01:30');
  const {get,calls}=card(data);
  get('grid_duration-hours').value='';get('grid_duration-minutes').value='';
  get('grid_duration-hours').validity={badInput:true};
  await get('grid_duration-hours').onchange();
  assert.equal(calls.length,0);
  assert.equal(get('error').hidden,false);
});
test('duration edit survives stale HA updates and preserves focused counterpart',async()=>{
  const data=states(true);data['text.time']=state('grid_duration','A','');
  const {card:c,get}=card(data);
  let finish;c._hass.callService=()=>new Promise(resolve=>finish=resolve);
  get('grid_duration-hours').value='24';
  const pending=get('grid_duration-hours').onchange();
  c.hass={...c._hass};
  assert.equal(get('grid_duration-hours').value,'24');
  assert.equal(get('grid_duration-minutes').value,'00');
  c.shadowRoot.activeElement=get('grid_duration-minutes');
  get('grid_duration-minutes').value='15';
  c.hass={...c._hass};
  assert.equal(get('grid_duration-minutes').value,'15');
  finish();await pending;
  assert.equal(get('grid_duration-minutes').value,'15');
});
