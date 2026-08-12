from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Adjust these imports based on where your router, engine, and Base are located
from app.api.routers import campaigns
from app.db.database import engine, Base 
# Note: Make sure you also import your models so Base knows about them!
# Example: from app.db.models import Experiment, Campaign  (if they aren't already imported via the router)

# --- ADD THIS LIFESPAN FUNCTION ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # This runs when the server starts up
    async with engine.begin() as conn:
        # Creates all tables (like 'experiments') if they don't exist yet
        await conn.run_sync(Base.metadata.create_all)
    yield
    # This is where shutdown logic would go (if we had any)
# ----------------------------------

# --- UPDATE FASTAPI INIT TO USE LIFESPAN ---
app = FastAPI(
    title="Oracle AI Security Red Teaming API", 
    lifespan=lifespan
)
# -------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(campaigns.router, prefix="/api/v1") # Ensure prefix matches what the frontend is calling

@app.get("/")
async def root():
    return {"message": "API is running"}