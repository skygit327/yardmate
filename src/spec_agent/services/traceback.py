"""Suggest which process most likely caused a defect (root-cause candidates, not verdicts).

Three sources of evidence are combined on top of the part's process flow:

1. Process order - a cause must sit at or before the step where the defect was found.
2. Process knowledge - which steps can create which defect type (``process_lexicon.json``).
3. Data - for NCR measurement tables, stratification by factor (mold, machine, die, lot,
   measuring position) shows whether the failures cluster on one equipment level or
   affect every level alike (a common, systematic cause); for appearance defects, the
   customer's earlier limit-sample decisions record which process caused similar defects.

The result is always presented as ranked candidates with the evidence behind them.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from statistics import mean, pstdev

LEXICON = json.loads(Path(__file__).with_name("process_lexicon.json").read_text("utf-8"))
STEPS = LEXICON["steps"]
DEFECTS = LEXICON["defects"]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def match_defect(text: str) -> str | None:
    """Map free text (an item name, NCR problem) to a defect key; longest alias wins."""
    text = _norm(text)
    best, length = None, 0
    for key, entry in DEFECTS.items():
        for alias in entry["aliases"]:
            alias_norm = _norm(alias)
            if alias_norm and alias_norm in text and len(alias_norm) > length:
                best, length = key, len(alias_norm)
    return best


def match_step(name: str, steps: list[dict]) -> str | None:
    """Map a factor or process name from a document to a step key in the flow."""
    name = _norm(name)
    best, length = None, 0
    for step in steps:
        aliases = [*STEPS.get(step["key"], {}).get("aliases", []), step.get("label") or ""]
        for alias in aliases:
            alias_norm = _norm(alias)
            if (
                alias_norm
                and (alias_norm == name or alias_norm in name)
                and len(alias_norm) > length
            ):
                best, length = step["key"], len(alias_norm)
    return best


# --- Process flows ---------------------------------------------------------------------


def templates() -> list[dict]:
    return [
        {
            "id": template["id"],
            "names": template["names"],
            "steps": [{"key": key} for key in template["steps"]],
        }
        for template in LEXICON["templates"]
    ]


def library() -> dict:
    """Step and defect vocabulary for the flow editor."""
    return {
        "steps": {
            key: {
                "names": step["names"],
                "aliases": step["aliases"],
                "defects": step["defects"],
                "checks": step["checks"],
            }
            for key, step in STEPS.items()
        },
        "defects": {key: {"names": entry["names"]} for key, entry in DEFECTS.items()},
        "templates": templates(),
    }


def default_flow() -> dict:
    template = LEXICON["templates"][0]
    return {
        "id": None,
        "name": template["names"]["en"],
        "template": template["id"],
        "customer": "",
        "part_numbers": [],
        "steps": [{"key": key} for key in template["steps"]],
    }


# --- NCR reading -----------------------------------------------------------------------


def _float(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def read_ncr(filename: str, content: bytes) -> dict:
    import pdfplumber
    from pypdf import PdfReader

    if not content.startswith(b"%PDF-"):
        raise ValueError("NCR files must be PDF files.")
    plain = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    fields = LEXICON["ncr"]["fields"]
    labels = {word for words in fields.values() for word in words}
    header: dict[str, str] = {}
    datasets = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                cells = [[(cell or "").strip() for cell in row] for row in table]
                for row in cells:
                    for index, cell in enumerate(row):
                        key = next((k for k, words in fields.items() if cell in words), None)
                        if key and key not in header:
                            value = next((v for v in row[index + 1 :] if v), "")
                            if value and value not in labels:
                                header[key] = value
                measurement = next(
                    (
                        row
                        for row in cells
                        if row and sum(_float(cell) is not None for cell in row[1:]) >= 3
                    ),
                    None,
                )
                if measurement is None or len(cells) > 12:
                    continue
                count = len(measurement) - 1
                factors = {}
                for row in cells:
                    label = row[0]
                    if (
                        not label
                        or row is measurement
                        or label == LEXICON["ncr"]["measurement_marker"]
                    ):
                        continue
                    if sum(1 for cell in row[1:] if cell) == 0:
                        continue
                    values, last = [], ""
                    for cell in row[1 : count + 1]:
                        last = cell or last
                        values.append(last)
                    if len(set(values)) >= 1 and any(values):
                        factors[label] = values
                values = [_float(cell) for cell in measurement[1:]]
                datasets.append(
                    {
                        "name": "",
                        "method": measurement[0],
                        "factors": factors,
                        "values": values,
                    }
                )
    marker = LEXICON["ncr"]["measurement_marker"]
    names = [lines[i - 1] for i, line in enumerate(lines) if line == marker and i > 0]
    names = [name for i, name in enumerate(names) if i == 0 or name != names[i - 1]]
    for dataset, name in zip(datasets, names, strict=False):
        dataset["name"] = name
    spec = None
    match = re.search(
        r"([A-Za-z][A-Za-z ]*?)\s+(\d+(?:\.\d+)?)\s*\+\s*(\d*\.?\d+)\s*/?\s*-\s*(\d*\.?\d+)", plain
    )
    if match:
        nominal, plus, minus = (float(match.group(i)) for i in (2, 3, 4))
        spec = {
            "characteristic": match.group(1).strip(),
            "nominal": nominal,
            "plus": plus,
            "minus": minus,
            "lower": round(nominal - minus, 6),
            "upper": round(nominal + plus, 6),
            "text": re.sub(r"\s+", " ", match.group(0)).strip(),
        }
    actions, collecting = [], False
    for line in lines:
        if LEXICON["ncr"]["actions_marker"].lower() in line.lower():
            collecting = True
            continue
        if collecting:
            item = re.match(r"^\d+\.\s+(.+)$", line)
            if item:
                actions.append(item.group(1).strip())
            elif actions:
                break
    actions = list(dict.fromkeys(actions))
    return {
        "filename": filename,
        "header": header,
        "spec": spec,
        "datasets": datasets,
        "actions": actions,
    }


# --- Analysis --------------------------------------------------------------------------


def _eta_squared(groups: dict[str, list[float]]) -> float:
    values = [value for group in groups.values() for value in group]
    if len(values) < 3:
        return 0.0
    grand = mean(values)
    total = sum((value - grand) ** 2 for value in values)
    if total == 0:
        return 0.0
    between = sum(len(group) * (mean(group) - grand) ** 2 for group in groups.values() if group)
    return between / total


def stratify(ncr: dict, steps: list[dict]) -> dict:
    """Pool every measured piece and compare failure rates and means per factor level."""
    spec = ncr["spec"]
    pieces = []
    for dataset in ncr["datasets"]:
        for index, value in enumerate(dataset["values"]):
            if value is None:
                continue
            factors = {
                name: levels[index]
                for name, levels in dataset["factors"].items()
                if index < len(levels)
            }
            factors["Product"] = dataset["name"] or f"#{len(pieces)}"
            ok = spec["lower"] <= value <= spec["upper"] if spec else True
            pieces.append({"value": value, "ok": ok, "factors": factors})
    if not pieces:
        raise ValueError("No measurements were found in the NCR.")
    values = [piece["value"] for piece in pieces]
    center = (spec["lower"] + spec["upper"]) / 2 if spec else mean(values)
    tolerance = (spec["upper"] - spec["lower"]) if spec else 0
    ng = sum(not piece["ok"] for piece in pieces)
    summary = {
        "n": len(pieces),
        "ng": ng,
        "ng_rate": round(ng / len(pieces), 3),
        "mean": round(mean(values), 4),
        "min": min(values),
        "max": max(values),
        "std": round(pstdev(values), 4),
        "center": round(center, 4),
        "shift": round(mean(values) - center, 4),
        "below": sum(spec is not None and piece["value"] < spec["lower"] for piece in pieces),
        "above": sum(spec is not None and piece["value"] > spec["upper"] for piece in pieces),
        "tolerance": round(tolerance, 4),
    }
    factors = []
    names = list(dict.fromkeys(name for piece in pieces for name in piece["factors"]))
    for name in names:
        groups: dict[str, list[dict]] = {}
        for piece in pieces:
            level = piece["factors"].get(name)
            if level:
                groups.setdefault(level, []).append(piece)
        if len(groups) < 2:
            continue
        levels = [
            {
                "level": level,
                "n": len(items),
                "ng": sum(not item["ok"] for item in items),
                "mean": round(mean(item["value"] for item in items), 4),
            }
            for level, items in groups.items()
        ]
        # Levels seen only once say little; judge on levels with at least two pieces.
        solid = [level for level in levels if level["n"] >= 2]
        rates = [level["ng"] / level["n"] for level in solid]
        eta2 = _eta_squared(
            {level: [item["value"] for item in items] for level, items in groups.items()}
        )
        if len(solid) < 2:
            verdict = "insufficient"
        elif min(rates) >= 0.5:
            verdict = "common"
        elif max(rates) - min(rates) >= 0.5 and eta2 >= 0.3:
            verdict = "specific"
        else:
            verdict = "mixed"
        factors.append(
            {
                "name": name,
                "step": match_step(name, steps),
                "levels": levels,
                "eta2": round(eta2, 3),
                "verdict": verdict,
                "worst": max(
                    solid or levels, key=lambda level: (level["ng"] / level["n"], level["n"])
                )["level"],
            }
        )
    return {"pieces": pieces, "summary": summary, "factors": factors}


def _history(defect: str | None, approvals: list[dict], steps: list[dict]) -> dict[str, list[dict]]:
    """Earlier limit-sample decisions for the same defect type, grouped by cause step."""
    found: dict[str, list[dict]] = {}
    if not defect:
        return found
    for approval in approvals:
        for sample in approval.get("samples", []):
            name = f"{sample.get('defect', '')} {sample.get('defect_en', '')}"
            if match_defect(name) != defect:
                continue
            step = match_step(sample.get("cause_process", ""), steps)
            if step:
                found.setdefault(step, []).append(
                    {
                        "no": sample.get("no"),
                        "model": approval.get("model", ""),
                        "decision": sample.get("decision"),
                        "cause": sample.get("cause", ""),
                        "countermeasures": sample.get("countermeasures", []),
                    }
                )
    return found


def analyze(
    flow: dict,
    defect: str | None,
    detected_at: str | None = None,
    stratified: dict | None = None,
    approvals: list[dict] | None = None,
) -> dict:
    steps = flow["steps"]
    keys = [step["key"] for step in steps]
    if detected_at in keys:
        limit = keys.index(detected_at)
        # An inspection step finds defects made before it; it can only add measurement error.
        candidates_keys = keys[: limit + 1]
    else:
        candidates_keys = keys
    history = _history(defect, approvals or [], steps)
    factors = (stratified or {}).get("factors", [])
    summary = (stratified or {}).get("summary")
    common = (
        bool(summary)
        and summary["ng_rate"] >= 0.6
        and not any(f["verdict"] == "specific" for f in factors)
    )
    candidates = []
    for position, key in enumerate(keys):
        if key not in candidates_keys:
            continue
        knowledge = STEPS.get(key, {}).get("defects", {}).get(defect or "", 0)
        score, reasons = float(knowledge), []
        if knowledge:
            reasons.append({"code": "CAN_CAUSE", "params": {"strength": knowledge}})
        for factor in (f for f in factors if f["step"] == key):
            if factor["verdict"] == "specific":
                score += 3
                level = next(
                    level for level in factor["levels"] if level["level"] == factor["worst"]
                )
                reasons.append(
                    {
                        "code": "FACTOR_SPECIFIC",
                        "params": {
                            "factor": factor["name"],
                            "level": factor["worst"],
                            "ng": level["ng"],
                            "n": level["n"],
                        },
                    }
                )
            elif factor["verdict"] == "common":
                reasons.append(
                    {
                        "code": "FACTOR_NOT_SPECIFIC",
                        "params": {"factor": factor["name"], "levels": len(factor["levels"])},
                    }
                )
        if common and knowledge >= 3:
            score += 1
            reasons.append(
                {
                    "code": "COMMON_SHIFT",
                    "params": {"shift": summary["shift"], "ng": summary["ng"], "n": summary["n"]},
                }
            )
        if common and STEPS.get(key, {}).get("role") == "inspection":
            score += 1
            reasons.append({"code": "MEASUREMENT_CHECK", "params": {}})
        if key in history:
            records = history[key]
            score += min(len(records), 3)
            reasons.append(
                {
                    "code": "HISTORY",
                    "params": {
                        "count": len(records),
                        "numbers": [record["no"] for record in records][:8],
                        "cause": next((r["cause"] for r in records if r["cause"]), ""),
                    },
                }
            )
        if score <= 0:
            continue
        countermeasures = list(
            dict.fromkeys(
                item for record in history.get(key, []) for item in record["countermeasures"]
            )
        )[:6]
        candidates.append(
            {
                "step": key,
                "position": position,
                "label": next((s.get("label") for s in steps if s["key"] == key), None),
                "names": STEPS.get(key, {}).get("names"),
                "score": score,
                "reasons": reasons,
                "checks": STEPS.get(key, {}).get("checks", []),
                "countermeasures": countermeasures,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], -item["position"]))
    top = candidates[0]["score"] if candidates else 0
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        ratio = candidate["score"] / top if top else 0
        candidate["likelihood"] = (
            "high" if rank == 1 or ratio >= 0.8 else "medium" if ratio >= 0.45 else "low"
        )
    findings = []
    if common:
        findings.append(
            {
                "code": "COMMON_CAUSE",
                "params": {
                    "ng": summary["ng"],
                    "n": summary["n"],
                    "shift": summary["shift"],
                    "factors": [
                        f["name"]
                        for f in factors
                        if f["verdict"] == "common" and f["name"] != "Product"
                    ],
                },
            }
        )
    for factor in factors:
        if factor["verdict"] == "specific":
            findings.append(
                {
                    "code": "SPECIFIC_FACTOR",
                    "params": {"factor": factor["name"], "level": factor["worst"]},
                }
            )
    if summary and summary["tolerance"]:
        tolerance = summary["tolerance"]
        if abs(summary["shift"]) > tolerance / 4 and summary["std"] < tolerance / 6:
            findings.append(
                {
                    "code": "SYSTEMATIC_OFFSET",
                    "params": {
                        "shift": summary["shift"],
                        "std": summary["std"],
                        "direction": "below" if summary["shift"] < 0 else "above",
                    },
                }
            )
    if not defect:
        findings.append({"code": "UNKNOWN_DEFECT", "params": {}})
    return {
        "flow": flow,
        "defect": defect,
        "detected_at": detected_at,
        "candidates": candidates,
        "findings": findings,
        "history": history,
    }
