#!/usr/bin/env python3
"""Build the manuscript figures from results.json.

Figure 1  (a) per-agent reliability in both experiments, with Clopper-Pearson intervals
          (b) what each artifact costs, median tokens with the interquartile range

Figure 2  protocol 1 across four models: the same prompt, the same harness, one variable changed

Run collect_results.py first; it refuses to declare itself ready while any series is short of
n=20, so a figure can never be built from uneven counts.

Layout notes, because these were all mistakes in the first draft: value labels sit in a fixed
right-hand column so they can never collide with a confidence interval; legends sit outside the
axes so they cannot cover a bar; and figure 2b puts wall clock on its own axis rather than sharing
one with tokens, which would be a shared scale between two different units.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from scipy.stats import beta      # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent

# Running inside the published bundle this script sits in code/analysis/ and its outputs belong in
# results/ at the bundle root; in the working tree there is no such directory and they stay beside
# the script. A reproduction run therefore refreshes exactly the files it is checking.
OUT = HERE.parent.parent / "results"
if not OUT.is_dir():
    OUT = HERE

ISO = "#3B6FB6"
FPLC = "#C4622D"
DEVICE = "#6B7A8F"
GRID = "#DCE0E6"
BAND = "#F4F6F8"
TEXT = "#22272E"
MUTED = "#5B6672"

AGENT_ORDER = ["orchestration", "analysis", "report", "database"]


def ci(k: int, n: int, alpha: float = 0.05):
    lo = 0.0 if k == 0 else beta.ppf(alpha / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(1 - alpha / 2, k + 1, n - k)
    return 100 * lo, 100 * hi


def clean(ax, axis="x"):
    ax.set_axisbelow(True)
    ax.grid(axis=axis, color=GRID, lw=0.8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT, length=0, labelsize=10)


def stack(items):
    """y positions with a gap whenever the row label changes; returns (positions, ticks, names)."""
    pos, y, last = [], 0.0, None
    for label, *_ in items:
        if last is not None and label != last:
            y += 0.6
        pos.append(y)
        y += 1.0
        last = label
    ticks, names = [], []
    for label, *_ in items:
        if label in names:
            continue
        ys = [p for p, it in zip(pos, items) if it[0] == label]
        ticks.append(sum(ys) / len(ys))
        names.append(label)
    return pos, ticks, names


def bands(ax, ticks, width):
    """Alternating row bands, so the eye can carry a row across the two panels."""
    for i, t in enumerate(ticks):
        if i % 2 == 0:
            ax.axhspan(t - width, t + width, color=BAND, zorder=0)


def figure1(res, out):
    agents = res["agents"]
    dp = res["device_protocol"]["isotherm"]["by_protocol"]

    rows = []
    for a in AGENT_ORDER:
        for src, colour in (("isotherm", ISO), ("fplc_gradient", FPLC)):
            v = agents[src].get(a)
            if v:
                rows.append((a, v["passed"], v["n"], colour,
                             (v.get("telemetry") or {}).get("tokens")))
    for p in sorted(dp):
        t = res["device_protocol"]["isotherm"]["telemetry"].get("device_protocol_%s" % p, {})
        rows.append(("device protocol %s" % p, dp[p]["pass"], dp[p]["n"], DEVICE,
                     t.get("tokens")))

    pos, ticks, names = stack(rows)
    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(13.6, 7.0), gridspec_kw={"width_ratios": [1.4, 1]})

    # ---------------------------------------------------------------- (a) reliability
    bands(ax, ticks, 1.05)
    for (label, k, n, colour, _t), yy in zip(rows, pos):
        pct = 100.0 * k / n
        lo, hi = ci(k, n)
        ax.barh(yy, pct, height=0.80, color=colour, alpha=0.95, zorder=3)
        ax.plot([lo, hi], [yy, yy], color=TEXT, lw=1.5, zorder=4, alpha=0.8,
                solid_capstyle="butt")
        # fixed label column, clear of every interval
        ax.text(113.5, yy, "%d/%d" % (k, n), va="center", ha="right",
                fontsize=9.5, color=MUTED, zorder=5, family="monospace")

    ax.set_yticks(ticks)
    ax.set_yticklabels(names, fontsize=11, color=TEXT)
    ax.invert_yaxis()
    ax.set_xlim(0, 114)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: "%d%%" % v))
    ax.set_xlabel("replicates judged equivalent to the reference   (n = 20 each)", fontsize=10.5)
    ax.set_title("a   Reliability by agent", loc="left", fontsize=13,
                 color=TEXT, fontweight="bold", pad=12)
    clean(ax)

    # figure-level legend on its own band above both panels, so it cannot sit on a title or a bar
    handles = [plt.Rectangle((0, 0), 1, 1, color=ISO),
               plt.Rectangle((0, 0), 1, 1, color=FPLC),
               plt.Rectangle((0, 0), 1, 1, color=DEVICE)]
    fig.legend(handles, ["isotherm  (Opentrons)", "FPLC gradient  (ÄKTA)",
                         "device protocol  (isotherm)"],
               loc="upper left", bbox_to_anchor=(0.155, 0.995), ncol=3,
               frameon=False, fontsize=10, handlelength=1.1, handleheight=1.1,
               columnspacing=2.4, labelcolor=TEXT)

    # ---------------------------------------------------------------- (b) cost
    bands(bx, ticks, 1.05)
    for (label, _k, _n, colour, tok), yy in zip(rows, pos):
        if not tok:
            continue
        bx.barh(yy, tok["median"] / 1000.0, height=0.80, color=colour, alpha=0.95, zorder=3)
        bx.plot([tok["q1"] / 1000.0, tok["q3"] / 1000.0], [yy, yy], color=TEXT, lw=1.5,
                zorder=4, alpha=0.8, solid_capstyle="butt")
    bx.set_yticks(ticks)
    bx.set_yticklabels([])
    bx.invert_yaxis()
    bx.set_xlabel("tokens per artifact, thousands   (median, IQR)", fontsize=10.5)
    bx.set_title("b   Cost of one artifact", loc="left", fontsize=13,
                 color=TEXT, fontweight="bold", pad=12)
    clean(bx)

    fig.subplots_adjust(left=0.155, right=0.985, top=0.885, bottom=0.085, wspace=0.06)
    fig.savefig(out, dpi=300)
    # matplotlib stamps the PDF with the time it was written, which would make the published file
    # differ from a reader's regeneration of it for no reason that concerns the data
    fig.savefig(str(out).replace(".png", ".pdf"), metadata={"CreationDate": None})
    print("wrote %s (+ .pdf)" % out)
    plt.close(fig)


def figure2(res, out):
    """Two protocols, four models. The interaction is the result: an easy task separates almost
    nothing, a hard one separates sharply, so capability binds where the task carries logic."""
    order = ["gpt-5-nano", "gpt-5-mini", "gpt-5.1", "gpt-5.5"]
    p1 = res["models"].get("1", {})
    p4 = res["models"].get("4", {})
    models = [m for m in order if m in p1 or m in p4]
    xs = list(range(len(models)))

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(12.4, 5.2),
                                 gridspec_kw={"width_ratios": [1.25, 1]})

    # ---------------------------------------------------------------- (a) rate, both protocols
    off = 0.16
    for series, colour, dx, name in ((p4, ISO, -off, "protocol 4  (shake and incubate)"),
                                     (p1, FPLC, off, "protocol 1  (dilution series)")):
        for x, m in zip(xs, models):
            v = series.get(m)
            if not v:
                continue
            k, n = v["passed"], v["n"]
            pct = 100.0 * k / n
            lo, hi = ci(k, n)
            ax.plot([x + dx, x + dx], [lo, hi], color=MUTED, lw=1.5, alpha=0.8, zorder=3)
            ax.plot([x + dx - 0.05, x + dx + 0.05], [lo, lo], color=MUTED, lw=1.5, zorder=3)
            ax.plot([x + dx - 0.05, x + dx + 0.05], [hi, hi], color=MUTED, lw=1.5, zorder=3)
            ax.plot(x + dx, pct, "o", ms=10, color=colour, zorder=4,
                    markeredgecolor="white", markeredgewidth=1.3)
            # align each series' label outward from its point, so two coincident values
            # (gpt-5.5 is 20/20 on both protocols) cannot print on top of each other
            ax.text(x + dx * 1.5, hi + 3.5, "%d/%d" % (k, n),
                    ha="right" if dx < 0 else "left", va="bottom",
                    fontsize=9, color=TEXT, family="monospace")
    # join each model's two points, so the collapse from easy to hard is visible as a drop
    for x, m in zip(xs, models):
        a, b = p4.get(m), p1.get(m)
        if a and b:
            ya = 100.0 * a["passed"] / a["n"]
            yb = 100.0 * b["passed"] / b["n"]
            ax.plot([x - off, x + off], [ya, yb], color=MUTED, lw=1.0, alpha=0.45,
                    zorder=2, linestyle=":")

    ax.set_xticks(xs)
    ax.set_xticklabels(models, fontsize=10.5, color=TEXT)
    ax.set_xlim(-0.5, len(models) - 0.5)
    ax.set_ylim(-8, 124)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: "%d%%" % v))
    ax.set_ylabel("replicates correct   (n = 20)", fontsize=10.5)
    ax.set_title("a   An easy and a hard protocol, same four models", loc="left",
                 fontsize=12.5, color=TEXT, fontweight="bold", pad=14)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for sd in ("top", "right"):
        ax.spines[sd].set_visible(False)
    for sd in ("bottom", "left"):
        ax.spines[sd].set_color(GRID)
    ax.tick_params(colors=TEXT, length=0, labelsize=10)
    ax.legend([plt.Line2D([], [], marker="o", ls="", color=ISO, ms=9),
               plt.Line2D([], [], marker="o", ls="", color=FPLC, ms=9)],
              ["protocol 4  (shake and incubate)", "protocol 1  (dilution series)"],
              loc="lower left", frameon=False, fontsize=9.5, labelcolor=TEXT)

    # ---------------------------------------------------------------- (b) cost, protocol 1
    width = 0.34
    cx = bx.twinx()
    for x, m in zip(xs, models):
        v = p1.get(m)
        if not v:
            continue
        tok = v["telemetry"].get("tokens")
        wc = v["telemetry"].get("wall_clock_s")
        if tok:
            bx.bar(x - width / 2, tok["median"] / 1000.0, width, color=ISO, alpha=0.95, zorder=3)
        if wc:
            cx.bar(x + width / 2, wc["median"], width, color=FPLC, alpha=0.95, zorder=3)
    bx.set_xticks(xs)
    bx.set_xticklabels(models, fontsize=10.5, color=TEXT)
    bx.set_ylabel("tokens, thousands", fontsize=10.5, color=ISO)
    cx.set_ylabel("wall clock, s", fontsize=10.5, color=FPLC)
    bx.tick_params(axis="y", colors=ISO, length=0, labelsize=10)
    cx.tick_params(axis="y", colors=FPLC, length=0, labelsize=10)
    bx.tick_params(axis="x", colors=TEXT, length=0, labelsize=10)
    bx.set_title("b   Cost of one protocol-1 attempt   (median)", loc="left",
                 fontsize=12.5, color=TEXT, fontweight="bold", pad=14)
    bx.set_axisbelow(True)
    bx.grid(axis="y", color=GRID, lw=0.8)
    bx.spines["top"].set_visible(False)
    cx.spines["top"].set_visible(False)
    for sd in ("bottom", "left", "right"):
        bx.spines[sd].set_color(GRID)
        cx.spines[sd].set_color(GRID)

    fig.subplots_adjust(left=0.075, right=0.925, top=0.86, bottom=0.115, wspace=0.3)
    fig.savefig(out, dpi=300)
    # matplotlib stamps the PDF with the time it was written, which would make the published file
    # differ from a reader's regeneration of it for no reason that concerns the data
    fig.savefig(str(out).replace(".png", ".pdf"), metadata={"CreationDate": None})
    print("wrote %s (+ .pdf)" % out)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(OUT / "results.json"))
    args = ap.parse_args()
    res = json.loads(pathlib.Path(args.results).read_text(encoding="utf-8"))
    if res.get("missing"):
        print("refusing to plot; results.json reports missing series:")
        for m in res["missing"]:
            print("  - " + m)
        return 1
    figure1(res, OUT / "figure1_reliability.png")
    figure2(res, OUT / "figure2_models.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
