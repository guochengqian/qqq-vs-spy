"""
QQQ vs SPY: monthly dollar-cost-averaging (DCA) backtest on real, dividend-adjusted data.

Pipeline:
  1. Download monthly adjusted OHLC for QQQ and SPY from Yahoo Finance (needs `yfinance`).
     If the download fails (offline), falls back to the bundled snapshot in data/etf_monthly.csv.
  2. Simulate investing $10,000 on the first trading day of every month, dividends reinvested,
     from five start years (2000/2005/2010/2015/2020) through today.
  3. Print IRR (annualized money-weighted return), max drawdown, and final wealth for each run,
     and save chart.png comparing the two portfolio-value curves since 2000.

Usage:
  pip install -r requirements.txt
  python dca_backtest.py

Not investment advice. Past performance does not predict future returns.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(HERE, "data", "etf_monthly.csv")
TICKERS = ["QQQ", "SPY"]
MONTHLY_USD = 10_000.0
START_YEARS = ["2000-01", "2005-01", "2010-01", "2015-01", "2020-01"]


# ----------------------------------------------------------------------------- data
def download() -> dict:
    """Fetch monthly dividend-adjusted open/close/low from Yahoo and refresh the CSV."""
    import yfinance as yf

    rows = {}
    for t in TICKERS:
        df = yf.download(t, period="max", interval="1mo", auto_adjust=True, progress=False)
        if hasattr(df.columns, "levels"):  # flatten MultiIndex columns from newer yfinance
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        rows[t] = [
            (idx.strftime("%Y-%m"), float(r["Open"]), float(r["Close"]), float(r["Low"]))
            for idx, r in df.iterrows()
        ]
    if any(len(rows[t]) < 100 for t in TICKERS):  # sanity check before touching the CSV
        raise RuntimeError("Yahoo returned empty/short history")
    os.makedirs(os.path.dirname(DATA_CSV), exist_ok=True)
    with open(DATA_CSV, "w") as f:
        for t in TICKERS:
            f.write(f"#{t}\n")
            for ym, o, c, l in rows[t]:
                f.write(f"{ym},{o:.4f},{c:.4f},{l:.4f}\n")
    return rows


def load_csv() -> dict:
    """Load the bundled snapshot (format: '#TICKER' header lines, then 'YYYY-MM,open,close,low')."""
    data, cur = {}, None
    for line in open(DATA_CSV):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            cur = line[1:]
            data[cur] = {}
        else:
            ym, o, c, l = line.split(",")
            if ym in data[cur]:  # duplicate partial month: keep first open, last close, lowest low
                po, pc, pl = data[cur][ym]
                data[cur][ym] = (po, float(c), min(pl, float(l)))
            else:
                data[cur][ym] = (float(o), float(c), float(l))
    return {t: [(ym,) + d[ym] for ym in sorted(d)] for t, d in data.items()}


def get_data() -> dict:
    try:
        rows = download()
        print("Data: downloaded fresh from Yahoo Finance.\n")
        return rows
    except Exception as e:
        print(f"Data: download failed ({type(e).__name__}), using bundled snapshot {DATA_CSV}\n")
        return load_csv()


# ------------------------------------------------------------------------- backtest
def irr_annual(flows) -> float:
    """Annualized IRR of [(t_years, cashflow), ...] via bisection."""
    def npv(r):
        return sum(cf / (1.0 + r) ** t for t, cf in flows)

    lo, hi = -0.95, 20.0
    if npv(lo) * npv(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def dca(rows, start_ym, monthly=MONTHLY_USD) -> dict:
    """Buy `monthly` dollars at each month's (adjusted) open; value at (adjusted) close."""
    d = [r for r in rows if r[0] >= start_ym]
    shares, flows, vals_close, vals_low = 0.0, [], [], []
    for i, (ym, o, c, l) in enumerate(d):
        shares += monthly / o
        flows.append((i / 12.0, -monthly))
        vals_close.append(shares * c)
        vals_low.append(shares * l)  # intramonth trough (approximation for drawdown)
    flows.append(((len(d) - 1) / 12.0, vals_close[-1]))

    peak, mdd = -1e18, 0.0
    for c, l in zip(vals_close, vals_low):
        peak = max(peak, c)
        mdd = min(mdd, l / peak - 1.0)

    total = monthly * len(d)
    return dict(irr=irr_annual(flows), mdd=mdd, final=vals_close[-1], total=total,
                mult=vals_close[-1] / total, values=vals_close, n=len(d))


def main():
    data = get_data()

    print(f"{'start':>7} | {'ticker':>6} | {'invested':>12} | {'final':>14} | "
          f"{'multiple':>8} | {'IRR/yr':>7} | {'maxDD':>7}")
    print("-" * 80)
    results = {}
    for s in START_YEARS:
        for t in TICKERS:
            r = dca(data[t], s)
            results[(t, s)] = r
            print(f"{s[:4]:>7} | {t:>6} | ${r['total']:>10,.0f} | ${r['final']:>12,.0f} | "
                  f"{r['mult']:>7.2f}x | {r['irr']*100:>6.1f}% | {r['mdd']*100:>6.1f}%")
        q, p = results[("QQQ", s)], results[("SPY", s)]
        print(f"{'':>7} | QQQ final / SPY final = {q['final'] / p['final']:.2f}x\n" + "-" * 80)

    avg_q = np.mean([results[("QQQ", s)]["irr"] for s in START_YEARS]) * 100
    avg_p = np.mean([results[("SPY", s)]["irr"] for s in START_YEARS]) * 100
    print(f"\nAverage IRR across the 5 start years:  QQQ {avg_q:.1f}%/yr  vs  SPY {avg_p:.1f}%/yr")

    # ---- chart: the two portfolio-value curves, DCA since Jan 2000 (log scale)
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    for t, color in [("QQQ", "#7c3aed"), ("SPY", "#0ea5e9")]:
        r = results[(t, "2000-01")]
        x = 2000 + np.arange(r["n"]) / 12.0
        ax.plot(x, r["values"], color=color, lw=1.8,
                label=f"{t} DCA  (final ${r['final']/1e6:.1f}M, IRR {r['irr']*100:.1f}%/yr)")
    n = results[("QQQ", "2000-01")]["n"]
    ax.plot(2000 + np.arange(n) / 12.0, MONTHLY_USD * (np.arange(n) + 1),
            "--", color="#9ca3af", lw=1.2, label="Total invested")
    ax.set_yscale("log")
    ax.set_title("$10k/month DCA since Jan 2000: QQQ vs SPY (dividends reinvested)")
    ax.set_ylabel("Portfolio value ($, log scale)")
    ax.legend()
    ax.grid(alpha=0.3)
    out = os.path.join(HERE, "chart.png")
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"\nChart saved to {out}")


if __name__ == "__main__":
    main()
