"""Exam schemas (Bloque C · I5).

The most important thing in this file is a field that does NOT exist: `PaperQuestionOut`
declares no `correct_answer` and no `explanation`, so the paper a member is sitting cannot
leak them however the service is later changed. The answers appear only in
`AnswerSolutionOut`, which the service builds exclusively in the two situations decision D5
allows (the attempt passed, or it failed with no attempts left).
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.portfolio import PersonRef  # shared shape: id + name, nothing else

EXTRA_TIME_CHOICES = (0, 25, 50, 100)
MAX_RESPONSE_LENGTH = 4000


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------
class AttemptStart(BaseModel):
    # "Haré este examen por mí mismo": the only integrity measure asked of a minor (§4.6).
    pledge: bool
    # Only for an IN_PERSON exam (Bloque C · I6).
    session_code: str | None = Field(default=None, max_length=8)

    _clean = field_validator("session_code", mode="before")(_blank_to_none)


class AnswerIn(BaseModel):
    """One answer, saved on its own (autosave). For MULTIPLE_CHOICE it is the index of the
    option **as the member sees it**: the server maps it back through the shuffle it stored,
    so the client never learns the original order."""

    response: str | None = Field(default=None, max_length=MAX_RESPONSE_LENGTH)


class ExtraTimeIn(BaseModel):
    percent: Literal[0, 25, 50, 100]


# ----------------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------------
class PaperQuestionOut(BaseModel):
    """What the member sees while sitting the exam. No answers, by construction."""

    position: int
    requirement_position: int
    question_text: str
    question_type: str
    # Already shuffled; the order is the one stored with the attempt.
    options: list[str] | None
    points: int
    response: str | None
    answered_at: datetime | None


class RequirementScore(BaseModel):
    requirement_position: int
    questions: int
    points_total: int
    points_awarded: int | None


class AnswerFeedbackOut(BaseModel):
    """A failed attempt with attempts left: what was asked, what the member answered and
    whether it was right — never the right answer (D5a)."""

    position: int
    requirement_position: int
    question_text: str
    question_type: str
    options: list[str] | None
    your_response: str | None
    is_correct: bool | None
    points_possible: int
    points_awarded: int | None
    grader_note: str | None


class AnswerSolutionOut(AnswerFeedbackOut):
    """...and, once the attempt passed or no attempts are left, the answer and why."""

    correct_answer: str
    explanation: str | None


class AttemptOut(BaseModel):
    """Status and score: what the hierarchy and the club's director ever see of an attempt."""

    id: str
    enrollment_id: str
    course_id: str
    attempt_no: int
    status: str
    started_at: datetime
    deadline_at: datetime
    # Server time only; None once the attempt is closed.
    remaining_seconds: int | None
    submitted_at: datetime | None
    finished_at: datetime | None
    time_limit_minutes: int | None
    passing_score: int
    points_total: int
    points_awarded: int | None
    score_percent: int | None
    passed: bool
    auto_submitted: bool
    proctored: bool
    void_reason: str | None


class AttemptPaper(AttemptOut):
    """Only ever built for the owner of an attempt in progress."""

    questions: list[PaperQuestionOut]


class AttemptResult(AttemptOut):
    breakdown: list[RequirementScore]
    feedback: list[AnswerFeedbackOut]


class AttemptSolution(AttemptOut):
    breakdown: list[RequirementScore]
    feedback: list[AnswerSolutionOut]


class AnswerSaved(BaseModel):
    position: int
    answered_at: datetime
    remaining_seconds: int | None


class ExamStateOut(BaseModel):
    """The exam card of an enrollment: the rules, what is left and the last result."""

    enrollment_id: str
    course_id: str
    course_title: str
    exam_mode: str
    passing_score: int
    time_limit_minutes: int | None
    extra_time_percent: int
    max_attempts: int
    attempts_used: int
    attempts_left: int
    exam_positions: list[int]
    pending_positions: list[int]
    open_attempt_id: str | None
    last_result: AttemptOut | None
    can_start: bool
    blocked_reason: str | None


# ----------------------------------------------------------------------------
# Bloque C · I6 — manual grading, voiding and the in-person session
# ----------------------------------------------------------------------------
GRADER_NOTE_MAX_LENGTH = 1000
VOID_REASON_MAX_LENGTH = 2000
# §4.6: a session lasts as long as the class does.
SESSION_MINUTES_MIN, SESSION_MINUTES_MAX, SESSION_MINUTES_DEFAULT = 15, 240, 120


class GradeIn(BaseModel):
    """One answer, graded by a person: 0…`points_possible` and an optional note.

    The note is one of only two texts an instructor ever writes towards a member (the other
    is the verdict note of A) and the guardian of a minor reads it too (§6).
    """

    points_awarded: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=GRADER_NOTE_MAX_LENGTH)

    _clean = field_validator("note", mode="before")(_blank_to_none)


class VoidIn(BaseModel):
    """Voiding is never silent: the member reads the reason on the attempt."""

    reason: str = Field(min_length=3, max_length=VOID_REASON_MAX_LENGTH)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class GradingQueueItem(BaseModel):
    """What the instructor needs to pick an attempt to grade. No contact detail of the
    member travels here — name and nothing else, as everywhere else in a course (§6)."""

    attempt_id: str
    enrollment_id: str
    course_id: str
    course_title: str
    member: PersonRef
    attempt_no: int
    submitted_at: datetime | None
    pending_answers: int
    points_total: int


class ExamSessionIn(BaseModel):
    minutes: int = Field(
        default=SESSION_MINUTES_DEFAULT, ge=SESSION_MINUTES_MIN, le=SESSION_MINUTES_MAX
    )


class ExamSessionOut(BaseModel):
    """The ONLY time the code exists outside the instructor's screen: what is stored is
    its hash, so a closed or forgotten session cannot be recovered, only reopened."""

    code: str
    expires_at: datetime
