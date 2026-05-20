#!/usr/bin/env python3
"""词级 AIGC 贡献度分析器 v2

核心问题诊断：
  之前的版本只标注规则匹配的短语（机械连接词、空洞宏大词等），
  但检测系统在 BERT/Ensemble/LR 模式下，AIGC 率主要由以下因素决定：
    - LR: 22维统计特征，规则匹配仅占 ~3.5% 权重
    - BERT: 整体语义序列分类
    - Ensemble: 37维特征

  这意味着修改规则匹配的短语（如"综上所述"→"总之"）几乎不会改变AIGC率！

  真正影响AIGC率的核心因素（按LR权重排序）：
    1. sent_len_cv (-1.752): 句长变异系数 — AI文本句长过于均匀
    2. news_vs_human (+1.487): 新闻语料ngram概率差 — AI用词像新闻
    3. wiki_vs_primary (-1.201): 维基语料ngram概率差 — AI用词像百科
    4. sent_len_equal_mid_frac (+0.816): 中等长度句子占比过高
    5. wiki_vs_human (+0.819): 维基vs人类ngram概率差
    6. uni_tri_ratio (+0.700): 单字/三字困惑度比值
    7. perplexity (-0.669): 整体困惑度
    8. gltr_top10_frac (-0.495): 高概率续接占比
    9. gltr_top100_frac (-0.442): top100续接占比
    10. trans_density (+0.424): 过渡词密度（唯一规则特征）

改进策略：
  1. 标注过渡词（trans_density, 权重0.424）— 唯一可直接修改的规则特征
  2. 标注高可预测性字符段（对应 perplexity, gltr_top10, wiki/news_vs_human）
  3. 标注句长过于均匀的区域（对应 sent_len_cv）
  4. 标注规则匹配短语（detect_patterns，权重低但仍有贡献）
  5. 给出每个标注的"修改后预期降幅"提示

用法:
    from highlight import analyze_text_highlights
    highlights = analyze_text_highlights(text, ai_score=75)
"""

import re
import sys
import os
import math
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from humanize_cn.check_pkg.detect import detect_patterns, split_sentences, count_chinese_chars
from humanize_cn.models.ngram import (
    _extract_chinese, _load_freq, _trigram_log_prob,
    compute_transition_density, analyze_text as ngram_analyze,
    compute_lr_score,
)
from humanize_cn.check_pkg.api import check


# ─── 过渡词列表（与LR模型完全一致）───
# 从 ngram.py 的 _TRANSITION_PHRASES 复制，确保标注与检测完全对齐
_TRANSITION_PHRASES = [
    # Structural / ordering
    '首先', '其次', '再次', '最后', '然后', '接下来', '与此同时',
    # Summary / conclusion
    '综上所述', '总的来说', '总而言之', '归根结底', '由此可见',
    '一方面', '另一方面', '换言之', '简而言之',
    # Spotlight / emphasis
    '值得注意的是', '需要指出的是', '需要强调的是', '不可否认',
    '尤其是', '特别是', '尤为', '显著',
    # Contrast / concession
    '然而', '不过', '相反', '相较而言', '与之相对', '反之', '反观',
    '诚然', '固然', '纵然', '尽管如此',
    # Causation / consequence
    '因此', '所以', '故而', '由此', '进而', '从而', '基于此',
    # Elaboration
    '此外', '另外', '除此之外', '具体而言', '具体来说', '具体地说',
    '举例来说', '举个例子', '更进一步', '进一步',
    '事实上', '实际上', '实质上', '本质上',
    # Hedging
    '或许', '不妨', '也许', '大致', '大概', '某种程度上', '一定程度上',
    '可能', '应当', '应该', '理论上',
    '需要注意', '需要说明', '值得一提', '请注意',
]


def _find_text_positions(text, pattern_text):
    """找到 pattern_text 在 text 中的所有位置。"""
    positions = []
    start = 0
    while True:
        idx = text.find(pattern_text, start)
        if idx == -1:
            break
        positions.append((idx, idx + len(pattern_text)))
        start = idx + 1
    return positions


def _compute_char_logprobs(text):
    """计算每个中文字符的 trigram log-prob。"""
    freq = _load_freq()
    chars = _extract_chinese(text)
    if len(chars) < 5:
        return []
    char_positions = []
    cn_idx = 0
    for i, c in enumerate(text):
        if '\u4e00' <= c <= '\u9fff':
            if cn_idx < len(chars):
                char_positions.append((c, i, cn_idx))
                cn_idx += 1
    result = []
    for i in range(2, len(chars)):
        lp = _trigram_log_prob(chars[i-2], chars[i-1], chars[i], freq)
        char_idx = i
        if char_idx < len(char_positions):
            c, text_pos, _ = char_positions[char_idx]
            result.append({'char': c, 'text_pos': text_pos, 'log_prob': lp, 'char_idx': char_idx})
    return result


def _smooth(series, window=5):
    if not series or window < 2:
        return series[:]
    result = []
    half = window // 2
    for i in range(len(series)):
        start = max(0, i - half)
        end = min(len(series), i + half + 1)
        result.append(sum(series[start:end]) / (end - start))
    return result


def _extract_phrases(text, flagged_positions, min_cn_chars=2):
    """将连续标记位置合并为短语。"""
    if not flagged_positions:
        return []
    sorted_pos = sorted(flagged_positions)
    groups = []
    group_start = sorted_pos[0]
    group_end = sorted_pos[0]
    for pos in sorted_pos[1:]:
        if pos <= group_end + 4:
            group_end = pos
        else:
            groups.append((group_start, group_end))
            group_start = pos
            group_end = pos
    groups.append((group_start, group_end))
    result = []
    for start, end in groups:
        segment = text[start:end + 1]
        cn_count = sum(1 for c in segment if '\u4e00' <= c <= '\u9fff')
        if cn_count >= min_cn_chars:
            result.append({'start': start, 'end': end + 1, 'text': segment})
    return result


def _get_lr_contributions(text):
    """获取LR模型中各特征对AIGC分数的贡献度。"""
    try:
        lr_result = compute_lr_score(text, scene='auto')
        if lr_result:
            return lr_result.get('top_contributions', [])
    except Exception:
        pass
    return []


def _get_detection_method(text):
    """获取检测使用的方法。"""
    try:
        result = check(text)
        return result.get('ai_method', 'unknown'), result.get('ai_score', 0)
    except Exception:
        return 'unknown', 0


def analyze_text_highlights(text, ai_score=None, top_n_phrases=15):
    """分析文本中推高 AIGC 率的词/短语。

    改进版：多信号融合标注
    1. 过渡词标注（trans_density, LR权重0.424）— 唯一规则特征
    2. 高可预测性字符段（perplexity, gltr_top10, wiki/news相关）
    3. 规则匹配短语（detect_patterns, 权重低但仍有贡献）
    4. 句长均匀性标注（sent_len_cv, LR权重-1.752）

    Args:
        text: 待分析文本
        ai_score: 段落AI率(0-100)，如果为None则自动检测
        top_n_phrases: 最多返回多少个高亮短语

    Returns:
        dict: {'highlights': [...], 'stats': {...}, 'method_info': {...}}
    """
    if ai_score is None:
        method, ai_score = _get_detection_method(text)
    else:
        method = 'provided'

    # 获取LR特征贡献度（用于标注优先级）
    lr_contribs = _get_lr_contributions(text)

    flagged_positions = set()
    all_highlights = []

    # ━━━ 信号1：过渡词标注（与LR模型完全一致的词表）━━━
    # 这是LR模型中唯一的规则特征，权重0.424
    # 修改这些词可以直接降低trans_density特征值
    trans_density_info = compute_transition_density(text)
    trans_count = trans_density_info.get('count', 0)

    if trans_count > 0:
        # 按长度排序，优先标注更长的过渡短语（信息量更大）
        found_transitions = []
        for phrase in sorted(_TRANSITION_PHRASES, key=len, reverse=True):
            positions = _find_text_positions(text, phrase)
            if positions:
                for start, end in positions:
                    for pos in range(start, end):
                        flagged_positions.add(pos)
                    found_transitions.append({
                        'start': start, 'end': end,
                        'text': phrase,
                        'reason': 'AI过渡词（影响过渡词密度特征）',
                        'severity': 0.8 if len(phrase) >= 3 else 0.6,
                        'feature': 'trans_density',
                        'weight': 0.424,
                    })
        all_highlights.extend(found_transitions)

    # ━━━ 信号2：规则匹配标注（detect_patterns）━━━
    # 虽然权重低，但仍是检测信号的一部分
    try:
        issues, metrics = detect_patterns(text)
    except Exception:
        issues = {}

    # critical 规则
    for category in ['three_part_structure', 'mechanical_connectors', 'empty_grand_words']:
        for item in issues.get(category, []):
            matched_text = item.get('text', '')
            if matched_text and len(matched_text) >= 2:
                positions = _find_text_positions(text, matched_text)
                if not positions:
                    continue
                for start, end in positions:
                    for pos in range(start, end):
                        flagged_positions.add(pos)
                all_highlights.append({
                    'start': start, 'end': end,
                    'text': matched_text,
                    'reason': f'命中{category}规则',
                    'severity': 0.7,
                    'feature': 'rule_' + category,
                    'weight': 0.1,
                })

    # high 规则
    for category in ['ai_high_freq_words', 'filler_phrases', 'template_sentences', 'balanced_arguments']:
        for item in issues.get(category, []):
            matched_text = item.get('text', '')
            if matched_text and len(matched_text) >= 2:
                positions = _find_text_positions(text, matched_text)
                if not positions:
                    continue
                for start, end in positions:
                    for pos in range(start, end):
                        flagged_positions.add(pos)
                all_highlights.append({
                    'start': start, 'end': end,
                    'text': matched_text,
                    'reason': f'命中{category}规则',
                    'severity': 0.5,
                    'feature': 'rule_' + category,
                    'weight': 0.05,
                })

    # medium 规则
    for category in ['hedging_language', 'list_addiction', 'punctuation_overuse', 'excessive_rhetoric']:
        for item in issues.get(category, []):
            matched_text = item.get('text', '')
            if matched_text and len(matched_text) >= 2:
                positions = _find_text_positions(text, matched_text)
                if not positions:
                    continue
                for start, end in positions:
                    for pos in range(start, end):
                        flagged_positions.add(pos)
                all_highlights.append({
                    'start': start, 'end': end,
                    'text': matched_text,
                    'reason': f'命中{category}规则',
                    'severity': 0.3,
                    'feature': 'rule_' + category,
                    'weight': 0.02,
                })

    # ━━━ 信号3：高可预测性字符段（ngram log-prob）━━━
    # 对应 LR 模型中权重最高的统计特征：
    #   perplexity (-0.669), gltr_top10_frac (-0.495), wiki_vs_human (+1.487)
    # AI文本的字符序列更可预测，标注这些区域可以帮助用户理解哪些表达"太AI了"
    char_lps = _compute_char_logprobs(text)
    if char_lps and len(char_lps) >= 10:
        raw_lps = [item['log_prob'] for item in char_lps]
        mean_lp = sum(raw_lps) / len(raw_lps)
        var_lp = sum((x - mean_lp) ** 2 for x in raw_lps) / len(raw_lps)
        std_lp = var_lp ** 0.5 if var_lp > 0 else 1.0
        smoothed = _smooth(raw_lps, window=7)

        # 根据AI率调整阈值：AI率越高，标注越宽松
        if ai_score >= 75:
            sorted_lps = sorted(raw_lps)
            median_lp = sorted_lps[len(sorted_lps) // 2]
            threshold = median_lp
            flat_tol = 1.0 * std_lp
        elif ai_score >= 50:
            threshold = mean_lp + 0.2 * std_lp
            flat_tol = 0.8 * std_lp
        elif ai_score >= 30:
            threshold = mean_lp + 0.5 * std_lp
            flat_tol = 0.5 * std_lp
        else:
            threshold = mean_lp + 1.0 * std_lp
            flat_tol = 0.3 * std_lp

        ngram_flagged = set()
        for i, item in enumerate(char_lps):
            lp = item['log_prob']
            sm = smoothed[i]
            # 高可预测 + 局部平滑（连续高可预测区域）= AI典型模式
            if lp > threshold and abs(lp - sm) < flat_tol:
                flagged_positions.add(item['text_pos'])
                ngram_flagged.add(item['text_pos'])

        # 将ngram标记合并为短语
        ngram_phrases = _extract_phrases(text, ngram_flagged, min_cn_chars=3)

        # 去重：跳过已被过渡词或规则覆盖的短语
        existing_ranges = set()
        for hl in all_highlights:
            for pos in range(hl['start'], hl['end']):
                existing_ranges.add(pos)

        for phrase in ngram_phrases:
            phrase_positions = set(range(phrase['start'], phrase['end']))
            overlap = phrase_positions & existing_ranges
            if len(overlap) > len(phrase_positions) * 0.5:
                continue  # 大部分已被覆盖

            # 计算该短语的可预测性程度
            positions = [p for p in range(phrase['start'], phrase['end']) if p in ngram_flagged]
            if positions:
                phrase_lps = []
                lp_by_pos = {item['text_pos']: item['log_prob'] for item in char_lps}
                for p in positions:
                    if p in lp_by_pos:
                        phrase_lps.append(lp_by_pos[p])
                if phrase_lps:
                    avg_lp = sum(phrase_lps) / len(phrase_lps)
                    lp_z = (avg_lp - mean_lp) / std_lp if std_lp > 0 else 0
                    severity = min(1.0, max(0.1, lp_z / 2.0 + 0.5))
                else:
                    severity = 0.3
            else:
                severity = 0.3

            all_highlights.append({
                'start': phrase['start'],
                'end': phrase['end'],
                'text': phrase['text'],
                'reason': '字符序列可预测性偏高（AI典型用词模式）',
                'severity': round(severity, 2),
                'feature': 'ngram_predictability',
                'weight': 0.669,  # perplexity权重
            })

    # ━━━ 信号4：句长均匀性标注 ━━━
    # 对应 sent_len_cv (-1.752)，AI文本句长过于均匀
    sentences = split_sentences(text)
    if len(sentences) >= 5:
        sent_lens = [count_chinese_chars(s) for s in sentences]
        avg_len = sum(sent_lens) / len(sent_lens)
        if avg_len > 0:
            cv = (sum((l - avg_len) ** 2 for l in sent_lens) / len(sent_lens)) ** 0.5 / avg_len
            if cv < 0.3:  # 句长过于均匀（人类写作CV通常在0.3-0.6）
                # 找到长度最接近平均值的句子（最"AI"的句子）
                for i, s in enumerate(sentences):
                    s_len = sent_lens[i]
                    if abs(s_len - avg_len) / avg_len < 0.15:  # 长度非常接近平均值
                        # 在原文中找到这个句子的位置
                        s_start = text.find(s)
                        if s_start >= 0:
                            s_end = s_start + len(s)
                            # 检查是否已被标注
                            already_covered = any(
                                hl['start'] <= s_start < hl['end'] or
                                hl['start'] < s_end <= hl['end']
                                for hl in all_highlights
                            )
                            if not already_covered:
                                all_highlights.append({
                                    'start': s_start,
                                    'end': s_end,
                                    'text': s[:50] + ('...' if len(s) > 50 else ''),
                                    'reason': f'句长过于均匀（CV={cv:.2f}，建议调整句式长短）',
                                    'severity': 0.4,
                                    'feature': 'sent_len_cv',
                                    'weight': 1.752,
                                })

    # ━━━ 去重和排序 ━━━
    # 按特征权重 × 严重度排序，优先标注修改后效果最明显的部分
    for hl in all_highlights:
        hl['impact_score'] = hl.get('weight', 0.1) * hl.get('severity', 0.5)

    all_highlights.sort(key=lambda x: x['impact_score'], reverse=True)

    # 限制数量
    all_highlights = all_highlights[:top_n_phrases]

    # 统计信息
    stats = {
        'total_chars': count_chinese_chars(text),
        'flagged_chars': len(flagged_positions),
        'transition_count': trans_count,
        'transition_density': trans_density_info.get('density', 0),
        'rule_hits': sum(1 for hl in all_highlights if hl.get('feature', '').startswith('rule_')),
        'ngram_hits': sum(1 for hl in all_highlights if hl.get('feature') == 'ngram_predictability'),
        'trans_hits': sum(1 for hl in all_highlights if hl.get('feature') == 'trans_density'),
    }

    # 方法信息
    method_info = {
        'method': method,
        'ai_score': ai_score,
        'lr_top_contributions': lr_contribs[:5] if lr_contribs else [],
        'note': '',
    }

    # 根据检测方法给出标注效果说明
    if method in ('bert', 'ensemble', 'xgboost'):
        method_info['note'] = (
            '当前检测主要依赖深度学习模型，标注内容为辅助参考。'
            '模型关注的是整体语义模式，修改个别词语对分数影响有限。'
            '建议从句式多样性、用词个性化、信息密度等方面全面改写。'
        )
    elif method == 'lr':
        method_info['note'] = (
            '当前检测使用LR模型，标注的过渡词和可预测性区域与模型特征直接对应。'
            '但LR模型中统计特征（如困惑度、句长变异系数）占主导（~62%），'
            '仅修改标注词可能不够，还需调整句式结构和用词多样性。'
        )
    else:
        method_info['note'] = (
            '当前检测使用规则+统计模式，标注内容与检测信号直接对应。'
            '修改标注词应能有效降低AIGC率。'
        )

    return {
        'highlights': all_highlights,
        'stats': stats,
        'method_info': method_info,
    }


def build_annotated_segments(text, highlights):
    """将高亮信息与原文合并，生成带标注的片段列表。

    Returns:
        list of {'text', 'is_aigc', 'reason', 'severity'}
    """
    if not highlights:
        return [{'text': text, 'is_aigc': False, 'reason': None, 'severity': 0}]

    sorted_hl = sorted(highlights, key=lambda x: x['start'])
    segments = []
    last_end = 0

    for hl in sorted_hl:
        if hl['start'] > last_end:
            t = text[last_end:hl['start']]
            if t.strip():
                segments.append({'text': t, 'is_aigc': False, 'reason': None, 'severity': 0})
        t = text[hl['start']:hl['end']]
        if t.strip():
            segments.append({'text': t, 'is_aigc': True, 'reason': hl['reason'], 'severity': hl['severity']})
        last_end = hl['end']

    if last_end < len(text):
        t = text[last_end:]
        if t.strip():
            segments.append({'text': t, 'is_aigc': False, 'reason': None, 'severity': 0})

    return segments
