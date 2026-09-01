"""Robust validation v0.17.2.
GOOD-only data, walk-forward development folds, untouched final lockbox, and cost/fill stress.
Research only; Control/live rules are never changed.
"""
from profitability_lab import (
    _candidates,_eval,FILTERS,EXIT_CONFIGS,REGIME_MODES,SURGE_MODES,
    ENTRY_MODES,PLAY_MODES,BASE_SLIPPAGE
)

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
                        for em in ENTRY_MODES:
                            for pm in PLAY_MODES:
                                m=_eval(cands,f,cfg,rm,sm,train,entry_mode=em,play_mode=pm)
                                if m['trades']>=8:
                                    complexity=f['penalty']+cfg['penalty']+rm['penalty']+sm['penalty']+em['penalty']+pm['penalty']
                                    objective=m['expectancyPct']+max(-.05,min(.05,(m['profitFactor']-1)*.05))-complexity
                                    ranked.append((objective,m['profitFactor'],f,cfg,rm,sm,em,pm,m))
        ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
        if not ranked:continue
        _,_,f,cfg,rm,sm,em,pm,tr=ranked[0]
        te=_eval(cands,f,cfg,rm,sm,test,entry_mode=em,play_mode=pm)
        results.append({
            'train':tr,'test':te,'strategy':f['id'],'exit':cfg['id'],'market':rm['id'],
            'surge':sm['id'],'entry':em['id'],'play':pm['id']
        })
    positive=sum(1 for x in results if x['test']['profitFactor']>1 and x['test']['expectancyPct']>0)
    if results:
        winner=max(results,key=lambda x:(x['test']['profitFactor']>1 and x['test']['expectancyPct']>0,x['test']['expectancyPct'],x['test']['profitFactor']))
        f=next(x for x in FILTERS if x['id']==winner['strategy'])
        cfg=next(x for x in EXIT_CONFIGS if x['id']==winner['exit'])
        rm=next(x for x in REGIME_MODES if x['id']==winner['market'])
        sm=next(x for x in SURGE_MODES if x['id']==winner['surge'])
        em=next(x for x in ENTRY_MODES if x['id']==winner['entry'])
        pm=next(x for x in PLAY_MODES if x['id']==winner['play'])
        lock=_eval(cands,f,cfg,rm,sm,lockbox,entry_mode=em,play_mode=pm)
        stress=_eval(cands,f,cfg,rm,sm,lockbox,BASE_SLIPPAGE*2,1,em,pm)
        selected={'strategy':f['id'],'exit':cfg['id'],'entry':em['id'],'market':rm['id'],'surge':sm['id'],'play':pm['id']}
    else:
        lock={'trades':0,'profitFactor':0,'expectancyPct':0,'winRate':0,'avgWinPct':0,'avgLossPct':0,'payoffRatio':0,'maxDrawdownPct':0}
        stress=dict(lock);selected=None
    pass_gate=bool(
        results and positive>=max(2,len(results)-1) and
        lock['trades']>=10 and lock['profitFactor']>1 and lock['expectancyPct']>0 and
        stress['profitFactor']>=1 and stress['expectancyPct']>=0
    )
    return {
        'ok':True,'version':'0.17.2','researchOnly':True,'qualityGate':'GOOD_ONLY',
        'candidateTrades':len(cands),'selectedForLockbox':selected,
        'walkForward':{'folds':len(results),'positiveFolds':positive,'results':results},
        'lockbox':lock,'lockboxStress':stress,
        'oneMinuteExitValidation':{
            'ready':False,
            'reason':'현재 저장소는 5분봉 중심입니다. 1분봉 데이터가 축적되기 전에는 실전 승격 근거로 사용하지 않습니다.'
        },
        'pass':pass_gate,
        'gate':'GOOD data + walk-forward 반복 양수 + 미사용 final lockbox 양수 + lockbox 2x slippage/1bar-late 방어',
        'liveRuleAutoMutation':False,'realOrderEnabled':False
    }
