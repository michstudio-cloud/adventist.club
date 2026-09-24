import re, unicodedata, uuid
from typing import Literal
from datetime import date
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_db
from app.printing import LayoutRequest, compute_layout, render_pdf
from slowapi.errors import RateLimitExceeded
from app.models import Application, Certificate, Honor, Ministry, Organization, User
from app.deps import get_optional_user
from app.schemas.portfolio import SIGNATURE_MAX_LENGTH
from app.services import certificate_signatures
from app.certificates.render import TemplateError, load_template
from app.services.portfolio import DEFAULT_CERTIFICATE_TEMPLATE
from app.monitoring import init_sentry
from app.rate_limit import account_or_ip, limiter, rate_limit_exceeded_handler
from app.routers import auth as auth_router, clubs as clubs_router, honors as honors_router, media as media_router, memberships as memberships_router, org as org_router, portfolio as portfolio_router, render as render_router, users as users_router
# Bloque B: la carta de la iglesia (verificación del instructor virtual) y los cursos.
from app.routers import church_letters as church_letters_router, courses as courses_router
# Bloque C: los intentos de examen del curso.
from app.routers import exams as exams_router
# Bloque F: el catálogo de programas (clases de club, Guía Mayor, EMC, CMJA) y el registro
# de actividades (horas de servicio y asistencia).
from app.routers import activity as activity_router, programs as programs_router
# Mapas: el token de corta vida que MapKit JS (Apple Maps) necesita en el navegador.
from app.routers import maps as maps_router
# Bloque G: el perfil público, el XP y la barra de buena conducta.
from app.routers import profiles as profiles_router
# Bloque H: la secretaría del club (cargos, pasar lista, página pública y puntuación).
from app.routers import secretaria as secretaria_router
from app.routers import club_classes as club_classes_router
# La ficha de especialidad en PDF, generada desde los datos propios (reemplaza los PDF de terceros).
from app.routers import honor_sheets as honor_sheets_router
from app.routers import notifications as notifications_router
# Miniaturas de plantilla del asistente (GET cacheable en R2; antes, un POST /render por miniatura).
from app.routers import template_thumbnails as template_thumbnails_router
# El campo «Club» del asistente: clubes registrados (lookup público) y el club propio.
from app.routers import club_lookup as club_lookup_router
# Issuance lives in the service so the portfolio issues the very same certificate; the names stay importable from here.
from app.services.certificates import REVOKED_STATUS, course_context, get_or_create_club, get_or_create_template, hash_cert, issue_certificate, resolve_issuer_organization, template_slug

init_sentry()  # no-op unless SENTRY_DSN is set
app=FastAPI(title=settings.APP_NAME,version="0.3.0")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_list,allow_credentials=True,allow_methods=["*"],allow_headers=["*"],expose_headers=["X-Total-Count"])
app.state.limiter=limiter
app.add_exception_handler(RateLimitExceeded,rate_limit_exceeded_handler)
# GET /api/v1/honors (public catalogue) now lives in app/routers/honors.py with the rest of the honors workflow.
for _router in (auth_router,users_router,org_router,honors_router,media_router,render_router,portfolio_router,church_letters_router,courses_router,memberships_router,clubs_router,exams_router,programs_router,activity_router,maps_router):app.include_router(_router.router)
app.include_router(profiles_router.router)
app.include_router(profiles_router.clubs_router)
app.include_router(secretaria_router.router)
app.include_router(club_classes_router.router)
app.include_router(honor_sheets_router.router)
app.include_router(notifications_router.router)
app.include_router(template_thumbnails_router.router)
app.include_router(club_lookup_router.router)

class PrototypeBatchCreate(BaseModel):
    recipient_names:list[str]=Field(min_length=1,max_length=200)
    ministry:str=Field(default="pathfinders",min_length=2,max_length=60)
    application:str=Field(default="conquistadores",min_length=2,max_length=80)
    honor_id:uuid.UUID|None=None
    honor_name:str=Field(min_length=2,max_length=180)
    club_name:str=Field(min_length=2,max_length=180)
    # A registered club picked in the assistant (GET /org-nodes/clubs/lookup). The certificate
    # prints ITS name (club_name is then ignored); 404 `club_not_found` if it is not active.
    club_id:uuid.UUID|None=None
    # «Asociación o misión» of the templates that print it (`association_name`). Kept with the
    # `issued` event, outside the hash, so a folio'd render prints it (services/certificates.py).
    association_name:str|None=Field(default=None,max_length=180)
    issued_date:date
    place:str|None=None
    instructor_name:str|None=None
    director_name:str|None=None
    # SEC-03: open endpoint — bounded so it cannot mint templates of absurd sizes.
    width_in:float=Field(gt=0,le=48)
    height_in:float=Field(gt=0,le=48)
    # 021: kept with the certificates ONLY with a session («para guardar el archivo pidamos que
    # se registren»); without one they are ignored and the signature lives in /render alone.
    # A data URL or the person's own saved signature (services/certificate_signatures.py).
    signature_director:str|None=Field(default=None,max_length=SIGNATURE_MAX_LENGTH)
    signature_instructor:str|None=Field(default=None,max_length=SIGNATURE_MAX_LENGTH)
    # Slug of the server template the assistant rendered (GET /certificates/templates), kept in
    # `certificate_templates.name` with `supports_svg` exactly as the portfolio does, so
    # /verify/{no} offers PNG/PDF for these folios too. None = DEFAULT_CERTIFICATE_TEMPLATE;
    # one the engine does not know is 422 `template_not_found`.
    template:str|None=Field(default=None,pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")

class PrintPdfRequest(BaseModel):
    """Legacy shape (margin_in / gap_in) plus the full imposition options.

    Per-side margins, gap_x/gap_y, bleed and orientation are optional; when
    absent they fall back to margin_in / gap_in so existing clients keep working.
    """
    images:list[str]=Field(min_length=1,max_length=500)  # SEC-03: open endpoint, bounded
    page_width_in:float=Field(gt=0)
    page_height_in:float=Field(gt=0)
    item_width_in:float=Field(gt=0)
    item_height_in:float=Field(gt=0)
    margin_in:float=Field(default=0,ge=0)
    gap_in:float=Field(default=0,ge=0)
    allow_rotation:bool=True
    crop_marks:bool=False
    orientation:Literal["auto","portrait","landscape"]="auto"
    margin_top_in:float|None=Field(default=None,ge=0)
    margin_right_in:float|None=Field(default=None,ge=0)
    margin_bottom_in:float|None=Field(default=None,ge=0)
    margin_left_in:float|None=Field(default=None,ge=0)
    gap_x_in:float|None=Field(default=None,ge=0)
    gap_y_in:float|None=Field(default=None,ge=0)
    bleed_in:float=Field(default=0,ge=0)
    allow_scale_down:bool=False

    def layout_request(self)->LayoutRequest:
        m=lambda v:self.margin_in if v is None else v
        return LayoutRequest(page_width=self.page_width_in,page_height=self.page_height_in,item_width=self.item_width_in,item_height=self.item_height_in,unit="in",orientation=self.orientation,margin_top=m(self.margin_top_in),margin_right=m(self.margin_right_in),margin_bottom=m(self.margin_bottom_in),margin_left=m(self.margin_left_in),gap_x=self.gap_in if self.gap_x_in is None else self.gap_x_in,gap_y=self.gap_in if self.gap_y_in is None else self.gap_y_in,bleed=self.bleed_in,allow_rotation=self.allow_rotation,allow_scale_down=self.allow_scale_down,total_items=len(self.images))

def slugify(value:str)->str:
    value=unicodedata.normalize("NFKD",value).encode("ascii","ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+","-",value).strip("-").lower() or "especialidad"

@app.get("/")
async def root():return {"service":"adventist.club","api":"/api/v1","docs":"/docs"}

@app.get("/api/v1/health")
async def health(db:AsyncSession=Depends(get_db)):
    await db.execute(select(Ministry.id).limit(1))
    return {"ok":True,"service":"adventist.club","database":"connected"}

@app.get("/api/v1/ministries")
async def ministries(db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(Ministry).where(Ministry.status=="active").order_by(Ministry.name))).scalars().all()
    # Public: the ministry pickers (new club, club search) read it. Active ones only.
    return [{"id":str(x.id),"slug":x.slug,"name":x.name,"status":x.status.upper()} for x in rows]

@app.get("/api/v1/applications")
async def applications(db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(Application).where(Application.status=="active").order_by(Application.name))).scalars().all()
    return [{"id":str(x.id),"slug":x.slug,"name":x.name,"domain":x.domain,"ministry_id":str(x.ministry_id) if x.ministry_id else None} for x in rows]

@app.post("/api/v1/certificates/prototype-batch",status_code=201)
# 021: with a session it arrives through the web app's proxy (one address for everybody).
@limiter.limit("30/hour",key_func=account_or_ip)
async def prototype_batch(request:Request,payload:PrototypeBatchCreate,db:AsyncSession=Depends(get_db),viewer:User|None=Depends(get_optional_user)):
    # Owner, 2026-09-24: without an account the assistant issues one certificate per run; batches
    # need a session (the web app enforces it in the form, this makes it true for the API too).
    if viewer is None and len(payload.recipient_names)>1:
        raise HTTPException(422,{"code":"batch_requires_account","detail":"Para emitir varios certificados a la vez, crea tu cuenta."})
    # 021: validated before anything is written. Anonymous: nothing is kept (the certificate
    # re-downloads unsigned, the signature travels only in each POST /render).
    signatures=await certificate_signatures.prepare({"signature_director":payload.signature_director,"signature_instructor":payload.signature_instructor},viewer) if viewer else {}
    ministry=(await db.execute(select(Ministry).where(Ministry.slug==payload.ministry,Ministry.status=="active"))).scalar_one_or_none()
    if not ministry:raise HTTPException(404,"Ministerio no encontrado.")
    approw=(await db.execute(select(Application).where(Application.slug==payload.application))).scalar_one_or_none()
    org=await resolve_issuer_organization(db)
    registered=None
    if payload.club_id:
        registered=await db.get(Organization,payload.club_id)
        if not club_lookup_router.is_active_club(registered):
            raise HTTPException(404,{"code":"club_not_found","detail":"Ese club no está registrado o ya no está activo."})
    # `certificates.club_id` points at the legacy `clubs` table (under the issuing organisation),
    # not at `organizations`: a registered club gets the legacy row with its exact name, as the
    # portfolio does, and the organisation itself travels in the `issued` event.
    club=await get_or_create_club(db,org.id,ministry.id,registered.name if registered else payload.club_name.strip())
    association=(payload.association_name or "").strip() or None
    extra={**({"signed_by":str(viewer.id)} if signatures else {}),**({"club_organization_id":str(registered.id)} if registered else {}),**({"association_name":association} if association else {})}
    honor=await db.get(Honor,payload.honor_id) if payload.honor_id else None
    if not honor:
        # Honor versions share a name: take the newest live one instead of failing on duplicates.
        honor=(await db.execute(select(Honor).where(Honor.ministry_id==ministry.id,Honor.name.ilike(payload.honor_name.strip())).order_by(Honor.active.desc(),Honor.version.desc(),Honor.created_at.desc()).limit(1))).scalars().first()
    if not honor:
        base=slugify(payload.honor_name);slug=base;i=2
        while (await db.execute(select(Honor).where(Honor.ministry_id==ministry.id,Honor.slug==slug))).scalar_one_or_none():slug=f"{base}-{i}";i+=1
        honor=Honor(id=uuid.uuid4(),ministry_id=ministry.id,name=payload.honor_name.strip(),slug=slug,source_url="https://www.guiasmayores.com/especialidades-ja.html",active=True,status="DRAFT");db.add(honor);await db.flush()
    slug=payload.template or DEFAULT_CERTIFICATE_TEMPLATE
    try:svg_template=load_template(slug)
    except TemplateError:raise HTTPException(422,{"code":"template_not_found","detail":"Esa plantilla no existe."})
    # The record keeps the engine's slug and ITS size (the print sheet is the assistant's
    # business: width_in/height_in still only size the imposition on the client).
    w,h=round(svg_template.width_pt/72,4),round(svg_template.height_pt/72,4)
    template=await get_or_create_template(db,ministry.id,slug,w,h,orientation="landscape" if w>=h else "portrait",supports_svg=True)
    created=[]
    for raw in payload.recipient_names:
        name=raw.strip()
        if len(name)<2:continue
        created.append(await issue_certificate(db,ministry_id=ministry.id,application_id=approw.id if approw else None,organization=org,club=club,honor_id=honor.id,honor_name=honor.name,template=template,recipient_name=name,issued_date=payload.issued_date,place=payload.place,instructor_name=payload.instructor_name,director_name=payload.director_name,event_metadata=extra or None))
    # One immutable copy per signature for the whole batch (the folder of its first certificate).
    await certificate_signatures.attach(created,signatures)
    await db.commit()
    return [{"id":str(c.id),"certificate_no":c.certificate_no,"recipient_name":c.recipient_name,"honor_name_snapshot":c.honor_name_snapshot,"club_name_snapshot":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"status":c.status,"certificate_hash":c.certificate_hash} for c in created]

@app.get("/api/v1/certificates/verify/{certificate_no}")
async def verify(certificate_no:str,db:AsyncSession=Depends(get_db)):
    c=(await db.execute(select(Certificate).where(Certificate.certificate_no==certificate_no))).scalar_one_or_none()
    if not c:raise HTTPException(404,"Certificado no encontrado")
    current=hash_cert(c);org=await db.get(Organization,c.organization_id);valid=c.status=="issued" and c.certificate_hash==current
    # Bloque D I7 §5.4: the course and its instructor are shown BY RELATION
    # (certificate -> enrollment -> course), outside the hash: they are context, not the
    # fact certified, and `canonical()` must never change (hallazgo 7). Revocation adds a
    # status and a date and NOTHING else about the person (§5.5): not the reason, which is
    # for the holder and the audit trail, and no identifier.
    # Bloque F §1.7: `kind` tells each frontend whether to write «especialidad» or «investidura».
    mode,course_title=await course_context(db,c)
    # 016: the language it was issued in and its template, so the page can show both.
    slug=await template_slug(db,c.template_id)
    # SEC-03: the open prototype tool (no session) writes certificates too. They are records,
    # not credentials: `official` is True only when a person of the platform issued it (the
    # portfolio or the automatic course certificate always set `issued_by_id`, `user_id` and
    # `enrollment_id`; any one of them survives a deleted account, ON DELETE SET NULL).
    official=any(x is not None for x in (c.issued_by_id,c.user_id,c.enrollment_id))
    if c.certificate_hash!=current:state="modificado"
    elif c.status==REVOKED_STATUS:state="revocado"
    elif valid:state="válido"
    else:state=c.status
    return {"valid":valid,"status":state,"certificate_no":c.certificate_no,"recipient_name":c.recipient_name,"honor_name":c.honor_name_snapshot,"kind":"program" if c.program_id else "honor","club_name":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"issuer_name":org.name if org else None,"hash_short":(c.certificate_hash or "")[:12] or None,"mode":mode,"course_title":course_title,"instructor_name":c.instructor_name,"revoked_at":c.revoked_at.isoformat() if c.revoked_at else None,"official":official,"locale":c.locale or "es","template_slug":slug}

@app.post("/api/v1/printing/layout")
async def printing_layout(payload:LayoutRequest):
    """How many certificates fit and where; same maths the PDF uses."""
    return compute_layout(payload).as_dict()

@app.post("/api/v1/printing/pdf")
@limiter.limit("60/hour")
async def printing_pdf(request:Request,payload:PrintPdfRequest):
    layout=compute_layout(payload.layout_request())
    if layout.per_page<1:raise HTTPException(422,"El certificado no cabe físicamente en la hoja.")
    try:pdf_bytes=render_pdf(payload.images,layout,crop_marks=payload.crop_marks)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return Response(pdf_bytes,media_type="application/pdf",headers={"Content-Disposition":'attachment; filename="certificados-impresion.pdf"'})
