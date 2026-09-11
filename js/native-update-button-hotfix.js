// Native Android server-update button hotfix.
// Avoids JavaScript modal confirmation because the minimal WebView shell does
// not provide a WebChromeClient. Uses an explicit two-tap confirmation instead.
(()=>{
  if(new URLSearchParams(location.search).get('native')!=='1')return;

  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const getStatusBox=()=>document.querySelector('#nativeServerUpdateStatus');
  const setStatus=(text,kind='info')=>{
    const box=getStatusBox();
    if(!box)return;
    box.textContent=text;
    box.dataset.kind=kind;
    box.style.color=kind==='ok'?'#5ee0a3':kind==='bad'?'#ff9a9a':'';
  };
  async function json(url,opts={}){
    const r=await fetch(url,{cache:'no-store',...opts});
    let data={};
    try{data=await r.json()}catch{}
    if(!r.ok)throw new Error(data?.error||data?.detail||`HTTP ${r.status}`);
    return data;
  }

  async function startUpdate(btn){
    btn.disabled=true;
    btn.textContent='요청 중';
    setStatus('업데이트 요청을 서버로 전송 중...');

    let beforePid=null;
    try{
      const live=await json(`/api/system/liveness?update_hotfix_pre=${Date.now()}`);
      beforePid=Number(live?.pid||0)||null;
    }catch{}

    try{
      const result=await json('/api/system/update/run',{method:'POST',headers:{Accept:'application/json'}});
      setStatus(result?.message||'업데이트 요청 접수됨 · 테스트/백업/재시작 진행 중');
      btn.textContent='업데이트 중';
    }catch(err){
      setStatus(`업데이트 요청 실패: ${err?.message||err}`,'bad');
      btn.textContent='서버 업데이트';
      btn.disabled=false;
      return;
    }

    let offlineSeen=false;
    for(let i=0;i<90;i++){
      await sleep(2000);
      try{
        const live=await json(`/api/system/liveness?update_hotfix_poll=${Date.now()}`);
        const pid=Number(live?.pid||0)||null;
        if((beforePid&&pid&&pid!==beforePid)||(offlineSeen&&live?.ok)){
          setStatus('업데이트 완료 · 새 서버 재시작 확인 ✓','ok');
          btn.textContent='완료';
          await sleep(1000);
          location.reload();
          return;
        }
        try{
          const st=await json(`/api/system/update/status?update_hotfix_status=${Date.now()}`);
          const u=st?.update||{};
          if(u?.lastError){
            setStatus(`업데이트 실패: ${u.lastError}`,'bad');
            btn.textContent='서버 업데이트';
            btn.disabled=false;
            return;
          }
          if(u?.running)setStatus('업데이트 진행 중 · 테스트/DB 백업/서버 재시작 대기...');
        }catch{}
      }catch{
        offlineSeen=true;
        setStatus('서버 재시작 중 · 자동 재연결 대기...');
      }
    }
    setStatus('완료 확인 시간이 초과됐습니다. 잠시 후 새로고침해 확인하세요.','bad');
    btn.textContent='서버 업데이트';
    btn.disabled=false;
  }

  function bind(){
    const btn=document.querySelector('#nativeServerUpdateBtn');
    if(!btn||btn.dataset.webviewHotfix==='1')return Boolean(btn);
    btn.dataset.webviewHotfix='1';
    btn.dataset.confirmUntil='0';
    btn.addEventListener('click',event=>{
      event.preventDefault();
      event.stopImmediatePropagation();
      if(btn.disabled)return;
      const now=Date.now();
      const until=Number(btn.dataset.confirmUntil||0);
      if(now>until){
        btn.dataset.confirmUntil=String(now+8000);
        btn.textContent='한 번 더 누르기';
        setStatus('8초 안에 버튼을 한 번 더 누르면 안전 업데이트를 시작합니다. 열린 Paper 포지션이면 서버가 자동 차단합니다.');
        setTimeout(()=>{
          if(btn.disabled)return;
          if(Date.now()>Number(btn.dataset.confirmUntil||0)){
            btn.dataset.confirmUntil='0';
            btn.textContent='서버 업데이트';
          }
        },8200);
        return;
      }
      btn.dataset.confirmUntil='0';
      startUpdate(btn);
    },true);
    return true;
  }

  function start(){
    if(bind())return;
    const obs=new MutationObserver(()=>{if(bind())obs.disconnect()});
    obs.observe(document.documentElement,{childList:true,subtree:true});
    setTimeout(()=>obs.disconnect(),20000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
