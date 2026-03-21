#!/usr/bin/env python3
"""
update_catalog.py — Infofase auto-catalog updater
Actualiza tienda general (2900+ productos) y Zona Apple (593) desde el CSV de Megastore.
CSV columns: codigo, denominacion, precio, marca, producto, stock, viajando, dto, canon
"""
import csv, json, base64, re, os, sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime

CSV_URL  = "https://infofase.com/tienda/megastore.csv"
TEMPLATE = "template.html"
OUTPUT   = "index.html"
LOG      = "update.log"
IGIC     = 0.07
_csv_rows = []  # set in main(), shared with update_zona_apple

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def download_csv():
    log(f"Descargando {CSV_URL}")
    req = Request(CSV_URL, headers={"User-Agent": "Infofase-Bot/2.0"})
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
        log(f"  ERROR: {e}")
    return None

# ── Mapeo columna 'producto' → categoría ─────────────────────
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

def process_csv(text):
    lines = text.splitlines()
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(lines, delimiter=delim)
    products, skipped = [], 0
    for row in reader:
        pid  = row.get('codigo','').strip()
        name = row.get('denominacion','').strip()
        if not pid or not name: continue
        cat = categorize(row.get('producto',''))
        if not cat: skipped += 1; continue
        price, canon_v = calc_price(
            row.get('precio','0'), row.get('dto','0'), row.get('canon','0'))
        if not price: continue
        st    = stock_status(row.get('stock','0'), row.get('viajando','0'))
        brand = row.get('marca','').strip()
        prod  = row.get('producto','').strip()
        p = {"id":pid,"n":name,"p":price,"cat":cat,"s":prod,"b":brand,"st":st}
        if canon_v > 0: p["c"] = canon_v
        a = extract_attrs(name)
        if a: p["a"] = a
        products.append(p)
    log(f"  Procesados: {len(products)}, descartados: {skipped}")
    return products

def ascii_encode(html_str):
    out = []
    for ch in html_str:
        if ord(ch) > 127:
            out.append(f'&#{ord(ch)};')
        else:
            out.append(ch)
    return ''.join(out)

def update_zona_apple(html, csv_rows):
    """Actualiza precios y stock de los 593 productos Apple dentro de _ZA."""
    csv_by_id = {r.get('codigo','').strip().lower(): r for r in csv_rows}

    za_m = re.search(r'(var _ZA\s*=\s*")([A-Za-z0-9+/=]+)(")', html)
    if not za_m:
        log("  ZA: _ZA no encontrado, saltando")
        return html, 0

    za_html = base64.b64decode(za_m.group(2)).decode('ascii', errors='replace')

    # Find let ALL = [...] inside ZA
    all_start = za_html.find('let ALL    = [')
    if all_start < 0:
        all_start = za_html.find('let ALL = [')
    if all_start < 0:
        log("  ZA: let ALL no encontrado")
        return html, 0

    bracket_pos = za_html.index('[', all_start)
    all_end     = za_html.find('];\n', bracket_pos)
    prefix      = za_html[:bracket_pos]
    suffix      = za_html[all_end + 1:]  # keeps ;\n

    try:
        za_products = json.loads(za_html[bracket_pos:all_end + 1])
    except Exception as e:
        log(f"  ZA: error parseando: {e}")
        return html, 0

    updated = 0
    for p in za_products:
        pid = p.get('id','').strip().lower()
        row = csv_by_id.get(pid)
        if not row:
            continue
        price, canon_v = calc_price(
            row.get('precio','0'), row.get('dto','0'), row.get('canon','0'))
        if price and price > 0:
            p['price'] = price
            p['canon'] = canon_v
        try:
            qty      = int(row.get('stock','0').strip() or 0)
            viajando = int(row.get('viajando','0').strip() or 0)
        except:
            qty, viajando = 0, 0
        p['stock']   = qty
        p['transit'] = viajando
        p['status']  = 'stock' if qty > 0 else ('transit' if viajando > 0 or qty < 0 else 'agotado')
        updated += 1

    log(f"  ZA: {updated}/{len(za_products)} productos Apple actualizados")

    new_all    = json.dumps(za_products, ensure_ascii=False, separators=(',',':'))
    new_za_html = prefix + new_all + suffix
    new_za_b64  = base64.b64encode(
        ascii_encode(new_za_html).encode('ascii')
    ).decode('ascii')

    return html[:za_m.start(2)] + new_za_b64 + html[za_m.end(2):], updated

def update_html(products):
    if not os.path.exists(TEMPLATE):
        log(f"ERROR: {TEMPLATE} no encontrado"); return False

    with open(TEMPLATE, encoding="utf-8", errors="replace") as f:
        html = f.read()

    new_prods_b64 = base64.b64encode(
        json.dumps(products, ensure_ascii=False,
                   separators=(",",":")).encode("utf-8")
    ).decode("ascii")

    # Strategy A: standalone tienda (var ALL = ...)
    pat_direct = r'(var ALL = JSON\.parse\((?:new TextDecoder\(\)\.decode\(Uint8Array\.from\(atob\(|atob\()")[A-Za-z0-9+/=]+'
    html2, n = re.subn(
        pat_direct,
        lambda m: m.group(1) + '"' + new_prods_b64,
        html, count=1)

    if n > 0:
        log("  Modo: standalone tienda")
        za_updated = 0
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
            lambda m: m.group(1) + '"' + new_prods_b64,
            tg_html, count=1)

        if n2 == 0:
            log("ERROR: patrón no encontrado dentro de _TG"); return False

        new_tg_b64 = base64.b64encode(
            ascii_encode(tg_new).encode('ascii')
        ).decode('ascii')
        html2 = html[:tg_m.start(2)] + new_tg_b64 + html[tg_m.end(2):]

        # Update Zona Apple
        html2, za_updated = update_zona_apple(html2, _csv_rows)

    # Update product count label
    count = len(products)
    html2 = re.sub(
        r'\d[\d.,]* productos',
        f'{count:,} productos'.replace(",", "."),
        html2, count=3)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html2)

    size_mb = os.path.getsize(OUTPUT) / 1024 / 1024
    log(f"  {OUTPUT}: {size_mb:.1f}MB | tienda: {count} | Apple: {za_updated}")
    return True

def main():
    global _csv_rows
    log("=" * 50)
    log("Infofase catalog updater")
    text = download_csv()
    if not text: sys.exit(1)
    _csv_rows = list(csv.DictReader(text.splitlines(), delimiter=";"))
    products  = process_csv(text)
    if not products: sys.exit(1)
    if not update_html(products): sys.exit(1)
    log("Completado OK")
    log("=" * 50)

if __name__ == "__main__":
    main()
