# app/models/schemas.py
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class HourEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day from 0 to 23")
    demand_kwh: float = Field(..., ge=0.0, description="Campus demand in kWh")
    solar_kwh: float = Field(..., ge=0.0, description="Base solar generation available in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0.0, description="Grid tariff price in BDT/kWh")


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(..., gt=0.0, description="Maximum energy battery can store in kWh")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Battery energy at start of hour 0 in kWh")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Base reserve level battery cannot drop below")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Max energy added in one hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Max energy removed in one hour")

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be less than minimum_energy_kwh")
        return self


class ScenarioRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique synthetic scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 operator notes")
    hours: List[HourEntry] = Field(..., min_length=24, max_length=24, description="Exactly 24 hourly entries")
    battery: BatteryConfig = Field(..., description="Battery configuration parameters")

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, v: List[str]) -> List[str]:
        if not (1 <= len(v) <= 3):
            raise ValueError("operator_notes must contain between 1 and 3 entries")
        for idx, note in enumerate(v):
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"operator_note at index {idx} must be a non-empty string")
        return v

    @field_validator("hours")
    @classmethod
    def validate_24_hours(cls, v: List[HourEntry]) -> List[HourEntry]:
        if len(v) != 24:
            raise ValueError(f"hours array must contain exactly 24 entries, found {len(v)}")
        hours_seen = set()
        for entry in v:
            if entry.hour in hours_seen:
                raise ValueError(f"Duplicate hour {entry.hour} in hours array")
            hours_seen.add(entry.hour)
        if hours_seen != set(range(24)):
            raise ValueError("hours array must contain every hour from 0 through 23")
        return sorted(v, key=lambda x: x.hour)


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0, description="Zero-based index of corresponding operator note")
    applies: bool = Field(..., description="True for active directives, False only for no_op")
    directive_type: Literal[
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ] = Field(..., description="Supported directive type")
    structured_adjustment: Optional[Dict[str, Any]] = Field(
        None, description="Directive parameters, or null only for no_op"
    )
    explanation: str = Field(..., description="Short explanation of the interpretation")


class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour 0 through 23")
    grid_kwh: float = Field(..., ge=0.0, description="Grid energy purchased in kWh")
    solar_used_kwh: float = Field(..., ge=0.0, description="Solar energy utilized in kWh")
    battery_action: Literal["charge", "discharge", "idle"] = Field(
        ..., description="Battery action for this hour"
    )
    battery_kwh: float = Field(..., ge=0.0, description="Magnitude of battery action, 0 when idle")
    battery_energy_after_kwh: float = Field(
        ..., ge=0.0, description="Battery energy immediately after this hour"
    )


class ScenarioResponse(BaseModel):
    scenario_id: str = Field(..., description="Echoed scenario identifier")
    directive_interpretation: List[DirectiveInterpretation] = Field(
        ..., description="Interpretation for each operator note in index order"
    )
    hourly_plan: List[HourlyPlanEntry] = Field(..., description="24-hour dispatch schedule")
    total_grid_kwh: float = Field(..., ge=0.0, description="Sum of grid energy over 24 hours")
    total_cost_bdt: float = Field(..., ge=0.0, description="Total grid electricity cost in BDT")
    peak_grid_kwh: float = Field(..., ge=0.0, description="Maximum hourly grid energy in kWh")
    plan_summary: str = Field(..., description="Human-readable summary of the optimization strategy")
