"""Rules shared by the content of honors and courses: where media may live, which videos
are accepted, and what a gradable question is. One implementation, two schemas: an honor's
bank is copied into courses (services/courses.import_honor_bank), so both must agree.
"""
import re
import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import settings

QuestionType = Literal["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY"]

MAX_BANK_PER_REQUIREMENT = 200
# SHORT_ANSWER: accepted answers separated by "|".
SHORT_ANSWER_SEPARATOR = "|"
MAX_SHORT_ANSWERS = 10
MAX_SHORT_ANSWER_LENGTH = 120
MULTIPLE_CHOICE_OPTIONS = (2, 6)
MAX_LINK_LENGTH = 2000

VIDEO_ID = {
    "youtube": re.compile(r"^[A-Za-z0-9_-]{6,20}$"),
    "vimeo": re.compile(r"^[0-9]{6,12}$"),
}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}


def blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


# ----------------------------------------------------------------------------
# Media and links
# ----------------------------------------------------------------------------
def media_url(value: str) -> str:
    """Only the platform's own media bucket: an `<img>` from anywhere else is a beacon
    that tells a third party which minor opened the lesson, and can change under us."""
    base = settings.R2_PUBLIC_URL.rstrip("/")
    if not value.startswith(f"{base}/") or len(value) > 600:
        raise ValueError(f"El archivo debe estar alojado en {base}")
    return value


def http_url(value: str) -> str:
    """An absolute http(s) link: never `javascript:`, `data:` or a relative path."""
    value = value.strip()
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or len(value) > MAX_LINK_LENGTH
        or any(char.isspace() for char in value)
    ):
        raise ValueError(
            f"La dirección debe empezar por http:// o https:// (máximo {MAX_LINK_LENGTH} caracteres)"
        )
    return value


def parse_video(url: str) -> tuple[str, str]:
    """(provider, video_id) from a YouTube or Vimeo URL. Only these two, and only the id is
    stored: the frontend embeds it with youtube-nocookie.com / player.vimeo.com."""
    parsed = urllib.parse.urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        if host.endswith("youtu.be"):
            video_id = parsed.path.lstrip("/")
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path[len("/embed/"):]
        else:
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        provider = "youtube"
    elif host in _VIMEO_HOSTS:
        video_id, provider = parsed.path.rstrip("/").rsplit("/", 1)[-1], "vimeo"
    else:
        raise ValueError("Solo se admiten vídeos de YouTube o Vimeo")
    video_id = video_id.split("?")[0].split("&")[0].strip()
    if not VIDEO_ID[provider].match(video_id):
        raise ValueError("No se reconoce el vídeo en esa dirección")
    return provider, video_id


def canonical_video_url(url: str) -> str:
    """The one spelling of a YouTube / Vimeo video that is stored, whatever form was pasted."""
    provider, video_id = parse_video(url)
    if provider == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}"
    return f"https://vimeo.com/{video_id}"


# ----------------------------------------------------------------------------
# Questions
# ----------------------------------------------------------------------------
def normalize_answer(
    question_type: str, options: list[str] | None, correct_answer: str
) -> tuple[list[str] | None, str]:
    """(options, correct_answer) as stored, or ValueError when the question cannot be graded
    (spec §4.1). The single implementation behind every question bank."""
    low, high = MULTIPLE_CHOICE_OPTIONS
    if question_type == "MULTIPLE_CHOICE":
        cleaned = [option.strip() for option in options or []]
        if not low <= len(cleaned) <= high:
            raise ValueError(f"Una pregunta de opción múltiple lleva de {low} a {high} opciones")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Las opciones no pueden repetirse")
        if correct_answer.strip() not in cleaned:
            raise ValueError("La respuesta correcta debe ser una de las opciones")
        return cleaned, correct_answer.strip()
    if options:
        raise ValueError("Solo una pregunta de opción múltiple lleva opciones")
    if question_type == "TRUE_FALSE":
        value = correct_answer.strip().lower()
        if value not in ("true", "false"):
            raise ValueError("La respuesta de verdadero o falso es «true» o «false»")
        return None, value
    if question_type == "SHORT_ANSWER":
        answers = [part.strip() for part in correct_answer.split(SHORT_ANSWER_SEPARATOR)]
        answers = [answer for answer in answers if answer]
        if not 1 <= len(answers) <= MAX_SHORT_ANSWERS:
            raise ValueError(
                f"Una respuesta corta admite de 1 a {MAX_SHORT_ANSWERS} respuestas aceptadas,"
                f" separadas por «{SHORT_ANSWER_SEPARATOR}»"
            )
        if any(len(answer) > MAX_SHORT_ANSWER_LENGTH for answer in answers):
            raise ValueError(
                f"Cada respuesta aceptada ocupa como mucho {MAX_SHORT_ANSWER_LENGTH} caracteres"
            )
        return None, SHORT_ANSWER_SEPARATOR.join(answers)
    return None, correct_answer


class GradableQuestion(BaseModel):
    """One question of a bank, honor's or course's. A bank that cannot be graded is a bank
    nobody can sit: every rule of §4.1 is enforced here, for both."""

    question_text: str = Field(min_length=1, max_length=1000)
    question_type: QuestionType
    options: list[str] | None = None
    correct_answer: str = Field(min_length=1, max_length=2000)
    points: int = Field(default=1, ge=1, le=10)
    explanation: str | None = Field(default=None, max_length=1000)

    _clean = field_validator("explanation", mode="before")(blank_to_none)

    @model_validator(mode="after")
    def _matches_its_type(self):
        self.options, self.correct_answer = normalize_answer(
            self.question_type, self.options, self.correct_answer
        )
        return self
