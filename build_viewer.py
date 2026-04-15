#!/usr/bin/env python3
"""
Build viewer.html — embeds JSZip and all viewer logic into a single self-contained file.
Run:  python3 build_viewer.py
"""
import os, sys

JSZIP_PATH = "/tmp/jszip.min.js"

def main():
    if not os.path.exists(JSZIP_PATH):
        print(f"ERROR: {JSZIP_PATH} not found.")
        print("Download it with:")
        print("  curl -sL https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js -o /tmp/jszip.min.js")
        sys.exit(1)

    with open(JSZIP_PATH, encoding="utf-8") as f:
        jszip = f.read()

    html = build_viewer(jszip)
    out = "viewer.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"viewer.html written ({len(html)//1024} KB)")

def build_viewer(jszip):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<title>JumRouteBook Viewer</title>
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="RouteBook">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#2b6cb0">
<link rel="apple-touch-icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 180 180'%3E%3Crect width='180' height='180' rx='40' fill='%232b6cb0'/%3E%3Ctext x='90' y='125' font-size='96' text-anchor='middle'%3E%F0%9F%9A%B4%3C/text%3E%3C/svg%3E">
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
@media (max-width: 767px) {
  #nav-drawer {
    width: min(300px, 88vw);
    transform: translateX(-100%); transition: transform .28s cubic-bezier(.4,0,.2,1);
    box-shadow: 4px 0 32px rgba(0,0,0,.3);
  }
  #nav-drawer.open { transform: translateX(0); }
}
@media (min-width: 768px) {
  #nav-overlay { display: none !important; }
  #nav-drawer { transform: none; transition: transform .3s cubic-bezier(.4,0,.2,1); box-shadow: none; }
  #burger { display: none; }
  #section-counter { display: none; }
  #sidebar-toggle { display: flex; }
  #topbar { left: 280px; }
  #viewport { left: 280px; }
  #dots { left: 280px; }
}
@media (min-width: 768px) {
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
.imgs img { max-width: 100%; border-radius: 10px; display: block; box-shadow: 0 1px 4px rgba(0,0,0,.1); }
@media (max-width: 600px) { .imgs { grid-template-columns: 1fr; } }

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
"""

# ─────────────────────────────────────────────────────────────────────────────
VIEWER_JS = """
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
var isSidebar = window.innerWidth >= 768;
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
}

// ── Section HTML renderer ───────────────────────────────────────────────────
const POI_ICON = {
  restaurant:'🍽️', cafe:'☕', fast_food:'🍔', bar:'🍺', pub:'🍺',
  drinking_water:'💧', bicycle_repair_station:'🔧', fuel:'⛽',
  bicycle:'🚲', viewpoint:'👁️', hotel:'🏨', guest_house:'🏠',
  hostel:'🛏️', camp_site:'⛺', information:'ℹ️', spring:'💧', wayside_cross:'✝️',
};
const PRIORITY_OPEN = new Set();

function renderSection(sec, assets) {
  const mapKey  = 'assets/section_' + String(sec.index).padStart(2,'0') + '/map.png';
  const elevKey = 'assets/section_' + String(sec.index).padStart(2,'0') + '/elevation.png';
  const mapSrc  = assets[mapKey]  || null;
  const elevSrc = assets[elevKey] || null;

  const mapTag  = mapSrc  ? '<img src="' + mapSrc  + '" style="max-width:100%;border-radius:10px;">'
                          : '<p><em>Map unavailable</em></p>';
  const elevTag = elevSrc ? '<img src="' + elevSrc + '" style="max-width:100%;border-radius:10px;">'
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
    '<div class="imgs">' + mapTag + elevTag + '</div>' +
    turnsHtml + wpsHtml +
  '</div>';
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
  if (e.key === 'Escape') showLibrary();
});

// ── Resize ───────────────────────────────────────────────────────────────────
window.addEventListener('resize', () => {
  isSidebar = window.innerWidth >= 768;
  isDesktop  = window.innerWidth >= 960;
  TOPBAR_H   = document.getElementById('topbar').getBoundingClientRect().height || 52;
  if (!isSidebar) document.body.classList.remove('sidebar-hidden');
  const slider = document.getElementById('slider');
  if (!isDesktop) slider.style.transform = 'translateX(-' + (current*100) + '%)';
  else slider.style.transform = '';
});

// ── Init ─────────────────────────────────────────────────────────────────────
renderLibrary();
"""

if __name__ == '__main__':
    main()
