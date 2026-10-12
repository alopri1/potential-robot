"""Moteur price action EUR/USD : structure (BOS/CHoCH), liquidité, sweeps, FVG, OB, premium/discount,
sessions, plan de trade. Outil de recherche, pas un conseil financier."""
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

PIP = 0.0001
LABEL = {1: "HAUSSIÈRE", -1: "BAISSIÈRE", 0: "NEUTRE"}
THRESHOLD = 7.0


# ───────────────────────── Indicateurs & structure ─────────────────────────
def add_indicators(df):
    x = df.copy()
    for p in (20, 50, 200):
        x[f"ema{p}"] = x.close.ewm(span=p, adjust=False).mean()
    pc = x.close.shift(1)
    tr = pd.concat([x.high - x.low, (x.high - pc).abs(), (x.low - pc).abs()], axis=1).max(axis=1)
    x["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return x


def structure_pass(h, l, c, n=3):
    """Passe causale : pivots confirmés n bougies après leur formation, puis BOS / CHoCH sur clôture.
    Retourne state[], dernier swing high[], dernier swing low[], événements, pivots."""
    N = len(c)
    state = np.zeros(N, dtype=int)
    lh = np.full(N, np.nan)
    ll = np.full(N, np.nan)
    ev, ph, pl = [], [], []
    last_h = last_l = np.nan
    h_broken = l_broken = True
    st = 0
    for i in range(N):
        p = i - n
        if p >= n:
            if h[p] >= h[p - n:p + n + 1].max() and h[p] > h[p - n:p].max():
                ph.append((p, float(h[p]))); last_h = h[p]; h_broken = False
            if l[p] <= l[p - n:p + n + 1].min() and l[p] < l[p - n:p].min():
                pl.append((p, float(l[p]))); last_l = l[p]; l_broken = False
        if not h_broken and c[i] > last_h:
            ev.append((i, "CHoCH" if st == -1 else "BOS", 1, float(last_h))); st = 1; h_broken = True
        elif not l_broken and c[i] < last_l:
            ev.append((i, "CHoCH" if st == 1 else "BOS", -1, float(last_l))); st = -1; l_broken = True
        state[i] = st; lh[i] = last_h; ll[i] = last_l
    return state, lh, ll, ev, ph, pl


# ───────────────────────── Zones & liquidité ─────────────────────────
def find_fvgs(df, atr_arr, lookback=80):
    h, l = df.high.to_numpy(), df.low.to_numpy()
    N, out = len(df), []
    for i in range(max(2, N - lookback), N):
        a = atr_arr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= 0.1 * a:
            kind, lo, hi = "bull", float(h[i - 2]), float(l[i])
            after = l[i + 1:]
            filled = after.size > 0 and after.min() <= lo
            touched = after.size > 0 and after.min() < hi
        elif h[i] < l[i - 2] and (l[i - 2] - h[i]) >= 0.1 * a:
            kind, lo, hi = "bear", float(h[i]), float(l[i - 2])
            after = h[i + 1:]
            filled = after.size > 0 and after.max() >= hi
            touched = after.size > 0 and after.max() > lo
        else:
            continue
        if not filled:
            out.append({"type": kind, "low": lo, "high": hi, "time": df.index[i].isoformat(),
                        "touched": bool(touched), "age": int(N - 1 - i)})
    return out


def find_obs(df, atr_arr, lookback=60):
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    N, out = len(df), []
    for i in range(max(11, N - lookback), N):
        a = atr_arr[i]
        if not np.isfinite(a) or a <= 0 or abs(c[i] - o[i]) < 1.3 * a:
            continue
        b = i - 1
        after = c[i + 1:]
        if c[i] > o[i] and c[b] < o[b] and c[i] > h[i - 10:i].max():
            kind, lo, hi = "bull", float(l[b]), float(max(o[b], c[b]))
            mitig = after.size > 0 and after.min() < lo
        elif c[i] < o[i] and c[b] > o[b] and c[i] < l[i - 10:i].min():
            kind, lo, hi = "bear", float(min(o[b], c[b])), float(h[b])
            mitig = after.size > 0 and after.max() > hi
        else:
            continue
        if not mitig:
            out.append({"type": kind, "low": lo, "high": hi, "time": df.index[b].isoformat(), "age": int(N - 1 - b)})
    return out


def liquidity_pools(ph, pl, h, l, atr, max_pivots=14):
    """Equal highs / equal lows non balayés (liquidité visible)."""
    tol = 0.25 * atr
    pools = []
    for side, piv in (("high", ph[-max_pivots:]), ("low", pl[-max_pivots:])):
        used = set()
        for a in range(len(piv)):
            if a in used:
                continue
            grp = [a] + [b for b in range(a + 1, len(piv)) if abs(piv[b][1] - piv[a][1]) <= tol]
            if len(grp) < 2:
                continue
            used.update(grp)
            lvl = float(np.mean([piv[g][1] for g in grp]))
            last_i = max(piv[g][0] for g in grp)
            rest = h[last_i + 1:] if side == "high" else l[last_i + 1:]
            swept = rest.size > 0 and ((rest.max() > lvl + tol * 0.4) if side == "high" else (rest.min() < lvl - tol * 0.4))
            if not swept:
                pools.append({"side": side, "price": lvl, "touches": len(grp), "weight": 3,
                              "label": "Equal highs" if side == "high" else "Equal lows"})
    return pools


def candle_pattern(df):
    if len(df) < 2:
        return 0, "—"
    p, q = df.iloc[-2], df.iloc[-1]
    rng = float(q.high - q.low)
    if rng <= 0:
        return 0, "—"
    body = abs(q.close - q.open)
    lw = min(q.open, q.close) - q.low
    uw = q.high - max(q.open, q.close)
    if p.close < p.open and q.close > q.open and q.close >= p.open and q.open <= p.close:
        return 1, "Engulfing haussier"
    if p.close > p.open and q.close < q.open and q.close <= p.open and q.open >= p.close:
        return -1, "Engulfing baissier"
    if lw >= 2 * max(body, rng * 0.05) and lw >= 0.6 * rng:
        return 1, "Pin bar haussier (rejet bas)"
    if uw >= 2 * max(body, rng * 0.05) and uw >= 0.6 * rng:
        return -1, "Pin bar baissier (rejet haut)"
    return 0, "—"


# ───────────────────────── Sessions & niveaux de référence ─────────────────────────
def session_info(now):
    h, wd = now.hour + now.minute / 60, now.weekday()
    closed = wd == 5 or (wd == 4 and h >= 22) or (wd == 6 and h < 22)
    if 7 <= h < 11:
        name, kz = "Londres (killzone)", True
    elif 12 <= h < 16:
        name, kz = "New York (killzone)", True
    elif h < 7:
        name, kz = "Asie", False
    else:
        name, kz = "Hors killzone", False
    return {"name": "Marché fermé" if closed else name, "killzone": kz and not closed, "closed": closed}


def asia_levels(df5, now):
    day = now.date() if now.hour >= 7 else (now - timedelta(days=1)).date()
    idx = df5.index
    sub = df5[(idx.date == day) & (idx.hour < 7)]
    if len(sub) < 6:
        return None
    return float(sub.high.max()), float(sub.low.min())


def daily_levels(daily, now):
    if daily is None or len(daily) < 3:
        return []
    done = daily.iloc[:-1] if daily.index[-1].date() == now.date() else daily
    if len(done) < 1:
        return []
    pd_ = done.iloc[-1]
    w = done.tail(5)
    return [("Haut de la veille", float(pd_.high), "high"), ("Bas de la veille", float(pd_.low), "low"),
            ("Haut 5 jours", float(w.high.max()), "high"), ("Bas 5 jours", float(w.low.min()), "low")]


def dxy_trend(df):
    if df is None or len(df) < 60:
        return 0
    x = add_indicators(df).iloc[-1]
    return 1 if x.ema20 > x.ema50 and x.close > x.ema50 else -1 if x.ema20 < x.ema50 and x.close < x.ema50 else 0


# ───────────────────────── Analyse par unité de temps ─────────────────────────
def tf_analysis(raw, n=3):
    df = add_indicators(raw)
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    atr_arr = df.atr.to_numpy(float)
    atr = float(atr_arr[-1]) if np.isfinite(atr_arr[-1]) else float((df.high - df.low).tail(14).mean())
    state, lh, ll, ev, ph, pl = structure_pass(h, l, c, n)
    last, st, price = df.iloc[-1], int(state[-1]), float(c[-1])
    ema_b = 1 if last.ema20 > last.ema50 and price > last.ema50 else -1 if last.ema20 < last.ema50 and price < last.ema50 else 0
    bias = float(st) if (st != 0 and st == ema_b) else st * 0.5 if (st != 0 and ema_b == 0) else ema_b * 0.5 if st == 0 else 0.0
    N = len(df)
    rng_hi, rng_lo = lh[-1], ll[-1]
    pos = float((price - rng_lo) / (rng_hi - rng_lo)) if np.isfinite(rng_hi) and np.isfinite(rng_lo) and rng_hi > rng_lo else None
    pat, pat_name = candle_pattern(df)
    return {
        "df": df, "atr": atr, "state": st, "bias": bias, "price": price, "time": df.index[-1].isoformat(),
        "ema20": float(last.ema20), "ema50": float(last.ema50),
        "ema200": float(last.ema200) if pd.notna(last.ema200) else None,
        "swing_high": float(rng_hi) if np.isfinite(rng_hi) else None,
        "swing_low": float(rng_lo) if np.isfinite(rng_lo) else None, "pos": pos,
        "events": [{"kind": k, "dir": d, "level": lv, "bars_ago": int(N - 1 - i)} for i, k, d, lv in ev[-3:]],
        "pools": liquidity_pools(ph, pl, h, l, atr),
        "swings": {"high": ph[-2:], "low": pl[-2:]},
        "fvgs": find_fvgs(df, atr_arr), "obs": find_obs(df, atr_arr),
        "pattern": pat, "pattern_name": pat_name,
    }


def detect_sweep(df, levels, lookback):
    """Mèche au-delà d'un niveau de liquidité + clôture de réintégration, sans nouvelle clôture au-delà ensuite."""
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    N, best = len(df), None
    for k in range(max(1, N - lookback), N):
        for lv in levels:
            if k <= lv.get("idx", -1):
                continue
            p = lv["price"]
            if lv["side"] == "low" and l[k] < p < c[k] and c[k:].min() > p:
                cand = {"dir": 1, "level": p, "label": lv["label"], "weight": lv["weight"],
                        "extreme": float(l[k:].min()), "bars_ago": int(N - 1 - k)}
            elif lv["side"] == "high" and h[k] > p > c[k] and c[k:].max() < p:
                cand = {"dir": -1, "level": p, "label": lv["label"], "weight": lv["weight"],
                        "extreme": float(h[k:].max()), "bars_ago": int(N - 1 - k)}
            else:
                continue
            if best is None or (cand["bars_ago"], -cand["weight"]) < (best["bars_ago"], -best["weight"]):
                best = cand
    return best


def _public_tf(t, key):
    ev = t["events"][-1] if t["events"] else None
    return {"trend": LABEL[t["state"]], "bias": t["bias"], "price": t["price"], "time": t["time"],
            "ema20": t["ema20"], "ema50": t["ema50"], "ema200": t["ema200"], "atr": t["atr"],
            "atr_pips": t["atr"] / PIP, "premium_pct": None if t["pos"] is None else round(t["pos"] * 100),
            "last_event": ev, "pattern": t["pattern_name"], "swing_high": t["swing_high"], "swing_low": t["swing_low"]}


# ───────────────────────── Signal complet ─────────────────────────
def analyze(frames, fund=None, now=None):
    now = now or datetime.now(timezone.utc)
    fund = fund or {}
    T = {"60m": tf_analysis(frames["60m"]), "15m": tf_analysis(frames["15m"]), "5m": tf_analysis(frames["5m"])}
    if frames.get("1m") is not None:
        trig, trig_name = tf_analysis(frames["1m"], 2), "M1"
    else:
        trig, trig_name = T["5m"], "M5 (repli, M1 indisponible)"
    h1, m15, m5 = T["60m"], T["15m"], T["5m"]
    price, atr5 = trig["price"], max(m5["atr"], 0.00008)
    sess = session_info(now)

    # Niveaux de référence (liquidité externe)
    refs = []
    asia = asia_levels(frames["5m"], now)
    if asia:
        refs += [("Haut Asie", asia[0], "high"), ("Bas Asie", asia[1], "low")]
    refs += daily_levels(frames.get("1d"), now)
    ref_levels = [{"side": k, "price": p, "label": nm, "weight": 2, "idx": -1} for nm, p, k in refs]

    def levels_for(key):
        own = T[key]
        out = [dict(p, idx=-1) for tf in ("5m", "15m") for p in T[tf]["pools"]] + ref_levels
        out += [{"side": "high", "price": p, "label": "Swing high", "weight": 1, "idx": i} for i, p in own["swings"]["high"]]
        out += [{"side": "low", "price": p, "label": "Swing low", "weight": 1, "idx": i} for i, p in own["swings"]["low"]]
        return out

    sweep = detect_sweep(m5["df"], levels_for("5m"), 8)
    sw15 = detect_sweep(m15["df"], levels_for("15m"), 3)
    if sweep is None and sw15 is not None:
        sweep = dict(sw15, label=sw15["label"] + " (M15)")
    elif sweep is not None:
        sweep["label"] += " (M5)"

    # ── Score technique
    reasons, tech = [], 0.0

    def add(val, text):
        nonlocal tech
        if val:
            tech += val
            reasons.append(f"{'+' if val > 0 else ''}{val:g} · {text}")

    for key, name, w in (("60m", "H1", 3), ("15m", "M15", 2), ("5m", "M5", 1)):
        t = T[key]
        ev = t["events"][-1] if t["events"] else None
        ev_txt = f" (dernier {ev['kind']} {'haussier' if ev['dir'] == 1 else 'baissier'} il y a {ev['bars_ago']} bougies)" if ev else ""
        add(round(w * t["bias"], 2), f"{name} structure {LABEL[t['state']].lower()}{ev_txt}")
    if sweep:
        side = "plus bas" if sweep["dir"] == 1 else "plus haut"
        add(sweep["dir"] * (2 + (1 if sweep["weight"] >= 2 else 0)),
            f"Sweep des {side} : {sweep['label']} à {sweep['level']:.5f}, réintégration il y a {sweep['bars_ago']} bougies")
    zones_in, zdir = [], 0
    for key in ("5m", "15m"):
        for kind, arr in (("FVG", T[key]["fvgs"]), ("OB", T[key]["obs"])):
            for z in arr:
                tol = 0.1 * T[key]["atr"]
                if z["low"] - tol <= price <= z["high"] + tol:
                    d = 1 if z["type"] == "bull" else -1
                    zones_in.append(f"{kind} {'haussier' if d == 1 else 'baissier'} {key.upper().replace('60M', 'H1')}")
                    zdir += d
    if zdir:
        add(1.5 if zdir > 0 else -1.5, "Prix dans une zone non mitigée : " + ", ".join(sorted(set(zones_in))))
    if m15["pos"] is not None:
        if m15["pos"] <= 0.4:
            add(1, f"Prix en zone discount du range M15 ({m15['pos'] * 100:.0f} %)")
        elif m15["pos"] >= 0.6:
            add(-1, f"Prix en zone premium du range M15 ({m15['pos'] * 100:.0f} %)")
    if m5["pattern"]:
        add(m5["pattern"], f"M5 : {m5['pattern_name']}")
    d60, d15 = dxy_trend(frames.get("DXY_60m")), dxy_trend(frames.get("DXY_15m"))
    if d60 and d60 == d15:
        add(-d60, f"DXY {'haussier' if d60 == 1 else 'baissier'} sur H1 et M15 (corrélation inverse)")

    fscore = float(fund.get("score", 0) or 0)
    if fscore:
        reasons.append(f"{fscore * 0.75:+.2f} · Fondamentaux ({fund.get('bias_label', '')}, score {fscore:+.1f}/4 pondéré ×0,75)")
    total = tech + 0.75 * fscore
    thr = THRESHOLD if sess["killzone"] else THRESHOLD + 2
    d = 1 if total >= thr else -1 if total <= -thr else 0
    lean = 1 if total >= thr - 2 else -1 if total <= -(thr - 2) else 0

    # ── Déclencheur & garde-fous
    want = d or lean
    ev = [e for e in trig["events"] if e["dir"] == want and e["bars_ago"] <= 15] if want else []
    trigger = ev[-1] if ev else None
    blackout = (fund.get("blackout") or {})
    checks = [
        {"label": "Marché ouvert", "ok": not sess["closed"]},
        {"label": f"Biais H1/M15 compatible", "ok": bool(want) and h1["state"] != -want and m15["state"] != -want},
        {"label": "Liquidité balayée ou zone active", "ok": bool(want) and ((sweep and sweep["dir"] == want) or (zdir * want > 0))},
        {"label": f"Déclencheur {trig_name} (CHoCH/BOS)", "ok": trigger is not None},
        {"label": "Killzone (Londres / New York)", "ok": sess["killzone"]},
        {"label": "Pas de news majeure imminente", "ok": not blackout.get("active")},
        {"label": "Fondamentaux non contraires", "ok": not (want and fscore * want <= -2)},
    ]
    blockers = [c["label"] for c in checks if not c["ok"] and c["label"] != "Killzone (Londres / New York)"]
    if not d:
        blockers.insert(0, f"Score {total:+.1f} sous le seuil ±{thr:g}")
    direction = d if (d and not blockers) else 0

    # ── Plan de trade
    plan = {"entry": None, "sl": None, "tp1": None, "tp2": None, "rr": None}
    if direction:
        if sweep and sweep["dir"] == direction:
            sl = sweep["extreme"] - direction * 0.3 * atr5
        else:
            sl = (m5["swing_low"] - 0.3 * atr5) if direction == 1 else (m5["swing_high"] + 0.3 * atr5)
        risk = abs(price - sl)
        risk = max(risk, 0.8 * atr5, 0.0004)
        sl = price - direction * risk
        if risk > 3 * atr5:
            blockers.append("Stop trop large (> 3 ATR M5) : entrée tardive")
        else:
            pools = [lv for lv in levels_for("5m") + levels_for("15m")
                     if (lv["side"] == ("high" if direction == 1 else "low")) and (lv["price"] - price) * direction > 0]
            pools.sort(key=lambda z: abs(z["price"] - price))
            tp2, tgt = price + direction * 2 * risk, "2R par défaut"
            if pools:
                nxt = pools[0]
                if abs(nxt["price"] - price) / risk < 1.5:
                    blockers.append(f"Pas d'espace : {nxt['label']} à {nxt['price']:.5f} trop proche (< 1,5R)")
                else:
                    tp2, tgt = nxt["price"], f"liquidité {nxt['label']}"
            if not blockers:
                plan = {"entry": price, "sl": sl, "tp1": price + direction * risk, "tp2": tp2,
                        "rr": round(abs(tp2 - price) / risk, 2), "target_note": tgt, "risk_pips": round(risk / PIP, 1)}
        if blockers:
            direction = 0
    signal = "LONG" if direction == 1 else "SHORT" if direction == -1 else "WAIT"
    state_txt = ("SIGNAL ACTIF" if direction else
                 f"EN CONSTRUCTION ({'long' if lean == 1 else 'short'})" if lean else "AUCUN SETUP")

    # ── Données d'affichage
    zones = []
    for key in ("15m", "5m"):
        nm = "M15" if key == "15m" else "M5"
        for kind, arr in (("FVG", T[key]["fvgs"]), ("OB", T[key]["obs"])):
            for z in arr[-4:]:
                zones.append({"name": f"{kind} {'haussier' if z['type'] == 'bull' else 'baissier'} · {nm}", "low": z["low"],
                              "high": z["high"], "inside": z["low"] <= price <= z["high"], "age": z["age"]})
    zones.sort(key=lambda z: abs((z["low"] + z["high"]) / 2 - price))
    levels = [{"name": nm, "price": p, "kind": k} for nm, p, k in refs]
    for tf, nm in (("5m", "M5"), ("15m", "M15")):
        levels += [{"name": f"{p['label']} ×{p['touches']} · {nm}", "price": p["price"], "kind": p["side"]} for p in T[tf]["pools"]]
    levels.sort(key=lambda z: abs(z["price"] - price))
    timeframes = {k: _public_tf(v, k) for k, v in T.items()}
    timeframes["trigger"] = {"name": trig_name, "event": trigger, "state": LABEL[trig["state"]]}
    return {
        "signal": signal, "state_text": state_txt, "score": round(total, 2), "tech_score": round(tech, 2),
        "fund_score": fscore, "threshold": thr, "price": price, "time": trig["time"], "session": sess,
        "reasons": reasons, "checks": checks, "blockers": blockers, "plan": plan,
        "sweep": sweep, "timeframes": timeframes, "zones": zones[:10], "levels": levels[:12],
        "dxy": {"h1": LABEL[d60], "m15": LABEL[d15]}, "atr_m5_pips": round(atr5 / PIP, 1),
    }
