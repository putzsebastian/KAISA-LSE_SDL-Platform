#!/usr/bin/env python3
"""Every number behind the figures, as markdown tables.

Clopper-Pearson (exact binomial) intervals throughout: the normal approximation collapses to zero
width at 0/n and n/n, which is precisely where the uncertainty most needs showing.

    python stats_table.py [--results results.json] [--out STATISTICS.md]
"""
from __future__ import annotations

import argparse
import json
import pathlib

from scipy.stats import beta, fisher_exact

HERE = pathlib.Path(__file__).resolve().parent

# Running inside the published bundle this script sits in code/analysis/ and its outputs belong in
# results/ at the bundle root; in the working tree there is no such directory and they stay beside
# the script. A reproduction run therefore refreshes exactly the files it is checking.
OUT = HERE.parent.parent / "results"
if not OUT.is_dir():
    OUT = HERE
AGENT_ORDER = ["orchestration", "analysis", "report", "database"]

# List prices, US dollars per million tokens, uncached. The key is the exact identifier the n8n
# chat node requested, which is what the executions record and therefore what was billed.
#   model identifier -> (input, output)
PRICES = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5.5": (5.00, 30.00),
}
PRICE_DATE = "2026-08-13"
EXPERIMENTS = [("isotherm", "isotherm (Opentrons)"), ("fplc_gradient", "FPLC gradient (ÄKTA)")]


def ci(k: int, n: int, alpha: float = 0.05):
    lo = 0.0 if k == 0 else beta.ppf(alpha / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(1 - alpha / 2, k + 1, n - k)
    return 100 * lo, 100 * hi


def rate_row(label: str, k: int, n: int, extra: str = "") -> str:
    lo, hi = ci(k, n)
    return "| %s | %d/%d | %.1f%% | [%.1f%%, %.1f%%] | %.1f | %s |" % (
        label, k, n, 100.0 * k / n, lo, hi, hi - lo, extra)


def tel(t, key, scale=1.0, fmt="%.0f"):
    v = (t or {}).get(key)
    if not v:
        return "—", "—"
    med = fmt % (v["median"] / scale)
    iqr = "%s–%s" % (fmt % (v["q1"] / scale), fmt % (v["q3"] / scale))
    return med, iqr


def quantiles(v):
    """Median and quartiles by the nearest-rank convention used throughout this document.

    Deliberately the same rule the token and wall-clock summaries use, so a cost quartile and a
    token quartile in adjacent tables mean the same thing.
    """
    if not v:
        return None
    s = sorted(v)
    import statistics as _st
    return {"n": len(s), "median": _st.median(s), "q1": s[len(s) // 4],
            "q3": s[min(len(s) - 1, 3 * len(s) // 4)], "min": s[0], "max": s[-1]}


def costs(telemetry, model):
    """Per-replicate cost in dollars, priced from that replicate's own token counts.

    Computed per run and only then summarised. Pricing the 25th percentile of input tokens against
    the 25th percentile of output tokens would combine two different replicates at two different
    rates and produce a number no run actually cost.
    """
    pin, pout = PRICES[model]
    rows = (telemetry or {}).get("per_replicate") or []
    return [r["in"] / 1e6 * pin + r["out"] / 1e6 * pout for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(OUT / "results.json"))
    ap.add_argument("--out", default=str(OUT / "STATISTICS.md"))
    args = ap.parse_args()
    res = json.loads(pathlib.Path(args.results).read_text(encoding="utf-8"))
    L = []

    L.append("# Replication statistics\n")
    L.append("All intervals are Clopper-Pearson (exact binomial) at 95%. `width` is the interval "
             "span in percentage points — at n = 20 it never falls below ~17, which bounds what "
             "this study can resolve.\n")

    # ------------------------------------------------------------------ reliability
    L.append("\n## 1. Reliability by agent\n")
    L.append("| agent | rate | % | 95% CI | width | notes |")
    L.append("|---|---|---|---|---|---|")
    for exp_key, exp_label in EXPERIMENTS:
        L.append("| **%s** | | | | | |" % exp_label)
        for a in AGENT_ORDER:
            v = res["agents"][exp_key].get(a)
            if not v:
                continue
            note = "—"
            if v.get("excluded_bad_upstream"):
                note = "%d excluded: failed %s" % (v["excluded_bad_upstream"],
                                                  v["upstream_gate"])
            L.append(rate_row("&nbsp;&nbsp;" + a, v["passed"], v["n"], note))

    dp = res["device_protocol"]["isotherm"]["by_protocol"]
    tot_k = sum(v["pass"] for v in dp.values())
    tot_n = sum(v["n"] for v in dp.values())
    L.append("| **device protocol** (isotherm) | | | | | |")
    for p in sorted(dp):
        L.append(rate_row("&nbsp;&nbsp;protocol %s" % p, dp[p]["pass"], dp[p]["n"], "—"))
    L.append(rate_row("&nbsp;&nbsp;**pooled 1–5**", tot_k, tot_n, "—"))
    pk = sum(dp[p]["pass"] for p in dp if p != "1")
    pn = sum(dp[p]["n"] for p in dp if p != "1")
    L.append(rate_row("&nbsp;&nbsp;**pooled 2–5**", pk, pn, "excludes protocol 1"))

    # ------------------------------------------------------------------ models
    order = ["gpt-5-nano", "gpt-5-mini", "gpt-5.1", "gpt-5.5"]
    PROTO_LABEL = {"1": "protocol 1 (dilution series - the study's hardest logic)",
                   "4": "protocol 4 (shake and incubate - no arithmetic)"}
    L.append("\n## 2. Two protocols across four models\n")
    L.append("Identical payload prompt, identical system prompt, identical harness and "
             "comparator; one variable changed. Measuring an easy protocol alongside a hard one "
             "is what distinguishes 'this model is worse' from 'this task needs capability'.\n")
    L.append("| model | %s | %s |" % (PROTO_LABEL["4"], PROTO_LABEL["1"]))
    L.append("|---|---|---|")
    for m in order:
        cells = []
        for proto in ("4", "1"):
            v = (res["models"].get(proto) or {}).get(m)
            if not v:
                cells.append("—")
                continue
            lo, hi = ci(v["passed"], v["n"])
            cells.append("%d/%d — %.1f%%  [%.1f, %.1f]"
                         % (v["passed"], v["n"], 100.0 * v["passed"] / v["n"], lo, hi))
        L.append("| %s | %s | %s |" % (m, cells[0], cells[1]))

    L.append("\n### Within model: does the hard protocol separate what the easy one does not?\n")
    L.append("| model | protocol 4 | protocol 1 | Fisher p |")
    L.append("|---|---|---|---|")
    for m in order:
        a = (res["models"].get("4") or {}).get(m)
        b = (res["models"].get("1") or {}).get(m)
        if not (a and b):
            continue
        _o, p = fisher_exact([[a["passed"], a["n"] - a["passed"]],
                              [b["passed"], b["n"] - b["passed"]]])
        L.append("| %s | %d/%d | %d/%d | %.5f%s |"
                 % (m, a["passed"], a["n"], b["passed"], b["n"], p,
                    "" if p < 0.05 else "  (not distinguishable)"))

    for proto in ("4", "1"):
        series = res["models"].get(proto) or {}
        if not series:
            continue
        L.append("\n### Pairwise on %s (Fisher exact, two-sided)\n" % PROTO_LABEL[proto])
        L.append("| comparison | p | |")
        L.append("|---|---|---|")
        ms = [(m, series[m]) for m in order if m in series]
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                (na, va), (nb, vb) = ms[i], ms[j]
                _o, p = fisher_exact([[va["passed"], va["n"] - va["passed"]],
                                      [vb["passed"], vb["n"] - vb["passed"]]])
                verdict = "distinguishable" if p < 0.05 else "not distinguishable"
                L.append("| %s vs %s | %.5f | %s |" % (na, nb, p, verdict))

    # ------------------------------------------------------------------ resolution
    L.append("\n## 3. What n = 20 can resolve\n")
    L.append("Every pair below is one series against another at the same n, to show where the "
             "study's resolution actually lies.\n")
    L.append("| comparison | Fisher p | |")
    L.append("|---|---|---|")
    for ka, kb in [(20, 19), (20, 18), (20, 17), (20, 16), (20, 15), (20, 14), (20, 13),
                   (20, 12), (20, 10)]:
        _o, p = fisher_exact([[ka, 20 - ka], [kb, 20 - kb]])
        L.append("| %d/20 vs %d/20 | %.4f | %s |"
                 % (ka, kb, p, "distinguishable" if p < 0.05 else "not distinguishable"))

    # ------------------------------------------------------------------ cost
    L.append("\n## 4. Cost per artifact\n")
    L.append("Every column carries its interquartile range, tool calls included: the tool-call "
             "spread is what backs the retry reading of the weaker models, and a point value "
             "cannot support it.\n")
    L.append("| series | tokens (median) | tokens IQR | wall clock s | s IQR | tool calls | calls IQR |")
    L.append("|---|---|---|---|---|---|---|")

    def cost_row(label, t):
        tm, ti = tel(t, "tokens", 1000.0, "%.1fk")
        wm, wi = tel(t, "wall_clock_s")
        cm, cq = tel(t, "tool_calls", 1.0, "%.1f")
        return "| %s | %s | %s | %s | %s | %s | %s |" % (label, tm, ti, wm, wi, cm, cq)

    for exp_key, exp_label in EXPERIMENTS:
        L.append("| **%s** | | | | | | |" % exp_label)
        for a in AGENT_ORDER:
            v = res["agents"][exp_key].get(a)
            if v:
                L.append(cost_row("&nbsp;&nbsp;" + a, v.get("telemetry") or {}))
    L.append("| **device protocol** | | | | | | |")
    dtel = res["device_protocol"]["isotherm"]["telemetry"]
    for p in sorted(dp):
        L.append(cost_row("&nbsp;&nbsp;protocol %s" % p,
                          dtel.get("device_protocol_%s" % p) or {}))
    for proto in ("1", "4"):
        L.append("| **models, protocol %s** | | | | | | |" % proto)
        for m in order:
            v = (res["models"].get(proto) or {}).get(m)
            if v:
                L.append(cost_row("&nbsp;&nbsp;" + m, v.get("telemetry") or {}))

    # ------------------------------------------------------------------ input vs output tokens
    L.append("\n### 4a. Input and output tokens, by model\n")
    L.append("Prompt and completion tokens are priced differently and driven by different things — "
             "input by how much context the agent carries, output by how much it reasons and "
             "writes — so a single total hides which of the two a model is actually spending. "
             "Medians across the same 20 replicates as above; `out %` is the completion share of "
             "the total.\n")
    L.append("| protocol | model | in (median) | in IQR | out (median) | out IQR | out % |")
    L.append("|---|---|---|---|---|---|---|")
    for proto in ("1", "4"):
        for m in order:
            v = (res["models"].get(proto) or {}).get(m)
            if not v:
                continue
            t = v.get("telemetry") or {}
            im, ii = tel(t, "tokens_in", 1000.0, "%.1fk")
            om, oi = tel(t, "tokens_out", 1000.0, "%.1fk")
            ti_, to_ = (t.get("tokens_in") or {}), (t.get("tokens_out") or {})
            share = "—"
            if ti_.get("median") and to_.get("median"):
                share = "%.1f%%" % (100.0 * to_["median"]
                                    / (ti_["median"] + to_["median"]))
            L.append("| %s | %s | %s | %s | %s | %s | %s |"
                     % (proto, m, im, ii, om, oi, share))

    # ------------------------------------------------------------------ cost per generation run
    L.append("\n### 4b. Cost per generation run, by model\n")
    L.append("List price of one replicate: its own input and output tokens at the rates in "
             "Supplementary Table S7. Cost is computed per replicate and only then summarised — "
             "quartiles are taken over the 20 per-run costs, not derived from the token quartiles "
             "in 4a, which come from different replicates and are billed at different rates, so "
             "their product is the cost of no actual run. Median input and output tokens are "
             "repeated here for cross-checking.\n")
    L.append("| protocol | model | median cost | cost IQR | range | in (median) | out (median) | n |")
    L.append("|---|---|---|---|---|---|---|---|")
    for proto in ("1", "4"):
        for m in order:
            v = (res["models"].get(proto) or {}).get(m)
            if not v or m not in PRICES:
                continue
            t = v.get("telemetry") or {}
            c = quantiles(costs(t, m))
            if not c:
                continue
            im, _ = tel(t, "tokens_in", 1000.0, "%.1fk")
            om, _ = tel(t, "tokens_out", 1000.0, "%.1fk")
            L.append("| %s | %s | $%.4f | $%.4f–$%.4f | $%.4f–$%.4f | %s | %s | %d |"
                     % (proto, m, c["median"], c["q1"], c["q3"], c["min"], c["max"],
                        im, om, c["n"]))

    L.append("\nEvery replicate is included regardless of outcome — the tokens were spent whether "
             "or not the artifact was usable, and a cost conditioned on success would understate "
             "what the weaker models actually cost to obtain their failures. **No replicate hit "
             "the harness timeout**: all 160 completed in a single attempt, and the wall-clock "
             "distributions have no pile-up at a ceiling, so no cost here is a truncated lower "
             "bound. One replicate (protocol 1, gpt-5-nano) returned without an artifact; it is "
             "counted at full cost and as a failure.\n")

    # ------------------------------------------------------------------ price schedule
    L.append("\n### Supplementary Table S7. Price schedule\n")
    L.append("| model | API identifier | input $/1M | output $/1M | retrieved |")
    L.append("|---|---|---|---|---|")
    for m in order:
        if m in PRICES:
            pin, pout = PRICES[m]
            L.append("| %s | `%s` | %.2f | %.2f | %s |" % (m, m, pin, pout, PRICE_DATE))
    L.append("\nThe API identifier is the exact string the n8n chat node requested, verified "
             "against the model recorded in every published execution. Rates are **uncached**.\n")
    L.append("Two things these figures are not. **They are list prices, not billed amounts** — "
             "no invoice was reconciled against them, and any discount, credit or tier would move "
             "them. **They are an upper bound on input cost**: the executions carry no "
             "cached-token accounting, so every prompt token is priced at the uncached rate; had "
             "any prompt been served from cache, the true input cost would be lower. Retrieval "
             "embeddings (`text-embedding-3-small`) are excluded — they are identical across all "
             "eight cells and are not counted in the token totals above.\n")

    L.append("\n## 5. Caveats that bear on reading these\n")
    L.append("* **Each bar is one experiment.** The intervals cover sampling at fixed conditions, "
             "not generalisation to an unseen protocol.")
    L.append("* **Gated series.** `database` consumes the analysis script, so replicates whose "
             "analysis failed are excluded rather than counted — handing an agent a broken input "
             "measures the upstream defect. Replicates were drawn in order until 20 had valid "
             "upstream input.")

    text = "\n".join(L) + "\n"
    pathlib.Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
