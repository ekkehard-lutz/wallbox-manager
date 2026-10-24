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
  for (const [role,value] of Object.entries({connected:'on',control_authority:'local',charging_profile:'NETZ',soll_power:'7.2',min_soc:'20',soll_soc_speicher:'95',pv_approximation:'down',
    charge_action:'unknown',charging_enabled:'on',connector_state:'available',charging_state:'idle',power:'1500',session_energy:'1.2',session_duration:'60',
    grid_start_delay:'',grid_duration:'02:30',optimum_lower_soc:'20',optimum_upper_soc:'80',optimum_max_discharge_w:'3000',estimated_daily_house_consumption_kwh:'12'})) {
    result[`${entry}.${role}`] = {state:value,attributes:{wallbox_manager_entry:entry,wallbox_manager_role:role,wallbox_manager_scope:'connector',station_id:`station-${entry}`,
      options:role==='charging_profile'?['NETZ','PV_SURPLUS','PV_OPTIMUM','PV_MAXIMUM']:undefined,min:0,max:100}};
  }
  Object.assign(result[`${entry}.charge_action`].attributes,{charge_action_ready:false,charge_action:"start",charge_release:false});
  Object.assign(result[`${entry}.soll_power`].attributes,{technical_min_kw:1.38,technical_max_kw:18.63});
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
  assert.equal(get('charge-action').disabled,true);
  assert.equal(typeof get('takeover').onclick,'function');
  assert.equal(typeof get('charge-action').onclick,'function');
  assert.match(get('charge-action').textContent,/Start charging/);
  assert.match(get('status').textContent,/explicit actions/);
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
  assert.equal(calls[1][0],'text'); assert.equal(calls[1][2].value,'03:15');
  assert.equal(calls[1][2].entity_id,'E.grid_duration');
});

test('German controls stay disabled and physical OFF stays visible',()=>{
  const data=states(); data['E.charging_enabled'].state='off';
  const {get}=card(data,{},'de');
  assert.equal(get('takeover').textContent,'Steuerung übernehmen');
  assert.match(get('charge-action').textContent,/Ladung starten/);
  assert.equal(get('charge-action').disabled,true);
  assert.equal(get('connection').textContent,'Frei');
  assert.match(get('status').textContent,/Hardwarefreigabe/);
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
  assert.match(get('status').textContent,/unsupported/);
  assert.match(get('status').textContent,/accepted_unconfirmed.*readback_timeout/);
  c._hass.callService=async()=>{throw new Error('Station rejected');};
  await get('charge-action').onclick();
  assert.match(get('error').textContent,/Station rejected/);
  assert.equal(data['E.charging_enabled'].state,'on');
  data['E.charge_action'].attributes.charge_action_ready=false;
  c.hass=c._hass;
  assert.equal(get('charge-action').disabled,true);
});

test('last takeover result stays separate from observed authority and reconnect availability',()=>{
  for (const language of ['en','de']) {
    const data=states();
    data['E.take_control']={state:'unknown',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'take_control',execution_ready:true,command_status:'confirmed'}};
    data['E.control_authority']={state:'remote',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'control_authority'}};
    const {card:c,get}=card(data,{},language);
    const commandLabel=language==='en' ? /Last takeover command: confirmed/ : /Letzter Übernahmebefehl: confirmed/;
    assert.match(get('status').textContent,commandLabel);
    assert.doesNotMatch(get('actual').textContent,/Observed authority|Beobachtete Steuerung|U L1|I L1/);
    data['E.control_authority'].state='local'; c.hass=c._hass;
    assert.doesNotMatch(get('actual').textContent,/Observed authority|Beobachtete Steuerung/);
    assert.match(get('status').textContent,commandLabel);
    data['E.connected'].state='off';
    data['E.control_authority'].state='remote'; // Retained data is not current while offline.
    Object.assign(data['E.take_control'].attributes,{execution_ready:false,execution_blocked_reason:'disconnected'});
    c.hass=c._hass;
    assert.equal(get('actual').textContent,'—');
    assert.match(get('status').textContent,commandLabel);
    assert.match(get('status').textContent,/disconnected/);
    assert.equal(get('takeover').disabled,true);
    data['E.connected'].state='on'; data['E.control_authority'].state='local';
    Object.assign(data['E.take_control'].attributes,{execution_ready:true,execution_blocked_reason:null});
    c.hass=c._hass;
    assert.match(get('status').textContent,commandLabel);
    assert.doesNotMatch(get('actual').textContent,/Observed authority|Beobachtete Steuerung/);
    data['E.take_control'].attributes.command_status='superseded'; c.hass=c._hass;
    assert.match(get('status').textContent,/superseded/);
    assert.doesNotMatch(get('status').textContent,commandLabel);
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


test('Grid approximation and confirmed offer stay distinct from measured power',async()=>{
  const data=states();
  data['E.power_approximation']={state:'nearest',attributes:{wallbox_manager_entry:'E',wallbox_manager_role:'power_approximation'}};
  Object.assign(data['E.soll_power'].attributes,{profile_status:'rejected',command_reason:'PhaseSwitchLockout',applied_phase_count:3,applied_current_a:6,applied_offered_power_w:4140});
  const {get,calls}=card(data);
  assert.equal(get('approximation').value,'nearest');
  assert.equal(get('approximation-row').hidden,false);
  assert.equal(get('actual').textContent,'3-phase 6A');
  assert.equal(get('live-power').textContent,'1.5 kW');
  assert.match(get('status').textContent,/NETZ: rejected \(PhaseSwitchLockout\)/);
  await get('approximation').onchange({target:{value:'down'}});
  assert.equal(calls[0][2].entity_id,'E.power_approximation');
  assert.equal(calls[0][2].option,'down');
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

test('locale numeric steps and time clearing stage drafts and wait for backend acknowledgement',async()=>{
  const data=states(); data['E.soll_power'].state='1.4';
  data['E.charge_action'].attributes.charge_action_ready=true;
  const {card:c,get,calls}=card(data,{},'de');
  assert.equal(get('power').value,'1,4');
  await c.stepNumber('power',1); await c.stepNumber('power',1);
  assert.equal(get('power').value,'1,6');
  assert.deepEqual(calls.map(call=>call[2].value),[1.5,1.6]);
  assert.equal(get('charge-action').disabled,true);
  await get('charge-action').onclick(); assert.equal(calls.length,2);
  data['E.soll_power'].state='1.6'; c.hass=c._hass;
  assert.equal(get('charge-action').disabled,false);
  get('grid_duration-clear').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[2][0],'text'); assert.equal(calls[2][2].value,'');
  assert.equal(get('charge-action').disabled,true);
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
  assert.ok(calls.every(call=>call[0]==='number' && call[2].entity_id==='E.soll_power'));
  assert.notEqual(get('charge-action').hidden,true); // The unified action is always visible.
  const count=calls.length; await new Promise(resolve=>setTimeout(resolve,175));
  assert.equal(calls.length,count);
});

test('newer backend draft revisions supersede another card optimistic edit',async()=>{
  const data=states(); data['E.soll_power'].attributes.grid_draft_revision=0;
  data['E.charge_action'].attributes.charge_action_ready=true;
  const a=card(data), b=card(data);
  a.get('power').value='3'; await a.card.editNumber('power');
  assert.equal(a.get('power').value,'3');
  // Another card wins; the first card never observes an intermediate 3 kW state.
  b.get('power').value='4'; await b.card.editNumber('power');
  data['E.soll_power'].state='4'; data['E.soll_power'].attributes.grid_draft_revision=2;
  a.card.hass=a.card._hass; b.card.hass=b.card._hass;
  assert.equal(a.get('power').value,'4'); assert.equal(b.get('power').value,'4');
  assert.equal(a.get('charge-action').disabled,false);
  data['E.charge_action'].attributes.grid_approval_error='Disk full'; a.card.hass=a.card._hass;
  assert.match(a.get('status').textContent,/Disk full/);
});
