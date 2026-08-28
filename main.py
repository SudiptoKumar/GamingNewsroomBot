import argparse,logging
from datetime import datetime,timezone
from config import *
from discovery import discover
from editorial import classify,generate_story
from publisher import publish,self_test as publisher_test
from article import fetch_article
from state import load_state,load_posted,save

logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')

def selftest():
    from discovery import parse_dt,canon
    from editorial import extract_json
    assert parse_dt('2026-08-28T12:00:00Z').tzinfo is not None
    assert canon('https://www.example.com/a/?utm_source=x')=='https://example.com/a'
    x=extract_json('```json\n{"classifications":[]}\n```');assert x=={'classifications':[]}
    publisher_test();print('GamingNewsroom V1 self-test passed.')

def run():
    if not (EXA_API_KEY and CEREBRAS_API_KEY and TOKEN): raise SystemExit('Missing EXA_API_KEY, CEREBRAS_API_KEY, or TELEGRAM_BOT_TOKEN')
    state=load_state();posted=load_posted();clock=datetime.now(timezone.utc)
    candidates,health=discover(clock,False)
    print(f'PRIMARY CANDIDATES={len(candidates)}')
    if len(candidates)<THIN_DAY_THRESHOLD:
        extra,_=discover(clock,True);candidates+=extra
        # exact URL dedup
        seen=set();candidates=[c for c in sorted(candidates,key=lambda x:x.published_at,reverse=True) if not (c.url in seen or seen.add(c.url))]
        print(f'AFTER FALLBACK CANDIDATES={len(candidates)}')
    # cluster by normalized title tokens
    clusters={}
    import re,hashlib
    for c in candidates:
        key=' '.join(sorted(set(re.findall(r'[a-z0-9]+',c.title.lower())))-{'the','a','and','of','to','in','for','on','at','is','new'})
        cid=hashlib.sha1(key.encode()).hexdigest()[:12] if key else hashlib.sha1(c.url.encode()).hexdigest()[:12]
        c.cluster_id=cid;clusters.setdefault(cid,[]).append(c)
    reps=[]
    for group in clusters.values(): reps.append(sorted(group,key=lambda x:(x.tier,-x.published_at.timestamp()))[0])
    reps=sorted(reps,key=lambda x:x.published_at,reverse=True)
    classifications=classify(reps,posted)
    important=[c for c in classifications if c.important and c.score>=THRESHOLD]
    print(f'IMPORTANT STORIES={len(important)}')
    if len(important)>CIRCUIT_BREAKER: important=sorted(important,key=lambda x:x.score,reverse=True)[:CIRCUIT_BREAKER]
    stories=[]
    for cl in important:
        candidate=reps[cl.index]
        article=fetch_article(candidate.url)
        if article.get('image_url') and not candidate.image_url: candidate.image_url=article['image_url']
        story=generate_story(candidate,cl,article)
        if story: stories.append(story)
    print(f'FINAL PUBLISHABLE STORIES={len(stories)}')
    successful=publish(stories)
    published=len(successful)
    for s in successful: posted.add(s.url)
    state['source_health']=health;state['last_run']=clock.isoformat();state['last_published']=published
    save(state,posted)
    print(f'DONE published={published}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');a=p.parse_args();selftest() if a.self_test else run()
