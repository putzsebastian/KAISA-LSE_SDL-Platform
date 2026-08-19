#!/usr/bin/env python3
"""M3 slice 3c - database comparator.

Executes a candidate commit script against a scratch database and compares the rows it writes,
and the column mapping it declares, to the reference record.

The reference record is produced by running the REFERENCE commit script against the same scratch
database, rather than being read from the production database: that keeps the comparison to what
the two scripts do, and never touches sdl_experiments.

Scratch handling: a database is created per run and dropped afterwards, with the two tables the
commit script writes (process_schemas, experiment_executions) copied structurally from the live
schema so the candidate meets the real constraints.

Volatile columns (surrogate id, created_at, updated_at) are excluded from the comparison; JSON
columns are compared as parsed structures so key order does not matter.

Taxonomy: EXEC_FAILED, ROW_MISSING, ROW_MISMATCH, COLUMN_MAPPING_MISMATCH

Usage (inside the sdl-app container):
    python database.py --experiment <exp dir> --reference-script <commit_to_db.py> \
                       --candidate-script <commit_to_db.py> --elab-id 5016
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

TABLES = ("process_schemas", "experiment_executions")
VOLATILE = {"id", "created_at", "updated_at"}
# Leaf keys inside JSON columns that record wall-clock time. The commit script stamps
# results.timestamp when it runs, so reference and candidate differ by however long the two runs
# were apart - nothing to do with what either script does.
VOLATILE_LEAVES = {"timestamp", "committed_at", "commit_time", "generated_at"}
# Human-readable prose, not structure. A JSON Schema is the same schema whether a property is
# described as "Buffer used for the loading isotherm experiment" or "Buffer solution used." These
# differences are still counted and reported under their own code - they are simply not what
# decides equivalence, the same way the report comparator checks sections and placeholders rather
# than wording. Pass --strict-prose to make them fatal.
PROSE_LEAVES = {"description", "title"}
# COLUMN_MAPPING_MISMATCH is naming, not correctness: the agent chooses its own column names and
# `cip_h2o1_duration_s` is no worse than the reference's `cip_3_duration`. What has to hold is that
# the mapping is USABLE, which mapping_consistency() checks directly against eLabFTW and the
# candidate's own schema - FIELD_MAPPING_UNKNOWN_FIELD, FIELD_MAPPING_NO_SUCH_COLUMN and
# UNIT_MISMATCH stay fatal and catch the defects a string diff was only catching by accident.
# The verdict rests on a POSITIVE definition of the task, not on reproducing the published schema:
# the script commits, every eLabFTW parameter is stored with the right value and unit, and the field
# mapping is usable (stored_values + mapping_consistency, both fatal). Everything the row-by-row
# diff against the reference turns up is recorded and reported, but does not decide - across 20 FPLC
# replicates it consisted entirely of schema DESIGN differences: whether a unit lives in a sibling
# column or in the schema, how a results enum is ordered, how the process is described, whether a
# text field is typed string or integer. Two independently authored schemas differ on all of those
# and neither is wrong.
NON_FATAL = {"DESCRIPTION_TEXT_DIFFERS", "COLUMN_MAPPING_MISMATCH",
             "SCHEMA_COLUMN_NAMING_DIFFERS", "ROW_MISMATCH"}
JSON_COLUMNS = {"metadata_schema", "results_schema", "field_mapping", "expected_files",
                "workflow_json", "metadata", "raw_data", "results"}

DB_HOST = os.environ.get("EXPERIMENT_DB_HOST", "sdl-experiment-db")
DB_PORT = os.environ.get("EXPERIMENT_DB_INTERNAL_PORT", "5432")
DB_USER = os.environ.get("EXPERIMENT_DB_USER", "sdl_admin")
DB_PASS = os.environ.get("EXPERIMENT_DB_PASSWORD", "sdl_secure_2025")
DB_SRC = os.environ.get("EXPERIMENT_DB_NAME", "sdl_experiments")


def connect(dbname: str):
    import psycopg2
    return psycopg2.connect(host=DB_HOST, port=int(DB_PORT), dbname=dbname,
                            user=DB_USER, password=DB_PASS)


def table_ddl(cur, table: str) -> str:
    """Reconstruct CREATE TABLE from the catalog.

    pg_dump is not in the application image, and adding a binary dependency for two tables is not
    worth it. The catalog gives the same information, and crucially includes the unique constraints
    that the commit script's ON CONFLICT clauses depend on.
    """
    cur.execute("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (f"public.{table}",))
    serial_for = {"integer": "serial", "bigint": "bigserial", "smallint": "smallserial"}
    cols = []
    for name, typ, notnull, default in cur.fetchall():
        # A serial column reports as integer DEFAULT nextval('<seq>'). Copying that verbatim
        # would reference a sequence the scratch database does not have, so fold it back into
        # the serial shorthand and let Postgres create the sequence.
        if default and default.startswith("nextval(") and typ in serial_for:
            cols.append(f'"{name}" {serial_for[typ]}')
            continue
        piece = f'"{name}" {typ}'
        if default:
            piece += f" DEFAULT {default}"
        if notnull:
            piece += " NOT NULL"
        cols.append(piece)

    cur.execute("""
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = %s::regclass ORDER BY contype DESC
    """, (f"public.{table}",))
    cols += [c[0] for c in cur.fetchall()]
    return f'CREATE TABLE "{table}" (\n  ' + ",\n  ".join(cols) + "\n)"


def create_scratch(name: str):
    """Create the scratch database and copy the two tables' structure from the live schema."""
    src = connect(DB_SRC)
    with src.cursor() as cur:
        ddl = [table_ddl(cur, t) for t in TABLES]
    src.close()

    admin = connect("postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    con = connect(name)
    con.autocommit = True
    with con.cursor() as cur:
        for stmt in ddl:
            cur.execute(stmt)
    con.close()


def drop_scratch(name: str):
    admin = connect("postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def truncate(name: str):
    con = connect(name)
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    con.close()


def snapshot(name: str) -> dict:
    """Every row of both tables, volatile columns dropped and JSON parsed."""
    out = {}
    con = connect(name)
    with con.cursor() as cur:
        for table in TABLES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position", (table,))
            cols = [c[0] for c in cur.fetchall() if c[0] not in VOLATILE]
            cur.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM {table} '
                        f'ORDER BY 1')
            rows = []
            for record in cur.fetchall():
                row = {}
                for col, val in zip(cols, record):
                    if col in JSON_COLUMNS and isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except json.JSONDecodeError:
                            pass
                    row[col] = val if not hasattr(val, "isoformat") else val.isoformat()
                rows.append(row)
            out[table] = rows
    con.close()
    return out


def run_commit(script: pathlib.Path, experiment: pathlib.Path, elab_id: str, dbname: str,
               workdir: pathlib.Path):
    """Run a commit script with its expected folder layout, against the scratch database."""
    db_dir = workdir / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script, db_dir / script.name)
    for extra in ("field_mapping.json", "schema.json"):
        src = experiment / "db" / extra
        if src.exists():
            shutil.copy2(src, db_dir / extra)
    for sub in ("results", "data"):
        if (experiment / sub).is_dir():
            shutil.copytree(experiment / sub, workdir / sub, dirs_exist_ok=True)
    for name in (f"experiment_{elab_id}.json", "workflow.json"):
        if (experiment / name).exists():
            shutil.copy2(experiment / name, workdir / name)

    env = {**os.environ, "EXPERIMENT_DB_NAME": dbname, "EXPERIMENT_DB_HOST": DB_HOST,
           "EXPERIMENT_DB_INTERNAL_PORT": str(DB_PORT), "EXPERIMENT_DB_USER": DB_USER,
           "EXPERIMENT_DB_PASSWORD": DB_PASS}
    proc = subprocess.run([sys.executable, script.name, str(elab_id)],
                          cwd=str(db_dir), capture_output=True, text=True, timeout=600, env=env)
    if proc.returncode != 0:
        return f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}"
    return None


TMPDIR = re.compile(r"/tmp/tmp[A-Za-z0-9_]+")


def normalise(obj):
    """Blank out the per-run working directory.

    The commit script records absolute paths (raw_data.folder_path, results.result_folder_path).
    Reference and candidate necessarily run in different temp directories, so those strings differ
    for reasons that have nothing to do with what either script does.
    """
    if isinstance(obj, str):
        return TMPDIR.sub("<workdir>", obj)
    if isinstance(obj, dict):
        return {k: normalise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalise(v) for v in obj]
    return obj


def leaf_deltas(a, b, path: str = ""):
    """Differing leaves of two JSON-ish values, as (dotted path, reference, candidate)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += leaf_deltas(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
        return out
    if isinstance(a, list) and isinstance(b, list):
        scalar = all(not isinstance(v, (dict, list)) for v in (*a, *b))
        # A list of scalars here is an inventory - the expected data files, the expected plots -
        # and inventories have no order. Comparing them elementwise reported three mismatches for
        # two schemas listing the same files in a different sequence.
        if scalar:
            miss = [v for v in a if v not in b]
            extra = [v for v in b if v not in a]
            if not miss and not extra:
                return []
            return [(path, sorted(map(str, a)), sorted(map(str, b)))]
        if len(a) == len(b):
            out = []
            for i, (x, y) in enumerate(zip(a, b)):
                out += leaf_deltas(x, y, f"{path}[{i}]")
            return out
    return [] if a == b else [(path, a, b)]


def anchor_process_id(tables: dict) -> dict:
    """Replace this side's own process id with a sentinel.

    The commit script carries the process id as a constant taken from its prompt - 33 when the
    published template was generated, 15 in this installation. Both scripts therefore do exactly
    the same thing and would differ on every replicate for a reason that belongs to the
    environment, not the agent. Substituting each side's own id keeps the check that the execution
    row points at the schema row it committed: an execution row referencing some OTHER process
    keeps its literal value and still mismatches.
    """
    schemas = tables.get("process_schemas") or []
    own = schemas[0].get("process_id") if schemas else None
    if own is None:
        return tables
    out = {}
    for table, rows in tables.items():
        out[table] = [{k: ("<process_id>" if k == "process_id" and v == own else v)
                       for k, v in row.items()} for row in rows]
    return out


def mapping_consistency(rows: list, elab_fields: dict | None,
                        reference_rows: list | None = None) -> list:
    """Is the candidate's own field mapping usable, regardless of what it named things?

    Column names are the agent's to choose - `cip_3_duration` is no more correct than
    `cip_h2o1_duration_s`, and after the eLabFTW descriptions were passed through the agent started
    naming columns after what the step actually is. What matters is that the mapping WORKS:
    commit_to_db iterates `for elab_name, db_name in FIELD_MAPPING.items()` and looks up
    `extra_fields[elab_name]`, so a key that is not a real eLabFTW field silently stores nothing,
    and a value with no matching column has nowhere to go. Those are the real defects, and they are
    invisible to a string comparison against the reference's naming.
    """
    # Anything the reference itself does is not held against the candidate: the published isotherm
    # mapping contains keys eLabFTW does not declare for that experiment, so flagging them would
    # fail the reference against itself.
    ref_map = {}
    ref_units = {}
    if reference_rows:
        rm = reference_rows[0].get("field_mapping")
        ref_map = rm if isinstance(rm, dict) else {}
        rs = reference_rows[0].get("metadata_schema")
        rp = (rs or {}).get("properties") if isinstance(rs, dict) else None
        if isinstance(rp, dict):
            ref_units = {k: (v.get("unit") if isinstance(v, dict) else None)
                         for k, v in rp.items()}

    out = []
    for i, row in enumerate(rows):
        mapping = row.get("field_mapping")
        if not isinstance(mapping, dict):
            continue
        schema = row.get("metadata_schema")
        columns = set()
        if isinstance(schema, dict):
            props = schema.get("properties")
            columns = set(props if isinstance(props, dict) else schema)
        for elab_name, db_name in mapping.items():
            if (elab_fields and elab_name not in elab_fields
                    and elab_name not in ref_map):
                out.append({"code": "FIELD_MAPPING_UNKNOWN_FIELD", "row": i,
                            "column": f"field_mapping.{elab_name}",
                            "detail": "not a field eLabFTW declares; commit_to_db would store "
                                      "nothing for it"})
            if columns and isinstance(db_name, str) and db_name not in columns:
                out.append({"code": "FIELD_MAPPING_NO_SUCH_COLUMN", "row": i,
                            "column": f"field_mapping.{elab_name}", "candidate": db_name,
                            "detail": "mapped to a column the schema does not declare"})
        if elab_fields:
            for elab_name, meta in elab_fields.items():
                unit = (meta.get("unit") or "").strip() if isinstance(meta, dict) else ""
                col = mapping.get(elab_name)
                if not unit or not isinstance(col, str) or not isinstance(schema, dict):
                    continue
                props = schema.get("properties")
                spec = (props or schema).get(col) if isinstance(props or schema, dict) else None
                got = (spec or {}).get("unit") if isinstance(spec, dict) else None
                # only where the REFERENCE agrees with eLabFTW: eLabFTW records the
                # isotherm's resin_mass in uL, and a candidate calling it mg is correcting
                # the record rather than contradicting it
                ref_u = _norm_unit(ref_units.get(col)) if ref_units else _norm_unit(unit)
                if ref_units and ref_u != _norm_unit(unit):
                    continue
                if (isinstance(got, str) and got.strip()
                        and _norm_unit(got) != _norm_unit(unit)):
                    out.append({"code": "UNIT_MISMATCH", "row": i, "column": col,
                                "reference": unit, "candidate": got.strip(),
                                "detail": "eLabFTW declares a different unit for this field"})
    return out


def rekey_by_elab_field(tables: dict) -> dict:
    """Re-key the stored metadata and its schema by eLabFTW field name, not by column name.

    Column names are the agent's choice (see NON_FATAL), and that choice propagates: a row committed
    into `cip_naoh_hold_duration_s` looks like a MISSING value when compared against a reference
    that used `cip_2_hold_duration`, even though both stored 600 for the same eLabFTW field. Keying
    each side through its OWN field_mapping removes the naming from the comparison and leaves the
    values, which is what a database actually has to get right.

    Anything with no mapping entry keeps its own key, so a column the agent invented is still
    visible rather than being quietly dropped.
    """
    schemas = tables.get("process_schemas") or []
    mapping = schemas[0].get("field_mapping") if schemas else None
    if not isinstance(mapping, dict):
        return tables
    col_to_field = {v: k for k, v in mapping.items() if isinstance(v, str)}

    def rekey(d):
        return {col_to_field.get(k, k): v for k, v in d.items()} if isinstance(d, dict) else d

    out = {}
    for table, rows in tables.items():
        new_rows = []
        for row in rows:
            row = dict(row)
            if isinstance(row.get("metadata"), dict):
                row["metadata"] = rekey(row["metadata"])
            schema = row.get("metadata_schema")
            if isinstance(schema, dict):
                schema = dict(schema)
                if isinstance(schema.get("properties"), dict):
                    # keep type and unit, drop the prose: a differently worded description of the
                    # same column is not a database defect
                    schema["properties"] = {
                        k: ({kk: vv for kk, vv in v.items() if kk in ("type", "unit")}
                            if isinstance(v, dict) else v)
                        for k, v in rekey(schema["properties"]).items()}
                row["metadata_schema"] = schema
            new_rows.append(row)
        out[table] = new_rows
    return out


def _norm_unit(u) -> str:
    """Units differing only in how a character was typed are the same unit.

    'degC' and '°C', 'uL' and 'µL', 'ml/min' and 'mL/min' - the ASCII spellings are what a script
    can write without worrying about encoding, and treating them as contradictions flagged ten
    isotherm replicates for agreeing with the reference.
    """
    s = str(u or "").strip()
    for a, b in (("µ", "u"), ("μ", "u"), ("°", ""), (" ", "")):
        s = s.replace(a, b)
    s = s.lower()
    # '°C', 'degC' and 'C' are one unit written three ways, and the column they annotate is
    # usually named ..._c anyway; folding the degree marker away entirely keeps all three equal
    if s.startswith("deg"):
        s = s[3:]
    return s


def _tokens(v):
    """A delimited eLabFTW string and the list a script parsed it into, as one comparable form.

    eLabFTW keeps multi-valued parameters as "0.2;0.5;1.0;1.5;2;5;7". Storing that as
    [0.2, 0.5, 1.0, 1.5, 2.0, 5.0, 7.0] is a faithful representation, not a different value, and
    an isotherm candidate was flagged for exactly that.
    """
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v]
    if isinstance(v, str) and re.search(r"[;,]", v):
        return [t.strip() for t in re.split(r"[;,]", v) if t.strip()]
    return None


def _same_value(a, b) -> bool:
    """Equal as data, ignoring how the type was spelled: eLabFTW stores "346", a column may hold
    346 or 346.0, and none of those is a different measurement."""
    if a is None or b is None:
        return a is b
    ta, tb = _tokens(a), _tokens(b)
    if ta is not None and tb is not None:
        if len(ta) != len(tb):
            return False
        return all(_same_value(x, y) for x, y in zip(ta, tb))
    try:
        return abs(float(a) - float(b)) <= max(1e-9, 1e-6 * abs(float(a)))
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def stored_values(tables: dict, elab_fields: dict | None,
                  reference: dict | None = None) -> list:
    """Did every eLabFTW parameter reach the database with the value eLabFTW holds?

    This is the positive definition of the task, and it replaced diffing the committed rows against
    the published schema. That diff measured schema DESIGN - whether units live in a sibling column
    or in the schema, how the results enum is ordered, how the process is described, whether a text
    field is typed as string or integer - none of which is a database defect, and all of which
    differs between two independently authored schemas. What a commit script has to get right is
    that the parameters are there and correct.

    Expects `tables` already re-keyed by eLabFTW field name (see rekey_by_elab_field).
    """
    if not elab_fields:
        return []
    execs = tables.get("experiment_executions") or []
    if not execs:
        return [{"code": "NO_EXECUTION_ROW",
                 "detail": "nothing was committed for this experiment"}]
    stored = execs[0].get("metadata")
    if not isinstance(stored, dict):
        return [{"code": "NO_METADATA_COMMITTED",
                 "detail": "the execution row carries no parameter metadata"}]
    # The baseline is the REFERENCE, not eLabFTW in the abstract. Not every field eLabFTW declares
    # for an experiment is one the published commit script stores - the isotherm reference does not
    # store several - so requiring all of them fails the reference against itself, which means the
    # check is measuring the template rather than the agent. Requiring the candidate to do as well
    # as the reference keeps the check honest and self-calibrating.
    ref_stored = {}
    if reference:
        rexecs = reference.get("experiment_executions") or []
        if rexecs and isinstance(rexecs[0].get("metadata"), dict):
            ref_stored = rexecs[0]["metadata"]

    out = []
    for name, meta in sorted(elab_fields.items()):
        want = meta.get("value") if isinstance(meta, dict) else meta
        if want in (None, ""):
            continue
        if reference and name not in ref_stored:
            continue                       # the reference does not store it either
        if name not in stored:
            out.append({"code": "PARAMETER_NOT_STORED", "column": name, "reference": want,
                        "detail": "the reference stores this parameter and no column holds it"})
            continue
        # The reference's own stored value is the baseline, not eLabFTW's raw string. Both commit
        # scripts coerce as they see fit - the isotherm's `Buffer` is "20 mM NaAc" in eLabFTW and
        # 20.0 in the database, in the reference as much as in the candidates - and comparing the
        # candidate against eLabFTW flagged fifteen replicates for doing exactly what the published
        # script does. eLabFTW decides which fields are in scope; the reference decides what
        # storing them correctly looks like.
        baseline = ref_stored.get(name, want) if reference else want
        if not _same_value(baseline, stored[name]):
            out.append({"code": "PARAMETER_VALUE_WRONG", "column": name,
                        "reference": baseline, "candidate": stored[name],
                        "elab_value": want})
    return out


def compare(ref: dict, cand: dict, strict_prose: bool = False,
            elab_fields: dict | None = None) -> dict:
    # consistency is checked on the RAW candidate, before re-keying, because it is precisely the
    # mapping itself that is under test there
    findings = list(mapping_consistency(
        normalise(cand).get("process_schemas") or [], elab_fields,
        normalise(ref).get("process_schemas") or []))
    ref = normalise(anchor_process_id(rekey_by_elab_field(ref)))
    cand = normalise(anchor_process_id(rekey_by_elab_field(cand)))
    findings += stored_values(cand, elab_fields, ref)

    # Coverage, by eLabFTW field name rather than column name, so it is unaffected by renaming:
    # every parameter the reference stores must still be stored. This is what stops the naming
    # leniency above from letting a dropped field through.
    def mapped(rows):
        m = (rows or [{}])[0].get("field_mapping")
        return set(m) if isinstance(m, dict) else set()

    dropped = mapped(ref.get("process_schemas")) - mapped(cand.get("process_schemas"))
    for name in sorted(dropped):
        findings.append({"code": "FIELD_MAPPING_MISSING_FIELD", "row": 0,
                         "column": f"field_mapping.{name}",
                         "detail": "the reference stores this eLabFTW field and the candidate "
                                   "maps no column for it"})
    for table in TABLES:
        rr, cr = ref.get(table, []), cand.get(table, [])
        if len(rr) != len(cr):
            findings.append({"code": "ROW_MISSING", "table": table,
                             "reference_rows": len(rr), "candidate_rows": len(cr)})
            continue
        for i, (a, b) in enumerate(zip(rr, cr)):
            for col in sorted(set(a) | set(b)):
                av, bv = a.get(col), b.get(col)
                if av == bv:
                    continue
                base = ("COLUMN_MAPPING_MISMATCH" if col == "field_mapping"
                        else "ROW_MISMATCH")
                # For JSON columns report the differing leaves rather than a truncated blob,
                # otherwise a one-key difference inside a large document is undiagnosable.
                for path, x, y in leaf_deltas(av, bv):
                    leaf = path.split(".")[-1].split("[")[0]
                    if leaf in VOLATILE_LEAVES:
                        continue
                    # A renamed column shows up twice - once in field_mapping, once as a property
                    # of metadata_schema that one side has and the other does not. Forgiving only
                    # the first would leave the rename fatal anyway. Coverage is enforced
                    # separately by FIELD_MAPPING_MISSING_FIELD, so nothing can be dropped under
                    # cover of a rename.
                    schema_naming = (col == "metadata_schema"
                                     and path.startswith("properties.")
                                     and (x is None or y is None))
                    # results_schema describes what the ANALYSIS produced, and has no eLabFTW
                    # field to key on. A candidate declaring extra result properties is recording
                    # more than the reference, not getting something wrong; one declaring FEWER is
                    # dropping results and stays fatal.
                    if col == "results_schema" and path.startswith("properties.") and x is None:
                        schema_naming = True
                    prose = (leaf in PROSE_LEAVES or (not path and col == "description")) \
                        and isinstance(x, str) and isinstance(y, str)
                    code = ("SCHEMA_COLUMN_NAMING_DIFFERS" if schema_naming
                            else "DESCRIPTION_TEXT_DIFFERS" if prose and not strict_prose
                            else base)
                    findings.append({"code": code, "table": table, "row": i,
                                     "column": col + (f".{path}" if path else ""),
                                     "reference": str(x)[:120], "candidate": str(y)[:120]})
    # A long finding list is truncated for readability, so the shape of the disagreement is
    # summarised separately - otherwise 125 findings look the same as 20 and there is no way to see
    # whether they concentrate in one JSON column or spread across the committed data.
    def group(f):
        # .get for table: the positive checks (stored_values, mapping_consistency) report on
        # the candidate as a whole rather than on one snapshot table, so they carry no table key.
        return (f.get("table", "-"), (f.get("column") or "").split(".")[0].split("[")[0],
                f["code"])

    breakdown = collections.Counter(group(f) for f in findings)
    example = {}
    for f in findings:
        example.setdefault(group(f), f)
    fatal = [f for f in findings if f["code"] not in NON_FATAL]
    return {"pass": not fatal, "codes": sorted({f["code"] for f in findings}),
            "fatal_codes": sorted({f["code"] for f in fatal}),
            "findings": fatal[:20], "n_findings": len(fatal),
            "n_prose_differences": len(findings) - len(fatal),
            "findings_by_column": [{"table": t, "column": c, "code": k, "count": n,
                                    "example": example[(t, c, k)]}
                                   for (t, c, k), n in breakdown.most_common()],
            "reference_rows": {t: len(ref.get(t, [])) for t in TABLES},
            "candidate_rows": {t: len(cand.get(t, [])) for t in TABLES}}


def evaluate(experiment: pathlib.Path, ref_script: pathlib.Path, cand_script: pathlib.Path,
             elab_id: str, dbname: str, strict_prose: bool = False) -> dict:
    create_scratch(dbname)
    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            err = run_commit(ref_script, experiment, elab_id, dbname, pathlib.Path(tmp))
        if err:
            return {"pass": False, "codes": ["EXEC_FAILED"],
                    "findings": [{"reference_error": err}]}
        ref = snapshot(dbname)

        truncate(dbname)
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            err = run_commit(cand_script, experiment, elab_id, dbname, pathlib.Path(tmp))
        if err:
            return {"pass": False, "codes": ["EXEC_FAILED"],
                    "findings": [{"candidate_error": err}]}
        cand = snapshot(dbname)
        # eLabFTW's own field metadata is the ground truth for the consistency checks: which field
        # names exist and what unit each carries.
        elab = {}
        ej = experiment / f"experiment_{elab_id}.json"
        if ej.exists():
            try:
                doc = json.loads(ej.read_text(encoding="utf-8"))
                elab = (doc.get("metadata_decoded") or {}).get("extra_fields") or {}
            except Exception:  # noqa: BLE001
                elab = {}
        return compare(ref, cand, strict_prose, elab or None)
    finally:
        drop_scratch(dbname)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--reference-script", required=True)
    ap.add_argument("--candidate-script", required=True)
    ap.add_argument("--elab-id", required=True)
    ap.add_argument("--scratch-db", default="sdl_replication_scratch")
    ap.add_argument("--strict-prose", action="store_true",
                    help="count differing schema/field descriptions as failures (off by default: "
                         "they are reported under DESCRIPTION_TEXT_DIFFERS but do not decide "
                         "equivalence)")
    ap.add_argument("--out")
    args = ap.parse_args()

    r = evaluate(pathlib.Path(args.experiment), pathlib.Path(args.reference_script),
                 pathlib.Path(args.candidate_script), args.elab_id, args.scratch_db,
                 args.strict_prose)
    print(f"  rows       : reference {r.get('reference_rows')}, candidate {r.get('candidate_rows')}")
    print(f"  prose diffs: {r.get('n_prose_differences')} (reported, not fatal)")
    print(f"  verdict    : {'PASS' if r['pass'] else 'FAIL ' + ','.join(r.get('fatal_codes', []))}")
    for f in r["findings"][:6]:
        print(f"      {json.dumps(f)[:200]}")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
