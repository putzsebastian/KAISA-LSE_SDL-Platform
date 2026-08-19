"""
FPLC validation chromatogram -- panel e of the manuscript figure.

Linear gradient elution on SDL 2 (AEKTA Pure), performed in triplicate
(eLabFTW experiments 5520, 5522, 5523).

Signals:
- UV1   (mAU)   on the LEFT y-axis  -- experimental analogue of protein conc.
- Cond. (mS/cm) on the RIGHT y-axis -- experimental analogue of NaCl conc.

Changed relative to the previous version: the crop now extends through the 2000 mM CIP
step (346 s), so the window matches the simulated panel exactly. The subsequent alkaline
CIP is not shown here; it lies outside the domain of the model and is shown in full in
the Supplementary Information.

Replicate 5520 has a 254 s offset relative to 5522/5523 (total recording 6637 s vs
6891 s; all features shifted earlier by 254 s in the data). Per-replicate phase
boundaries are used to align all three traces.
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
# Per-replicate phase boundaries (AEKTA cumulative seconds).
#   equilibration ends       ->  load starts at   780 s
#   load_inject     150 s    ->  wash starts at   930 s
#   wash            346 s    ->  gradient starts  1276 s
#   elute_gradient 2309 s    ->  CIP high salt    3585 s
#   CIP high salt   346 s    ->  end of window    3931 s
# Replicate 5520 has 254 s less pre-equilibration -- boundaries shifted earlier.
# ------------------------------------------------------------------
REPLICATE_INFO = {
    5520: {'load_start': 526, 'window_end': 3677},
    5522: {'load_start': 780, 'window_end': 3931},
    5523: {'load_start': 780, 'window_end': 3931},
}

# Boundaries on the plot (simulation-aligned) timeline:
T_PLOT_LOAD_END = 150      # Load
T_PLOT_WASH_END = 496      # + Wash
T_PLOT_GRADIENT_END = 2805  # + Gradient
T_PLOT_END = 3151          # + CIP high salt

COLOR_UV = '#1f2937'       # near-black slate
COLOR_COND = '#0d9488'     # dark teal (matches simulated NaCl colour)
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


def load_and_crop(eid):
    """Crop one replicate to Load + Wash + Gradient + CIP and rebase time to t = 0.

    UV is baseline-corrected by subtracting its mean over the Load+Wash window, where no
    protein is expected to elute; this removes per-replicate post-AutoZero drift.
    """
    info = REPLICATE_INFO[eid]
    with open(FPLC_DIR / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t = np.array(d['time'])
    uv = np.array([v if v is not None else np.nan for v in d['uv1']])
    cond = np.array(d['cond'])

    mask = (t >= info['load_start']) & (t < info['window_end'])
    t_plot = t[mask] - info['load_start']
    uv_crop, cond_crop = uv[mask], cond[mask]

    baseline = np.nanmean(uv_crop[t_plot < T_PLOT_WASH_END])
    return t_plot, uv_crop - baseline, cond_crop


def plot_fplc_gradient():
    fig = plt.figure(figsize=FIGSIZE)
    ax_L = fig.add_axes(AX_POS)
    ax_R = ax_L.twinx()

    ax_L.axvspan(T_PLOT_GRADIENT_END / 60.0, T_PLOT_END / 60.0,
                 color=COLOR_CIP_SHADE, zorder=0)

    uv_handle = cond_handle = None
    for i, eid in enumerate(REPLICATE_INFO):
        t_s, uv, cond = load_and_crop(eid)
        t_min = t_s / 60.0
        h_uv = ax_L.plot(t_min, uv, color=COLOR_UV, linestyle='-',
                         linewidth=LW, alpha=ALPHA, zorder=3)[0]
        h_co = ax_R.plot(t_min, cond, color=COLOR_COND, linestyle='--',
                         linewidth=LW, alpha=ALPHA, zorder=2)[0]
        if i == 0:
            uv_handle, cond_handle = h_uv, h_co

    for t_s in (T_PLOT_LOAD_END, T_PLOT_WASH_END, T_PLOT_GRADIENT_END):
        ax_L.axvline(t_s / 60.0, color='gray', linestyle=':',
                     linewidth=0.8, alpha=0.7, zorder=1)

    ax_L.set_xlabel('Time (min)')
    ax_L.set_ylabel('UV (mAU)')
    ax_R.set_ylabel(r'Conductivity (mS cm$^{-1}$)')
    ax_L.set_title('Gradient elution')

    ax_L.set_xlim(left=0, right=T_PLOT_END / 60.0)
    ax_L.set_ylim(bottom=0)
    ax_R.set_ylim(bottom=0)
    ax_L.xaxis.set_major_locator(MultipleLocator(5))

    ax_L.text((T_PLOT_GRADIENT_END + T_PLOT_END) / 120.0, ax_L.get_ylim()[1] * 0.5,
              'CIP', ha='center', va='center', fontsize=FONTSIZE - 2,
              color='#6b7280', rotation=90)

    ax_L.legend([uv_handle, cond_handle], ['UV', 'Conductivity'],
                loc='upper left', bbox_to_anchor=(1.16, 1.0),
                framealpha=0.95, borderaxespad=0, fontsize=FONTSIZE - 2)

    for ext in ('png', 'pdf', 'svg'):
        out = OUT_DIR / f'fplc_gradient.{ext}'
        fig.savefig(out, dpi=DPI)
        print(f'  wrote {out}')
    plt.close(fig)


if __name__ == '__main__':
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_fplc_gradient()
    print('Done.')
