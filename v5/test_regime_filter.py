import unittest
from unittest.mock import patch

import shadow
import strategy as st


def bars(first, last, n=100):
    out = [{'c': first, 'h': first, 'l': first, 't': str(i)} for i in range(n - 1)]
    out.append({'c': last, 'h': last, 'l': last, 't': 'x'})
    return out


class MarketIndex(unittest.TestCase):
    def test_equal_weight_mean_of_period_returns(self):
        data = {f'KRW-C{i}': bars(100, 110) for i in range(10)}
        data.update({f'KRW-D{i}': bars(100, 90) for i in range(10)})
        self.assertAlmostEqual(st.market_index(data, 96, min_markets=20), 0.0)
        data = {f'KRW-C{i}': bars(100, 110) for i in range(20)}
        self.assertAlmostEqual(st.market_index(data, 96, min_markets=20), 0.10)

    def test_too_few_markets_or_bars_is_unknown_not_zero(self):
        self.assertIsNone(st.market_index({f'KRW-C{i}': bars(100, 110) for i in range(19)}, 96, min_markets=20))
        self.assertIsNone(st.market_index({f'KRW-C{i}': bars(100, 110, n=50) for i in range(30)}, 96, min_markets=20))
        self.assertIsNone(st.market_index({}, 96))


class RegimeGate(unittest.TestCase):
    CAPS = {'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9, 'max_sell_krw_per_plan': 10 ** 9}

    def setUp(self):
        for method in ('accounts', 'tickers', 'orderbooks', 'market_buy', 'market_sell'):
            block = patch.object(shadow.upbit, method, side_effect=AssertionError('unexpected external call'))
            block.start()
            self.addCleanup(block.stop)
        self.ranked = [{'market': f'KRW-HOT{i}', 'rank': i, 'rs': .05} for i in range(1, 9)]
        self.eligible = {r['market'] for r in self.ranked}

    def plan(self, holdings=None, enabled=True, index=None):
        params = st.Params(M=5, entry_want=3, entry_scan=8, coin_cap=1., total_cap=1., regime_filter=enabled)
        return shadow.proposal(holdings or {}, self.ranked, {}, self.eligible, params, self.CAPS, 990_000, 0,
                               market_index=index)

    def test_rising_market_allows_normal_buys(self):
        text, reasons = self.plan(index=0.02)
        self.assertIn('매수 HOT1', text)
        self.assertTrue(any('장세 필터 통과' in r for r in reasons))

    def test_flat_or_falling_market_blocks_new_buys_only(self):
        for index in (0.0, -0.01):
            text, reasons = self.plan(index=index)
            self.assertIn('매수 없음', text)
            self.assertTrue(any('장세 필터' in r and '보류' in r for r in reasons))

    def test_unknown_index_fails_closed_for_entries(self):
        text, reasons = self.plan(index=None)
        self.assertIn('매수 없음', text)
        self.assertTrue(any('자료 부족' in r for r in reasons))

    def test_sells_and_holds_are_never_blocked_by_the_filter(self):
        held = {'KRW-OLD': 50_000, 'KRW-HOT1': 50_000}
        self.ranked.append({'market': 'KRW-OLD', 'rank': 9, 'rs': .01})
        self.eligible.add('KRW-OLD')
        text, reasons = self.plan(holdings=held, index=-0.05)
        self.assertIn('매도 OLD 100%', text)  # 순위 밖 순환 매도는 장세와 무관하게 유지
        self.assertTrue(any('HOT1 유지' in r for r in reasons))
        self.assertIn('매수 없음', text)

    def test_expiry_closes_new_buys_in_the_generator_too(self):
        import time
        params = st.Params(M=5, entry_want=3, entry_scan=8, coin_cap=1., total_cap=1., regime_filter=True,
                           regime_expires=time.time() - 1)
        text, reasons = shadow.proposal({}, self.ranked, {}, self.eligible, params, self.CAPS, 990_000, 0, market_index=0.05)
        self.assertIn('매수 없음', text)
        self.assertTrue(any('만료' in r for r in reasons))

    def test_halt_keeps_sells_and_holds_but_creates_no_buys(self):
        held = {'KRW-OLD': 50_000, 'KRW-HOT1': 50_000}
        self.ranked.append({'market': 'KRW-OLD', 'rank': 9, 'rs': .01})
        self.eligible.add('KRW-OLD')
        params = st.Params(M=5, entry_want=3, entry_scan=8, coin_cap=1., total_cap=1., halt_new_buys=True)
        text, reasons = shadow.proposal(held, self.ranked, {}, self.eligible, params, self.CAPS, 990_000, 0, market_index=0.05)
        self.assertIn('매도 OLD 100%', text)
        self.assertIn('매수 없음', text)
        self.assertTrue(any('신규 매수 임시 중단' in r for r in reasons))

    def test_disabled_filter_changes_nothing(self):
        for index in (None, -0.05, 0.02):
            text, reasons = self.plan(enabled=False, index=index)
            self.assertIn('매수 HOT1', text)
            self.assertFalse(any('장세 필터' in r for r in reasons))


class MarketIndexIntegrity(unittest.TestCase):
    def test_non_finite_and_non_positive_prices_are_excluded(self):
        data = {f'KRW-C{i}': bars(100, 110) for i in range(20)}
        data['KRW-NAN'] = bars(100, float('nan'))
        data['KRW-INF'] = bars(100, float('inf'))
        data['KRW-ZERO'] = bars(0, 110)
        data['KRW-TXT'] = bars(100, 'abc')
        self.assertAlmostEqual(st.market_index(data, 96, min_markets=20), 0.10)

    def test_markets_with_misaligned_bars_are_excluded_not_averaged_in(self):
        data = {f'KRW-C{i}': bars(100, 110) for i in range(20)}
        late = bars(100, 50)
        late[-1]['t'] = 'older'  # 거래 정지·결측으로 마지막 봉 시각이 다른 종목
        data['KRW-LATE'] = late
        self.assertAlmostEqual(st.market_index(data, 96, min_markets=20), 0.10)
        data = {f'KRW-C{i}': bars(100, 110) for i in range(19)}
        data['KRW-LATE'] = late  # 일치 종목이 20개 미만이면 지수를 만들지 않는다
        self.assertIsNone(st.market_index(data, 96, min_markets=20))

    def test_invalid_arguments_raise(self):
        for n, m in ((0, 20), (-1, 20), (96.0, 20), (96, 0), (96, 2.5)):
            with self.assertRaises(ValueError):
                st.market_index({}, n, m)

    def test_proposal_rejects_nan_index(self):
        gate = RegimeGate('test_unknown_index_fails_closed_for_entries')
        gate.setUp()
        try:
            text, reasons = gate.plan(index=float('nan'))
        finally:
            gate.doCleanups()
        self.assertIn('매수 없음', text)


class ReceiverEnforcement(unittest.TestCase):
    """생성기와 별개로 실행 쪽에서도 필터를 강제한다."""

    def setUp(self):
        import tempfile
        import live
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        params = st.Params(regime_filter=True)
        self.lv = live.Live(self.tmp.name, {'enabled': True, 'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9}, params)
        for method in ('accounts', 'tickers', 'orderbooks', 'market_buy', 'market_sell'):
            block = patch.object(live.upbit, method, side_effect=AssertionError('unexpected external call'))
            block.start()
            self.addCleanup(block.stop)

    def run_plan(self, **kw):
        return self.lv.execute(('봇 주문안 test', '매수 MPLX 94000원'), {'KRW-MPLX': {'warning': False}}, automatic=True, **kw)

    def test_buys_without_a_positive_index_are_dropped_before_any_account_or_order_call(self):
        for index in (None, 0.0, -0.01, float('nan'), float('inf') * 0, 'x'):
            self.assertIn('장세 필터', self.run_plan(market_index=index), index)

    def test_expired_temporary_filter_blocks_all_buys_even_with_a_rising_market(self):
        import time
        self.lv.p = st.Params(regime_filter=True, regime_expires=time.time() - 1)
        self.assertIn('장세 필터', self.run_plan(market_index=0.05))
        self.lv.p = st.Params(regime_filter=True, regime_expires=time.time() + 3600)
        with self.assertRaises(AssertionError):  # 기간 안이고 상승장이면 통과해 정상 경로(계좌 조회)로 간다
            self.run_plan(market_index=0.05)

    def test_halt_new_buys_blocks_all_automatic_buys_even_in_a_rising_market(self):
        self.lv.p = st.Params(regime_filter=True, halt_new_buys=True)
        self.assertIn('신규 매수 차단', self.run_plan(market_index=0.05))
        self.lv.p = st.Params(regime_filter=False, halt_new_buys=True)
        self.assertIn('신규 매수 차단', self.run_plan(market_index=0.05))

    def test_filter_off_does_not_block(self):
        self.lv.p = st.Params(regime_filter=False)
        with self.assertRaises(AssertionError):  # 필터가 꺼져 있으면 정상 경로로 계좌 조회까지 간다(시험에서는 차단됨)
            self.run_plan(market_index=None)



class NoDustOnFullSell(unittest.TestCase):
    """2026-11-06: 전량 매도가 이진 소수 오차로 1e-08개를 남겼다(CAP 950.45500505 → 950.45500504 매도)."""

    def test_vol8_never_rounds_up_or_loses_the_last_digit(self):
        import upbit
        self.assertEqual(upbit.vol8(950.45500505), '950.45500505')
        self.assertEqual(upbit.vol8(254.23728813), '254.23728813')
        self.assertEqual(upbit.vol8(1.234567899), '1.23456790')
        self.assertEqual(upbit.vol8(1e-08 + 37.43), '37.43000001')   # 자투리+매수분 합계가 float에서 살짝 작아져도 정확히

    def _sell(self, plan, balance, price):
        import tempfile, live
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        lv = live.Live(tmp.name, {'enabled': True, 'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9,
                                  'max_sell_krw_per_plan': 10 ** 9}, st.Params())
        sent = []
        with patch.object(live.upbit, 'accounts', return_value=[{'currency': 'CAP', 'balance': balance, 'locked': '0'}]),              patch.object(live.upbit, 'tickers', return_value={'KRW-CAP': {'trade_price': price}}),              patch.object(lv, '_trade', side_effect=lambda side, m, v, *a, **k: sent.append(v) or (_ for _ in ()).throw(RuntimeError('stop'))):
            try: lv.execute(('봇 주문안 t', plan), {'KRW-CAP': {'warning': False}}, automatic=True, market_index=0.01)
            except RuntimeError: pass
        return sent

    def test_full_sell_sends_the_exact_balance(self):
        self.assertEqual(self._sell('매도 CAP 100%; 매수 없음', '950.45500505', 147.0), [950.45500505])

    def test_partial_sell_leaving_less_than_min_order_sells_all(self):
        self.assertEqual(self._sell('매도 CAP 97%; 매수 없음', '950.45500505', 147.0), [950.45500505])   # 남는 3% ≈ 4,190원
        self.assertEqual(self._sell('매도 CAP 50%; 매수 없음', '950.45500505', 147.0), [475.22750252])

if __name__ == '__main__':
    unittest.main()
