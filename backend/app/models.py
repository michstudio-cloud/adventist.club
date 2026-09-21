import uuid
from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

class Base(DeclarativeBase): pass


class LtreeType(UserDefinedType):
    """
    PostgreSQL `ltree`. Values travel as text: parameters are wrapped in
    text2ltree() and columns are read through ltree2text(), so no driver-level
    codec is needed and `Organization.path.op("<@")("a.b")` just works.
    """
    cache_ok = True

    def get_col_spec(self, **kw):
        return "LTREE"

    def bind_expression(self, bindvalue):
        return func.text2ltree(bindvalue)

    def column_expression(self, col):
        return func.ltree2text(col)


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
    # 001_identity_org_honors.sql
    path: Mapped[str|None]=mapped_column(LtreeType())
    city: Mapped[str|None]=mapped_column(String(120))
    state: Mapped[str|None]=mapped_column(String(120))
    country: Mapped[str|None]=mapped_column(String(120))
    latitude: Mapped[float|None]=mapped_column(Float)
    longitude: Mapped[float|None]=mapped_column(Float)
    metadata_json: Mapped[dict|None]=mapped_column(JSONB)
    legacy_mongo_id: Mapped[str|None]=mapped_column(String(24))

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

class HonorCategory(Base):
    __tablename__="honor_categories"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    ministry_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    name: Mapped[str]=mapped_column(String(120))
    slug: Mapped[str]=mapped_column(String(120))
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
    # 001_identity_org_honors.sql
    code: Mapped[str|None]=mapped_column(String(40))
    description: Mapped[str|None]=mapped_column(Text)
    difficulty_level: Mapped[str|None]=mapped_column(String(20))
    honor_type: Mapped[str|None]=mapped_column(String(20))
    status: Mapped[str]=mapped_column(String(30),server_default="DRAFT")
    org_scope_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    estimated_hours: Mapped[int|None]=mapped_column(Integer)
    exam_passing_score: Mapped[int]=mapped_column(Integer,server_default="80")
    exam_time_limit_minutes: Mapped[int|None]=mapped_column(Integer)
    max_exam_attempts: Mapped[int]=mapped_column(Integer,server_default="3")
    created_by_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("users.id"))
    approved_zone_org_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    approved_association_org_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id"))
    thumbnail_url: Mapped[str|None]=mapped_column(Text)
    version: Mapped[int]=mapped_column(Integer,server_default="1")
    previous_version_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("honors.id"))
    changes_description: Mapped[str|None]=mapped_column(Text)
    published_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    legacy_mongo_id: Mapped[str|None]=mapped_column(String(24))
    # 005_honor_translations.sql — link to the official wiki ("AY Honors/<wiki_title>") and its public facts
    wiki_title: Mapped[str|None]=mapped_column(String(200))
    authority: Mapped[str|None]=mapped_column(String(10))
    skill_level: Mapped[int|None]=mapped_column(SmallInteger)
    year_introduced: Mapped[int|None]=mapped_column(SmallInteger)

class HonorTranslation(Base):
    """honors.name is the source text (Spanish); every other language is one row here."""
    __tablename__="honor_translations"
    honor_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("honors.id",ondelete="CASCADE"),primary_key=True)
    locale: Mapped[str]=mapped_column(String(16),primary_key=True)
    name: Mapped[str]=mapped_column(String(180))
    description: Mapped[str|None]=mapped_column(Text)
    source: Mapped[str|None]=mapped_column(String(40))
    source_url: Mapped[str|None]=mapped_column(Text)
    license: Mapped[str|None]=mapped_column(String(40))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

class HonorCategoryTranslation(Base):
    __tablename__="honor_category_translations"
    category_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("honor_categories.id",ondelete="CASCADE"),primary_key=True)
    locale: Mapped[str]=mapped_column(String(16),primary_key=True)
    name: Mapped[str]=mapped_column(String(120))
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
    # 007_portfolio.sql — set only on certificates issued from a portfolio; never part of the hash
    user_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("users.id"))
    enrollment_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("honor_enrollments.id"))
    issued_by_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("users.id"))
    issued_role: Mapped[str|None]=mapped_column(String(40))

class CertificateEvent(Base):
    __tablename__="certificate_events"
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True)
    certificate_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("certificates.id"))
    event_type: Mapped[str]=mapped_column(String(40))
    actor_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True))
    metadata_json: Mapped[dict|None]=mapped_column(JSONB)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())


# ---------------------------------------------------------------------------
# 001_identity_org_honors.sql: identity, audit and the honors review workflow.
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(180))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(40), server_default="STUDENT")
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    is_minor: Mapped[bool] = mapped_column(Boolean, server_default="false")
    birth_date: Mapped[date | None] = mapped_column(Date)
    mfa_secret: Mapped[str | None] = mapped_column(Text)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    verification_status: Mapped[str] = mapped_column(String(20), server_default="PENDING")
    child_protection_completed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    child_protection_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy_mongo_id: Mapped[str | None] = mapped_column(String(24), unique=True)
    # 003_club_signup.sql: state of the club a CLUB_DIRECTOR requested (NULL = not applicable).
    club_approval: Mapped[str | None] = mapped_column(String(20))
    club_approval_reason: Mapped[str | None] = mapped_column(Text)
    club_approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Guardianship(Base):
    __tablename__ = "guardianships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    guardian_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    child_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    relationship: Mapped[str] = mapped_column(String(20), server_default="PARENT")
    consent_status: Mapped[str] = mapped_column(String(20), server_default="PENDING")
    consent_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy_mongo_id: Mapped[str | None] = mapped_column(String(24), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(Text, unique=True)
    code: Mapped[str] = mapped_column(String(6))
    type: Mapped[str] = mapped_column(String(30))
    used: Mapped[bool] = mapped_column(Boolean, server_default="false")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    user_email: Mapped[str | None] = mapped_column(CITEXT)
    user_role: Mapped[str | None] = mapped_column(String(40))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    details: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    legacy_mongo_id: Mapped[str | None] = mapped_column(String(24), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HonorRequirement(Base):
    __tablename__ = "honor_requirements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    honor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    description: Mapped[str] = mapped_column(Text)
    is_theoretical: Mapped[bool] = mapped_column(Boolean, server_default="true")
    instructions: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 006_requirement_locale.sql — one list per language; imported rows keep their attribution
    locale: Mapped[str] = mapped_column(String(16), server_default="es")
    source: Mapped[str | None] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(40))


class HonorQuestion(Base):
    __tablename__ = "honor_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("honor_requirements.id")
    )
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    question_text: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(20))
    options: Mapped[list | None] = mapped_column(JSONB)
    correct_answer: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, server_default="1")
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HonorReview(Base):
    __tablename__ = "honor_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    honor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewer_name: Mapped[str | None] = mapped_column(String(180))
    reviewer_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(20))
    comments: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 009b_courses.sql — set on the review of a COURSE; NULL means "review of the honor".
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE", use_alter=True)
    )


class HonorResource(Base):
    __tablename__ = "honor_resources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    honor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 007_portfolio.sql: enrollment in an honor, progress per requirement, evidence.
# ---------------------------------------------------------------------------


class HonorEnrollment(Base):
    __tablename__ = "honor_enrollments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # The published version the member enrolled in; it never moves to a newer one.
    honor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
    mode: Mapped[str] = mapped_column(String(10), server_default="CLUB")
    # Follows the member's current club on every write; frozen once certified.
    club_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    locale: Mapped[str] = mapped_column(String(16), server_default="es")
    status: Mapped[str] = mapped_column(String(15), server_default="IN_PROGRESS")
    # certificates.enrollment_id points back here: use_alter breaks the cycle for the metadata sort.
    certificate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("certificates.id", use_alter=True)
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementProgress(Base):
    __tablename__ = "requirement_progress"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("honor_enrollments.id", ondelete="CASCADE")
    )
    # Stable across languages: requirement 3 in `es` and in `en` is the same requirement.
    requirement_position: Mapped[int] = mapped_column(Integer)
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("honor_requirements.id", ondelete="SET NULL")
    )
    is_practical: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(12), server_default="PENDING")
    completed_via: Mapped[str | None] = mapped_column(String(8))
    member_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    progress_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirement_progress.id", ondelete="CASCADE")
    )
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(15), server_default="PENDING_UPLOAD")
    # Key in the PRIVATE bucket, never a URL.
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    taken_on: Mapped[date | None] = mapped_column(Date)
    place: Mapped[str | None] = mapped_column(String(180))
    caption: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by migrations/purge_removed_evidence.py once the object is gone from the bucket.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 009_church_letters.sql: the church letter that backs an office, and its validation.
# ---------------------------------------------------------------------------


class ChurchLetter(Base):
    """Platform-wide, by organization and not by club: a virtual instructor may belong to no
    club at all. Bloque E reuses this table for other offices through `role_requested`."""

    __tablename__ = "church_letters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    role_requested: Mapped[str] = mapped_column(String(40), server_default="INSTRUCTOR")
    # The applicant's organization when they presented it: it decides who reviews it.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    church_name: Mapped[str] = mapped_column(String(180))
    pastor_name: Mapped[str | None] = mapped_column(String(180))
    church_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    # Key in the PRIVATE bucket, never a URL.
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), server_default="PENDING_UPLOAD")
    zone_validated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    zone_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # NULL = no expiry; whoever authorizes decides (D7).
    valid_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 009b_courses.sql: an instructor's offering of a published honor, with lessons
# and the per-requirement evaluation plan.
# ---------------------------------------------------------------------------


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # The published version the course was built on; it never moves.
    honor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
    instructor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # The instructor's organization when they created it: it decides which reviewers see it.
    org_scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    locale: Mapped[str] = mapped_column(String(16), server_default="es")
    title: Mapped[str] = mapped_column(String(180))
    summary: Mapped[str | None] = mapped_column(String(600))
    cover_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), server_default="DRAFT")
    # True only when a reviewer withdrew it, not when the instructor archived it.
    archived_by_authority: Mapped[bool] = mapped_column(Boolean, server_default="false")
    archive_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id")
    )
    changes_description: Mapped[str | None] = mapped_column(Text)
    approved_zone_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    approved_association_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    # Operational: they stay editable on a published course.
    enrollment_open: Mapped[bool] = mapped_column(Boolean, server_default="true")
    capacity: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseLesson(Base):
    __tablename__ = "course_lessons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    requirement_positions: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default="{}")
    # The content blocks (text, image, pdf, video): always read and written with their lesson.
    blocks: Mapped[list] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseRequirement(Base):
    """How this course evaluates each requirement of the honor. Never more lenient than
    the honor: a practical requirement must stay EVIDENCE."""

    __tablename__ = "course_requirements"

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    requirement_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment: Mapped[str] = mapped_column(String(10))
    # Questions drawn per attempt; only an EXAM requirement has a bank (Bloque C).
    draw_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    guidance: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# 008b_club_membership.sql: the book of who belongs to which club and how.
# `users.organization_id` stays the truth for the RBAC; this table is the
# history, and only app/services/memberships.py writes either of them.
# ---------------------------------------------------------------------------


class ClubMembership(Base):
    __tablename__ = "club_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    role: Mapped[str] = mapped_column(String(40), server_default="STUDENT")
    # PENDING_CONSENT -> PENDING_APPROVAL -> ACTIVE -> ENDED; REJECTED; CANCELLED
    status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(12))
    # FK added by 008c (club_invitations); unit_id by 008d (club_units).
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    message: Mapped[str | None] = mapped_column(String(500))
    # Minors only: who was asked for consent for THIS club.
    guardian_email: Mapped[str | None] = mapped_column(CITEXT)
    consent_token_hash: Mapped[str | None] = mapped_column(String(64))
    consent_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(20))
    ended_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 008c_club_invitations.sql: the link a club shares, and the record of which
# transactional e-mails went out (never their body).
# ---------------------------------------------------------------------------


class ClubInvitation(Base):
    __tablename__ = "club_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    role: Mapped[str] = mapped_column(String(40))
    # FK to club_units arrives with 008d (E5).
    unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Set on a nominal invitation: only that account may accept it.
    email: Mapped[str | None] = mapped_column(CITEXT)
    # SHA-256 of the token shown once to whoever created the invitation.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    max_uses: Mapped[int] = mapped_column(Integer, server_default="1")
    uses: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    """One row per transactional message: what kind, about which row, to which
    address. Never the body. It is how a send is not repeated and how a daily
    cap is enforced without a scheduler."""

    __tablename__ = "notification_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(CITEXT)
    kind: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ok: Mapped[bool] = mapped_column(Boolean, server_default="true")


# ---------------------------------------------------------------------------
# 008_mfa_recovery.sql: the way back in when the authenticator is lost.
# ---------------------------------------------------------------------------


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    # SHA-256 of the code shown once. The plain code is never stored anywhere.
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 008d_club_units.sql: the units a club splits into. A light table, NOT a node
# of the organization tree: the member keeps hanging from the club, which is
# what `users.organization_id` (and therefore the whole RBAC) reads.
# ---------------------------------------------------------------------------


class ClubUnit(Base):
    __tablename__ = "club_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(80))
    # A warning, never a refusal: birthdays move people out of their bracket.
    min_age: Mapped[int | None] = mapped_column(SmallInteger)
    max_age: Mapped[int | None] = mapped_column(SmallInteger)
    # Hard: the director raises it in one touch.
    capacity: Mapped[int | None] = mapped_column(SmallInteger)
    counselor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
