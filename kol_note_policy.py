"""Failure-column presentation only; retry/checkpoint reasons remain unchanged."""
import re
from kol_failures import temporary_reason


def managed_note(value, prefixes):
    text=str(value or '')
    return not text or text.startswith(prefixes) or text.startswith(
        'remaining requests queued; Public Views unavailable:')


def failure_note(reason):
    parts=[x.strip() for x in str(reason or '').split(';') if x.strip()]
    pending_views=any(('Views' in x or 'views' in x) and (temporary_reason(x) or re.search(r'pending|deferred|queued|not queried',x,re.I)) for x in parts)
    pending_interaction=any(re.search(r'engagement|interaction',x,re.I) and (temporary_reason(x) or re.search(r'pending|deferred|queued|not queried',x,re.I)) for x in parts)
    result=[]
    for clause in parts:
        if temporary_reason(clause) or re.search(r'\b(?:queued|pending|deferred|not queried)\b',clause,re.I):continue
        if clause in ('existing values retained','existing metrics retained','HTTP request failed'):continue
        if pending_views and clause.startswith(('CPM not refreshed:','Total Views unavailable')):continue
        if pending_interaction and clause.startswith(('CPE not refreshed:','Total Interaction unavailable')):continue
        result.append(clause)
    return '; '.join(dict.fromkeys(result))
