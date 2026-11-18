"""파일 교체(os.replace) 재시도. 2026-11-18: 다른 프로세스(브라우저·백신·관찰창)가 대상 파일을 잠깐 열고 있으면
Windows에서 PermissionError(WinError 5)가 나서 account_sync가 죽고 live.json 저장이 한 주기 밀렸다(11-08, 11-16 3회)."""
import os
import time


def replace_retry(src, dst, tries=10, wait=0.1):
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))
