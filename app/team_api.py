from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import team

router = APIRouter()


class TeamBody(BaseModel):
    kind: str
    request: str


@router.get("/api/team")
def get_team():
    return team.status()


@router.post("/api/team")
def start_team(b: TeamBody):
    r = team.start(b.kind, b.request)
    if not r["ok"]:
        return JSONResponse({"error": r["error"]}, status_code=r["code"])
    return {"ok": True, **team.status()}
