"""Browser kiosk served by the backend: GET /kiosk (burst capture, Tier 2 colour flash + head turn, OTP step-up, enrol)
and GET /config (zones/users for the dropdowns). Same origin as the API, so no CORS/URL setup."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from backend.campus_adapter import load_policy_seed

router = APIRouter()


@router.get("/config")
def config():
    seed = load_policy_seed()
    return {"zones": [{"id": z["id"], "name": z["name"], "risk": z["risk"]} for z in seed["zones"]],
            "users": [{"id": u["id"], "group": u["groups"][0], "status": u["status"]} for u in seed["users"]]}


@router.get("/kiosk", response_class=HTMLResponse)
def kiosk():
    return HTML


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HID Liveness Kiosk</title><style>
:root{--bg:#0b1b33;--panel:#11284a;--ink:#e8f0ff;--mute:#8fa6c8;--allow:#1fbf75;--step:#f0a020;--deny:#e5484d;--line:#25406b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,Segoe UI,Arial}
header{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line)}
header b{background:#2a6df4;padding:2px 10px;border-radius:4px;letter-spacing:2px}
main{display:grid;grid-template-columns:minmax(300px,1.2fr) minmax(300px,1fr);gap:16px;padding:16px;max-width:1100px;margin:auto}
@media(max-width:800px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
video{width:100%;border-radius:10px;background:#000;transform:scaleX(-1)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}label{font-size:12px;color:var(--mute);display:block}
select,input,button{background:#0d2140;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}
button{cursor:pointer;background:#2a6df4;border-color:#2a6df4;font-weight:600}button.sec{background:#0d2140;border-color:var(--line)}button:disabled{opacity:.5}
#result{text-align:center;padding:18px;border:2px solid var(--line);min-height:190px}#outcome{font-size:44px;font-weight:800}
.ALLOW{border-color:var(--allow)!important;color:var(--allow)}.STEP_UP{border-color:var(--step)!important;color:var(--step)}.DENY{border-color:var(--deny)!important;color:var(--deny)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:13px;color:var(--mute);text-align:left;margin-top:10px}.kv span:nth-child(even){color:var(--ink);text-align:right}
small{color:var(--mute)}table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:3px 6px;border-bottom:1px solid var(--line);text-align:left}
#flash{position:fixed;inset:0;display:none;z-index:9;align-items:flex-start;justify-content:center;padding-top:8vh;color:#fff;font:700 34px system-ui;text-shadow:0 2px 6px #000}
</style></head><body>
<div id="flash"><div id="flashmsg"></div></div>
<header><b>HID</b><div><strong>Liveness &amp; Access Kiosk</strong><br><small id="status">connecting…</small></div></header>
<main><section class="card"><video id="cam" autoplay playsinline muted></video>
<div class="row"><div><label>Badge</label><select id="badge"></select></div><div><label>Door</label><select id="zone"></select></div>
<div><label>Demo clock (optional)</label><input id="clock" placeholder="real time | Tue 23:30 | 2026-11-20 10:00" size="28"></div></div>
<div class="row"><button id="go">Scan &amp; request access</button><button id="enrol" class="sec">Enrol this face as selected badge</button></div>
<small>Scan captures ~6 frames in ~0.6 s. Enrolment stores face vectors in the Rekognition collection, not images.</small></section>
<section><div id="result" class="card"><div id="outcome">READY</div><div id="why">Look at the camera and press Scan.</div><div id="kv" class="kv"></div>
<div id="stepup" style="display:none;margin-top:12px"><div class="row" style="justify-content:center">
<button id="recap" class="sec">Recapture</button><button id="chal" class="sec">Liveness challenge (colour + head turn)</button><button id="otpreq" class="sec">Send me a code</button></div>
<div class="row" style="justify-content:center"><input id="otp" placeholder="6-digit code" size="10" maxlength="6"><button id="otpbtn">Verify code</button></div><small id="otphint"></small></div></div>
<div class="card" style="margin-top:12px"><strong>Recent decisions</strong><table id="log"><thead><tr><th>Time</th><th>Badge</th><th>Door</th><th>Outcome</th><th>ms</th></tr></thead><tbody></tbody></table></div></section></main>
<script>
const $=id=>document.getElementById(id);
const WHY={OK_IN_SCHEDULE:"Live person, identity verified, access allowed right now.",SPOOF_DETECTED:"Presentation attack detected (photo / screen / video / mask).",
ID_MISMATCH:"Face does not match the selected badge.",USER_UNKNOWN:"Badge is not enrolled or unknown.",USER_REVOKED:"This badge is suspended or revoked.",USER_EXPIRED:"Badge outside its validity dates.",
ZONE_NOT_AUTHORISED:"Not authorised for this door.",CALENDAR_BLOCK:"Door closed today (holiday, audit or lockdown).",OUT_OF_HOURS_STEPUP:"Outside permitted hours: approval code required.",
LOW_CONF_OUT_OF_HOURS:"Unclear scan and outside permitted hours.",LOW_CONFIDENCE:"Scan unclear: recapture, run the liveness challenge, or use a code.",HIGH_RISK_2FA:"High-security door: second factor required.",
CHALLENGE_FAILED:"Liveness challenge failed (colour reflection or head turn did not match).",CHALLENGE_EXPIRED:"Challenge expired. Start again.",INTERNAL_ERROR_FAIL_CLOSED:"System error: access denied (fail closed).",
ATTEMPTS_EXCEEDED:"Too many failed attempts. Locked."};
let last=null;
async function api(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});let j={};try{j=await r.json()}catch(e){};if(!r.ok&&!j.outcome&&!j.verified&&j.status===undefined)j={error:r.status,...j};return j.detail&&typeof j.detail==='object'&&!j.outcome?{...j.detail,_status:r.status}:{...j,_status:r.status}}
async function init(){try{const h=await api('/health');$('status').textContent='online · '+(h.region||'')+' · AWS Rekognition + B liveness model';
 const c=await api('/config');$('badge').innerHTML=c.users.map(u=>`<option value="${u.id}">${u.id} (${u.group})</option>`).join('');
 $('zone').innerHTML=c.zones.map(z=>`<option value="${z.id}">${z.name} · ${z.risk}</option>`).join('');}catch(e){$('status').textContent='backend not reachable'}
 try{$('cam').srcObject=await navigator.mediaDevices.getUserMedia({video:{width:640,height:480}})}catch(e){$('why').textContent='Camera unavailable: '+e.message}}
function grab(cv){const v=$('cam');cv.width=480;cv.height=Math.round(480*v.videoHeight/v.videoWidth)||360;cv.getContext('2d').drawImage(v,0,0,cv.width,cv.height);return cv.toDataURL('image/jpeg',0.82)}
async function burst(n=6,gap=90){const cv=document.createElement('canvas'),o=[];for(let i=0;i<n;i++){o.push(grab(cv));await new Promise(r=>setTimeout(r,gap))}return o}
function show(res,ms){const o=res.outcome||'DENY';const box=$('result');box.className='card '+o;$('outcome').textContent=o.replace('_','-');
 $('why').innerHTML=(res.reasons||[res.error||'']).map(r=>WHY[r]||r).join('<br>');const t=res.timings||{},d=res.detail||{},kv=[];
 if(ms!=null)kv.push(['Decision time',ms.toFixed(0)+' ms'+(ms<=2000?' ✓ ≤ 2 s':' ✗ over 2 s')]);
 if(res.liveness!=null)kv.push(['Liveness',res.liveness.toFixed(2)]);if(res.similarity!=null)kv.push(['Identity match',res.similarity.toFixed(2)]);
 if(d.spoof_prob!=null)kv.push(['Spoof probability',d.spoof_prob.toFixed(2)]);if(d.phone_paper_confidence)kv.push(['Phone/paper label',d.phone_paper_confidence.toFixed(2)]);
 if(t.total_ms!=null)kv.push(['Server time',t.total_ms.toFixed(0)+' ms (liveness '+(t.liveness_ms||0).toFixed(0)+', Rekognition '+(t.rekognition_ms||0).toFixed(0)+')']);
 if(res.challenge&&res.challenge.colour)kv.push(['Colour sync',`score ${res.challenge.colour.score.toFixed(2)}, p=${res.challenge.colour.p_value.toFixed(3)}`]);
 kv.push(['Reason codes',(res.reasons||[]).join(', ')]);$('kv').innerHTML=kv.map(([a,b])=>`<span>${a}</span><span>${b}</span>`).join('');
 $('stepup').style.display=o==='STEP_UP'?'block':'none';last=res;
 const tr=document.createElement('tr');tr.innerHTML=`<td>${new Date().toLocaleTimeString()}</td><td>${$('badge').value}</td><td>${$('zone').value}</td><td>${o}</td><td>${ms!=null?ms.toFixed(0):''}</td>`;$('log').tBodies[0].prepend(tr)}
async function scan(){$('go').disabled=true;$('outcome').textContent='SCANNING…';$('result').className='card';
 try{const frames=await burst();const t0=performance.now();const {nonce}=await api('/session',{});
  const res=await api('/decide',{nonce,frames,badge_id:$('badge').value,zone_id:$('zone').value,now:$('clock').value||null});show(res,performance.now()-t0)}
 catch(e){$('outcome').textContent='ERROR';$('why').textContent=String(e)}$('go').disabled=false}
async function challenge(){$('chal').disabled=true;try{const ch=await api('/challenge',{});const names={red:'#ff2828',green:'#28ff28',blue:'#2828ff'};
 const act=ch.action==='turn_left'?'LEFT':'RIGHT';const fl=$('flash'),cv=document.createElement('canvas');fl.style.display='flex';
 $('flashmsg').textContent='Slowly turn your head to YOUR '+act+', then look back';const frames=[],t_ms=[],t0=performance.now(),total=ch.colors.length*ch.seg_ms+500;
 while(performance.now()-t0<total){const t=performance.now()-t0;fl.style.background=names[ch.colors[Math.min(Math.floor(t/ch.seg_ms),ch.colors.length-1)]]+'cc';
  frames.push(grab(cv));t_ms.push(Math.round(t));await new Promise(r=>setTimeout(r,70))}fl.style.display='none';
 const t1=performance.now();const res=await api('/challenge/verify',{challenge_id:ch.id,frames,t_ms,badge_id:$('badge').value,zone_id:$('zone').value,now:$('clock').value||null});
 show(res,performance.now()-t1)}catch(e){$('why').textContent=String(e);$('flash').style.display='none'}$('chal').disabled=false}
$('go').onclick=scan;$('recap').onclick=scan;$('chal').onclick=challenge;
$('otpreq').onclick=async()=>{const r=await api('/generate-otp',{badge_id:$('badge').value});$('otphint').textContent='DEMO mode: code is '+r.code_for_demo+' (normally sent by email/SMS)'};
$('otpbtn').onclick=async()=>{const r=await api('/verify-otp',{badge_id:$('badge').value,otp_code:$('otp').value});
 const ok=r.status==='VERIFIED';show({outcome:ok?'ALLOW':(r.status==='LOCKED_OUT'?'DENY':'STEP_UP'),reasons:[ok?'STEPUP_OK':(r.status==='LOCKED_OUT'?'ATTEMPTS_EXCEEDED':'OTP_WRONG')]},null);$('why').textContent=r.message||''};
$('enrol').onclick=async()=>{$('enrol').disabled=true;const cv=document.createElement('canvas'),badge=$('badge').value;let done=false,last='';
 const advice={LOW_SHARPNESS:'Image too soft: move a bit closer, clean the lens, add light on your face.',LOW_BRIGHTNESS:'Too dark: face a light source.',EYES_CLOSED:'Keep your eyes open and look at the camera.',
  OFF_ANGLE_POSE:'Face the camera straight on.',NO_FACE_DETECTED:'No face found: centre your face in view.',MULTIPLE_FACES_DETECTED:'Only one person in view, please.',FACE_NOT_INDEXED_BY_FILTER:'Frame rejected by the quality filter: try again with better light.'};
 for(let i=0;i<6&&!done;i++){$('result').className='card';$('outcome').textContent='ENROLLING…';$('why').textContent=`Hold still and look at the camera (${i+1}/6)`;
  const r=await api('/enroll',{badge_id:badge,image_base64:grab(cv).split(',')[1]});
  if(r.success){done=true;$('result').className='card ALLOW';$('outcome').textContent='ENROLLED';$('why').textContent=`Enrolled ${badge}. You can scan now.`;$('kv').innerHTML='';$('stepup').style.display='none'}
  else{last=r.error||(typeof r.detail==='string'?r.detail:JSON.stringify(r));await new Promise(x=>setTimeout(x,400))}}
 if(!done){const key=Object.keys(advice).find(k=>String(last).startsWith(k));$('result').className='card DENY';$('outcome').textContent='NOT ENROLLED';$('why').innerHTML=(key?advice[key]:last)+'<br><small>('+last+')</small>'}
 $('enrol').disabled=false};
init();
</script></body></html>"""
