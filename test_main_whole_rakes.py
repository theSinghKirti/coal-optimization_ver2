"""
Whole-number rake tests for the root CLI (main.py).

Loaded via importlib with a unique module name so importing the root
main.py can never collide with backend/main.py when the full test suite
runs from the repo root.
"""
import importlib.util
import os
import pathlib

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).parent

_spec = importlib.util.spec_from_file_location("cli_main_under_test", ROOT / "main.py")
main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main)


# ---------------------------------------------------------------------
# is_whole_rake
# ---------------------------------------------------------------------
@pytest.mark.parametrize("ok", [0, 1, 52, 52.0, "52", "0"])
def test_whole_rakes_are_accepted(ok):
    assert main.is_whole_rake(ok)


@pytest.mark.parametrize("bad", [10.5, 52.25, -1, -0.5, "10.5", "nan", float("inf"), None, "abc"])
def test_fractional_negative_or_invalid_rakes_are_rejected(bad):
    assert not main.is_whole_rake(bad)


# ---------------------------------------------------------------------
# clean_and_validate (Excel reader path)
# ---------------------------------------------------------------------
def _df_with(rakes):
    return pd.DataFrame({
        "Plant": ["P1", "P1"],
        "Source": ["A", "B"],
        "Rakes": rakes,
        "Variable Cost": [4.0, 3.9],
    })


def test_clean_and_validate_accepts_whole_rakes():
    df = main.clean_and_validate(_df_with([60, 40]))
    assert all(v == int(v) for v in df["Rakes"])


def test_clean_and_validate_rejects_fractional_rakes():
    with pytest.raises(ValueError, match="WHOLE"):
        main.clean_and_validate(_df_with([52.5, 40]))


def test_clean_and_validate_rejects_negative_rakes():
    with pytest.raises(ValueError, match="WHOLE"):
        main.clean_and_validate(_df_with([-5, 40]))


# ---------------------------------------------------------------------
# parse_freeze_arg
# ---------------------------------------------------------------------
def test_freezes_whole_values_are_parsed_as_ints():
    frozen = main.parse_freeze_arg("Parichha:NCL=52,Panki:CCL=60")
    assert frozen == {("Parichha", "NCL"): 52, ("Panki", "CCL"): 60}
    assert all(isinstance(v, int) for v in frozen.values())


def test_fractional_freeze_value_is_rejected():
    with pytest.raises(ValueError, match="whole number"):
        main.parse_freeze_arg("Parichha:NCL=52.25")


def test_negative_freeze_value_is_rejected():
    with pytest.raises(ValueError, match="whole number"):
        main.parse_freeze_arg("Parichha:NCL=-3")


# ---------------------------------------------------------------------
# apply_daily_variation (CSV path)
# ---------------------------------------------------------------------
def test_daily_variation_whole_overrides_are_applied_as_ints(tmp_path):
    csv_path = tmp_path / "daily.csv"
    csv_path.write_text(
        "plant,source,available_rakes\nParichha,NCL,52\nParichha,CCL,30\n",
        encoding="utf-8",
    )
    df = pd.DataFrame({
        "Plant": ["Parichha", "Parichha"],
        "Source": ["NCL", "CCL"],
        "Rakes": [60, 20],
        "Variable Cost": [4.0, 3.9],
    })
    result = main.apply_daily_variation(df, str(csv_path))
    assert result.loc[0, "Available Rakes"] == 52
    assert result.loc[1, "Available Rakes"] == 30
    assert all(int(v) == v for v in result["Available Rakes"])


def test_daily_variation_fractional_override_is_rejected(tmp_path):
    csv_path = tmp_path / "daily.csv"
    csv_path.write_text(
        "plant,source,available_rakes\nParichha,NCL,52.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="whole numbers"):
        main.apply_daily_variation(_df_with([60, 20]), str(csv_path))


def test_daily_variation_negative_override_is_rejected(tmp_path):
    csv_path = tmp_path / "daily.csv"
    csv_path.write_text(
        "plant,source,available_rakes\nParichha,NCL,-1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="whole numbers"):
        main.apply_daily_variation(_df_with([60, 20]), str(csv_path))


# ---------------------------------------------------------------------
# manual_change_test
# ---------------------------------------------------------------------
def test_manual_change_fractional_rakes_are_rejected():
    df = _df_with([60, 40])
    with pytest.raises(ValueError, match="whole number"):
        main.manual_change_test(df, "P1", "A", 52.5)


def test_manual_change_whole_rakes_is_applied():
    df = _df_with([60, 40])
    old_vc, new_vc, status, changed = main.manual_change_test(df, "P1", "A", 52)
    assert int(changed.loc[0, "Rakes"]) == 52


# ---------------------------------------------------------------------
# optimize_plant produces whole optimized rakes
# ---------------------------------------------------------------------
def test_optimize_plant_outputs_whole_rakes_only():
    df = _df_with([60, 40])
    result = main.optimize_all_plants(df)
    for v in result["Optimized Rakes"]:
        assert float(v).is_integer(), f"optimized rake {v} is fractional"
    by_plant = result.groupby("Plant")
    for plant, sub in by_plant:
        before = int(df[df["Plant"] == plant]["Rakes"].sum())
        after = int(sub["Optimized Rakes"].sum())
        assert before == after, f"plant {plant} total not conserved"