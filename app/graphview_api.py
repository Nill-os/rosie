"""The Memory graph page's API (graphview.py does the reading; static/graph.js draws it). Read-only."""
from fastapi import APIRouter

import graphview

router = APIRouter()


@router.get("/api/graph/view")
def graph_view(source: str = "corner", q: str = "", limit: int = 150, hide: str = "", old: bool = False):
    try:
        return graphview.view(source, q, limit, tuple(hide.split("|")) if hide else (), old)
    except graphview.Unavailable as e:
        return {"error": str(e), "nodes": [], "edges": []}


@router.get("/api/graph/node")
def graph_node(source: str = "corner", id: str = ""):
    try:
        return graphview.node(source, id)
    except graphview.Unavailable as e:
        return {"error": str(e)}


@router.get("/api/graph/links")
def graph_links(source: str = "corner", pairs: str = ""):
    try:
        return {"links": graphview.links(source, [p.split("~", 1) for p in pairs.split("|") if "~" in p])}
    except graphview.Unavailable as e:
        return {"error": str(e), "links": {}}


@router.get("/api/graph/counts")
def graph_counts():
    return graphview.counts()
