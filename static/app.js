const tg = window.Telegram?.WebApp;
if(tg){ tg.ready(); tg.expand(); try{ tg.setHeaderColor('#0B0F1A'); }catch(e){} const user=tg.initDataUnsafe?.user; if(user){ const b=document.getElementById('tgBanner'); b.classList.remove('hidden'); document.getElementById('tgUser').textContent='@'+(user.username||user.first_name); document.getElementById('tgUsername').value='@'+(user.username||user.id); } }

const $=s=>document.querySelector(s);
let webUserId = localStorage.getItem('webUserId') || ('web_' + Math.random().toString(36).slice(2,9));
localStorage.setItem('webUserId', webUserId);

function toast(msg, ok=true){
  const t=$('#toast'); t.textContent=msg; t.className='toast show'; t.style.borderColor= ok ? '#064E3B' : '#7F1D1D'; t.style.background= ok ? '#052E1A' : '#2A0A0A';
  setTimeout(()=> t.className='toast', 2400);
  if(tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred(ok?'success':'error');
}

async function refreshStats(){
  try{
    const r=await fetch('/api/health').then(r=>r.json());
    $('#statTotal').textContent=r.alerts_total;
    $('#statActive').textContent=r.active;
    $('#statMock').textContent=r.mock?'MOCK':'LIVE';
  }catch(e){}
}

function chipHandler(){
  document.querySelectorAll('.chip').forEach(ch=>{
    ch.onclick=()=>{
      const target = ch.dataset.months ? '#months' : '#durations';
      const input=$(target);
      if(ch.dataset.months) input.value=ch.dataset.months;
      if(ch.dataset.dur) input.value=ch.dataset.dur;
      if(tg?.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
    };
  });
}
chipHandler();

$('#btnHeroTelegram').onclick=()=> window.open('https://t.me/flightsaro_bot','_blank');
$('#btnHeroScroll').onclick=()=> document.querySelector('.main').scrollIntoView({behavior:'smooth'});
$('#btnRefresh').onclick=loadAlerts;
$('#btnCheck').onclick=async()=>{
  toast('Check immediato...', true);
  const r=await fetch('/api/check', {method:'POST'}).then(r=>r.json()).catch(()=>({ok:false}));
  toast(r.ok?'Check completato':'Check inviato', true);
  setTimeout(loadAlerts, 800);
};

async function loadAlerts(){
  const q=$('#search').value.trim().toLowerCase();
  const r=await fetch('/api/alerts?user_id='+encodeURIComponent(webUserId) + '&tg='+encodeURIComponent($('#tgUsername').value||'')).then(r=>r.json()).catch(()=>({alerts:[]}));
  const list=$('#alertsList');
  let alerts=r.alerts || [];
  if(q) alerts=alerts.filter(a=> (a.departure+' '+a.destination).toLowerCase().includes(q));
  if(!alerts.length){ list.innerHTML='<div class="empty">Nessun alert — creane uno a sinistra 👈<br><span class="hint">Oppure usa Telegram: @flightsaro_bot → /alert</span></div>'; return; }
  list.innerHTML='';
  alerts.forEach(a=>{
    const card=document.createElement('div'); card.className='alert-card';
    const status = a.active ? '<span class="badge on">● Attivo</span>' : '<span class="badge off">○ Disattivo</span>';
    const months = a.months.join(', ');
    const durs = a.durations.join(', ');
    card.innerHTML=`
      <div class="alert-top">
        <span class="alert-id">#${a.id}</span>
        ${status}
        <span class="alert-route">${a.departure} → ${a.destination}</span>
        <span style="margin-left:auto;font-weight:800;color:#10B981">≤ ${a.max_price}€</span>
      </div>
      <div class="alert-meta">📅 ${months} • ⏱ ${durs} giorni • hits ${a.hits||0} • ${new Date(a.created).toLocaleDateString('it-IT')}</div>
      <div class="alert-links">
        <button class="link" onclick="stopAlert(${a.id})">⏸ Disattiva</button>
        <button class="link" onclick="checkAlert(${a.id})">⚡ Check</button>
        <button class="link primary" onclick="previewLinks('${a.departure}','${a.destination}')">🔗 Link voli</button>
      </div>
    `;
    list.appendChild(card);
  });
}

async function createAlert(){
  const departure=$('#departure').value.trim();
  const destination=$('#destination').value.trim();
  const months=$('#months').value.trim();
  const durations=$('#durations').value.trim();
  const price=$('#price').value.trim();
  const tgUser=$('#tgUsername').value.trim();
  const msg=$('#formMsg');
  if(!departure||!destination||!months||!durations||!price){ msg.textContent='Compila tutti i campi'; msg.className='form-msg err'; return; }
  msg.textContent='Creazione...'; msg.className='form-msg';
  const body={departure, destination, months, durations, max_price: parseInt(price), user_id: webUserId, tg_username: tgUser, initData: tg?.initData || ''};
  try{
    const r=await fetch('/api/alerts', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}).then(r=>r.json());
    if(r.ok){
      msg.textContent=`✅ Alert #${r.alert.id} creato! Controllo ogni 30 min su Render.`; msg.className='form-msg ok';
      toast('Alert creato!', true);
      // se miniapp Telegram, prova a inviare via Telegram
      if(tg?.sendData) try{ tg.sendData(JSON.stringify({action:'alert_created', id:r.alert.id})); }catch(e){}
      loadAlerts(); refreshStats();
      if(tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    } else {
      msg.textContent='❌ '+(r.error||'Errore'); msg.className='form-msg err';
      toast(r.error||'Errore', false);
    }
  }catch(e){
    msg.textContent='❌ Errore rete'; msg.className='form-msg err';
  }
}
$('#btnCreate').onclick=createAlert;

async function stopAlert(id){
  await fetch('/api/alerts/'+id, {method:'DELETE', headers:{'Content-Type':'application/json'}, body: JSON.stringify({user_id: webUserId})});
  toast('Alert disattivato', true);
  loadAlerts(); refreshStats();
}
async function checkAlert(id){
  toast('Check...', true);
  await fetch('/api/check', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({alert_id:id})});
  toast('Check inviato', true);
}
function previewLinks(dep, dest){
  const d1=new Date(Date.now()+30*86400000).toISOString().slice(0,10);
  const d2=new Date(Date.now()+37*86400000).toISOString().slice(0,10);
  const q=encodeURIComponent(`volo da ${dep} a ${dest} ${d1} ritorno ${d2}`);
  window.open(`https://www.google.com/travel/flights?q=${q}`,'_blank');
}

$('#search').oninput=loadAlerts;

// init
refreshStats(); loadAlerts();
setInterval(()=>{ refreshStats(); loadAlerts(); }, 15000);

// Telegram MainButton
if(tg){
  tg.MainButton.setText('🚨 Crea alert');
  tg.MainButton.show();
  tg.MainButton.onClick(createAlert);
  tg.BackButton.onClick(()=> window.scrollTo({top:0, behavior:'smooth'}));
}
