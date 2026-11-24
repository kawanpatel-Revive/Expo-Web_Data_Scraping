"""The 35 columns of every year tab, and the rules for each kind of column."""

SINGLE = ["Company Name", "Country", "Website", "Address / Corporate Address",
          "About the Company", "Company Profile", "Email", "Logo / Image"]
MULTI = ["Product", "Category", "Subcategory", "Services",
         "Press Release", "Video"]            # (no Brochures column: brochure links are not put in the Sheet)
CONTACT_FIELDS = ["Name", "Designation", "Phone", "Email"]
N_CONTACTS = 4
MANUAL = "Hard Copy Brochure"
HELPERS = ["Source", "Check", "Extra", "Last Updated"]


def contact_col(n, field):
    return f"Contact Person {n} {field}"


CONTACT_COLS = [contact_col(n, f) for n in range(1, N_CONTACTS + 1) for f in CONTACT_FIELDS]

HEADERS = SINGLE + ["Product", "Category", "Subcategory", "Services"] + \
    ["Press Release", "Video"] + CONTACT_COLS + [MANUAL] + HELPERS

assert len(HEADERS) == 35, len(HEADERS)
IDX = {h: i for i, h in enumerate(HEADERS)}


def col_letter(i):
    """0-based index -> A, B, ... AJ"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def empty_row():
    return [""] * len(HEADERS)
