"""
Tests for the post-diversion VC calculator (POST /calculate-diversion):
plant-wise and overall blended VC from operator-entered actual rakes.

The calculator is deliberately separate from /optimize - no solver, no
bounds, no RSD logic - but rake quantities are whole numbers there too:
fractional rakes are rejected at parse time like everywhere else in the
project.
"""
import math

import pytest

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _payload():
    return {
        "plants": [
            {
                "plant": "PlantA",
                "sources": [
                    {"company": "BCCL", "current_rakes": 10, "current_vc": 4.78, "rakes": 6},
                    {"company": "CCL", "current_rakes": 0, "current_vc": 3.90, "rakes": 4},
                ],
            },
            {
                "plant": "PlantB",
                "sources": [
                    {"company": "NCL", "current_rakes": 5, "current_vc": 4.20, "rakes": 8},
                ],
            },
        ]
    }


def test_calculator_returns_plant_wise_and_overall_vc():
    resp = client.post("/calculate-diversion", json=_payload())
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"

    # PlantA: current 10 rakes @ 4.78; actual 6@4.78 + 4@3.90 = 4.428
    a = next(p for p in body["plants"] if p["plant"] == "PlantA")
    assert a["current_rakes"] == 10
    assert a["actual_rakes"] == 10
    assert a["current_vc"] == pytest.approx(4.78)
    assert a["actual_vc"] == pytest.approx(4.428)
    assert a["delta_vc"] == pytest.approx(-0.352)

    # PlantB: unchanged 4.20 in both mixes
    b = next(p for p in body["plants"] if p["plant"] == "PlantB")
    assert b["current_vc"] == pytest.approx(4.20)
    assert b["actual_vc"] == pytest.approx(4.20)
    assert b["delta_vc"] == pytest.approx(0.0)

    # Overall: current (10*4.78 + 5*4.20)/15; actual (10*4.428 + 8*4.20)/18
    assert body["weighted_vc_current"] == pytest.approx(4.5866667)
    assert body["weighted_vc_actual"] == pytest.approx(4.3266667)
    assert body["vc_improvement"] == pytest.approx(4.5866667 - 4.3266667)
    assert body["total_rakes_current"] == 15
    assert body["total_rakes_actual"] == 18


def test_zero_rake_plant_has_null_vc_and_others_still_calculate():
    payload = {
        "plants": [
            {
                "plant": "PlantA",
                "sources": [
                    {"company": "BCCL", "current_rakes": 10, "current_vc": 4.78, "rakes": 0},
                    {"company": "CCL", "current_rakes": 0, "current_vc": 3.90, "rakes": 0},
                ],
            },
            {
                "plant": "PlantB",
                "sources": [
                    {"company": "NCL", "current_rakes": 5, "current_vc": 4.20, "rakes": 5},
                ],
            },
        ]
    }
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 200
    body = resp.json()

    a = next(p for p in body["plants"] if p["plant"] == "PlantA")
    assert a["actual_rakes"] == 0
    assert a["actual_vc"] is None
    assert a["delta_vc"] is None

    b = next(p for p in body["plants"] if p["plant"] == "PlantB")
    assert b["actual_vc"] == pytest.approx(4.20)

    assert body["weighted_vc_actual"] == pytest.approx(4.20)


def test_fractional_rakes_are_rejected():
    """Rakes are physical units everywhere - the calculator rejects fractional
    actual or current rakes at parse time (HTTP 422), never rounds them."""
    payload = {
        "plants": [
            {
                "plant": "PlantA",
                "sources": [
                    {"company": "BCCL", "current_rakes": 12, "current_vc": 4.78, "rakes": 12.3},
                ],
            },
        ]
    }
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 422
    msg = resp.json()["detail"][0]["msg"].lower()
    assert "whole number" in msg

    payload["plants"][0]["sources"][0]["rakes"] = 12
    payload["plants"][0]["sources"][0]["current_rakes"] = 12.5
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 422


def test_negative_rakes_are_rejected():
    """Negative rakes fail Pydantic's ge=0 field constraint (HTTP 422)
    before the endpoint's business validation runs."""
    payload = _payload()
    payload["plants"][0]["sources"][0]["rakes"] = -1
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 422


@pytest.mark.parametrize("bad", ["nan", "-inf"])
def test_nan_and_negative_inf_rakes_are_rejected(bad):
    """NaN and -inf fail Pydantic's ge=0 field constraint (HTTP 422)."""
    payload = _payload()
    payload["plants"][0]["sources"][0]["rakes"] = bad
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 422


def test_infinite_rakes_return_business_validation_error():
    """+inf (as a string) fails the whole-number rake parse-time validation
    (HTTP 422)."""
    payload = _payload()
    payload["plants"][0]["sources"][0]["rakes"] = "inf"
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 422


@pytest.mark.parametrize("bad", ["nan", "-inf"])
def test_nan_and_negative_inf_current_vc_are_rejected(bad):
    payload = _payload()
    payload["plants"][1]["sources"][0]["current_vc"] = bad
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 422


def test_infinite_current_vc_returns_business_validation_error():
    payload = _payload()
    payload["plants"][1]["sources"][0]["current_vc"] = "inf"
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "validation_error"
    assert any(e["field"] == "current_vc" for e in body["errors"])


def test_empty_payload_returns_validation_error():
    resp = client.post("/calculate-diversion", json={"plants": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "validation_error"
    assert body["message"] == "No plant/source data supplied"


def test_all_zero_rakes_yield_null_overall_vcs():
    payload = _payload()
    for plant in payload["plants"]:
        for src in plant["sources"]:
            src["rakes"] = 0
    resp = client.post("/calculate-diversion", json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert body["weighted_vc_actual"] is None
    assert body["vc_improvement"] is None
    assert body["total_rakes_actual"] == 0


def test_response_rake_fields_serialize_as_integers():
    """totals and plant rake sums are emitted as JSON integers (only VCs are
    decimals)."""
    resp = client.post("/calculate-diversion", json=_payload())
    assert resp.status_code == 200
    body = resp.json()

    assert type(body["total_rakes_current"]) is int
    assert type(body["total_rakes_actual"]) is int
    for p in body["plants"]:
        assert type(p["current_rakes"]) is int, p
        assert type(p["actual_rakes"]) is int, p


def test_response_shape_is_distinct_from_optimize_response():
    """Separate endpoint, separate response shape: no optimizer-only fields
    (allocations, constraint_status, total_shutdowns, rsd_*) leak in."""
    resp = client.post("/calculate-diversion", json=_payload())
    body = resp.json()

    assert "allocations" not in body
    assert "constraint_status" not in body
    assert "total_shutdowns" not in body
    assert "weighted_vc_before" not in body
    assert "weighted_vc_after" not in body


def test_optimize_endpoint_untouched():
    """The optimizer endpoint remains intact and separate: its own payload
    (with minRakes/maxRakes) still solves exactly as before."""
    payload = {
        "plants": [
            {
                "plant": "PlantA",
                "sources": [
                    {"company": "BCCL", "current_rakes": 10, "current_vc": 4.78, "minRakes": 5, "maxRakes": 15},
                ],
            },
        ]
    }
    resp = client.post("/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("Optimal", "Feasible")
    assert body["weighted_vc_before"] == pytest.approx(4.78)
    assert body["weighted_vc_after"] == pytest.approx(4.78)