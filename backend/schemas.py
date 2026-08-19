"""
Pydantic schemas for the Coal Rake Diversion Optimizer API.
"""

import math
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


def _rakes_must_be_whole(value, field_name: str):
    """Rakes are physical units - a rake cannot be fractional. Rejects any
    non-whole, negative, or non-finite value with a clear message (never
    rounds or truncates)."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a whole number of rakes")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a whole number of rakes")
    if not math.isfinite(num) or num < 0:
        raise ValueError(f"{field_name} must be a non-negative whole number of rakes")
    if num != int(num):
        raise ValueError(
            f"{field_name} must be a whole number of rakes - "
            f"fractional values (e.g. {value}) are not allowed"
        )
    return int(num)


class SourceInput(BaseModel):
    """A single Plant x Coal Company allocation as currently planned.

    current_vc is a manually-entered, monthly-changing input (not derived
    from any formula or Excel sheet) - it is the cost coefficient the
    optimizer minimizes against, and stays fixed across optimization; only
    the rake allocation changes.

    Every rake quantity (current_rakes, minRakes, maxRakes) must be a
    non-negative whole number - fractional rakes are rejected at parse time.
    """
    company: str = Field(..., description="Coal company / source name, e.g. 'BCCL'")
    current_rakes: int = Field(..., ge=0, description="Current rakes for this plant-company pair (whole number, may be 0)")
    current_vc: float = Field(..., gt=0, description="Manually entered current Variable Cost (Rs/unit) for this source")
    minRakes: int = Field(..., ge=0, description="Minimum allowed rakes for this plant-company pair (whole number)")
    maxRakes: int = Field(..., ge=0, description="Maximum allowed rakes for this plant-company pair (whole number)")

    @field_validator("current_rakes", "minRakes", "maxRakes", mode="before")
    @classmethod
    def _validate_whole_rakes(cls, value):
        return _rakes_must_be_whole(value, "Rakes")


class PlantInput(BaseModel):
    plant: str = Field(..., description="Plant name")
    sources: List[SourceInput]
    rsd_threshold_vc: Optional[float] = Field(
        None,
        description=(
            "Optional RSD threshold (VC) cap. null/empty means no RSD constraint; "
            "a numeric value caps the plant's rake-weighted VC."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_rsd_threshold(cls, data):
        """Backward compatibility: map the legacy 'rsd_threshold' key onto
        'rsd_threshold_vc' so existing API callers keep working unchanged."""
        if isinstance(data, dict):
            legacy = data.get("rsd_threshold")
            new_value = data.get("rsd_threshold_vc")
            if isinstance(new_value, str):
                new_value = new_value.strip() or None
            if new_value is None and legacy is not None:
                data["rsd_threshold_vc"] = legacy
            data.pop("rsd_threshold", None)
        return data

    @field_validator("rsd_threshold_vc", mode="before")
    @classmethod
    def _normalize_rsd_threshold_vc(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        if value is None:
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return value  # let Pydantic raise its usual type error
        if not math.isfinite(num):
            raise ValueError("RSD threshold must be a finite number")
        return value

    @property
    def rsd_threshold(self) -> Optional[float]:
        """Backward-compatible read-only access to the RSD/VC threshold under
        the legacy field name (used by the optimizer until it is migrated)."""
        return self.rsd_threshold_vc


class OptimizeRequest(BaseModel):
    plants: List[PlantInput]
    # Optional overrides for the global bounds described in the spec.
    # Defaults match the spec exactly (plant total 80-110%, plant-company handled
    # via the minRakes/maxRakes already supplied per source).
    plantMinPct: float = Field(0.8, description="Minimum plant total as a fraction of its planned total")
    plantMaxPct: float = Field(1.1, description="Maximum plant total as a fraction of its planned total")


class AllocationResult(BaseModel):
    plant: str
    company: str
    current_rakes: int  # whole numbers only - rakes are physical units
    optimized_rakes: int
    source_vc: float
    delta_rakes: int  # optimized - current; negative = diverted away, positive = received
    minRakes: int
    maxRakes: int


class PlantVCResult(BaseModel):
    plant: str
    rsd_threshold_vc: Optional[float] = None  # configured RSD/VC threshold (None = no RSD constraint)
    rsd_status: str = "no_constraint"  # "safe" (within threshold) | "rsd" (exceeds) | "no_constraint"
    current_rakes: int  # whole numbers only
    optimized_rakes: int
    current_vc: Optional[float] = None    # rake-weighted, using CURRENT rakes
    optimized_vc: Optional[float] = None  # rake-weighted, using OPTIMIZED rakes
    delta_vc: Optional[float] = None      # optimized_vc - current_vc
    exceeded_threshold: bool = False
    threshold_margin: Optional[float] = None


class ConstraintStatusEntry(BaseModel):
    name: str
    satisfied: bool
    detail: str


class ValidationErrorEntry(BaseModel):
    plant: Optional[str] = None
    company: Optional[str] = None
    field: str
    message: str


class OptimizeResponse(BaseModel):
    status: str  # "Optimal" | "Feasible" | "Infeasible" | "validation_error"
    weighted_vc_before: Optional[float] = None
    weighted_vc_after: Optional[float] = None
    vc_improvement: Optional[float] = None  # weighted_vc_before - weighted_vc_after
    plants: List[PlantVCResult] = []
    allocations: List[AllocationResult] = []
    constraint_status: List[ConstraintStatusEntry] = []
    errors: List[ValidationErrorEntry] = []
    message: Optional[str] = None
    total_shutdowns: int = 0


# ---------------------------------------------------------------------------
# Post-diversion calculator (deliberately separate from the optimizer: it
# evaluates operator-entered ACTUAL/manual rake diversions as pure weighted
# averages - no solving, no bounds, no company conservation logic).
# ---------------------------------------------------------------------------

class DiversionSourceInput(BaseModel):
    """A single Plant x Coal Company row as actually diverted by the operator.

    rakes is the ACTUAL/manual allocation entered by the operator (used only
    as a weighting factor for the VC blend - it is never fed to any solver);
    current_rakes stays the pre-diversion reference for the "current" VC
    baseline. Both are rakes in the real-world domain, so they must be
    non-negative whole numbers.
    """
    company: str = Field(..., description="Coal company / source name, e.g. 'BCCL'")
    current_rakes: int = Field(..., ge=0, description="Pre-diversion rakes for this plant-company pair (whole number, reference baseline)")
    current_vc: float = Field(..., gt=0, description="Manually entered current Variable Cost (Rs/unit) for this source")
    rakes: int = Field(..., ge=0, description="Actually diverted / manually entered rakes for this plant-company pair (whole number)")

    @field_validator("current_rakes", "rakes", mode="before")
    @classmethod
    def _validate_whole_rakes(cls, value):
        return _rakes_must_be_whole(value, "Rakes")


class DiversionPlantInput(BaseModel):
    plant: str = Field(..., description="Plant name")
    sources: List[DiversionSourceInput]


class DiversionRequest(BaseModel):
    plants: List[DiversionPlantInput]


class PlantDiversionResult(BaseModel):
    plant: str
    current_rakes: int  # sum of current_rakes across the plant's sources (whole)
    actual_rakes: int   # sum of the entered actual rakes (whole)
    current_vc: Optional[float] = None  # rake-weighted, using CURRENT rakes
    actual_vc: Optional[float] = None   # rake-weighted, using ACTUAL rakes
    delta_vc: Optional[float] = None    # actual_vc - current_vc


class DiversionResponse(BaseModel):
    status: str  # "ok" | "validation_error"
    weighted_vc_current: Optional[float] = None  # overall blended VC of the current mix
    weighted_vc_actual: Optional[float] = None   # overall blended VC of the actual mix
    vc_improvement: Optional[float] = None       # weighted_vc_current - weighted_vc_actual
    total_rakes_current: int = 0
    total_rakes_actual: int = 0
    plants: List[PlantDiversionResult] = []
    errors: List[ValidationErrorEntry] = []
    message: Optional[str] = None
