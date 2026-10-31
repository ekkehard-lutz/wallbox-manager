// Real browser geometry: run with Chrome installed (as on the CI Ubuntu runner).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {mkdtempSync,writeFileSync,readFileSync,rmSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');

test('home battery aligns with wallbox parameters and stacks by card width',()=>{
  const dir=mkdtempSync(join(tmpdir(),'wbm-card-layout-'));
  try {
    const source=readFileSync('custom_components/wallbox_manager/www/wallbox-manager-card.js','utf8');
    const html=`<!doctype html><html><meta charset="utf-8"><style>body{margin:0} ha-card{display:block} wallbox-manager-card{margin:8px}</style><body>
      <script>${source}</script><script>
      const results=[];
      for (const language of ['de','en']) for (const width of [640,400,320,280]) {
        const c=document.createElement('wallbox-manager-card'); c.style.width=width+'px'; document.body.append(c); c.setConfig({entry_id:'E'});
        const states={};
        const put=(role,value,attrs={})=>states['E.'+role]={state:value,attributes:{wallbox_manager_entry:'E',wallbox_manager_scope:'connector',wallbox_manager_role:role,...attrs}};
        put('connected','on'); put('charging_profile','NETZ',{options:['NETZ'],planned_grid_reserve:40,battery_soc_entity_id:'sensor.soc',battery_reserve_entity_id:'number.reserve'});
        put('soll_power','4.8',{applied_phase_count:3,applied_current_a:6,requested_phase_count:1,requested_current_a:17});
        states['sensor.soc']={state:'69.125',attributes:{}};states['number.reserve']={state:'40',attributes:{}};
        c.hass={language,states,callService:()=>{throw Error('Unexpected service')}};
        const root=c.shadowRoot, battery=root.getElementById('battery-label').parentElement, parameters=root.getElementById('parameters-label').parentElement;
        const b=battery.getBoundingClientRect(),p=parameters.getBoundingClientRect();
        results.push({language,width,stacked:b.top>p.bottom,aligned:Math.abs(b.top-p.top)<1 && b.left>p.right,
          overflow:c.scrollWidth>c.clientWidth, battery:root.getElementById('battery-soc').textContent,
          point:root.getElementById('actual').textContent,
          heading:root.getElementById('battery-label').textContent,
          liveLabel:root.getElementById('charging-label').textContent,
          captionAbsent:!root.getElementById('battery-soc-label') && !battery.querySelector('.caption'),
          directPair:root.getElementById('battery-label').nextElementSibling===root.getElementById('battery-soc') && battery.children.length===2,
          aria:root.getElementById('battery-soc').getAttribute('aria-label'),
          tooltip:root.getElementById('battery-soc').title,
          pairAligned:Math.abs(root.getElementById('battery-soc').getBoundingClientRect().top-root.getElementById('actual').getBoundingClientRect().top)<1,
          headingGap:root.getElementById('battery-soc').getBoundingClientRect().top-root.getElementById('battery-label').getBoundingClientRect().bottom,
          afterLive:b.top>=root.querySelector('.live').getBoundingClientRect().bottom});
      }
      document.body.innerHTML='<pre id="results">'+JSON.stringify(results)+'</pre>';
      </script></body></html>`;
    const path=join(dir,'test.html'); writeFileSync(path,html);
    const chrome=process.env.CHROME_BIN || 'google-chrome';
    const result=spawnSync(chrome,['--headless','--no-sandbox','--disable-gpu','--no-proxy-server','--disable-dev-shm-usage','--user-data-dir='+join(dir,'profile'),'--dump-dom','file://'+path],{encoding:'utf8',timeout:30000,maxBuffer:1024*1024});
    assert.equal(result.status,0,result.stderr);
    const match=result.stdout.match(/<pre id="results">(.*?)<\/pre>/s);
    assert.ok(match,result.stdout);
    const results=JSON.parse(match[1]); assert.equal(results.length,8);
    for (const r of results) {
      assert.equal(r.overflow,false,JSON.stringify(r));
      assert.equal(r.afterLive,true,JSON.stringify(r));
      assert.equal(r.width<=320?r.stacked:r.aligned,true,JSON.stringify(r));
      assert.match(r.point,/6A → .*17A/);
      assert.equal(r.battery,r.language==='de'?'69,1 % → 40,0 %':'69.1 % → 40.0 %');
      assert.equal(r.heading,r.language==='de'?'Hausspeicher':'Home battery');
      assert.equal(r.liveLabel,r.language==='de'?'Ladezustand':'Charging state');
      assert.equal(r.captionAbsent,true);
      assert.equal(r.directPair,true);
      assert.equal(r.aria,r.tooltip);
      assert.match(r.aria,r.language==='de'?/Ladezustand: .*Geplante NETZ-Mindestreserve:/:/State of charge: .*Planned NETZ minimum reserve:/);
      assert.ok(Math.abs(r.headingGap-8)<1,JSON.stringify(r));
      if (r.aligned) assert.equal(r.pairAligned,true,JSON.stringify(r));
    }
  } finally { rmSync(dir,{recursive:true,force:true}); }
});
