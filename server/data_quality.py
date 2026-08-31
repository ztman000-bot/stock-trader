"""Read-only market-data quality audit for research/backtests."""
import sqlite3
from datetime import datetime
from collector import DB_PATH,KST

def audit(max_days=120):
 with sqlite3.connect(DB_PATH,timeout=10) as c:
  rows=c.execute("SELECT code,substr(bucket,1,10) d,bucket,open,high,low,close,volume FROM bars_5m ORDER BY code,bucket").fetchall()
 groups={}
 for row in rows:
  groups.setdefault((row[0],row[1]),[]).append(row)
 out=[]
 for (code,day),arr in groups.items():
  seen=set();dup=bad=0
  for _,_,bucket,o,h,l,cl,v in arr:
   try:
    dt=datetime.fromisoformat(str(bucket)).astimezone(KST);hm=dt.hour*60+dt.minute
    if hm in seen:dup+=1
    seen.add(hm)
    if min(float(o),float(h),float(l),float(cl))<=0 or int(v)<0:bad+=1
   except:bad+=1
  expected=set(range(540,926,5));missing=len(expected-seen);n=len(arr)
  grade='GOOD' if n>=76 and missing<=2 and dup==0 and bad==0 else 'PARTIAL' if n>=65 and missing<=13 and bad==0 else 'BAD'
  out.append({'code':code,'date':day,'bars':n,'missingBuckets':missing,'duplicates':dup,'badRows':bad,'grade':grade})
 out=out[-max(1,int(max_days))*100:]
 counts={g:sum(1 for x in out if x['grade']==g) for g in ('GOOD','PARTIAL','BAD')}
 return {'ok':True,'daysAudited':len(out),'counts':counts,'goodPct':round(100*counts['GOOD']/len(out),2) if out else 0,'badExamples':[x for x in out if x['grade']!='GOOD'][-20:]}
