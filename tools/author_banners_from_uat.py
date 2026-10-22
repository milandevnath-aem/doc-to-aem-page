"""Copy the hero banner of each article from UAT onto the local AEM author.

The UAT pages run the same new-hero-banner build as local, so every authored
value is recoverable from the rendered markup: .hero-card-title is
bannerTitle, .hero-card-desc carries bannerDescription as rich text, the <img>
in .hero-image-pane gives imagePath + imageAlt, and the alignment is a
layout-align-<x> class on .hero-layout-container.

Nothing is written without --apply. The default run reports what it would
change, old value against new, so the diff can be read before anything
touches the repository.

    set UAT_USER=...
    set UAT_PASS=...
    python tools/author_banners_from_uat.py                  # dry run
    python tools/author_banners_from_uat.py --apply --with-images
"""
import argparse
import base64
import html as htmlmod
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

LOCAL = "http://localhost:5402"
LOCAL_AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()
UAT = "https://uat.iffcotokio.co.in"
BANNER_NODE = "jcr:content/root/responsivegrid/new_hero_banner"

# local JCR page path -> public UAT path
PAGES = {
    "/content/iffcotokio/en/health-insurance/cashless-treatment-vs-reimbursement-claims":
        "/health-insurance/cashless-treatment-vs-reimbursement-claims",
    "/content/iffcotokio/en/health-insurance/family-health-insurance-vs-individual-health-insurance":
        "/health-insurance/family-health-insurance-vs-individual-health-insurance",
    "/content/iffcotokio/en/health-insurance/how-much-sum-insured-a-family-have-in-india":
        "/health-insurance/how-much-sum-insured-a-family-have-in-india",
    "/content/iffcotokio/en/health-insurance/what-is-medical-insurance-claim-procedure-and-how-does-it-work":
        "/health-insurance/what-is-medical-insurance-claim-procedure-and-how-does-it-work",
    "/content/iffcotokio/en/health-insurance/family-health-insurance/who-should-buy-family-health-insurance":
        "/health-insurance/family-health-insurance/who-should-buy-family-health-insurance",
    "/content/iffcotokio/en/health-insurance/family-health-insurance/why-buy-family-health-insurance-online":
        "/health-insurance/family-health-insurance/why-buy-family-health-insurance-online",
    "/content/iffcotokio/en/health-insurance/family-health-insurance/why-family-health-insurance-is-important-in-india":
        "/health-insurance/family-health-insurance/why-family-health-insurance-is-important-in-india",
}

FIELDS = ["bannerTitle", "bannerDescription", "imagePath", "imageAlt",
          "cardAlign", "bannerDate", "bannerReadTime", "backLink"]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def uat_auth():
    user, pw = os.environ.get("UAT_USER"), os.environ.get("UAT_PASS")
    if not user or not pw:
        sys.exit("Set UAT_USER and UAT_PASS, or pass --from-dir with the UAT pages saved as "
                 "<slug>.html -- uat.iffcotokio.co.in answers 401 to anonymous requests.")
    return "Basic " + base64.b64encode((user + ":" + pw).encode()).decode()


def read_saved(folder, slug):
    """Fall back to a saved copy of the UAT page.

    The dispatcher wants HTTP Basic credentials this process does not have, so
    allow the pages to be supplied as files instead: save each one from a
    signed-in browser as <slug>.html and point --from-dir at the folder.
    """
    for name in (slug + ".html", slug + ".htm", slug):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return None


def fetch(url, auth=None, binary=False, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    if auth:
        req.add_header("Authorization", auth)
    body = urllib.request.urlopen(req, timeout=timeout, context=CTX).read()
    return body if binary else body.decode("utf-8", "replace")


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def parse_banner(page_html):
    """Pull the authored values back out of the rendered component."""
    sec = re.search(r'<div class="insurance-hero-section".*?(?=<div class="(?:breadcrumb|responsivegrid))',
                    page_html, re.S)
    chunk = sec.group(0) if sec else page_html
    out = {}

    m = re.search(r'class="hero-layout-container[^"]*layout-align-(\w+)', chunk)
    out["cardAlign"] = m.group(1) if m else "left"

    m = re.search(r'<div class="hero-image-pane">\s*<img([^>]*)>', chunk, re.S)
    if m:
        src = re.search(r'src="([^"]+)"', m.group(1))
        alt = re.search(r'alt="([^"]*)"', m.group(1))
        if src:
            out["imagePath"] = htmlmod.unescape(src.group(1))
        if alt:
            out["imageAlt"] = htmlmod.unescape(alt.group(1))

    m = re.search(r'<h1 class="hero-card-title">(.*?)</h1>', chunk, re.S)
    if m:
        out["bannerTitle"] = htmlmod.unescape(strip_tags(m.group(1)))

    m = re.search(r'<div class="hero-card-desc">(.*?)</div>', chunk, re.S)
    if m:
        out["bannerDescription"] = m.group(1).strip()

    for field, cls in (("bannerDate", "hero-card-date"), ("bannerReadTime", "hero-card-readtime")):
        m = re.search(r'class="' + cls + r'"[^>]*>(.*?)<', chunk, re.S)
        if m and strip_tags(m.group(1)):
            out[field] = strip_tags(m.group(1))
    return out


def local_banner(page):
    try:
        return json.loads(fetch(LOCAL + page + "/" + BANNER_NODE + ".json", LOCAL_AUTH, timeout=25))
    except urllib.error.HTTPError as e:
        return {"__error": "HTTP " + str(e.code)}


def post(url, fields, files=None):
    boundary = "----banner" + base64.b16encode(os.urandom(8)).decode()
    body = b""
    for k, v in fields.items():
        part = "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + k + "\"\r\n\r\n" + str(v) + "\r\n"
        body += part.encode("utf-8")
    for k, (name, blob, ctype) in (files or {}).items():
        head = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + k
                + "\"; filename=\"" + name + "\"\r\nContent-Type: " + ctype + "\r\n\r\n")
        body += head.encode("utf-8") + blob + b"\r\n"
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", LOCAL_AUTH)
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    return urllib.request.urlopen(req, timeout=180).status


def upload_asset(dam_path, blob, ctype="image/png"):
    """Create the DAM folder if needed, then put the asset in it.

    /content/dam/iffcotokio/banner does not exist on the local instance, so a
    plain createasset against it 404s. Create the folder first; a second
    create against an existing folder is harmless.
    """
    folder, name = dam_path.rsplit("/", 1)
    parent, leaf = folder.rsplit("/", 1)
    try:
        post(LOCAL + parent, {"./" + leaf + "/jcr:primaryType": "sling:OrderedFolder"})
    except urllib.error.HTTPError:
        pass
    return post(LOCAL + folder + ".createasset.html", {"fileName": name},
                {"file": (name, blob, ctype)})


def apply_from_json(path, do_apply):
    """Author from a saved property set rather than re-reading UAT.

    The UAT dispatcher serves the component's JCR node as JSON under
    /content/..., which gives the authored values exactly as stored instead
    of the rendered text -- UAT runs a newer banner template (different date
    prefix, a 'min read' suffix, an author line local does not have), so
    reading the markup back would bake its presentation into our properties.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    changes = []
    for page, new in data.items():
        if page.startswith("_"):
            continue
        cur = local_banner(page)
        if "__error" in cur:
            print("=" * 104)
            print(page.rsplit("/", 1)[-1] + "  -- local node " + cur["__error"] + ", skipped")
            continue
        diff = {k: v for k, v in new.items() if str(cur.get(k, "")) != str(v)}
        print("=" * 104)
        print(page.rsplit("/", 1)[-1])
        for k in FIELDS + ["textIsRich"]:
            if k not in new:
                continue
            mark = "*" if k in diff else " "
            print("  " + mark + " " + k.ljust(20) + str(new[k])[:76])
            if k in diff:
                print("    " + " " * 20 + "was: " + str(cur.get(k, "-"))[:72])
        if diff:
            changes.append((page, new))

    print("\n" + "=" * 104)
    print(str(len(changes)) + " of " + str(len(data) - 1) + " pages would change")
    if not do_apply:
        print("dry run -- pass --apply to write")
        return
    for page, new in changes:
        fields = {"./" + k: v for k, v in new.items()}
        status = post(LOCAL + page + "/" + BANNER_NODE, fields)
        print(page.rsplit("/", 1)[-1].ljust(62) + "POST " + str(status))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to localhost:5402 (default is a dry run)")
    ap.add_argument("--with-images", action="store_true", help="also copy the banner image into the local DAM")
    ap.add_argument("--from-dir", help="read the UAT pages from <slug>.html files instead of fetching them")
    ap.add_argument("--from-json", help="read the banner properties straight from a JSON file "
                                        "(page path -> properties), skipping UAT entirely")
    args = ap.parse_args()

    if args.from_json:
        return apply_from_json(args.from_json, args.apply)

    auth = None if args.from_dir else uat_auth()
    changes = []
    for page, uat_path in PAGES.items():
        slug = page.rsplit("/", 1)[-1]
        print("=" * 104)
        print(slug)
        if args.from_dir:
            page_html = read_saved(args.from_dir, slug)
            if page_html is None:
                print("   no saved page for this slug in " + args.from_dir + " -- skipped")
                continue
        else:
            try:
                page_html = fetch(UAT + uat_path, auth)
            except urllib.error.HTTPError as e:
                print("   UAT HTTP " + str(e.code) + " -- skipped")
                continue
        new = parse_banner(page_html)
        if not new.get("bannerTitle"):
            print("   no hero banner found on the UAT page -- skipped")
            continue
        cur = local_banner(page)
        diff = {k: v for k, v in new.items() if str(cur.get(k, "")) != str(v)}
        for k in FIELDS:
            if k not in new:
                continue
            mark = "*" if k in diff else " "
            print("  " + mark + " " + k.ljust(20) + str(new[k])[:74])
            if k in diff:
                print("    " + " " * 20 + "was: " + str(cur.get(k, "-"))[:70])
        if diff:
            changes.append((page, new))

    print("\n" + "=" * 104)
    print(str(len(changes)) + " of " + str(len(PAGES)) + " pages would change")
    if not args.apply:
        print("dry run -- pass --apply to write")
        return

    for page, new in changes:
        dam = new.get("imagePath", "")
        if args.with_images and auth is None:
            print("--with-images needs UAT credentials; image copy skipped")
        elif args.with_images and dam.startswith("/content/dam/"):
            try:
                blob = fetch(UAT + dam, auth, binary=True)
            except urllib.error.HTTPError as e:
                print("image " + dam + " -> UAT HTTP " + str(e.code) + ", reference left dangling")
            else:
                ctype = "image/" + ("jpeg" if dam.lower().endswith((".jpg", ".jpeg")) else dam.rsplit(".", 1)[-1].lower())
                upload_asset(dam, blob, ctype)
                print("image " + dam + " -> uploaded (" + str(len(blob)) + " bytes)")
        fields = {"./" + k: v for k, v in new.items()}
        status = post(LOCAL + page + "/" + BANNER_NODE, fields)
        print(page.rsplit("/", 1)[-1].ljust(62) + "POST " + str(status))


if __name__ == "__main__":
    main()
