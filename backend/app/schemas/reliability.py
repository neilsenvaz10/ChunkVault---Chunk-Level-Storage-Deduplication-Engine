from __future__ import annotations

from pydantic import BaseModel, Field


class ExpectationInfo(BaseModel):
    key: str
    label: str
    expected: str


class ScenarioInfo(BaseModel):
    id: str
    title: str
    operation: str
    fault_point: str
    kind: str = Field(description="'crash' = process death (engine cleanup does not run); 'exception' = the engine handles the failure")
    description: str
    steps: list[str]
    fault_step: int
    expectations: list[ExpectationInfo]


class ScenarioListResponse(BaseModel):
    enabled: bool
    scenarios: list[ScenarioInfo]
    safety: list[str]


class ExperimentRequest(BaseModel):
    scenario_id: str = Field(pattern=r"^[a-z_]{3,48}$")
