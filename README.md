# El Diario de Wilfredo

Un periódico con final: las noticias del día en siete secciones, y cuando lo terminas, cierras.

- `index.html` — la página (HTML, CSS y JS puro).
- `fetch_news.py` — lee los feeds RSS/Atom y genera `news.json` (solo librería estándar).
- `.github/workflows/noticias.yml` — actualiza `news.json` a las 7, 12 y 19 h de Arnhem.

## Probar en local

```bash
python3 -m http.server 8000
```

Abre http://localhost:8000. Sin `news.json` verás noticias de ejemplo con una franja roja de vista previa.
Para descargar noticias reales: `python3 fetch_news.py`.

## Cambiar feeds

Edita la lista `SECTIONS` al principio de `fetch_news.py`.
