#!/usr/bin/env python3
"""Per-template configuration for the replication harness.

Everything that differs between the replicated experiments lives here, so the runner, the replicate
layout and the scorer all read one definition instead of each hardcoding the isotherm.

Two experiments are defined:

**isotherm** - template 278, process 15, lab 167. Five agent-generated Opentrons protocols plus
orchestration, analysis, report and database.

**fplc_gradient** - template 387, process 36, lab 168. The ÄKTA protocol is assembled
deterministically by the app rather than by an agent, so there is no device_protocol step: only the
four LLM-driven agents. Its analysis is judged by `akta_analysis.py` against the chromatogram, and
its analysis script fetches from an ÄKTA control server rather than a Tecan one.
"""
from __future__ import annotations

TEMPLATES = {
    "isotherm": {
        "template_id": "278",
        "process_id": 15,
        "lab_id": "167",
        "exp_id": "5016",
        "reference_template": ("Generated Templates", "Isotherm Workflow",
                               "Loading_Isotherm_Resin_20251113_104110_15"),
        "reference_experiment": ("Experiments", "Run Adsorption Isotherms", "Ovalbumin",
                                 "0,100,200,300 mM NaCl", "experiment_5016"),
        # agent -> (payload stem, dependencies)
        # order matters: this is the execution order the isotherm runs were run in, and it is
        # preserved verbatim so adding a second template does not quietly change what was measured
        "chain": [
            ("device_protocol_1", "device_protocol_1", []),
            ("device_protocol_2", "device_protocol_2", []),
            ("device_protocol_3", "device_protocol_3", []),
            ("device_protocol_4", "device_protocol_4", []),
            ("device_protocol_5", "device_protocol_5", []),
            ("analysis", "analysis", []),
            ("orchestration", "orchestration",
             ["device_protocol_1", "device_protocol_2", "device_protocol_3",
              "device_protocol_4", "device_protocol_5"]),
            ("report", "report", ["analysis"]),
            ("database", "database", ["analysis"]),
        ],
        "seed": [
            "workflow.json",
            "tecan_field_mappings.json",
            "protocols/Template_278_Script_Generator.py",
            "protocols/Template_278_TECAN_Protocol_1.xml",
            "protocols/cytiva_96_filterwellplate_1ml.json",
        ],
        "artifact_target": {
            **{f"device_protocol_{i}": f"protocols/Template_278_OT_Protocol_{i}.py"
               for i in range(1, 6)},
            "orchestration": "main.py",
            "analysis": "analysis/analysis_script_15.py",
            "report": "reports/report_template_278.html",
            "database": "db/commit_to_db.py",
        },
        "analysis_comparator": "tecan",
    },

    "fplc_gradient": {
        "template_id": "387",
        "process_id": 36,
        "lab_id": "168",
        "exp_id": "5520",
        "reference_template": ("Generated Templates", "FPLC Gradient Elution Workflow",
                               "AKTA_-_Gradient_Elution_20260326_100607_36"),
        "reference_experiment": ("Experiments", "FPLC Validation", "Gradient Elution", "Run 1",
                                 "experiment_5520"),
        # No device_protocol: the ÄKTA protocol is generated deterministically by the app, not by
        # an agent, so replicating it would measure the app's templating rather than an LLM.
        "chain": [
            ("orchestration", "orchestration", []),
            ("analysis", "analysis", []),
            ("report", "report", ["analysis"]),
            ("database", "database", ["analysis"]),
        ],
        "seed": [
            "workflow.json",
            "protocols/Template_387_AKTA_Protocol_1.py",
            "protocols/Template_387_Script_Generator.py",
            "config/akta_sequence_1.json",
        ],
        "artifact_target": {
            "orchestration": "main.py",
            "analysis": "analysis/analysis_script_36.py",
            "report": "reports/report_template_387.html",
            "database": "db/commit_to_db.py",
        },
        "analysis_comparator": "akta",
        # The orchestration agent is per LAB - it has to know which instruments exist and how to
        # drive them - and this experiment runs in lab 168. Sending it to the lab-167 agent (the
        # harness default, because the isotherm is a 167 experiment) produced a main.py that
        # declared `lab_id: "167"`, pre-declared the 167 instrument set, and never drove the ÄKTA
        # at all. analysis, report and database are lab-agnostic and need no override.
        "agent_overrides": {"orchestration": "orchestration_lab168"},
    },
}


def get(name: str) -> dict:
    if name not in TEMPLATES:
        raise SystemExit("unknown template %r; known: %s"
                         % (name, ", ".join(sorted(TEMPLATES))))
    return TEMPLATES[name]


def steps_of(name: str) -> list[str]:
    return [s for s, _p, _d in get(name)["chain"]]
