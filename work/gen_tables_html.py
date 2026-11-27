# -*- coding: utf-8 -*-
"""生成《怪物 + 技能总表》单页 HTML（可搜索/筛选/排序）。

数据来源：res_cfg/Monster.xml、res_cfg/Skill.xml、res_cfg/Class.xml
输出：docs/reference/怪物与技能表.html
"""
import json
import os
import re
import sys
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "res_cfg")
OUT = os.path.join(ROOT, "docs", "reference/怪物与技能表.html")

ITEM = re.compile(r'<item ([^>]*?)/>')


def load(name):
    with open(os.path.join(CFG, name), encoding="utf-8", errors="ignore") as f:
        s = f.read()
    out = []
    for m in ITEM.finditer(s):
        d = dict(re.findall(r'([A-Za-z_]\w*)="([^"]*)"', m.group(1)))
        if d.get("id"):
            out.append(d)
    return out


def num(v, d=0):
    try:
        return float(v)
    except Exception:
        return d


mobs_raw = load("Monster.xml")
sk_raw = load("Skill.xml")
cls_raw = load("Class.xml")

# ---------- 职业名 ----------
CLASS_NAME = {}
for d in cls_raw:
    CLASS_NAME.setdefault(d["id"], d["name"])

TYPE_TIER = {
    "1": "普通怪",
    "2": "精英怪",
    "3": "头目",
    "4": "世界BOSS",
    "6": "剧情/特殊",
}

TT = {
    "1": "自身/无目标",
    "2": "友方单体",
    "3": "友方群体",
    "4": "敌方单体",
    "5": "敌方群体",
    "6": "敌方特殊单体",
    "7": "我方特殊",
    "8": "敌方特殊群体",
}

STYPE = {"1": "主动", "2": "被动", "4": "心法/效果", "5": "状态/常驻", "6": "特殊"}

# ---------- 怪物 ----------
mobs = []
for d in mobs_raw:
    t = d.get("monster_type", "1")
    mobs.append({
        "id": int(d["id"]),
        "name": d.get("name", ""),
        "lv": int(num(d.get("level"), 0)),
        "hp": int(num(d.get("hp_max"), 0)),
        "type": t,
        "tier": TYPE_TIER.get(t, "其他"),
        "talk": (d.get("fight_talk") or d.get("round_talk") or "").strip(),
    })
mobs.sort(key=lambda x: (x["type"], x["lv"], x["id"]))

# ---------- 技能 ----------
skills = []
for d in sk_raw:
    skills.append({
        "id": int(d["id"]),
        "name": d.get("name", ""),
        "cls": d.get("class", ""),
        "lv": int(num(d.get("level"), 0)),
        "pct": num(d.get("damage_percent"), 0),
        "mp": int(num(d.get("consume_mp"), 0)),
        "tt": d.get("target_type", ""),
        "st": d.get("type", ""),
        "cat": d.get("category", ""),
        "cd": num(d.get("cooldown"), 0),
        "desc": d.get("description", ""),
    })
skills.sort(key=lambda x: (x["cls"], x["id"]))

# ---------- 玩家技能：按职业 + 技能名聚合 ----------
player = OrderedDict()
for cid in ("1", "2", "3", "4", "5", "6"):
    pub = [s for s in skills if s["cls"] == cid]
    if not pub:
        continue
    groups = OrderedDict()
    for s in sorted(pub, key=lambda x: x["id"]):
        groups.setdefault(s["name"], []).append(s)
    player[cid] = {
        "name": CLASS_NAME.get(cid, "未知"),
        "list": [{
            "name": k,
            "st": v[0]["st"],
            "tt": v[0]["tt"],
            "cat": v[0]["cat"],
            "desc": v[0]["desc"],
            "rows": [{"id": x["id"], "lv": x["lv"], "pct": x["pct"],
                      "mp": x["mp"], "cd": x["cd"]} for x in v],
        } for k, v in groups.items()],
    }

mon_skills = [s for s in skills if s["cls"] == "8"]
other_skills = [s for s in skills if s["cls"] in ("7", "9")]

stat = {
    "mobs": len(mobs),
    "mobTypes": Counter(m["type"] for m in mobs).most_common(),
    "lvMin": min(m["lv"] for m in mobs),
    "lvMax": max(m["lv"] for m in mobs),
    "skills": len(skills),
    "monSkills": len(mon_skills),
    "playerSkills": sum(len(v["list"]) for v in player.values()),
}

DATA = {
    "mobs": mobs, "player": player,
    "monSkills": mon_skills, "otherSkills": other_skills,
    "stat": stat, "TT": TT, "STYPE": STYPE, "TIER": TYPE_TIER,
}
payload = json.dumps(DATA, ensure_ascii=False, separators=(",", ":"))

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>怪物 · 技能总表</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f5f6f8;color:#1b1f27;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#6b7280;font-size:12.5px;margin-bottom:22px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.card{background:#fff;border:1px solid #e6e8ee;border-radius:10px;padding:14px 16px}
.card .k{color:#6b7280;font-size:12px}
.card .v{font-size:22px;font-weight:600;margin-top:2px}
.tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.tab{padding:8px 15px;border-radius:8px;background:#fff;border:1px solid #e6e8ee;cursor:pointer;font-size:13px;color:#4b5563}
.tab.on{background:#1b1f27;border-color:#1b1f27;color:#fff}
.panel{display:none}.panel.on{display:block}
.tools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
input[type=search],select{padding:8px 11px;border:1px solid #dfe3ea;border-radius:8px;background:#fff;font-size:13px;outline:none;min-width:150px}
input[type=search]{min-width:240px}
input[type=search]:focus,select:focus{border-color:#1b1f27}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e6e8ee;border-radius:10px;overflow:hidden;font-size:13px}
th{background:#fafbfc;text-align:left;padding:9px 12px;font-weight:600;color:#4b5563;border-bottom:1px solid #e6e8ee;white-space:nowrap;cursor:pointer;user-select:none;font-size:12.5px}
th:hover{color:#111}
td{padding:8px 12px;border-bottom:1px solid #f0f2f5;vertical-align:top}
tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbfc}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11.5px;background:#eef1f5;color:#4b5563;white-space:nowrap}
.p1{background:#eef1f5;color:#4b5563}.p2{background:#e7f0ff;color:#1d5fd0}
.p3{background:#fff3e0;color:#b46000}.p4{background:#fdecec;color:#b52020}
.p6{background:#f3ecff;color:#6b2fb5}
.mut{color:#9aa3af}
.scroll{max-height:640px;overflow:auto;border-radius:10px}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.skillcard{background:#fff;border:1px solid #e6e8ee;border-radius:10px;padding:14px 16px;margin-bottom:10px}
.skillcard h4{margin:0 0 6px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.skillcard .meta{color:#6b7280;font-size:12.5px;margin-bottom:8px}
.lvtable{border:1px solid #eef0f4;border-radius:8px;overflow:hidden}
.lvtable table{border:0;font-size:12.5px}
.lvtable th{background:#fafbfc;font-size:11.5px;padding:6px 10px}
.lvtable td{padding:5px 10px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.chip{padding:6px 13px;border-radius:20px;border:1px solid #e6e8ee;background:#fff;cursor:pointer;font-size:12.5px;color:#4b5563}
.chip.on{background:#1b1f27;color:#fff;border-color:#1b1f27}
.note{background:#fff;border:1px solid #e6e8ee;border-left:3px solid #1b1f27;border-radius:8px;padding:12px 15px;font-size:13px;color:#374151;margin-bottom:18px}
.note b{color:#111}
.empty{padding:26px;text-align:center;color:#9aa3af}
</style></head><body><div class="wrap">
<h1>怪物 · 技能总表</h1>
<div class="sub">KD163 本地服 · 直接读 res_cfg/Monster.xml + Skill.xml 生成，未做任何人工改写</div>
<div class="cards" id="cards"></div>

<div class="note" id="note"></div>

<div class="tabs">
  <div class="tab on" data-p="mobs">怪物图鉴</div>
  <div class="tab" data-p="player">玩家技能（5职业）</div>
  <div class="tab" data-p="mon">怪物技能</div>
  <div class="tab" data-p="other">心法 / 抵抗 / 祝福</div>
</div>

<div class="panel on" id="p-mobs">
  <div class="tools">
    <input type="search" id="mq" placeholder="搜名字 / ID / 台词">
    <select id="mt"><option value="">全部类型</option></select>
    <select id="ml"><option value="">全部等级</option></select>
    <span class="mut" id="mcount"></span>
  </div>
  <div class="scroll"><table>
    <thead><tr><th data-k="id">ID</th><th data-k="name">名称</th><th data-k="lv">等级</th>
    <th data-k="hp">血量</th><th data-k="type">类型</th><th>战斗台词</th></tr></thead>
    <tbody id="mbody"></tbody>
  </table></div>
</div>

<div class="panel" id="p-player">
  <div class="chips" id="pcls"></div>
  <div id="pbody"></div>
</div>

<div class="panel" id="p-mon">
  <div class="tools">
    <input type="search" id="sq" placeholder="搜技能名 / ID">
    <select id="stt"><option value="">全部目标类型</option></select>
    <span class="mut" id="scount"></span>
  </div>
  <div class="scroll"><table>
    <thead><tr><th>ID</th><th>技能名</th><th>等级</th><th>倍率</th>
    <th>目标</th><th>类型</th></tr></thead>
    <tbody id="sbody"></tbody>
  </table></div>
</div>

<div class="panel" id="p-other">
  <div class="tools">
    <input type="search" id="oq" placeholder="搜名字 / ID">
    <select id="ocls"><option value="">全部</option><option value="7">心法·抵抗(cls7)</option><option value="9">祝福·重生(cls9)</option></select>
    <span class="mut" id="ocount"></span>
  </div>
  <div class="scroll"><table>
    <thead><tr><th>ID</th><th>名称</th><th>归属</th><th>等级</th><th>耗蓝</th>
    <th>目标</th><th>类型</th></tr></thead>
    <tbody id="obody"></tbody>
  </table></div>
</div>
</div>

<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = t => String(t==null?'':t).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ---- 概览卡 ---- */
const tiers = {};
(D.stat.mobTypes||[]).forEach(([t,n]) => tiers[t]=n);
$('#cards').innerHTML = [
  ['怪物总数', D.stat.mobs],
  ['等级范围', D.stat.lvMin+' – '+D.stat.lvMax],
  ['世界BOSS', tiers['4']||0],
  ['头目/精英', (tiers['3']||0)+' / '+(tiers['2']||0)],
  ['技能条目', D.stat.skills],
  ['玩家技能', D.stat.playerSkills+' 个'],
].map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

$('#note').innerHTML =
 '<b>先看这条：</b>Monster.xml 里<b>没有攻击力、防御、技能字段</b>，只有 id / 名称 / 等级 / 血量 / 怪物类型 / 台词 / 动作资源。'+
 '所以怪物的“属性”只有等级和血量是真的，<b>打你多少伤害是服务端自己算的</b>（当前按玩家最大生命的百分比建模）；'+
 '怪物会不会放技能由服务端的战斗逻辑决定，配置表里查不到对应关系。';

/* ---- 怪物表 ---- */
const TIER = D.TIER;
let mSort = {k:'type', asc:true};
function renderMobs(){
  const q = $('#mq').value.trim().toLowerCase();
  const t = $('#mt').value, lv = $('#ml').value;
  let rows = D.mobs.filter(m =>
    (!t || m.type===t) && (!lv || String(m.lv)===lv) &&
    (!q || m.name.toLowerCase().includes(q) || String(m.id).includes(q) || (m.talk||'').toLowerCase().includes(q)));
  rows.sort((a,b)=>{
    const x=a[mSort.k], y=b[mSort.k];
    if (typeof x==='number') return mSort.asc? x-y : y-x;
    return mSort.asc? String(x).localeCompare(String(y)) : String(y).localeCompare(String(x));
  });
  $('#mcount').textContent = rows.length + ' 条';
  $('#mbody').innerHTML = rows.map(m=>`<tr>
    <td class="mono mut">${m.id}</td><td>${esc(m.name)}</td>
    <td class="mono">${m.lv}</td><td class="mono">${m.hp}</td>
    <td><span class="pill p${m.type}">${esc(m.tier)}</span></td>
    <td class="mut">${esc(m.talk)||'—'}</td></tr>`).join('')
    || '<tr><td colspan="6" class="empty">没有匹配</td></tr>';
}
const mt=$('#mt');
[...new Set(D.mobs.map(m=>m.type))].sort().forEach(t=>{
  mt.insertAdjacentHTML('beforeend', `<option value="${t}">${TIER[t]||('类型'+t)}</option>`);
});
const ml=$('#ml');
[...new Set(D.mobs.map(m=>m.lv))].sort((a,b)=>a-b).forEach(l=>{
  ml.insertAdjacentHTML('beforeend', `<option value="${l}">Lv ${l}</option>`);
});
$('#mq').oninput=renderMobs; mt.onchange=renderMobs; ml.onchange=renderMobs;
document.querySelectorAll('#p-mobs th[data-k]').forEach(th=>{
  th.onclick=()=>{ const k=th.dataset.k; mSort.asc = (mSort.k===k)? !mSort.asc : true; mSort.k=k; renderMobs(); };
});
renderMobs();

/* ---- 玩家技能 ---- */
let curCls = Object.keys(D.player)[0];
function renderTabs(){
  $('#pcls').innerHTML = Object.entries(D.player).map(([c,v])=>
    `<div class="chip ${c===curCls?'on':''}" data-c="${c}">${v.name}${c==='6'?'（新手）':''}</div>`).join('');
  document.querySelectorAll('#pcls .chip').forEach(el=>el.onclick=()=>{curCls=el.dataset.c;renderTabs();renderPlayer();});
}
function renderPlayer(){
  const c = D.player[curCls];
  $('#pbody').innerHTML = c.list.map(sk=>{
    const act = sk.st==='1';
    const rows = sk.rows.map(r=>`<tr>
      <td class="mono mut">${r.id}</td><td class="mono">${r.lv}</td>
      <td class="mono">${r.pct? (r.pct*100).toFixed(0)+'%' : '—'}</td>
      <td class="mono">${r.mp||'—'}</td><td class="mono">${r.cd||'—'}</td></tr>`).join('');
    return `<div class="skillcard">
      <h4>${esc(sk.name)}
        <span class="pill ${act?'p2':'p1'}">${D.STYPE[sk.st]||sk.st}</span>
        <span class="pill p3">${D.TT[sk.tt]||sk.tt}</span></h4>
      <div class="meta">共 ${sk.rows.length} 级 · ${sk.desc?'“'+esc(sk.desc)+'”':''}</div>
      <div class="lvtable"><table><thead><tr><th>技能ID</th><th>等级</th><th>伤害倍率</th><th>耗蓝</th><th>冷却</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div>`;
  }).join('');
}
renderTabs(); renderPlayer();

/* ---- 怪物技能 ---- */
const stt=$('#stt');
[...new Set(D.monSkills.map(s=>s.tt))].sort().forEach(t=>
  stt.insertAdjacentHTML('beforeend',`<option value="${t}">${D.TT[t]||t}</option>`));
function renderMon(){
  const q=$('#sq').value.trim().toLowerCase(), t=stt.value;
  const rows=D.monSkills.filter(s=>(!t||s.tt===t)&&(!q||s.name.toLowerCase().includes(q)||String(s.id).includes(q)));
  $('#scount').textContent=rows.length+' 条';
  $('#sbody').innerHTML=rows.map(s=>`<tr>
    <td class="mono mut">${s.id}</td><td>${esc(s.name)}</td>
    <td class="mono">${s.lv}</td><td class="mono">${s.pct?(s.pct*100).toFixed(0)+'%':'—'}</td>
    <td><span class="pill p3">${D.TT[s.tt]||s.tt}</span></td>
    <td class="mut">${D.STYPE[s.st]||s.st}</td></tr>`).join('')
    || '<tr><td colspan="6" class="empty">没有匹配</td></tr>';
}
$('#sq').oninput=renderMon; stt.onchange=renderMon; renderMon();

/* ---- 其它 ---- */
function renderOther(){
  const q=$('#oq').value.trim().toLowerCase(), c=$('#ocls').value;
  const rows=D.otherSkills.filter(s=>(!c||s.cls===c)&&(!q||s.name.toLowerCase().includes(q)||String(s.id).includes(q)));
  $('#ocount').textContent=rows.length+' 条';
  $('#obody').innerHTML=rows.map(s=>`<tr>
    <td class="mono mut">${s.id}</td><td>${esc(s.name)}</td>
    <td class="mut">${s.cls==='7'?'心法/抵抗':'祝福/重生'}</td>
    <td class="mono">${s.lv}</td><td class="mono">${s.mp||'—'}</td>
    <td class="mut">${D.TT[s.tt]||s.tt}</td><td class="mut">${D.STYPE[s.st]||s.st}</td></tr>`).join('')
    || '<tr><td colspan="7" class="empty">没有匹配</td></tr>';
}
$('#oq').oninput=renderOther; $('#ocls').onchange=renderOther; renderOther();

/* ---- 切页 ---- */
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
  t.classList.add('on'); $('#p-'+t.dataset.p).classList.add('on');
});
</script></body></html>"""

html = HTML.replace("__DATA__", payload)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)
print("写出", OUT, "%.1f KB" % (len(html.encode('utf-8')) / 1024))
print("怪物", stat["mobs"], "技能", stat["skills"], "玩家技能名", stat["playerSkills"])
