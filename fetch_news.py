import os
import requests
import logging
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Required config
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY')
NEWS_QUERY = os.getenv(
    'NEWS_QUERY',
    'elective medicine cybersecurity OR med spa cybersecurity OR plastic surgery cybersecurity OR fertility cybersecurity OR "24-hour vet" cybersecurity OR dermatology cybersecurity OR cosmetic dentistry cybersecurity OR ophthalmology cybersecurity OR IV therapy cybersecurity'
)
WHITELIST_DOMAINS = os.getenv('WHITELIST_DOMAINS', 'newsapi.org,cisa.gov,fda.gov,healthit.gov,cybereason.com').split(',')
MAX_ARTICLES = int(os.getenv('MAX_ARTICLES', '16'))

# Fail fast
if not NEWSAPI_KEY:
    logging.error('Missing NEWSAPI_KEY')
    raise RuntimeError('Missing NEWSAPI_KEY')

# HTTP GET helper with retry & timeout
def http_get(url, **kwargs):
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _get():
        resp = requests.get(url, timeout=10, **kwargs)
        resp.raise_for_status()
        return resp
    return _get()

# Fetch whitelisted articles
def fetch_articles():
    seven_days = (datetime.utcnow() - timedelta(days=7)).isoformat()
    url = (
        f'https://newsapi.org/v2/everything?'
        f'q={requests.utils.quote(NEWS_QUERY)}&'
        f'from={seven_days}&language=en&sortBy=publishedAt&apiKey={NEWSAPI_KEY}'
    )
    resp = http_get(url)
    raw = resp.json().get('articles', [])

    filtered = [a for a in raw if any(dom in a.get('url','') for dom in WHITELIST_DOMAINS)]
    if not filtered:
        logging.info('No new whitelisted articles; exiting early')
        return []
    articles = filtered[:MAX_ARTICLES]
    logging.info(f'Fetched {len(articles)} whitelisted articles')
    return [{'title': a['title'], 'url': a['url'], 'source': a['source']['name']} for a in articles]
