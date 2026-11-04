"""Capacity simulation: a pure mathematical model (no I/O, no storage access)."""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import ErrorResponse
from app.schemas.simulation import SimulationRequest, SimulationResponse
from app.simulation.model import SimulationInput, simulate

router = APIRouter(prefix="/simulations", tags=["simulation"])


@router.post("", response_model=SimulationResponse, responses={422: {"model": ErrorResponse}},
             summary="Evaluate the capacity model for the given assumptions (not a measurement)")
def run_simulation(body: SimulationRequest):
    return simulate(SimulationInput(**body.model_dump()))
