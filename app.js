const $=id=>document.getElementById(id);
const fmt=(n,d=5)=>n!==null&&n!==undefined&&Number.isFinite(Number(n))?Number(n).toFixed(d):'—';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when=s=>{try{return new Date(s).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'})}catch{return '—'}};
let lastSignal=null;

function notify(sig,d){
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  if(sig!==lastSignal&&(sig==='LONG'||sig==='SHORT')){
    new Notification(`EUR/USD ${sig}`,{body:`Entrée ~${fmt(d.plan.entry)} · SL ${fmt(d.plan.sl)} · TP2 ${fmt(d.plan.tp2)} (R:R ${d.plan.rr})`});
  }
}
async function load(){
  $('refresh').disabled=true;$('status').textContent='Actualisation…';
  try{
    const r=await fetch('/api/analysis',{cache:'no-store'});const d=await r.json();
    if(!r.ok)throw new Error(d.error||'Données indisponibles');
    render(d);notify(d.signal,d);lastSignal=d.signal;$('error').classList.add('hidden');
    $('status').textContent='Mis à jour '+when(d.generated_at);
  }catch(e){$('error').textContent=e.message;$('error').classList.remove('hidden');$('status').textContent='Données indisponibles';}
  finally{$('refresh').disabled=false}
}
function render(d){
  $('session').textContent=d.session.name;
  const b=d.fundamentals&&d.fundamentals.blackout;
  if(b&&b.active){$('blackout').textContent=`⚠ News majeure : ${b.event} — trading suspendu autour de la publication.`;$('blackout').classList.remove('hidden')}else $('blackout').classList.add('hidden');
  const s=$('signal');s.textContent=d.signal;s.className='signal '+(d.signal==='LONG'?'long':d.signal==='SHORT'?'short':'wait');
  $('state').textContent=d.state_text;
  $('s-tech').textContent=(d.tech_score>0?'+':'')+d.tech_score;$('s-fund').textContent=(d.fund_score>0?'+':'')+d.fund_score+' /4';
  $('s-total').textContent=`${d.score>0?'+':''}${d.score} / ±${d.threshold}`;
  $('bar').style.width=Math.min(100,Math.abs(d.score)/d.threshold*100)+'%';
  $('bar').style.background=Math.abs(d.score)>=d.threshold?(d.score>0?'var(--gr)':'var(--rd)'):'var(--cy)';
  $('price').textContent=fmt(d.price);$('ptime').textContent='dernière bougie '+when(d.time);
  const p=d.plan;$('entry').textContent=fmt(p.entry);$('sl').textContent=fmt(p.sl);$('tp1').textContent=fmt(p.tp1);$('tp2').textContent=fmt(p.tp2);
  $('risk').textContent=p.risk_pips?p.risk_pips+' pips':'—';$('rr').textContent=p.rr?'1 : '+p.rr:'—';
  $('target').textContent=p.target_note?'Cible : '+p.target_note+'.':'';
  $('checks').innerHTML=d.checks.map(c=>`<li><span class="${c.ok?'ok':'ko'}">${c.ok?'✔':'✘'}</span>${esc(c.label)}</li>`).join('');
  $('blockers').textContent=d.blockers.length?'Bloquant : '+d.blockers.join(' · '):'';
  $('reasons').innerHTML=d.reasons.length?d.reasons.map(x=>`<li>${esc(x)}</li>`).join(''):'<li class="muted">Aucune confluence détectée.</li>';
  const names={'60m':'H1','15m':'M15','5m':'M5'};
  const tg=d.timeframes.trigger,te=tg.event;
  $('tfs').innerHTML=['60m','15m','5m'].map(k=>{const t=d.timeframes[k],e=t.last_event;
    const col=t.trend==='HAUSSIÈRE'?'pos':t.trend==='BAISSIÈRE'?'neg':'';
    const pp=t.premium_pct;
    return `<div class="tf"><div class="n">${names[k]}</div><div class="t ${col}">${t.trend}</div><small>${e?`${e.kind} ${e.dir>0?'↑':'↓'} il y a ${e.bars_ago} b. @ ${fmt(e.level)}`:'—'}</small><small>Range : ${pp==null?'—':pp+' % '+(pp<=40?'(discount)':pp>=60?'(premium)':'(équilibre)')}</small><small>ATR ${fmt(t.atr_pips,1)} pips · ${esc(t.pattern)}</small></div>`}).join('')
   +`<div class="tf"><div class="n">DÉCLENCHEUR ${esc(tg.name)}</div><div class="t">${tg.state}</div><small>${te?`${te.kind} ${te.dir>0?'↑':'↓'} il y a ${te.bars_ago} b.`:'Pas de CHoCH/BOS récent dans le sens du biais'}</small><small>DXY H1 ${d.dxy.h1.toLowerCase()} · M15 ${d.dxy.m15.toLowerCase()}</small></div>`;
  $('zones').innerHTML=d.zones.length?d.zones.map(z=>`<div class="row ${z.inside?'in':''}"><span>${esc(z.name)}${z.inside?' ◀ prix dedans':''}</span><span>${fmt(z.low)} – ${fmt(z.high)}</span></div>`).join(''):'<span class="muted">Aucune zone non mitigée proche.</span>';
  $('levels').innerHTML=d.levels.length?d.levels.map(l=>`<div class="row"><span>${esc(l.name)}</span><span>${fmt(l.price)} <small>${l.kind==='high'?'↑ liquidité haute':l.kind==='low'?'↓ liquidité basse':''}</small></span></div>`).join(''):'<span class="muted">—</span>';
  renderFund(d.fundamentals);
  $('warnings').innerHTML=[...d.warnings,...Object.entries(d.data_warnings||{}).map(([k,v])=>`Donnée optionnelle indisponible (${k}) : ${v}`)].map(x=>`<li>${esc(x)}</li>`).join('');
  $('source').textContent='Sources : '+d.source;
}
function renderFund(f){
  if(!f)return;
  $('f-bias').textContent=f.bias_label;$('f-score').textContent=`${f.score>0?'+':''}${f.score} / ±${f.max}`;
  $('f-comps').innerHTML=f.components.map(c=>`<div class="row"><span><b>${esc(c.name)}</b><br><small>${esc(c.detail)}</small></span><b class="${c.score>0?'pos':c.score<0?'neg':''}">${c.score>0?'+':''}${c.score}</b></div>`).join('');
  const p=f.policy;$('f-policy').textContent=p&&p.available?`Fed (borne haute) ${p.fed_upper.toFixed(2)} % · BCE dépôt ${p.ecb_deposit.toFixed(2)} % · écart ${p.diff_bp} pb (contexte, non noté)`:'Taux indisponibles.';
  const ev=(f.calendar&&f.calendar.events)||[];
  $('f-cal').innerHTML=ev.length?ev.slice(0,8).map(e=>{const m=e.minutes_to,t=m<0?`il y a ${-m} min`:m<60?`dans ${m} min`:when(e.time);
    return `<div class="row"><span>${e.impact==='High'?'🔴':'🟠'} ${esc(e.country)} · ${esc(e.title)}<br><small>prév. ${esc(e.forecast||'—')} · préc. ${esc(e.previous||'—')}</small></span><small>${t}</small></div>`}).join(''):'<span class="muted">Calendrier indisponible.</span>';
  $('f-note').textContent=f.disclaimer;
}
async function backtest(){
  const btn=$('bt-run');btn.disabled=true;btn.textContent='Calcul…';$('bt').textContent='Récupération des bougies…';
  try{
    const q=new URLSearchParams({days:$('bt-days').value,cost_pips:$('bt-cost').value,strategy:$('bt-strat').value});
    const r=await fetch('/api/backtest?'+q);const d=await r.json();if(!r.ok)throw new Error(d.error||'Backtest indisponible');
    const st=[['Trades',d.trades],['Réussite',d.win_rate_pct==null?'—':d.win_rate_pct+' %'],['Espérance',d.expectancy_R==null?'—':d.expectancy_R+' R'],['Profit factor',d.profit_factor==null?'—':d.profit_factor],['Résultat net',d.net_R+' R'],['Drawdown max',d.max_drawdown_R+' R'],['Espérance 1re moitié',d.expectancy_first_half==null?'—':d.expectancy_first_half],['Espérance 2e moitié',d.expectancy_second_half==null?'—':d.expectancy_second_half],['Unité',d.interval]];
    $('bt').innerHTML=`<div class="stats">${st.map(([a,b])=>`<div class="stat"><span>${a}</span><b>${b}</b></div>`).join('')}</div>${curve(d.equity_curve)}<p class="tiny muted">${esc(d.method)}</p><ul class="list tiny">${d.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
  }catch(e){$('bt').innerHTML=`<div class="alert">${esc(e.message)}</div>`}
  finally{btn.disabled=false;btn.textContent='Lancer'}
}
function curve(v){
  if(!v||v.length<2)return '<p class="muted tiny">Pas assez de trades pour une courbe.</p>';
  const w=600,h=140,mn=Math.min(0,...v),mx=Math.max(0,...v),sx=w/(v.length-1),sy=y=>h-6-(y-mn)/((mx-mn)||1)*(h-12);
  const pts=v.map((y,i)=>`${(i*sx).toFixed(1)},${sy(y).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:140px;background:#0c1422;border-radius:8px"><line x1="0" x2="${w}" y1="${sy(0)}" y2="${sy(0)}" stroke="#263348"/><polyline fill="none" stroke="${v[v.length-1]>=0?'#51d6a1':'#ff7587'}" stroke-width="2" points="${pts}"/></svg><p class="tiny muted">Courbe d'équité cumulée (en R).</p>`;
}
$('refresh').onclick=load;$('bt-run').onclick=backtest;
$('notif').onclick=async()=>{if(!('Notification' in window))return alert('Notifications non supportées par ce navigateur.');const p=await Notification.requestPermission();$('notif').textContent=p==='granted'?'🔔 Activées':'🔕 Refusées'};
if('Notification' in window&&Notification.permission==='granted')$('notif').textContent='🔔 Activées';
load();setInterval(load,60000);
