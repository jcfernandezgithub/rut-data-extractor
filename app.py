import os
import re
import time
from typing import Dict, Any, List, Optional

import uvicorn
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

RUT_URL = "https://r.rutificador.co/pr/{rut}"

app = FastAPI(title="Rutificador Proxy", version="1.0.0")

# CORS abierto para frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Helpers ----------
def format_rut_lenient(raw: str) -> Optional[str]:
    """
    Formatea el RUT de forma laxa (no valida DV):
    - Acepta "15421741K", "15.421.741-K", "15421741-K", etc.
    - Devuelve "XX.XXX.XXX-Y" si puede.
    """
    if not raw:
        return None
    s = "".join(ch for ch in str(raw).lower() if ch.isdigit() or ch == "k")
    if len(s) < 2:
        return None
    body, dv = s[:-1], s[-1].upper()
    rev = body[::-1]
    chunks = [rev[i : i + 3] for i in range(0, len(rev), 3)]
    dotted = ".".join(c[::-1] for c in chunks[::-1])
    return f"{dotted}-{dv}"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
COMMON_HEADERS = {
    "Origin": "https://r.rutificador.co",
    "Referer": "https://r.rutificador.co/",
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

TD_RX = re.compile(r"<td[^>]*>(.*?)</td>", flags=re.IGNORECASE | re.DOTALL)
TR_RX = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", flags=re.IGNORECASE)

def fetch_via_requests(url: str, timeout: int = 20) -> requests.Response:
    return requests.post(url, headers=COMMON_HEADERS, timeout=timeout, allow_redirects=True)

def fetch_via_playwright(url: str, wait_ms: int = 3500) -> str:
    """
    Fallback con navegador real (Chromium headless) para pasar Cloudflare.
    Importa Playwright en tiempo de uso para no romper el arranque si no está instalado.
    """
    try:
        from playwright.sync_api import sync_playwright  # import perezoso
    except Exception as e:
        raise RuntimeError(
            "Playwright no está instalado. Si usás Nixpacks, agrega "
            "'playwright==1.55.0' a requirements.txt y en build ejecuta: "
            "'python -m playwright install --with-deps chromium'. "
            "Si usás Dockerfile, usa la imagen oficial de Playwright."
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="es-CL",
            extra_http_headers={
                "Accept": COMMON_HEADERS["Accept"],
                "Accept-Language": COMMON_HEADERS["Accept-Language"],
            },
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # pequeña espera por challenge
        time.sleep(wait_ms / 1000.0)
        try:
            page.wait_for_selector("tr td", timeout=4000)
        except Exception:
            pass
        html = page.content()
        browser.close()
    return html or ""

def ensure_has_tds(html: str) -> bool:
    return bool(re.search(r"<tr[^>]*>[\s\S]*?<td[^>]*>", html or "", re.IGNORECASE))

def extract_first_tr_values(html: str) -> List[str]:
    m = TR_RX.search(html or "")
    if not m:
        return []
    tr_content = m.group(1)
    values: List[str] = []
    for td in TD_RX.finditer(tr_content):
        txt = td.group(1)
        clean = re.sub(r"<[^>]+>", "", txt or "")
        clean = re.sub(r"\s+", " ", clean.replace("\u00A0", " ")).strip()
        if clean:
            values.append(clean)
    return values

def map_values(values: List[str]) -> Dict[str, str]:
    """
    Orden esperado: [nombre, rut, genero, direccion, comuna]
    Si vienen menos de 5, cae a campo1, campo2, ...
    """
    if len(values) >= 5:
        nombre, rut_resp, genero, direccion, comuna = values[:5]
        return {
            "nombre": nombre,
            "rut": rut_resp,
            "genero": genero,
            "direccion": direccion,
            "comuna": comuna,
        }
    return {f"campo{i+1}": v for i, v in enumerate(values)}

# ---------- Endpoints ----------
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/rut/{rut}")
def get_rut(rut: str, inspect: Optional[bool] = False) -> Dict[str, Any]:
    formatted = format_rut_lenient(rut)
    if not formatted:
        raise HTTPException(status_code=400, detail="RUT inválido")

    url = RUT_URL.format(rut=formatted)

    # 1) Intento rápido con requests
    try:
        r = fetch_via_requests(url)
        html = r.text or ""
        print("========== [requests] ==========")
        print("status:", r.status_code)
        print("snippet:", html[:600])
        print("================================")

        if r.status_code != 403 and ensure_has_tds(html):
            vals = extract_first_tr_values(html)
            if not vals:
                raise HTTPException(status_code=400, detail="No se encontraron columnas <td> válidas")
            data = map_values(vals)
            return {
                "source": url,
                "rut_consultado": formatted,
                "columnas": len(vals),
                "data": data,
                "raw": vals,
            }
    except requests.RequestException as e:
        print("requests error:", str(e))

    # 2) Fallback Playwright (Cloudflare / sin <td>)
    print("[rutificador] requests bloqueado o sin <td>. Probando Playwright…")
    html = fetch_via_playwright(url)
    print("------ [playwright] snippet ------")
    print(html[:600])

    if not ensure_has_tds(html):
        if inspect:
            return {
                "source": url,
                "rut_consultado": formatted,
                "message": "No se detectaron <td> incluso con Playwright.",
                "snippet": html[:1200],
            }
        raise HTTPException(status_code=400, detail="No se encontraron filas <tr> con columnas <td> en la respuesta")

    vals = extract_first_tr_values(html)
    if not vals:
        raise HTTPException(status_code=400, detail="La fila no contiene columnas <td>")

    data = map_values(vals)
    return {
        "source": url,
        "rut_consultado": formatted,
        "columnas": len(vals),
        "data": data,
        "raw": vals,
    }

@app.get("/rut/{rut}/raw")
def get_rut_raw(rut: str):
    formatted = format_rut_lenient(rut)
    if not formatted:
        raise HTTPException(status_code=400, detail="RUT inválido")
    url = RUT_URL.format(rut=formatted)

    # Primero requests; si 403, Playwright
    try:
        r = fetch_via_requests(url)
        html = r.text or ""
        if r.status_code != 403 and html:
            return {"status": r.status_code, "source": url, "html": html[:20000]}
    except requests.RequestException:
        pass

    html = fetch_via_playwright(url)
    return {"status": 200, "source": url, "html": html[:20000]}

if __name__ == "__main__":
    # Railway expone PORT; fallback 8000
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, workers=1)
