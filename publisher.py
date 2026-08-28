import time
import requests
from telegramify_markdown import richify, telegramify_rich
from models import Story
from config import TOKEN,CHANNEL,POST_DELAY

API='https://api.telegram.org/bot{}/sendRichMessage'

def markdown(story:Story):
    status='🔴 '+story.badge.upper()
    platforms='🎮 '+' • '.join(story.platforms) if story.platforms else ''
    lines=[]
    if story.image_url: lines.append(f'![image]({story.image_url})')
    lines += [f'# {story.headline}','',story.summary,'',f'```\n{status}\n{platforms}\n```','', '## Key Highlights','']
    lines += [f'• {x}' for x in story.highlights]
    lines += ['', '<details>', '<summary>What to Know</summary>', '', story.what_to_know, '', '</details>', '']
    lines += [' '.join(story.hashtags), f'Source: [{story.source}]({story.url})']
    return '\n'.join(lines)

def send(story):
    if not TOKEN or not CHANNEL: raise RuntimeError('TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL missing')
    chunks=telegramify_rich(markdown(story),mode='html')
    for chunk in chunks:
        payload={'chat_id':CHANNEL,'rich_message':chunk.to_dict()}
        r=requests.post(API.format(TOKEN),json=payload,timeout=30)
        r.raise_for_status();data=r.json()
        if not data.get('ok'):raise RuntimeError(data.get('description','Telegram error'))
    return True

def publish(stories):
    successful=[]
    for story in stories:
        try: send(story);count+=1
        except Exception as e: print('TELEGRAM PUBLISH FAILED:',e)
        time.sleep(POST_DELAY)
    return successful

def self_test():
    s=Story('Test Headline','Test summary',['One','Two'],'Test expandable text',['PS5','PC'],['#GamingNews'],'Confirmed','Test Source','https://example.com')
    md=markdown(s);assert '# Test Headline' in md and '<details>' in md and '```' in md and '[Test Source]' in md
    x=richify(md,mode='html').to_dict();assert 'html' in x or 'markdown' in x
