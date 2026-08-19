"""
Tests for the optional RSD/VC threshold field (rsd_threshold_vc) on
PlantInput: acceptance, null/empty semantics, and backward compatibility
with the legacy 'rsd_threshold' request key.
"""
import pytest

from fastapi.testclient import TestClient

from schemas import PlantInput
from main import app

client = TestClient(app)


def _plant(**overrides):
    base = {
        "plant": "TestPlant",
        "sources": [
            {"company": "BCCL", "current_rakes": 10, "current_vc": 4.78, "minRakes": 5, "maxRakes": 15},
        ],
    }
    base.update(overrides)
    return base


def test_rsd_threshold_vc_accepts_numeric_value():
    plant = PlantInput.model_validate(_plant(rsd_threshold_vc=4.12))
    assert plant.rsd_threshold_vc == 4.12


def test_rsd_threshold_vc_missing_means_no_constraint():
    plant = PlantInput.model_validate(_plant())
    assert plant.rsd_threshold_vc is None


def test_rsd_threshold_vc_null_means_no_constraint():
    plant = PlantInput.model_validate(_plant(rsd_threshold_vc=None))
    assert plant.rsd_threshold_vc is None


def test_rsd_threshold_vc_empty_string_means_no_constraint():
    plant = PlantInput.model_validate(_plant(rsd_threshold_vc=""))
    assert plant.rsd_threshold_vc is None


def test_rsd_threshold_vc_numeric_string_is_coerced():
    plant = PlantInput.model_validate(_plant(rsd_threshold_vc="4.12"))
    assert plant.rsd_threshold_vc == 4.12


def test_legacy_rsd_threshold_key_still_accepted():
    plant = PlantInput.model_validate(_plant(rsd_threshold=4.12))
    assert plant.rsd_threshold_vc == 4.12
    assert plant.rsd_threshold == 4.12


def test_rsd_threshold_vc_takes_precedence_over_legacy_key():
    plant = PlantInput.model_validate(_plant(rsd_threshold=4.12, rsd_threshold_vc=5.5))
    assert plant.rsd_threshold_vc == 5.5
    assert plant.rsd_threshold == 5.5


def test_legacy_rsd_threshold_null_means_no_constraint():
    plant = PlantInput.model_validate(_plant(rsd_threshold=None))
    assert plant.rsd_threshold_vc is None


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_non_finite_rsd_threshold_vc_is_rejected(bad):
    resp = client.post("/optimize", json={"plants": [_plant(rsd_threshold_vc=bad)]})
    assert resp.status_code == 422


def test_legacy_key_works_end_to_end_via_api():
    payload = {"plants": [_plant(rsd_threshold=5.0)]}
    resp = client.post("/optimize", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("Optimal", "Feasible")


def test_new_key_works_end_to_end_via_api():
    payload = {"plants": [_plant(rsd_threshold_vc=5.0)]}
    resp = client.post("/optimize", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("Optimal", "Feasible")


def test_empty_threshold_works_end_to_end_via_api():
    payload = {"plants": [_plant(rsd_threshold_vc="")]}
    resp = client.post("/optimize", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("Optimal", "Feasible")
