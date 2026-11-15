import json, sys, urllib.request, os, itertools
URL="https://b3-api.duckdns.org/mcp"
TOK=os.environ.get("QTX_TOKEN","proxy-injected")  # the cloud proxy injects real auth for this host
SESS_FILE=os.path.join(os.environ.get("TMPDIR","/tmp"),".qtx_sess")
def post(payload, sess=None):
    h={"Content-Type":"application/json","Accept":"application/json, text/event-stream","Authorization":"Bearer "+TOK}
    if sess: h["Mcp-Session-Id"]=sess
    req=urllib.request.Request(URL,data=json.dumps(payload).encode(),headers=h)
    with urllib.request.urlopen(req,timeout=600) as r:
        sid=r.headers.get("Mcp-Session-Id"); body=r.read().decode()
    if body.startswith("event:") or "data:" in body[:20]:
        datas=[l[5:].strip() for l in body.splitlines() if l.startswith("data:")]
        body=datas[-1] if datas else ""
    return sid, (json.loads(body) if body.strip() else None)
def init():
    sid,_=post({"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"research-client","version":"1"}}})
    post({"jsonrpc":"2.0","method":"notifications/initialized"},sid)
    open(SESS_FILE,"w").write(sid or "")
    return sid
def call(name,args):
    sid=open(SESS_FILE).read() if os.path.exists(SESS_FILE) else init()
    try:
        _,res=post({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":args}},sid or None)
    except urllib.error.HTTPError as e:
        sid=init(); _,res=post({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":args}},sid or None)
    return res
if __name__=="__main__":
    name=sys.argv[1]; args=json.loads(sys.argv[2]) if len(sys.argv)>2 else {}
    if name=="__list":
        sid=init(); _,r=post({"jsonrpc":"2.0","id":1,"method":"tools/list"},sid); print(json.dumps(r,indent=1)); sys.exit()
    r=call(name,args)
    if r and "result" in r:
        for c in r["result"].get("content",[]):
            print(c.get("text",c))
        if r["result"].get("isError"): print("ISERROR")
    else: print(json.dumps(r,indent=1))
