"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re
from typing import Tuple


# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# 1-G 修复：中文评级映射
_CN_RATING_MAP = {
    "买入": "Buy",
    "增持": "Overweight",
    "持有": "Hold",
    "观望": "Hold",
    "减持": "Underweight",
    "卖出": "Sell",
    "买进": "Buy",
    "强烈建议买入": "Buy",
    "建议买入": "Buy",
    "强烈推荐": "Buy",
    "推荐": "Overweight",
    "中性": "Hold",
    "谨慎": "Underweight",
    "回避": "Sell",
}

# 1-G 修复：否定词检测
_NEGATION_WORDS = {"not", "no", "never", "avoid", "don't", "doesn't", "isn't"}
# 前向否定范围（中文）：不/非/无/莫 开头的短语
_CN_NEGATION_RE = re.compile(r'(不|非|无|莫|别|勿|禁止)\s*[\w\u4e00-\u9fff]*')

# Matches "Rating: X" / "rating - X" / "评级：X" — tolerates markdown
# 1-G 修复：支持全角冒号（：）和中英文标点
_RATING_LABEL_RE = re.compile(r"(?:rating|评级|Recommendation).*?[:\-：\-][\s*]*(\w+)", re.IGNORECASE)

# 中文评级标签匹配
_CN_RATING_RE = re.compile(r"(?:评级|推荐).*?[：:]\s*([\u4e00-\u9fff]+)")


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Three-pass strategy:
    1. Look for an explicit "Rating: X" or "评级：买入" label.
    2. Look for Chinese rating words (买入/增持/持有/减持/卖出).
    3. Fall back to the first 5-tier rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    # Pass 1: Explicit "Rating: X" label (English + full-width colon)
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            # 1-G: 否定词前向检测
            if _has_negation_before(line, m.start()):
                continue
            return m.group(1).capitalize()

    # Pass 1b: Chinese "评级：买入" label
    for line in text.splitlines():
        m = _CN_RATING_RE.search(line)
        if m:
            cn_word = m.group(1).strip()
            for key in sorted(_CN_RATING_MAP, key=len, reverse=True):
                if key in cn_word:
                    if _has_negation_before(line, m.start()):
                        continue
                    return _CN_RATING_MAP[key]

    # Pass 2: Chinese rating words anywhere in text
    for line in text.splitlines():
        for cn_word, en_word in sorted(_CN_RATING_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            if cn_word in line:
                # 1-G: 否定词前向检测
                idx = line.find(cn_word)
                if _has_negation_before(line, idx):
                    continue
                return en_word

    # Pass 3: English rating words (fallback)
    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                # 1-G: 否定词前向检测
                word_idx = line.lower().find(clean)
                if _has_negation_before(line, word_idx):
                    continue
                return clean.capitalize()

    return default


def _has_negation_before(line: str, target_pos: int) -> bool:
    """1-G: 检查目标词之前 3 个词内是否有否定词。
    
    Returns True if negation found within 3 words before target_pos.
    """
    # 检查目标位置之前的 20 个字符是否有英文否定词
    before = line[max(0, target_pos - 20):target_pos].lower()
    for neg in _NEGATION_WORDS:
        if neg in before.split():
            return True
    # 检查中文否定词（不/非/无）
    if _CN_NEGATION_RE.search(line[max(0, target_pos - 10):target_pos]):
        return True
    return False
