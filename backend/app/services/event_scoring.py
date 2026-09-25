"""Motor de puntajes de eventos (spec 2026-09-24-eventos §3.2).

DECLARATIVO: una actividad dice qué `kind` es y su `config` (datos, nunca fórmulas); el juez
captura HECHOS (`inputs`: aciertos, criterios, estaciones completadas) y este módulo calcula
los puntos y el desglose. Nada de aquí toca la base de datos: funciones puras, probadas en
`tests/test_event_scoring.py`.

Validación estricta en las dos puertas:
  * `validate_config(kind, config, draft=...)` — la forma de `config` por `kind`. En borrador
    (`draft=True`, actividades TO_DEFINE) los números pueden faltar (`null`) y las listas estar
    vacías: la asociación completa después. Lo que sí viene, se valida igual.
  * `score(kind, config, inputs)` — la config debe estar COMPLETA y los `inputs` dentro de
    rango; cualquier clave desconocida es error.

`group` no se evalúa: es un contenedor y su puntaje es la suma de sus hijas (lo arma el
servicio). No hay piso ni techo implícitos en el total del evento; el único límite es el de
cada criterio (0..max), que es lo que el boletín define.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PARTICIPATION = "participation"
RUBRIC = "rubric"
BANDS = "bands"
PER_CORRECT = "per_correct"
STATIONS = "stations"
GROUP = "group"
KINDS = (PARTICIPATION, RUBRIC, BANDS, PER_CORRECT, STATIONS, GROUP)
SCORABLE_KINDS = tuple(kind for kind in KINDS if kind != GROUP)

# Clave común a todo `kind` evaluable: sólo los finalistas marcados por la coordinación
# (event_registrations.finalist_flags) se capturan; el resto vale 0 en ESA actividad.
FINALISTS_ONLY = "finalists_only"

MAX_POINTS = Decimal("100000")
MAX_COUNT = 100000
MAX_ITEMS = 100
KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
CENT = Decimal("0.01")


class ScoringError(ValueError):
    """Config o captura inválida. `str(error)` es un mensaje en español para el usuario."""

    def __init__(self, message: str, path: str | None = None):
        super().__init__(message)
        self.message = message
        self.path = path


@dataclass
class ScoreResult:
    points: Decimal
    breakdown: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Tipos básicos
# ----------------------------------------------------------------------------
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def to_points(value: Any, path: str, *, allow_none: bool = False) -> Decimal | None:
    """Un número de puntos: >= 0, <= 100000, a lo más dos decimales."""
    if value is None and allow_none:
        return None
    if not _is_number(value):
        raise ScoringError(f"{path}: debe ser un número", path)
    try:
        number = Decimal(str(value))
    except InvalidOperation:  # pragma: no cover - float('nan') etc.
        raise ScoringError(f"{path}: número inválido", path)
    if not number.is_finite():
        raise ScoringError(f"{path}: número inválido", path)
    if number < 0:
        raise ScoringError(f"{path}: no puede ser negativo", path)
    if number > MAX_POINTS:
        raise ScoringError(f"{path}: es demasiado grande", path)
    if number != number.quantize(CENT):
        raise ScoringError(f"{path}: admite a lo más dos decimales", path)
    return number.quantize(CENT)


def _count(value: Any, path: str, *, allow_none: bool = False, minimum: int = 0) -> int | None:
    """Un conteo: entero >= minimum (nunca booleano ni decimal)."""
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise ScoringError(f"{path}: debe ser un número entero", path)
    if value < minimum:
        raise ScoringError(f"{path}: debe ser al menos {minimum}", path)
    if value > MAX_COUNT:
        raise ScoringError(f"{path}: es demasiado grande", path)
    return value


def _object(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise ScoringError(f"{path}: debe ser un objeto", path)
    return value


def _only_keys(value: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ScoringError(f"{path}: claves desconocidas: {', '.join(unknown)}", path)


def _label(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoringError(f"{path}: falta el nombre", path)
    value = " ".join(value.split())
    if len(value) > 120:
        raise ScoringError(f"{path}: el nombre es demasiado largo", path)
    return value


def _key(value: Any, path: str) -> str:
    if not isinstance(value, str) or not KEY_RE.match(value):
        raise ScoringError(
            f"{path}: la clave debe tener 1–40 caracteres en minúscula, dígitos o «_»", path
        )
    return value


def _list(value: Any, path: str, *, draft: bool) -> list:
    if value is None and draft:
        return []
    if not isinstance(value, list):
        raise ScoringError(f"{path}: debe ser una lista", path)
    if len(value) > MAX_ITEMS:
        raise ScoringError(f"{path}: demasiados elementos", path)
    if not value and not draft:
        raise ScoringError(f"{path}: no puede estar vacía", path)
    return value


def _unique_keys(items: list[dict], path: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item["key"] in seen:
            raise ScoringError(f"{path}: clave repetida «{item['key']}»", path)
        seen.add(item["key"])


def _num_out(value: Decimal | None) -> float | int | None:
    """Para JSON: entero si no tiene decimales."""
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
def validate_config(kind: str, config: Any, *, draft: bool = False) -> dict:
    """La config normalizada (copia). `draft=True` acepta números faltantes y listas vacías."""
    if kind not in KINDS:
        raise ScoringError(f"Tipo de evaluación desconocido: {kind}", "kind")
    if config is None:
        config = {}
    config = _object(config, "config")
    common: dict[str, Any] = {}
    if kind != GROUP and FINALISTS_ONLY in config:
        if not isinstance(config[FINALISTS_ONLY], bool):
            raise ScoringError("config.finalists_only: debe ser verdadero o falso", "config")
        if config[FINALISTS_ONLY]:
            common[FINALISTS_ONLY] = True

    if kind == GROUP:
        _only_keys(config, set(), "config")
        return {}

    if kind == PARTICIPATION:
        _only_keys(config, {"max", FINALISTS_ONLY}, "config")
        maximum = to_points(config.get("max"), "config.max", allow_none=draft)
        if maximum is not None and maximum <= 0:
            raise ScoringError("config.max: debe ser mayor que cero", "config.max")
        return {"max": _num_out(maximum), **common}

    if kind == RUBRIC:
        _only_keys(config, {"criteria", FINALISTS_ONLY}, "config")
        criteria = []
        for index, raw in enumerate(_list(config.get("criteria"), "config.criteria", draft=draft)):
            path = f"config.criteria[{index}]"
            raw = _object(raw, path)
            _only_keys(raw, {"key", "label", "max", "deduction_step"}, path)
            maximum = to_points(raw.get("max"), f"{path}.max", allow_none=draft)
            if maximum is not None and maximum <= 0:
                raise ScoringError(f"{path}.max: debe ser mayor que cero", path)
            step = to_points(raw.get("deduction_step"), f"{path}.deduction_step", allow_none=True)
            if step is not None:
                if step <= 0:
                    raise ScoringError(f"{path}.deduction_step: debe ser mayor que cero", path)
                if maximum is not None and step > maximum:
                    raise ScoringError(f"{path}.deduction_step: no puede exceder el máximo", path)
            item = {"key": _key(raw.get("key"), f"{path}.key"),
                    "label": _label(raw.get("label"), f"{path}.label"),
                    "max": _num_out(maximum)}
            if step is not None:
                item["deduction_step"] = _num_out(step)
            criteria.append(item)
        _unique_keys(criteria, "config.criteria")
        return {"criteria": criteria, **common}

    if kind == BANDS:
        _only_keys(config, {"bands", FINALISTS_ONLY}, "config")
        bands = []
        for index, raw in enumerate(_list(config.get("bands"), "config.bands", draft=draft)):
            path = f"config.bands[{index}]"
            raw = _object(raw, path)
            _only_keys(raw, {"min", "max", "points"}, path)
            low = _count(raw.get("min"), f"{path}.min", allow_none=draft)
            high = _count(raw.get("max"), f"{path}.max", allow_none=draft)
            if low is not None and high is not None and low > high:
                raise ScoringError(f"{path}: el mínimo no puede ser mayor que el máximo", path)
            points = to_points(raw.get("points"), f"{path}.points", allow_none=draft)
            bands.append({"min": low, "max": high, "points": _num_out(points)})
        complete = [band for band in bands if band["min"] is not None and band["max"] is not None]
        ordered = sorted(complete, key=lambda band: band["min"])
        for before, after in zip(ordered, ordered[1:]):
            if after["min"] <= before["max"]:
                raise ScoringError("config.bands: las bandas se traslapan", "config.bands")
        if not draft:
            if ordered[0]["min"] != 0:
                raise ScoringError("config.bands: la primera banda debe empezar en 0", "config.bands")
            for before, after in zip(ordered, ordered[1:]):
                if after["min"] != before["max"] + 1:
                    raise ScoringError(
                        "config.bands: hay un hueco entre bandas "
                        f"({before['max']} → {after['min']})", "config.bands")
            bands = sorted(bands, key=lambda band: band["min"], reverse=True)
        return {"bands": bands, **common}

    if kind == PER_CORRECT:
        _only_keys(config, {"points_each", "max_items", FINALISTS_ONLY}, "config")
        each = to_points(config.get("points_each"), "config.points_each", allow_none=draft)
        if each is not None and each <= 0:
            raise ScoringError("config.points_each: debe ser mayor que cero", "config.points_each")
        items = _count(config.get("max_items"), "config.max_items", allow_none=draft, minimum=1)
        return {"points_each": _num_out(each), "max_items": items, **common}

    # STATIONS
    _only_keys(config, {"stations", FINALISTS_ONLY}, "config")
    stations = []
    for index, raw in enumerate(_list(config.get("stations"), "config.stations", draft=draft)):
        path = f"config.stations[{index}]"
        raw = _object(raw, path)
        _only_keys(raw, {"key", "label", "points"}, path)
        points = to_points(raw.get("points"), f"{path}.points", allow_none=draft)
        stations.append({"key": _key(raw.get("key"), f"{path}.key"),
                         "label": _label(raw.get("label"), f"{path}.label"),
                         "points": _num_out(points)})
    _unique_keys(stations, "config.stations")
    return {"stations": stations, **common}


def is_complete(kind: str, config: Any) -> bool:
    """¿La config pasa la validación estricta (lista para capturar)?"""
    try:
        validate_config(kind, config, draft=False)
    except ScoringError:
        return False
    return True


def config_max(kind: str, config: Any) -> Decimal | None:
    """El máximo que la config permite (None para `group` o una config incompleta)."""
    if kind == GROUP or not is_complete(kind, config):
        return None
    config = validate_config(kind, config)
    if kind == PARTICIPATION:
        return Decimal(str(config["max"])).quantize(CENT)
    if kind == RUBRIC:
        return sum((Decimal(str(c["max"])) for c in config["criteria"]), Decimal(0)).quantize(CENT)
    if kind == BANDS:
        return max(Decimal(str(b["points"])) for b in config["bands"]).quantize(CENT)
    if kind == PER_CORRECT:
        return (Decimal(str(config["points_each"])) * config["max_items"]).quantize(CENT)
    return sum((Decimal(str(s["points"])) for s in config["stations"]), Decimal(0)).quantize(CENT)


# ----------------------------------------------------------------------------
# Cálculo
# ----------------------------------------------------------------------------
def score(kind: str, config: Any, inputs: Any) -> ScoreResult:
    """Puntos + desglose de una captura. Levanta `ScoringError` ante cualquier dato fuera de
    rango, clave desconocida o config incompleta."""
    if kind == GROUP:
        raise ScoringError("Un grupo no se evalúa directamente: se evalúan sus rondas o estaciones")
    if kind not in KINDS:
        raise ScoringError(f"Tipo de evaluación desconocido: {kind}", "kind")
    try:
        config = validate_config(kind, config, draft=False)
    except ScoringError as error:
        raise ScoringError(f"La actividad no tiene reglas completas ({error.message})") from None
    inputs = _object(inputs, "inputs")

    if kind == PARTICIPATION:
        _only_keys(inputs, {"points"}, "inputs")
        if "points" not in inputs:
            raise ScoringError("inputs.points: es obligatorio", "inputs.points")
        points = to_points(inputs["points"], "inputs.points")
        maximum = Decimal(str(config["max"]))
        if points > maximum:
            raise ScoringError(f"inputs.points: el máximo es {_num_out(maximum)}", "inputs.points")
        return ScoreResult(points, {"kind": kind, "points": _num_out(points), "max": config["max"]})

    if kind == RUBRIC:
        _only_keys(inputs, {"criteria"}, "inputs")
        given = _object(inputs.get("criteria"), "inputs.criteria")
        known = {criterion["key"] for criterion in config["criteria"]}
        _only_keys(given, known, "inputs.criteria")
        missing = [c["key"] for c in config["criteria"] if c["key"] not in given]
        if missing:
            raise ScoringError(
                f"inputs.criteria: faltan criterios: {', '.join(missing)}", "inputs.criteria")
        rows, total = [], Decimal(0)
        for criterion in config["criteria"]:
            path = f"inputs.criteria.{criterion['key']}"
            maximum = Decimal(str(criterion["max"]))
            raw = given[criterion["key"]]
            row: dict[str, Any] = {"key": criterion["key"], "label": criterion["label"],
                                   "max": criterion["max"]}
            if isinstance(raw, dict):
                step = criterion.get("deduction_step")
                if step is None:
                    raise ScoringError(f"{path}: este criterio no admite descuentos", path)
                _only_keys(raw, {"deductions"}, path)
                deductions = _count(raw.get("deductions"), f"{path}.deductions")
                deducted = Decimal(str(step)) * deductions
                value = maximum - deducted
                # 0..max por criterio: el descuento nunca pasa del propio criterio.
                if value < 0:
                    value = Decimal(0)
                    row["floored"] = True
                row.update(deductions=deductions, deduction_step=step)
            else:
                value = to_points(raw, path)
                if value > maximum:
                    raise ScoringError(f"{path}: el máximo es {criterion['max']}", path)
            value = value.quantize(CENT)
            row["score"] = _num_out(value)
            rows.append(row)
            total += value
        total = total.quantize(CENT)
        return ScoreResult(total, {"kind": kind, "criteria": rows, "points": _num_out(total)})

    if kind == BANDS:
        _only_keys(inputs, {"correct"}, "inputs")
        if "correct" not in inputs:
            raise ScoringError("inputs.correct: es obligatorio", "inputs.correct")
        correct = _count(inputs["correct"], "inputs.correct")
        for band in config["bands"]:
            if band["min"] <= correct <= band["max"]:
                points = Decimal(str(band["points"])).quantize(CENT)
                return ScoreResult(points, {"kind": kind, "correct": correct, "band": band,
                                            "points": _num_out(points)})
        top = max(band["max"] for band in config["bands"])
        raise ScoringError(f"inputs.correct: el máximo es {top}", "inputs.correct")

    if kind == PER_CORRECT:
        _only_keys(inputs, {"correct"}, "inputs")
        if "correct" not in inputs:
            raise ScoringError("inputs.correct: es obligatorio", "inputs.correct")
        correct = _count(inputs["correct"], "inputs.correct")
        if correct > config["max_items"]:
            raise ScoringError(f"inputs.correct: el máximo es {config['max_items']}", "inputs.correct")
        points = (Decimal(str(config["points_each"])) * correct).quantize(CENT)
        return ScoreResult(points, {"kind": kind, "correct": correct,
                                    "points_each": config["points_each"],
                                    "points": _num_out(points)})

    # STATIONS
    _only_keys(inputs, {"completed"}, "inputs")
    completed = inputs.get("completed")
    if not isinstance(completed, list):
        raise ScoringError("inputs.completed: debe ser una lista de estaciones", "inputs.completed")
    known = {station["key"] for station in config["stations"]}
    if any(not isinstance(key, str) for key in completed):
        raise ScoringError("inputs.completed: claves inválidas", "inputs.completed")
    unknown = sorted(set(completed) - known)
    if unknown:
        raise ScoringError(
            f"inputs.completed: estaciones desconocidas: {', '.join(unknown)}", "inputs.completed")
    if len(set(completed)) != len(completed):
        raise ScoringError("inputs.completed: estación repetida", "inputs.completed")
    done = set(completed)
    rows, total = [], Decimal(0)
    for station in config["stations"]:
        hit = station["key"] in done
        rows.append({**station, "completed": hit})
        if hit:
            total += Decimal(str(station["points"]))
    total = total.quantize(CENT)
    return ScoreResult(total, {"kind": kind, "stations": rows, "points": _num_out(total)})


# ----------------------------------------------------------------------------
# Honores (events.honor_bands)
# ----------------------------------------------------------------------------
def validate_honor_bands(bands: Any) -> list[dict]:
    """`[{key, label, min, max}]`: `min` inclusivo (null = sin piso, a lo más una banda),
    `max` sólo informativo. Se asigna la banda de mayor `min` que el total alcanza."""
    if bands is None:
        return []
    if not isinstance(bands, list) or len(bands) > 20:
        raise ScoringError("honor_bands: debe ser una lista (máximo 20)", "honor_bands")
    clean, mins, floors = [], set(), 0
    for index, raw in enumerate(bands):
        path = f"honor_bands[{index}]"
        raw = _object(raw, path)
        _only_keys(raw, {"key", "label", "min", "max"}, path)
        low = _signed(raw.get("min"), f"{path}.min")
        high = _signed(raw.get("max"), f"{path}.max")
        if low is not None and high is not None and low > high:
            raise ScoringError(f"{path}: el mínimo no puede ser mayor que el máximo", path)
        if low is None:
            floors += 1
        elif low in mins:
            raise ScoringError(f"{path}: mínimo repetido", path)
        else:
            mins.add(low)
        clean.append({"key": _key(raw.get("key"), f"{path}.key"),
                      "label": _label(raw.get("label"), f"{path}.label"),
                      "min": _num_out(low), "max": _num_out(high)})
    if floors > 1:
        raise ScoringError("honor_bands: sólo una banda puede no tener mínimo", "honor_bands")
    _unique_keys(clean, "honor_bands")
    return clean


def _signed(value: Any, path: str) -> Decimal | None:
    if value is None:
        return None
    if not _is_number(value):
        raise ScoringError(f"{path}: debe ser un número", path)
    number = Decimal(str(value))
    if not number.is_finite() or abs(number) > MAX_POINTS:
        raise ScoringError(f"{path}: número inválido", path)
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def honor_for(total: Decimal, bands: list[dict] | None) -> dict | None:
    if not bands:
        return None
    reached = [band for band in bands
               if band.get("min") is None or total >= Decimal(str(band["min"]))]
    if not reached:
        return None
    best = max(reached, key=lambda band: Decimal("-Infinity") if band.get("min") is None
               else Decimal(str(band["min"])))
    return {"key": best["key"], "label": best["label"]}


def as_number(value: Decimal | None) -> float | int | None:
    return _num_out(value)
