"""Closed-period cache policy shared by local reporting adapters."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import os

def force_refresh_requested(flag=False):
    return bool(flag) or os.environ.get('AI_DASHBOARD_FORCE_REFRESH') == '1'

def resolved_zone(zone):
    zone={'PacificTimeUSCanadaTijuana':'America/Los_Angeles'}.get(zone,zone)
    if not zone:return None
    try:ZoneInfo(zone);return zone
    except (ValueError,TypeError,KeyError):return None

def period_closed(end, zone, at=None):
    zone=resolved_zone(zone)
    if not zone:return False
    try:
        instant=at or datetime.now(timezone.utc)
        if instant.tzinfo is None:instant=instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(ZoneInfo(zone)).date() > date.fromisoformat(end)
    except (ValueError,TypeError,KeyError):return False

def captured_closed_period(cache,end,zone):
    try:
        captured=datetime.fromisoformat(str(cache.get('fetchedAt','')).replace('Z','+00:00'))
    except (ValueError,TypeError):return False
    return period_closed(end,zone,captured)

def reusable_cache(cache,end,zone,complete,force=False,now=None):
    if force_refresh_requested(force):return False
    instant=now or datetime.now(timezone.utc)
    try:
        captured=datetime.fromisoformat(str(cache.get('fetchedAt','')).replace('Z','+00:00'))
        if captured.tzinfo is None:captured=captured.replace(tzinfo=timezone.utc)
        age=(instant-captured).total_seconds()
    except (ValueError,TypeError):return False
    if 0 <= age < 900:return True
    return cache.get('status','ready')=='ready' and complete and captured_closed_period(cache,end,zone) and period_closed(end,zone,instant)

def closure_metadata(cache,end,zone):
    # Unknown descriptive account zones are not evidence of closure. The
    # publisher may apply its conservative latest-timezone fallback.
    if not resolved_zone(zone):return {}
    closed=captured_closed_period(cache,end,zone)
    return {'periodClosed':closed,'fetchedAfterPeriodEnd':closed}
