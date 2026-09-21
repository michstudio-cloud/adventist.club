"""Age of a person: the one place that decides who is a minor.

Pure functions, no database. Registration, membership and the roster all read
from here so that "minor" never means two different things.
"""
from datetime import date

from app.models import User
from app.security import utcnow

ADULT_AGE = 18


def age_in_years(birth_date: date | None, today: date | None = None) -> int | None:
    """None when the birth date is unknown. Never exposed as a date: the roster
    of a club with minors shows years, not birthdays (spec E §7)."""
    if birth_date is None:
        return None
    today = today or utcnow().date()
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (0 if had_birthday else 1)


def is_minor(birth_date: date | None, declared_minor: bool, today: date | None = None) -> bool:
    """A birth date under 18 makes the account a minor regardless of the checkbox."""
    age = age_in_years(birth_date, today)
    return declared_minor or (age is not None and age < ADULT_AGE)


def is_minor_user(user: User, today: date | None = None) -> bool:
    """`birth_date` when it exists, the stored flag otherwise (spec E §5.9)."""
    return is_minor(user.birth_date, user.is_minor, today)
