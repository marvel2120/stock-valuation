"""
PEG / ROE 多维估值模型

PEG = PE / (净利润增长率 × 100)

ROE 估值 = ROE / 要求回报率 × 每股净资产
"""

import logging
from typing import Dict, Any

from services.data_fetcher import (
    get_financial_indicators,
    get_realtime_quote,
)

logger = logging.getLogger(__name__)


def calculate_peg_roe(code: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    PEG/ROE 估值

    参数:
        code: 股票代码
        params:
            - required_return: 要求回报率 (默认 0.10)
    """
    if params is None:
        params = {}

    required_return = float(params.get("required_return", 0.10))

    # 获取数据
    quote = get_realtime_quote(code)
    indicators = get_financial_indicators(code)

    if quote.get("price") is None:
        return {"error": "无法获取当前股价"}

    current_price = float(quote["price"])
    pe = quote.get("pe") or 0
    eps = indicators.get("eps", 0)
    bps = indicators.get("bps", 0)
    roe = indicators.get("roe", 0)  # 百分比形式，如 15.5 表示 15.5%
    profit_growth = indicators.get("profit_growth", 0)  # 百分比形式

    # === PEG 分析 ===
    peg_result = _calc_peg(pe, profit_growth)

    # === ROE 估值 ===
    roe_result = _calc_roe_valuation(roe, bps, current_price, required_return)

    # === 综合得分 ===
    # PEG 得分: 0-100 (越低越好)
    peg_val = peg_result.get("peg")
    if peg_val is not None and peg_val > 0:
        peg_score = max(0, min(100, (1 - peg_val / 3) * 100))
    else:
        peg_score = 50
    # ROE 得分: 0-100
    roe_score = max(0, min(100, roe_result.get("roe_score", 50)))

    # 综合:
    # PEG 和 ROE 各占 50%
    composite_score = peg_score * 0.5 + roe_score * 0.5

    # 综合估值
    if roe_result["roe_valuation"] > 0 and peg_val is not None and peg_val < 5:
        # PEG 估值: 合理PEG=1, 当前PE / 合理PEG
        peg_fair_pe = max(profit_growth, 5) if profit_growth > 0 else 15  # PEG=1时的合理PE
        peg_valuation = peg_fair_pe * eps if eps > 0 else current_price
        roe_valuation_val = roe_result["roe_valuation"]
        composite_valuation = peg_valuation * 0.4 + roe_valuation_val * 0.6
    else:
        composite_valuation = current_price

    if composite_valuation > 0:
        margin = (composite_valuation - current_price) / composite_valuation
    else:
        margin = 0

    return {
        "model": "PEG/ROE 多维估值",
        "current_price": round(current_price, 2),
        "intrinsic_value": round(composite_valuation, 2),
        "margin_of_safety": round(margin * 100, 1),
        "rating": _get_rating(margin),
        "peg_analysis": peg_result,
        "roe_analysis": {
            "roe": round(roe, 2),
            "bps": round(bps, 4),
            "roe_valuation": round(roe_result["roe_valuation"], 2),
            "roe_score": round(roe_score, 1),
        },
        "composite_score": round(composite_score, 1),
        "params": {
            "required_return": round(required_return * 100, 1),
        },
    }


def _calc_peg(pe: float, profit_growth: float) -> dict:
    """计算 PEG"""
    if pe <= 0 or profit_growth == 0:
        return {
            "peg": None,
            "peg_label": "无法计算 (PE或增长率为负)",
            "is_undervalued": False,
        }

    peg = pe / abs(profit_growth) if profit_growth != 0 else 999

    if peg < 0:
        label = "负增长，PEG无意义"
    elif peg < 0.5:
        label = "强低估 (PEG < 0.5)"
    elif peg < 1:
        label = "合理偏低估 (0.5 ≤ PEG < 1)"
    elif peg < 2:
        label = "合理偏高估 (1 ≤ PEG < 2)"
    else:
        label = "高估 (PEG ≥ 2)"

    return {
        "peg": round(peg, 2),
        "pe": round(pe, 2),
        "profit_growth_pct": round(profit_growth, 2),
        "peg_label": label,
        "is_undervalued": 0 < peg < 1,
    }


def _calc_roe_valuation(roe: float, bps: float, current_price: float,
                         required_return: float) -> dict:
    """
    ROE 估值法

    合理PB ≈ ROE / required_return (简化假设g=0)
    ROE估值 = 合理PB × BPS
    """
    roe_decimal = roe / 100.0 if roe > 1 else roe  # 统一转换为小数

    if roe_decimal <= 0 or required_return <= 0 or bps <= 0:
        return {
            "roe_valuation": current_price,
            "roe_score": 50,
            "fair_pb": 0,
        }

    # 合理市净率
    fair_pb = roe_decimal / required_return

    # 基于ROE的估值
    roe_valuation = fair_pb * bps

    # ROE 得分
    if roe_decimal >= 0.20:  # ROE ≥ 20%
        roe_score = 90
    elif roe_decimal >= 0.15:
        roe_score = 75
    elif roe_decimal >= 0.10:
        roe_score = 60
    elif roe_decimal >= 0.05:
        roe_score = 40
    else:
        roe_score = 20

    return {
        "roe_valuation": roe_valuation,
        "roe_score": roe_score,
        "fair_pb": round(fair_pb, 2),
        "roe_decimal": round(roe_decimal * 100, 1),
    }


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
