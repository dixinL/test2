"""技术指标纯函数库（标准库实现，无第三方依赖）。

约定：
- 输入一律为**前复权**价格序列（除权跳空会制造假金叉/假破位）。
- 均线/MACD/RSI 计算口径与主流行情软件一致：EMA 以首值种子递推，
  RSI 用 Wilder 平滑。
- 序列中的 None（停牌等）由调用方在构建 ctx 前剔除。
"""
from __future__ import annotations


def sma(values: list[float], n: int) -> list[float | None]:
    """简单移动平均。前 n-1 位为 None。"""
    out: list[float | None] = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values: list[float], n: int) -> list[float]:
    """指数移动平均（首值种子递推，alpha=2/(n+1)，通达信/东财口径）。"""
    alpha = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + alpha * (v - out[-1]))
    return out


def macd(close: list[float]) -> tuple[list[float], list[float], list[float]]:
    """MACD(12,26,9)。返回 (dif, dea, hist)。"""
    e12 = ema(close, 12)
    e26 = ema(close, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    hist = [2.0 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


def rsi(close: list[float], n: int = 14) -> list[float | None]:
    """Wilder RSI。前 n 位为 None。"""
    out: list[float | None] = [None] * len(close)
    if len(close) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        chg = close[i] - close[i - 1]
        gains += max(chg, 0.0)
        losses += max(-chg, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    out[n] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(n + 1, len(close)):
        chg = close[i] - close[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(chg, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-chg, 0.0)) / n
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def swing_lows(low: list[float], left: int = 2, right: int = 2) -> list[int]:
    """摆动低点索引：low[i] 严格低于左右各 left/right 邻位。"""
    out = []
    for i in range(left, len(low) - right):
        window = low[i - left:i + right + 1]
        if low[i] < min(window[:left] + window[left + 1:]):
            out.append(i)
    return out


def ma_state(ma60: list[float | None], i: int, lookback: int = 5,
             eps: float = 0.002) -> str:
    """60 日线单日三态： slope(lookback) 相对阈值 -> '向上'/'走平'/'向下'。"""
    if i < lookback or ma60[i] is None or ma60[i - lookback] is None:
        return '未知'
    base = ma60[i - lookback]
    chg = (ma60[i] - base) / base
    if chg > eps:
        return '向上'
    if chg < -eps:
        return '向下'
    return '走平'


def has_pattern_down_flat_up(states: list[str]) -> bool:
    """序列中是否存在 向下→走平→向上 的先后次序，且当前为'向上'。"""
    if not states or states[-1] != '向上':
        return False
    it = iter(enumerate(states))
    stage = 0  # 0=等向下 1=等走平 2=等向上(非末位)
    for _, s in it:
        if stage == 0 and s == '向下':
            stage = 1
        elif stage == 1 and s == '走平':
            stage = 2
        elif stage == 2 and s == '向上':
            return True
    return False
