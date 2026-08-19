"""
Adsorption isotherm plots for Ovalbumin and Transferrin.

Two separate figures, one per protein:
- Data points from processed_aggregated_*.csv with x- and y-error bars
- Langmuir fit curves from langmuir_fits_*.csv (R^2 taken from fit file as-is)
- Ovalbumin 500 mM NaCl: data points only (no fit -- experimentally no binding)
"""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
# Default paths assume the repo layout:
#   <repo_root>/data/{isotherms,simulations,fplc}
#   <repo_root>/output/
# Overridable from the command line via --data-dir and --output-dir.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parent
DATA_DIR = _REPO_ROOT / 'data'
OUT_DIR  = _REPO_ROOT / 'output'
ISOTHERM_DIR = DATA_DIR / 'isotherms'
# Combine the two experiment IDs per protein (low-salt + 500 mM runs)
PROTEINS = {
    'Ovalbumin':   [5016, 5020],
    'Transferrin': [5025, 5024],
}

# Series for which we deliberately omit the Langmuir fit curve
NO_FIT = {('Ovalbumin', 500.0)}

# Curated sequential palette: blue -> purple -> red (no washed-out middle)
SALT_COLORS = {
    0:   '#1d4ed8',   # deep blue
    100: '#7e22ce',   # violet
    200: '#c026d3',   # magenta
    300: '#dc2626',   # red
    500: '#7f1d1d',   # dark red / maroon
}
# Distinct markers per salt concentration
SALT_MARKERS = {
    0:   'o',   # circle
    100: 's',   # square
    200: '^',   # triangle up
    300: 'D',   # diamond
    500: 'v',   # triangle down
}
SALT_LEVELS = [0, 100, 200, 300, 500]

# Plot styling
# Figure size and axes position are fixed so that both protein plots are
# saved at identical dimensions and the plot areas align exactly when placed
# side-by-side. The right portion of the figure is reserved for the legend.
FIGSIZE  = (8.0, 4.5)
AX_POS   = (0.085, 0.135, 0.555, 0.795)  # [left, bottom, width, height]
DPI      = 300
MS       = 5
LW       = 1.6
CAPSIZE  = 2
ELW      = 0.8
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
    'xtick.top':       True,
    'ytick.right':     True,
})

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def langmuir(c, qmax, K):
    """Langmuir isotherm: q = qmax * K * c / (1 + K * c)."""
    return qmax * K * c / (1.0 + K * c)

def load_protein(name):
    """Load and concatenate aggregated data + Langmuir fits for a protein."""
    eids = PROTEINS[name]
    agg = pd.concat(
        [pd.read_csv(ISOTHERM_DIR / f'processed_aggregated_{e}.csv') for e in eids],
        ignore_index=True,
    )
    fit = pd.concat(
        [pd.read_csv(ISOTHERM_DIR / f'langmuir_fits_{e}.csv') for e in eids],
        ignore_index=True,
    )
    return agg, fit

# ------------------------------------------------------------------
# Plotting
# ------------------------------------------------------------------
def plot_protein(name, show_legend=True, show_r2_in_legend=False):
    agg, fit = load_protein(name)

    fig = plt.figure(figsize=FIGSIZE)
    ax  = fig.add_axes(AX_POS)

    # Smooth x range for fit curves
    x_max = agg['cE_mean'].max() * 1.05

    for salt in SALT_LEVELS:
        color = SALT_COLORS[salt]
        sub   = agg[agg['salt_conc'] == salt]
        if sub.empty:
            continue

        row     = fit[fit['salt_conc'] == salt]
        has_fit = (not row.empty) and ((name, float(salt)) not in NO_FIT)

        if has_fit:
            qmax = float(row['qmax'].iloc[0])
            K    = float(row['K'].iloc[0])
            r2   = float(row['r2'].iloc[0])
            if show_r2_in_legend:
                label = f'{int(salt)} mM NaCl (R²={r2:.3f})'
            else:
                label = f'{int(salt)} mM NaCl'
        else:
            label = f'{int(salt)} mM NaCl'

        # Data points with x- and y- error bars
        ax.errorbar(
            sub['cE_mean'], sub['q_mean'],
            xerr=sub['cE_std'], yerr=sub['q_std'],
            fmt=SALT_MARKERS[salt], color=color, markersize=MS,
            capsize=CAPSIZE, elinewidth=ELW, linewidth=0,
            label=label, zorder=3,
        )

        # Fit curve
        if has_fit:
            c_smooth = np.linspace(0, x_max, 400)
            q_smooth = langmuir(c_smooth, qmax, K)
            ax.plot(c_smooth, q_smooth, color=color, linewidth=LW, zorder=2)

    ax.set_xlabel(r'$c^{*}$ (g L$^{-1}$)')
    ax.set_ylabel(r'$q$ (g L$^{-1}$)')
    ax.set_title(name)
    ax.xaxis.set_major_locator(MultipleLocator(1))

    # Legend placed outside right edge, top-aligned -- standard scientific
    # convention for external legends; avoids any overlap with data points
    # and fit curves.
    if show_legend:
        ax.legend(
            loc='upper left',
            bbox_to_anchor=(1.02, 1.0),
            framealpha=0.95,
            borderaxespad=0,
            fontsize=FONTSIZE - 2,
        )

    # NOTE: deliberately NOT calling tight_layout / bbox_inches='tight' --
    # we want both protein figures saved at IDENTICAL dimensions with the
    # plot area at an identical position, so the panels align exactly when
    # placed side-by-side.
    for ext in ('png', 'pdf', 'svg'):
        out = OUT_DIR / f'isotherm_{name.lower()}.{ext}'
        fig.savefig(out, dpi=DPI)
        print(f'  wrote {out}')
    plt.close(fig)

# ------------------------------------------------------------------
def _parse_cli_args():
    import argparse
    p = argparse.ArgumentParser(
        description=(__doc__ or '').strip().splitlines()[0] if __doc__ else '')
    p.add_argument('--data-dir', type=Path, default=DATA_DIR,
                   help=('Root data directory (default: %(default)s). '
                         'Must contain the appropriate subfolders: '
                         'isotherms.'))
    p.add_argument('--output-dir', type=Path, default=OUT_DIR,
                   help='Output directory (default: %(default)s).')
    return p.parse_args()


if __name__ == '__main__':
    args = _parse_cli_args()
    DATA_DIR = args.data_dir
    OUT_DIR  = args.output_dir
    ISOTHERM_DIR = DATA_DIR / 'isotherms'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Both panels render with the legend (no R^2; R^2 values are reported
    # in a separate table). Both figures are saved at identical dimensions
    # so the plot areas align when placed side-by-side -- the user can
    # crop the legend from one panel in the final composition.
    for protein in PROTEINS:
        print(f'Plotting {protein} ...')
        plot_protein(protein, show_legend=True, show_r2_in_legend=False)
    print('Done.')
