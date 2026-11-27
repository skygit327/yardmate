"""Root-cause process suggestions: process flows, the NCR sample and limit-sample history."""

from pathlib import Path

import pytest

from spec_agent.services.traceback import analyze, default_flow, match_defect, read_ncr, stratify

DOCS = Path(__file__).resolve().parents[1] / "docs"


def sample(pattern: str) -> Path:
    matches = sorted(DOCS.glob(pattern))
    if not matches:
        pytest.skip(f"{pattern} is not available")
    return matches[0]


def test_ncr_is_read_into_measurements_and_factors():
    ncr = read_ncr("ncr.pdf", sample("*NCR*.pdf").read_bytes())
    assert ncr["header"]["ncr_no"] == "NCR-CASE-001"
    assert ncr["header"]["problem"] == "Diameter 85.85 is out of specification"
    assert ncr["spec"]["lower"] == 85.83 and ncr["spec"]["upper"] == 85.86
    assert [d["name"] for d in ncr["datasets"]] == ["HOUSING PRODUCT B", "HOUSING PRODUCT A"]
    product_b = ncr["datasets"][0]
    assert list(product_b["factors"]) == [
        "MOLD",
        "MACHINING",
        "MACHINE",
        "STAMPING DIE",
        "LOT",
        "Method",
    ]
    assert product_b["factors"]["LOT"][5] == "LOT-B (20,000 pcs)"  # merged cells carried forward
    assert product_b["values"][0] == 85.8246 and len(product_b["values"]) == 10
    assert ncr["actions"][0] == "Please make 8D's Report" and len(ncr["actions"]) == 6


def test_ncr_points_to_a_common_cause_in_machining_then_measurement():
    ncr = read_ncr("ncr.pdf", sample("*NCR*.pdf").read_bytes())
    flow = default_flow()
    data = stratify(ncr, flow["steps"])
    assert data["summary"]["n"] == 20 and data["summary"]["ng"] == 17
    assert data["summary"]["below"] == 17 and data["summary"]["above"] == 0
    verdicts = {factor["name"]: (factor["step"], factor["verdict"]) for factor in data["factors"]}
    assert verdicts["MOLD"] == ("mold", "common")
    assert verdicts["MACHINE"] == ("machining", "common")  # a one-piece level does not decide
    assert verdicts["STAMPING DIE"] == ("trimming", "common")
    result = analyze(flow, match_defect(ncr["header"]["problem"]), None, data, [])
    ranked = [candidate["step"] for candidate in result["candidates"]]
    assert ranked[:2] == ["machining", "inspection"]
    assert result["candidates"][0]["likelihood"] == "high"
    codes = [finding["code"] for finding in result["findings"]]
    assert codes == ["COMMON_CAUSE", "SYSTEMATIC_OFFSET"]


def test_a_failure_cluster_on_one_mold_is_singled_out():
    ncr = {
        "spec": {"lower": 9.9, "upper": 10.1},
        "datasets": [
            {
                "name": "P",
                "factors": {"MOLD": ["M1"] * 4 + ["M2"] * 4, "MACHINE": ["E1", "E2"] * 4},
                "values": [9.8, 9.82, 9.85, 9.81, 10.0, 10.01, 9.99, 10.0],
            }
        ],
    }
    flow = default_flow()
    data = stratify(ncr, flow["steps"])
    mold = next(factor for factor in data["factors"] if factor["name"] == "MOLD")
    assert mold["verdict"] == "specific" and mold["worst"] == "M1"
    result = analyze(flow, "dimension", None, data, [])
    top = result["candidates"][0]
    assert top["step"] == "mold"
    assert any(
        reason["code"] == "FACTOR_SPECIFIC" and reason["params"]["level"] == "M1"
        for reason in top["reasons"]
    )


def test_only_steps_before_detection_are_suggested():
    flow = default_flow()
    result = analyze(flow, "scratch", "polishing", None, [])
    keys = [step["key"] for step in flow["steps"]]
    assert all(keys.index(c["step"]) <= keys.index("polishing") for c in result["candidates"])
    assert result["candidates"][0]["step"] == "polishing"


@pytest.mark.parametrize(
    ("text", "defect"),
    [
        ("파팅면 버(Burr)", "burr"),
        ("오버플로 잔류물 높이", "gate_residue"),
        ("Crack", "crack"),
        ("표면 기공", "porosity"),
        ("???", None),
    ],
)
def test_defect_names(text, defect):
    assert match_defect(text) == defect


def test_api_flow_editing_history_and_cases(client):
    library = client.get("/api/process-library").json()
    assert (
        "machining" in library["steps"] and library["templates"][0]["id"] == "die_casting_housing"
    )

    # Limit-sample history from model A informs appearance defects.
    path = sample("*모델A*.pdf")
    reading = client.post(
        "/api/forms", files={"file": (path.name, path.read_bytes(), "application/pdf")}
    ).json()
    client.post(f"/api/forms/{reading['id']}/limit-approval", json={"customer": "DEMO-OEM"})
    case = client.post("/api/traceback", json={"defect": "크랙", "part_number": "Part-A"}).json()
    top = case["analysis"]["candidates"][0]
    assert top["step"] == "casting"
    assert any(reason["code"] == "HISTORY" for reason in top["reasons"])
    assert top["countermeasures"]

    # A part-specific flow without CNC machining changes the suggestion order.
    flow = client.post(
        "/api/process-flows",
        json={
            "name": "Housing line 2",
            "part_numbers": ["P-A001"],
            "steps": [
                {"key": "mold"},
                {"key": "casting"},
                {"key": "trimming", "label": "Press 2"},
                {"key": "inspection"},
            ],
        },
    )
    assert flow.status_code == 201, flow.text
    duplicate = client.post(
        "/api/process-flows", json={"name": "x", "steps": [{"key": "mold"}, {"key": "mold"}]}
    )
    assert duplicate.status_code == 422
    path = sample("*NCR*.pdf")
    ncr_case = client.post(
        "/api/traceback/ncr", files={"file": (path.name, path.read_bytes(), "application/pdf")}
    ).json()
    assert ncr_case["analysis"]["flow"]["name"] == "Housing line 2"
    assert "machining" not in [c["step"] for c in ncr_case["analysis"]["candidates"]]
    assert ncr_case["summary"]["ng"] == 17

    saved = client.post(f"/api/traceback/{ncr_case['id']}/save", json={"customer": "DEMO-OEM"})
    assert saved.status_code == 200
    assert len(client.get("/api/traceback/cases", params={"customer": "amx"}).json()) == 1
    assert client.get(f"/api/traceback/{ncr_case['id']}").json()["customer"] == "DEMO-OEM"
    assert client.delete(f"/api/process-flows/{flow.json()['id']}").status_code == 200
    assert client.get("/api/traceback/missing").status_code == 404
