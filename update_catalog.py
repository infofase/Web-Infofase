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

def extract_attrs(name, cat_raw=''):
    """Extrae atributos del nombre del producto para filtros de navegación.
    Diseñado para los nombres reales de Binary Canarias.
    Orientado por categoría para máxima precisión.
    """
    a = {}
    n = name        # case original para algunos patrones
    nl = name.lower()
    cat = cat_raw.lower()

    # ─── DETECTAR SI ES UN ACCESORIO/SOPORTE — sin atributos útiles ──
    if re.search(r'\bsoporte\b|\bbrazo\b|\belevador\b|\bstand\b|\bpedestal\b'
                 r'|\bbandeja\b|\badaptador para\b|\bcargador appro\b', nl):
        # Soportes de monitor, de CPU, cargadores de portátil → solo color
        pass  # continúa para extraer color si procede

    # ─── PANTALLA (solo para dispositivos con pantalla real) ──────
    # EXCLUIR: discos 2.5"/3.5", cables, soportes
    is_disk  = re.search(r'\b[23]\.5\s*["\u201d]\s*(?:SATA|SAS|NVMe|HDD|SSD|\d+Tb|\d+Gb)', n, re.I)
    is_cable = re.search(r'\bcable\b|\blatiguillo\b|\bconector\b', nl)
    is_soporte = re.search(r'\bsoporte\b|\bbrazo\b|\belevador\b', nl)

    if not is_disk and not is_cable and not is_soporte:
        m = re.search(r'(\d{1,2}[.,]\d{1,2})\s*["\u201d]', n)
        if not m: m = re.search(r'(\b(?:11|13|14|15|16|17|18|19|21|22|24|27|28|32|34|43|49|65)\b)\s*["\u201d]', n)
        if m:
            try:
                sz = float(m.group(1).replace(',', '.'))
                # Solo asignar pantalla para rangos que tienen sentido como pantallas
                if 5 <= sz <= 100:
                    # Discos duros usan 2.5" / 3.5" — ya filtrado arriba
                    if sz < 13:      a['pantalla'] = 'Hasta 13 pulg.'
                    elif sz < 14.5:  a['pantalla'] = '13-14 pulg.'
                    elif sz < 15.5:  a['pantalla'] = '14 pulg.'
                    elif sz < 16.5:  a['pantalla'] = '15-16 pulg.'
                    elif sz < 18:    a['pantalla'] = '17 pulg.'
                    elif sz < 23:    a['pantalla'] = '19-22 pulg.'
                    elif sz < 25.5:  a['pantalla'] = '24 pulg.'
                    elif sz < 29:    a['pantalla'] = '27 pulg.'
                    elif sz < 33:    a['pantalla'] = '27-32 pulg.'
                    elif sz >= 33:   a['pantalla'] = '34+ pulg.'
            except: pass

    # ─── PROCESADOR ──────────────────────────────────────────────
    # Orden estricto de búsqueda para evitar confusiones R7↔Ultra7, etc.
    cpu = None
    # 1. Intel Ultra explícito: "Intel Core Ultra 7", "Core Ultra 5"
    m = re.search(r'\bIntel\s+(?:Core\s+)?Ultra\s*([579])[\s-]|\bCore\s+Ultra\s*([579])\b', n, re.I)
    if m: cpu = f"Intel Ultra {(m.group(1) or m.group(2))}"
    # 2. U5/U7/U9 abreviado (NO al final de número: 3700U no cuenta)
    if not cpu:
        m = re.search(r'(?<![0-9A-Za-z])(U[579])-\d{3}', n, re.I)
        if m: cpu = f"Intel Ultra {m.group(1)[1]}"
    # 3. "Ultra 7", "Ultra5" standalone
    if not cpu:
        m = re.search(r'\bUltra\s*([579])\b', n, re.I)
        if m: cpu = f"Intel Ultra {m.group(1)}"
    # 4. Intel i3/i5/i7/i9
    if not cpu:
        m = re.search(r'\b(i[3579])[\s-]\d{2,5}|\bCore\s+(i[3579])\b|\b(i[3579])\s+\d+Gb', n, re.I)
        if m:
            g = m.group(1) or m.group(2) or m.group(3)
            cpu = g[:2].upper()
    # 5. AMD Ryzen explícito: "Ryzen 5", "Ryzen5"
    if not cpu:
        m = re.search(r'\bRyzen\s*([3579])\b', n, re.I)
        if m: cpu = f"AMD Ryzen {m.group(1)}"
    # 6. AMD Ryzen abreviado: "R7-3700U", "R5 8Gb"
    if not cpu:
        m = re.search(r'(?<![A-Za-z])(R[3579])[-\s]\d', n, re.I)
        if m: cpu = f"AMD Ryzen {m.group(1)[1]}"
    # 7. Intel N-series (Atom): N4020, N100
    if not cpu:
        m = re.search(r'\b(N\d{3,4}[A-Z]?)\b', n, re.I)
        if m: cpu = 'Intel N-series'
    # 8. Celeron, Pentium
    if not cpu:
        m = re.search(r'\b(Celeron|Pentium)\b', n, re.I)
        if m: cpu = m.group(1).capitalize()
    # 9. Intel Core nuevo (sin "i"): "7-150U", "5-120U", "7-240H", "7-1355U"
    if not cpu:
        m = re.search(r'(?<![A-Za-z0-9])([579])-(\d{3,4}[A-Z]{0,2})', n, re.I)
        if m: cpu = f"I{m.group(1)}"  # normalizar como I5/I7/I9
    # 10. Intel Ultra abreviado: " U5 ", " U7 ", " U9 " (standalone sin guión)
    if not cpu:
        m = re.search(r'(?<![A-Z0-9])(U[579])(?=\s+\d|\s+[A-Z])', n, re.I)
        if m: cpu = f"Intel Ultra {m.group(1)[1]}"
    # 11. AMD Ryzen con guión: "R9-HX370", "R9-7945HX"
    if not cpu:
        m = re.search(r'(?<![A-Za-z])(R[3579])[-]', n, re.I)
        if m: cpu = f"AMD Ryzen {m.group(1)[1]}"
    # 12. Intel i9 con prefijo typo: "1i9-13900H"
    if not cpu:
        m = re.search(r'1?(i[3579])[-]\d', n, re.I)
        if m: cpu = m.group(1)[:2].upper()
    # 13. Apple Silicon M1/M2/M3/M4
    if not cpu:
        m = re.search(r'\b(M[1-4])(?:\s+(?:Pro|Max|Ultra))?\b', n, re.I)
        if m and m.group(0).upper().startswith('M'):
            cpu = f"Apple {m.group(0).strip()}"
    # 14. Apple MacBook sin chip explícito → "Apple M1" genérico
    if not cpu and 'macbook' in nl:
        cpu = 'Apple M1'
    # 15. Qualcomm Snapdragon X / otros ARM
    if not cpu:
        m = re.search(r'\b(Snapdragon|X1[EHP]|MediaTek|Dimensity|Exynos)\b', n, re.I)
        if m:
            if 'X1' in m.group(1).upper() or 'Snapdragon' in m.group(1):
                cpu = 'Snapdragon X'
            else:
                cpu = m.group(1).capitalize()
    if cpu: a['procesador'] = cpu


            # ─── MEMORIA RAM ─────────────────────────────────────────────
    ram_val = None
    _is_phone = 'smartphone' in cat or 'iphone' in nl or 'movil' in cat
    _is_laptop = 'portatil' in cat or 'notebook' in cat or 'ordenador' in cat

    # 1. Explícito: "16Gb RAM", "DDR4 8Gb"
    m = re.search(r'(\d+)\s*Gb\s+(?:RAM|DDR\d|LPDDR\d|SODIMM)', n, re.I)
    if not m: m = re.search(r'(?:RAM|DDR\d|LPDDR\d)\s+(\d+)\s*Gb', n, re.I)
    # 2. CPU con guión + RAM: "i7-13700H 16Gb", "i5-12450HX 24Gb", "R9-HX370 32Gb"
    # 2a. CPU hyphenated model then RAM: "i7-13700H 16Gb", "i5-12450HX 24Gb"
    if not m: m = re.search(
        r'(?:i[3579]-[\w]+|R[3579]-[\w]+)\s+(\d+)\s*Gb', n, re.I)
    # 2b. CPU direct then RAM: "i5 16Gb", "R7 8Gb", "N100 8Gb", "M2 8Gb"
    if not m: m = re.search(
        r'(?:i[3579]\b|Ultra\s*[579]|U[579]-\S+|Ryzen\s+[579]|N\d{3,4}|M[1-4])\s+(\d+)\s*Gb', n, re.I)
    # 3. Intel/AMD nuevo naming sin "i": "7-155H 16Gb", "5-120U 8Gb"
    if not m: m = re.search(r'(?<![A-Za-z0-9])(?:[579]-\d{3,4}[A-Z]{0,2})\s+(\d+)\s*Gb', n, re.I)
    # 4. Después de pulgadas (solo portátiles/tablets, NO smartphones)
    #    EXCLUIR: si ya hay otro Gb antes de las pulgadas (ese otro es RAM real)
    if not m and not _is_phone:
        # Check: is there a Gb before the screen size?
        screen_m = re.search(r'[\d.]+["”]\s+(\d+)\s*Gb', n, re.I)
        if screen_m:
            screen_pos = screen_m.start()
            # Check if there's a Gb value BEFORE screen size that looks like RAM
            before_screen = n[:screen_pos]
            prev_gb = re.findall(r'\b(\d+)\s*Gb\b', before_screen, re.I)
            # If there's already a RAM-like value before screen, the after-screen Gb is VRAM
            has_prior_ram = any(int(v) in (4,6,8,12,16,24,32,48) for v in prev_gb)
            if not has_prior_ram:
                m = screen_m
    # 5. Smartphones Android: patrón "8Gb 256Gb" → primer=RAM, segundo=storage
    if not m and _is_phone and not re.search(r'iPhone|Apple\s+iPhone', n, re.I):
        two_gb = [m for m in re.findall(r'\b(\d+)\s*Gb(?!/\s*s)\b', n, re.I)]
        if len(two_gb) >= 2:
            v1, v2 = int(two_gb[0]), int(two_gb[1])
            if v1 <= 24 and v2 >= 32:
                ram_val = v1
                if 'almacenamiento' not in a:
                    if v2 <= 64:    a['almacenamiento'] = '64 GB'
                    elif v2 <= 128: a['almacenamiento'] = '128 GB'
                    elif v2 <= 256: a['almacenamiento'] = '256 GB'
                    elif v2 <= 512: a['almacenamiento'] = '512 GB'
                    else:           a['almacenamiento'] = '1 TB+'
    # 6. Fallback portátiles/PCs: primer Gb que no sea VRAM de GPU o storage
    if not m and not _is_phone and ram_val is None:
        candidates = []
        for cap in re.finditer(r'\b(\d+)\s*Gb\b', n, re.I):
            v = int(cap.group(1))
            after = n[cap.end():cap.end()+30]
            before = n[max(0,cap.start()-5):cap.start()]
            # Skip if followed by GPU name or preceded by storage context
            is_vram = bool(re.search(r'(RTX|GTX|RX\s|MX\d|Iris|Radeon|GeForce|Frame|VRAM|Arc\s)', after, re.I))
            # Skip speed notation: "6Gb/s" (disk interface speed, not memory)
            is_speed = bool(re.search(r'^\s*/\s*s', after, re.I))
            # Skip large values that are clearly storage (256Gb, 512Gb, 1Tb)
            is_stor = v >= 128
            if v in (2,3,4,6,8,12,16,24,32,48,64,96) and not is_vram and not is_speed and not is_stor:
                candidates.append((cap.start(), v))
        # For portátiles: prefer >4 GB to avoid picking up 4Gb GPU VRAM
        preferred = [c for c in candidates if c[1] > 4] if _is_laptop else candidates
        if preferred: ram_val = preferred[0][1]
        elif candidates: ram_val = candidates[0][1]
    if m: ram_val = int(m.group(1))
    if ram_val:
        if ram_val <= 4:    a['ram'] = '4 GB'
        elif ram_val <= 6:  a['ram'] = '6 GB'
        elif ram_val <= 8:  a['ram'] = '8 GB'
        elif ram_val <= 12: a['ram'] = '12 GB'
        elif ram_val <= 16: a['ram'] = '16 GB'
        elif ram_val <= 24: a['ram'] = '24 GB'
        elif ram_val <= 32: a['ram'] = '32 GB'
        else:               a['ram'] = '64 GB o más'

    # ─── ALMACENAMIENTO ───────────────────────────────────────────
    # Orden: nombre explícito > CPU+RAM+storage > SSD solo > Tb genérico
    stor_val = None; stor_unit = None
    # 1. "512Gb SSD", "1Tb NVMe", "250Gb SATA3"
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(Tb|Gb)\s*(?:SSD|HDD|NVMe|eMMC|EMMC|NAND|Flash|M\.2|SATA|mSATA|V-NAND)', n, re.I)
    if not m: m = re.search(r'SSD[^\d]*(\d+(?:[.,]\d+)?)\s*(Gb|Tb)', n, re.I)
    if not m: m = re.search(r'NVMe[^\d]*(\d+(?:[.,]\d+)?)\s*(Gb|Tb)', n, re.I)
    # 2. CPU+RAM+storage: "i5 16Gb 512Gb" — segundo número Gb grande
    if not m and 'ram' in a:
        m = re.search(r'\b\d+\s*Gb\s+(\d+(?:[.,]\d+)?)\s*(Gb|Tb)\b', n, re.I)
    # 3. "2Tb" standalone (para HDDs donde no hay keyword)
    if not m:
        for mc in re.finditer(r'\b(\d+(?:[.,]\d+)?)\s*(Tb|Gb)\b', n, re.I):
            v = float(mc.group(1).replace(',','.'))
            unit = mc.group(2).upper()
            gb_equiv = v * 1000 if unit == 'TB' else v
            # Capacidades típicas de disco: >= 128GB y no coincide con RAM ya extraída
            # For phones: 32GB+; for others: 120GB+
            _min_gb = 32 if ('smartphone' in cat or 'iphone' in nl or 'tablet' in cat) else 120
            if gb_equiv >= _min_gb and not (a.get('ram') and str(int(v)) + ' GB' == a.get('ram','')):
                m = mc; break
    if m:
        stor_val = float(m.group(1).replace(',','.'))
        stor_unit = m.group(2).upper()
        if stor_unit == 'TB': stor_gb = stor_val * 1000
        else:                 stor_gb = stor_val
        # For phones/tablets: 32GB+ is storage. For laptops: 64GB+ 
        _min_stor = 32 if ('smartphone' in cat or 'iphone' in nl or 'tablet' in cat) else 64
        if stor_gb < _min_stor:   pass  # demasiado pequeño
        elif stor_gb < 128:    a['almacenamiento'] = '64 GB'
        elif stor_gb < 256:    a['almacenamiento'] = '128 GB'
        elif stor_gb < 512:    a['almacenamiento'] = '256 GB'
        elif stor_gb < 900:    a['almacenamiento'] = '512 GB'
        elif stor_gb < 1500:   a['almacenamiento'] = '1 TB'
        elif stor_gb < 2500:   a['almacenamiento'] = '2 TB'
        elif stor_gb < 4500:   a['almacenamiento'] = '4 TB'
        elif stor_gb < 7000:   a['almacenamiento'] = '6 TB'
        elif stor_gb < 10000:  a['almacenamiento'] = '8 TB'
        else:                  a['almacenamiento'] = '12 TB o más'

        # Tipo de disco
        if re.search(r'\bSSD\b|\bNVMe\b|\bM\.2\b|\beMMC\b|\bmSATA\b|\bV-NAND\b', n, re.I):
            a['tipo_disco'] = 'SSD'
        elif re.search(r'\bHDD\b|\b7200\s*rpm\b|\b5400\s*rpm\b|\bSATA\b|\bSAS\b', n, re.I):
            a['tipo_disco'] = 'HDD'

    # ─── RESOLUCIÓN ───────────────────────────────────────────────
    if re.search(r'\b(4K|UHD|3840|2160p)\b', n, re.I):
        a['resolucion'] = '4K UHD'
    elif re.search(r'\b(QHD|2K|2560|1440p|WQHD|2K)\b', n, re.I):
        a['resolucion'] = 'QHD 2K'
    elif re.search(r'\b(FHD|Full\s*HD|1080p|1920[xX×]1080)\b', n, re.I):
        a['resolucion'] = 'Full HD 1080p'
    elif re.search(r'\b(HD|720p|1280[xX×]720)\b', n, re.I):
        a['resolucion'] = 'HD 720p'

    # ─── TIPO DE PANEL ────────────────────────────────────────────
    if re.search(r'\b(OLED|AMOLED)\b', n, re.I):     a['panel'] = 'OLED'
    elif re.search(r'\bQLED\b', n, re.I):              a['panel'] = 'QLED'
    elif re.search(r'\bIPS\b', n, re.I):               a['panel'] = 'IPS'
    elif re.search(r'\bVA\b', n):                      a['panel'] = 'VA'
    elif re.search(r'\bTN\b', n):                      a['panel'] = 'TN'

    # ─── TASA DE REFRESCO ─────────────────────────────────────────
    m = re.search(r'(\d+)\s*Hz', n, re.I)
    if m:
        hz = int(m.group(1))
        if hz <= 60:    a['refresco'] = '60 Hz'
        elif hz <= 75:  a['refresco'] = '75 Hz'
        elif hz <= 100: a['refresco'] = '100-120 Hz'
        elif hz <= 144: a['refresco'] = '144 Hz'
        elif hz <= 165: a['refresco'] = '165 Hz'
        else:           a['refresco'] = '240 Hz+'

    # ─── SISTEMA OPERATIVO ────────────────────────────────────────
    if any(c in cat for c in ['notebook','portátil','ordenador','barebones',
                               'ordenadores portátiles','ordenadores>']):
        if re.search(r'\bW11\s*P\b|\bWindows\s*11\s*Pro\b|W11PRO', n, re.I):
            a['sistema_op'] = 'Windows 11 Pro'
        elif re.search(r'\bW11[PHS]?\b|\bWindows\s*11\b', n, re.I):
            a['sistema_op'] = 'Windows 11'
        elif re.search(r'\bW10\s*P\b|\bWindows\s*10\s*Pro\b|W10PRO', n, re.I):
            a['sistema_op'] = 'Windows 10 Pro'
        elif re.search(r'\bW10[PHS]?\b|\bWindows\s*10\b', n, re.I):
            a['sistema_op'] = 'Windows 10'
        elif re.search(r'\bFreeDos\b|\bFreeD\b|\bFree\s*Dos\b|\bLinux\b|\bUbuntu\b'
                       r'|\bNo[\s-]OS\b|\bNo[\s-]Sistema\b|\bSin\s+AC\b', n, re.I):
            a['sistema_op'] = 'Sin Windows'

    # ─── GRÁFICA DEDICADA (portátiles y ordenadores) ───────────────
    if re.search(r'\bRTX\s*\d{4}|\bRTX\s*\d{3}|\bGTX\s*\d{4}|\bGTX\s*\d{3}'
                 r'|\bRX\s*\d{4}|\bRX\s*\d{3}|\bArcA\d+\b|\bRadeon\s+RX\b'
                 r'|\bA\d{3}\b(?=\s*\d+Gb)\b', n, re.I):
        a['grafica'] = 'Dedicada'

    # ─── CONECTIVIDAD (ratones, teclados, auriculares, tablets…) ──
    has_bt  = bool(re.search(r'\bBluetooth\b|\bBT\s*\d?\b|\bWireless\b|\bInalámb', n, re.I))
    has_rf  = bool(re.search(r'\bRF\b|\b2\.4\s*GHz\b|\bWireless\s+RF\b', n, re.I))
    has_usb = bool(re.search(r'\bUSB[\s-]?[AC]\b|\bUSB[\s-]?2\b|\bUSB[\s-]?3\b', n, re.I))
    has_jack = bool(re.search(r'3\.5\s*mm|\bjack\b', nl))
    has_4g  = bool(re.search(r'\b4G\b|\bLTE\b', n, re.I))
    has_5g  = bool(re.search(r'\b5G\b', n, re.I))

    # Para periféricos de entrada/salida y audio
    peri_cats = ['ratón','raton','teclado','auric','altavoz','webcam',
                 'tablet','smartp','móvil','movil','wearable','smartwatch']
    if any(c in cat for c in peri_cats):
        if has_5g:               a['conectividad'] = '5G'
        elif has_4g:             a['conectividad'] = '4G'
        elif has_bt or has_rf:   a['conectividad'] = 'Inalámbrico'
        elif has_usb or has_jack: a['conectividad'] = 'Con cable'

    # Para auriculares: detallar tipo de conexión
    if 'auric' in cat or 'sonido' in cat:
        if has_bt:               a['tipo_conexion'] = 'Bluetooth'
        elif has_usb:            a['tipo_conexion'] = 'USB'
        elif has_jack:           a['tipo_conexion'] = 'Jack 3.5mm'

    # ─── TIPO IMPRESORA / FORMATO PAPEL / WIFI ────────────────────
    # Usar cat_raw como fuente primaria para tipo_imp (más fiable que el nombre)
    _imp_cat = cat_raw.lower() if cat_raw else ''
    _is_printer = ('impresora' in cat or 'láser' in cat or 'inyección' in cat
                   or 'etiqueta' in _imp_cat or 'rotuladora' in _imp_cat
                   or 'plotter' in _imp_cat or 'matricial' in _imp_cat
                   or 'multif' in nl)

    if _is_printer:
        # ── Determinar tipo_imp por categoría Binary (cat_raw) ──
        if re.search(r'inyecci[oó]n.*multifunci[oó]n|multifunci[oó]n.*inyecci[oó]n|ink.*multi|multi.*ink', _imp_cat):
            a['tipo_imp'] = 'Multifunción'
        elif re.search(r'inyecci[oó]n de tinta|inkjet', _imp_cat):
            # Distinguir impresoras individuales de multifunciones por nombre
            if re.search(r'Multif|Multifunci[oó]n', n, re.I):
                a['tipo_imp'] = 'Multifunción'
            else:
                a['tipo_imp'] = 'Inkjet'
        elif re.search(r'l[aá]ser.*multifunci[oó]n|multifunci[oó]n.*b[/\\\\]?n|multifunci[oó]n.*color', _imp_cat):
            a['tipo_imp'] = 'Multifunción'
        elif re.search(r'impresoras l[aá]ser>(?:monocromo|color)', _imp_cat):
            a['tipo_imp'] = 'Láser'
        elif re.search(r'impresoras l[aá]ser>varios', _imp_cat):
            pass  # Accesorios láser — sin tipo_imp
        elif re.search(r'l[aá]ser', _imp_cat):
            # Cualquier otro láser: asignar por nombre
            if re.search(r'Multif|Multifunci[oó]n', n, re.I):
                a['tipo_imp'] = 'Multifunción'
            else:
                a['tipo_imp'] = 'Láser'
        elif re.search(r'rotuladoras?', _imp_cat):
            a['tipo_imp'] = 'Rotuladora'
        elif re.search(r'etiquetas?', _imp_cat):
            a['tipo_imp'] = 'Térmica'
        elif re.search(r'plotter', _imp_cat):
            if re.search(r'Plotter', n, re.I):
                a['tipo_imp'] = 'Plotter'
        elif re.search(r'matricial', _imp_cat):
            a['tipo_imp'] = 'Matricial'
        elif re.search(r'impresoras 3d', _imp_cat):
            a['tipo_imp'] = '3D'
        elif re.search(r'impresoras tpv|tpv.*impresora', _imp_cat):
            a['tipo_imp'] = 'Térmica'
        else:
            # Fallback: determinar por nombre del producto
            if re.search(r'Multif|Multifunci[oó]n', n, re.I):
                a['tipo_imp'] = 'Multifunción'
            elif re.search(r'Laser[Jj]et|LaserJet|L[aá]ser', n, re.I):
                a['tipo_imp'] = 'Láser'
            elif re.search(r'T[eé]rmica|Termica', n, re.I):
                a['tipo_imp'] = 'Térmica'
            elif re.search(r'Plotter', n, re.I):
                a['tipo_imp'] = 'Plotter'
            elif re.search(r'Rotuladora', n, re.I):
                a['tipo_imp'] = 'Rotuladora'
            elif re.search(r'Matricial|LX-|LQ-', n, re.I):
                a['tipo_imp'] = 'Matricial'
            elif _is_printer:
                a['tipo_imp'] = 'Inkjet'

        # Color / BN (solo para impresoras con tipo_imp asignado)
        if a.get('tipo_imp') and a['tipo_imp'] not in ('Rotuladora','Térmica','3D'):
            if re.search(r'\bColor\b', n, re.I):                   a['color_imp'] = 'Color'
            elif re.search(r'\bB/N\b|\bMono\b|\bMonocromo\b', n, re.I): a['color_imp'] = 'Monocromo'
        # Formato papel (solo para impresoras de papel)
        if a.get('tipo_imp') and a['tipo_imp'] in ('Inkjet','Láser','Multifunción'):
            if re.search(r'\bA3\+?\b', n):    a['formato_papel'] = 'A3'
            elif re.search(r'\bA4\b', n):     a['formato_papel'] = 'A4'
        # WiFi
        if a.get('tipo_imp'):
            if re.search(r'\bWi[\s-]?Fi\b|\bWireless\b|\bWF\b', n, re.I): a['wifi_imp'] = 'Con WiFi'
            # Dúplex
            if re.search(r'\bDúplex\b|\bDuplex\b', n, re.I):      a['duplex'] = 'Dúplex automático'

    # ─── SAI / UPS ────────────────────────────────────────────────
    if 'sai' in cat or 'ups' in cat or re.search(r'\bS\.A\.I\b|\bSAI\b|\bUPS\b', n, re.I):
        m = re.search(r'(\d+)\s*VA\b', n, re.I)
        if m:
            va = int(m.group(1))
            if va <= 600:    a['potencia_va'] = 'Hasta 600 VA'
            elif va <= 1000: a['potencia_va'] = '600-1000 VA'
            elif va <= 1500: a['potencia_va'] = '1000-1500 VA'
            elif va <= 2000: a['potencia_va'] = '1500-2000 VA'
            else:            a['potencia_va'] = 'Más de 2000 VA'

    # ─── MEMORIAS RAM (componentes) ───────────────────────────────
    if 'memori' in cat:
        # tipo_ram: DDR5, DDR4, DDR3L, DDR3, DDR2, DDR, LPDDR5, SDRAM
        m = re.search(r'(LPDDR[45]?|DDR[2-5]L?|DDR)\s*(\d+)?\s*Gb', n, re.I)
        if m:
            raw = m.group(1).upper()
            size_str = m.group(2)
            # Normalize type
            if 'LPDDR5' in raw:      ddr = 'DDR5'
            elif 'LPDDR4' in raw:    ddr = 'DDR4'
            elif 'DDR5' in raw:      ddr = 'DDR5'
            elif 'DDR4' in raw:      ddr = 'DDR4'
            elif 'DDR3' in raw:      ddr = 'DDR3'
            elif 'DDR2' in raw:      ddr = 'DDR2 (legacy)'
            else:                    ddr = 'DDR'
            a['tipo_ram'] = ddr
            if size_str:
                size = int(size_str)
                if size <= 8:    a['capacidad_ram'] = '8 GB o menos'
                elif size <= 16: a['capacidad_ram'] = '16 GB'
                elif size <= 32: a['capacidad_ram'] = '32 GB'
                else:            a['capacidad_ram'] = '64 GB+'
        elif re.search(r'\bSDRAM\b', n, re.I):
            a['tipo_ram'] = 'DDR (legacy)'
        # Also capture size separately if not already found (e.g. "DDR5 32Gb" without match)
        if not a.get('tipo_ram'):
            m2 = re.search(r'(DDR[2-5]?L?)\b', n, re.I)
            if m2: a['tipo_ram'] = m2.group(1).upper().replace('DDR3L','DDR3').replace('DDR2','DDR2 (legacy)')
        if not a.get('capacidad_ram'):
            ms = re.search(r'(\d+)\s*Gb', n, re.I)
            if ms:
                size = int(ms.group(1))
                if size <= 8:    a['capacidad_ram'] = '8 GB o menos'
                elif size <= 16: a['capacidad_ram'] = '16 GB'
                elif size <= 32: a['capacidad_ram'] = '32 GB'
                else:            a['capacidad_ram'] = '64 GB+'
        # Velocidad
        m = re.search(r'(\d{4,5})\s*(?:MHz|Mhz)', n)
        if m:
            mhz = int(m.group(1))
            if mhz <= 2666:   a['velocidad_ram'] = '2666 MHz'
            elif mhz <= 3200: a['velocidad_ram'] = '3200 MHz'
            elif mhz <= 3600: a['velocidad_ram'] = '3600 MHz'
            elif mhz <= 4800: a['velocidad_ram'] = '4800 MHz'
            else:             a['velocidad_ram'] = '5000+ MHz'
        m = re.search(r'(SODIMM|DIMM)', n, re.I)
        if m: a['formato_ram'] = m.group(1).upper()

    # ─── CABLES ───────────────────────────────────────────────────
    _is_cable_cat = 'cable' in cat or 'latiguillo' in nl or 'conector' in nl
    if _is_cable_cat:
        if re.search(r'\bHDMI\b', n, re.I):                           a['tipo_cable'] = 'HDMI'
        elif re.search(r'\bDisplayPort\b|\bDP\b|\bDP/', n, re.I):     a['tipo_cable'] = 'DisplayPort'
        elif re.search(r'\bVGA\b', n, re.I):                           a['tipo_cable'] = 'VGA'
        elif re.search(r'\bDVI\b', n, re.I):                           a['tipo_cable'] = 'DVI'
        elif re.search(r'\bThunderbolt\b', n, re.I):                   a['tipo_cable'] = 'Thunderbolt'
        elif re.search(r'USB[\s-]?C|Type[\s-]?C|USB[\s-]?3\.[12]', n, re.I): a['tipo_cable'] = 'USB-C'
        elif re.search(r'\bUSB\b', n, re.I):                           a['tipo_cable'] = 'USB'
        elif re.search(r'\bRJ45\b|Cat\.?\s*[5678]|FTP|UTP|STP|latiguillo', n, re.I): a['tipo_cable'] = 'Red RJ45'
        elif re.search(r'\bRJ11\b|\bRJ12\b', n, re.I):                a['tipo_cable'] = 'Teléfono RJ11'
        elif re.search(r'3\.5\s*mm|Audio|Jack|Minijack|TRS|XLR', n, re.I): a['tipo_cable'] = 'Audio'
        elif re.search(r'\bOptical\b|\bToslink\b|\bFibra\b', n, re.I): a['tipo_cable'] = 'Óptico'
        elif re.search(r'\bCoaxial\b|\bCoax\b|\bRF\b|F\s*/\s*[FM]|TV.*anten|RG[\s-]?\d', n, re.I): a['tipo_cable'] = 'Coaxial RF'
        elif re.search(r'\bSATA\b|\beSATA\b', n, re.I):               a['tipo_cable'] = 'SATA'
        elif re.search(r'mUSB|Micro[\s-]USB|Mini[\s-]USB', n, re.I):  a['tipo_cable'] = 'USB'
        elif re.search(r'\bSVGA\b|HDB15|VGA.*[MH]-[MH]', n, re.I):  a['tipo_cable'] = 'VGA'
        elif re.search(r'Splitter|Switch.*HDMI|Multiplicador', n, re.I): a['tipo_cable'] = 'HDMI'
        elif re.search(r'\bMolex\b|\bATX\b|\bCPU.*8.?pin|8.*pin.*CPU|\bPCIe\b', n, re.I): a['tipo_cable'] = 'Alimentación PC'
        elif re.search(r'C13|C14|C7|C8|CEE7|Schuko|CA\b|AC\b|IEC\b|alimentaci', n, re.I): a['tipo_cable'] = 'Alimentación'
        elif re.search(r'Micro\s*HDMI|Mini\s*HDMI', n, re.I):         a['tipo_cable'] = 'HDMI'
        elif re.search(r'\bKVM\b', n, re.I):                           a['tipo_cable'] = 'KVM'
        elif re.search(r'serie\b|serial\b|RS[\s-]?232|RS[\s-]?485|COM\b', n, re.I): a['tipo_cable'] = 'Serie'
        elif re.search(r'paralelo|parallel|LPT\b|centronics', n, re.I): a['tipo_cable'] = 'Paralelo'
        elif re.search(r'PS/2|PS2', n, re.I):                         a['tipo_cable'] = 'PS/2'
        # Longitud
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*m\b', nl)
        if m:
            lng = float(m.group(1).replace(',', '.'))
            if lng <= 0.5:   a['longitud'] = '0.5 m'
            elif lng <= 1.0: a['longitud'] = '1 m'
            elif lng <= 2.0: a['longitud'] = '2 m'
            elif lng <= 3.0: a['longitud'] = '3 m'
            elif lng <= 5.0: a['longitud'] = '5 m'
            else:            a['longitud'] = 'Más de 5 m'

    # ─── CABLES RED — categoría de cable ─────────────────────────
    if 'cables red' in cat or 'latiguillos' in cat or 'cable' in cat:
        m = re.search(r'\b(Cat\.?\s*[5-9][a-e]?)\b', n, re.I)
        if m: a['categoria_red'] = m.group(1).upper().replace(' ','')

    # ─── PENDRIVE / MEMORIA FLASH ─────────────────────────────────
    if 'pendrive' in cat or 'microsd' in nl or 'secure digital' in nl:
        m = re.search(r'(\d+)\s*Gb\b', n, re.I)
        if m:
            gb = int(m.group(1))
            if gb <= 32:    a['capacidad'] = '32 GB'
            elif gb <= 64:  a['capacidad'] = '64 GB'
            elif gb <= 128: a['capacidad'] = '128 GB'
            elif gb <= 256: a['capacidad'] = '256 GB'
            elif gb <= 512: a['capacidad'] = '512 GB'
            else:           a['capacidad'] = '1 TB+'
        # USB versión
        if re.search(r'\bUSB[\s-]?3\.[12]\b|\bUSB[\s-]?C\b', n, re.I): a['usb_ver'] = 'USB 3.x'
        elif re.search(r'\bUSB[\s-]?3\b', n, re.I):  a['usb_ver'] = 'USB 3.0'
        elif re.search(r'\bUSB[\s-]?2\b', n, re.I):  a['usb_ver'] = 'USB 2.0'

    # ─── SWITCH / ROUTER ─────────────────────────────────────────
    if 'switch' in cat or 'router' in cat:
        m = re.search(r'(\d+)\s*x\s*RJ45', n, re.I)
        if m:
            ports = int(m.group(1))
            if ports <= 5:    a['puertos'] = '5 puertos'
            elif ports <= 8:  a['puertos'] = '8 puertos'
            elif ports <= 16: a['puertos'] = '16 puertos'
            elif ports <= 24: a['puertos'] = '24 puertos'
            else:             a['puertos'] = '48 puertos'
        if re.search(r'\bPoE\+?\b', n, re.I): a['poe'] = 'Con PoE'
        if re.search(r'\b10\s*G\b|10\s*Gbit|10\s*Gbps', n, re.I): a['velocidad_red'] = '10 Gbps'
        elif re.search(r'\bGb[Ee]\b|\bGigabit\b|\b1000\b', n, re.I): a['velocidad_red'] = 'Gigabit 1G'
        elif re.search(r'\bFast\b|\b100\s*Mbps\b', n, re.I): a['velocidad_red'] = 'Fast 100M'
        # Gestionable
        if re.search(r'\bManaged\b|\bGestionable\b|\bSmart\b', n, re.I): a['gestionable'] = 'Gestionable'

    # ─── COLOR ───────────────────────────────────────────────────
    m = re.search(
        r'\b(Negro|Negra|Blanco|Blanca|Gris|Plata|Plateado|Plateada'
        r'|Rojo|Roja|Azul|Verde|Naranja|Morado|Rosa|Amarillo'
        r'|Dorado|Dorada|Black|White|Silver|Gold)\b', n, re.I)
    if m: a['color'] = m.group(1).capitalize()

    return a or None

    a = {}
    n = name  # mantener case para algunos patrones
    nl = name.lower()
    cat = cat_raw.lower()

    # ─── PANTALLA / TAMAÑO ────────────────────────────────────────
    # Patrones: 15.6", 27", 10.36", 11.6"
    m = re.search(r'(\d{1,2}[.,]\d{1,2})\s*["\u201d]', n)
    if not m: m = re.search(r'(\d{1,2}[.,]\d{1,2})\s*(?:pulg|pulgadas|inch)', nl)
    if not m: m = re.search(r'(\b(?:11|13|14|15|16|17|19|21|22|24|27|28|32|34|43|49|65)\b)\s*["\u201d]', n)
    if m:
        size = m.group(1).replace(',', '.')
        # Normalizar a rangos para no fragmentar demasiado
        try:
            sz = float(size)
            if sz < 13:   a['pantalla'] = 'Hasta 13 pulg.'
            elif sz < 14: a['pantalla'] = '13 pulg.'
            elif sz < 15: a['pantalla'] = '14 pulg.'
            elif sz < 16: a['pantalla'] = '15-16 pulg.'
            elif sz < 17: a['pantalla'] = '16 pulg.'
            elif sz < 18: a['pantalla'] = '17 pulg.'
            elif sz < 23: a['pantalla'] = '19-22 pulg.'
            elif sz < 25: a['pantalla'] = '23-24 pulg.'
            elif sz < 29: a['pantalla'] = '27-28 pulg.'
            elif sz < 33: a['pantalla'] = '32 pulg.'
            elif sz >= 33: a['pantalla'] = '34+ pulg.'
        except: pass

    # ─── PROCESADOR / CHIP ────────────────────────────────────────
    # Intel Core i3/i5/i7/i9, Ryzen 3/5/7/9, Celeron, N-series, M1/M2
    m = re.search(
        r'\b(i3|i5|i7|i9)(?:[-\s]\d+\w*)?\b'
        r'|\b(Ryzen\s+[3579])(?:\s+\d+\w*)?\b'
        r'|\b(Celeron|Pentium|Atom)\b'
        r'|\b(N\d{4}[A-Z]?)\b'          # Intel N4020, N5100...
        r'|\b(Core\s+Ultra\s*[579])\b'
        r'|\b(M[1-4](?:\s+(?:Pro|Max|Ultra))?)\b'
        r'|\b(Snapdragon|MediaTek|Helio|Dimensity)\b'
        r'|\b(A[0-9]{1,2})\s+Bionic\b',
        n, re.I)
    if m:
        chip = next(g for g in m.groups() if g)
        chip = chip.strip()
        # Normalizar familias Intel
        if re.match(r'i[3579]', chip, re.I): a['procesador'] = chip[:2].upper()  # i5
        elif 'Ryzen' in chip: a['procesador'] = 'AMD ' + chip[:9]
        elif chip.startswith('N') and chip[1:].isdigit(): a['procesador'] = 'Intel N-series'
        elif 'Celeron' in chip or 'Pentium' in chip: a['procesador'] = chip.capitalize()
        elif re.match(r'M[1-4]', chip, re.I): a['procesador'] = 'Apple ' + chip
        else: a['procesador'] = chip

    # ─── MEMORIA RAM ──────────────────────────────────────────────
    m = re.search(r'(\d+)\s*Gb\s+(?:RAM|DDR|LPDDR|SODIMM)', n)
    if not m: m = re.search(r'(?:RAM|DDR\d)\s+(\d+)\s*Gb', n, re.I)
    # CPU con número de modelo: i7-1360P 32Gb / R7-3700U 8Gb
    if not m: m = re.search(r'(?:i[3579]|Ryzen|Core|N\d{4}|R[3579]-\d)[\w.-]*\s+(\d+)\s*Gb', n, re.I)
    # Teléfonos/tablets: tamaño" RAMGb (ej: 6.36" 12Gb / 11" 8Gb)
    if not m: m = re.search(r'[\d.]+["\u201d]\s+(\d+)\s*Gb\b', n, re.I)
    if not m: m = re.search(r'\b(\d+)\s*Gb\b(?=.*(?:RAM|DDR|\bW\d\d\b|\bFreeD))', n)
    if m:
        ram = int(m.group(1))
        if ram <= 4:   a['ram'] = '4 GB'
        elif ram <= 8: a['ram'] = '8 GB'
        elif ram <= 16:a['ram'] = '16 GB'
        elif ram <= 32:a['ram'] = '32 GB'
        else:          a['ram'] = '64 GB o más'

    # ─── ALMACENAMIENTO / DISCO ───────────────────────────────────
    m = re.search(r'(\d+(?:\.\d+)?)\s*(Tb|Gb)\s*(?:SSD|HDD|NVMe|eMMC|EMMC|NAND|Flash|M\.2|SATA)', n, re.I)
    if not m: m = re.search(r'(\d+(?:\.\d+)?)\s*(Tb|Gb)SSD', n, re.I)
    if not m: m = re.search(r'SSD[^\d]*(\d+)\s*(Gb|Tb)', n, re.I)
    # Portátiles/Tablets: [CPU] [RAM]Gb [storage]Gb — el segundo número Gb
    if not m: m = re.search(r'(?:i[3579]|Ryzen|Core|N\d{4}|R[3579]-\d)[\w-]*\s+(\d+)\s*Gb\s+(\d+)\s*(Gb|Tb)', n, re.I)
    # Smartphones/tablets: [tamaño"] [RAM]Gb [storage]Gb/Tb
    if not m: m = re.search(r'[\d.]+["\u201d]\s+(\d+)\s*Gb\s+(\d+)\s*(Gb|Tb)', n, re.I)
    # Fallback: si ya tenemos RAM, el siguiente Gb/Tb es el almacenamiento
    if not m and 'ram' in a:
        m = re.search(r'\b\d+\s*Gb\s+(\d+)\s*(Gb|Tb)\b', n, re.I)
    if m:
        cap  = float(m.group(1))
        unit = m.group(2).upper()
        if unit == 'TB': cap_gb = cap * 1000
        else:            cap_gb = cap
        if cap_gb <= 128:    a['almacenamiento'] = '128 GB'
        elif cap_gb <= 256:  a['almacenamiento'] = '256 GB'
        elif cap_gb <= 512:  a['almacenamiento'] = '512 GB'
        elif cap_gb <= 1000: a['almacenamiento'] = '1 TB'
        elif cap_gb <= 2000: a['almacenamiento'] = '2 TB'
        elif cap_gb <= 4000: a['almacenamiento'] = '4 TB'
        else:                a['almacenamiento'] = '6 TB o más'
        # Tipo de disco
        if re.search(r'\bSSD\b|\bNVMe\b|\bM\.2\b|\beMMC\b', n, re.I):
            a['tipo_disco'] = 'SSD'
        elif re.search(r'\bHDD\b|\bSATA\b|\b7200rpm\b|\b5400rpm\b', n, re.I):
            a['tipo_disco'] = 'HDD'

    # ─── RESOLUCIÓN (monitores, webcams, TVs) ─────────────────────
    m = re.search(r'\b(4K|UHD|2160p)\b', n, re.I)
    if m: a['resolucion'] = '4K'
    elif re.search(r'\b(QHD|2K|2560|1440p|WQHD)\b', n, re.I):
        a['resolucion'] = 'QHD 2K'
    elif re.search(r'\b(FHD|Full\s*HD|1080p|1920x1080|FullHD)\b', n, re.I):
        a['resolucion'] = 'Full HD'
    elif re.search(r'\b(HD|720p|1280x720)\b', n, re.I):
        a['resolucion'] = 'HD'

    # ─── TIPO DE PANEL (monitores) ────────────────────────────────
    m = re.search(r'\b(OLED|AMOLED|QLED)\b', n, re.I)
    if m: a['panel'] = m.group(1).upper()
    elif re.search(r'\bIPS\b', n, re.I): a['panel'] = 'IPS'
    elif re.search(r'\bVA\b', n):        a['panel'] = 'VA'
    elif re.search(r'\bTN\b', n):        a['panel'] = 'TN'

    # ─── FRECUENCIA DE REFRESCO (monitores, gaming) ───────────────
    m = re.search(r'(\d+)\s*Hz', n, re.I)
    if m:
        hz = int(m.group(1))
        if hz <= 60:    a['refresco'] = '60 Hz'
        elif hz <= 75:  a['refresco'] = '75 Hz'
        elif hz <= 100: a['refresco'] = '100 Hz'
        elif hz <= 144: a['refresco'] = '144 Hz'
        elif hz <= 165: a['refresco'] = '165 Hz'
        else:           a['refresco'] = '240 Hz+'

    # ─── CONECTIVIDAD / CONEXIÓN ──────────────────────────────────
    conexiones = []
    if re.search(r'\bUSB[\s-]?C\b|USB\s*Type[\s-]?C', n, re.I): conexiones.append('USB-C')
    if re.search(r'\bUSB[\s-]?3\b|USB\s*3\.[01]', n, re.I):      conexiones.append('USB 3.0')
    if re.search(r'\bBluetooth\b|\bBT\b(?!\s*(?:1[89]|[2-9]\d))', n, re.I): conexiones.append('Bluetooth')
    if re.search(r'\bWi[\s-]?Fi\b|\bWireless\b|\bWF\b', n, re.I): conexiones.append('WiFi')
    if re.search(r'\bHDMI\b', n, re.I): conexiones.append('HDMI')
    if re.search(r'\bDisplayPort\b|\bDP\b(?=\s*/M)', n, re.I):   conexiones.append('DisplayPort')
    if re.search(r'\bVGA\b', n, re.I):  conexiones.append('VGA')
    if re.search(r'\bNFC\b', n, re.I):  conexiones.append('NFC')
    if re.search(r'\b4G\b|\bLTE\b', n, re.I): conexiones.append('4G')
    if re.search(r'\b5G\b', n, re.I):   conexiones.append('5G')
    if conexiones and any(c in cat for c in ['raton','ratón','teclado','auric','altavoz','sonido','webcam','tablet','smartp','movil','móvil']):
        # Para periféricos: conectividad como filtro principal
        if 'Bluetooth' in conexiones or 'WiFi' in conexiones or '4G' in conexiones or '5G' in conexiones:
            a['conectividad'] = 'Inalámbrico'
        else:
            a['conectividad'] = 'Con cable'

    # ─── SISTEMA OPERATIVO (portátiles, ordenadores) ──────────────
    if any(c in cat for c in ['notebook','portátil','ordenador','barebones']):
        if re.search(r'\bW11(?:P|H|S)?\b|\bWindows\s*11\b', n, re.I):
            a['sistema_op'] = 'Windows 11'
        elif re.search(r'\bW10(?:P|H|S)?\b|\bWindows\s*10\b', n, re.I):
            a['sistema_op'] = 'Windows 10'
        elif re.search(r'\bFreeDos\b|\bFree\s*Dos\b|\bLinux\b|\bUbuntu\b', n, re.I):
            a['sistema_op'] = 'Sin Windows'
        elif re.search(r'\bNo[\s-]?OS\b|\bNo\s*Sistema\b', n, re.I):
            a['sistema_op'] = 'Sin Windows'

    # ─── TIPO IMPRESORA ───────────────────────────────────────────
    if 'impresora' in cat or 'multif' in nl or 'láser' in cat or 'inyección' in cat:
        if re.search(r'\bMultif\b|\bMultifunción\b', n, re.I): a['tipo_imp'] = 'Multifunción'
        elif re.search(r'\bLáser\b|\bLaser\b', n, re.I):       a['tipo_imp'] = 'Láser'
        elif re.search(r'\bTérmica\b|\bTermica\b', n, re.I):   a['tipo_imp'] = 'Térmica'
        elif re.search(r'\bPlotter\b', n, re.I):                a['tipo_imp'] = 'Plotter'
        else:                                                    a['tipo_imp'] = 'Inkjet'

        if re.search(r'\bA3\b', n): a['formato_papel'] = 'A3'
        else:                       a['formato_papel'] = 'A4'

        if re.search(r'\bWi[\s-]?Fi\b|\bWireless\b', n, re.I): a['wifi_imp'] = 'Con WiFi'

    # ─── CATEGORÍA SAI / UPS ──────────────────────────────────────
    if 'sai' in cat or 'ups' in cat or 'sai' in nl:
        m = re.search(r'(\d+)\s*VA\b', n, re.I)
        if m:
            va = int(m.group(1))
            if va <= 600:   a['potencia'] = 'Hasta 600 VA'
            elif va <= 1000:a['potencia'] = '600-1000 VA'
            elif va <= 1500:a['potencia'] = '1000-1500 VA'
            elif va <= 2000:a['potencia'] = '1500-2000 VA'
            else:           a['potencia'] = 'Más de 2000 VA'

    # ─── CATEGORÍA RAM INTERNA (integración) ─────────────────────
    if 'memori' in cat:
        m = re.search(r'(DDR[45]?)\s*(\d+)\s*Gb', n, re.I)
        if m:
            ddr  = m.group(1).upper()
            size = int(m.group(2))
            a['tipo_ram'] = ddr
            if size <= 8:   a['capacidad_ram'] = '8 GB o menos'
            elif size <= 16:a['capacidad_ram'] = '16 GB'
            elif size <= 32:a['capacidad_ram'] = '32 GB'
            else:           a['capacidad_ram'] = '64 GB+'
        m = re.search(r'(SODIMM|DIMM)', n, re.I)
        if m: a['formato_ram'] = m.group(1).upper()

    # ─── CABLES: tipo de conector ─────────────────────────────────
    if 'cable' in cat or 'latiguillo' in nl:
        if re.search(r'\bHDMI\b', n, re.I):       a['tipo_cable'] = 'HDMI'
        elif re.search(r'\bDisplayPort\b|\bDP\b', n, re.I): a['tipo_cable'] = 'DisplayPort'
        elif re.search(r'\bVGA\b', n, re.I):       a['tipo_cable'] = 'VGA'
        elif re.search(r'\bDVI\b', n, re.I):       a['tipo_cable'] = 'DVI'
        elif re.search(r'\bUSB[\s-]?C\b', n, re.I):a['tipo_cable'] = 'USB-C'
        elif re.search(r'\bRJ45\b|\bCat\.\s*\d', n, re.I): a['tipo_cable'] = 'Red RJ45'
        elif re.search(r'\bJack\b|\b3\.5\s*mm\b', n, re.I):a['tipo_cable'] = 'Audio Jack'
        # Longitud
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*m\b', nl)
        if m:
            lng = float(m.group(1).replace(',','.'))
            if lng <= 0.5:   a['longitud'] = '0.5 m'
            elif lng <= 1:   a['longitud'] = '1 m'
            elif lng <= 2:   a['longitud'] = '2 m'
            elif lng <= 3:   a['longitud'] = '3 m'
            elif lng <= 5:   a['longitud'] = '5 m'
            else:            a['longitud'] = 'Más de 5 m'

    # ─── PENDRIVE / MEMORIA FLASH ─────────────────────────────────
    if 'pendrive' in cat or 'micro sd' in nl or 'microsd' in nl or 'secure digital' in nl:
        m = re.search(r'(\d+)\s*Gb\b', n, re.I)
        if m:
            gb = int(m.group(1))
            if gb <= 32:    a['capacidad'] = '32 GB'
            elif gb <= 64:  a['capacidad'] = '64 GB'
            elif gb <= 128: a['capacidad'] = '128 GB'
            elif gb <= 256: a['capacidad'] = '256 GB'
            else:           a['capacidad'] = '512 GB+'

    # ─── SWITCH / ROUTER ──────────────────────────────────────────
    if 'switch' in cat or 'router' in cat:
        m = re.search(r'(\d+)\s*x\s*RJ45', n, re.I)
        if m: a['puertos'] = m.group(1) + ' puertos'
        if re.search(r'\bPoE\+?\b', n, re.I): a['poe'] = 'Con PoE'
        if re.search(r'\bGb[Ee]\b|\bGigabit\b|\b1000\b', n, re.I): a['velocidad_red'] = 'Gigabit'
        elif re.search(r'\b10G\b|10\s*Gb', n, re.I): a['velocidad_red'] = '10 Gb'
        else: a['velocidad_red'] = '10/100 Mbps'

    # ─── COLOR (si es relevante) ──────────────────────────────────
    m = re.search(
        r'\b(Negro|Negra|Blanco|Blanca|Gris|Plata|Plateado|Plateada'
        r'|Rojo|Roja|Azul|Verde|Naranja|Morado|Rosa|Amarillo'
        r'|Dorado|Dorada|Black|White|Silver|Gold)\b', n, re.I)
    if m: a['color'] = m.group(1).capitalize()

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




# ── Taxonomía de la tienda Binary ────────────────────────────────────────────
# Mapea cada ruta de categoría de Binary a (familia, subfamilia) con nombres
# cortos y claros. Reglas de diseño:
#   - familia    = nombre del icono superior (máx 2-3 palabras)
#   - subfamilia = nombre corto (máx 2 palabras), SIN tamaños ni marcas
#   - Tamaño pantalla, capacidad disco, SO → atributos, NO subfamilias
#   - "Seminuevo", "Varios", "Otros" → absorbidos en subfamilia principal
#   - Categorías no IT (hogar, mascotas, ocio físico) → None = descartado

_FAM_SUB_MAP = [
    # ─── PORTÁTILES ─────────────────────────────────────────────
    ("ordenadores portátiles>notebooks",             ("Portátiles",     "Portátiles")),
    ("ordenadores portátiles>maletines>mochilas",    ("Fundas / Bolsas","Mochilas")),
    ("ordenadores portátiles>maletines>trolley",     ("Fundas / Bolsas","Trolleys")),
    ("ordenadores portátiles>maletines>maletines",   ("Fundas / Bolsas","Maletines")),
    ("ordenadores portátiles>maletines>fundas",      ("Fundas / Bolsas","Fundas portátil")),
    ("ordenadores portátiles>maletines",             ("Fundas / Bolsas","Fundas portátil")),
    ("ordenadores portátiles>accesorios notebook>alimentación", ("Portátiles","Cargadores portátil")),
    ("ordenadores portátiles>accesorios notebook>soportes",     ("Portátiles","Soportes portátil")),
    ("ordenadores portátiles>accesorios notebook",   ("Portátiles",     "Accesorios portátil")),

    # ─── ORDENADORES ────────────────────────────────────────────
    ("ordenadores>ordenadores otras marcas>all in one",   ("Ordenadores","All-in-One")),
    ("ordenadores>ordenadores otras marcas>torre sobremesa",("Ordenadores","Torre sobremesa")),
    ("ordenadores>ordenadores otras marcas>pcs compactos",("Ordenadores","Mini PC")),
    ("ordenadores>ordenadores otras marcas",              ("Ordenadores","Sobremesa")),
    ("ordenadores>ordenadores qi",                        ("Ordenadores","Mini PC")),
    ("ordenadores>barebones",                             ("Ordenadores","Barebone")),
    ("ordenadores>servidores",                            ("Ordenadores","Servidores")),
    ("ordenadores>raspberry",                             ("Ordenadores","Raspberry Pi")),
    ("ordenadores>extensiones de garantía",               None),

    # ─── COMPONENTES ────────────────────────────────────────────
    ("integración>microprocesadores",    ("Componentes","Procesadores")),
    ("integración>placas base",          ("Componentes","Placas base")),
    ("integración>memorias",             ("Componentes","Memorias RAM")),
    ("integración>discos internos>discos ssd m.2",     ("Componentes","SSD M.2")),
    ("integración>discos internos>discos ssd sata",    ("Componentes","SSD SATA")),
    ("integración>discos internos>sata 3.5",           ("Componentes","HDD 3.5\"")),
    ("integración>discos internos>sata 2.5",           ("Componentes","HDD 2.5\"")),
    ("integración>discos internos>discos servidor",    ("Componentes","HDD Servidor")),
    ("integración>discos internos",                    ("Componentes","Discos internos")),
    ("integración>fuentes alimentación",  ("Componentes","Fuentes alimentación")),
    ("integración>carcasas>servidor",     ("Componentes","Carcasas servidor")),
    ("integración>carcasas>carcasas de rack",("Componentes","Rack")),
    ("integración>carcasas",              ("Componentes","Carcasas PC")),
    ("integración>tarjetas gráficas",     ("Componentes","Tarjetas gráficas")),
    ("integración>tarjetas de sonido",    ("Componentes","Tarjetas de sonido")),
    ("integración>refrigeración",         ("Componentes","Refrigeración")),
    ("integración>controladoras",         ("Componentes","Controladoras")),
    ("integración>ópticos",               ("Componentes","Unidades ópticas")),

    # ─── MONITORES ──────────────────────────────────────────────
    ("periféricos>monitores",            ("Monitores","Monitores")),
    ("imagen y sonido>televisores",      ("Monitores","Televisores")),
    ("imagen y sonido>proyectores>pantallas",("Monitores","Pantallas proyección")),
    ("imagen y sonido>proyectores>lámparas", ("Monitores","Lámparas proyector")),
    ("imagen y sonido>proyectores",      ("Monitores","Proyectores")),
    ("imagen y sonido>marcos digitales", ("Monitores","Marcos digitales")),
    ("imagen y sonido>soportes tv",      ("Monitores","Soportes y brazos")),
    ("imagen y sonido>home cinema>barras de sonido",("Audio / Vídeo","Barras de sonido")),
    ("imagen y sonido>home cinema",      ("Audio / Vídeo","Home Cinema")),

    # ─── IMPRESORAS ─────────────────────────────────────────────
    ("impresoras y escáner>impresoras inyección de tinta>impresoras multifunción",("Impresoras","Multifunción tinta")),
    ("impresoras y escáner>impresoras inyección de tinta", ("Impresoras","Inyección de tinta")),
    ("impresoras y escáner>impresoras láser>multifunción b/n",  ("Impresoras","Multifunción láser BN")),
    ("impresoras y escáner>impresoras láser>multifunción color",("Impresoras","Multifunción láser color")),
    ("impresoras y escáner>impresoras láser>color",        ("Impresoras","Láser color")),
    ("impresoras y escáner>impresoras láser>monocromo",    ("Impresoras","Láser monocromo")),
    ("impresoras y escáner>impresoras láser",              ("Impresoras","Láser")),
    ("impresoras y escáner>impresoras matriciales",        ("Impresoras","Matriciales")),
    ("impresoras y escáner>impresoras plotter",            ("Impresoras","Plotter")),
    ("impresoras y escáner>impresoras etiquetas>rotuladoras",("Impresoras","Rotuladoras")),
    ("impresoras y escáner>impresoras etiquetas",          ("Impresoras","Etiquetas")),
    ("impresoras y escáner>impresoras 3d",                 ("Impresoras","Impresoras 3D")),
    ("impresoras y escáner>tabletas digitalizadoras",      ("Periféricos","Tabletas digitalizadoras")),
    ("impresoras y escáner>manualidades",                  ("Impresoras","Manualidades")),
    ("impresoras y escáner>extensión garantía",            None),

    # ─── ESCÁNERES ──────────────────────────────────────────────
    ("impresoras y escáner>escáner>boligrafos digitales",  ("Escáneres","Bolígrafos digitales")),
    ("impresoras y escáner>escáner>escáner de mano",       ("Escáneres","Escáner de mano")),
    ("impresoras y escáner>escáner>escáner de mesa",       ("Escáneres","Escáner de mesa")),
    ("impresoras y escáner>escáner",                       ("Escáneres","Escáneres")),
    ("terminales tpv>escáner>escáner de código de barras", ("TPV / Comercio","Lectores código barras")),
    ("terminales tpv>escáner>lectores de tarjeta",         ("TPV / Comercio","Lectores tarjeta")),
    ("terminales tpv>escáner",                             ("TPV / Comercio","Escáneres TPV")),

    # ─── CONSUMIBLES ────────────────────────────────────────────
    ("consumibles impresión>tintas marcas",      ("Consumibles","Tintas de marca")),
    ("consumibles impresión>tintas genericas",   ("Consumibles","Tintas genéricas")),
    ("consumibles impresión>toner marcas",       ("Consumibles","Tóner de marca")),
    ("consumibles impresión>toner genericos",    ("Consumibles","Tóner genérico")),
    ("consumibles impresión>otros consumibles>cintas",  ("Consumibles","Cintas")),
    ("consumibles impresión>otros consumibles>bobinas", ("Consumibles","Bobinas")),
    ("consumibles impresión>otros consumibles>papel",   ("Consumibles","Papel impresión")),
    ("consumibles impresión>otros consumibles>sellos",  ("Consumibles","Sellos")),
    ("consumibles impresión>otros consumibles",         ("Consumibles","Otros consumibles")),
    ("consumibles varios>pilas y baterías>alcalinas",   ("Consumibles","Pilas alcalinas")),
    ("consumibles varios>pilas y baterías>recargables", ("Consumibles","Pilas recargables")),
    ("consumibles varios>pilas y baterías>cargadores",  ("Consumibles","Cargadores pilas")),
    ("consumibles varios>pilas y baterías",             ("Consumibles","Pilas y baterías")),
    ("consumibles varios>material de oficina>papelería",("Consumibles","Papelería")),
    ("consumibles varios>material de oficina>mochilas", ("Fundas / Bolsas","Mochilas")),
    ("consumibles varios>material de oficina",          ("Consumibles","Material de oficina")),
    ("consumibles varios>consumibles grabación",        ("Consumibles","Cintas grabación")),

    # ─── PERIFÉRICOS ────────────────────────────────────────────
    ("periféricos>ratones>ratones gaming",    ("Periféricos","Ratones gaming")),
    ("periféricos>ratones>ratones notebook",  ("Periféricos","Ratones portátil")),
    ("periféricos>ratones>punteros",          ("Periféricos","Presentadores")),
    ("periféricos>ratones>trackball",         ("Periféricos","Trackball")),
    ("periféricos>ratones",                   ("Periféricos","Ratones")),
    ("periféricos>teclados>teclados gaming",  ("Periféricos","Teclados gaming")),
    ("periféricos>teclados>teclados + ratón", ("Periféricos","Teclado + ratón")),
    ("periféricos>teclados>teclados mini",    ("Periféricos","Teclados mini")),
    ("periféricos>teclados",                  ("Periféricos","Teclados")),
    ("periféricos>hubs y adaptadores>adaptadores bluetooth", ("Periféricos","Adaptadores BT")),
    ("periféricos>hubs y adaptadores>docks - kvm",           ("Periféricos","Docks y KVM")),
    ("periféricos>hubs y adaptadores>hub hdmi",              ("Periféricos","Splitters HDMI")),
    ("periféricos>hubs y adaptadores>hubs usb",              ("Periféricos","Hubs USB")),
    ("periféricos>hubs y adaptadores>adaptadores usb",       ("Periféricos","Adaptadores USB")),
    ("periféricos>hubs y adaptadores",        ("Periféricos","Hubs y adaptadores")),
    ("periféricos>sais y regletas>sais",      ("SAI / UPS",  "SAI / UPS")),
    ("periféricos>sais y regletas>regletas",  ("SAI / UPS",  "Regletas")),
    ("periféricos>sais y regletas",           ("SAI / UPS",  "SAI / UPS")),
    ("periféricos>accesorios streaming",      ("Periféricos","Accesorios streaming")),

    # ─── ALMACENAMIENTO / FLASH ─────────────────────────────────
    ("periféricos>pendrive",                  ("Periféricos","Pendrives")),
    ("periféricos>memoria flash>lectores",    ("Periféricos","Lectores tarjeta")),
    ("periféricos>memoria flash>micro secure digital",("Periféricos","MicroSD")),
    ("periféricos>memoria flash>secure digital",      ("Periféricos","Tarjetas SD")),
    ("periféricos>memoria flash",             ("Periféricos","Memoria flash")),
    ("periféricos>discos y cajas externos>discos ssd externos",("Discos Externos","SSD externo")),
    ("periféricos>discos y cajas externos>discos externos red", ("NAS","NAS")),
    ("periféricos>discos y cajas externos>cajas servidor nas",  ("NAS","Cajas NAS")),
    ("periféricos>discos y cajas externos>discos externos",     ("Discos Externos","HDD externo")),
    ("periféricos>discos y cajas externos>cajas sata 2.5",      ("Discos Externos","Cajas 2.5\"")),
    ("periféricos>discos y cajas externos>cajas sata 3.5",      ("Discos Externos","Cajas 3.5\"")),
    ("periféricos>discos y cajas externos>cajas ssd",           ("Discos Externos","Cajas SSD")),
    ("periféricos>discos y cajas externos",                     ("Discos Externos","Discos externos")),

    # ─── CABLES ─────────────────────────────────────────────────
    ("periféricos>cables>video",              ("Cables",    "Vídeo")),
    ("periféricos>cables>datos",              ("Cables",    "Datos")),
    ("periféricos>cables>alimentación",       ("Cables",    "Alimentación")),
    ("periféricos>cables>televisión",         ("Cables",    "TV / RF")),
    ("periféricos>cables>conectores de red",  ("Cables Red","Conectores")),
    ("periféricos>cables>cables de red",      ("Cables Red","Cables red")),
    ("periféricos>cables>latiguillos de red", ("Cables Red","Latiguillos")),
    ("periféricos>cables",                    ("Cables",    "Cables")),

    # ─── REDES ──────────────────────────────────────────────────
    ("redes y cctv>switch y routers>switch 10gbit",     ("Redes","Switch 10G")),
    ("redes y cctv>switch y routers>switch 10/100/1000",("Redes","Switch Gigabit")),
    ("redes y cctv>switch y routers>switch 10/100",     ("Redes","Switch Fast")),
    ("redes y cctv>switch y routers>routers adsl",      ("Redes","Routers ADSL")),
    ("redes y cctv>switch y routers>routers",           ("Redes","Routers")),
    ("redes y cctv>switch y routers>kvm",               ("Redes","KVM")),
    ("redes y cctv>switch y routers",                   ("Redes","Switches")),
    ("redes y cctv>wifi>puntos de acceso",              ("Redes","Puntos de acceso")),
    ("redes y cctv>wifi>antenas",                       ("Redes","Antenas WiFi")),
    ("redes y cctv>wifi>tarjetas de red wireless",      ("Redes","Tarjetas WiFi")),
    ("redes y cctv>wifi>servidores de impresión",       ("Redes","Servidores impresión")),
    ("redes y cctv>wifi",                               ("Redes","WiFi")),
    ("redes y cctv>lan>powerline",                      ("Redes","Powerline")),
    ("redes y cctv>lan>tarjetas de red",                ("Redes","Tarjetas de red")),
    ("redes y cctv>lan",                                ("Redes","LAN")),
    ("redes y cctv>cámaras ip>cámaras ip wireless",     ("Redes","Cámaras IP WiFi")),
    ("redes y cctv>cámaras ip>cámaras ip lan",          ("Redes","Cámaras IP LAN")),
    ("redes y cctv>cámaras ip",                         ("Redes","Cámaras IP")),
    ("redes y cctv>cctv>cámaras ip",                    ("Redes","Cámaras IP")),
    ("redes y cctv>cctv>videograbadores",               ("Redes","Videograbadores")),
    ("redes y cctv>cctv",                               ("Redes","CCTV")),
    ("redes y cctv>armarios y cajas>armarios",          ("Redes","Armarios rack")),
    ("redes y cctv>armarios y cajas",                   ("Redes","Cajas rack")),
    ("redes y cctv>fax",                                ("Redes","Fax / Módem")),
    ("redes y cctv>cables>cables de red",               ("Cables Red","Cables red")),
    ("redes y cctv>cables>latiguillos de red",          ("Cables Red","Latiguillos")),
    ("redes y cctv>cables>conectores de red",           ("Cables Red","Conectores")),
    ("redes y cctv>cables>video",                       ("Cables",    "Vídeo")),
    ("redes y cctv>cables>datos",                       ("Cables",    "Datos")),
    ("redes y cctv>cables>alimentación",                ("Cables",    "Alimentación")),
    ("redes y cctv>cables",                             ("Cables Red","Cables")),

    # ─── AUDIO / VÍDEO ──────────────────────────────────────────
    ("multimedia>sonido>auriculares y micrófonos",      ("Audio / Vídeo","Auriculares")),
    ("multimedia>sonido>altavoces",                     ("Audio / Vídeo","Altavoces")),
    ("multimedia>sonido>tarjetas de sonido externas",   ("Audio / Vídeo","Tarjetas sonido")),
    ("multimedia>sonido",                               ("Audio / Vídeo","Sonido")),
    ("multimedia>webcam",                               ("Periféricos","Webcams")),
    ("multimedia>sintonizadoras y capturadores",        ("Audio / Vídeo","Capturadoras")),
    ("multimedia>reproductores",                        ("Audio / Vídeo","Reproductores")),
    ("multimedia>discos y cajas multimedia",            ("Audio / Vídeo","Media Players")),
    ("imagen y sonido>fotografía y video>cámaras fotográficas reflex",   ("Cámaras","Réflex")),
    ("imagen y sonido>fotografía y video>cámaras fotográficas compactas",("Cámaras","Compactas")),
    ("imagen y sonido>fotografía y video>cámaras de video",              ("Cámaras","Vídeo")),
    ("imagen y sonido>fotografía y video",              ("Cámaras","Fotografía y vídeo")),

    # ─── SMARTPHONES ────────────────────────────────────────────
    ("telefonía / smartphones>telefonía móvil y smartphones",("Smartphones","Smartphones")),
    ("telefonía / smartphones>accesorios de telefonía móvil>fundas y carcasas",("Fundas / Bolsas","Fundas móvil")),
    ("telefonía / smartphones>accesorios de telefonía móvil>protectores de pantalla",("Smartphones","Protectores pantalla")),
    ("telefonía / smartphones>accesorios de telefonía móvil>smartwatch",   ("Wearables","Smartwatch")),
    ("telefonía / smartphones>accesorios de telefonía móvil>pulseras smartband",("Wearables","Smartband")),
    ("telefonía / smartphones>accesorios de telefonía móvil",              ("Periféricos","Accesorios móvil")),
    ("telefonía / smartphones>telefonía fija e ip>telefonía ip",           ("Telefonía Fija","VoIP")),
    ("telefonía / smartphones>telefonía fija e ip>telefonía fija",         ("Telefonía Fija","Teléfonos fijos")),
    ("telefonía / smartphones>telefonía fija e ip",                        ("Telefonía Fija","Telefonía IP")),

    # ─── TABLETS ────────────────────────────────────────────────
    ("tablets / ebooks>tabletas>apple ipad",            ("Tablets","iPad")),
    ("tablets / ebooks>tabletas>android",               ("Tablets","Tablets Android")),
    ("tablets / ebooks>tabletas",                       ("Tablets","Tablets")),
    ("tablets / ebooks>libros electrónicos",            ("Tablets","eBooks")),
    ("tablets / ebooks>accesorios pad y tablet>alimentación y powerbank",("Power Bank","Power Bank")),
    ("tablets / ebooks>accesorios pad y tablet>fundas pad y tablet",("Fundas / Bolsas","Fundas tablet")),
    ("tablets / ebooks>accesorios pad y tablet>protectores de pantalla",("Tablets","Protectores pantalla")),
    ("tablets / ebooks>accesorios pad y tablet>soportes",("Tablets","Soportes tablet")),
    ("tablets / ebooks>accesorios pad y tablet>stylus",  ("Tablets","Stylus")),
    ("tablets / ebooks>accesorios pad y tablet>teclados y ratones",("Tablets","Teclados para tablet")),
    ("tablets / ebooks>accesorios pad y tablet",         ("Tablets","Accesorios tablet")),
    ("tablets / ebooks>accesorios libros",               ("Tablets","Accesorios eBook")),

    # ─── TPV / COMERCIO ─────────────────────────────────────────
    ("terminales tpv>impresoras tpv",                        ("TPV / Comercio","Impresoras TPV")),
    ("terminales tpv>terminal punto venta>cajones",          ("TPV / Comercio","Cajones portamonedas")),
    ("terminales tpv>terminal punto venta>monitores y display",("TPV / Comercio","Monitores TPV")),
    ("terminales tpv>terminal punto venta>tpv montados",     ("TPV / Comercio","TPV completos")),
    ("terminales tpv>terminal punto venta",                  ("TPV / Comercio","Terminales TPV")),

    # ─── GAMING ─────────────────────────────────────────────────
    ("juegos y consolas>gaming pc>sillas",          ("Gaming","Sillas gaming")),
    ("juegos y consolas>gaming pc>mesas",           ("Gaming","Mesas gaming")),
    ("juegos y consolas>gaming pc>alfombrillas",    ("Gaming","Alfombrillas")),
    ("juegos y consolas>gaming pc>volantes",        ("Gaming","Volantes")),
    ("juegos y consolas>gaming pc>joysticks",       ("Gaming","Joysticks")),
    ("juegos y consolas>gaming pc>mandos",          ("Gaming","Mandos")),
    ("juegos y consolas>gaming pc",                 ("Gaming","Accesorios gaming")),
    ("juegos y consolas>accesorios consolas>nintendo switch",("Gaming","Nintendo Switch")),
    ("juegos y consolas>accesorios consolas>playstation ps5",("Gaming","PlayStation 5")),
    ("juegos y consolas>accesorios consolas>xbox one / series",("Gaming","Xbox")),
    ("juegos y consolas>accesorios consolas",       ("Gaming","Consolas accesorios")),
    ("juegos y consolas>consolas",                  ("Gaming","Consolas")),
    ("juegos y consolas>videojuegos",               None),

    # ─── SOFTWARE ───────────────────────────────────────────────
    ("software esd",                                ("Software","Software digital")),
    ("software>antivirus",                          ("Software","Antivirus")),
    ("software>paquetes office",                    ("Software","Office")),
    ("software>sistemas operativos>windows 11",     ("Software","Windows 11")),
    ("software>sistemas operativos>windows server", ("Software","Windows Server")),
    ("software>sistemas operativos",                ("Software","Sistemas operativos")),
    ("software>utilidades>diseño",                  ("Software","Diseño")),
    ("software>utilidades",                         ("Software","Utilidades")),
    ("servicios",                                   None),

    # ─── DESCARTADOS (fuera ámbito IT) ──────────────────────────
    ("hogar / electrónica consumo>electrodom",      None),
    ("hogar / electrónica consumo>menaje",          None),
    ("hogar / electrónica consumo>mascotas",        None),
    ("hogar / electrónica consumo>cuidado personal",None),
    ("hogar / electrónica consumo>descanso",        None),
    ("hogar / electrónica consumo>ferretería",      None),
    ("hogar / electrónica consumo>minicadenas",     None),
    ("hogar / electrónica consumo>ocio>bicicleta",  None),
    ("hogar / electrónica consumo>ocio>patines",    None),
    ("hogar / electrónica consumo>ocio>ciclomotor", None),
    ("hogar / electrónica consumo>ocio>optica",     None),
    ("hogar / electrónica consumo>ocio",            None),
    ("hogar / electrónica consumo>aire acondicionado",None),
    ("hogar / electrónica consumo>energia",         None),
    ("hogar / electrónica consumo>iluminacion",     None),
    ("hogar / electrónica consumo>gps",             None),
    ("hogar / electrónica consumo>seguridad",       None),
    ("hogar / electrónica consumo>destructoras",    None),
    ("repuestos",                                   None),
]

def get_fam_sub(cat_raw):
    """Devuelve (familia, subfamilia) limpias para una ruta de categoría de Binary.
    Prueba prefijos de más largo a más corto. Devuelve None si está descartada.
    """
    if not cat_raw:
        return None
    key = cat_raw.strip().lower()
    for prefix, result in _FAM_SUB_MAP:
        if key.startswith(prefix):
            return result
    return None


def categorize_binary(cat_raw):
    """Devuelve código interno de categoría o None si hay que descartar."""
    result = get_fam_sub(cat_raw)
    if result is None:
        return None
    fam = result[0].lower()
    _codes = {
        'portátiles':'portatiles','ordenadores':'ordenadores','componentes':'componentes',
        'monitores':'monitores','impresoras':'impresoras','escáneres':'escaneres',
        'consumibles':'consumibles','periféricos':'perifericos','cables':'cables',
        'cables red':'cable_red','redes':'redes','audio / vídeo':'audio',
        'smartphones':'smartphones','tablets':'tablets','discos externos':'disco_externo',
        'nas':'nas','fundas / bolsas':'fundas','power bank':'powerbank',
        'cámaras':'camaras','wearables':'wearables','sai / ups':'sai_ups',
        'tpv / comercio':'tpv','gaming':'gaming','software':'software',
        'telefonía fija':'perifericos',
    }
    for k, v in _codes.items():
        if k in fam:
            return v
    return 'otros'



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
            "s":   cat_raw,          # descripción de categoría completa
            "b":   marca,
            "st":  st,
            "_src": "binary",        # marca interna de origen
            "_cod": codigo,          # código original Binary (para deduplicación)
        }

        # Jerarquía de categorías para navegación — nombres limpios via get_fam_sub
        fam_sub = get_fam_sub(cat_raw)
        if fam_sub:
            p["fam"] = fam_sub[0]
            p["sub"] = fam_sub[1]
            p["s"]   = fam_sub[1]   # p.s es lo que la tienda usa para subfamilias

        if canon_v > 0:  p["c"]   = canon_v
        if st == "stock" and qty > 0:
            p["qty"] = qty
        elif st == "transito" and qty > 0:
            p["tv"]  = qty

        # Imagen directamente desde la URL del CSV
        if img_url:
            p["img"]  = img_url
            p["imgH"] = img_url  # misma URL — el proveedor no da alta resolución separada

        a = extract_attrs(nombre, cat_raw)
        if a: p["a"] = a

        products.append(p)  # ← CRÍTICO: añadir el producto a la lista

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

# ── Navegación jerárquica por familias ────────────────────────
def _build_nav_patch(products):
    """Filtros de atributos v4 — por subfamilia (p.s).
    Detecta la subfamilia activa y muestra solo atributos relevantes para ella.
    Multi-selección OR por atributo. Layout compacto con iconos.
    """
    import json as _j

    # ── Atributos permitidos por SUBFAMILIA (p.s) ─────────────────
    # Primero subfamilia específica; si no hay match, usar categoría
    SUB_ATTRS = {
        # COMPONENTES
        "Memorias RAM":        ["tipo_ram","capacidad_ram","velocidad_ram","formato_ram"],
        "SSD M.2":             ["almacenamiento","tipo_disco"],
        "SSD SATA":            ["almacenamiento","tipo_disco"],
        "HDD 3.5\"":           ["almacenamiento","tipo_disco"],
        "HDD 2.5\"":           ["almacenamiento","tipo_disco"],
        "HDD Servidor":        ["almacenamiento","tipo_disco"],
        "Discos internos":     ["almacenamiento","tipo_disco"],
        "Procesadores":        ["procesador"],
        "Placas base":         [],
        "Tarjetas gráficas":   [],
        "Carcasas PC":         [],
        "Carcasas servidor":   [],
        "Fuentes alimentación":["tipo_disco"],
        "Refrigeración":       [],
        "Controladoras":       [],
        "Unidades ópticas":    [],
        "Tarjetas de sonido":  [],
        "Rack":                [],
        # MONITORES
        "Monitores":           ["pantalla","panel","resolucion","refresco"],
        "Televisores":         ["pantalla","resolucion","panel"],
        "Proyectores":         ["resolucion"],
        "Soportes y brazos":   [],
        "Pantallas proyección": [],
        "Lámparas proyector":  [],
        "Marcos digitales":    [],
        # PORTÁTILES
        "Portátiles":          ["pantalla","procesador","ram","almacenamiento","sistema_op","grafica"],
        "Cargadores portátil": [],
        "Soportes portátil":   [],
        "Accesorios portátil": [],
        # IMPRESORAS
        "Multifunción tinta":     ["color_imp","formato_papel","wifi_imp"],
        "Multifunción láser BN":  ["formato_papel","wifi_imp","duplex"],
        "Multifunción láser color":["color_imp","formato_papel","wifi_imp"],
        "Inyección de tinta":     ["color_imp","formato_papel","wifi_imp"],
        "Láser monocromo":        ["formato_papel","wifi_imp","duplex"],
        "Láser color":            ["color_imp","formato_papel","wifi_imp"],
        "Láser":                  [],
        "Etiquetas":              [],
        "Plotter":                [],
        "Rotuladoras":            [],
        "Matriciales":            [],
        # REDES
        "Switch Gigabit":      ["puertos","poe"],
        "Switch 10G":          ["puertos","poe"],
        "Switch Fast":         ["puertos"],
        "Switches":            ["puertos","poe","velocidad_red"],
        "Routers":             [],
        "Puntos de acceso":    [],
        "WiFi":                [],
        "Tarjetas WiFi":       ["conectividad"],
        "Powerline":           ["velocidad_red"],
        "Armarios rack":       [],
        "Cajas rack":          [],
        "Cámaras IP WiFi":     [],
        "Cámaras IP LAN":      [],
        "Cámaras IP":          [],
        "CCTV":                [],
        # PERIFÉRICOS
        "Ratones":             ["conectividad"],
        "Ratones gaming":      ["conectividad"],
        "Ratones portátil":    ["conectividad"],
        "Teclados":            ["conectividad"],
        "Teclados gaming":     ["conectividad"],
        "Teclado + ratón":     ["conectividad"],
        "Pendrives":           ["capacidad","usb_ver"],
        "MicroSD":             ["capacidad"],
        "Tarjetas SD":         ["capacidad"],
        "Memoria flash":       ["capacidad"],
        "Lectores tarjeta":    [],
        "Webcams":             ["resolucion"],
        "Hubs USB":            [],
        "Adaptadores USB":     [],
        "Accesorios streaming":["conectividad"],
        "Accesorios móvil":    [],
        "Docks y KVM":         [],
        "Presentadores":       ["conectividad"],
        # AUDIO
        "Auriculares":         ["tipo_conexion"],
        "Altavoces":           ["conectividad"],
        "Sonido":              ["tipo_conexion"],
        "Barras de sonido":    ["conectividad"],
        "Media Players":       [],
        "Capturadoras":        [],
        # TABLETS
        "Tablets Android":     ["pantalla","ram","almacenamiento","conectividad"],
        "Tablets":             ["pantalla","ram","almacenamiento","conectividad"],
        "iPad":                ["pantalla","ram","almacenamiento"],
        "eBooks":              [],
        "Accesorios tablet":   [],
        # SMARTPHONES
        "Smartphones":         ["pantalla","ram","almacenamiento","conectividad"],
        # SAI/UPS
        "SAI / UPS":           ["potencia_va"],
        "Regletas":            [],
        # DISCOS EXTERNOS
        "HDD externo":         ["almacenamiento"],
        "SSD externo":         ["almacenamiento"],
        "Cajas 2.5\"":         [],
        "Cajas 3.5\"":         [],
        "Cajas SSD":           [],
        "Discos externos":     ["almacenamiento"],
        # CABLES
        "Vídeo":               ["tipo_cable","longitud"],
        "Datos":               ["tipo_cable","longitud"],
        "Alimentación":        ["longitud"],
        "TV / RF":             ["longitud"],
        # CABLES RED
        "Latiguillos":         ["longitud","categoria_red"],
        "Cables red":          ["longitud","categoria_red"],
        "Conectores":          [],
        # NAS
        "Cajas NAS":           [],
        "NAS":                 ["almacenamiento","tipo_disco"],
        # TPV
        "Impresoras TPV":      [],
        "Lectores código barras":[],
        "Terminales TPV":      [],
        "Monitores TPV":       ["pantalla"],
        # FUNDAS
        "Fundas portátil":     ["pantalla"],
        "Fundas tablet":       ["pantalla"],
        "Fundas móvil":        [],
        "Mochilas":            [],
        "Maletines":           [],
        # ORDENADORES
        "Torre sobremesa":     ["procesador","ram","almacenamiento","tipo_disco","sistema_op"],
        "All-in-One":          ["procesador","ram","almacenamiento","sistema_op","pantalla"],
        "Mini PC":             ["procesador","ram","almacenamiento","tipo_disco","sistema_op"],
        "Sobremesa":           ["procesador","ram","almacenamiento","tipo_disco","sistema_op"],
        "Barebone":            ["procesador","ram"],
        "Servidores":          ["procesador","ram","almacenamiento"],
        # WEARABLES
        "Smartwatch":          ["conectividad"],
        "Smartband":           ["conectividad"],
        # SOFTWARE
        "Antivirus":           [],
        "Office":              [],
        "Windows 11":          [],
        "Windows Server":      [],
    }

    # Fallback por categoría cuando no hay subfamilia específica
    CAT_ATTRS_FALLBACK = {
        "portatiles":    ["pantalla","procesador","ram","almacenamiento","sistema_op","grafica"],
        "ordenadores":   ["procesador","ram","almacenamiento","tipo_disco","sistema_op"],
        "monitores":     ["pantalla","panel","resolucion","refresco"],
        "componentes":   ["almacenamiento","tipo_disco","procesador","tipo_ram","capacidad_ram"],
        "impresoras":    ["tipo_imp","color_imp","formato_papel"],
        "escaneres":     [],
        "cables":        ["tipo_cable","longitud"],
        "cable_red":     ["longitud","categoria_red"],
        "redes":         ["puertos","velocidad_red","poe"],
        "tablets":       ["pantalla","ram","almacenamiento","conectividad"],
        "smartphones":   ["pantalla","ram","almacenamiento","conectividad"],
        "audio":         ["tipo_conexion","conectividad"],
        "perifericos":   ["conectividad","capacidad","usb_ver"],
        "sai_ups":       ["potencia_va"],
        "tpv":           ["tipo_imp","pantalla"],
        "disco_externo": ["almacenamiento"],
        "nas":           ["almacenamiento"],
        "wearables":     ["conectividad"],
        "camaras":       ["resolucion"],
        "fundas":        ["pantalla"],
        "powerbank":     ["capacidad"],
        "gaming":        ["conectividad"],
        "consumibles":   [],
        "software":      [],
    }

    # Construir AIDX por subfamilia Y por categoría
    def build_aidx_for(prods, allowed_keys):
        raw = {}
        for p in prods:
            if not p.get("a"): continue
            for k, v in p["a"].items():
                if k not in allowed_keys: continue
                if not v or k == "color": continue
                v = str(v).replace('"','').replace("'",'').replace('\\\\','')
                if k not in raw: raw[k] = set()
                raw[k].add(v)
        result = {}
        for k in allowed_keys:
            if k not in raw: continue
            vals = [v for v in raw[k] if v]
            if len(vals) < 2: continue
            def sk(x):
                try:
                    n = float("".join(c for c in x if c.isdigit() or c==".") or "999")
                    return (0,n) if x and x[0].isdigit() else (1,x)
                except: return (1,x)
            result[k] = sorted(vals, key=sk)
        return result

    # Índice por subfamilia — incluye subs con [] para que JS sepa no hacer fallback
    sub_idx = {}
    for sub, allowed in SUB_ATTRS.items():
        if not allowed:
            # Sub conocida sin attrs: añadir entrada vacía para que JS no haga fallback
            sub_idx[sub] = {}
            continue
        prods_sub = [p for p in products if p.get("s") == sub]
        if not prods_sub:
            sub_idx[sub] = {}  # sub conocida, sin productos: también vacía
            continue
        idx = build_aidx_for(prods_sub, allowed)
        sub_idx[sub] = idx  # puede ser vacío si no hay >=2 valores

    # Índice por categoría (fallback)
    cat_idx = {}
    for cat, allowed in CAT_ATTRS_FALLBACK.items():
        if not allowed: continue
        prods_cat = [p for p in products if p.get("cat") == cat]
        if not prods_cat: continue
        idx = build_aidx_for(prods_cat, allowed)
        if idx: cat_idx[cat] = idx

    SUB_IDX_JSON = _j.dumps(sub_idx, ensure_ascii=True, separators=(",",":"))
    CAT_IDX_JSON = _j.dumps(cat_idx, ensure_ascii=True, separators=(",",":"))

    lbl = {
        "pantalla":"Pantalla","procesador":"Procesador","ram":"RAM",
        "almacenamiento":"Disco","tipo_disco":"Tipo disco","sistema_op":"Sistema op.",
        "grafica":"Grafica","resolucion":"Resolucion","panel":"Panel",
        "refresco":"Refresco","conectividad":"Conectividad",
        "tipo_conexion":"Conexion","tipo_cable":"Tipo cable","longitud":"Longitud",
        "categoria_red":"Categoria","puertos":"Puertos","velocidad_red":"Velocidad",
        "poe":"PoE","gestionable":"Gestionable","tipo_imp":"Tipo",
        "color_imp":"Color","formato_papel":"Papel","wifi_imp":"WiFi",
        "potencia_va":"Potencia","capacidad":"Capacidad",
        "usb_ver":"USB","tipo_ram":"Tipo RAM","capacidad_ram":"Capacidad",
        "velocidad_ram":"Velocidad","formato_ram":"Formato",
    }
    icons = {
        "pantalla":"📐","procesador":"⚙️","ram":"🧠","almacenamiento":"💾",
        "tipo_disco":"💿","sistema_op":"🖥️","grafica":"🎮","resolucion":"🔭",
        "panel":"🎨","refresco":"⚡","conectividad":"📶","tipo_conexion":"🔌",
        "tipo_cable":"🔗","longitud":"📏","categoria_red":"🌐","puertos":"🔌",
        "velocidad_red":"🚀","poe":"⚡","gestionable":"⚙️","tipo_imp":"🖨️",
        "color_imp":"🎨","formato_papel":"📄","wifi_imp":"📶","potencia_va":"⚡",
        "capacidad":"💾","usb_ver":"🔌","tipo_ram":"🧠","capacidad_ram":"🧠",
        "velocidad_ram":"🚀","formato_ram":"📦",
    }
    LBL_JSON   = _j.dumps(lbl,   ensure_ascii=True, separators=(",",":"))
    ICONS_JSON = _j.dumps(icons, ensure_ascii=False, separators=(",",":"))

    css = (
        "<style>"
        "#ifx-attrs{margin-top:4px}"
        ".ifx-wrap{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:flex-start}"
        ".ifx-section{min-width:140px;max-width:260px;flex:1 1 140px}"
        ".ifx-lbl{font-size:9px;font-weight:700;color:var(--ink4,#9b9b9b);"
        "text-transform:uppercase;letter-spacing:.06em;"
        "display:flex;align-items:center;gap:3px;margin:0 0 4px}"
        ".ifx-row{display:flex;flex-wrap:wrap;gap:3px}"
        ".ifx-btn{font-size:11px;font-weight:500;padding:2px 9px;"
        "border-radius:20px;border:1.5px solid var(--bdr2,rgba(0,0,0,.13));"
        "background:var(--bg,#fff);color:var(--ink2,#555);"
        "cursor:pointer;white-space:nowrap;transition:.12s;line-height:1.5}"
        ".ifx-btn:hover{border-color:var(--or,#F57008);color:var(--or,#F57008)}"
        ".ifx-btn.on{background:var(--or,#F57008);color:#fff;"
        "border-color:var(--or,#F57008)}"
        "#ifx-sep{height:1px;background:var(--bdr,rgba(0,0,0,.07));margin:8px 0 6px}"
        "</style>"
    )

    js = """<script>
(function(){
  var SUBIDX=__SUBIDX__;
  var CATIDX=__CATIDX__;
  var LBL=__LBL__;
  var ICO=__ICO__;
  var _sel={}, _curCat=null, _curSub=null, _catALL=null, _busy=false, _obs=null, _catBackup={};

  // ── Detectar cat activa (variable global `cat` de la tienda) ──
  function getActiveCat(){
    return (typeof cat!=='undefined'&&cat)?String(cat):null;
  }

  // ── Detectar sub activa: cualquier boton con fondo no-blanco en #sb ────
  // Estrategia robusta: cualquier boton con background != blanco/transparente = activo.
  // No depende del color exacto de la tienda.
  function getActiveSub(){
    var fp=document.getElementById('fpin');
    if(!fp)return null;
    var ifx=document.getElementById('ifx-attrs');
    var all=fp.querySelectorAll('*');
    // Estrategia 1: fondo naranja en elemento con formato "Nombre (N)"
    // Los botones de subfamilia tienen texto que termina en (N) con el count
    for(var i=0;i<all.length;i++){
      var el=all[i];
      if(el===ifx)break;
      if(ifx&&ifx.contains(el))continue;
      var raw=(el.textContent||'').trim();
      if(!raw.match(/[(][0-9]+[)]$/))continue;
      var bg=window.getComputedStyle(el).backgroundColor;
      var m=bg.match(/[0-9]+/g);
      if(m&&m.length>=3&&+m[0]>150&&+m[2]<60){
        var t=raw.replace(/[ ]*[(][0-9]+[)][ ]*$/,'').trim();
        if(t)return t;
      }
    }
    // Estrategia 2: texto blanco en elemento con formato "Nombre (N)"
    for(var j=0;j<all.length;j++){
      var el2=all[j];
      if(el2===ifx)break;
      if(ifx&&ifx.contains(el2))continue;
      var raw2=(el2.textContent||'').trim();
      if(!raw2.match(/[(][0-9]+[)]$/))continue;
      var col=window.getComputedStyle(el2).color;
      if(col&&col.indexOf('255, 255, 255')>=0){
        var t2=raw2.replace(/[ ]*[(][0-9]+[)][ ]*$/,'').trim();
        if(t2)return t2;
      }
    }
    // Estrategia 3: clase CSS .on/.active en elemento con "(N)"
    var sels='.on,.active,[aria-current],[aria-selected=true]';
    var cands=fp.querySelectorAll(sels);
    for(var k=0;k<cands.length;k++){
      var ca=cands[k];
      if(ifx&&ifx.contains(ca))continue;
      var raw3=(ca.textContent||'').trim();
      if(!raw3.match(/[(][0-9]+[)]$/))continue;
      var t3=raw3.replace(/[ ]*[(][0-9]+[)][ ]*$/,'').trim();
      if(t3)return t3;
    }
    return null;
  }

  // ── Obtener índice para cat/sub ────────────────────────────────
  function getRef(c,s){
    // Si sub es conocida en SUBIDX (aunque sea vacía): NO hacer fallback a cat
    if(s&&(s in SUBIDX)){var r=SUBIDX[s];return(r&&Object.keys(r).length)?r:{};}
    // Sub no conocida: usar catidx como fallback
    if(c&&CATIDX[c])return CATIDX[c];
    return null;
  }

  function getCatProds(c){return(window.ALL||[]).filter(function(p){return p.cat===c;});}
  function el(t){return document.createElement(t);}

  // ── Construir HTML de filtros ──────────────────────────────────
  function buildFilters(c,s){
    var ref=getRef(c,s);
    if(!ref)return null;
    var keys=Object.keys(ref);if(!keys.length)return null;
    var sep=el('div');sep.id='ifx-sep';sep.style.cssText='height:1px;background:var(--bdr,rgba(0,0,0,.07));margin:8px 0 6px';
    var wrap=el('div');wrap.className='ifx-wrap';
    keys.forEach(function(ak){
      var vals=ref[ak];if(!vals||!vals.length)return;
      var sel=_sel[ak]||new Set();
      var sec=el('div');sec.className='ifx-section';
      var lbl=el('div');lbl.className='ifx-lbl';
      lbl.innerHTML=(ICO[ak]?'<span>'+ICO[ak]+'</span> ':'')+' '+(LBL[ak]||ak);
      sec.appendChild(lbl);
      var row=el('div');row.className='ifx-row';
      function mkBtn(txt,on,fn){
        var b=el('button');b.className='ifx-btn'+(on?' on':'');
        b.textContent=txt;b.onclick=fn;return b;
      }
      row.appendChild(mkBtn('Todos',!sel||!sel.size,(function(k){return function(){_sel[k]=new Set();applyFilter();};})(ak)));
      vals.forEach(function(v){
        row.appendChild(mkBtn(v,sel&&sel.has(v),(function(k,vv){return function(){
          if(!_sel[k])_sel[k]=new Set();
          if(_sel[k].has(vv))_sel[k].delete(vv);else _sel[k].add(vv);
          applyFilter();
        };})(ak,v)));
      });
      sec.appendChild(row);wrap.appendChild(sec);
    });
    var cont=el('div');cont.appendChild(sep);cont.appendChild(wrap);
    return cont;
  }

  // ── Inyectar filtros en #fpin ──────────────────────────────────
  function inject(){
    var fp=document.getElementById('fpin');if(!fp)return;
    if(_obs)_obs.disconnect();
    var c=getActiveCat();
    var s=getActiveSub();
    // Reset sel cuando cambia cat o sub
    if(c!==_curCat||s!==_curSub){
      _curCat=c;_curSub=s;_sel={};_passIds=null;
      // _catALL = full category from backup (not filtered window.ALL!)
      if(c&&_catBackup[c]){ _catALL=_catBackup[c]; }
      else{ _catALL=c?(window.ALL||[]).filter(function(p){return p.cat===c;}):null; }
    }
    
    
    var old=document.getElementById('ifx-attrs');
    if(old&&old.parentNode)old.parentNode.removeChild(old);
    var html=buildFilters(c,s);
    if(html){var cont=el('div');cont.id='ifx-attrs';cont.appendChild(html);fp.appendChild(cont);}
    
    if(_obs)_obs.observe(fp,{childList:true});
  }

  // ── Aplicar filtros de atributos ───────────────────────────────
  // IDs de productos que pasan el filtro actual (null = todos)
  var _passIds = null;

  function applyFilter(){
    if(!_catALL||!_curCat)return;
    _busy=true;
    var ks=Object.keys(_sel).filter(function(k){return _sel[k]&&_sel[k].size;});
    var base=_curSub?_catALL.filter(function(p){return p.s===_curSub;}):_catALL;
    if(ks.length){
      var pass=base.filter(function(p){
        if(!p.a)return false;
        return ks.every(function(k){return _sel[k].has(p.a[k]);});
      });
      _passIds=new Set(pass.map(function(p){return (p.id||'').toLowerCase();}));
      // Modify window.ALL so pagination also shows correct products
      var other=(window.ALL||[]).filter(function(p){return p.cat!==_curCat;});
      window.ALL=other.concat(pass);
    }else{
      _passIds=null;
      // Restore full category to window.ALL
      if(_catBackup[_curCat]){
        var other2=(window.ALL||[]).filter(function(p){return p.cat!==_curCat;});
        window.ALL=other2.concat(_catBackup[_curCat]);
      }
    }
    if(typeof applyAll==='function')applyAll();
    _busy=false;
    inject();
  }

  // cardId: extrae ID del producto de una tarjeta del grid
  function cardId(card){
    var ref=card.querySelector('.cref,.card-ref,[class*=ref]');
    if(ref){var id=(ref.textContent||'').trim().toLowerCase();if(id)return id;}
    var did=card.getAttribute('data-id')||card.getAttribute('data-ref')||card.getAttribute('data-sku');
    if(did)return did.toLowerCase();
    return '';
  }

  // applyGridFilter: aplica CSS en las tarjetas actuales del grid
  function applyGridFilter(){
    var grid=document.getElementById('grid');
    if(!grid)return;
    var cards=Array.from(grid.querySelectorAll('[class*=card]:not([class*=ifx])'));
    if(!cards.length) cards=Array.from(grid.children);
    var vis=0;
    cards.forEach(function(card){
      if(!_passIds){card.style.display='';vis++;}
      else{
        var id=cardId(card);
        var show=(!id)||_passIds.has(id);
        card.style.display=show?'':'none';
        if(show)vis++;
      }
    });
    // Actualizar contador #shown
    var shEl=document.getElementById('shown');
    if(shEl){
      if(_passIds){
        if(!shEl.getAttribute('data-ifx-orig'))shEl.setAttribute('data-ifx-orig',shEl.textContent);
        shEl.textContent=String(vis);
      }else{
        var orig=shEl.getAttribute('data-ifx-orig');
        if(orig){shEl.textContent=orig;shEl.removeAttribute('data-ifx-orig');}
      }
    }
  }
  // Tag grid cards with IDs after applyAll (for SUBIDX CSS mode)

  // ── MutationObserver en #fpin ──────────────────────────────────
  function startObs(){
    var fp=document.getElementById('fpin');if(!fp){setTimeout(startObs,300);return;}
    _obs=new MutationObserver(function(){
      if(_busy)return;
      clearTimeout(window.__ifxM);window.__ifxM=setTimeout(function(){inject();},220);
    });
    _obs.observe(fp,{childList:true});
  }

  // ── Envolver applyAll si es posible ───────────────────────────
  function tryWrap(){
    if(typeof applyAll!=='function')return;
    var _orig=applyAll;
    try{applyAll=function(){
      if(!_busy){
        var _newSub=getActiveSub();
        var _newCat=getActiveCat();
        var _subChanged=(_newSub!==_curSub)||(_newCat!==_curCat);
        if(_subChanged){
          // Sub/cat changed: clear filter AND restore full category from backup
          _passIds=null; _sel={};
          var _restoreCat=_newCat||_curCat;
          if(_restoreCat&&_catBackup[_restoreCat]){
            var _oth=(window.ALL||[]).filter(function(p){return p.cat!==_restoreCat;});
            window.ALL=_oth.concat(_catBackup[_restoreCat]);
          }
        }
      }
      _orig.apply(this,arguments);
      // Re-apply CSS filter after every applyAll (pagination/sort/sub change)
      if(!_busy){
        applyGridFilter();
        clearTimeout(window.__ifxW);window.__ifxW=setTimeout(inject,90);
      }
    };}catch(e){}
  }

  // ── Polling principal ──────────────────────────────────────────
  // Detecta cambios de cat Y sub, sin depender de eventos
  function startPoll(){
    var lc=null,ls=null;
    setInterval(function(){
      if(_busy)return;
      var c=getActiveCat(),s=getActiveSub();
      if(c!==lc||s!==ls){lc=c;ls=s;clearTimeout(window.__ifxP);window.__ifxP=setTimeout(function(){inject();},120);}
    },400);
  }

  function init(){
    if(!window.ALL||!window.ALL.length){setTimeout(init,300);return;}
    window.ALL.forEach(function(p){if(p.cat){if(!_catBackup[p.cat])_catBackup[p.cat]=[];_catBackup[p.cat].push(p);}});
    startObs();tryWrap();startPoll();
    setTimeout(inject,900);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
</script>"""
    js = js.replace('__SUBIDX__', SUB_IDX_JSON)
    js = js.replace('__CATIDX__', CAT_IDX_JSON)
    js = js.replace('__LBL__', LBL_JSON)
    js = js.replace('__ICO__', ICONS_JSON)

    return css + js


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

          # Estrategia 1: ZA — busca .badge con .badge-lines o .bls
          'function addToBadge(root,d){'
            'if(!root||!root.querySelector)return false;'
            'var b=root.querySelector(".badge");'
            'if(!b)return false;'
            'if(b.querySelector(".badge-qty"))return true;'
            'var t=qtyText(d);if(!t)return true;'
            'var s=document.createElement("span");'
            's.className="badge-qty";s.textContent=t;'
            # .badge-lines = Zona Apple | .bls = Tienda Online
            'var bl=b.querySelector(".badge-lines,.bls");'
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

    # Strategy A: standalone tienda (var ALL directamente en el HTML)
    pat_direct = r'(var ALL = JSON\.parse\((?:new TextDecoder\(\)\.decode\(Uint8Array\.from\(atob\(|atob\()")[A-Za-z0-9+/=]+'
    html2, n = re.subn(
        pat_direct,
        lambda m: m.group(1) + new_prods_b64,
        html, count=1)

    if n > 0:
        log("  Modo: standalone tienda")

        # ── Inyectar nav y stock patch en el HTML principal ────────
        # En Strategy A la tienda está embebida directamente en el HTML
        # (no en _TG), así que el iframe #f-tienda carga el mismo documento.
        # Buscamos </body> del documento principal o añadimos al final.
        nav_patch = _build_nav_patch(products)

        # Stock patch para Strategy A
        to_stock_a = {}
        for p in products:
            pid2 = p.get('id','').strip().lower()
            st   = p.get('st','')
            if st == 'stock' and p.get('qty', 0) > 0:
                to_stock_a[pid2] = {'st': 'stock',    'qty': p['qty']}
            elif st == 'transito' and p.get('tv', 0) > 0:
                to_stock_a[pid2] = {'st': 'transito', 'net': p['tv']}
        to_patch_a = _build_stock_patch(to_stock_a, 'TO')

        combined = nav_patch + to_patch_a
        if '</body>' in html2:
            html2 = html2.replace('</body>', combined + '</body>', 1)
        else:
            html2 += combined
        log(f"  Tienda A: nav + stock patch inyectados ({len(to_stock_a)} prods)")

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

        # ── Inyectar navegación por familias ─────────────────────
        nav_patch = _build_nav_patch(products)
        if '</body>' in tg_fixed:
            tg_fixed = tg_fixed.replace('</body>', nav_patch + '</body>', 1)
        else:
            tg_fixed += nav_patch
        log(f"  Tienda: navegación por familias inyectada")

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
    # La tienda online muestra SOLO productos de Binary.
    # Megastore sigue descargándose únicamente para actualizar la Zona Apple.
    binary_text = download_binary_csv()
    if binary_text:
        binary_products = process_binary_csv(binary_text)
        if binary_products:
            products = binary_products  # Tienda = solo Binary
            log(f"  Tienda: usando solo Binary ({len(products)} productos)")
        else:
            log("  AVISO: Binary CSV sin productos — manteniendo Megastore en tienda")
    else:
        if BINARY_CSV_URL:
            log("  AVISO: No se pudo descargar el CSV de Binary")
        else:
            log("  Binary CSV: BINARY_CSV_URL no configurado — tienda mostrará Megastore")

    # Save updated cache
    save_img_cache(img_cache)
    log(f"  Cache guardado: {len(img_cache)} entradas")

    if not update_html(products): sys.exit(1)

    log("Completado OK")
    log("=" * 50)

if __name__ == "__main__":
    main()
