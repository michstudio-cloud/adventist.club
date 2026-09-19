"""Upload normalised patches through the deployed API. Resumable via uploaded.json."""
import json, os, pathlib, sys, time, uuid, urllib.request, urllib.error
API="https://api.adventist.club/api/v1"
env=dict(l.split("=",1) for l in (pathlib.Path.home()/".adventist-migration.env").read_text().splitlines() if "=" in l)
def call(path,data=None,headers=None,raw=None):
    req=urllib.request.Request(API+path,data=raw if raw is not None else (json.dumps(data).encode() if data is not None else None),headers=headers or {"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req,timeout=120))
def login():
    return call("/auth/login",{"email":env["IMPORTER_EMAIL"].strip(),"password":env["IMPORTER_PASSWORD"].strip()})["access_token"]
def upload(token,path,folder="patches"):
    b="----cq"+uuid.uuid4().hex; name=os.path.basename(path); body=open(path,"rb").read()
    parts=(f'--{b}\r\nContent-Disposition: form-data; name="folder"\r\n\r\n{folder}\r\n'.encode()
          +f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: image/webp\r\n\r\n'.encode()+body+f'\r\n--{b}--\r\n'.encode())
    return call(f"/media/upload?folder={folder}",raw=parts,headers={"Authorization":f"Bearer {token}","Content-Type":f"multipart/form-data; boundary={b}"})
limit=int(sys.argv[1]) if len(sys.argv)>1 else 10**9
done=json.load(open("uploaded.json")) if os.path.exists("uploaded.json") else {}
files=sorted(f for f in os.listdir("out") if f.endswith(".webp") and f not in done)[:limit]
token=login(); t0=time.time(); fails=0
for i,f in enumerate(files,1):
    for attempt in (1,2,3):
        try:
            r=upload(token,f"out/{f}"); done[f]={"url":r["url"],"key":r["key"]}; break
        except urllib.error.HTTPError as e:
            if e.code==401: token=login(); continue
            err=f"{e.code} {e.read()[:160]!r}"
        except Exception as e:
            err=str(e)[:160]
        time.sleep(2*attempt)
    else:
        fails+=1; print("FAIL",f,err,flush=True)
    if i%25==0 or i==len(files):
        json.dump(done,open("uploaded.json","w"),indent=1); print(f"{i}/{len(files)} ok={len(done)} fails={fails} {time.time()-t0:.0f}s",flush=True)
json.dump(done,open("uploaded.json","w"),indent=1)
print("DONE total uploaded:",len(done),"fails:",fails)
