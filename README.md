# Infofase Web — Guía de instalación completa

## Qué hace esto
Cada 45 minutos, cron-job.org dispara GitHub Actions, que descarga el CSV
de Megastore, procesa los productos y actualiza la web automáticamente.
**Sin hosting. Coste: 0 €.**

---

## PASO 1 — Preparar el repositorio (5 min)

### 1.1 Sube estos archivos a tu repo de GitHub

En tu repositorio `infofase-web`, sube:
- `index.html` ← renombra `infofase_web_integrada.html` a `index.html`
- `template.html` ← copia exacta de `index.html` (no cambia nunca)
- `update_catalog.py`
- `README.md`
- La carpeta `.github/workflows/update.yml`

**Forma más fácil de subir (sin Git instalado):**
1. Entra en tu repo en github.com
2. Botón `Add file` → `Upload files`
3. Arrastra todos los archivos y la carpeta `.github`
4. `Commit changes`

### 1.2 Activar GitHub Pages

1. En el repo → pestaña **Settings**
2. Menú izquierdo → **Pages**
3. Source: **Deploy from a branch**
4. Branch: **main** → carpeta **/ (root)**
5. **Save**

En 2 minutos tu web estará en:
`https://TU_USUARIO.github.io/infofase-web`

---

## PASO 2 — Crear el token secreto (3 min)

Este token permite a cron-job.org disparar GitHub Actions.

1. github.com → tu foto → **Settings**
2. Scroll abajo → **Developer settings**
3. **Personal access tokens** → **Tokens (classic)**
4. **Generate new token (classic)**
   - Note: `infofase-cronjob-trigger`
   - Expiration: **No expiration**
   - Scope: marca solo ✅ **`workflow`**
5. **Generate token**
6. **Copia el token** (empieza por `ghp_...`) — solo se ve una vez

---

## PASO 3 — Configurar cron-job.org (5 min)

1. Entra en **cron-job.org** con tu cuenta
2. **Dashboard** → **CREATE CRONJOB**
3. Rellena así:

| Campo | Valor |
|-------|-------|
| Title | `Infofase - Actualizar catálogo` |
| URL | `https://api.github.com/repos/TU_USUARIO/infofase-web/dispatches` |
| Execution schedule | Every 45 minutes |
| Request method | **POST** |

4. Despliega **Advanced** → **Headers** → añade:
   ```
   Accept: application/vnd.github.v3+json
   Authorization: token ghp_XXXXXXXX_TU_TOKEN_AQUI
   Content-Type: application/json
   ```

5. **Body (request body)**:
   ```json
   {"event_type":"update-catalog"}
   ```

6. **CREATE** → guardar

**Reemplaza `TU_USUARIO` por tu nombre de usuario de GitHub.**
**Reemplaza `ghp_XXXXXXXX_TU_TOKEN_AQUI` por el token del Paso 2.**

---

## PASO 4 — Probar manualmente (2 min)

Para verificar que todo funciona antes de esperar 45 min:

1. En tu repo de GitHub → pestaña **Actions**
2. **Actualizar catálogo Infofase** (menú izquierdo)
3. **Run workflow** → **Run workflow**
4. Espera ~2 minutos
5. Verás un commit nuevo en el repo con mensaje "Auto: catálogo actualizado..."

Si el workflow falla, haz clic en él para ver el error en los logs.

---

## PASO 5 — Verificar cron-job.org

Tras la primera ejecución automática (a los 45 min):

1. cron-job.org → tu cronjob → pestaña **History**
2. Debe mostrar `200 OK` en cada ejecución
3. En GitHub → Actions: verás los workflows ejecutados
4. En GitHub → Commits: verás un commit automático cada 45 min

---

## Solución de problemas

### El workflow falla con error 401
→ El token no tiene permiso `workflow` o está mal copiado en cron-job.org

### El workflow termina pero no hay cambios en index.html
→ `template.html` no existe en el repo. Sube una copia de `index.html` como `template.html`

### cron-job.org devuelve error 404
→ La URL tiene `TU_USUARIO` sin reemplazar, o el repo no se llama `infofase-web`

### Los productos no se actualizan
→ El CSV de Megastore cambió el formato. Mira el log en GitHub Actions → paso "Descargar CSV"

---

## Estructura final del repo

```
infofase-web/
├── .github/
│   └── workflows/
│       └── update.yml      ← el cron job
├── index.html              ← web publicada (se actualiza sola)
├── template.html           ← base fija (no tocar)
├── update_catalog.py       ← script de actualización
├── update.log              ← log automático
└── README.md
```
