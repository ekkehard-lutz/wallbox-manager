// Real Chrome geometry across both languages, profiles, directions and card widths.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {mkdtempSync,writeFileSync,readFileSync,rmSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');

test('status columns, battery caption and directional power remain responsive and accessible',()=>{
  const dir=mkdtempSync(join(tmpdir(),'wbm-card-layout-'));
  try {
    const source=readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js','utf8');
    const html=`<!doctype html><html><meta charset="utf-8"><style>body{margin:0} ha-card{display:block} wallbox-manager-card{margin:8px} :root{--error-color:rgb(160,0,0);--success-color:rgb(0,120,0);--secondary-text-color:rgb(90,90,90)}</style><body>
      <script>${source}</script><script>
      const results=[];
      for (const language of ['de','en']) for (const width of [640,400,320,280]) for(const profile of ['NETZ','PV_SURPLUS','PV_OPTIMUM','PV_MAXIMUM']) for(const direction of [0,1,null]) {
        const c=document.createElement('wallbox-manager-card'); c.style.width=width+'px'; document.body.append(c); c.setConfig({entry_id:'E'});
        const states={};
        const put=(role,value,attrs={})=>states['E.'+role]={state:value,attributes:{wallbox_manager_entry:'E',wallbox_manager_scope:'connector',wallbox_manager_role:role,...attrs}};
        put('connected','on');put('charging_profile',profile,{options:[profile],planned_grid_reserve:40,effective_target_soc:40,battery_soc_entity_id:'sensor.soc',battery_reserve_entity_id:'number.reserve',pv_surplus_target:{soc:40,maximum_reserve:38},grid_import_power_entity_id:'sensor.negative',grid_export_power_entity_id:'sensor.positive',storage_discharge_power_entity_id:'sensor.negative',storage_charge_power_entity_id:'sensor.positive'});
        put('soll_soc_speicher','40');
        put('soll_power','4.8',{applied_phase_count:3,applied_current_a:6,requested_phase_count:1,requested_current_a:17});
        put('connector_state','occupied');put('charging_state','charging');put('power','1330');put('session_energy','12.3');put('session_duration','3600');
        states['sensor.soc']={state:'69.125',attributes:{}};states['number.reserve']={state:'20',attributes:{}};
        states['sensor.negative']={state:direction===0?'1330':'0',attributes:{unit_of_measurement:'W'}};
        states['sensor.positive']={state:direction===1?'1330':'0',attributes:{unit_of_measurement:'W'}};
        c.hass={language,states,callService:()=>{throw Error('Unexpected service')}};
        const root=c.shadowRoot, get=id=>root.getElementById(id), rect=id=>get(id).getBoundingClientRect();
        const battery=get('battery-label').parentElement,flow=get('flow-label').parentElement;
        const b=battery.getBoundingClientRect(),f=flow.getBoundingClientRect(), live=root.querySelector('.live').getBoundingClientRect();
        const left=['connection','energy','duration'].map(rect),right=['charging','live-power','actual'].map(rect);
        results.push({language,width,profile,direction,
          stacked:f.top>=b.bottom,aligned:Math.abs(b.top-f.top)<1&&f.left>b.right,
          upperColumns:left.every((r,i)=>Math.abs(r.left-left[0].left)<1&&right[i].left>r.right&&Math.abs(r.top-right[i].top)<1&&(i===0||r.top>left[i-1].bottom)),
          overflow:c.scrollWidth>c.clientWidth,afterLive:Math.min(b.top,f.top)>=live.bottom,
          messagesBelow:rect('messages').top>=Math.max(b.bottom,f.bottom),
          battery:get('battery-soc').textContent,point:get('actual').textContent,
          statusHeading:get('wallbox-status-label').textContent,heading:get('battery-label').textContent,
          caption:get('battery-caption').textContent,captionSmall:get('battery-caption').classList.contains('caption'),
          redundantCaptionAbsent:!get('battery-soc-label'),
          captionBeforePair:get('battery-caption').nextElementSibling===get('battery-soc'),
          liveLabel:get('charging-label').textContent,evLabel:get('live-power-label').textContent,
          aria:get('battery-soc').getAttribute('aria-label'),tooltip:get('battery-soc').title,
          flowLabel:get('flow-label').textContent,flow:get('flow-power').textContent,
          flowAria:get('flow-power').getAttribute('aria-label'),flowTitle:get('flow-power').title,
          flowColor:getComputedStyle(get('flow-power')).color});
        c.remove();
      }
      document.body.innerHTML='<pre id="results">'+JSON.stringify(results)+'</pre>';
      </script></body></html>`;
    const path=join(dir,'test.html');writeFileSync(path,html);
    const chrome=process.env.CHROME_BIN||'google-chrome';
    const result=spawnSync(chrome,['--headless','--no-sandbox','--disable-gpu','--no-proxy-server','--disable-dev-shm-usage','--user-data-dir='+join(dir,'profile'),'--dump-dom','file://'+path],{encoding:'utf8',timeout:30000,maxBuffer:1024*1024});
    assert.equal(result.status,0,result.stderr);
    const match=result.stdout.match(/<pre id="results">(.*?)<\/pre>/s);assert.ok(match,result.stdout);
    const results=JSON.parse(match[1]);assert.equal(results.length,96);
    for(const r of results) {
      const de=r.language==='de',grid=r.profile==='NETZ';
      for(const key of ['upperColumns','afterLive','messagesBelow','captionSmall','redundantCaptionAbsent','captionBeforePair']) assert.equal(r[key],true,`${key}: ${JSON.stringify(r)}`);
      assert.equal(r.overflow,false,JSON.stringify(r));
      assert.equal(r.width<=320?r.stacked:r.aligned,true,JSON.stringify(r));
      assert.match(r.point,grid?/6A → .*17A/:/3-(phasig|phase) 6A/);
      assert.equal(r.battery,de?'69,1 % → 40,0 %':'69.1 % → 40.0 %');
      assert.equal(r.statusHeading,de?'Wallbox Status':'Wallbox status');
      assert.equal(r.heading,de?'Hausspeicher':'Home battery');
      assert.equal(r.caption,de?'Ladung Ist → Soll':'Charge actual → target');
      assert.equal(r.liveLabel,de?'Ladezustand':'Charging state');
      assert.equal(r.evLabel,de?'EV Leistung':'EV power');
      assert.equal(r.aria,r.tooltip);assert.equal(r.flowAria,r.flowTitle);
      if(grid) assert.match(r.aria,de?/Geplante NETZ-Mindestreserve: .*Beobachtete Mindestreserve:/:/Planned NETZ minimum reserve: .*Observed minimum reserve:/);
      const labels=grid?(de?['Netzbezugsleistung','Netzeinspeiseleistung']:['Grid import power','Grid export power']):(de?['Entladeleistung','Ladeleistung']:['Discharging power','Charging power']);
      assert.equal(r.flowLabel,r.direction===null?(grid?(de?'Netzleistung':'Grid power'):(de?'Speicherleistung':'Battery power')):labels[r.direction]);
      assert.equal(r.flow,r.direction===null?'—':de?'1,33 kW':'1.33 kW');
      assert.equal(r.flowColor,r.direction===null?'rgb(90, 90, 90)':r.direction===0?'rgb(160, 0, 0)':'rgb(0, 120, 0)');
    }
  } finally {rmSync(dir,{recursive:true,force:true});}
});
