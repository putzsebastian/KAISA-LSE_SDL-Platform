#!/usr/bin/env python3
"""M3 slice 3c - report comparator.

What a generated report template has to get right is what the PROMPT asked for, not the wording the
published template happened to use. The prompt for the isotherm report is:

    Structure:
    Heading
    Start & End-Date
    Methods (Describe the automated workflow)
    Results (Plots)

and "Use the existing placeholders and the plots". So a template that files the same content under
"Objective" instead of "Overview", or "Procedure" instead of "Automated sequence", has done the job;
matching the reference's headings literally is not the requirement and an earlier version of this
comparator failed a perfectly good template on exactly that.

Four checks, all derived from the prompt:

  REQUESTED_SECTION_MISSING  a section the prompt asked for has no counterpart
  PLOT_NOT_EMBEDDED          a selected plot has no placeholder to render it into
  PLACEHOLDER_NOT_AVAILABLE  a placeholder that is not an offered ELN field or plot slot
  MALFORMED_TEMPLATE         empty, no headings, or tags eLabFTW cannot render

Reported but NOT fatal, because they are style rather than substance:

  SECTION_NAMING_DIFFERS     the same content under a different heading
  REFERENCE_PLACEHOLDER_UNUSED  an offered field the reference used and this one did not

Section matching is by token overlap against the whole document, not by heading text, because a
requested section's content can legitimately live under a differently-named heading - "Start &
End-Date" is satisfied by an "Experiment Information" block carrying the start date and the report
date. A requirement counts as met when a majority of its distinctive tokens appear; the evidence for
each is recorded so a verdict can be audited rather than trusted.

Usage:
    python report.py --candidate <cand.html> --fields <template_fields.json>
                     --prompt <report.payload.json> [--reference <ref.html>]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

PLACEHOLDER = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
STANDARD = {"EXPERIMENT_ID", "EXPERIMENT_TITLE", "EXPERIMENT_DATE", "REPORT_DATE"}
PLOT_SLOT = re.compile(r"^(PLOT_\d+|PLOTS_SECTION|PLOT_DESCRIPTION_\d+|ELAB_EXPERIMENT_URL)$")
# eLabFTW cannot render these; the system prompt forbids them explicitly.
FORBIDDEN_TAG = re.compile(r"<\s*(div|script|style|link|html|head|body|!doctype)\b", re.I)

STOPWORDS = {"the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to", "with", "describe",
             "please", "use", "using", "include", "section", "sections", "all", "each", "existing"}
# Concept synonyms. Kept deliberately small and explicit: these are the recurring section concepts
# of a scientific report, and expanding this table is a scoring decision that should be visible in
# the diff rather than buried in a fuzzy-match threshold.
SYNONYMS = {
    "date": {"date", "dates", "dated", "started", "start", "finished", "finish", "end", "ended",
             "duration", "timestamp", "generated"},
    "method": {"method", "methods", "methodology", "procedure", "procedures", "materials",
               "protocol", "workflow", "experimental", "automated", "sequence", "steps"},
    "result": {"result", "results", "data", "outcome", "outcomes", "findings", "analysis"},
    "plot": {"plot", "plots", "figure", "figures", "graph", "graphs", "isotherm", "isotherms",
             "chart", "image", "images"},
    "heading": {"heading", "title", "header"},
}


def app_placeholder(field_name: str) -> str:
    """api.py:2067-2069 - the application's own field-name to placeholder rule."""
    p = field_name.upper().replace(" ", "_").replace("-", "_")
    return re.sub(r"[^A-Z0-9_]", "", p)


def sections(html: str) -> list[str]:
    out = []
    for m in HEADING.finditer(html):
        text = re.sub(r"\s+", " ", TAG.sub("", m.group(2))).strip()
        if text:
            out.append(text.lower())
    return out


def visible_text(html: str) -> str:
    """Headings, body text and placeholder names, lower-cased - everything a reader would see."""
    text = TAG.sub(" ", html)
    text = text.replace("[[", " ").replace("]]", " ").replace("_", " ")
    return re.sub(r"\s+", " ", text).lower()


def tokens(phrase: str) -> list[str]:
    words = re.findall(r"[a-z]+", phrase.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def expand(token: str) -> set[str]:
    """A token plus its accepted synonyms, so 'methods' is satisfied by 'procedure'."""
    for group in SYNONYMS.values():
        if token in group:
            return set(group)
    return {token, token + "s", token.rstrip("s")}


def mentions(text: str, words) -> bool:
    """Whole-word search. Substring matching would find 'date' inside 'candidate' and 'end'
    inside 'recommendations', which is how a missing section quietly counts as present."""
    return bool(re.search(r"\b(" + "|".join(re.escape(w) for w in sorted(words)) + r")\b", text))


def requested_sections(prompt: str) -> list[str]:
    """The lines of the prompt's `Structure:` block.

    Read from the prompt rather than configured per template, so the same comparator serves the
    isotherm and FPLC reports without being told what either asks for.
    """
    lines = prompt.splitlines()
    out = []
    for i, line in enumerate(lines):
        if re.match(r"^\s*structure\s*:?\s*$", line, re.I):
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if not s or s.lower().startswith(("use ", "note", "the attached")):
                    break
                out.append(s)
            break
    return out


def check_sections(requested: list[str], html: str) -> tuple[list, list]:
    """(findings, evidence). A requirement is met when most of its distinctive tokens appear."""
    findings, evidence = [], []
    text = visible_text(html)
    heads = sections(html)
    for req in requested:
        toks = tokens(req)
        if not toks or set(toks) & SYNONYMS["heading"]:
            # "Heading" asks for a title, not a section: look for a large-font or h1 opener.
            has_title = bool(re.search(r"font-size:\s*1[6-9]pt|font-size:\s*[2-9]\dpt|<h1", html,
                                       re.I))
            evidence.append({"requested": req, "kind": "title", "satisfied": has_title})
            if not has_title:
                findings.append({"code": "REQUESTED_SECTION_MISSING", "requested": req,
                                 "detail": "no title-styled opening element"})
            continue
        hits = [t for t in toks if mentions(text, expand(t))]
        satisfied = len(hits) * 2 >= len(toks)
        near = [h for h in heads if any(mentions(h, expand(t)) for t in toks)]
        evidence.append({"requested": req, "tokens": toks, "matched": hits,
                         "headings": near[:3], "satisfied": satisfied})
        if not satisfied:
            findings.append({"code": "REQUESTED_SECTION_MISSING", "requested": req,
                             "tokens": toks, "matched": hits})
        elif not near:
            findings.append({"code": "SECTION_NAMING_DIFFERS", "requested": req,
                             "detail": "content present but under no similarly-named heading"})
    return findings, evidence


def check_plots(selected_plots: list, used: set) -> list:
    """Every selected plot needs somewhere to render.

    PLOTS_SECTION covers all of them at once and PLOT_n covers the nth; the application resolves
    both to a real eLabFTW image URL (report_generator_template.py.j2:429 and :441-443), so either
    satisfies the prompt's "use the plots" and neither is preferred.
    """
    if not selected_plots:
        return []
    if "PLOTS_SECTION" in used:
        return []
    missing = [i for i in range(1, len(selected_plots) + 1) if f"PLOT_{i}" not in used]
    if missing:
        return [{"code": "PLOT_NOT_EMBEDDED", "selected_plots": len(selected_plots),
                 "no_placeholder_for": missing,
                 "detail": "neither PLOTS_SECTION nor the numbered slot is present"}]
    return []


def compare(cand_html: str, field_names: list[str], prompt: str, selected_plots: list,
            ref_html: str | None = None) -> dict:
    findings = []

    if not cand_html.strip():
        return {"pass": False, "codes": ["MALFORMED_TEMPLATE"],
                "findings": [{"code": "MALFORMED_TEMPLATE", "detail": "empty template"}]}
    heads = sections(cand_html)
    used = set(PLACEHOLDER.findall(cand_html))
    if not heads:
        findings.append({"code": "MALFORMED_TEMPLATE", "detail": "no headings at all"})
    if not used:
        findings.append({"code": "MALFORMED_TEMPLATE", "detail": "no [[PLACEHOLDER]] fields"})
    bad_tags = sorted({m.group(1).lower() for m in FORBIDDEN_TAG.finditer(cand_html)})
    if bad_tags:
        findings.append({"code": "MALFORMED_TEMPLATE", "forbidden_tags": bad_tags,
                         "detail": "eLabFTW cannot render these"})

    requested = requested_sections(prompt)
    sec_findings, evidence = check_sections(requested, cand_html)
    findings += sec_findings
    findings += check_plots(selected_plots, used)

    allowed = {app_placeholder(n) for n in field_names} | STANDARD
    unknown = sorted(p for p in used if p not in allowed and not PLOT_SLOT.match(p))
    if unknown:
        findings.append({"code": "PLACEHOLDER_NOT_AVAILABLE", "invented": unknown,
                         "detail": "not an offered ELN field, not a plot slot"})

    ref_used = set(PLACEHOLDER.findall(ref_html)) if ref_html else set()
    unused = sorted(p for p in ref_used if p not in used)
    if unused:
        findings.append({"code": "REFERENCE_PLACEHOLDER_UNUSED", "placeholders": unused})

    fatal = [f for f in findings if f["code"] not in NON_FATAL]
    return {
        "pass": not fatal,
        "codes": sorted({f["code"] for f in findings}),
        "fatal_codes": sorted({f["code"] for f in fatal}),
        "findings": findings,
        "requested_sections": requested,
        "section_evidence": evidence,
        "candidate_sections": heads,
        "candidate_placeholders": len(used),
        "reference_placeholders": len(ref_used),
        "parameter_set_size": len(allowed),
        "selected_plots": len(selected_plots),
    }


NON_FATAL = {"SECTION_NAMING_DIFFERS", "REFERENCE_PLACEHOLDER_UNUSED"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--fields", required=True,
                    help="JSON list of ELN field names, or the analysis payload's template_fields")
    ap.add_argument("--prompt", required=True,
                    help="the report payload JSON; its original_prompt carries the Structure block")
    ap.add_argument("--reference", help="optional: only used for the non-fatal unused-field report")
    ap.add_argument("--out")
    args = ap.parse_args()

    raw = json.loads(pathlib.Path(args.fields).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("template_fields", raw.get("extra_fields", []))
    names = [f["name"] if isinstance(f, dict) else str(f) for f in raw]

    payload = json.loads(pathlib.Path(args.prompt).read_text(encoding="utf-8"))
    prompt = payload.get("original_prompt") or payload.get("prompt") or ""
    plots = payload.get("selected_plots") or []

    ref = pathlib.Path(args.reference).read_text(encoding="utf-8", errors="replace") \
        if args.reference else None
    r = compare(pathlib.Path(args.candidate).read_text(encoding="utf-8", errors="replace"),
                names, prompt, plots, ref)

    print(f"  requested    : {r['requested_sections']}")
    print(f"  candidate    : {r['candidate_sections']}")
    print(f"  placeholders : candidate {r['candidate_placeholders']}, "
          f"parameter set {r['parameter_set_size']}, plots {r['selected_plots']}")
    print(f"  verdict      : {'PASS' if r['pass'] else 'FAIL ' + ','.join(r['fatal_codes'])}")
    for f in r["findings"]:
        print(f"      {json.dumps(f)[:220]}")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
