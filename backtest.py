"""Backtest causal sur bougies publiques : signaux à la clôture, exécution à l'ouverture suivante.
Les fondamentaux ne sont pas backtestés (pas d'historique gratuit propre des publications)."""
import math
import numpy as np
import pandas as pd
from data import get_candles, MarketDataError
from engine import add_indicators, structure_pass, PIP

PA = {"pa", "pa_dxy"}
RR = {"ema": 1.5, "sweep": 1.5, "pa": 2.0, "pa_dxy": 2.0}
METHOD = {
    "ema": "Croisement EMA20/EMA50, SL 1,2 ATR, TP 1,5R",
    "sweep": "Sweep du dernier swing + tendance EMA, SL 1,2 ATR, TP 1,5R",
    "pa": "Price action : structure haussière/baissière confirmée + sweep du swing + bougie de rejet + killzone, SL derrière la mèche, TP 2R",
    "pa_dxy": "Price action (ci-dessus) + filtre DXY aligné (tendance inverse), TP 2R",
}


def make_signals(df, strategy, dxy=None):
    x = add_indicators(df)
    h, l, c = (x[k].to_numpy(float) for k in ("high", "low", "close"))
    state, lh, ll, _, _, _ = structure_pass(h, l, c, 3)
    x["state"], x["lh"], x["ll"] = state, lh, ll
    up = (x.ema20 > x.ema50) & (x.close > x.ema50)
    dn = (x.ema20 < x.ema50) & (x.close < x.ema50)
    sig = pd.Series(0, index=x.index, dtype=int)
    x["sl_ref"] = np.nan
    if strategy == "ema":
        sig = pd.Series(np.where((x.ema20 > x.ema50) & (x.ema20.shift(1) <= x.ema50.shift(1)), 1,
                        np.where((x.ema20 < x.ema50) & (x.ema20.shift(1) >= x.ema50.shift(1)), -1, 0)), index=x.index)
    else:
        sw_long = (x.low < x.ll) & (x.close > x.ll)
        sw_short = (x.high > x.lh) & (x.close < x.lh)
        if strategy == "sweep":
            sig[sw_long & up] = 1
            sig[sw_short & dn] = -1
        elif strategy in PA:
            rng = (x.high - x.low).replace(0, np.nan)
            lw = (np.minimum(x.open, x.close) - x.low) / rng
            uw = (x.high - np.maximum(x.open, x.close)) / rng
            hr = pd.Series(x.index.hour, index=x.index)
            kz = ((hr >= 7) & (hr < 11)) | ((hr >= 12) & (hr < 16))
            long_ = sw_long & (x.state == 1) & (x.close > x.ema50) & (x.close > x.open) & (lw >= 0.35) & kz
            short_ = sw_short & (x.state == -1) & (x.close < x.ema50) & (x.close < x.open) & (uw >= 0.35) & kz
            if strategy == "pa_dxy" and dxy is not None:
                dx = add_indicators(dxy)[["close", "ema20", "ema50"]].reindex(x.index, method="ffill")
                long_ &= (dx.ema20 < dx.ema50) & (dx.close < dx.ema50)
                short_ &= (dx.ema20 > dx.ema50) & (dx.close > dx.ema50)
            sig[long_] = 1
            sig[short_] = -1
            x["sl_ref"] = np.where(long_, x.low - 0.2 * x.atr, np.where(short_, x.high + 0.2 * x.atr, np.nan))
    x["signal"] = sig
    return x


def run_backtest(days, cost_pips, strategy):
    if days <= 60:
        interval, period, bpd = "15m", "60d", 96
    else:
        interval, period, bpd = "60m", "1y", 24
    raw = get_candles(interval=interval, period=period)
    dxy = None
    if strategy == "pa_dxy":
        try:
            dxy = get_candles(symbol="DX-Y.NYB", interval=interval, period=period)
        except MarketDataError:
            raise MarketDataError("Ce backtest nécessite l'historique DXY, indisponible actuellement.")
    df = make_signals(raw.tail(days * bpd + 300), strategy, dxy).dropna(subset=["atr", "ema20", "ema50"]).reset_index()
    O, H, L, C, A = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr"))
    sig, slref = df.signal.to_numpy(int), df.sl_ref.to_numpy(float)
    rr_t, hold = RR[strategy], (16 if strategy in PA else 12)
    trades, i = [], 1
    while i < len(df) - 1:
        side = int(sig[i])
        if side == 0 or not math.isfinite(A[i]) or A[i] <= 0:
            i += 1
            continue
        entry, atr = O[i + 1], A[i]
        if strategy in PA:
            risk = max(abs(entry - slref[i]) if math.isfinite(slref[i]) else 0, 0.8 * atr, 0.0004)
            if risk > 3 * atr:
                i += 1
                continue
        else:
            risk = 1.2 * atr
        sl, tp = entry - side * risk, entry + side * rr_t * risk
        end = min(i + hold, len(df) - 1)
        out, exit_px, exit_j = None, C[end], end
        for j in range(i + 1, end + 1):
            stop = L[j] <= sl if side == 1 else H[j] >= sl
            tgt = H[j] >= tp if side == 1 else L[j] <= tp
            if stop:                                   # stop prioritaire si les deux sont touchés dans la même bougie
                out, exit_px, exit_j = -1.0, sl, j
                break
            if tgt:
                out, exit_px, exit_j = rr_t, tp, j
                break
        if out is None:
            out = side * (exit_px - entry) / risk
        out -= cost_pips / (risk / PIP)
        trades.append({"side": "LONG" if side == 1 else "SHORT", "entry": round(entry, 5), "exit": round(float(exit_px), 5),
                       "r_multiple": round(float(out), 3), "time": str(df["time"].iloc[i + 1])})
        i = exit_j + 1
    rs = [t["r_multiple"] for t in trades]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r < 0]
    curve = np.cumsum(rs) if rs else np.array([])
    dd = (np.maximum.accumulate(np.insert(curve, 0, 0))[1:] - curve) if rs else np.array([])
    half = len(rs) // 2
    pf = (sum(wins) / abs(sum(losses))) if losses else None
    lim = ["Bougies publiques Yahoo : pas de tick ni de bid/ask historique ; coût constant en pips ; glissement et rollover non modélisés.",
           "Si stop et cible sont touchés dans la même bougie, le stop est compté en premier (hypothèse prudente).",
           "Les fondamentaux, FVG/OB complets et DXY intraday fin ne sont pas tous reproductibles en backtest gratuit.",
           "Optimiser sur ce même historique crée du surajustement : valide en avant (paper trading) avant tout capital réel."]
    if len(rs) < 30:
        lim.insert(0, f"Échantillon faible ({len(rs)} trades) : statistiquement peu fiable.")
    return {"strategy": strategy, "method": METHOD[strategy], "interval": interval, "period_days_requested": days,
            "bars": int(len(df)), "trades": len(rs), "win_rate_pct": round(100 * len(wins) / len(rs), 1) if rs else None,
            "expectancy_R": round(float(np.mean(rs)), 3) if rs else None,
            "profit_factor": round(pf, 2) if pf else ("∞" if wins else None),
            "net_R": round(float(sum(rs)), 2), "max_drawdown_R": round(float(dd.max()), 2) if rs else 0.0,
            "expectancy_first_half": round(float(np.mean(rs[:half])), 3) if half else None,
            "expectancy_second_half": round(float(np.mean(rs[half:])), 3) if half else None,
            "equity_curve": [round(float(v), 2) for v in curve], "cost_assumption_pips": cost_pips,
            "trades_preview": trades[-15:], "limitations": lim}
