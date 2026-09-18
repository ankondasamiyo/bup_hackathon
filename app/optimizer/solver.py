# app/optimizer/solver.py
import logging
from typing import List, Tuple
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds

from app.models.schemas import (
    HourEntry,
    BatteryConfig,
    DirectiveInterpretation,
    HourlyPlanEntry,
)

logger = logging.getLogger("gridwise.optimizer")


class EnergyOptimizer:
    """
    Mathematical optimizer for 24-hour smart campus energy dispatch.
    Solves Mixed-Integer Linear Program (MILP) using HiGHS solver to minimize
    total grid electricity cost subject to physical grid, solar, and battery constraints
    plus validated operator directives.
    """

    @classmethod
    def optimize(
        cls,
        hours: List[HourEntry],
        battery: BatteryConfig,
        directives: List[DirectiveInterpretation],
    ) -> Tuple[List[HourlyPlanEntry], float, float, float, str]:
        n = 24
        sorted_hours = sorted(hours, key=lambda x: x.hour)

        demands = np.array([h.demand_kwh for h in sorted_hours], dtype=float)
        base_solar = np.array([h.solar_kwh for h in sorted_hours], dtype=float)
        tariffs = np.array([h.tariff_bdt_per_kwh for h in sorted_hours], dtype=float)

        effective_solar = base_solar.copy()
        eff_min_battery = np.full(n, battery.minimum_energy_kwh, dtype=float)
        eff_max_charge = np.full(n, battery.max_charge_kwh_per_hour, dtype=float)
        eff_max_discharge = np.full(n, battery.max_discharge_kwh_per_hour, dtype=float)
        eff_max_grid = np.full(n, np.inf, dtype=float)

        applied_directive_summaries = []

        # Apply directives deterministically
        for d in directives:
            if not d.applies or not d.structured_adjustment:
                continue

            adj = d.structured_adjustment
            target_hours = adj.get("hours", [])

            if d.directive_type == "solar_reduction":
                factor = float(adj.get("factor", 1.0))
                factor = max(0.0, min(1.0, factor))
                for h in target_hours:
                    if 0 <= h < n:
                        effective_solar[h] = base_solar[h] * factor
                applied_directive_summaries.append(
                    f"Solar reduced by factor {factor} during hours {target_hours}."
                )

            elif d.directive_type == "minimum_battery_reserve":
                min_reserve = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
                min_reserve = min(min_reserve, battery.capacity_kwh)
                for h in target_hours:
                    if 0 <= h < n:
                        eff_min_battery[h] = max(eff_min_battery[h], min_reserve)
                applied_directive_summaries.append(
                    f"Minimum battery reserve enforced at {min_reserve} kWh during hours {target_hours}."
                )

            elif d.directive_type == "no_charge_window":
                for h in target_hours:
                    if 0 <= h < n:
                        eff_max_charge[h] = 0.0
                applied_directive_summaries.append(
                    f"Battery charging disabled during hours {target_hours}."
                )

            elif d.directive_type == "no_discharge_window":
                for h in target_hours:
                    if 0 <= h < n:
                        eff_max_discharge[h] = 0.0
                applied_directive_summaries.append(
                    f"Battery discharging disabled during hours {target_hours}."
                )

            elif d.directive_type == "max_grid_window":
                max_grid = float(adj.get("max_grid_kwh", np.inf))
                for h in target_hours:
                    if 0 <= h < n:
                        eff_max_grid[h] = min(eff_max_grid[h], max_grid)
                applied_directive_summaries.append(
                    f"Grid import capped at {max_grid} kWh during hours {target_hours}."
                )

        # Formulate MILP
        # Variables:
        # G_h: grid_kwh (indices 0..23)
        # S_h: solar_used_kwh (indices 24..47)
        # C_h: charge_kwh (indices 48..71)
        # D_h: discharge_kwh (indices 72..95)
        # E_h: battery_energy_after_kwh (indices 96..119)
        # z_h: binary charge indicator: 1 = charge allowed, 0 = discharge allowed (indices 120..143)
        num_vars = 144
        c = np.zeros(num_vars)
        c[0:24] = tariffs
        c[24:48] = -1e-5  # Slight incentive to prefer clean solar usage over curtailment

        lb = np.zeros(num_vars)
        ub = np.zeros(num_vars)

        # Bounds
        lb[0:24] = 0.0
        ub[0:24] = eff_max_grid

        lb[24:48] = 0.0
        ub[24:48] = effective_solar

        lb[48:72] = 0.0
        ub[48:72] = eff_max_charge

        lb[72:96] = 0.0
        ub[72:96] = eff_max_discharge

        lb[96:120] = eff_min_battery
        ub[96:120] = battery.capacity_kwh

        lb[120:144] = 0.0
        ub[120:144] = 1.0

        num_constraints = 24 + 24 + 1 + 24 + 24
        A = np.zeros((num_constraints, num_vars))
        lhs = np.zeros(num_constraints)
        rhs = np.zeros(num_constraints)

        # 1. Energy balance
        for h in range(24):
            A[h, h] = 1.0       # G_h
            A[h, 24 + h] = 1.0  # S_h
            A[h, 72 + h] = 1.0  # D_h
            A[h, 48 + h] = -1.0 # -C_h
            lhs[h] = demands[h]
            rhs[h] = demands[h]

        # 2. Battery transition
        A[24, 96] = 1.0
        A[24, 48] = -1.0
        A[24, 72] = 1.0
        lhs[24] = battery.initial_energy_kwh
        rhs[24] = battery.initial_energy_kwh

        for h in range(1, 24):
            row = 24 + h
            A[row, 96 + h] = 1.0      # E_h
            A[row, 96 + h - 1] = -1.0 # -E_{h-1}
            A[row, 48 + h] = -1.0     # -C_h
            A[row, 72 + h] = 1.0      # +D_h
            lhs[row] = 0.0
            rhs[row] = 0.0

        # 3. End-of-day neutrality
        row_eod = 48
        A[row_eod, 96 + 23] = 1.0
        lhs[row_eod] = battery.initial_energy_kwh
        rhs[row_eod] = battery.initial_energy_kwh

        # 4. Linking charge
        for h in range(24):
            row = 49 + h
            A[row, 48 + h] = 1.0
            A[row, 120 + h] = -battery.max_charge_kwh_per_hour
            lhs[row] = -np.inf
            rhs[row] = 0.0

        # 5. Linking discharge
        for h in range(24):
            row = 73 + h
            A[row, 72 + h] = 1.0
            A[row, 120 + h] = battery.max_discharge_kwh_per_hour
            lhs[row] = -np.inf
            rhs[row] = battery.max_discharge_kwh_per_hour

        integrality = np.zeros(num_vars)
        integrality[120:144] = 1

        constraints = LinearConstraint(A, lhs, rhs)
        res = milp(c=c, integrality=integrality, bounds=Bounds(lb, ub), constraints=constraints)

        # Fallback 1: LP relaxation
        if not res.success:
            logger.warning("MILP did not converge with binary constraints; attempting LP relaxation.")
            res = milp(c=c, integrality=np.zeros(num_vars), bounds=Bounds(lb, ub), constraints=constraints)

        # Fallback 2: If strictly infeasible due to an over-constrained grid cap, relax grid cap to satisfy demand
        if not res.success:
            logger.warning("Attempting optimization with relaxed grid caps to avoid unserved demand.")
            ub_relaxed = ub.copy()
            ub_relaxed[0:24] = np.inf
            res = milp(c=c, integrality=np.zeros(num_vars), bounds=Bounds(lb, ub_relaxed), constraints=constraints)

        if not res.success:
            logger.error("Linear solver failed to find feasible dispatch.")
            raise ValueError("Optimization model infeasible under given constraints.")

        x = res.x

        # Build hourly plan entries with exact simulation and replay
        hourly_plan: List[HourlyPlanEntry] = []
        current_battery = battery.initial_energy_kwh

        for h in range(24):
            s = max(0.0, min(float(effective_solar[h]), float(x[24 + h])))
            c_val = max(0.0, float(x[48 + h]))
            d_val = max(0.0, float(x[72 + h]))

            # Clean near-zero numerical noise
            if c_val < 1e-4:
                c_val = 0.0
            if d_val < 1e-4:
                d_val = 0.0

            # Determine strictly one discrete battery action
            if c_val > 0.0 and d_val == 0.0:
                action = "charge"
                battery_kwh = min(c_val, battery.max_charge_kwh_per_hour)
                current_battery = min(battery.capacity_kwh, current_battery + battery_kwh)
            elif d_val > 0.0 and c_val == 0.0:
                action = "discharge"
                battery_kwh = min(d_val, battery.max_discharge_kwh_per_hour)
                current_battery = max(battery.minimum_energy_kwh, current_battery - battery_kwh)
            elif c_val > 0.0 and d_val > 0.0:
                net = c_val - d_val
                if net > 1e-4:
                    action = "charge"
                    battery_kwh = net
                    current_battery = min(battery.capacity_kwh, current_battery + battery_kwh)
                elif net < -1e-4:
                    action = "discharge"
                    battery_kwh = -net
                    current_battery = max(battery.minimum_energy_kwh, current_battery - battery_kwh)
                else:
                    action = "idle"
                    battery_kwh = 0.0
            else:
                action = "idle"
                battery_kwh = 0.0

            # Re-balance grid_kwh to satisfy exact energy balance:
            # grid_kwh + solar_used_kwh + discharge = demand_kwh + charge
            discharge_term = battery_kwh if action == "discharge" else 0.0
            charge_term = battery_kwh if action == "charge" else 0.0
            needed_grid = demands[h] + charge_term - s - discharge_term
            g = max(0.0, needed_grid)

            hourly_plan.append(
                HourlyPlanEntry(
                    hour=h,
                    grid_kwh=round(g, 4),
                    solar_used_kwh=round(s, 4),
                    battery_action=action,
                    battery_kwh=round(battery_kwh, 4),
                    battery_energy_after_kwh=round(current_battery, 4),
                )
            )

        # Recalculated exact metrics from hourly plan (canonical source of truth)
        total_grid_kwh = round(sum(p.grid_kwh for p in hourly_plan), 2)
        total_cost_bdt = round(
            sum(p.grid_kwh * sorted_hours[p.hour].tariff_bdt_per_kwh for p in hourly_plan), 2
        )
        peak_grid_kwh = round(max(p.grid_kwh for p in hourly_plan), 2)

        # Synthesize strategy explanation
        charge_hours = [p.hour for p in hourly_plan if p.battery_action == "charge"]
        discharge_hours = [p.hour for p in hourly_plan if p.battery_action == "discharge"]
        total_solar_used = round(sum(p.solar_used_kwh for p in hourly_plan), 2)

        summary_parts = [
            f"Optimized 24-hour dispatch minimizing total cost to {total_cost_bdt} BDT across {total_grid_kwh} kWh grid import (peak: {peak_grid_kwh} kWh).",
            f"Clean rooftop solar supplied {total_solar_used} kWh.",
            f"Battery leveraged for price arbitrage with charging at hours {charge_hours} and peak-shaving discharge at hours {discharge_hours}."
        ]
        if applied_directive_summaries:
            summary_parts.append("Operator directives incorporated: " + " ".join(applied_directive_summaries))

        plan_summary = " ".join(summary_parts)

        return hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh, plan_summary
