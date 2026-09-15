"""Render a CoinMarketCap-style price chart from stored data."""
import io
import time
import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def render_chart(rows, hours=None, out_path=None):
    if not rows:
        return None
    ts = [datetime.datetime.fromtimestamp(r[0]) for r in rows]
    mid = [r[3] for r in rows]
    buy = [r[1] for r in rows]
    sell = [r[2] for r in rows]

    fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    color = "#16c784" if mid[-1] >= mid[0] else "#ea3943"
    ax.plot(ts, mid, color=color, lw=1.8, label="Mid price")
    ax.fill_between(ts, mid, min(mid) * 0.999, color=color, alpha=0.12)
    ax.plot(ts, buy, color="#58a6ff", lw=0.8, alpha=0.7, label="Buy")
    ax.plot(ts, sell, color="#f7b731", lw=0.8, alpha=0.7, label="Sell")

    ax.set_title(f"USD/KHR  {mid[-1]:,.2f} KHR  ({'+' if mid[-1]>=mid[0] else ''}{mid[-1]-mid[0]:,.2f})",
                 color="white", fontsize=13, pad=12)
    ax.legend(facecolor="#0d1117", labelcolor="white", frameon=False)
    ax.tick_params(colors="#8b949e")
    for s in ax.spines.values():
        s.set_color("#30363d")
    ax.grid(color="#21262d", lw=0.6)
    fig.autofmt_xdate()
    fig.tight_layout()

    if out_path:
        fig.savefig(out_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        return out_path
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf
