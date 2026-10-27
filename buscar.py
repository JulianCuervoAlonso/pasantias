#!/usr/bin/env python3
"""Buscador diario de pasantías.

Junta ofertas de varias fuentes (Computrabajo, LinkedIn, Jooble, Bumeran, ZonaJobs
y búsquedas por empresa), las guarda en data/jobs.json y genera docs/index.html,
que es la página que vas a mirar. Si una fuente falla, las demás siguen."""
import json
import os
import re
import time
import unicodedata
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


def normalizar(txt):
    """Minúsculas, sin acentos, sin espacios ni símbolos: 'Mercedes-Benz' -> 'mercedesbenz'."""
    t = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower())


# ---------- Fuentes que se leen directo ----------
def buscar_linkedin(consulta, ubicacion, paginas=(0, 25, 50)):
    """Endpoint público 'guest' de LinkedIn (sin login). Últimos 14 días."""
    ofertas = []
    for inicio in paginas:
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


def buscar_linkedin_empresa(empresa, termino, ubicacion):
    """Busca '<empresa> <término>' en LinkedIn y se queda solo con avisos de esa empresa."""
    res = buscar_linkedin(f"{empresa} {termino}", ubicacion, paginas=(0,))
    clave = normalizar(empresa)
    return [o for o in res if clave and clave in normalizar(o["empresa"])]


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
        lugar = None
        for sp in art.select("p.fs16 span"):
            txt = limpiar(sp.text)
            # saltear la calificación de la empresa (ej. "4,3")
            if txt and not re.fullmatch(r"\d([.,]\d)?", txt):
                lugar = sp
                break
        ofertas.append({
            "titulo": limpiar(a.text),
            "empresa": limpiar(empresa.text) if empresa else "",
            "lugar": limpiar(lugar.text) if lugar else "",
            "url": urllib.parse.urljoin(base, a["href"]).split("#")[0],
            "fuente": "Computrabajo",
        })
    return ofertas


def buscar_jooble(consulta):
    """API oficial de Jooble. Necesita la clave gratuita guardada como secreto JOOBLE_KEY."""
    clave = os.environ.get("JOOBLE_KEY", "").strip()
    datos = {}
    # si con la ubicación configurada no hay nada, prueba variantes (el filtro de zona se aplica después)
    for ubicacion in dict.fromkeys([CONFIG.get("jooble_ubicacion", "Buenos Aires"),
                                    "Buenos Aires, Argentina", "Argentina"]):
        r = requests.post(f"https://jooble.org/api/{clave}", timeout=25,
                          json={"keywords": consulta, "location": ubicacion})
        r.raise_for_status()
        datos = r.json()
        if datos.get("jobs"):
            break
        print(f"     Jooble sin avisos para '{consulta}' en '{ubicacion}' "
              f"(totalCount={datos.get('totalCount')}, respuesta: {str(datos)[:140]})")
        time.sleep(1)
    ofertas = []
    for j in datos.get("jobs", []):
        if not j.get("link"):
            continue
        ofertas.append({
            "titulo": limpiar(BeautifulSoup(j.get("title", ""), "html.parser").text),
            "empresa": limpiar(j.get("company", "")),
            "lugar": limpiar(j.get("location", "")),
            "url": j["link"].split("#")[0],
            "fuente": "Jooble",
        })
    return ofertas


# ---------- Páginas que necesitan navegador (Bumeran, ZonaJobs, empresas) ----------
# Ciudades y provincias del interior: si la tarjeta del aviso menciona una, se marca como tal y se descarta.
INTERIOR = [
    "córdoba", "cordoba", "santa fe", "rosario", "mendoza", "tucumán", "tucuman", "salta", "neuquén",
    "neuquen", "chubut", "santa cruz", "entre ríos", "entre rios", "misiones", "corrientes", "chaco",
    "jujuy", "san juan", "san luis", "la pampa", "río negro", "rio negro", "tierra del fuego",
    "catamarca", "la rioja", "santiago del estero", "formosa", "mar del plata", "bahía blanca",
    "bahia blanca", "la plata", "campana", "zárate", "zarate", "pergamino", "olavarría", "olavarria",
]


def contexto_tarjeta(a):
    """Texto de la tarjeta del aviso: sube desde el enlace hasta el contenedor más grande
    que todavía tenga un solo aviso adentro."""
    nodo, txt = a, limpiar(a.get_text(" "))
    for _ in range(4):
        padre = nodo.parent
        if padre is None:
            break
        hrefs = {x["href"].split("?")[0] for x in padre.select("a[href]")
                 if re.search(r"/empleos/.+-\d{6,}\.html$", x["href"].split("?")[0])}
        if len(hrefs) > 1:
            break
        nodo = padre
        txt = limpiar(nodo.get_text(" "))
    return txt[:700]


def adivinar_lugar(texto):
    """Busca en el texto de la tarjeta una zona del interior o una zona permitida. '' si no hay."""
    t = texto.lower()

    def hay(p):
        return re.search(rf"(?<!\w){re.escape(p)}(?!\w)", t) is not None

    for p in INTERIOR:
        if hay(p):
            return p.title()
    for p in CONFIG.get("lugares_permitidos", []):
        if hay(p):
            return p.upper() if len(p) <= 4 else p.title()
    return ""


def extraer_enlaces(html, base_url, tipo, fuente, empresa=""):
    """Saca ofertas de una página ya cargada.
    tipo 'portal'   -> enlaces con forma /empleos/<texto>-<número>.html (Bumeran, ZonaJobs).
    tipo 'generica' -> cualquier enlace cuyo texto contenga una palabra de 'palabras_en_titulo'
                       (sirve para páginas de empleo de empresas)."""
    soup = BeautifulSoup(html, "html.parser")
    mejores = {}
    for a in soup.select("a[href]"):
        href = a["href"].split("#")[0].split("?")[0]
        if tipo == "portal":
            if not re.search(r"/empleos/.+-\d{6,}\.html$", href):
                continue
            h = a.select_one("h1, h2, h3")
            if h:
                titulo = limpiar(h.text)
            else:
                lineas = [limpiar(x) for x in a.get_text("\n").split("\n") if len(limpiar(x)) > 8]
                titulo = lineas[0] if lineas else ""
        else:
            titulo = limpiar(a.get_text(" "))
            if not (6 < len(titulo) < 160) or not tiene_palabra_clave(titulo):
                continue
        if not titulo:
            continue
        url = urllib.parse.urljoin(base_url, href)
        lugar = adivinar_lugar(contexto_tarjeta(a)) if tipo == "portal" else ""
        previo = mejores.get(url)
        if previo is None or len(titulo) > len(previo["titulo"]):
            mejores[url] = {"titulo": titulo, "empresa": empresa,
                            "lugar": lugar or (previo["lugar"] if previo else ""),
                            "url": url, "fuente": fuente}
        elif lugar and not previo["lugar"]:
            previo["lugar"] = lugar
    return list(mejores.values())


def buscar_con_navegador(paginas):
    """Abre cada página en un Chrome sin pantalla (Playwright), espera a que carguen
    los avisos y los extrae. Devuelve (ofertas, avisos)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright no está instalado: se omiten Bumeran/ZonaJobs/páginas de empresas")
        return [], ["Navegador no disponible (Bumeran/ZonaJobs/empresas)"]
    ofertas, avisos = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(locale="es-AR", user_agent=HEADERS["User-Agent"])
            for pag in paginas:
                nombre = f"Navegador · {pag['nombre']}"
                page = ctx.new_page()
                try:
                    page.goto(pag["url"], wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(7000)
                    for _ in range(3):  # bajar para que carguen más avisos
                        page.mouse.wheel(0, 4000)
                        page.wait_for_timeout(1500)
                    res = extraer_enlaces(page.content(), pag["url"], pag.get("tipo", "generica"),
                                          pag.get("fuente", pag["nombre"]), pag.get("empresa", ""))
                    if not res:
                        raise RuntimeError("no encontré avisos (¿bloqueo o cambió la página?)")
                    if pag.get("pasantia"):  # la propia página ya es de pasantías/trainee
                        for o in res:
                            o["pasantia"] = True
                    print(f"OK   {nombre}: {len(res)}")
                    ofertas += res
                except Exception as e:
                    print(f"FALLÓ {nombre}: {e}")
                    avisos.append(nombre)
                finally:
                    page.close()
                time.sleep(3)
            browser.close()
    except Exception as e:  # por ejemplo, Chrome no instalado
        print(f"FALLÓ navegador: {e}")
        avisos.append("Navegador (no pudo iniciar)")
    return ofertas, avisos


# ---------- Orquestación ----------
def recolectar():
    todas, avisos = [], []

    def correr(nombre, fn):
        try:
            res = fn()
            print(f"OK   {nombre}: {len(res)}")
            todas.extend(res)
            return True
        except Exception as e:  # una fuente caída no frena a las demás
            print(f"FALLÓ {nombre}: {e}")
            avisos.append(nombre)
            return False

    # 1) Computrabajo
    for q in CONFIG["busquedas"]:
        correr(f"Computrabajo · {q}", lambda q=q: buscar_computrabajo(q))
        time.sleep(3)

    # 2) Jooble (solo si está la clave)
    if os.environ.get("JOOBLE_KEY", "").strip():
        for q in CONFIG.get("busquedas_jooble", CONFIG["busquedas"]):
            correr(f"Jooble · {q}", lambda q=q: buscar_jooble(q))
            time.sleep(2)
    else:
        print("Jooble: no hay clave (JOOBLE_KEY), se omite")

    # 3) Bumeran, ZonaJobs y páginas de empresas (con navegador)
    paginas = CONFIG.get("paginas_navegador", [])
    if paginas:
        res, av = buscar_con_navegador(paginas)
        todas.extend(res)
        avisos.extend(av)

    # 4) LinkedIn: búsquedas generales y por empresa (lo último, porque es lo que más limita)
    ubicaciones = CONFIG["ubicaciones_linkedin"]
    tareas = []
    for q in CONFIG["busquedas"]:
        for ub in ubicaciones:
            tareas.append((f"LinkedIn · {q} · {ub}", lambda q=q, ub=ub: buscar_linkedin(q, ub)))
    for emp in CONFIG.get("empresas", []):
        for termino in CONFIG.get("terminos_empresa", ["pasante"]):
            tareas.append((f"LinkedIn · {emp} · {termino}",
                           lambda e=emp, t=termino: buscar_linkedin_empresa(e, t, ubicaciones[0])))
    seguidos, bloqueado = 0, False
    for nombre, fn in tareas:
        if bloqueado:
            continue
        seguidos = 0 if correr(nombre, fn) else seguidos + 1
        if seguidos >= 3:  # LinkedIn nos frenó: no insistir con el resto
            bloqueado = True
            print("LinkedIn limitó las consultas; se omiten las que faltan")
            avisos.append("LinkedIn limitó las consultas: se omitieron las búsquedas restantes")
        time.sleep(4)
    return todas, avisos


def lugar_ok(o):
    """True si la oferta está en una zona permitida (o si el lugar es genérico/remoto)."""
    permitidos = CONFIG.get("lugares_permitidos")
    if not permitidos:
        return True
    l = o.get("lugar", "").lower().strip()
    if not l or l in ("argentina", "remoto", "remote") or "remot" in l:
        return True
    return any(p in l for p in permitidos)


PALABRAS_COMPLETAS = {"intern", "jr"}  # solo cuentan como palabra entera ("intern" ≠ "internacional")


def _coincide(p, t):
    if p in PALABRAS_COMPLETAS:
        return re.search(rf"\b{re.escape(p)}s?\b", t) is not None
    return p in t


def tiene_palabra_clave(titulo):
    """Palabras fuertes (pasante, intern, trainee...) alcanzan solas. Las de nivel (junior, jr,
    estudiante...) valen solo si el título además nombra un área técnica (ingeniería, planta...)."""
    t = titulo.lower()
    if any(_coincide(p, t) for p in CONFIG["palabras_en_titulo"]):
        return True
    if any(_coincide(p, t) for p in CONFIG.get("palabras_nivel", [])):
        return any(a in t for a in CONFIG.get("palabras_area", []))
    return False


def tiene_area(titulo):
    t = titulo.lower()
    return any(a in t for a in CONFIG.get("palabras_area", []))


def es_relevante(o):
    t = o["titulo"].lower()
    if o.get("pasantia"):
        # viene de una página de pasantías: el título no tiene que decir "pasante",
        # pero sí nombrar un área técnica (o una palabra clave fuerte)
        if not (tiene_palabra_clave(o["titulo"]) or tiene_area(o["titulo"])):
            return False
    elif not tiene_palabra_clave(o["titulo"]):
        return False
    if any(p in t for p in CONFIG.get("palabras_excluir", [])):
        return False
    return lugar_ok(o)


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
    # además de borrar las viejas, aplica los filtros actuales a lo ya guardado
    datos = {u: o for u, o in datos.items() if o["ultima_vez"] >= limite and es_relevante(o)}
    DATOS.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    return datos


# ---------- Página ----------
PLANTILLA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pasantías</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#fafafa;color:#222}
h1{font-size:1.3rem;margin:0 0 4px} .s{color:#666;font-size:.9rem;margin-bottom:12px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input{flex:1;min-width:200px;max-width:420px;padding:8px;font-size:1rem;box-sizing:border-box}
button{padding:8px 14px;font-size:1rem;border:1px solid #2e9e44;background:#fff;color:#2e9e44;border-radius:6px;cursor:pointer}
button.on{background:#2e9e44;color:#fff}
table{border-collapse:collapse;width:100%;background:#fff} th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:.9rem}
th{background:#f0f0f0} tr.nueva{background:#eefbea} .b{background:#2e9e44;color:#fff;border-radius:4px;padding:1px 6px;font-size:.7rem}
.w{background:#fff4d6;padding:8px;border-radius:6px;font-size:.85rem} a{color:#0b5cd5}
@media(max-width:700px){th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){display:none}}
</style></head><body>
<h1>Pasantías en ingeniería mecánica</h1>
<div class=s>Actualizado: __FECHA__ · __TOTAL__ ofertas · <b id=cn>__NUEVAS__</b> nuevas <span id=desde></span></div>
__AVISO__
<div class=bar><input id=q placeholder="Filtrar por empresa, puesto, ciudad..."><button id=bn>Solo nuevas</button></div>
<table id=t><thead><tr><th>Puesto</th><th>Empresa</th><th>Lugar</th><th>Fuente</th><th>Detectada</th></tr></thead>
<tbody>__FILAS__</tbody></table>
<script>
(function(){
 var HOY="__HOY__", CORTE="__CORTE__", prev=null;
 try{
  var cur=localStorage.getItem('pas_cur');
  if(cur!==HOY){ if(cur){localStorage.setItem('pas_prev',cur);} localStorage.setItem('pas_cur',HOY); }
  prev=localStorage.getItem('pas_prev');
 }catch(e){}
 var filas=[].slice.call(document.querySelectorAll('#t tbody tr')), solo=false, nn=0;
 filas.forEach(function(r){
  var f=r.getAttribute('data-f'), nueva=prev?(f>prev):(f>=CORTE);
  r.setAttribute('data-n',nueva?'1':'0'); r.classList.toggle('nueva',nueva);
  r.querySelector('.b').hidden=!nueva; if(nueva){nn++;}
 });
 document.getElementById('cn').textContent=nn;
 document.getElementById('desde').textContent=prev?('(desde tu visita del '+prev.split('-').reverse().join('/')+')'):'(de hoy)';
 function aplicar(){
  var v=document.getElementById('q').value.toLowerCase();
  filas.forEach(function(r){
   var ok=r.textContent.toLowerCase().indexOf(v)>=0 && (!solo||r.getAttribute('data-n')==='1');
   r.style.display=ok?'':'none';
  });
 }
 document.getElementById('q').oninput=aplicar;
 var b=document.getElementById('bn');
 b.onclick=function(){solo=!solo; b.classList.toggle('on',solo); b.textContent=solo?'Ver todas':'Solo nuevas'; aplicar();};
})();
</script>
</body></html>"""


def generar_pagina(datos, avisos):
    corte = (date.today() - timedelta(days=CONFIG["dias_nuevo"] - 1)).isoformat()
    filas = sorted(datos.values(), key=lambda o: (o["primera_vez"], o["titulo"]), reverse=True)
    nuevas = sum(1 for o in filas if o["primera_vez"] >= corte)
    cuerpo = []
    for o in filas:
        cuerpo.append(
            f"<tr data-f='{o['primera_vez']}'>"
            f"<td><span class=b hidden>NUEVA</span> "
            f"<a href='{escape(o['url'])}' target='_blank' rel='noopener'>{escape(o['titulo'])}</a></td>"
            f"<td>{escape(o['empresa'])}</td><td>{escape(o['lugar'])}</td>"
            f"<td>{escape(o['fuente'])}</td><td>{o['primera_vez']}</td></tr>")
    aviso = ""
    if avisos:
        lista = "".join(f"<li>{escape(a)}</li>" for a in avisos)
        aviso = (f"<div class=w>Hoy no se pudieron consultar {len(avisos)} fuentes "
                 f"(el sitio limitó, bloqueó o cambió). <details><summary>Ver cuáles</summary>"
                 f"<ul>{lista}</ul></details></div><br>")
    html = (PLANTILLA
            .replace("__FECHA__", datetime.now().strftime("%d/%m/%Y"))
            .replace("__TOTAL__", str(len(filas)))
            .replace("__NUEVAS__", str(nuevas))
            .replace("__AVISO__", aviso)
            .replace("__FILAS__", "".join(cuerpo))
            .replace("__HOY__", HOY)
            .replace("__CORTE__", corte))
    PAGINA.parent.mkdir(exist_ok=True)
    PAGINA.write_text(html, encoding="utf-8")


def main():
    todas, avisos = recolectar()
    relevantes = [o for o in todas if es_relevante(o)]
    print(f"Total {len(todas)} · relevantes {len(relevantes)}")
    # diagnóstico por fuente: cuántos avisos trajo, cuántos pasaron y ejemplos de los descartados
    for fuente in sorted({o["fuente"] for o in todas}):
        de_fuente = [o for o in todas if o["fuente"] == fuente]
        pasan = [o for o in de_fuente if es_relevante(o)]
        print(f"  [{fuente}] trajo {len(de_fuente)} · pasan el filtro {len(pasan)}")
        for o in [o for o in de_fuente if not es_relevante(o)][:6]:
            print(f"      descartado: {o['titulo'][:70]} | {o.get('lugar') or 'sin lugar'}")
    datos = actualizar_datos(relevantes)
    generar_pagina(datos, avisos)
    print(f"Página generada con {len(datos)} ofertas.")


if __name__ == "__main__":
    main()
