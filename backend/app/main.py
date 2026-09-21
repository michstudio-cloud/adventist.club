import re, unicodedata, uuid
from typing import Literal
from datetime import date
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_db
from app.printing import LayoutRequest, compute_layout, render_pdf
from slowapi.errors import RateLimitExceeded
from app.models import Application, Certificate, Honor, Ministry, Organization
from app.monitoring import init_sentry
from app.rate_limit import limiter, rate_limit_exceeded_handler
from app.routers import auth as auth_router, honors as honors_router, media as media_router, org as org_router, portfolio as portfolio_router, render as render_router, users as users_router
# Issuance lives in the service so the portfolio issues the very same certificate; the names stay importable from here.
from app.services.certificates import get_or_create_club, get_or_create_template, hash_cert, issue_certificate, resolve_issuer_organization

init_sentry()  # no-op unless SENTRY_DSN is set
app=FastAPI(title=settings.APP_NAME,version="0.3.0")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_list,allow_credentials=True,allow_methods=["*"],allow_headers=["*"],expose_headers=["X-Total-Count"])
app.state.limiter=limiter
app.add_exception_handler(RateLimitExceeded,rate_limit_exceeded_handler)
# GET /api/v1/honors (public catalogue) now lives in app/routers/honors.py with the rest of the honors workflow.
for _router in (auth_router,users_router,org_router,honors_router,media_router,render_router,portfolio_router):app.include_router(_router.router)

class PrototypeBatchCreate(BaseModel):
    recipient_names:list[str]=Field(min_length=1,max_length=200)
    ministry:str=Field(default="pathfinders",min_length=2,max_length=60)
    application:str=Field(default="conquistadores",min_length=2,max_length=80)
    honor_id:uuid.UUID|None=None
    honor_name:str=Field(min_length=2,max_length=180)
    club_name:str=Field(min_length=2,max_length=180)
    issued_date:date
    place:str|None=None
    instructor_name:str|None=None
    director_name:str|None=None
    width_in:float=Field(gt=0)
    height_in:float=Field(gt=0)

class PrintPdfRequest(BaseModel):
    """Legacy shape (margin_in / gap_in) plus the full imposition options.

    Per-side margins, gap_x/gap_y, bleed and orientation are optional; when
    absent they fall back to margin_in / gap_in so existing clients keep working.
    """
    images:list[str]=Field(min_length=1)
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
    return [{"id":str(x.id),"slug":x.slug,"name":x.name} for x in rows]

@app.get("/api/v1/applications")
async def applications(db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(Application).where(Application.status=="active").order_by(Application.name))).scalars().all()
    return [{"id":str(x.id),"slug":x.slug,"name":x.name,"domain":x.domain,"ministry_id":str(x.ministry_id) if x.ministry_id else None} for x in rows]

@app.post("/api/v1/certificates/prototype-batch",status_code=201)
async def prototype_batch(payload:PrototypeBatchCreate,db:AsyncSession=Depends(get_db)):
    ministry=(await db.execute(select(Ministry).where(Ministry.slug==payload.ministry,Ministry.status=="active"))).scalar_one_or_none()
    if not ministry:raise HTTPException(404,"Ministerio no encontrado.")
    approw=(await db.execute(select(Application).where(Application.slug==payload.application))).scalar_one_or_none()
    org=await resolve_issuer_organization(db)
    club=await get_or_create_club(db,org.id,ministry.id,payload.club_name.strip())
    honor=await db.get(Honor,payload.honor_id) if payload.honor_id else None
    if not honor:
        # Honor versions share a name: take the newest live one instead of failing on duplicates.
        honor=(await db.execute(select(Honor).where(Honor.ministry_id==ministry.id,Honor.name.ilike(payload.honor_name.strip())).order_by(Honor.active.desc(),Honor.version.desc(),Honor.created_at.desc()).limit(1))).scalars().first()
    if not honor:
        base=slugify(payload.honor_name);slug=base;i=2
        while (await db.execute(select(Honor).where(Honor.ministry_id==ministry.id,Honor.slug==slug))).scalar_one_or_none():slug=f"{base}-{i}";i+=1
        honor=Honor(id=uuid.uuid4(),ministry_id=ministry.id,name=payload.honor_name.strip(),slug=slug,source_url="https://www.guiasmayores.com/especialidades-ja.html",active=True,status="DRAFT");db.add(honor);await db.flush()
    tname=f"Prototipo {payload.width_in:g}x{payload.height_in:g}in"
    template=await get_or_create_template(db,ministry.id,tname,payload.width_in,payload.height_in)
    created=[]
    for raw in payload.recipient_names:
        name=raw.strip()
        if len(name)<2:continue
        created.append(await issue_certificate(db,ministry_id=ministry.id,application_id=approw.id if approw else None,organization=org,club=club,honor_id=honor.id,honor_name=honor.name,template=template,recipient_name=name,issued_date=payload.issued_date,place=payload.place,instructor_name=payload.instructor_name,director_name=payload.director_name))
    await db.commit()
    return [{"id":str(c.id),"certificate_no":c.certificate_no,"recipient_name":c.recipient_name,"honor_name_snapshot":c.honor_name_snapshot,"club_name_snapshot":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"status":c.status,"certificate_hash":c.certificate_hash} for c in created]

@app.get("/api/v1/certificates/verify/{certificate_no}")
async def verify(certificate_no:str,db:AsyncSession=Depends(get_db)):
    c=(await db.execute(select(Certificate).where(Certificate.certificate_no==certificate_no))).scalar_one_or_none()
    if not c:raise HTTPException(404,"Certificado no encontrado")
    current=hash_cert(c);org=await db.get(Organization,c.organization_id);valid=c.status=="issued" and c.certificate_hash==current
    return {"valid":valid,"status":"válido" if valid else ("modificado" if c.certificate_hash!=current else c.status),"certificate_no":c.certificate_no,"recipient_name":c.recipient_name,"honor_name":c.honor_name_snapshot,"club_name":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"issuer_name":org.name if org else None,"hash_short":(c.certificate_hash or "")[:12] or None}

@app.post("/api/v1/printing/layout")
async def printing_layout(payload:LayoutRequest):
    """How many certificates fit and where; same maths the PDF uses."""
    return compute_layout(payload).as_dict()

@app.post("/api/v1/printing/pdf")
async def printing_pdf(payload:PrintPdfRequest):
    layout=compute_layout(payload.layout_request())
    if layout.per_page<1:raise HTTPException(422,"El certificado no cabe físicamente en la hoja.")
    try:pdf_bytes=render_pdf(payload.images,layout,crop_marks=payload.crop_marks)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return Response(pdf_bytes,media_type="application/pdf",headers={"Content-Disposition":'attachment; filename="certificados-impresion.pdf"'})
