from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent

def edit(name, old, new):
    path = ROOT / name
    text = path.read_text(encoding='utf-8')
    assert old in text, (name, old)
    path.write_text(text.replace(old, new), encoding='utf-8')

for name in ('trend_web.py', 'launch_trend_web.py'):
    edit(name, '8767', '8768')
    edit(name, 'upbit-trend-web-v2', 'auto-trading-v4')
edit('scalping_strategy.py', "VERSION = 'scalp-v3.2'", "VERSION = 'auto-v4.0'")
edit('auto_trader.py', "root/'auto_v3.json'", "root/'auto_v4.json'")
edit('auto_trader.py', 'from investment_profiles import PROFILES', 'from investment_profiles import PROFILES\nfrom research_v4 import adjust_plan')
edit('auto_trader.py', "                        amount=plan.pop('amount');plan.pop('stop')", "                        plan=adjust_plan(plan, price)\n                        amount=plan.pop('amount');plan.pop('stop')")
edit('portfolio.py', 'def profile_signal(rows, profile, now):', 'def profile_signal(rows, profile, now):\n    from research_v4 import trend_assessment')
edit('portfolio.py', "    return {'atr': atr, 'entry': ready, 'id': str(rows[-1]['t']), 'close': closes[-1]}", "    research = trend_assessment(rows, profile['unit'], now)\n    return {'atr': atr, 'entry': ready and research['trend_ok'], 'id': str(rows[-1]['t']), 'close': closes[-1], 'research_v4': research}")
path = ROOT/'trend_dashboard.html'
text = path.read_text(encoding='utf-8')
text = re.sub(r'>([^<>]+)<', lambda m: '>'+m[1].replace('업비트 거래 봇 V3', '자동거래 v4').replace('자동 거래 V3.1 · 원화마켓 전체 단타 감시', '자동거래 v4 · 비용·변동성 필터').replace('코인봇 V3', '자동거래 v4').replace('V3', 'v4')+'<', text)
text = text.replace('<section class="news-panel" id="auto-v3-panel"', '<section class="news-panel"><h2>전략 연구 반영 · v4</h2><p>단타: 예상 왕복 비용 대비 계획 보상 3배 이상 · ATR 비율 0.5% 초과 시 투자금 축소. 스윙·장기: 기존 돌파 조건에 양의 20봉 모멘텀·EMA200 필터 추가. 초단타·캐리·차익거래는 미구현 연구 항목입니다. 수익성은 아직 검증되지 않았습니다.</p></section>\n<section class="news-panel" id="auto-v3-panel"')
path.write_text(text, encoding='utf-8')
edit('install_trend_shortcut.py', '코인봇 V3 실행', '자동거래 v4 실행')
edit('install_trend_shortcut.py', '코인봇 V3.2 · 눌림 / 초기 돌파 모의거래 · 투자 포트폴리오', '자동거래 v4 · 비용·변동성 필터 · 모의거래')
