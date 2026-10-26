#!/usr/bin/env python3
"""Buscador diario de pasantías. Junta ofertas de varios portales, las guarda
en data/jobs.json y genera docs/index.html (la página que vas a mirar)."""
import json
import re
import time
import urllib.parse
import unicodedata
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RAIZ = Path(__file__).parent
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
DATOS = RAIZ / "data" / "jobs.json"
PAGINA = RAIZ / "docs" / "index.html"
HOY = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date().isoformat()

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


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", limpiar(texto).lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def ubicacion_permitida(lugar):
    """Exige una ubicación explícita; Argentina/remoto solos no confirman provincia."""
    lugar = normalizar(lugar)
    otras_provincias = ("santa fe", "cordoba", "misiones", "entre rios", "corrientes", "chaco",
                        "formosa", "jujuy", "salta", "tucuman", "santiago del estero",
                        "catamarca", "la rioja", "san juan", "san luis", "mendoza", "la pampa",
                        "neuquen", "rio negro", "chubut", "santa cruz", "tierra del fuego")
    if any(re.search(r"(?<![a-z0-9])" + re.escape(provincia) + r"(?![a-z0-9])", lugar)
           for provincia in otras_provincias):
        return False
    return any(re.search(r"(?<![a-z0-9])" + re.escape(normalizar(alias)) + r"(?![a-z0-9])", lugar)
               for alias in CONFIG["lugares_permitidos"])


def lugar_computrabajo(art):
    # La ubicación es el span sin enlaces del párrafo de empresa/ubicación.
    # Los spans de evaluación (p. ej. 4,3) no son ubicaciones.
    for selector in ("p.fs16.fc_base.mt5", "p.fs16"):
        for parrafo in art.select(selector):
            for span in parrafo.select("span"):
                texto = limpiar(span.get_text(" ", strip=True))
                if not texto or span.select_one("a") or re.fullmatch(r"[\d.,\s★☆]+", texto):
                    continue
                if "," in texto or ubicacion_permitida(texto):
                    return texto
    return ""


def identidad(url):
    # LinkedIn puede cambiar el slug del título conservando el ID del aviso.
    partes = urllib.parse.urlsplit(url)
    if "linkedin.com" in partes.netloc:
        numero = re.search(r"(?:-|/)(\d+)/?$", partes.path)
        if numero:
            return "linkedin:" + numero.group(1)
    return urllib.parse.urlunsplit((partes.scheme, partes.netloc, partes.path.rstrip("/"), "", ""))


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
        lugar = lugar_computrabajo(art)
        ofertas.append({
            "titulo": limpiar(a.text),
            "empresa": limpiar(empresa.text) if empresa else "",
            "lugar": lugar,
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
    archivo_historial = DATOS.parent / "historial.json"
    historial = json.loads(archivo_historial.read_text(encoding="utf-8")) if archivo_historial.exists() else {}
    # Conserva la primera detección incluso después de retirar una oferta de la página.
    por_id = {}
    for o in datos.values():
        clave = identidad(o["url"])
        historial[clave] = min(historial.get(clave, o["primera_vez"]), o["primera_vez"])
        por_id[clave] = o
    for o in encontradas:
        clave = identidad(o["url"])
        historial.setdefault(clave, HOY)
        o = dict(o, primera_vez=historial[clave], ultima_vez=HOY)
        por_id[clave] = o
    limite = (date.fromisoformat(HOY) - timedelta(days=CONFIG["dias_a_conservar"])).isoformat()
    datos = {o["url"]: o for o in por_id.values() if o["ultima_vez"] >= limite}
    DATOS.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    archivo_historial.write_text(json.dumps(historial, ensure_ascii=False, indent=1), encoding="utf-8")
    return datos


# ---------- Página ----------
def generar_pagina(datos, avisos):
    filas = sorted((o for o in datos.values() if ubicacion_permitida(o["lugar"])),
                   key=lambda o: (o["primera_vez"], o["titulo"]), reverse=True)
    cuerpo = []
    for o in filas:
        cuerpo.append(
            f"<tr data-id='{escape(identidad(o['url']), quote=True)}'>"
            f"<td><span class='b' hidden>NUEVA</span> "
            f"<a href='{escape(o['url'], quote=True)}' target='_blank' rel='noopener'>{escape(o['titulo'])}</a></td>"
            f"<td>{escape(o['empresa'])}</td><td>{escape(o['lugar'])}</td>"
            f"<td>{escape(o['fuente'])}</td><td>{escape(o['primera_vez'])}</td></tr>")
    aviso = (f"<p class=w>No se pudieron consultar {len(avisos)} búsquedas hoy (el portal bloqueó o cambió).</p>"
             if avisos else "")
    html = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pasantías</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#fafafa;color:#222}
h1{font-size:1.3rem;margin:0 0 4px} .s{color:#666;font-size:.9rem;margin-bottom:12px}
input[type=search]{width:100%;max-width:420px;padding:8px;font-size:1rem;box-sizing:border-box}
.controles{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}
button{padding:9px;cursor:pointer} .tabla{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:#fff} th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:.9rem}
th{background:#f0f0f0} tr.nueva{background:#eefbea} .b{background:#2e9e44;color:#fff;border-radius:4px;padding:1px 6px;font-size:.7rem}
[hidden]{display:none!important}.w{background:#fff4d6;padding:8px;border-radius:6px} a{color:#0b5cd5}
</style></head><body>
<h1>Pasantías en ingeniería mecánica</h1>
<p class=s>CABA y toda la provincia de Buenos Aires</p>
<div class=s>Actualizado: __FECHA__ · __TOTAL__ ofertas · <span id=nuevas></span></div>
__AVISO__
<p id=estado class=s></p>
<div class=controles>
<input type=search id=q aria-label="Filtrar ofertas" placeholder="Filtrar por empresa, puesto, ciudad...">
<label><input type=checkbox id=solo> Solo nuevas desde mi última visita</label>
<button id=leidas type=button>Marcar todas como vistas</button>
</div>
<p id=contador class=s></p>
<div class=tabla><table id=t><thead><tr><th>Puesto</th><th>Empresa</th><th>Lugar</th><th>Fuente</th><th>Detectada</th></tr></thead>
<tbody>__FILAS__</tbody></table></div>
<p id=vacio hidden>No hay ofertas que coincidan con estos filtros.</p>
<p class=s>Las novedades se recuerdan en este navegador. Al volver, solo serán nuevas las ofertas que todavía no te habíamos mostrado. La fecha «Detectada» es la primera vez que el buscador encontró el aviso.</p>
<script>
const clave='pasantias-vistas-v1', preferencia='pasantias-solo-nuevas-v1';
const filas=Array.from(document.querySelectorAll('#t tbody tr'));
let vistas=new Set(), anteriores=false, guardar=true;
const estado=document.getElementById('estado'), solo=document.getElementById('solo');
try{
  const dato=localStorage.getItem(clave);
  if(dato!==null){const lista=JSON.parse(dato);if(!Array.isArray(lista))throw Error('Historial inválido');vistas=new Set(lista);anteriores=true;}
  solo.checked=localStorage.getItem(preferencia)==='true';
}catch(e){guardar=false;}
filas.forEach(r=>{r.dataset.nueva=vistas.has(r.dataset.id)?'false':'true';});
function pintar(){
  let cantidad=0, nuevas=0;
  const texto=document.getElementById('q').value.toLocaleLowerCase('es');
  filas.forEach(r=>{
    const nueva=r.dataset.nueva==='true';if(nueva)nuevas++;
    r.classList.toggle('nueva',nueva);r.querySelector('.b').hidden=!nueva;
    r.hidden=!(r.textContent.toLocaleLowerCase('es').includes(texto)&&(!solo.checked||nueva));
    if(!r.hidden)cantidad++;
  });
  document.getElementById('nuevas').textContent=nuevas+' nuevas para vos';
  document.getElementById('contador').textContent='Mostrando '+cantidad+' de '+filas.length+' ofertas';
  document.getElementById('vacio').hidden=cantidad!==0;
}
function recordar(){
  if(!guardar)return;
  try{filas.forEach(r=>vistas.add(r.dataset.id));localStorage.setItem(clave,JSON.stringify([...vistas]));}
  catch(e){guardar=false;}
}
recordar();
estado.textContent=!guardar?'El navegador no permite guardar el historial: las novedades no se recordarán al volver.':
  anteriores?'En verde: ofertas que no figuraban en tus visitas anteriores.':'Primera visita: todas las ofertas se muestran como nuevas. En tu próxima visita se compararán con estas.';
document.getElementById('q').addEventListener('input',pintar);
solo.addEventListener('change',()=>{try{localStorage.setItem(preferencia,String(solo.checked));}catch(e){}pintar();});
document.getElementById('leidas').addEventListener('click',()=>{filas.forEach(r=>r.dataset.nueva='false');recordar();pintar();});
pintar();
</script></body></html>"""
    html = (html.replace("__FECHA__", date.fromisoformat(HOY).strftime("%d/%m/%Y"))
            .replace("__TOTAL__", str(len(filas))).replace("__AVISO__", aviso)
            .replace("__FILAS__", "".join(cuerpo)))
    PAGINA.parent.mkdir(exist_ok=True)
    PAGINA.write_text(html, encoding="utf-8")


def main():
    todas, avisos = recolectar()
    relevantes = [o for o in todas if es_relevante(o) and ubicacion_permitida(o["lugar"])]
    print(f"Total {len(todas)} · relevantes {len(relevantes)}")
    datos = actualizar_datos(relevantes)
    generar_pagina(datos, avisos)
    print(f"Página generada con {len(datos)} ofertas.")


if __name__ == "__main__":
    main()
