"""
综合估值聚合逻辑
"""

import csv
import io
import json
import logging
import threading
import time
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs

from config import SUMMARY_WEIGHTS, RATING_THRESHOLDS
from services.data_fetcher import (
    get_all_financials,
    get_stock_info,
    get_realtime_quote,
    get_top_stocks_by_volume,
    search_stocks,
)
from services.dcf_model import calculate_dcf
from services.pe_pb_model import calculate_pe_pb
from services.graham_model import calculate_graham
from services.peg_roe_model import calculate_peg_roe

logger = logging.getLogger(__name__)

# 批量估值状态
_batch_state = {
    "status": "idle",      # idle | running | done
    "done": 0,
    "total": 0,
    "current": "",
    "csv_data": "",        # CSV 字符串内容
}
_batch_lock = threading.Lock()


def handle_api(path: str, query: str = "", body: dict = None) -> tuple:
    """
    API 路由分发

    返回: (status_code, response_dict)
    """
    if body is None:
        body = {}

    # 解析查询参数
    parsed = urlparse("?" + query) if query else urlparse("")
    params = {k: v[0] for k, v in parse_qs(query).items()}

    # === 健康检查 ===
    if path == "/api/health":
        return 200, {"status": "ok", "service": "A股估值工具"}

    # === 股票搜索 ===
    if path == "/api/stock/search":
        keyword = params.get("keyword", "")
        if not keyword:
            return 400, {"error": "请提供 keyword 参数"}
        results = search_stocks(keyword)
        return 200, {"results": results, "count": len(results)}

    # === 股票基本信息 ===
    if path == "/api/stock/info":
        code = params.get("code", "")
        if not code:
            return 400, {"error": "请提供 code 参数"}
        info = get_stock_info(code)
        quote = get_realtime_quote(code)
        return 200, {"info": info, "quote": quote}

    # === 综合财务数据 ===
    if path == "/api/stock/financials":
        code = params.get("code", "")
        if not code:
            return 400, {"error": "请提供 code 参数"}
        data = get_all_financials(code)
        return 200, data

    # === DCF 估值 ===
    if path == "/api/valuation/dcf":
        code = body.get("code", params.get("code", ""))
        if not code:
            return 400, {"error": "请提供 code"}
        result = calculate_dcf(code, body.get("params"))
        return 200, result

    # === PE/PB 估值 ===
    if path == "/api/valuation/pe_pb":
        code = body.get("code", params.get("code", ""))
        if not code:
            return 400, {"error": "请提供 code"}
        result = calculate_pe_pb(code, body.get("params"))
        return 200, result

    # === Graham 估值 ===
    if path == "/api/valuation/graham":
        code = body.get("code", params.get("code", ""))
        if not code:
            return 400, {"error": "请提供 code"}
        result = calculate_graham(code, body.get("params"))
        return 200, result

    # === PEG/ROE 估值 ===
    if path == "/api/valuation/peg_roe":
        code = body.get("code", params.get("code", ""))
        if not code:
            return 400, {"error": "请提供 code"}
        result = calculate_peg_roe(code, body.get("params"))
        return 200, result

    # === 综合估值 ===
    if path == "/api/valuation/summary":
        code = params.get("code", "")
        if not code:
            return 400, {"error": "请提供 code"}

        try:
            dcf = calculate_dcf(code)
            pe_pb = calculate_pe_pb(code)
            graham = calculate_graham(code)
            peg_roe = calculate_peg_roe(code)

            # 加权综合
            dcf_val = dcf.get("intrinsic_value", 0) if "error" not in dcf else 0
            pe_pb_val = pe_pb.get("intrinsic_value", 0) if "error" not in pe_pb else 0
            graham_val = graham.get("intrinsic_value", 0) if "error" not in graham else 0
            peg_roe_val = peg_roe.get("intrinsic_value", 0) if "error" not in peg_roe else 0

            # 只聚合有效的估值
            valid_weights = 0
            composite_value = 0

            for val, model_name in [(dcf_val, "dcf"), (pe_pb_val, "pe_pb"),
                                      (graham_val, "graham"), (peg_roe_val, "peg_roe")]:
                if val > 0:
                    w = SUMMARY_WEIGHTS.get(model_name, 0.25)
                    composite_value += val * w
                    valid_weights += w

            if valid_weights > 0:
                composite_value /= valid_weights

            # 获取当前价
            quote = get_realtime_quote(code)
            current_price = quote.get("price", 0) or 0

            # 安全边际
            if composite_value > 0 and current_price > 0:
                margin = (composite_value - current_price) / composite_value
            else:
                margin = 0

            return 200, {
                "code": code,
                "current_price": round(current_price, 2),
                "composite_value": round(composite_value, 2),
                "margin_of_safety": round(margin * 100, 1),
                "rating": _get_summary_rating(margin),
                "weighted": SUMMARY_WEIGHTS,
                "models": {
                    "dcf": dcf,
                    "pe_pb": pe_pb,
                    "graham": graham,
                    "peg_roe": peg_roe,
                },
            }
        except Exception as e:
            logger.error(f"综合估值{code}失败: {e}", exc_info=True)
            return 500, {"error": f"计算失败: {str(e)}"}

    # === 批量估值：启动 ===
    if path == "/api/batch/run":
        top_n = int(params.get("top_n", "100"))
        top_n = min(top_n, 300)

        with _batch_lock:
            if _batch_state["status"] == "running":
                return 200, {"status": "already_running", "done": _batch_state["done"], "total": _batch_state["total"]}
            _batch_state["status"] = "running"
            _batch_state["done"] = 0
            _batch_state["total"] = top_n
            _batch_state["current"] = ""
            _batch_state["csv_data"] = ""

        thread = threading.Thread(target=_run_batch, args=(top_n,), daemon=True)
        thread.start()
        return 200, {"status": "started", "total": top_n}

    # === 批量估值：进度查询 ===
    if path == "/api/batch/status":
        with _batch_lock:
            return 200, {
                "status": _batch_state["status"],
                "done": _batch_state["done"],
                "total": _batch_state["total"],
                "current": _batch_state["current"],
            }

    # === 批量估值：下载 CSV ===
    if path == "/api/batch/download":
        with _batch_lock:
            if _batch_state["status"] != "done":
                return 400, {"error": "任务未完成"}
            csv_data = _batch_state["csv_data"]
        return 200, {"csv": csv_data, "filename": "batch_result.csv"}

    return 404, {"error": f"未知接口: {path}"}


def _run_batch(top_n: int):
    """后台线程：批量估值"""
    global _batch_state
    try:
        print(f"[batch] 开始获取 {top_n} 只股票...", flush=True)
        stocks = get_top_stocks_by_volume(top_n)
        print(f"[batch] 获取到 {len(stocks)} 只股票", flush=True)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "排名", "代码", "名称", "最新价", "PE", "PB", "成交量(手)",
            "DCF估值", "PE/PB估值", "Graham估值", "PEG/ROE估值",
            "综合估值", "安全边际%", "评级",
        ])

        for i, s in enumerate(stocks):
            code = s["code"]
            name = s["name"]
            print(f"[batch] [{i+1}/{top_n}] 开始估值 {code} {name}...", flush=True)

            with _batch_lock:
                _batch_state["current"] = f"{code} {name}"

            try:
                dcf = calculate_dcf(code)
                pe_pb = calculate_pe_pb(code)
                graham = calculate_graham(code)
                peg_roe = calculate_peg_roe(code)

                dcf_val = dcf.get("intrinsic_value", 0) if "error" not in dcf else 0
                pe_pb_val = pe_pb.get("intrinsic_value", 0) if "error" not in pe_pb else 0
                graham_val = graham.get("intrinsic_value", 0) if "error" not in graham else 0
                peg_roe_val = peg_roe.get("intrinsic_value", 0) if "error" not in peg_roe else 0

                composite = 0
                valid = 0
                for val, mn in [(dcf_val, "dcf"), (pe_pb_val, "pe_pb"),
                                (graham_val, "graham"), (peg_roe_val, "peg_roe")]:
                    if val > 0:
                        w = SUMMARY_WEIGHTS.get(mn, 0.25)
                        composite += val * w
                        valid += w
                if valid > 0:
                    composite /= valid

                price = s["price"] or pe_pb.get("current_price", 0) or 0
                if composite > 0 and price > 0:
                    margin = (composite - price) / composite
                else:
                    margin = 0

                writer.writerow([
                    i + 1, code, name,
                    round(price, 2), round(s["pe"], 2), round(s["pb"], 2),
                    int(s["volume"]),
                    round(dcf_val, 2), round(pe_pb_val, 2),
                    round(graham_val, 2), round(peg_roe_val, 2),
                    round(composite, 2), round(margin * 100, 1),
                    _get_summary_rating(margin),
                ])
            except Exception as e:
                print(f"[batch] {code} {name} 失败: {e}", flush=True)
                logger.warning(f"批量{code}失败: {e}")

            with _batch_lock:
                _batch_state["done"] = i + 1
                _batch_state["current"] = f"{code} {name}"

            time.sleep(0.3)

        with _batch_lock:
            _batch_state["status"] = "done"
            _batch_state["csv_data"] = output.getvalue()
    except Exception as e:
        logger.error(f"批量估值失败: {e}", exc_info=True)
        with _batch_lock:
            _batch_state["status"] = "idle"


def _get_summary_rating(margin: float) -> str:
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
