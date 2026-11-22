"""Human approvals (ARCHITECTURE §6.5). Framework-free.

A node asks with `request_approval` and then interrupts the graph. LangGraph re-executes that node
from its beginning on resume (spike A-4), so the request is an upsert keyed by the node's position
(DECISIONS C-5): the second call finds the same row.

A person answers with `decide`. That records the answer and, if the run is waiting, requeues it;
the executor then resumes the graph with the answer.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import utcnow
from src.core.databases.models.runs import Approval, ResearchRun
from src.core.errors import ConflictError, NotFound
from src.core.events import store
from src.core.events.store import EventContext
from src.core.ids import uuid7
from src.runs import events, service
from src.runs.dispatch import Dispatcher
from src.runs.states import TERMINAL


async def request_approval(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    request_key: str,
    kind: str,
    payload: dict[str, Any],
    context: EventContext,
) -> Approval:
    """Create the approval request once; later calls return the same row (C-5)."""
    inserted = await session.scalar(
        insert(Approval)
        .values(
            id=uuid7(),
            run_id=run_id,
            request_key=request_key,
            kind=kind,
            request_payload=payload,
            status="pending",
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=[Approval.request_key])
        .returning(Approval.id)
    )
    approval = await session.scalar(select(Approval).where(Approval.request_key == request_key))
    assert approval is not None
    if inserted is not None:
        await store.append(
            session,
            type_=events.APPROVAL_REQUESTED.name,
            payload=events.ApprovalRequested(
                run_id=str(run_id), approval_id=str(approval.id), kind=kind
            ),
            context=context,
            aggregate_type="research_run",
            aggregate_id=str(run_id),
        )
    return approval


async def decide(
    session: AsyncSession,
    approval_id: uuid.UUID,
    *,
    approved: bool,
    comment: str | None,
    context: EventContext,
    dispatcher: Dispatcher,
) -> Approval:
    approval = await session.scalar(
        select(Approval).where(Approval.id == approval_id).with_for_update()
    )
    if approval is None:
        raise NotFound(f"approval {approval_id} does not exist")
    if approval.status != "pending":
        raise ConflictError(f"approval is already {approval.status}")
    run = await session.scalar(
        select(ResearchRun).where(ResearchRun.id == approval.run_id).with_for_update()
    )
    assert run is not None
    if run.status in TERMINAL:
        raise ConflictError(f"the run is {run.status}; it can no longer be approved")
    approval.status = "approved" if approved else "rejected"
    approval.response = {"approved": approved, "comment": comment}
    approval.decided_by = (
        f"{context.actor.type}:{context.actor.id}" if context.actor.id else context.actor.type
    )
    approval.decided_at = utcnow()
    await store.append(
        session,
        type_=events.APPROVAL_DECIDED.name,
        payload=events.ApprovalDecided(
            run_id=str(run.id), approval_id=str(approval.id), approved=approved, comment=comment
        ),
        context=context,
        aggregate_type="research_run",
        aggregate_id=str(run.id),
    )
    if run.status == "awaiting_approval":
        await service.transition(session, run, "queued", context=context, reason="approval decided")
        await dispatcher.enqueue_run(session, run_id=run.id, thread_id=run.thread_id)
    await session.flush()
    return approval


async def decided_responses(
    session: AsyncSession, approval_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Any]:
    """Answers for the given approvals that have been decided (used to resume the graph)."""
    rows = await session.scalars(
        select(Approval).where(
            Approval.id.in_(approval_ids), Approval.status.in_(["approved", "rejected"])
        )
    )
    return {a.id: a.response for a in rows}
