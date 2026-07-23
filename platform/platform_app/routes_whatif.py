"""What-if portfolio simulation: replay the tenant's real dated cash flows through
an alternative strategy. Filled in by the what-if feature wave.

Owned by the what-if agent. Registered in main.py as `whatif_router`.
"""
from fastapi import APIRouter

whatif_router = APIRouter(prefix="/whatif", tags=["whatif"])
