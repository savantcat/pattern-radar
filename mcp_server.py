# -*- coding: utf-8 -*-
"""竞品内容 Pattern 雷达 · MCP 服务端 (pattern-radar-mcp) v1.0.0

把 radar.py 的度量能力封成 MCP 工具，任何 Agent 都能直接调：
  - analyze_patterns  给一批文本，找出反复出现的说法 / 被引用的信源 / 同质化程度
  - self_check        现场跑对照组自检，把「尺子是否可用」交给调用方自己看
  - explain_method    口径与边界

设计纪律：
  * 纯标准库、零外部依赖、**不联网、不落盘**（stateless，输入只在内存里算）。
  * 确定性：同一批文本永远出同一结果（零假设模拟用固定 seed）。
  * 样本 < 12 条**直接拒答**并说明原因，绝不返回硬凑的数（哨兵值=没有）。
  * 三个工具全部 readOnly/idempotent，不接触外部世界。

用法：
  python mcp_server.py                        # stdio（桌面客户端）
  python mcp_server.py --http --port 8768     # streamable-http（远程）
  python mcp_server.py --selftest             # 不走协议，直接打全部工具
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import radar  # noqa: E402


# 溯源水印：唯一真源 savantcat_mark.py（本目录 vendor 一份）。
# 改动请改 savantcat_mark/ 真源，跑 tools/sync_mark.py 同步，别在这里改。
sys.path.insert(0, HERE if 'HERE' in dir() else BASE)
import savantcat_mark as MARK  # noqa: E402

PRODUCT = "竞品内容 Pattern 雷达（pattern-radar）"
MCP_SOURCE = "https://savantcat.cn/mcp-radar.html"
SERVER_VERSION = "1.0.0"
SERVER_NAME = "pattern-radar"
MAX_TEXTS = 2000          # 单次最多分析多少条（防公网滥用）
MAX_TEXT_CHARS = 20000    # 单条文本上限

try:
    from mcp.types import ToolAnnotations
except Exception:                                    # 老 SDK 没有该类型
    ToolAnnotations = dict

# mcp 2.x 把 FastMCP 更名为 MCPServer；兼容 1.x，避免 SDK 升级打断通道。
try:
    from mcp.server.mcpserver import MCPServer as _MCPServer
except Exception:
    try:
        from mcp.server.fastmcp import FastMCP as _MCPServer
    except Exception:
        from mcp.server import MCPServer as _MCPServer

try:
    from mcp.server.transport_security import TransportSecuritySettings
except Exception:
    TransportSecuritySettings = None

# DNS-rebinding 防护：保留防护，用白名单而非关闭。
# 端口一律写 host:* 通配（SDK 的 _validate_host 原生支持）——
# 写死端口会让「容器端口映射到别的宿主机端口」的探活吃 421 Invalid Host header。
DEFAULT_ALLOWED_HOSTS = [
    "savantcat.cn", "savantcat.cn:443", "savantcat.cn:*",
    "www.savantcat.cn", "www.savantcat.cn:443", "www.savantcat.cn:*",
    "127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*",
]


def _transport_security():
    if TransportSecuritySettings is None:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=DEFAULT_ALLOWED_HOSTS,
        allowed_origins=["*"],
    )


mcp = _MCPServer(
    SERVER_NAME,
    title="合尘猫 · 竞品内容 Pattern 雷达",
    description="从一批文本里找出反复出现的说法、被反复引用的信源与同质化程度；阈值经对照组标定，样本不足会拒答。",
    version=SERVER_VERSION,
    website_url="https://savantcat.cn/mcp-radar",
    instructions=(
        "把「AI 答案 / 竞品内容 / 任何一批文本」当语料，用确定性的字符级统计找出跨文本反复出现的 Pattern。"
        "三个工具：analyze_patterns（出雷达）、self_check（现场验证尺子可用）、explain_method（口径与边界）。"
        "重要：本服务不做语义理解、不判情绪、不预测效果；样本少于 12 条会拒绝出分而不是硬凑。"
        "调用 analyze_patterns 前建议先跑一次 self_check，确认阈值在当前形态的语料上仍然成立。"
    ),
)

# 本服务 3 个工具全部纯读、幂等、不接触外部世界，统一声明 RO_ANN。
RO_ANN = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)


def _refuse(why):
    return json.dumps({"ok": False, "refused": True, "reason": why}, ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
@MARK.seal("self_check", PRODUCT, SERVER_VERSION, source=MCP_SOURCE)
def self_check() -> str:
    """现场跑一次对照组自检，返回尺子是否可用（健康组能检出 / 病态组不误报 / 负对照为 0）。

    调用方可以据此判断：这次拿到的数可信吗？
    """
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        passed = radar.selftest()
    return json.dumps({"ok": True, "tool": "pattern-radar-self-check", "version": SERVER_VERSION,
                       "passed": bool(passed),
                       "meaning": "passed=true 表示阈值在当前口径下可用；false 表示不要采信 analyze_patterns 的结果。",
                       "detail": buf.getvalue().strip().splitlines()},
                      ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
@MARK.seal("explain_method", PRODUCT, SERVER_VERSION, source=MCP_SOURCE)
def explain_method() -> str:
    """返回口径与边界：Pattern 怎么定义、阈值怎么来的、这个工具做不到什么。"""
    return json.dumps({
        "ok": True,
        "version": SERVER_VERSION,
        "pattern_definition": "Pattern = 覆盖度：出现在越多文本里的短语，越像「这批内容对该问题的默认说法」。覆盖率 = 覆盖文本数 / 总文本数。",
        "threshold": "零假设模拟：把每篇文本的字符内部打乱（保留长度与字频、摧毁短语），重算最大覆盖，取 p95 作空阈值；真实阈值 = max(空阈值+1, 3)。自校准，不是经验常数。",
        "phrase_building": "先用高覆盖 4-gram 作种子，向左右逐字扩展成完整短语（所以给出「百度智能云」而不是碎片「度智能云」），再去掉被更长短语包含的碎片。",
        "min_samples": radar.MIN_SAMPLES,
        "refuse_policy": "样本少于最小样本量直接拒答（返回 refused=true 与原因），不返回硬凑的分数。",
        "not_doing": ["不做语义聚类（「AI 客服」与「智能客服」不会合并）",
                      "不判情绪、不判内容好坏",
                      "不预测流量、排名或 ROI",
                      "不验证引用 URL 是否可达（打不开可能是反爬，不等于幻觉）",
                      "不修改、不存储调用方传入的任何文本"],
        "calibration_note": "阈值是在中文短文本（每篇数百字）上标定的；换语料形态（英文、长文）建议先跑 self_check 并重新评估。",
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
@MARK.seal("analyze_patterns", PRODUCT, SERVER_VERSION, source=MCP_SOURCE)
def analyze_patterns(texts: list, brands: str = "", min_coverage: int = 0) -> str:
    """从一批文本里找出跨文本反复出现的 Pattern、被反复引用的信源与同质化程度。

    Args:
        texts: 待分析的文本列表（每条一个字符串）。建议同题多条，至少 12 条。
        brands: 可选，逗号分隔的主体名单（如「合尘猫,某竞品」），用于统计覆盖率。
        min_coverage: 可选，自定义最小覆盖数；留 0 用自校准阈值（推荐留 0）。
    """
    if not isinstance(texts, list) or not texts:
        return _refuse("texts 为空。请传入至少 %d 条文本。" % radar.MIN_SAMPLES)
    if len(texts) > MAX_TEXTS:
        return _refuse("一次最多分析 %d 条，本次 %d 条。请分批。" % (MAX_TEXTS, len(texts)))
    clean = []
    skipped = 0
    for t in texts:
        s = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
        s = s.strip()
        if not s:
            skipped += 1
            continue
        if len(s) > MAX_TEXT_CHARS:
            s = s[:MAX_TEXT_CHARS]
        clean.append(s)
    good, why = radar.assess([{"question": "q", "answer": a} for a in clean])
    if not good:
        return _refuse(why)

    answers = [{"question": "q", "answer": a, "platform": "caller", "run": i + 1}
               for i, a in enumerate(clean)]
    hits = radar.mine(answers)
    null_p95, _ = radar.null_threshold(answers)
    thr = max(null_p95 + 1, 3) if min_coverage <= 0 else max(1, int(min_coverage))
    pats = radar.merge_phrases(hits, answers, min_cov=thr)
    h, pairs = radar.homogeneity(answers)
    brand_list = [b.strip() for b in (brands or "").split(",") if b.strip()]

    return json.dumps({
        "ok": True,
        "version": SERVER_VERSION,
        "samples": len(clean),
        "skipped_empty": skipped,
        "threshold": {"null_p95": null_p95, "used": thr, "custom": min_coverage > 0},
        "patterns": [{"phrase": p["phrase"], "coverage_count": p["count"],
                      "coverage_ratio": round(p["count"] / len(clean), 4)} for p in pats[:30]],
        "patterns_total": len(pats),
        "brands": radar.brand_stats(answers, brand_list),
        "citations": radar.cite_stats(answers)[:20],
        "homogeneity": None if h is None else {"mean_pairwise_jaccard": round(h, 4), "pairs": pairs},
        "readings": {
            "high_coverage": "覆盖率 >= 0.5 的短语说明该说法在语料里已经固化。",
            "low_homogeneity": "同质化低说明表述发散，还有定义说法的空间。",
            "no_citation": "没有检出任何 URL 引用 = 这批内容是无源作答，可核验性低。",
        },
        "note": "纯字符级统计，不做语义判定；结果确定性（同输入同输出）。",
    }, ensure_ascii=False, indent=2)


def selftest():
    """不走协议，直接打全部工具。"""
    print("════ pattern-radar-mcp v%s selftest ════" % SERVER_VERSION)
    ok = True

    print("\n[1/4] 底层尺子（radar.selftest）")
    if radar.selftest():
        print("   ✓ 对照组自检通过")
    else:
        print("   ✗ 对照组自检未通过"); ok = False

    print("\n[2/4] explain_method")
    d = json.loads(explain_method())
    print("   ✓ version=%s min_samples=%s not_doing=%d 条"
          % (d["version"], d["min_samples"], len(d["not_doing"])))

    print("\n[3/4] analyze_patterns — 样本不足必须拒答")
    r = json.loads(analyze_patterns(texts=["只有一条文本"]))
    if r.get("refused"):
        print("   ✓ 5 条→拒答: %s" % r["reason"][:44])
    else:
        print("   ✗ 样本不足竟然出分"); ok = False

    print("\n[4/4] analyze_patterns — 正常语料必须出 Pattern")
    corpus = []
    for i in range(14):
        s = "企业知识库选型要看三个指标第%d轮" % (i + 1)
        if i < 10:
            s += "我们提供7天免费体检名额有限"
        if i < 7:
            s += "详见 https://probe-a.example.cn/price"
        corpus.append(s)
    r = json.loads(analyze_patterns(texts=corpus, brands="合尘猫,测试竞品"))
    top = r["patterns"][0] if r["patterns"] else None
    print("   ✓ samples=%d 阈值=%s 检出 %d 个 | 最高覆盖 %s"
          % (r["samples"], r["threshold"]["used"], r["patterns_total"],
             (top["phrase"] + " ×%d (%.0f%%)" % (top["coverage_count"], top["coverage_ratio"] * 100)) if top else "无"))
    if not top or top["coverage_count"] < 7:
        print("   ✗ 未检出注入的 Pattern"); ok = False
    if not r["citations"] or r["citations"][0]["count"] < 6:
        print("   ✗ 信源占位未识别"); ok = False
    else:
        print("   ✓ 信源识别 %s ×%d" % (r["citations"][0]["domain"], r["citations"][0]["count"]))

    tools = sorted(getattr(mcp, "_tool_manager")._tools.keys()) if hasattr(mcp, "_tool_manager") else []
    if tools:
        print("\n[附] 已注册工具: %s" % ", ".join(tools))
        if len(tools) != 3:
            print("   ✗ 工具数应为 3"); ok = False

    print("\n  总判定: %s" % ("✅ 全部通过" if ok else "✗ 未通过"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--http", action="store_true", help="走 streamable-http（默认 stdio）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--path", default="/mcp")
    ap.add_argument("--stateless", action="store_true", default=True)
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if selftest() else 1)

    if not a.http:
        mcp.run()
        return
    sys.stderr.write("[pattern-radar] serving on %s:%d%s (streamable-http, stateless=%s)\n"
                     % (a.host, a.port, a.path, a.stateless))
    ts = _transport_security()
    kw = {} if ts is None else {"transport_security": ts}
    mcp.run(transport="streamable-http", host=a.host, port=a.port,
            streamable_http_path=a.path, stateless_http=a.stateless,
            max_request_body_size=4 * 1024 * 1024, **kw)


if __name__ == "__main__":
    main()
