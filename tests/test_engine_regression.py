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
                obj=dict(event_key='zelda ocarina remake announcement',subject='Zelda Ocarina remake',event_type='announcement',action='announce',status='current',modality='confirmed',game='Zelda Ocarina remake',franchise='The Legend of Zelda',institution='Nintendo',target='remake',platforms=['Switch 2'],claim='Nintendo announced the remake',topic='Nintendo')
            elif 'GTA' in title:
                obj=dict(event_key='gta6 release update',subject='GTA 6 release update',event_type='release',action='update',status='current',modality='confirmed',game='GTA 6',franchise='Grand Theft Auto',institution='Rockstar Games',target='release',platforms=['PS5','Xbox Series X|S'],claim='GTA 6 release information',topic='Major Releases')
            else:
                obj=dict(event_key='minor patch',subject='Minor patch',event_type='patch',action='patch',status='current',modality='confirmed',game='Small game',franchise='Small game',institution='Indie',target='patch',platforms=['PC'],claim='Minor patch',topic='Game Updates')
            items.append({'id':i,**obj})
        return Resp({'items':items})
    if name=='gaming_significance_v2':
        return Resp({'items':[{'id':i,'significance':45 if i==1 else 8,'reason':'Major event' if i==1 else 'Routine item'} for i in ids]})

    if name=='gaming_editorial_slate_v3_1':
        return Response({'selected_ids':[1],'decisions':[{'id':1,'decision':'select','reason':'Strongest in slate.'},{'id':2,'decision':'reject','reason':'Same franchise/topic repetition.'}]})
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


def test_same_franchise_slate_is_deconcentrated():
    now=datetime.now(timezone.utc)
    engine=EventEngine(fake_ai,now,80,35)
    a=article('Zelda concert announced','VGC',1)
    b=article('Zelda remake teased','IGN',2)
    selected,_=engine.run([a,b],{},20)
    assert len(selected)==1


def test_v31_broad_pool_allows_editor_to_choose_below_preferred_threshold():
    now=datetime.now(timezone.utc)
    engine=EventEngine(fake_ai,now,80,35,candidate_threshold=60,publish_floor=70)
    def stub_ai(**kwargs):
        name=kwargs['response_format']['json_schema']['name']
        if name=='gaming_editorial_slate_v3_1':
            return Response({'selected_ids':[1,2,3,4], 'decisions':[
                {'id':1,'decision':'select','reason':'Strong Zelda story.'},
                {'id':2,'decision':'select','reason':'Independent studio story.'},
                {'id':3,'decision':'select','reason':'Same Zelda franchise repetition.'},
                {'id':4,'decision':'select','reason':'Independent GTA story.'},
            ]})
        return fake_ai(**kwargs)
    engine.ai_create=stub_ai
    frame=lambda game,franchise,topic,event_type: {'game':game,'franchise':franchise,'institution':'Publisher','event_type':event_type,'action':'announce','target':game,'modality':'confirmed'}
    clusters=[
        {'cluster_id':'a','event_key':'zelda-remake','event_subject':'Zelda remake','event_frame':frame('Zelda','The Legend of Zelda','Zelda Anniversary','reveal'),'event_type':'reveal','topic':'Zelda Anniversary','modality':'confirmed','importance_score':74,'publishable':True,'editor_eligible':True,'representative':{},'sources':['VGC'],'articles':[{}]},
        {'cluster_id':'b','event_key':'studio-layoffs','event_subject':'Studio layoffs','event_frame':frame('Studio X','Studio X','Industry','layoff'),'event_type':'layoff','topic':'Industry','modality':'confirmed','importance_score':73,'publishable':True,'editor_eligible':True,'representative':{},'sources':['Game Developer'],'articles':[{}]},
        {'cluster_id':'c','event_key':'zelda-lego','event_subject':'Zelda LEGO','event_frame':frame('Zelda','The Legend of Zelda','Zelda Anniversary','announcement'),'event_type':'announcement','topic':'Zelda Anniversary','modality':'confirmed','importance_score':72,'publishable':True,'editor_eligible':True,'representative':{},'sources':['IGN'],'articles':[{}]},
        {'cluster_id':'d','event_key':'gta6','event_subject':'GTA 6','event_frame':frame('GTA 6','Grand Theft Auto','GTA 6','release'),'event_type':'release','topic':'GTA 6','modality':'confirmed','importance_score':71,'publishable':True,'editor_eligible':True,'representative':{},'sources':['GameSpot'],'articles':[{}]},
    ]
    selected=engine.diversify(clusters,20)
    assert [c['cluster_id'] for c in selected]==['a','b','d']
