"""
A股批量估值脚本
获取成交量前 N 只股票，逐一估值，输出 CSV 文件
"""

import sys
import os
import csv
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SUMMARY_WEIGHTS
from services.data_fetcher import get_top_stocks_by_volume
from services.dcf_model import calculate_dcf
from services.pe_pb_model import calculate_pe_pb
from services.graham_model import calculate_graham
from services.peg_roe_model import calculate_peg_roe

TOP_N = 200
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_result.csv")


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


def safe_get(d: dict, key: str, default=0):
    """安全获取字典值"""
    val = d.get(key, default)
    if val is None:
        return default
    return val


def main():
    print(f"正在获取成交量前 {TOP_N} 只A股...", flush=True)
    stocks = get_top_stocks_by_volume(TOP_N)
    print(f"获取到 {len(stocks)} 只股票\n", flush=True)

    results = []
    success_count = 0
    skip_count = 0

    for i, s in enumerate(stocks):
        code = s["code"]
        name = s["name"]
        rank = i + 1

        try:
            dcf = calculate_dcf(code)
            pe_pb = calculate_pe_pb(code)
            graham = calculate_graham(code)
            peg_roe = calculate_peg_roe(code)

            # 复用 summary 端点聚合逻辑
            dcf_val = safe_get(dcf, "intrinsic_value") if "error" not in dcf else 0
            pe_pb_val = safe_get(pe_pb, "intrinsic_value") if "error" not in pe_pb else 0
            graham_val = safe_get(graham, "intrinsic_value") if "error" not in graham else 0
            peg_roe_val = safe_get(peg_roe, "intrinsic_value") if "error" not in peg_roe else 0

            composite_value = 0
            valid_weights = 0

            for val, model_name in [
                (dcf_val, "dcf"), (pe_pb_val, "pe_pb"),
                (graham_val, "graham"), (peg_roe_val, "peg_roe")
            ]:
                if val > 0:
                    w = SUMMARY_WEIGHTS.get(model_name, 0.25)
                    composite_value += val * w
                    valid_weights += w

            if valid_weights > 0:
                composite_value /= valid_weights

            current_price = s["price"] or safe_get(pe_pb, "current_price") or 0
            if composite_value > 0 and current_price > 0:
                margin = (composite_value - current_price) / composite_value
            else:
                margin = 0

            volume = s["volume"]
            results.append({
                "排名": rank,
                "代码": code,
                "名称": name,
                "最新价": round(current_price, 2),
                "PE": round(s["pe"], 2),
                "PB": round(s["pb"], 2),
                "成交量(手)": int(volume),
                "DCF估值": round(dcf_val, 2),
                "PE/PB估值": round(pe_pb_val, 2),
                "Graham估值": round(graham_val, 2),
                "PEG/ROE估值": round(peg_roe_val, 2),
                "综合估值": round(composite_value, 2),
                "安全边际%": round(margin * 100, 1),
                "评级": _get_rating(margin),
            })

            success_count += 1
            if (i + 1) % 10 == 0:
                print(f"[{i + 1}/{TOP_N}] {code} {name} OK", flush=True)

        except Exception as e:
            skip_count += 1
            reason = str(e)[:50]
            print(f"[{i + 1}/{TOP_N}] {code} {name} FAIL ({reason})", flush=True)
            continue

        # 请求间短暂休息，避免过快
        time.sleep(0.3)

    # 写入 CSV
    if not results:
        print("\n没有成功估值的股票，退出")
        return

    fieldnames = [
        "排名", "代码", "名称", "最新价", "PE", "PB", "成交量(手)",
        "DCF估值", "PE/PB估值", "Graham估值", "PEG/ROE估值",
        "综合估值", "安全边际%", "评级",
    ]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n完成! 成功 {success_count} 只, 跳过 {skip_count} 只")
    print(f"结果已保存到: {OUTPUT_FILE}")

    # 预览前 10 条
    print(f"\n===== 前 10 名 =====")
    for r in results[:10]:
        vol = r.get("成交量(手)", 0)
        print(
            f"{r['排名']:>3}. {r['代码']} {r['名称']:<8} "
            f"价¥{r['最新价']:>8}  量{vol:>10}手  估值¥{r['综合估值']:>8}  "
            f"安全边际{r['安全边际%']:>6}%  {r['评级']}"
        )


if __name__ == "__main__":
    main()
