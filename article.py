import re
import requests
from html import unescape
from config import HEADERS, REQUEST_TIMEOUT

def fetch_article(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=REQUEST_TIMEOUT,allow_redirects=True)
        r.raise_for_status(); text=r.text
    except Exception:
        return {'text':'','image_url':''}
    image=''
    m=re.search(r'<meta[^>]+property=[\"\']og:image[\"\'][^>]+content=[\"\']([^\"\']+)',text,re.I)
    if not m: m=re.search(r'<meta[^>]+content=[\"\']([^\"\']+)[\"\'][^>]+property=[\"\']og:image[\"\']',text,re.I)
    if m: image=unescape(m.group(1))
    body=re.sub(r'(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>',' ',text)
    body=re.sub(r'(?is)<[^>]+>',' ',body)
    body=re.sub(r'\s+',' ',unescape(body)).strip()
    return {'text':body[:12000],'image_url':image}
