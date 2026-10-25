"""Stable query scheduling independent of physical spreadsheet row order."""
from datetime import datetime,timedelta
import math


def launch_order(value, year=2026):
    if isinstance(value,bool):return -1
    if isinstance(value,(int,float)):
        return float(value) if math.isfinite(value) else -1
    text=str(value or '').strip()
    for fmt in ('%Y-%m-%d','%Y-%m-%dT%H:%M:%S','%b-%d','%b %d'):
        try:
            date=datetime.strptime(text[:19] if 'T' in text else text,fmt)
            if '%Y' not in fmt:date=date.replace(year=int(year))
            return (date-datetime(1899,12,30)).total_seconds()/86400
        except (ValueError,OverflowError):pass
    return -1
