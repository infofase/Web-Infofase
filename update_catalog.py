#!/usr/bin/env python3
"""
update_catalog.py — Infofase auto-catalog updater v3
- Descarga CSV de Megastore
- Actualiza tienda general (2900+ productos) con imágenes de Icecat
- Actualiza Zona Apple (593 productos)
- Publica en GitHub Pages via git push automático

Secretos necesarios en GitHub:
  ICECAT_USER — usuario de icecat.biz
  ICECAT_PASS — contraseña de icecat.biz
"""
import csv, json, base64, re, os, sys, time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from datetime import datetime

CSV_URL   = "https://infofase.com/tienda/megastore.csv"
TEMPLATE  = "template.html" if os.path.exists("template.html") else "index.html"
OUTPUT    = "index.html"
LOG       = "update.log"
IMG_CACHE = "img_cache.json"   # cache de icecat_id ya resueltos
IGIC      = 0.07

# Credenciales de Icecat — vienen de GitHub Secrets (variables de entorno)
ICECAT_USER = os.environ.get('ICECAT_USER', '')
ICECAT_PASS = os.environ.get('ICECAT_PASS', '')

_csv_rows = []

# ── Logging ───────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── CSV download ──────────────────────────────────────────────
def download_csv():
    log(f"Descargando {CSV_URL}")
    req = Request(CSV_URL, headers={"User-Agent": "Infofase-Bot/3.0"})
    try:
        with urlopen(req, timeout=30) as r:
            raw = r.read()
        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                text = raw.decode(enc)
                log(f"  CSV: {len(raw)//1024}KB ({enc})")
                return text
            except UnicodeDecodeError:
                continue
    except URLError as e:
        log(f"  ERROR descargando CSV: {e}")
    return None

# ── Categorización ────────────────────────────────────────────
PRODUCTO_CAT = {
    'tinta':'consumibles','toner':'consumibles','tambor':'consumibles',
    'cinta':'consumibles','etiquetas':'consumibles','papeleria':'consumibles',
    'impresora':'impresoras','imp multif':'impresoras','imp profes':'impresoras',
    'iphone':'smartphones','ipad':'tablets','ipad mini':'tablets',
    'macbook':'portatiles','imac':'componentes','watch':'wearables',
    'airtag':'perifericos','applecare':'software',
    'portatil':'portatiles','pc':'componentes','cpu':'componentes',
    'componente':'componentes','chasis':'componentes','fuente':'componentes',
    'ventilador':'componentes','memoria':'componentes',
    'ssd int1':'disco_interno','disco int6':'disco_interno','disco int6+':'disco_interno',
    'raton':'perifericos','teclado':'perifericos','monitor':'monitores',
    'soporte':'perifericos','adaptador':'cables','cable':'cables',
    'latiguillo':'cable_red','vga':'cables','cargador':'cables','wireless':'perifericos',
    'auricular':'audio','altavoz':'audio','camara vid':'camaras','televisor':'monitores',
    'switch':'redes','router':'redes','wifi':'redes','nas':'nas',
    'power bank':'powerbank','funda':'fundas','maletin':'fundas',
    'mochila':'fundas','accesorio':'perifericos',
    'ups':'sai_ups','tpv':'tpv','scanner cb':'escaneres','scann pro':'escaneres',
    'tablet':'tablets','smartphone':'smartphones','regleta':'sai_ups',
    'destructor':None,'imp etique':None,
}

def categorize(producto):
    p = (producto or "").strip().lower()
    if p in PRODUCTO_CAT:
        return PRODUCTO_CAT[p]
    for key, cat in PRODUCTO_CAT.items():
        if cat and (key in p or p in key):
            return cat
    return None

def calc_price(pvp_s, dto_s, canon_s="0"):
    try:
        pvp   = float(str(pvp_s).replace(",",".").replace("€","").strip() or 0)
        dto   = float(str(dto_s).replace(",",".").replace("%","").strip() or 0)
        canon = float(str(canon_s).replace(",",".").replace("€","").strip() or 0)
        if pvp <= 0: return None, 0
        net = pvp * (1 - dto/100)
        return round(net * (1 + IGIC), 2), round(canon * (1 + IGIC), 2)
    except:
        return None, 0

def stock_status(stock_val, viajando_val="0"):
    try:
        qty      = int(str(stock_val).strip() or 0)
        viajando = int(str(viajando_val).strip() or 0)
    except:
        qty, viajando = 0, 0
    if qty > 0:             return "stock"
    if viajando > 0 or qty < 0: return "transito"
    return "agotado"

def extract_attrs(name):
    a = {}
    n = name.lower()
    m = re.search(r'(\d+)\s*gb\s+(?:ram|ddr)', n)
    if not m: m = re.search(r'(\d+)\s*gb\s+ram', n)
    if m: a["ram"] = m.group(1) + "GB"
    m = re.search(r'(\d+)\s*(tb|gb)\s+(?:ssd|hdd|nvme|emmc)', n)
    if m: a["storage"] = m.group(1) + m.group(2).upper()
    m = re.search(r'(\d{1,2}[.,]\d)\s*["\']', n)
    if m: a["screen"] = m.group(1).replace(",", ".") + '"'
    m = re.search(
        r'(intel\s+(?:core\s+)?(?:i[3579]|ultra\s*[579])[- ]\d+\w*'
        r'|ryzen\s+[3579][\s\d\w]*'
        r'|apple\s+m[1-4](?:\s+(?:pro|max|ultra))?)', n, re.I)
    if m: a["chip"] = m.group(1).strip().title()
    return a or None

# ── Icecat image lookup ───────────────────────────────────────
def load_img_cache():
    """Carga el cache de imágenes ya resueltas para no repetir llamadas a Icecat."""
    if os.path.exists(IMG_CACHE):
        try:
            with open(IMG_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_img_cache(cache):
    with open(IMG_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

def get_icecat_img(brand, product_code, cache):
    """Consulta Icecat Live y guarda thumb, high, gallery, desc, specs."""
    if not ICECAT_USER:
        return None, None

    cache_key = f"{brand.lower()}|{product_code.lower()}"
    if cache_key in cache:
        cached = cache[cache_key]
        if cached is None: return None, None
        return cached.get('thumb'), cached.get('high')

    from urllib.parse import quote
    brand_enc = quote(brand.strip().title())
    code_enc  = quote(product_code.strip())
    url = (f"https://live.icecat.biz/api/"
           f"?UserName={ICECAT_USER}"
           f"&Language=EN"
           f"&Brand={brand_enc}"
           f"&ProductCode={code_enc}")
    try:
        req = Request(url, headers={"User-Agent": "Infofase-Bot/3.0"})
        with urlopen(req, timeout=10) as r:
            raw  = r.read().decode("utf-8", errors="replace")
            data = json.loads(raw)

        if not hasattr(get_icecat_img, '_logged'):
            log(f"  DEBUG keys: {list(data.keys())}")
            get_icecat_img._logged = True

        # Navigate response — try data.Image, then msg.Image
        root = data.get("data") or data.get("msg") or {}
        if isinstance(root, str): root = {}

        # Main image
        img_node = root.get("Image") or {}
        thumb = img_node.get("ThumbPic","") or img_node.get("Pic75x75","")
        high  = img_node.get("HighPic", "") or img_node.get("Pic500x500","")

        # Gallery
        gallery_raw = root.get("Gallery") or []
        gallery = []
        for g in gallery_raw[:6]:  # max 6 gallery images
            u = g.get("HighPic","") or g.get("LowPic","")
            if u and u != high: gallery.append(u)

        # Short description
        desc = ""
        gi = root.get("GeneralInfo") or {}
        desc_node = gi.get("Description") or {}
        if isinstance(desc_node, dict):
            desc = desc_node.get("ShortDesc","") or ""
        if not desc:
            desc = gi.get("GeneratedIntTitle",{}).get("Value","") if isinstance(gi.get("GeneratedIntTitle"),dict) else ""

        # Specs (FeaturesGroups → top 20 features)
        specs = []
        for fg in (root.get("FeaturesGroups") or []):
            for feat in (fg.get("Features") or [])[:20]:
                fname = (feat.get("Feature") or {}).get("Name","")
                fval  = feat.get("Value","") or feat.get("LocalValue","")
                funit = (feat.get("Feature") or {}).get("Measure",{})
                if isinstance(funit, dict): funit = funit.get("Signs",{}).get("_","") 
                else: funit = ""
                if fname and fval:
                    specs.append({"n": fname, "v": str(fval) + (" "+funit if funit else "")})
            if len(specs) >= 20: break

        if thumb or high:
            entry = {"thumb": thumb, "high": high}
            if gallery: entry["gallery"] = gallery
            if desc:    entry["desc"]    = desc[:300]
            if specs:   entry["specs"]   = specs[:20]
            cache[cache_key] = entry
            return thumb, high
        else:
            cache[cache_key] = None
            return None, None

    except HTTPError as e:
        if e.code in (404, 403): cache[cache_key] = None
        return None, None
    except Exception as ex:
        if not hasattr(get_icecat_img, '_err_logged'):
            log(f"  DEBUG error: {ex}")
            get_icecat_img._err_logged = True
        return None, None


def process_csv(text, img_cache):
    lines = text.splitlines()
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(lines, delimiter=delim)

    products   = []
    skipped    = 0
    img_found  = 0
    img_miss   = 0
    img_skip   = 0  # Apple products — skip (have their own images)

    # Rate limit: Icecat free allows ~50-70 req/sec, we go conservative
    # Only fetch NEW images not in cache
    new_lookups = 0
    MAX_NEW_PER_RUN = 500  # fetch max 500 new images per run to stay within limits

    for row in reader:
        pid   = row.get('codigo','').strip()
        name  = row.get('denominacion','').strip()
        brand = row.get('marca','').strip()
        if not pid or not name: continue

        cat = categorize(row.get('producto',''))
        if not cat: skipped += 1; continue

        price, canon_v = calc_price(
            row.get('precio','0'), row.get('dto','0'), row.get('canon','0'))
        if not price: continue

        st   = stock_status(row.get('stock','0'), row.get('viajando','0'))
        prod = row.get('producto','').strip()

        p = {"id":pid, "n":name, "p":price, "cat":cat,
             "s":prod, "b":brand, "st":st}
        if canon_v > 0: p["c"] = canon_v
        a = extract_attrs(name)
        if a: p["a"] = a

        # Get Icecat image — skip Apple (already handled in ZA)
        if brand.lower() == 'apple':
            img_skip += 1
        else:
            cache_key = f"{brand.lower()}|{pid.lower()}"
            in_cache  = cache_key in img_cache

            if not in_cache and new_lookups < MAX_NEW_PER_RUN:
                thumb, high = get_icecat_img(brand, pid, img_cache)
                new_lookups += 1
                # Small delay to be respectful of rate limits
                if new_lookups % 50 == 0:
                    time.sleep(0.5)
            elif in_cache:
                cached = img_cache.get(cache_key)
                thumb  = cached.get('thumb') if cached else None
                high   = cached.get('high')  if cached else None
            else:
                thumb, high = None, None

            if thumb or high:
                p["img"]  = thumb or high
                if high:  p["imgH"] = high
                # Extended Icecat data
                cached_entry = img_cache.get(cache_key, {}) or {}
                if cached_entry.get("gallery"): p["gallery"] = cached_entry["gallery"]
                if cached_entry.get("desc"):    p["desc"]    = cached_entry["desc"]
                if cached_entry.get("specs"):   p["specs"]   = cached_entry["specs"]
                img_found += 1
            else:
                img_miss += 1

        products.append(p)

    log(f"  Procesados: {len(products)} | descartados: {skipped}")
    log(f"  Imágenes: {img_found} encontradas | {img_miss} sin imagen | {img_skip} Apple (skip)")
    log(f"  Nuevas consultas Icecat: {new_lookups}")
    return products

# ── ASCII encode helper ───────────────────────────────────────
def ascii_encode(html_str):
    out = []
    for ch in html_str:
        if ord(ch) > 127:
            out.append(f'&#{ord(ch)};')
        else:
            out.append(ch)
    return ''.join(out)

# ── Update Zona Apple ─────────────────────────────────────────
def update_zona_apple(html, csv_rows):
    csv_by_id = {r.get('codigo','').strip().lower(): r for r in csv_rows}
    za_m = re.search(r'(var _ZA\s*=\s*")([A-Za-z0-9+/=]+)(")', html)
    if not za_m:
        log("  ZA: _ZA no encontrado, saltando"); return html, 0
    za_html = base64.b64decode(za_m.group(2)).decode('ascii', errors='replace')
    all_start = za_html.find('let ALL    = [')
    if all_start < 0: all_start = za_html.find('let ALL = [')
    if all_start < 0:
        log("  ZA: let ALL no encontrado"); return html, 0
    bracket_pos = za_html.index('[', all_start)
    all_end     = za_html.find('];\n', bracket_pos)
    prefix      = za_html[:bracket_pos]
    suffix      = za_html[all_end + 1:]
    try:
        za_products = json.loads(za_html[bracket_pos:all_end + 1])
    except Exception as e:
        log(f"  ZA: error parseando: {e}"); return html, 0
    updated = 0
    for p in za_products:
        pid = p.get('id','').strip().lower()
        row = csv_by_id.get(pid)
        if not row: continue
        price, canon_v = calc_price(
            row.get('precio','0'), row.get('dto','0'), row.get('canon','0'))
        if price and price > 0:
            p['price'] = price; p['canon'] = canon_v
        try:
            qty      = int(row.get('stock','0').strip() or 0)
            viajando = int(row.get('viajando','0').strip() or 0)
        except:
            qty, viajando = 0, 0
        p['stock']   = qty; p['transit'] = viajando
        p['status']  = ('stock' if qty > 0 else
                        'transit' if viajando > 0 or qty < 0 else 'agotado')
        updated += 1
    log(f"  ZA: {updated}/{len(za_products)} productos Apple actualizados")
    new_all     = json.dumps(za_products, ensure_ascii=False, separators=(',',':'))
    new_za_html = prefix + new_all + suffix
    new_za_b64  = base64.b64encode(
        ascii_encode(new_za_html).encode('ascii')).decode('ascii')
    return html[:za_m.start(2)] + new_za_b64 + html[za_m.end(2):], updated

# ── Update HTML ───────────────────────────────────────────────
def update_html(products):
    if not os.path.exists(TEMPLATE):
        log(f"ERROR: {TEMPLATE} no encontrado"); return False
    with open(TEMPLATE, encoding="utf-8", errors="replace") as f:
        html = f.read()

    new_prods_b64 = base64.b64encode(
        json.dumps(products, ensure_ascii=False,
                   separators=(",",":")).encode("utf-8")
    ).decode("ascii")

    # Strategy A: standalone tienda
    pat_direct = r'(var ALL = JSON\.parse\((?:new TextDecoder\(\)\.decode\(Uint8Array\.from\(atob\(|atob\()")[A-Za-z0-9+/=]+'
    html2, n = re.subn(
        pat_direct,
        lambda m: m.group(1) + new_prods_b64,
        html, count=1)

    if n > 0:
        log("  Modo: standalone tienda"); za_updated = 0
    else:
        # Strategy B: integrated site — products inside _TG
        tg_m = re.search(r"(var _TG = ')([A-Za-z0-9+/=]+)(')", html)
        if not tg_m:
            log("ERROR: no se encontró _TG ni var ALL"); return False
        log("  Modo: web integrada (_TG)")
        tg_html = base64.b64decode(tg_m.group(2)).decode('ascii', errors='replace')
        pat_inner = r'(JSON\.parse\((?:new TextDecoder\(\)\.decode\(Uint8Array\.from\(atob\(|atob\()")[A-Za-z0-9+/=]+'
        tg_new, n2 = re.subn(
            pat_inner,
            lambda m: m.group(1) + new_prods_b64,
            tg_html, count=1)
        if n2 == 0:
            log("ERROR: patrón no encontrado dentro de _TG"); return False
        new_tg_b64 = base64.b64encode(
            ascii_encode(tg_new).encode('ascii')).decode('ascii')
        html2 = html[:tg_m.start(2)] + new_tg_b64 + html[tg_m.end(2):]
        html2, za_updated = update_zona_apple(html2, _csv_rows)

    count = len(products)
    html2 = re.sub(r'\d[\d.,]* productos',
                   f'{count:,} productos'.replace(",", "."), html2, count=3)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html2)
    size_mb = os.path.getsize(OUTPUT) / 1024 / 1024
    log(f"  {OUTPUT}: {size_mb:.1f}MB | tienda: {count} | Apple: {za_updated}")
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    global _csv_rows
    log("=" * 50)
    log("Infofase catalog updater v3 — con imágenes Icecat")

    if not ICECAT_USER:
        log("  AVISO: ICECAT_USER no configurado — se omitirán imágenes")

    # Load image cache
    img_cache = load_img_cache()
    # Purge null entries so failed lookups are retried
    null_keys = [k for k, v in img_cache.items() if v is None]
    for k in null_keys:
        del img_cache[k]
    log(f"  Cache: {len(img_cache)} entradas válidas ({len(null_keys)} nulos purgados)")

    text = download_csv()
    if not text: sys.exit(1)

    _csv_rows = list(csv.DictReader(text.splitlines(), delimiter=";"))
    products  = process_csv(text, img_cache)
    if not products: sys.exit(1)

    # Save updated cache
    save_img_cache(img_cache)
    log(f"  Cache guardado: {len(img_cache)} entradas")

    if not update_html(products): sys.exit(1)

    log("Completado OK")
    log("=" * 50)

if __name__ == "__main__":
    main()
