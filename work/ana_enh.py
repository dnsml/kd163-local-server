import re
_D = chr(92) + "d"
PAT = re.compile(r"\[(" + _D + _D + ":" + _D + _D + ":" + _D + _D + r")\]"
                 + chr(92) + "s+(C->S|S->C)" + chr(92) + "s+cmd=(" + _D + "+)"
                 + chr(92) + "s+seq=(" + _D + "+)" + chr(92) + "s+len=(" + _D + "+)"
                 + chr(92) + "s+hex=([0-9a-fA-F]*)")

def vread(b,i):
    if i>=len(b): return None,i
    c=b[i]
    if c==0xCE and i+4<len(b): return int.from_bytes(b[i+1:i+5],'big'),i+5
    if c==0xCD and i+2<len(b): return int.from_bytes(b[i+1:i+3],'big'),i+3
    if c==0xCC and i+1<len(b): return b[i+1],i+2
    return c,i+1

def bindcoin(h):
    b=bytes.fromhex(h)
    for i in range(len(b)-4):
        if b[i]==0xCE and b[i+1]==0x00 and b[i+2]==0x22:
            return int.from_bytes(b[i+1:i+5],'big')
    return None

cur=None
rows=[]
for ln in open('logs/relay.log',encoding='utf-8',errors='ignore'):
    m=PAT.search(ln)
    if not m: continue
    t,d,cmd,seq,ln_,h=m.groups(); cmd=int(cmd); b=bytes.fromhex(h)
    if d=='C->S' and cmd==822:
        if cur: rows.append(cur)
        cur={'t':t,'req':h,'slot':b[0] if b else None}
    elif cur is None: continue
    elif d=='S->C' and cmd==818:
        i=1; recs=[]
        while i+8<len(b):
            slot=b[i]; j=i+1
            iid,j=vread(b,j)
            lv=b[j+2] if j+2<len(b) else None
            recs.append((slot,iid,lv)); i=j+22
        cur['recs']=recs
    elif d=='S->C' and cmd==28:
        cur['bc']=bindcoin(h)
        rows.append(cur); cur=None
if cur: rows.append(cur)

prev=None
print("时间      槽码 回818槽 物品     +N  绑定铜钱   本次消耗")
for r in rows:
    rec=r.get('recs',[(None,None,None)])[0]
    bc=r.get('bc')
    d=(prev-bc) if (prev is not None and bc is not None) else None
    print("%s 0x%02x   %s    %-6s  %-3s %-9s %s" % (r['t'], r['slot'] if r['slot'] is not None else -1, rec[0], rec[1], rec[2], bc, d))
    if bc is not None: prev=bc
