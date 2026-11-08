# -*- coding: utf-8 -*-
"""中文轉簡體（對齊官方／上海出品用字）。優先使用 OpenCC。"""
from __future__ import annotations

_CC = None
_CC_FAILED = False

# OpenCC 不可用時的精簡後備字表（常見繁體）
_FALLBACK = str.maketrans(
    {
        "屬": "属", "燼": "烬", "盡": "尽", "來": "来", "見": "见", "歡": "欢",
        "歲": "岁", "過": "过", "時": "时", "尋": "寻", "風": "风", "為": "为",
        "們": "们", "與": "与", "夢": "梦", "畫": "画", "華": "华", "無": "无",
        "開": "开", "關": "关", "東": "东", "車": "车", "馬": "马", "雲": "云",
        "電": "电", "語": "语", "說": "说", "這": "这", "還": "还", "對": "对",
        "從": "从", "後": "后", "裡": "里", "點": "点", "長": "长", "間": "间",
        "樂": "乐", "聲": "声", "愛": "爱", "憶": "忆", "戰": "战", "擊": "击",
        "術": "术", "機": "机", "錄": "录", "體": "体", "賽": "赛", "國": "国",
        "紀": "纪", "塵": "尘", "環": "环", "動": "动", "約": "约", "將": "将",
        "進": "进", "別": "别", "遺": "遗", "鬧": "闹", "則": "则", "臨": "临",
        "騎": "骑", "潔": "洁", "爾": "尔", "遙": "遥",
    }
)


def _get_opencc():
    global _CC, _CC_FAILED
    if _CC is not None or _CC_FAILED:
        return _CC
    try:
        from opencc import OpenCC

        _CC = OpenCC("t2s")
    except Exception:
        _CC_FAILED = True
        _CC = None
    return _CC


def to_simplified(text: str | None) -> str:
    """把字串轉成簡體；空值回傳空字串。"""
    if text is None:
        return ""
    s = str(text)
    if not s:
        return ""
    cc = _get_opencc()
    if cc is not None:
        return cc.convert(s)
    return s.translate(_FALLBACK)
