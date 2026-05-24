"""
数据获取层
使用新浪、腾讯等公开 API 获取 A 股数据
"""

import os
import re
import time
import logging
from typing import Dict, Any, List

import requests
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup

from utils.cache import cached

logger = logging.getLogger(__name__)

# 清除代理环境变量
for _env in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_env, None)
    os.environ.pop(_env.upper(), None)

# HTTP 会话
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
})


def _get(url: str, **kwargs) -> requests.Response:
    """带重试的 GET 请求"""
    kwargs.setdefault("timeout", 15)
    for attempt in range(3):
        try:
            resp = _session.get(url, **kwargs)
            # apparent_encoding 有时把 GBK 误判为 Shift_JIS，优先相信响应头
            if "charset" not in resp.headers.get("content-type", "").lower():
                detected = resp.apparent_encoding or ""
                if "shift" not in detected.lower():
                    resp.encoding = detected
                else:
                    resp.encoding = "gbk"
            return resp
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1)


# ============================================================
# 代码格式化
# ============================================================

def normalize_code(code: str) -> str:
    """标准化股票代码为纯数字"""
    code = code.strip().upper()
    for prefix in ("SH.", "SZ.", "BJ.", "SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    return code


def _get_market(code: str) -> str:
    """根据代码判断市场前缀"""
    code = normalize_code(code)
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith("0") or code.startswith("3"):
        return f"sz{code}"
    elif code.startswith("4") or code.startswith("8"):
        return f"bj{code}"
    return f"sh{code}"


# ============================================================
# 实时行情 (腾讯 API)
# ============================================================

@cached(ttl=120)
def get_realtime_quote(code: str) -> Dict[str, Any]:
    """
    获取单只股票实时行情
    使用腾讯行情 API (qt.gtimg.cn)，不依赖 push2.eastmoney.com
    """
    market_code = _get_market(code)
    try:
        resp = _get(f"https://qt.gtimg.cn/q={market_code}", timeout=10)
        text = resp.text

        if not text or "none" in text.lower():
            return {"price": None, "pe": None, "pb": None, "error": "未找到该股票"}

        # 腾讯行情格式: v_sh600519="1~name~code~price~..."
        # 字段索引:
        #   [1] name, [3] price, [4] yesterday_close, [5] open,
        #   [6] volume(手), [31] change, [32] change_pct,
        #   [33] high, [34] low, [36] volume, [37] amount(万),
        #   [39] PE_TTM, [44] total_market_cap(亿),
        #   [45] circulating_market_cap(亿), [46] PB
        match = re.search(r'"(.+)"', text)
        if not match:
            return {"price": None, "error": "解析腾讯行情失败"}

        parts = match.group(1).split("~")

        def _field(index: int, default=""):
            return parts[index] if len(parts) > index else default

        return {
            "name": _field(1),
            "price": safe_float(_field(3)),
            "change_pct": safe_float(_field(32)),
            "volume": safe_float(_field(6)),
            "amount": safe_float(_field(37)) * 10000,  # 转换为元
            "high": safe_float(_field(33)),
            "low": safe_float(_field(34)),
            "pe": safe_float(_field(39)),
            "pb": safe_float(_field(46)),
            "total_value": safe_float(_field(44)) * 1e8,  # 亿转元
            "circulating_value": safe_float(_field(45)) * 1e8,
        }
    except Exception as e:
        logger.warning(f"腾讯行情({code})失败: {e}")
        return {"price": None, "pe": None, "pb": None, "error": str(e)}


# ============================================================
# 股票搜索 (新浪 API)
# ============================================================

def search_stocks(keyword: str) -> List[dict]:
    """模糊搜索股票，返回 {code, name, price, pe, pb} 列表"""
    keyword = keyword.strip()
    if not keyword:
        return []

    results = []

    try:
        # 新浪 suggest API 进行模糊搜索
        resp = _get(
            "https://suggest3.sinajs.cn/suggest/",
            params={"type": "11,12,13,14,15", "key": keyword},
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=10,
        )
        text = resp.text

        # 格式: var suggestvalue="sh600519,11,600519,sh600519,贵州茅台,...;...";
        # 先提取外层引号中的完整内容
        outer_match = re.search(r'"([^"]*)"', text)
        if outer_match:
            groups = outer_match.group(1).split(";")
            for group in groups:
                parts = [p.strip() for p in group.split(",")]
                if len(parts) < 5:
                    continue
                code = clean_suggest_code(parts[2])
                name = parts[4]
                if not code or not name:
                    continue
                results.append({
                    "code": code,
                    "name": name,
                    "price": 0,
                    "pe": 0,
                    "pb": 0,
                })
    except Exception as e:
        logger.warning(f"suggest搜索失败: {e}")

    # 如果 suggest 无结果，尝试通过腾讯行情验证代码
    if not results and keyword.isdigit() and len(keyword) == 6:
        try:
            market_code = _get_market(keyword)
            resp = _get(f"https://qt.gtimg.cn/q={market_code}", timeout=10)
            if resp.text and keyword in resp.text and "~" in resp.text:
                parts = resp.text.split("~")
                name = parts[1] if len(parts) > 1 else keyword
                results.append({
                    "code": keyword,
                    "name": name,
                    "price": 0,
                    "pe": 0,
                    "pb": 0,
                })
        except Exception:
            pass

    return results[:20]


def clean_suggest_code(code: str) -> str:
    """清理 suggest API 返回的代码"""
    code = code.strip()
    # 移除市场前缀
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if code.lower().startswith(prefix.lower()):
            code = code[len(prefix):]
            break
    return code


# ============================================================
# 股票基本信息
# ============================================================

@cached(ttl=600)
def get_stock_info(code: str) -> Dict[str, Any]:
    """获取股票基本信息"""
    code = normalize_code(code)
    market_code = _get_market(code)

    # 从实时行情获取名称
    name = code
    try:
        quote = get_realtime_quote(code)
        name = quote.get("name", code) or code
    except Exception:
        pass

    info = {"code": code, "name": name, "full_name": name}

    # 获取总股本 (新浪公司页面的 JS 变量)
    try:
        market = "sh" if code.startswith("6") else "sz"
        resp = _get(
            f"https://finance.sina.com.cn/realstock/company/{market_code}/nc.shtml",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=10,
        )
        text = resp.text

        # var totalcapital = 125227.021500; //总股本（万股）
        m = re.search(r"var\s+totalcapital\s*=\s*([\d.]+)", text)
        if m:
            info["total_shares"] = safe_float(m.group(1))

        m = re.search(r"var\s+currcapital\s*=\s*([\d.]+)", text)
        if m:
            info["circulating_shares"] = safe_float(m.group(1))
    except Exception as e:
        logger.debug(f"获取{code}股本失败: {e}")

    info.setdefault("total_shares", 0)
    info.setdefault("circulating_shares", 0)
    info.setdefault("industry", "")
    info.setdefault("listing_date", "")

    return info


# ============================================================
# 财务三表 (新浪 HTML 解析)
# ============================================================

def _parse_sina_financial_table(url: str, years: int = 1) -> pd.DataFrame:
    """
    解析新浪财务报表 HTML 页面，提取表格数据
    url: 报表 URL 模板，支持 {year} 占位符
    返回 DataFrame: index 为报告期, columns 为科目名称
    """
    from datetime import datetime
    current_year = datetime.now().year

    all_dfs = []
    for year_offset in range(years):
        year = current_year - year_offset
        year_url = url.format(year=year)

        try:
            resp = _get(year_url, timeout=15)
        except Exception:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # 查找数据表格
        table = (soup.find("table", id="BalanceSheetNewTable0") or
                 soup.find("table", id="ProfitStatementNewTable0") or
                 soup.find("table", id="CashFlowStatementNewTable0") or
                 soup.find("table"))

        if not table:
            continue

        rows = table.find_all("tr")
        if not rows:
            continue

        # 找日期行
        dates = []
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            cell_texts = [c.get_text(strip=True) for c in cells]
            if not cell_texts:
                continue
            if "报表日期" in cell_texts[0]:
                for cell in cells[1:]:
                    text = cell.get_text(strip=True)
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
                    if m:
                        dates.append(m.group(1))
                break

        if not dates:
            continue

        # 提取数据行
        data = {}
        seen_date_row = False
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            first_text = tds[0].get_text(strip=True)
            if first_text == "报表日期":
                seen_date_row = True
                continue
            if not seen_date_row:
                continue
            colspan = tds[0].get("colspan")
            if colspan and int(colspan) > 1:
                continue
            if not first_text:
                continue

            values = []
            for td in tds[1:1 + len(dates)]:
                val = td.get_text(strip=True).replace(",", "").replace("--", "")
                # 新浪数据单位为万元，转为元
                values.append(safe_float(val) * 10000)
            if len(values) == len(dates):
                data[first_text] = values

        if data:
            df = pd.DataFrame(data, index=dates)
            all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs)
    # 只保留年末数据 (12-31)，去除累计的季报数据
    result = result[result.index.str.endswith("-12-31")]
    return result


@cached(ttl=3600)
def get_balance_sheet(code: str) -> pd.DataFrame:
    """获取资产负债表（最近5年年末数据）"""
    code = normalize_code(code)
    url_tpl = f"https://money.finance.sina.com.cn/corp/go.php/vFD_BalanceSheet/stockid/{code}/ctrl/{{year}}/displaytype/4.phtml"
    return _parse_sina_financial_table(url_tpl, years=5)


@cached(ttl=3600)
def get_income_statement(code: str) -> pd.DataFrame:
    """获取利润表（最近5年年末数据）"""
    code = normalize_code(code)
    url_tpl = f"https://money.finance.sina.com.cn/corp/go.php/vFD_ProfitStatement/stockid/{code}/ctrl/{{year}}/displaytype/4.phtml"
    return _parse_sina_financial_table(url_tpl, years=5)


@cached(ttl=3600)
def get_cash_flow_statement(code: str) -> pd.DataFrame:
    """获取现金流量表（最近5年年末数据）"""
    code = normalize_code(code)
    url_tpl = f"https://money.finance.sina.com.cn/corp/go.php/vFD_CashFlow/stockid/{code}/ctrl/{{year}}/displaytype/4.phtml"
    return _parse_sina_financial_table(url_tpl, years=5)


# ============================================================
# 财务指标 (新浪)
# ============================================================

@cached(ttl=3600)
def get_financial_indicators(code: str) -> Dict[str, Any]:
    """获取关键财务指标"""
    code = normalize_code(code)

    try:
        resp = _get(
            f"https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine/stockid/{code}/ctrl/2024/displaytype/4.phtml",
            timeout=15,
        )

        text = resp.text

        # 从新浪财务指标页提取数据
        def _extract(pattern: str) -> float:
            m = re.search(pattern, text)
            if m:
                return safe_float(m.group(1))
            return 0.0

        # 最新一期数据
        eps = _extract(r"每股收益.*?>\s*([\d.-]+)")
        bps = _extract(r"每股净资产.*?>\s*([\d.-]+)")
        roe = _extract(r"净资产收益率.*?>\s*([\d.-]+)")
        roa = _extract(r"总资产报酬率.*?>\s*([\d.-]+)")
        gross_margin = _extract(r"销售毛利率.*?>\s*([\d.-]+)")
        net_margin = _extract(r"销售净利率.*?>\s*([\d.-]+)")
        debt_ratio = _extract(r"资产负债率.*?>\s*([\d.-]+)")
        current_ratio = _extract(r"流动比率.*?>\s*([\d.-]+)")
        quick_ratio = _extract(r"速动比率.*?>\s*([\d.-]+)")

        # 增长率
        revenue_growth = _extract(r"营业收入增长率.*?>\s*([\d.-]+)")
        profit_growth = _extract(r"净利润增长率.*?>\s*([\d.-]+)")
        total_asset_growth = _extract(r"总资产增长率.*?>\s*([\d.-]+)")

        # 如果上面的正则没提取到，尝试用 HTML 解析
        soup = BeautifulSoup(resp.text, "html.parser")

        def _td_value(label: str) -> float:
            for td in soup.find_all("td"):
                text = td.get_text(strip=True)
                if label in text:
                    next_td = td.find_next("td")
                    if next_td:
                        return safe_float(next_td.get_text(strip=True))
            return 0.0

        if eps == 0:
            eps = _td_value("摊薄每股收益") or _td_value("基本每股收益")
        if bps == 0:
            bps = _td_value("每股净资产")
        if roe == 0:
            roe = _td_value("净资产收益率")
        if revenue_growth == 0:
            revenue_growth = _td_value("营业收入增长率")

        return {
            "eps": eps,
            "bps": bps,
            "roe": roe,
            "roa": roa,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "debt_ratio": debt_ratio,
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "revenue_growth": revenue_growth,
            "profit_growth": profit_growth,
            "total_asset_growth": total_asset_growth,
        }
    except Exception as e:
        logger.warning(f"财务指标({code})失败: {e}")
        return {
            "eps": 0, "bps": 0, "roe": 0, "roa": 0,
            "gross_margin": 0, "net_margin": 0, "debt_ratio": 0,
            "current_ratio": 0, "quick_ratio": 0,
            "revenue_growth": 0, "profit_growth": 0, "total_asset_growth": 0,
        }


# ============================================================
# PE/PB 历史数据
# ============================================================

@cached(ttl=3600)
def get_pe_pb_history(code: str) -> pd.DataFrame:
    """获取财务指标历史数据"""
    # 复用 get_financial_indicators 的数据源，返回历史 Dataframe
    # 实际上历史 PE/PB 计算需要历史股价和 EPS/BPS
    # 这里返回财务指标历史用于模型计算
    code = normalize_code(code)
    try:
        resp = _get(
            f"https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine/stockid/{code}/ctrl/2024/displaytype/4.phtml",
            timeout=15,
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        # 尝试解析历史数据表
        table = soup.find("table")
        if not table:
            return pd.DataFrame()

        return pd.read_html(str(table))[0] if table else pd.DataFrame()
    except Exception as e:
        logger.warning(f"获取{code}历史PE/PB失败: {e}")
        return pd.DataFrame()


# ============================================================
# 历史股价 (新浪 kline)
# ============================================================

@cached(ttl=1800)
def get_price_history(code: str, period: str = "monthly") -> pd.DataFrame:
    """获取历史股价"""
    code = normalize_code(code)
    market_code = _get_market(code)

    # 周期映射
    scale_map = {"daily": 240, "weekly": 30, "monthly": 360}
    scale = scale_map.get(period, 240)

    try:
        # 新浪 kline API
        resp = _get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
            params={
                "symbol": market_code,
                "scale": scale,
                "ma": "no",
                "datalen": scale,
            },
            timeout=15,
        )
        data = resp.json(strict=False)
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df = df.rename(columns={
            "day": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "volume": "成交量",
        })
        df["开盘"] = df["开盘"].apply(safe_float)
        df["最高"] = df["最高"].apply(safe_float)
        df["最低"] = df["最低"].apply(safe_float)
        df["收盘"] = df["收盘"].apply(safe_float)
        df["成交量"] = df["成交量"].apply(safe_float)
        return df
    except Exception as e:
        logger.warning(f"历史股价({code})失败: {e}")
        return pd.DataFrame()


# ============================================================
# 成交量排名 (新浪 Market Center API)
# ============================================================

@cached(ttl=120)
def get_top_stocks_by_volume(top_n: int = 200) -> List[dict]:
    """
    获取成交量前 N 只 A 股
    返回 [{code, name, price, pe, pb, volume}]
    """
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    page_size = 100
    pages_needed = (top_n + page_size - 1) // page_size
    all_stocks = []

    for page in range(1, pages_needed + 1):
        try:
            resp = _get(url, params={
                "page": page,
                "num": page_size,
                "sort": "volume",
                "asc": "0",
                "node": "hs_a",
            }, timeout=15)
            data = resp.json(strict=False)
            if not data:
                break
            for s in data:
                all_stocks.append({
                    "code": str(s.get("code", "")),
                    "name": str(s.get("name", "")),
                    "price": safe_float(s.get("trade", 0)),
                    "pe": safe_float(s.get("per", 0)),
                    "pb": safe_float(s.get("pb", 0)),
                    "volume": safe_float(s.get("volume", 0)),
                })
            if page < pages_needed:
                time.sleep(0.5)
        except Exception as e:
            logger.warning(f"获取成交量排名第{page}页失败: {e}")

    return all_stocks[:top_n]


# ============================================================
# 综合数据获取
# ============================================================

def get_all_financials(code: str) -> Dict[str, Any]:
    """获取一只股票的所有财务数据（综合接口）"""
    info = get_stock_info(code)
    quote = get_realtime_quote(code)
    indicators = get_financial_indicators(code)

    return {
        "info": info,
        "quote": quote,
        "indicators": indicators,
    }


# ============================================================
# 工具函数
# ============================================================

def safe_float(val, default=0.0) -> float:
    """安全转换为 float"""
    if val is None or val == "" or val == "-" or val == "None":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_value(val):
    """将 numpy/pandas 类型转为 Python 原生类型"""
    if val is None or pd.isna(val):
        return None
    if hasattr(val, "item"):
        return val.item()
    return val
