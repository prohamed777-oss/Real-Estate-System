"""AI handlers: knowledge embedding + profile seeding job."""

from __future__ import annotations

from app.events.registry import job_handler


@job_handler("ai.embed_knowledge")
async def embed_knowledge_job(session, tenant_id, payload):  # noqa: ANN001
    """Embed pending knowledge docs into chunks (§85 pipeline, knowledge part)."""
    from sqlalchemy import select

    from app.ai.embeddings import get_embedding_provider
    from app.ai.models import KnowledgeChunk, KnowledgeDoc

    if tenant_id is None:
        return
    docs = (
        await session.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.tenant_id == tenant_id, KnowledgeDoc.status == "pending"
            )
        )
    ).scalars().all()
    if not docs:
        return
    provider = get_embedding_provider()
    for doc in docs:
        text = doc.text_content or ""
        chunks = [text[i:i + 1200] for i in range(0, len(text), 1200)][:20]
        if not chunks:
            doc.status = "ready"
            continue
        vectors = await provider.embed(chunks)
        for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=False)):
            session.add(
                KnowledgeChunk(
                    tenant_id=tenant_id, doc_id=doc.id, chunk_no=i, text=chunk, embedding=vec,
                )
            )
        doc.status = "ready"
    await session.flush()


@job_handler("ai.seed_profiles")
async def seed_profiles_job(session, tenant_id, payload):  # noqa: ANN001
    from app.ai.agents import seed_default_profiles

    await seed_default_profiles(session)
