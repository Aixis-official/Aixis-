"""Japanese text analysis utilities for scoring."""

import re
import unicodedata


def contains_mojibake(text: str) -> bool:
    """Detect common mojibake (garbled text) patterns."""
    # Replacement character
    if "\ufffd" in text:
        return True
    # Common mojibake patterns (Shift-JIS misinterpreted as UTF-8, etc.)
    mojibake_patterns = [
        r"[\xc0-\xff][\x80-\xbf]",  # Broken UTF-8 sequences rendered as latin
        r"&#\d{4,5};",  # Unresolved HTML entities
        r"\\u[0-9a-fA-F]{4}",  # Unresolved unicode escapes in output
    ]
    for pattern in mojibake_patterns:
        if re.search(pattern, text):
            return True
    return False


def count_keigo_markers(text: str) -> dict[str, int]:
    """Count formal/informal speech markers in text."""
    markers = {
        "desu_masu": len(re.findall(r"(?:です|ます|ました|ません|でした|でしょう)", text)),
        "casual": len(re.findall(r"(?:だよ|だね|だろ|じゃん|っす|だぜ|だな)", text)),
        "honorific_prefix": len(re.findall(r"(?:お[^\s]{1,4}|ご[^\s]{1,4})", text)),
        "humble": len(re.findall(r"(?:いたします|申します|参ります|存じます|いたしました)", text)),
        "respectful": len(re.findall(r"(?:いらっしゃ|おっしゃ|なさ|くださ|ご覧)", text)),
    }
    return markers


def has_keigo_consistency(text: str) -> tuple[bool, str]:
    """Check if the text maintains consistent speech register."""
    markers = count_keigo_markers(text)
    formal_count = markers["desu_masu"] + markers["humble"] + markers["respectful"]
    casual_count = markers["casual"]

    if formal_count == 0 and casual_count == 0:
        return True, "speech_register_neutral"

    if formal_count > 0 and casual_count > 0:
        ratio = casual_count / (formal_count + casual_count)
        if ratio > 0.3:
            return False, f"mixed_register (formal:{formal_count}, casual:{casual_count})"

    return True, "consistent"


def count_japanese_chars(text: str) -> dict[str, int]:
    """Count different types of Japanese characters."""
    counts = {"hiragana": 0, "katakana": 0, "kanji": 0, "ascii": 0, "other": 0}
    for ch in text:
        name = unicodedata.name(ch, "")
        if "HIRAGANA" in name:
            counts["hiragana"] += 1
        elif "KATAKANA" in name:
            counts["katakana"] += 1
        elif "CJK UNIFIED" in name or "CJK COMPATIBILITY" in name:
            counts["kanji"] += 1
        elif ch.isascii():
            counts["ascii"] += 1
        else:
            counts["other"] += 1
    return counts


def is_meaningful_japanese(text: str, min_jp_ratio: float = 0.1) -> bool:
    """Check if text contains meaningful Japanese content."""
    if not text or len(text.strip()) == 0:
        return False
    counts = count_japanese_chars(text)
    jp_chars = counts["hiragana"] + counts["katakana"] + counts["kanji"]
    total = sum(counts.values())
    if total == 0:
        return False
    return (jp_chars / total) >= min_jp_ratio


BUSINESS_TERMS = [
    "売上", "利益", "コスト", "予算", "決算", "株主", "取締役",
    "事業計画", "中期経営計画", "四半期", "年度", "前年同月比",
    "損益計算書", "貸借対照表", "キャッシュフロー",
    "KPI", "ROI", "PDCA", "ステークホルダー",
    "稟議", "決裁", "承認", "報告書", "議事録",
    "お見積り", "ご請求", "納品", "検収", "契約",
    "弊社", "御社", "貴社", "担当者", "部長", "課長",
]


def count_business_terms(text: str) -> int:
    """Count recognized Japanese business terms in text."""
    count = 0
    for term in BUSINESS_TERMS:
        count += text.count(term)
    return count


def detect_contradiction_acknowledgment(text: str) -> bool:
    """Check if the response acknowledges a contradiction in the input."""
    indicators = [
        "矛盾", "相反", "整合性", "一致しない", "一貫性",
        "両立しない", "相容れない", "食い違", "齟齬",
        "ただし", "しかし一方で", "ご確認ください",
        "どちらを優先", "明確にしていただ",
    ]
    return any(ind in text for ind in indicators)


def count_addressed_steps(text: str, expected_steps: int) -> int:
    """Estimate how many steps of a multi-step instruction were addressed."""
    # Look for numbered items, bullet points, or section breaks
    numbered = re.findall(r"(?:^|\n)\s*(?:\d+[\.\)）]|[①-⑳]|・|[-＊])\s*\S", text)
    paragraph_breaks = text.count("\n\n")
    return max(len(numbered), min(paragraph_breaks + 1, expected_steps))
