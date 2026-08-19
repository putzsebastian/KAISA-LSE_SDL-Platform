"""
CADET simulation chromatograms -- panels c and d of the manuscript figure.

- Panel c: Gradient elution (linear NaCl 0 -> 2000 mM), followed by the 2000 mM CIP step
- Panel d: Two-step elution (650 mM, then 1600 mM), followed by the 2000 mM CIP step

Changed relative to the previous version:
  * the corrected maximum binding capacities are used (normalised to the particle skeleton
    volume rather than to the packed bed), which is what the simulations now reflect;
  * the CIP phase is included and shaded, because the model has something to say about it:
    2000 mM NaCl does not elute transferrin. Only the salt step is simulated; the alkaline
    wash that follows it experimentally is outside the domain of the MPM model.

Protein concentrations are converted from CADET's internal unit (mol/m^3, which equals
mmol/L) to g/L using the molecular weights recorded in the eLabFTW metadata. NaCl is left
in mM since mol/m^3 == mM for a monovalent salt.
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
SIM_DIR = DATA_DIR / 'simulations'

# Component molecular weights (g/mol)
MW_OVALBUMIN = 44290     # eLabFTW: component_1_molecular_weight
MW_TRANSFERRIN = 80000   # eLabFTW: component_2_molecular_weight

SIMULATIONS = {
    'gradient': {
        'results_file': 'results_gradient.json',
        'title': 'Gradient elution',
        # Phase durations [s] from eLabFTW experiment 5932
        'phases': [
            ('Load', 150),      # load_duration
            ('Wash', 346),      # wash_duration
            ('Elute', 2309),    # elute_duration  (gradient 0 -> 2000 mM)
            ('CIP', 346),       # CIP_duration    (2000 mM)
        ],
    },
    'step': {
        'results_file': 'results_step.json',
        'title': 'Two-step elution',
        # Phase durations [s] from eLabFTW experiment 5936
        'phases': [
            ('Load', 150),      # load_duration
            ('Wash', 346),      # wash_duration
            ('Step 1', 404),    # elution_step_1_duration  (650 mM)
            ('Step 2', 692),    # elution_step_2_duration  (1600 mM)
            ('CIP', 692),       # step5_duration           (2000 mM)
        ],
    },
}

COLOR_OVALBUMIN = '#1d4ed8'   # blue   (matches isotherm 0 mM)
COLOR_TRANSFERRIN = '#dc2626'  # red    (matches isotherm 300 mM)
COLOR_NACL = '#0d9488'         # dark teal (distinct from both proteins)
COLOR_CIP_SHADE = '#f3f4f6'
LS_PROTEIN = '-'
LS_NACL = '--'
LW = 1.7

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


def load_results(filename):
    with open(SIM_DIR / filename) as f:
        d = json.load(f)
    co = d['column_outlet']
    return {
        't_s': np.array(co['time']),
        'c_salt': np.array(co['concentration']['component_0']),
        'c_ova': np.array(co['concentration']['component_1']),
        'c_tra': np.array(co['concentration']['component_2']),
    }


def phase_boundaries_seconds(phases):
    """Cumulative phase boundary times in seconds (excluding 0 and end)."""
    cum = np.cumsum([d for _, d in phases])
    return cum[:-1]


def cip_start_seconds(phases):
    """Start of the final (CIP) phase, in seconds."""
    return float(np.cumsum([d for _, d in phases])[-2])


def plot_chromatogram(key):
    cfg = SIMULATIONS[key]
    data = load_results(cfg['results_file'])

    t_min = data['t_s'] / 60.0
    ova_gL = data['c_ova'] * MW_OVALBUMIN / 1000.0
    tra_gL = data['c_tra'] * MW_TRANSFERRIN / 1000.0
    salt_mM = data['c_salt']

    fig = plt.figure(figsize=FIGSIZE)
    ax_L = fig.add_axes(AX_POS)
    ax_R = ax_L.twinx()

    # CIP window shaded: the model describes the salt step only
    t_cip = cip_start_seconds(cfg['phases']) / 60.0
    ax_L.axvspan(t_cip, t_min[-1], color=COLOR_CIP_SHADE, zorder=0)

    ax_L.plot(t_min, ova_gL, color=COLOR_OVALBUMIN, linestyle=LS_PROTEIN,
              linewidth=LW, label='Ovalbumin', zorder=3)
    ax_L.plot(t_min, tra_gL, color=COLOR_TRANSFERRIN, linestyle=LS_PROTEIN,
              linewidth=LW, label='Transferrin', zorder=3)
    ax_R.plot(t_min, salt_mM, color=COLOR_NACL, linestyle=LS_NACL,
              linewidth=LW, label='NaCl', zorder=2)

    for t_s in phase_boundaries_seconds(cfg['phases']):
        ax_L.axvline(t_s / 60.0, color='gray', linestyle=':',
                     linewidth=0.8, alpha=0.7, zorder=1)

    ax_L.set_xlabel('Time (min)')
    ax_L.set_ylabel(r'Protein concentration (g L$^{-1}$)')
    ax_R.set_ylabel('NaCl (mM)')
    ax_L.set_title(cfg['title'])

    ax_L.set_xlim(left=0, right=t_min[-1])
    ax_L.set_ylim(bottom=0)
    ax_R.set_ylim(bottom=0)
    ax_L.xaxis.set_major_locator(MultipleLocator(5))

    # CIP label inside the shaded band
    ax_L.text(t_cip + (t_min[-1] - t_cip) / 2, ax_L.get_ylim()[1] * 0.5, 'CIP',
              ha='center', va='center', fontsize=FONTSIZE - 2, color='#6b7280',
              rotation=90)

    handles_L, labels_L = ax_L.get_legend_handles_labels()
    handles_R, labels_R = ax_R.get_legend_handles_labels()
    ax_L.legend(handles_L + handles_R, labels_L + labels_R,
                loc='upper left', bbox_to_anchor=(1.16, 1.0),
                framealpha=0.95, borderaxespad=0, fontsize=FONTSIZE - 2)

    for ext in ('png', 'pdf', 'svg'):
        out = OUT_DIR / f'chromatogram_{key}.{ext}'
        fig.savefig(out, dpi=DPI)
        print(f'  wrote {out}')
    plt.close(fig)


if __name__ == '__main__':
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key in SIMULATIONS:
        print(f'Plotting {key} ...')
        plot_chromatogram(key)
    print('Done.')
