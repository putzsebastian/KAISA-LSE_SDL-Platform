"""
Peak retention times, widths and elution NaCl concentrations for the four chromatograms in
the manuscript figure (gradient simulated, two-step simulated, gradient measured, two-step
measured). These are the values quoted in the Results.

Replaces the earlier resolution calculation. Resolutions are no longer reported: with the
corrected binding capacities the simulations predict a single eluting protein under both
strategies, so a resolution between two simulated peaks is undefined, and the identity of
the two measured peaks was not established experimentally, so a resolution between them
cannot be attributed to a protein pair.

For the simulations the two protein traces are available separately, so peak
identification is trivial. Transferrin does not elute within the simulated cycle under
either strategy; this is reported as such rather than as a retention time, since the
maximum of an essentially flat trace is meaningless.

For the experiments UV at 280 nm is not protein-specific. Within the simulation-equivalent
window the two most prominent peaks are located and reported as peak 1 and peak 2, without
assignment to a protein. Under both methods the bulk of the loaded protein is released
only during the alkaline clean-in-place step, outside this window, so the peaks reported
here represent partial elution.
"""
from pathlib import Path
import json
import numpy as np
from scipy.signal import find_peaks, peak_widths

# Default paths assume the repo layout:
#   <repo_root>/data/{isotherms,simulations,fplc}
#   <repo_root>/output/
# Overridable from the command line via --data-dir and --output-dir.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
DATA_DIR = _REPO_ROOT / 'data'
OUT_DIR = _REPO_ROOT / 'output'
SIM_DIR = DATA_DIR / 'simulations'
FPLC_DIR = DATA_DIR / 'fplc'

# ---------------------------------------------------------------
# Configuration (matches the plot scripts)
# ---------------------------------------------------------------
MW_OVALBUMIN = 44290     # g/mol
MW_TRANSFERRIN = 80000   # g/mol

SIM_FILES = {
    'Gradient (simulation)': 'results_gradient.json',
    'Two-step (simulation)': 'results_step.json',
}

# A protein counts as eluting only if its trace rises meaningfully above zero; without
# this, argmax on an essentially flat trace returns the last time point.
SIM_ELUTION_THRESHOLD_GL = 1e-4   # g/L

# Gradient experiment: per-replicate load_start due to the 5520 offset. The window now
# runs through the 2000 mM CIP high-salt step, matching the simulation.
GRAD_INFO = {
    5520: {'load_start': 526, 'window_end': 3677},
    5522: {'load_start': 780, 'window_end': 3931},
    5523: {'load_start': 780, 'window_end': 3931},
}
GRAD_T_PLOT_WASH_END = 496       # s
GRAD_T_PLOT_GRADIENT_END = 2805  # s
GRAD_T_PLOT_END = 3151           # s

# Two-step experiment: same boundaries for all three replicates
STEP_REPLICATES = [5545, 5549, 5553]
STEP_T_LOAD_START = 780
STEP_T_WASH_END = 1276
STEP_T_STEP1_START = 1336
STEP_T_STEP1_END = 1740
STEP_T_STEP2_START = 1800
STEP_T_STEP2_END = 2492
STEP_T_CIP_END = 3184
STEP_T_PLOT_WASH_END = 496
STEP_T_PLOT_STEP1_END = 900
STEP_T_PLOT_STEP2_END = 1592
STEP_T_PLOT_END = 2284


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def width_at_half_prominence(t, y, i_peak):
    """Width of the peak at index i_peak, measured at half its prominence.

    For an isolated peak on a flat near-zero baseline this equals the FWHM; for shoulder
    peaks or peaks on a sloping baseline it gives the right answer where a naive
    FWHM-from-zero would not. Returns (width, t_left, t_right) in the units of t.
    """
    widths, _, left_ips, right_ips = peak_widths(y, [i_peak], rel_height=0.5)

    def ip_to_t(ip):
        i0 = int(np.floor(ip))
        i1 = min(i0 + 1, len(t) - 1)
        return t[i0] + (ip - i0) * (t[i1] - t[i0])

    t_left = ip_to_t(float(left_ips[0]))
    t_right = ip_to_t(float(right_ips[0]))
    return t_right - t_left, t_left, t_right


def find_peak_in_window(t, y, t_lo, t_hi, prominence=None):
    """Most prominent local maximum within [t_lo, t_hi].

    Returns (i_peak, t_peak, y_peak, w_half) or None if the window is too short.
    """
    idx_window = np.where((t >= t_lo) & (t <= t_hi))[0]
    if len(idx_window) < 3:
        return None
    y_w = y[idx_window]
    peaks, props = find_peaks(y_w, prominence=prominence)
    if len(peaks) == 0:
        i_local = int(np.argmax(y_w))          # edge case: no proper local maximum
    else:
        i_local = int(peaks[int(np.argmax(props['prominences']))])
    i_peak = int(idx_window[i_local])
    w, _, _ = width_at_half_prominence(t, y, i_peak)
    return i_peak, float(t[i_peak]), float(y[i_peak]), float(w)


def cond_to_NaCl(cond_value, cond_baseline, cond_high, NaCl_high):
    """Two-point linear conductivity -> NaCl conversion."""
    if cond_high == cond_baseline:
        return float('nan')
    return (cond_value - cond_baseline) / (cond_high - cond_baseline) * NaCl_high


def cond_to_NaCl_piecewise(cond_value, points):
    """Piecewise linear conductivity -> NaCl conversion from (cond, NaCl) calibration
    points sorted by conductivity. Linear extrapolation outside the range."""
    conds = np.array([p[0] for p in points], dtype=float)
    NaCls = np.array([p[1] for p in points], dtype=float)
    return float(np.interp(cond_value, conds, NaCls))


# ---------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------
def analyze_sim(label, filename):
    with open(SIM_DIR / filename) as f:
        d = json.load(f)
    co = d['column_outlet']
    t_min = np.array(co['time']) / 60.0
    c_salt = np.array(co['concentration']['component_0'])   # mol/m^3 == mM
    traces = {
        'Ovalbumin': np.array(co['concentration']['component_1']) * MW_OVALBUMIN / 1000.0,
        'Transferrin': np.array(co['concentration']['component_2']) * MW_TRANSFERRIN / 1000.0,
    }

    out = {'label': label, 'components': {}}
    for name, c in traces.items():
        if c.max() < SIM_ELUTION_THRESHOLD_GL:
            out['components'][name] = {'elutes': False}
            continue
        i = int(np.argmax(c))
        w, _, _ = width_at_half_prominence(t_min, c, i)
        out['components'][name] = {
            'elutes': True,
            't_R': float(t_min[i]), 'W_half': float(w),
            'c_peak': float(c[i]), 'NaCl': float(c_salt[i]),
        }
    return out


# ---------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------
def load_step(eid):
    """Crop to the simulated phases and stitch out the two 60 s pump washes."""
    with open(FPLC_DIR / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t = np.array(d['time'])
    uv = np.array([v if v is not None else np.nan for v in d['uv1']])
    cond = np.array(d['cond'])

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
    cond_s = np.concatenate([cond[m] for m, _, _ in segments])
    bl = np.nanmean(uv_s[t_s < STEP_T_PLOT_WASH_END])
    return t_s / 60.0, uv_s - bl, cond_s


def load_grad(eid):
    info = GRAD_INFO[eid]
    with open(FPLC_DIR / f'akta_results_{eid}.json') as f:
        d = json.load(f)
    t = np.array(d['time'])
    uv = np.array([v if v is not None else np.nan for v in d['uv1']])
    cond = np.array(d['cond'])
    m = (t >= info['load_start']) & (t < info['window_end'])
    t_plot = t[m] - info['load_start']
    bl = np.nanmean(uv[m][t_plot < GRAD_T_PLOT_WASH_END])
    return t_plot / 60.0, uv[m] - bl, cond[m]


def analyze_exp_step():
    """Peak 1 in the step-1 window, peak 2 in the step-2 window."""
    rows = []
    for eid in STEP_REPLICATES:
        t, uv, cond = load_step(eid)
        p1 = find_peak_in_window(t, uv, 8.27, 15.0, prominence=1.0)
        p2 = find_peak_in_window(t, uv, 15.0, 26.5, prominence=1.0)
        if p1 is None or p2 is None:
            continue
        i1, t1, y1, w1 = p1
        i2, t2, y2, w2 = p2
        # Three-point conductivity calibration anchored to the known buffer compositions
        # during the run; more accurate than two-point extrapolation, since the
        # conductivity-NaCl relation is slightly sublinear at high concentration.
        bl_cond = float(np.nanmean(cond[t < STEP_T_PLOT_WASH_END / 60.0]))
        cond_650 = float(np.nanmean(cond[(t > 14.0) & (t < 15.0)]))
        cond_1600 = float(np.nanmean(cond[(t > 23.0) & (t < 25.0)]))
        cal = [(bl_cond, 0.0), (cond_650, 650.0), (cond_1600, 1600.0)]
        rows.append((eid,
                     t1, w1, y1, float(cond[i1]), cond_to_NaCl_piecewise(cond[i1], cal),
                     t2, w2, y2, float(cond[i2]), cond_to_NaCl_piecewise(cond[i2], cal)))
    return rows


def analyze_exp_grad():
    """Peak 1 in the early gradient, peak 2 in the late gradient."""
    rows = []
    for eid in GRAD_INFO:
        t, uv, cond = load_grad(eid)
        p1 = find_peak_in_window(t, uv, 18.0, 26.0, prominence=0.3)
        p2 = find_peak_in_window(t, uv, 26.0, 40.0, prominence=0.3)
        if p1 is None or p2 is None:
            continue
        i1, t1, y1, w1 = p1
        i2, t2, y2, w2 = p2
        # Two-point calibration: baseline during the wash -> 0 mM; the gradient plateau
        # just before the CIP step -> 2000 mM (100 % B).
        bl_cond = float(np.nanmean(cond[t < GRAD_T_PLOT_WASH_END / 60.0]))
        plateau = ((t > GRAD_T_PLOT_GRADIENT_END / 60.0 - 2.0)
                   & (t < GRAD_T_PLOT_GRADIENT_END / 60.0))
        cond_high = float(np.nanmean(cond[plateau]))
        rows.append((eid,
                     t1, w1, y1, float(cond[i1]),
                     cond_to_NaCl(cond[i1], bl_cond, cond_high, 2000.0),
                     t2, w2, y2, float(cond[i2]),
                     cond_to_NaCl(cond[i2], bl_cond, cond_high, 2000.0)))
    return rows


# ---------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------
def fmt_sim(r):
    print(f"  {r['label']}")
    for name, c in r['components'].items():
        if not c['elutes']:
            print(f"    {name:<12} does not elute within the simulated cycle")
            continue
        print(f"    {name:<12} t_R = {c['t_R']:6.2f} min  W_0.5 = {c['W_half']:5.2f} min  "
              f"c_peak = {c['c_peak']:.4f} g/L  [NaCl] = {c['NaCl']:6.1f} mM")
    print()


def fmt_exp(label, rows):
    print(f"  {label}")
    print(f"    {'EID':>5}  {'t_R1':>6}  {'W1':>5}  {'UV1':>6}  {'cond1':>7}  {'NaCl1':>6}"
          f"  {'t_R2':>6}  {'W2':>5}  {'UV2':>6}  {'cond2':>7}  {'NaCl2':>6}")
    print(f"    {'':>5}  {'(min)':>6}  {'(min)':>5}  {'(mAU)':>6}  {'(mS/cm)':>7}  "
          f"{'(mM)':>6}  {'(min)':>6}  {'(min)':>5}  {'(mAU)':>6}  {'(mS/cm)':>7}  "
          f"{'(mM)':>6}")
    t1s, t2s, s1s, s2s = [], [], [], []
    for eid, t1, w1, y1, c1, s1, t2, w2, y2, c2, s2 in rows:
        t1s.append(t1); t2s.append(t2); s1s.append(s1); s2s.append(s2)
        print(f"    {eid:>5}  {t1:6.2f}  {w1:5.2f}  {y1:6.2f}  {c1:7.1f}  {s1:6.0f}"
              f"  {t2:6.2f}  {w2:5.2f}  {y2:6.2f}  {c2:7.1f}  {s2:6.0f}")

    def ms(v):
        a = np.array(v)
        return a.mean(), (a.std(ddof=1) if len(a) > 1 else 0.0)

    (m1, e1), (m2, e2) = ms(t1s), ms(t2s)
    (n1, f1), (n2, f2) = ms(s1s), ms(s2s)
    print(f"    mean   peak 1: t_R = {m1:.2f} +/- {e1:.2f} min, "
          f"[NaCl] = {n1:.0f} +/- {f1:.0f} mM")
    print(f"           peak 2: t_R = {m2:.2f} +/- {e2:.2f} min, "
          f"[NaCl] = {n2:.0f} +/- {f2:.0f} mM   (n = {len(rows)})\n")


def _parse_cli_args():
    import argparse
    p = argparse.ArgumentParser(
        description=(__doc__ or '').strip().splitlines()[0] if __doc__ else '')
    p.add_argument('--data-dir', type=Path, default=DATA_DIR,
                   help=('Root data directory (default: %(default)s). Must contain the '
                         'appropriate subfolders: simulations, fplc.'))
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

    print('=' * 78)
    print('Peak retention times, half-prominence widths and elution NaCl concentrations')
    print('Times and widths in minutes; peak heights in g/L (simulated) or mAU (measured)')
    print('=' * 78)
    print('\nSIMULATIONS')
    fmt_sim(analyze_sim('Gradient elution', SIM_FILES['Gradient (simulation)']))
    fmt_sim(analyze_sim('Two-step elution', SIM_FILES['Two-step (simulation)']))
    print('EXPERIMENTS (triplicates; peaks are not assigned to proteins)')
    fmt_exp('Gradient elution', analyze_exp_grad())
    fmt_exp('Two-step elution', analyze_exp_step())
