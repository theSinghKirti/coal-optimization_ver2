"""
Regression tests establishing backend/optimizer.py (via POST /optimize) as
the ONLY authoritative optimizer in this project.

Run from the repo root with the project's venv, e.g.:
    cd coal-optimizer-package
    .venv/Scripts/python.exe -m pytest backend/tests/ -v

Or from inside backend/:
    pytest tests/ -v
"""
import os

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A known-feasible, two-plant payload sharing coal companies across plants -
# the shape a real dashboard POST /optimize call sends.
VALID_PAYLOAD = {
    "plants": [
        {
            "plant": "Harduaganj (2X250 MW)",
            "sources": [
                {"company": "BCCL", "current_rakes": 20, "current_vc": 4.78, "minRakes": 8, "maxRakes": 24},
                {"company": "CCL", "current_rakes": 40, "current_vc": 3.90, "minRakes": 16, "maxRakes": 48},
            ],
        },
        {
            "plant": "Parichha",
            "sources": [
                {"company": "NCL", "current_rakes": 60, "current_vc": 3.93, "minRakes": 24, "maxRakes": 72},
                {"company": "CCL", "current_rakes": 25, "current_vc": 3.59, "minRakes": 10, "maxRakes": 30},
                {"company": "BCCL", "current_rakes": 0, "current_vc": 4.99, "minRakes": 0, "maxRakes": 42},
            ],
        },
    ]
}

# A single (plant, company) row whose own [minRakes, maxRakes] bound cannot
# possibly contain the company's conserved total (10) - guarantees the
# solver finds no feasible point, regardless of any other constraint.
INFEASIBLE_PAYLOAD = {
    "plants": [
        {
            "plant": "TestPlant",
            "sources": [
                {"company": "TestCo", "current_rakes": 10, "current_vc": 4.0, "minRakes": 15, "maxRakes": 20},
            ],
        }
    ]
}


def _is_whole_number(value, tol=1e-6):
    return abs(value - round(value)) < tol


# ---------------------------------------------------------------------
# /optimize returns the official OR-Tools result with all required fields
# ---------------------------------------------------------------------
def test_optimize_returns_official_result_with_required_fields():
    resp = client.post("/optimize", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] in ("Optimal", "Feasible")
    assert "allocations" in body and len(body["allocations"]) > 0
    assert "plants" in body and len(body["plants"]) == len(VALID_PAYLOAD["plants"])
    assert "weighted_vc_before" in body and body["weighted_vc_before"] is not None
    assert "weighted_vc_after" in body and body["weighted_vc_after"] is not None
    assert "constraint_status" in body and len(body["constraint_status"]) > 0


def test_allocation_rows_reference_source_vc_not_a_recomputed_value():
    """The optimizer must use the caller-supplied current_vc as-is as the
    cost coefficient, never generate or overwrite it."""
    resp = client.post("/optimize", json=VALID_PAYLOAD)
    body = resp.json()

    supplied_vc = {
        (p["plant"], s["company"]): s["current_vc"]
        for p in VALID_PAYLOAD["plants"] for s in p["sources"]
    }
    for a in body["allocations"]:
        assert a["source_vc"] == supplied_vc[(a["plant"], a["company"])]


# ---------------------------------------------------------------------
# All rake values are integers
# ---------------------------------------------------------------------
def test_all_optimized_rake_values_are_integers():
    resp = client.post("/optimize", json=VALID_PAYLOAD)
    body = resp.json()

    for a in body["allocations"]:
        assert _is_whole_number(a["optimized_rakes"]), (
            f"{a['plant']}/{a['company']} optimized_rakes={a['optimized_rakes']} is not integral"
        )
    for p in body["plants"]:
        assert _is_whole_number(p["optimized_rakes"]), (
            f"{p['plant']} optimized_rakes={p['optimized_rakes']} is not integral"
        )


# ---------------------------------------------------------------------
# Company-wide conservation is actually enforced (the whole point of using
# the backend instead of the per-plant client/CLI previews)
# ---------------------------------------------------------------------
def test_company_conservation_is_enforced_exactly():
    resp = client.post("/optimize", json=VALID_PAYLOAD)
    body = resp.json()

    current_by_company = {}
    for p in VALID_PAYLOAD["plants"]:
        for s in p["sources"]:
            current_by_company[s["company"]] = current_by_company.get(s["company"], 0) + s["current_rakes"]

    optimized_by_company = {}
    for a in body["allocations"]:
        optimized_by_company[a["company"]] = optimized_by_company.get(a["company"], 0) + a["optimized_rakes"]

    for company, total in current_by_company.items():
        assert abs(optimized_by_company[company] - total) < 0.5, (
            f"company {company}: {total} -> {optimized_by_company[company]}"
        )

    company_constraints = [c for c in body["constraint_status"] if c["name"].startswith("Company conservation")]
    assert len(company_constraints) == len(current_by_company)
    assert all(c["satisfied"] for c in company_constraints)


# ---------------------------------------------------------------------
# Failure does not silently produce a valid-looking result
# ---------------------------------------------------------------------
def test_infeasible_input_returns_explicit_infeasible_status():
    resp = client.post("/optimize", json=INFEASIBLE_PAYLOAD)
    assert resp.status_code == 200  # infeasibility is a valid, explicit response - not an HTTP error
    body = resp.json()

    assert body["status"] == "Infeasible"
    assert body["message"]  # a human-readable reason must be present
    # Must not also claim a solved allocation alongside "Infeasible"
    assert body["allocations"] == []


def test_empty_payload_returns_explicit_infeasible_not_silent_success():
    resp = client.post("/optimize", json={"plants": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Infeasible"
    assert "No plant/source data supplied" in body["message"]


def test_invalid_current_vc_is_rejected_not_silently_accepted():
    bad_payload = {
        "plants": [{
            "plant": "TestPlant",
            "sources": [
                {"company": "TestCo", "current_rakes": 10, "current_vc": 0, "minRakes": 5, "maxRakes": 15},
            ],
        }]
    }
    resp = client.post("/optimize", json=bad_payload)
    assert resp.status_code == 422  # Pydantic's current_vc > 0 constraint rejects it outright


def test_non_integer_rakes_returns_business_validation_error():
    bad_payload = {
        "plants": [{
            "plant": "TestPlant",
            "sources": [
                {"company": "TestCo", "current_rakes": 10.5, "current_vc": 4.0, "minRakes": 5, "maxRakes": 15},
            ],
        }]
    }
    resp = client.post("/optimize", json=bad_payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "validation_error"
    assert len(body["errors"]) > 0
    assert body["allocations"] == []


# ---------------------------------------------------------------------
# No greedy fallback exists in the backend, and it never returns a heuristic
# result labeled as if it were an OR-Tools solve.
# ---------------------------------------------------------------------
def test_backend_optimizer_source_has_no_greedy_fallback():
    """Regression guard: backend/optimizer.py must never grow a silent
    greedy/heuristic fallback path the way the CLI (main.py) used to have.
    If OR-Tools can't solve, it must return Infeasible - never substitute
    a heuristic result labeled as if it were the real solve."""
    with open(os.path.join(BACKEND_DIR, "optimizer.py"), encoding="utf-8") as f:
        source = f.read().lower()
    assert "greedy" not in source
    assert "heuristic" not in source


def test_solver_creation_failure_returns_explicit_error_not_fake_result(monkeypatch):
    """If OR-Tools itself can't produce a solver (e.g. native lib blocked),
    the endpoint must fail explicitly, not fabricate a result."""
    import optimizer as optimizer_module

    class _NullSolver:
        @staticmethod
        def CreateSolver(_name):
            return None

    class _NullPywraplp:
        Solver = _NullSolver

    monkeypatch.setattr(optimizer_module, "pywraplp", _NullPywraplp)
    resp = client.post("/optimize", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Infeasible"
    assert "solver" in body["message"].lower()
    assert body["allocations"] == []


# ---------------------------------------------------------------------
# Frontend preview is not used in / does not influence backend computation
# ---------------------------------------------------------------------
def test_backend_has_no_dependency_on_frontend_files():
    """The backend must be fully independent of dashboard_template.html /
    output/index.html and the client-side preview optimizer - it only ever
    computes from the JSON request body."""
    for fname in ("optimizer.py", "main.py", "schemas.py"):
        with open(os.path.join(BACKEND_DIR, fname), encoding="utf-8") as f:
            source = f.read().lower()
        assert "dashboard_template" not in source
        assert "output/index.html" not in source
        assert "optimizeplant(" not in source  # the JS/CLI client-side preview function name
        assert "water-filling" not in source
        assert "greedy" not in source


def test_result_depends_only_on_request_body_not_on_prior_calls():
    """Two back-to-back requests with different current_vc must produce
    different results - proving there's no cached/shared/global state
    (e.g. a leftover client-preview value) leaking between requests."""
    resp1 = client.post("/optimize", json=VALID_PAYLOAD)
    body1 = resp1.json()

    payload2 = {
        "plants": [
            {
                "plant": p["plant"],
                "sources": [
                    {**s, "current_vc": s["current_vc"] + 5.0}  # make BCCL/CCL/etc drastically more expensive
                    for s in p["sources"]
                ],
            }
            for p in VALID_PAYLOAD["plants"]
        ]
    }
    resp2 = client.post("/optimize", json=payload2)
    body2 = resp2.json()

    assert body1["weighted_vc_after"] != body2["weighted_vc_after"]
