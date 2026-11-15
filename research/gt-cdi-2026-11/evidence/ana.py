import json,math,glob
def _cdi_monthly():
    cv=json.load(open("runs/03_B_CDI_full.curve.json"))["curve"]
    d,v=cv["date"],cv["pretax"]; out={}
    for i in range(1,len(d)):
        g=v[i]/v[i-1]; y,m=int(d[i][:4]),int(d[i][5:7])
        if d[i]=="2026-12-31":   # partial quarter: Oct 1 .. Nov 15 = 1.5 months
            mg=g**(1/1.5); out[(2026,10)]=mg-1; out[(2026,11)]=mg**0.5-1
        else:
            for k in range(3): out[(y,m-2+k)]=g**(1/3)-1
    return out
CDI=_cdi_monthly()
def load(n):
    f=glob.glob(f"runs/{n:02d}_*.curve.json")[0]; return json.load(open(f))
def series(n,basis="aftertax"):
    cv=load(n)["curve"]; d,v=cv["date"],cv[basis]
    return [(d[i],v[i]/v[i-1]-1) for i in range(1,len(d))], d[0]
def cdi_for(date,months):
    y,m=int(date[:4]),int(date[5:7]); g=1
    for k in range(months):
        mm=m-k; yy=y
        while mm<=0: mm+=12; yy-=1
        g*=1+CDI.get((yy,mm),0)
    return g-1
def stats(n,lo,hi,basis="aftertax"):
    s,first=series(n,basis)
    step=3 if len(s)>1 and int(s[1][0][5:7])-int(s[0][0][5:7]) in (3,-9) else 1
    s=[(d,r) for d,r in s if lo<=d[:7]<=hi]
    if not s: return None
    ppy=12/step
    ex=[r-cdi_for(d,step) for d,r in s]
    rets=[r for d,r in s]
    yrs=len(s)/ppy
    nav=[1.0]
    for r in rets: nav.append(nav[-1]*(1+r))
    ann=nav[-1]**(1/yrs)-1
    cdig=1
    for d,_ in s: cdig*=1+cdi_for(d,step)
    cdi_ann=cdig**(1/yrs)-1
    mu=sum(ex)/len(ex); sd=math.sqrt(sum((x-mu)**2 for x in ex)/(len(ex)-1))
    vol=math.sqrt(sum((x-sum(rets)/len(rets))**2 for x in rets)/(len(rets)-1))*math.sqrt(ppy)
    sh=mu/sd*math.sqrt(ppy); dd=math.sqrt(sum(min(x,0)**2 for x in ex)/len(ex)); so=mu/dd*math.sqrt(ppy) if dd>0 else float('nan')
    peak=nav[0]; mdd=0; dur=0; maxdur=0
    for x in nav:
        if x>=peak: peak=x; dur=0
        else: dur+=1; maxdur=max(maxdur,dur)
        mdd=min(mdd,x/peak-1)
    win=sum(1 for x in ex if x>0)/len(ex)
    yr={}
    for d,r in s: yr[d[:4]]=yr.get(d[:4],1)*(1+r)
    return dict(n=len(s),years=round(yrs,2),ann=ann*100,cdi_ann=cdi_ann*100,vol=vol*100,sharpe=sh,t=sh*math.sqrt(yrs),sortino=so,
                mdd=mdd*100,dd_dur_periods=maxdur,dd_dur_months=maxdur*step,win=win*100,first=s[0][0],last=s[-1][0],
                yearly={k:round((v-1)*100,1) for k,v in yr.items()})
def fmt(st):
    if not st: return "n/a"
    return (f"[{st['first']}..{st['last']}, {st['years']}y] ann {st['ann']:.2f}% (CDI {st['cdi_ann']:.2f}%) vol {st['vol']:.1f}% "
            f"Sharpe {st['sharpe']:.2f} t {st['t']:.2f} Sortino {st['sortino']:.2f} MDD {st['mdd']:.1f}% DDdur {st['dd_dur_months']}m win {st['win']:.0f}%")
