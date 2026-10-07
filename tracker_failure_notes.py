"""English, row-scoped update reasons in an existing user-owned column."""
HEADER = 'Reason for data update failure'
PREFIX = 'Update failed: '


def plan_notes(values, successful_rows, failures, creator_col, first_row):
    headers = values[0] if values else []
    columns = [i for i, value in enumerate(headers) if str(value).strip() == HEADER]
    if not columns:
        return []  # Never create/move the user's columns implicitly.
    if len(columns) != 1:
        raise RuntimeError('Duplicate failure reason headers')
    col = columns[0]
    reasons = {}
    for item in failures:
        reasons.setdefault(item['row'], []).append(item['reason'])
    changes = []
    for number, row in enumerate(values[first_row-1:], first_row):
        if len(row) <= creator_col or not row[creator_col] or str(row[creator_col]).strip().lower() == 'summary':
            continue
        old = str(row[col]) if len(row) > col else ''
        if old and not old.startswith(PREFIX):
            continue  # Manual notes are user-owned.
        if number in reasons:
            value = PREFIX + '; '.join(dict.fromkeys(reasons[number]))
        elif number in successful_rows:
            value = ''
        else:
            continue
        if old != value:
            changes.append({'row': number, 'changes': {col: value}})
    return changes
