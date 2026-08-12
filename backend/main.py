from fastapi import FastAPI

app = FastAPI(
    title="Jailbreak Oracle API",
    description="Autonomous Multi-Agent LLM Red-Teaming & Adaptive Defense Platform",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    """
    Deterministic health check to verify API routing and availability.
    """
    return {"status": "ok", "service": "jailbreak-oracle-backend"}
