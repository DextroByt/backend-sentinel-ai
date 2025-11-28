import enum
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Any

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Text, Integer, select, or_
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Base

# --- 1. Enums ---

class VerificationStatusEnum(str, enum.Enum):
    VERIFIED = "VERIFIED"
    DEBUNKED = "DEBUNKED"
    UNCONFIRMED = "UNCONFIRMED"

class AnalysisStatusEnum(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

# --- 2. SQLAlchemy Models ---

class Crisis(Base):
    __tablename__ = "crises"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    description = Column(Text)
    keywords = Column(Text, nullable=False)
    severity = Column(Integer, default=50) 
    # [CHANGE] Added Location
    location = Column(Text, nullable=True, default="Unknown Location")
    
    verdict_status = Column(String, default="PENDING")
    verdict_summary = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TimelineItem(Base):
    __tablename__ = "timeline_items"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    crisis_id = Column(UUID(as_uuid=True), ForeignKey("crises.id", ondelete="CASCADE"), nullable=False)
    claim_text = Column(Text, nullable=False, unique=True)
    summary = Column(Text, nullable=False)
    status = Column(PgEnum(VerificationStatusEnum, name="VerificationStatusEnum", create_type=True), nullable=False)
    # [CHANGE] Added Location
    location = Column(Text, nullable=True)
    sources = Column(JSON)
    timestamp = Column(DateTime, default=datetime.utcnow)

class AdHocAnalysis(Base):
    __tablename__ = "adhoc_analyses"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_text = Column(Text, nullable=False)
    status = Column(PgEnum(AnalysisStatusEnum, name="AnalysisStatusEnum", create_type=True), nullable=False, default=AnalysisStatusEnum.PENDING)
    verdict_status = Column(String, nullable=True) 
    verdict_summary = Column(Text, nullable=True)
    verdict_sources = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class SystemNotification(Base):
    __tablename__ = "system_notifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content = Column(Text, nullable=False)
    notification_type = Column(String, default="MISINFO_ALERT") 
    crisis_id = Column(UUID(as_uuid=True), ForeignKey("crises.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# --- 3. CRUD Functions ---

async def create_crisis(db: AsyncSession, name: str, description: str, keywords: str, severity: int, location: str = "Unknown") -> Crisis:
    """
    Autonomously creates a new crisis entry with severity score and location.
    """
    db_obj = Crisis(
        name=name,
        description=description,
        keywords=keywords,
        severity=severity,
        location=location, # [CHANGE]
        verdict_status="PENDING",
        verdict_summary="Initial assessment in progress. Sentinel AI is aggregating claims..."
    )
    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def get_crisis_by_fuzzy_name(db: AsyncSession, name: str) -> Optional[Crisis]:
    result = await db.execute(select(Crisis).where(Crisis.name.ilike(f"%{name}%")))
    return result.scalar_one_or_none()

async def get_crises(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Crisis]:
    result = await db.execute(
        select(Crisis)
        .offset(skip)
        .limit(limit)
        .order_by(Crisis.severity.desc(), Crisis.created_at.desc())
    )
    return result.scalars().all()

async def get_crisis(db: AsyncSession, crisis_id: uuid.UUID) -> Optional[Crisis]:
    result = await db.execute(select(Crisis).where(Crisis.id == crisis_id))
    return result.scalar_one_or_none()

async def update_crisis_verdict(db: AsyncSession, crisis_id: uuid.UUID, verdict_status: str, verdict_summary: str) -> Optional[Crisis]:
    result = await db.execute(select(Crisis).where(Crisis.id == crisis_id))
    crisis = result.scalar_one_or_none()
    if crisis:
        crisis.verdict_status = verdict_status
        crisis.verdict_summary = verdict_summary
        crisis.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(crisis)
    return crisis

# --- Timeline Management ---

async def get_timeline_items(db: AsyncSession, crisis_id: uuid.UUID) -> List[TimelineItem]:
    result = await db.execute(
        select(TimelineItem)
        .where(TimelineItem.crisis_id == crisis_id)
        .order_by(TimelineItem.timestamp.desc())
    )
    return result.scalars().all()

async def get_timeline_item_by_claim_text(db: AsyncSession, claim_text: str) -> Optional[TimelineItem]:
    result = await db.execute(select(TimelineItem).where(TimelineItem.claim_text == claim_text))
    return result.scalar_one_or_none()

async def get_unconfirmed_timeline_items(db: AsyncSession, limit: int = 10) -> List[TimelineItem]:
    result = await db.execute(
        select(TimelineItem)
        .where(TimelineItem.status == VerificationStatusEnum.UNCONFIRMED)
        .order_by(TimelineItem.timestamp.asc()) 
        .limit(limit)
    )
    return result.scalars().all()

async def update_timeline_item(db: AsyncSession, item_id: uuid.UUID, status: str, summary: str, sources: List[Any]) -> Optional[TimelineItem]:
    result = await db.execute(select(TimelineItem).where(TimelineItem.id == item_id))
    item = result.scalar_one_or_none()
    if item:
        status_enum = VerificationStatusEnum(status) if isinstance(status, str) else status
        item.status = status_enum
        item.summary = summary
        item.sources = sources
        item.timestamp = datetime.utcnow()
        await db.commit()
        await db.refresh(item)
    return item

async def create_timeline_item(db: AsyncSession, crisis_id: uuid.UUID, claim_text: str, summary: str, status: str | VerificationStatusEnum, sources: List[Any], location: str = None) -> Optional[TimelineItem]:
    existing = await get_timeline_item_by_claim_text(db, claim_text)
    if existing: return existing
    status_enum = VerificationStatusEnum(status) if isinstance(status, str) else status
    # [CHANGE] Added location to insert
    item = TimelineItem(crisis_id=crisis_id, claim_text=claim_text, summary=summary, status=status_enum, sources=sources, location=location)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item

# --- AdHoc Analysis Management ---

async def create_adhoc_analysis(db: AsyncSession, query_text: str) -> AdHocAnalysis:
    db_obj = AdHocAnalysis(query_text=query_text, status=AnalysisStatusEnum.PENDING)
    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def get_adhoc_analysis(db: AsyncSession, analysis_id: uuid.UUID) -> Optional[AdHocAnalysis]:
    result = await db.execute(select(AdHocAnalysis).where(AdHocAnalysis.id == analysis_id))
    return result.scalar_one_or_none()

async def update_adhoc_analysis(db: AsyncSession, analysis_id: uuid.UUID, status: AnalysisStatusEnum, verdict: Optional[dict] = None) -> Optional[AdHocAnalysis]:
    result = await db.execute(select(AdHocAnalysis).where(AdHocAnalysis.id == analysis_id))
    obj = result.scalar_one_or_none()
    if obj:
        obj.status = status
        if verdict:
            obj.verdict_status = verdict.get("status")
            obj.verdict_summary = verdict.get("summary")
            obj.verdict_sources = verdict.get("sources")
        await db.commit()
        await db.refresh(obj)
    return obj

# --- Notification Management ---

async def create_notification(db: AsyncSession, content: str, type: str = "MISINFO_ALERT", crisis_id: Optional[uuid.UUID] = None) -> SystemNotification:
    obj = SystemNotification(content=content, notification_type=type, crisis_id=crisis_id)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj

async def get_latest_notification(db: AsyncSession) -> Optional[SystemNotification]:
    result = await db.execute(
        select(SystemNotification)
        .order_by(SystemNotification.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()

# --- Cleanup Logic ---

async def delete_old_crises(db: AsyncSession, days_retention: int = 3):
    cutoff_date = datetime.utcnow() - timedelta(days=days_retention)
    result = await db.execute(select(Crisis).where(Crisis.created_at < cutoff_date))
    old_crises = result.scalars().all()
    count = 0
    for crisis in old_crises:
        await db.delete(crisis)
        count += 1
    await db.commit()
    return count

async def delete_old_adhoc_analyses(db: AsyncSession, hours_retention: int = 6):
    cutoff_date = datetime.utcnow() - timedelta(hours=hours_retention)
    result = await db.execute(select(AdHocAnalysis).where(AdHocAnalysis.created_at < cutoff_date))
    old_items = result.scalars().all()
    count = 0
    for item in old_items:
        await db.delete(item)
        count += 1
    await db.commit()
    return count

async def delete_stale_unconfirmed_items(db: AsyncSession, hours_retention: int = 48):
    cutoff_date = datetime.utcnow() - timedelta(hours=hours_retention)
    result = await db.execute(
        select(TimelineItem)
        .where(TimelineItem.status == VerificationStatusEnum.UNCONFIRMED)
        .where(TimelineItem.timestamp < cutoff_date)
    )
    stale_items = result.scalars().all()
    count = 0
    for item in stale_items:
        await db.delete(item)
        count += 1
    await db.commit()
    return count