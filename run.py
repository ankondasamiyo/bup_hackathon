# run.py
"""Entry point for starting the GridWise LLM Optimization API Server."""
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting GridWise LLM Optimization Service on http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
