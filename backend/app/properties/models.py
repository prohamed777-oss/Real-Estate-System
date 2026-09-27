"""Property & Supply models (§12-16, §20-21): assets, inventory, pricing, mandates.

Key separations (§13, §21):
- PropertyAsset is the physical/commercial asset (standalone OR project→building→unit)
- Listing is a marketing view of an asset (one asset → many listings)
- Price is VERSIONED history, never mutated
- Inventory state is a state machine, not a boolean
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Developer(Base, UUIDPk, Timestamped):
    __tablename__ = "developers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    contact: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    external_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Project(Base, UUIDPk, Timestamped):
    __tablename__ = "projects"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    developer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("developers.id"))
    name: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {city, area, address}
    geo: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {lat, lng}
    location_timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    delivery_date: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {ar, en}
    media: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(30), default="active")


class Building(Base, UUIDPk, Timestamped):
    __tablename__ = "buildings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    floors: Mapped[int | None] = mapped_column(Integer)


class PropertyAsset(Base, UUIDPk, Timestamped):
    """Canonical asset (§14). asset_type: 'unit' (in project) or 'standalone'."""

    __tablename__ = "property_assets"
    __table_args__ = (Index("ix_assets_type_attrs", "tenant_id", "asset_type"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(20), default="standalone")  # standalone | unit
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    building_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("buildings.id"))
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"))
    title: Mapped[str] = mapped_column(String(300))
    property_type: Mapped[str] = mapped_column(String(40))  # apartment|villa|duplex|studio|office|shop|land|chalet
    purpose: Mapped[str] = mapped_column(String(20), default="sale")  # sale | rent
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    area_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    area_unit: Mapped[str] = mapped_column(String(10), default="sqm")
    floor: Mapped[str | None] = mapped_column(String(30))
    finishing: Mapped[str | None] = mapped_column(String(40))  # finished|semi_finished|core_shell
    view: Mapped[str | None] = mapped_column(String(80))
    orientation: Mapped[str | None] = mapped_column(String(40))
    delivery_status: Mapped[str | None] = mapped_column(String(40))  # ready|under_construction|off_plan
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {city, area, compound, address}
    geo: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    location_timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # open attributes
    media: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Supply provenance (§15, §104): where did this asset come from, how fresh?
    source_type: Mapped[str | None] = mapped_column(String(50))  # developer|owner|partner_agency|internal|external_feed
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    external_id: Mapped[str | None] = mapped_column(String(200))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str] = mapped_column(String(20), default="local")  # local|synced|conflict|stale
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[int] = mapped_column(Integer, default=80)


class SupplySource(Base, UUIDPk, Timestamped):
    __tablename__ = "supply_sources"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # developer_api|erp|csv|manual|partner_feed
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str] = mapped_column(String(20), default="idle")
    health: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Mandate(Base, UUIDPk, Timestamped):
    """Rights over an asset are separate from ownership (§16)."""

    __tablename__ = "mandates"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"))
    mandate_type: Mapped[str] = mapped_column(String(30), default="open")  # open | exclusive
    rights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    commission_terms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|expired|terminated
    created_by: Mapped[str | None] = mapped_column(String(64))


class UnitInventory(Base):
    """1:1 with unit-type assets. State machine state lives here (§17)."""

    __tablename__ = "unit_inventory"
    __table_args__ = (UniqueConstraint("tenant_id", "asset_id", name="uq_inventory_asset"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="AVAILABLE", index=True)
    hold_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency
    availability_confidence: Mapped[str] = mapped_column(String(20), default="verified")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class InventoryHold(Base, UUIDPk):
    __tablename__ = "inventory_holds"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|released|expired


class PriceVersion(Base, UUIDPk):
    """Append-only price history (§20). Never UPDATE a price — insert a version."""

    __tablename__ = "price_versions"
    __table_args__ = (Index("ix_price_versions_asset", "tenant_id", "asset_id", "valid_from"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    payment_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(60))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {verified_at, confidence}


class PaymentPlan(Base, UUIDPk, Timestamped):
    """Payment plan template (§20): down payment + installments + fees."""

    __tablename__ = "payment_plans"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("property_assets.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    down_payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    down_payment_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    installment_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    installment_count: Mapped[int | None] = mapped_column(Integer)
    installment_period_months: Mapped[int | None] = mapped_column(Integer)
    maintenance_fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    admin_fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
