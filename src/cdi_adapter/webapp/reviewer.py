"""``/reviewer`` workbasket -> worklist console API (ARCHITECTURE §4.5, §7.1).

| Method / path                                   | Purpose                                     |
|-------------------------------------------------|---------------------------------------------|
| GET  /api/reviewer/workbasket                   | shared queue                                |
| GET  /api/reviewer/worklist                     | the signed-in reviewer's claimed items      |
| POST /api/reviewer/items/{id}/claim|approve|reject | move / decide an item (approve promotes)  |
| GET  /api/reviewer/items/{id}/elements          | the item's facts (current versions)         |
| GET  /api/reviewer/items/{id}/bundle            | the generated canonical FHIR bundle         |

Held facts inside an item are decided with the S8 endpoint (``POST /api/facts/{id}/review``).
The reviewer is the signed-in user (backlog E7-S1): a ``reviewer`` sent by the client is ignored.
Only the reviewer who claimed an item may approve or reject it; an admin may act on any claimed
item, recorded under the admin's own name. The Customer-360 read model is backlog E7-S8.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from cdi_adapter.canon import promote
from cdi_adapter.canon import repo as canon
from cdi_adapter.db import session_scope
from cdi_adapter.webapp import auth
from cdi_adapter.webapp.reviewer_page import reviewer_page

router = APIRouter()


class Decision(BaseModel):
    # no reviewer field (E7-S1): the reviewer is the session user; a client-sent one is ignored
    reason: str | None = Field(default=None, max_length=2000)


def _call(fn):
    try:
        return fn()
    except promote.WorkflowError as e:
        raise HTTPException(e.status, e.detail) from e


@router.get("/reviewer", response_class=HTMLResponse)
def page() -> str:
    return reviewer_page()


@router.get("/api/reviewer/workbasket")
def workbasket() -> list[dict[str, Any]]:
    with session_scope() as c:
        return canon.list_items(c, status="workbasket")


@router.get("/api/reviewer/worklist")
def worklist(request: Request) -> list[dict[str, Any]]:
    me = auth.actor(request).username
    with session_scope() as c:
        return canon.list_items(c, status="claimed", assignee=me)


@router.post("/api/reviewer/items/{item_id}/claim")
def claim(item_id: UUID, request: Request) -> dict[str, Any]:
    me = auth.actor(request).username
    with session_scope() as c:
        return _call(lambda: promote.claim(c, item_id, me))


@router.post("/api/reviewer/items/{item_id}/approve")
def approve(item_id: UUID, request: Request) -> dict[str, Any]:
    user = auth.actor(request)
    with session_scope() as c:
        return _call(
            lambda: promote.promote_review_item(
                c, item_id, user.username, override=user.role == "admin"
            )
        )


@router.post("/api/reviewer/items/{item_id}/reject")
def reject(item_id: UUID, request: Request, body: Decision | None = None) -> dict[str, Any]:
    user = auth.actor(request)
    reason = body.reason if body else None
    with session_scope() as c:
        return _call(
            lambda: promote.reject_item(
                c, item_id, user.username, reason, override=user.role == "admin"
            )
        )


@router.get("/api/reviewer/items/{item_id}/elements")
def item_elements(item_id: UUID) -> dict[str, Any]:
    with session_scope() as c:
        item = canon.get_item(c, item_id)
        if item is None:
            raise HTTPException(404, "review item not found")
        return {"item": item, "elements": canon.elements(c, item_id)}


@router.get("/api/reviewer/items/{item_id}/bundle")
def item_bundle(item_id: UUID) -> dict[str, Any]:
    with session_scope() as c:
        row = c.execute(text("SELECT * FROM cn_fhir_bundle WHERE rv_item_id = :i "
                             "ORDER BY created_at DESC LIMIT 1"), {"i": str(item_id)}
                        ).mappings().first()  # fmt: skip
    if row is None:
        raise HTTPException(404, "no bundle yet — approve the item first")
    return dict(row)
