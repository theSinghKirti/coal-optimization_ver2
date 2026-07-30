"""
FastAPI backend for the Coal Rake Diversion Optimizer.

Run with:
    uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import OptimizeRequest, OptimizeResponse
from optimizer import optimize

app = FastAPI(
    title="Coal Rake Diversion Optimizer",
    description="Minimizes weighted Variable Cost across UPRVUNL plants using OR-Tools.",
    version="1.0.0",
)

# Allow the existing static HTML dashboard (opened via file:// or any local
# dev server / Netlify preview) to call this API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize", response_model=OptimizeResponse)
def optimize_allocation(request: OptimizeRequest) -> OptimizeResponse:
    """
    Accepts the current plant/source planning data and returns the
    OR-Tools-solved optimal rake allocation that minimizes weighted VC.
    """
    return optimize(request)
