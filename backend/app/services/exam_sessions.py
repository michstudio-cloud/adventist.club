"""Bloque C · I6 — The "light proctoring" of the spec (§4.6): a short-lived code the
instructor dictates in the classroom.

This is deliberately the ONLY integrity measure beyond the pledge: no camera, no
microphone, no screen capture, no browser lockdown, no tab-change detection, no device
fingerprint and no geolocation. All of those are invasive for a minor and unreliable.

What the code is, and what it is not:

  * six characters from an alphabet without 0/O/1/I, so an instructor can dictate it out
    loud and a child can type it without ambiguity;
  * stored as `sha256("<course_id>:<CODE>")` and never in clear (`010b`), so reading the
    course row does not hand anybody the code, and the same code in two courses has two
    different hashes: a code is worth exactly one course;
  * short lived (15–240 minutes) and replaceable — opening another session substitutes the
    previous one, `DELETE` closes it;
  * rate limited on failures: five wrong codes in ten minutes and the member waits, so six
    characters cannot be guessed by brute force. The counter is the audit trail itself
    (`EXAM_SESSION_CODE_FAILED`), which is where an attempt to guess belongs anyway.

The history of sessions lives in `audit_log` (`EXAM_SESSION_OPEN` / `_CLOSE`) and no table
was needed. The code itself is never written to the audit row.
"""
import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Course, User
from app.schemas.exam import ExamSessionIn, ExamSessionOut
from app.security import utcnow
from app.services.audit import record_audit
from app.workflow import PUBLISHED

# No 0/O and no 1/I: the code is read out loud in a room.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 6

# §4.6: five wrong codes in ten minutes and the member waits.
MAX_FAILED_CODES = 5
FAILURE_WINDOW = timedelta(minutes=10)
FAILED_ACTION = "EXAM_SESSION_CODE_FAILED"

ENTITY = "COURSE"
_random = secrets.SystemRandom()


def hash_code(course_id, code: str) -> str:
    """Salted with the course id: a hash never matches another course's code."""
    return hashlib.sha256(f"{course_id}:{code.strip().upper()}".encode()).hexdigest()


def _generate() -> str:
    return "".join(_random.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


# ----------------------------------------------------------------------------
# The instructor's side
# ----------------------------------------------------------------------------
async def open_session(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: ExamSessionIn, request: Request | None
) -> ExamSessionOut:
    """Open (or replace) the session of a published course. The code is returned once and
    then only its hash exists."""
    from app.services import courses as courses_service

    course = await courses_service._get_course(db, course_id, lock=True)
    await courses_service._require_author(db, actor, course)
    await courses_service._require_verified(db, actor)
    if course.status != PUBLISHED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Solo un curso publicado abre sesiones de examen"
        )

    code = _generate()
    expires_at = utcnow() + timedelta(minutes=payload.minutes)
    course.session_code_hash = hash_code(course.id, code)
    course.session_code_expires_at = expires_at
    course.updated_at = utcnow()
    record_audit(
        db,
        action="EXAM_SESSION_OPEN",
        entity_type=ENTITY,
        entity_id=course.id,
        actor=actor,
        # Never the code: the audit trail says a session was opened, not how to enter it.
        metadata={"minutes": payload.minutes, "expires_at": expires_at.isoformat()},
        request=request,
    )
    await db.commit()
    return ExamSessionOut(code=code, expires_at=expires_at)


async def close_session(
    db: AsyncSession, actor: User, course_id: uuid.UUID, request: Request | None
) -> None:
    from app.services import courses as courses_service

    course = await courses_service._get_course(db, course_id, lock=True)
    await courses_service._require_author(db, actor, course)
    course.session_code_hash = None
    course.session_code_expires_at = None
    course.updated_at = utcnow()
    record_audit(
        db,
        action="EXAM_SESSION_CLOSE",
        entity_type=ENTITY,
        entity_id=course.id,
        actor=actor,
        request=request,
    )
    await db.commit()


# ----------------------------------------------------------------------------
# The member's side: one code, checked once, at the start of an attempt
# ----------------------------------------------------------------------------
async def _recent_failures(db: AsyncSession, actor: User) -> int:
    stmt = select(func.count()).where(
        AuditLog.action == FAILED_ACTION,
        AuditLog.user_id == actor.id,
        AuditLog.created_at > utcnow() - FAILURE_WINDOW,
    )
    return (await db.execute(stmt)).scalar_one()


async def _record_failure(
    db: AsyncSession, actor: User, course: Course, request: Request | None
) -> None:
    """Committed on its own, because the request it belongs to is about to fail: a counter
    that rolls back with the rejection would count nothing."""
    record_audit(
        db,
        action=FAILED_ACTION,
        entity_type=ENTITY,
        entity_id=course.id,
        actor=actor,
        request=request,
    )
    await db.commit()


async def verify_code(
    db: AsyncSession,
    actor: User,
    course: Course,
    code: str | None,
    request: Request | None,
) -> bool:
    """Returns whether the attempt about to start is `proctored`.

    In IN_PERSON the code is required (403 without it); in ONLINE it is optional and only
    decides the `proctored` flag. A wrong code is always a 403 and never says *why* — a
    message distinguishing "expired" from "wrong" is a hint to whoever is guessing.
    """
    if code is None:
        if course.exam_mode != "ONLINE":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Este examen es presencial: pide a tu instructor el código de la sesión",
            )
        return False

    if await _recent_failures(db, actor) >= MAX_FAILED_CODES:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiados códigos incorrectos. Espera unos minutos y vuelve a intentarlo.",
        )
    live = (
        course.session_code_hash is not None
        and course.session_code_expires_at is not None
        and course.session_code_expires_at > utcnow()
        and secrets.compare_digest(course.session_code_hash, hash_code(course.id, code))
    )
    if not live:
        await _record_failure(db, actor, course, request)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "El código de la sesión no es válido o ya venció"
        )
    return True
