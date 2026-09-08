import json
from datetime import datetime, timezone, timedelta
from event_engine import EventEngine
from article_normalizer import hard_dedup, canonical_url
from post_validator import validate_story

class Resp:
    def __init__(self, obj):
        self.choices=[type('C',(),{'message':type('M',(),{'content':json.dumps(obj)})()})()]

def fake_ai(**kw):
    name=kw['response_format']['json_schema']['name']
    blocks=kw['messages'][1]['content'].split('\n\n')
    ids=[int(next(line for line in b.splitlines() if line.startswith('ID: ')).split(':',1)[1]) for b in blocks]
    if name=='gaming_event_identity_v2':
        items=[]
        for i,b in zip(ids,blocks):
            title=next(line for line in b.splitlines() if line.startswith('Title: '))[7:]
            if 'Zelda' in title:
                obj=dict(event_key='zelda ocarina remake announcement',subject='Zelda Ocarina remake',event_type='announcement',action='announce',status='current',modality='confirmed',game='Zelda Ocarina remake',institution='Nintendo',target='remake',platforms=['Switch 2'],claim='Nintendo announced the remake',topic='Nintendo')
            elif 'GTA' in title:
                obj=dict(event_key='gta6 release update',subject='GTA 6 release update',event_type='release',action='update',status='current',modality='confirmed',game='GTA 6',institution='Rockstar Games',target='release',platforms=['PS5','Xbox Series X|S'],claim='GTA 6 release information',topic='Major Releases')
            else:
                obj=dict(event_key='minor patch',subject='Minor patch',event_type='patch',action='patch',status='current',modality='confirmed',game='Small game',institution='Indie',target='patch',platforms=['PC'],claim='Minor patch',topic='Game Updates')
            items.append({'id':i,**obj})
        return Resp({'items':items})
    if name=='gaming_significance_v2':
        return Resp({'items':[{'id':i,'significance':45 if i==1 else 8,'reason':'Major event' if i==1 else 'Routine item'} for i in ids]})
    if name=='gaming_material_change_v2':
        return Resp({'same_event':True,'material_change':False,'new_claims':[],'reason':'No material development'})
    raise AssertionError(name)

def article(title, source, minutes=0):
    dt=datetime.now(timezone.utc)-timedelta(minutes=minutes)
    return {'title':title,'url':f'https://{source.lower()}.example/{minutes}','canonical':f'{source.lower()}.example/{minutes}','source':source,'region':'Gaming','excerpt':'Substantive report','published_date':dt.isoformat(),'first_seen_at':dt.isoformat()}

def test_dedup_and_event_cluster():
    engine=EventEngine(fake_ai,datetime.now(timezone.utc),80,35)
    selected, clusters=engine.run([article('Nintendo announces Zelda remake','VGC',5),article('Zelda remake screenshots revealed','IGN',10),article('Minor patch released','IGN',15)],{},20)
    assert len(clusters)==2
    z=next(c for c in clusters if c['event_subject']=='Zelda Ocarina remake')
    assert len(z['articles'])==2
    assert z['importance_score']>=80
    assert len(selected)==1

def test_cross_run_suppresses_repeat():
    now=datetime.now(timezone.utc)
    engine=EventEngine(fake_ai,now,80,35)
    previous={'evt_old':{'event_id':'evt_old','status':'published','event_key':'zelda ocarina remake announcement','event_subject':'Zelda Ocarina remake','event_frame':{'game':'Zelda Ocarina remake','event_type':'announcement','institution':'Nintendo','action':'announce','target':'remake','modality':'confirmed'},'headline':'Zelda remake','summary':'Nintendo announced it','claims':['Nintendo announced the remake']}}
    selected, clusters=engine.run([article('Zelda remake screenshots revealed','VGC',5)],previous,20)
    assert selected==[]
    assert clusters[0]['repeat_status']=='repeat'

def test_material_update_can_publish():
    class UpdateEngine(EventEngine):
        def material_change(self, cluster, previous):
            return True,'new official release date',['release date confirmed']
    now=datetime.now(timezone.utc)
    engine=UpdateEngine(fake_ai,now,80,35)
    previous={'evt_old':{'event_id':'evt_old','status':'published','event_key':'zelda ocarina remake announcement','event_subject':'Zelda Ocarina remake','event_frame':{'game':'Zelda Ocarina remake','event_type':'announcement','institution':'Nintendo','action':'announce','target':'remake','modality':'confirmed'},'headline':'Zelda remake','summary':'Nintendo announced it','claims':['Nintendo announced the remake']}}
    selected, _=engine.run([article('Zelda remake release date confirmed','VGC',5)],previous,20)
    assert selected and selected[0]['repeat_status']=='material_update'

def test_validator_rejects_markdown_leak():
    ok, errors=validate_story({'headline':'**Zelda**','summary':'Clean summary','highlights':['a','b','c'],'importance_score':90})
    assert not ok and 'markdown_asterisk' in errors

def test_canonical_and_hard_dedup():
    assert canonical_url('https://www.example.com/story/?utm_source=x')=='example.com/story'
    out=hard_dedup([article('Same News','IGN'), article('Same News','VGC')])
    assert len(out)==2  # different canonical URLs are retained despite same headline
