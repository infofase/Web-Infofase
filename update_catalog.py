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
        # "tv": unidades netas próximas (qty+viajando) — sólo cuando hay tránsito
        # Ejemplo: stock=-1, viajando=2 → tv=1 → web muestra "1 unidad próxima entrada"
        if st == "transito":
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
    new_all     = json.dumps(za_products, ensure_ascii=False, separators=(',',':'))

    # ── Construir diccionario de stock para el script patch ───────
    # Contiene solo los productos con unidades relevantes (stock>0 o transito>0)
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

    # ── Script patch: inyecta window.__ZA_STOCK y parchea los badges ──
    # El ZA es un iframe con JS dinámico — no podemos ver el código del render,
    # así que usamos MutationObserver para interceptar las tarjetas al crearlas
    # y añadirles una segunda línea en el badge con las unidades.
    # CSS .badge-qty: texto pequeño debajo de la etiqueta de estado.
    patch_css = (
        '<style>'
        '.badge-qty{font-size:8.5px;font-weight:500;opacity:.85;margin-top:1px}'
        '.badge.st .badge-qty{color:#1D4ED8}'
        '.badge.tr .badge-qty{color:#C2410C}'
        '</style>'
    )
    stock_json = json.dumps(stock_data, ensure_ascii=False, separators=(',',':'))
    patch_js = (
        '<script>'
        'window.__ZA_STOCK=' + stock_json + ';'
        '(function(){'
          'var SD=window.__ZA_STOCK||{};'
          'function qty(d){'
            'if(!d)return null;'
            'if(d.st==="stock"&&d.qty>0)return d.qty+(d.qty===1?" unidad":" unidades");'
            'if(d.st==="transito"&&d.net>0)return d.net+(d.net===1?" en camino":" en camino");'
            'return null;'
          '}'
          'function patch(card){'
            'var oc=card.getAttribute("onclick")||"";'
            'var m=oc.match(/openModal\\([\'"]([^\'"]+)[\'"]/);'
            'if(!m)return;'
            'var d=SD[m[1].toLowerCase()];'
            'var t=qty(d);'
            'if(!t)return;'
            'var b=card.querySelector(".badge");'
            'if(!b||b.querySelector(".badge-qty"))return;'
            'var bl=b.querySelector(".badge-lines");'
            'if(bl){'
              'var s=document.createElement("span");'
              's.className="badge-qty";s.textContent=t;bl.appendChild(s);'
            '}'
          '}'
          'function patchAll(){document.querySelectorAll(".card").forEach(patch);}'
          'var g=document.getElementById("grid");'
          'if(g)new MutationObserver(function(ms){'
            'ms.forEach(function(mu){'
              'mu.addedNodes.forEach(function(n){'
                'if(n.nodeType!==1)return;'
                'if(n.classList.contains("card"))patch(n);'
                'else if(n.querySelectorAll)n.querySelectorAll(".card").forEach(patch);'
              '});'
            '});'
          '}).observe(g,{childList:true,subtree:true});'
          'if(document.readyState==="loading")'
            'document.addEventListener("DOMContentLoaded",patchAll);'
          'else{patchAll();setTimeout(patchAll,800);}'
        '})();'
        '</script>'
    )
    patch_block = patch_css + patch_js

    # Insertar el patch justo antes de </body> en el ZA HTML
    new_za_html = prefix + new_all + suffix
    if '</body>' in new_za_html:
        new_za_html = new_za_html.replace('</body>', patch_block + '</body>', 1)
        log(f"  ZA: patch de unidades inyectado ({len(stock_data)} productos con stock/transito)")
    else:
        new_za_html = new_za_html + patch_block
        log(f"  ZA: patch de unidades añadido al final ({len(stock_data)} productos)")

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
        tg_fixed    = fix_tienda_html(tg_new)
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

    # Load image cache
    img_cache = load_img_cache()
    # Purge null entries so failed lookups are retried
    null_keys = [k for k, v in img_cache.items() if v is None]
    for k in null_keys:
        del img_cache[k]
    log(f"  Cache: {len(img_cache)} entradas válidas ({len(null_keys)} nulos purgados)")

    text = download_csv()
    if not text: sys.exit(1)

    # Autodetectar delimitador — igual que process_csv() para garantizar consistencia
    # BUG ANTERIOR: delimiter=";" fijo → si el CSV usaba comas, _csv_rows quedaba
    # mal parseado y update_zona_apple no actualizaba ningún producto Apple.
    _lines = text.splitlines()
    _delim = ";" if _lines[0].count(";") > _lines[0].count(",") else ","
    log(f"  Delimitador CSV detectado: '{_delim}'")
    _csv_rows = list(csv.DictReader(_lines, delimiter=_delim))
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
