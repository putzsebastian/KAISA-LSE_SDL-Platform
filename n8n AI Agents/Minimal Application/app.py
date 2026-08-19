"""
SDL Agent Tools Service
=======================
Standalone service providing tool endpoints for AI agent workflows
in Self-Driving Laboratory (SDL) platforms. Designed to be deployed
alongside n8n workflow automation as a tool backend.

Endpoints:
    POST /validate              - Static analysis of generated Python scripts
    POST /save                  - Save generated scripts to a target path
    POST /simulate/opentrons    - Simulate Opentrons protocols (optional, requires SDK)
    GET  /health                - Health check with feature availability

Usage:
    uvicorn app:app --host 0.0.0.0 --port 8100

Docker:
    docker compose up -d
"""

import ast
import os
import sys
import importlib
import importlib.util
import logging
import tempfile
from typing import Optional
from pathlib import Path

from pydantic import BaseModel, Field
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import JSONResponse

# =============================================================================
# Configuration
# =============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("agent-tools")

# Base directory for script saving (configurable via env var)
# When deployed alongside an SDL app, set this to the experiments root
SCRIPTS_BASE_DIR = os.environ.get("SCRIPTS_BASE_DIR", "/data")

app = FastAPI(
    title="SDL Agent Tools Service",
    description=(
        "Tool endpoints for AI agent workflows in Self-Driving Laboratories. "
        "Provides script validation, script saving, and optional Opentrons "
        "protocol simulation."
    ),
    version="1.0.0",
    docs_url="/docs",
)


# =============================================================================
# Models
# =============================================================================

class ValidationRequest(BaseModel):
    """Request body for POST /validate."""
    script: str = Field(..., description="Python script content to validate")
    script_type: Optional[str] = Field(
        None,
        description="Script category for structural checks: "
                    "'analysis', 'orchestration', 'report', 'database', 'device'",
    )
    check_imports: bool = Field(True, description="Verify imports can be resolved")
    check_structure: bool = Field(True, description="Run script-type-specific checks")
    known_modules: Optional[list[str]] = Field(
        None,
        description="Extra module names to treat as available "
                    "(device packages not installed in this container)",
    )


class ValidationIssue(BaseModel):
    """A single validation finding."""
    level: str = Field(..., description="'error' or 'warning'")
    category: str = Field(..., description="Issue category: syntax, import, encoding, structure")
    message: str
    line: Optional[int] = Field(None, description="Source line number if applicable")


class ValidationResponse(BaseModel):
    """Response from POST /validate."""
    valid: bool = Field(..., description="True if no errors (warnings are allowed)")
    errors: int = Field(0)
    warnings: int = Field(0)
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: str = Field("", description="Human-readable summary for the AI agent")


class SaveRequest(BaseModel):
    """Request body for POST /save (JSON mode)."""
    script: str = Field(..., description="Script content to save")
    path: str = Field(..., description="Target file path (absolute or relative to SCRIPTS_BASE_DIR)")
    script_type: str = Field("generic", description="Script category for logging")


class SimulationRequest(BaseModel):
    """Request body for POST /simulate/opentrons."""
    script: str = Field(..., description="Python protocol content to simulate")


# =============================================================================
# Known Modules
# =============================================================================
# Device-specific packages that are valid in generated scripts but will not
# be installed in this container. Extend this set for your lab's devices.
# Standard scientific packages (numpy, pandas, scipy, matplotlib) are
# installed in the container so their submodule imports resolve correctly.

DEFAULT_KNOWN_MODULES: set[str] = {
    # -- SDL device control libraries (example entries) --
    # Lab 167 (Particle Processing SDL)
    "Devices_167", "Devices_167.OpentronsV2", "Devices_167.UniversalRobot_V2",
    "Devices_167.URMovement_V2", "Devices_167.URPosition", "Devices_167.UR5_position",
    "Devices_167.TecanSpark_API", "Devices_167.VacuuSelect",
    "Devices_167.HeaterShaker_HTTP", "Devices_167.Zetasizer_HTTP",
    "Devices_167.DLSInjectionSetup_HTTP", "Devices_167.device_config",
    "Devices_167.Labware",
    # Lab 168 (Biochemical Analytics SDL)
    "Devices_168", "Devices_168.OpentronsV2", "Devices_168.UniversalRobot_V2",
    "Devices_168.URMovement_V2", "Devices_168.URPosition",
    "Devices_168.UR5_position", "Devices_168.UR10_position",
    "Devices_168.TecanSpark_API", "Devices_168.TecanSpark_HTTP",
    "Devices_168.VacuuSelect", "Devices_168.X500R_HTTP",
    "Devices_168.X500R_API_V2", "Devices_168.X500R_FileClient_HTTP",
    "Devices_168.ESIInjectionSetup_HTTP", "Devices_168.ESIInjectionSetup_API",
    "Devices_168.LabProValve_API", "Devices_168.device_config",
    # Legacy / unqualified device imports
    "Devices", "Devices.Opentrons", "Devices.OpentronsFlex",
    "Devices.UniversalRobot_V2", "Devices.URMovement_V2",
    "Devices.TecanSpark_API", "Devices.VacuuSelect",
    # CADET chromatography simulation
    "CADETProcess", "CADETProcess.processModel", "CADETProcess.simulator",
    "CADETProcess.comparison", "CADETProcess.optimization", "CADETProcess.fractionation",
    # Miscellaneous lab utilities
    "automation1", "automation1.tecan_micro_service_client",
    # Database drivers
    "pg8000", "pg8000.dbapi", "psycopg2", "psycopg2.extensions",
    # Opentrons SDK (may not be installed in this container)
    "opentrons", "opentrons.protocol_api", "opentrons.simulate", "opentrons_shared_data",
    # ML / optimisation (unlikely in generated scripts but valid)
    "bofire", "botorch", "gpytorch", "torch",
    # Serial / USB / Modbus device communication
    "pymodbus", "pymodbus.client", "pyusb", "usb", "pyserial", "serial",
    # Electronic lab notebook
    "elabapi_python",
    # Data formats
    "openpyxl",
}


# =============================================================================
# Validation Logic
# =============================================================================

def check_syntax(script: str) -> tuple[Optional[ast.Module], list[ValidationIssue]]:
    """Parse the script into an AST. Returns (tree, issues)."""
    try:
        return ast.parse(script), []
    except SyntaxError as e:
        return None, [ValidationIssue(
            level="error", category="syntax",
            message=f"SyntaxError: {e.msg}", line=e.lineno,
        )]


def check_imports(tree: ast.Module, known: set[str]) -> list[ValidationIssue]:
    """Verify every imported module can be resolved."""
    issues: list[ValidationIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_available(alias.name, known):
                    issues.append(ValidationIssue(
                        level="error", category="import",
                        message=f"Module not found: '{alias.name}'", line=node.lineno,
                    ))
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module.split(".")[0]
            if not _module_available(node.module, known) and \
               not _module_available(base, known):
                issues.append(ValidationIssue(
                    level="error", category="import",
                    message=f"Module not found: '{node.module}'", line=node.lineno,
                ))
    return issues


def _module_available(name: str, known: set[str]) -> bool:
    """Check known-list then try importlib without executing."""
    if name in known:
        return True
    for k in known:
        if name.startswith(k + ".") or k.startswith(name + "."):
            return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


# Unicode characters commonly used in scientific strings that are acceptable
_ALLOWED_UNICODE = set("\u00b5\u00b0\u00b1\u00b2\u00b3\u207b\u2265\u2264")  # mu, degree, pm, sup2/3, minus, >=, <=


def check_structure(tree: ast.Module, script: str, script_type: Optional[str]) -> list[ValidationIssue]:
    """Script-type-specific structural checks."""
    issues: list[ValidationIssue] = []

    # Common: flag non-ASCII in strings (frequent LLM issue)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for ch in node.value:
                if ord(ch) > 127 and ch not in _ALLOWED_UNICODE:
                    issues.append(ValidationIssue(
                        level="warning", category="encoding",
                        message=f"Non-ASCII character '{ch}' (U+{ord(ch):04X}) in string. Use ASCII alternative.",
                        line=node.lineno,
                    ))
                    break  # one warning per string

    if not script_type:
        return issues

    # Type-specific checks
    _type_checks = {
        "analysis": _check_analysis,
        "orchestration": _check_orchestration,
        "database": _check_database,
        "report": _check_report,
    }
    checker = _type_checks.get(script_type)
    if checker:
        issues.extend(checker(tree, script))
    return issues


def _w(msg: str, cat: str = "structure") -> ValidationIssue:
    return ValidationIssue(level="warning", category=cat, message=msg)


def _check_analysis(tree: ast.Module, s: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "results" not in s and "results_folder" not in s:
        issues.append(_w("No results folder reference found."))
    if "experiment_id" not in s:
        issues.append(_w("No 'experiment_id' reference found."))
    if "analysis_results" not in s and "results.json" not in s:
        issues.append(_w("No analysis results JSON output found."))
    if not any(isinstance(n, ast.Try) for n in ast.walk(tree)):
        issues.append(_w("No try/except blocks found."))
    return issues


def _check_orchestration(_tree: ast.Module, s: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "requests" not in s and "http" not in s.lower():
        issues.append(_w("No HTTP requests found."))
    if "log_execution_step" not in s:
        issues.append(_w("No log_execution_step calls found."))
    return issues


def _check_database(_tree: ast.Module, s: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "INSERT" not in s and "insert" not in s:
        issues.append(_w("No SQL INSERT statements found."))
    if "ON CONFLICT" not in s and "on conflict" not in s.lower():
        issues.append(_w("No ON CONFLICT clause found."))
    if "conn.close" not in s and "connection.close" not in s:
        issues.append(_w("No explicit database connection close found."))
    return issues


def _check_report(_tree: ast.Module, s: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "html" not in s.lower() and "HTML" not in s:
        issues.append(_w("No HTML content generation found."))
    return issues


# =============================================================================
# POST /validate — Script Validation
# =============================================================================

@app.post("/validate", response_model=ValidationResponse, tags=["validation"])
async def validate_endpoint(req: ValidationRequest) -> ValidationResponse:
    """
    Static analysis of a Python script.

    Three levels: (1) syntax via AST, (2) import resolution, (3) structural patterns.
    Returns a human-readable summary designed to be fed back to an AI agent.
    """
    all_issues: list[ValidationIssue] = []
    known = DEFAULT_KNOWN_MODULES.copy()
    if req.known_modules:
        known.update(req.known_modules)

    # 1. Syntax
    tree, syntax_issues = check_syntax(req.script)
    all_issues.extend(syntax_issues)
    if tree is None:
        return ValidationResponse(
            valid=False, errors=len(syntax_issues),
            issues=syntax_issues,
            summary=f"Script has syntax errors: {syntax_issues[0].message}",
        )

    # 2. Imports
    if req.check_imports:
        all_issues.extend(check_imports(tree, known))

    # 3. Structure
    if req.check_structure:
        all_issues.extend(check_structure(tree, req.script, req.script_type))

    errors = sum(1 for i in all_issues if i.level == "error")
    warnings = sum(1 for i in all_issues if i.level == "warning")
    valid = errors == 0

    if valid and warnings == 0:
        summary = "Script validation passed with no issues."
    elif valid:
        summary = (
            f"Script validation passed with {warnings} warning(s). "
            f"Warnings: {'; '.join(i.message for i in all_issues if i.level == 'warning')}"
        )
    else:
        summary = (
            f"Script validation FAILED with {errors} error(s) and {warnings} warning(s). "
            f"Errors: {'; '.join(i.message for i in all_issues if i.level == 'error')}"
        )

    return ValidationResponse(
        valid=valid, errors=errors, warnings=warnings,
        issues=all_issues, summary=summary,
    )


# =============================================================================
# POST /save — Save Script (JSON body)
# =============================================================================

@app.post("/save", tags=["save"])
async def save_script_json(req: SaveRequest):
    """
    Save a script to the filesystem (JSON body).

    If the path is relative it is resolved against SCRIPTS_BASE_DIR.
    Creates parent directories as needed.
    """
    target = Path(req.path)
    if not target.is_absolute():
        target = Path(SCRIPTS_BASE_DIR) / target

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.script, encoding="utf-8")
        logger.info(f"Saved {req.script_type} script ({len(req.script)} chars) to {target}")
        return {"path": str(target), "message": "Script saved successfully"}
    except Exception as e:
        logger.error(f"Failed to save script to {target}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# POST /save/upload — Save Script (multipart form, legacy compatibility)
# =============================================================================

@app.post("/save/upload", tags=["save"])
async def save_script_upload(
    file: UploadFile = File(...),
    protocol_path: Optional[str] = Form(None),
    script_path: Optional[str] = Form(None),
    script_type: str = Form("generic"),
):
    """
    Save a script via multipart file upload.

    Accepts both 'protocol_path' and 'script_path' form fields
    for compatibility with different n8n workflow configurations.
    """
    save_path = protocol_path or script_path
    if not save_path:
        return JSONResponse({"error": "No protocol_path or script_path provided"}, status_code=400)

    try:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"Saved {script_type} script to {save_path}")
        return {"filename": save_path, "message": "File uploaded successfully", "script_type": script_type}
    except Exception as e:
        logger.error(f"Error saving script: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# =============================================================================
# POST /simulate/opentrons — Opentrons Protocol Simulation (optional)
# =============================================================================

_OT_AVAILABLE = False
try:
    from opentrons.simulate import simulate as _ot_simulate, format_runlog as _ot_format
    _OT_AVAILABLE = True
    logger.info("Opentrons SDK detected — simulation endpoint enabled")
except ImportError:
    logger.info("Opentrons SDK not installed — simulation endpoint disabled (501)")


# Declared as a sync `def` (NOT `async def`) on purpose: opentrons.simulate.simulate()
# calls asyncio.run() internally, which raises "asyncio.run() cannot be called from a
# running event loop" if invoked inside FastAPI's event loop. A sync path operation runs
# in FastAPI's worker threadpool (no running loop), so the simulator works there.
@app.post("/simulate/opentrons", tags=["simulation"])
def simulate_opentrons(req: SimulationRequest):
    """
    Run the Opentrons protocol simulator and return the formatted run log.

    Requires the opentrons Python SDK (pip install opentrons).
    Returns 501 if the SDK is not installed.
    """
    if not _OT_AVAILABLE:
        return JSONResponse(
            {"status": "error",
             "message": "Opentrons SDK not installed. Install with: pip install opentrons"},
            status_code=501,
        )

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(req.script)
            tmp_path = tmp.name
        try:
            with open(tmp_path) as f:
                runlog, _ = _ot_simulate(f, tmp_path)
            return {
                "status": "success",
                "message": "Simulation completed successfully",
                "runlog": _ot_format(runlog),
            }
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# GET /health
# =============================================================================

@app.get("/health", tags=["system"])
async def health():
    """Health check showing which features are available."""
    return {
        "status": "ok",
        "service": "sdl-agent-tools",
        "python_version": sys.version.split()[0],
        "scripts_base_dir": SCRIPTS_BASE_DIR,
        "features": {
            "validation": True,
            "save_script": True,
            "opentrons_simulation": _OT_AVAILABLE,
        },
    }


# =============================================================================
# Entrypoint
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
