import datetime as dt
from email.utils import formatdate
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from news_monitor import NewsMonitor, parse_feed, top_three, annotate, deduplicate, safe_link, INTERVAL

NOW=1793066400.0
FEED={'id':'test','name':'테스트 언론','scope':'domestic','url':'https://example.org/rss','host':'example.org'}


def rss(title='한국은행 기준금리 동결',published=None,link='https://example.org/news/1',extra=''):
    pub=formatdate(NOW-60,usegmt=True) if published is None else published
    return ('<rss version="2.0"><channel><item><title>'+escape(title)+'</title><link>'+escape(link)+
            '</link><pubDate>'+escape(pub)+'</pubDate>'+extra+'</item></channel></rss>').encode()


def article(name='한국은행 기준금리 동결',scope='domestic',age=60,url='https://example.org/news/1'):
    return parse_feed(rss(name,formatdate(NOW-age,usegmt=True),url),{**FEED,'scope':scope},NOW)[0]


class FeedTests(unittest.TestCase):
    def test_domestic_rss_has_source_link_and_timestamp(self):
        item=parse_feed(rss(),FEED,NOW)[0]
        self.assertEqual(item['impact'],'domestic')
        self.assertEqual(item['source'],FEED['name'])
        self.assertEqual(item['published'],NOW-60)
        self.assertEqual(item['url'],'https://example.org/news/1')

    def test_foreign_event_in_korean_domestic_feed_is_international(self):
        item=parse_feed(rss('미국 연준 기준금리 결정'),FEED,NOW)[0]
        self.assertEqual(item['impact'],'international')

    def test_joint_effect_can_be_both(self):
        self.assertEqual(annotate('미국 연준 금리 인하와 한국은행 대응','domestic')['impact'],'both')

    def test_fed_in_korean_headline_is_international(self):
        self.assertEqual(annotate('유가 하락과 Fed 금리 결정 주목','domestic')['impact'],'international')

    def test_exchange_publicity_is_not_major_market_news(self):
        self.assertIsNone(annotate('코인원 보이스피싱 피해금 돌려줘','domestic'))
        self.assertIsNone(annotate('은행 우대금리 특판 이벤트','domestic'))

    def test_corporate_notice_not_ranked_as_market_news(self):
        self.assertIsNone(annotate('[게시판] 빗썸, 금융사기 수사 협조 감사장','domestic'))

    def test_irrelevant_or_promotional_content_ignored(self):
        self.assertEqual(parse_feed(rss('지역 전통 음식 축제 개최'),FEED,NOW),[])
        self.assertEqual(parse_feed(rss('[광고] 비트코인 투자 이벤트'),FEED,NOW),[])

    def test_ether_substring_in_whether_is_not_crypto(self):
        self.assertIsNone(annotate('Whether people enjoy working together','international'))

    def test_missing_or_naive_dates_not_invented(self):
        self.assertEqual(parse_feed(rss(published=''),FEED,NOW),[])
        self.assertEqual(parse_feed(rss(published='2026-10-27T10:00:00'),FEED,NOW),[])

    def test_future_and_expired_articles_rejected(self):
        self.assertEqual(parse_feed(rss(published=formatdate(NOW+600,usegmt=True)),FEED,NOW),[])
        self.assertEqual(parse_feed(rss(published=formatdate(NOW-31*86400,usegmt=True)),FEED,NOW),[])

    def test_atom_feed_supported(self):
        xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Federal Reserve interest rate decision</title><link href="https://example.org/fed"/><published>2026-10-27T01:00:00Z</published></entry></feed>'
        result=parse_feed(xml,{**FEED,'scope':'international'},NOW)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['impact'],'international')

    def test_entities_unsafe_urls_and_nonfeeds_rejected(self):
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE rss [<!ENTITY evil SYSTEM "file:///secret">]><rss/>',FEED,NOW)
        with self.assertRaises(ValueError):
            parse_feed(b'<html><title>not RSS</title></html>',FEED,NOW)
        for url in ('javascript:alert(1)','https://evil.example/a','https://example.org.evil/a','https://name:pass@example.org/a'):
            self.assertIsNone(safe_link(url,'example.org'))

    def test_tracking_query_removed(self):
        self.assertEqual(safe_link('http://example.org/a?utm_source=rss&id=1#fragment','example.org'),'https://example.org/a?id=1')

    def test_headline_html_is_plain_text(self):
        item=parse_feed(rss('<b>한국은행</b> 기준금리 동결'),FEED,NOW)[0]
        self.assertNotIn('<b>',item['title'])


class SelectionTests(unittest.TestCase):
    def test_top_three_include_both_scopes(self):
        items=[article('한국은행 금리 결정',url='https://example.org/a'),
               article('미국 연준 금리 결정',url='https://example.org/b'),
               article('비트코인 ETF 수급 변화','international',url='https://example.org/c'),
               article('업비트 가상자산 거래 안내',url='https://example.org/d')]
        top=top_three(items,NOW)
        self.assertEqual(len(top),3)
        self.assertTrue(any(a['impact']=='domestic' for a in top))
        self.assertTrue(any(a['impact']=='international' for a in top))

    def test_never_fabricates_or_fills_with_old_articles(self):
        self.assertEqual(top_three([article(age=90000)],NOW),[])
        self.assertEqual(len(top_three([article()],NOW)),1)

    def test_same_url_and_near_identical_titles_deduplicated(self):
        a=article('한국은행 기준금리 동결하고 향후 통화정책 검토',url='https://example.org/a')
        b=article('[속보] 한국은행 기준금리 동결하고 향후 통화정책 검토',url='https://example.org/b')
        self.assertEqual(len(deduplicate([a,b,dict(a)])),1)

    def test_source_diversity_for_third_slot(self):
        a=article('한국은행 기준금리 동결',url='https://example.org/a')
        b=article('미국 연준 금리 동결',url='https://example.org/b')
        c=article('미국 달러 환율 변화',url='https://example.org/c');c['source']='다른 언론'
        d=article('비트코인 가상자산 규제 변화',url='https://example.org/d')
        self.assertIn('다른 언론',{x['source'] for x in top_three([a,b,c,d],NOW)})


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def monitor(self,fetcher,feeds=(FEED,),clock=lambda:NOW):
        return NewsMonitor(self.tmp.name,feeds=feeds,fetcher=fetcher,clock=clock)

    def test_refresh_persists_and_restart_keeps_history(self):
        m=self.monitor(lambda f,n:[article()]);m.refresh()
        restored=self.monitor(lambda f,n:[])
        self.assertEqual(len(restored.snapshot()['history']),1)
        self.assertEqual(restored.snapshot()['top'][0]['title'],article()['title'])

    def test_all_sources_fail_keep_cache_but_mark_error(self):
        m=self.monitor(lambda f,n:[article()]);m.refresh()
        m.fetcher=Mock(side_effect=TimeoutError())
        m.refresh()
        result=m.snapshot()
        self.assertEqual(len(result['top']),1)
        self.assertTrue(result['error'])
        self.assertEqual(result['sources_ok'],0)

    def test_partial_failure_is_visible(self):
        second={**FEED,'id':'second'}
        def fetch(feed,now):
            if feed['id']=='second': raise TimeoutError()
            return [article()]
        m=self.monitor(fetch,(FEED,second));m.refresh()
        self.assertTrue(m.snapshot()['partial'])
        self.assertEqual(m.snapshot()['sources_ok'],1)

    def test_refresh_concurrency_does_not_duplicate_network_jobs(self):
        fetch=Mock(return_value=[])
        m=self.monitor(fetch)
        m.refresh_lock.acquire()
        try: m.refresh()
        finally: m.refresh_lock.release()
        fetch.assert_not_called()

    def test_success_with_no_relevant_articles_not_failure(self):
        m=self.monitor(lambda f,n:[]);m.refresh()
        self.assertFalse(m.snapshot()['error'])
        self.assertEqual(m.snapshot()['top'],[])
        self.assertEqual(m.snapshot()['next_check'],NOW+600)

    def test_expired_cache_and_malicious_cached_url_excluded(self):
        a=article()
        state={'version':1,'articles':[dict(a,url='javascript:alert(1)'),dict(a,published=NOW-31*86400)],'last_success':NOW}
        Path(self.tmp.name,'headlines.json').write_text(json.dumps(state),encoding='utf-8')
        self.assertFalse(self.monitor(lambda f,n:[]).snapshot()['history'])

    def test_corrupt_cache_preserved_and_new_collection_recovers(self):
        Path(self.tmp.name,'headlines.json').write_text('broken')
        m=self.monitor(lambda f,n:[article()]);m.refresh()
        self.assertTrue(list(Path(self.tmp.name).glob('headlines.bad-*.json')))
        self.assertEqual(len(m.snapshot()['top']),1)

    def test_single_background_worker_uses_ten_minute_interval(self):
        finished=threading.Event()
        class Stop:
            done=False
            seconds=None
            def is_set(self): return self.done
            def wait(self,seconds):
                self.seconds=seconds;self.done=True;finished.set();return True
        stop=Stop();fetch=Mock(return_value=[])
        m=self.monitor(fetch);m.start(stop);m.start(stop)
        self.assertTrue(finished.wait(3))
        self.assertTrue(590<=stop.seconds<=600)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(INTERVAL,600)


if __name__=='__main__':
    unittest.main()
