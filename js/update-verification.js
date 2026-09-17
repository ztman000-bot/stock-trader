// Shared Android result interpretation. A PID change is not proof of an update.
const KEY='stock-trader-pending-update-v1';
const FAILED=new Set(['FAILED','ROLLED_BACK','INTERRUPTED']);

export function classifyUpdate(status,requestId){
  const u=status?.update||{};
  if(!requestId||!u.requestId)return {state:'waiting',message:'업데이트 검증 기록 확인 중...'};
  if(u.requestId!==requestId)return {state:'warning',message:'다른 업데이트 요청의 기록입니다. 현재 요청의 완료는 확인되지 않았습니다.'};
  if(FAILED.has(u.phase)||u.lastError)return {state:'failed',message:u.phase==='ROLLED_BACK'?'업데이트 실패 · 이전 코드로 돌아갔습니다. 서버 상태를 확인하세요.':u.lastError||'업데이트를 완료하지 못했습니다.'};
  const exact=Boolean(u.targetSha&&u.targetSha===status.runningCommit&&u.codeVerified===true&&u.localSafetyPassed===true);
  if(u.phase==='SUCCEEDED'&&exact&&u.verified===true&&u.referenceVerified===true){
    const ref=u.reference||{};
    return {state:'success',message:`업데이트 완료 · 코드 ${u.targetSha.slice(0,7)} · 안전검사 통과 · ${ref.enabled===false?'시장 참고자료 사용 안 함':`시장 집계 ${Number(ref.rows||0).toLocaleString()}행 확인`} ✓`};
  }
  if(u.phase==='COMPLETED_WITH_WARNINGS'&&exact)return {state:'warning',message:`코드 ${u.targetSha.slice(0,7)} 적용·안전검사 완료 · 시장 집계 확인 필요 (${u.reference?.state||'CHECK'})`};
  if(['SUCCEEDED','COMPLETED_WITH_WARNINGS'].includes(u.phase)&&!exact)return {state:'warning',message:'저장된 업데이트 기록과 실행 버전이 일치하지 않습니다. 서버 상태 확인이 필요합니다.'};
  const phases={QUEUED:'업데이트 준비 중',FETCHING:'최신 코드 확인 중',UPDATING:'안전검사·백업·서버 교체 중',VERIFYING:'실행 버전·시장 집계 검증 중'};
  return {state:'waiting',message:phases[u.phase]||'실행 버전과 검증 결과 확인 중...'};
}

async function json(url,options={}){
  const response=await fetch(url,{cache:'no-store',...options});
  const data=await response.json();
  if(!response.ok){
    if(response.status===409&&data.requestId)return {ok:true,alreadyRunning:true,requestId:data.requestId};
    throw new Error(data.error||data.detail||`HTTP ${response.status}`);
  }
  return data;
}

export async function waitForUpdate(requestId,onStatus,{attempts=300,delay=2000}={}){
  if(!requestId)throw new Error('이 서버에는 업데이트 검증 기록이 없습니다. 적용 후 새 화면에서 상태를 확인하세요.');
  for(let i=0;i<attempts;i++){
    await new Promise(resolve=>setTimeout(resolve,delay));
    let status;
    try{status=await json(`/api/system/update/status?verify=${Date.now()}`)}
    catch{onStatus('서버 재연결 대기 중...','waiting');continue}
    const result=classifyUpdate(status,requestId);
    onStatus(result.message,result.state);
    if(result.state!=='waiting'){
      try{sessionStorage.removeItem(KEY)}catch{}
      return result;
    }
  }
  return {state:'waiting',message:'완료를 아직 확인하지 못했습니다. 화면을 다시 열면 같은 업데이트 확인을 이어갑니다.'};
}

export async function startVerifiedUpdate(onStatus){
  const result=await json('/api/system/update/run',{method:'POST',headers:{Accept:'application/json'}});
  try{if(result.requestId)sessionStorage.setItem(KEY,result.requestId)}catch{}
  onStatus(result.message||'업데이트 요청 접수됨 · 검증 결과를 기다립니다.','waiting');
  return waitForUpdate(result.requestId,onStatus);
}

export async function resumeVerifiedUpdate(onStatus){
  let requestId=null;
  try{requestId=sessionStorage.getItem(KEY)}catch{}
  // Read once on page reopen: durable receipts also survive WebView restarts.
  const status=await json(`/api/system/update/status?resume=${Date.now()}`);
  requestId=requestId||status?.update?.requestId;
  if(!requestId)return null;
  const result=classifyUpdate(status,requestId);
  onStatus(result.message,result.state);
  if(result.state!=='waiting')return result;
  const final=await waitForUpdate(requestId,onStatus);
  onStatus(final.message,final.state);
  return final;
}
