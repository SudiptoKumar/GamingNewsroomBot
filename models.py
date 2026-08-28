from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class Candidate:
    title:str
    url:str
    source:str
    domain:str
    published_at:datetime
    summary:str=''
    image_url:str=''
    discovery:str='rss'
    tier:int=2
    age_bucket:str='primary'
    trending:bool=False
    cluster_id:str=''

@dataclass
class Classification:
    index:int
    score:int
    important:bool
    reason:str
    category:str
    exclusive:bool=False
    confidence:str='confirmed'
    trending:bool=False
    age_bucket:str='primary'

@dataclass
class Story:
    headline:str
    summary:str
    highlights:list[str]
    what_to_know:str
    platforms:list[str]
    hashtags:list[str]
    badge:str
    source:str
    url:str
    image_url:Optional[str]=''
    score:int=0
    category:str='Gaming'
    age_bucket:str='primary'
