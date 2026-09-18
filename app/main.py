# app/main.py
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.models.schemas import (
    ScenarioRequest,
    ScenarioResponse,
    HealthResponse,
)
from app.llm.interpreter import LLMInterpreter
from app.guardrails.validator import GuardrailValidator
from app.optimizer.solver import EnergyOptimizer

# Configure structured logging without leaking secrets
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("gridwise.main")

# Global interpreter instance
interpreter: LLMInterpreter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global interpreter
    logger.info("Initializing GridWise Smart Campus Energy Service...")
    interpreter = LLMInterpreter()
    logger.info("GridWise Service is ready to serve requests.")
    yield
    logger.info("GridWise Service shutting down.")


app = FastAPI(
    title="BUP CSE FEST 2026 - GridWise LLM Smart Campus Energy Optimization Service",
    description="LLM-Assisted Operator Directive Interpretation & 24-Hour Energy Scheduling",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for external judge harness
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return controlled HTTP 400 for malformed or structurally invalid requests."""
    logger.warning(f"Malformed request payload received on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Malformed or structurally invalid request.",
            "details": [
                {
                    "loc": list(err.get("loc", [])),
                    "msg": err.get("msg", "Invalid input"),
                    "type": err.get("type", "value_error"),
                }
                for err in exc.errors()
            ],
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Controlled HTTP 500 handler.
    Strictly protects secrets, credentials, and internal stack traces from leaking to API clients.
    """
    logger.error(f"Controlled internal error during request {request.url.path}: {str(exc)}", exc_info=False)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "An internal processing error occurred while optimizing the energy schedule.",
            "status": "internal_error"
        },
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health and readiness probe",
    tags=["System"],
)
async def health():
    """Returns readiness status for judging harness."""
    return HealthResponse(status="ok")


@app.post(
    "/optimize-energy",
    response_model=ScenarioResponse,
    status_code=status.HTTP_200_OK,
    summary="Optimize 24-hour campus energy schedule with operator directives",
    tags=["Optimization"],
)
async def optimize_energy(payload: ScenarioRequest):
    """
    Complete 4-stage pipeline:
    1. Interpret operator notes via LLM / generative parser.
    2. Enforce deterministic guardrails and time/parameter validation.
    3. Solve 24-hour mathematical optimization model (HiGHS MILP).
    4. Deterministically replay schedule and return verified response.
    """
    global interpreter
    if interpreter is None:
        interpreter = LLMInterpreter()

    # Stage 1: Interpret operator notes
    raw_interpretations = interpreter.interpret(payload.operator_notes)

    # Stage 2: Deterministic Guardrail Validation
    validated_directives = GuardrailValidator.sanitize_and_validate(
        raw_interpretations=raw_interpretations,
        operator_notes=payload.operator_notes,
        battery=payload.battery,
    )

    # Stage 3: Mathematical Optimization
    (
        hourly_plan,
        total_grid_kwh,
        total_cost_bdt,
        peak_grid_kwh,
        plan_summary,
    ) = EnergyOptimizer.optimize(
        hours=payload.hours,
        battery=payload.battery,
        directives=validated_directives,
    )

    # Stage 4: Construct verified canonical response
    return ScenarioResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=validated_directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=plan_summary,
    )


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "GridWise LLM Service", "health": "/health", "docs": "/docs"}
