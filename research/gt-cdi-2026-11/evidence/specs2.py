U={"min_adtv":2000000,"min_price":1.0,"smallcap_below_median":False}
def rh(name,desc,expr,rebalance="QE",top=0.2,regime=None,exposure=None,months=None,pad=1):
    s={"kind":"rank_and_hold","name":name,"description":desc,"market":"B3","rebalance":rebalance,"weighting":"equal",
       "universe":U,"signal":{"expr":expr,"lookback":1},"selection":{"top_pct":top,"min_names":5,"dedup_roots":True},"warmup_pad":pad}
    if regime: s["regime"]=regime
    if exposure: s["exposure_expr"]=exposure
    if months: s["rebalance_months"]=months
    return s
DIV3="where(div_yield_12m, div_years_3y >= 3)"
DIV3_D="where(shift(div_yield_12m, 1), shift(div_years_3y, 1) >= 3)"
VOL126="-at_rebalance(roll_std(pct_change(adj_close), 126))"
EP="where(1 / f_pe_ratio_dyn, f_pe_ratio_dyn > 0)"
def trend(off=1): return {"add_ibov":False,"park_when_too_few":True,"inputs":{"trend":{"flag":"bench_above","offset":off}},"rules":[{"trend":True},{"default":True,"park":True}]}
S2={
 "GTC2 H4a div3y yield top20 QE": rh("GTC2 H4a div3y yield top20 QE","H4 regular dividend payers (paid in each of last 3 years), highest 12m yield, top 20%, quarterly.",DIV3),
 "GTC2 H4b div3y yield top20 semi": rh("GTC2 H4b div3y yield top20 semi","H4 as H4a, rebalanced only in Jun and Dec.",DIV3,months=[6,12]),
 "GTC2 H4c div3y yield top30 QE": rh("GTC2 H4c div3y yield top30 QE","H4 as H4a, top 30%.",DIV3,top=0.3),
 "GTC2 H5a lowvol trendgate ME": rh("GTC2 H5a lowvol trendgate ME","H5 126d low-vol top 20%, invested only when Ibovespa is above its 10m MA (offset 1), else CDI.",VOL126,rebalance="ME",regime=trend()),
 "GTC2 H5b EW trendgate ME": rh("GTC2 H5b EW trendgate ME","H5 B_EW book, invested only when Ibovespa above 10m MA (offset 1), else CDI.","adtv",rebalance="ME",top=1.0,regime=trend()),
 "GTC2 H5c lowvol trendgate QE": rh("GTC2 H5c lowvol trendgate QE","H5 as H5a on a quarterly grid.",VOL126,rebalance="QE",regime=trend()),
 "GTC2 H6a EP top20 QE": rh("GTC2 H6a EP top20 QE","H6 earnings yield (1/PE, positive earnings), top 20%, quarterly.",EP),
 "GTC2 H6b EP sector top20 QE": rh("GTC2 H6b EP sector top20 QE","H6 sector-relative earnings yield, top 20%, quarterly.","sector_rank("+EP+")"),
 "GTC2 H6c EP top20 ME": rh("GTC2 H6c EP top20 ME","H6 earnings yield, top 20%, monthly.",EP,rebalance="ME"),
 # neighbourhood probe for H5: own MA via exposure_expr on bench_px
 "GTC2 probe bench ma7": rh("GTC2 probe bench ma7","probe","adtv",rebalance="ME",top=1.0,exposure="(shift(bench_px,1) > roll_mean(shift(bench_px,1), 7)) * 1.0"),
}
