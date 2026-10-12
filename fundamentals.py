"""Partie fondamentale 100 % gratuite et sans clé :
- Écart de rendements États-Unis / zone euro (FRED + BCE)
- Taux directeurs Fed / BCE (FRED)
- Positionnement des fonds spéculatifs sur l'euro (CFTC, COT hebdomadaire)
- Calendrier économique (Forex Factory, flux JSON public) + blackout news
- Tendance journalière du DXY (via les bougies Yahoo fournies)
Chaque brique échoue proprement : une source en panne ne bloque jamais l'analyse."""
import io, re, time, threading
from datetime import datetime, timezone
import requests
import pandas as pd
from engine import dxy_trend

H = {"User-Agent": "Mozilla/5.0 (compatible; EURUSD-Engine/2.0)"}
_cache, _lock = {}, threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _lock:
        item = _cache.get(key)
    if item and now - item[0] < ttl:
        return item[1]
    try:
        val = fn()
    except Exception as exc:                       # source publique capricieuse : on dégrade proprement
        if item:
            return {**item[1], "stale": True}
        return {"available": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    with _lock:
        _cache[key] = (now, val)
    return val


def fred(series, tail=60):
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series}, headers=H, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(df.iloc[:, 0]))
    return s.dropna().tail(tail)


def ecb_yield(tenor):
    url = f"https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor}"
    r = requests.get(url, params={"lastNObservations": 60, "format": "csvdata"}, headers=H, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return pd.Series(pd.to_numeric(df["OBS_VALUE"], errors="coerce").values, index=pd.to_datetime(df["TIME_PERIOD"])).dropna()


def _step(bp):
    return 2 if bp >= 10 else 1 if bp >= 4 else -2 if bp <= -10 else -1 if bp <= -4 else 0


def _yields():
    out, scores = {}, []
    for tenor, us_id, w in (("2Y", "DGS2", 0.7), ("10Y", "DGS10", 0.3)):
        us, eu = fred(us_id), ecb_yield(tenor)
        j = pd.concat([eu.rename("eu"), us.rename("us")], axis=1).dropna()
        spread = (j.eu - j.us) * 100                      # points de base ; hausse = favorable à l'EUR
        d5 = float(spread.iloc[-1] - spread.iloc[-6])
        out[tenor] = {"us": round(float(j.us.iloc[-1]), 3), "ea": round(float(j.eu.iloc[-1]), 3),
                      "spread_bp": round(float(spread.iloc[-1]), 1), "change_5d_bp": round(d5, 1),
                      "date": str(j.index[-1].date())}
        scores.append(w * _step(d5))
    score = max(-2.0, min(2.0, sum(scores)))
    return {"available": True, "data": out, "score": round(score, 2),
            "note": "Écart zone euro (courbe AAA BCE) − US Treasuries. Un écart qui monte est favorable à l'EUR."}


def _policy():
    fed, ecb = fred("DFEDTARU", 10), fred("ECBDFR", 10)
    return {"available": True, "fed_upper": float(fed.iloc[-1]), "ecb_deposit": float(ecb.iloc[-1]),
            "diff_bp": round((float(ecb.iloc[-1]) - float(fed.iloc[-1])) * 100)}


def _cot():
    r = requests.get("https://publicreporting.cftc.gov/resource/6dca-aqww.json",
                     params={"cftc_contract_market_code": "099741", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "8"},
                     headers=H, timeout=20)
    r.raise_for_status()
    rows = r.json()
    net = [int(float(x["noncomm_positions_long_all"])) - int(float(x["noncomm_positions_short_all"])) for x in rows]
    if len(net) < 4:
        raise ValueError("COT incomplet")
    score = 1 if net[0] > net[1] and net[0] > net[3] else -1 if net[0] < net[1] and net[0] < net[3] else 0
    return {"available": True, "net_noncommercial": net[0], "change_1w": net[0] - net[1], "change_3w": net[0] - net[3],
            "report_date": rows[0]["report_date_as_yyyy_mm_dd"][:10], "score": score,
            "note": "Positions nettes des spéculateurs sur futures EUR (CME). Donnée hebdomadaire, publiée avec retard."}


def _num(s):
    if s is None:
        return None
    m = re.search(r"-?\d+(\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


def _calendar(now):
    r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=H, timeout=15)
    r.raise_for_status()
    ev = []
    for e in r.json():
        if e.get("country") not in ("USD", "EUR") or e.get("impact") not in ("High", "Medium"):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        ev.append({"title": e.get("title", ""), "country": e["country"], "impact": e["impact"], "time": t.isoformat(),
                   "forecast": e.get("forecast") or "", "previous": e.get("previous") or "", "_t": t})
    return {"available": True, "events": ev}


def get_calendar(now):
    cal = cached("calendar", 600, lambda: _calendar(now))
    if not cal.get("available"):
        return {"available": False, "error": cal.get("error"), "events": [], "blackout": {"active": False}}
    evs = []
    for e in cal["events"]:
        mins = (e["_t"] - now).total_seconds() / 60
        evs.append({**{k: v for k, v in e.items() if k != "_t"}, "minutes_to": round(mins)})
    evs.sort(key=lambda z: z["minutes_to"])
    upcoming = [e for e in evs if e["minutes_to"] > -60][:10]
    hot = [e for e in evs if e["impact"] == "High" and -30 <= e["minutes_to"] <= 15]
    blackout = {"active": bool(hot), "event": hot[0]["title"] + f" ({hot[0]['country']})" if hot else None,
                "minutes_to": hot[0]["minutes_to"] if hot else None}
    return {"available": True, "events": upcoming, "blackout": blackout, "stale": cal.get("stale", False)}


def get_fundamentals(dxy_daily=None, now=None):
    now = now or datetime.now(timezone.utc)
    y = cached("yields", 3600, _yields)
    p = cached("policy", 6 * 3600, _policy)
    c = cached("cot", 6 * 3600, _cot)
    cal = get_calendar(now)
    dx = dxy_trend(dxy_daily) if dxy_daily is not None else 0
    comps = []
    if y.get("available"):
        d = y["data"]
        comps.append({"name": "Écart de rendements EUR−USD", "score": y["score"], "max": 2,
                      "detail": f"2 ans : {d['2Y']['spread_bp']:+.0f} pb ({d['2Y']['change_5d_bp']:+.0f} pb sur 5 j) · "
                                f"10 ans : {d['10Y']['spread_bp']:+.0f} pb ({d['10Y']['change_5d_bp']:+.0f} pb sur 5 j)"})
    else:
        comps.append({"name": "Écart de rendements EUR−USD", "score": 0, "max": 2, "detail": "Indisponible : " + str(y.get("error"))})
    if c.get("available"):
        comps.append({"name": "Positionnement COT (euro)", "score": c["score"], "max": 1,
                      "detail": f"Net {c['net_noncommercial']:+,} contrats ({c['change_1w']:+,} sur 1 sem.) · rapport du {c['report_date']}".replace(",", " ")})
    else:
        comps.append({"name": "Positionnement COT (euro)", "score": 0, "max": 1, "detail": "Indisponible : " + str(c.get("error"))})
    comps.append({"name": "Dollar (DXY) journalier", "score": -dx, "max": 1,
                  "detail": {1: "DXY haussier (pression sur EUR/USD)", -1: "DXY baissier (soutien à EUR/USD)", 0: "DXY neutre ou indisponible"}[dx]})
    score = round(sum(x["score"] for x in comps), 2)
    label = "EUR favorisé" if score >= 1.5 else "USD favorisé" if score <= -1.5 else "Neutre / mitigé"
    return {"available": True, "score": score, "max": 4, "bias_label": label, "components": comps,
            "policy": p, "calendar": cal, "blackout": cal.get("blackout", {"active": False}),
            "yields": y if y.get("available") else None, "cot": c if c.get("available") else None,
            "generated_at": now.isoformat(),
            "disclaimer": "Les fondamentaux donnent un contexte directionnel de fond, pas un timing d'entrée. "
                          "Le calendrier gratuit ne fournit pas les chiffres publiés (« actual »)."}
