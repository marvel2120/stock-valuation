"""
简易内存缓存 - 基于 dict + 时间戳
"""

import time
from functools import wraps

_cache: dict = {}


def cached(ttl: int = 300):
    """
    装饰器：缓存函数结果，ttl 秒过期

    Args:
        ttl: 缓存有效期（秒），默认 300 秒（5分钟）
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 构造缓存 key
            key_parts = [func.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)

            now = time.time()
            if cache_key in _cache:
                entry = _cache[cache_key]
                if now - entry["ts"] < ttl:
                    return entry["value"]

            result = func(*args, **kwargs)
            _cache[cache_key] = {"ts": now, "value": result}
            return result

        return wrapper

    return decorator


def clear_cache():
    """清空所有缓存"""
    _cache.clear()


def cache_stats() -> dict:
    """返回缓存统计"""
    now = time.time()
    total = len(_cache)
    active = sum(1 for v in _cache.values() if now - v["ts"] < 300)
    return {"total_entries": total, "active_entries": active}
