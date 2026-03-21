#!/usr/bin/env python3
"""
update_catalog.py — Infofase auto-catalog updater
Descarga el CSV de Megastore, procesa productos y actualiza index.html
"""
import csv, json, base64, re, os, sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime

CSV_URL   = "https://infofase.com/tienda/megastore.csv"
TEMPLATE  = "template.html"
OUTPUT    = "index.html"
LOG       = "update.log"
IGIC      = 0.07

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

CONCORDANCIAS = {
    "toner":"consumibles","cartucho":"consumibles","tinta":"consumibles",
    "papel":"consumibles","etiqueta":"consumibles","cinta":"consumibles",
    "tambor":"consumibles","fusor":"consumibles","consumible":"consumibles",
    "cable usb":"cables","cable hdmi":"cables","cable vga":"cables",
    "cable dp":"cables","cable tipo-c":"cables","cable rj":"cable_red",
    "cable de red":"cable_red","cable ethernet":"cable_red",
    "impresora":"impresoras","multifuncion":"impresoras","plotter":"impresoras",
    "portatil":"portatiles","portátil":"portatiles","notebook":"portatiles","laptop":"portatiles",
    "tablet":"tablets","ipad":"tablets",
    "smartphone":"smartphones","movil":"smartphones","móvil":"smartphones","iphone":"smartphones",
    "monitor":"monitores","pantalla lcd":"monitores","pantalla led":"monitores",
    "teclado":"perifericos","raton":"perifericos","ratón":"perifericos",
    "webcam":"perifericos","camara web":"perifericos",
    "auricular":"audio","altavoz":"audio","microfono":"audio","micrófono":"audio",
    "switch":"redes","router":"redes","access point":"redes",
    "punto de acceso":"redes","patch panel":"redes","firewall":"redes",
    "tarjeta de red":"redes","nas":"nas",
    "memoria ram":"componentes","procesador":"componentes",
    "tarjeta grafica":"componentes","tarjeta gráfica":"componentes",
    "placa base":"componentes","fuente de alimentacion":"componentes",
    "fuente de alimentación":"componentes","refrigeracion":"componentes",
    "caja ordenador":"componentes","chasis":"componentes",
    "disco duro":"disco_interno","ssd":"disco_interno","nvme":"disco_interno",
    "disco externo":"disco_externo","pendrive":"pendrives","memoria usb":"pendrives",
    "escaner":"escaneres","escáner":"escaneres",
    "sai":"sai_ups","ups":"sai_ups",
    "camara":"camaras","cámara":"camaras",
    "funda":"fundas","maletin":"fundas","maletín":"fundas","mochila":"fundas",
    "servidor":"servidores","smartwatch":"wearables","watch":"wearables",
    "powerbank":"powerbank","software":"software","licencia":"software",
    "tpv":"tpv","terminal punto de venta":"tpv",
}
EXCLUDED = {"etiquetadora","consumible etiquetadora"}

def categorize(sub, fam):
    sf = (sub or "").lower().strip()
    fa = (fam or "").lower().strip()
    if any(ex in sf for ex in EXCLUDED): return None
    for key, cat in CONCORDANCIAS.items():
        if key in sf: return cat
    for key, cat in CONCORDANCIAS.items():
        if key in fa: return cat
    return None

def calc_price(pvp_s, dto_s, canon_s="0"):
    try:
        pvp   = float(str(pvp_s).replace(",",".").replace("€","").strip() or 0)
        dto   = float(str(dto_s).replace(",",".").replace("%","").strip() or 0)
        canon = float(str(canon_s).replace(",",".").replace("€","").strip() or 0)
        if pvp <= 0: return None, 0
        net = pvp * (1 - dto/100)
        return round(net * (1 + IGIC), 2), round(canon * (1 + IGIC), 2)
    except: return None, 0

def extract_attrs(name, sub):
    a = {}
    n = name.lower()
    m = re.search(r'(\d+)\s*gb\s+(?:ram|ddr)', n)
    if m: a["ram"] = m.group(1)+"GB"
    m = re.search(r'(\d+)\s*(tb|gb)\s+(?:ssd|hdd|nvme|emmc)', n)
    if m: a["storage"] = m.group(1)+m.group(2).upper()
    m = re.search(r'(\d{1,2}[.,]\d)\s*["\']', n)
    if m: a["screen"] = m.group(1).replace(",",".")+'"'
    m = re.search(
        r'(intel\s+(?:core\s+)?(?:i[3579]|ultra\s*[579])[- ]\d+\w*'
        r'|ryzen\s+[3579][\s\d\w]*|apple\s+m[1-4](?:\s+(?:pro|max|ultra))?'
        r'|snapdragon\s+\w+)', n, re.I)
    if m: a["chip"] = m.group(1).strip().title()
    return a or None

def process_csv(text):
    lines = text.splitlines()
    # Detect delimiter
    first = lines[0] if lines else ""
    delim = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(lines, delimiter=delim)
    raw_fields = reader.fieldnames or []
    norm = {h: h.strip().lower().replace(" ","_") for h in raw_fields}
    
    col = {}
    for h, hn in norm.items():
        if "referencia" in hn or hn in ("ref","codigo"): col["id"] = h
        elif "nombre" in hn or "descripcion" in hn:      col["name"] = h
        elif "precio" in hn and "tarifa" not in hn:      col["pvp"] = h
        elif "descuento" in hn or hn.startswith("dto"):  col["dto"] = h
        elif "subfamilia" in hn or "sub_familia" in hn:  col["sub"] = h
        elif "familia" in hn:                            col["fam"] = h
        elif "marca" in hn:                              col["brand"] = h
        elif "stock" in hn or "estado" in hn:            col["stock"] = h
        elif "canon" in hn:                              col["canon"] = h
    log(f"  Columnas: {col}")
    
    products, skipped = [], 0
    for row in reader:
        pid   = row.get(col.get("id",""),"").strip()
        name  = row.get(col.get("name",""),"").strip()
        if not pid or not name: continue
        cat = categorize(row.get(col.get("sub",""),""), row.get(col.get("fam",""),""))
        if not cat: skipped += 1; continue
        price, canon_v = calc_price(
            row.get(col.get("pvp",""),"0"),
            row.get(col.get("dto",""),"0"),
            row.get(col.get("canon",""),"0"))
        if not price: continue
        st_raw = row.get(col.get("stock",""),"").lower()
        if any(x in st_raw for x in ["si","sí","stock","disponible","1"]): st = "stock"
        elif any(x in st_raw for x in ["transito","tránsito","pedido","proximo"]): st = "transito"
        else: st = "agotado"
        sub   = row.get(col.get("sub",""),"").strip()
        brand = row.get(col.get("brand",""),"").strip()
        p = {"id":pid,"n":name,"p":price,"cat":cat,"s":sub,"b":brand,"st":st}
        if canon_v > 0: p["c"] = canon_v
        a = extract_attrs(name, sub)
        if a: p["a"] = a
        products.append(p)
    log(f"  Procesados: {len(products)}, descartados: {skipped}")
    return products

def update_html(products):
    if not os.path.exists(TEMPLATE):
        log(f"ERROR: {TEMPLATE} no encontrado")
        return False
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    b64 = base64.b64encode(
        json.dumps(products, ensure_ascii=False, separators=(",",":")).encode("utf-8")
    ).decode("ascii")
    # Replace products b64 — handles both TextDecoder and plain atob patterns
    pat = r'((?:JSON\.parse\(new TextDecoder\(\)\.decode\(Uint8Array\.from\(atob\(|JSON\.parse\(atob\()")[A-Za-z0-9+/=]+'
    html2, n = re.subn(pat, lambda m: m.group(1)+'"'+b64, html, count=1)
    if n == 0:
        log("ERROR: patrón de datos no encontrado en template.html"); return False
    # Update count label
    count = len(products)
    html2 = re.sub(r'\d[\d.,]* productos', f'{count:,} productos'.replace(",","."), html2, count=3)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html2)
    log(f"  {OUTPUT} → {os.path.getsize(OUTPUT)//1024//1024}MB, {count} productos")
    return True

def main():
    log("="*50)
    log("Infofase catalog updater")
    text = download_csv()
    if not text: sys.exit(1)
    products = process_csv(text)
    if not products: sys.exit(1)
    if not update_html(products): sys.exit(1)
    log("Completado OK")
    log("="*50)

if __name__ == "__main__":
    main()
