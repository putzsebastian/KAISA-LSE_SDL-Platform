# Figure 5 — analysis and plotting code

Source code and analysis scripts that regenerate **Figure 5** of the main
manuscript and **Figure S5.1** + **Table S5.1** of the Supplementary
Information for:

> Putz, S., Mama, A. K., Franzreb, M.
> *AI-Agent-assisted generation of experimental and simulative
> template-based workflows in Self-Driving Laboratories applied to
> chromatographic screening*. 

## Layout

```
Figure 5/
├── README.md
├── requirements.txt
├── analysis/
│   └── peak_analysis.py            # peak picks, widths, elution [NaCl] (Table S5.1)
├── figures/
│   ├── plot_isotherms.py                  # Fig. 5a, 5b
│   ├── plot_chromatograms.py              # Fig. 5c, 5d
│   ├── plot_fplc_gradient.py              # Fig. 5e
│   ├── plot_fplc_step.py                  # Fig. 5f
│   ├── plot_fplc_full_chromatograms_SI.py # Fig. S5.1
│   └── plot_peak_annotations.py           # QA overlay (peak picks)
├── data/                            # raw inputs (see Data section)
│   ├── isotherms/
│   ├── simulations/
│   └── fplc/
└── output/                          # generated figures + analysis output
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Tested with Python 3.11 on Ubuntu 22.04.

## Reproducing Figure 5

Run any script from inside the repo; default paths assume the layout above.

```bash
# Panels a, b — isotherms
python figures/plot_isotherms.py

# Panels c, d — simulated chromatograms
python figures/plot_chromatograms.py

# Panel e — gradient FPLC triplicate
python figures/plot_fplc_gradient.py

# Panel f — step FPLC triplicate
python figures/plot_fplc_step.py

# Supplementary Fig. S5.1 — full chromatograms with all CIP phases
python figures/plot_fplc_full_chromatograms_SI.py

# Supplementary Table S5.1 — peak retention, widths, elution [NaCl]
python analysis/peak_analysis.py

# Internal QA overlay — peak-pick verification (not in manuscript)
python figures/plot_peak_annotations.py
```

Each script accepts `--data-dir` and `--output-dir` overrides:

```bash
python figures/plot_isotherms.py --data-dir /path/to/data --output-dir /tmp/figs
```

See `--help` on any script for details.

## Data


| Subfolder | Files |
|---|---|
| `data/isotherms/` | `processed_aggregated_{5016,5020,5024,5025}.csv`, `langmuir_fits_{5016,5020,5024,5025}.csv` |
| `data/simulations/` | `results_5932.json` (gradient), `results_5936.json` (step) |
| `data/fplc/` | `akta_results_{5520,5522,5523}.json` (gradient triplicate), `akta_results_{5545,5549,5553}.json` (step triplicate) |

Numeric eLabFTW experiment IDs are preserved as filenames for direct
traceability to the SDL provenance database.

## Scope of the plotted window

The simulated and measured panels cover the same phases: load, wash, elution
(gradient or two steps) and the 2000 mM high-salt CIP step. The alkaline CIP that
follows experimentally is outside the domain of the mobile phase modulator model,
which carries a single modulator and no pH dependence, and is therefore not
simulated; it is shown in full in Supplementary Fig. S5.1, where the shaded
rectangle marks the window reproduced in the main-text panels.

The measured time axis is offset by the 780 s of pump washes and equilibration
that precede loading and have no counterpart in the simulation, so that both
clocks start at the beginning of the load. For the two-step method the two 60 s
pump-wash transitions between elution phases are excised and the axis stitched,
so that the measured and simulated timelines coincide exactly.

## Analysis methodology

Peak retention times are local maxima within phase-specific time windows
(`scipy.signal.find_peaks`); peak widths are at half prominence
(`scipy.signal.peak_widths`, `rel_height=0.5`).

Resolutions are not computed. With the corrected maximum binding capacities the
simulations predict a single eluting protein under both elution strategies, so a
resolution between two simulated peaks is undefined; and the identity of the two
measured peaks was not established experimentally, since UV detection at 280 nm is
not protein-specific, so a resolution between them cannot be attributed to a
protein pair. Measured peaks are therefore reported as peak 1 and peak 2 without
assignment to a component.

Conductivity is converted to NaCl concentration per replicate using
buffer-anchored calibration (three-point piecewise for the step elution,
two-point linear for the gradient). Full methodological description in
Supplementary Section S5.1.

## Note on the simulation inputs

The simulations deposited here use maximum binding capacities normalised to the
particle skeleton volume, as required by the CADET solid-phase convention. An
earlier version of this analysis passed capacities normalised to the packed bed
volume, which underestimates them by a factor of 1/[(1 − ε_b)(1 − ε_p)] and
caused both proteins to be predicted to elute within the salt range applied. The
results files listed above are those of eLabFTW experiments 5932 (gradient) and
5936 (two-step), which use the corrected values.

## License

Code: **MIT** (see `LICENSE`).
Data: **CC-BY 4.0** (via Zenodo deposition).

## Citation

If you use this code, please cite the manuscript above and the Zenodo
archive (DOI: **TBD**).
