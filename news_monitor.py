"""Public RSS headline monitor. No account data, article-body scraping, or trade signals."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import datetime as dt
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

INTERVAL = 600
RETENTION = 30 * 86400
MAX_ITEMS = 1000
ATOM = '{http://www.w3.org/2005/Atom}'
FEEDS = (
    {'id':'yna_economy','name':'연합뉴스','scope':'domestic','url':'https://www.yna.co.kr/rss/economy.xml','host':'yna.co.kr'},
    {'id':'yna_world','name':'연합뉴스','scope':'international','url':'https://www.yna.co.kr/rss/international.xml','host':'yna.co.kr'},
    {'id':'hk_economy','name':'한국경제','scope':'domestic','url':'https://www.hankyung.com/feed/economy','host':'hankyung.com'},
    {'id':'hk_finance','name':'한국경제','scope':'domestic','url':'https://www.hankyung.com/feed/finance','host':'hankyung.com'},
    {'id':'hk_world','name':'한국경제','scope':'international','url':'https://www.hankyung.com/feed/international','host':'hankyung.com'},
    {'id':'newsis_economy','name':'뉴시스','scope':'domestic','url':'https://www.newsis.com/RSS/economy.xml','host':'newsis.com'},
    {'id':'newsis_world','name':'뉴시스','scope':'international','url':'https://www.newsis.com/RSS/international.xml','host':'newsis.com'},
    {'id':'coindesk','name':'CoinDesk','scope':'international','url':'https://www.coindesk.com/arc/outboundfeeds/rss/','host':'coindesk.com'},
    {'id':'fed','name':'미 연준','scope':'international','url':'https://www.federalreserve.gov/feeds/press_monetary.xml','host':'federalreserve.gov'},
)

TOPICS = (
    ('가상자산·수급', 48, ('비트코인','이더리움','가상자산','암호화폐','스테이블코인','업비트','빗썸','코인원','디지털자산','bitcoin','ethereum','ether','crypto','cryptocurrency','cryptocurrencies','stablecoin','coinbase','binance')),
    ('금리·통화', 45, ('금리','통화정책','연준','연방준비','한국은행','한은','금통위','양적완화','양적긴축','fomc','fed','ecb','interest rate','federal funds','monetary policy','federal reserve')),
    ('물가·경기', 25, ('인플레이션','소비자물가','생산자물가','고용지표','경기침체','실업률','비농업','국내총생산','inflation','payroll','recession','unemployment',' cpi',' pce',' gdp')),
    ('환율·유동성', 22, ('환율','달러','원화','국채금리','유동성','외환시장','엔화','exchange rate','dollar','treasury yield','liquidity')),
    ('재정·정책 리스크', 25, ('정부 셧다운','부채한도','은행 파산','국가부도','계엄','탄핵','government shutdown','debt ceiling','bank failure','sovereign debt','capital controls')),
    ('규제·보안', 20, ('금융위원회','금융감독원','금융당국','가상자산법','증권거래위원회','해킹','거래지원 종료','상장폐지','거래중단','입출금 중단','cyberattack','hack','exploit','securities and exchange','sec approves','sec rejects')),
    ('지정학·에너지', 18, ('전쟁','공습','미사일','휴전','관세','경제제재','원유','유가','호르무즈','war ','airstrike','ceasefire','tariff','sanction','crude oil','oil price','hormuz')),
)
PROMOTION = ('[광고]','[인사]','[부고]','[포토]','[사진]','[게시판]','[동정]','감사장','업무협약','sponsored','press release provided','price prediction','presale','에어드롭 이벤트')
INTERNATIONAL = ('미국','중국','유럽','일본','연준','연방준비','트럼프','백악관','중동','이란','러시아','우크라이나','federal reserve','fomc','fed','ecb','boj','trump','china','europe','iran')
DOMESTIC = ('한국은행','한은','금융위원회','금융감독원','금통위','업비트','빗썸','코인원','국내 가상자산','한국 가상자산')


def clean(value):
    text = re.sub(r'<[^>]*>', ' ', str(value or ''))
    return re.sub(r'\s+', ' ', html.unescape(text)).strip()[:300]


def safe_link(value, host):
    try:
        p = urlsplit(str(value))
        name = (p.hostname or '').lower()
        if p.scheme not in ('https','http') or p.username or p.password or p.port not in (None,80,443):
            return None
        if name != host and not name.endswith('.'+host):
            return None
        query = [(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
        return urlunsplit(('https',name,p.path,urlencode(query),''))
    except (ValueError,TypeError):
        return None


def timestamp(value):
    try:
        date = parsedate_to_datetime(value)
    except (ValueError,TypeError,IndexError):
        try:
            date = dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        except (ValueError,TypeError,AttributeError):
            return None
    if date.tzinfo is None:
        return None  # never invent the publisher's timezone
    result = date.timestamp()
    return result if math.isfinite(result) else None


def annotate(title, scope):
    text = (' '+title+' ').casefold()
    if any(x.casefold() in text for x in PROMOTION):
        return None
    def contains(word):
        word=word.casefold().strip()
        if re.search('[a-z]',word):
            return re.search(r'(?<![a-z0-9])'+re.escape(word)+r'(?![a-z0-9])',text) is not None
        return word in text
    matched = [(name,weight) for name,weight,words in TOPICS if any(contains(w) for w in words)]
    if not matched:
        return None
    if (any(w in text for w in ('돌려줘','캠페인','기부','후원','특판','우대금리','금리 우대','이벤트','채용'))
            and not any(w in text for w in ('해킹','유출','파산','금융당국','상장폐지','거래중단'))):
        return None
    domestic = any(contains(w) for w in DOMESTIC)
    international = any(contains(w) for w in INTERNATIONAL)
    # Newspaper location/language is not itself an impact classification.
    if domestic and international:
        impact = 'both'
    elif international:
        impact = 'international'
    elif domestic:
        impact = 'domestic'
    else:
        impact = scope
    score = max(w for _,w in matched) + min(18, (len(matched)-1)*6)
    return {'impact':impact,'topics':[name for name,_ in matched][:3],'relevance':score}


def parse_feed(blob, feed, now=None):
    now = time.time() if now is None else now
    if len(blob)>2_000_000 or b'<!DOCTYPE' in blob.upper() or b'<!ENTITY' in blob.upper():
        raise ValueError('unsupported RSS payload')
    root = ET.fromstring(blob)
    nodes = root.findall('.//item') or root.findall('.//'+ATOM+'entry')
    if root.tag not in ('rss',ATOM+'feed','{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF'):
        raise ValueError('not an RSS/Atom feed')
    articles = []
    for node in nodes[:200]:
        title = clean(node.findtext('title') or node.findtext(ATOM+'title'))
        link = node.findtext('link')
        if not link:
            link = next((x.get('href') for x in node.findall(ATOM+'link') if x.get('rel','alternate')=='alternate'),None)
        link = safe_link(link,feed['host'])
        pub = timestamp(node.findtext('pubDate') or node.findtext(ATOM+'published') or node.findtext(ATOM+'updated'))
        if not title or not link or pub is None or not -300 <= now-pub <= RETENTION:
            continue
        info = annotate(title,feed['scope'])
        if info is None:
            continue
        articles.append({'id':hashlib.sha256(link.encode()).hexdigest()[:24],'title':title,'url':link,
                         'source':feed['name'],'feed_id':feed['id'],'published':pub,**info})
    return articles


def fetch_feed(feed, now=None):
    request = Request(feed['url'],headers={'User-Agent':'local-market-news-monitor/1.0',
                      'Accept':'application/rss+xml,application/atom+xml,application/xml,text/xml'})
    with urlopen(request,timeout=6) as response:
        blob = response.read(2_000_001)
    return parse_feed(blob,feed,now)


def normalized_title(title):
    return re.sub(r'[^a-z0-9가-힣]', '', re.sub(r'\[(속보|종합\d*|\d+보)\]', '',title.casefold()))


def deduplicate(articles):
    selected=[]
    for a in sorted(articles,key=lambda a:(a['published'],a['relevance']),reverse=True):
        title=normalized_title(a['title'])
        if any(a['id']==b['id'] or (abs(a['published']-b['published'])<=86400
               and len(title)>=15 and SequenceMatcher(None,title,normalized_title(b['title'])).ratio()>=.87)
               for b in selected):
            continue
        selected.append(a)
    return selected


def top_three(articles, now=None):
    now=time.time() if now is None else now
    recent=[a for a in articles if 0<=now-a['published']<=86400]
    def score(a):
        return a['relevance'] + 20*math.exp(-(now-a['published'])/21600)
    ranked=sorted(recent,key=lambda a:(score(a),a['published']),reverse=True)
    chosen=[]
    # Reserve room for domestic and international when both categories are present.
    for scope in ('domestic','international'):
        best=next((a for a in ranked if a['impact'] in (scope,'both') and a not in chosen),None)
        if best:
            chosen.append(best)
    for different_source in (True,False):
        for a in ranked:
            if len(chosen)>=3:
                break
            if a not in chosen and (not different_source or a['source'] not in {x['source'] for x in chosen}):
                chosen.append(a)
    return sorted(chosen,key=score,reverse=True)[:3]


class NewsMonitor:
    def __init__(self, root, feeds=FEEDS, fetcher=fetch_feed, clock=time.time):
        self.root=Path(root)
        self.path=self.root/'headlines.json'
        self.feeds=feeds
        self.fetcher=fetcher
        self.clock=clock
        self.lock=threading.RLock()
        self.refresh_lock=threading.Lock()
        self.started=False
        self.articles=[]
        self.last_attempt=0
        self.last_success=0
        self.next_check=0
        self.feed_status=[]
        self.error=''
        if self.path.exists():
            try:
                state=json.loads(self.path.read_text(encoding='utf-8'))
                if state.get('version')!=1 or not isinstance(state.get('articles'),list):
                    raise ValueError('bad news cache')
                allowed={f['id']:f for f in self.feeds}
                for item in state['articles'][:MAX_ITEMS]:
                    feed=allowed.get(item.get('feed_id'))
                    link=safe_link(item.get('url'),feed['host']) if feed else None
                    if not feed or not link:
                        continue
                    pub=float(item['published'])
                    info=annotate(clean(item['title']),feed['scope'])
                    if info and math.isfinite(pub) and -300<=self.clock()-pub<=RETENTION:
                        self.articles.append({'id':hashlib.sha256(link.encode()).hexdigest()[:24],
                            'url':link,'title':clean(item['title']),'published':pub,'feed_id':feed['id'],
                            'source':feed['name'],'first_seen':pub,'last_seen':pub,**info})
                self.last_success=float(state.get('last_success',0))
                if not math.isfinite(self.last_success):
                    self.last_success=0
            except (OSError,ValueError,KeyError,TypeError,AttributeError):
                self.articles=[]
                self.error='이전 뉴스 기록을 읽지 못했습니다. 새 수집 후 복구합니다.'
                try:
                    self.path.replace(self.path.with_name('headlines.bad-'+str(time.time_ns())+'.json'))
                except OSError:
                    pass

    def save(self):
        self.root.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'version':1,'articles':self.articles,'last_success':self.last_success},
                                  ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(self.path)

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            now=self.clock()
            with self.lock:
                self.last_attempt=now
            collected=[]
            statuses=[]
            with ThreadPoolExecutor(max_workers=4) as pool:
                pending={pool.submit(self.fetcher,feed,now):feed for feed in self.feeds}
                for future in as_completed(pending):
                    feed=pending[future]
                    try:
                        articles=future.result()
                        collected.extend(articles)
                        statuses.append({'id':feed['id'],'source':feed['name'],'ok':True,'relevant':len(articles)})
                    except Exception:
                        statuses.append({'id':feed['id'],'source':feed['name'],'ok':False,'relevant':0})
            with self.lock:
                old={a['id']:a for a in self.articles if -300<=now-a['published']<=RETENTION}
                for article in collected:
                    first=old.get(article['id'],{}).get('first_seen',now)
                    old[article['id']]={**article,'first_seen':first,'last_seen':now}
                self.articles=deduplicate(list(old.values()))[:MAX_ITEMS]
                self.feed_status=sorted(statuses,key=lambda x:x['id'])
                if any(x['ok'] for x in statuses):
                    self.last_success=now
                    self.error=''
                else:
                    self.error='뉴스 출처에 연결할 수 없습니다. 이전 기록을 표시합니다.'
                self.next_check=now+INTERVAL
                try:
                    self.save()
                except OSError:
                    self.error='뉴스는 갱신했지만 기록을 저장하지 못했습니다.'
        finally:
            self.refresh_lock.release()

    def snapshot(self):
        now=self.clock()
        with self.lock:
            articles=[a for a in self.articles if 0<=now-a['published']<=RETENTION]
            ok=sum(x['ok'] for x in self.feed_status)
            return copy.deepcopy({'top':top_three(articles,now),'history':sorted(articles,key=lambda a:a['published'],reverse=True),
                     'last_attempt':self.last_attempt,'last_success':self.last_success,'next_check':self.next_check,
                     'interval_seconds':INTERVAL,'stale':not self.last_success or now-self.last_success>900,
                     'partial':bool(self.feed_status) and ok<len(self.feed_status),
                     'sources_ok':ok,'sources_total':len(self.feeds),'error':self.error,
                     'refreshing':self.refresh_lock.locked(),'started':self.started})

    def start(self, stop_event):
        with self.lock:
            if self.started:
                return
            self.started=True
        def loop():
            while not stop_event.is_set():
                started=time.monotonic()
                self.refresh()
                stop_event.wait(max(1,INTERVAL-(time.monotonic()-started)))
        threading.Thread(target=loop,daemon=True,name='market-news').start()
