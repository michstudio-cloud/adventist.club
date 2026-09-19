import json, os, time, urllib.request, urllib.parse, hashlib
UA="Mozilla/5.0 (adventist.club catalog importer; contact michstudio.com@gmail.com)"
m=json.load(open("manifest.json")); ok=fail=skip=0
for i,o in enumerate(m):
    ext=o["image_src"].rsplit(".",1)[-1].lower()
    o["local"]=f"img/{hashlib.sha1(o['image_src'].encode()).hexdigest()[:16]}.{ext}"
    if os.path.exists(o["local"]) and os.path.getsize(o["local"])>200: skip+=1; continue
    try:
        p=urllib.parse.urlsplit(o["image_src"]); url=urllib.parse.urlunsplit(p._replace(path=urllib.parse.quote(p.path,safe="/%")))
        data=urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":UA}),timeout=40).read()
        open(o["local"],"wb").write(data); ok+=1
    except Exception as e:
        o["error"]=str(e)[:120]; fail+=1
    time.sleep(0.4)
    if i%100==0: print(i,ok,fail,skip,flush=True)
json.dump(m,open("manifest.local.json","w"),ensure_ascii=False,indent=1)
print("DONE ok",ok,"fail",fail,"skip",skip)
