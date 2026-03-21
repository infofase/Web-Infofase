#!/usr/bin/env python3
"""
update_catalog.py — Infofase auto-catalog updater
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

# ── Mapeo columna 'producto' → categoría interna ──────────────
PRODUCTO_CAT = {
    # Consumibles
    'tinta':       'consumibles',
    'toner':       'consumibles',
    'tambor':      'consumibles',
    'cinta':       'consumibles',
    'etiquetas':   'consumibles',
    'papeleria':   'consumibles',
    # Impresoras
    'impresora':   'impresoras',
    'imp multif':  'impresoras',
    'imp profes':  'impresoras',
    'imp etique':  'impresoras',
    # Apple dispositivos
    'iphone':      'smartphones',
    'ipad':        'tablets',
    'ipad mini':   'tablets',
    'macbook':     'portatiles',
    'imac':        'componentes',
    'watch':       'wearables',
    'airtag':      'perifericos',
    'applecare':   'software',
    # PC / portátiles
    'portatil':    'portatiles',
    'pc':          'componentes',
    'cpu':         'componentes',
    'componente':  'componentes',
    'chasis':      'componentes',
    'fuente':      'componentes',
    'ventilador':  'componentes',
    'memoria':     'componentes',
    'ssd int1':    'disco_interno',
    'disco int6':  'disco_interno',
    'disco int6+': 'disco_interno',
    # Periféricos
    'raton':       'perifericos',
    'teclado':     'perifericos',
    'monitor':     'monitores',
    'soporte':     'perifericos',
    'adaptador':   'cables',
    'cable':       'cables',
    'latiguillo':  'cable_red',
    'vga':         'cables',
    'cargador':    'cables',
    'wireless':    'perifericos',
    # Audio/vídeo
    'auricular':   'audio',
    'altavoz':     'audio',
    'camara vid':  'camaras',
    'televisor':   'monitores',
    # Redes
    'switch':      'redes',
    'router':      'redes',
    'wifi':        'redes',
    'nas':         'nas',
    # Almacenamiento
    'power bank':  'powerbank',
    # Accesorios / fundas
    'funda':       'fundas',
    'maletin':     'fundas',
    'mochila':     'fundas',
    'accesorio':   'perifericos',
    # Otros
    'ups':         'sai_ups',
    'tpv':         'tpv',
    'scanner cb':  'escaneres',
    'scann pro':   'escaneres',
    'tablet':      'tablets',
    'smartphone':  'smartphones',
    'regleta':     'sai_ups',
    # Excluidos
    'destructor':  None,
    'imp etique':  None,
}

EXCLUDED = {'destructor', 'imp etique'}

def categorize(producto):
    p = (producto or "").strip().lower()
    if p in EXCLUDED:
        return None
    if p in PRODUCTO_CAT:
        return PRODUCTO_CAT[p]
    # Partial match fallback
    for key, cat in PRODUCTO_CAT.items():
        if key in p or p in key:
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
    if qty > 0:
        return "stock"
    elif viajando > 0 or qty < 0:
        return "transito"
    else:
        return "agotado"

def extract_attrs(name, producto):
    a = {}
    n = name.lower()
    # RAM
    m = re.search(r'(\d+)\s*gb\s+(?:ram|ddr)', n)
    if not m: m = re.search(r'(\d+)\s*gb\s+ram', n)
    if m: a["ram"] = m.group(1) + "GB"
    # Storage
    m = re.search(r'(\d+)\s*(tb|gb)\s+(?:ssd|hdd|nvme|emmc|almacenamiento)', n)
    if m: a["storage"] = m.group(1) + m.group(2).upper()
    # Screen
    m = re.search(r'(\d{1,2}[.,]\d)\s*["\']', n)
    if m: a["screen"] = m.group(1).replace(",", ".") + '"'
    # Chip
    m = re.search(
        r'(intel\s+(?:core\s+)?(?:i[3579]|ultra\s*[579])[- ]\d+\w*'
        r'|ryzen\s+[3579][\s\d\w]*'
        r'|apple\s+m[1-4](?:\s+(?:pro|max|ultra))?'
        r'|snapdragon\s+\w+)', n, re.I)
    if m: a["chip"] = m.group(1).strip().title()
    # Color
    for col in ["negro","blanco","plata","gris","azul","rojo","verde","oro","rosa","space gray"]:
        if col in n:
            a["color"] = col.capitalize()
            break
    return a or None

def process_csv(text):
    lines = text.splitlines()
    delim = ";" if (lines[0].count(";") > lines[0].count(",")) else ","
    reader = csv.DictReader(lines, delimiter=delim)

    products, skipped = [], 0
    for row in reader:
        pid   = row.get('codigo', '').strip()
        name  = row.get('denominacion', '').strip()
        if not pid or not name:
            continue

        prod_col = row.get('producto', '').strip()
        cat = categorize(prod_col)
        if not cat:
            skipped += 1
            continue

        price, canon_v = calc_price(
            row.get('precio', '0'),
            row.get('dto', '0'),
            row.get('canon', '0'))
        if not price:
            continue

        st    = stock_status(row.get('stock', '0'), row.get('viajando', '0'))
        brand = row.get('marca', '').strip()

        p = {"id": pid, "n": name, "p": price, "cat": cat,
             "s": prod_col, "b": brand, "st": st}
        if canon_v > 0:
            p["c"] = canon_v
        a = extract_attrs(name, prod_col)
        if a:
            p["a"] = a
        products.append(p)

    log(f"  Procesados: {len(products)}, descartados: {skipped}")
    return products

def update_html(products):
    if not os.path.exists(TEMPLATE):
        log(f"ERROR: {TEMPLATE} no encontrado"); return False
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    b64 = base64.b64encode(
        json.dumps(products, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    pat = r'((?:JSON\.parse\(new TextDecoder\(\)\.decode\(Uint8Array\.from\(atob\(|JSON\.parse\(atob\()")[A-Za-z0-9+/=]+'
    html2, n = re.subn(pat, lambda m: m.group(1) + '"' + b64, html, count=1)
    if n == 0:
        log("ERROR: patrón de datos no encontrado en template.html"); return False
    count = len(products)
    html2 = re.sub(r'\d[\d.,]* productos',
                   f'{count:,} productos'.replace(",", "."), html2, count=3)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html2)
    log(f"  {OUTPUT} → {os.path.getsize(OUTPUT)//1024//1024}MB, {count} productos")
    return True

def main():
    log("=" * 50)
    log("Infofase catalog updater")
    text = download_csv()
    if not text: sys.exit(1)
    products = process_csv(text)
    if not products: sys.exit(1)
    if not update_html(products): sys.exit(1)
    log("Completado OK")
    log("=" * 50)

if __name__ == "__main__":
    main()
