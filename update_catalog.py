#!/usr/bin/env python3
"""
update_catalog.py — Infofase auto-catalog updater v4
- Descarga CSV de Megastore (proveedor 1)
- Descarga CSV de Binary Canarias (proveedor 2, con imágenes propias)
- Fusiona ambos catálogos: si mismo Part Number, gana el precio menor
- Actualiza tienda general con imágenes de Icecat (Megastore) y URLs propias (Binary)
- Actualiza Zona Apple (593 productos)
- Publica en GitHub Pages via git push automático

Secretos necesarios en GitHub:
  ICECAT_USER    — usuario de icecat.biz
  ICECAT_PASS    — contraseña de icecat.biz
  ICECAT_APP_KEY — clave API Full Icecat
  BINARY_CSV_URL — URL del CSV de Binary Canarias (se configura cuando esté disponible)
"""
import csv, json, base64, re, os, sys, time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from datetime import datetime

CSV_URL        = "https://infofase.com/tienda/megastore.csv"
BINARY_CSV_URL = os.environ.get('BINARY_CSV_URL', '')  # URL del CSV de Binary Canarias
TEMPLATE  = "template.html" if os.path.exists("template.html") else "index.html"
OUTPUT    = "index.html"
LOG       = "update.log"
IMG_CACHE = "img_cache.json"   # cache de icecat_id ya resueltos
IGIC      = 0.07

# Credenciales de Icecat — vienen de GitHub Secrets (variables de entorno)
ICECAT_USER    = os.environ.get('ICECAT_USER', '')
ICECAT_PASS    = os.environ.get('ICECAT_PASS', '')
ICECAT_APP_KEY = os.environ.get('ICECAT_APP_KEY', '')  # Requerido para Full Icecat (cuenta de pago)

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
    """
    Lógica correcta:
    - stock    = unidades físicas (negativo = pedidos pendientes de servir)
    - viajando = unidades en tránsito desde el proveedor
    - efectivo = stock + viajando (unidades netas disponibles/llegando)

    Ejemplos:
      stock=5,  viajando=0 → 'stock'    (hay unidades)
      stock=-1, viajando=0 → 'agotado'  (pedidos pendientes, nada llegando)
      stock=-1, viajando=1 → 'agotado'  (-1+1=0, no hay neto positivo)
      stock=-2, viajando=3 → 'transito' (-2+3=1, llega 1 unidad neta)
      stock=0,  viajando=2 → 'transito' (0+2=2, llegan unidades)
    """
    try:
        qty      = int(str(stock_val).strip() or 0)
        viajando = int(str(viajando_val).strip() or 0)
    except:
        qty, viajando = 0, 0
    if qty > 0:                    return "stock"
    if qty + viajando > 0:         return "transito"
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
    """Consulta Icecat Live (cuenta de pago) y guarda thumb, high, gallery, desc, specs.
    Autenticación: UserName + app_key en la URL (Full Icecat).
    Solo consulta productos que NO están en el cache (nuevos).
    """
    if not ICECAT_USER:
        return None, None

    cache_key = f"{brand.lower()}|{product_code.lower()}"
    if cache_key in cache:
        # En cache (con datos o como None) → ya procesado, no repetir consulta
        cached = cache[cache_key]
        if cached is None: return None, None
        return cached.get('thumb'), cached.get('high')

    from urllib.parse import quote
    brand_enc = quote(brand.strip())
    code_enc  = quote(product_code.strip())
    # app_key: requerido para Full Icecat (contenido de pago)
    # Sin él devuelve HTTP 403 en productos con licencia
    app_key_param = f"&app_key={ICECAT_APP_KEY}" if ICECAT_APP_KEY else ""
    url = (f"https://live.icecat.biz/api/"
           f"?UserName={ICECAT_USER}"
           f"{app_key_param}"
           f"&Language=EN"
           f"&Brand={brand_enc}"
           f"&ProductCode={code_enc}")
    try:
        req = Request(url, headers={"User-Agent": "Infofase-Bot/4.0"})
        with urlopen(req, timeout=15) as r:
            status = r.status
            raw    = r.read().decode("utf-8", errors="replace")
            data   = json.loads(raw)

        # Log de diagnóstico para los primeros 3 productos procesados
        if not hasattr(get_icecat_img, '_diag_count'):
            get_icecat_img._diag_count = 0
        if get_icecat_img._diag_count < 3:
            keys = list(data.keys())
            code_resp = data.get('code', data.get('Code', '—'))
            log(f"  DIAG Icecat [{brand}|{product_code}]: HTTP {status} | keys={keys} | code={code_resp}")
            get_icecat_img._diag_count += 1

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
            # Sin imagen — loguear el response completo para los primeros fallos
            if not hasattr(get_icecat_img, '_fail_count'):
                get_icecat_img._fail_count = 0
            if get_icecat_img._fail_count < 3:
                log(f"  DIAG sin imagen [{brand}|{product_code}]: root_keys={list(root.keys())[:8]} | img_node={img_node}")
                get_icecat_img._fail_count += 1
            # Guardar None para no reintentar en próximas ejecuciones
            cache[cache_key] = None
            return None, None

    except HTTPError as e:
        # Loguear primeros errores HTTP para diagnóstico
        if not hasattr(get_icecat_img, '_http_err_count'):
            get_icecat_img._http_err_count = 0
        if get_icecat_img._http_err_count < 5:
            try:
                err_body = e.read().decode('utf-8', errors='replace')[:200]
            except:
                err_body = '(no body)'
            log(f"  DIAG HTTP {e.code} [{brand}|{product_code}]: {err_body}")
            get_icecat_img._http_err_count += 1
        # 404/403/401 = producto no encontrado → cachear None, no reintentar
        if e.code in (404, 403, 401):
            cache[cache_key] = None
        return None, None
    except Exception as ex:
        if not hasattr(get_icecat_img, '_err_logged'):
            log(f"  Icecat error ({brand} {product_code}): {ex}")
            get_icecat_img._err_logged = True
        return None, None



# ── Binary Canarias — mapeo de categorías ────────────────────────────────────
# El CSV de Binary usa una jerarquía de 3 niveles: "Nivel1>Nivel2>Nivel3"
# La mapeamos a las mismas categorías internas que Megastore
BINARY_CAT_MAP = {
    # Consumibles
    'consumibles impresión>tintas':          'consumibles',
    'consumibles impresión>toner':           'consumibles',
    'consumibles impresión>otros':           'consumibles',
    'consumibles varios>pilas':              'consumibles',
    'consumibles varios>material de oficina':'consumibles',
    'consumibles varios>consumibles':        'consumibles',

    # Impresoras y escáneres
    'impresoras y escáner>impresoras inyección': 'impresoras',
    'impresoras y escáner>impresoras láser':     'impresoras',
    'impresoras y escáner>impresoras matriciales':'impresoras',
    'impresoras y escáner>impresoras plotter':   'impresoras',
    'impresoras y escáner>impresoras etiquetas': 'impresoras',
    'impresoras y escáner>escáner':              'escaneres',
    'terminales tpv>impresoras tpv':             'tpv',
    'terminales tpv>terminal punto venta':       'tpv',
    'terminales tpv>escáner':                    'escaneres',

    # Portátiles
    'ordenadores portátiles>notebooks':          'portatiles',
    'ordenadores portátiles>accesorios notebook':'perifericos',
    'ordenadores portátiles>maletines':          'fundas',

    # Ordenadores / componentes
    'ordenadores>ordenadores':                   'componentes',
    'ordenadores>servidores':                    'componentes',
    'ordenadores>barebones':                     'componentes',
    'ordenadores>raspberry':                     'componentes',
    'integración>microprocesadores':             'componentes',
    'integración>placas base':                   'componentes',
    'integración>memorias':                      'componentes',
    'integración>carcasas':                      'componentes',
    'integración>fuentes alimentación':          'componentes',
    'integración>refrigeración':                 'componentes',
    'integración>tarjetas gráficas':             'componentes',
    'integración>tarjetas de sonido':            'componentes',
    'integración>controladoras':                 'componentes',
    'integración>ópticos':                       'componentes',

    # Disco interno
    'integración>discos internos':               'disco_interno',

    # Monitores
    'periféricos>monitores':                     'monitores',
    'imagen y sonido>televisores':               'monitores',
    'imagen y sonido>proyectores':               'monitores',
    'imagen y sonido>marcos digitales':          'monitores',

    # Smartphones y tablets
    'telefonía / smartphones>telefonía móvil':   'smartphones',
    'tablets / ebooks>tabletas':                 'tablets',
    'tablets / ebooks>libros electrónicos':      'tablets',
    'tablets / ebooks>pda':                      'tablets',

    # Periféricos
    'periféricos>ratones':                       'perifericos',
    'periféricos>teclados':                      'perifericos',
    'periféricos>hubs y adaptadores':            'perifericos',
    'periféricos>memoria flash':                 'perifericos',
    'periféricos>pendrive':                      'perifericos',
    'periféricos>accesorios streaming':          'perifericos',
    'periféricos>discos y cajas externos':       'nas',
    'multimedia>webcam':                         'perifericos',
    'multimedia>sonido':                         'audio',
    'multimedia>sintonizadoras':                 'perifericos',
    'multimedia>reproductores':                  'perifericos',
    'multimedia>discos y cajas multimedia':      'perifericos',
    'imagen y sonido>home cinema':               'audio',
    'imagen y sonido>fotografía':                'camaras',

    # Cables
    'periféricos>cables':                        'cables',
    'redes y cctv>cables':                       'cable_red',

    # Redes
    'redes y cctv>switch y routers':             'redes',
    'redes y cctv>wifi':                         'redes',
    'redes y cctv>lan':                          'redes',
    'redes y cctv>cámaras ip':                   'redes',
    'redes y cctv>cctv':                         'redes',
    'redes y cctv>armarios':                     'redes',
    'redes y cctv>fax':                          'redes',

    # NAS y almacenamiento externo
    'periféricos>discos y cajas externos>cajas servidor nas': 'nas',
    'periféricos>discos y cajas externos>discos externos red':'nas',

    # SAI / UPS
    'periféricos>sais y regletas':               'sai_ups',

    # Powerbank / fundas
    'tablets / ebooks>accesorios pad y tablet>alimentación y powerbank': 'powerbank',
    'tablets / ebooks>accesorios pad y tablet>fundas':                   'fundas',
    'ordenadores portátiles>maletines>fundas':                           'fundas',
    'ordenadores portátiles>maletines>mochilas':                         'fundas',
    'telefonía / smartphones>accesorios de telefonía móvil>fundas':      'fundas',

    # Wearables / smartwatch
    'telefonía / smartphones>accesorios de telefonía móvil>smartwatch':  'wearables',
    'telefonía / smartphones>accesorios de telefonía móvil>pulseras':    'wearables',

    # Software
    'software':                                  'software',
    'software esd':                              'software',

    # Soportes TV
    'imagen y sonido>soportes tv':               'monitores',

    # Gaming
    'juegos y consolas':                         'perifericos',

    # Hogar / electrodomésticos → descartados (fuera del core IT)
    # 'hogar / electrónica consumo': None,  → se descartan
}

def categorize_binary(categoria_str):
    """Mapea la categoría jerárquica de Binary a categoría interna.
    Prueba de más específica a más general.
    Devuelve None para categorías no IT (hogar, mascotas, etc.).
    """
    if not categoria_str:
        return None
    cat = categoria_str.strip().lower()

    # Excluir explícitamente categorías no IT
    excluded = [
        'hogar / electrónica consumo>electrodom',
        'hogar / electrónica consumo>menaje',
        'hogar / electrónica consumo>mascotas',
        'hogar / electrónica consumo>cuidado personal',
        'hogar / electrónica consumo>descanso',
        'hogar / electrónica consumo>ferretería',
        'hogar / electrónica consumo>minicadenas',
        'hogar / electrónica consumo>ocio',
        'hogar / electrónica consumo>aire acondicionado',
        'hogar / electrónica consumo>energia',
        'hogar / electrónica consumo>iluminacion',
        'hogar / electrónica consumo>gps',
        'hogar / electrónica consumo>seguridad',
        'repuestos',
        'servicios',
        'juegos y consolas>videojuegos',
        'juegos y consolas>consolas',
        'juegos y consolas>accesorios consolas',
    ]
    for ex in excluded:
        if cat.startswith(ex):
            return None

    # Intentar coincidencia de más largo a más corto
    for key in sorted(BINARY_CAT_MAP.keys(), key=len, reverse=True):
        if cat.startswith(key):
            return BINARY_CAT_MAP[key]

    return None


def download_binary_csv():
    """Descarga el CSV de Binary Canarias o lo lee del fichero local binary.csv.
    Prioridad: 1) BINARY_CSV_URL (secret GitHub), 2) fichero local binary.csv
    Devuelve el texto o None.
    """
    # 1. Intentar URL remota (cuando esté configurada)
    if BINARY_CSV_URL:
        log(f"  Descargando Binary CSV: {BINARY_CSV_URL}")
        req = Request(BINARY_CSV_URL, headers={"User-Agent": "Infofase-Bot/4.0"})
        try:
            with urlopen(req, timeout=30) as r:
                raw = r.read()
            for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
                try:
                    text = raw.decode(enc)
                    log(f"    Binary CSV URL: {len(raw)//1024}KB ({enc})")
                    return text
                except UnicodeDecodeError:
                    continue
        except URLError as e:
            log(f"    ERROR descargando Binary CSV URL: {e}")

    # 2. Fallback: fichero local binary.csv (subido directamente al repo)
    local_path = "binary.csv"
    if os.path.exists(local_path):
        for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
            try:
                with open(local_path, encoding=enc) as f:
                    text = f.read()
                size_kb = os.path.getsize(local_path) // 1024
                log(f"  Binary CSV local ({local_path}): {size_kb}KB ({enc})")
                return text
            except UnicodeDecodeError:
                continue

    log("  Binary CSV: BINARY_CSV_URL no configurado y binary.csv no encontrado — omitiendo segundo proveedor")
    return None


def calc_price_binary(precio_sin_iva, pct_iva, pct_liquidacion, canon):
    """Calcula PVP final con IGIC canario para productos de Binary.
    Fórmula: precio_base × (1 - dto%) × 1.07 + canon_igic
    Los productos de Binary ya tienen IVA peninsular incluido implícitamente
    en el precio — lo que nos dan es el precio SIN impuestos.
    En Canarias aplicamos IGIC 7% en lugar del IVA peninsular.
    """
    try:
        base   = float(str(precio_sin_iva).replace(',','.').strip() or 0)
        dto    = float(str(pct_liquidacion).replace('%','').strip() or 0) / 100
        c      = float(str(canon).replace(',','.').strip() or 0)
        if base <= 0:
            return None, 0
        pvp   = round(base * (1 - dto) * (1 + IGIC), 2)
        c_out = round(c * (1 + IGIC), 2)
        return pvp, c_out
    except:
        return None, 0


def stock_status_binary(stock_local, stock_prov):
    """Binary tiene stock_local y stock_provincias (no hay campo viajando).
    - stock_local > 0          → 'stock'   (disponible hoy)
    - stock_local = 0 y prov>0 → 'transito' (llega en 2-3 días de Península)
    - todo cero                → 'agotado'
    """
    try:
        sl = int(str(stock_local).strip() or 0)
        sp = int(str(stock_prov).strip() or 0)
    except:
        sl, sp = 0, 0
    if sl > 0:         return 'stock',    sl
    if sp > 0:         return 'transito', sp
    return 'agotado',  0


def process_binary_csv(text):
    """Procesa el CSV de Binary Canarias y devuelve lista de productos
    con la misma estructura que process_csv() (Megastore).
    Las imágenes vienen directamente de la URL del CSV.
    """
    lines = text.splitlines()
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(lines, delimiter=delim)

    products  = []
    skipped   = 0
    no_price  = 0

    for row in reader:
        # Campos clave
        codigo  = row.get('Código', '').strip()
        nombre  = row.get('Artículo', '').strip()
        marca   = row.get('Marca', '').strip()
        cat_raw = row.get('Categoría', '').strip()
        part_n  = row.get('Part number', '').strip()
        img_url = row.get('URL imagen', '').strip()

        if not codigo or not nombre:
            continue

        # Usar Part Number como ID si existe, sino código del proveedor
        # Prefijo 'BIN-' para distinguir de Megastore en caso de colisión
        pid = part_n if part_n else f"BIN-{codigo}"

        # Categoría
        cat = categorize_binary(cat_raw)
        if not cat:
            skipped += 1
            continue

        # Precio
        price, canon_v = calc_price_binary(
            row.get('Precio sin impuestos', '0'),
            row.get('Porcentaje impuestos', '7'),
            row.get('Porcentaje liquidación', '0'),
            row.get('Canon digital', '0'),
        )
        if not price:
            no_price += 1
            continue

        # Stock
        st, qty = stock_status_binary(
            row.get('Stock local', '0'),
            row.get('Stock provincias', '0'),
        )

        p = {
            "id":  pid,
            "n":   nombre,
            "p":   price,
            "cat": cat,
            "s":   cat_raw,          # descripción de categoría
            "b":   marca,
            "st":  st,
            "_src": "binary",        # marca interna de origen
            "_cod": codigo,          # código original Binary (para deduplicación)
        }
        if canon_v > 0:  p["c"]   = canon_v
        if st == "stock" and qty > 0:
            p["qty"] = qty
        elif st == "transito" and qty > 0:
            p["tv"]  = qty

        # Imagen directamente desde la URL del CSV
        if img_url:
            p["img"]  = img_url
            p["imgH"] = img_url  # misma URL — el proveedor no da alta resolución separada

        a = extract_attrs(nombre)
        if a: p["a"] = a

        products.append(p)

    log(f"  Binary: {len(products)} productos cargados | {skipped} descartados (cat. no IT) | {no_price} sin precio")
    return products


def merge_products(megastore_products, binary_products):
    """Fusiona los dos catálogos.
    Regla: si mismo Part Number/ID → queda el de precio menor.
    Los productos exclusivos de cada proveedor se añaden directamente.
    """
    # Indexar Megastore por id
    merged = {p['id'].lower(): p for p in megastore_products}

    added    = 0
    replaced = 0
    skipped  = 0

    for bp in binary_products:
        key = bp['id'].lower()
        if key in merged:
            existing = merged[key]
            if bp['p'] < existing['p']:
                # Binary más barato → reemplazar, conservar imagen de Icecat si existe
                if not bp.get('img') and existing.get('img'):
                    bp['img']  = existing['img']
                    bp['imgH'] = existing.get('imgH', existing['img'])
                if existing.get('gallery'): bp['gallery'] = existing['gallery']
                if existing.get('desc'):    bp['desc']    = existing['desc']
                if existing.get('specs'):   bp['specs']   = existing['specs']
                merged[key] = bp
                replaced += 1
            else:
                skipped += 1  # Megastore es más barato
        else:
            merged[key] = bp
            added += 1

    result = list(merged.values())
    log(f"  Fusión: {len(megastore_products)} Megastore + {len(binary_products)} Binary")
    log(f"    → {added} nuevos de Binary | {replaced} reemplazados (Binary más barato) | {skipped} ignorados (Megastore más barato)")
    log(f"    → Total catálogo fusionado: {len(result)} productos")
    return result


def process_csv(text, img_cache):
    lines = text.splitlines()
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(lines, delimiter=delim)

    products   = []
    skipped    = 0
    img_found  = 0
    img_miss   = 0
    img_skip   = 0  # Apple products — skip (have their own images)
    img_cached = 0  # Productos ya en cache (no se consulta Icecat)
    new_lookups = 0  # Consultas nuevas a Icecat en esta ejecución

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

        # Extraer valores brutos para calcular unidades netas en tránsito
        try:
            qty_raw = int(str(row.get('stock','0')).strip() or 0)
            via_raw = int(str(row.get('viajando','0')).strip() or 0)
        except:
            qty_raw, via_raw = 0, 0

        st   = stock_status(qty_raw, via_raw)
        prod = row.get('producto','').strip()

        p = {"id":pid, "n":name, "p":price, "cat":cat,
             "s":prod, "b":brand, "st":st}
        # Unidades para mostrar en badge y ficha de producto:
        # "qty": unidades físicas disponibles  (st=stock)
        # "tv":  unidades netas en camino      (st=transito)
        if st == "stock" and qty_raw > 0:
            p["qty"] = qty_raw
        elif st == "transito":
            p["tv"] = qty_raw + via_raw
        if canon_v > 0: p["c"] = canon_v
        a = extract_attrs(name)
        if a: p["a"] = a

        # Get Icecat image — skip Apple (already handled in ZA)
        if brand.lower() == 'apple':
            img_skip += 1
        else:
            cache_key = f"{brand.lower()}|{pid.lower()}"
            in_cache  = cache_key in img_cache

            if not in_cache:
                # Producto nuevo — consultar Icecat
                thumb, high = get_icecat_img(brand, pid, img_cache)
                new_lookups += 1
                # Pausa cada 20 peticiones para no saturar la API
                if new_lookups % 20 == 0:
                    time.sleep(0.3)
            else:
                # Ya en cache (con imagen o como None) — no repetir consulta
                cached = img_cache.get(cache_key)
                thumb  = cached.get('thumb') if cached else None
                high   = cached.get('high')  if cached else None
                img_cached += 1

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
    log(f"  Imágenes: {img_found} con imagen | {img_miss} sin imagen | {img_skip} Apple (skip)")
    if new_lookups > 0:
        log(f"  Icecat: {new_lookups} productos NUEVOS consultados en esta ejecución")
    else:
        log(f"  Icecat: sin productos nuevos — no se realizaron consultas")
    log(f"  Cache: {img_cached} productos servidos desde cache")
    return products

# ── ASCII encode helper ───────────────────────────────────────
def fix_js_script(s):
    """Fix JS in HTML: replace backslash-n between statements with actual newlines,
    fix actual newlines inside strings back to backslash-n. Regex-aware."""
    result = []; state='code'; prev_token=''; i=0
    while i<len(s):
        c=s[i]
        if state=='code':
            if c=='/' and i+1<len(s) and s[i+1] not in ('/', '*'):
                pt=prev_token.rstrip()
                if (not pt or pt[-1] in '=([,!&|?:{};' or
                    pt.endswith('return') or pt.endswith('typeof') or
                    pt.endswith('void') or pt.endswith('delete')):
                    state='regex'; result.append(c); i+=1; continue
            if c=="'": state='sq'; result.append(c)
            elif c=='"': state='dq'; result.append(c)
            elif c=='`': state='tpl'; result.append(c)
            elif c=='/' and i+1<len(s) and s[i+1]=='/': state='cl'; result.append(c)
            elif c=='/' and i+1<len(s) and s[i+1]=='*': state='cb'; result.append(c)
            else: result.append(c)
            if c not in ' \t\n': prev_token=(prev_token+c)[-50:]
        elif state=='sq':
            if c=='\\' and i+1<len(s): result.append(c); result.append(s[i+1]); i+=2; continue
            elif c=="'": state='code'; result.append(c)
            elif c=='\n': result.append('\\n')
            else: result.append(c)
        elif state=='dq':
            if c=='\\' and i+1<len(s): result.append(c); result.append(s[i+1]); i+=2; continue
            elif c=='"': state='code'; result.append(c)
            elif c=='\n': result.append('\\n')
            else: result.append(c)
        elif state=='tpl':
            if c=='\\' and i+1<len(s): result.append(c); result.append(s[i+1]); i+=2; continue
            elif c=='`': state='code'; result.append(c)
            else: result.append(c)
        elif state=='regex':
            if c=='\\' and i+1<len(s): result.append(c); result.append(s[i+1]); i+=2; continue
            elif c=='[': state='regex_class'; result.append(c)
            elif c=='/': state='code'; result.append(c)
            else: result.append(c)
        elif state=='regex_class':
            if c=='\\' and i+1<len(s): result.append(c); result.append(s[i+1]); i+=2; continue
            elif c==']': state='regex'; result.append(c)
            else: result.append(c)
        elif state=='cl':
            result.append(c)
            if c=='\n': state='code'
        elif state=='cb':
            result.append(c)
            if c=='*' and i+1<len(s) and s[i+1]=='/': result.append(s[i+1]); i+=2; state='code'; continue
        i+=1
    return ''.join(result)

def fix_tienda_html(html):
    """Fix JS scripts in tienda HTML: normalize backslash-n sequences."""
    import re as _re
    def fix_script(m):
        sc = m.group(2)
        sc2 = sc.replace('\\n', '\n')  # backslash-n → actual newline
        sc3 = fix_js_script(sc2)           # newlines in strings → backslash-n
        return m.group(1) + sc3 + m.group(3)
    return _re.sub(r'(<script[^>]*>)(.*?)(</script>)', fix_script, html, flags=_re.S)

def ascii_encode(html_str):
    # Legacy - kept for ZA encoding compatibility
    out = []
    for ch in html_str:
        if ord(ch) > 127:
            out.append(f'&#{ord(ch)};')
        else:
            out.append(ch)
    return ''.join(out)

# ── Stock badge patch builder ─────────────────────────────────
def _build_stock_patch(stock_data, var_suffix):
    """Genera CSS + JS para mostrar unidades en tarjetas Y modal/ficha.

    Estrategia doble para máxima compatibilidad con cualquier estructura HTML:
      1. Busca .badge / .badge-lines (Zona Apple — clases conocidas)
      2. Si no encuentra, busca por contenido de texto: "Disponible", "24h",
         "recogida", "agotado", etc. (Tienda Online — clases desconocidas)
    Funciona en ambos iframes sin conocer las clases CSS de la tienda.
    """
    stock_json = json.dumps(stock_data, ensure_ascii=False, separators=(',',':'))
    vname = '__STOCK_' + var_suffix   # window.__STOCK_ZA / window.__STOCK_TO
    cprod = '__CPROD_' + var_suffix   # producto abierto actualmente

    css = (
        '<style>'
        '.badge-qty{'
          'display:block;font-size:8.5px;font-weight:600;'
          'margin-top:2px;line-height:1.3'
        '}'
        '.badge.st .badge-qty{color:#1D4ED8}'
        '.badge.tr .badge-qty{color:#C2410C}'
        '.m-info .badge-qty{font-size:10px}'
        # Tienda: elemento inline junto al texto de disponibilidad
        '.ifx-qty{'
          'display:inline-block;font-size:11px;font-weight:600;'
          'margin-left:8px;vertical-align:middle'
        '}'
        '.ifx-qty.ifx-st{color:#1D4ED8}'
        '.ifx-qty.ifx-tr{color:#C2410C}'
        '.modal .ifx-qty,.m-info .ifx-qty{font-size:12px;margin-left:0;display:block;margin-top:4px}'
        '</style>'
    )

    js = (
        '<script>'
        f'window.{vname}={stock_json};'
        '(function(){'
          f'var SD=window.{vname}||{{}};'

          # Texto de unidades
          'function qtyText(d){'
            'if(!d)return null;'
            'if(d.st==="stock"&&d.qty>0)'
              'return d.qty+(d.qty===1?" ud":" uds");'
            'if(d.st==="transito"&&d.net>0)'
              'return d.net+(d.net===1?" en camino":" en camino");'
            'return null;'
          '}'

          # Estrategia 1: ZA — busca .badge con .badge-lines
          'function addToBadge(root,d){'
            'if(!root||!root.querySelector)return false;'
            'var b=root.querySelector(".badge");'
            'if(!b)return false;'
            'if(b.querySelector(".badge-qty"))return true;'
            'var t=qtyText(d);if(!t)return true;'
            'var s=document.createElement("span");'
            's.className="badge-qty";s.textContent=t;'
            'var bl=b.querySelector(".badge-lines");'
            'if(bl)bl.appendChild(s);else b.appendChild(s);'
            'return true;'
          '}'

          # Estrategia 2: Tienda — buscar elemento por texto de disponibilidad
          'function findAvailEl(root){'
            'var kws=["disponible","agotado","transito","recogida","24h","48h","72h","envío","envio"];'
            'if(!root||!root.querySelectorAll)return null;'
            'var els=root.querySelectorAll("*");'
            'for(var i=0;i<els.length;i++){'
              'var el=els[i];'
              'if(el.childElementCount>3)continue;'
              'var t=(el.textContent||"").trim().toLowerCase();'
              'if(t.length<2||t.length>120)continue;'
              'for(var j=0;j<kws.length;j++){'
                'if(t.indexOf(kws[j])>=0)return el;'
              '}'
            '}'
            # Fallback: buscar por clase parcial
            'var clsFb=["badge","status","avail","stock","delivery","disp"];'
            'for(var k=0;k<clsFb.length;k++){'
              'var f=root.querySelector("[class*=\\""+clsFb[k]+"\\"]");'
              'if(f)return f;'
            '}'
            'return null;'
          '}'

          # Añadir qty al elemento correcto
          'function addQty(root,d){'
            'if(!d)return;'
            'var t=qtyText(d);if(!t)return;'
            'if(root&&root.querySelector&&root.querySelector(".badge-qty,.ifx-qty"))return;'
            # Estrategia 1: ZA badge
            'if(addToBadge(root,d))return;'
            # Estrategia 2: tienda
            'var el=findAvailEl(root);'
            'if(el){'
              'var sp=document.createElement("span");'
              'sp.className="ifx-qty "+(d.st==="stock"?"ifx-st":"ifx-tr");'
              'sp.textContent=t;'
              'el.parentNode.insertBefore(sp,el.nextSibling);'
              'return;'
            '}'
            # Último recurso: añadir al cuerpo de la tarjeta/modal
            'var body=root.querySelector&&root.querySelector(".card-body,.m-info,.product-info");'
            'if(body){'
              'var dv=document.createElement("div");'
              'dv.className="ifx-qty "+(d.st==="stock"?"ifx-st":"ifx-tr");'
              'dv.textContent=t;body.appendChild(dv);'
            '}'
          '}'

          # ID del producto: onclick (ZA) o .cref/.card-ref (Tienda)
          'function cardId(card){'
            # Estrategia 1: onclick="openModal('id')" — Zona Apple
            'var oc=card.getAttribute("onclick")||"";'
            r'var m=oc.match(/openModal\([\'"]([^\'"]+)[\'"]\)/);'
            'if(m)return m[1].toLowerCase();'
            # Estrategia 2: .cref o .card-ref — Tienda Online
            'var ref=card.querySelector(".cref,.card-ref,[class*=ref]");'
            'if(ref)return(ref.textContent||"").trim().toLowerCase();'
            'return null;'
          '}'

          # Parchear tarjeta
          'function patchCard(card){'
            'var id=cardId(card);'
            'if(id)addQty(card,SD[id]);'
          '}'

          # Rastrear producto abierto
          f'window.{cprod}=null;'
          'document.addEventListener("click",function(e){'
            'var c=e.target.closest?e.target.closest(".card"):null;'
            f'if(c){{var id=cardId(c);if(id)window.{cprod}=id;}}'
          '},true);'

          # Parchear modal
          'function patchModal(inner){'
            f'var id=window.{cprod};'
            'if(!id){'
              # ZA: .m-ref | Tienda: .cref o cualquier elemento con clase *ref*
              'var ref=inner.querySelector?inner.querySelector(".m-ref,.cref,.card-ref,[class*=ref]"):null;'
              'if(ref)id=(ref.textContent||"").trim().toLowerCase();'
            '}'
            'if(id)addQty(inner,SD[id]);'
          '}'

          # Observar #grid
          'function patchAll(){document.querySelectorAll(".card").forEach(patchCard);}'
          'var grid=document.getElementById("grid");'
          'if(grid)new MutationObserver(function(ms){'
            'ms.forEach(function(mu){'
              'mu.addedNodes.forEach(function(n){'
                'if(n.nodeType!==1)return;'
                'if(n.classList&&n.classList.contains("card"))patchCard(n);'
                'else if(n.querySelectorAll)n.querySelectorAll(".card").forEach(patchCard);'
              '});'
            '});'
          '}).observe(grid,{childList:true,subtree:true});'

          # Observar cualquier modal/ficha (modalInner u otros)
          'function observeModal(el){'
            'new MutationObserver(function(){'
              'setTimeout(function(){patchModal(el);},80);'
            '}).observe(el,{childList:true,subtree:true});'
          '}'
          'var mi=document.getElementById("modalInner");'
          'if(mi)observeModal(mi);'
          'document.querySelectorAll("[id*=modal],[id*=Modal],[id*=detail],[id*=ficha],[id*=product]")'
          '.forEach(function(el){if(el.id!=="modalInner")observeModal(el);});'

          'if(document.readyState==="loading")'
            'document.addEventListener("DOMContentLoaded",patchAll);'
          'else{patchAll();setTimeout(patchAll,600);setTimeout(patchAll,2000);}'
        '})();'
        '</script>'
    )
    return css + js

# ── Update Zona Apple ─────────────────────────────────────────
def _find_array_end(s, start):
    """Bracket matching robusto — localiza el ] de cierre ignorando strings y anidados.
    Necesario porque find('];\n') falla con: \r\n, sin salto de línea, arrays anidados."""
    depth = 0; in_str = False; esc = False
    for i in range(start, len(s)):
        c = s[i]
        if esc:                  esc = False; continue
        if c == '\\' and in_str: esc = True;  continue
        if c == '"':             in_str = not in_str; continue
        if in_str:               continue
        if   c == '[': depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0: return i
    return -1

def update_zona_apple(html, csv_rows):
    csv_by_id = {r.get('codigo','').strip().lower(): r for r in csv_rows}
    log(f"  ZA: {len(csv_by_id)} entradas en CSV para cruzar con Zona Apple")

    # FIX A: aceptar comillas SIMPLES o DOBLES alrededor del blob base64
    # Bug original: r'(var _ZA\s*=\s*")...' sólo buscaba comillas dobles.
    # Si el HTML usa comillas simples, za_m era None → saltaba toda la actualización.
    za_m = re.search(r'(var _ZA\s*=\s*["\'])([A-Za-z0-9+/=]+)(["\'])', html)
    if not za_m:
        log("  ZA: _ZA no encontrado en HTML, saltando"); return html, 0

    za_html = base64.b64decode(za_m.group(2)).decode('ascii', errors='replace')
    all_start = za_html.find('let ALL    = [')
    if all_start < 0: all_start = za_html.find('let ALL = [')
    if all_start < 0:
        log("  ZA: let ALL no encontrado"); return html, 0

    bracket_pos = za_html.index('[', all_start)

    # FIX B: bracket matching en lugar de find('];\n')
    # Bug original: find('];\n') devuelve -1 con \r\n, sin \n tras ]; o con arrays
    # anidados en los datos. Al devolver -1: suffix=za_html[0:] (todo el HTML),
    # el slice JSON quedaba vacío → json.loads('') lanzaba excepción → return sin actualizar.
    all_end = _find_array_end(za_html, bracket_pos)
    if all_end == -1:
        log("  ZA: no se encontró cierre del array ALL"); return html, 0

    prefix = za_html[:bracket_pos]
    suffix = za_html[all_end + 1:]
    try:
        za_products = json.loads(za_html[bracket_pos:all_end + 1])
    except Exception as e:
        log(f"  ZA: error parseando JSON: {e}"); return html, 0

    log(f"  ZA: {len(za_products)} productos Apple en catálogo")
    updated = 0; not_found = 0
    for p in za_products:
        pid = p.get('id','').strip().lower()
        row = csv_by_id.get(pid)
        if not row: not_found += 1; continue
        price, canon_v = calc_price(
            row.get('precio','0'), row.get('dto','0'), row.get('canon','0'))
        if price and price > 0:
            p['price'] = price; p['canon'] = canon_v
        try:
            qty      = int(row.get('stock','0').strip() or 0)
            viajando = int(row.get('viajando','0').strip() or 0)
        except:
            qty, viajando = 0, 0
        net = qty + viajando   # unidades netas reales disponibles/llegando
        p['stock']   = qty
        p['transit'] = viajando
        # "net": unidades próximas entrada (>0 sólo en tránsito)
        # Ejemplo: stock=-1, viajando=2 → net=1 → web muestra "1 unidad próxima entrada"
        # Lógica unificada con stock_status() de la tienda general:
        #   qty > 0            → 'stock'
        #   qty+viajando > 0   → 'transit'  (hay neto positivo llegando)
        #   en otro caso       → 'agotado'
        p['status']  = ('stock'    if qty > 0 else
                        'transito' if net > 0  else 'agotado')
        if p['status'] == 'transito':
            p['net'] = net
        # LOG DIAGNÓSTICO: mostrar valores CSV exactos para productos en tránsito
        # y específicamente para MDE14Y/A
        if pid == 'mde14y/a' or (pid.upper() == 'MDE14Y/A'):
            log(f"  ZA DIAG MDE14Y/A: stock_csv={row.get('stock','?')} viajando_csv={row.get('viajando','?')} → qty={qty} via={viajando} net={net} status={p['status']}")
        updated += 1
    log(f"  ZA: {updated}/{len(za_products)} actualizados | {not_found} sin match en CSV")
    new_all = json.dumps(za_products, ensure_ascii=False, separators=(',',':'))

    # ── Diccionario de stock para el patch JS ─────────────────────────────────
    # Solo productos con unidades relevantes (stock>0 o transito>0)
    stock_data = {}
    for p in za_products:
        pid2 = p.get('id','').strip().lower()
        st   = p.get('status','')
        qty2 = p.get('stock', 0)
        net2 = p.get('net', 0)
        if st == 'stock' and qty2 > 0:
            stock_data[pid2] = {'st': 'stock',    'qty': qty2}
        elif st == 'transito' and net2 > 0:
            stock_data[pid2] = {'st': 'transito', 'net': net2}

    patch_block = _build_stock_patch(stock_data, 'ZA')
    log(f"  ZA: patch unidades inyectado ({len(stock_data)} prods con stock/transito)")

    new_za_html = prefix + new_all + suffix
    if '</body>' in new_za_html:
        new_za_html = new_za_html.replace('</body>', patch_block + '</body>', 1)
    else:
        new_za_html += patch_block

    new_za_b64 = base64.b64encode(
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
        log("  Modo: standalone tienda")
        # FIX CRITICO: _ZA puede coexistir con var ALL en la misma pagina.
        # Bug anterior: za_updated=0 y NO se llamaba a update_zona_apple,
        # por lo que Zona Apple NUNCA se actualizaba en este modo.
        html2, za_updated = update_zona_apple(html2, _csv_rows)
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
        tg_fixed = fix_tienda_html(tg_new)

        # ── Inyectar patch de unidades en la tienda ───────────────
        to_stock = {}
        for p in products:
            pid2 = p.get('id','').strip().lower()
            st   = p.get('st','')
            if st == 'stock' and p.get('qty', 0) > 0:
                to_stock[pid2] = {'st': 'stock',    'qty': p['qty']}
            elif st == 'transito' and p.get('tv', 0) > 0:
                to_stock[pid2] = {'st': 'transito', 'net': p['tv']}
        to_patch = _build_stock_patch(to_stock, 'TO')
        if '</body>' in tg_fixed:
            tg_fixed = tg_fixed.replace('</body>', to_patch + '</body>', 1)
        else:
            tg_fixed += to_patch
        log(f"  Tienda: patch unidades inyectado ({len(to_stock)} prods con stock/transito)")

        new_tg_b64  = base64.b64encode(tg_fixed.encode('utf-8')).decode('ascii')
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

    # Cargar cache de imágenes
    # Los entries con valor = imagen encontrada
    # Los entries con valor = None  = ya consultado en Icecat, no encontrado → NO reintentar
    # Los entries ausentes           = producto nuevo → consultar Icecat
    img_cache = load_img_cache()
    valid  = sum(1 for v in img_cache.values() if v is not None)
    nulls  = sum(1 for v in img_cache.values() if v is None)

    # PURGE_NULL_CACHE=true → borrar nulls para reintentarlos (usar tras cambio de cuenta)
    # Activar con variable de entorno en el workflow o manualmente.
    # Después de una ejecución con esto activo, volver a False.
    purge_nulls = os.environ.get('PURGE_NULL_CACHE','').lower() in ('1','true','yes')
    if purge_nulls and nulls > 0:
        for k in [k for k,v in img_cache.items() if v is None]:
            del img_cache[k]
        log(f"  Cache: {valid} con imagen | {nulls} nulls PURGADOS para reintentar con cuenta de pago | total ahora {len(img_cache)}")
    else:
        log(f"  Cache: {valid} con imagen | {nulls} sin imagen (no se reintentarán) | total {len(img_cache)}")

    text = download_csv()
    if not text: sys.exit(1)

    # Autodetectar delimitador — igual que process_csv() para garantizar consistencia
    _lines = text.splitlines()
    _delim = ";" if _lines[0].count(";") > _lines[0].count(",") else ","
    log(f"  Delimitador CSV detectado: '{_delim}'")
    _csv_rows = list(csv.DictReader(_lines, delimiter=_delim))
    products  = process_csv(text, img_cache)
    if not products: sys.exit(1)

    # ── Segundo proveedor: Binary Canarias ──────────────────────
    binary_text = download_binary_csv()
    if binary_text:
        binary_products = process_binary_csv(binary_text)
        if binary_products:
            products = merge_products(products, binary_products)
    else:
        if BINARY_CSV_URL:
            log("  AVISO: No se pudo descargar el CSV de Binary")
        else:
            log("  Binary CSV: BINARY_CSV_URL no configurado — omitiendo segundo proveedor")

    # Save updated cache
    save_img_cache(img_cache)
    log(f"  Cache guardado: {len(img_cache)} entradas")

    if not update_html(products): sys.exit(1)

    log("Completado OK")
    log("=" * 50)

if __name__ == "__main__":
    main()
