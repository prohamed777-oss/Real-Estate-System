"""AI Platform models (§51, §54, §62, §95): profiles, executions ledger, tools, knowledge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base, Timestamped, UUIDPk


class AgentProfile(Base, UUIDPk, Timestamped):
    """ONE runtime, many profiles (review rule #1). Each profile is a versioned
    bundle: system prompt + tool scopes + guardrails + model profile + limits."""

    __tablename__ = "agent_profiles"
    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("tenant_id", "key", "version",
                                                  name="uq_agent_profiles_key_version"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id"), index=True
    )  # NULL = global default template
    # reception|qualification|matching|scheduling|followup|reactivation|sales_copilot|manager_copilot|analytics
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str | None] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text)
    model_profile: Mapped[str] = mapped_column(String(30), default="standard")  # fast|standard|reasoning
    tool_scopes: Mapped[list] = mapped_column(JSONB, default=list)  # allowed tool names
    knowledge_scopes: Mapped[list] = mapped_column(JSONB, default=list)
    memory_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    escalation_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    max_steps: Mapped[int] = mapped_column(Integer, default=6)
    max_latency_ms: Mapped[int] = mapped_column(Integer, default=20000)
    max_cost_usd: Mapped[float] = mapped_column(Float, default=0.50)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(default=True)


class AIExecution(Base, UUIDPk):
    """AI Action Ledger (§95) — the basis of debugging + AI governance."""

    __tablename__ = "ai_executions"
    __table_args__ = (
        __import__("sqlalchemy").Index("ix_ai_exec_tenant_time", "tenant_id", "started_at"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_key: Mapped[str] = mapped_column(String(60))
    agent_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    agent_version: Mapped[int] = mapped_column(Integer, default=1)
    prompt_version: Mapped[str] = mapped_column(String(30), default="v1")
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    model: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str | None] = mapped_column(String(40))
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    tool_results: Mapped[list] = mapped_column(JSONB, default=list)
    authorization: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # running|completed|failed|timeout|budget_exceeded|escalated|handoff
    status: Mapped[str] = mapped_column(String(30), default="running")
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    final_message: Mapped[str | None] = mapped_column(Text)
    guardrail_flags: Mapped[list] = mapped_column(JSONB, default=list)
    human_handoff: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeDoc(Base, UUIDPk, Timestamped):
    __tablename__ = "knowledge_docs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), default="faq")  # faq|policy|project_info|document
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    text_content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|failed


class KnowledgeChunk(Base, UUIDPk):
    __tablename__ = "knowledge_chunks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_docs.id"), index=True)
    chunk_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class EvalRun(Base, UUIDPk):
    """Agent evaluation suite results (§63) — gate for any new version."""

    __tablename__ = "eval_runs"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_key: Mapped[str] = mapped_column(String(60))
    agent_version: Mapped[int] = mapped_column(Integer)
    dataset_name: Mapped[str] = mapped_column(String(100))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cases: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
