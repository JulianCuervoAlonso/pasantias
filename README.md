# Buscador diario de pasantías

Cada mañana (08:00 hora Argentina/Uruguay) GitHub corre `buscar.py`, que consulta
LinkedIn y Computrabajo, guarda las ofertas en `data/jobs.json` y regenera la página
`docs/index.html`. Las ofertas detectadas ese día aparecen marcadas como **NUEVA**.

## Puesta en marcha (una sola vez, ~10 minutos)

1. Creá una cuenta en https://github.com si no tenés.
2. Creá un repositorio nuevo (por ejemplo `pasantias`), **público** (así podés usar GitHub Pages gratis).
3. Subí todo el contenido de esta carpeta al repositorio, incluida la carpeta oculta `.github`.
   Lo más simple: en la página del repo, "Add file" → "Upload files" y arrastrá todo.
   Si no te deja subir carpetas ocultas, creá a mano el archivo `.github/workflows/diario.yml`
   ("Add file" → "Create new file" y escribí esa ruta completa) y pegá su contenido.
4. En el repo: **Settings → Actions → General → Workflow permissions** → elegí
   "Read and write permissions" y guardá.
5. Pestaña **Actions** → "Buscar pasantías" → **Run workflow**, para la primera corrida.
   Tarda un par de minutos.
6. **Settings → Pages** → Source: "Deploy from a branch" → Branch: `main`, carpeta `/docs` → Save.
7. A los minutos tu página queda en `https://TU-USUARIO.github.io/pasantias/`.
   Guardala como acceso directo en el celular.

Desde ese momento se actualiza sola todos los días.

## Personalizar

Todo se cambia en `config.json`:

- `pais_computrabajo`: `ar` (Argentina), `uy` (Uruguay), `cl`, `mx`, `co`, `pe`, etc.
- `ubicaciones_linkedin`: lista de países o ciudades, por ejemplo `["Uruguay", "Argentina"]`.
- `busquedas`: las frases que se buscan. Agregá otras (por ejemplo "pasante diseño mecánico",
  "pasante mantenimiento", "pasante CAD").
- `palabras_en_titulo`: filtro para quedarse solo con pasantías/junior. Si ves ofertas que
  no sirven, sacá palabras (por ejemplo `junior`); si faltan, agregá.
- `dias_a_conservar`: cuánto tiempo se guarda una oferta que ya no aparece.

## Limitaciones a tener en cuenta

- Los portales pueden bloquear o cambiar su HTML sin aviso. Si pasa, la página muestra un
  aviso amarillo y el resto de las fuentes siguen funcionando. Si una fuente deja de andar
  del todo, avisame y la ajustamos.
- LinkedIn en modo público muestra un subconjunto de las ofertas; no reemplaza tener
  alertas activadas en tu cuenta.
- Muchas pasantías industriales no se publican en portales: sumá a mano las páginas de
  empleo de las empresas que te interesan y la oficina de pasantías de tu facultad.
- Probalo primero con una corrida manual (paso 5) antes de confiar en él.

## Probarlo en tu computadora (opcional)

```
pip install -r requirements.txt
python buscar.py
```
Después abrí `docs/index.html` en el navegador.
