"""Native desktop app. Opening icon never starts real trading."""
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from trend_engine import Engine, ROOT, MARKETS, KST
from market_regime import ZONES

BG = '#0c1323'
PANEL = '#152036'
TEXT = '#eef3ff'
MUTED = '#9aabc9'
GREEN = '#38dbb0'
ORANGE = '#ffb266'


class App:
    def __init__(self, window):
        self.w = window
        self.engine = Engine()
        self.jobs = queue.Queue()
        self.busy = False
        window.title('업비트 트렌드 봇 2 · 시뮬레이션 / 실거래')
        window.geometry('1220x930')
        window.minsize(1050, 720)
        window.configure(bg=BG)
        icon = ROOT / 'trend_bot.ico'
        if icon.exists():
            window.iconbitmap(str(icon))
        scroll = tk.Canvas(window, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(window, orient='vertical', command=scroll.yview)
        scrollbar.pack(side='right', fill='y')
        scroll.pack(side='left', fill='both', expand=True)
        scroll.configure(yscrollcommand=scrollbar.set)
        self.body = tk.Frame(scroll, bg=BG)
        content_id = scroll.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda e: scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>', lambda e: scroll.itemconfigure(content_id, width=e.width))
        def wheel(event):
            if scroll.bbox('all') and scroll.bbox('all')[3] > scroll.winfo_height():
                scroll.yview_scroll(-int(event.delta / 120), 'units')
        window.bind('<MouseWheel>', wheel)
        window = self.body
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview', background=PANEL, foreground=TEXT, fieldbackground=PANEL,
                        rowheight=29, font=('맑은 고딕', 10), borderwidth=0)
        style.configure('Treeview.Heading', background='#233350', foreground=TEXT, font=('맑은 고딕', 10, 'bold'))
        style.map('Treeview', background=[('selected', '#29496b')])
        top = tk.Frame(window, bg=BG)
        top.pack(fill='x', padx=26, pady=(20, 10))
        tk.Label(top, text='UPBIT  /  TREND BOT 02', fg=TEXT, bg=BG, font=('맑은 고딕', 21, 'bold')).pack(side='left')
        self.badge = tk.Label(top, text='시뮬레이션', bg='#183e3a', fg=GREEN, padx=18, pady=8, font=('맑은 고딕', 12, 'bold'))
        self.badge.pack(side='right')
        tk.Label(window, text='100만 원 한도  ·  현물 BTC / ETH  ·  마감 4시간 추세 + 1시간 돌파  ·  수익성 검증 전',
                 bg=BG, fg=MUTED, font=('맑은 고딕', 10), anchor='w').pack(fill='x', padx=28)
        market = tk.Frame(window, bg=PANEL, padx=14, pady=8)
        market.pack(fill='x', padx=26, pady=(10, 0))
        heading = tk.Frame(market, bg=PANEL)
        heading.pack(fill='x')
        tk.Label(heading, text='거래 국면 · BTC 추세 / 급락 방어', bg=PANEL, fg=TEXT,
                 font=('맑은 고딕', 11, 'bold')).pack(side='left')
        self.market_label = tk.Label(heading, text='확인 불가 · 수집 중', bg=PANEL, fg=MUTED,
                                     font=('맑은 고딕', 12, 'bold'))
        self.market_label.pack(side='right')
        self.market_bar = tk.Canvas(market, height=51, bg=PANEL, highlightthickness=0)
        self.market_bar.pack(fill='x', pady=(1, 0))
        self.market_details = tk.Label(market, bg=PANEL, fg=MUTED, anchor='w', font=('맑은 고딕', 9))
        self.market_details.pack(fill='x')
        self.market_view = {'available': False, 'reason': '시황 데이터 수집 중'}
        self.market_bar.bind('<Configure>', lambda e: self.draw_market())
        controls = tk.Frame(window, bg=PANEL, padx=14, pady=12)
        controls.pack(fill='x', padx=26, pady=12)
        self.button(controls, '시뮬레이션', lambda: self.switch('paper'), '#29496b').pack(side='left', padx=4)
        self.button(controls, '실거래로 전환', lambda: self.switch('live'), '#80502b').pack(side='left', padx=4)
        self.button(controls, '▶ 자동 매수 시작', self.start, '#16785f').pack(side='left', padx=(16, 4))
        self.button(controls, 'Ⅱ 신규 매수 중지', lambda: self.run(self.engine.pause), '#33435e').pack(side='left', padx=4)
        self.button(controls, '보유분 전량 청산', self.flatten, '#914853').pack(side='right', padx=4)
        self.status = tk.Label(window, bg=BG, fg=GREEN, anchor='w', font=('맑은 고딕', 11), wraplength=1150)
        self.status.pack(fill='x', padx=28, pady=(0, 8))
        cards = tk.Frame(window, bg=BG)
        cards.pack(fill='x', padx=26)
        self.cards = {}
        for title in ('봇 자산', '누적 손익', '주문 가능 가상 원화', '최고점 대비 하락'):
            frame = tk.Frame(cards, bg=PANEL, padx=18, pady=12)
            frame.pack(side='left', fill='both', expand=True, padx=3)
            tk.Label(frame, text=title, bg=PANEL, fg=MUTED, font=('맑은 고딕', 10)).pack(anchor='w')
            label = tk.Label(frame, bg=PANEL, fg=TEXT, font=('맑은 고딕', 18, 'bold'))
            label.pack(anchor='w', pady=(5, 0))
            self.cards[title] = label
        self.notice = tk.Label(window, text='', bg=BG, fg=ORANGE, anchor='w', wraplength=1150, font=('맑은 고딕', 10))
        self.notice.pack(fill='x', padx=28, pady=8)
        self.signal_table = self.table('진입 조건 · 조건 미충족 시 대기', ('종목', '매수 조건', '거래량 배수', '호가 차이', '현재 매도 / 매수호가'), (100, 430, 110, 100, 250), 2)
        self.pos_table = self.table('보유 포지션 · 기존 계좌 코인은 별도', ('종목', '수량', '진입가', '현재 평가', '손절선', '평가손익'), (100, 160, 160, 160, 160, 160), 2)
        self.trade_table = self.table('최근 완료 거래 · 매수/매도 수수료 포함', ('시각', '종목', '순손익', '청산 사유'), (250, 130, 140, 470), 3)
        foot = tk.Frame(window, bg=BG)
        foot.pack(fill='x', padx=26, pady=(9, 5))
        for name, action in [('API 조회 연결 확인', self.connect), ('미접수 주문 확인/복구', self.recover),
                             ('모의 자산 초기화', self.reset), ('실패 분석·사용법', self.docs),
                             ('기록 폴더', lambda: os.startfile(str(self.engine.root)))]:
            self.button(foot, name, action, '#263650', small=True).pack(side='left', padx=3)
        self.event = tk.Label(window, text='', bg=BG, fg=MUTED, anchor='w', justify='left', font=('맑은 고딕', 9))
        self.event.pack(fill='x', padx=28, pady=(2, 7))
        tk.Label(window, text='실거래는 전환 후 시작해야 활성화됩니다. 창/PC가 꺼지면 자동 손절도 멈춥니다. 기존 코인·다른 봇과 동시 거래하지 마세요.',
                 bg=BG, fg=ORANGE, font=('맑은 고딕', 9), wraplength=1150).pack(fill='x', padx=26, pady=(0, 12))
        self.w.protocol('WM_DELETE_WINDOW', self.close)
        self.tick()

    def button(self, parent, text, command, color, small=False):
        return tk.Button(parent, text=text, command=command, bg=color, fg=TEXT, activebackground='#486080', activeforeground=TEXT,
                         relief='flat', bd=0, padx=10, pady=7 if small else 9,
                         cursor='hand2', font=('맑은 고딕', 9 if small else 10, 'bold'))

    def table(self, title, cols, widths, height):
        tk.Label(self.body, text=title, bg=BG, fg=MUTED, anchor='w', font=('맑은 고딕', 10, 'bold')).pack(fill='x', padx=29, pady=(8, 4))
        t = ttk.Treeview(self.body, columns=cols, show='headings', height=height)
        for name, width in zip(cols, widths):
            t.heading(name, text=name)
            t.column(name, width=width, anchor='w', minwidth=65)
        t.pack(fill='x', padx=29)
        return t

    def run(self, function):
        if self.busy:
            return
        self.busy = True
        def work():
            try:
                result = function()
                self.jobs.put(('ok', result))
            except Exception as e:
                self.jobs.put(('error', str(e)))
        threading.Thread(target=work, daemon=True).start()

    def switch(self, mode):
        if mode == 'live' and self.engine.mode != 'live':
            if not messagebox.askyesno('실거래 모드 전환', '100만 원 한도로 실제 주문이 가능한 모드입니다.\n전환만으로 주문하지 않으며, 별도로 자동 매수 시작이 필요합니다.\n\n기존 보유 코인은 제외합니다. 다른 봇/수동 거래는 중지하세요.\n실거래 모드로 전환할까요?', parent=self.w):
                return
        self.run(lambda: self.engine.switch(mode))

    def start(self):
        if self.engine.mode == 'live':
            if not messagebox.askyesno('실제 자동 매수 시작', '이제 실제 원화로 매수·매도합니다.\n거래당 계획 손실 0.5%, 종목당 최대 40%, 총 최대 80%입니다.\n손절 가격과 손실 한도는 체결을 보장하지 않습니다.\n\n실제 자동 매수를 시작할까요?', parent=self.w):
                return
        self.run(self.engine.start)

    def flatten(self):
        mode = '실제 계좌의 봇 보유분' if self.engine.mode == 'live' else '모의 보유분'
        if messagebox.askyesno('전량 청산', mode + '을 전량 매도하고 운용을 잠급니다.\n미체결 주문은 확인 후 후속 청산합니다. 진행할까요?', parent=self.w):
            self.run(self.engine.flatten)

    def connect(self):
        self.run(self.engine.connect)

    def recover(self):
        self.run(self.engine.clear_unsubmitted)

    def reset(self):
        if messagebox.askyesno('모의 자산 초기화', '모의 기록을 보관하고 100만 원으로 초기화할까요?', parent=self.w):
            self.run(self.engine.reset_paper)

    def docs(self):
        os.startfile(str(ROOT / 'TREND_BOT_사용법.md'))

    def rows(self, table, rows):
        table.delete(*table.get_children())
        for row in rows:
            table.insert('', 'end', values=row)

    def draw_market(self):
        c = self.market_bar
        c.delete('all')
        width = max(c.winfo_width(), 100)
        left, right = 8, width - 8
        step = (right-left)/5
        summary = self.market_view
        active = summary.get('zone') if summary.get('available') else None
        for i, (name, color) in enumerate(ZONES):
            x1, x2 = left + i*step, left + (i+1)*step-3
            c.create_rectangle(x1, 17, x2, 45, fill=color, outline=TEXT if i==active else color, width=2)
            c.create_text((x1+x2)/2, 31, text=name, fill=TEXT, font=('맑은 고딕', 10, 'bold'))
        if active is not None:
            x = left + max(0, min(1, (summary['score']+100)/200))*(right-left-3)
            c.create_polygon(x-6, 2, x+6, 2, x, 13, fill=TEXT, outline=TEXT)
            c.create_line(x, 14, x, 46, fill=TEXT, width=2)

    def tick(self):
        try:
            while True:
                kind, value = self.jobs.get_nowait()
                self.busy = False
                if kind == 'error':
                    messagebox.showerror('작업 확인', value, parent=self.w)
                elif value:
                    messagebox.showinfo('확인', str(value), parent=self.w)
        except queue.Empty:
            pass
        # Avoid blocking the UI while worker performs account/order requests under lock.
        if self.engine.lock.acquire(blocking=False):
            try:
                s = self.engine.snapshot()
            finally:
                self.engine.lock.release()
            mode = s['mode']
            state = s['states'][mode]
            eq = s['equity'][mode]
            live = mode == 'live'
            self.badge.config(text=('실거래' if live else '시뮬레이션') + (' · 실행 중' if s['running'] else ' · 신규 매수 정지'),
                              fg=ORANGE if live else GREEN, bg='#4c3026' if live else '#183e3a')
            self.status.config(text=('처리 중…  ' if self.busy else '') + s['status'], fg=ORANGE if live else GREEN)
            values = [f'{eq:,.0f} 원', f'{eq - 1000000:+,.0f} 원', f'{state["cash"]:,.0f} 원', f'{(1-eq/state["peak"])*100:.2f}%']
            for key, value in zip(self.cards, values):
                self.cards[key].config(text=value)
            day_loss = eq <= state['day_start'] * .97
            notice = state['halt'] or ('일일 -3%: 오늘 신규 매수 중지' if day_loss else '거래당 위험 0.5% · 호가 차이 0.1% 이하 · 신호가 대비 +0.3% 초과 추격 금지 · 재진입 4시간 제한')
            if state['pending']:
                notice = '실제 주문 확인 중 · 중복 전송 및 모드 전환 잠금'
            self.notice.config(text=notice)
            signals, positions = [], []
            for m in MARKETS:
                signal, q = s['signals'].get(m, {}), s['quotes'].get(m, {})
                signals.append((m, signal.get('reason', '봉 데이터 수집 중'), f'{signal.get("volume_ratio",0):.2f}x',
                                f'{q.get("spread",0)*100:.3f}%' if q else '—',
                                f'{q.get("bid",0):,.0f} / {q.get("ask",0):,.0f}' if q else '—'))
                p = state['positions'].get(m)
                if p:
                    value = p['volume'] * q.get('bid', p['entry'])
                    positions.append((m, f'{p["volume"]:.8f}', f'{p["entry"]:,.0f}', f'{value:,.0f}', f'{p["stop"]:,.0f}', f'{value-p["cost"]:+,.0f}'))
            self.rows(self.signal_table, signals)
            self.rows(self.pos_table, positions)
            self.rows(self.trade_table, [(t['time'][5:19].replace('T',' '), t['market'], f'{t["net"]:+,.0f} 원', t['reason']) for t in reversed(state['trades'][-3:])])
            self.event.config(text='\n'.join(s['events'][-2:]) or '실제 호가 기반 모의 체결 · 편도 수수료 0.05% + 호가 차이 + 편도 슬리피지 0.05% 가정')
            self.market_view = s['trade_policy']
            market = s['market_summary']
            if market.get('available'):
                color = ZONES[market['zone']][1]
                self.market_label.config(text=f'{market["label"]}  {market["score"]:+.1f}', fg=color)
                stamp = dt.datetime.fromtimestamp(market['time'], KST).strftime('%H:%M:%S')
                self.market_details.config(text=(f'전일 종가 대비(한국 09시 기준)  ·  BTC {market["btc"]:+.2%}  ETH {market["eth"]:+.2%}'
                    f'  ·  거래대금 상위 {market["count"]}개: 상승 {market["up"]} / 보합 {market["flat"]} / 하락 {market["down"]}'
                    f'  ·  {stamp} 갱신  ·  참고용'))
            else:
                self.market_label.config(text='확인 불가', fg=MUTED)
                self.market_details.config(text=market.get('reason', '시황 데이터 수집 중'))
            policy = s['trade_policy']
            if policy.get('available'):
                self.market_label.config(text='거래 국면: '+policy['label'],fg=ZONES[policy['zone']][1])
                self.market_details.config(text=policy['action'])
            else:
                self.market_label.config(text='거래 국면: 확인 불가',fg=MUTED)
                self.market_details.config(text=policy['reason'])
            self.draw_market()
        self.w.after(1000, self.tick)

    def close(self):
        if self.busy:
            messagebox.showinfo('처리 중', '작업이 끝난 뒤 종료하세요.', parent=self.w)
            return
        with self.engine.lock:
            if self.engine.live_exposure():
                messagebox.showwarning('실거래 관리 중', '실거래 보유분 또는 확인 중 주문이 남아 있습니다.\n전량 청산 및 체결 확인 후 종료하세요.\n창을 최소화하면 관리는 계속됩니다.', parent=self.w)
                return
            self.engine.stop_event.set()
        self.w.destroy()


def main():
    mutex = None
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel.CreateMutexW(None, False, 'Local\\UpbitTrendBotV2')
        if not mutex:
            raise RuntimeError('실행 잠금 실패')
        if ctypes.get_last_error() == 183:
            ctypes.windll.user32.MessageBoxW(None, '트렌드 봇이 이미 실행 중입니다. 작업표시줄을 확인하세요.', '중복 실행 방지', 0x40)
            return
    window = tk.Tk()
    try:
        App(window)
        window.mainloop()
    except Exception as e:
        messagebox.showerror('시작 실패', str(e), parent=window)
        window.destroy()


if __name__ == '__main__':
    main()
