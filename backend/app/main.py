import base64, hashlib, json, re, secrets, unicodedata, uuid
from io import BytesIO
from datetime import date
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_db
from app.models import Application, Certificate, CertificateEvent, CertificateTemplate, Club, Honor, Ministry, Organization

app=FastAPI(title=settings.APP_NAME,version="0.2.0")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_list,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class PrototypeBatchCreate(BaseModel):
    recipient_names:list[str]=Field(min_length=1,max_length=200)
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
    images:list[str]=Field(min_length=1)
    page_width_in:float=Field(gt=0)
    page_height_in:float=Field(gt=0)
    item_width_in:float=Field(gt=0)
    item_height_in:float=Field(gt=0)
    margin_in:float=Field(default=0,ge=0)
    gap_in:float=Field(default=0,ge=0)
    allow_rotation:bool=True
    crop_marks:bool=False

def slugify(value:str)->str:
    value=unicodedata.normalize("NFKD",value).encode("ascii","ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+","-",value).strip("-").lower() or "especialidad"

def cert_no()->str:return f"CC-{secrets.token_hex(5).upper()}"

def canonical(c:Certificate)->dict:
    return {"certificate_no":c.certificate_no,"application_id":str(c.application_id) if c.application_id else None,"ministry_id":str(c.ministry_id),"organization_id":str(c.organization_id),"club_id":str(c.club_id) if c.club_id else None,"honor_id":str(c.honor_id) if c.honor_id else None,"template_id":str(c.template_id),"recipient_name":c.recipient_name,"honor_name":c.honor_name_snapshot,"club_name":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"place":c.place,"instructor_name":c.instructor_name,"director_name":c.director_name}

def hash_cert(c:Certificate)->str:
    raw=json.dumps(canonical(c),sort_keys=True,ensure_ascii=False,separators=(",",":"))
    return hashlib.sha256(raw.encode()).hexdigest()

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

@app.get("/api/v1/honors")
async def honors(q:str|None=None,ministry:str="pathfinders",db:AsyncSession=Depends(get_db)):
    m=(await db.execute(select(Ministry).where(Ministry.slug==ministry))).scalar_one_or_none()
    if not m:return []
    stmt=select(Honor).where(Honor.ministry_id==m.id,Honor.active.is_(True))
    if q:stmt=stmt.where(Honor.name.ilike(f"%{q}%"))
    rows=(await db.execute(stmt.order_by(Honor.name).limit(500))).scalars().all()
    return [{"id":str(x.id),"name":x.name,"slug":x.slug,"image_url":x.image_url,"source_url":x.source_url,"active":x.active} for x in rows]

@app.post("/api/v1/certificates/prototype-batch",status_code=201)
async def prototype_batch(payload:PrototypeBatchCreate,db:AsyncSession=Depends(get_db)):
    ministry=(await db.execute(select(Ministry).where(Ministry.slug=="pathfinders"))).scalar_one()
    approw=(await db.execute(select(Application).where(Application.slug=="conquistadores"))).scalar_one_or_none()
    org=(await db.execute(select(Organization).where(Organization.code=="PROTOTYPE"))).scalar_one_or_none()
    if not org:
        org=Organization(id=uuid.uuid4(),type="club_network",name="Red Global de Certificados — Prototipo",code="PROTOTYPE",status="active");db.add(org);await db.flush()
    club=(await db.execute(select(Club).where(Club.organization_id==org.id,Club.ministry_id==ministry.id,Club.name==payload.club_name.strip()))).scalar_one_or_none()
    if not club:
        club=Club(id=uuid.uuid4(),organization_id=org.id,ministry_id=ministry.id,name=payload.club_name.strip(),status="active");db.add(club);await db.flush()
    honor=await db.get(Honor,payload.honor_id) if payload.honor_id else None
    if not honor:
        honor=(await db.execute(select(Honor).where(Honor.ministry_id==ministry.id,Honor.name.ilike(payload.honor_name.strip())))).scalar_one_or_none()
    if not honor:
        base=slugify(payload.honor_name);slug=base;i=2
        while (await db.execute(select(Honor).where(Honor.ministry_id==ministry.id,Honor.slug==slug))).scalar_one_or_none():slug=f"{base}-{i}";i+=1
        honor=Honor(id=uuid.uuid4(),ministry_id=ministry.id,name=payload.honor_name.strip(),slug=slug,source_url="https://www.guiasmayores.com/especialidades-ja.html",active=True);db.add(honor);await db.flush()
    tname=f"Prototipo {payload.width_in:g}x{payload.height_in:g}in"
    template=(await db.execute(select(CertificateTemplate).where(CertificateTemplate.ministry_id==ministry.id,CertificateTemplate.name==tname))).scalar_one_or_none()
    if not template:
        template=CertificateTemplate(id=uuid.uuid4(),ministry_id=ministry.id,name=tname,width=payload.width_in,height=payload.height_in,unit="in",orientation="landscape",bleed=0,safe_margin=.125,supports_svg=False,active=True);db.add(template);await db.flush()
    created=[]
    for raw in payload.recipient_names:
        name=raw.strip()
        if len(name)<2:continue
        c=Certificate(id=uuid.uuid4(),application_id=approw.id if approw else None,ministry_id=ministry.id,organization_id=org.id,club_id=club.id,honor_id=honor.id,template_id=template.id,certificate_no=cert_no(),recipient_name=name,club_name_snapshot=club.name,honor_name_snapshot=honor.name,issued_date=payload.issued_date,place=payload.place,instructor_name=payload.instructor_name,director_name=payload.director_name,status="issued")
        c.certificate_hash=hash_cert(c);db.add(c);await db.flush();db.add(CertificateEvent(id=uuid.uuid4(),certificate_id=c.id,event_type="issued",metadata_json={"hash":c.certificate_hash}));created.append(c)
    await db.commit()
    return [{"id":str(c.id),"certificate_no":c.certificate_no,"recipient_name":c.recipient_name,"honor_name_snapshot":c.honor_name_snapshot,"club_name_snapshot":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"status":c.status,"certificate_hash":c.certificate_hash} for c in created]

@app.get("/api/v1/certificates/verify/{certificate_no}")
async def verify(certificate_no:str,db:AsyncSession=Depends(get_db)):
    c=(await db.execute(select(Certificate).where(Certificate.certificate_no==certificate_no))).scalar_one_or_none()
    if not c:raise HTTPException(404,"Certificado no encontrado")
    current=hash_cert(c);org=await db.get(Organization,c.organization_id);valid=c.status=="issued" and c.certificate_hash==current
    return {"valid":valid,"status":"válido" if valid else ("modificado" if c.certificate_hash!=current else c.status),"certificate_no":c.certificate_no,"recipient_name":c.recipient_name,"honor_name":c.honor_name_snapshot,"club_name":c.club_name_snapshot,"issued_date":c.issued_date.isoformat(),"issuer_name":org.name if org else None,"hash_short":(c.certificate_hash or "")[:12] or None}

def _fit(pw,ph,iw,ih,m,g):
    uw,uh=pw-m*2,ph-m*2
    if uw<=0 or uh<=0:return (0,0,0)
    cols=int((uw+g)//(iw+g));rows=int((uh+g)//(ih+g));return cols,rows,cols*rows

@app.post("/api/v1/printing/pdf")
async def printing_pdf(payload:PrintPdfRequest):
    pt=72.;pw,ph=payload.page_width_in*pt,payload.page_height_in*pt;ow,oh=payload.item_width_in*pt,payload.item_height_in*pt;m,g=payload.margin_in*pt,payload.gap_in*pt
    candidates=[];c,r,n=_fit(pw,ph,ow,oh,m,g);candidates.append((n,False,c,r,ow,oh))
    if payload.allow_rotation:c,r,n=_fit(pw,ph,oh,ow,m,g);candidates.append((n,True,c,r,oh,ow))
    count,rotated,cols,rows,sw,sh=max(candidates,key=lambda x:(x[0],not x[1]))
    if count<1:raise HTTPException(422,"El certificado no cabe físicamente en la hoja.")
    out=BytesIO();pdf=canvas.Canvas(out,pagesize=(pw,ph))
    for idx,image in enumerate(payload.images):
        slot=idx%count
        if slot==0 and idx>0:pdf.showPage()
        row,col=divmod(slot,cols);x=m+col*(sw+g);y=ph-m-(row+1)*sh-row*g
        raw=image.split(",",1)[1] if "," in image else image;reader=ImageReader(BytesIO(base64.b64decode(raw)))
        if rotated:
            pdf.saveState();pdf.translate(x+sw/2,y+sh/2);pdf.rotate(90);pdf.drawImage(reader,-ow/2,-oh/2,width=ow,height=oh,preserveAspectRatio=True,anchor="c");pdf.restoreState()
        else:pdf.drawImage(reader,x,y,width=sw,height=sh,preserveAspectRatio=True)
    pdf.save()
    return Response(out.getvalue(),media_type="application/pdf",headers={"Content-Disposition":'attachment; filename="certificados-impresion.pdf"'})
