# -*- coding: utf-8 -*-
"""竞品内容 Pattern 雷达 (Competitive Content Pattern Radar) v1.0.0

做什么：把「AI 答案 / 竞品内容」当语料，找出**反复出现的 Pattern**——
谁被反复提到、以什么说法被提到、AI 反复引用哪些信源、答案空间是否已经同质化。

不做什么：不做语义理解、不判断内容好坏、不预测 ROI。只做字符级统计 + 显著性判定。

四条铁律（内建）：
  1. 只读：本工具不修改任何输入数据。
  2. 不编数据：拿不到就报错，绝不返回哨兵值或占位答案。
  3. 阈值必须先在对照组上标定：`--selftest` 不通过，就不许拿真实数据出结论。
  4. 占比型度量：Pattern 强度 = 覆盖的回答数 / 总回答数（不用均值，避免被长短摊薄）。

用法：
  python radar.py --selftest                      # 对照组自检（必须先跑，必须全绿）
  python radar.py --input <记录.json>             # 对真实数据出雷达报告（默认自动先跑自检）
  python radar.py --input <记录.json> --no-selftest
  python radar.py --input <记录.json> --brands 合尘猫,某竞品A,某竞品B --out <报告.md>

输入格式（两种都支持）：
  A. geo-monitor 的 记录/<YYYY-MM>.json：{"samples": [{"question","platform","run","answer",...}]}
  B. 纯数组：[{"question","answer","platform"?,"run"?}, ...]
"""
import argparse, io, json, os, random, re, sys
from collections import defaultdict

VERSION = "1.0.0"
NGRAM = 4                 # 中文按 4 字滑窗
MIN_SAMPLES = 12          # 最小样本量：低于此直接拒绝出分（不硬凑数）
NULL_ITERS = 30           # 零假设模拟轮数
SENTINEL_REFUSE = "样本量不足"

PUNCT = re.compile(r"[\s\u3000,，。.;；:：!！?？、\"'“”‘’()（）\[\]【】<>\-—_/\\|+*#~`]+")
# URL 不参与短语挖掘：它会被标点切成 `https` / `example` / `cn` 这类碎片，
# 而碎片不是「说法」，会把噪声顶进 Pattern 榜（信源统计仍读原文，见 cite_stats）。
URL_RE = re.compile(r"https?://\S+")


def clean(t):
    return PUNCT.sub("", URL_RE.sub(" ", t or ""))


def grams(text, n=NGRAM):
    t = clean(text)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))} if len(t) >= n else set()


# ---------------------------------------------------------------- 语料加载
def load_samples(path):
    """返回 [{'question','answer','platform','run'}]；格式不对就抛错，不猜。"""
    if not os.path.exists(path):
        raise IOError("输入文件不存在：%s" % path)
    d = json.loads(io.open(path, encoding="utf-8").read())
    if isinstance(d, dict):
        raw = d.get("samples") or []
    elif isinstance(d, list):
        raw = d
    else:
        raise ValueError("输入格式不认识：既不是 {samples:[...]} 也不是 [...]")
    out = []
    for i, x in enumerate(raw):
        if not isinstance(x, dict):
            continue
        a = x.get("answer") or x.get("content") or x.get("text")
        if not a or not str(a).strip():
            continue                              # 空答案=没测到，丢弃并计数，不当成数据
        out.append({"question": x.get("question") or x.get("q") or "(无题)",
                    "answer": str(a), "platform": x.get("platform") or "未知",
                    "run": x.get("run") or 0})
    return out


# ---------------------------------------------------------------- 核心度量
def coverage_of(gram, answers):
    hit = [i for i, a in enumerate(answers) if gram in clean(a["answer"])]
    return hit


def mine(answers, n=NGRAM):
    """返回 {gram: [命中回答索引]}。"""
    idx = {}
    for i, a in enumerate(answers):
        for g in grams(a["answer"], n):
            idx.setdefault(g, []).append(i)
    return idx


def null_threshold(answers, n=NGRAM, iters=NULL_ITERS, seed=20261004):
    """零假设基线：把每篇答案内部字符打乱（保留长度与字频、摧毁短语），
    重算「最大覆盖数」的分布，取 p95。这是自校准的空阈值，不拍脑袋。"""
    rnd = random.Random(seed)
    maxes = []
    for _ in range(iters):
        shuf = []
        for a in answers:
            ch = list(clean(a["answer"]))
            rnd.shuffle(ch)
            shuf.append({"answer": "".join(ch)})
        best = 0
        for g, hit in mine(shuf, n).items():
            if len(hit) > best:
                best = len(hit)
        maxes.append(best)
    maxes.sort()
    return maxes[int(len(maxes) * 0.95) - 1] if maxes else 0, maxes


def clean_map(text):
    """返回 (去标点文本, 段落边界位置集合)。
    边界位置 k = 去标点串里第 k 个字符原本是被标点/空白隔开的；
    扩展短语时不得跨过 k —— 否则会产出「名额有限详见https…」这种没法引用的句子。
    """
    t = URL_RE.sub(" ", text or "")           # 与 clean() 一致：URL 整段剔除，别切成碎片
    out, bounds = [], set()
    for ch in t:
        if PUNCT.match(ch):
            if out:
                bounds.add(len(out))
        else:
            out.append(ch)
    return "".join(out), bounds


def mine_bounded(texts, bounds, n=NGRAM):
    """只在段内取 n-gram（跨标点的不算），保证 Pattern 是原句里连续的一段。"""
    idx = {}
    for k, t in enumerate(texts):
        b = bounds[k]
        for i in range(max(0, len(t) - n + 1)):
            if any(x in b for x in range(i + 1, i + n)):
                continue
            idx.setdefault(t[i:i + n], []).append(k)
    return idx


def _occurs(t, bnd, g):
    """g 在 t 里是否存在一次「不跨段」的出现。"""
    i = t.find(g)
    while i >= 0:
        if not any(x in bnd for x in range(i + 1, i + len(g))):
            return True
        i = t.find(g, i + 1)
    return False


def _cov(g, texts, bounds=None):
    if bounds is None:
        return sum(1 for t in texts if g in t)
    return sum(1 for k, t in enumerate(texts) if _occurs(t, bounds[k], g))


def extend_phrase(seed, texts, thr, bounds=None, maxlen=24):
    """把种子 n-gram 向左右逐字扩展：只要新短语覆盖数仍 >= thr 就继续长，
    且**不跨标点边界**。这样 `度智能云` 补全成 `百度智能云`，也不会粘出整句。"""
    cur = seed
    while len(cur) < maxlen:                      # 向右扩
        cands = set()
        for k, t in enumerate(texts):
            b = bounds[k] if bounds else set()
            i = t.find(cur)
            while i >= 0:
                j = i + len(cur)
                if j < len(t) and j not in b:
                    cands.add(cur + t[j])
                i = t.find(cur, i + 1)
        ok = [c for c in cands if _cov(c, texts, bounds) >= thr]
        if not ok:
            break
        cur = max(ok, key=lambda c: (_cov(c, texts, bounds), sum(t.count(c) for t in texts), c))
    while len(cur) < maxlen:                      # 向左扩
        cands = set()
        for k, t in enumerate(texts):
            b = bounds[k] if bounds else set()
            i = t.find(cur)
            while i >= 0:
                if i > 0 and i not in b:
                    cands.add(t[i - 1] + cur)
                i = t.find(cur, i + 1)
        ok = [c for c in cands if _cov(c, texts, bounds) >= thr]
        if not ok:
            break
        cur = max(ok, key=lambda c: (_cov(c, texts, bounds), sum(t.count(c) for t in texts), c))
    return cur


def merge_phrases(hits, answers, n=NGRAM, min_cov=3):
    """先取高覆盖 n-gram 作种子（跨标点的不作种子），扩成最长短语，再去掉碎片。"""
    cm = [clean_map(a["answer"]) for a in answers]
    texts = [c for c, _ in cm]
    bounds = [b for _, b in cm]
    hb = mine_bounded(texts, bounds, n)
    seeds = sorted((g for g, h in hb.items() if len(h) >= min_cov),
                   key=lambda g: (-len(hb[g]), g))
    cands = []
    seen = set()
    for g in seeds:
        p = extend_phrase(g, texts, min_cov, bounds)
        if p in seen:
            continue
        seen.add(p)
        idx = [k for k, t in enumerate(texts) if _occurs(t, bounds[k], p)]
        if len(idx) < min_cov:
            continue
        cands.append({"phrase": p, "count": len(idx), "answers": idx})
    cands.sort(key=lambda x: (-x["count"], -len(x["phrase"]), x["phrase"]))
    out = []
    for c in cands:                               # 碎片（被已选更长短语包含）丢弃
        if any(c["phrase"] in s["phrase"] and len(s["phrase"]) > len(c["phrase"]) for s in out):
            continue
        out.append(c)
    return out


def homogeneity(answers):
    """同质化程度：同题内两两 4-gram Jaccard 的均值（占比型：交/并）。"""
    byq = defaultdict(list)
    for a in answers:
        byq[a["question"]].append(grams(a["answer"]))
    vals = []
    for q, sets in byq.items():
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                u = len(sets[i] | sets[j])
                if u:
                    vals.append(len(sets[i] & sets[j]) / u)
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


DOMAIN = re.compile(r"https?://([^/\s\)\]]+)")
PLACEHOLDER = re.compile(r"你的|我的|示例|测试|待填|xxx|example|placeholder|todo|域名|abc\.|test\.", re.I)


def cite_stats(answers):
    cnt, qs = defaultdict(int), defaultdict(set)
    for a in answers:
        for d in {m.lower().lstrip("www.") for m in DOMAIN.findall(a["answer"])}:
            cnt[d] += 1; qs[d].add(a["question"])
    return sorted(({"domain": d, "count": c, "questions": len(qs[d]),
                    "placeholder": bool(PLACEHOLDER.search(d))} for d, c in cnt.items()),
                  key=lambda x: (-x["count"], x["domain"]))


def brand_stats(answers, brands):
    out = []
    for b in brands:
        hit = [i for i, a in enumerate(answers) if b.lower() in a["answer"].lower()]
        first = sum(1 for i in hit if clean(answers[i]["answer"]).lower().find(b.lower()) < 120)
        out.append({"brand": b, "count": len(hit),
                    "coverage": len(hit) / len(answers) if answers else 0.0, "head": first})
    return sorted(out, key=lambda x: (-x["count"], x["brand"]))


# ---------------------------------------------------------------- 出分闸门
def assess(answers):
    """样本量闸门：不够就明确拒绝出分，并说清为什么，不硬凑。"""
    n = len(answers)
    if n == 0:
        return False, "没有可用答案（0 条）。请先采集真实回答，不要用占位内容。"
    if n < MIN_SAMPLES:
        return False, ("可用答案 %d 条 < 最小样本量 %d。Pattern 判定在这么小的样本上只会出噪声，"
                       "本工具拒绝出分。请补采到 %d 条以上（建议同题多轮）。" % (n, MIN_SAMPLES, MIN_SAMPLES))
    return True, "样本量 %d 条 ≥ %d，可出分。" % (n, MIN_SAMPLES)


# ---------------------------------------------------------------- 对照组自检
def selftest():
    """A 健康组（有真 Pattern）/ B 病态组（无重复）。
    断言：① A 能检出 Pattern 且显著高于空阈值 ② B 不得报出 Pattern ③ 负对照（打乱语料）恒为 0 个 Pattern。"""
    print("════ 对照组自检 (v%s) ════" % VERSION)
    ok = True

    def mk(qs, bodies):
        return [{"question": q, "answer": b, "platform": "对照", "run": i + 1}
                for i, (q, b) in enumerate(zip(qs, bodies))]

    # A：12 条答案，10 条含同一句「7天免费体检」，8 条引用同一信源
    qa = ["q1", "q2", "q3", "q4"] * 3
    bodies_a = []
    for i in range(12):
        s = "先用免费体检看结论再决定要不要花钱改造客服流程第%d轮" % (i + 1)
        if i < 10:
            s += "我们提供7天免费体检名额有限"
        if i < 8:
            s += "详见 https://probe-a.example.cn/pricing"
        s += "另外一些无关内容%d号" % (i * 7 + 3)
        bodies_a.append(s)
    A = mk(qa, bodies_a)

    # B：同样 12 条，每条只用「属于它自己」的一组汉字（互不重叠）→ 跨答案不可能出现共同 4-gram
    bodies_b = []
    for i in range(12):
        pool = [chr(0x4E00 + i * 12 + k) for k in range(12)]
        ri = random.Random(1000 + i)
        bodies_b.append("".join(ri.choice(pool) for _ in range(60)))
    B = mk(qa, bodies_b)
    # 先验证对照组本身满足「无重复」前提：不满足 = 语料构造错了，不是工具错了（铁律）
    _bm = mine(B)
    bmax = max((len(h) for h in _bm.values()), default=0)
    if bmax >= 2:
        print("  ✗ 对照组 B 构造有误：称其「无重复」但内部存在跨答案重复(最大覆盖 %d 条)。先修语料，不许改工具。" % bmax)
        ok = False
    else:
        print("  ✓ 对照组 B 前提成立：跨答案最大覆盖 %d 条（真无重复）" % bmax)

    for name, data in (("A 健康组(有Pattern)", A), ("B 病态组(无Pattern)", B)):
        hit = mine(data)
        null_p95, _ = null_threshold(data)
        thr = max(null_p95 + 1, 3)
        found = merge_phrases(hit, data, min_cov=thr)
        top = found[0]["count"] if found else 0
        cov = top / len(data)
        print("  [%s] 空阈值(p95)=%d 采用阈值=%d → 检出 Pattern %d 个，最高覆盖 %d/%d (%.0f%%)"
              % (name, null_p95, thr, len(found), top, len(data), cov * 100))
        if "A" in name:
            if cov < 0.6:
                print("     ✗ 健康组未检出应有 Pattern（期望覆盖 ≥60%%）"); ok = False
            else:
                print("     ✓ 检出注入的 Pattern")
            cs = cite_stats(data)
            if not cs or cs[0]["domain"] != "probe-a.example.cn" or cs[0]["count"] < 6:
                print("     ✗ 信源占位未识别出注入域名"); ok = False
            else:
                print("     ✓ 信源占位识别正确 (%s ×%d)" % (cs[0]["domain"], cs[0]["count"]))
        else:
            if found:
                print("     ✗ 病态组报出了 Pattern（假阳性）：%s" % found[0]["phrase"]); ok = False
            else:
                print("     ✓ 病态组未报 Pattern（无假阳性）")
        if not assess(data)[0]:
            print("     ✗ 样本量闸门误拒"); ok = False

    # 负对照：A 打乱后必须 0 Pattern
    rnd = random.Random(7)
    shuf = []
    for a in A:
        ch = list(clean(a["answer"])); rnd.shuffle(ch)
        shuf.append({"question": a["question"], "answer": "".join(ch)})
    null_p95, _ = null_threshold(shuf)
    shuf_found = merge_phrases(mine(shuf), shuf, min_cov=max(null_p95 + 1, 3))
    print("  [负对照] A 语料字符打乱后 → 检出 Pattern %d 个（必须为 0）" % len(shuf_found))
    if shuf_found:
        print("     ✗ 链路本身在造伪 Pattern"); ok = False
    else:
        print("     ✓ 链路不造伪 Pattern")

    # 样本量闸门必须真的拦
    small = A[:5]
    g, why = assess(small)
    print("  [闸门] 5 条样本 → %s（必须拒）" % ("拒：%s" % why[:34] if not g else "✗ 未拦"))
    if g:
        ok = False

    print()
    print("  总判定: %s" % ("✅ 全部通过 —— 尺子可用" if ok else "✗ 未通过 —— 禁止用于真实数据"))
    return ok


# ---------------------------------------------------------------- 报告
def report(answers, brands, title="竞品内容 Pattern 雷达"):
    good, why = assess(answers)
    L = []
    L.append("# %s" % title)
    L.append("")
    L.append("- 生成工具：pattern-radar v%s（纯标准库 · 只读 · 不做语义判定）" % VERSION)
    L.append("- 样本：**%d 条回答** / %d 道问题 / 平台 %s"
             % (len(answers), len({a["question"] for a in answers}),
                "、".join(sorted({a["platform"] for a in answers}))))
    L.append("- 样本量闸门：%s" % why)
    L.append("")
    if not good:
        L.append("> **拒绝出分。** %s" % why)
        return "\n".join(L), False

    hit = mine(answers)
    null_p95, _ = null_threshold(answers)
    thr = max(null_p95 + 1, 3)
    pats = merge_phrases(hit, answers, min_cov=thr)

    L.append("## 一、AI 答案里的重复 Pattern（阈值已在对组上标定）")
    L.append("")
    L.append("零假设阈值（同语料字符打乱后的最大覆盖）= **%d 条**；采用阈值 = **%d 条**（高于它的重复才叫 Pattern）。" % (null_p95, thr))
    L.append("")
    if not pats:
        L.append("未检出跨答案重复的 Pattern —— 说明当前答案空间的表述是分散的（或样本还不够）。")
    else:
        L.append("| # | 重复 Pattern（短语） | 覆盖回答 | 覆盖率 | 命中的题数 |")
        L.append("|---|---|---|---|---|")
        for i, p in enumerate(pats[:15], 1):
            qs = len({answers[j]["question"] for j in p["answers"]})
            L.append("| %d | `%s` | %d/%d | %.0f%% | %d |"
                     % (i, p["phrase"], p["count"], len(answers), 100.0 * p["count"] / len(answers), qs))
    L.append("")

    L.append("## 二、竞品占位（谁被 AI 提到）")
    L.append("")
    if brands:
        bs = brand_stats(answers, brands)
        L.append("| 主体 | 覆盖回答 | 覆盖率 | 出现在答案靠前位置 |")
        L.append("|---|---|---|---|")
        for b in bs:
            L.append("| %s | %d/%d | %.0f%% | %d |"
                     % (b["brand"], b["count"], len(answers), b["coverage"] * 100, b["head"]))
        L.append("")
        L.append("> 覆盖率 = 提到该主体的回答数 / 总回答数。**覆盖率高 ≠ 评价好**，本工具不做情绪判定。")
    else:
        L.append("未指定监测主体（用 `--brands A,B` 传入）。")
    L.append("")

    L.append("## 三、AI 反复引用的信源（信源池占位）")
    L.append("")
    cs = cite_stats(answers)
    ph = [c for c in cs if c["placeholder"]]
    if not cs:
        L.append("**本轮回答中没有任何 URL 引用** —— 说明这些答案的可核验性低（AI 在无源作答）。")
    else:
        L.append("| 信源域名 | 被引用次数 | 覆盖题数 | 备注 |")
        L.append("|---|---|---|---|")
        for c in cs[:12]:
            L.append("| %s | %d | %d | %s |" % (c["domain"], c["count"], c["questions"],
                     "⚠️ 占位符，非真实信源" if c["placeholder"] else ""))
        L.append("")
        if ph:
            L.append("> ⚠️ 检出 **%d 个占位符域名**（如「你的域名」）——那是答案正文里的模板文字，"
                     "**不能当作 AI 的真实信源**，已从第五节的结论中排除。" % len(ph))
        L.append("> 反复被引用的域名 = AI 在该问题上的实际信源。想进答案，先成为这些域名的同类信源。")
    L.append("")

    h, pairs = homogeneity(answers)
    L.append("## 四、答案空间同质化程度")
    L.append("")
    if h is None:
        L.append("同题内的可比较对数不足，跳过。")
    else:
        L.append("- 同题内两两相似度（4-gram Jaccard 均值）= **%.3f**（基于 %d 对）" % (h, pairs))
        L.append("- 判读：越高说明 AI 对这个问题越「有定见」——**说法固化 = Pattern 已成**；"
                 "越低说明答案发散，你还有机会定义说法。")
    L.append("")

    L.append("## 五、最该先修的一件事")
    L.append("")
    L.append(suggest(pats, cs, answers, brands))
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 附录：口径与边界")
    L.append("")
    L.append("- **Pattern = 覆盖度**：出现在越多的回答里，说明它越像「AI 对这个问题的默认说法」；本工具只做字符级 n-gram，不做语义聚类。")
    L.append("- **阈值已标定**：`--selftest` 在已知答案的对照组上验证过（健康组检出/病态组不误报/负对照为 0）。换语料形态（如英文、长文）需重新标定。")
    L.append("- **不做的**：不判情绪、不判内容好坏、不预测流量或 ROI、不修改任何输入数据。")
    L.append("- **样本不足会拒答**：低于 %d 条直接拒绝出分，不硬凑（硬凑出的数比没有更误导）。" % MIN_SAMPLES)
    return "\n".join(L), True


def suggest(pats, cs, answers, brands):
    """只给一条建议——用户不需要十条。占位符域名不计入。"""
    real = [c for c in cs if not c.get("placeholder")]
    if pats and pats[0]["count"] / len(answers) >= 0.5:
        return ("**「%s」已经覆盖 %.0f%% 的回答，说法基本固化了。** 硬碰这个说法没有胜算，"
                "沿它做「延展 + 证据化」：把它讲清楚并附上可核验来源，抢的是引用位而不是口号位。"
                % (pats[0]["phrase"], 100.0 * pats[0]["count"] / len(answers)))
    if not real:
        return ("**本轮答案里没有任何真实信源被引用**（全是无源作答或占位符）。这是最便宜的切入机会："
                "把该问题写成一篇带可核验数据的定义性页面——AI 缺信源时会优先抓独立的、结构化的那篇。")
    return ("**先盯住 `%s`（被引用 %d 次）** —— 它已被 AI 认作该问题的信源。"
            "下一步是查它覆盖了哪些子问题、还漏了哪些，**漏掉的那个子问题就是你的落点**。"
            % (real[0]["domain"], real[0]["count"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--out")
    ap.add_argument("--brands", default="")
    ap.add_argument("--title", default="竞品内容 Pattern 雷达")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="同时输出结构化 JSON")
    a = ap.parse_args()

    if a.selftest or not a.input:
        good = selftest()
        if a.selftest:
            sys.exit(0 if good else 1)
        if not good:
            print("\n自检未通过，拒绝继续处理真实数据。"); sys.exit(1)
        if not a.input:
            print("\n（未提供 --input，仅完成自检）"); return
    elif not a.no_selftest:
        print("── 出分前先跑对照组自检 ──")
        if not selftest():
            print("\n自检未通过，拒绝出分。"); sys.exit(1)
        print("")

    answers = load_samples(a.input)
    brands = [b.strip() for b in a.brands.split(",") if b.strip()]
    md, good = report(answers, brands, a.title)
    print(md)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        io.open(a.out, "w", encoding="utf-8", newline="").write(md)
        print("\n[已写出] %s（%d 字符）" % (a.out, len(md)))
    if a.json and good:
        hit = mine(answers); p95, _ = null_threshold(answers)
        thr = max(p95 + 1, 3)
        j = {"tool": "pattern-radar", "version": VERSION, "samples": len(answers),
             "null_p95": p95, "threshold": thr,
             "patterns": [{"phrase": p["phrase"], "count": p["count"],
                           "coverage": round(p["count"] / len(answers), 4)} for p in merge_phrases(hit, answers, min_cov=thr)],
             "brands": brand_stats(answers, brands), "citations": cite_stats(answers),
             "homogeneity": homogeneity(answers)[0]}
        p = (a.out or "radar") + ".json" if a.out else os.path.join(os.getcwd(), "radar.json")
        io.open(p, "w", encoding="utf-8", newline="").write(json.dumps(j, ensure_ascii=False, indent=1))
        print("[已写出] %s" % p)


if __name__ == "__main__":
    main()
