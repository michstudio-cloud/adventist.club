import uuid
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): pass

class Ministry(Base):
    __tablename__="ministries"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    slug: Mapped[str]=mapped_column(String(60),unique=True)
    name: Mapped[str]=mapped_column(String(120))
    status: Mapped[str]=mapped_column(String(20))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class Application(Base):
    __tablename__="applications"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    ministry_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    name: Mapped[str]=mapped_column(String(160))
    slug: Mapped[str]=mapped_column(String(80),unique=True)
    domain: Mapped[str|None]=mapped_column(String(255))
    status: Mapped[str]=mapped_column(String(20))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class Organization(Base):
    __tablename__="organizations"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    parent_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    type: Mapped[str]=mapped_column(String(40))
    name: Mapped[str]=mapped_column(String(180))
    code: Mapped[str|None]=mapped_column(String(60))
    status: Mapped[str]=mapped_column(String(20))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class Club(Base):
    __tablename__="clubs"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    ministry_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    name: Mapped[str]=mapped_column(String(180))
    code: Mapped[str|None]=mapped_column(String(60))
    status: Mapped[str]=mapped_column(String(20))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class Honor(Base):
    __tablename__="honors"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    ministry_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    category_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("honor_categories.id"))
    name: Mapped[str]=mapped_column(String(180))
    slug: Mapped[str]=mapped_column(String(180))
    image_url: Mapped[str|None]=mapped_column(Text)
    source_url: Mapped[str|None]=mapped_column(Text)
    active: Mapped[bool]=mapped_column(Boolean)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class CertificateTemplate(Base):
    __tablename__="certificate_templates"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    ministry_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    organization_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    name: Mapped[str]=mapped_column(String(180))
    width: Mapped[float]=mapped_column(Float)
    height: Mapped[float]=mapped_column(Float)
    unit: Mapped[str]=mapped_column(String(8))
    orientation: Mapped[str]=mapped_column(String(20))
    bleed: Mapped[float]=mapped_column(Float)
    safe_margin: Mapped[float]=mapped_column(Float)
    supports_svg: Mapped[bool]=mapped_column(Boolean)
    background_url: Mapped[str|None]=mapped_column(Text)
    layout_json: Mapped[dict|None]=mapped_column(JSONB)
    active: Mapped[bool]=mapped_column(Boolean)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class Certificate(Base):
    __tablename__="certificates"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    application_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("applications.id"))
    ministry_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    club_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("clubs.id"))
    honor_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("honors.id"))
    template_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("certificate_templates.id"))
    certificate_no: Mapped[str]=mapped_column(String(80),unique=True)
    recipient_name: Mapped[str]=mapped_column(String(180))
    club_name_snapshot: Mapped[str|None]=mapped_column(String(180))
    honor_name_snapshot: Mapped[str]=mapped_column(String(180))
    issued_date: Mapped[date]=mapped_column(Date)
    place: Mapped[str|None]=mapped_column(String(180))
    instructor_name: Mapped[str|None]=mapped_column(String(180))
    director_name: Mapped[str|None]=mapped_column(String(180))
    status: Mapped[str]=mapped_column(String(30))
    certificate_hash: Mapped[str|None]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class CertificateEvent(Base):
    __tablename__="certificate_events"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    certificate_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("certificates.id"))
    event_type: Mapped[str]=mapped_column(String(40))
    actor_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True))
    metadata_json: Mapped[dict|None]=mapped_column(JSONB)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
