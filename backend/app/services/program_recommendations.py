"""Bloque F — the honors a class asks for, as a list a leader can plan with.

Two halves:

1. A pure, deterministic matcher (`NameIndex`, `find_mentions`, `skill_levels`) that reads a
   requirement written in prose — «Completar la especialidad de Seguridad Básica en el Agua O
   Natación I» — and answers which honors / categories of OUR catalogue it names and whether
   the member picks one of them or needs all of them. No fuzzy matching: a name is matched
   whole, token by token, accent- and case-insensitively, so a guess never reaches a member.

2. `build_recommendations`, which turns the requirements of a program into items:
     HONOR with a target honor      -> that honor, choose "all"
     HONOR with a target category   -> the category + up to 8 suggestions, choose "one"
     HONOR with neither             -> nothing to suggest, choose "any"
     FREE whose text names honors   -> those honors (and/or categories), "one" or "all"
   PROGRAM / HOURS requirements and FREE texts that name no honor are not recommendations.

Nothing here writes: it only reads the program and the honors catalogue, once per request.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Honor,
    HonorCategory,
    HonorCategoryTranslation,
    HonorTranslation,
    Ministry,
    Program,
)
from app.schemas.program import (
    CategoryTargetRef,
    RecommendationItem,
    RecommendedHonor,
    RecommendationSection,
)
from app.services import curriculum
from app.services.locales import SOURCE_LOCALE

PUBLISHED = "PUBLISHED"
MAX_SUGGESTIONS = 8

# Which ministry's honors a program's requirements refer to. Classes of Pathfinders and the
# Master Guide curriculum both ask for Pathfinder honors.
DEFAULT_HONOR_MINISTRY = "pathfinders"
HONOR_MINISTRY_OF = {"adventurers": "adventurers"}

# ----------------------------------------------------------------------------
# 1. The matcher (pure)
# ----------------------------------------------------------------------------
ROMAN = frozenset({"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"})
# «Natación I - Avanzado» is a different honor from «Natación I».
QUALIFIERS = frozenset({"avanzado", "avanzada", "advanced", "avancado", "avance"})
LEVEL_WORDS = frozenset({"nivel", "level", "niveau"})
DISJUNCTIONS = frozenset({"o", "or", "ou", "u"})
JOINERS = DISJUNCTIONS | frozenset({"y", "e", "and", "et"})
# A name that opens with one of these («La iglesia») is only read when written as a name.
FUNCTION_WORDS = frozenset({
    "la", "el", "los", "las", "lo", "un", "una", "de", "del", "en", "y", "o", "a", "al",
    "the", "of", "and", "an", "le", "les", "des", "du", "et", "os", "as", "da", "do",
})
# The text has to talk about honors at all before any name in it is read as one.
HONOR_WORDS = ("especialidad", "honor", "honour", "specialite")
ONE_OF = re.compile(
    r"\b(?:una? de (?:las|los) siguientes|una de la lista|elegir (?:uno|una)|"
    r"one of the following|one from the list|choose one|uma das seguintes|l'une des suivantes)\b"
)
# «una especialidad de <categoría> o <categoría>»: a category is only read where ONE honor of
# it is being asked for.
ASKS_FOR_ONE_HONOR = re.compile(
    r"\b(?:una|alguna) especialidad\b|\b(?:an?|one) honou?r\b|\buma especialidade\b|\bune specialite\b"
)
SKILL_LEVEL = re.compile(
    r"\b(?:nivel de (?:destreza|habilidad)|skill level|niveau)(?: de| of)?\s+([1-3])"
    r"(?:\s*(?:o|or|y|and|,|-|a|to)\s*([1-3]))?"
)
_WORD = re.compile(r"\w+")
_LIST_MARKER = re.compile(r"\s*(?:[a-z]{1,4}|#?\d{1,3})?[.)]?\s*", re.IGNORECASE)

# The wiki names some categories differently from our catalogue (by category slug).
CATEGORY_ALIASES = {
    "vocational": ("Vocación", "Vocaciones", "Vocacionales"),
    "outdoor-industries": ("Industrias Agropecuarias", "Industrias agrícolas", "Outdoor Industries"),
    "arts-crafts-hobbies": ("Artes y Manualidades", "Arts and Crafts", "Arts & Crafts",
                            "Arts, Crafts and Hobbies"),
    "household-arts": ("Artes del Hogar", "Household Arts"),
    "health-science": ("Salud y Ciencias", "Health and Science"),
    "spiritual-growth": ("Crecimiento Espiritual", "Spiritual Growth"),
    "nature": ("Naturaleza", "Nature"),
    "recreation": ("Recreación", "Recreation", "Recreational", "Recreativas"),
}


def normalize(value: str) -> str:
    """Lower case, without accents: «Natación» -> «natacion»."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


@dataclass(frozen=True)
class Token:
    norm: str
    capitalized: bool
    opens_sentence: bool
    # Only blanks between this word and the previous one (no comma, no new line).
    follows_space: bool = False


def _opens_sentence(text: str, start: int) -> bool:
    """Is the word at `start` the first word of a sentence (so its capital means nothing)?

    A word that opens a later LINE is a list item («a. Nudos», «  Orientación») and its
    capital does mean it is a name."""
    before = text[:start]
    line = before.rsplit("\n", 1)[-1]
    if "\n" in before:
        if _LIST_MARKER.fullmatch(line):
            return False
    elif not line.strip():
        return True
    stripped = line.rstrip()
    return bool(stripped) and stripped[-1] in ".!?¿¡"


def tokenize(text: str) -> list[Token]:
    """Words of `text`, normalized; «Nivel» before a numeral is dropped («Campamento Nivel II»
    is «Campamento II»)."""
    text = text or ""
    raw = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    tokens = []
    previous_end = 0
    for index, (word, start, end) in enumerate(raw):
        norm = normalize(word)
        following = normalize(raw[index + 1][0]) if index + 1 < len(raw) else ""
        follows_space = index > 0 and not text[previous_end:start].strip(" \t")
        previous_end = end
        if norm in LEVEL_WORDS and (following in ROMAN or following.isdigit()):
            continue
        tokens.append(Token(norm, word[:1].isupper(), _opens_sentence(text, start), follows_space))
    return tokens


def _glued(tokens: list[Token], i: int) -> bool:
    """Is the lone word at `i` part of a longer capitalised name («Aptitud Física»)?"""
    before = tokens[i - 1] if i > 0 and tokens[i].follows_space else None
    after = tokens[i + 1] if i + 1 < len(tokens) and tokens[i + 1].follows_space else None
    return bool(
        (before is not None and before.capitalized and not before.opens_sentence)
        or (after is not None and after.capitalized)
    )


def name_key(name: str) -> tuple[str, ...]:
    return tuple(token.norm for token in tokenize(name))


class NameIndex:
    """Whole names -> a value. The first value given for a name wins, so the caller decides
    which of two homonyms is suggested by the order of `entries`."""

    def __init__(self, entries: Iterable[tuple[str, Any]] = ()):
        self._names: dict[tuple[str, ...], Any] = {}
        self.max_len = 0
        for name, value in entries:
            self.add(name, value)

    def add(self, name: str, value: Any) -> None:
        key = name_key(name)
        if key and key not in self._names:
            self._names[key] = value
            self.max_len = max(self.max_len, len(key))

    def get(self, key: tuple[str, ...]) -> Any:
        return self._names.get(key)

    def __len__(self) -> int:
        return len(self._names)


def category_index(rows: Iterable[tuple[str, str, Any]]) -> NameIndex:
    """(slug, name, value) rows -> an index that also knows the wiki's wording of each."""
    rows = list(rows)
    index = NameIndex((name, value) for _, name, value in rows)
    for slug, _, value in rows:
        for alias in CATEGORY_ALIASES.get(slug, ()):
            index.add(alias, value)
    return index


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    value: Any


def _matches(tokens: list[Token], index: NameIndex | None) -> list[_Match]:
    """Longest whole names first, never overlapping, in reading order."""
    if index is None or not len(index):
        return []
    found: list[_Match] = []
    count = len(tokens)
    i = 0
    while i < count:
        advanced = False
        for length in range(min(index.max_len, count - i), 0, -1):
            key = tuple(t.norm for t in tokens[i:i + length])
            value = index.get(key)
            if value is None:
                continue
            if length == 1 and (
                not tokens[i].capitalized or tokens[i].opens_sentence or _glued(tokens, i)
            ):
                # A lone word is a name only when written as one: «Nudos», not «nudos», nor
                # the word that merely opens a sentence, nor a piece of «Aptitud Física».
                continue
            if key[0] in FUNCTION_WORDS and not tokens[i].capitalized:
                continue  # «de la iglesia» is prose; «La Iglesia» is the honor.
            following = tokens[i + length].norm if i + length < count else None
            if following in QUALIFIERS:
                continue  # «X avanzado» is another honor, not X.
            if following in ROMAN and key[-1] not in ROMAN:
                continue  # «Alerta roja II» is not «Alerta roja».
            found.append(_Match(i, i + length, value))
            end = i + length
            if key[-1] in ROMAN:
                # «Campamento I, II, III y IV»: the numerals that follow are more honors.
                base, j = key[:-1], end
                while j < count:
                    if tokens[j].norm in JOINERS and j + 1 < count and tokens[j + 1].norm in ROMAN:
                        j += 1
                    if tokens[j].norm in ROMAN and index.get(base + (tokens[j].norm,)) is not None:
                        found.append(_Match(j, j + 1, index.get(base + (tokens[j].norm,))))
                        j += 1
                        end = j
                        continue
                    break
            i = end
            advanced = True
            break
        if not advanced:
            i += 1
    return found


@dataclass
class Mentions:
    honors: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    choose: str = "all"

    def __bool__(self) -> bool:
        return bool(self.honors or self.categories)


def _unique(values: Iterable) -> list:
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def talks_about_honors(text: str | None) -> bool:
    normalized = normalize(text or "")
    return any(word in normalized for word in HONOR_WORDS)


def find_mentions(text: str | None, honors: NameIndex | None,
                  categories: NameIndex | None) -> Mentions:
    """The honors and categories `text` names, and whether they are alternatives."""
    if not talks_about_honors(text):
        return Mentions()
    normalized = normalize(text)
    tokens = tokenize(text)
    honor_matches = _matches(tokens, honors)
    category_matches = _matches(tokens, categories) if ASKS_FOR_ONE_HONOR.search(normalized) else []

    alternatives = bool(ONE_OF.search(normalized)) or any(
        any(t.norm in DISJUNCTIONS for t in tokens[a.end:b.start])
        for a, b in zip(honor_matches, honor_matches[1:])
    )
    return Mentions(
        honors=_unique(m.value for m in honor_matches),
        categories=_unique(m.value for m in category_matches),
        choose="one" if alternatives or category_matches else "all",
    )


def skill_levels(text: str | None) -> set[int]:
    """«(Nivel de destreza 2 ó 3)» -> {2, 3}."""
    levels: set[int] = set()
    for match in SKILL_LEVEL.finditer(normalize(text or "")):
        levels.update(int(group) for group in match.groups() if group)
    return levels


# ----------------------------------------------------------------------------
# 2. Recommendations of a program
# ----------------------------------------------------------------------------
KIND_HONOR, KIND_FROM_CATEGORY, KIND_ANY, KIND_TEXT = (
    "HONOR", "HONOR_FROM_CATEGORY", "HONOR_ANY", "TEXT")


def _language(locale: str | None) -> str:
    return (locale or SOURCE_LOCALE).lower().split("-")[0]


def _spec_text(spec: curriculum.RequirementSpec) -> str:
    return "\n".join(part for part in (spec.description, spec.instructions) if part)


def _structural_kind(spec: curriculum.RequirementSpec) -> str | None:
    if spec.kind == curriculum.HONOR:
        if spec.target_honor_id:
            return KIND_HONOR
        if spec.target_category_id:
            return KIND_FROM_CATEGORY
        return KIND_ANY
    return None


class _Catalogue:
    """The honors catalogue of one ministry, read once per request."""

    def __init__(self, db: AsyncSession, ministry_id: uuid.UUID | None, locale: str):
        self.db = db
        self.ministry_id = ministry_id
        self.locale = locale
        self._indexes: tuple[NameIndex, NameIndex] | None = None

    async def indexes(self) -> tuple[NameIndex, NameIndex]:
        if self._indexes is not None:
            return self._indexes
        language = _language(self.locale)
        honor_rows = (
            await self.db.execute(
                select(Honor.id, Honor.name)
                .where(Honor.ministry_id == self.ministry_id, Honor.status == PUBLISHED,
                       Honor.active.is_(True))
                .order_by(Honor.version.desc(), Honor.slug, Honor.id)
            )
        ).all()
        honors = NameIndex((name, honor_id) for honor_id, name in honor_rows)
        category_rows = (
            await self.db.execute(
                select(HonorCategory.id, HonorCategory.slug, HonorCategory.name)
                .where(HonorCategory.ministry_id == self.ministry_id)
                .order_by(HonorCategory.slug, HonorCategory.id)
            )
        ).all()
        categories = category_index((slug, name, cid) for cid, slug, name in category_rows)
        if language != SOURCE_LOCALE:
            ids = [row[0] for row in honor_rows]
            if ids:
                translated = (
                    await self.db.execute(
                        select(HonorTranslation.honor_id, HonorTranslation.name, HonorTranslation.locale)
                        .where(HonorTranslation.honor_id.in_(ids))
                        .order_by(HonorTranslation.locale)
                    )
                ).all()
                order = {honor_id: position for position, honor_id in enumerate(ids)}
                for honor_id, name, locale in sorted(translated, key=lambda r: order[r[0]]):
                    if _language(locale) == language:
                        honors.add(name, honor_id)
            category_ids = [row[0] for row in category_rows]
            if category_ids:
                translated = (
                    await self.db.execute(
                        select(HonorCategoryTranslation.category_id, HonorCategoryTranslation.name,
                               HonorCategoryTranslation.locale)
                        .where(HonorCategoryTranslation.category_id.in_(category_ids))
                        .order_by(HonorCategoryTranslation.category_id, HonorCategoryTranslation.locale)
                    )
                ).all()
                for category_id, name, locale in translated:
                    if _language(locale) == language:
                        categories.add(name, category_id)
        self._indexes = (honors, categories)
        return self._indexes


async def _honor_ministry_id(db: AsyncSession, program: Program) -> uuid.UUID | None:
    program_ministry = await db.scalar(select(Ministry.slug).where(Ministry.id == program.ministry_id))
    slug = HONOR_MINISTRY_OF.get(program_ministry or "", DEFAULT_HONOR_MINISTRY)
    return await db.scalar(select(Ministry.id).where(Ministry.slug == slug))


@dataclass
class _Draft:
    spec: curriculum.RequirementSpec
    kind: str
    choose: str
    honor_ids: list = field(default_factory=list)
    category_ids: list = field(default_factory=list)
    levels: set = field(default_factory=set)


async def _drafts(db: AsyncSession, program: Program, specs, locale: str) -> list[_Draft]:
    catalogue: _Catalogue | None = None
    drafts: list[_Draft] = []
    for spec in specs:
        kind = _structural_kind(spec)
        if kind == KIND_HONOR:
            drafts.append(_Draft(spec, kind, "all", honor_ids=[spec.target_honor_id]))
        elif kind == KIND_FROM_CATEGORY:
            drafts.append(_Draft(spec, kind, "one", category_ids=[spec.target_category_id],
                                 levels=skill_levels(_spec_text(spec))))
        elif kind == KIND_ANY:
            drafts.append(_Draft(spec, kind, "any"))
        elif spec.kind == curriculum.FREE and talks_about_honors(_spec_text(spec)):
            if catalogue is None:
                catalogue = _Catalogue(db, await _honor_ministry_id(db, program), locale)
            honors, categories = await catalogue.indexes()
            text = _spec_text(spec)
            found = find_mentions(text, honors, categories)
            if found:
                drafts.append(_Draft(spec, KIND_TEXT, found.choose, honor_ids=found.honors,
                                     category_ids=found.categories, levels=skill_levels(text)))
    return drafts


async def count_recommendations(db: AsyncSession, program: Program, specs, locale: str) -> int:
    """How many requirements carry a recommendation (the badge of `/admin/clases`)."""
    return len(await _drafts(db, program, specs, locale))


async def _localized_names(db: AsyncSession, model, key_column, ids, locale: str) -> dict:
    language = _language(locale)
    if language == SOURCE_LOCALE or not ids:
        return {}
    rows = (
        await db.execute(
            select(key_column, model.name, model.locale).where(key_column.in_(ids))
            .order_by(model.locale)
        )
    ).all()
    names: dict = {}
    for row_id, name, row_locale in rows:
        if row_locale.lower() == locale.lower():
            names[row_id] = name
        elif _language(row_locale) == language:
            names.setdefault(row_id, name)
    return names


async def _suggestions(db: AsyncSession, category_ids: list, levels: set[int]) -> list[uuid.UUID]:
    """Up to MAX_SUGGESTIONS published honors of these categories, basic first, then by name.
    When the text asks for a skill level, only honors of that level (if there are any)."""
    per_category: list[list[uuid.UUID]] = []
    for category_id in category_ids:
        base = (
            select(Honor.id)
            .where(Honor.category_id == category_id, Honor.status == PUBLISHED,
                   Honor.active.is_(True))
            .order_by(Honor.skill_level.asc().nulls_last(), Honor.name, Honor.id)
            .limit(MAX_SUGGESTIONS)
        )
        ids: list[uuid.UUID] = []
        if levels:
            ids = list((await db.execute(base.where(Honor.skill_level.in_(sorted(levels))))).scalars())
        if not ids:
            ids = list((await db.execute(base)).scalars())
        per_category.append(ids)
    # Round-robin, so two categories share the eight places.
    out: list[uuid.UUID] = []
    for position in range(MAX_SUGGESTIONS):
        for ids in per_category:
            if position < len(ids) and ids[position] not in out:
                out.append(ids[position])
    return out[:MAX_SUGGESTIONS]


async def build_recommendations(
    db: AsyncSession, program: Program, locale: str | None
) -> tuple[list[RecommendationItem], str]:
    specs, resolved = await curriculum.load_program_specs(db, program.id, locale)
    drafts = await _drafts(db, program, specs, resolved)

    suggested: dict[int, list[uuid.UUID]] = {}
    for position, draft in enumerate(drafts):
        if draft.category_ids:
            suggested[position] = await _suggestions(db, draft.category_ids, draft.levels)

    honor_ids = {h for d in drafts for h in d.honor_ids} | {h for ids in suggested.values() for h in ids}
    category_ids = {c for d in drafts for c in d.category_ids}
    honors = {}
    if honor_ids:
        rows = (
            await db.execute(
                select(Honor, HonorCategory.slug)
                .outerjoin(HonorCategory, HonorCategory.id == Honor.category_id)
                .where(Honor.id.in_(honor_ids))
            )
        ).all()
        honors = {honor.id: (honor, category_slug) for honor, category_slug in rows}
    categories = {}
    if category_ids:
        categories = {
            row.id: row
            for row in (
                await db.execute(select(HonorCategory).where(HonorCategory.id.in_(category_ids)))
            ).scalars()
        }
    honor_names = await _localized_names(db, HonorTranslation, HonorTranslation.honor_id,
                                         list(honor_ids), resolved)
    category_names = await _localized_names(db, HonorCategoryTranslation,
                                            HonorCategoryTranslation.category_id,
                                            list(category_ids), resolved)

    def honor_out(honor_id) -> RecommendedHonor | None:
        entry = honors.get(honor_id)
        if entry is None:
            return None
        honor, category_slug = entry
        return RecommendedHonor(
            id=str(honor.id), name=honor_names.get(honor.id) or honor.name, slug=honor.slug,
            image_url=honor.image_url, category_slug=category_slug, skill_level=honor.skill_level,
        )

    items = []
    for position, draft in enumerate(drafts):
        refs = [
            CategoryTargetRef(id=str(c.id), name=category_names.get(c.id) or c.name, slug=c.slug)
            for c in (categories.get(cid) for cid in draft.category_ids) if c is not None
        ]
        listed = [honor_out(h) for h in [*draft.honor_ids, *suggested.get(position, [])]]
        spec = draft.spec
        items.append(RecommendationItem(
            requirement_id=str(spec.source_id),
            section=RecommendationSection(slug=spec.section.slug, name=spec.section.name)
            if spec.section else None,
            label=spec.label or str(spec.position),
            text=spec.description,
            kind=draft.kind,
            choose=draft.choose,
            category=refs[0] if refs else None,
            categories=refs,
            honors=_unique_honors(h for h in listed if h is not None),
        ))
    return items, resolved


def _unique_honors(honors: Iterable[RecommendedHonor]) -> list[RecommendedHonor]:
    seen, out = set(), []
    for honor in honors:
        if honor.id not in seen:
            seen.add(honor.id)
            out.append(honor)
    return out
