"""FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from reportback import __version__

ATTRIBUTION = "Contains information licensed under the Open Government Licence – Toronto."


class Health(BaseModel):
    status: str
    version: str
    attribution: str


app = FastAPI(title="ReportBack TO", version=__version__)


@app.get("/healthz")
def healthz() -> Health:
    return Health(status="ok", version=__version__, attribution=ATTRIBUTION)
