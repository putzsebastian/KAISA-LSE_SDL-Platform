#!/usr/bin/env python3
"""
CADET Report Generator
Generated for Process ID: 62
Simulation: 3 Comp - Two Step Elution - MPM
Result Type: chromatogram_single

This script:
1. Loads simulation results from the experiment folder
2. Generates configured plots
3. Creates an HTML report with metrics and visualizations
4. Uploads results to eLabFTW and updates experiment body
"""

import json
import os
import re
import sys
import argparse
import requests
from pathlib import Path
from datetime import datetime
import mimetypes

# Configuration
ELAB_API_BASE = os.environ.get('ELAB_API_BASE', 'https://<ELAB_HOST>')
TEMPLATE_FILENAME = "report_template_62.html"
PROCESS_ID = 62
RESULT_TYPE = "chromatogram_single"

# Field mapping from eLabFTW to template placeholders
FIELD_MAPPING = {"component_0_name": "COMPONENT_0_NAME", "component_0_molecular_weight": "COMPONENT_0_MOLECULAR_WEIGHT", "component_0_charge": "COMPONENT_0_CHARGE", "component_1_name": "COMPONENT_1_NAME", "component_1_molecular_weight": "COMPONENT_1_MOLECULAR_WEIGHT", "component_1_charge": "COMPONENT_1_CHARGE", "component_2_name": "COMPONENT_2_NAME", "component_2_molecular_weight": "COMPONENT_2_MOLECULAR_WEIGHT", "component_2_charge": "COMPONENT_2_CHARGE", "column_length": "COLUMN_LENGTH", "column_diameter": "COLUMN_DIAMETER", "column_axial_dispersion": "COLUMN_AXIAL_DISPERSION", "column_bed_porosity": "COLUMN_BED_POROSITY", "column_particle_porosity": "COLUMN_PARTICLE_POROSITY", "column_particle_radius": "COLUMN_PARTICLE_RADIUS", "column_film_diffusion_0": "COLUMN_FILM_DIFFUSION_0", "column_film_diffusion_1": "COLUMN_FILM_DIFFUSION_1", "column_film_diffusion_2": "COLUMN_FILM_DIFFUSION_2", "column_binding_is_kinetic": "COLUMN_BINDING_IS_KINETIC", "column_binding_0_adsorption_rate": "COLUMN_BINDING_0_ADSORPTION_RATE", "column_binding_0_desorption_rate": "COLUMN_BINDING_0_DESORPTION_RATE", "column_binding_0_capacity": "COLUMN_BINDING_0_CAPACITY", "column_binding_0_gamma": "COLUMN_BINDING_0_GAMMA", "column_binding_0_beta": "COLUMN_BINDING_0_BETA", "column_binding_1_adsorption_rate": "COLUMN_BINDING_1_ADSORPTION_RATE", "column_binding_1_desorption_rate": "COLUMN_BINDING_1_DESORPTION_RATE", "column_binding_1_capacity": "COLUMN_BINDING_1_CAPACITY", "column_binding_1_gamma": "COLUMN_BINDING_1_GAMMA", "column_binding_1_beta": "COLUMN_BINDING_1_BETA", "column_binding_2_adsorption_rate": "COLUMN_BINDING_2_ADSORPTION_RATE", "column_binding_2_desorption_rate": "COLUMN_BINDING_2_DESORPTION_RATE", "column_binding_2_capacity": "COLUMN_BINDING_2_CAPACITY", "column_binding_2_gamma": "COLUMN_BINDING_2_GAMMA", "column_binding_2_beta": "COLUMN_BINDING_2_BETA", "column_initial_c_0": "COLUMN_INITIAL_C_0", "column_initial_q_0": "COLUMN_INITIAL_Q_0", "column_initial_c_1": "COLUMN_INITIAL_C_1", "column_initial_q_1": "COLUMN_INITIAL_Q_1", "column_initial_c_2": "COLUMN_INITIAL_C_2", "column_initial_q_2": "COLUMN_INITIAL_Q_2", "dead_volume_length": "DEAD_VOLUME_LENGTH", "dead_volume_diameter": "DEAD_VOLUME_DIAMETER", "dead_volume_axial_dispersion": "DEAD_VOLUME_AXIAL_DISPERSION", "flow_rate_injection": "FLOW_RATE_INJECTION", "load_c_1": "LOAD_C_1", "load_c_2": "LOAD_C_2", "flow_rate": "FLOW_RATE", "elute_step_1_c_0": "ELUTE_STEP_1_C_0", "elute_step_2_c_0": "ELUTE_STEP_2_C_0", "cip_c_0": "CIP_C_0", "load_duration": "LOAD_DURATION", "wash_duration": "WASH_DURATION", "elution_step_1_duration": "ELUTION_STEP_1_DURATION", "elution_step_2_duration": "ELUTION_STEP_2_DURATION", "step5_duration": "STEP5_DURATION", "cycle_time": "CYCLE_TIME"}

# Configured metrics to extract
CONFIGURED_METRICS = {"retention_time": {"enabled": True}, "peak_concentration": {"enabled": True}, "peak_width_half": {"enabled": True}, "recovery": {"enabled": True}}

# Selected plots information
SELECTED_PLOTS = [{"filename": "chromatogram_[[EXPERIMENT_ID]].png", "display_name": "chromatogram", "type": "chromatogram"}, {"filename": "inlet_profile_[[EXPERIMENT_ID]].png", "display_name": "inlet_profile", "type": "inlet_profile"}]

# Data exports to include
DATA_EXPORTS = {"chromatogram_csv": {"enabled": True}, "inlet_csv": {"enabled": True}}

# Default eLabFTW API key (when called from app)
DEFAULT_ELAB_TOKEN = os.environ.get('ELAB_API_TOKEN', None)


class CADETReportGenerator:
    """Report generator for CADET simulation experiments."""

    def __init__(self, elab_token, experiment_id):
        self.elab_token = elab_token
        self.experiment_id = experiment_id
        self.headers = {
            "Authorization": elab_token,
            "Content-Type": "application/json"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def get_experiment_data(self):
        """Get experiment data from eLabFTW API."""
        url = f"{ELAB_API_BASE}/api/v2/experiments/{self.experiment_id}"

        try:
            print(f"INFO: Getting experiment data from: {url}")
            response = self.session.get(url)

            if response.status_code != 200:
                print(f"ERROR: Failed to get experiment data: {response.text}")
                response.raise_for_status()

            experiment_data = response.json()
            print(f"SUCCESS: Retrieved experiment data (title: {experiment_data.get('title', 'N/A')})")
            return experiment_data

        except requests.RequestException as e:
            print(f"ERROR: HTTP Error getting experiment data: {e}")
            raise Exception(f"Failed to get experiment data: {e}")

    def extract_field_values(self, experiment_data):
        """Extract field values for template replacement."""
        values = {}

        try:
            metadata = experiment_data.get('metadata_decoded', {})
            extra_fields = metadata.get('extra_fields', {})

            for field_name, placeholder in FIELD_MAPPING.items():
                if field_name in extra_fields:
                    field_data = extra_fields[field_name]
                    if isinstance(field_data, dict):
                        field_value = field_data.get('value', '')
                        values[placeholder] = str(field_value).strip() if field_value else 'Not specified'
                    else:
                        values[placeholder] = str(field_data)
                else:
                    values[placeholder] = 'Field not found'

            # Add basic experiment metadata
            values['EXPERIMENT_ID'] = str(self.experiment_id)
            values['EXPERIMENT_TITLE'] = experiment_data.get('title', f'Experiment {self.experiment_id}')
            values['EXPERIMENT_DATE'] = experiment_data.get('date', experiment_data.get('created_at', ''))
            values['REPORT_DATE'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            values['PROCESS_ID'] = str(PROCESS_ID)
            values['RESULT_TYPE'] = RESULT_TYPE.replace('_', ' ').title()

            print(f"Extracted {len(values)} field values")

        except Exception as e:
            print(f"Warning: Error extracting field values: {e}")
            values = {
                'EXPERIMENT_ID': str(self.experiment_id),
                'EXPERIMENT_TITLE': f'Experiment {self.experiment_id}',
                'REPORT_DATE': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

        return values

    def load_simulation_results(self, results_folder):
        """Load CADET simulation results."""
        results_path = Path(results_folder)

        # Look for simulation_results.json
        results_patterns = [
            'simulation_results.json',
            f'*results*{self.experiment_id}*.json',
            '*simulation*.json',
            '*results*.json'
        ]

        for pattern in results_patterns:
            matching_files = list(results_path.glob(pattern))
            if matching_files:
                try:
                    with open(matching_files[0], 'r') as f:
                        results = json.load(f)
                        print(f"Loaded simulation results from: {matching_files[0]}")
                        return results
                except Exception as e:
                    print(f"Error loading {matching_files[0]}: {e}")

        print("No simulation results found")
        return None

    def extract_metrics(self, simulation_results):
        """Calculate metrics from raw simulation data.

        Supports all result types: chromatogram and breakthrough,
        single-column and multi-column.
        Uses RESULT_TYPE to determine which metrics to calculate.
        """
        metrics = {}

        if not simulation_results:
            return metrics

        # Find all column outlets
        outlet_keys = sorted([k for k in simulation_results if '_outlet' in k])
        if not outlet_keys:
            print("WARNING: No outlet data found in results")
            return metrics

        is_breakthrough = 'breakthrough' in RESULT_TYPE
        is_multi = 'multi' in RESULT_TYPE

        # For multi-column: analyze all outlets; for single: only last (closest to product)
        analyze_outlets = outlet_keys if is_multi else [outlet_keys[-1]]

        for outlet_key in analyze_outlets:
            print(f"Analyzing outlet: {outlet_key}")
            outlet = simulation_results[outlet_key]
            time = outlet.get('time', [])
            conc = outlet.get('concentration', {})

            if not time or not conc:
                continue

            # Column label prefix for multi-column
            col_prefix = ''
            if is_multi and len(analyze_outlets) > 1:
                col_name = outlet_key.replace('_outlet', '').replace('_', ' ').title()
                col_prefix = f'{col_name} - '

            comp_keys = sorted(conc.keys())
            n_comp = len(comp_keys)

            # Skip component_0 if 2+ components (typically salt/modulator)
            analyze_keys = comp_keys[1:] if n_comp > 1 else comp_keys

            for comp_key in analyze_keys:
                c = conc[comp_key]
                comp_label = comp_key.replace('component_', 'Comp ')

                # Build prefix: column + component
                prefix = col_prefix
                if len(analyze_keys) > 1:
                    prefix += f'{comp_label}: '

                if is_breakthrough:
                    self._calc_breakthrough_metrics(metrics, prefix, time, c)
                else:
                    self._calc_chromatogram_metrics(metrics, prefix, time, c)

        return metrics

    def _calc_chromatogram_metrics(self, metrics, prefix, time, c):
        """Calculate chromatogram peak metrics for one component."""
        max_c = max(c)
        if max_c <= 0:
            return

        peak_idx = c.index(max_c)
        ret_time = time[peak_idx]

        if 'retention_time' in CONFIGURED_METRICS:
            metrics[f'{prefix}Retention Time'] = f'{ret_time:.1f} s ({ret_time/60:.2f} min)'
        if 'peak_concentration' in CONFIGURED_METRICS:
            metrics[f'{prefix}Peak Concentration'] = f'{max_c:.4f} mol/m^3'

        if 'peak_width_half' in CONFIGURED_METRICS:
            half_max = max_c / 2.0
            left_t, right_t = None, None
            for i in range(peak_idx, 0, -1):
                if c[i] >= half_max and c[i-1] < half_max:
                    frac = (half_max - c[i-1]) / (c[i] - c[i-1]) if c[i] != c[i-1] else 0
                    left_t = time[i-1] + frac * (time[i] - time[i-1])
                    break
            for i in range(peak_idx, len(c)-1):
                if c[i] >= half_max and c[i+1] < half_max:
                    frac = (half_max - c[i]) / (c[i+1] - c[i]) if c[i+1] != c[i] else 0
                    right_t = time[i] + frac * (time[i+1] - time[i])
                    break
            if left_t is not None and right_t is not None:
                width = right_t - left_t
                metrics[f'{prefix}Peak Width (Half Height)'] = f'{width:.1f} s ({width/60:.2f} min)'
            else:
                metrics[f'{prefix}Peak Width (Half Height)'] = 'Could not determine'

    def _calc_breakthrough_metrics(self, metrics, prefix, time, c):
        """Calculate breakthrough curve metrics for one component."""
        max_c = max(c)
        if max_c <= 0:
            return

        # Estimate feed concentration as average of last 10% of data (plateau)
        n_tail = max(1, len(c) // 10)
        c_feed = sum(c[-n_tail:]) / n_tail
        if c_feed <= 0:
            c_feed = max_c

        # Breakthrough times at different thresholds
        thresholds = [
            ('breakthrough_time_1pct', 0.01, 'Breakthrough Time (1%)'),
            ('breakthrough_time_10pct', 0.10, 'Breakthrough Time (10%)'),
            ('breakthrough_time_50pct', 0.50, 'Breakthrough Time (50%)'),
        ]

        bt_times = {}  # Store for DBC calculation
        for metric_name, fraction, display_name in thresholds:
            if metric_name not in CONFIGURED_METRICS:
                continue
            target = fraction * c_feed
            bt_time = None
            for i in range(len(c) - 1):
                if c[i] < target <= c[i+1]:
                    denom = c[i+1] - c[i]
                    frac = (target - c[i]) / denom if denom != 0 else 0
                    bt_time = time[i] + frac * (time[i+1] - time[i])
                    break
            bt_times[metric_name] = bt_time
            if bt_time is not None:
                metrics[f'{prefix}{display_name}'] = f'{bt_time:.1f} s ({bt_time/60:.2f} min)'
            else:
                metrics[f'{prefix}{display_name}'] = 'Not reached'

        # DBC at 10% breakthrough: integral of (c_feed - c_outlet) from 0 to t_10%
        # Units: mol*s/m^3 (multiply by Q/V_col to get mol/m^3)
        if 'dynamic_binding_capacity_10pct' in CONFIGURED_METRICS:
            t_10 = bt_times.get('breakthrough_time_10pct')
            if t_10 is not None:
                retained = 0.0
                for i in range(len(time) - 1):
                    if time[i] >= t_10:
                        break
                    t_end = min(time[i+1], t_10)
                    dt = t_end - time[i]
                    avg_deficit = ((c_feed - c[i]) + (c_feed - c[i+1])) / 2.0
                    if avg_deficit > 0:
                        retained += avg_deficit * dt
                metrics[f'{prefix}DBC (10%)'] = f'{retained:.2f} mol*s/m^3'
            else:
                metrics[f'{prefix}DBC (10%)'] = 'Not reached'

        # Saturation capacity: integral of (c_feed - c_outlet) over full curve
        if 'saturation_capacity' in CONFIGURED_METRICS:
            total_retained = 0.0
            for i in range(len(time) - 1):
                dt = time[i+1] - time[i]
                avg_deficit = ((c_feed - c[i]) + (c_feed - c[i+1])) / 2.0
                if avg_deficit > 0:
                    total_retained += avg_deficit * dt
            metrics[f'{prefix}Saturation Capacity'] = f'{total_retained:.2f} mol*s/m^3'

    def find_plot_files(self, results_folder):
        """Find generated plot files in the experiment folder."""
        plot_files = []
        results_path = Path(results_folder)

        if not results_path.exists():
            print(f"Results folder not found: {results_folder}")
            return plot_files

        # Search for known plot patterns (chromatogram + breakthrough)
        plot_patterns = [
            ('chromatogram_dual_axis_*.png', 'Chromatogram (Dual Y-Axis)'),
            ('chromatogram_*.png', 'Chromatogram'),
            ('breakthrough_curve_*.png', 'Breakthrough Curve'),
            ('breakthrough_*.png', 'Breakthrough'),
            ('inlet_profile.png', 'Inlet Profile'),
            ('loading_profile_*.png', 'Column Loading Profile'),
            ('column_bulk_*.png', 'Column Loading'),
            ('component_comparison.png', 'Component Comparison'),
        ]

        seen_files = set()
        plot_number = 1
        for pattern, display_name in plot_patterns:
            for filepath in sorted(results_path.glob(pattern)):
                if filepath.name in seen_files:
                    continue
                seen_files.add(filepath.name)
                plot_files.append({
                    'path': filepath,
                    'info': {'display_name': display_name, 'type': pattern.split('*')[0].rstrip('_')},
                    'found': True,
                    'plot_number': plot_number
                })
                print(f"Found plot: {filepath.name} -> {display_name}")
                plot_number += 1

        return plot_files

    def upload_file_to_elab(self, file_path):
        """Upload file to eLabFTW and return longname."""
        url = f"{ELAB_API_BASE}/api/v2/experiments/{self.experiment_id}/uploads"

        try:
            with open(file_path, 'rb') as f:
                files = {'file': (file_path.name, f, mimetypes.guess_type(str(file_path))[0])}
                upload_headers = {"Authorization": self.elab_token}

                response = requests.post(url, files=files, headers=upload_headers)
                response.raise_for_status()

                print(f"Uploaded {file_path.name} to eLabFTW")

                # Get longname for proper URL
                uploads_response = self.session.get(url)
                uploads_response.raise_for_status()

                for upload in uploads_response.json():
                    if upload.get('real_name') == file_path.name:
                        return upload.get('long_name')

                return file_path.name

        except Exception as e:
            print(f"ERROR: Failed to upload {file_path.name}: {e}")
            return None

    def update_experiment_body(self, report_html):
        """Append report to existing experiment body in eLabFTW."""
        url = f"{ELAB_API_BASE}/api/v2/experiments/{self.experiment_id}"

        try:
            # GET existing body first (use separate request without Content-Type header)
            get_headers = {"Authorization": self.elab_token}
            response = requests.get(url, headers=get_headers)
            existing_body = ''
            if response.status_code == 200:
                existing_body = response.json().get('body', '') or ''
                print(f"INFO: Fetched existing body ({len(existing_body)} chars)")
            else:
                print(f"WARNING: Could not fetch existing body (status {response.status_code}), will set new body")

            # Append report with separator
            separator = '<hr style="border: none; border-top: 2px solid #667eea; margin: 30px 0;">'
            new_body = existing_body + separator + report_html
            print(f"INFO: New body length: {len(new_body)} chars (existing: {len(existing_body)}, report: {len(report_html)})")

            patch_headers = {"Authorization": self.elab_token, "Content-Type": "application/json"}
            response = requests.patch(url, json={"body": new_body}, headers=patch_headers)

            if response.status_code != 200:
                print(f"ERROR: Failed to update experiment body: {response.text}")
                return False

            print(f"SUCCESS: Appended report to experiment {self.experiment_id}")
            return True

        except Exception as e:
            print(f"ERROR: Error updating experiment body: {e}")
            return False

    def generate_report(self, results_folder='results', upload_files=True):
        """Generate complete report."""
        print(f"Generating CADET report for experiment {self.experiment_id}")
        print(f"Result type: {RESULT_TYPE}")

        # Load template
        template_path = Path(__file__).parent / TEMPLATE_FILENAME
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                template_content = f.read()
        except FileNotFoundError:
            raise Exception(f"Template file not found: {template_path}")

        # Get experiment data
        experiment_data = self.get_experiment_data()
        field_values = self.extract_field_values(experiment_data)

        # Load simulation results
        simulation_results = self.load_simulation_results(results_folder)
        metrics = self.extract_metrics(simulation_results)

        # Find plot files
        plot_files = self.find_plot_files(results_folder)

        # Build replacements
        replacements = {**field_values}

        # Build metrics table HTML dynamically
        if metrics:
            rows = ''.join(
                f'<tr><td style="padding: 10px; border-bottom: 1px solid #eee;"><strong>{name}</strong></td>'
                f'<td style="padding: 10px; border-bottom: 1px solid #eee;">{value}</td></tr>'
                for name, value in metrics.items()
            )
            metrics_html = (
                '<table style="width: 100%; border-collapse: collapse;">'
                '<thead><tr>'
                '<th style="padding: 10px; text-align: left; border-bottom: 2px solid #667eea; color: #333;">Metric</th>'
                '<th style="padding: 10px; text-align: left; border-bottom: 2px solid #667eea; color: #333;">Value</th>'
                '</tr></thead><tbody>' + rows + '</tbody></table>'
            )
        else:
            metrics_html = '<p>No metrics available</p>'
        replacements['METRICS'] = metrics_html

        # Upload all plot files and build plots HTML
        uploaded_plots = []
        plots_html_parts = []
        for plot_data in plot_files:
            plot_path = plot_data['path']
            display_name = plot_data['info']['display_name']

            if plot_path and plot_path.exists():
                if upload_files:
                    # 1. Upload plot to eLabFTW
                    longname = self.upload_file_to_elab(plot_path)
                    if longname:
                        # 2. Build img tag with longname reference
                        img_tag = f'<img src="app/download.php?name={plot_path.name}&f={longname}&storage=1" alt="{display_name}" style="max-width: 100%; height: auto;">'
                        uploaded_plots.append(plot_path.name)
                    else:
                        img_tag = f'<p style="color: #856404;">Failed to upload {plot_path.name}</p>'
                else:
                    img_tag = f'<img src="{plot_path.name}" alt="{display_name}" style="max-width: 100%; height: auto;">'

                plots_html_parts.append(f'<div style="margin: 16px 0; text-align: center;"><h4 style="color: #495057;">{display_name}</h4>{img_tag}</div>')

        # 3. Embed all plots via [[PLOTS]] placeholder
        replacements['PLOTS'] = '\n'.join(plots_html_parts) if plots_html_parts else '<p>No plots available</p>'

        # Replace placeholders
        report_html = template_content
        for placeholder, value in replacements.items():
            pattern = r'\[\[' + placeholder + r'\]\]'
            report_html = re.sub(pattern, str(value), report_html)

        # Update experiment body
        if upload_files:
            self.update_experiment_body(report_html)

        # Save local copy to reports folder (next to template)
        report_file = Path(__file__).parent / f'report_{self.experiment_id}.html'
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report_html)

        # Save computed metrics to metrics.json (for commit script to read)
        metrics_file = Path(results_folder) / 'metrics.json'
        try:
            with open(metrics_file, 'w', encoding='utf-8') as f:
                json.dump(metrics, f, indent=2)
            print(f"Metrics saved to {metrics_file}")
        except Exception as e:
            print(f"WARNING: Could not save metrics.json: {e}")

        return {
            'success': True,
            'report_file': str(report_file),
            'experiment_id': self.experiment_id,
            'metrics_extracted': len(metrics),
            'plots_uploaded': len(uploaded_plots)
        }


def main():
    """Command line interface."""
    parser = argparse.ArgumentParser(description='Generate CADET simulation report')
    parser.add_argument('experiment_id', help='eLabFTW experiment ID')
    parser.add_argument('--elab-token', help='eLabFTW API token')
    parser.add_argument('--results-folder', default='results', help='Results folder path')
    parser.add_argument('--no-upload', action='store_true', help='Skip uploads to eLabFTW')

    args = parser.parse_args()

    elab_token = args.elab_token or DEFAULT_ELAB_TOKEN
    if not elab_token:
        print("ERROR: eLabFTW API token required")
        return 1

    try:
        generator = CADETReportGenerator(elab_token, args.experiment_id)
        result = generator.generate_report(
            results_folder=args.results_folder,
            upload_files=not args.no_upload
        )

        print(f"\nReport generated successfully!")
        print(f"   Experiment: {args.experiment_id}")
        print(f"   Metrics: {result['metrics_extracted']}")
        print(f"   Plots uploaded: {result['plots_uploaded']}")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
