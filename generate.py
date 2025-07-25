import os
import datetime
import json
import logging
import requests
import bleach
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv
from openai import OpenAI
from feedgen.feed import FeedGenerator
from fetch_news import fetch_articles

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Required config
OPENAI_API_KEY    = os.getenv('OPENAI_API_KEY')
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL')
MODEL_NAME        = os.getenv('MODEL_NAME', 'gpt-4o-mini')
BLOG_BASE_URL     = os.getenv('BLOG_BASE_URL', 'https://zerodaycyber.io/cybersecurity-for-healthcare')
LOGO_URL          = os.getenv('JSON_LD_LOGO_URL', f'{BLOG_BASE_URL}/logo.png')
HL_API_URL        = os.getenv('HL_API_URL')
HL_API_KEY        = os.getenv('HL_API_KEY')
HL_AUTOMATION_ID  = os.getenv('HL_AUTOMATION_ID')
BOOKING_LINK      = os.getenv('BOOKING_LINK')

# Fail-fast
for var in ['OPENAI_API_KEY','TEAMS_WEBHOOK_URL']:
    if not os.getenv(var):
        logging.error(f'Missing required env var: {var}')
        raise RuntimeError(f'Missing required env var: {var}')

# Initialize OpenAI client
gpt = OpenAI(api_key=OPENAI_API_KEY)

# HTTP POST with retry
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def post_with_retry(url, json_payload, headers=None):
    r = requests.post(url, json=json_payload, headers=headers or {}, timeout=10)
    r.raise_for_status()
    return r

# JSON-LD template
JSON_LD_TEMPLATE = ('<script type="application/ld+json">{{'
    '"@context":"https://schema.org",'
    '"@type":"NewsArticle",'
    '"headline":"{title}",'
    '"datePublished":"{date}",'
    '"author":{{"@type":"Person","name":"Andres Torres"}},'
    '"publisher":{{"@type":"Organization","name":"Zeroday Cybersecurity","logo":{{"@type":"ImageObject","url":"{logo}"}}}},'
    '"mainEntityOfPage":{{"@type":"WebPage","@id":"{url}"}}'
    '}}</script>')

# Prompts
SYSTEM_MSG  = {'role':'system','content':'You are Zeroday Cyber Concierge AI.'}
DRAFT_PROMPT = os.getenv('DRAFT_PROMPT','Generate an AI-SEO optimized weekly Cybersecurity News post...')

# Fact-check prompt
def fact_verify(html):
    return f"Verify technical claims and list discrepancies:\n<html>{html}</html>"

# RSS validation
def validate_rss(path='rss.xml'):
    import xmlschema
    xmlschema.XMLSchema11('https://www.w3.org/2005/Atom').validate(path)
    logging.info('RSS validation passed')

# Teams notification
def notify_teams(title, message):
    if not TEAMS_WEBHOOK_URL:
        return
    payload = {'text': f"**{title}**\n{message}"}
    try:
        requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=5)
    except Exception:
        logging.error('Failed to send Teams notification')

# Main pipeline
def generate_and_publish():
    try:
        articles = fetch_articles()
        if not articles:
            logging.info('No articles, skipping draft.')
            return

        # Draft vs. last-pub delta-check
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse('rss.xml')
            dates = [item.find('pubDate').text for item in tree.findall('.//item')]
            last_date = max(dates)
            new_articles = [a for a in articles if a['url'] not in dates]
            if not new_articles:
                logging.info('No new articles beyond last publish date')
                return
        except Exception:
            logging.info('No existing rss.xml or parse error; proceeding')
            new_articles = articles

        # Dynamic model selection
        threshold = int(os.getenv('FALLBACK_THRESHOLD','5'))
        model = os.getenv('FALLBACK_MODEL','gpt-3.5-turbo') if len(new_articles)<=threshold else MODEL_NAME

        # 1) Draft
        resp = gpt.chat.completions.create(
            model=model,
            messages=[SYSTEM_MSG, {'role':'user','content': DRAFT_PROMPT + json.dumps(new_articles)}]
        )
        raw = resp.choices[0].message.content
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.error('Invalid JSON: %s', raw)
            notify_teams('Draft JSON Error', raw[:200])
            raise

        # Save social snippets
        with open('social.json','w') as f:
            json.dump({'social_snippets':data.get('social_snippets',[])}, f)

        # 2) Fact-check
        verify = gpt.chat.completions.create(
            model=model,
            messages=[SYSTEM_MSG, {'role':'user','content':fact_verify(data.get('html',''))}]
        )
        if 'discrepancy' in verify.choices[0].message.content.lower():
            notify_teams('Fact-Check Failed', verify.choices[0].message.content)
            raise RuntimeError('Fact-check failure')

        # 3) Sanitize HTML
        soup = BeautifulSoup(data['html'],'html.parser')
        clean = bleach.clean(str(soup), tags=bleach.ALLOWED_TAGS+['h2','h3','img'], attributes={'img':['src','alt']})

        # 4) Build content + JSON-LD + disclaimer
        post_id = datetime.datetime.utcnow().isoformat()
        url = f"{BLOG_BASE_URL}/blog/{post_id}"
        ld  = JSON_LD_TEMPLATE.format(title=data['title'],date=post_id,logo=LOGO_URL,url=url)
        disclaimer = '<p><strong>Disclaimer:</strong> Informational only.</p>'
        content = disclaimer + ld + clean

        # 5) Atomic RSS write
        tmp='rss.tmp.xml'
        fg = FeedGenerator()
        fg.id(BLOG_BASE_URL)
        fg.title(os.getenv('RSS_TITLE','Medical Cyber News | Insights'))
        fg.link(href=BLOG_BASE_URL, rel='alternate')
        fg.link(href=f"{BLOG_BASE_URL}/rss.xml", rel='self')
        fg.language('en')
        fe=fg.add_entry()
        fe.id(url)
        fe.title(data['title'])
        fe.content(content, type='html')
        fe.pubDate(datetime.datetime.utcnow())
        fg.rss_file(tmp)
        os.replace(tmp,'rss.xml')
        validate_rss('rss.xml')
        logging.info('Draft ready: %s', data['title'])

    except Exception as e:
        logging.exception('Pipeline error')
        notify_teams('Pipeline Error',str(e))
        raise

if __name__=='__main__':
    generate_and_publish()
