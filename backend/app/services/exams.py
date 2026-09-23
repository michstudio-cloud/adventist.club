"""Bloque C · I5 — Exam attempts: drawing, autosave, resume, deadlines and grading.

    attempt   IN_PROGRESS -> PASSED | FAILED | PENDING_GRADING            (-> VOIDED in I6)

Everything that decides anything happens on the server:
  * the draw and the shuffle are made here with `secrets.SystemRandom` and PERSISTED, so the
    client never chooses which questions it gets nor in which order;
  * the clock is `utcnow()` against `exam_attempts.deadline_at`, never a time the browser sends;
  * the correct answers never travel towards a member before decision D5 allows it, and the
    schema of the paper does not even declare the fields (app/schemas/exam.py).

Integrity rules (spec §4.9):
  1. One open attempt per enrollment (partial unique index); VOIDED attempts do not count.
  2. The paper never changes after it is created; a published course is immutable.
  3. `completed_via = 'EXAM'` is written only here, and only over `EXAM` requirements of the plan.
  4. Nothing is written on a CERTIFIED or WITHDRAWN enrollment (rule 3 of A).
  5. Nobody grades their own attempt (the instructor cannot be enrolled in their own course).

There are no background jobs (the API runs on 0,15 CPU): an expired attempt is closed lazily,
by `finalize_if_expired`, the first time anybody touches it.
"""
import secrets
import unicodedata
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Course,
    CourseQuestion,
    CourseRequirement,
    ExamAnswer,
    ExamAttempt,
    Guardianship,
    HonorEnrollment,
    RequirementProgress,
    User,
)
from app.rbac import (
    CONSENT_GRANTED,
    can_grade_attempt,
    can_view_enrollment,
    course_author_in_good_standing,
    is_course_instructor,
    is_master,
)
from app.schemas.exam import (
    AnswerFeedbackOut,
    AnswerIn,
    AnswerSaved,
    AnswerSolutionOut,
    AttemptOut,
    AttemptPaper,
    AttemptResult,
    AttemptSolution,
    AttemptStart,
    ExamStateOut,
    ExtraTimeIn,
    GradeIn,
    GradingQueueItem,
    PaperQuestionOut,
    RequirementScore,
    VoidIn,
)
from app.schemas.portfolio import PersonRef
from app.security import utcnow
from app.services import auto_certificate, course_enrollment, exam_sessions, portfolio
from app.services.audit import record_audit
from app.services.courses import EXAM

IN_PROGRESS, PENDING_GRADING, PASSED, FAILED, VOIDED = (
    "IN_PROGRESS", "PENDING_GRADING", "PASSED", "FAILED", "VOIDED",
)
CLOSED = (PENDING_GRADING, PASSED, FAILED, VOIDED)
# What a member may still be answering after the deadline, so a click that was already on
# its way does not lose an answer to a network hiccup.
GRACE_SECONDS = 30
# Without a time limit the attempt still has to end at some point, or it would hold the one
# open slot for ever (spec §4.1).
NO_LIMIT_WINDOW_HOURS = 72
AUTO_GRADED = {"MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER"}
SHORT_ANSWER_SEPARATOR = "|"

ATTEMPT, ENROLLMENT = "EXAM_ATTEMPT", "ENROLLMENT"
_random = secrets.SystemRandom()


# ----------------------------------------------------------------------------
# Lookups and guards
# ----------------------------------------------------------------------------
async def _get_attempt(db: AsyncSession, attempt_id: uuid.UUID, lock: bool = False) -> ExamAttempt:
    stmt = select(ExamAttempt).where(ExamAttempt.id == attempt_id)
    if lock:
        stmt = stmt.with_for_update()
    attempt = (await db.execute(stmt)).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intento no encontrado")
    return attempt


def _require_owner(actor: User, attempt: ExamAttempt) -> None:
    if attempt.user_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Este intento no es tuyo")


async def _live_course(db: AsyncSession, enrollment: HonorEnrollment) -> Course:
    """The course must still be able to hold an exam: published or archived by its own
    instructor, never withdrawn by a reviewer, and with the letter in force."""
    course = await db.get(Course, enrollment.course_id) if enrollment.course_id else None
    if course is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta inscripción no pertenece a un curso")
    if course.status not in ("PUBLISHED", "ARCHIVED") or course.archived_by_authority:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El curso fue retirado y su examen está cerrado"
        )
    instructor = await db.get(User, course.instructor_id)
    if not await course_author_in_good_standing(db, instructor):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El instructor del curso no tiene su carta autorizada en este momento",
        )
    return course


async def _exam_positions(db: AsyncSession, enrollment: HonorEnrollment) -> list[int]:
    plan = await portfolio.course_plan(db, enrollment)
    return sorted(position for position, assessment in plan.items() if assessment == EXAM)


async def _pending_positions(
    db: AsyncSession, enrollment: HonorEnrollment, positions: list[int]
) -> list[int]:
    if not positions:
        return []
    stmt = select(RequirementProgress.requirement_position).where(
        RequirementProgress.enrollment_id == enrollment.id,
        RequirementProgress.requirement_position.in_(positions),
        RequirementProgress.status != portfolio.COMPLETE,
    )
    return sorted((await db.execute(stmt)).scalars().all())


async def _attempts_of(db: AsyncSession, enrollment_id: uuid.UUID) -> list[ExamAttempt]:
    stmt = (
        select(ExamAttempt)
        .where(ExamAttempt.enrollment_id == enrollment_id)
        .order_by(ExamAttempt.attempt_no)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _answers_of(db: AsyncSession, attempt_id: uuid.UUID) -> list[ExamAnswer]:
    stmt = (
        select(ExamAnswer)
        .where(ExamAnswer.attempt_id == attempt_id)
        .order_by(ExamAnswer.position)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _questions_by_id(
    db: AsyncSession, answers: list[ExamAnswer]
) -> dict[uuid.UUID, CourseQuestion]:
    ids = {answer.question_id for answer in answers}
    if not ids:
        return {}
    rows = (await db.execute(select(CourseQuestion).where(CourseQuestion.id.in_(ids)))).scalars()
    return {row.id: row for row in rows}


# ----------------------------------------------------------------------------
# The clock. Server time, always.
# ----------------------------------------------------------------------------
def _deadline(started, minutes: int | None, extra_percent: int):
    if minutes is None:
        return started + timedelta(hours=NO_LIMIT_WINDOW_HOURS), None
    effective = (minutes * (100 + extra_percent)) // 100
    return started + timedelta(minutes=effective), effective


def _remaining(attempt: ExamAttempt) -> int | None:
    if attempt.status != IN_PROGRESS:
        return None
    return max(0, int((attempt.deadline_at - utcnow()).total_seconds()))


def _expired(attempt: ExamAttempt) -> bool:
    return (
        attempt.status == IN_PROGRESS
        and utcnow() > attempt.deadline_at + timedelta(seconds=GRACE_SECONDS)
    )


async def finalize_if_expired(
    db: AsyncSession, attempt: ExamAttempt, request: Request | None = None
) -> ExamAttempt:
    """Lazy expiry (hallazgo 8): every read and every write of an attempt goes through here,
    and there is no cron anywhere in this design."""
    if not _expired(attempt):
        return attempt
    attempt.auto_submitted = True
    await _close(db, attempt, submitted_at=attempt.deadline_at, actor=None, request=request)
    await db.commit()
    return attempt


# ----------------------------------------------------------------------------
# Starting an attempt
# ----------------------------------------------------------------------------
async def start_attempt(
    db: AsyncSession,
    actor: User,
    enrollment_id: uuid.UUID,
    payload: AttemptStart,
    request: Request | None,
) -> AttemptPaper:
    """§4.2. Idempotent: while an attempt is open, starting again answers with that one."""
    enrollment = await portfolio._get_enrollment(db, enrollment_id, lock=True)
    _require_enrollment_owner(actor, enrollment)
    portfolio._require_open(enrollment)  # rule 4: nothing is written on a frozen enrollment
    if enrollment.mode != "COURSE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta inscripción no se cursa en un curso")
    course = await _live_course(db, enrollment)

    open_attempt = next(
        (a for a in await _attempts_of(db, enrollment.id) if a.status == IN_PROGRESS), None
    )
    if open_attempt is not None:
        await finalize_if_expired(db, open_attempt, request)
        if open_attempt.status == IN_PROGRESS:
            return await _paper(db, open_attempt)

    positions = await _exam_positions(db, enrollment)
    if not positions:
        raise HTTPException(status.HTTP_409_CONFLICT, "Este curso no evalúa nada con un examen")
    pending = await _pending_positions(db, enrollment, positions)
    if not pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya completaste la parte teórica del curso")

    attempts = await _attempts_of(db, enrollment.id)
    if any(attempt.status == PENDING_GRADING for attempt in attempts):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Tu instructor todavía está revisando tu intento anterior"
        )
    used = [attempt for attempt in attempts if attempt.status != VOIDED]
    if len(used) >= course.max_exam_attempts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Ya usaste los {course.max_exam_attempts} intentos de este examen",
        )
    if not payload.pledge:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Acepta la promesa «haré este examen por mí mismo» antes de empezar",
        )
    # Last check, and the only one that can write before failing (the failure counter):
    # everything cheaper has already said yes, so a wrong code costs one audit row.
    proctored = await exam_sessions.verify_code(
        db, actor, course, payload.session_code, request
    )

    now = utcnow()
    deadline, effective_minutes = _deadline(
        now, course.exam_time_limit_minutes, enrollment.exam_extra_time_percent
    )
    attempt = ExamAttempt(
        id=uuid.uuid4(),
        enrollment_id=enrollment.id,
        course_id=course.id,
        user_id=actor.id,
        attempt_no=max((a.attempt_no for a in attempts), default=0) + 1,
        status=IN_PROGRESS,
        started_at=now,
        deadline_at=deadline,
        time_limit_minutes=effective_minutes,
        passing_score=course.exam_passing_score,
        points_total=0,
        completed_positions=[],
        proctored=proctored,
        auto_submitted=False,
    )
    db.add(attempt)
    await db.flush()
    attempt.points_total = await _draw(db, attempt, course, pending)
    record_audit(
        db,
        action="EXAM_START",
        entity_type=ATTEMPT,
        entity_id=attempt.id,
        actor=actor,
        metadata={"enrollment_id": str(enrollment.id), "course_id": str(course.id),
                  "attempt_no": attempt.attempt_no, "proctored": attempt.proctored,
                  "positions": pending},
        request=request,
    )
    await db.commit()
    return await _paper(db, attempt)


def _require_enrollment_owner(actor: User, enrollment: HonorEnrollment) -> None:
    if enrollment.user_id != actor.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo quien cursa la especialidad rinde su examen"
        )


async def _draw(
    db: AsyncSession, attempt: ExamAttempt, course: Course, positions: list[int]
) -> int:
    """`draw_count` questions per pending EXAM requirement, preferring the ones this member
    has not seen yet, then shuffled — questions and options — and written down. The paper is
    settled here and never moves again."""
    plan = {
        row.requirement_position: row
        for row in (
            await db.execute(
                select(CourseRequirement).where(CourseRequirement.course_id == course.id)
            )
        ).scalars()
    }
    seen = set(
        (
            await db.execute(
                select(ExamAnswer.question_id)
                .join(ExamAttempt, ExamAttempt.id == ExamAnswer.attempt_id)
                .where(ExamAttempt.enrollment_id == attempt.enrollment_id)
            )
        ).scalars()
    )
    drawn: list[CourseQuestion] = []
    for position in positions:
        row = plan.get(position)
        if row is None or row.draw_count < 1:
            continue
        bank = list(
            (
                await db.execute(
                    select(CourseQuestion)
                    .where(
                        CourseQuestion.course_id == course.id,
                        CourseQuestion.requirement_position == position,
                    )
                    .order_by(CourseQuestion.position, CourseQuestion.id)
                )
            ).scalars()
        )
        if len(bank) < row.draw_count:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"El banco del requisito {position} no alcanza para sortear el examen",
            )
        fresh = [question for question in bank if question.id not in seen]
        repeated = [question for question in bank if question.id in seen]
        _random.shuffle(fresh)
        _random.shuffle(repeated)
        drawn += (fresh + repeated)[: row.draw_count]

    _random.shuffle(drawn)
    total = 0
    for index, question in enumerate(drawn, start=1):
        option_order = None
        if question.options:
            option_order = list(range(len(question.options)))
            _random.shuffle(option_order)
        db.add(
            ExamAnswer(
                attempt_id=attempt.id,
                position=index,
                question_id=question.id,
                requirement_position=question.requirement_position,
                option_order=option_order,
                points_possible=question.points,
            )
        )
        total += question.points
    if not drawn:
        raise HTTPException(status.HTTP_409_CONFLICT, "El examen de este curso no tiene preguntas")
    return total


# ----------------------------------------------------------------------------
# Answering
# ----------------------------------------------------------------------------
async def save_answer(
    db: AsyncSession,
    actor: User,
    attempt_id: uuid.UUID,
    position: int,
    payload: AnswerIn,
    request: Request | None,
) -> AnswerSaved:
    """Autosave of ONE answer. Navigating back and forth is free until the attempt is handed
    in; past the deadline (plus the grace) the attempt closes with whatever was saved."""
    attempt = await _get_attempt(db, attempt_id, lock=True)
    _require_owner(actor, attempt)
    await finalize_if_expired(db, attempt, request)
    if attempt.status != IN_PROGRESS:
        raise HTTPException(status.HTTP_409_CONFLICT, "El examen ya está cerrado")

    answer = await db.get(ExamAnswer, {"attempt_id": attempt.id, "position": position})
    if answer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa pregunta no está en tu examen")
    question = await db.get(CourseQuestion, answer.question_id)
    answer.response = _normalize_response(question, answer, payload.response)
    answer.answered_at = utcnow()
    await db.commit()
    return AnswerSaved(
        position=position, answered_at=answer.answered_at, remaining_seconds=_remaining(attempt)
    )


def _normalize_response(
    question: CourseQuestion, answer: ExamAnswer, response: str | None
) -> str | None:
    """What travels in is what the member sees; what is stored is what grading needs."""
    if response is None or not response.strip():
        return None
    value = response.strip()
    if question.question_type == "MULTIPLE_CHOICE":
        order = list(answer.option_order or [])
        try:
            shown = int(value)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Responde con el número de la opción elegida",
            )
        if not 0 <= shown < len(order):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Esa opción no existe en la pregunta"
            )
        return str(order[shown])  # the ORIGINAL index: the shuffle stays on the server
    if question.question_type == "TRUE_FALSE":
        if value.lower() not in ("true", "false"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Responde «true» o «false»"
            )
        return value.lower()
    return value


# ----------------------------------------------------------------------------
# Grading
# ----------------------------------------------------------------------------
def normalize_text(value: str) -> str:
    """Lowercase, without accents, without punctuation and with single spaces: a child who
    writes «Nudo llano.» answered the same thing as «nudo llano»."""
    folded = unicodedata.normalize("NFKD", value.strip().lower())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    kept = [char if (char.isalnum() or char.isspace()) else " " for char in folded]
    return " ".join("".join(kept).split())


def grade_answer(question: CourseQuestion, answer: ExamAnswer) -> bool | None:
    """True / False, or None when only a person can decide (§4.4)."""
    if question.question_type not in AUTO_GRADED:
        return None  # ESSAY: always a person, with the rubric in front of them
    if answer.response is None:
        return False  # left blank: nothing to interpret
    if question.question_type == "MULTIPLE_CHOICE":
        options = list(question.options or [])
        try:
            chosen = options[int(answer.response)]
        except (ValueError, IndexError):
            return False
        return chosen == question.correct_answer
    if question.question_type == "TRUE_FALSE":
        return answer.response.strip().lower() == question.correct_answer.strip().lower()
    # SHORT_ANSWER: a miss is NOT a mistake. A child may have written something valid the
    # instructor did not foresee, so it waits for a person instead of being marked wrong.
    accepted = {
        normalize_text(part)
        for part in question.correct_answer.split(SHORT_ANSWER_SEPARATOR)
        if part.strip()
    }
    return True if normalize_text(answer.response) in accepted else None


async def _close(
    db: AsyncSession,
    attempt: ExamAttempt,
    *,
    submitted_at,
    actor: User | None,
    request: Request | None,
    background=None,
) -> None:
    """Grade what a machine can, resolve the three outcomes of §4.4 and, if it passed,
    complete the theoretical requirements. Staged on the caller's session."""
    answers = await _answers_of(db, attempt.id)
    questions = await _questions_by_id(db, answers)
    for answer in answers:
        question = questions.get(answer.question_id)
        if question is None or answer.points_awarded is not None:
            continue
        verdict = grade_answer(question, answer)
        if verdict is None:
            continue  # waits for the instructor (Bloque C · I6)
        answer.is_correct = verdict
        answer.points_awarded = answer.points_possible if verdict else 0

    attempt.submitted_at = attempt.submitted_at or submitted_at
    await _settle(
        db, attempt, answers=answers, actor=actor, request=request, background=background
    )
    record_audit(
        db,
        action="EXAM_SUBMIT",
        entity_type=ATTEMPT,
        entity_id=attempt.id,
        actor=actor,
        metadata={"status": attempt.status, "auto_submitted": attempt.auto_submitted,
                  "score_percent": attempt.score_percent,
                  "points": [attempt.points_awarded, attempt.points_total],
                  "enrollment_id": str(attempt.enrollment_id)},
        request=request,
    )


async def _settle(
    db: AsyncSession,
    attempt: ExamAttempt,
    *,
    answers: list[ExamAnswer] | None = None,
    actor: User | None,
    request: Request | None,
    background=None,
) -> None:
    """The three outcomes of §4.4, evaluated over whatever is graded RIGHT NOW.

    Called once when the attempt is handed in and again after every manual grade, so the
    attempt closes the moment the result stops depending on what is still pending:
      * what is already awarded reaches the threshold           -> PASSED
      * not even awarding everything pending would reach it     -> FAILED
      * anything else                                           -> PENDING_GRADING

    Integer arithmetic on purpose: a rounded 79,6 % is not 80 %.
    """
    answers = answers if answers is not None else await _answers_of(db, attempt.id)
    awarded = sum(answer.points_awarded or 0 for answer in answers)
    pending = sum(answer.points_possible for answer in answers if answer.points_awarded is None)
    total = attempt.points_total or sum(answer.points_possible for answer in answers)
    attempt.points_awarded = awarded
    attempt.score_percent = (100 * awarded) // total if total else 0

    now = utcnow()
    if awarded * 100 >= attempt.passing_score * total:
        attempt.status, attempt.finished_at = PASSED, now
    elif (awarded + pending) * 100 < attempt.passing_score * total:
        attempt.status, attempt.finished_at = FAILED, now
    else:
        attempt.status, attempt.finished_at = PENDING_GRADING, None
    if attempt.status == PASSED:
        await _complete_exam_requirements(db, attempt, actor, request, background)


async def _complete_exam_requirements(
    db: AsyncSession,
    attempt: ExamAttempt,
    actor: User | None,
    request: Request | None,
    background=None,
) -> None:
    """Rule 3: the ONLY place that writes `completed_via = 'EXAM'`, and only over requirements
    the plan evaluates with the exam. Then rule 2 of A decides whether the enrollment is READY.

    ...and, since I7, automatic issuance hangs from the end of it: inside a SAVEPOINT, only
    when the enrollment has just become READY and not one requirement of the plan is
    practical (spec §5.3, app/services/auto_certificate.py). A failure to issue never
    reaches this far: the savepoint gives back the certificate and keeps the exam.
    """
    enrollment = await portfolio._get_enrollment(db, attempt.enrollment_id, lock=True)
    positions = await _exam_positions(db, enrollment)
    if not positions:
        return
    rows = (
        await db.execute(
            select(RequirementProgress).where(
                RequirementProgress.enrollment_id == enrollment.id,
                RequirementProgress.requirement_position.in_(positions),
                RequirementProgress.status != portfolio.COMPLETE,
            )
        )
    ).scalars().all()
    now = utcnow()
    completed = []
    for row in rows:
        row.status = portfolio.COMPLETE
        row.completed_via = "EXAM"
        row.reviewed_by_id = None  # the exam has no reviewer; the course does
        row.reviewed_at = now
        completed.append(row.requirement_position)
    attempt.completed_positions = sorted(completed)
    await portfolio._touch(db, enrollment)
    await course_enrollment._recalculate_ready(db, enrollment, now)
    if completed:
        record_audit(
            db,
            action="REQUIREMENT_COMPLETE_EXAM",
            entity_type=ENROLLMENT,
            entity_id=enrollment.id,
            actor=actor,
            metadata={"attempt_id": str(attempt.id), "positions": sorted(completed),
                      "enrollment_status": enrollment.status},
            request=request,
        )
    # Bloque D · I7. The LAST thing that happens after a pass, and the only thing here
    # that can fail without consequences: it runs inside its own SAVEPOINT.
    await auto_certificate.maybe_issue(db, enrollment, attempt, actor, request, background)


async def submit_attempt(
    db: AsyncSession,
    actor: User,
    attempt_id: uuid.UUID,
    request: Request | None,
    background=None,
):
    """Hand it in. Idempotent: an attempt already closed answers with its result."""
    attempt = await _get_attempt(db, attempt_id, lock=True)
    _require_owner(actor, attempt)
    await finalize_if_expired(db, attempt, request)
    if attempt.status != IN_PROGRESS:
        return await _result_for_owner(db, attempt)
    await _close(
        db, attempt, submitted_at=utcnow(), actor=actor, request=request, background=background
    )
    await db.commit()
    return await _result_for_owner(db, attempt)


# ----------------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------------
def _base(attempt: ExamAttempt) -> dict:
    return {
        "id": str(attempt.id),
        "enrollment_id": str(attempt.enrollment_id),
        "course_id": str(attempt.course_id),
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "deadline_at": attempt.deadline_at,
        "remaining_seconds": _remaining(attempt),
        "submitted_at": attempt.submitted_at,
        "finished_at": attempt.finished_at,
        "time_limit_minutes": attempt.time_limit_minutes,
        "passing_score": attempt.passing_score,
        "points_total": attempt.points_total,
        "points_awarded": attempt.points_awarded,
        "score_percent": attempt.score_percent,
        "passed": attempt.status == PASSED,
        "auto_submitted": attempt.auto_submitted,
        "proctored": attempt.proctored,
        "void_reason": attempt.void_reason,
    }


def _shown_options(question: CourseQuestion, answer: ExamAnswer) -> list[str] | None:
    if not question.options:
        return None
    order = list(answer.option_order or range(len(question.options)))
    return [question.options[index] for index in order]


def _shown_response(question: CourseQuestion, answer: ExamAnswer) -> str | None:
    """The stored value is the ORIGINAL option index; the member gets back the one they see."""
    if answer.response is None or question.question_type != "MULTIPLE_CHOICE":
        return answer.response
    order = list(answer.option_order or [])
    try:
        return str(order.index(int(answer.response)))
    except (ValueError, IndexError):
        return None


async def _paper(db: AsyncSession, attempt: ExamAttempt) -> AttemptPaper:
    answers = await _answers_of(db, attempt.id)
    questions = await _questions_by_id(db, answers)
    return AttemptPaper(
        **_base(attempt),
        questions=[
            PaperQuestionOut(
                position=answer.position,
                requirement_position=answer.requirement_position,
                question_text=questions[answer.question_id].question_text,
                question_type=questions[answer.question_id].question_type,
                options=_shown_options(questions[answer.question_id], answer),
                points=answer.points_possible,
                response=_shown_response(questions[answer.question_id], answer),
                answered_at=answer.answered_at,
            )
            for answer in answers
            if answer.question_id in questions
        ],
    )


def _breakdown(answers: list[ExamAnswer]) -> list[RequirementScore]:
    grouped: dict[int, list[ExamAnswer]] = {}
    for answer in answers:
        grouped.setdefault(answer.requirement_position, []).append(answer)
    return [
        RequirementScore(
            requirement_position=position,
            questions=len(rows),
            points_total=sum(row.points_possible for row in rows),
            points_awarded=None
            if any(row.points_awarded is None for row in rows)
            else sum(row.points_awarded for row in rows),
        )
        for position, rows in sorted(grouped.items())
    ]


async def _result(db: AsyncSession, attempt: ExamAttempt, *, with_solutions: bool):
    """D5a. `with_solutions` is the ONLY switch that lets a correct answer out, and it builds
    a different schema: the one without solutions cannot carry them."""
    answers = await _answers_of(db, attempt.id)
    questions = await _questions_by_id(db, answers)
    fields = {**_base(attempt), "breakdown": _breakdown(answers)}
    feedback = []
    for answer in answers:
        question = questions.get(answer.question_id)
        if question is None:
            continue
        common = dict(
            position=answer.position,
            requirement_position=answer.requirement_position,
            question_text=question.question_text,
            question_type=question.question_type,
            options=_shown_options(question, answer),
            your_response=_shown_response(question, answer),
            is_correct=answer.is_correct,
            points_possible=answer.points_possible,
            points_awarded=answer.points_awarded,
            grader_note=answer.grader_note,
        )
        feedback.append(
            AnswerSolutionOut(
                **common, correct_answer=question.correct_answer, explanation=question.explanation
            )
            if with_solutions
            else AnswerFeedbackOut(**common)
        )
    if with_solutions:
        return AttemptSolution(**fields, feedback=feedback)
    return AttemptResult(**fields, feedback=feedback)


async def _may_see_solutions(db: AsyncSession, attempt: ExamAttempt) -> bool:
    """Passed, or failed with no attempts left (D5a). While another attempt is possible the
    bank would just become something to memorise."""
    if attempt.status == PASSED:
        return True
    if attempt.status != FAILED:
        return False
    course = await db.get(Course, attempt.course_id)
    used = (
        await db.execute(
            select(func.count()).where(
                ExamAttempt.enrollment_id == attempt.enrollment_id,
                ExamAttempt.status != VOIDED,
            )
        )
    ).scalar_one()
    return course is None or used >= course.max_exam_attempts


async def _result_for_owner(db: AsyncSession, attempt: ExamAttempt):
    if attempt.status == PENDING_GRADING:
        # No score at all: a half-graded mark would be a wrong one.
        return AttemptResult(**{**_base(attempt), "points_awarded": None, "score_percent": None},
                             breakdown=[], feedback=[])
    return await _result(db, attempt, with_solutions=await _may_see_solutions(db, attempt))


# ----------------------------------------------------------------------------
# Reading an attempt
# ----------------------------------------------------------------------------
async def get_attempt(db: AsyncSession, actor: User, attempt_id: uuid.UUID):
    attempt = await _get_attempt(db, attempt_id)
    await finalize_if_expired(db, attempt)
    enrollment = await portfolio._get_enrollment(db, attempt.enrollment_id)
    if attempt.user_id == actor.id:
        if attempt.status == IN_PROGRESS:
            return await _paper(db, attempt)
        return await _result_for_owner(db, attempt)

    if not await can_view_enrollment(db, actor, enrollment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes ver este intento")
    if attempt.status == IN_PROGRESS:
        # Nobody watches over a member's shoulder while they answer.
        return AttemptOut(**_base(attempt))
    if await _reads_the_whole_attempt(db, actor, enrollment):
        return await _result(db, attempt, with_solutions=True)
    # The director and the hierarchy: status and score, never the text a minor wrote (§6).
    return AttemptOut(**_base(attempt))


async def _reads_the_whole_attempt(
    db: AsyncSession, actor: User, enrollment: HonorEnrollment
) -> bool:
    """The member's own guardian, the instructor of the course and MASTER_GC (§4.5)."""
    if is_master(actor) or await is_course_instructor(db, actor, enrollment):
        return True
    consent = select(Guardianship.id).where(
        Guardianship.guardian_id == actor.id,
        Guardianship.child_id == enrollment.user_id,
        Guardianship.consent_status == CONSENT_GRANTED,
    )
    return (await db.execute(consent.limit(1))).scalar_one_or_none() is not None


# ----------------------------------------------------------------------------
# The exam card of an enrollment
# ----------------------------------------------------------------------------
async def exam_state(db: AsyncSession, actor: User, enrollment_id: uuid.UUID) -> ExamStateOut:
    enrollment = await portfolio._get_enrollment(db, enrollment_id)
    if not await can_view_enrollment(db, actor, enrollment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes ver este examen")
    if enrollment.mode != "COURSE" or enrollment.course_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta inscripción no se cursa en un curso")
    course = await db.get(Course, enrollment.course_id)

    attempts = await _attempts_of(db, enrollment.id)
    for attempt in attempts:
        await finalize_if_expired(db, attempt)
    positions = await _exam_positions(db, enrollment)
    pending = await _pending_positions(db, enrollment, positions)
    used = [attempt for attempt in attempts if attempt.status != VOIDED]
    open_attempt = next((a for a in attempts if a.status == IN_PROGRESS), None)
    finished = [a for a in used if a.status in CLOSED]

    blocked = None
    if not positions:
        blocked = "Este curso no evalúa nada con un examen"
    elif not pending:
        blocked = "Ya completaste la parte teórica del curso"
    elif enrollment.status != portfolio.IN_PROGRESS:
        blocked = "La inscripción ya no admite intentos"
    elif any(a.status == PENDING_GRADING for a in attempts):
        blocked = "Tu instructor todavía está revisando tu intento anterior"
    elif len(used) >= course.max_exam_attempts:
        blocked = f"Ya usaste los {course.max_exam_attempts} intentos de este examen"
    return ExamStateOut(
        enrollment_id=str(enrollment.id),
        course_id=str(course.id),
        course_title=course.title,
        exam_mode=course.exam_mode,
        passing_score=course.exam_passing_score,
        time_limit_minutes=course.exam_time_limit_minutes,
        extra_time_percent=enrollment.exam_extra_time_percent,
        max_attempts=course.max_exam_attempts,
        attempts_used=len(used),
        attempts_left=max(course.max_exam_attempts - len(used), 0),
        exam_positions=positions,
        pending_positions=pending,
        open_attempt_id=str(open_attempt.id) if open_attempt else None,
        last_result=AttemptOut(**_base(finished[-1])) if finished else None,
        can_start=blocked is None and open_attempt is None,
        blocked_reason=blocked,
    )


# ----------------------------------------------------------------------------
# Extra time (WCAG 2.2.1)
# ----------------------------------------------------------------------------
async def set_extra_time(
    db: AsyncSession,
    actor: User,
    enrollment_id: uuid.UUID,
    payload: ExtraTimeIn,
    request: Request | None,
) -> ExamStateOut:
    """An adult sets it for themselves, a guardian for their child and the instructor for any
    of their members. It applies to the NEXT attempt: a clock does not stretch mid-exam."""
    enrollment = await portfolio._get_enrollment(db, enrollment_id, lock=True)
    portfolio._require_open(enrollment)
    if enrollment.mode != "COURSE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta inscripción no se cursa en un curso")
    member = await db.get(User, enrollment.user_id)
    own_and_adult = actor.id == enrollment.user_id and not member.is_minor
    allowed = (
        own_and_adult
        or is_master(actor)
        or await is_course_instructor(db, actor, enrollment)
        or await _is_guardian(db, actor, enrollment)
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El tiempo adicional lo activa el propio miembro adulto, su tutor o el instructor",
        )
    open_attempt = next(
        (a for a in await _attempts_of(db, enrollment.id) if a.status == IN_PROGRESS), None
    )
    if open_attempt is not None and not _expired(open_attempt):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Hay un examen en curso: el tiempo adicional se aplica desde el siguiente intento",
        )
    previous = enrollment.exam_extra_time_percent
    enrollment.exam_extra_time_percent = payload.percent
    enrollment.updated_at = utcnow()
    record_audit(
        db,
        action="EXAM_EXTRA_TIME",
        entity_type=ENROLLMENT,
        entity_id=enrollment.id,
        actor=actor,
        metadata={"from": previous, "to": payload.percent},
        request=request,
    )
    await db.commit()
    return await exam_state(db, actor, enrollment_id)


async def _is_guardian(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> bool:
    consent = select(Guardianship.id).where(
        Guardianship.guardian_id == actor.id,
        Guardianship.child_id == enrollment.user_id,
        Guardianship.consent_status == CONSENT_GRANTED,
    )
    return (await db.execute(consent.limit(1))).scalar_one_or_none() is not None


# ----------------------------------------------------------------------------
# Bloque C · I6 — Manual grading
#
# A SHORT_ANSWER that missed the accepted list and every ESSAY wait here. The person who
# decides is the instructor of THAT course, while their letter is authorized, or MASTER_GC:
# the director never grades an exam, because the exam belongs to the course (§4.4), and
# `can_grade_attempt` makes it impossible to grade one's own attempt (rule 5).
# ----------------------------------------------------------------------------
async def _require_grader(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> None:
    if not await can_grade_attempt(db, actor, enrollment):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo el instructor del curso, con su carta autorizada, califica este examen",
        )


async def grading_queue(
    db: AsyncSession,
    actor: User,
    course_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[GradingQueueItem]:
    """The attempts of MY courses waiting for a person (§4.8).

    The filter is the set of courses the caller teaches, so another instructor's queue can
    never show up here; MASTER_GC reads every course. A caller who teaches nothing at all
    gets a 403 rather than an empty list: there is no queue to speak of.
    """
    taught = None
    if not is_master(actor):
        if not await course_author_in_good_standing(db, actor):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "No tienes cola de calificación de exámenes"
            )
        taught = list(
            (
                await db.execute(select(Course.id).where(Course.instructor_id == actor.id))
            ).scalars()
        )
        if not taught:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "No tienes cola de calificación de exámenes"
            )

    conditions = [ExamAttempt.status == PENDING_GRADING, ExamAttempt.user_id != actor.id]
    if taught is not None:
        conditions.append(ExamAttempt.course_id.in_(taught))
    if course_id is not None:
        conditions.append(ExamAttempt.course_id == course_id)

    rows = (
        await db.execute(
            select(ExamAttempt, User, Course)
            .join(User, User.id == ExamAttempt.user_id)
            .join(Course, Course.id == ExamAttempt.course_id)
            .where(*conditions)
            .order_by(ExamAttempt.submitted_at, ExamAttempt.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    pending = await _pending_counts(db, [attempt.id for attempt, _, _ in rows])
    return [
        GradingQueueItem(
            attempt_id=str(attempt.id),
            enrollment_id=str(attempt.enrollment_id),
            course_id=str(course.id),
            course_title=course.title,
            member=PersonRef(id=str(member.id), name=member.name),
            attempt_no=attempt.attempt_no,
            submitted_at=attempt.submitted_at,
            pending_answers=pending.get(attempt.id, 0),
            points_total=attempt.points_total,
        )
        for attempt, member, course in rows
    ]


async def _pending_counts(db: AsyncSession, attempt_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not attempt_ids:
        return {}
    stmt = (
        select(ExamAnswer.attempt_id, func.count())
        .where(
            ExamAnswer.attempt_id.in_(attempt_ids),
            ExamAnswer.points_awarded.is_(None),
        )
        .group_by(ExamAnswer.attempt_id)
    )
    return {attempt_id: count for attempt_id, count in (await db.execute(stmt)).all()}


async def grade(
    db: AsyncSession,
    actor: User,
    attempt_id: uuid.UUID,
    position: int,
    payload: GradeIn,
    request: Request | None,
    background=None,
):
    """One answer, 0…`points_possible`. After each grade the attempt is settled again and
    closes as soon as the outcome is decided (§4.4)."""
    attempt = await _get_attempt(db, attempt_id, lock=True)
    enrollment = await portfolio._get_enrollment(db, attempt.enrollment_id, lock=True)
    await _require_grader(db, actor, enrollment)
    portfolio._require_open(enrollment)  # rule 4: nothing is written on a frozen enrollment
    if attempt.status != PENDING_GRADING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Este intento no está esperando calificación"
        )

    answer = await db.get(ExamAnswer, {"attempt_id": attempt.id, "position": position})
    if answer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa pregunta no está en este examen")
    if answer.points_awarded is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa respuesta ya está calificada")
    if payload.points_awarded > answer.points_possible:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Esta pregunta vale como mucho {answer.points_possible} puntos",
        )

    now = utcnow()
    answer.points_awarded = payload.points_awarded
    # Partial credit exists, so «correct» means the full mark; anything less is not.
    answer.is_correct = payload.points_awarded == answer.points_possible
    answer.graded_by_id = actor.id
    answer.graded_at = now
    answer.grader_note = payload.note
    record_audit(
        db,
        action="EXAM_GRADE",
        entity_type=ATTEMPT,
        entity_id=attempt.id,
        actor=actor,
        metadata={"position": position, "points": payload.points_awarded,
                  "points_possible": answer.points_possible,
                  "enrollment_id": str(attempt.enrollment_id)},
        request=request,
    )
    await _settle(db, attempt, actor=actor, request=request, background=background)
    await db.commit()
    return await _result(db, attempt, with_solutions=True)


# ----------------------------------------------------------------------------
# Bloque C · I6 — Voiding an attempt
#
# The one mechanism behind «give the child another chance», «the room lost power» and «this
# attempt was not honest»: the attempt stops counting. When it had passed, the requirements
# it completed go back EXACTLY as they were — `completed_positions` is the list the passing
# transaction wrote down for precisely this (§4.4).
# ----------------------------------------------------------------------------
async def void(
    db: AsyncSession,
    actor: User,
    attempt_id: uuid.UUID,
    payload: VoidIn,
    request: Request | None,
):
    attempt = await _get_attempt(db, attempt_id, lock=True)
    enrollment = await portfolio._get_enrollment(db, attempt.enrollment_id, lock=True)
    await _require_grader(db, actor, enrollment)
    if attempt.status == VOIDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Este intento ya está anulado")
    if enrollment.status in portfolio.FROZEN:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La inscripción ya está certificada: lo que se anula es el certificado",
        )

    now = utcnow()
    previous = attempt.status
    reverted = (
        await _revert(db, attempt, enrollment, now)
        if previous == PASSED
        else []
    )
    attempt.status = VOIDED
    attempt.voided_by_id = actor.id
    attempt.voided_at = now
    attempt.void_reason = payload.reason
    record_audit(
        db,
        action="EXAM_VOID",
        entity_type=ATTEMPT,
        entity_id=attempt.id,
        actor=actor,
        details=payload.reason,
        metadata={"from": previous, "reverted_positions": reverted,
                  "enrollment_id": str(enrollment.id), "enrollment_status": enrollment.status},
        request=request,
    )
    await db.commit()
    return await _result(db, attempt, with_solutions=True)


async def _revert(
    db: AsyncSession, attempt: ExamAttempt, enrollment: HonorEnrollment, now
) -> list[int]:
    """Undo exactly what THIS attempt completed, and only while it is still the exam that
    holds it: a row an instructor has since judged by hand carries `completed_via = NULL`
    or a reviewer, and is left alone."""
    positions = list(attempt.completed_positions or [])
    if not positions:
        return []
    rows = (
        await db.execute(
            select(RequirementProgress).where(
                RequirementProgress.enrollment_id == enrollment.id,
                RequirementProgress.requirement_position.in_(positions),
                RequirementProgress.status == portfolio.COMPLETE,
                RequirementProgress.completed_via == "EXAM",
            )
        )
    ).scalars().all()
    reverted = []
    for row in rows:
        row.status = portfolio.PENDING
        row.completed_via = None
        row.reviewed_by_id = None
        row.reviewed_at = None
        reverted.append(row.requirement_position)
    await portfolio._touch(db, enrollment)
    await course_enrollment._recalculate_ready(db, enrollment, now)
    return sorted(reverted)
