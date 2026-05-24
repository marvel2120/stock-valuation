"""
Graham Number 估值模型

Graham Number = sqrt(22.5 × EPS × BVPS)

由本杰明·格雷厄姆提出，基于两个简单原则：
- 市盈率不超过 15
- 市净率不超过 1.5
因此 15 × 1.5 = 22.5

当股价低于 Graham Number 时，股票可能被低估。
当股价低于 Graham Number 的 2/3 时，具备安全边际。
"""

import math
import logging
from typing import Dict, Any

from services.data_fetcher import (
    get_financial_indicators,
    get_realtime_quote,
)

logger = logging.getLogger(__name__)


def calculate_graham(code: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Graham Number 估值

    参数:
        code: 股票代码
        params:
            - max_pe: 最大PE (默认 15)
            - max_pb: 最大PB (默认 1.5)
    """
    if params is None:
        params = {}

    max_pe = float(params.get("max_pe", 15))
    max_pb = float(params.get("max_pb", 1.5))
    factor = max_pe * max_pb

    # 获取数据
    quote = get_realtime_quote(code)
    indicators = get_financial_indicators(code)

    if quote.get("price") is None:
        return {"error": "无法获取当前股价"}

    current_price = float(quote["price"])
    eps = indicators.get("eps", 0)
    bps = indicators.get("bps", 0)

    if eps <= 0 or bps <= 0:
        return {
            "model": "Graham Number",
            "current_price": round(current_price, 2),
            "error": "EPS 或 BPS 为负，无法计算 Graham Number（仅适用于盈利公司）",
            "eps": round(eps, 4),
            "bps": round(bps, 4),
        }

    # 标准 Graham Number
    graham_number = math.sqrt(factor * eps * bps)

    # 安全边际
    if graham_number > 0:
        margin = (graham_number - current_price) / graham_number
        safety_margin_price = graham_number * 0.66
    else:
        margin = 0
        safety_margin_price = 0

    # 变体：激进版（PE上限20）和保守版（PE上限12）
    aggressive_pe = 20
    conservative_pe = 12
    graham_aggressive = math.sqrt(aggressive_pe * max_pb * eps * bps)
    graham_conservative = math.sqrt(conservative_pe * max_pb * eps * bps)

    return {
        "model": "Graham Number",
        "current_price": round(current_price, 2),
        "intrinsic_value": round(graham_number, 2),
        "margin_of_safety": round(margin * 100, 1),
        "rating": _get_graham_rating(current_price, graham_number, safety_margin_price),
        "params": {
            "max_pe": max_pe,
            "max_pb": max_pb,
            "factor": factor,
        },
        "details": {
            "eps": round(eps, 4),
            "bps": round(bps, 4),
            "graham_number": round(graham_number, 2),
            "safety_margin_price": round(safety_margin_price, 2),
            "aggressive": round(graham_aggressive, 2),
            "conservative": round(graham_conservative, 2),
        },
    }


def _get_graham_rating(price: float, graham: float, safety_price: float) -> str:
    """Graham 特定评级"""
    if price <= safety_price:
        return "具备安全边际 (股价 < 2/3 Graham Number)"
    elif price <= graham:
        return "低估 (股价 < Graham Number)"
    else:
        return "高于 Graham Number"
