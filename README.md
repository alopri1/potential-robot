---
title: EURUSD Engine Pro
emoji: 📈
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# EURUSD Engine Pro v2

Tableau de bord de **recherche** EUR/USD : price action (structure BOS/CHoCH, liquidité, sweeps, FVG, Order Blocks, premium/discount, killzones) + fondamentaux gratuits (écart de rendements US/zone euro, COT, calendrier, DXY). Aucun ordre n'est exécuté. Ce n'est pas un conseil financier.

## Sources 100 % gratuites
| Donnée | Source | Clé |
|---|---|---|
| Bougies EUR/USD, DXY | Yahoo Finance (public) | non |
| Secours bougies (optionnel) | Twelve Data (plan gratuit) | `TWELVEDATA_KEY` |
| Rendements US, taux Fed | FRED (CSV public) | non |
| Courbe des taux zone euro, taux BCE | BCE Data Portal + FRED | non |
| Positionnement COT euro | CFTC (API publique) | non |
| Calendrier économique | Forex Factory (JSON public) | non |

## Déploiement Render
1. Mets **tout le contenu du dossier** à la racine d'un dépôt GitHub.
2. Render → **New + → Blueprint** → choisis le dépôt. `render.yaml` fait le reste.
3. Teste `/api/health`, puis `/`.

## Alertes Telegram + garder Render éveillé (optionnel)
1. Crée un bot avec @BotFather → `TELEGRAM_BOT_TOKEN`. Récupère ton `TELEGRAM_CHAT_ID` (via @userinfobot).
2. Ajoute ces variables dans Render, plus `CRON_TOKEN` (mot de passe de ton choix).
3. Sur cron-job.org (gratuit), crée un appel toutes les 5 min vers `https://TON-APP.onrender.com/api/cron?token=TON_TOKEN`.
   Cela empêche la mise en veille et envoie une alerte à chaque nouveau signal.

## API
`/api/analysis` · `/api/fundamentals` · `/api/backtest?days=30&cost_pips=1.2&strategy=pa` (`pa`, `pa_dxy`, `sweep`, `ema`) · `/api/cron` · `/api/health`

## Logique du signal (résumé)
- **Top-down** : H1 (±3), M15 (±2), M5 (±1) pondérés par structure + EMA.
- **Liquidité** : sweep d'equal highs/lows, Asie, veille, 5 jours, swings (+2 à +3).
- **Zones** : prix dans un FVG/OB non mitigé (±1,5) · discount/premium M15 (±1) · rejet M5 (±1) · DXY (±1).
- **Fondamental** : rendements (±2), COT (±1), DXY journalier (±1), pondéré ×0,75.
- **Signal** si score ≥ 7 (≥ 9 hors killzone) **et** déclencheur CHoCH/BOS M1, H1/M15 non opposés, pas de news, fondamentaux non contraires, stop ≤ 3 ATR, espace ≥ 1,5R avant la prochaine liquidité.
- **Plan** : SL derrière la mèche du sweep, TP1 = 1R (break-even), TP2 = prochaine liquidité (sinon 2R).

## Limites honnêtes
Yahoo est un flux non officiel (retards possibles) ; pas de volume FX centralisé (VWAP/POC volontairement retirés plutôt que simulés) ; backtests approximatifs (pas de tick/bid-ask) et fondamentaux non backtestés. **Valide en paper trading plusieurs semaines avant tout capital réel.**

## Local
```bash
pip install -r requirements.txt && python app.py   # http://127.0.0.1:5000
```
