import uuid
from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, FetchedValue, Float, ForeignKey, ForeignKeyConstraint, Integer, Numeric, SmallInteger, String, Text, func
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
    # 019_club_ministry.sql: the ministry of a CLUB (NULL for every other node, and for a
    # club that never declared one). Rule 3 of ESTADO.md: nothing guesses it.
    ministry_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"))
    # 022_club_ministries.sql: since then `ministry_id` is the PRINCIPAL ministry, derived from
    # `organization_ministries` (the first one chosen). The list is the truth.
    # Where the club meets, as Google Places (or a pasted link) names it; and its logo.
    address: Mapped[str|None]=mapped_column(Text)
    place_id: Mapped[str|None]=mapped_column(String(255))
    maps_url: Mapped[str|None]=mapped_column(Text)
    logo_url: Mapped[str|None]=mapped_column(Text)

class OrganizationMinistry(Base):
    """022_club_ministries.sql: every ministry a club works with (one row each)."""
    __tablename__="organization_ministries"
    organization_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),primary_key=True)
    ministry_id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("ministries.id"),primary_key=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())

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
    # 011_certificate_revocation.sql (Bloque D I7) — annulling is never a delete, and none
    # of these three is part of `canonical()`: the hash of a revoked certificate is the
    # one it was issued with, so it verifies as «revocado», never as «modificado».
    revoked_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("users.id"))
    revocation_reason: Mapped[str|None]=mapped_column(Text)
    # 012_programs.sql (Bloque F) — investiture certificate of a program. Exactly one of
    # `honor_id` / `program_id` is set on a portfolio certificate, and ONLY `honor_id`
    # enables buying the patch: a class certificate is never an honor certificate.
    program_id: Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("programs.id",use_alter=True))
    # 016_certificate_locale.sql — the language it was issued in (one the template speaks).
    # Outside `canonical()`: the hash never covers it. Rows from before 016 are 'es'.
    locale: Mapped[str]=mapped_column(String(8),default="es",server_default="es")
    # 021_certificate_signatures.sql — the handwritten signatures printed when it was issued:
    # an immutable copy in the media bucket (`certificates/signatures/<id>/`), written only at
    # issuance by someone with an account (services/certificate_signatures.py). Never the
    # account's own `users.signature_url`, never deleted, outside `canonical()`.
    signature_director_url: Mapped[str|None]=mapped_column(Text)
    signature_instructor_url: Mapped[str|None]=mapped_column(Text)
    # 023_certificate_text_overrides.sql — the fixed phrases reworded at issuance by someone with
    # an account ({phrase key: text}, keys from the template's `editable_strings`). NULL = the
    # template's own. Outside `canonical()`; every later render by folio prints these.
    text_overrides: Mapped[dict|None]=mapped_column(JSONB)

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
    # 008f_leader_verification.sql (E7): copy of the AUTHORIZED church letter in
    # force, so `rbac.is_verified_leader` is a pure function. Written ONLY by
    # app/services/church_letters.py and app/services/memberships.py.
    leader_verified_until: Mapped[date | None] = mapped_column(Date)
    # 008g_notify_progress.sql (E9): switches OFF the portfolio progress e-mails.
    # Security, invitation, consent and membership decisions are never silenced.
    notify_progress: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # 014_profiles.sql (Bloque G): the public profile. `handle` is filled by a database
    # trigger on insert (from the e-mail's local part), so no sign-up path sets it.
    handle: Mapped[str | None] = mapped_column(String(32), unique=True, server_default=FetchedValue())
    bio: Mapped[str | None] = mapped_column(String(280))
    cover_url: Mapped[str | None] = mapped_column(Text)
    profile_visibility: Mapped[str] = mapped_column(String(10), server_default="private")
    guardian_allows_avatar: Mapped[bool] = mapped_column(Boolean, server_default="false")
    handle_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 018_onboarding.sql: the first-use guide (/bienvenida) was finished or skipped. NULL = not yet.
    # Written only by `PATCH /users/me/onboarding`.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 020_signatures.sql: the saved handwritten signature (public media bucket, `signatures/`).
    # Written only by `POST|DELETE /users/me/signature`; only its owner ever reads it.
    signature_url: Mapped[str | None] = mapped_column(Text)
    # 024_master_guide_catalog.sql: the ministry and club chosen in the shell's selector.
    # Preferences, never permissions: written only by `PATCH /users/me/preferences`, which
    # accepts only what `app/services/ministry_context.py` offers. NULL = the default.
    active_ministry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ministries.id", ondelete="SET NULL")
    )
    active_club_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL")
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
    # NULL only on a program enrollment (012_programs.sql); see `program_id` below.
    honor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
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
    # 009c_course_enrollment.sql — mode COURSE: `course_id` is NOT NULL exactly then (CHECK).
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", use_alter=True)
    )
    course_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the instructor removed them; cleared when they join a course again.
    course_removed_reason: Mapped[str | None] = mapped_column(Text)
    # 010_exams.sql — accessibility: extra time on exams (0, 25, 50 or 100 %).
    exam_extra_time_percent: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    # 012_programs.sql (Bloque F) — the enrollment is in an honor OR in a program, never in
    # both nor in neither (CHECK honor_enrollments_one_curriculum_check). That is the ONLY
    # reason `honor_id` above is nullable in the database.
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id", use_alter=True)
    )


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
    # 012_programs.sql (Bloque F) — copied at enrollment like `is_practical`, so a later
    # change to the catalogue cannot alter an enrollment in progress.
    kind: Mapped[str] = mapped_column(String(8), server_default="FREE")
    program_requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_requirements.id", ondelete="SET NULL", use_alter=True)
    )
    # WHICH achievement completed this requirement automatically (F2).
    satisfied_by_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("honor_enrollments.id", ondelete="SET NULL")
    )


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
    # 010_exams.sql — exam parameters. Content: reviewed with the rest and frozen on publish.
    exam_passing_score: Mapped[int] = mapped_column(Integer, server_default="80")
    exam_time_limit_minutes: Mapped[int | None] = mapped_column(Integer)
    max_exam_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    exam_mode: Mapped[str] = mapped_column(String(10), server_default="ONLINE")
    # Operational: the code an instructor dictates in the classroom (Bloque C · I6).
    # `session_code` is the plain column `010` created and I6 left unused (always NULL):
    # the code is a shared secret, so what is stored is the SHA-256 of
    # "<course_id>:<CODE>" in `session_code_hash` (010b_exam_session_code.sql).
    session_code: Mapped[str | None] = mapped_column(String(8))
    session_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_code_hash: Mapped[str | None] = mapped_column(String(64))


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


class Notification(Base):
    """017_notifications.sql — «Avisos», the in-app inbox (the bell).

    One row per notice and person. `notification_log` records the e-mails and carries the
    12-hour cap; this is what the person reads in the app. While a row is unread, a new
    notice of the same kind about the same entity updates it (`count` goes up) instead of
    piling up rows. `link` is always a relative path of the app.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer, server_default="1")
    data: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
# 010_exams.sql: the course's own question bank, the attempts and their paper.
# The bank belongs to the COURSE and not to `honor_questions`, which hangs from
# a requirement row shared by every course of that honor version and language.
# ---------------------------------------------------------------------------


class CourseQuestion(Base):
    __tablename__ = "course_questions"
    # A question never outlives the position it evaluates. Declared here as well as in the
    # migration so the unit of work inserts the plan before its bank.
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "requirement_position"],
            ["course_requirements.course_id", "course_requirements.requirement_position"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    requirement_position: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    question_text: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(20))
    options: Mapped[list | None] = mapped_column(JSONB)
    # Never leaves the server towards a member: see app/schemas/exam.py.
    correct_answer: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, server_default="1")
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("honor_enrollments.id", ondelete="CASCADE")
    )
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("courses.id"))
    # Denormalised for the queues and the attempt limit.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    attempt_no: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(16), server_default="IN_PROGRESS")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Server time only: the clock of an exam is never the browser's.
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_limit_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    passing_score: Mapped[int] = mapped_column(SmallInteger)
    points_total: Mapped[int] = mapped_column(Integer, server_default="0")
    points_awarded: Mapped[int | None] = mapped_column(Integer)
    score_percent: Mapped[int | None] = mapped_column(SmallInteger)
    # What this attempt completed, so voiding it reverts exactly that and nothing else.
    completed_positions: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default="{}")
    proctored: Mapped[bool] = mapped_column(Boolean, server_default="false")
    auto_submitted: Mapped[bool] = mapped_column(Boolean, server_default="false")
    voided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)


class ExamAnswer(Base):
    """The paper: one row per question drawn, written when the attempt starts."""

    __tablename__ = "exam_answers"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("course_questions.id")
    )
    requirement_position: Mapped[int] = mapped_column(Integer)
    # The permutation the options were shuffled with, so the paper reads the same on resume.
    option_order: Mapped[list | None] = mapped_column(JSONB)
    response: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    points_possible: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    points_awarded: Mapped[int | None] = mapped_column(SmallInteger)
    graded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grader_note: Mapped[str | None] = mapped_column(Text)


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


# ---------------------------------------------------------------------------
# 012_programs.sql (Bloque F): the catalogue of PROGRAMS — classes, Master Guide,
# EMC, CMJA — parallel to `honors` so that not one honors query has to change.
# The engine (enrollment, progress, evidence, review, certificate) is block A's.
# ---------------------------------------------------------------------------


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ministry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ministries.id"))
    kind: Mapped[str] = mapped_column(String(12))
    slug: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    # Which manual this text follows (D2): GC, NAD, IAD, SAD…
    authority: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), server_default="DRAFT")
    # D3: CLUB = the club director invests; ASSOCIATION = the Association does (F4).
    issuer_level: Mapped[str] = mapped_column(String(12), server_default="CLUB")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(40))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProgramTranslation(Base):
    """`programs.name` is the source text (Spanish); every other language is one row here.
    The name of a class changes by territory (Orientador / Pioneiro / Ranger)."""

    __tablename__ = "program_translations"

    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProgramSection(Base):
    __tablename__ = "program_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    slug: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProgramSectionTranslation(Base):
    __tablename__ = "program_section_translations"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sections.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProgramRequirement(Base):
    """The STRUCTURE of a requirement: one row, the same in every language.
    `position` is global inside the program and is block A's `requirement_position`."""

    __tablename__ = "program_requirements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE")
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sections.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(8), server_default="FREE")
    evidence_required: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # HONOR: a concrete honor (any version of its lineage)…
    target_honor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("honors.id"))
    # …or an open slot: a category, or nothing at all ("an honor of your choice").
    target_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("honor_categories.id")
    )
    target_program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id")
    )
    target_quantity: Mapped[float | None] = mapped_column(Numeric(5, 1))
    activity_category: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivityLog(Base):
    """013_activity_logs.sql (Bloque F · F2) — service hours and attendance.

    The member records their own (SUBMITTED) or the director records the club's (APPROVED);
    only an approved row counts towards a `HOURS` requirement. `description` and `place` are
    where a minor was and when: they are read with `can_view_portfolio` and never public.
    """

    __tablename__ = "activity_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    club_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(12))
    performed_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[float] = mapped_column(Numeric(4, 1))
    description: Mapped[str] = mapped_column(String(500))
    place: Mapped[str | None] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(10), server_default="SUBMITTED")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 015_secretaria.sql (Bloque H): the meeting whose list produced this attendance. At most
    # one row per meeting and person (partial unique index); NULL for everything else.
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("club_meetings.id", ondelete="SET NULL")
    )


class XpAward(Base):
    """014_profiles.sql (Bloque G §4.1) — points a club's staff awards a member.

    Never deleted: a mistake is corrected with another, negative row. `conducta`,
    `puntualidad` and `uniforme` feed the «barra de buena conducta».
    """

    __tablename__ = "xp_awards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    awarded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(15))
    points: Mapped[int] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(String(200))
    occurred_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProgramRequirementText(Base):
    __tablename__ = "program_requirement_texts"

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_requirements.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 015_secretaria.sql (Bloque H): the club's officers and «pasar lista».
# ---------------------------------------------------------------------------


class ClubOfficer(Base):
    """A cargo of the club. A TITLE, never a permission: permissions keep coming from
    `club_memberships.role` / `users.role`. Never deleted: closed with `until`."""

    __tablename__ = "club_officers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("club_memberships.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(12))
    custom_title: Mapped[str | None] = mapped_column(String(60))
    since: Mapped[date] = mapped_column(Date)
    until: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClubMeeting(Base):
    __tablename__ = "club_meetings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    held_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(12))
    title: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClubAttendance(Base):
    """The status of one member in one meeting. What earns XP is the `activity_logs` row
    that a PRESENT entry keeps in step (`app/services/attendance.py`)."""

    __tablename__ = "club_attendance"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("club_meetings.id", ondelete="CASCADE"), primary_key=True
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("club_memberships.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(10))
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ----------------------------------------------------------------------------
# Bloque I §1 (026_role_assignments.sql): several scoped roles per person, and the
# nominal invitations that grant them.
# ----------------------------------------------------------------------------
class OrgInvitation(Base):
    __tablename__ = "org_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    role: Mapped[str] = mapped_column(String(40))
    # Always nominal: only the account with this e-mail may accept it.
    email: Mapped[str] = mapped_column(CITEXT)
    # SHA-256 of the token shown once to whoever created (or resent) it.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleAssignment(Base):
    """One scoped role of one person. `users.role` / `users.organization_id` mirror the
    `is_primary` row; `app/services/role_assignments.py` is the only writer."""

    __tablename__ = "role_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(40))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(10), server_default="ACTIVE")
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source: Mapped[str] = mapped_column(String(20), server_default="ADMIN")
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_invitations.id", ondelete="SET NULL")
    )
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(40))


# ---------------------------------------------------------------------------
# 025_events.sql — eventos y puntajes (spec 2026-09-24-eventos §3). The rules live in
# app/services/event_access.py (who), event_scoring.py (how much) and event_scores.py (totals).
# ---------------------------------------------------------------------------
from decimal import Decimal  # noqa: E402  (kept with its block to spare merge conflicts)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    ministry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ministries.id"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    venue: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), server_default="DRAFT")
    registration_closes_on: Mapped[date | None] = mapped_column(Date)
    rules_version: Mapped[int] = mapped_column(Integer, server_default="1")
    honor_bands: Mapped[list] = mapped_column(JSONB, server_default="[]")
    # NULL = no floor. The displayed total is max(raw total, total_floor); honours use it.
    total_floor: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    source_note: Mapped[str | None] = mapped_column(Text)
    template_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventActivity(Base):
    __tablename__ = "event_activities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_activities.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    max_points: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(10), server_default="READY")
    counts_to_total: Mapped[bool] = mapped_column(Boolean, server_default="true")
    schedule_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventAdjustmentType(Base):
    __tablename__ = "event_adjustment_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(8))
    label: Mapped[str] = mapped_column(String(200))
    # FIXED: `points` applied as-is (NULL = to define). FREE: points per adjustment, <= max_points.
    amount_mode: Mapped[str] = mapped_column(String(5), server_default="FIXED")
    points: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    max_points: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    max_per_event: Mapped[int | None] = mapped_column(Integer)
    max_per_club: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventStaff(Base):
    """COORDINATOR / JUDGE of ONE event. Contextual: never touches `users.role`."""

    __tablename__ = "event_staff"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(12))
    activity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_activities.id", ondelete="CASCADE")
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class EventRegistration(Base):
    __tablename__ = "event_registrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE")
    )
    club_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(10), server_default="REGISTERED")
    pass_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    finalist_flags: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    # Manual tiebreak (coordination only): lower first among equal totals, NULL last.
    tiebreak_rank: Mapped[int | None] = mapped_column(Integer)
    registered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="CASCADE")
    )
    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_activities.id", ondelete="CASCADE")
    )
    inputs: Mapped[dict] = mapped_column(JSONB)
    points: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    breakdown: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    rules_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), server_default="CONFIRMED")
    judge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # JUDGE, or COORDINATION when coordination captured it (a missing judge).
    captured_as: Mapped[str] = mapped_column(String(12), server_default="JUDGE")
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvaluationRevision(Base):
    """Immutable (a trigger refuses UPDATE): the values an evaluation had BEFORE a change."""

    __tablename__ = "evaluation_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    inputs: Mapped[dict] = mapped_column(JSONB)
    points: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    status: Mapped[str] = mapped_column(String(10))
    rules_version: Mapped[int] = mapped_column(Integer)
    judge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(Text)
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventAdjustment(Base):
    __tablename__ = "event_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="CASCADE")
    )
    activity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_activities.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(8))
    adjustment_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_adjustment_types.id", ondelete="SET NULL")
    )
    points: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    reason: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
