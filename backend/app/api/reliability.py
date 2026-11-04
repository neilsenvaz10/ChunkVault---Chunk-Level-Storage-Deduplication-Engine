"""Isolated fault-injection experiments (disabled unless CHUNKVAULT_ENABLE_FAULT_LAB=1).

There is deliberately no endpoint that accepts a fault description: a request can only select one
of the fixed, built-in scenarios by id, and each runs against its own temporary store.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_settings
from app.core.config import Settings
from app.reliability.faultlab import list_scenarios, run_scenario
from app.schemas.common import ErrorResponse
from app.schemas.reliability import ExperimentRequest, ScenarioListResponse

router = APIRouter(prefix="/reliability", tags=["reliability"])


@router.get("/scenarios", response_model=ScenarioListResponse, summary="Built-in fault scenarios and whether the lab is enabled")
def scenarios(settings: Settings = Depends(get_settings)):
    return ScenarioListResponse(
        enabled=settings.fault_lab_enabled, scenarios=list_scenarios(),
        safety=[
            "Every experiment runs against its own temporary store and removes it afterwards.",
            "The application's database and chunk store are never opened by an experiment.",
            "Experiments only run when explicitly started, and only when the lab is enabled by the operator.",
            "Only the fixed built-in scenarios can be selected; no custom fault can be submitted.",
        ])


@router.post("/experiments", response_model=dict[str, Any],
             responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
             summary="Run one built-in fault scenario against an isolated temporary store")
def run_experiment(body: ExperimentRequest, settings: Settings = Depends(get_settings)):
    return run_scenario(body.scenario_id, settings)
