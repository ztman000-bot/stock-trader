"""Robust validation layer. Research-only and deliberately independent of live rules."""
from profitability_lab import _candidates,_eval,FILTERS,EXIT_CONFIGS,REGIME_MODES,SURGE_MODES,BASE_SLIPPAGE

def _date_slices(cands,folds=4):
 dates=sorted({x['date'] for x in cands});n=len(dates)
 if n<10:return [],set()
 lock_n=max(2,int(n*.20));dev=dates[:-lock_n];lock=set(dates[-lock_n:]);chunks=[]
 for k in range(folds):
  cut=max(3,int(len(dev)*(.45+.10*k)));test_end=min(len(dev),cut+max(1,int(len(dev)*.15)))
  if test_end>cut:chunks.append((set(dev[:cut]),set(dev[cut:test_end])))
 return chunks,lock

def run_robust_validation(max_codes=40):
 cands=_candidates(max(10,min(int(max_codes),100)));folds,lockbox=_date_slices(cands);results=[]
 for train,test in folds:
  ranked=[]
  for f in FILTERS:
   for cfg in EXIT_CONFIGS:
    for rm in REGIME_MODES:
     for sm in SURGE_MODES:
      m=_eval(cands,f,cfg,rm,sm,train)
      if m['trades']>=8:ranked.append((m['expectancyPct'],m['profitFactor'],f,cfg,rm,sm,m))
  ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
  if not ranked:continue
  _,_,f,cfg,rm,sm,tr=ranked[0];te=_eval(cands,f,cfg,rm,sm,test);results.append({'train':tr,'test':te,'strategy':f['id'],'exit':cfg['id'],'market':rm['id'],'surge':sm['id']})
 positive=sum(1 for x in results if x['test']['profitFactor']>1 and x['test']['expectancyPct']>0)
 # Choose only from development folds; final lockbox is never used for selection.
 if results:
  winner=max(results,key=lambda x:(x['test']['expectancyPct'],x['test']['profitFactor']));f=next(x for x in FILTERS if x['id']==winner['strategy']);cfg=next(x for x in EXIT_CONFIGS if x['id']==winner['exit']);rm=next(x for x in REGIME_MODES if x['id']==winner['market']);sm=next(x for x in SURGE_MODES if x['id']==winner['surge']);lock=_eval(cands,f,cfg,rm,sm,lockbox);stress=_eval(cands,f,cfg,rm,sm,lockbox,BASE_SLIPPAGE*2,1)
 else:lock={'trades':0,'profitFactor':0,'expectancyPct':0,'winRate':0,'maxDrawdownPct':0};stress=dict(lock)
 pass_gate=bool(results and positive>=max(2,len(results)-1) and lock['trades']>=10 and lock['profitFactor']>1 and lock['expectancyPct']>0 and stress['profitFactor']>=1 and stress['expectancyPct']>=0)
 return {'ok':True,'researchOnly':True,'walkForward':{'folds':len(results),'positiveFolds':positive,'results':results},'lockbox':lock,'lockboxStress':stress,'oneMinuteExitValidation':{'ready':False,'reason':'현재 저장소는 5분봉 중심입니다. 1분봉 데이터가 축적되기 전에는 실전 승격 근거로 사용하지 않습니다.'},'pass':pass_gate,'gate':'walk-forward 반복 양수 + 미사용 final lockbox 양수 + lockbox 2x slippage/1bar-late 방어','liveRuleAutoMutation':False,'realOrderEnabled':False}
