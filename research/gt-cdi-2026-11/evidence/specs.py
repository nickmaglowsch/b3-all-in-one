import json
U={"min_adtv":2000000,"min_price":1.0,"smallcap_below_median":False}
SEL={"top_pct":0.2,"min_names":5,"dedup_roots":True}
def rh(name,desc,expr,rebalance="ME",sel=None,regime=None,shift=0,pad=1):
    s={"kind":"rank_and_hold","name":name,"description":desc,"market":"B3","rebalance":rebalance,
       "weighting":"equal","universe":U,"signal":{"expr":expr,"lookback":1,"shift":shift},
       "selection":sel or SEL,"warmup_pad":pad}
    if regime: s["regime"]=regime
    return s
VOL126="-at_rebalance(roll_std(pct_change(adj_close), 126))"
VOL252="-at_rebalance(roll_std(pct_change(adj_close), 252))"
MOM12="roll_sum(shift(log_ret, 1), 12)"
MOM6="roll_sum(shift(log_ret, 1), 6)"
EWSEL={"top_pct":1.0,"min_names":5,"dedup_roots":True}
def gate(kind):
    inp={"easing":{"flag":"is_easing","offset":1},"trend":{"flag":"bench_above","offset":1}}
    if kind=="a": return {"add_ibov":False,"park_when_too_few":True,"inputs":{"easing":inp["easing"]},"rules":[{"easing":True},{"default":True,"park":True}]}
    if kind=="b": return {"add_ibov":False,"park_when_too_few":True,"inputs":inp,"rules":[{"min_true":1},{"default":True,"park":True}]}
    if kind=="c": return {"add_ibov":False,"park_when_too_few":True,"inputs":inp,"rules":[{"easing":True,"trend":True},{"default":True,"park":True}]}
SPECS={
 "GTC B_EW liquid equal-weight": rh("GTC B_EW liquid equal-weight","Baseline: naive equal-weight liquid B3 book (ADTV>=R$2M, price>=1, dedup roots), monthly.","adtv",sel=EWSEL),
 "GTC H1a lowvol126 top20 ME": rh("GTC H1a lowvol126 top20 ME","H1 low-vol (leverage/lottery counterparty): 126d daily vol, top 20%, monthly. Pre-registered 2026-11-15.",VOL126),
 "GTC H1b lowvol252 top20 ME": rh("GTC H1b lowvol252 top20 ME","H1 low-vol: 252d daily vol, top 20%, monthly. Pre-registered.",VOL252),
 "GTC H1c lowvol126 top20 QE": rh("GTC H1c lowvol126 top20 QE","H1 low-vol: 126d daily vol, top 20%, quarterly. Pre-registered.",VOL126,rebalance="QE"),
 "GTC H2a EW easing gate": rh("GTC H2a EW easing gate","H2 COPOM-cycle gate on B_EW: invested iff is_easing (offset 1), else CDI. Pre-registered.","adtv",sel=EWSEL,regime=gate("a")),
 "GTC H2b EW easing-or-trend gate": rh("GTC H2b EW easing-or-trend gate","H2 gate on B_EW: invested iff is_easing OR bench_above (offset 1). Pre-registered.","adtv",sel=EWSEL,regime=gate("b")),
 "GTC H2c EW easing-and-trend gate": rh("GTC H2c EW easing-and-trend gate","H2 gate on B_EW: invested iff is_easing AND bench_above (offset 1). Pre-registered.","adtv",sel=EWSEL,regime=gate("c")),
 "GTC H3a mom12-1 top20 ME": rh("GTC H3a mom12-1 top20 ME","H3 momentum (disposition-effect counterparty): 12-1, top 20%, monthly. Pre-registered.",MOM12),
 "GTC H3b mom12-1 top20 QE": rh("GTC H3b mom12-1 top20 QE","H3 momentum: 12-1, top 20%, quarterly. Pre-registered.",MOM12,rebalance="QE"),
 "GTC H3c mom6-1 top20 ME": rh("GTC H3c mom6-1 top20 ME","H3 momentum: 6-1, top 20%, monthly. Pre-registered.",MOM6),
 "GTC B_BOVA BOVA11 buy-hold": {"kind":"fixed_weight","name":"GTC B_BOVA BOVA11 buy-hold","description":"Baseline: 100% BOVA11 (CDI before listing).","rebalance":"QE","sleeves":[{"ticker":"BOVA11.SA","target_pct":100}]},
}
