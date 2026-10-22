"""Explicit KOL retry outcomes. Unavailable data is not a transport failure."""
import re
from dataclasses import dataclass

import requests


def temporary_reason(reason):
    text=str(reason)
    # Meta can incorrectly use HTTP 500 for invalid parameters. Exclude that
    # individual failure without hiding a different field's genuine HTTP 503.
    text=re.sub(r'HTTP\s+5\d\d\s*/\s*Meta code (?:100|110|190|200)\b','invalid request',text)
    return bool(re.search(r'HTTP\s+(?:408|429|5\d\d)\b|Meta code (?:4|17|613|80002)\b|'
        r'\b(?:timeout|timed out|rate limit|quota|usage near limit|budget|batch pending|'
        r'read request failed|network request failed|connection failed|backoff)\b',text,re.I))


def classify_exception(error):
    from kol_meta_runtime import MetaDeferred
    if isinstance(error,MetaDeferred):return True,str(error)
    if isinstance(error,(requests.Timeout,requests.ConnectionError)):
        return True,'Network timeout or connection failure; retry queued'
    if isinstance(error,requests.HTTPError) and error.response is not None:
        status=error.response.status_code
        return status in (408,429) or status>=500,'HTTP %d; existing metrics retained'%status
    message=str(error)
    http=re.search(r'HTTP\s+(\d{3})(?:\s*/\s*Meta code\s+(\d+))?',message)
    if http:return temporary_reason(http.group()),http.group()+'; existing metrics retained'
    if temporary_reason(message):return True,'Temporary provider request failure; retry queued'
    if 'sheet changed' in message.lower() or 'target changed' in message.lower():
        return True,'Sheet changed during update; retry queued'
    return False,'Update requires review (%s); not retried this cycle'%type(error).__name__


@dataclass
class Outcome:
    success: bool = False
    reason: str = 'No usable metrics returned; existing values retained'
    retryable: bool = False
    retry_at: float = 0


class RowReport:
    def __init__(self):
        self.reasons=[];self.retryable=False;self.retry_at=0;self.incomplete=False

    def record(self,reason,retryable=False,retry_at=0):
        if reason and str(reason) not in self.reasons:self.reasons.append(str(reason))
        self.retryable=self.retryable or retryable
        self.retry_at=max(self.retry_at,retry_at)


def record_failure(env,reason,retryable=None,retry_at=0):
    report=env.get('_KOL_ROW_REPORT')
    if report is not None:
        report.incomplete=True
        report.record(reason,temporary_reason(reason) if retryable is None else retryable,retry_at)


class RowResult(set):
    def __init__(self,keys=(),unavailable=None):
        super().__init__(keys)
        self.unavailable=unavailable or {}
