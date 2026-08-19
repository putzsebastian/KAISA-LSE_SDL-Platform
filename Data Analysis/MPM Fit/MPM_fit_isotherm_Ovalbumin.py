"""
MPM Model Fitting Script
Fits the Mobile Phase Modulator model to K_L_eff vs salt concentration data.

MPM model: K_L_eff(c) = K_L,0_eff * exp(gamma * c) * c^(-beta)

Usage: Replace the data in the DATA section below with your values and run.
"""

import numpy as np
from scipy.optimize import curve_fit
import matplotlib.pyplot as plt

# ============================================================
# DATA — Replace with your values
# ============================================================

# Salt concentrations in mM (must include 0 mM as reference)
c_salt = np.array([0, 100, 200, 300, 500])

# Effective Langmuir constants at each salt concentration
K_L_eff = np.array([13.44, 4.34, 4.06, 1.33, 0])

# Label for the plot
protein_name = "Ovalbumin"

# ============================================================
# FITTING
# ============================================================

# K_L,0_eff is the value at c_salt = 0
K_L_0 = K_L_eff[0]

# Only fit to non-zero salt concentrations (c^(-beta) is undefined at c=0)
c_fit = c_salt[c_salt > 0]
K_fit = K_L_eff[c_salt > 0]

def mpm_model(c, gamma, beta):
    """MPM model with K_L_0 as a fixed global parameter."""
    return K_L_0 * np.exp(gamma * c) * c ** (-beta)

# Fit with physical constraints: gamma <= 0, beta >= 0
bounds = ([-np.inf, 0], [-np.inf, np.inf])
popt, pcov = curve_fit(mpm_model, c_fit, K_fit, p0=[-0.005, 1.0], bounds=bounds)
gamma, beta = popt
perr = np.sqrt(np.diag(pcov))

# R²
K_pred = mpm_model(c_fit, gamma, beta)
ss_res = np.sum((K_fit - K_pred) ** 2)
ss_tot = np.sum((K_fit - np.mean(K_fit)) ** 2)
R2 = 1 - ss_res / ss_tot

# ============================================================
# RESULTS
# ============================================================

print(f"{'='*50}")
print(f"MPM Fit Results — {protein_name}")
print(f"{'='*50}")
print(f"K_L,0_eff = {K_L_0:.4f}")
print(f"gamma     = {gamma:.6f}  (± {perr[0]:.6f})")
print(f"beta      = {beta:.4f}      (± {perr[1]:.4f})")
print(f"R²        = {R2:.6f}")
print()
print(f"{'c_NaCl (mM)':>12} {'K_L exp':>10} {'K_L pred':>10} {'Residual':>10}")
print(f"{'-'*44}")
for c, k_exp, k_pred in zip(c_fit, K_fit, K_pred):
    print(f"{c:12.0f} {k_exp:10.4f} {k_pred:10.4f} {k_exp - k_pred:10.4f}")

# ============================================================
# PLOT
# ============================================================

c_smooth = np.linspace(max(c_fit.min() * 0.5, 1), c_fit.max() * 1.1, 200)
K_smooth = mpm_model(c_smooth, gamma, beta)

fig, ax = plt.subplots(1, 1, figsize=(6, 4))
ax.scatter(c_fit, K_fit, s=60, zorder=5, color="C0", label="Experimental")
ax.plot(c_smooth, K_smooth, "-", color="C1", label="MPM fit")
ax.set_xlabel("$c_{NaCl}$ (mM)", fontsize=12)
ax.set_ylabel("$K_L^{eff}$ (mL mg$^{-1}$)", fontsize=12)
ax.set_title(f"{protein_name} — MPM fit\n"
             f"$\\gamma$ = {gamma:.4f}, $\\beta$ = {beta:.4f}, R² = {R2:.4f}",
             fontsize=11)
ax.legend()
ax.set_xlim(left=0)
ax.set_ylim(bottom=0)
fig.tight_layout()
plt.savefig(f"mpm_fit_{protein_name}.png", dpi=200)
plt.show()
print(f"\nPlot saved as mpm_fit_{protein_name}.png")