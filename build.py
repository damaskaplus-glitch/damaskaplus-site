#!/usr/bin/env python3
"""Damaska Plus website generator.

Pulls the Facebook page's posts (Graph API), sorts them into the three series by hashtag,
downloads their images, and writes a static site into _site/ in the approved "الشاشة" design.

Env:
  FB_PAGE_TOKEN   page access token (GitHub secret). Without it the build reuses data/posts.json.
  FB_PAGE_ID      optional, defaults to the Damaska Plus page.
  FONT_TITLE_B64  optional base64 of Fatimah-Bold (GitHub secret). Titles are then drawn as
                  outlines; the font file itself is never published. Without it titles use the system font.
"""
import base64, datetime as dt, html, io, json, os, re, shutil, sys, unicodedata, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
MEDIA = os.path.join(ROOT, "media")
OUT = os.path.join(ROOT, "_site")
SITE = "https://damaskaplus.com"
PAGE_ID = os.environ.get("FB_PAGE_ID", "540390955818372")
GRAPH = "https://graph.facebook.com/v23.0"
FB_PAGE = "https://www.facebook.com/DamaskaPlus"
SOCIAL = [("فيسبوك", FB_PAGE), ("إنستغرام", "https://www.instagram.com/damaskaplus"),
          ("إكس", "https://x.com/DamaskaPlus"), ("يوتيوب", "https://www.youtube.com/@DamaskaPlus")]
BIO = ("منصة سورية تهدف إلى نشر الوعي والإعلام الحر وتعزيز حرية التعبير وتوفير محتوى هادف "
       "يعكس الواقع السوري بموضوعية وشفافية. تسعى إلى تمكين المجتمع السوري من الوصول إلى "
       "المعلومات الدقيقة والمستقلة، والتفاعل مع القضايا الاجتماعية والسياسية.")
LICENSE_LINE = "أول منصة إعلامية مسجّلة لدى وزارة الإعلام السورية بعد التحرير"

SERIES = {
    "street": {"name": "سؤال الشارع", "tag": "#سوال_الشارع", "slug": "street-question", "day": "الاثنين",
               "about": "ننزل إلى الشارع ونسأل الناس أسئلة محددة، ونسمع إجاباتهم كما هي. كل اثنين."},
    "changed": {"name": "شو تغيّر", "tag": "#شو_تغير", "slug": "what-changed", "day": "الخميس",
                "about": "نقارن بين ما كان في عهد النظام البائد وما صار بعد التحرير، في مختلف جوانب الحياة. كل خميس."},
    "numbers": {"name": "سوريا بالأرقام", "tag": "#سوريا_بالارقام", "slug": "syria-in-numbers", "day": "دوري",
                "about": "إحصائيات رسمية ومجتمعية وثقافية من داخل سوريا، في إنفوغراف يُقرأ بنظرة."},
}
MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول",
          "تشرين الأول", "تشرين الثاني", "كانون الأول"]
DAMASCUS = dt.timezone(dt.timedelta(hours=3))
E = lambda s: html.escape(str(s), quote=True)

# ---------------------------------------------------------------- Facebook

def graph(path, **params):
    params["access_token"] = os.environ["FB_PAGE_TOKEN"]
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)

def paged(path, limit_total, **params):
    out, data = [], graph(path, **params)
    while True:
        out += data.get("data", [])
        nxt = data.get("paging", {}).get("next")
        if not nxt or len(out) >= limit_total:
            return out[:limit_total]
        with urllib.request.urlopen(nxt, timeout=40) as r:
            data = json.load(r)

POST_FIELDS = ("id,message,created_time,permalink_url,full_picture,"
               "attachments{media_type,type,url,target{id},media{image{src,width,height},source},"
               "subattachments.limit(30){media{image{src,width,height}},type,url}}")
REEL_FIELDS = "id,description,created_time,permalink_url,thumbnails{uri,is_preferred,width,height}"

def fetch(existing):
    first = not existing
    posts = paged(f"{PAGE_ID}/posts", 100 if first else 40, fields=POST_FIELDS, limit=50)
    try:
        reels = paged(f"{PAGE_ID}/video_reels", 60 if first else 20, fields=REEL_FIELDS, limit=30)
    except Exception as ex:  # reels endpoint may be unavailable for some pages
        print("reels:", ex); reels = []
    items = {}
    video_ids = set()
    for p in posts:
        it = from_post(p)
        if it:
            items[it["id"]] = it
            if it.get("video_id"): video_ids.add(it["video_id"])
    for r in reels:
        if r["id"] in video_ids or any(r["id"] in (i.get("url") or "") for i in items.values()):
            continue
        items["reel_" + r["id"]] = from_reel(r)
    return items

def from_post(p):
    att = (p.get("attachments") or {}).get("data") or [{}]
    a = att[0]
    mt, url = (a.get("media_type") or "").lower(), a.get("url") or ""
    subs = (a.get("subattachments") or {}).get("data") or []
    images = [s["media"]["image"]["src"] for s in subs if s.get("media", {}).get("image")]
    if not images and a.get("media", {}).get("image"):
        images = [a["media"]["image"]["src"]]
    if not images and p.get("full_picture"):
        images = [p["full_picture"]]
    if mt == "video":
        kind = "ريل" if "/reel/" in url or "/reel/" in (p.get("permalink_url") or "") else "فيديو"
    elif mt == "album" or len(images) > 1:
        kind = "ألبوم"
    elif mt == "photo":
        kind = "إنفوغراف"
    else:
        kind = "منشور"
    link = url if mt == "video" and "facebook.com" in url else p.get("permalink_url")
    return {"id": p["id"], "kind": kind, "message": p.get("message") or "", "created": p["created_time"],
            "permalink": p.get("permalink_url"), "video_url": link if kind in ("ريل", "فيديو") else None,
            "video_id": (a.get("target") or {}).get("id"), "remote_images": images[:30], "url": url}

def from_reel(r):
    th = (r.get("thumbnails") or {}).get("data") or []
    pref = [t for t in th if t.get("is_preferred")] or sorted(th, key=lambda t: -(t.get("width") or 0))
    link = r.get("permalink_url") or ""
    if link.startswith("/"): link = "https://www.facebook.com" + link
    return {"id": "reel_" + r["id"], "kind": "ريل", "message": r.get("description") or "",
            "created": r["created_time"], "permalink": link, "video_url": link, "video_id": r["id"],
            "remote_images": [pref[0]["uri"]] if pref else [], "url": link}

def download_images(items):
    os.makedirs(MEDIA, exist_ok=True)
    for it in items.values():
        local = it.get("images") or []
        if len(local) >= len(it.get("remote_images") or []) and local:
            continue
        local = []
        for n, src in enumerate(it.get("remote_images") or []):
            name = re.sub(r"[^0-9A-Za-z_]", "_", it["id"]) + (f"_{n}" if n else "") + ".jpg"
            path = os.path.join(MEDIA, name)
            if not os.path.exists(path):
                try:
                    with urllib.request.urlopen(src, timeout=40) as r, open(path, "wb") as f:
                        f.write(r.read())
                except Exception as ex:
                    print("image", it["id"], ex); continue
            local.append(name)
        it["images"] = local

# ---------------------------------------------------------------- content helpers

TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟـ]")

def norm(s):
    s = TASHKEEL.sub("", unicodedata.normalize("NFC", s or ""))
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ؤ", "و"), ("ئ", "ي"), ("ى", "ي")):
        s = s.replace(a, b)
    return s

def detect_series(msg):
    n = norm(msg)
    for key, s in SERIES.items():
        if norm(s["tag"]) in n:
            return key
    return None

HASHTAG = re.compile(r"#[\w؀-ۿ_]+")

def split_text(msg, kind):
    lines = [HASHTAG.sub("", l).strip() for l in (msg or "").splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return ({"ريل": "ريل جديد", "فيديو": "فيديو جديد", "ألبوم": "ألبوم صور",
                 "إنفوغراف": "إنفوغراف"}.get(kind, "منشور جديد"), [])
    title = lines[0]
    if len(title) > 120:
        cut = title[:118].rsplit(" ", 1)[0]
        return cut + "…", [title] + lines[1:]
    return title, lines[1:]

def when(iso):
    d = dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(DAMASCUS)
    return d, f"{d.day} {MONTHS[d.month - 1]} {d.year}"

def prepare(items, overrides):
    posts = []
    for it in items.values():
        ov = overrides.get(it["id"], {})
        if ov.get("hide"):
            continue
        p = dict(it)
        p["series"] = ov.get("series", detect_series(it["message"]))
        if p["series"] not in SERIES: p["series"] = None
        p["title"], p["body"] = split_text(it["message"], it["kind"])
        if ov.get("title"): p["title"] = ov["title"]
        if it.get("created"):
            p["dt"], p["date"] = when(it["created"])
        else:
            p["dt"], p["date"] = None, ""
        p["slug"] = re.sub(r"[^0-9A-Za-z_-]", "-", it["id"])
        p["href"] = f"/p/{p['slug']}/"
        p["thumb"] = f"/media/{it['images'][0]}" if it.get("images") else None
        posts.append(p)
    if all(p["dt"] for p in posts):
        posts.sort(key=lambda p: p["dt"], reverse=True)
    else:
        posts.sort(key=lambda p: p.get("order", 0))
    return posts

def kind_from_url(u):
    if "/reel/" in u: return "ريل"
    if "/videos/" in u or "/watch" in u: return "فيديو"
    if "/photo" in u: return "إنفوغراف"
    return "منشور"

def from_links():
    """Items listed by hand in data/links.json (newest first): no Facebook token needed."""
    path = os.path.join(DATA, "links.json")
    if not os.path.exists(path):
        return {}
    items = {}
    for n, e in enumerate(json.load(open(path, encoding="utf-8")).get("items", [])):
        u = e["url"].strip()
        num = re.findall(r"\d{6,}", u)
        iid = "fb_" + (num[-1] if num else str(n))
        kind = e.get("kind") or kind_from_url(u)
        msg = (e.get("title", "") + "\n" + e.get("text", "")).strip()
        if e.get("series"):
            msg += "\n" + SERIES.get(e["series"], {}).get("tag", "")
        items[iid] = {"id": iid, "kind": kind, "message": msg, "created": e.get("date"), "order": n,
                      "permalink": u, "video_url": u if kind in ("ريل", "فيديو") else None,
                      "images": e.get("images", []), "remote_images": []}
    return items

# ---------------------------------------------------------------- Fatimah outlines (optional)

class Outliner:
    def __init__(self):
        self.ok, self.used = False, {}
        b64 = os.environ.get("FONT_TITLE_B64", "").strip()
        if not b64:
            return
        try:
            import uharfbuzz as hb
            from fontTools.ttLib import TTFont
            raw = base64.b64decode(b64)
            tt = TTFont(io.BytesIO(raw))
            buf = io.BytesIO(); tt.flavor = None; tt.save(buf); sfnt = buf.getvalue()
            self.hb, self.tt = hb, TTFont(io.BytesIO(sfnt))
            self.font = hb.Font(hb.Face(sfnt))
            self.gs, self.order = self.tt.getGlyphSet(), self.tt.getGlyphOrder()
            self.asc, self.desc = self.tt["hhea"].ascent, self.tt["hhea"].descent
            self.cache, self.ok = {}, True
        except Exception as ex:
            print("font:", ex)

    def glyph(self, name):
        if name not in self.cache:
            from fontTools.pens.svgPathPen import SVGPathPen
            from fontTools.pens.transformPen import TransformPen
            pen = SVGPathPen(self.gs, ntos=lambda v: str(round(v)))
            self.gs[name].draw(TransformPen(pen, (1, 0, 0, -1, 0, 0)))
            self.cache[name] = (f"g{len(self.cache)}", pen.getCommands())
        gid, d = self.cache[name]
        if d: self.used[gid] = d
        return gid if d else None

    def word(self, w):
        b = self.hb.Buffer(); b.add_str(w); b.guess_segment_properties()
        self.hb.shape(self.font, b, {"dlig": False, "swsh": False})
        x, uses = 0, []
        for info, pos in zip(b.glyph_infos, b.glyph_positions):
            gid = self.glyph(self.order[info.codepoint])
            if gid:
                y = f' y="{-pos.y_offset}"' if pos.y_offset else ""
                uses.append(f'<use href="#{gid}" x="{x + pos.x_offset}"{y}/>')
            x += pos.x_advance
        h = self.asc - self.desc
        return (f'<svg class="w" aria-hidden="true" viewBox="0 {-self.asc} {x} {h}" '
                f'style="aspect-ratio:{x}/{h}">{"".join(uses)}</svg>')

    def text(self, s, tag="span", cls=""):
        if not self.ok or not s:
            return f'<{tag} class="{cls}">{E(s)}</{tag}>'
        words = "".join(self.word(w) for w in s.split())
        return f'<{tag} class="ot {cls}"><span class="sr">{E(s)}</span>{words}</{tag}>'

    def defs(self):
        if not self.used:
            return ""
        out = "".join(f'<path id="{i}" d="{d}"/>' for i, d in self.used.items())
        self.used = {}
        return f'<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>{out}</defs></svg>'

OT = None  # set in main

# ---------------------------------------------------------------- page chrome

CSS = """
:root{--bg:#161D28;--panel:#1C2433;--card:#2F3F57;--line:#3A4A63;--text:#FEF5EF;--muted:#AAB8CC;--yellow:#FFCF50;--blue:#2F3F57;--cream:#FEF5EF}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,"Segoe UI",Tahoma,sans-serif;line-height:1.7}
a{color:var(--yellow)}a:hover{color:var(--text)}
img{max-width:100%;display:block}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.ot{display:flex;flex-wrap:wrap;column-gap:.24em;row-gap:0;align-items:center}
.ot .w{height:2em;width:auto;fill:currentColor;max-width:100%;margin:-.22em 0}
.skip{position:absolute;right:-999px}.skip:focus{right:16px;top:8px;background:var(--yellow);color:var(--blue);padding:8px 14px;z-index:10}
header.top{background:var(--panel);border-bottom:1px solid var(--card)}
header.top .wrap{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding-top:12px;padding-bottom:12px}
.logo img{height:46px;width:auto;border-radius:8px}
nav.main{display:flex;flex-wrap:wrap;gap:4px 20px;font-weight:500}
nav.main a{color:var(--text);text-decoration:none;padding:8px 0}
nav.main a[aria-current]{color:var(--yellow)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:10px;min-height:46px;padding:10px 24px;border-radius:999px;font-weight:700;text-decoration:none}
.btn.y{background:var(--yellow);color:var(--blue)}.btn.y:hover{color:var(--blue);filter:brightness(1.05)}
.btn.o{border:2px solid var(--line);color:var(--text);font-weight:500}
.btn:focus-visible,a:focus-visible,button:focus-visible{outline:3px solid var(--yellow);outline-offset:3px}
.tag{display:inline-block;background:var(--yellow);color:var(--blue);font-weight:700;font-size:14px;padding:3px 14px;border-radius:6px}
.chip{display:inline-block;background:rgba(22,29,40,.86);color:var(--text);border-radius:6px;padding:1px 9px;font-size:12px}
.hero{padding:48px 0}
.hero .wrap{display:flex;flex-wrap:wrap;gap:40px;align-items:center}
.hero .txt{flex:999 1 460px;min-width:0;display:flex;flex-direction:column;gap:16px;align-items:flex-start}
.hero h1{margin:0;font-size:clamp(30px,5vw,52px);line-height:1.3}
.hero p{margin:0;font-size:19px;color:var(--muted);max-width:580px}
.hero .med{flex:1 1 280px;display:flex;justify-content:center}
.frame{position:relative;width:min(320px,100%);aspect-ratio:9/16;background:var(--card);border:1px solid var(--line);border-radius:24px;overflow:hidden;display:block}
.frame img{width:100%;height:100%;object-fit:cover}
.play{position:absolute;inset:0;margin:auto;width:72px;height:72px;border-radius:50%;background:var(--yellow);display:flex;align-items:center;justify-content:center;box-shadow:0 6px 24px rgba(0,0,0,.35)}
.sched{background:var(--panel);border-top:1px solid var(--card);border-bottom:1px solid var(--card)}
.sched .wrap{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;padding-top:18px;padding-bottom:18px}
.sched a{display:flex;align-items:center;gap:14px;color:var(--text);text-decoration:none}
.sched b{background:var(--card);border-radius:10px;padding:6px 14px;min-width:76px;text-align:center}
.sched span{font-size:18px;font-weight:500}
section.blk{padding:48px 0 8px}
.head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:20px}
.head h2,.head h1{margin:0;font-size:clamp(24px,3.2vw,34px)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:18px}
.grid.sq{grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
.card{display:flex;flex-direction:column;gap:10px;color:var(--text);text-decoration:none}
.card .th{position:relative;aspect-ratio:9/16;background:var(--card);border-radius:16px;overflow:hidden}
.grid.sq .card .th{aspect-ratio:1/1}
.card .th img{width:100%;height:100%;object-fit:cover}
.card .th .chip{position:absolute;bottom:10px;right:10px}
.card h3{margin:0;font-size:16px;line-height:1.55;font-weight:500}
.tile{position:absolute;inset:0;display:flex;align-items:center;justify-content:center}
.tile.blue{background:#2F3F57}.tile.olive{background:#616F47}.tile.sand{background:#F4E0B2}
.card .th .tile .mk{position:absolute;top:12px;right:12px;width:30px;height:30px;border-radius:7px;object-fit:contain}
.tile .pl:not(:empty){width:58px;height:58px;border-radius:50%;background:var(--yellow);display:flex;align-items:center;justify-content:center}
.tile .pl svg{width:22px;height:22px}
.frame iframe{width:100%;height:100%;border:0;display:block}
.fbtl{display:flex;justify-content:center}
.fbtl iframe{width:100%;max-width:500px;height:720px;border:0;border-radius:16px;background:#fff}
.card time{font-size:13px;color:var(--muted)}
.card:hover h3{color:var(--yellow)}
.about{padding:40px 0 64px}
.about .box{background:var(--cream);color:var(--blue);border-radius:20px;padding:32px;display:flex;flex-wrap:wrap;gap:20px;align-items:center;justify-content:space-between}
.about .box p{margin:0}.about .box .t{font-weight:700;font-size:22px;line-height:1.5;margin-bottom:6px}
.about .box .s{color:#5C6675}
.btn.b{background:var(--blue);color:var(--cream)}
footer.bot{background:var(--panel);border-top:1px solid var(--card);color:var(--muted)}
footer.bot .wrap{display:flex;flex-wrap:wrap;justify-content:space-between;gap:14px;padding-top:26px;padding-bottom:26px}
footer.bot nav{display:flex;flex-wrap:wrap;gap:18px}
footer.bot a{color:var(--muted)}
.empty{color:var(--muted);padding:24px 0}
/* post page */
.post{padding:28px 0 8px}
.post .wrap{max-width:760px}
.meta{display:flex;flex-wrap:wrap;gap:10px;align-items:center;color:var(--muted);font-size:14px}
.post h1{margin:12px 0 18px;font-size:clamp(26px,4vw,40px);line-height:1.4}
.player{width:100%;max-width:420px;margin:0 auto;aspect-ratio:9/16;border-radius:20px;overflow:hidden;background:var(--card);border:1px solid var(--line)}
.player.wide{max-width:none;aspect-ratio:16/9}
.player iframe{width:100%;height:100%;border:0}
.gallery{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:8px}
.gallery img{flex:0 0 min(86%,520px);scroll-snap-align:center;border-radius:16px;background:var(--card)}
.single img{border-radius:16px;margin:0 auto}
.body{font-size:18px;margin-top:20px}
.body p{margin:0 0 12px}
.share{margin-top:22px;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.share a,.share button{min-height:46px;background:var(--panel);border:1px solid var(--line);border-radius:12px;color:var(--text);font:inherit;font-weight:500;text-decoration:none;display:flex;align-items:center;justify-content:center;cursor:pointer}
.cta{margin-top:12px;width:100%}
.filters{display:flex;flex-wrap:wrap;gap:8px}
.filters button{font:inherit;font-weight:500;min-height:44px;padding:6px 18px;border-radius:999px;border:2px solid var(--line);background:transparent;color:var(--text);cursor:pointer}
.filters button[aria-pressed=true]{background:var(--yellow);border-color:var(--yellow);color:var(--blue)}
.lead{font-size:18px;color:var(--muted);max-width:720px;margin:0 0 8px}
.prose{max-width:760px;font-size:18px}
.prose h2{font-size:24px;margin:28px 0 8px}
@media (max-width:640px){
 nav.main{order:3;width:100%;gap:2px 16px;font-size:15px}
 .hero{padding:28px 0}.hero .wrap{gap:22px}
 .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
 .grid.sq{grid-template-columns:repeat(2,minmax(0,1fr))}
 .card h3{font-size:14px}
 .about .box{padding:22px}
}
"""

PLAY = '<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4l12 8-12 8z" fill="#2F3F57"/></svg>'
PLAY_S = '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l13 8-13 8z" fill="#2F3F57"/></svg>'

def page(path, title, body, desc=BIO, image=None, current=None):
    nav = [("/", "الرئيسية")] + [(f"/{s['slug']}/", s["name"]) for s in SERIES.values()] + \
          [("/all/", "كل المنشورات"), ("/about/", "من نحن")]
    cur = ' aria-current="page"'
    navh = "".join(f'<a href="{h}"{cur if h == current else ""}>{E(t)}</a>' for h, t in nav)
    soc = "".join(f'<a href="{u}" rel="noopener" target="_blank">{t}</a>' for t, u in SOCIAL)
    full = f"{title} | دامسكا بلس" if title != "دامسكا بلس" else title
    og_img = SITE + (image or "/logo.png")
    defs = OT.defs() if OT else ""
    doc = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(full)}</title>
<meta name="description" content="{E(desc[:200])}">
<meta name="theme-color" content="#1C2433">
<link rel="canonical" href="{SITE}{path}">
<link rel="alternate" type="application/rss+xml" title="دامسكا بلس" href="/rss.xml">
<meta property="og:site_name" content="دامسكا بلس">
<meta property="og:title" content="{E(title)}">
<meta property="og:description" content="{E(desc[:200])}">
<meta property="og:url" content="{SITE}{path}">
<meta property="og:image" content="{og_img}">
<meta property="og:locale" content="ar_AR">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/style.css">
</head>
<body>
{defs}
<a class="skip" href="#main">تخطَّ إلى المحتوى</a>
<header class="top"><div class="wrap">
<a class="logo" href="/"><img src="/logo.svg" alt="دامسكا بلس" width="111" height="48"></a>
<nav class="main" aria-label="القائمة الرئيسية">{navh}</nav>
<a class="btn y" href="{FB_PAGE}" rel="noopener" target="_blank">تابعنا</a>
</div></header>
<main id="main">
{body}
</main>
<footer class="bot"><div class="wrap">
<span>دامسكا بلس · {E(LICENSE_LINE)}</span>
<nav aria-label="حسابات التواصل">{soc}<a href="/rss.xml">RSS</a></nav>
</div></footer>
</body>
</html>
"""
    dest = os.path.join(OUT, path.strip("/"), "index.html") if path != "/404.html" else os.path.join(OUT, "404.html")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(doc)

def T(s, tag="span", cls=""):
    return OT.text(s, tag, cls)

def card(p, show_series=True):
    label = SERIES[p["series"]]["name"] if (show_series and p["series"]) else p["kind"]
    if p["thumb"]:
        th = f'<img src="{p["thumb"]}" alt="" loading="lazy">'
    else:
        tone = {"changed": "olive", "numbers": "sand"}.get(p["series"], "blue")
        icon = PLAY if p["kind"] in ("ريل", "فيديو") else ""
        th = f'<span class="tile {tone}"><img class="mk" src="/favicon.svg" alt=""><span class="pl">{icon}</span></span>'
    tm = f'<time datetime="{p["dt"].isoformat()}">{p["date"]}</time>' if p["dt"] else ""
    return (f'<a class="card" href="{p["href"]}"><div class="th">{th}<span class="chip">{E(label)}</span></div>'
            f'<h3>{E(p["title"])}</h3>{tm}</a>')

def sched():
    rows = "".join(f'<a href="/{s["slug"]}/"><b>{s["day"]}</b><span>{E(s["name"])}</span></a>' for s in SERIES.values())
    return f'<section class="sched" aria-label="مواعيد البرامج"><div class="wrap">{rows}</div></section>'

def about_strip():
    return (f'<section class="about"><div class="wrap"><div class="box"><div style="flex:999 1 420px;min-width:0">'
            f'<p class="t">{E(LICENSE_LINE)}</p><p class="s">منصة سورية تهدف إلى نشر الوعي والإعلام الحر وتعزيز حرية التعبير.</p>'
            f'</div><a class="btn b" href="/about/">من نحن</a></div></div></section>')

# ---------------------------------------------------------------- pages

def home(posts):
    episodes = [p for p in posts if p["series"] in ("street", "changed")]
    videos = [p for p in posts if p["kind"] in ("ريل", "فيديو")]
    lead = (episodes or videos or posts or [None])[0]
    if lead:
        label = (f"الحلقة الجديدة · {SERIES[lead['series']]['name']}" if lead["series"] else "الأحدث")
        excerpt = (lead["body"][0] if lead["body"] else "")[:220]
        more = (f'<a class="btn o" href="/{SERIES[lead["series"]]["slug"]}/">كل حلقات {E(SERIES[lead["series"]]["name"])}</a>'
                if lead["series"] else '<a class="btn o" href="/all/">كل المنشورات</a>')
        img = f'<img src="{lead["thumb"]}" alt="">' if lead["thumb"] else ""
        if not lead["thumb"] and lead.get("video_url"):
            src = "https://www.facebook.com/plugins/video.php?" + urllib.parse.urlencode({"href": lead["video_url"], "show_text": "false", "width": "320"})
            media_html = (f'<div class="frame"><iframe src="{E(src)}" title="{E(lead["title"])}" '
                          f'allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share" allowfullscreen></iframe></div>')
        else:
            media_html = f'<a class="frame" href="{lead["href"]}" aria-label="{E(lead["title"])}">{img}<span class="play">{PLAY}</span></a>'
        hero = f"""<section class="hero"><div class="wrap">
<div class="txt"><span class="tag">{E(label)}</span>{T(lead["title"], "h1")}
{f"<p>{E(excerpt)}</p>" if excerpt else ""}
<div style="display:flex;flex-wrap:wrap;gap:12px"><a class="btn y" href="{lead["href"]}">{PLAY_S}{"شاهد الحلقة" if lead["kind"] in ("ريل","فيديو") else "اقرأ المنشور"}</a>{more}</div></div>
<div class="med">{media_html}</div>
</div></section>"""
    else:
        hero = f"""<section class="hero"><div class="wrap"><div class="txt"><span class="tag">قريباً</span>{T("منصة سورية للإعلام الحر", "h1")}<p>{E(BIO)}</p></div></div></section>"""
    used = {lead["id"]} if lead else set()
    reels = [p for p in videos if p["id"] not in used][:8]
    nums = [p for p in posts if p["series"] == "numbers"][:3]
    used |= {p["id"] for p in reels + nums}
    others = [p for p in posts if p["id"] not in used and p["kind"] in ("إنفوغراف", "ألبوم", "منشور")][:8]
    body = hero + sched()
    if reels:
        body += (f'<section class="blk"><div class="wrap"><div class="head">{T("أحدث الريلز", "h2")}'
                 f'<a href="/all/#ريل">عرض الكل</a></div><div class="grid">{"".join(card(p) for p in reels)}</div></div></section>')
    fbtl = ("https://www.facebook.com/plugins/page.php?" + urllib.parse.urlencode({
        "href": FB_PAGE, "tabs": "timeline", "width": "500", "height": "720", "small_header": "true",
        "adapt_container_width": "true", "hide_cover": "false", "show_facepile": "false", "locale": "ar_AR"}))
    body += (f'<section class="blk"><div class="wrap"><div class="head">{T("آخر منشوراتنا على فيسبوك", "h2")}'
             f'<a href="{FB_PAGE}" rel="noopener" target="_blank">افتح الصفحة</a></div>'
             f'<div class="fbtl"><iframe src="{E(fbtl)}" title="آخر منشورات دامسكا بلس على فيسبوك" loading="lazy"></iframe></div></div></section>')
    if nums:
        body += (f'<section class="blk"><div class="wrap"><div class="head">{T("سوريا بالأرقام", "h2")}'
                 f'<a href="/syria-in-numbers/">كل الإنفوغرافات</a></div><div class="grid sq">{"".join(card(p, False) for p in nums)}</div></div></section>')
    if others:
        body += (f'<section class="blk"><div class="wrap"><div class="head">{T("منشورات أخرى", "h2")}'
                 f'<a href="/all/">كل المنشورات</a></div><div class="grid sq">{"".join(card(p) for p in others)}</div></div></section>')
    body += about_strip()
    page("/", "دامسكا بلس", body, current="/", image=(lead or {}).get("thumb"))

def post_page(p, posts):
    s = SERIES.get(p["series"]) if p["series"] else None
    meta = (f'<a class="tag" href="/{s["slug"]}/">{E(s["name"])}</a><span>{"كل " + s["day"] if s["day"] != "دوري" else "إنفوغرافات"} · ' if s
            else f'<span class="tag">{E(p["kind"])}</span><span>') + (f'<time datetime="{p["dt"].isoformat()}">{p["date"]}</time>' if p["dt"] else "دامسكا بلس") + '</span>'
    if p["kind"] in ("ريل", "فيديو") and p.get("video_url"):
        src = ("https://www.facebook.com/plugins/video.php?" +
               urllib.parse.urlencode({"href": p["video_url"], "show_text": "false", "width": "420" if p["kind"] == "ريل" else "760"}))
        media = (f'<div class="player{" wide" if p["kind"] == "فيديو" else ""}"><iframe src="{E(src)}" title="{E(p["title"])}" loading="lazy" '
                 f'allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share" allowfullscreen></iframe></div>')
    elif not p.get("images") and p.get("permalink") and "facebook.com" in p["permalink"]:
        src = "https://www.facebook.com/plugins/post.php?" + urllib.parse.urlencode({"href": p["permalink"], "show_text": "true", "width": "500"})
        media = (f'<div class="fbtl"><iframe src="{E(src)}" title="{E(p["title"])}" loading="lazy" style="height:760px" '
                 f'allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share"></iframe></div>')
    elif len(p.get("images") or []) > 1:
        media = '<div class="gallery">' + "".join(
            f'<img src="/media/{i}" alt="صورة {n + 1} من {len(p["images"])}" loading="lazy">' for n, i in enumerate(p["images"])) + "</div>"
    elif p["thumb"]:
        media = f'<div class="single"><a href="{p["thumb"]}"><img src="{p["thumb"]}" alt="{E(p["title"])}"></a></div>'
    else:
        media = ""
    body_txt = "".join(f"<p>{E(l)}</p>" for l in p["body"])
    url = SITE + p["href"]
    q = urllib.parse.quote
    share = (f'<div class="share" aria-label="شارك"><a href="https://wa.me/?text={q(p["title"] + " " + url)}" rel="noopener" target="_blank">واتساب</a>'
             f'<a href="https://www.facebook.com/sharer/sharer.php?u={q(url)}" rel="noopener" target="_blank">فيسبوك</a>'
             f'<a href="https://t.me/share/url?url={q(url)}&amp;text={q(p["title"])}" rel="noopener" target="_blank">تلغرام</a>'
             f'<button type="button" onclick="navigator.clipboard&&navigator.clipboard.writeText(\'{url}\');this.textContent=\'تم النسخ\'">نسخ الرابط</button></div>')
    cta = f'<a class="btn y cta" href="{E(p["permalink"])}" rel="noopener" target="_blank">علّق على فيسبوك</a>' if p.get("permalink") else ""
    rel = [x for x in posts if x["id"] != p["id"] and (x["series"] == p["series"] if p["series"] else True)][:4]
    rel_title = f"حلقات أخرى من {s['name']}" if s else "منشورات أخرى"
    rel_html = (f'<section class="blk"><div class="wrap"><div class="head">{T(rel_title, "h2")}</div>'
                f'<div class="grid">{"".join(card(x) for x in rel)}</div></div></section>') if rel else ""
    body = (f'<article class="post"><div class="wrap"><div class="meta">{meta}</div>{T(p["title"], "h1")}{media}'
            f'<div class="body">{body_txt}</div>{share}{cta}</div></article>{sched()}{rel_html}<div style="height:40px"></div>')
    desc = " ".join([p["title"]] + p["body"])[:200]
    page(p["href"], p["title"], body, desc=desc, image=p["thumb"])

def series_page(key, posts):
    s = SERIES[key]
    eps = [p for p in posts if p["series"] == key]
    grid = (f'<div class="grid{" sq" if key == "numbers" else ""}">{"".join(card(p, False) for p in eps)}</div>' if eps
            else '<p class="empty">ستظهر الحلقات هنا فور نشرها.</p>')
    body = (f'<section class="blk"><div class="wrap"><span class="tag">{"كل " + s["day"] if s["day"] != "دوري" else "إنفوغرافات"}</span>'
            f'<div class="head" style="margin-top:12px">{T(s["name"], "h1")}</div><p class="lead">{E(s["about"])}</p>{grid}</div></section>'
            f'<div style="height:48px"></div>')
    page(f"/{s['slug']}/", s["name"], body, desc=s["about"], current=f"/{s['slug']}/",
         image=eps[0]["thumb"] if eps else None)

def all_page(posts):
    kinds = [k for k in ("ريل", "فيديو", "إنفوغراف", "ألبوم", "منشور") if any(p["kind"] == k for p in posts)]
    btns = '<button type="button" aria-pressed="true" data-k="">الكل</button>' + "".join(
        f'<button type="button" aria-pressed="false" data-k="{k}">{k}</button>' for k in kinds)
    cards = "".join(card(p).replace('<a class="card"', f'<a class="card" data-k="{p["kind"]}"', 1) for p in posts)
    script = """<script>
(function(){var b=document.querySelectorAll('.filters button'),c=document.querySelectorAll('.card[data-k]');
function set(k){b.forEach(function(x){x.setAttribute('aria-pressed',x.dataset.k===k)});c.forEach(function(x){x.hidden=!!k&&x.dataset.k!==k});}
b.forEach(function(x){x.addEventListener('click',function(){set(x.dataset.k);history.replaceState(null,'',x.dataset.k?'#'+x.dataset.k:location.pathname)})});
var h=decodeURIComponent(location.hash.slice(1));if(h)set(h);})();
</script>"""
    body = (f'<section class="blk"><div class="wrap"><div class="head">{T("كل المنشورات", "h1")}'
            f'<div class="filters" role="group" aria-label="تصفية حسب الشكل">{btns}</div></div>'
            f'<div class="grid sq">{cards or ""}</div>{"" if posts else "<p class=empty>ستظهر المنشورات هنا قريباً.</p>"}</div></section>{script}<div style="height:48px"></div>')
    page("/all/", "كل المنشورات", body, current="/all/")

def about_page():
    soc = "".join(f'<li><a href="{u}" rel="noopener" target="_blank">{t}</a></li>' for t, u in SOCIAL)
    body = f"""<section class="blk"><div class="wrap prose">{T("من نحن", "h1")}
<p>دامسكا بلس {E(BIO)}</p>
<h2>محطات</h2>
<p>{E(LICENSE_LINE)}. حصلت المنصة أولاً على الترخيص المؤقت، ثم على الترخيص الدائم.</p>
<h2>برامجنا</h2>
<ul>{"".join(f'<li><a href="/{s["slug"]}/">{E(s["name"])}</a>: {E(s["about"])}</li>' for s in SERIES.values())}</ul>
<h2>تواصل معنا</h2>
<p>البريد: <a href="mailto:damaskaplus@gmail.com" dir="ltr">damaskaplus@gmail.com</a></p>
<ul>{soc}</ul>
</div></section><div style="height:48px"></div>"""
    page("/about/", "من نحن", body, current="/about/")

def feeds(posts):
    now = dt.datetime.now(dt.timezone.utc)
    items = "".join(
        f"<item><title>{E(p['title'])}</title><link>{SITE}{p['href']}</link><guid>{SITE}{p['href']}</guid>"
        + (f"<pubDate>{p['dt'].astimezone(dt.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>" if p['dt'] else "") +
        f"<description>{E(' '.join(p['body'])[:400])}</description></item>" for p in posts[:30])
    rss = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>دامسكا بلس</title>'
           f'<link>{SITE}/</link><description>{E(BIO)}</description><language>ar</language>'
           f'<lastBuildDate>{now.strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>{items}</channel></rss>')
    open(os.path.join(OUT, "rss.xml"), "w", encoding="utf-8").write(rss)
    urls = ["/", "/all/", "/about/"] + [f"/{s['slug']}/" for s in SERIES.values()] + [p["href"] for p in posts]
    sm = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + \
         "".join(f"<url><loc>{SITE}{u}</loc></url>" for u in urls) + "</urlset>"
    open(os.path.join(OUT, "sitemap.xml"), "w", encoding="utf-8").write(sm)
    open(os.path.join(OUT, "robots.txt"), "w").write(f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")

# ---------------------------------------------------------------- main

def main():
    global OT
    os.makedirs(DATA, exist_ok=True)
    data_path = os.path.join(DATA, "posts.json")
    items = json.load(open(data_path, encoding="utf-8")) if os.path.exists(data_path) else {}
    fetched = False
    if os.environ.get("FB_PAGE_TOKEN"):
        try:
            new = fetch(items)
            for k, v in new.items():
                if k in items:
                    v["images"] = items[k].get("images", [])
                items[k] = v
            fetched = True
        except Exception as ex:
            body = getattr(ex, "read", lambda: b"")().decode("utf-8", "ignore")[:500]
            print("::warning::Facebook fetch failed:", ex, body)
        download_images(items)
        json.dump(items, open(data_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    if not fetched and not items:
        items = from_links()
    ov_path = os.path.join(DATA, "overrides.json")
    overrides = json.load(open(ov_path, encoding="utf-8")) if os.path.exists(ov_path) else {}
    overrides = {k: v for k, v in overrides.items() if not k.startswith("_")}
    posts = prepare(items, overrides)
    print(f"posts: {len(posts)} (fetched now: {fetched})")

    OT = Outliner()
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    for f in ("logo.svg", "logo.png", "favicon.svg", "CNAME"):
        if os.path.exists(os.path.join(ROOT, f)):
            shutil.copy(os.path.join(ROOT, f), OUT)
    if os.path.isdir(MEDIA):
        shutil.copytree(MEDIA, os.path.join(OUT, "media"))
    open(os.path.join(OUT, "style.css"), "w").write(CSS)
    home(posts)
    for p in posts:
        post_page(p, posts)
    for key in SERIES:
        series_page(key, posts)
    all_page(posts)
    about_page()
    page("/404.html", "الصفحة غير موجودة",
         f'<section class="hero"><div class="wrap"><div class="txt">{T("الصفحة غير موجودة", "h1")}<a class="btn y" href="/">إلى الرئيسية</a></div></div></section>')
    feeds(posts)
    ready = bool(posts)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"ready={'true' if ready else 'false'}\n")
    print("built", OUT, "ready" if ready else "(no posts yet: deploy skipped)")

if __name__ == "__main__":
    main()
