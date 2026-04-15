#!/usr/bin/env python3
"""
Build viewer.html + sw.js — embeds JSZip, Leaflet, and all viewer logic into a single self-contained file.
Run:  python3 build_viewer.py
"""
import os, sys
from datetime import datetime, timezone

JSZIP_PATH       = "/tmp/jszip.min.js"
LEAFLET_JS_PATH  = "/tmp/leaflet.js"
LEAFLET_CSS_PATH = "/tmp/leaflet.css"

def build_sw(build_ts):
    return f"""// RouteBook service worker — auto-generated, do not edit
const VERSION = '{build_ts}';
const CACHE   = 'routebook-' + VERSION;

self.addEventListener('install', function(e) {{
  e.waitUntil(
    caches.open(CACHE).then(function(c) {{
      return c.addAll(['./viewer.html', './sw.js']);
    }}).then(function() {{ return self.skipWaiting(); }})
  );
}});

self.addEventListener('activate', function(e) {{
  e.waitUntil(
    caches.keys().then(function(keys) {{
      return Promise.all(
        keys.filter(function(k) {{ return k !== CACHE; }})
            .map(function(k) {{ return caches.delete(k); }})
      );
    }}).then(function() {{ return self.clients.claim(); }})
    .then(function() {{
      // Notify all open tabs that a new version is available
      self.clients.matchAll({{ type: 'window' }}).then(function(clients) {{
        clients.forEach(function(c) {{ c.postMessage({{ type: 'UPDATE_AVAILABLE', version: VERSION }}); }});
      }});
    }})
  );
}});

self.addEventListener('fetch', function(e) {{
  // Network-first for viewer.html and sw.js so updates are always picked up
  if (e.request.url.endsWith('viewer.html') || e.request.url.endsWith('sw.js')) {{
    e.respondWith(
      fetch(e.request).then(function(resp) {{
        return caches.open(CACHE).then(function(c) {{
          c.put(e.request, resp.clone());
          return resp;
        }});
      }}).catch(function() {{ return caches.match(e.request); }})
    );
    return;
  }}
  // Cache-first for everything else (tiles etc)
  e.respondWith(
    caches.match(e.request).then(function(r) {{
      return r || fetch(e.request);
    }})
  );
}});
"""

def main():
    missing = []
    for path, cmd in [
        (JSZIP_PATH,       "curl -sL https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js -o /tmp/jszip.min.js"),
        (LEAFLET_JS_PATH,  "curl -sL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js  -o /tmp/leaflet.js"),
        (LEAFLET_CSS_PATH, "curl -sL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o /tmp/leaflet.css"),
    ]:
        if not os.path.exists(path):
            missing.append((path, cmd))
    if missing:
        print("ERROR: missing asset files. Download with:")
        for _, cmd in missing:
            print(f"  {cmd}")
        sys.exit(1)

    with open(JSZIP_PATH,       encoding="utf-8") as f: jszip       = f.read()
    with open(LEAFLET_JS_PATH,  encoding="utf-8") as f: leaflet_js  = f.read()
    with open(LEAFLET_CSS_PATH, encoding="utf-8") as f: leaflet_css = f.read()

    build_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    html = build_viewer(jszip, leaflet_js, leaflet_css, build_ts)
    out = "viewer.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"viewer.html written ({len(html)//1024} KB)")

    sw = build_sw(build_ts)
    with open("sw.js", "w", encoding="utf-8") as f:
        f.write(sw)
    print(f"sw.js written (version {build_ts})")

def build_viewer(jszip, leaflet_js, leaflet_css, build_ts):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<title>JumRouteBook Viewer</title>
<meta name="app-version" content="{build_ts}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="RouteBook">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#2b6cb0">
<link rel="apple-touch-icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 180 180'%3E%3Crect width='180' height='180' rx='40' fill='%232b6cb0'/%3E%3Ctext x='90' y='125' font-size='96' text-anchor='middle'%3E%F0%9F%9A%B4%3C/text%3E%3C/svg%3E">
<script>
if ('serviceWorker' in navigator) {{
  navigator.serviceWorker.register('./sw.js').catch(function(){{}});
}}
</script>
<style>
{leaflet_css}
</style>
<script>
{leaflet_js}
</script>
<style>
{VIEWER_CSS}
</style>
</head>
<body>

<!-- ═══════════════════════════════════ LIBRARY SCREEN ═══════════════════════ -->
<div id="screen-library">
  <div id="lib-topbar">
    <span id="lib-title">RouteBook</span>
    <label id="open-btn" title="Open .jumroutebook file">
      <input type="file" id="file-input" accept=".jumroutebook" multiple>
      + Open file
    </label>
  </div>

  <div id="lib-body">
    <div id="lib-empty">
      <div id="lib-empty-icon">🚴</div>
      <p>No routes loaded yet.</p>
      <p class="lib-hint">Tap <strong>+ Open file</strong> to load a <code>.jumroutebook</code> file,<br>or paste a URL below.</p>
      <div id="url-row">
        <input id="url-input" type="url" placeholder="https://…  .jumroutebook URL">
        <button id="url-load-btn" onclick="loadFromUrl()">Load</button>
      </div>
    </div>
    <ul id="lib-list"></ul>
    <div id="url-row-bottom">
      <input id="url-input2" type="url" placeholder="https://…  .jumroutebook URL">
      <button onclick="loadFromUrl2()">Load URL</button>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════════ ROUTE SCREEN ═══════════════════════════ -->
<div id="screen-route" class="hidden">

  <!-- Top bar -->
  <div id="topbar">
    <button id="back-btn" onclick="showLibrary()" aria-label="Back to library">&#8592;</button>
    <button id="burger" aria-label="Section list" onclick="toggleNav()">
      <span></span><span></span><span></span>
    </button>
    <div id="topbar-title">Route</div>
    <span id="section-counter"></span>
    <button id="prev-btn" onclick="goTo(current-1)" aria-label="Previous">&#8592;</button>
    <button id="next-btn" onclick="goTo(current+1)" aria-label="Next">&#8594;</button>
  </div>

  <!-- Nav sidebar / drawer -->
  <div id="nav-overlay" onclick="closeNav()"></div>
  <div id="nav-drawer">
    <div id="nav-header">
      <div id="nav-header-text">
        <span id="nav-route-title">Route</span>
        <small id="nav-route-meta"></small>
      </div>
      <button id="sidebar-toggle" onclick="toggleSidebar()" aria-label="Collapse sidebar">&#8249;</button>
    </div>
    <ul id="nav-list"></ul>
  </div>

  <!-- Content -->
  <div id="viewport">
    <div id="slider"></div>
  </div>

  <!-- Dots -->
  <div id="dots"></div>

</div>

<!-- Loading overlay -->
<div id="loading" class="hidden">
  <div id="loading-box">
    <div id="loading-spinner"></div>
    <div id="loading-msg">Loading…</div>
  </div>
</div>

<!-- Toast -->
<div id="toast"></div>

<!-- Elevation tooltip -->
<div id="elev-tooltip"></div>

<!-- Fullscreen map modal -->
<div id="map-modal">
  <div id="map-modal-inner">
    <div id="map-modal-leaflet"></div>
    <button id="map-modal-close" aria-label="Close map">&#x2715;</button>
  </div>
</div>

<script>
{jszip}
</script>
<script>
{VIEWER_JS}
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
VIEWER_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
* { -webkit-tap-highlight-color: transparent; }
html, body {
  height: 100%; overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 16px; line-height: 1.5;
  background: #f0f4f8; color: #2d3748;
}
.hidden { display: none !important; }

/* ── Library screen ── */
#screen-library { height: 100%; display: flex; flex-direction: column; }
#lib-topbar {
  background: #2b6cb0; color: white;
  padding: max(1rem, calc(.75rem + env(safe-area-inset-top))) max(1rem, env(safe-area-inset-right)) .75rem max(1rem, env(safe-area-inset-left));
  display: flex; align-items: center; justify-content: space-between;
  box-shadow: 0 1px 0 rgba(0,0,0,.2);
  flex-shrink: 0;
}
#lib-title { font-weight: 700; font-size: 1.1rem; }
#open-btn {
  background: rgba(255,255,255,.18); color: white;
  border: none; border-radius: 8px;
  padding: .4rem .9rem; font-size: .9rem; font-weight: 600;
  cursor: pointer; transition: background .15s;
  white-space: nowrap;
}
#open-btn:active { background: rgba(255,255,255,.35); }
#file-input { display: none; }

#lib-body {
  flex: 1; overflow-y: auto; -webkit-overflow-scrolling: touch;
  padding: 1.5rem 1rem calc(1.5rem + env(safe-area-inset-bottom));
}
#lib-empty { text-align: center; padding: 3rem 1rem 2rem; color: #718096; }
#lib-empty-icon { font-size: 4rem; margin-bottom: 1rem; }
#lib-empty p { margin-bottom: .5rem; font-size: .95rem; }
.lib-hint { font-size: .85rem; color: #a0aec0; }

#url-row, #url-row-bottom {
  display: flex; gap: .5rem; margin-top: 1.25rem; max-width: 520px; margin-left: auto; margin-right: auto;
}
#url-row-bottom { margin-top: 1.5rem; }
#url-input, #url-input2 {
  flex: 1; border: 1px solid #cbd5e0; border-radius: 8px;
  padding: .5rem .75rem; font-size: .88rem; color: #2d3748;
  background: white;
}
#url-row button, #url-row-bottom button {
  background: #2b6cb0; color: white; border: none; border-radius: 8px;
  padding: .5rem 1rem; font-size: .88rem; font-weight: 600; cursor: pointer;
  white-space: nowrap; transition: background .15s;
}
#url-row button:active, #url-row-bottom button:active { background: #2c5282; }

#lib-list { list-style: none; max-width: 640px; margin: 0 auto; }
#lib-list li {
  background: white; border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,.07);
  margin-bottom: .75rem; display: flex; align-items: center;
  overflow: hidden; cursor: pointer; transition: box-shadow .15s;
}
#lib-list li:active { box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.lib-item-body {
  flex: 1; padding: .85rem 1rem; min-width: 0;
}
.lib-item-title { font-weight: 700; font-size: .97rem; color: #1a365d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lib-item-meta { font-size: .8rem; color: #718096; margin-top: 2px; }
.lib-item-open {
  flex-shrink: 0; padding: 1rem; font-size: 1.3rem; color: #2b6cb0;
  display: flex; align-items: center;
}
.lib-item-del {
  flex-shrink: 0; padding: 1rem; font-size: 1rem; color: #fc8181;
  background: none; border: none; cursor: pointer;
  display: flex; align-items: center;
}
.lib-item-del:active { color: #e53e3e; }

/* ── Route screen: top bar ── */
#screen-route { position: fixed; inset: 0; }
#topbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  height: calc(52px + env(safe-area-inset-top));
  padding-top: env(safe-area-inset-top);
  background: #2b6cb0;
  display: flex; align-items: center;
  padding-left: max(.5rem, env(safe-area-inset-left));
  padding-right: max(.75rem, env(safe-area-inset-right));
  gap: .4rem;
  box-shadow: 0 1px 0 rgba(0,0,0,.2), 0 2px 8px rgba(0,0,0,.25);
}
#back-btn {
  background: rgba(255,255,255,.18); border: none; color: white; cursor: pointer;
  border-radius: 8px; min-width: 38px; min-height: 38px;
  font-size: 1.1rem; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  transition: background .15s, transform .1s;
}
#back-btn:active { transform: scale(.9); background: rgba(255,255,255,.35); }
#burger {
  background: none; border: none; cursor: pointer;
  min-width: 44px; min-height: 44px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 5px; flex-shrink: 0; border-radius: 8px; transition: background .15s;
}
#burger:active { background: rgba(255,255,255,.18); }
#burger span { display: block; width: 22px; height: 2px; background: white; border-radius: 2px; }
#topbar-title {
  color: white; font-weight: 700; font-size: .95rem; flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#section-counter { color: rgba(255,255,255,.75); font-size: .82rem; white-space: nowrap; flex-shrink: 0; }
#prev-btn, #next-btn {
  background: rgba(255,255,255,.18); border: none; color: white; cursor: pointer;
  border-radius: 8px; min-width: 44px; min-height: 36px;
  font-size: 1rem; flex-shrink: 0;
  transition: background .15s, transform .1s;
  display: flex; align-items: center; justify-content: center;
}
#prev-btn:hover, #next-btn:hover { background: rgba(255,255,255,.28); }
#prev-btn:active, #next-btn:active { transform: scale(.9); background: rgba(255,255,255,.35); }
#prev-btn:disabled, #next-btn:disabled { opacity: .3; cursor: default; transform: none; }

/* ── Nav sidebar / drawer ── */
#nav-overlay {
  display: none; position: fixed; inset: 0; z-index: 200;
  background: rgba(0,0,0,.45); backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}
#nav-overlay.open { display: block; }
#sidebar-toggle {
  display: none;
  background: rgba(255,255,255,.18); border: none; color: white; cursor: pointer;
  border-radius: 7px; min-width: 32px; min-height: 32px; margin-top: 2px;
  align-items: center; justify-content: center;
  font-size: 1rem; flex-shrink: 0; transition: background .15s;
}
#sidebar-toggle:active { background: rgba(255,255,255,.35); }
#nav-drawer {
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 201;
  width: 280px; background: #f8fafc;
  display: flex; flex-direction: column; overflow: hidden;
  border-right: 1px solid #e2e8f0;
}
@media (max-width: 959px) {
  #nav-drawer {
    width: min(300px, 88vw);
    transform: translateX(-100%); transition: transform .28s cubic-bezier(.4,0,.2,1);
    box-shadow: 4px 0 32px rgba(0,0,0,.3);
  }
  #nav-drawer.open { transform: translateX(0); }
}
@media (min-width: 960px) {
  #nav-overlay { display: none !important; }
  #nav-drawer { transform: none; transition: transform .3s cubic-bezier(.4,0,.2,1); box-shadow: none; }
  #burger { display: none; }
  #section-counter { display: none; }
  #sidebar-toggle { display: flex; }
  #topbar { left: 280px; }
  #viewport { left: 280px; }
  #dots { left: 280px; }
}
@media (min-width: 960px) {
  body.sidebar-hidden #nav-drawer { transform: translateX(-100%); }
  body.sidebar-hidden #topbar { left: 0; }
  body.sidebar-hidden #viewport { left: 0; }
  body.sidebar-hidden #dots { left: 0; }
  body.sidebar-hidden #burger { display: flex; }
  body.sidebar-hidden #section-counter { display: inline; }
}
@media (min-width: 960px) {
  body.sidebar-hidden { padding-left: 0; }
}
#nav-header {
  background: #2b6cb0; color: white;
  padding: max(1rem, calc(.75rem + env(safe-area-inset-top))) 1rem .85rem 1.2rem;
  font-weight: 700; font-size: 1rem; flex-shrink: 0;
  display: flex; align-items: flex-start; gap: .5rem;
}
#nav-header-text { flex: 1; min-width: 0; }
#nav-route-title { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#nav-header small, #nav-route-meta {
  display: block; font-weight: 400; font-size: .78rem; opacity: .8; margin-top: 3px;
}
#nav-list { list-style: none; overflow-y: auto; flex: 1; -webkit-overflow-scrolling: touch; }
#nav-list li { border-bottom: 1px solid #e8edf2; }
.nav-link {
  display: flex; align-items: center; gap: .75rem;
  min-height: 56px; padding: 0 1.2rem;
  text-decoration: none; color: #2d3748; transition: background .12s;
}
.nav-link:active { background: #dbeafe; }
.nav-link:hover, .nav-link.active { background: #ebf8ff; color: #2b6cb0; }
.nav-num {
  background: #2b6cb0; color: white; border-radius: 6px;
  padding: 2px 7px; font-size: .75rem; font-weight: 700; flex-shrink: 0;
  min-width: 28px; text-align: center;
}
.nav-link.active .nav-num { background: #c05621; }
.nav-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.nav-route { font-size: .88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3; }
.nav-km { font-size: .74rem; color: #718096; }
.nav-link.active .nav-km { color: #2b6cb0; opacity: .85; }

/* ── Slide viewport ── */
#viewport {
  position: fixed;
  top: calc(52px + env(safe-area-inset-top));
  left: 0; right: 0; bottom: 0;
  overflow: hidden;
}
#slider {
  display: flex; height: 100%;
  transition: transform .38s cubic-bezier(.4,0,.2,1);
  will-change: transform;
}
.section {
  flex: 0 0 100%; width: 100%; height: 100%;
  overflow-y: auto; overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  background: white;
}
.section-inner {
  max-width: 860px; margin: 0 auto;
  padding: 1rem 1rem calc(1.5rem + env(safe-area-inset-bottom));
}

/* ── Section header ── */
.section-header { display: flex; align-items: center; gap: .6rem; margin-bottom: 1rem; flex-wrap: wrap; }
.num { background: #2b6cb0; color: white; border-radius: 6px; padding: 3px 9px; font-size: .85rem; font-weight: 700; flex-shrink: 0; }
.section-title { font-size: 1.15rem; font-weight: 700; color: #1a365d; }

/* ── Meta table ── */
.meta { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
.meta td { padding: .35rem .5rem; border-bottom: 1px solid #e2e8f0; font-size: .9rem; line-height: 1.4; }
.meta td:first-child { font-weight: 600; width: 100px; color: #4a5568; }

/* ── Map + elevation ── */
.imgs { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin-bottom: 1rem; }
@media (max-width: 600px) { .imgs { grid-template-columns: 1fr; } }

/* ── Embedded section Leaflet map ── */
.sec-map-wrap { position: relative; min-height: 220px; height: 100%; isolation: isolate; }
@media (max-width: 600px) { .sec-map-wrap { min-height: 200px; } }
.sec-map {
  border-radius: 10px; overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,.1);
  width: 100%; height: 100%; min-height: inherit;
  background: #e8edf2;
}
.map-btns {
  position: absolute; top: 10px; left: 10px; z-index: 1000;
  display: flex; flex-direction: column; gap: 2px;
  -webkit-transform: translateZ(0); transform: translateZ(0);
}
.map-zoom-btn, .map-expand-btn {
  background: rgba(255,255,255,.88); border: none; border-radius: 6px;
  width: 30px; height: 30px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 1px 4px rgba(0,0,0,.25); font-size: 18px; line-height: 1;
  transition: background .15s; color: #333; font-weight: bold;
}
.map-expand-btn {
  position: absolute; bottom: 10px; right: 10px; z-index: 1000;
  font-size: 14px;
  -webkit-transform: translateZ(0); transform: translateZ(0);
}
.map-zoom-btn:hover, .map-expand-btn:hover { background: rgba(255,255,255,1); }
/* ── Fullscreen map modal ── */
#map-modal {
  display: none; position: fixed; inset: 0; z-index: 400;
  background: rgba(0,0,0,.35); backdrop-filter: blur(3px);
}
#map-modal.open { display: block; }
#map-modal-inner {
  position: absolute;
  inset: max(32px, env(safe-area-inset-top)) 32px max(32px, env(safe-area-inset-bottom)) 32px;
  border-radius: 18px; overflow: hidden;
  box-shadow: 0 12px 48px rgba(0,0,0,.5);
}
#map-modal-leaflet { width: 100%; height: 100%; }
#map-modal-close {
  position: absolute; top: 12px; right: 12px; z-index: 410;
  background: rgba(255,255,255,.88); border: none; border-radius: 50%;
  width: 34px; height: 34px; cursor: pointer; font-size: 1rem;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 1px 4px rgba(0,0,0,.25); transition: background .15s;
}
#map-modal-close:hover { background: white; }

/* ── Surface / road type stats bars ── */
.stats-bars { margin-bottom: 1rem; }
.stats-bar-row { margin-bottom: .55rem; }
.stats-bar-label { font-size: .75rem; font-weight: 600; color: #4a5568; text-transform: uppercase; letter-spacing: .03em; margin-bottom: .18rem; }
.stats-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #e2e8f0; }
.stats-bar-seg { height: 100%; }
.stats-legend { display: flex; flex-wrap: wrap; gap: .25rem .65rem; margin-top: .22rem; }
.stats-legend-item { display: flex; align-items: center; gap: .3rem; font-size: .72rem; color: #4a5568; }
.stats-legend-dot { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; }

/* ── Headings ── */
h4 { margin: .75rem 0 .4rem; color: #4a5568; font-size: .9rem; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; }

/* ── Turn cards ── */
.turn-cards { display: flex; flex-wrap: wrap; gap: .6rem; margin-bottom: .5rem; }
.turn-card {
  display: flex; gap: .6rem; background: #f7fafc; border: 1px solid #e2e8f0;
  border-radius: 10px; padding: .6rem; min-width: 200px; flex: 1 1 200px; align-items: flex-start;
}
.turn-photo-col { flex-shrink: 0; text-align: center; }
.turn-photo { width: 110px; height: 74px; object-fit: cover; border-radius: 7px; display: block; }
.turn-photo-na {
  width: 110px; height: 74px; background: #e2e8f0; border-radius: 7px;
  display: flex; align-items: center; justify-content: center;
  font-size: .7rem; color: #a0aec0; text-align: center; padding: .3rem;
}
.turn-info { flex: 1; font-size: .85rem; line-height: 1.4; }
.turn-dir { font-weight: 800; font-size: .95rem; margin-bottom: .2rem; }
.turn-dir.left  { color: #c05621; }
.turn-dir.right { color: #276749; }
.turn-meta { color: #4a5568; margin-bottom: .08rem; }
.turn-road { color: #718096; font-size: .78rem; margin-top: .15rem; }
.photo-src { display: block; font-size: .68rem; color: #a0aec0; margin-top: .12rem; text-align: center; }
.maps-link { font-size: .78rem; color: #3182ce; text-decoration: none; margin-left: .25rem; }
.maps-link:hover { text-decoration: underline; }

/* ── POI groups ── */
.poi-group { border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: .5rem; overflow: hidden; }
.poi-group summary {
  min-height: 44px; padding: 0 .85rem; cursor: pointer;
  font-weight: 600; font-size: .9rem;
  background: #f7fafc; color: #2d3748; list-style: none;
  display: flex; align-items: center; gap: .5rem; user-select: none; transition: background .12s;
}
.poi-group summary:active { background: #dbeafe; }
.poi-group summary::-webkit-details-marker { display: none; }
.poi-group summary::before { content: "▸"; font-size: .72rem; color: #a0aec0; transition: transform .15s; }
.poi-group[open] summary::before { transform: rotate(90deg); }
.poi-group[open] summary { background: #ebf8ff; color: #2b6cb0; }
.poi-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
.poi-table th { background: #f0f8ff; color: #4a5568; padding: .28rem .65rem; text-align: left; font-weight: 600; border-bottom: 1px solid #e2e8f0; }
.poi-table td { padding: .28rem .65rem; border-bottom: 1px solid #f0f4f8; line-height: 1.4; }
.poi-table tr:last-child td { border-bottom: none; }
.badge { background: #2b6cb0; color: white; border-radius: 99px; padding: 1px 7px; font-size: .72rem; font-weight: 700; margin-left: auto; }
.oh { color: #718096; font-size: .8rem; }
.desc { color: #718096; font-size: .8rem; font-weight: 400; }

/* ── Dots ── */
#dots {
  position: fixed;
  bottom: max(12px, env(safe-area-inset-bottom));
  left: 0; right: 0; z-index: 50;
  display: flex; justify-content: center; gap: 7px; pointer-events: none;
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: rgba(255,255,255,.35); transition: background .2s, transform .2s; }
.dot.active { background: white; transform: scale(1.25); }
@media (min-width: 768px) { #dots { display: none; } }

/* ── Desktop body-scroll layout ── */
@media (min-width: 960px) {
  html { overflow: hidden; height: 100%; }
  body { overflow-y: auto; height: 100%; background: #f0f4f8; padding-left: 280px; }
  #screen-route { position: static; }
  #viewport { position: static; overflow: visible; left: auto; right: auto; top: auto; bottom: auto; }
  #slider { display: block; transform: none !important; transition: none; }
  .section { overflow: visible; height: auto; background: white; border-radius: 14px; box-shadow: 0 2px 12px rgba(0,0,0,.08); margin-bottom: 2rem; }
  #topbar { position: sticky; top: 0; left: 280px; right: 0; width: auto; }
  body.sidebar-hidden { padding-left: 0; }
}

/* ── Loading overlay ── */
#loading {
  position: fixed; inset: 0; z-index: 500;
  background: rgba(0,0,0,.55); backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
}
#loading-box {
  background: white; border-radius: 16px; padding: 2rem 2.5rem;
  text-align: center; box-shadow: 0 8px 32px rgba(0,0,0,.25);
}
#loading-spinner {
  width: 40px; height: 40px; border: 4px solid #e2e8f0;
  border-top-color: #2b6cb0; border-radius: 50%;
  animation: spin .8s linear infinite; margin: 0 auto 1rem;
}
@keyframes spin { to { transform: rotate(360deg); } }
#loading-msg { color: #4a5568; font-size: .95rem; }

/* ── Toast ── */
#toast {
  position: fixed; bottom: max(2rem, calc(1rem + env(safe-area-inset-bottom)));
  left: 50%; transform: translateX(-50%);
  background: #2d3748; color: white;
  padding: .6rem 1.2rem; border-radius: 8px;
  font-size: .88rem; z-index: 600;
  opacity: 0; transition: opacity .3s; pointer-events: none;
  white-space: nowrap;
}
#toast.show { opacity: 1; }

/* ── Elevation canvas ── */
.elev-canvas {
  width: 100%; height: 100%; min-height: 220px; display: block;
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.1); cursor: crosshair;
}
@media (max-width: 600px) { .elev-canvas { min-height: 200px; } }

/* ── Elevation tooltip ── */
#elev-tooltip {
  position: fixed; background: #2d3748; color: white;
  padding: .3rem .6rem; border-radius: 6px; font-size: .78rem;
  pointer-events: none; z-index: 200; white-space: nowrap;
  opacity: 0; transition: opacity .1s;
}
#elev-tooltip.show { opacity: 1; }
"""

# ─────────────────────────────────────────────────────────────────────────────
VIEWER_JS = """
// ── Service worker update notification ──────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'UPDATE_AVAILABLE') {
      var v = e.data.version, d = new Date(Date.UTC(+v.slice(0,4),+v.slice(4,6)-1,+v.slice(6,8),+v.slice(8,10),+v.slice(10,12),+v.slice(12,14)));
      var ts = d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
      showToast('App updated to ' + ts + ' ✓');
    }
  });
}

// ── IndexedDB storage ──────────────────────────────────────────────────────
const DB_NAME = 'jumroutebook-store';
const DB_VER  = 1;
const STORE   = 'routes';

function openDB() {
  return new Promise((res, rej) => {
    const req = indexedDB.open(DB_NAME, DB_VER);
    req.onupgradeneeded = e => e.target.result.createObjectStore(STORE, { keyPath: 'id' });
    req.onsuccess = e => res(e.target.result);
    req.onerror   = e => rej(e.target.error);
  });
}
async function dbPut(record) {
  const db = await openDB();
  return new Promise((res, rej) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(record);
    tx.oncomplete = res; tx.onerror = e => rej(e.target.error);
  });
}
async function dbGetAll() {
  const db = await openDB();
  return new Promise((res, rej) => {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).getAll();
    req.onsuccess = e => res(e.target.result);
    req.onerror   = e => rej(e.target.error);
  });
}
async function dbDelete(id) {
  const db = await openDB();
  return new Promise((res, rej) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(id);
    tx.oncomplete = res; tx.onerror = e => rej(e.target.error);
  });
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function showLoading(msg) {
  document.getElementById('loading-msg').textContent = msg || 'Loading…';
  document.getElementById('loading').classList.remove('hidden');
}
function hideLoading() { document.getElementById('loading').classList.add('hidden'); }

let toastTimer;
function showToast(msg, ms) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms || 3000);
}

// ── File loading ────────────────────────────────────────────────────────────
document.getElementById('file-input').addEventListener('change', async function() {
  for (const file of this.files) {
    await loadFile(file);
  }
  this.value = '';
});

async function loadFile(file) {
  if (!file.name.endsWith('.jumroutebook')) {
    showToast('Not a .jumroutebook file: ' + file.name); return;
  }
  showLoading('Reading ' + file.name + '…');
  try {
    const buf = await file.arrayBuffer();
    await processZipBuffer(buf, file.name);
  } catch(e) {
    showToast('Error: ' + e.message);
  } finally { hideLoading(); }
}

async function loadFromUrl() { await _loadUrl(document.getElementById('url-input').value); }
async function loadFromUrl2() { await _loadUrl(document.getElementById('url-input2').value); }

async function _loadUrl(url) {
  url = (url || '').trim();
  if (!url) { showToast('Enter a URL first'); return; }
  showLoading('Fetching…');
  try {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const buf = await resp.arrayBuffer();
    const name = url.split('/').pop() || 'route.jumroutebook';
    await processZipBuffer(buf, name);
  } catch(e) {
    showToast('Error: ' + e.message);
  } finally { hideLoading(); }
}

async function processZipBuffer(buf, filename) {
  const zip = await JSZip.loadAsync(buf);

  // Validate
  const manifestFile = zip.file('manifest.json');
  if (!manifestFile) throw new Error('Not a valid .jumroutebook — manifest.json missing');
  const manifest = JSON.parse(await manifestFile.async('string'));
  if (manifest.format !== 'jumroutebook') throw new Error('Invalid format: ' + manifest.format);

  // Read handbook.json
  const hbFile = zip.file('handbook.json');
  if (!hbFile) throw new Error('handbook.json missing');
  const handbook = JSON.parse(await hbFile.async('string'));

  // Read all asset images → base64 data URIs
  const assets = {};
  const assetFiles = zip.filter((path) => path.startsWith('assets/') && !path.endsWith('/'));
  for (const f of assetFiles) {
    const b64 = await f.async('base64');
    const ext = f.name.split('.').pop().toLowerCase();
    const mime = ext === 'png' ? 'image/png' : 'image/jpeg';
    assets[f.name] = 'data:' + mime + ';base64,' + b64;
  }

  // Build record
  const id = manifest.title + '::' + manifest.created;
  const record = { id, manifest, handbook, assets, filename, storedAt: Date.now() };
  await dbPut(record);
  showToast('✓ Loaded: ' + manifest.title);
  renderLibrary();
}

// ── Library ─────────────────────────────────────────────────────────────────
async function renderLibrary() {
  const routes = await dbGetAll();
  const list = document.getElementById('lib-list');
  const empty = document.getElementById('lib-empty');
  const urlBottom = document.getElementById('url-row-bottom');
  list.innerHTML = '';

  if (routes.length === 0) {
    empty.style.display = '';
    urlBottom.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  urlBottom.style.display = 'flex';

  routes.sort((a, b) => b.storedAt - a.storedAt);
  for (const r of routes) {
    const m = r.manifest;
    const li = document.createElement('li');
    li.innerHTML =
      '<div class="lib-item-body">' +
        '<div class="lib-item-title">' + escHtml(m.title.replace(/\\.gpx$/i,'')) + '</div>' +
        '<div class="lib-item-meta">' + m.sections + ' sections &nbsp;·&nbsp; ' + m.total_km + ' km &nbsp;·&nbsp; ' + m.created + '</div>' +
      '</div>' +
      '<div class="lib-item-open">›</div>' +
      '<button class="lib-item-del" title="Remove" data-id="' + escHtml(r.id) + '">🗑</button>';
    li.querySelector('.lib-item-body').addEventListener('click', () => openRoute(r));
    li.querySelector('.lib-item-open').addEventListener('click', () => openRoute(r));
    li.querySelector('.lib-item-del').addEventListener('click', async (e) => {
      e.stopPropagation();
      await dbDelete(r.id);
      renderLibrary();
    });
    list.appendChild(li);
  }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Route viewer ────────────────────────────────────────────────────────────
var current = 0;
var total = 0;
var isSidebar = window.innerWidth >= 960;
var isDesktop  = window.innerWidth >= 960;
var TOPBAR_H   = 52;
var titles     = [];

function openRoute(record) {
  const { handbook, assets, manifest } = record;
  const sections = handbook.sections;
  total   = sections.length;
  current = 0;
  titles  = sections.map(s => 'Sec ' + String(s.index).padStart(2,'0') + ': ' + s.start_name + ' → ' + s.end_name);

  // Nav header
  document.getElementById('nav-route-title').textContent = manifest.title.replace(/\\.gpx$/i,'');
  document.getElementById('nav-route-meta').textContent  =
    manifest.total_km + ' km · ' + manifest.sections + ' sections';

  // Build nav list
  const navList = document.getElementById('nav-list');
  navList.innerHTML = '';
  sections.forEach((sec, i) => {
    const li = document.createElement('li');
    li.innerHTML =
      '<a class="nav-link" data-index="' + i + '" href="#sec' + sec.index + '">' +
        '<span class="nav-num">' + String(sec.index).padStart(2,'0') + '</span>' +
        '<span class="nav-text">' +
          '<span class="nav-route">' + escHtml(sec.start_name) + ' → ' + escHtml(sec.end_name) + '</span>' +
          '<span class="nav-km">' + Math.round(sec.start_km) + '–' + Math.round(sec.end_km) + ' km</span>' +
        '</span>' +
      '</a>';
    li.querySelector('.nav-link').addEventListener('click', (e) => {
      e.preventDefault(); closeNav(); goTo(i);
    });
    navList.appendChild(li);
  });

  // Build slides
  const slider = document.getElementById('slider');
  slider.innerHTML = '';
  sections.forEach((sec, i) => {
    const div = document.createElement('div');
    div.className = 'section';
    div.id = 'sec' + sec.index;
    div.setAttribute('data-index', i);
    div.innerHTML = renderSection(sec, assets);
    slider.appendChild(div);
  });

  // Draw elevation canvases and attach hover
  currentBook = record;
  setTimeout(() => {
    initSectionMaps();
  }, 50);

  // Dots
  const dotsEl = document.getElementById('dots');
  dotsEl.innerHTML = sections.map((_, i) =>
    '<div class="dot' + (i===0?' active':'') + '" data-i="' + i + '"></div>'
  ).join('');

  // Show route screen
  document.getElementById('screen-library').classList.add('hidden');
  document.getElementById('screen-route').classList.remove('hidden');
  document.body.classList.remove('sidebar-hidden');
  document.getElementById('sidebar-toggle').innerHTML = '&#8249;';

  TOPBAR_H = document.getElementById('topbar').getBoundingClientRect().height || 52;

  // IntersectionObserver for desktop scroll sync
  if (window.IntersectionObserver) {
    if (window._routeObserver) window._routeObserver.disconnect();
    window._routeObserver = new IntersectionObserver(function(entries) {
      if (!isDesktop) return;
      entries.forEach(function(entry) {
        if (entry.isIntersecting) {
          var idx = parseInt(entry.target.getAttribute('data-index'), 10);
          if (!isNaN(idx) && idx !== current) { current = idx; updateUI(); }
        }
      });
    }, { root: document.body, rootMargin: '-' + TOPBAR_H + 'px 0px -55% 0px', threshold: 0 });
    document.querySelectorAll('.section').forEach(s => window._routeObserver.observe(s));
  }

  updateUI();

  // Auto-navigate to nearest section based on current GPS position
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      function(pos) {
        jumpToNearestSection(pos.coords.latitude, pos.coords.longitude);
      },
      function() {} // silently ignore denial or timeout
    );
  }
}

// ── Section HTML renderer ───────────────────────────────────────────────────
const POI_ICON = {
  restaurant:'🍽️', cafe:'☕', fast_food:'🍔', bar:'🍺', pub:'🍺',
  drinking_water:'💧', bicycle_repair_station:'🔧', fuel:'⛽',
  bicycle:'🚲', viewpoint:'👁️', hotel:'🏨', guest_house:'🏠',
  hostel:'🛏️', camp_site:'⛺', information:'ℹ️', spring:'💧', wayside_cross:'✝️',
};
const PRIORITY_OPEN = new Set();

const STATS_COLORS = {
  asphalt: '#48bb78', gravel: '#ed8936', unpaved: '#e53e3e',
  cycleway: '#3182ce', path: '#805ad5',
  minor_road: '#718096', main_road: '#e53e3e', unknown: '#a0aec0',
};
function renderStatsBar(label, stats) {
  if (!stats || !Object.keys(stats).length) return '';
  const segs = Object.entries(stats).filter(([,p]) => p > 0).map(([cat, pct]) =>
    '<div class="stats-bar-seg" style="width:'+pct+'%;background:'+(STATS_COLORS[cat]||'#a0aec0')+'" title="'+cat+' '+pct+'%"></div>'
  ).join('');
  const legend = Object.entries(stats).filter(([,p]) => p > 0).map(([cat, pct]) =>
    '<span class="stats-legend-item">' +
    '<span class="stats-legend-dot" style="background:'+(STATS_COLORS[cat]||'#a0aec0')+'"></span>' +
    cat.replace(/_/g,' ')+' '+pct+'%</span>'
  ).join('');
  return '<div class="stats-bar-row"><div class="stats-bar-label">'+label+'</div>' +
    '<div class="stats-bar">'+segs+'</div>' +
    '<div class="stats-legend">'+legend+'</div></div>';
}

function renderSection(sec, assets) {
  const mapKey  = 'assets/section_' + String(sec.index).padStart(2,'0') + '/map.png';
  const elevKey = 'assets/section_' + String(sec.index).padStart(2,'0') + '/elevation.png';
  const mapSrc  = assets[mapKey]  || null;
  const elevSrc = assets[elevKey] || null;

  const mapTag  = '<div class="sec-map-wrap">' +
    '<div class="sec-map" id="map-' + sec.index + '"></div>' +
    '<div class="map-btns">' +
      '<button class="map-zoom-btn" data-sec-index="' + sec.index + '" data-zoom="+1" aria-label="Zoom in">+</button>' +
      '<button class="map-zoom-btn" data-sec-index="' + sec.index + '" data-zoom="-1" aria-label="Zoom out">−</button>' +
    '</div>' +
    '<button class="map-expand-btn" data-sec-index="' + sec.index + '" title="Fullscreen map" aria-label="Fullscreen map">⛶</button>' +
    '</div>';
  const elevTag = (sec.elevation_profile && sec.elevation_profile.length)
    ? '<canvas class="elev-canvas" id="elev-' + sec.index + '" data-sec-index="' + sec.index + '" width="400" height="220"></canvas>'
    : '<p><em>Elevation unavailable</em></p>';

  // Turns
  let turnsHtml = '';
  if (sec.notable_turns && sec.notable_turns.length) {
    const cards = sec.notable_turns.map(t => {
      const photo = t.photo || {};
      const thumb = photo.thumb_b64 || null;
      const dirCls = (t.direction||'').toLowerCase().includes('left') ? 'left' : 'right';
      const srcBadge = photo.source === 'wikimedia' ? '<span class="photo-src">📷 Commons</span>'
                     : photo.source === 'osm_map'   ? '<span class="photo-src">🗺️ OSM map</span>'
                     : photo.date                   ? '<span class="photo-src">📷 KartaView ' + photo.date + '</span>'
                     : thumb                        ? '<span class="photo-src">📷 KartaView</span>'
                     : '';
      const imgHtml = thumb
        ? '<a href="' + escHtml(photo.full_url||'#') + '" target="_blank"><img src="' + thumb + '" class="turn-photo" alt="Photo"></a>' + srcBadge
        : '<div class="turn-photo-na">📷 No photo</div>';
      const mapsHref = 'https://www.google.com/maps/search/?api=1&query=' + t.lat + ',' + t.lon;
      return '<div class="turn-card">' +
        '<div class="turn-photo-col">' + imgHtml + '</div>' +
        '<div class="turn-info">' +
          '<div class="turn-dir ' + dirCls + '">' + escHtml(t.direction||'') + '</div>' +
          '<div class="turn-meta">@ ' + (t.dist_km||0).toFixed(1) + ' km</div>' +
          (t.road_name ? '<div class="turn-road">' + escHtml(t.road_name) + '</div>' : '') +
          '<a class="maps-link" href="' + mapsHref + '" target="_blank">📍 Maps</a>' +
        '</div>' +
      '</div>';
    }).join('');
    turnsHtml = '<h4>Notable turns</h4><div class="turn-cards">' + cards + '</div>';
  }

  // POIs
  let wpsHtml = '';
  if (sec.waypoints && sec.waypoints.length) {
    const groups = {};
    sec.waypoints.forEach(wp => {
      const cat = wp.category || 'other';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(wp);
    });
    const order = ['restaurant','cafe','fast_food','bar','pub','drinking_water','spring',
      'bicycle','bicycle_repair_station','fuel','hotel','guest_house','hostel','camp_site',
      'viewpoint','information','wayside_cross','other'];
    const sorted = Object.keys(groups).sort((a,b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia<0?99:ia) - (ib<0?99:ib);
    });
    const groupHtml = sorted.map(cat => {
      const wps = groups[cat];
      const icon = POI_ICON[cat] || '📌';
      const open = PRIORITY_OPEN.has(cat) ? ' open' : '';
      const rows = wps.map(wp => {
        const gm = 'https://www.google.com/maps/search/?api=1&query=' + wp.lat + ',' + wp.lon;
        const gmLink = '<a href="' + gm + '" target="_blank">📍</a>';
        let linkCell = gmLink;
        if (wp.website) linkCell = gmLink + ' <a href="' + escHtml(wp.website) + '" target="_blank">🔗</a>';
        else if (wp.opening_hours) linkCell = gmLink + ' <span class="oh">' + escHtml(wp.opening_hours) + '</span>';
        return '<tr><td>' + escHtml(wp.name||'') + (wp.description ? ' <span class="desc">'+escHtml(wp.description)+'</span>' : '') +
          '</td><td>' + (wp.dist_km||0).toFixed(1) + ' km</td><td>' + linkCell + '</td></tr>';
      }).join('');
      return '<details class="poi-group"' + open + '><summary>' +
        icon + ' ' + cat.replace(/_/g,' ') +
        '<span class="badge">' + wps.length + '</span>' +
        '</summary>' +
        '<table class="poi-table"><thead><tr><th>Name</th><th>Dist</th><th>Links</th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table>' +
        '</details>';
    }).join('');
    wpsHtml = '<h4>Points of interest</h4>' + groupHtml;
  }

  return '<div class="section-inner">' +
    '<div class="section-header">' +
      '<span class="num">' + String(sec.index).padStart(2,'0') + '</span>' +
      '<span class="section-title">' + escHtml(sec.start_name) + ' → ' + escHtml(sec.end_name) + '</span>' +
    '</div>' +
    '<table class="meta">' +
      '<tr><td>Distance</td><td>' + (sec.distance_km||0).toFixed(1) + ' km</td></tr>' +
      '<tr><td>Route km</td><td>' + Math.round(sec.start_km) + ' – ' + Math.round(sec.end_km) + ' km</td></tr>' +
      '<tr><td>Ascent</td><td>↑ ' + (sec.elevation_gain_m||0) + ' m &nbsp; ↓ ' + (sec.elevation_loss_m||0) + ' m</td></tr>' +
    '</table>' +
    (sec.surface_stats || sec.road_stats ? '<div class="stats-bars">' +
      (sec.surface_stats ? renderStatsBar('Surface', sec.surface_stats) : '') +
      (sec.road_stats    ? renderStatsBar('Road type', sec.road_stats)  : '') +
    '</div>' : '') +
    '<div class="imgs">' + mapTag + elevTag + '</div>' +
    turnsHtml + wpsHtml +
  '</div>';
}

// ── Per-section embedded Leaflet maps ───────────────────────────────────────
var currentBook = null;

const SURF_COLORS = { a:'#48bb78', g:'#ed8936', u:'#e53e3e', k:'#a0aec0' };

function initSectionMaps() {
  // Destroy any existing maps
  if (window._secMaps) {
    Object.values(window._secMaps).forEach(function(m) { try { m.remove(); } catch(e){} });
  }
  window._secMaps = {};

  currentBook.handbook.sections.forEach(function(sec) {
    const el = document.getElementById('map-' + sec.index);
    if (!el) return;

    const map = L.map(el, { zoomControl: false, scrollWheelZoom: false });
    window._secMaps[sec.index] = map;

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>', maxZoom: 19
    }).addTo(map);

    // Colored track
    if (sec.track_points && sec.track_points.length > 1) {
      const pts = sec.track_points;
      let seg = [pts[0]], segColor = SURF_COLORS[pts[0].s] || '#a0aec0';
      for (let i = 1; i < pts.length; i++) {
        const c = SURF_COLORS[pts[i].s] || '#a0aec0';
        if (c === segColor) { seg.push(pts[i]); }
        else {
          L.polyline(seg.map(function(p){return [p.la,p.lo];}), {color:segColor,weight:4,opacity:.9}).addTo(map);
          seg = [pts[i-1], pts[i]]; segColor = c;
        }
      }
      L.polyline(seg.map(function(p){return [p.la,p.lo];}), {color:segColor,weight:4,opacity:.9}).addTo(map);
      map.fitBounds(L.latLngBounds(pts.map(function(p){return [p.la,p.lo];})));
    } else {
      map.setView([sec.start_lat, sec.start_lon], 13);
    }

    // Start / end markers
    L.circleMarker([sec.start_lat, sec.start_lon],
      {radius:7,color:'white',fillColor:'#276749',fillOpacity:1,weight:2})
      .bindPopup('<b>Start</b><br>' + escHtml(sec.start_name)).addTo(map);
    L.circleMarker([sec.end_lat, sec.end_lon],
      {radius:7,color:'white',fillColor:'#c05621',fillOpacity:1,weight:2})
      .bindPopup('<b>End</b><br>' + escHtml(sec.end_name)).addTo(map);

    // Turn markers
    (sec.notable_turns || []).forEach(function(t) {
      L.circleMarker([t.lat, t.lon],
        {radius:5,color:'#2b6cb0',fillColor:'white',fillOpacity:1,weight:2})
        .bindPopup(escHtml((t.direction||'')+' '+(t.bearing_change||'')+'\u00b0')).addTo(map);
    });
  });

  // Draw elevation canvases (needs maps already set up for hover)
  drawElevationCanvases();

  // Fullscreen map button handler
  document.querySelectorAll('.map-expand-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.secIndex);
      const sec = currentBook.handbook.sections.find(function(s) { return s.index === idx; });
      if (sec) openMapModal(sec);
    });
  });

  // Zoom button handler
  document.querySelectorAll('.map-zoom-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.secIndex);
      const delta = parseInt(btn.dataset.zoom);
      const map = window._secMaps && window._secMaps[idx];
      if (map) map.zoomIn ? (delta > 0 ? map.zoomIn() : map.zoomOut()) : null;
    });
  });
}

// ── Fullscreen map modal ─────────────────────────────────────────────────────
function openMapModal(sec) {
  const modal = document.getElementById('map-modal');
  const container = document.getElementById('map-modal-leaflet');
  modal.classList.add('open');

  // Destroy previous modal map if any
  if (window._modalMap) { window._modalMap.remove(); window._modalMap = null; }
  container.innerHTML = '';

  const map = L.map(container, { zoomControl: true, scrollWheelZoom: true });
  window._modalMap = map;

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>', maxZoom: 19
  }).addTo(map);

  const SURF_COLORS = { a:'#48bb78', g:'#ed8936', u:'#e53e3e', k:'#a0aec0' };
  if (sec.track_points && sec.track_points.length > 1) {
    const pts = sec.track_points;
    let seg = [pts[0]], segColor = SURF_COLORS[pts[0].s] || '#a0aec0';
    for (let i = 1; i < pts.length; i++) {
      const c = SURF_COLORS[pts[i].s] || '#a0aec0';
      if (c === segColor) { seg.push(pts[i]); }
      else {
        L.polyline(seg.map(function(p){return [p.la,p.lo];}), {color:segColor,weight:4,opacity:.9}).addTo(map);
        seg = [pts[i-1], pts[i]]; segColor = c;
      }
    }
    L.polyline(seg.map(function(p){return [p.la,p.lo];}), {color:segColor,weight:4,opacity:.9}).addTo(map);
    map.fitBounds(L.latLngBounds(pts.map(function(p){return [p.la,p.lo];})));
  } else {
    map.setView([sec.start_lat, sec.start_lon], 13);
  }

  L.circleMarker([sec.start_lat, sec.start_lon],
    {radius:7,color:'white',fillColor:'#276749',fillOpacity:1,weight:2})
    .bindPopup('<b>Start</b><br>' + escHtml(sec.start_name)).addTo(map);
  L.circleMarker([sec.end_lat, sec.end_lon],
    {radius:7,color:'white',fillColor:'#c05621',fillOpacity:1,weight:2})
    .bindPopup('<b>End</b><br>' + escHtml(sec.end_name)).addTo(map);

  (sec.notable_turns || []).forEach(function(t) {
    L.circleMarker([t.lat, t.lon],
      {radius:5,color:'#2b6cb0',fillColor:'white',fillOpacity:1,weight:2})
      .bindPopup(escHtml((t.direction||'') + ' ' + (t.bearing_change||'') + '\\u00b0')).addTo(map);
  });

  document.getElementById('map-modal-close').onclick = closeMapModal;
  modal.addEventListener('click', function onBg(e) {
    if (e.target === modal) { closeMapModal(); modal.removeEventListener('click', onBg); }
  });
}

function closeMapModal() {
  document.getElementById('map-modal').classList.remove('open');
  if (window._modalMap) { window._modalMap.remove(); window._modalMap = null; }
}
function drawElevationCanvases() {
  // Compute global elevation range across all sections for comparable Y axis
  var globalMinE = Infinity, globalMaxE = -Infinity;
  currentBook.handbook.sections.forEach(function(sec) {
    if (!sec.elevation_profile) return;
    sec.elevation_profile.forEach(function(p) {
      if (p.e < globalMinE) globalMinE = p.e;
      if (p.e > globalMaxE) globalMaxE = p.e;
    });
  });
  if (!isFinite(globalMinE)) { globalMinE = 0; globalMaxE = 1000; }

  document.querySelectorAll('.elev-canvas').forEach(function(canvas) {
    const idx = parseInt(canvas.dataset.secIndex) - 1;
    const sec = currentBook && currentBook.handbook.sections[idx];
    if (!sec) return;
    drawElevation(canvas, sec, globalMinE, globalMaxE);
    attachElevationHover(canvas, globalMinE, globalMaxE);
  });
}

function drawElevation(canvas, sec, globalMinE, globalMaxE) {
  const prof = sec.elevation_profile;
  if (!prof || !prof.length) return;
  const W = canvas.offsetWidth || 400;
  const H = canvas.offsetHeight || 220;
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  // Use global range if provided, fall back to local for standalone redraws
  const minE = (globalMinE !== undefined) ? globalMinE : Math.min.apply(null, prof.map(function(p){return p.e;}));
  const maxE = (globalMaxE !== undefined) ? globalMaxE : Math.max.apply(null, prof.map(function(p){return p.e;}));
  const maxD = prof[prof.length-1].d;
  const pad = {l:44, r:10, t:14, b:30};
  const W2 = W - pad.l - pad.r;
  const H2 = H - pad.t - pad.b;
  const eRange = maxE - minE || 1;

  function xp(d) { return pad.l + (d / maxD) * W2; }
  function yp(e) { return pad.t + (1 - (e - minE) / eRange) * H2; }

  // Grid lines + Y labels
  ctx.font = '10px -apple-system,sans-serif';
  ctx.fillStyle = '#718096';
  ctx.textAlign = 'right';
  const ticks = [minE, Math.round((minE + maxE) / 2), maxE];
  ticks.forEach(function(v) {
    const y = yp(v);
    ctx.fillText(Math.round(v) + 'm', pad.l - 5, y + 4);
    ctx.beginPath(); ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 0.5;
    ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
  });

  // X axis labels
  ctx.textAlign = 'center';
  [0, maxD / 2, maxD].forEach(function(v) {
    ctx.fillText(v.toFixed(1) + 'km', xp(v), H - pad.b + 18);
  });

  // Fill
  ctx.beginPath();
  ctx.moveTo(xp(prof[0].d), H - pad.b);
  prof.forEach(function(p) { ctx.lineTo(xp(p.d), yp(p.e)); });
  ctx.lineTo(xp(prof[prof.length-1].d), H - pad.b);
  ctx.closePath();
  ctx.fillStyle = 'rgba(49,130,206,.18)';
  ctx.fill();

  // Line
  ctx.beginPath();
  prof.forEach(function(p, i) {
    i === 0 ? ctx.moveTo(xp(p.d), yp(p.e)) : ctx.lineTo(xp(p.d), yp(p.e));
  });
  ctx.strokeStyle = '#3182ce'; ctx.lineWidth = 1.8; ctx.stroke();

  // Cache metadata on canvas element
  canvas._prof = prof; canvas._pad = pad; canvas._maxD = maxD;
  canvas._minE = minE; canvas._maxE = maxE; canvas._sec = sec;
  canvas._globalMinE = minE; canvas._globalMaxE = maxE;
}

function showElevTooltip(canvas, xPos, yPos, d, e) {
  const tip = document.getElementById('elev-tooltip');
  const rect = canvas.getBoundingClientRect();
  tip.textContent = d.toFixed(2) + ' km  ·  ' + Math.round(e) + ' m';
  tip.classList.add('show');
  let tx = rect.left + xPos + 12;
  let ty = rect.top  + yPos - 28;
  if (tx + 140 > window.innerWidth) tx = rect.left + xPos - 150;
  if (ty < 4) ty = rect.top + yPos + 12;
  tip.style.left = tx + 'px';
  tip.style.top  = ty + 'px';
}
function hideElevTooltip() {
  document.getElementById('elev-tooltip').classList.remove('show');
}

function attachElevationHover(canvas, globalMinE, globalMaxE) {
  // Store global range on canvas so redraws triggered from hover use same scale
  if (globalMinE !== undefined) { canvas._globalMinE = globalMinE; canvas._globalMaxE = globalMaxE; }

  function onMove(clientX, clientY) {
    const prof = canvas._prof, pad = canvas._pad, maxD = canvas._maxD;
    if (!prof) return;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    if (x < pad.l) return;
    const d = (x - pad.l) / (canvas.offsetWidth - pad.l - pad.r) * maxD;
    // Find closest profile point
    let best = prof[0], bestDiff = Math.abs(prof[0].d - d);
    for (let i = 1; i < prof.length; i++) {
      const diff = Math.abs(prof[i].d - d);
      if (diff < bestDiff) { bestDiff = diff; best = prof[i]; }
    }
    // Redraw canvas base (with global scale), then crosshair overlay
    drawElevation(canvas, canvas._sec, canvas._globalMinE, canvas._globalMaxE);
    const W = canvas.offsetWidth, H = canvas.offsetHeight;
    const dpr = window.devicePixelRatio || 1;
    const ctx = canvas.getContext('2d');
    // drawElevation already set canvas.width/height and called ctx.scale(dpr,dpr)
    // so coordinates here are in CSS pixels (the scale is already applied)
    const eRange = canvas._maxE - canvas._minE || 1;
    const xPos = pad.l + (best.d / maxD) * (W - pad.l - canvas._pad.r);
    const yPos = pad.t + (1 - (best.e - canvas._minE) / eRange) * (H - pad.t - pad.b);
    ctx.save();
    ctx.beginPath(); ctx.strokeStyle = 'rgba(45,55,72,.45)'; ctx.lineWidth = 1;
    ctx.setLineDash([3,3]);
    ctx.moveTo(xPos, pad.t); ctx.lineTo(xPos, H - pad.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(xPos, yPos, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#3182ce'; ctx.fill();
    ctx.strokeStyle = 'white'; ctx.lineWidth = 2; ctx.stroke();
    ctx.restore();

    showElevTooltip(canvas, xPos, yPos, best.d, best.e);

    // Move dot on this section's embedded Leaflet map
    if (canvas._sec && canvas._sec.track_points) {
      const secMap = window._secMaps && window._secMaps[canvas._sec.index];
      if (secMap) {
        const tp = canvas._sec.track_points;
        const ti = Math.min(Math.round(best.d / maxD * (tp.length - 1)), tp.length - 1);
        const pt = tp[ti];
        if (!canvas._elevMarker) {
          canvas._elevMarker = L.circleMarker([pt.la, pt.lo],
            {radius:7, color:'#2b6cb0', fillColor:'white', fillOpacity:1, weight:2})
            .addTo(secMap);
        } else {
          canvas._elevMarker.setLatLng([pt.la, pt.lo]);
        }
      }
    }
  }

  canvas.addEventListener('mousemove', function(e) { onMove(e.clientX, e.clientY); });
  canvas.addEventListener('touchmove', function(e) {
    e.preventDefault(); onMove(e.touches[0].clientX, e.touches[0].clientY);
  }, {passive: false});
  canvas.addEventListener('mouseleave', function() {
    hideElevTooltip();
    if (canvas._elevMarker) {
      const secMap = window._secMaps && window._secMaps[canvas._sec && canvas._sec.index];
      if (secMap) secMap.removeLayer(canvas._elevMarker);
      canvas._elevMarker = null;
    }
    drawElevation(canvas, canvas._sec, canvas._globalMinE, canvas._globalMaxE);
  });
}

// ── GPS nearest-section ─────────────────────────────────────────────────────
function haversineDist(lat1, lon1, lat2, lon2) {
  var R = 6371;
  var dLat = (lat2 - lat1) * Math.PI / 180;
  var dLon = (lon2 - lon1) * Math.PI / 180;
  var a = Math.sin(dLat/2)*Math.sin(dLat/2) +
          Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*
          Math.sin(dLon/2)*Math.sin(dLon/2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function jumpToNearestSection(lat, lon) {
  if (!currentBook) return;
  var sections = currentBook.handbook.sections;
  var bestIdx = 0, bestDist = Infinity;
  sections.forEach(function(sec, i) {
    (sec.track_points || []).forEach(function(tp) {
      var d = haversineDist(lat, lon, tp.la, tp.lo);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    });
  });
  if (bestIdx !== current) goTo(bestIdx);
}

// ── Navigation ──────────────────────────────────────────────────────────────
function goTo(n) {
  if (n < 0 || n >= total) return;
  current = n;
  const slider = document.getElementById('slider');
  if (isDesktop) {
    const el = document.getElementById('sec' + (n + 1));
    if (el) {
      var y = el.getBoundingClientRect().top + document.body.scrollTop - TOPBAR_H - 8;
      document.body.scrollTo({ top: y, behavior: 'smooth' });
    }
  } else {
    slider.style.transform = 'translateX(-' + (current * 100) + '%)';
    const slide = slider.children[current];
    if (slide) slide.scrollTop = 0;
    // Invalidate Leaflet map size after slide transition (double-tap for iOS repaint)
    setTimeout(function() {
      const sec = currentBook && currentBook.handbook.sections[n];
      if (sec && window._secMaps && window._secMaps[sec.index]) {
        window._secMaps[sec.index].invalidateSize();
      }
    }, 400);
    setTimeout(function() {
      const sec = currentBook && currentBook.handbook.sections[n];
      if (sec && window._secMaps && window._secMaps[sec.index]) {
        window._secMaps[sec.index].invalidateSize();
      }
    }, 800);
  }
  updateUI();
}

function updateUI() {
  const navLinks = document.querySelectorAll('.nav-link');
  document.getElementById('topbar-title').textContent  = titles[current] || 'Route';
  document.getElementById('section-counter').textContent = (current+1) + ' / ' + total;
  document.getElementById('prev-btn').disabled = current === 0;
  document.getElementById('next-btn').disabled = current === total - 1;
  document.querySelectorAll('.dot').forEach((d,i) => d.classList.toggle('active', i===current));
  navLinks.forEach((l,i) => l.classList.toggle('active', i===current));
}

function showLibrary() {
  document.getElementById('screen-route').classList.add('hidden');
  document.getElementById('screen-library').classList.remove('hidden');
  // Reset desktop body scroll
  if (isDesktop) document.body.scrollTop = 0;
}

function toggleNav() {
  if (isSidebar) { toggleSidebar(); return; }
  document.getElementById('nav-overlay').classList.toggle('open');
  document.getElementById('nav-drawer').classList.toggle('open');
}
function closeNav() {
  document.getElementById('nav-overlay').classList.remove('open');
  document.getElementById('nav-drawer').classList.remove('open');
}
function toggleSidebar() {
  const hidden = document.body.classList.toggle('sidebar-hidden');
  document.getElementById('sidebar-toggle').innerHTML = hidden ? '&#8250;' : '&#8249;';
  setTimeout(() => {
    TOPBAR_H = document.getElementById('topbar').getBoundingClientRect().height || 52;
  }, 320);
}

// ── Swipe ───────────────────────────────────────────────────────────────────
var tx0, ty0, swiping = false;
const vp = document.getElementById('viewport');
vp.addEventListener('touchstart', e => {
  if (isSidebar) return;
  tx0 = e.touches[0].clientX; ty0 = e.touches[0].clientY; swiping = true;
}, { passive: true });
vp.addEventListener('touchmove', e => {
  if (!swiping || isSidebar) return;
  const dx = e.touches[0].clientX - tx0, dy = e.touches[0].clientY - ty0;
  if (Math.abs(dx) > Math.abs(dy) + 10) e.preventDefault();
}, { passive: false });
vp.addEventListener('touchend', e => {
  if (!swiping || isSidebar) return;
  swiping = false;
  const dx = e.changedTouches[0].clientX - tx0, dy = e.changedTouches[0].clientY - ty0;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) goTo(dx < 0 ? current+1 : current-1);
}, { passive: true });

// ── Keyboard ────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (document.getElementById('screen-route').classList.contains('hidden')) return;
  if (e.key === 'ArrowRight') goTo(current+1);
  if (e.key === 'ArrowLeft')  goTo(current-1);
  if (e.key === 'Escape') {
    if (document.getElementById('map-modal').classList.contains('open')) { closeMapModal(); }
    else { showLibrary(); }
  }
});

// ── Resize ───────────────────────────────────────────────────────────────────
window.addEventListener('resize', () => {
  isSidebar = window.innerWidth >= 960;
  isDesktop  = window.innerWidth >= 960;
  TOPBAR_H   = document.getElementById('topbar').getBoundingClientRect().height || 52;
  if (!isSidebar) { document.body.classList.remove('sidebar-hidden'); closeNav(); }
  const slider = document.getElementById('slider');
  if (!isDesktop) slider.style.transform = 'translateX(-' + (current*100) + '%)';
  else slider.style.transform = '';
});

// ── Init ─────────────────────────────────────────────────────────────────────
renderLibrary();
"""

if __name__ == '__main__':
    main()
