"""``/reviewer`` workbasket -> worklist console API (ARCHITECTURE §4.5, §7.1).

| Method / path                                   | Purpose                                     |
|-------------------------------------------------|---------------------------------------------|
| GET  /api/reviewer/workbasket                   | shared queue                                |
| GET  /api/reviewer/worklist                     | the signed-in reviewer's claimed items      |
| POST /api/reviewer/items/{id}/claim|approve|reject | move / decide an item (approve promotes)  |
| GET  /api/reviewer/items/{id}/elements          | the item's facts (current versions)         |
| GET  /api/reviewer/items/{id}/bundle            | the generated canonical FHIR bundle         |
| GET  /api/reviewer/items/{id}/bundle/download   | that bundle as a FHIR file (fhir+json)      |
| GET  /api/reviewer/approved                     | the latest approved items (to download)     |

Held facts inside an item are decided with the S8 endpoint (``POST /api/facts/{id}/review``).
The reviewer is the signed-in user (backlog E7-S1): a ``reviewer`` sent by the client is ignored.
Only the reviewer who claimed an item may approve or reject it; an admin may act on any claimed
item, recorded under the admin's own name. The Customer-360 read model is backlog E7-S8.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from cdi_adapter.canon import promote
from cdi_adapter.canon import repo as canon
from cdi_adapter.catalogue.service import matches_for_facts
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
        els = canon.elements(c, item_id)
        med_ids = [str(e["fact"]["id"]) for e in els
                   if e["fact"] and e["fact"]["fact_type"] == "medication"]  # fmt: skip
        catalogue = matches_for_facts(c, med_ids)
        for e in els:  # a medication's detail: what the page shows and its Edit form fills
            f = e["fact"]
            e["medication"] = None
            e["catalogue"] = None
            if f and f["fact_type"] == "medication":
                m = c.execute(text("SELECT * FROM medication_detail WHERE fact_id = :i"),
                              {"i": str(f["id"])}).mappings().first()  # fmt: skip
                e["medication"] = dict(m) if m else None
                # the Tata 1mg catalogue's identification of this line (None: not matched yet)
                e["catalogue"] = catalogue.get(str(f["id"]))
        return {"item": item, "elements": els}


APPROVED_SHOWN = 20


@router.get("/api/reviewer/approved")
def approved() -> list[dict[str, Any]]:
    """The latest approved items, newest first: an approved item leaves the worklist, and this is
    where its FHIR bundle is downloaded from afterwards."""
    with session_scope() as c:
        latest: dict[Any, dict[str, Any]] = {}
        # one line per document: its latest approval (the same document uploaded again and
        # approved again is the newer record of it, not a second one)
        for i in canon.list_items(c, status="approved", limit=APPROVED_SHOWN * 3):
            latest.setdefault(i["source_document_id"] or i["id"], i)
        items = list(latest.values())[:APPROVED_SHOWN]
        for i in items:  # the bundle's own status: ready_to_share, or draft (validation failed)
            i["bundle_status"] = c.execute(
                text("SELECT status FROM cn_fhir_bundle WHERE rv_item_id = :i "
                     "ORDER BY created_at DESC LIMIT 1"), {"i": str(i["id"])}
            ).scalar_one_or_none()  # fmt: skip
    return items


def _latest_bundle(item_id: UUID) -> dict[str, Any]:
    with session_scope() as c:
        row = c.execute(text("SELECT * FROM cn_fhir_bundle WHERE rv_item_id = :i "
                             "ORDER BY created_at DESC LIMIT 1"), {"i": str(item_id)}
                        ).mappings().first()  # fmt: skip
    if row is None:
        raise HTTPException(404, "no bundle yet — approve the item first")
    return dict(row)


@router.get("/api/reviewer/items/{item_id}/bundle")
def item_bundle(item_id: UUID) -> dict[str, Any]:
    return _latest_bundle(item_id)


@router.get("/api/reviewer/items/{item_id}/bundle/download")
def item_bundle_download(item_id: UUID) -> Response:
    """The approved item's canonical FHIR Bundle as a file: the Bundle itself, nothing around it
    (its status and validation stay in ``/bundle``)."""
    row = _latest_bundle(item_id)
    disposition = f'attachment; filename="fhir_{row["artifact_type"]}_{str(item_id)[:8]}.json"'
    return Response(json.dumps(row["bundle"], default=str, indent=2),
                    media_type="application/fhir+json",
                    headers={"Content-Disposition": disposition})  # fmt: skip
