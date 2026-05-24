"""
A股股票估值工具 - 配置文件
"""

import os

# 服务器配置
SERVER_CONFIG = {
    "host": "0.0.0.0",
    "port": 8000,
    "reload": True,
}

# 默认估值模型参数
VALUATION_DEFAULTS = {
    "dcf": {
        "projection_years": 5,          # 预测年限
        "perpetual_growth_rate": 0.03,   # 永续增长率 3%
        "wacc": 0.08,                    # 折现率 8%
        "fcf_growth_rate": None,         # None 表示自动从历史数据计算
    },
    "pe_pb": {
        "pe_weight": 0.6,               # PE法权重
        "pb_weight": 0.4,               # PB法权重
    },
    "graham": {
        "max_pe": 15,                   # 格雷厄姆建议最大PE
        "max_pb": 1.5,                  # 格雷厄姆建议最大PB
        "aggressive_pe": 20,            # 激进版PE上限
        "conservative_pe": 12,          # 保守版PE上限
    },
    "peg_roe": {
        "required_return": 0.10,        # 要求回报率 10%
    },
}

# 综合估值权重
SUMMARY_WEIGHTS = {
    "dcf": 0.40,
    "pe_pb": 0.30,
    "graham": 0.15,
    "peg_roe": 0.15,
}

# 缓存配置（秒）
CACHE_TTL = 300  # 5分钟

# AKShare 请求配置
AKSHARE_CONFIG = {
    "timeout": 30,
    "retry_times": 3,
    "retry_delay": 1.0,  # 秒
}

# 估值评级阈值
RATING_THRESHOLDS = {
    "strong_undervalued": 0.30,   # 安全边际 > 30% → 强低估
    "undervalued": 0.10,          # 安全边际 > 10% → 低估
    "fair": -0.10,                # 安全边际 > -10% → 合理
    "overvalued": -0.30,          # 安全边际 > -30% → 高估
    # 安全边际 < -30% → 强高估
}

# 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
