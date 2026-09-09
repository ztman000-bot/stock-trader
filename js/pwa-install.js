// Chrome/PWA install helper for Classic mobile UI.
// Display/install UX only: never talks to order APIs or mutates Paper/Control state.
(()=>{
  let deferredPrompt=null;
  let button=null;
  let statusBox=null;

  const standalone=()=>window.matchMedia?.('(display-mode: standalone)').matches||window.navigator.standalone===true;

  function message(text,kind='info'){
    if(!statusBox){
      const anchor=document.querySelector('#nhStatusText');
      if(!anchor)return;
      statusBox=document.createElement('div');
      statusBox.id='pwaInstallStatus';
      statusBox.className='backtest-box';
      statusBox.style.marginTop='8px';
      anchor.insertAdjacentElement('afterend',statusBox);
    }
    statusBox.textContent=text;
    statusBox.dataset.kind=kind;
  }

  function setState(){
    if(!button)return;
    if(standalone()){
      button.textContent='✓ 앱 설치됨';
      button.disabled=true;
      button.title='Chrome에서 설치된 앱으로 실행 중입니다.';
      return;
    }
    if(!window.isSecureContext){
      button.textContent='앱 설치 · HTTPS 필요';
      button.disabled=false;
      button.title='Chrome PWA 설치에는 HTTPS 보안 연결이 필요합니다.';
      return;
    }
    if(deferredPrompt){
      button.textContent='＋ 앱 설치';
      button.disabled=false;
      button.title='Chrome 앱 설치 창을 엽니다.';
    }else{
      button.textContent='앱 설치 준비';
      button.disabled=false;
      button.title='Chrome 설치 조건을 확인 중입니다.';
    }
  }

  async function install(){
    if(standalone())return;
    if(!window.isSecureContext){
      message('현재 주소가 HTTP라 Chrome 앱 설치가 불가능합니다. Tailscale 내부의 유효한 HTTPS 주소로 접속하면 설치 버튼이 활성화됩니다. 기존 HTTP 주소는 홈 화면 바로가기만 가능합니다.','warn');
      return;
    }
    if(!deferredPrompt){
      message('Chrome 설치 조건을 확인 중입니다. 잠시 후 다시 누르거나 Chrome 메뉴의 “앱 설치”를 확인하세요.','info');
      return;
    }
    const prompt=deferredPrompt;
    deferredPrompt=null;
    try{
      await prompt.prompt();
      const choice=await prompt.userChoice;
      if(choice?.outcome==='accepted')message('설치를 승인했습니다. 설치가 끝나면 홈 화면의 Stock Day Trader 앱으로 실행할 수 있습니다.','ok');
      else message('앱 설치가 취소되었습니다. 원할 때 다시 설치할 수 있습니다.','info');
    }catch(err){
      message(`앱 설치 요청 실패: ${err?.message||err}`,'warn');
    }
    setState();
  }

  function ensureButton(){
    if(button)return button;
    const head=document.querySelector('.connection-panel .panel-head');
    if(!head)return null;
    button=document.createElement('button');
    button.id='pwaInstallBtn';
    button.className='btn ghost compact';
    button.type='button';
    button.addEventListener('click',install);
    head.appendChild(button);
    setState();
    return button;
  }

  window.addEventListener('beforeinstallprompt',event=>{
    event.preventDefault();
    deferredPrompt=event;
    ensureButton();
    setState();
    message('Chrome 앱 설치 준비 완료 · 설치 후에는 브라우저 주소창 없이 독립 앱처럼 실행됩니다.','ok');
  });

  window.addEventListener('appinstalled',()=>{
    deferredPrompt=null;
    ensureButton();
    setState();
    message('Stock Day Trader 앱 설치가 완료되었습니다.','ok');
  });

  function start(){
    ensureButton();
    if(standalone())message('Chrome 설치 앱으로 실행 중입니다. 공기계 서버가 데이터/매매 연구를 계속 담당합니다.','ok');
    else if(!window.isSecureContext)message('Chrome 앱 설치를 사용하려면 현재 HTTP Tailscale 주소 대신 유효한 HTTPS 주소가 필요합니다.','warn');
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
