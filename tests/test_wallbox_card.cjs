// Fresh focused tests for the passive card, not the beta.18 frontend suite.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js', 'utf8');

function load(clock={}) {
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
    Date:clock.Date || Date, setTimeout:clock.setTimeout || setTimeout,clearTimeout:clock.clearTimeout || clearTimeout,setInterval(){assert.fail('Passive card must not start an idle interval');}};
  vm.runInNewContext(source, context);
  return registry.get('wallbox-manager-card');
}
function states(entry='E') {
  const result = {};
  for (const [role,value] of Object.entries({connected:'on',control_authority:'local',charging_profile:'NETZ',soll_power:'7.2',min_soc:'20',soll_soc_speicher:'95',pv_approximation:'down',
    charge_action:'unknown',charging_enabled:'on',connector_state:'available',charging_state:'idle',power:'1500',session_energy:'1.2',session_duration:'60',
    grid_start_delay:'',grid_duration:'02:30',optimum_lower_soc:'20',optimum_upper_soc:'80',optimum_max_discharge_w:'3000',estimated_daily_house_consumption_kwh:'12'})) {
    result[`${entry}.${role}`] = {state:value,attributes:{wallbox_manager_entry:entry,wallbox_manager_role:role,wallbox_manager_scope:'connector',station_id:`station-${entry}`,
      options:role==='charging_profile'?['NETZ','PV_SURPLUS','PV_OPTIMUM','PV_MAXIMUM']:undefined,min:0,max:100}};
  }
  Object.assign(result[`${entry}.charge_action`].attributes,{charge_action_ready:false,charge_action:"start",charge_release:false});
  Object.assign(result[`${entry}.soll_power`].attributes,{technical_min_kw:1.4,technical_max_kw:18.6,grid_draft_revision:0,runtime_incarnation:entry});
  for (const role of ['grid_start_delay','grid_duration']) Object.assign(result[`${entry}.${role}`].attributes,{grid_timing_revision:0,runtime_incarnation:entry});
  return result;
}
// Canned backend neighbour payloads; the card never derives electrical steps.
const neighbours = {
  '1:1.4': [null,[1.6,1]], '1:1.6': [[1.4,1],[1.8,1]],
  '1:1.8': [[1.6,1],[2.1,1]], '1:2.1': [[1.8,1],[2.3,1]],
  '1:3.9': [[3.7,1],[4.1,1]], '1:4.1': [[3.9,1],[4.4,1]],
  '1:4.4': [[4.1,1],[4.6,1]], '1:4.6': [[4.4,1],[4.8,3]],
  '3:4.1': [[3.9,1],[4.8,3]], '3:4.8': [[4.1,3],[5.5,3]],
  '3:5.5': [[4.8,3],[6.2,3]], '3:7.2': [[6.2,3],[7.6,3]],
};
function backendPoint(state, point=null) {
  if (point) { state.state=String(point.power_kw); state.attributes.selected_phase_count=point.power_phases; state.attributes.draft_phase_hint=point.power_phases; }
  let phase=state.attributes.selected_phase_count || 3;
  if (!neighbours[`${phase}:${state.state}`]) phase=1;
  const pair=neighbours[`${phase}:${state.state}`];
  if (pair) {
    state.attributes.selected_phase_count=phase;
    [state.attributes.power_step_down,state.attributes.power_step_up]=pair.map(p=>p?{power_kw:p[0],power_phases:p[1]}:null);
  }
}
function card(data=states(), config={}, language='en', clock={}) {
  const Card = load(clock), card = new Card(), calls=[];
  card.setConfig({type:'custom:wallbox-manager-card',...config});
  for (const state of Object.values(data)) if (state.attributes.wallbox_manager_role==='soll_power') backendPoint(state);
  card.hass={states:data,language,callService:async(...args)=>{
    calls.push(args);
    if (args[0]==='wallbox_manager' && args[1]==='edit_timing') {
      const r=args[2], state=data[r.entity_id], entry=state.attributes.wallbox_manager_entry;
      if (r.revision!==state.attributes.grid_timing_revision) throw new Error('Stale timing revision');
      if (r.action==='renew') return;
      if (r.action==='update') state.state=r.value;
      for (const role of ['grid_start_delay','grid_duration']) {
        const a=data[`${entry}.${role}`].attributes;
        a.grid_timing_revision++;
        a.grid_timing_edit=r.action==='end' ? null : {owner:r.owner,active:true};
      }
      card.hass=card._hass;
    }
    if (args[0]==='wallbox_manager' && args[1]==='step_power') {
      const request=args[2], state=data[request.entity_id];
      if (request.revision!==state.attributes.grid_draft_revision) throw new Error('Stale revision');
      const point=state.attributes[request.direction>0?'power_step_up':'power_step_down'];
      backendPoint(state,point); state.attributes.grid_draft_revision++;
      card.hass=card._hass;
    }
  }};
  return {card,calls,get:id=>card.shadowRoot.getElementById(id)};
}

test('familiar layout, read-only permission and no multi-wallbox UI',()=>{
  const {card:c,get,calls}=card();
  assert.match(c.shadowRoot.html,/ha-card/);
  assert.match(c.shadowRoot.html,/class="numeric"/);
  assert.doesNotMatch(c.shadowRoot.html,/id="wallbox"/);
  assert.equal(get('takeover').disabled,true);
  assert.equal(get('charge-action').disabled,true);
  assert.equal(typeof get('takeover').onclick,'function');
  assert.equal(typeof get('charge-action').onclick,'function');
  assert.match(get('charge-action').textContent,/Start charging/);
  assert.equal(get('error').textContent,'—');
  assert.equal(get('connection').textContent,'Available');
  assert.equal(get('live-power').textContent,'1.50 kW');
  assert.equal(get('energy').textContent,'1.2 kWh');
  assert.equal(calls.length,0);
  c.connectedCallback(); c.disconnectedCallback();
});

test('multiple instances require explicit entry; two cards never cross entries',async()=>{
  const data={...states('A'),...states('B')};
  const missing=card(data);
  assert.equal(Object.keys(missing.card.discovery.roles).length,0);
  assert.match(missing.get('error').textContent,/entry_id/);
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

test('a missing explicit binding never falls back to another entry',async()=>{
  for (const config of [{entity:'removed.soll_power'}, {entry_id:'removed'}]) {
    const {card:c,get,calls}=card(states('OTHER'),config);
    assert.equal(Object.keys(c.discovery.roles).length,0);
    get('power').value='8.4'; await c.editNumber('power');
    assert.equal(calls.length,0);
  }
});

test('number and duration edits keep local service interactions',async()=>{
  const {card:c,get,calls}=card();
  get('power').value='8.4'; await c.editNumber('power');
  get('grid_duration-hours').value='03'; get('grid_duration-minutes').value='15';
  await c.editDuration('grid_duration');
  assert.equal(calls[0][0],'number'); assert.equal(calls[0][2].value,8.4);
  assert.equal(calls[1][1],'edit_timing'); assert.equal(calls[1][2].action,'begin');
  assert.equal(calls[2][2].value,'03:15'); assert.equal(calls[2][2].entity_id,'E.grid_duration');
  await c.endTiming();
});

test('German controls stay disabled and physical OFF stays visible',()=>{
  const data=states(); data['E.charging_enabled'].state='off';
  const {get}=card(data,{},'de');
  assert.equal(get('takeover').textContent,'Steuerung übernehmen');
  assert.match(get('charge-action').textContent,/Ladung starten/);
  assert.equal(get('charge-action').disabled,true);
  assert.equal(get('connection').textContent,'Frei');
  assert.equal(get('error').textContent,'—');
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
  assert.match(get('charge-action').textContent,/Start charging/);
});

test('explicit authority and permission actions stay scoped and observed',async()=>{
  const data=states();
  data['E.take_control']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:true}};
  data['E.charge_action'].attributes.charge_action_ready=true;
  const {card:c,get,calls}=card(data);
  assert.equal(get('takeover').disabled,false);
  assert.equal(get('charge-action').disabled,false);
  await get('takeover').onclick();
  await get('charge-action').onclick();
  assert.equal(calls[0][0],'button'); assert.equal(calls[0][1],'press');
  assert.equal(calls[0][2].entity_id,'E.take_control');
  assert.equal(calls[1][0],'button'); assert.equal(calls[1][1],'press');
  assert.equal(data['E.charging_enabled'].state,'on');
  data['E.charging_enabled'].state='off'; c.hass=c._hass;
  await get('charge-action').onclick();
  assert.equal(calls[2][1],'press');
  await c.call('switch','turn_on',{entity_id:'OTHER.charging_enabled'});
  await c.call('button','press',{entity_id:'E.charging_enabled'});
  assert.equal(calls.length,3);
});

test('pending, unsupported and service failures remain visible without optimistic state',async()=>{
  const data=states();
  data['E.take_control']={state:'unavailable',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:false,execution_blocked_reason:'unsupported'}};
  Object.assign(data['E.charge_action'].attributes,{charge_action_ready:true,command_status:'accepted_unconfirmed',command_reason:'readback_timeout'});
  const {card:c,get}=card(data);
  assert.equal(get('takeover').disabled,true);
  assert.equal(get('error').textContent,'—'); // Ordinary pending state is not a warning.
  c._hass.callService=async()=>{throw new Error('Station rejected');};
  await get('charge-action').onclick();
  assert.match(get('error').textContent,/Station rejected/);
  assert.equal(data['E.charging_enabled'].state,'on');
  data['E.charge_action'].attributes.charge_action_ready=false;
  c.hass=c._hass;
  assert.equal(get('charge-action').disabled,true);
});

test('historical takeover results and ordinary Local or disconnected states are not messages',()=>{
  const data=states();
  data['E.take_control']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:true,command_status:'confirmed'}};
  const {card:c,get}=card(data);
  for (const status of ['confirmed','accepted_unconfirmed','superseded']) {
    data['E.take_control'].attributes.command_status=status;
    c.hass=c._hass;
    assert.equal(get('error').textContent,'—');
  }
});

test('takeover follows Local to Remote to external Local through HA updates',async()=>{
  for (const language of ['en','de']) {
    const data=states();
    data['E.take_control']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:true}};
    data['E.charge_action'].attributes.charge_action_ready=true;
    const {card:c,get,calls}=card(data,{},language);
    assert.equal(get('takeover').hidden,false);
    assert.equal(get('takeover').disabled,false);
    await get('takeover').onclick();
    assert.equal(get('takeover').hidden,false); // Sending a command is no observation.
    data['E.control_authority'].state='remote'; c.hass=c._hass;
    assert.equal(get('takeover').hidden,true);
    assert.notEqual(get('charge-action').hidden,true);
    assert.equal(get('charge-action').disabled,false);
    await get('charge-action').onclick();
    data['E.control_authority'].state='local'; c.hass=c._hass;
    assert.equal(get('takeover').hidden,false);
    assert.equal(get('takeover').disabled,false);
    c.busy=true; c.hass=c._hass;
    assert.equal(get('takeover').disabled,true);
    c.busy=false;
    data['E.take_control'].attributes.execution_ready=false; c.hass=c._hass;
    assert.equal(get('takeover').disabled,true);
    await get('takeover').onclick();
    assert.equal(calls.length,2);
    assert.equal(calls[0][2].entity_id,'E.take_control');
    assert.equal(calls[1][2].entity_id,'E.charge_action');
  }
});

test('missing, unknown, unavailable and disconnected authority keeps takeover visible and disabled',()=>{
  for (const connection of ['on','off','unknown','unavailable']) {
    for (const authority of [null,'unknown','unavailable','','local','remote']) {
      if (connection==='on' && ['local','remote'].includes(authority)) continue;
      const data=states();
      data['E.connected'].state=connection;
      if (authority===null) delete data['E.control_authority'];
      else data['E.control_authority'].state=authority;
      // Even optimistic readiness and historical success cannot establish authority.
      data['E.take_control']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:true,command_status:'confirmed'}};
      const {get}=card(data);
      assert.equal(get('takeover').hidden,false,`${connection}/${authority}`);
      assert.equal(get('takeover').disabled,true,`${connection}/${authority}`);
      assert.notEqual(get('charge-action').hidden,true);
    }
  }
});

test('takeover visibility depends on observations independently of command history',()=>{
  const data=states();
  data['E.take_control']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:true}};
  const {card:c,get}=card(data);
  for (const status of [undefined,'pending','accepted_unconfirmed','confirmed','rejected','unsupported','timeout','communication_failure','superseded']) {
    data['E.take_control'].attributes.command_status=status;
    for (const authority of ['remote','local']) {
      data['E.control_authority'].state=authority; c.hass=c._hass;
      assert.equal(get('takeover').hidden,authority==='remote',`${status}/${authority}`);
      assert.notEqual(get('charge-action').hidden,true);
    }
  }
});

test('authority visibility stays isolated across entries and card rebinding',()=>{
  const data={...states('A'),...states('B')};
  for (const entry of ['A','B']) data[`${entry}.take_control`]={state:'unknown',attributes:{wallbox_manager_entry:entry,wallbox_manager_role:'take_control',execution_ready:true}};
  data['A.control_authority'].state='remote';
  const a=card(data,{entry_id:'A'}), b=card(data,{entry_id:'B'}), ambiguous=card(data);
  assert.equal(a.get('takeover').hidden,true);
  assert.equal(b.get('takeover').hidden,false);
  assert.equal(b.get('takeover').disabled,false);
  assert.equal(ambiguous.get('takeover').hidden,false);
  assert.equal(ambiguous.get('takeover').disabled,true);
  data['B.control_authority'].state='remote';
  a.card.hass=a.card._hass; b.card.hass=b.card._hass;
  assert.equal(a.get('takeover').hidden,true);
  assert.equal(b.get('takeover').hidden,true);
  data['A.control_authority'].state='local';
  a.card.hass=a.card._hass; b.card.hass=b.card._hass;
  assert.equal(a.get('takeover').hidden,false);
  assert.equal(b.get('takeover').hidden,true);
  b.card.setConfig({entry_id:'A'}); b.card.hass=b.card._hass;
  assert.equal(b.get('takeover').hidden,false);
  assert.equal(b.get('takeover').disabled,false);
});

test('device wake-up button is excluded from card rendering and service actions',async()=>{
  const data=states();
  data['E.wake_up_vehicle']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'wake_up_vehicle',execution_ready:true}};
  const {card:c,calls}=card(data);
  assert.doesNotMatch(c.shadowRoot.html,/wake_up_vehicle|Wake up vehicle|Fahrzeug aufwecken/);
  await c.call('button','press',{entity_id:'E.wake_up_vehicle'});
  assert.equal(calls.length,0);
});

test('automatic wake-up switch has no card markup or authorized card action',async()=>{
  const data=states();
  data['E.auto_wakeup_enabled']={state:'on',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'auto_wakeup_enabled',execution_ready:true}};
  const {card:c,calls}=card(data);
  assert.doesNotMatch(c.shadowRoot.html,/auto_wakeup_enabled|Automatic vehicle wake-up|Automatisches Aufwecken/);
  await c.call('switch','turn_on',{entity_id:'E.auto_wakeup_enabled'});
  await c.call('switch','turn_off',{entity_id:'E.auto_wakeup_enabled'});
  assert.equal(calls.length,0);
});


test('reserve and approximation controls are absent while measured power remains separate',()=>{
  const data=states();
  data['E.power_approximation']={state:'nearest',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'power_approximation'}};
  Object.assign(data['E.soll_power'].attributes,{profile_status:'rejected',command_reason:'PhaseSwitchLockout',applied_phase_count:3,applied_current_a:6});
  const {card:c,get}=card(data);
  assert.doesNotMatch(c.shadowRoot.html,/id="reserve|id="approximation|Entladereserve|Leistungsannäherung/);
  assert.equal(c.discovery.roles.min_soc,'E.min_soc');
  assert.equal(c.discovery.roles.power_approximation,'E.power_approximation');
  assert.equal(get('actual').textContent,'3-phase 6A');
  assert.equal(get('live-power').textContent,'1.50 kW');
});

test('Grid parameters show actual and requested modes only, including pending pauses',()=>{
  const data=states();
  Object.assign(data['E.soll_power'].attributes,{applied_phase_count:3,applied_current_a:0,requested_phase_count:1,requested_current_a:6});
  const {card:c,get}=card(data,{},'de');
  assert.equal(get('actual').textContent,'3-phasig 0A → 1-phasig 6A');
  Object.assign(data['E.soll_power'].attributes,{applied_current_a:6,requested_current_a:7});
  c.hass=c._hass;
  assert.equal(get('actual').textContent,'3-phasig 6A → 1-phasig 7A');
  Object.assign(data['E.soll_power'].attributes,{applied_phase_count:1,applied_current_a:7});
  c.hass=c._hass;
  assert.equal(get('actual').textContent,'1-phasig 7A');
  assert.doesNotMatch(get('actual').textContent,/Voltage|Strom|U L1|I L1|Beobachtete Steuerung|Bestätigtes Angebot/);
});

test('Grid actual requires a complete confirmed point; unknown and offline stay unknown',()=>{
  const data=states();
  data['E.physical_phases']={state:'3',attributes:{...data['E.soll_power'].attributes,wallbox_manager_role:'physical_phases'}};
  Object.assign(data['E.soll_power'].attributes,{applied_phase_count:null,applied_current_a:7,requested_phase_count:1,requested_current_a:6});
  const {card:c,get}=card(data,{},'de');
  assert.equal(get('actual').textContent,'— → 1-phasig 6A');
  Object.assign(data['E.soll_power'].attributes,{applied_phase_count:1,applied_current_a:6});
  c.hass=c._hass;
  assert.equal(get('actual').textContent,'1-phasig 6A');
  data['E.connected'].state='off'; c.hass=c._hass;
  assert.equal(get('actual').textContent,'—');
});

test('unconfirmed interim actual stays unknown until zero-current readback',()=>{
  const data=states();
  Object.assign(data['E.soll_power'].attributes,{applied_phase_count:null,applied_current_a:null,acknowledged_current_a:0,requested_phase_count:1,requested_current_a:6});
  const {card:c,get}=card(data,{},'de');
  assert.equal(get('actual').textContent,'— → 1-phasig 6A');
  Object.assign(data['E.soll_power'].attributes,{applied_phase_count:3,applied_current_a:0});
  c.hass=c._hass;
  assert.equal(get('actual').textContent,'3-phasig 0A → 1-phasig 6A');
});


test('one charging action follows backend start, apply, stop in both languages',async()=>{
  for (const language of ['en','de']) {
    const data=states();
    const a=data['E.charge_action'].attributes;
    Object.assign(a,{charge_action_ready:true,charge_action:'start'});
    const {card:c,get,calls}=card(data,{},language);
    assert.doesNotMatch(c.shadowRoot.html,/id="permission"|id="grid-set"/);
    const labels=language==='de'?['Ladung starten','Ladeparameter übernehmen','Ladung beenden']:['Start charging','Apply charging parameters','Stop charging'];
    assert.equal(get('charge-action').textContent,labels[0]);
    await get('charge-action').onclick();
    Object.assign(a,{charge_release:true,charge_action:'apply'}); c.hass=c._hass;
    assert.equal(get('charge-action').textContent,labels[1]);
    await get('charge-action').onclick();
    assert.equal(get('charge-action').textContent,labels[1]); // Wait for backend approval.
    a.charge_action='stop'; c.hass=c._hass;
    assert.equal(get('charge-action').textContent,labels[2]);
    await get('charge-action').onclick();
    assert.equal(calls.length,3);
    for (const call of calls) assert.equal(JSON.stringify(call),JSON.stringify(['button','press',{entity_id:'E.charge_action'}]));
  }
});

test('backend readiness and multiple cards fence unavailable and foreign actions',async()=>{
  const data={...states('A'),...states('B')};
  data['B.charge_action'].attributes.charge_action_ready=true;
  const a=card(data,{entry_id:'A'}),b=card(data,{entry_id:'B'});
  assert.equal(a.get('charge-action').disabled,true);
  await a.get('charge-action').onclick();
  await a.card.call('button','press',{entity_id:'B.charge_action'});
  assert.equal(a.calls.length,0);
  await b.get('charge-action').onclick();
  assert.equal(b.calls[0][2].entity_id,'B.charge_action');
});

test('charging action suppresses duplicates and preserves failed drafts',async()=>{
  const data=states(); data['E.charge_action'].attributes.charge_action_ready=true;
  const {card:c,get}=card(data);
  let reject,calls=0;
  c._hass.callService=()=>{calls++;return new Promise((resolve,fail)=>{reject=fail;});};
  const pending=get('charge-action').onclick();
  assert.equal(get('charge-action').disabled,true);
  await get('charge-action').onclick();
  assert.equal(calls,1);
  reject(new Error('Persistence failed')); await pending;
  assert.match(get('error').textContent,/Persistence failed/);
  assert.equal(get('power').value,'7.2');
  assert.equal(get('charge-action').textContent,'Start charging');
});

test('locale technical steps use backend revisions and time clearing remains a draft',async()=>{
  const data=states(); data['E.soll_power'].state='1.4';
  data['E.charge_action'].attributes.charge_action_ready=true;
  const {card:c,get,calls}=card(data,{},'de');
  assert.equal(get('power').value,'1,4');
  await c.stepNumber('power',1); await c.stepNumber('power',1);
  assert.equal(get('power').value,'1,8');
  assert.deepEqual(calls.map(call=>call[2].revision),[0,1]);
  assert.ok(calls.every(call=>call[0]==='wallbox_manager' && call[1]==='step_power' && call[2].direction===1));
  assert.equal(get('charge-action').disabled,false);
  get('grid_duration-clear').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[2][1],'edit_timing');
  assert.equal(calls[3][2].action,'update'); assert.equal(calls[3][2].value,'');
  data['E.grid_duration'].state=''; c.hass=c._hass;
  assert.equal(get('charge-action').disabled,false);
});

test('power long press still stages repeated numeric edits without pressing SET',async()=>{
  const data=states(); data['E.soll_power'].state='1.4';
  const {card:c,get,calls}=card(data,{},'de');
  const button=get('power-up');
  button.setPointerCapture=()=>{}; button.hasPointerCapture=()=>true; button.releasePointerCapture=()=>{};
  c.startHold(button,'power',1,{button:0,isPrimary:true,pointerId:1,preventDefault(){}});
  await new Promise(resolve=>setTimeout(resolve,500));
  c.stopHold();
  assert.ok(calls.length>=2);
  assert.ok(calls.every(call=>call[0]==='wallbox_manager' && call[1]==='step_power' && call[2].entity_id==='E.soll_power'));
  assert.notEqual(get('charge-action').hidden,true); // The unified action is always visible.
  const count=calls.length; await new Promise(resolve=>setTimeout(resolve,175));
  assert.equal(calls.length,count);
});

test('newer backend draft revisions supersede another card optimistic edit',async()=>{
  const data=states(); data['E.soll_power'].attributes.grid_draft_revision=0;
  data['E.charge_action'].attributes.charge_action_ready=true;
  const a=card(data), b=card(data);
  a.get('power').value='3'; await a.card.editNumber('power');
  assert.equal(a.get('power').value,'3.0');
  // Another card wins; the first card never observes an intermediate 3 kW state.
  b.get('power').value='4'; await b.card.editNumber('power');
  data['E.soll_power'].state='4'; data['E.soll_power'].attributes.grid_draft_revision=2;
  a.card.hass=a.card._hass; b.card.hass=b.card._hass;
  assert.equal(a.get('power').value,'4.0'); assert.equal(b.get('power').value,'4.0');
  assert.equal(a.get('charge-action').disabled,false);
  data['E.charge_action'].attributes.active_message={text:'Disk full',raised_at:1}; a.card.hass=a.card._hass;
  assert.match(a.get('error').textContent,/Disk full/);
});


test('technical phase transitions use backend identity and preserve overlap direction',async()=>{
  const data=states(); data['E.soll_power'].state='4.6'; data['E.soll_power'].attributes.selected_phase_count=1;
  const {card:c,get}=card(data);
  await c.stepNumber('power',1);
  assert.equal(get('power').value,'4.8');
  assert.equal(data['E.soll_power'].attributes.selected_phase_count,3);
  await c.stepNumber('power',-1);
  assert.equal(get('power').value,'4.1');
  assert.equal(data['E.soll_power'].attributes.selected_phase_count,3);
  await c.stepNumber('power',-1);
  assert.equal(get('power').value,'3.9');
  assert.equal(data['E.soll_power'].attributes.selected_phase_count,1);
});

test('queued repeated presses and keyboard wait for backend acknowledgement',async()=>{
  const data=states(); data['E.soll_power'].state='1.4'; data['E.charge_action'].attributes.charge_action_ready=true;
  const {card:c,get,calls}=card(data);
  let release;
  const original=c._hass.callService;
  c._hass.callService=async(...args)=>{await new Promise(resolve=>{release=resolve;}); return original(...args);};
  const first=c.stepNumber('power',1),second=c.stepNumber('power',1);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,0);
  assert.equal(get('charge-action').disabled,true);
  release(); await first;
  await new Promise(resolve=>setImmediate(resolve));
  release(); await second;
  assert.equal(get('power').value,'1.8');
  c._hass.callService=original;
  get('power').onkeydown({key:'ArrowUp',preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('power').value,'2.1');
  assert.deepEqual(calls.map(call=>call[2].revision),[0,1,2]);
});

test('service return alone never acknowledges a step; another card revision cancels it',async()=>{
  const data=states(); data['E.soll_power'].state='1.4';
  const {card:c,get}=card(data);
  c._hass.callService=async()=>{};
  const pending=c.stepNumber('power',1);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('power').value,'1.4');
  assert.equal(c.draftWrites.size,1);
  data['E.soll_power'].state='4.1'; data['E.soll_power'].attributes.grid_draft_revision=2;
  c.hass=c._hass;
  await pending;
  assert.equal(get('power').value,'4.1');
  assert.match(get('error').textContent,/selection changed/);
});

for (const [language,value,expected] of [['de',1378,'1,38 kW'],['de',5523,'5,52 kW'],['de',0,'0,00 kW'],['en',0,'0.00 kW'],['en',5523,'5.52 kW']]) {
  test(`live measured power uses two locale decimals ${language}/${value}`,()=>{
    const data=states(); data['E.power'].state=String(value); data['E.soll_power'].state='11';
    const {get}=card(data,{},language);
    assert.equal(get('live-power').textContent,expected);
    assert.equal(get('power').value,language==='de'?'11,0':'11.0');
  });
}

test('unavailable technical capabilities show a placeholder and disable navigation',()=>{
  const data=states(); data['E.soll_power'].state='unavailable';
  Object.assign(data['E.soll_power'].attributes,{technical_min_kw:null,technical_max_kw:null,power_step_up:null,power_step_down:null});
  const {get}=card(data);
  assert.equal(get('power').value,'');
  assert.equal(get('power').disabled,true);
  assert.equal(get('power-up').disabled,true);
  assert.equal(get('power-down').disabled,true);
});

test('relative steps synchronize two cards and remain scoped after rebinding',async()=>{
  const data={...states('A'),...states('B')};
  data['A.soll_power'].state='1.4'; data['B.soll_power'].state='1.4';
  const a=card(data,{entry_id:'A'}), other=card(data,{entry_id:'A'});
  await a.card.stepNumber('power',1);
  other.card.hass=other.card._hass;
  assert.equal(other.get('power').value,'1.6');
  await other.card.stepNumber('power',1);
  a.card.hass=a.card._hass;
  assert.equal(a.get('power').value,'1.8');
  assert.deepEqual([...a.calls,...other.calls].map(call=>call[2].revision),[0,1]);
  other.card.setConfig({entry_id:'B'});
  await other.card.stepNumber('power',1);
  assert.equal(data['B.soll_power'].state,'1.6');
  assert.equal(data['A.soll_power'].state,'1.8');
  assert.equal(other.calls.at(-1)[2].entity_id,'B.soll_power');
});

test('rebinding cancels queued steps and isolates a late acknowledgement',async()=>{
  const data={...states('A'),...states('B')};
  data['A.soll_power'].state='1.4'; data['B.soll_power'].state='1.4';
  const {card:c,get,calls}=card(data,{entry_id:'A'});
  let release;
  const original=c._hass.callService;
  c._hass.callService=async(...args)=>{await new Promise(resolve=>{release=resolve;}); return original(...args);};
  const first=c.stepNumber('power',1),second=c.stepNumber('power',1);
  await new Promise(resolve=>setImmediate(resolve));
  c.setConfig({entry_id:'B'});
  release(); await first; await second;
  assert.equal(calls.length,1);
  assert.equal(calls[0][2].entity_id,'A.soll_power');
  assert.equal(get('power').value,'1.4');
  assert.equal(get('error').textContent,'—');
  c._hass.callService=original;
  await c.stepNumber('power',1);
  assert.equal(get('power').value,'1.6');
  assert.equal(calls.at(-1)[2].entity_id,'B.soll_power');
});

function uiClock(now=1000000) {
  const timers=new Map(); let serial=0;
  return {Date:class extends Date { static now(){return now;} },
    setTimeout(fn,delay){const id=++serial;timers.set(id,{fn,at:now+delay});return id;},
    clearTimeout(id){timers.delete(id);},
    advance(ms){now+=ms;const due=[...timers.entries()].filter(([,t])=>t.at<=now);for(const [id,t] of due){timers.delete(id);t.fn();}},
    get pending(){return timers.size;}};
}
for(const language of ['en','de']) for(const [value,expected] of [[0,'0.0'],[1.234,'1.2'],[12.36,'12.4']]) {
  test(`session energy has one locale decimal ${language}/${value}`,()=>{
    const data=states();data['E.session_energy'].state=String(value);
    const {get}=card(data,{},language);
    assert.equal(get('energy').textContent,`${language==='de'?expected.replace('.',','):expected} kWh`);
    assert.equal(data['E.session_energy'].state,String(value));
    assert.equal(get('live-power').textContent,language==='de'?'1,50 kW':'1.50 kW');
  });
}
test('canonical countdown uses absolute deadlines, floors minutes, and generates no HA calls',()=>{
  const data=states(),clock=uiClock();
  Object.assign(data['E.charge_action'].attributes,{charge_release:true,charge_action:'stop'});
  Object.assign(data['E.grid_duration'].attributes,{grid_schedule:{epoch_at:1000,start_at:1120,end_at:1300}});
  const {card:c,get,calls}=card(data,{},'en',clock);
  assert.equal(get('grid_start_delay-hours').value,'00');
  assert.equal(get('grid_start_delay-minutes').value,'02');
  assert.equal(get('grid_duration-minutes').value,'03');
  clock.advance(61000);
  assert.equal(get('grid_start_delay-minutes').value,'00');
  assert.equal(get('grid_duration-minutes').value,'03');
  clock.advance(59000);
  assert.equal(get('grid_start_delay-minutes').value,'');
  assert.equal(get('grid_duration-minutes').value,'03');
  clock.advance(121000);
  assert.equal(get('grid_duration-minutes').value,'00');
  clock.advance(59000);
  assert.equal(get('grid_duration-minutes').value,'');
  assert.equal(calls.length,0);
  c.disconnectedCallback();assert.equal(clock.pending,0);
});
test('both timing fields show drafts during one editing area and await revision acknowledgements',async()=>{
  const data=states(),clock=uiClock();
  data['E.grid_start_delay'].state='00:02';data['E.grid_duration'].state='00:03';
  Object.assign(data['E.charge_action'].attributes,{charge_release:true,charge_action:'stop'});
  Object.assign(data['E.grid_duration'].attributes,{grid_schedule:{epoch_at:900,start_at:1020,end_at:1200}});
  const {card:c,get,calls}=card(data,{},'en',clock);
  assert.equal(get('grid_start_delay-minutes').value,'00');
  c.shadowRoot.activeElement=get('grid_duration-minutes');
  await get('grid_duration-minutes').onfocus();
  assert.equal(get('grid_start_delay-minutes').value,'02');
  assert.equal(get('grid_duration-minutes').value,'03');
  get('grid_duration-minutes').value='10';await get('grid_duration-minutes').oninput();
  assert.equal(data['E.grid_duration'].state,'00:10');
  c.shadowRoot.activeElement=get('grid_start_delay-minutes');
  await get('grid_duration-minutes').onblur();
  assert.equal(calls.filter(c=>c[2].action==='end').length,0);
  c.shadowRoot.activeElement=null;await get('grid_start_delay-minutes').onblur();await c.timingQueue.tail;
  assert.deepEqual(calls.map(c=>c[2].revision),[0,1,2]);
  c.disconnectedCallback();
});
test('backend edit window is shared, duration expiry retains drafts, and a later run is independent',()=>{
  const data=states(),clock=uiClock();
  const a=data['E.grid_duration'].attributes;
  Object.assign(data['E.charge_action'].attributes,{charge_release:true,charge_action:'apply'});
  Object.assign(a,{grid_schedule:{epoch_at:1000,start_at:1000,end_at:1060},grid_timing_edit:{owner:'other-card',active:false,expires_at:1015}});
  data['E.grid_duration'].state='00:20';
  const first=card(data,{},'en',clock),second=card(data,{},'en',clock);
  for(const instance of [first,second]) assert.equal(instance.get('grid_duration-minutes').value,'20');
  a.grid_timing_edit=null;a.grid_schedule=null;
  Object.assign(data['E.charge_action'].attributes,{charge_release:false,charge_action:'start'});
  for(const instance of [first,second]) {instance.card.hass=instance.card._hass;assert.equal(instance.get('grid_duration-minutes').value,'20');assert.equal(instance.get('charge-action').textContent,'Start charging');}
  assert.equal(clock.pending,0);
});
test('timing service return alone is not acknowledgement and foreign revisions cancel queued writes',async()=>{
  const {card:c,get}=card();let sent;
  c._hass.callService=async(...args)=>{sent=args[2];};
  const begin=c.beginTiming();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(sent.action,'begin');assert.equal(c.draftWrites.size,1);
  for(const role of ['grid_duration','grid_start_delay']) c._hass.states[`E.${role}`].attributes.grid_timing_revision=2;
  c.hass=c._hass;await begin;
  assert.match(get('error').textContent,/Timing selection changed/);
  assert.equal(c.draftWrites.size,0);
});
test('runtime replacement fences a late timing response without warning in the replacement',async()=>{
  const {card:c,get}=card();let finish;
  c._hass.callService=()=>new Promise(resolve=>{finish=resolve;});
  const pending=c.beginTiming();await new Promise(resolve=>setImmediate(resolve));
  for(const role of ['grid_duration','grid_start_delay']) c._hass.states[`E.${role}`].attributes.runtime_incarnation='new';
  c.hass=c._hass;finish();await pending;
  assert.equal(get('error').textContent,'—');assert.equal(c.timingEditing,false);
});
test('only one newest active warning is shown, resolution falls back, and pending history is silent',async()=>{
  const data=states();data['E.charge_action'].attributes.active_message={text:'Old active warning',raised_at:1};
  const {card:c,get}=card(data);
  assert.equal(get('error').textContent,'Old active warning');
  c.warn('grid_duration','Invalid minutes');
  assert.equal(get('error').textContent,'Invalid minutes');
  c.clearWarning('grid_duration');assert.equal(get('error').textContent,'Old active warning');
  data['E.charge_action'].attributes.active_message=null;
  data['E.charge_action'].attributes.command_status='accepted_unconfirmed';c.hass=c._hass;
  assert.equal(get('error').textContent,'—');
  assert.doesNotMatch(c.shadowRoot.html,/id="status"|id="no-messages"/);
});

test('moving focus from timing fields to Apply keeps the first explicit button click',async()=>{
  const data=states();Object.assign(data['E.charge_action'].attributes,{charge_release:true,charge_action:'apply',charge_action_ready:true});
  const {card:c,get,calls}=card(data);
  c.shadowRoot.activeElement=get('grid_duration-minutes');await c.beginTiming();
  get('grid_duration-minutes').value='12';await c.editDuration('grid_duration');
  c.shadowRoot.activeElement=get('charge-action');await get('grid_duration-minutes').onblur();
  assert.equal(get('charge-action').disabled,false);
  await get('charge-action').onclick();
  assert.equal(calls.at(-1)[0],'button');assert.equal(calls.at(-1)[1],'press');
  assert.equal(calls.filter(call=>call[2].action==='end').length,1);
});
test('off schedule shows no fictitious countdown but keeps meaningful next-start drafts',()=>{
  const data=states();Object.assign(data['E.grid_duration'].attributes,{grid_approved_duration:9000,grid_approved_start_delay:null});
  const {card:c,get}=card(data);
  assert.equal(get('grid_duration-minutes').value,'');
  data['E.grid_duration'].state='00:12';c.hass=c._hass;
  assert.equal(get('grid_duration-minutes').value,'12');
});
test('confirmed foreign field updates resolve a local validation warning',()=>{
  const {card:c,get}=card();c.warn('grid_duration','Invalid minutes');
  c._hass.states['E.grid_duration'].state='00:12';c.hass=c._hass;
  assert.equal(get('error').textContent,'—');
});

test('focused active editor renews for long pauses without revisions or draft writes; End stops heartbeat',async()=>{
  const data=states(),clock=uiClock();
  const {card:c,get,calls}=card(data,{},'en',clock);
  c.shadowRoot.activeElement=get('grid_duration-minutes');
  await c.beginTiming();
  const revision=data['E.grid_duration'].attributes.grid_timing_revision;
  for(let i=0;i<12;i++) {
    // Unrelated hass updates must not postpone the scheduled renewal forever.
    clock.advance(10000);c.hass=c._hass;
    clock.advance(10000);await c.timingQueue.tail;
    assert.equal(c.draftWrites.size,0);
    assert.equal(data['E.grid_duration'].attributes.grid_timing_revision,revision);
  }
  assert.equal(calls.filter(call=>call[2].action==='renew').length,12);
  c.shadowRoot.activeElement=null;await c.endTiming();
  const count=calls.length;clock.advance(120000);await c.timingQueue.tail;
  assert.equal(calls.length,count);
});
test('backend abandonment cancels local focused ownership and a late heartbeat cannot rebegin',async()=>{
  const data=states(),clock=uiClock();
  const {card:c,get,calls}=card(data,{},'en',clock);
  c.shadowRoot.activeElement=get('grid_duration-minutes');await c.beginTiming();
  for(const role of ['grid_duration','grid_start_delay']) {
    const a=data[`E.${role}`].attributes;a.grid_timing_revision++;
    a.grid_timing_edit={owner:null,active:false,expires_at:1075};
  }
  c.hass=c._hass;
  assert.equal(c.timingEditing,false);
  const count=calls.length;clock.advance(120000);
  assert.equal(calls.length,count);
  assert.equal(clock.pending,0);
});
test('failed heartbeat relinquishes local focus ownership without retrying or sending End',async()=>{
  const data=states(),clock=uiClock();
  const {card:c,get,calls}=card(data,{},'en',clock);
  c.shadowRoot.activeElement=get('grid_duration-minutes');await c.beginTiming();
  c._hass.callService=async(...args)=>{calls.push(args);throw new Error('Network unavailable');};
  clock.advance(20000);await c.timingQueue.tail;
  assert.equal(c.timingEditing,false);assert.match(get('error').textContent,/Network unavailable/);
  const count=calls.length;clock.advance(120000);
  assert.equal(calls.length,count);assert.equal(clock.pending,0);
});
test('runtime replacement and disconnect stop editor lease renewal',async()=>{
  const data=states(),clock=uiClock();
  const {card:c,get,calls}=card(data,{},'en',clock);
  c.shadowRoot.activeElement=get('grid_duration-minutes');await c.beginTiming();
  for(const role of ['grid_duration','grid_start_delay']) data[`E.${role}`].attributes.runtime_incarnation='replacement';
  c.hass=c._hass;
  clock.advance(120000);assert.equal(calls.filter(call=>call[2].action==='renew').length,0);
  assert.equal(c.timingEditing,false);c.disconnectedCallback();assert.equal(clock.pending,0);
});

test('missing intermediate abandonment update still restores a focused countdown after rollback',async()=>{
  const data=states(),clock=uiClock();
  Object.assign(data['E.charge_action'].attributes,{charge_release:true});
  Object.assign(data['E.grid_duration'].attributes,{grid_schedule:{epoch_at:900,start_at:900,end_at:1300}});
  const {card:c,get,calls}=card(data,{},'en',clock);
  c.shadowRoot.activeElement=get('grid_duration-minutes');await c.beginTiming();
  for(const role of ['grid_duration','grid_start_delay']) {
    const a=data[`E.${role}`].attributes;a.grid_timing_revision+=2;a.grid_timing_edit=null;
  }
  c.hass=c._hass;
  assert.equal(c.timingEditing,false);
  assert.equal(get('grid_duration-minutes').value,'05');
  clock.advance(20000);
  assert.equal(calls.filter(call=>call[2].action==='renew').length,0);
  c.disconnectedCallback();
});
