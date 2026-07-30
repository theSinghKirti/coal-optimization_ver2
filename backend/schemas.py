"""
Pydantic schemas for the Coal Rake Diversion Optimizer API.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class SourceInput(BaseModel):
    """A single Plant x Coal Company allocation as currently planned.

    current_vc is a manually-entered, monthly-changing input (not derived
    from any formula or Excel sheet) - it is the cost coefficient the
    optimizer minimizes against, and stays fixed across optimization; only
    the rake allocation changes.
    """
    company: str = Field(..., description="Coal company / source name, e.g. 'BCCL'")
    current_rakes: float = Field(..., ge=0, description="Current rakes for this plant-company pair (may be 0)")
    current_vc: float = Field(..., gt=0, description="Manually entered current Variable Cost (Rs/unit) for this source")
    minRakes: float = Field(..., ge=0, description="Minimum allowed rakes for this plant-company pair")
    maxRakes: float = Field(..., ge=0, description="Maximum allowed rakes for this plant-company pair")


class PlantInput(BaseModel):
    plant: str = Field(..., description="Plant name")
    sources: List[SourceInput]


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
    current_rakes: float
    optimized_rakes: float
    source_vc: float
    delta_rakes: float  # optimized - current; negative = diverted away, positive = received
    minRakes: float
    maxRakes: float


class PlantVCResult(BaseModel):
    plant: str
    current_rakes: float
    optimized_rakes: float
    current_vc: Optional[float] = None    # rake-weighted, using CURRENT rakes
    optimized_vc: Optional[float] = None  # rake-weighted, using OPTIMIZED rakes
    delta_vc: Optional[float] = None      # optimized_vc - current_vc


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
