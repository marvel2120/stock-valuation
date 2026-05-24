"""
DCF 现金流折现估值模型

自由现金流 FCF = 经营活动现金流净额 - 资本支出
              = 经营活动产生的现金流量净额 - 购建固定资产无形资产支付的现金
"""

import math
import logging
from typing import Dict, Any

import pandas as pd
import numpy as np

from services.data_fetcher import (
    get_cash_flow_statement,
    get_balance_sheet,
    get_income_statement,
    get_stock_info,
    get_realtime_quote,
    safe_float,
)

logger = logging.getLogger(__name__)


def calculate_dcf(code: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    计算 DCF 估值

    参数:
        code: 股票代码
        params: 可选参数字典
            - projection_years: 预测年限 (默认 5)
            - fcf_growth_rate: FCF增长率 (None=自动从历史计算)
            - perpetual_growth_rate: 永续增长率 (默认 0.03)
            - wacc: 加权平均资本成本/折现率 (默认 0.08)

    返回:
        包含计算过程和结果的字典
    """
    if params is None:
        params = {}

    projection_years = int(params.get("projection_years", 5))
    perpetual_growth_rate = float(params.get("perpetual_growth_rate", 0.03))
    wacc = float(params.get("wacc", 0.08))

    # 获取现金流量表
    cf = get_cash_flow_statement(code)
    if cf.empty:
        return {"error": "无法获取现金流量表数据"}

    # 获取实时行情和基本信息
    quote = get_realtime_quote(code)
    info = get_stock_info(code)

    if quote.get("price") is None:
        return {"error": "无法获取当前股价"}

    current_price = float(quote["price"])
    total_shares = float(info.get("total_shares", 0))

    # 提取自由现金流
    fcf_history = _extract_fcf_history(cf)
    if not fcf_history or len(fcf_history) < 2:
        return {"error": "自由现金流数据不足，至少需要2期"}

    # 计算历史FCF增长率
    historical_growth = _calc_growth_rate(fcf_history)

    # 使用用户指定的增长率或自动计算的
    fcf_growth_rate = params.get("fcf_growth_rate")
    if fcf_growth_rate is None:
        fcf_growth_rate = max(min(historical_growth, 0.25), -0.10)  # 限制在 -10% ~ 25%

    # 最近一期 FCF
    latest_fcf = fcf_history[0]

    # 预测未来 FCF
    projected_fcfs = []
    pv_fcfs = []
    for year in range(1, projection_years + 1):
        fcf = latest_fcf * (1 + fcf_growth_rate) ** year
        pv = fcf / (1 + wacc) ** year
        projected_fcfs.append({"year": year, "fcf": round(fcf, 4), "pv": round(pv, 4)})
        pv_fcfs.append(pv)

    # 终值计算 (戈登增长模型)
    terminal_fcf = latest_fcf * (1 + fcf_growth_rate) ** projection_years * (1 + perpetual_growth_rate)
    terminal_value = terminal_fcf / (wacc - perpetual_growth_rate)
    pv_terminal = terminal_value / (1 + wacc) ** projection_years

    # 企业价值
    enterprise_value = sum(pv_fcfs) + pv_terminal

    # 获取现金和负债信息
    cash, total_debt = _get_cash_and_debt(code)

    # 调整后企业价值
    adjusted_value = enterprise_value + cash - total_debt

    # 每股内在价值
    if total_shares > 0:
        intrinsic_per_share = adjusted_value / (total_shares * 10000)  # 总股本单位通常是万股
    else:
        intrinsic_per_share = 0

    # 安全边际
    if intrinsic_per_share > 0:
        margin_of_safety = (intrinsic_per_share - current_price) / intrinsic_per_share
    else:
        margin_of_safety = 0

    return {
        "model": "DCF",
        "current_price": round(current_price, 2),
        "intrinsic_value": round(intrinsic_per_share, 2),
        "margin_of_safety": round(margin_of_safety * 100, 1),
        "rating": _get_rating(margin_of_safety),
        "params": {
            "projection_years": projection_years,
            "fcf_growth_rate": round(fcf_growth_rate * 100, 1),
            "historical_growth": round(historical_growth * 100, 1),
            "perpetual_growth_rate": round(perpetual_growth_rate * 100, 1),
            "wacc": round(wacc * 100, 1),
        },
        "cash_flows": {
            "historical_fcf": [round(x, 4) for x in fcf_history[:3]],
            "latest_fcf": round(latest_fcf, 4),
            "projected": projected_fcfs,
            "terminal_value": round(terminal_value, 4),
            "pv_terminal": round(pv_terminal, 4),
            "enterprise_value": round(enterprise_value, 4),
            "cash": round(cash, 4),
            "debt": round(total_debt, 4),
            "adjusted_value": round(adjusted_value, 4),
        },
    }


def _extract_fcf_history(cf_df: pd.DataFrame) -> list:
    """
    从现金流量表提取历史自由现金流

    A股现金流量表常见列名：
    - "经营活动产生的现金流量净额" 或包含"经营活动"和"净额"
    - "购建固定资产、无形资产和其他长期资产支付的现金" 或包含"购建固定"
    """
    fcf_list = []

    for _, row in cf_df.iterrows():
        operating_cf = 0
        capex = 0

        for col in cf_df.columns:
            col_str = str(col)
            val = safe_float(row[col])

            if "经营活动" in col_str and ("净额" in col_str or "净流量" in col_str):
                operating_cf = val
            elif "购建固定" in col_str or "购建固定资产" in col_str:
                capex = val

        if operating_cf != 0:
            fcf = operating_cf - abs(capex)  # 资本支出取绝对值（报表中通常为负数）
            fcf_list.append(fcf)

    return fcf_list


def _calc_growth_rate(values: list) -> float:
    """计算年均复合增长率 (CAGR)"""
    if len(values) < 2:
        return 0.0

    values = [v for v in values if v != 0]
    if len(values) < 2:
        return 0.0

    first, last = values[-1], values[0]
    periods = len(values) - 1

    if first <= 0:
        return 0.0

    # 如果最新一期 FCF 为负，或首尾符号不同，无法计算合理 CAGR
    if last <= 0 or last * first < 0:
        return 0.0

    try:
        cagr = (last / first) ** (1 / periods) - 1
        return float(cagr)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def _get_cash_and_debt(code: str) -> tuple:
    """获取现金及等价物和总负债"""
    bs = get_balance_sheet(code)
    if bs.empty:
        return 0, 0

    cash = 0
    debt = 0

    latest = bs.iloc[0]
    for col in bs.columns:
        col_str = str(col)
        val = safe_float(latest[col])

        if "货币资金" in col_str:
            cash = val
        elif "负债合计" in col_str:
            debt = val

    return cash, debt


def _get_rating(margin: float) -> str:
    """根据安全边际返回评级"""
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
