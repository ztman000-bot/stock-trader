import {startVerifiedUpdate,resumeVerifiedUpdate} from './update-verification.js';
// Native Android server-update button hotfix.
// Avoids JavaScript modal confirmation because the minimal WebView shell does
// not provide a WebChromeClient. Uses an explicit two-tap confirmation instead.
(()=>{
  if(new URLSearchParams(location.search).get('native')!=='1')return;

  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  let requestStarted=false;
  const getStatusBox=()=>document.querySelector('#nativeServerUpdateStatus');
  const setStatus=(text,kind='info')=>{
    const box=getStatusBox();
    if(!box)return;
    box.textContent=text;
    box.dataset.kind=kind;
    box.style.color=kind==='ok'?'#5ee0a3':kind==='bad'?'#ff9a9a':'';
  };
  function showProgress(btn,text,state){
    setStatus(text,state==='success'?'ok':state==='failed'?'bad':'info');
    btn.disabled=state==='waiting';
    btn.textContent=state==='waiting'?'검증 중':state==='success'?'완료':'서버 업데이트';
  }

  async function startUpdate(btn){
    requestStarted=true;
    btn.disabled=true;
    btn.textContent='요청 중';
    setStatus('업데이트 요청을 서버로 전송 중...');
    try{
      const result=await startVerifiedUpdate((text,state)=>showProgress(btn,text,state));
      showProgress(btn,result.message,result.state);
      if(result.state==='success'){
        await sleep(2000);
        location.reload();
      }
    }catch(err){
      showProgress(btn,`업데이트 요청/확인 실패: ${err?.message||err}`,'failed');
    }
  }

  function bind(){
    const btn=document.querySelector('#nativeServerUpdateBtn');
    if(!btn||btn.dataset.webviewHotfix==='1')return Boolean(btn);
    btn.dataset.webviewHotfix='1';
    btn.dataset.confirmUntil='0';
    resumeVerifiedUpdate((text,state)=>{if(!requestStarted)showProgress(btn,text,state)}).catch(()=>{});
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
