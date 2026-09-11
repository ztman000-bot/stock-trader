// Native Android client usability fixes: keep content above the fixed mobile nav
// and provide an explicit server-update control. The update action only calls the
// existing guarded Android updater; it never touches broker/order endpoints.
(()=>{
  const qs=new URLSearchParams(location.search);
  if(qs.get('native')!=='1')return;

  const style=document.createElement('style');
  style.id='nativeClientUsabilityFixes';
  style.textContent=`
    html.native-client,html.native-client body{min-height:100%;overflow-y:auto!important;-webkit-overflow-scrolling:touch}
    html.native-client body{padding-bottom:calc(126px + env(safe-area-inset-bottom))!important}
    html.native-client main.wrap{padding-bottom:calc(138px + env(safe-area-inset-bottom))!important}
    html.native-client .mobile-nav{padding-bottom:calc(8px + env(safe-area-inset-bottom))!important}
    html.native-client #nativeShadowPanel .panel-head{flex-wrap:wrap;align-items:center}
    html.native-client .native-shadow-actions{display:flex;gap:6px;align-items:center;margin-left:auto}
    html.native-client #nativeServerUpdateBtn{min-width:72px;white-space:nowrap}
    html.native-client #nativeServerUpdateStatus{margin-top:8px;font-size:11px;line-height:1.45}
    html.native-client main.wrap>section:last-child{margin-bottom:24px!important}
  `;
  document.head.appendChild(style);

  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const msgOf=(j,fallback='오류')=>j?.error||j?.detail||j?.message||fallback;

  async function getJson(url,opts={}){
    const r=await fetch(url,{cache:'no-store',...opts});
    let j={};
    try{j=await r.json()}catch{}
    if(!r.ok){const e=new Error(msgOf(j,`HTTP ${r.status}`));e.payload=j;throw e}
    return j;
  }

  function setUpdateStatus(text,kind='info'){
    const box=document.querySelector('#nativeServerUpdateStatus');
    if(!box)return;
    box.textContent=text;
    box.dataset.kind=kind;
    box.style.color=kind==='ok'?'#5ee0a3':kind==='bad'?'#ff9a9a':'';
  }

  async function runServerUpdate(){
    const btn=document.querySelector('#nativeServerUpdateBtn');
    if(!btn||btn.disabled)return;
    if(!confirm('공기계 Stock Trader 서버를 GitHub 최신 main으로 안전 업데이트할까요?\n\n열린 Paper 포지션이나 안전검사 실패 시 자동 차단됩니다.'))return;

    btn.disabled=true;
    btn.textContent='확인 중';
    setUpdateStatus('현재 서버 상태 확인 중...');

    let beforePid=null;
    try{
      const live=await getJson(`/api/system/liveness?native_update_pre=${Date.now()}`);
      beforePid=Number(live?.pid||0)||null;
    }catch{}

    try{
      const r=await getJson('/api/system/update/run',{method:'POST',headers:{'Accept':'application/json'}});
      setUpdateStatus(r?.message||'업데이트 요청 성공 · 안전검사/테스트/백업을 진행합니다.');
      btn.textContent='업데이트 중';
    }catch(err){
      setUpdateStatus(`업데이트 차단/실패: ${err?.message||err}`,'bad');
      btn.disabled=false;
      btn.textContent='서버 업데이트';
      return;
    }

    let sawOffline=false;
    let lastError='';
    for(let i=0;i<75;i++){
      await sleep(2000);
      try{
        const live=await getJson(`/api/system/liveness?native_update_poll=${Date.now()}`);
        const pid=Number(live?.pid||0)||null;
        if(beforePid&&pid&&pid!==beforePid){
          setUpdateStatus('업데이트 완료 · 새 서버 프로세스 재시작 확인 ✓','ok');
          btn.textContent='완료';
          await sleep(900);
          location.reload();
          return;
        }
        if(sawOffline&&live?.ok){
          setUpdateStatus('서버 재연결 완료 · 최신 화면을 다시 불러옵니다.','ok');
          btn.textContent='완료';
          await sleep(900);
          location.reload();
          return;
        }
        try{
          const st=await getJson(`/api/system/update/status?native_update_status=${Date.now()}`);
          const u=st?.update||{};
          if(u?.lastError){
            lastError=String(u.lastError);
            break;
          }
          if(u?.running)setUpdateStatus('업데이트 진행 중 · 테스트/DB 백업/서버 재시작 대기...');
        }catch{}
      }catch{
        sawOffline=true;
        setUpdateStatus('서버 재시작 중 · 자동 재연결 대기...');
      }
    }

    if(lastError)setUpdateStatus(`업데이트 실패: ${lastError}`,'bad');
    else setUpdateStatus('업데이트 완료 여부 확인 시간이 초과됐습니다. 새로고침 후 다시 확인하세요.','bad');
    btn.disabled=false;
    btn.textContent='서버 업데이트';
  }

  function installControls(){
    const panel=document.querySelector('#nativeShadowPanel');
    const detail=document.querySelector('#nativeUiModeBtn');
    const head=panel?.querySelector('.panel-head');
    if(!panel||!detail||!head)return false;

    let actions=head.querySelector('.native-shadow-actions');
    if(!actions){
      actions=document.createElement('div');
      actions.className='native-shadow-actions';
      head.appendChild(actions);
    }

    if(!document.querySelector('#nativeServerUpdateBtn')){
      const update=document.createElement('button');
      update.id='nativeServerUpdateBtn';
      update.type='button';
      update.className='btn secondary-action compact';
      update.textContent='서버 업데이트';
      update.addEventListener('click',runServerUpdate);
      actions.appendChild(update);
    }
    if(detail.parentElement!==actions)actions.appendChild(detail);

    if(!document.querySelector('#nativeServerUpdateStatus')){
      const status=document.createElement('div');
      status.id='nativeServerUpdateStatus';
      status.className='backtest-box';
      status.textContent='업데이트 버튼은 공기계 서버만 갱신합니다. APK 자체 업데이트는 별도 설치가 필요합니다.';
      head.insertAdjacentElement('afterend',status);
    }
    return true;
  }

  function start(){
    if(installControls())return;
    const obs=new MutationObserver(()=>{if(installControls())obs.disconnect()});
    obs.observe(document.documentElement,{subtree:true,childList:true});
    setTimeout(()=>obs.disconnect(),20000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
