"""
Post-diversion VC calculator.

Evaluates the ACTUAL/manual rake diversions entered by the operator and
computes the resulting plant-wise and portfolio-wide blended Variable
Cost. It is deliberately separate from the optimizer (backend/optimizer.py
+ the RSD shutdown logic): this module performs NO solving, NO bounds, and
NO company-conservation rules - each source's entered `rakes` is taken as
the operator's ground truth and used purely as a weighting factor:

    plant VC   = sum(rakes * current_vc) / sum(rakes)
    overall VC = sum over all sources of (rakes * current_vc) / total rakes

The `current_vc` values are the same manually-entered, authoritative cost
figures the optimizer consumes (see schemas.SourceInput) - they are never
generated or recomputed here.

Rakes are strictly whole numbers everywhere, exactly like the optimizer:
the Pydantic schemas reject fractional rake values (e.g. 12.3) at parse
time with a clear message - a rake is a physical unit and cannot be
fractional. Only the resulting VCs (weighted averages) are decimal.

No results are ever hardcoded - every number is a direct weighted average
of the caller-supplied inputs.
"""

import math
from typing import List, Optional, Tuple

from schemas import (
    DiversionPlantInput,
    DiversionRequest,
    DiversionResponse,
    PlantDiversionResult,
    ValidationErrorEntry,
)

Pair = Tuple[float, float]  # (rakes, vc)


def _weighted_vc(rakes_and_vc: List[Pair]) -> Optional[float]:
    """[(rakes, vc), ...] -> rake-weighted average VC, or None if total rakes is 0."""
    total_rakes = sum(r for r, _ in rakes_and_vc)
    if total_rakes <= 0:
        return None
    total_cost = sum(r * vc for r, vc in rakes_and_vc)
    return total_cost / total_rakes


def _validate(request: DiversionRequest) -> List[ValidationErrorEntry]:
    """Business-rule validation beyond the Pydantic field types (rakes >= 0
    and whole-number-only are enforced at parse time). Catches non-finite
    values."""
    errors: List[ValidationErrorEntry] = []
    for plant in request.plants:
        for src in plant.sources:
            if not math.isfinite(src.current_vc) or src.current_vc <= 0:
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="current_vc",
                    message="Current VC is required for the diversion calculation.",
                ))
            if src.current_rakes < 0 or src.current_rakes != int(src.current_rakes):
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="current_rakes",
                    message="Current rakes must be a non-negative whole number.",
                ))
            if src.rakes < 0 or src.rakes != int(src.rakes):
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="rakes",
                    message="Actually diverted rakes must be a non-negative whole number.",
                ))
    return errors


def calculate(request: DiversionRequest) -> DiversionResponse:
    validation_errors = _validate(request)
    if validation_errors:
        return DiversionResponse(status="validation_error", errors=validation_errors)

    # ---- Flatten input into (plant, company, current_rakes, current_vc, rakes)
    rows: List[Tuple[str, str, float, float, float]] = []
    for plant in request.plants:
        for src in plant.sources:
            rows.append((plant.plant, src.company, src.current_rakes, src.current_vc, src.rakes))

    if not rows:
        return DiversionResponse(status="validation_error", message="No plant/source data supplied")

    current_pairs = [(r[2], r[3]) for r in rows]
    actual_pairs = [(r[4], r[3]) for r in rows]

    weighted_vc_current = _weighted_vc(current_pairs)
    weighted_vc_actual = _weighted_vc(actual_pairs)

    by_plant: dict = {}
    for plant, company, current_rakes, vc, rakes in rows:
        by_plant.setdefault(plant, []).append((current_rakes, vc, rakes))

    plants_result: List[PlantDiversionResult] = []
    for plant, triples in by_plant.items():
        cur_total = sum(t[0] for t in triples)
        act_total = sum(t[2] for t in triples)
        cur_vc = _weighted_vc([(t[0], t[1]) for t in triples])
        act_vc = _weighted_vc([(t[2], t[1]) for t in triples])
        delta_vc = (act_vc - cur_vc) if (cur_vc is not None and act_vc is not None) else None
        plants_result.append(PlantDiversionResult(
            plant=plant,
            current_rakes=cur_total,
            actual_rakes=act_total,
            current_vc=round(cur_vc, 6) if cur_vc is not None else None,
            actual_vc=round(act_vc, 6) if act_vc is not None else None,
            delta_vc=round(delta_vc, 6) if delta_vc is not None else None,
        ))

    vc_improvement = None
    if weighted_vc_current is not None and weighted_vc_actual is not None:
        vc_improvement = weighted_vc_current - weighted_vc_actual

    return DiversionResponse(
        status="ok",
        weighted_vc_current=round(weighted_vc_current, 6) if weighted_vc_current is not None else None,
        weighted_vc_actual=round(weighted_vc_actual, 6) if weighted_vc_actual is not None else None,
        vc_improvement=round(vc_improvement, 6) if vc_improvement is not None else None,
        total_rakes_current=sum(r[2] for r in rows),
        total_rakes_actual=sum(r[4] for r in rows),
        plants=plants_result,
    )