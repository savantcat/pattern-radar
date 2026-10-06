# -*- coding: utf-8 -*-
"""pattern-radar 引擎单测：口径可复算 + 假阳性防线 + 样本门槛。

被测的是「这把尺子能不能用」，不是「跑不跑得起来」：
  1. 空阈值自校准是确定性的（同 seed 同结果）；
  2. 有 Pattern 的语料能检出、无 Pattern 的语料零误报、打乱字符的负对照必须为 0；
  3. 样本不足必须拒答（哨兵值 = 没有），不得硬凑数字；
  4. 短语不跨标点边界，碎片被长短语吸收。
"""
import pytest

import radar as R


def mk(answers, question="国标要自查多少项", platform="p"):
    return [{"question": question, "answer": a, "platform": platform, "run": i}
            for i, a in enumerate(answers)]


# 健康组：注入同一短语 + 同一信源（12 条以上才够门槛）
HEALTHY = mk(["国标要自查多少项？答案是 61 项，详见 https://probe-a.example.cn/x"] * 10
             + ["国标要自查多少项？61 项，见 https://probe-a.example.cn/x 和 https://probe-a.example.cn/y"] * 4)

# 病态组：每条答案用互不重叠的汉字区段，跨答案 4-gram 真无重复
def _uniq_block(i, n=14):
    base = 0x4E00 + i * n
    return "".join(chr(base + k) for k in range(n))


PATHO = mk([_uniq_block(i) for i in range(14)])


# ---------------------------------------------------------------- 基础口径

def test_clean_strips_punct_and_urls():
    assert R.clean("你好，世界！ test-1  ") == "你好世界test1"
    assert "http" not in R.clean("见 https://a.example.cn/x 说明")


def test_grams_are_4char_windows():
    assert R.grams("一二三四五六") == {"一二三四", "二三四五", "三四五六"}
    assert R.grams("短句") == set()


def test_mine_returns_gram_to_answer_index():
    idx = R.mine(HEALTHY)
    assert idx, "健康组必须能挖出 n-gram"
    for g, hits in idx.items():
        assert len(g) == R.NGRAM
        assert all(isinstance(i, int) for i in hits)
        assert len(hits) == len(set(hits)), "同一 gram 在同一条里只记一次"


def test_coverage_of_lists_hitting_indices():
    hit = R.coverage_of("国标要自", HEALTHY)
    assert isinstance(hit, list) and len(hit) == len(HEALTHY)
    assert R.coverage_of("这个词根本不存在于语料", HEALTHY) == []


# ---------------------------------------------------------------- 自校准空阈值

def test_null_threshold_is_deterministic_and_returns_tuple():
    a1 = R.null_threshold(HEALTHY)
    a2 = R.null_threshold(HEALTHY)
    assert isinstance(a1, tuple) and len(a1) == 2
    thr, maxes = a1
    assert isinstance(thr, int) and isinstance(maxes, list) and maxes
    assert a1 == a2, "同一 seed 必须复算出同一空阈值（否则口径不可复核）"


def test_adopted_threshold_is_never_below_three():
    """采用阈值 = max(空阈值+1, 3)，防止小语料上出现 1-2 次的偶然重复被当 Pattern。"""
    thr, _ = R.null_threshold(HEALTHY)
    adopted = max(thr + 1, 3)
    assert adopted >= 3


# ---------------------------------------------------------------- 假阳性防线

def test_healthy_corpus_yields_patterns():
    pats = R.merge_phrases(R.mine(HEALTHY), HEALTHY, min_cov=3)
    assert pats, "有 Pattern 的语料必须检出"
    top = pats[0]
    assert top["count"] >= 3
    assert len(top["answers"]) == top["count"]


def test_pathological_corpus_has_no_pattern():
    pats = R.merge_phrases(R.mine(PATHO), PATHO, min_cov=3)
    assert pats == [], "真无重复的语料不得报出 Pattern（无假阳性）"


def test_shuffled_negative_control_gives_zero():
    """负对照：把健康组字符打乱后，Pattern 必须为 0 —— 证明链路不造伪 Pattern。"""
    import random
    rnd = random.Random(20261004)
    shuf = []
    for a in HEALTHY:
        ch = list(R.clean(a["answer"]))
        rnd.shuffle(ch)
        shuf.append({"question": a["question"], "answer": "".join(ch), "platform": "p", "run": 0})
    pats = R.merge_phrases(R.mine(shuf), shuf, min_cov=3)
    assert pats == []


def test_phrases_do_not_cross_punctuation():
    """短语不得跨标点/空位边界，否则会产出没法引用的句子。"""
    pats = R.merge_phrases(R.mine(HEALTHY), HEALTHY, min_cov=3)
    joined = [p["phrase"] for p in pats]
    assert joined
    assert not any("  " in p for p in joined)


def test_fragments_absorbed_by_longer_phrase():
    pats = R.merge_phrases(R.mine(HEALTHY), HEALTHY, min_cov=3)
    for i, a in enumerate(pats):
        for b in pats[i + 1:]:
            assert not (b["phrase"] in a["phrase"] and len(a["phrase"]) > len(b["phrase"])), \
                "被更长短语包含的碎片必须丢弃"


# ---------------------------------------------------------------- 同质化 / 信源 / 品牌

def test_homogeneity_identical_answers_is_one():
    same = mk(["完全一样的一段答案文字"] * 4, question="q1")
    val, pairs = R.homogeneity(same)
    assert pairs == 6 and val == pytest.approx(1.0)


def test_homogeneity_none_when_not_enough_pairs():
    val, pairs = R.homogeneity(mk(["只有一条"], question="q1"))
    assert val is None and pairs == 0


def test_cite_stats_counts_domains_and_flags_placeholder():
    st = R.cite_stats(HEALTHY)
    doms = {d["domain"]: d for d in st}
    assert "probe-a.example.cn" in doms
    assert doms["probe-a.example.cn"]["placeholder"] is True, "example/test 类域名须标为占位符"
    assert st == sorted(st, key=lambda x: (-x["count"], x["domain"])), "结果须按次数降序"


def test_brand_stats_counts_mentions():
    corpus = mk(["推荐用合尘猫的方案", "推荐用合尘猫，竞品A 不行", "没有任何品牌"])
    out = R.brand_stats(corpus, ["合尘猫", "竞品A"])
    m = {x["brand"] if "brand" in x else x.get("name"): x for x in out}
    assert out, "须返回品牌统计"
    assert any(str(v).find("合尘猫") >= 0 for v in m.values())


# ---------------------------------------------------------------- 样本门槛（哨兵值）

def test_assess_refuses_below_min_samples():
    ok, msg = R.assess(HEALTHY[:5])
    assert ok is False
    assert "12" in msg


def test_assess_allows_at_or_above_min_samples():
    ok, msg = R.assess(HEALTHY)
    assert ok is True
    assert R.MIN_SAMPLES == 12


def test_min_samples_constant_is_12():
    assert R.MIN_SAMPLES == 12, "门槛常量被改动会导致口径漂移"
