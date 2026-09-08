import json
from datetime import datetime, timezone, timedelta
from event_engine import EventEngine, corroboration_score

class Response:
    def __init__(self, payload):
        self.choices=[type('C',(),{'message':type('M',(),{'content':json.dumps(payload)})()})()]

def fake_ai(**kwargs):
    name=kwargs['response_format']['json_schema']['name']
    blocks=kwargs['messages'][1]['content'].split('\n\n')
    ids=[int(next(x for x in b.splitlines() if x.startswith('ID: ')).split(':',1)[1]) for b in blocks]
    if name=='gaming_event_identity_v2':
        items=[]
        for i,b in zip(ids,blocks):
            title=next(x for x in b.splitlines() if x.startswith('Title: '))[7:]
            z='Zelda' in title
            items.append({'id':i,'event_key':'zelda ocarina remake announcement' if z else 'minor patch','subject':'Zelda Ocarina remake' if z else 'Minor patch','event_type':'announcement' if z else 'patch','action':'announce' if z else 'patch','status':'current','modality':'confirmed','game':'Zelda Ocarina remake' if z else 'Small game','franchise':'The Legend of Zelda' if z else 'Small game','institution':'Nintendo' if z else 'Indie','target':'remake' if z else 'patch','platforms':['Switch 2'] if z else ['PC'],'claim':'Nintendo announced the remake' if z else 'Minor patch','topic':'Nintendo' if z else 'Game Updates'})
        return Response({'items':items})
    if name=='gaming_significance_v2':
        return Response({'items':[{'id':i,'significance':45 if i==1 else 8,'reason':'Major event' if i==1 else 'Routine item'} for i in ids]})

    if name=='gaming_editorial_slate_v3_1':
        return Response({'selected_ids':[1],'decisions':[{'id':1,'decision':'select','reason':'Strongest in slate.'},{'id':2,'decision':'reject','reason':'Same franchise/topic repetition.'}]})
    if name=='gaming_material_change_v2':
        return Response({'same_event':True,'material_change':False,'new_claims':[],'reason':'No material development'})
    raise AssertionError(name)

def item(title, source, minutes=0):
    dt=datetime.now(timezone.utc)-timedelta(minutes=minutes)
    return {'title':title,'url':f'https://{source.lower()}.example/{minutes}','canonical':f'{source.lower()}.example/{minutes}','source':source,'region':'Gaming','excerpt':'Substantive report','published_date':dt.isoformat(),'first_seen_at':dt.isoformat()}

def test_zeldas_cluster_as_one_event_and_score_cluster_evidence():
    now=datetime.now(timezone.utc)
    e=EventEngine(fake_ai,now,80,35)
    selected, clusters=e.run([item('Nintendo announces Zelda remake','VGC',5),item('Zelda remake screenshots revealed','IGN',10),item('Small patch released','IGN',15)],{},20)
    assert len(clusters)==2
    z=next(c for c in clusters if c['event_subject']=='Zelda Ocarina remake')
    assert len(z['articles'])==2
    assert z['independent_source_count']>=1
    assert z['importance_score']>=80
    assert len(selected)==1

def test_corroboration_scale():
    assert corroboration_score(0)==0
    assert corroboration_score(1)==2
    assert corroboration_score(3)==10
    assert corroboration_score(5)==17

def test_cross_run_repeat_suppression():
    now=datetime.now(timezone.utc)
    e=EventEngine(fake_ai,now,80,35)
    prev={'old':{'event_id':'old','status':'published','event_key':'zelda ocarina remake announcement','event_subject':'Zelda Ocarina remake','event_frame':{'game':'Zelda Ocarina remake','event_type':'announcement','institution':'Nintendo','action':'announce','target':'remake','modality':'confirmed'},'headline':'Zelda remake','summary':'Nintendo announced it','claims':['Nintendo announced the remake']}}
    selected, clusters=e.run([item('Zelda remake screenshots revealed','VGC',5)],prev,20)
    assert not selected
    assert clusters[0]['repeat_status']=='repeat'



def test_same_franchise_slate_is_deconcentrated():
    now=datetime.now(timezone.utc)
    engine=EventEngine(fake_ai,now,80,35)
    def stub_ai(**kwargs):
        name=kwargs['response_format']['json_schema']['name']
        if name=='gaming_editorial_slate_v3_1':
            return Response({'selected_ids':[1,2],'decisions':[
                {'id':1,'decision':'select','reason':'Highest value.'},
                {'id':2,'decision':'select','reason':'AI initially selected; hard guard must reject repetition.'},
            ]})
        return fake_ai(**kwargs)
    engine.ai_create=stub_ai
    frame=lambda game,franchise,topic,event_type: {'game':game,'franchise':franchise,'institution':'Nintendo','event_type':event_type,'action':'announce','target':game,'modality':'confirmed'}
    clusters=[
        {'cluster_id':'a','event_key':'zelda concert','event_subject':'Zelda concert','event_frame':frame('Zelda concert','The Legend of Zelda','Zelda 40th','announcement'),'event_type':'announcement','topic':'Zelda 40th','modality':'confirmed','importance_score':91,'publishable':True,'editor_eligible':True,'representative':{},'sources':['VGC'],'articles':[{}]},
        {'cluster_id':'b','event_key':'zelda remake','event_subject':'Zelda remake','event_frame':frame('Zelda remake','The Legend of Zelda','Zelda 40th','reveal'),'event_type':'reveal','topic':'Zelda 40th','modality':'confirmed','importance_score':86,'publishable':True,'editor_eligible':True,'representative':{},'sources':['IGN'],'articles':[{}]},
        {'cluster_id':'c','event_key':'gta6 release','event_subject':'GTA 6 release','event_frame':frame('GTA 6','Grand Theft Auto','GTA 6','release'),'event_type':'release','topic':'Major Releases','modality':'confirmed','importance_score':84,'publishable':True,'editor_eligible':True,'representative':{},'sources':['GameSpot'],'articles':[{}]},
    ]
    selected=engine.diversify(clusters,20)
    assert [c['cluster_id'] for c in selected]==['a']
