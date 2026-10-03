"""Audit trail of administrative changes. Admins only."""

from typing import Annotated

from fastapi import APIRouter, Query

from vision_hub.api.deps import AdminOnly, AuditorDep
from vision_hub.domain.audit import AuditTarget
from vision_hub.schemas.audit import AuditEntryOut
from vision_hub.schemas.pagination import Page, PageParamsDep, encode_cursor
from vision_hub.schemas.problem import ProblemDetail

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[AdminOnly],
    responses={403: {"model": ProblemDetail}},
)


@router.get("", summary="List audit entries", responses={400: {"model": ProblemDetail}})
async def list_audit_entries(
    auditor: AuditorDep,
    page: PageParamsDep,
    target_type: AuditTarget | None = None,
    target_id: Annotated[str | None, Query(max_length=100)] = None,
    actor: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[AuditEntryOut]:
    """Newest first: who changed which device or user, and when. `request_id` matches the
    server logs of the request that made the change."""
    entries = await auditor.list(
        limit=page.limit + 1,
        target_type=target_type,
        target_id=target_id,
        actor=actor,
        before=page.time_position("at"),
    )
    items = entries[: page.limit]
    next_cursor = None
    if len(entries) > page.limit:
        last = items[-1]
        next_cursor = encode_cursor({"at": last.at.isoformat(), "id": last.id})
    return Page(items=[AuditEntryOut.from_entry(e) for e in items], next_cursor=next_cursor)
