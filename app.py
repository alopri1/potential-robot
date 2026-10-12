"""EURUSD Engine Pro v2 — outil de recherche price action + fondamentaux (sources gratuites).
Ce n'est ni un robot de trading ni un conseil financier."""
import os, time, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import requests
from flask import Flask, jsonify, render_template, request

import data
from data import MarketDataError
import engine, fundamentals
from backtest import run_backtest

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE, "templates"), static_folder=os.path.join(BASE, "static"))
REQUIRED = ["app.py", "engine.py", "data.py", "backtest.py", "fundamentals.py", "templates/index.html", "static/app.js", "static/style.css"]


def missing_files():
    return [f for f in REQUIRED if not os.path.exists(os.path.join(BASE, f))]
APP_NAME = "EURUSD Engine Pro"
_res_cache, _res_lock = {"t": 0, "v": None}, threading.Lock()
_last_alert = {"key": None, "t": 0}

REQUESTS = {"60m": ("EURUSD=X", "60m", None), "15m": ("EURUSD=X", "15m", None), "5m": ("EURUSD=X", "5m", None),
            "1m": ("EURUSD=X", "1m", None), "1d": ("EURUSD=X", "1d", None),
            "DXY_60m": ("DX-Y.NYB", "60m", None), "DXY_15m": ("DX-Y.NYB", "15m", None), "DXY_1d": ("DX-Y.NYB", "1d", None)}


def load_frames():
    frames, errors = {}, {}

    def load(k, spec):
        return k, data.get_candles(*spec)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(load, k, v): k for k, v in REQUESTS.items()}
        for f, k in futs.items():
            try:
                frames[k] = f.result()[1]
            except Exception as exc:
                errors[k] = str(exc)
    if not all(k in frames for k in ("60m", "15m", "5m")):
        raise MarketDataError("Historique EUR/USD incomplet (H1/M15/M5). Source publique indisponible, réessaie dans quelques minutes.")
    return frames, errors


def build_analysis():
    with _res_lock:
        if _res_cache["v"] and time.time() - _res_cache["t"] < 25:
            return _res_cache["v"]
    now = datetime.now(timezone.utc)
    frames, errors = load_frames()
    fund = fundamentals.get_fundamentals(frames.get("DXY_1d"), now)
    res = engine.analyze(frames, fund, now)
    res.update({"app": APP_NAME, "symbol": "EUR/USD", "generated_at": now.isoformat(), "fundamentals": fund,
                "data_warnings": errors,
                "source": "Yahoo Finance (bougies), FRED/BCE (rendements, taux), CFTC (COT), Forex Factory (calendrier)",
                "warnings": [
                    "Outil de recherche : aucun avantage statistique n'est garanti et aucun ordre n'est exécuté.",
                    "Score de confluence ≠ probabilité de gain. Valide en paper trading avant tout capital réel.",
                    "Yahoo est un flux public non officiel, parfois retardé ; spread et slippage réels du courtier non inclus.",
                    "Volume FX centralisé inexistant : pas de VWAP/POC de volume réel (volontairement non simulé).",
                    "FVG, OB, sweeps, BOS/CHoCH sont des définitions algorithmiques ; vérifie visuellement sur ton graphique."]})
    with _res_lock:
        _res_cache.update(t=time.time(), v=res)
    return res


def telegram(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text}, timeout=10)
        return r.ok
    except requests.RequestException:
        return False


@app.route("/")
def home():
    miss = missing_files()
    if miss:
        return ("<h2>Fichiers manquants dans le dépôt GitHub</h2><p>Il manque : <b>" + ", ".join(miss) +
                "</b></p><p>Ces fichiers doivent être à la racine du dépôt (pas dans un sous-dossier).</p>"), 500
    return render_template("index.html", app_name=APP_NAME)


@app.errorhandler(Exception)
def on_error(exc):
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return exc
    app.logger.exception("Unhandled error")
    return jsonify({"error": f"{type(exc).__name__}: {str(exc)[:200]}"}), 500


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "app": APP_NAME, "utc": datetime.now(timezone.utc).isoformat(),
                    "missing_files": missing_files(), "twelvedata_fallback": bool(data.TD_KEY), "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN"))})


@app.route("/api/analysis")
def api_analysis():
    try:
        return jsonify(build_analysis())
    except MarketDataError as exc:
        return jsonify({"error": str(exc), "status": "data_unavailable"}), 503
    except Exception:
        app.logger.exception("Analysis failed")
        return jsonify({"error": "Erreur interne pendant l'analyse. Consulte les logs Render."}), 500


@app.route("/api/fundamentals")
def api_fund():
    return jsonify(fundamentals.get_fundamentals(None))


@app.route("/api/cron")
def cron():
    """À appeler toutes les 5 min (cron-job.org) : garde Render éveillé et envoie l'alerte Telegram sur nouveau signal."""
    tok = os.getenv("CRON_TOKEN")
    if tok and request.args.get("token") != tok:
        return jsonify({"error": "token invalide"}), 403
    try:
        r = build_analysis()
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 503
    sent = False
    if r["signal"] in ("LONG", "SHORT"):
        key = f"{r['signal']}:{round(r['price'], 3)}"
        if key != _last_alert["key"] or time.time() - _last_alert["t"] > 3600:
            p = r["plan"]
            sent = telegram(f"{'🟢' if r['signal'] == 'LONG' else '🔴'} EUR/USD {r['signal']} (score {r['score']:+.1f})\n"
                            f"Entrée ~{p['entry']:.5f} · SL {p['sl']:.5f}\nTP1 {p['tp1']:.5f} · TP2 {p['tp2']:.5f} (R:R {p['rr']})\n"
                            f"{r['session']['name']} · Outil d'aide, pas un conseil financier.")
            _last_alert.update(key=key, t=time.time())
    return jsonify({"status": "ok", "signal": r["signal"], "score": r["score"], "telegram_sent": sent})


@app.route("/api/backtest")
def backtest():
    try:
        days = min(max(int(request.args.get("days", "30")), 5), 365)
        cost = min(max(float(request.args.get("cost_pips", "1.2")), 0), 10)
        strat = request.args.get("strategy", "pa").strip().lower()
        if strat not in {"ema", "sweep", "pa", "pa_dxy"}:
            return jsonify({"error": "Stratégie invalide (ema, sweep, pa, pa_dxy)."}), 400
        return jsonify(run_backtest(days, cost, strat))
    except MarketDataError as exc:
        return jsonify({"error": str(exc), "status": "data_unavailable"}), 503
    except (ValueError, OverflowError) as exc:
        return jsonify({"error": f"Paramètre invalide : {exc}"}), 400
    except Exception:
        app.logger.exception("Backtest failed")
        return jsonify({"error": "Backtest impossible actuellement; consulte les logs."}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
