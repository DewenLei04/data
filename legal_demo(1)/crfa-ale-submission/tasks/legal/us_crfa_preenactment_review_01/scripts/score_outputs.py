# -*- coding: utf-8 -*-
"""判分器 v2 —— 双轨。
  脚本轨:python-docx 读 Schedule A/B 两张表,按「值落容差内 ∧ 申报条款匹配 ∧ 源非空」命中。
  模型轨:判官读被判段落 + 判据问题 + 参考结论,返回严格 JSON。
交付面:output/memorandum.docx（单份 Word）。
用法: score_outputs.py [memorandum.docx] [--no-judge] [--votes N]
"""
import json,os,re,sys,concurrent.futures as cf,urllib.request,urllib.error,pathlib,time
from docx import Document

HERE=os.path.dirname(os.path.abspath(__file__))
def load(p):
    env=os.environ.get("ALE_REFERENCE_DIR")
    cands=([os.path.join(env,p)] if env else [])+[p,os.path.join("reference",p),
      os.path.join("task","reference",p),os.path.join(HERE,p),os.path.join(HERE,"..",p),
      os.path.join(HERE,"..","reference",p),os.path.join(HERE,"..","..","reference",p)]
    for c in cands:
        if os.path.exists(c): return json.load(open(c,encoding="utf-8"))
    raise FileNotFoundError(f"{p} not found; tried {cands}")

# ───────── 归一化 ─────────
def norm_prov(s):
    if s is None: return ""
    s=str(s).lower().replace("sec.","").replace("section","").replace("§","")
    for a in "‐‑–—−": s=s.replace(a,"-")   # en-dash/减号排版变体归一为连字符
    return re.sub(r"\s+","",s).strip(" .,;—-")

# ── 引注展开:三种合法写法在实测答卷里都出现过,朴素子串匹配会把后两种判错 ──
#    7-020(1)(c)        单条
#    7-020(1)(a),(c)    合并引注(Opus 5 用了这个写法,S-P1 因此被误杀,连带压掉 G2)
#    7-019(k)(i)-(iii)  区间
_ROMAN=["(i)","(ii)","(iii)","(iv)","(v)","(vi)"]
_ALPHA=[f"({c})" for c in "abcdefghij"]
_ANCHOR=re.compile(r"(\d+-\d+)((?:\([0-9a-z]+\))*)")
_GROUP=re.compile(r"\([0-9a-z]+\)")
def expand_provs(raw):
    """把一格引注展开成完整引注集合。裸括号组挂到上一条引注的同一层级。"""
    s=norm_prov(raw)
    if not s: return set()
    out=set(); stem=None; chain=[]
    i=0
    while i<len(s):
        m=_ANCHOR.match(s,i)
        if m:
            stem=m.group(1); chain=_GROUP.findall(m.group(2) or "")
            out.add(stem+"".join(chain)); i=m.end(); continue
        g=_GROUP.match(s,i)
        if g and stem and chain:
            sep=s[max(0,i-1):i]
            base=stem+"".join(chain[:-1])          # 与上一组同层
            tok=g.group()
            seq=_ROMAN if tok in _ROMAN else (_ALPHA if tok in _ALPHA else None)
            prev=chain[-1]
            if sep=="-" and seq and prev in seq and tok in seq:
                a,b=seq.index(prev),seq.index(tok)
                for t in seq[min(a,b):max(a,b)+1]: out.add(base+t)
            else:
                out.add(base+tok)
            chain=chain[:-1]+[tok]; i=g.end(); continue
        i+=1
    return out
NUM=re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
def norm_val(s):
    if s is None: return None
    if isinstance(s,(int,float)): return float(s)
    t=str(s).strip().replace(",","")
    neg=bool(re.search(r"\(\s*\$?\s*\d",t))
    m=NUM.search(t)
    if not m: return None
    try: v=float(m.group())
    except ValueError: return None
    return -abs(v) if (neg and v>0) else v
def hit_val(got,exp,tol,sign=False):
    if got is None: return False
    if sign: return abs(got-exp)<=tol
    return abs(got-exp)<=tol or abs(abs(got)-abs(exp))<=tol

# ── 单位等价:脚本轨「措辞不计分」,单位形式也是措辞。等价关系显式声明,不做模糊搜索。
#    实测:同一个量,DeepSeek 写 $M、Opus 5 写 ratio、我的期望是 %,三者都对。
def val_candidates(row,sc,eq):
    """按判据声明的 unit 与答卷行声明的 unit,给出该行可比的数值候选 [(值,换算说明)]。"""
    v=row["value"]
    if v is None: return []
    out=[(v,"as written")]
    cu=str(sc.get("unit","")).strip().lower()
    ru=str(row.get("unit","")).strip().lower()
    tgt=eq.get("assessment_target")
    if cu=="%":
        # 份额写成金额:占比 = 金额 / 评估总额 × 100(总额取自 SSOT,不写死)
        if tgt and ("$" in ru or "million" in ru or ru in ("m","$m","usd m","millions","million")):
            out.append((v/tgt*100,f"$M→% (÷{tgt:g}×100)"))
        # 份额写成倍数/比率
        if any(k in ru for k in ("ratio","multiple","x","times")):
            out.append((v*100,"ratio→% (×100)"))
    # 金额写成 $B:换成 $M
    if cu in ("$m","m","usd m","millions","million") and "$b" in ru.replace(" ",""):
        out.append((v*1000.0,"$B→$M (×1000)"))
    if cu=="kt" and ("tco2e" in ru.replace(" ","") or ru in ("t","tonnes","tons","t co2e")):
        out.append((v/1000.0,"t→kt (÷1000)"))
    if cu=="t" and ru in ("kt","kilotonnes"):
        out.append((v*1000.0,"kt→t (×1000)"))
    return out

# ───────── 读 docx ─────────
SIG_A=("item","basis","value","unit")
SIG_B=("provision","exposure","doctrinal","severity")
def _iter_blocks(doc):
    """按文档顺序产出 (段落文本) 与 (表格对象):定位附表要靠前文标题,不能只看表头。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in doc.element.body.iterchildren():
        tag=child.tag.rsplit("}",1)[-1]
        if tag=="p": yield ("p",Paragraph(child,doc).text)
        elif tag=="tbl": yield ("tbl",Table(child,doc))
def read_memo(path):
    """path 可以是文件路径,也可以是 file-like（平台侧从 session 读到的字节流）。
    附表定位:标题锚优先(前文三段内含 schedule a/b 的匹配表),无锚回退首个表头匹配。"""
    doc=Document(path)
    narrative="\n".join(p.text for p in doc.paragraphs if p.text.strip())
    A=B=None; A_fb=B_fb=None; recent=[]
    for kind,obj in _iter_blocks(doc):
        if kind=="p":
            t=obj.strip()
            if t: recent=(recent+[t.lower()])[-3:]
            continue
        t=obj
        hdr=[c.text.strip() for c in t.rows[0].cells]
        j=" | ".join(h.lower() for h in hdr); ctx="\n".join(recent)
        if all(k in j for k in SIG_A):
            if "schedule a" in ctx:
                if A is None: A=(t,hdr)
            elif A_fb is None: A_fb=(t,hdr)
        elif all(k in j for k in SIG_B):
            if "schedule b" in ctx:
                if B is None: B=(t,hdr)
            elif B_fb is None: B_fb=(t,hdr)
    if A is None: A=A_fb
    if B is None: B=B_fb
    multi = {"A_multi": 0, "B_multi": 0}
    # 多于一张同签名表时只取一张,后续表的行会静默丢失——计出来交给 errors 提示
    # (续表/拆表属偏离模板行为,但要让判分侧看得见)
    cntA = cntB = 0
    recent = []
    for kind,obj in _iter_blocks(doc):
        if kind=="p": continue
        hdr=[c.text.strip().lower() for c in obj.rows[0].cells]
        j=" | ".join(hdr)
        if all(k in j for k in SIG_A): cntA += 1
        elif all(k in j for k in SIG_B): cntB += 1
    multi["A_multi"]=cntA; multi["B_multi"]=cntB
    def rows(t,n):
        out=[]
        if t is None: return out
        for r in t[0].rows[1:]:
            cells=[c.text.strip() for c in r.cells][:n]
            if not any(cells): continue
            out.append(cells+[""]*(n-len(cells)))
        return out
    ra=rows(A,7); rb=rows(B,6)
    schedA=[{"item":r[0],"basis":r[1],"value":norm_val(r[2]),"unit":r[3],"prov_raw":r[4],
             "provs":[norm_prov(x) for x in re.split(r"[;,、/]",r[4]) if norm_prov(x)],
             "pset":expand_provs(r[4]),
             "source":r[5],"deriv":r[6]} for r in ra]
    schedB=[{"provs":[norm_prov(x) for x in re.split(r"[;,、/]",r[0]) if norm_prov(x)],
             "pset":expand_provs(r[0]),
             "exposure":r[1],"doctrine":r[2],"severity":r[3],"amend":r[4],"figs":r[5]}
            for r in rb]
    return {"narrative":narrative,"A":schedA,"B":schedB,
            "A_present":A is not None,"B_present":B is not None,
            "A_multi":multi["A_multi"],"B_multi":multi["B_multi"],
            "A_hdr":[h.strip() for h in (A[1] if A else [])],
            "B_hdr":[h.strip() for h in (B[1] if B else [])],
            "A_text":"\n".join(" | ".join(x) for x in ra),
            "B_text":"\n".join(" | ".join(x) for x in rb)}

# ───────── 判官 ─────────
class JudgeGatewayError(Exception):
    """判官通道未配置/不可达:基础设施故障,main.py 必须 re-raise,不得记 0.0。"""

def gateway():
    # 环境变量优先;~/.claude/skills/codex-pic/.env 仅为本机开发回退。
    # 宿主机两者都没有 = 评测侧没配好判官,是 infra 故障,不是答卷 0 分。
    base=os.environ.get("ALE_JUDGE_BASE_URL"); key=os.environ.get("ALE_JUDGE_API_KEY")
    if base and key:
        return re.sub(r"/v1/.*$","",base.strip().rstrip("/")),key.strip()
    p=pathlib.Path(os.path.expanduser("~/.claude/skills/codex-pic/.env"))
    if p.exists():
        env=dict(l.split("=",1) for l in p.read_text().strip().splitlines() if "=" in l)
        root=re.sub(r"/v1/.*$","",env["GPT_IMAGE_2_BASE_URL"].strip().rstrip("/"))
        return root,env["GPT_IMAGE_2_API_KEY"].strip()
    raise JudgeGatewayError(
        "judge gateway not configured: set ALE_JUDGE_BASE_URL/ALE_JUDGE_API_KEY")
JUDGE_SYS=("You are grading one criterion of a legal-work-product rubric. You are given an "
 "extract from the work product, one question, and the reference position held by the setter. "
 "Answer only whether the work product satisfies the question. Do not reward effort, length or "
 "confidence. Reply with strict JSON only: {\"hit\": true|false, \"why\": \"<=40 words\"}.")
def judge_one(root,key,model,temp,crit,ev,timeout=90):
    u=(f"CRITERION\n{crit['statement']}\n\nQUESTION\n{crit['model']['question']}\n\n"
       f"REFERENCE POSITION OF THE SETTER\n{crit['model']['reference']}\n\n"
       f"EXTRACT FROM THE WORK PRODUCT\n{ev[:20000]}")
    # gpt-5 是推理模型:预算须容得下 reasoning_tokens,否则 content 为空(finish_reason=length)
    body=json.dumps({"model":model,"max_tokens":1200,"reasoning_effort":"low",
      "messages":[{"role":"system","content":JUDGE_SYS},{"role":"user","content":u}]}).encode()
    req=urllib.request.Request(root+"/v1/chat/completions",data=body,
      headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=timeout) as r:
                d=json.loads(r.read())
            ch=(d.get("choices") or [{}])[0]
            txt=(ch.get("message") or {}).get("content")
            if not txt:
                raise RuntimeError(f"empty content (finish={ch.get('finish_reason')})")
            m=re.search(r"\{.*\}",txt,re.S)
            o=json.loads(m.group()) if m else {}
            return bool(o.get("hit")),str(o.get("why",""))[:160]
        except Exception as e:
            if attempt==2: return None,f"judge error: {type(e).__name__}"
            time.sleep(2*(attempt+1))
def evidence_for(crit,memo):
    w=crit["model"].get("evidence","all")
    if w=="schedule_a": return memo["A_text"]
    if w=="schedule_b": return memo["B_text"]
    if w=="narrative": return memo["narrative"]
    # 附表优先:M-12/M-BR2 等判据的主证据在两张附表;长答卷先裁叙述,保住附表。
    a=memo["A_text"]; b=memo["B_text"]
    full=memo["narrative"]+"\n\n[SCHEDULE A]\n"+a+"\n\n[SCHEDULE B]\n"+b
    if len(full)<=14000: return full
    budget=14000-len(a)-len(b)-60
    if budget>=0:
        return memo["narrative"][:budget]+"\n\n[SCHEDULE A]\n"+a+"\n\n[SCHEDULE B]\n"+b
    # 附表合计超限:叙述保留首部(建议所在 Part I),A 截断,B 截断——
    # 叙述整体丢弃会让 M-BR1(是否陈述建议)失据并误触 G0
    nb=min(2000,len(memo["narrative"]))
    bcap=min(len(b),8000)
    abudget=max(0,14000-nb-bcap-80)
    return (memo["narrative"][:nb]+"\n\n[SCHEDULE A]\n"+a[:abudget]
            +"\n\n[SCHEDULE B]\n"+b[:bcap])

# ───────── 可导入入口（平台 evaluate() 调它）─────────
def score_submission(docx, rubrics, *, declare=None, judge=True, votes=None):
    """docx: 路径或 bytes/file-like。rubrics: 已解析的 rubrics.json。
    返回与 CLI 相同结构的 dict。judge=False 时模型轨不判（仅供离线自测）。"""
    import io as _io
    if isinstance(docx,(bytes,bytearray)): docx=_io.BytesIO(docx)
    return _score(docx,rubrics,declare=declare,no_judge=not judge,votes=votes)

# ───────── 主流程 ─────────
def main():
    args=[a for a in sys.argv[1:] if not a.startswith("--")]
    flags=[a for a in sys.argv[1:] if a.startswith("--")]
    path=args[0] if args else os.path.join("output","memorandum.docx")
    no_judge=any(f=="--no-judge" for f in flags)
    declare=next((f.split("=",1)[1] for f in flags if f.startswith("--declare=")),None)
    votes=next((int(f.split("=")[1]) for f in flags if f.startswith("--votes=")),None)
    R=load("rubrics.json")
    if not os.path.exists(path):
        print(json.dumps({"final_score":0.0,"errors":[f"missing {path}"]},ensure_ascii=False)); return
    out=_score(path,R,declare=declare,no_judge=no_judge,votes=votes)
    print(json.dumps(out,ensure_ascii=False,indent=2))

def _score(path,R,*,declare=None,no_judge=False,votes=None):
    J=R["meta"]["judge"]
    votes=votes or J.get("votes",1)
    EQ=R["meta"].get("equiv_basis",{})     # 单位换算基数,来自 ssot.py
    res={"script":{},"model":{},"errors":[]}
    memo=read_memo(path)
    if not memo["A_present"]: res["errors"].append("Schedule A 未找到（表头不匹配）")
    if not memo["B_present"]: res["errors"].append("Schedule B 未找到（表头不匹配）")
    if memo["A_multi"]>1: res["errors"].append(f"检测到 {memo['A_multi']} 张 Schedule A 签名表,仅第一张被读取")
    if memo["B_multi"]>1: res["errors"].append(f"检测到 {memo['B_multi']} 张 Schedule B 签名表,仅第一张被读取")
    # ── 先定读法:按最佳拟合。带 by_reading 的判据在哪种读法下命中最多,就认那种 ──
    READINGS=list(R["meta"]["readings"])
    def fits(reading):
        n=0
        for c in R["criteria"]:
            if c["scorer"]!="script": continue
            sc=c["script"]
            if "by_reading" not in sc: continue
            if sc.get("readings") and reading not in sc["readings"]: continue
            exp=sc["by_reading"][reading]
            want_f={norm_prov(p) for p in sc["provisions"]}
            for r in memo["A"]:
                # 读法判定同样要过单位等价,否则「写成金额的那份」会被判成没采任何读法
                if not any(hit_val(v,exp,sc["tol"],sc.get("sign_sensitive"))
                           for v,_ in val_candidates(r,sc,EQ)):
                    continue
                # 条款闸:行的引注与判据条款不匹配的不计入 fit——
                # 否则稀疏答卷里一个 6(months)就能撞开 S-M1/S-P6 的读法判定
                if want_f:
                    got=r["pset"] or set(r["provs"])
                    wpar_f={re.sub(r"\([0-9a-z]+\)$","",p) for p in want_f}
                    gpar_f={re.sub(r"\([0-9a-z]+\)$","",p) for p in got}
                    if not (want_f & got or want_f & gpar_f or wpar_f & got):
                        continue
                n+=c["weight"]; break
        return n
    scores={r:fits(r) for r in READINGS}
    reading=max(scores,key=lambda r:scores[r]) if max(scores.values())>0 else READINGS[0]
    res["reading"]={"adopted":reading,"fit":scores}
    # 脚本轨
    for c in R["criteria"]:
        if c["scorer"]!="script": continue
        sc=c["script"]; ok=False; where=None; unit_via=None
        if sc.get("match")=="citation_precision": continue   # 待所有判据判完再算
        if sc.get("readings") and reading not in sc["readings"]:
            res["script"][c["id"]]={"hit":None,"row":"n/a on the reading adopted",
              "w":c["weight"],"dim":c["dimension"],"na":True}
            continue
        if sc.get("match")=="schedule_columns":     # 列完整性:不增删/改名/重排
            ws=lambda x: re.sub(r"\s+"," ",str(x).strip()).lower()
            ca=[ws(x) for x in sc.get("columns_a",[])]
            cb_=[ws(x) for x in sc.get("columns_b",[])]
            ha=[ws(x) for x in memo["A_hdr"]]; hb=[ws(x) for x in memo["B_hdr"]]
            ok=bool(ha and hb and ha==ca and hb==cb_)
            res["script"][c["id"]]={"hit":ok,
              "row":f"A={len(ha)} cols, B={len(hb)} cols",
              "w":c["weight"],"dim":c["dimension"]}
            continue
        if sc["unit"]=="flag":                      # 结构型判据
            rowsA=memo["A"]
            # 结构完备 = 每行「条款」与「源」两栏都填了（"none applies" 算填了,
            # 因为背景数值本无条款可挂;判据不得要求填一个不存在的条款）
            ok=bool(rowsA) and all(r["prov_raw"].strip() and r["source"].strip() for r in rowsA)
            res["script"][c["id"]]={"hit":ok,"row":f"{len(rowsA)} rows",
              "w":c["weight"],"dim":c["dimension"]}
            continue
        if "by_reading" in sc: sc=dict(sc,expected=sc["by_reading"][reading])
        prec=None
        want={norm_prov(p) for p in sc["provisions"]}
        # 父条款:只放一级(7-020(1) 顶 7-020(1)(c)),祖父级不认
        wpar={re.sub(r"\([0-9a-z]+\)$","",p) for p in want}
        wpar={p for p in wpar if p and p not in want}
        for r in memo["A"]:
            hitv=None
            for val,how in val_candidates(r,sc,EQ):
                if hit_val(val,sc["expected"],sc["tol"],sc.get("sign_sensitive")):
                    hitv=how; break
            if hitv is None: continue
            if sc["provisions"]:
                got=r["pset"] or set(r["provs"])
                # 子级方向:答卷引得更细(7-019(k)(i)-(iii) 顶 7-019(k))。题面指令就是
                # 「引最小款项」,这个方向必须给分且不计入父级分档;
                # 但子款须真实存在(rubrics meta.valid_subdivisions 白名单)——
                # 幻觉引注(7-019(k)(x) 之类)不给分。
                gpar={re.sub(r"\([0-9a-z]+\)$","",p) for p in got}
                gpar={p for p in gpar if p and p not in got}
                if want & got: p2="exact"
                elif want & gpar:
                    vs=R["meta"].get("valid_subdivisions",{})
                    sub_ok=False
                    for g in got:
                        par=re.sub(r"\([0-9a-z]+\)$","",g)
                        if par in want:
                            tail=g[len(par):]
                            allowed=vs.get(par)
                            if allowed is None or tail in allowed:
                                sub_ok=True; break
                    if sub_ok: p2="exact"
                    else: continue
                elif wpar & got: p2="parent"
                else: continue
            else: p2="exact"
            if not r["source"].strip(): continue
            ok=True; where=r["item"][:60]; prec=p2; unit_via=hitv; break
        res["script"][c["id"]]={"hit":ok,"row":where,"expected":sc["expected"],
                                "unit":sc["unit"],"w":c["weight"],"dim":c["dimension"],
                                "precision":prec,"value_via":(unit_via if ok else None)}
    # 引注精准度:数「只靠父条款才命中」的判据条数,按档扣。确定性,不交判官。
    for c in R["criteria"]:
        if c["scorer"]!="script" or (c.get("script") or {}).get("match")!="citation_precision":
            continue
        n=sum(1 for v in res["script"].values() if v.get("precision")=="parent")
        earned=0
        for thr,pts in c["script"]["grades"]:
            if n>=thr: earned=pts
        res["script"][c["id"]]={"hit":earned!=0,"row":f"{n} criteria matched at parent level",
          "parent_count":n,"earned_override":earned,"w":c["weight"],"dim":c["dimension"],
          "precision":None,"value_via":None}
    # 模型轨
    mcs=[c for c in R["criteria"] if c["scorer"]=="model"]
    if declare:
        # 正夹具（定义满分的参考件）绝不外发:judge 会把整份答卷发给上游模型,
        # 而参考件就是满分答案。故其 judge 层用声明值算分。
        dv=json.load(open(declare,encoding="utf-8"))
        for c in mcs:
            v=dv.get(c["id"])
            res["model"][c["id"]]={"hit":None if v is None else bool(v),
              "why":"declared (positive fixture; not sent to the judge)","w":c["weight"],
              "polarity":c["model"]["polarity"]}
    elif no_judge:
        for c in mcs: res["model"][c["id"]]={"hit":None,"why":"--no-judge","w":c["weight"]}
    else:
        root,key=gateway()
        def run(c):
            hits=[]
            for _ in range(votes):
                h,why=judge_one(root,key,J["model"],J.get("temperature",0),c,evidence_for(c,memo),
                                J.get("timeout_s",90))
                hits.append((h,why))
            valid=[h for h,_ in hits if h is not None]
            if not valid: return c["id"],{"hit":None,"why":hits[-1][1],"w":c["weight"]}
            maj=sum(valid)>len(valid)/2 if len(valid)>1 else valid[0]
            return c["id"],{"hit":bool(maj),"why":hits[0][1],"votes":valid,"w":c["weight"],
                            "polarity":c["model"]["polarity"]}
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for cid,v in ex.map(run,mcs): res["model"][cid]=v
    # 计分
    na={k for k,v in res["script"].items() if v.get("na")}
    pos=[c for c in R["criteria"] if c["weight"]>0 and c["id"] not in na]
    denom=sum(c["weight"] for c in pos)
    num=0.0; unjudged=0
    for c in R["criteria"]:
        r=res["script"].get(c["id"]) or res["model"].get(c["id"]) or {}
        h=r.get("hit")
        if h is None:
            # 不适用(na)不是「没判」:它已退出分母,也不得计入 unjudged,
            # 否则 every 读法答卷会撞上 main.py 的 RuntimeError
            if c["weight"]>0 and c["id"] not in na: unjudged+=c["weight"]
            continue
        if c["weight"]>0 and h: num+=c["weight"]
        if c["weight"]<0 and h:
            # 分档负分(如 S-N1 的引注精准度)按实算档位扣,不按满权重扣
            num+=r["earned_override"] if r.get("earned_override") is not None else c["weight"]
    raw=max(0.0,num/denom) if denom else 0.0
    fired=[]
    for g in R["gates"]:
        bad=any((res["script"].get(x) or res["model"].get(x) or {}).get("hit") is not True
                for x in g["criteria"])
        neg=any((res["model"].get(x) or {}).get("hit") is True
                for x in g.get("also_fires_if_negative",[]))
        if bad or neg: fired.append(g)
    mult=min([g["mult"] for g in fired],default=1.0)
    final=raw*mult
    sh=lambda t:(sum(v["w"] for k,v in res[t].items() if v.get("hit") and v["w"]>0),
                 sum(c["weight"] for c in pos if c["scorer"]==t))
    out={"final_score":round(final,4),"raw":round(raw,4),"gate_multiplier":mult,
      "reading_adopted":res["reading"]["adopted"],"reading_fit":res["reading"]["fit"],
      "criteria_not_applicable":sorted(na),
      "gates_fired":[g["id"] for g in fired],
      "gate_detail":[{"id":g["id"],"mult":g["mult"],"why":g["why"]} for g in fired],
      "script":{"earned":sh("script")[0],"total":sh("script")[1],
                "hits":sum(1 for v in res["script"].values() if v.get("hit")),
                "of":len([v for v in res["script"].values() if not v.get("na")])},
      "model":{"earned":sh("model")[0],"total":sh("model")[1],
               "hits":sum(1 for v in res["model"].values()
                          if v.get("hit") is True and v["w"]>0),
               "of":len([c for c in mcs if c['weight']>0]),
               "negatives_fired":[k for k,v in res["model"].items()
                                  if v.get("hit") is True and v["w"]<0],
               "unjudged_weight":unjudged},
      "errors":res["errors"],"detail":res}
    return out

if __name__=="__main__": main()
