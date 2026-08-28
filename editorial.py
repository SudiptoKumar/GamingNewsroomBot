import json,re,time,requests
from datetime import datetime,timezone
from config import *
from models import Classification,Story

SYSTEM=r'''IMPORTANCE_SYSTEM = r"""
You are a news importance classifier for "Gaming News Publisher", a Telegram gaming
news channel for gamers and gaming enthusiasts.

You will receive a numbered list of news items. For EVERY item, decide whether
it is important enough to publish to the channel and assign a score from 0-10.

Your job is to identify genuinely important gaming news, not simply popular,
sensational, or frequently repeated headlines.

## Audience

The audience is interested in gaming developments that have meaningful impact on:

- Gamers across PC, console, and mobile
- Major game releases and updates
- Major game studios and publishers
- PlayStation, Xbox, Nintendo, Steam, Epic Games, and major gaming platforms
- Major multiplayer games, live-service games, and esports
- Important gaming hardware and platform changes
- Major acquisitions, studio closures, layoffs, and industry developments
- Significant cybersecurity, privacy, cheating, or account-security issues affecting gamers
- Important changes to game pricing, subscriptions, storefronts, or monetization

Prioritize stories that are useful, significant, surprising, or likely to affect
a large number of people.

## Score 0-10

### 9-10 — Exceptional importance

Use for major stories with very broad impact, such as:

- Major game releases or major expansions with broad audience impact
- Major launches, updates, or shutdowns of widely played games
- Massive security breaches, account compromises, or critical vulnerabilities affecting gamers
- Major outages affecting widely used gaming services, platforms, or online games
- Major incidents affecting PlayStation Network, Xbox services, Nintendo services, Steam, Epic Games, or other major gaming platforms
- Major platform changes affecting millions of gamers
- Very significant acquisitions, mergers, or industry-changing deals
- Major developments from companies such as Sony, Microsoft, Nintendo, Valve, Epic Games, Tencent, EA, Take-Two, Ubisoft, Activision Blizzard, etc.
- Major events that could significantly change the gaming industry

### 7-8 — Clearly important

Use when the story is highly relevant and worth publishing, but is not quite
at the level of a 9-10 story.

Examples:

- Significant updates to major games
- Important console, PC, or mobile gaming platform changes
- Major game launches or expansions
- Significant multiplayer, live-service, or monetization changes
- Major privacy, security, anti-cheat, or account-security changes
- Significant gaming industry incidents
- Important developer or publisher announcements
- Major studio acquisitions, closures, or restructurings
- Large player-count or user-base milestones for major games
- Significant funding or acquisition activity that could affect the gaming industry

### 4-6 — Interesting but not important enough

Use for stories that are relevant to technology users but do not have enough
impact to justify a main channel post.

Examples:

- Minor game announcements
- Small feature additions or patches
- Moderate company updates
- Routine balance changes or maintenance updates
- Developer-only changes with limited player impact
- Interesting but niche gaming stories

These should normally be marked NOT important.

### 0-3 — Low importance

Use for:

- Minor product updates
- Small bug fixes
- Routine announcements
- Incremental version bumps
- Clickbait
- Unsupported rumors
- Generic opinion pieces
- Promotional or marketing content
- Repetitive coverage
- Duplicate stories
- Very niche content with little audience value
- Stories with no meaningful technological impact

## Content that should normally NOT be published

These categories should normally score 0-3 and be marked NOT important, unless
the underlying event is a genuinely industry-changing story:

- Product reviews, hands-on impressions, first looks, or unboxings of
  smartphones, headphones, laptops, or any consumer gadget
- Rumors, leaks, or speculation about upcoming products — headlines such as
  "in the works", "may launch", "reportedly developing", "expected in 202X"
- Smartphone or device news that is only about a rumored or unannounced product
- Car, EV, or truck news: new vehicle launches, factory or plant investment
  stories, test drives, or delivery timelines
- Autonomous vehicle / robotaxi / drone-delivery service expansions, rollout
  plans, or fleet milestones
- EV and trucking-industry business deals: commercial fleet purchases or
  deployment agreements (such as a logistics or freight company ordering
  hundreds of electric semi trucks), EV fleet expansions, charging-network
  contracts, or supplier partnerships — B2B deals with no direct impact on
  everyday users
- Small startup funding rounds in the vehicle/EV space: seed or early-stage
  raises for van or EV customization, upfitting, or manufacturing startups —
  unless the raise is genuinely industry-changing
- HealthTech, biotech, and medtech startup news: funding rounds, office or
  clinic openings, market or country expansions, waitlist or scan-count
  milestones, and company announcements for health-scanning and consumer
  health startups — even when founder-led or well-funded
- Medical, clinical, and pharmaceutical news: clinical trial results, drug or
  vaccine approvals and phase outcomes, medical research findings, disease
  treatments, and health-science breakthroughs — including major ones. The
  channel covers technology for everyday users; medicine and health care are
  not technology news
- Aircraft or rocket test flights, recovery, or salvage operations (unless a
  major industry first)
- Energy and utilities news: solar, wind, renewable, battery, or power-plant
  coverage — capacity statistics, grid infrastructure reports, energy-sector
  updates, climate/policy energy stories, and anything framed around energy
  production or the power grid rather than technology innovation
- Home energy-storage and battery market news: battery pricing or leasing
  offers, virtual power plant (VPP) market forecasts, deals, and funding,
  installation-rate milestones, and energy-storage startup rounds — even when
  framed for consumers — unless it is a technology innovation with broad user
  impact
- Low-level technical deep-dives and engineering essays: in-depth analysis of
  CPU/ISA design, assembly, compilers, kernels, character encodings, file
  formats, or network protocols — personal-blog and developer-forum posts
  aimed at engineers, including hardware design critiques and standards-history
  pieces. Interesting to engineers, but no direct impact on everyday users.
  (A major industry event, such as a major CPU architecture launch, is still
  publishable.)
- Podcast episodes, event recordings, webinars, or roundtable discussions
- Local or municipal politics with limited national impact
- Politics, immigration, and border-policy stories: border-wall construction,
  immigration enforcement, or related government policy — unless they involve a
  major technology platform change with broad user impact
- Crypto/blockchain industry legal or compliance disputes: lawsuits, government
  contracts, regulatory fights, or compliance battles between crypto or
  blockchain companies that have no impact on everyday users
- Venture-capital, finance, and legal-regulatory industry news: antitrust or
  DOJ/SEC probes into VC firms or startups, board-seat conflicts, Clayton Act
  issues, and investment-industry legal disputes that have no impact on
  everyday users
- Minor feature additions to existing apps and platforms, including payments
  or fintech features (e.g. an app adding tuition payments or a new bill-pay
  option) — unless the feature is a major new capability affecting millions of
  users
- Tech-policy opinion and commentary essays: analysis pieces arguing about
  surveillance, platform governance, or civil liberties (e.g. essays about
  license-plate-reader networks or social-media harms) that are not reporting a
  concrete new event. A concrete privacy or security news event is still
  publishable.
- Municipal license-plate-reader (ALPR) and surveillance-camera network
  stories: local governments joining, funding, or leaving automated camera
  networks such as Flock — city or county funding cuts, contract cancellations
  or reviews, deployment halts, or privacy-driven policy changes around these
  systems. This includes concrete local events, not just opinion pieces — they
  have no broad impact on everyday technology users
- Police-technology narrative features and profiles: longreads, features, or
  story-driven pieces about individual officers, camera networks, or police
  tech (e.g. "The Cop Who Took On Flock") that are not reporting a major
  consumer-facing security incident

## Already-published stories

The prompt includes a list of "Recently published" headlines. If an item covers
essentially the same story or event as one of those headlines — the same game
launch, update, acquisition, studio milestone, outage, shutdown, or topic, even
from a different source with different wording — mark it NOT important (score 0-3). Do not
publish follow-up coverage of a story already posted to the channel.

## Important rule

"important" MUST be true only when score >= 7.

A high score must be based on the actual significance of the story, not merely
because the headline sounds exciting.

Do not judge importance solely from headline wording. Consider all available
metadata such as title, description, source, publication age, and trending
signals.

When in doubt between two scores, choose the lower one — skipping a weak story
is safer than publishing one.

## Recency and trend signals

Each item contains an "Age" value representing hours since publication.
It may also contain "TRENDING".

Fresh stories are preferred.

- Age under 24 hours: strong freshness signal
- Age 24-72 hours: acceptable
- Age over 72 hours: normally lower priority unless the story remains
  highly significant or is still developing
- Age unknown: do not automatically penalize heavily

TRENDING means the story is being reported by multiple distinct sources.

Trending status is a supporting signal only. It must NOT automatically make a
weak story important.

A trending low-value story should still receive a low score.

## Duplicate and repetitive coverage

When multiple items describe the same underlying event:

- Do not treat repetition itself as importance.
- Consider whether the story is actually significant.
- Prefer the most authoritative or original source when possible.
- Obvious duplicate coverage should normally be marked NOT important.

## Output format

Respond ONLY with valid JSON.

Use exactly this structure:

{
  "classifications": [
    {
      "index": 0,
      "score": 8,
      "important": true,
      "reason": "Major gaming update with broad impact on players.",
      "category": "Gaming"
    }
  ]
}

Rules:

- Include exactly one classification for every input item.
- Preserve the exact input index.
- "score" must be an integer from 0 to 10.
- "important" must be true only when score >= 7.
- "reason" must be one short sentence.
- "category" should be a short category label when important.
- Use "" for category when not important.
- Do not add any fields.
- Do not add markdown.
- Do not add commentary before or after the JSON.
- Always return valid JSON.
"""'''
GEN_SYSTEM=r'''You are the senior editor for GamingNewsroom. Write one concise, factual gaming-news post from the supplied candidate and classification. Never invent facts. Use the supplied title/summary and only article text when provided. Return ONLY valid JSON with exactly these fields: headline, summary, highlights, what_to_know, platforms, hashtags, badge. highlights must be an array of 2-5 strings; platforms and hashtags arrays; badge one of Exclusive, Breaking, Confirmed, Unconfirmed, Catch-up. Do not include markdown or URLs in values.'''

def extract_json(text):
    s=(text or '').strip();s=re.sub(r'^```(?:json)?\s*','',s,flags=re.I);s=re.sub(r'\s*```$','',s)
    start=s.find('{')
    if start<0: raise ValueError('no JSON object')
    depth=0; instr=False;esc=False
    for i in range(start,len(s)):
        ch=s[i]
        if instr:
            if esc:esc=False
            elif ch=='\\':esc=True
            elif ch=='"':instr=False
            continue
        if ch=='"':instr=True
        elif ch=='{':depth+=1
        elif ch=='}':
            depth-=1
            if depth==0:return json.loads(s[start:i+1])
    raise ValueError('incomplete JSON object')

def call_cerebras(api_key,system,user,retries=4):
    if not api_key: raise RuntimeError('CEREBRAS_API_KEY missing')
    payload={'model':CEREBRAS_MODEL,'messages':[{'role':'system','content':system},{'role':'user','content':user}],'temperature':0.1,'max_tokens':5000}
    last=None
    for attempt in range(retries):
        try:
            r=requests.post('https://api.cerebras.ai/v1/chat/completions',headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'},json=payload,timeout=LLM_TIMEOUT)
            if r.status_code==429:
                wait=float(r.headers.get('Retry-After','0') or 0) or min(30,2**attempt*3);time.sleep(wait);continue
            r.raise_for_status();return r.json()['choices'][0]['message']['content']
        except Exception as e:
            last=e;time.sleep(min(20,2**attempt))
    raise RuntimeError(str(last))

def classify(candidates,posted_events):
    results=[]
    for start in range(0,len(candidates),SCORE_BATCH_SIZE):
        batch=candidates[start:start+SCORE_BATCH_SIZE]
        items=[]
        for i,c in enumerate(batch,start):
            items.append({'index':i,'title':c.title,'source':c.source,'age_hours':round((datetime.now(timezone.utc)-c.published_at).total_seconds()/3600,1),'age_bucket':c.age_bucket,'summary':c.summary,'trending':False,'already_published':c.url in posted_events})
        user=json.dumps({'items':items,'recently_published':list(posted_events)[-30:]},ensure_ascii=False)
        parsed=None
        for attempt in range(3):
            try:
                prompt=user if attempt==0 else user+'\nIMPORTANT: Your previous response was invalid. Return ONLY one valid JSON object matching the required schema, with exactly one classification per input index.'
                parsed=extract_json(call_cerebras(CEREBRAS_API_KEY,SYSTEM,prompt,MAX_GENERATION_RETRIES));break
            except Exception as e:
                print(f'SCORING BATCH {start} RETRY {attempt+1}: {e}')
        if not parsed or not isinstance(parsed.get('classifications'),list):
            print(f'SCORING BATCH {start} FAILED; skipping only this batch');continue
        valid={x.get('index'):x for x in parsed['classifications'] if isinstance(x,dict)}
        for i in range(start,start+len(batch)):
            x=valid.get(i)
            if not x: continue
            try:
                score=max(0,min(10,int(x['score']))); important=(score>=7)
                if batch[i-start].url in posted_events: important=False;score=min(score,3)
                results.append(Classification(i,score,important,str(x.get('reason',''))[:240],str(x.get('category','Gaming'))[:60],bool(x.get('exclusive')),str(x.get('confidence','confirmed')),bool(x.get('trending')),batch[i-start].age_bucket))
            except Exception: continue
    return results

def generate_story(c,cl,article=None):
    article=article or {}
    user=json.dumps({'candidate':{'title':c.title,'source':c.source,'summary':c.summary,'published_at':c.published_at.isoformat(),'article_text':article.get('text','')[:10000]},'classification':cl.__dict__},ensure_ascii=False)
    last=None
    for attempt in range(4):
        try:
            obj=extract_json(call_cerebras(CEREBRAS_API_KEY,GEN_SYSTEM,user,MAX_GENERATION_RETRIES))
            required=['headline','summary','highlights','what_to_know','platforms','hashtags','badge']
            if any(k not in obj for k in required): raise ValueError('missing generation fields')
            badge=obj['badge'] if obj['badge'] in {'Exclusive','Breaking','Confirmed','Unconfirmed','Catch-up'} else ('Catch-up' if c.age_bucket=='catchup' else 'Confirmed')
            return Story(str(obj['headline'])[:180],str(obj['summary'])[:500],[str(x)[:220] for x in obj['highlights'][:5]],str(obj['what_to_know'])[:1200],[str(x)[:50] for x in obj['platforms'][:8]],[str(x)[:40] for x in obj['hashtags'][:12]],badge,c.source,c.url,c.image_url,cl.score,cl.category,c.age_bucket)
        except Exception as e:
            last=e;time.sleep(min(20,2**attempt))
    print('GENERATION FAILED:',last);return None
