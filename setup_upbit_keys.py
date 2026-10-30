"""Local masked key entry; checks account access without sending any orders."""
from pathlib import Path
import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import upbit_bot as bot

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "key_setup_status.json"


def save_keys(access, secret):
    path = ROOT / ".env"
    existing = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    names = {"UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY"}
    lines = [line for line in existing if line.partition("=")[0].strip() not in names]
    lines.extend(["UPBIT_ACCESS_KEY=" + access, "UPBIT_SECRET_KEY=" + secret])
    temporary = ROOT / ".env.tmp"
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    window = tk.Tk()
    window.title("업비트 API 키 연결 설정")
    window.geometry("650x340")
    window.resizable(False, False)
    window.attributes("-topmost", True)
    panel = ttk.Frame(window, padding=22)
    panel.pack(fill="both", expand=True)
    ttk.Label(panel, text="새로 발급한 두 키를 아래에 직접 붙여 넣으세요.").pack(anchor="w")
    ttk.Label(panel, text="조회 요청만 전송하며, 실거래 봇은 시작하지 않습니다.").pack(anchor="w", pady=(4, 14))
    values = []
    for label in ("Access Key", "Secret Key"):
        ttk.Label(panel, text=label).pack(anchor="w")
        entry = ttk.Entry(panel, show="*", width=80)
        entry.pack(fill="x", pady=(3, 10))
        values.append(entry)
    status = tk.StringVar(value="저장 위치: " + str(ROOT / ".env"))
    ttk.Label(panel, textvariable=status, wraplength=600).pack(anchor="w", pady=6)
    results = queue.Queue()

    def work(access, secret):
        try:
            api = bot.Upbit.__new__(bot.Upbit)
            api.access, api.secret = access, secret
            accounts = api.request("GET", "/v1/accounts")
            if not isinstance(accounts, list):
                raise ValueError("계좌 조회 응답을 확인할 수 없습니다.")
            save_keys(access, secret)
            RESULT.write_text(json.dumps({"status": "connected", "account_read": True, "orders_sent": False}), encoding="utf-8")
            results.put((True, "저장 및 계좌 연결 확인 완료. 주문은 전송하지 않았습니다."))
        except Exception as exc:
            # Only a fixed error code reaches UI/status; no response bodies or keys.
            detail = str(exc)
            codes = {"no_authorization_ip": "등록된 공인 IP를 확인해 주세요.",
                     "invalid_access_key": "Access Key를 확인해 주세요.",
                     "jwt_verification": "두 키가 같은 발급 건인지 확인해 주세요.",
                     "out_of_scope": "자산조회 권한을 확인해 주세요.",
                     "expired_access_key": "키가 만료되었습니다. 새 키를 입력해 주세요."}
            reason = next((text for code, text in codes.items() if code in detail),
                          "연결 또는 저장에 실패했습니다. 인터넷, 키, IP 및 폴더 권한을 확인해 주세요.")
            results.put((False, reason))

    def submit():
        access, secret = (entry.get().strip() for entry in values)
        if not access or not secret or any(ch.isspace() for ch in access + secret):
            messagebox.showerror("입력 확인", "공백 없이 두 키를 모두 입력하세요.", parent=window)
            return
        button.configure(state="disabled")
        status.set("계좌 조회로 연결을 확인 중입니다…")
        threading.Thread(target=work, args=(access, secret), daemon=True).start()

    def poll():
        try:
            ok, text = results.get_nowait()
        except queue.Empty:
            window.after(150, poll)
            return
        status.set(text)
        button.configure(state="normal")
        if ok:
            for entry in values:
                entry.delete(0, "end")
            messagebox.showinfo("연결 완료", text, parent=window)
            window.destroy()
        else:
            messagebox.showerror("확인 필요", text, parent=window)
            window.after(150, poll)

    button = ttk.Button(panel, text="저장 및 연결 확인", command=submit)
    button.pack(anchor="e", pady=6)
    values[0].focus_set()
    window.after(150, poll)
    window.mainloop()


if __name__ == "__main__":
    main()
