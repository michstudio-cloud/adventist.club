"""Normalise downloaded patches: <=512px, WebP with alpha, stable slug file names."""
import json, os, sys, hashlib
from PIL import Image
m=json.load(open("manifest.json")); os.makedirs("out",exist_ok=True)
seen={}; done=[]; missing=0; bytes_in=bytes_out=0
for o in m:
    ext=o["image_src"].rsplit(".",1)[-1].lower()
    local=f"img/{hashlib.sha1(o['image_src'].encode()).hexdigest()[:16]}.{ext}"
    if not os.path.exists(local): missing+=1; continue
    slug=o["slug"]
    if slug in seen: slug=f"{slug}-{o['category_slug']}"          # same honor listed in two categories
    if slug in seen: slug=f"{slug}-{len(seen)}"
    seen[slug]=1
    dest=f"out/{slug}.webp"
    try:
        if os.path.exists(dest) and os.path.getsize(dest)>500:      # resumable
            im=Image.open(dest)
            bytes_in+=os.path.getsize(local); bytes_out+=os.path.getsize(dest)
            done.append({**{k:o[k] for k in ("name","category_slug","category_name","requirements_pdf","source_url")},
                         "slug":slug,"image_file":f"{slug}.webp","source_image_url":o["image_src"],"width":im.width,"height":im.height})
            continue
        im=Image.open(local); im.seek(0); im=im.convert("RGBA")
        bbox=im.getbbox()                                            # trim fully transparent margins
        if bbox: im=im.crop(bbox)
        im.thumbnail((512,512),Image.LANCZOS)
        im.save(dest,"WEBP",quality=86,method=4)
    except Exception as e:
        print("ERR",local,e); continue
    bytes_in+=os.path.getsize(local); bytes_out+=os.path.getsize(dest)
    done.append({**{k:o[k] for k in ("name","category_slug","category_name","requirements_pdf","source_url")},
                 "slug":slug,"image_file":f"{slug}.webp","source_image_url":o["image_src"],"width":im.width,"height":im.height})
json.dump(done,open("catalog.json","w"),ensure_ascii=False,indent=1)
print(f"processed {len(done)} (not downloaded yet: {missing}); {bytes_in/1e6:.1f}MB -> {bytes_out/1e6:.1f}MB")
