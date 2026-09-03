#!/usr/bin/env python3
"""
zotero_reader.py — self-contained reader for a local Zotero library.

Enumerates bibliographic items and resolves the on-disk path of each item's
PDF attachment(s), so the scholar-rag ingest pipeline can extract full text.

Design notes
------------
* Zotero stores metadata in a normalized EAV schema: `items` -> `itemData`
  (itemID, fieldID, valueID) -> `itemDataValues` (valueID, value), with the
  field name in `fields`. Creators live in `itemCreators`/`creators`.
* A PDF attachment is itself an *item* (itemTypeID = attachment). Its file
  lives at:  <ZOTERO_DIR>/storage/<ATTACHMENT_ITEM_KEY>/<filename>
  where the filename is `itemAttachments.path` with the leading `storage:`
  stripped, and <ATTACHMENT_ITEM_KEY> is the attachment item's own `key`.
  `linkMode` distinguishes stored (0/1 -> "storage:") from linked-file (2)
  attachments (an absolute/relative path on disk).
* We open the live `zotero.sqlite` read-only + immutable (no lock, no copy);
  if that fails we fall back to copying to a temp file, then to the `.bak`.

Stable id
---------
`doc_id` = first 16 hex of sha256(normalized DOI) if a DOI exists, else of
the normalized title. This lets scholar-rag cross-link to scholar-knowledge
by DOI/title without assuming an identical hashing recipe.

Usage
-----
  python3 zotero_reader.py stats                 # coverage counts
  python3 zotero_reader.py list [--out FILE]     # NDJSON, one item per line
        [--with-pdf-only] [--limit N] [--zotero-dir DIR] [--types t1,t2]
"""
import os, sys, re, json, hashlib, shutil, sqlite3, argparse, tempfile, glob

# ---- Zotero library location -------------------------------------------------

def detect_zotero_dir(explicit=None):
    """Return a directory containing zotero.sqlite, or None."""
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("SCHOLAR_ZOTERO_DIR"):
        cands.append(os.environ["SCHOLAR_ZOTERO_DIR"])
    home = os.path.expanduser("~")
    cands += [
        os.path.join(home, "Zotero"),
        os.path.join(home, "Documents", "Zotero"),
        os.path.join(home, "snap", "zotero-snap", "common", "Zotero"),
    ]
    # Google Drive / other CloudStorage mounts
    cs = os.path.join(home, "Library", "CloudStorage")
    for pat in ("*/zotero", "*/Zotero", "*/My Drive/zotero", "*/My Drive/Zotero"):
        cands += glob.glob(os.path.join(cs, pat))
    for c in cands:
        if c and (os.path.isfile(os.path.join(c, "zotero.sqlite"))
                  or os.path.isfile(os.path.join(c, "zotero.sqlite.bak"))):
            return c
    # last-ditch: shallow find under CloudStorage
    if os.path.isdir(cs):
        for root, dirs, files in os.walk(cs):
            if "zotero.sqlite" in files:
                return root
            # cap depth to avoid walking the whole drive
            if root.count(os.sep) - cs.count(os.sep) >= 6:
                dirs[:] = []
    return None


def open_zotero_db(zotero_dir):
    """Open the Zotero DB read-only without disturbing a running Zotero.

    Strategy: URI immutable=1 (no lock, no copy) -> temp copy -> .bak.
    Returns (connection, note).
    """
    live = os.path.join(zotero_dir, "zotero.sqlite")
    bak = os.path.join(zotero_dir, "zotero.sqlite.bak")
    if os.path.isfile(live):
        try:
            uri = "file:%s?mode=ro&immutable=1" % live
            con = sqlite3.connect(uri, uri=True)
            con.execute("SELECT 1 FROM items LIMIT 1")   # smoke-probe
            return con, "immutable-ro"
        except Exception:
            pass
        try:
            tmp = os.path.join(tempfile.gettempdir(), "scholar_rag_zotero.sqlite")
            shutil.copy2(live, tmp)
            con = sqlite3.connect(tmp)
            con.execute("SELECT 1 FROM items LIMIT 1")
            return con, "temp-copy"
        except Exception:
            pass
    if os.path.isfile(bak):
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % bak, uri=True)
        return con, "bak-immutable-ro"
    raise SystemExit("ERROR: no readable zotero.sqlite in %s" % zotero_dir)


# ---- helpers -----------------------------------------------------------------

def norm_doi(doi):
    if not doi:
        return ""
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip()


def norm_title(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())).strip()


def make_doc_id(doi, title):
    key = norm_doi(doi) or norm_title(title)
    return hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()[:16] if key else ""


# Regular (non-attachment/annotation/note) top-level bibliographic types.
_EXCLUDE_TYPES = ("attachment", "note", "annotation")


def fetch_items(con, zotero_dir, types=None, with_pdf_only=False, limit=None):
    """Yield dict records for bibliographic items with resolved PDF paths."""
    cur = con.cursor()

    # fieldID lookup (schema-stable via name)
    def fid(name):
        r = cur.execute("SELECT fieldID FROM fields WHERE fieldName=?", (name,)).fetchone()
        return r[0] if r else -1

    F = {n: fid(n) for n in (
        "title", "date", "publicationTitle", "DOI", "volume",
        "issue", "pages", "abstractNote", "bookTitle", "publisher")}

    type_filter = ""
    params = []
    if types:
        type_filter = " AND it.typeName IN (%s)" % ",".join("?" * len(types))
        params += list(types)

    # Base item + core metadata via correlated subqueries (robust to missing fields).
    sql = """
    SELECT i.itemID, i.key, it.typeName,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS title,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS date,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS pub,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS doi,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS volume,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS issue,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS pages,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS abstract,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS booktitle,
           (SELECT v.value FROM itemData d JOIN itemDataValues v ON d.valueID=v.valueID
              WHERE d.itemID=i.itemID AND d.fieldID=?) AS publisher
    FROM items i
    JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
    WHERE it.typeName NOT IN ('attachment','note','annotation')
      AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
    """ + type_filter + " ORDER BY i.itemID"
    base_params = [F["title"], F["date"], F["publicationTitle"], F["DOI"],
                   F["volume"], F["issue"], F["pages"], F["abstractNote"],
                   F["bookTitle"], F["publisher"]] + params

    n = 0
    for row in cur.execute(sql, base_params):
        (item_id, key, typ, title, date, pub, doi, vol, issue, pages,
         abstract, booktitle, publisher) = row
        if not title:
            continue
        authors = fetch_authors(con, item_id)
        pdfs = fetch_pdf_paths(con, zotero_dir, item_id)
        if with_pdf_only and not pdfs:
            continue
        year = (re.match(r"\s*(\d{4})", date or "") or [None, ""])[1] if date else ""
        try:
            year = int(year) if year else None
        except Exception:
            year = None
        rec = {
            "doc_id": make_doc_id(doi, title),
            "zotero_key": key,
            "item_type": typ,
            "title": title.strip(),
            "authors": authors,
            "year": year,
            "journal": (pub or booktitle or "").strip(),
            "doi": norm_doi(doi),
            "volume": (vol or "").strip(),
            "issue": (issue or "").strip(),
            "pages": (pages or "").strip(),
            "publisher": (publisher or "").strip(),
            "abstract": (abstract or "").strip(),
            "pdf_paths": pdfs,
            "n_pdf": len(pdfs),
        }
        yield rec
        n += 1
        if limit and n >= limit:
            break


def fetch_authors(con, item_id):
    cur = con.cursor()
    rows = cur.execute(
        """SELECT cr.lastName, cr.firstName
             FROM itemCreators icr JOIN creators cr ON icr.creatorID=cr.creatorID
            WHERE icr.itemID=? ORDER BY icr.orderIndex""", (item_id,)).fetchall()
    out = []
    for last, first in rows:
        last = (last or "").strip(); first = (first or "").strip()
        out.append(", ".join(p for p in (last, first) if p))
    return out


def fetch_pdf_paths(con, zotero_dir, item_id):
    """Resolve on-disk PDF paths for an item's attachments (stored + linked)."""
    cur = con.cursor()
    # Attachment is a child item; join to items to get its storage key.
    rows = cur.execute(
        """SELECT a.itemID, ai.key, a.path, a.linkMode, a.contentType
             FROM itemAttachments a JOIN items ai ON a.itemID = ai.itemID
            WHERE a.parentItemID = ?""", (item_id,)).fetchall()
    storage = os.path.join(zotero_dir, "storage")
    out = []
    for att_id, att_key, path, link_mode, ctype in rows:
        if not path:
            continue
        is_pdf = (ctype == "application/pdf") or str(path).lower().endswith(".pdf")
        if not is_pdf:
            continue
        if str(path).startswith("storage:"):
            fname = path[len("storage:"):]
            full = os.path.join(storage, att_key, fname)
        elif str(path).startswith("attachments:"):
            # base-dir linked attachment — resolved relative to storage as best effort
            full = os.path.join(storage, path[len("attachments:"):])
        else:
            full = path  # absolute/relative linked file
        out.append({"path": full, "exists": os.path.isfile(full),
                    "link_mode": link_mode, "key": att_key})
    return out


# ---- CLI ---------------------------------------------------------------------

def cmd_stats(con, zotero_dir):
    items = list(fetch_items(con, zotero_dir))
    by_type = {}
    with_pdf = with_pdf_exist = with_doi = 0
    for r in items:
        by_type[r["item_type"]] = by_type.get(r["item_type"], 0) + 1
        if r["n_pdf"]:
            with_pdf += 1
            if any(p["exists"] for p in r["pdf_paths"]):
                with_pdf_exist += 1
        if r["doi"]:
            with_doi += 1
    print(json.dumps({
        "total_items": len(items),
        "with_pdf_attachment": with_pdf,
        "with_pdf_on_disk": with_pdf_exist,
        "with_doi": with_doi,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
    }, indent=2))


def cmd_list(con, zotero_dir, args):
    out = open(args.out, "w") if args.out else sys.stdout
    types = args.types.split(",") if args.types else None
    n = 0
    for rec in fetch_items(con, zotero_dir, types=types,
                           with_pdf_only=args.with_pdf_only, limit=args.limit):
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
    if args.out:
        out.close()
        print("wrote %d records -> %s" % (n, args.out))
    else:
        sys.stderr.write("wrote %d records\n" % n)


def main():
    ap = argparse.ArgumentParser(description="Read a local Zotero library.")
    ap.add_argument("command", choices=["stats", "list"])
    ap.add_argument("--zotero-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--with-pdf-only", action="store_true")
    ap.add_argument("--types", default=None, help="comma-separated itemType filter")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    zdir = detect_zotero_dir(args.zotero_dir)
    if not zdir:
        raise SystemExit("ERROR: could not locate a Zotero library. Set SCHOLAR_ZOTERO_DIR.")
    con, note = open_zotero_db(zdir)
    sys.stderr.write("[zotero_reader] %s  (%s)\n" % (zdir, note))

    if args.command == "stats":
        cmd_stats(con, zdir)
    else:
        cmd_list(con, zdir, args)


if __name__ == "__main__":
    main()
