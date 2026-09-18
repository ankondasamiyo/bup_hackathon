# tests/test_api.py
import json
import time
import unittest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import ScenarioRequest, BatteryConfig, HourEntry
from app.llm.interpreter import LLMInterpreter
from app.guardrails.validator import GuardrailValidator
from app.optimizer.solver import EnergyOptimizer


class TestGridWiseService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        with open("sample_cases/sample_request.json") as f:
            cls.sample_payload = json.load(f)

    def test_01_health_endpoint(self):
        """GET /health must return HTTP 200 with status: ok."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, {"status": "ok"})

    def test_02_sample_case_full_pipeline(self):
        """POST /optimize-energy processes the canonical sample case correctly."""
        response = self.client.post("/optimize-energy", json=self.sample_payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Check top-level contract
        self.assertEqual(data["scenario_id"], "GRID-101")
        self.assertIn("directive_interpretation", data)
        self.assertIn("hourly_plan", data)
        self.assertIn("total_grid_kwh", data)
        self.assertIn("total_cost_bdt", data)
        self.assertIn("peak_grid_kwh", data)
        self.assertIn("plan_summary", data)

        # Check directive interpretations
        directives = data["directive_interpretation"]
        self.assertEqual(len(directives), 3)

        # Note 0: solar reduction from 1 PM to 3 PM
        self.assertEqual(directives[0]["note_index"], 0)
        self.assertTrue(directives[0]["applies"])
        self.assertEqual(directives[0]["directive_type"], "solar_reduction")
        self.assertEqual(directives[0]["structured_adjustment"]["hours"], [13, 14])
        self.assertAlmostEqual(directives[0]["structured_adjustment"]["factor"], 0.2, places=2)

        # Note 1: no charge between 2 PM and 4 PM
        self.assertEqual(directives[1]["note_index"], 1)
        self.assertTrue(directives[1]["applies"])
        self.assertEqual(directives[1]["directive_type"], "no_charge_window")
        self.assertEqual(directives[1]["structured_adjustment"]["hours"], [14, 15])

        # Note 2: distractor (cafeteria)
        self.assertEqual(directives[2]["note_index"], 2)
        self.assertFalse(directives[2]["applies"])
        self.assertEqual(directives[2]["directive_type"], "no_op")
        self.assertIsNone(directives[2]["structured_adjustment"])

        # Check hourly plan constraints
        hourly_plan = data["hourly_plan"]
        self.assertEqual(len(hourly_plan), 24)

        # Replay and check energy balance + battery neutrality
        initial_battery = self.sample_payload["battery"]["initial_energy_kwh"]
        capacity = self.sample_payload["battery"]["capacity_kwh"]
        min_reserve = self.sample_payload["battery"]["minimum_energy_kwh"]

        recalculated_grid = 0.0
        recalculated_cost = 0.0
        peak_grid = 0.0

        for idx, entry in enumerate(hourly_plan):
            self.assertEqual(entry["hour"], idx)
            h_req = self.sample_payload["hours"][idx]

            # Recomputed effective solar
            factor = 0.2 if idx in [13, 14] else 1.0
            eff_solar = h_req["solar_kwh"] * factor

            self.assertLessEqual(entry["solar_used_kwh"], eff_solar + 1e-4)
            self.assertGreaterEqual(entry["solar_used_kwh"], 0.0)

            # Check charge restriction on hours 14 and 15
            if idx in [14, 15]:
                self.assertNotEqual(entry["battery_action"], "charge")

            # Check energy balance
            chg = entry["battery_kwh"] if entry["battery_action"] == "charge" else 0.0
            dis = entry["battery_kwh"] if entry["battery_action"] == "discharge" else 0.0
            energy_lhs = entry["grid_kwh"] + entry["solar_used_kwh"] + dis
            energy_rhs = h_req["demand_kwh"] + chg
            self.assertAlmostEqual(energy_lhs, energy_rhs, places=2)

            # Battery bounds
            self.assertGreaterEqual(entry["battery_energy_after_kwh"], min_reserve - 1e-4)
            self.assertLessEqual(entry["battery_energy_after_kwh"], capacity + 1e-4)

            recalculated_grid += entry["grid_kwh"]
            recalculated_cost += entry["grid_kwh"] * h_req["tariff_bdt_per_kwh"]
            peak_grid = max(peak_grid, entry["grid_kwh"])

        # End-of-day battery neutrality
        self.assertAlmostEqual(hourly_plan[23]["battery_energy_after_kwh"], initial_battery, places=2)

        # Recalculated values match
        self.assertAlmostEqual(data["total_grid_kwh"], recalculated_grid, places=1)
        self.assertAlmostEqual(data["total_cost_bdt"], recalculated_cost, places=1)
        self.assertAlmostEqual(data["peak_grid_kwh"], peak_grid, places=1)

    def test_03_paraphrasing_variations(self):
        """Hidden language variation test cases from Section 11.4."""
        paraphrases = [
            "PV production will drop to about 20% between 13:00 and 15:00.",
            "Panel washing from one until three will leave roughly one-fifth of normal solar output.",
            "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window."
        ]
        interp = LLMInterpreter()
        battery = BatteryConfig(
            capacity_kwh=500, initial_energy_kwh=200, minimum_energy_kwh=50,
            max_charge_kwh_per_hour=100, max_discharge_kwh_per_hour=100
        )
        for note in paraphrases:
            raw = interp.interpret([note])
            validated = GuardrailValidator.sanitize_and_validate(raw, [note], battery)
            self.assertEqual(len(validated), 1)
            v = validated[0]
            self.assertTrue(v.applies)
            self.assertEqual(v.directive_type, "solar_reduction")
            self.assertEqual(v.structured_adjustment["hours"], [13, 14])
            self.assertAlmostEqual(v.structured_adjustment["factor"], 0.2, places=2)

    def test_04_all_directive_types(self):
        """Verifies interpretation and execution of all 5 active directives + no_op."""
        notes = [
            "Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
            "Avoid discharging the battery between 8 AM and 11 AM.",
            "Grid import may not exceed 250 kWh from 5 PM to 8 PM."
        ]
        payload = dict(self.sample_payload)
        payload["operator_notes"] = notes

        response = self.client.post("/optimize-energy", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        directives = data["directive_interpretation"]

        # Directive 0: minimum_battery_reserve
        self.assertTrue(directives[0]["applies"])
        self.assertEqual(directives[0]["directive_type"], "minimum_battery_reserve")
        self.assertEqual(directives[0]["structured_adjustment"]["hours"], [18, 19, 20])
        self.assertEqual(directives[0]["structured_adjustment"]["minimum_energy_kwh"], 120.0)

        # Directive 1: no_discharge_window
        self.assertTrue(directives[1]["applies"])
        self.assertEqual(directives[1]["directive_type"], "no_discharge_window")
        self.assertEqual(directives[1]["structured_adjustment"]["hours"], [8, 9, 10])

        # Directive 2: max_grid_window
        self.assertTrue(directives[2]["applies"])
        self.assertEqual(directives[2]["directive_type"], "max_grid_window")
        self.assertEqual(directives[2]["structured_adjustment"]["hours"], [17, 18, 19])
        self.assertEqual(directives[2]["structured_adjustment"]["max_grid_kwh"], 250.0)

        # Verify enforcement in hourly plan
        plan = data["hourly_plan"]
        # Hours 18, 19, 20: battery >= 120
        for h in [18, 19, 20]:
            self.assertGreaterEqual(plan[h]["battery_energy_after_kwh"], 120.0 - 1e-4)

        # Hours 8, 9, 10: no discharge
        for h in [8, 9, 10]:
            self.assertNotEqual(plan[h]["battery_action"], "discharge")

        # Hours 17, 18, 19: grid <= 250
        for h in [17, 18, 19]:
            self.assertLessEqual(plan[h]["grid_kwh"], 250.0 + 1e-4)

    def test_05_malformed_request_returns_400(self):
        """Malformed JSON or missing fields must return HTTP 400."""
        # Missing scenario_id
        bad_payload = dict(self.sample_payload)
        del bad_payload["scenario_id"]
        response = self.client.post("/optimize-energy", json=bad_payload)
        self.assertEqual(response.status_code, 400)

        # Invalid hours length (23 instead of 24)
        bad_payload = dict(self.sample_payload)
        bad_payload["hours"] = bad_payload["hours"][:23]
        response = self.client.post("/optimize-energy", json=bad_payload)
        self.assertEqual(response.status_code, 400)

    def test_06_performance_latency(self):
        """Service latency must be well under 5 seconds for p95 requirements."""
        t0 = time.time()
        response = self.client.post("/optimize-energy", json=self.sample_payload)
        elapsed = time.time() - t0
        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 2.0, f"Expected < 2.0s latency, got {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
