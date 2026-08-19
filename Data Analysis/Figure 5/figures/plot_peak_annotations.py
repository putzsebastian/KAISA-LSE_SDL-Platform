"""
Quality-assurance figure showing the peak-pick windows, retention times and
half-prominence widths used by peak_analysis.py, overlaid on the simulated and measured
chromatograms (2 x 2 layout).

Not a manuscript figure -- intended for verification of the peak-picking heuristics, so
that a reader can see exactly which peaks produced the retention times and elution NaCl
concentrations quoted in the Results.

Changed relative to the earlier version: resolutions are no longer computed or shown; the
simulated panels annotate only the protein that elutes, and the measured peaks are
labelled peak 1 and peak 2 in a neutral colour, since their identity was not established
experimentally.
"""
from pathlib import Path
import json
import sys
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import peak_widths

# Add the analysis folder to sys.path so we can import shared helpers without packaging
# this as a proper Python module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'analysis'))
from peak_analysis import (                                                     # noqa: E402
    MW_OVALBUMIN, MW_TRANSFERRIN, SIM_ELUTION_THRESHOLD_GL,
    SIM_FILES, GRAD_INFO, STEP_REPLICATES,
    STEP_T_LOAD_START, STEP_T_WASH_END,
    STEP_T_STEP1_START, STEP_T_STEP1_END,
    STEP_T_STEP2_START, STEP_T_STEP2_END, STEP_T_CIP_END,
    STEP_T_PLOT_WASH_END, STEP_T_PLOT_STEP1_END, STEP_T_PLOT_STEP2_END,
    STEP_T_PLOT_END, GRAD_T_PLOT_WASH_END, GRAD_T_PLOT_END,
    width_at_half_prominence, find_peak_in_window,
)

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
DATA_DIR = _REPO_ROOT / 'data'
OUT_DIR = _REPO_ROOT / 'output'
SIM_DIR = DATA_DIR / 'simulations'
FPLC_DIR = DATA_DIR / 'fplc'

FIGSIZE = (14.0, 8.0)
DPI = 200

COLOR_OVA = '#1d4ed8'
COLOR_TRA = '#dc2626'
COLOR_PEAK = '#1f2937'    # neutral: measured peaks are not assigned to a protein


# ------------------------------------------------------------------
# Local data loaders -- take paths as arguments so they work with the CLI overrides.
# ------------------------------------------------------------------
def load_step_local(eid, fplc_dir):
    with open(fplc_dir / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t = np.array(d['time'])
    uv = np.array([v if v is not None else np.nan for v in d['uv1']])
    segments = [
        ((t >= STEP_T_LOAD_START) & (t < STEP_T_WASH_END), STEP_T_LOAD_START, 0),
        ((t >= STEP_T_STEP1_START) & (t < STEP_T_STEP1_END), STEP_T_STEP1_START,
         STEP_T_PLOT_WASH_END),
        ((t >= STEP_T_STEP2_START) & (t < STEP_T_STEP2_END), STEP_T_STEP2_START,
         STEP_T_PLOT_STEP1_END),
        ((t >= STEP_T_STEP2_END) & (t < STEP_T_CIP_END), STEP_T_STEP2_END,
         STEP_T_PLOT_STEP2_END),
    ]
    t_s = np.concatenate([t[m] - t0 + off for m, t0, off in segments])
    uv_s = np.concatenate([uv[m] for m, _, _ in segments])
    bl = np.nanmean(uv_s[t_s < STEP_T_PLOT_WASH_END])
    return t_s / 60.0, uv_s - bl


def load_grad_local(eid, fplc_dir):
    info = GRAD_INFO[eid]
    with open(fplc_dir / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t = np.array(d['time'])
    uv = np.array([v if v is not None else np.nan for v in d['uv1']])
    m = (t >= info['load_start']) & (t < info['window_end'])
    t_plot = t[m] - info['load_start']
    bl = np.nanmean(uv[m][t_plot < GRAD_T_PLOT_WASH_END])
    return t_plot / 60.0, uv[m] - bl


# ------------------------------------------------------------------
# Plot helpers
# ------------------------------------------------------------------
def annotate_peak(ax, t, y, i_peak, color, label):
    t_peak, y_peak = float(t[i_peak]), float(y[i_peak])
    w, t_l, t_r = width_at_half_prominence(t, y, i_peak)
    ax.axvline(t_peak, color=color, linestyle=':', linewidth=0.8, alpha=0.7)
    _, width_heights, _, _ = peak_widths(y, [i_peak], rel_height=0.5)
    h = float(width_heights[0])
    ax.plot([t_l, t_r], [h, h], color=color, linewidth=2.0, alpha=0.9, zorder=5)
    ax.plot(t_peak, y_peak, marker='v', color=color, markersize=8, zorder=6)
    ax.annotate(
        f'{label}\n$t_R$={t_peak:.2f} min\n$W_{{0.5}}$={w:.2f} min\n$y$={y_peak:.2f}',
        xy=(t_peak, y_peak), xytext=(8, 8), textcoords='offset points',
        fontsize=8, color=color,
        bbox=dict(boxstyle='round,pad=0.3', fc='white', ec=color, alpha=0.85),
    )


def plot_sim(filename, label, ax, sim_dir):
    with open(sim_dir / filename) as f:
        d = json.load(f)
    co = d['column_outlet']
    t_min = np.array(co['time']) / 60.0
    c_ova = np.array(co['concentration']['component_1']) * MW_OVALBUMIN / 1000.0
    c_tra = np.array(co['concentration']['component_2']) * MW_TRANSFERRIN / 1000.0

    ax.plot(t_min, c_ova, color=COLOR_OVA, label='Ovalbumin', linewidth=1.6)
    ax.plot(t_min, c_tra, color=COLOR_TRA, label='Transferrin', linewidth=1.6)

    # Annotate only the components that actually elute; a peak pick on an essentially
    # flat trace would return the last time point.
    not_eluting = []
    for name, c, colour in (('Ovalbumin', c_ova, COLOR_OVA),
                            ('Transferrin', c_tra, COLOR_TRA)):
        if c.max() < SIM_ELUTION_THRESHOLD_GL:
            not_eluting.append(name)
            continue
        annotate_peak(ax, t_min, c, int(np.argmax(c)), colour, name)

    title = label
    if not_eluting:
        title += '  (' + ', '.join(not_eluting) + ': no elution)'
    ax.set_title(title)
    ax.set_xlabel('Time (min)')
    ax.set_ylabel(r'Concentration (g L$^{-1}$)')
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc='upper right', fontsize=9)


def _mean_trace(loader, ids, fplc_dir):
    traces, t_ref = [], None
    for eid in ids:
        t, uv = loader(eid, fplc_dir)
        if t_ref is None or len(t) < len(t_ref):
            t_ref = t
        traces.append(uv)
    L = min(len(a) for a in traces)
    return t_ref[:L], np.mean([a[:L] for a in traces], axis=0)


def plot_exp(ax, loader, ids, fplc_dir, windows, prominence, xmax, title):
    t, uv = _mean_trace(loader, ids, fplc_dir)
    ax.plot(t, uv, color=COLOR_PEAK, linewidth=1.3, label=f'UV (mean of {len(ids)})')
    for k, (lo, hi) in enumerate(windows, start=1):
        p = find_peak_in_window(t, uv, lo, hi, prominence=prominence)
        if p is not None:
            annotate_peak(ax, t, uv, p[0], COLOR_PEAK, f'Peak {k}')
    ax.set_title(title)
    ax.set_xlabel('Time (min)')
    ax.set_ylabel('UV (mAU)')
    ax.set_xlim(0, xmax)
    ax.set_ylim(bottom=0)
    ax.legend(loc='upper left', fontsize=9)


def _parse_cli_args():
    import argparse
    p = argparse.ArgumentParser(
        description=(__doc__ or '').strip().splitlines()[0] if __doc__ else '')
    p.add_argument('--data-dir', type=Path, default=DATA_DIR,
                   help='Root data directory (default: %(default)s). Must contain the '
                        'appropriate subfolders: simulations, fplc.')
    p.add_argument('--output-dir', type=Path, default=OUT_DIR,
                   help='Output directory (default: %(default)s).')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse_cli_args()
    DATA_DIR = args.data_dir
    OUT_DIR = args.output_dir
    SIM_DIR = DATA_DIR / 'simulations'
    FPLC_DIR = DATA_DIR / 'fplc'
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=FIGSIZE, dpi=DPI)
    plot_sim(SIM_FILES['Gradient (simulation)'], 'Gradient elution (simulation)',
             axes[0, 0], SIM_DIR)
    plot_sim(SIM_FILES['Two-step (simulation)'], 'Two-step elution (simulation)',
             axes[0, 1], SIM_DIR)
    plot_exp(axes[1, 0], load_grad_local, list(GRAD_INFO), FPLC_DIR,
             windows=[(18.0, 26.0), (26.0, 40.0)], prominence=0.3,
             xmax=GRAD_T_PLOT_END / 60.0,
             title='Gradient elution (experiment, n = 3)')
    plot_exp(axes[1, 1], load_step_local, STEP_REPLICATES, FPLC_DIR,
             windows=[(8.27, 15.0), (15.0, 26.5)], prominence=1.0,
             xmax=STEP_T_PLOT_END / 60.0,
             title='Two-step elution (experiment, n = 3)')
    fig.tight_layout()
    out = OUT_DIR / 'peak_annotations.png'
    fig.savefig(out, dpi=DPI, bbox_inches='tight')
    print(f'  wrote {out}')
