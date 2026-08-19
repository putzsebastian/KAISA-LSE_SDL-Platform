"""
FPLC validation chromatogram -- panel f of the manuscript figure.

Two-step elution on SDL 2 (AEKTA Pure), performed in triplicate
(eLabFTW experiments 5545, 5549, 5553).

Signals:
- UV1   (mAU)   on the LEFT y-axis  -- experimental analogue of protein conc.
- Cond. (mS/cm) on the RIGHT y-axis -- experimental analogue of NaCl conc.

The data are cropped to the phases that the simulation covers and the time axis is
stitched so that the two 60 s pump-wash transitions between phases (condition_elute_1,
condition_elute_2) are excised. Changed relative to the previous version: the crop now
extends through the 2000 mM CIP step (692 s), so 0 -> 2284 s in the plot maps exactly onto
the simulated Load + Wash + Step 1 + Step 2 + CIP timeline. The subsequent alkaline CIP is
not shown here; it lies outside the domain of the model and is shown in full in the
Supplementary Information.
"""
from pathlib import Path
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
DATA_DIR = _REPO_ROOT / 'data'
OUT_DIR = _REPO_ROOT / 'output'
FPLC_DIR = DATA_DIR / 'fplc'

# ------------------------------------------------------------------
# Phase timing in the AEKTA data (cumulative seconds from run start), Process ID 37:
#   equilibration      ->  load_inject starts at    780 s
#   load_inject  150 s ->  wash starts at           930 s
#   wash         346 s ->  condition_elute_1 at    1276 s
#   cond_elute_1  60 s ->  elute_isocratic_1 at    1336 s
#   elute_1      404 s ->  condition_elute_2 at    1740 s
#   cond_elute_2  60 s ->  elute_isocratic_2 at    1800 s
#   elute_2      692 s ->  CIP high salt at        2492 s
#   CIP high salt 692 s -> end of window at        3184 s
# ------------------------------------------------------------------
T_LOAD_START = 780
T_WASH_END = 1276
T_STEP1_START = 1336
T_STEP1_END = 1740
T_STEP2_START = 1800
T_STEP2_END = 2492
T_CIP_END = 3184

# Boundaries on the stitched (simulation-aligned) timeline:
T_PLOT_LOAD_END = 150
T_PLOT_WASH_END = 496
T_PLOT_STEP1_END = 900
T_PLOT_STEP2_END = 1592
T_PLOT_END = 2284

REPLICATES = [5545, 5549, 5553]

COLOR_UV = '#1f2937'
COLOR_COND = '#0d9488'
COLOR_CIP_SHADE = '#f3f4f6'
LW = 1.4
ALPHA = 0.75

FIGSIZE = (8.0, 4.5)
AX_POS = (0.085, 0.135, 0.555, 0.795)
DPI = 300
FONTSIZE = 11

plt.rcParams.update({
    'font.size': FONTSIZE,
    'axes.labelsize': FONTSIZE,
    'axes.titlesize': FONTSIZE + 1,
    'legend.fontsize': FONTSIZE - 2,
    'xtick.labelsize': FONTSIZE - 1,
    'ytick.labelsize': FONTSIZE - 1,
    'axes.linewidth': 0.8,
    'xtick.direction': 'in',
    'ytick.direction': 'in',
})


def load_and_stitch(eid):
    """Crop to the simulated phases and stitch the time axis to the simulation timeline."""
    with open(FPLC_DIR / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t = np.array(d['time'])
    uv = np.array([v if v is not None else np.nan for v in d['uv1']])
    cond = np.array(d['cond'])

    segments = [
        ((t >= T_LOAD_START) & (t < T_WASH_END), T_LOAD_START, 0),
        ((t >= T_STEP1_START) & (t < T_STEP1_END), T_STEP1_START, T_PLOT_WASH_END),
        ((t >= T_STEP2_START) & (t < T_STEP2_END), T_STEP2_START, T_PLOT_STEP1_END),
        ((t >= T_STEP2_END) & (t < T_CIP_END), T_STEP2_END, T_PLOT_STEP2_END),
    ]
    t_parts, uv_parts, cond_parts = [], [], []
    for mask, t0, offset in segments:
        t_parts.append(t[mask] - t0 + offset)
        uv_parts.append(uv[mask])
        cond_parts.append(cond[mask])

    t_plot = np.concatenate(t_parts)
    uv_plot = np.concatenate(uv_parts)
    cond_plot = np.concatenate(cond_parts)

    baseline = np.nanmean(uv_plot[t_plot < T_PLOT_WASH_END])
    return t_plot, uv_plot - baseline, cond_plot


def plot_fplc_step():
    fig = plt.figure(figsize=FIGSIZE)
    ax_L = fig.add_axes(AX_POS)
    ax_R = ax_L.twinx()

    ax_L.axvspan(T_PLOT_STEP2_END / 60.0, T_PLOT_END / 60.0,
                 color=COLOR_CIP_SHADE, zorder=0)

    uv_handle = cond_handle = None
    for i, eid in enumerate(REPLICATES):
        t_s, uv, cond = load_and_stitch(eid)
        t_min = t_s / 60.0
        h_uv = ax_L.plot(t_min, uv, color=COLOR_UV, linestyle='-',
                         linewidth=LW, alpha=ALPHA, zorder=3)[0]
        h_co = ax_R.plot(t_min, cond, color=COLOR_COND, linestyle='--',
                         linewidth=LW, alpha=ALPHA, zorder=2)[0]
        if i == 0:
            uv_handle, cond_handle = h_uv, h_co

    for t_s in (T_PLOT_LOAD_END, T_PLOT_WASH_END, T_PLOT_STEP1_END, T_PLOT_STEP2_END):
        ax_L.axvline(t_s / 60.0, color='gray', linestyle=':',
                     linewidth=0.8, alpha=0.7, zorder=1)

    ax_L.set_xlabel('Time (min)')
    ax_L.set_ylabel('UV (mAU)')
    ax_R.set_ylabel(r'Conductivity (mS cm$^{-1}$)')
    ax_L.set_title('Two-step elution')

    ax_L.set_xlim(left=0, right=T_PLOT_END / 60.0)
    ax_L.set_ylim(bottom=0)
    ax_R.set_ylim(bottom=0)
    ax_L.xaxis.set_major_locator(MultipleLocator(5))

    ax_L.text((T_PLOT_STEP2_END + T_PLOT_END) / 120.0, ax_L.get_ylim()[1] * 0.5,
              'CIP', ha='center', va='center', fontsize=FONTSIZE - 2,
              color='#6b7280', rotation=90)

    ax_L.legend([uv_handle, cond_handle], ['UV', 'Conductivity'],
                loc='upper left', bbox_to_anchor=(1.16, 1.0),
                framealpha=0.95, borderaxespad=0, fontsize=FONTSIZE - 2)

    for ext in ('png', 'pdf', 'svg'):
        out = OUT_DIR / f'fplc_step.{ext}'
        fig.savefig(out, dpi=DPI)
        print(f'  wrote {out}')
    plt.close(fig)


if __name__ == '__main__':
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_fplc_step()
    print('Done.')
