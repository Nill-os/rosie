from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import lifequest

router = APIRouter()


def err(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        return err(str(e))


class StatItem(BaseModel):
    id: str = ""
    name: str
    note: str = ""
    archived: bool = False


class StatsBody(BaseModel):
    stats: list[StatItem]


class QuestBody(BaseModel):
    id: int | None = None
    title: str
    stat: str
    xp: int
    kind: str = "quest"
    archived: bool = False


class DoneBody(BaseModel):
    done: bool = True
    note: str = ""


class EntryItem(BaseModel):
    stat: str
    xp: int


class LogBody(BaseModel):
    note: str
    entries: list[EntryItem]


class RewardBody(BaseModel):
    id: int | None = None
    title: str
    cost: int
    archived: bool = False


class ReviewBody(BaseModel):
    wins: str = ""
    lessons: str = ""
    next: str = ""


@router.get("/api/lifequest")
def lifequest_all():
    return lifequest.state()


@router.post("/api/lifequest/stats")
def lifequest_stats(b: StatsBody):
    return safe(lifequest.save_stats, [s.model_dump() for s in b.stats])


@router.post("/api/lifequest/quests")
def lifequest_quest(b: QuestBody):
    return safe(lifequest.save_quest, b.title, b.stat, b.xp, b.kind, b.id, b.archived)


@router.post("/api/lifequest/quests/{quest_id}/done")
def lifequest_quest_done(quest_id: int, b: DoneBody):
    return safe(lifequest.complete_quest, quest_id, b.done, b.note)


@router.post("/api/lifequest/log")
def lifequest_log(b: LogBody):
    return safe(lifequest.log_win, b.note, [e.model_dump() for e in b.entries])


@router.delete("/api/lifequest/log/{win}")
def lifequest_unlog(win: str):
    return safe(lifequest.remove_win, win)


@router.post("/api/lifequest/rewards")
def lifequest_reward(b: RewardBody):
    return safe(lifequest.save_reward, b.title, b.cost, b.id, b.archived)


@router.post("/api/lifequest/rewards/{reward_id}/claim")
def lifequest_claim(reward_id: int):
    return safe(lifequest.claim_reward, reward_id)


@router.post("/api/lifequest/review")
def lifequest_review(b: ReviewBody):
    return safe(lifequest.save_review, b.wins, b.lessons, b.next)
