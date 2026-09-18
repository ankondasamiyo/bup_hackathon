# app/guardrails/validator.py
import math
from typing import List, Dict, Any, Optional
from app.models.schemas import DirectiveInterpretation, BatteryConfig


ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


class GuardrailValidator:
    """
    Deterministic Guardrail Engine.
    Ensures LLM output strictly adheres to Section 04 & 08 of the canonical problem statement.
    Guarantees:
      - Valid directive types
      - Exactly 1 entry per operator note in sequential order 0..N-1
      - Correct applies semantics (applies=false only for no_op)
      - Hours sorted ascending, unique, strictly within [0..23]
      - Solar factor within [0.0, 1.0]
      - Battery reserve within [0.0, capacity_kwh]
      - Max grid kwh >= 0.0
      - Structured adjustments conform to exact required shapes
    """

    @classmethod
    def sanitize_and_validate(
        cls,
        raw_interpretations: List[Dict[str, Any]],
        operator_notes: List[str],
        battery: BatteryConfig,
    ) -> List[DirectiveInterpretation]:
        num_notes = len(operator_notes)
        validated_list: List[DirectiveInterpretation] = []

        # Index map from note_index to raw interpretation dict
        raw_by_index: Dict[int, Dict[str, Any]] = {}
        for item in raw_interpretations:
            if isinstance(item, dict) and "note_index" in item:
                try:
                    idx = int(item["note_index"])
                    if 0 <= idx < num_notes and idx not in raw_by_index:
                        raw_by_index[idx] = item
                except (ValueError, TypeError):
                    continue

        for i in range(num_notes):
            raw = raw_by_index.get(i)
            if not raw:
                # Fallback to no_op if LLM missed an index
                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=f"Note {i} defaulted to no_op due to missing interpretation.",
                    )
                )
                continue

            directive_type = str(raw.get("directive_type", "no_op")).strip().lower()
            if directive_type not in ALLOWED_DIRECTIVE_TYPES:
                directive_type = "no_op"

            explanation = str(raw.get("explanation", "")).strip()
            if not explanation:
                explanation = f"Interpretation for note {i}."

            if directive_type == "no_op":
                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=explanation,
                    )
                )
                continue

            # Non-no_op directives
            raw_adj = raw.get("structured_adjustment")
            if not isinstance(raw_adj, dict):
                # Malformed adjustment: fall back safely to no_op
                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=f"Malformed adjustment for note {i}, treated safely as no_op.",
                    )
                )
                continue

            # Validate and normalize hours
            hours_raw = raw_adj.get("hours", [])
            valid_hours = set()
            if isinstance(hours_raw, list):
                for h in hours_raw:
                    try:
                        h_int = int(h)
                        if 0 <= h_int <= 23:
                            valid_hours.add(h_int)
                    except (ValueError, TypeError):
                        continue

            sorted_hours = sorted(list(valid_hours))
            if not sorted_hours:
                # If directive specifies no valid hours, it cannot apply
                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation="No valid hours specified; safely classified as no_op.",
                    )
                )
                continue

            # Directive specific checks
            if directive_type == "solar_reduction":
                factor = raw_adj.get("factor")
                try:
                    factor_val = float(factor)
                    if math.isnan(factor_val) or math.isinf(factor_val):
                        raise ValueError()
                    factor_val = max(0.0, min(1.0, factor_val))
                    # Round to clean precision
                    factor_val = round(factor_val, 4)
                except (ValueError, TypeError):
                    factor_val = 1.0  # safe default

                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type="solar_reduction",
                        structured_adjustment={
                            "hours": sorted_hours,
                            "factor": factor_val,
                        },
                        explanation=explanation,
                    )
                )

            elif directive_type == "minimum_battery_reserve":
                reserve = raw_adj.get("minimum_energy_kwh")
                try:
                    reserve_val = float(reserve)
                    if math.isnan(reserve_val) or math.isinf(reserve_val):
                        raise ValueError()
                    reserve_val = max(0.0, min(battery.capacity_kwh, reserve_val))
                    reserve_val = round(reserve_val, 2)
                except (ValueError, TypeError):
                    reserve_val = battery.minimum_energy_kwh

                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type="minimum_battery_reserve",
                        structured_adjustment={
                            "hours": sorted_hours,
                            "minimum_energy_kwh": reserve_val,
                        },
                        explanation=explanation,
                    )
                )

            elif directive_type == "max_grid_window":
                max_grid = raw_adj.get("max_grid_kwh")
                try:
                    max_grid_val = float(max_grid)
                    if math.isnan(max_grid_val) or math.isinf(max_grid_val):
                        raise ValueError()
                    max_grid_val = max(0.0, max_grid_val)
                    max_grid_val = round(max_grid_val, 2)
                except (ValueError, TypeError):
                    max_grid_val = 0.0

                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type="max_grid_window",
                        structured_adjustment={
                            "hours": sorted_hours,
                            "max_grid_kwh": max_grid_val,
                        },
                        explanation=explanation,
                    )
                )

            elif directive_type in {"no_charge_window", "no_discharge_window"}:
                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type=directive_type,
                        structured_adjustment={"hours": sorted_hours},
                        explanation=explanation,
                    )
                )

            else:
                validated_list.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=explanation,
                    )
                )

        return validated_list
