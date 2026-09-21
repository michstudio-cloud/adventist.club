"""The review workflow shared by honors and by the courses of Bloque B.

    DRAFT -> ZONE_REVIEW -> ASSOCIATION_REVIEW -> PUBLISHED   (-> ARCHIVED)

Only constants live here: the stages, who decides at each one and the actions a
reviewer may take. `routers/honors.py` and `routers/courses.py` both import them so
the two workflows can never drift apart (spec Bloque B §3.4).
"""
from app.security import (
    ADMIN_ASSOCIATION,
    ADMIN_DIVISION,
    ADMIN_UNION,
    COORDINATOR_ZONE,
    MASTER_GC,
)

DRAFT = "DRAFT"
ZONE_REVIEW = "ZONE_REVIEW"
ASSOCIATION_REVIEW = "ASSOCIATION_REVIEW"
PUBLISHED = "PUBLISHED"
ARCHIVED = "ARCHIVED"

APPROVE = "APPROVE"
REJECT = "REJECT"
REQUEST_CHANGES = "REQUEST_CHANGES"

ASSOCIATION_REVIEWERS = (ADMIN_ASSOCIATION, ADMIN_UNION, ADMIN_DIVISION, MASTER_GC)
# A zone coordinator only gives the first step; everyone above may give either one
# (today almost no association has zone coordinators).
ZONE_REVIEWERS = (COORDINATOR_ZONE, *ASSOCIATION_REVIEWERS)

# Who may act on a draft at each review stage.
STAGE_REVIEWERS = {ZONE_REVIEW: ZONE_REVIEWERS, ASSOCIATION_REVIEW: ASSOCIATION_REVIEWERS}
