# 3-Minute Architecture & Solution Video Script
**Project**: GridWise LLM — Smart Campus Energy Optimization Service
**Target Duration**: Exactly 2 minutes 45 seconds to 3 minutes

---

### [0:00 - 0:30] Introduction & Problem Understanding
- **Visual**: Title slide with project name "GridWise LLM", team name, and architecture diagram overview.
- **Voiceover**:
  > *"Hello respected judges and organizers of BUP CSE Fest 2026. Today, we present **GridWise LLM**, our end-to-end intelligent energy scheduling system designed for smart campus microgrids.*
  >
  > *In modern campus operations, energy managers deal with dynamic campus electricity demand, fluctuating solar generation, and time-of-use tariffs. On top of that, operators frequently post real-time natural language notes—such as rooftop maintenance, battery reserve requirements, or peak shaving instructions.*
  >
  > *Our challenge was to bridge the gap between messy human language and rigorous mathematical optimization, returning a guaranteed valid, lowest-cost 24-hour dispatch schedule."*

---

### [0:30 - 1:15] End-to-End Pipeline Architecture
- **Visual**: Zoom in on the 4-stage processing pipeline diagram.
- **Voiceover**:
  > *"To ensure total mathematical correctness and eliminate hallucinations, we structured GridWise LLM as a strictly decoupled 4-stage pipeline:*
  >
  > *1. **Dual-Engine LLM Interpreter**: We utilize Google Gemini and OpenAI models via structured JSON output to parse operator notes into standard directive types: solar reduction, battery reserve limits, charge/discharge windows, and grid caps. We also built a zero-latency deterministic semantic parser fallback to ensure the service never goes down, even under API quota exhaustion or network dropouts.*
  >
  > *2. **Deterministic Guardrail Engine**: Before any interpretation reaches the solver, our validator enforces canonical constraints: unique ascending hours from 0 to 23, factor bounds between 0 and 1, strict applies boolean semantics, and zero invented parameters.*
  >
  > *3. **HiGHS Mathematical Optimizer**: Using SciPy's HiGHS MILP solver, we formulate the 24-hour dispatch problem with exact hourly energy balance, battery capacity limits, rate limits, and end-of-day battery neutrality ($E_{23} = E_{\text{init}}$).*
  >
  > *4. **Independent Physical Replay**: Finally, the system simulates the resulting schedule hour-by-hour to classify battery actions as charge, discharge, or idle, verifying zero unserved demand and recalculating exact costs."*

---

### [1:15 - 2:00] Paraphrasing & Guardrail Robustness
- **Visual**: Show side-by-side terminal or code of how paraphrased sentences are parsed into identical directives.
- **Voiceover**:
  > *"A critical challenge was handling unseen paraphrasing in hidden test cases. Notice how whether the operator writes:*
  > - *'PV production will drop to about 20% between 13:00 and 15:00',*
  > - *'Panel washing from one until three will leave roughly one-fifth of normal solar output', or*
  > - *'Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window',*
  >
  > *Our interpreter normalizes all 12-hour and 24-hour time expressions to the start-inclusive, end-exclusive window of hours `[13, 14]`, and correctly resolves the remaining usable solar factor to `0.2`.*
  >
  > *Irrelevant distractor notes—such as cafeteria menus—are cleanly flagged as `no_op` with `applies = false`."*

---

### [2:00 - 2:40] Live Demonstration & Verification
- **Visual**: Screen recording running `python -m unittest tests/test_api.py` and making a `curl` request to `http://localhost:8000/optimize-energy`.
- **Voiceover**:
  > *"Let's see the system in action.*
  >
  > *Running our automated test suite, all 6 comprehensive test cases pass in under 0.5 seconds—well below the 5-second p95 latency threshold.*
  >
  > *When we execute `POST /optimize-energy` with the public scenario, the response immediately returns: matching the `GRID-101` scenario ID, exact directive interpretations, and a full 24-hour hourly dispatch plan.*
  >
  > *The battery charges during low-cost morning hours and discharges during peak evening tariff hours, while fully respecting the no-charge window at hours 14 and 15 and returning to exactly 200 kWh at hour 23."*

---

### [2:40 - 3:00] Deployment & Conclusion
- **Visual**: Docker compose terminal, repository structure, and closing slide.
- **Voiceover**:
  > *"For deployment, we provide a complete Dockerfile and `docker-compose.yml` that binds to port 8000 with a verified healthcheck probe at `/health`.*
  >
  > *GridWise LLM combines the power of generative AI for language understanding with the unbreakable reliability of deterministic guardrails and mathematical optimization.*
  >
  > *Thank you!"*
