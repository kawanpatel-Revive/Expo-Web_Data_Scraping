"""Load a year tab into a TabState, and write the changes back in batches."""
from .columns import HEADERS, IDX
from .matcher import TabState, EXTRA


def build_state(header, rows):
    """Map the sheet's columns (by heading name) to our 35 columns.

    Returns (TabState, colmap) where colmap[our_col] = sheet column index or None.
    A column whose heading was renamed or removed is None: its data goes to Extra.
    """
    pos = {}
    for j, h in enumerate(header):
        h = (h or "").strip()
        if h in IDX and h not in pos:
            pos[h] = j
    colmap = [pos.get(h) for h in HEADERS]
    if colmap[EXTRA] is None:
        from .client import ClientError
        raise ClientError("The heading 'Extra' is missing in this tab. Please restore the heading row.")
    ours = []
    for r in rows:
        ours.append([(r[colmap[c]] if colmap[c] is not None and colmap[c] < len(r) else "")
                     for c in range(len(HEADERS))])
    missing = {c for c in range(len(HEADERS)) if colmap[c] is None}
    st = TabState(ours, missing=missing)
    return st, colmap, len(header)


def save_state(client, sheet_id, tab, state, colmap, width):
    """Write what changed since the last save. Returns number of edits + appended rows."""
    edits, appends, start = state.flush()
    if not edits and not appends:
        return 0
    sheet_edits = [[row, colmap[c], v] for row, c, v in edits if colmap[c] is not None]
    sheet_appends = []
    for r in appends:
        out = [""] * width
        for c, v in enumerate(r):
            if colmap[c] is not None:
                out[colmap[c]] = v
        sheet_appends.append(out)
    # keep requests small
    BATCH = 150
    for k in range(0, max(len(sheet_appends), 1), BATCH):
        part = sheet_appends[k:k + BATCH]
        e = sheet_edits if k == 0 else []
        client.write_tab(sheet_id, tab, e, part, start + k)
    if not sheet_appends and sheet_edits:
        pass
    return len(sheet_edits) + len(sheet_appends)
