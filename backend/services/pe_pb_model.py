"""
PE/PB 相对估值模型

基于当前 PE/PB 与合理倍数对比，估算合理股价区间

PE 合理倍数 = 净利润增长率 (彼得·林奇 PEG=1 原则)
PB 合理倍数 = ROE / 要求回报率
"""

import logging
from typing import Dict, Any

import pandas as pd
import numpy as np

from services.data_fetcher import (
    get_financial_indicators,
    get_realtime_quote,
    safe_float,
)

logger = logging.getLogger(__name__)


def calculate_pe_pb(code: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    PE/PB 相对估值

    参数:
        code: 股票代码
        params:
            - pe_weight: PE法权重 (默认 0.6)
            - pb_weight: PB法权重 (默认 0.4)
            - required_return: 要求回报率 (默认 0.10)
    """
    if params is None:
        params = {}

    pe_weight = float(params.get("pe_weight", 0.6))
    pb_weight = float(params.get("pb_weight", 0.4))
    required_return = float(params.get("required_return", 0.10))

    # 获取数据
    quote = get_realtime_quote(code)
    indicators = get_financial_indicators(code)

    if quote.get("price") is None:
        return {"error": "无法获取当前股价"}

    current_price = float(quote["price"])
    current_pe = quote.get("pe") or 0
    current_pb = quote.get("pb") or 0

    eps = indicators.get("eps", 0)
    bps = indicators.get("bps", 0)
    roe = indicators.get("roe", 0)  # 百分比
    profit_growth = indicators.get("profit_growth", 0)  # 百分比

    # === PE 法估值 ===
    # 合理PE = max(净利润增长率, 5) 基于 PEG=1 原则
    # 限制合理PE在5~30之间
    if profit_growth > 0:
        fair_pe = max(5, min(profit_growth, 30))
    else:
        fair_pe = 15  # 默认合理PE

    pe_valuation = fair_pe * eps if eps > 0 else current_price

    # 判断当前PE相对位置
    if current_pe > 0 and fair_pe > 0:
        pe_position = (fair_pe - current_pe) / fair_pe
    else:
        pe_position = 0

    pe_label = _get_pe_label(current_pe, fair_pe)

    # === PB 法估值 ===
    roe_decimal = roe / 100.0 if roe > 1 else roe

    # 合理PB = ROE / 要求回报率
    if roe_decimal > 0 and required_return > 0:
        fair_pb = roe_decimal / required_return
    else:
        fair_pb = 1.5  # 默认PB

    pb_valuation = fair_pb * bps if bps > 0 else current_price

    # 判断当前PB相对位置
    if current_pb > 0 and fair_pb > 0:
        pb_position = (fair_pb - current_pb) / fair_pb
    else:
        pb_position = 0

    pb_label = _get_pb_label(current_pb, fair_pb)

    # === 每股收益增长估值（PEG变体） ===
    # 合理市盈率 = 增长率 (PEG=1)
    if eps > 0 and profit_growth > 0:
        growth_valuation = eps * profit_growth
    else:
        growth_valuation = current_price

    # === 加权综合估值 ===
    weighted_valuation = pe_valuation * pe_weight + pb_valuation * pb_weight

    # 安全边际
    if weighted_valuation > 0:
        margin = (weighted_valuation - current_price) / weighted_valuation
    else:
        margin = 0

    return {
        "model": "PE/PB 相对估值",
        "current_price": round(current_price, 2),
        "intrinsic_value": round(weighted_valuation, 2),
        "margin_of_safety": round(margin * 100, 1),
        "rating": _get_rating(margin),
        "params": {
            "pe_weight": pe_weight,
            "pb_weight": pb_weight,
            "required_return": round(required_return * 100, 1),
        },
        "pe_analysis": {
            "current_pe": round(current_pe, 2),
            "fair_pe": round(fair_pe, 2),
            "pe_valuation": round(pe_valuation, 2),
            "pe_position": round(pe_position * 100, 1),
            "pe_label": pe_label,
            "eps": round(eps, 4),
            "profit_growth_pct": round(profit_growth, 2),
        },
        "pb_analysis": {
            "current_pb": round(current_pb, 2),
            "fair_pb": round(fair_pb, 2),
            "pb_valuation": round(pb_valuation, 2),
            "pb_position": round(pb_position * 100, 1),
            "pb_label": pb_label,
            "bps": round(bps, 4),
            "roe_pct": round(roe, 2),
        },
        "growth_valuation": round(growth_valuation, 2),
    }


def _get_pe_label(current_pe: float, fair_pe: float) -> str:
    if current_pe <= 0:
        return "PE为负，无法判断"
    if fair_pe <= 0:
        return "合理PE参考失效"

    ratio = current_pe / fair_pe
    if ratio < 0.5:
        return f"PE显著低于合理值 (仅{ratio:.0%})"
    elif ratio < 0.8:
        return f"PE低于合理值 ({ratio:.0%})"
    elif ratio < 1.2:
        return f"PE接近合理值 ({ratio:.0%})"
    elif ratio < 2.0:
        return f"PE高于合理值 ({ratio:.0%})"
    else:
        return f"PE显著高于合理值 ({ratio:.0%})"


def _get_pb_label(current_pb: float, fair_pb: float) -> str:
    if current_pb <= 0:
        return "PB为负，无法判断"
    if fair_pb <= 0:
        return "合理PB参考失效"

    ratio = current_pb / fair_pb
    if ratio < 0.5:
        return f"PB显著低于合理值 (仅{ratio:.0%})"
    elif ratio < 0.8:
        return f"PB低于合理值 ({ratio:.0%})"
    elif ratio < 1.2:
        return f"PB接近合理值 ({ratio:.0%})"
    elif ratio < 2.0:
        return f"PB高于合理值 ({ratio:.0%})"
    else:
        return f"PB显著高于合理值 ({ratio:.0%})"


def _get_rating(margin: float) -> str:
    if margin > 0.30:
        return "强低估"
    elif margin > 0.10:
        return "低估"
    elif margin > -0.10:
        return "合理"
    elif margin > -0.30:
        return "高估"
    else:
        return "强高估"
