"""
Supplementary figure: full chromatograms (entire ÄKTA method, including
all CIP phases) for the gradient and step elution validation runs.

Shows that under both methods the bulk of the loaded protein is released only during the
CIP NaOH phase, beyond the simulation-equivalent window depicted in the main-text panels.
The shaded rectangle in each panel indicates the time window shown in the corresponding
main-text panel, which now extends through the 2000 mM high-salt CIP step: that step is
still within the domain of the mobile phase modulator model, whereas the alkaline wash
that follows it is not.

Triplicate traces are baseline-corrected (UV mean over Load+Wash window
subtracted), as in the main-text figures.
"""
from pathlib import Path
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import MultipleLocator

# Default paths assume the repo layout:
#   <repo_root>/data/{isotherms,simulations,fplc}
#   <repo_root>/output/
# Overridable from the command line via --data-dir and --output-dir.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parent
DATA_DIR = _REPO_ROOT / 'data'
OUT_DIR  = _REPO_ROOT / 'output'
FPLC_DIR     = DATA_DIR / 'fplc'
# ------------------------------------------------------------------
# Phase boundaries (cumulative seconds from run start)
# ------------------------------------------------------------------
# Gradient method (Process 36) -- from Orbit script.
# Tuples are (t_end_s, label).
GRADIENT_PHASES = [
    (  60, 'Pump wash B'),
    ( 180, 'Pump wash A'),
    ( 780, 'Equilibration'),
    ( 930, 'Load'),
    (1276, 'Wash'),
    (3585, 'Elute (gradient)'),
    (3931, 'CIP high salt'),
    (3991, 'Pump wash NaOH'),
    (4568, 'CIP NaOH'),
    (5168, 'CIP NaOH (hold)'),
    (5228, r'Pump wash H$_2$O'),
    (5690, r'CIP H$_2$O'),
    (5750, 'Pump wash HCl'),
    (6327, 'CIP HCl'),
    (6387, r'Pump wash H$_2$O'),
    (6849, r'CIP H$_2$O'),
]

# Step method (Process 37) -- exact phase boundaries from the Orbit script.
# (Total method 6102 s; the data extends ~50 s further due to AKTA overhead.)
STEP_PHASES = [
    (  60, 'Pump wash B'),
    ( 180, 'Pump wash A'),
    ( 780, 'Equilibration'),
    ( 930, 'Load'),
    (1276, 'Wash'),
    (1336, 'Cond. elute 1'),
    (1740, 'Step 1 (650 mM)'),
    (1800, 'Cond. elute 2'),
    (2492, 'Step 2 (1600 mM)'),
    (3184, 'CIP high salt'),
    (3244, 'Pump wash NaOH'),
    (3821, 'CIP NaOH'),
    (4421, 'CIP NaOH (hold)'),
    (4481, r'Pump wash H$_2$O'),
    (4943, r'CIP H$_2$O'),
    (5003, 'Pump wash HCl'),
    (5580, 'CIP HCl'),
    (5640, r'Pump wash H$_2$O'),
    (6102, r'CIP H$_2$O'),
]

# Replicate offsets (gradient run 5520 started 254 s later than 5522/5523)
GRAD_REPLICATES = {5520: 254, 5522: 0, 5523: 0}
STEP_REPLICATES = [5545, 5549, 5553]

# Main-figure (Fig. 5 e/f) windows -- the part shown in main text.
# Both now run through the 2000 mM CIP high-salt step, so that they match the simulated
# panels, which include that step. The alkaline CIP that follows is outside the model.
GRAD_MAIN_WINDOW_S = (780, 3931)   # Load .. CIP high salt end
STEP_MAIN_WINDOW_S = (780, 3184)   # Load .. CIP high salt end (5 stitched phases)

# ------------------------------------------------------------------
# Styling
# ------------------------------------------------------------------
COLOR_UV   = '#1f2937'   # near-black slate
COLOR_COND = '#0d9488'   # dark teal (matches main fig)
LW    = 1.1
ALPHA = 0.75

FONTSIZE = 11
plt.rcParams.update({
    'font.size':       FONTSIZE,
    'axes.labelsize':  FONTSIZE,
    'axes.titlesize':  FONTSIZE + 1,
    'legend.fontsize': FONTSIZE - 2,
    'xtick.labelsize': FONTSIZE - 1,
    'ytick.labelsize': FONTSIZE - 1,
    'axes.linewidth':  0.8,
    'xtick.direction': 'in',
    'ytick.direction': 'in',
})


def load_full(eid, shift_s=0):
    """Load the full ÄKTA chromatogram; apply per-replicate time shift,
    and baseline-correct UV using the Load+Wash window (780-1276 s)."""
    with open(FPLC_DIR / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t    = np.array(d['time']) + shift_s
    uv   = np.array([v if v is not None else np.nan for v in d['uv1']])
    cond = np.array(d['cond'])
    bl_mask = (t >= 780) & (t < 1276)
    if bl_mask.sum() > 0:
        baseline = np.nanmean(uv[bl_mask])
        uv = uv - baseline
    return t / 60.0, uv, cond


def draw_phase_lines(ax, phases, y_top_for_labels):
    """Vertical dotted lines at every phase boundary; rotated labels
    centred above each phase span. All phases labelled (including the
    short pump-wash interludes between major phases)."""
    prev_end_s = 0.0
    for t_end_s, label in phases:
        # vertical line at end of this phase
        ax.axvline(t_end_s / 60.0, color='gray', linestyle=':',
                   linewidth=0.7, alpha=0.6, zorder=1)
        # label centred over phase span, rotated 90 deg
        if label:
            t_mid = 0.5 * (prev_end_s + t_end_s) / 60.0
            ax.text(t_mid, y_top_for_labels, label,
                    ha='left', va='bottom',
                    fontsize=FONTSIZE - 3, color='#374151',
                    rotation=90, rotation_mode='anchor',
                    clip_on=False)
        prev_end_s = t_end_s


def shade_main_window(ax, window_s, ymax):
    t_lo, t_hi = window_s
    ax.add_patch(Rectangle(
        (t_lo / 60.0, 0), (t_hi - t_lo) / 60.0, ymax,
        facecolor='#fbbf24', alpha=0.10, edgecolor='#f59e0b',
        linewidth=0.8, linestyle='--', zorder=0,
    ))
    ax.text(
        0.5 * (t_lo + t_hi) / 60.0, ymax * 0.92,
        'Fig. 5 window',
        ha='center', va='center',
        fontsize=FONTSIZE - 3, color='#92400e',
        bbox=dict(boxstyle='round,pad=0.2', fc='white',
                  ec='#f59e0b', lw=0.6),
        zorder=4,
    )


def plot_full(ax, replicates_or_dict, phases, main_window_s, title):
    """Render one full-chromatogram panel onto ax (and its twinx)."""
    ax_R = ax.twinx()

    # Plot each replicate
    uv_max_all = 0.0
    uv_handle = cond_handle = None
    if isinstance(replicates_or_dict, dict):
        items = replicates_or_dict.items()
    else:
        items = [(eid, 0) for eid in replicates_or_dict]

    for i, (eid, shift_s) in enumerate(items):
        t_min, uv, cond = load_full(eid, shift_s=shift_s)
        h_uv = ax.plot(  t_min, uv,   color=COLOR_UV,   linestyle='-',  linewidth=LW, alpha=ALPHA, zorder=3)[0]
        h_co = ax_R.plot(t_min, cond, color=COLOR_COND, linestyle='--', linewidth=LW, alpha=ALPHA, zorder=2)[0]
        if i == 0:
            uv_handle, cond_handle = h_uv, h_co
        uv_max_all = max(uv_max_all, np.nanmax(uv))

    # Decide y-range for UV (leave room for labels above)
    uv_ylim = uv_max_all * 1.15
    ax.set_ylim(0, uv_ylim)
    ax_R.set_ylim(0, None)

    # Main-figure window highlight
    shade_main_window(ax, main_window_s, uv_ylim)

    # Phase boundaries + rotated labels at top
    label_y = uv_ylim * 1.02
    draw_phase_lines(ax, phases, label_y)

    # Axes labels
    ax.set_xlabel('Time (min)')
    ax.set_ylabel('UV (mAU)')
    ax_R.set_ylabel(r'Conductivity (mS cm$^{-1}$)')
    ax.set_title(title, pad=80)  # extra pad so rotated phase labels fit

    # x range and ticks
    t_total_min = phases[-1][0] / 60.0
    ax.set_xlim(0, t_total_min)
    ax.xaxis.set_major_locator(MultipleLocator(10))

    # Legend (top-right of plot area)
    ax.legend(
        [uv_handle, cond_handle], ['UV', 'Conductivity'],
        loc='upper right',
        framealpha=0.95,
        fontsize=FONTSIZE - 2,
    )


# ------------------------------------------------------------------
# Build the figure
# ------------------------------------------------------------------
def main():
    fig, axes = plt.subplots(
        2, 1, figsize=(13, 10), dpi=300,
        gridspec_kw=dict(hspace=0.75, left=0.06, right=0.93, top=0.91, bottom=0.06),
    )

    plot_full(
        axes[0], GRAD_REPLICATES, GRADIENT_PHASES,
        GRAD_MAIN_WINDOW_S,
        title='Gradient elution -- full chromatogram (n = 3)',
    )

    plot_full(
        axes[1], STEP_REPLICATES, STEP_PHASES,
        STEP_MAIN_WINDOW_S,
        title='Step elution -- full chromatogram (n = 3)',
    )

    for ext in ('png', 'pdf', 'svg'):
        out = OUT_DIR / f'fplc_full_chromatograms_SI.{ext}'
        fig.savefig(out, dpi=300, bbox_inches='tight')
        print(f'  wrote {out}')
    plt.close(fig)


def _parse_cli_args():
    import argparse
    p = argparse.ArgumentParser(
        description=(__doc__ or '').strip().splitlines()[0] if __doc__ else '')
    p.add_argument('--data-dir', type=Path, default=DATA_DIR,
                   help=('Root data directory (default: %(default)s). '
                         'Must contain the appropriate subfolders: '
                         'fplc.'))
    p.add_argument('--output-dir', type=Path, default=OUT_DIR,
                   help='Output directory (default: %(default)s).')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse_cli_args()
    DATA_DIR = args.data_dir
    OUT_DIR  = args.output_dir
    FPLC_DIR     = DATA_DIR / 'fplc'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    main()
    print('Done.')
