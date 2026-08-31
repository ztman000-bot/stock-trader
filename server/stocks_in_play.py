"""Cross-independent Stocks-in-Play radar from the safe collector universe."""
from collector import latest_quotes,activity_metrics,instrument_meta,is_safe_code,universe_verified,collector,PROTECTED_CODES

def scan(limit=40):
 rows=[]
 for q in latest_quotes(getattr(collector,'watchlist',[]) or None):
  code=str(q.get('code') or '')
  if not code or code in PROTECTED_CODES:continue
  if universe_verified() and not is_safe_code(code):continue
  m=activity_metrics(q);price=float(q.get('price') or 0);op=float(q.get('day_open') or 0);hi=float(q.get('day_high') or 0);cum=int(q.get('cumulative_volume') or 0);change=float(q.get('change_rate') or 0);score=float(m.get('activityScore') or 0)
  if op>0 and price>0:
   openret=(price/op-1)*100
   if openret>=1:score+=10
   if openret>=2:score+=8
   if hi>0 and price>=hi*.995:score+=8
  if abs(change)>=2:score+=8
  if float(m.get('turnoverEok') or 0)>=20:score+=8
  rows.append({'code':code,'name':instrument_meta(code).get('name') or q.get('name') or code,'score':round(min(100,score),2),'price':price,'changeRate':change,'turnoverEok':m.get('turnoverEok'),'rangePct':m.get('rangePct'),'spreadPct':m.get('spreadPct'),'liquidityOk':m.get('liquidityOk'),'source':'SAFE_UNIVERSE_ACTIVITY','crossRequired':False})
 rows.sort(key=lambda x:(x['liquidityOk'],x['score'],x['turnoverEok'] or 0),reverse=True)
 return {'ok':True,'researchOnly':True,'crossIndependent':True,'universeSize':len(getattr(collector,'watchlist',[]) or []),'rows':rows[:max(1,min(int(limit),100))]}
