import hashlib,re,time
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse,urlunparse,parse_qsl,urlencode
import xml.etree.ElementTree as ET
import requests
from config import *
from models import Candidate

S=requests.Session()
def now(): return datetime.now(timezone.utc)
def parse_dt(v):
    if not v:return None
    if isinstance(v,datetime): return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s=str(v).strip()
    for fn in (lambda x:datetime.fromisoformat(x.replace('Z','+00:00')), parsedate_to_datetime):
        try:
            d=fn(s); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception: pass
    return None

def canon(u):
    try:
        p=urlparse(u)
        if not p.netloc:return ''
        q=[(k,v) for k,v in parse_qsl(p.query) if k.lower() not in {'utm_source','utm_medium','utm_campaign','utm_term','utm_content','gclid','fbclid','ref'}]
        return urlunparse(('https',p.netloc.lower().removeprefix('www.'),p.path.rstrip('/') or '/', '',urlencode(q),'')).rstrip('/')
    except Exception:return ''
def dom(u): return urlparse(u).netloc.lower().removeprefix('www.')
def strip(x): return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',x or '')).strip()
def xml_items(raw):
    root=ET.fromstring(raw); rows=[]
    for item in root.iter():
        tag=item.tag.rsplit('}',1)[-1]
        if tag not in ('item','entry'): continue
        d={}
        for c in item:
            k=c.tag.rsplit('}',1)[-1]
            val=(c.text or '').strip()
            if k=='link' and c.attrib.get('href'): val=c.attrib['href']
            d.setdefault(k,val)
        rows.append(d)
    return rows

def google_feed(domain):
    from urllib.parse import urlencode
    q=f'site:{domain} (gaming OR games OR videogame OR playstation OR xbox OR nintendo) when:3d'
    return 'https://news.google.com/rss/search?'+urlencode({'q':q,'hl':'en-US','gl':'US','ceid':'US:en'})

def source_feed(name,domain,tier,clock):
    urls=RSS_CANDIDATES.get(domain,[])+[google_feed(domain)]
    last=''
    for feed in urls:
        try:
            r=S.get(feed,headers=HEADERS,timeout=REQUEST_TIMEOUT); r.raise_for_status(); rows=xml_items(r.content)
        except Exception as e:
            last=str(e); continue
        accepted=[]
        for row in rows[:MAX_RSS_PER_SOURCE]:
            title=strip(row.get('title'))
            link=canon(row.get('link',''))
            pub=parse_dt(row.get('pubDate') or row.get('published') or row.get('updated') or row.get('date'))
            if not title or not link or not pub: continue
            if 'news.google.com' in dom(link):
                try:
                    rr=S.get(row.get('link',''),headers=HEADERS,timeout=10,allow_redirects=True); link=canon(rr.url)
                except Exception: continue
            if not link or not (dom(link)==domain or dom(link).endswith('.'+domain)): continue
            age=(clock-pub).total_seconds()/3600
            if age < -2 or age>LOOKBACK_HOURS: continue
            bucket='primary' if age<=PRIMARY_HOURS else 'catchup'
            accepted.append(Candidate(title,link,name,domain,pub,strip(row.get('description',''))[:1200],row.get('image','') or '', 'rss',tier,bucket))
        return accepted,{'ok':True,'feed':feed,'entries':len(rows),'accepted':len(accepted),'error':''}
    return [],{'ok':False,'feed':'','entries':0,'accepted':0,'error':last[:240]}

def exa(clock,source_rows):
    if not EXA_API_KEY:return []
    domains=list(dict.fromkeys(d for _,d,*_ in source_rows))
    body={'query':'important gaming news releases updates outages acquisitions announcements','numResults':MAX_EXA_RESULTS,'includeDomains':domains,'startPublishedDate':(clock-timedelta(hours=LOOKBACK_HOURS)).replace(microsecond=0).isoformat().replace('+00:00','Z'),'endPublishedDate':clock.replace(microsecond=0).isoformat().replace('+00:00','Z'),'contents':{'highlights':True},'type':'auto'}
    try:
        r=S.post('https://api.exa.ai/search',headers={'x-api-key':EXA_API_KEY,'Content-Type':'application/json'},json=body,timeout=40);r.raise_for_status(); data=r.json()
    except Exception as e:
        print('EXA FAILED:',e);return []
    out=[]
    for x in data.get('results',[]):
        u=canon(x.get('url','')); d=dom(u); pub=parse_dt(x.get('publishedDate') or x.get('published_date')); title=str(x.get('title') or '').strip()
        if not u or not pub or not title or not any(d==sd or d.endswith('.'+sd) for sd in domains):continue
        age=(clock-pub).total_seconds()/3600
        if age< -2 or age>LOOKBACK_HOURS:continue
        src=next((n for n,sd,*_ in source_rows if d==sd or d.endswith('.'+sd)),d); tier=next(((rest[0] if rest else 2) for n,sd,*rest in source_rows if d==sd or d.endswith('.'+sd)),2)
        h=x.get('highlights') or []
        out.append(Candidate(title,u,src,d,pub,' '.join(map(str,h))[:1200],x.get('image') or '','exa',tier,'primary' if age<=PRIMARY_HOURS else 'catchup'))
    print('EXA accepted=',len(out));return out

def discover(clock=None,fallback=False):
    clock=clock or now(); sources=FALLBACK_SOURCES if fallback else PRIMARY_SOURCES; rows=[]; health={}
    for src in sources:
        name,d,*tier=src;t=tier[0] if tier else 2
        if fallback:
            batch,st=source_feed(name,d,t,clock)
        else: batch,st=source_feed(name,d,t,clock)
        rows+=batch;health[name]=st
    rows+=exa(clock,sources)
    # exact URL dedup
    seen=set(); out=[]
    for c in sorted(rows,key=lambda z:z.published_at,reverse=True):
        if c.url in seen:continue
        seen.add(c.url);out.append(c)
    return out,health
