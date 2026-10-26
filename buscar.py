#!/usr/bin/env python3
"""Buscador diario de pasantías. Junta ofertas de varios portales, las guarda
en data/jobs.json y genera docs/index.html (la página que vas a mirar)."""
import json
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RAIZ = Path(__file__).parent
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
DATOS = RAIZ / "data" / "jobs.json"
PAGINA = RAIZ / "docs" / "index.html"
HOY = date.today().isoformat()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}


def pedir(url):
    """Pide la página; si el sitio dice 'demasiadas consultas' (429), espera y reintenta."""
    esperas = (20, 45, 90)
    for intento in range(len(esperas) + 1):
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 429 and intento < len(esperas):
            print(f"     429 (límite del sitio), espero {esperas[intento]}s y reintento...")
            time.sleep(esperas[intento])
            continue
        r.raise_for_status()
        return r.text


def limpiar(txt):
    return re.sub(r"\s+", " ", txt or "").strip()


# ---------- Fuentes ----------
def buscar_linkedin(consulta, ubicacion):
    """Endpoint público 'guest' de LinkedIn (sin login). Últimos 14 días."""
    ofertas = []
    for inicio in (0, 25, 50):
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
               + urllib.parse.urlencode({"keywords": consulta, "location": ubicacion,
                                         "f_TPR": "r1209600", "start": inicio}))
        try:
            html = pedir(url)
        except Exception as e:
            if ofertas:  # si ya trajimos páginas anteriores, las conservamos
                print(f"     LinkedIn cortó en start={inicio} ({e}); me quedo con lo ya traído")
                break
            raise
        soup = BeautifulSoup(html, "html.parser")
        tarjetas = soup.select("li")
        if not tarjetas:
            break
        for li in tarjetas:
            a = li.select_one("a.base-card__full-link") or li.select_one("a[href*='/jobs/view/']")
            titulo = li.select_one(".base-search-card__title")
            if not a or not titulo:
                continue
            empresa = li.select_one(".base-search-card__subtitle")
            lugar = li.select_one(".job-search-card__location")
            ofertas.append({
                "titulo": limpiar(titulo.text),
                "empresa": limpiar(empresa.text) if empresa else "",
                "lugar": limpiar(lugar.text) if lugar else "",
                "url": a["href"].split("?")[0],
                "fuente": "LinkedIn",
            })
        time.sleep(5)
    return ofertas


def buscar_computrabajo(consulta):
    pais = CONFIG["pais_computrabajo"]
    slug = re.sub(r"[^a-z0-9]+", "-", consulta.lower()
                  .translate(str.maketrans("áéíóúñ", "aeioun"))).strip("-")
    base = f"https://{pais}.computrabajo.com"
    soup = BeautifulSoup(pedir(f"{base}/trabajo-de-{slug}"), "html.parser")
    ofertas = []
    for art in soup.select("article.box_offer"):
        a = art.select_one("a.js-o-link") or art.select_one("h2 a")
        if not a:
            continue
        empresa = art.select_one("a.fc_base.t_ellipsis")
        lugar = art.select_one("p.fs16.fc_base.mt5 span.mr10") or art.select_one("p.fs16 span")
        ofertas.append({
            "titulo": limpiar(a.text),
            "empresa": limpiar(empresa.text) if empresa else "",
            "lugar": limpiar(lugar.text) if lugar else "",
            "url": urllib.parse.urljoin(base, a["href"]).split("#")[0],
            "fuente": "Computrabajo",
        })
    return ofertas


def recolectar():
    todas, avisos = [], []
    trabajos = []
    for q in CONFIG["busquedas"]:
        for ub in CONFIG["ubicaciones_linkedin"]:
            trabajos.append((f"LinkedIn · {q} · {ub}", lambda q=q, ub=ub: buscar_linkedin(q, ub)))
        trabajos.append((f"Computrabajo · {q}", lambda q=q: buscar_computrabajo(q)))
    for nombre, fn in trabajos:
        try:
            res = fn()
            print(f"OK   {nombre}: {len(res)}")
            todas += res
        except Exception as e:  # un portal caído no frena a los demás
            print(f"FALLÓ {nombre}: {e}")
            avisos.append(nombre)
        time.sleep(4)
    return todas, avisos


def es_relevante(o):
    t = o["titulo"].lower()
    return any(p in t for p in CONFIG["palabras_en_titulo"])


# ---------- Almacenamiento ----------
def actualizar_datos(encontradas):
    DATOS.parent.mkdir(exist_ok=True)
    datos = json.loads(DATOS.read_text(encoding="utf-8")) if DATOS.exists() else {}
    for o in encontradas:
        previo = datos.get(o["url"])
        o["primera_vez"] = previo["primera_vez"] if previo else HOY
        o["ultima_vez"] = HOY
        datos[o["url"]] = o
    limite = (date.today() - timedelta(days=CONFIG["dias_a_conservar"])).isoformat()
    datos = {u: o for u, o in datos.items() if o["ultima_vez"] >= limite}
    DATOS.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    return datos


# ---------- Página ----------
def generar_pagina(datos, avisos):
    corte = (date.today() - timedelta(days=CONFIG["dias_nuevo"] - 1)).isoformat()
    filas = sorted(datos.values(), key=lambda o: (o["primera_vez"], o["titulo"]), reverse=True)
    nuevas = sum(1 for o in filas if o["primera_vez"] >= corte)
    cuerpo = []
    for o in filas:
        es_nueva = o["primera_vez"] >= corte
        cuerpo.append(
            f"<tr class='{'nueva' if es_nueva else ''}'>"
            f"<td>{'<span class=b>NUEVA</span> ' if es_nueva else ''}"
            f"<a href='{escape(o['url'])}' target='_blank' rel='noopener'>{escape(o['titulo'])}</a></td>"
            f"<td>{escape(o['empresa'])}</td><td>{escape(o['lugar'])}</td>"
            f"<td>{escape(o['fuente'])}</td><td>{o['primera_vez']}</td></tr>")
    aviso = (f"<p class=w>No se pudieron consultar {len(avisos)} búsquedas hoy (el portal bloqueó o cambió).</p>"
             if avisos else "")
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pasantías</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#fafafa;color:#222}}
h1{{font-size:1.3rem;margin:0 0 4px}} .s{{color:#666;font-size:.9rem;margin-bottom:12px}}
input{{width:100%;max-width:420px;padding:8px;font-size:1rem;margin-bottom:12px;box-sizing:border-box}}
table{{border-collapse:collapse;width:100%;background:#fff}} th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:.9rem}}
th{{background:#f0f0f0}} tr.nueva{{background:#eefbea}} .b{{background:#2e9e44;color:#fff;border-radius:4px;padding:1px 6px;font-size:.7rem}}
.w{{background:#fff4d6;padding:8px;border-radius:6px}} a{{color:#0b5cd5}}
@media(max-width:700px){{th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){{display:none}}}}
</style></head><body>
<h1>Pasantías en ingeniería mecánica</h1>
<div class=s>Actualizado: {datetime.now().strftime('%d/%m/%Y')} · {len(filas)} ofertas · {nuevas} nuevas</div>
{aviso}
<input id=q placeholder="Filtrar por empresa, puesto, ciudad..." oninput="f()">
<table id=t><thead><tr><th>Puesto</th><th>Empresa</th><th>Lugar</th><th>Fuente</th><th>Detectada</th></tr></thead>
<tbody>{''.join(cuerpo)}</tbody></table>
<script>function f(){{var v=document.getElementById('q').value.toLowerCase();
document.querySelectorAll('#t tbody tr').forEach(function(r){{r.style.display=r.textContent.toLowerCase().includes(v)?'':'none'}})}}</script>
</body></html>"""
    PAGINA.parent.mkdir(exist_ok=True)
    PAGINA.write_text(html, encoding="utf-8")


def main():
    todas, avisos = recolectar()
    relevantes = [o for o in todas if es_relevante(o)]
    print(f"Total {len(todas)} · relevantes {len(relevantes)}")
    datos = actualizar_datos(relevantes)
    generar_pagina(datos, avisos)
    print(f"Página generada con {len(datos)} ofertas.")


if __name__ == "__main__":
    main()
