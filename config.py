import os
from pathlib import Path

TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
CHANNEL=os.getenv('TELEGRAM_CHANNEL','@GamingNewsroom').strip()
EXA_API_KEY=os.getenv('EXA_API_KEY','').strip()
CEREBRAS_API_KEY=os.getenv('CEREBRAS_API_KEY','').strip()
CEREBRAS_MODEL=os.getenv('CEREBRAS_MODEL','gpt-oss-120b').strip()

LOOKBACK_HOURS=72
PRIMARY_HOURS=24
THRESHOLD=7
THIN_DAY_THRESHOLD=3
CIRCUIT_BREAKER=40
POST_DELAY=2.5
REQUEST_TIMEOUT=25
LLM_TIMEOUT=60
MAX_RSS_PER_SOURCE=20
MAX_EXA_RESULTS=40
SCORE_BATCH_SIZE=8
MAX_GENERATION_RETRIES=4
STATE_FILE=Path('gaming_state.json')
POSTED_FILE=Path('posted_urls.txt')

PRIMARY_SOURCES=[
('VGC','vgc.news',1),('Insider Gaming','insider-gaming.com',1),('Gematsu','gematsu.com',1),('IGN','ign.com',1),
('GameSpot','gamespot.com',1),('Eurogamer','eurogamer.net',1),('GamesIndustry.biz','gamesindustry.biz',1),('PC Gamer','pcgamer.com',1),
('GamesRadar+','gamesradar.com',2),('Polygon','polygon.com',2),('Kotaku','kotaku.com',2),('VG247','vg247.com',2),
('Destructoid','destructoid.com',2),('Rock Paper Shotgun','rockpapershotgun.com',2),('Digital Foundry','eurogamer.net',2),
('Nintendo Life','nintendolife.com',2),('Push Square','pushsquare.com',2),('Pure Xbox','purexbox.com',2),
('Pocket Gamer','pocketgamer.com',2),('Game Developer','gamedeveloper.com',2)]
FALLBACK_SOURCES=[('TheGamer','thegamer.com'),('Siliconera','siliconera.com'),('Wccftech','wccftech.com'),('TouchArcade','toucharcade.com'),('Game Rant','gamerant.com')]

# Direct RSS candidates. A source may have several candidates; first working feed wins.
RSS_CANDIDATES={
'vgc.news':['https://www.videogameschronicle.com/feed/'],
'insider-gaming.com':['https://insider-gaming.com/feed/'],
'gematsu.com':['https://www.gematsu.com/feed'],
'ign.com':['https://feeds.feedburner.com/ign/all'],
'gamespot.com':['https://www.gamespot.com/feeds/news/'],
'eurogamer.net':['https://www.eurogamer.net/feed'],
'gamesindustry.biz':['https://www.gamesindustry.biz/feed'],
'pcgamer.com':['https://www.pcgamer.com/feeds.xml'],
'gamesradar.com':['https://www.gamesradar.com/feeds.xml'],
'polygon.com':['https://www.polygon.com/rss/index.xml'],
'kotaku.com':['https://kotaku.com/rss'],
'vg247.com':['https://www.vg247.com/feed'],
'destructoid.com':['https://www.destructoid.com/feed/'],
'rockpapershotgun.com':['https://www.rockpapershotgun.com/feed'],
'nintendolife.com':['https://www.nintendolife.com/feeds/latest'],
'pushsquare.com':['https://www.pushsquare.com/feeds/latest'],
'purexbox.com':['https://www.purexbox.com/feeds/latest'],
'pocketgamer.com':['https://www.pocketgamer.com/rss.xml'],
'gamedeveloper.com':['https://www.gamedeveloper.com/rss.xml'],
}
HEADERS={'User-Agent':'GamingNewsroom/1.0','Accept':'application/rss+xml, application/xml, text/xml, text/html;q=0.9,*/*;q=0.8'}
