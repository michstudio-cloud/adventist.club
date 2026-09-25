"""Motor declarativo de puntajes (spec 2026-09-24-eventos §3.2). Funciones puras: sin base."""
from decimal import Decimal

import pytest

from app.services.event_scoring import (
    ScoringError,
    config_max,
    honor_for,
    is_complete,
    score,
    validate_config,
    validate_honor_bands,
)

KIDS = {"bands": [{"min": 22, "max": 26, "points": 100}, {"min": 17, "max": 21, "points": 95},
                  {"min": 12, "max": 16, "points": 90}, {"min": 0, "max": 11, "points": 85}]}
PARENTS = {"bands": [{"min": 16, "max": 18, "points": 80}, {"min": 13, "max": 15, "points": 75},
                     {"min": 10, "max": 12, "points": 70}, {"min": 7, "max": 9, "points": 65},
                     {"min": 0, "max": 6, "points": 60}]}
ESENCIA = {"criteria": [
    {"key": "tiempo", "label": "Tiempo", "max": 40, "deduction_step": 2},
    {"key": "participacion", "label": "Participación", "max": 20, "deduction_step": 2},
    {"key": "mensaje", "label": "Mensaje final", "max": 40, "deduction_step": 2},
    {"key": "titeres", "label": "Creatividad títeres", "max": 50, "deduction_step": 2},
    {"key": "previa", "label": "Presentación previa", "max": 50},
]}
MISION = {"stations": [
    {"key": "plataforma", "label": "Plataforma", "points": 50},
    {"key": "reparacion", "label": "Reparación", "points": 30},
    {"key": "campo_base", "label": "Campo Base", "points": 30},
    {"key": "gravedad", "label": "Gravedad Cero", "points": 20},
    {"key": "asteroides", "label": "Asteroides", "points": 30},
    {"key": "nebulosas", "label": "Nebulosas", "points": 20},
    {"key": "explorando", "label": "Explorando", "points": 20},
    {"key": "centro_mando", "label": "Centro de Mando", "points": 0},
]}


def _raises(fragment: str, fn, *args, **kwargs):
    with pytest.raises(ScoringError) as error:
        fn(*args, **kwargs)
    assert fragment in str(error.value), str(error.value)
    return error.value


# ----------------------------------------------------------------------------
# participation
# ----------------------------------------------------------------------------
def test_participation_scores_within_range():
    result = score("participation", {"max": 150}, {"points": 150})
    assert result.points == Decimal("150.00")
    assert score("participation", {"max": 150}, {"points": 0}).points == 0
    assert score("participation", {"max": 150}, {"points": 72.5}).points == Decimal("72.50")
    assert result.breakdown == {"kind": "participation", "points": 150, "max": 150}


@pytest.mark.parametrize("inputs,fragment", [
    ({"points": 151}, "máximo es 150"),
    ({"points": -1}, "negativo"),
    ({"points": 1.005}, "dos decimales"),
    ({"points": True}, "número"),
    ({"points": "10"}, "número"),
    ({}, "obligatorio"),
    ({"points": 1, "total": 1}, "claves desconocidas"),
    ([1], "objeto"),
])
def test_participation_rejects_bad_inputs(inputs, fragment):
    _raises(fragment, score, "participation", {"max": 150}, inputs)


def test_participation_config():
    assert validate_config("participation", {"max": 50}) == {"max": 50}
    _raises("mayor que cero", validate_config, "participation", {"max": 0})
    _raises("número", validate_config, "participation", {})
    assert validate_config("participation", {}, draft=True) == {"max": None}
    _raises("claves desconocidas", validate_config, "participation", {"max": 5, "formula": "x"})


# ----------------------------------------------------------------------------
# rubric
# ----------------------------------------------------------------------------
def test_rubric_sums_criteria_and_applies_deductions():
    inputs = {"criteria": {"tiempo": {"deductions": 3}, "participacion": 20, "mensaje": 38,
                           "titeres": {"deductions": 0}, "previa": 50}}
    result = score("rubric", ESENCIA, inputs)
    assert result.points == Decimal("34") + 20 + 38 + 50 + 50
    first = result.breakdown["criteria"][0]
    assert first == {"key": "tiempo", "label": "Tiempo", "max": 40, "deductions": 3,
                     "deduction_step": 2, "score": 34}


def test_rubric_deductions_floor_inside_the_criterion_only():
    inputs = {"criteria": {"tiempo": 0, "participacion": {"deductions": 50}, "mensaje": 0,
                           "titeres": 0, "previa": 0}}
    result = score("rubric", ESENCIA, inputs)
    assert result.points == 0
    assert result.breakdown["criteria"][1]["floored"] is True


@pytest.mark.parametrize("criteria,fragment", [
    ({"tiempo": 41, "participacion": 0, "mensaje": 0, "titeres": 0, "previa": 0}, "máximo es 40"),
    ({"tiempo": 0, "participacion": 0, "mensaje": 0, "titeres": 0}, "faltan criterios: previa"),
    ({"tiempo": 0, "participacion": 0, "mensaje": 0, "titeres": 0, "previa": 0, "x": 1},
     "claves desconocidas"),
    ({"tiempo": 0, "participacion": 0, "mensaje": 0, "titeres": 0, "previa": {"deductions": 1}},
     "no admite descuentos"),
    ({"tiempo": {"deductions": -1}, "participacion": 0, "mensaje": 0, "titeres": 0, "previa": 0},
     "al menos 0"),
    ({"tiempo": {"deductions": 1.5}, "participacion": 0, "mensaje": 0, "titeres": 0, "previa": 0},
     "entero"),
    ({"tiempo": {"deductions": 1, "score": 3}, "participacion": 0, "mensaje": 0, "titeres": 0,
      "previa": 0}, "claves desconocidas"),
])
def test_rubric_rejects_bad_inputs(criteria, fragment):
    _raises(fragment, score, "rubric", ESENCIA, {"criteria": criteria})


def test_rubric_config_rules():
    assert config_max("rubric", ESENCIA) == Decimal("200")
    _raises("clave repetida", validate_config, "rubric",
            {"criteria": [{"key": "a", "label": "A", "max": 1}, {"key": "a", "label": "B", "max": 1}]})
    _raises("no puede estar vacía", validate_config, "rubric", {"criteria": []})
    _raises("no puede exceder", validate_config, "rubric",
            {"criteria": [{"key": "a", "label": "A", "max": 1, "deduction_step": 2}]})
    _raises("clave debe", validate_config, "rubric",
            {"criteria": [{"key": "Con Espacio", "label": "A", "max": 1}]})
    _raises("falta el nombre", validate_config, "rubric", {"criteria": [{"key": "a", "max": 1}]})
    # Borrador: criterios sin reparto de puntos (inspecciones TO_DEFINE).
    draft = {"criteria": [{"key": "versiculo", "label": "Versículo", "max": None}]}
    assert validate_config("rubric", draft, draft=True)["criteria"][0]["max"] is None
    assert not is_complete("rubric", draft)
    _raises("reglas completas", score, "rubric", draft, {"criteria": {"versiculo": 1}})


# ----------------------------------------------------------------------------
# bands
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("correct,points", [(26, 100), (22, 100), (21, 95), (17, 95), (16, 90),
                                            (12, 90), (11, 85), (0, 85)])
def test_bands_for_adventurers(correct, points):
    assert score("bands", KIDS, {"correct": correct}).points == points


@pytest.mark.parametrize("correct,points", [(18, 80), (16, 80), (15, 75), (13, 75), (12, 70),
                                            (10, 70), (9, 65), (7, 65), (6, 60), (0, 60)])
def test_bands_for_parents(correct, points):
    assert score("bands", PARENTS, {"correct": correct}).points == points


def test_zero_correct_is_not_zero_points():
    # Cero aciertos en la ronda general vale 85 + 60: nunca «pendiente» ni 0.
    assert score("bands", KIDS, {"correct": 0}).points + score("bands", PARENTS, {"correct": 0}).points == 145


def test_bands_reject_out_of_range_and_bad_configs():
    _raises("máximo es 26", score, "bands", KIDS, {"correct": 27})
    _raises("entero", score, "bands", KIDS, {"correct": 3.5})
    _raises("traslapan", validate_config, "bands",
            {"bands": [{"min": 0, "max": 5, "points": 1}, {"min": 5, "max": 9, "points": 2}]})
    _raises("hueco", validate_config, "bands",
            {"bands": [{"min": 0, "max": 5, "points": 1}, {"min": 7, "max": 9, "points": 2}]})
    _raises("empezar en 0", validate_config, "bands", {"bands": [{"min": 1, "max": 5, "points": 1}]})
    _raises("mayor que el máximo", validate_config, "bands",
            {"bands": [{"min": 5, "max": 1, "points": 1}]})
    assert config_max("bands", KIDS) == 100
    assert config_max("bands", PARENTS) == 80


# ----------------------------------------------------------------------------
# per_correct
# ----------------------------------------------------------------------------
def test_per_correct():
    config = {"points_each": 2, "max_items": 10, "finalists_only": True}
    assert score("per_correct", config, {"correct": 10}).points == 20
    assert score("per_correct", config, {"correct": 0}).points == 0
    _raises("máximo es 10", score, "per_correct", config, {"correct": 11})
    assert config_max("per_correct", config) == 20
    assert validate_config("per_correct", config)["finalists_only"] is True
    _raises("al menos 1", validate_config, "per_correct", {"points_each": 2, "max_items": 0})
    _raises("verdadero o falso", validate_config, "per_correct",
            {"points_each": 2, "max_items": 3, "finalists_only": "sí"})


# ----------------------------------------------------------------------------
# stations
# ----------------------------------------------------------------------------
def test_stations_sum_completed_ones_and_zero_point_pin():
    everything = [station["key"] for station in MISION["stations"]]
    assert score("stations", MISION, {"completed": everything}).points == 200
    result = score("stations", MISION, {"completed": ["plataforma", "centro_mando"]})
    assert result.points == 50
    marks = {row["key"]: row["completed"] for row in result.breakdown["stations"]}
    assert marks["centro_mando"] is True and marks["reparacion"] is False
    assert score("stations", MISION, {"completed": []}).points == 0
    assert config_max("stations", MISION) == 200


@pytest.mark.parametrize("inputs,fragment", [
    ({"completed": ["luna"]}, "desconocidas: luna"),
    ({"completed": ["plataforma", "plataforma"]}, "repetida"),
    ({"completed": "plataforma"}, "lista"),
    ({"completed": [1]}, "inválidas"),
    ({}, "lista"),
])
def test_stations_reject_bad_inputs(inputs, fragment):
    _raises(fragment, score, "stations", MISION, inputs)


# ----------------------------------------------------------------------------
# group y generales
# ----------------------------------------------------------------------------
def test_group_is_never_scored_and_takes_no_config():
    assert validate_config("group", None) == {}
    _raises("claves desconocidas", validate_config, "group", {"max": 1})
    _raises("no se evalúa", score, "group", {}, {})
    assert config_max("group", {}) is None


def test_unknown_kind_and_formulas_are_rejected():
    _raises("desconocido", validate_config, "formula", {"expr": "a+b"})
    _raises("desconocido", score, "formula", {}, {})
    _raises("objeto", validate_config, "participation", "max=5")


# ----------------------------------------------------------------------------
# honores
# ----------------------------------------------------------------------------
HONORS = [
    {"key": "primeros", "label": "Primeros honores", "min": 950, "max": None},
    {"key": "segundos", "label": "Segundos honores", "min": 800, "max": 949},
    {"key": "terceros", "label": "Terceros honores", "min": None, "max": 799},
]


@pytest.mark.parametrize("total,key", [(1050, "primeros"), (950, "primeros"), (949.5, "segundos"),
                                       (800, "segundos"), (799.99, "terceros"), (-40, "terceros")])
def test_honor_bands(total, key):
    bands = validate_honor_bands(HONORS)
    assert honor_for(Decimal(str(total)), bands)["key"] == key


def test_honor_band_validation():
    _raises("sólo una banda", validate_honor_bands,
            [{"key": "a", "label": "A", "min": None}, {"key": "b", "label": "B", "min": None}])
    _raises("mínimo repetido", validate_honor_bands,
            [{"key": "a", "label": "A", "min": 1}, {"key": "b", "label": "B", "min": 1}])
    assert honor_for(Decimal(5), []) is None
    assert honor_for(Decimal(5), validate_honor_bands([{"key": "a", "label": "A", "min": 10}])) is None
