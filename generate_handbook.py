#!/usr/bin/env python3
"""
GPX Route Handbook Generator
Parses a GPX file and generates a section-by-section handbook:
  - Static map PNG (OSM tiles via staticmap library)
  - Elevation profile PNG (via srtm.py + matplotlib)
  - Metadata JSON per section
  - Self-contained HTML handbook

Section breaks are snapped to named villages/towns via OSM Nominatim
reverse geocoding, so each section runs from one real place to another.
"""

import argparse
import base64
import io
import json
import math
import os
import sys
import time
import zipfile

# Ensure log output appears immediately even when stdout is piped/captured
sys.stdout.reconfigure(line_buffering=True)

import gpxpy
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
import srtm
from staticmap import StaticMap, Line, CircleMarker

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
SECTION_KM = 10.0
MAX_KM = None
OUTPUT_DIR = "handbook"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {"User-Agent": "gpx-handbook-generator/1.0"}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
KARTAVIEW_URL = "https://api.openstreetcam.org/1.0/list/nearby-photos/"
KARTAVIEW_BASE = "https://openstreetcam.org/"

# POI categories to query from Overpass — cycling-relevant
POI_FILTERS = [
    ('amenity', 'restaurant'),
    ('amenity', 'cafe'),
    ('amenity', 'fast_food'),
    ('amenity', 'bar'),
    ('amenity', 'pub'),
    ('amenity', 'drinking_water'),
    ('amenity', 'bicycle_repair_station'),
    ('amenity', 'fuel'),
    ('shop',    'bicycle'),
    ('tourism', 'viewpoint'),
    ('tourism', 'hotel'),
    ('tourism', 'guest_house'),
    ('tourism', 'hostel'),
    ('tourism', 'camp_site'),
    ('tourism', 'information'),
    ('natural', 'spring'),
    ('historic','wayside_cross'),
]

POI_ICON = {
    'restaurant': '🍽️', 'cafe': '☕', 'fast_food': '🍔', 'bar': '🍺', 'pub': '🍺',
    'drinking_water': '💧', 'bicycle_repair_station': '🔧', 'fuel': '⛽',
    'bicycle': '🚲', 'viewpoint': '👁️', 'hotel': '🏨', 'guest_house': '🏠',
    'hostel': '🛏️', 'camp_site': '⛺', 'information': 'ℹ️',
    'spring': '💧', 'wayside_cross': '✝️',
}


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

_cache_dir = None
_nominatim_cache = {}
_overpass_cache = {}

def init_cache(output_dir):
    global _cache_dir, _nominatim_cache, _overpass_cache, _photo_embed_cache
    _cache_dir = os.path.join(output_dir, ".cache")
    os.makedirs(_cache_dir, exist_ok=True)

    nom_path = os.path.join(_cache_dir, "nominatim.json")
    if os.path.exists(nom_path):
        with open(nom_path, encoding="utf-8") as f:
            _nominatim_cache = json.load(f)
        print(f"  Loaded {len(_nominatim_cache)} cached Nominatim entries")

    ovp_path = os.path.join(_cache_dir, "overpass.json")
    if os.path.exists(ovp_path):
        with open(ovp_path, encoding="utf-8") as f:
            _overpass_cache = json.load(f)
        print(f"  Loaded {len(_overpass_cache)} cached Overpass entries")

    kv_path = os.path.join(_cache_dir, "kartaview.json")
    if os.path.exists(kv_path):
        with open(kv_path, encoding="utf-8") as f:
            _kartaview_cache = json.load(f)
        print(f"  Loaded {len(_kartaview_cache)} cached KartaView entries")

    wm_path = os.path.join(_cache_dir, "wikimedia.json")
    if os.path.exists(wm_path):
        with open(wm_path, encoding="utf-8") as f:
            _wikimedia_cache = json.load(f)
        print(f"  Loaded {len(_wikimedia_cache)} cached Wikimedia entries")

    pe_path = os.path.join(_cache_dir, "photo_embed.json")
    if os.path.exists(pe_path):
        with open(pe_path, encoding="utf-8") as f:
            _photo_embed_cache = json.load(f)
        print(f"  Loaded {len(_photo_embed_cache)} cached photo embeds")

def _save_nominatim_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "nominatim.json"), "w", encoding="utf-8") as f:
        json.dump(_nominatim_cache, f, ensure_ascii=False)

def _save_overpass_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "overpass.json"), "w", encoding="utf-8") as f:
        json.dump(_overpass_cache, f, ensure_ascii=False)

_kartaview_cache = {}
_photo_embed_cache = {}  # url -> "data:image/jpeg;base64,..."

def _save_kartaview_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "kartaview.json"), "w", encoding="utf-8") as f:
        json.dump(_kartaview_cache, f, ensure_ascii=False)

def _save_photo_embed_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "photo_embed.json"), "w", encoding="utf-8") as f:
        json.dump(_photo_embed_cache, f, ensure_ascii=False)

def fetch_kartaview_photo(lat, lon, radius=50):
    """
    Return the closest KartaView photo within `radius` metres of (lat, lon).
    Downloads and base64-embeds the thumbnail immediately — KartaView URLs expire.
    Returns dict with thumb_b64, full_url, heading, date — or None if no coverage.
    Disk-cached by rounded coordinate key.
    """
    key = f"{round(lat,4)},{round(lon,4)}"
    if key in _kartaview_cache:
        return _kartaview_cache[key]

    result = None
    try:
        resp = requests.post(
            KARTAVIEW_URL,
            data={"lat": lat, "lng": lon, "radius": radius, "ipp": 1, "page": 1},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("currentPageItems", [])
        if items:
            p = items[0]
            thumb_url = KARTAVIEW_BASE + p["lth_name"]
            full_url  = KARTAVIEW_BASE + p["name"]
            # Download thumbnail immediately — URLs are temporary
            try:
                img_resp = requests.get(thumb_url, timeout=10, allow_redirects=True,
                                        headers={"User-Agent": "gpx-handbook-generator/1.0"})
                img_resp.raise_for_status()
                ct = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                if not ct.startswith("image/"):
                    ct = "image/jpeg"
                thumb_b64 = f"data:{ct};base64," + base64.b64encode(img_resp.content).decode()
                result = {
                    "thumb_b64": thumb_b64,
                    "full_url":  full_url,
                    "heading":   p.get("heading", ""),
                    "date":      p.get("shot_date", "")[:10] if p.get("shot_date") else "",
                }
            except Exception as e:
                print(f"    KartaView: thumbnail download failed: {e}")
    except Exception as exc:
        print(f"    KartaView warning: {exc}")

    _kartaview_cache[key] = result
    _save_kartaview_cache()
    return result


WIKIMEDIA_API = "https://en.wikipedia.org/w/api.php"
_wikimedia_cache = {}

def _save_wikimedia_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "wikimedia.json"), "w", encoding="utf-8") as f:
        json.dump(_wikimedia_cache, f, ensure_ascii=False)

def fetch_wikimedia_photo(lat, lon, radius=1000):
    """
    Return a geotagged Wikimedia Commons image near (lat, lon).
    Uses the Wikipedia geosearch API — no key required.
    Returns dict with thumb_url, full_url, title — or None.
    Disk-cached.
    """
    key = f"{round(lat,3)},{round(lon,3)}"
    if key in _wikimedia_cache:
        return _wikimedia_cache[key]

    result = None
    try:
        # Step 1: geosearch for nearby pages
        print(f"      Wikimedia geosearch at {lat:.4f},{lon:.4f}...")
        resp = requests.get(WIKIMEDIA_API, params={
            "action": "query", "list": "geosearch",
            "gscoord": f"{lat}|{lon}", "gsradius": radius,
            "gslimit": 10, "gsnamespace": 6,  # namespace 6 = File:
            "format": "json",
        }, timeout=10, headers={"User-Agent": "gpx-handbook-generator/1.0"})
        pages = resp.json().get("query", {}).get("geosearch", [])
        print(f"      Wikimedia: {len(pages)} nearby files found")

        # Step 2: for each nearby file page, get the image URL
        for page in pages:
            title = page["title"]  # e.g. "File:Some_image.jpg"
            img_resp = requests.get(WIKIMEDIA_API, params={
                "action": "query", "titles": title,
                "prop": "imageinfo", "iiprop": "url|thumburl",
                "iiurlwidth": 400,
                "format": "json",
            }, timeout=10, headers={"User-Agent": "gpx-handbook-generator/1.0"})
            pages_data = img_resp.json().get("query", {}).get("pages", {})
            for p in pages_data.values():
                info = (p.get("imageinfo") or [{}])[0]
                thumb = info.get("thumburl") or info.get("url")
                full  = info.get("url")
                if thumb and any(thumb.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                    # Download immediately so we have a stable base64 embed
                    try:
                        dl = requests.get(thumb, timeout=10,
                                          headers={"User-Agent": "gpx-handbook-generator/1.0"})
                        dl.raise_for_status()
                        ct = dl.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                        if not ct.startswith("image/"):
                            ct = "image/jpeg"
                        thumb_b64 = f"data:{ct};base64," + base64.b64encode(dl.content).decode()
                        result = {
                            "thumb_b64": thumb_b64,
                            "full_url":  full,
                            "title":     title.replace("File:", ""),
                            "source":    "wikimedia",
                            "heading":   "",
                            "date":      "",
                        }
                    except Exception as e:
                        print(f"    Wikimedia: thumbnail download failed: {e}")
                        continue
                    break
            if result:
                break
    except Exception as exc:
        print(f"    Wikimedia warning: {exc}")

    _wikimedia_cache[key] = result
    _save_wikimedia_cache()
    return result


def fetch_photo(lat, lon):
    """Try KartaView first, fall back to Wikimedia Commons, then OSM static map thumbnail."""
    photo = fetch_kartaview_photo(lat, lon, radius=500)
    if photo is None:
        print(f"      No KartaView — trying Wikimedia Commons...")
        photo = fetch_wikimedia_photo(lat, lon, radius=1000)
        if photo:
            print(f"      → Found: {photo['title'][:50]}")
    if photo is None:
        print(f"      No street photo — generating OSM map thumbnail...")
        photo = render_osm_thumbnail(lat, lon)
    return photo


def render_osm_thumbnail(lat, lon, width=220, height=148, zoom=15):
    """
    Fetch real OSM tile(s) and composite a map thumbnail with a red pin marker.
    Falls back to a Pillow-only placeholder if tile fetch fails.
    Disk-cached by rounded coordinate + zoom.
    """
    import math
    key = f"osm_{round(lat,4)}_{round(lon,4)}_{zoom}"
    cached = _kartaview_cache.get(key)  # reuse general in-memory cache
    if cached:
        return cached

    maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

    def _draw_marker(img, px, py):
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        r = 9
        draw.ellipse([px-r-2, py-r-2, px+r+2, py+r+2], fill="white")
        draw.ellipse([px-r,   py-r,   px+r,   py+r  ], fill="#e53e3e")
        draw.ellipse([px-3,   py-3,   px+3,   py+3  ], fill="white")

    def _lat_lon_to_tile(la, lo, z):
        n = 2 ** z
        tx = int((lo + 180) / 360 * n)
        ty = int((1 - math.log(math.tan(math.radians(la)) +
                               1 / math.cos(math.radians(la))) / math.pi) / 2 * n)
        return tx, ty

    def _tile_pixel_offset(la, lo, tx, ty, z, ts=256):
        n = 2 ** z
        px = (lo + 180) / 360 * n * ts - tx * ts
        py = (1 - math.log(math.tan(math.radians(la)) +
                            1 / math.cos(math.radians(la))) / math.pi) / 2 * n * ts - ty * ts
        return int(px), int(py)

    def _fetch_tile(tx, ty, z):
        """Return tile as PIL Image, using disk cache under cache/tiles/."""
        from PIL import Image
        if _cache_dir:
            tile_path = os.path.join(_cache_dir, "tiles", str(z), str(tx), f"{ty}.png")
            if os.path.exists(tile_path):
                return Image.open(tile_path).convert("RGB")
        else:
            tile_path = None
        url = f"https://tile.openstreetmap.org/{z}/{tx}/{ty}.png"
        hdrs = {"User-Agent": "gpx-handbook-generator/1.0"}
        resp = requests.get(url, timeout=8, headers=hdrs)
        resp.raise_for_status()
        if tile_path:
            os.makedirs(os.path.dirname(tile_path), exist_ok=True)
            with open(tile_path, "wb") as fh:
                fh.write(resp.content)
            return Image.open(tile_path).convert("RGB")
        return Image.open(io.BytesIO(resp.content)).convert("RGB")

    try:
        from PIL import Image
        cx, cy = _lat_lon_to_tile(lat, lon, zoom)
        ts = 256
        grid = Image.new("RGB", (ts * 3, ts * 3))
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                tile = _fetch_tile(cx + dx, cy + dy, zoom)
                grid.paste(tile, ((dx + 1) * ts, (dy + 1) * ts))

        # Pixel position of the point within the 3×3 composite
        px, py = _tile_pixel_offset(lat, lon, cx - 1, cy - 1, zoom, ts)

        # Crop to desired size centered on point
        left = max(0, min(px - width  // 2, ts * 3 - width))
        top  = max(0, min(py - height // 2, ts * 3 - height))
        crop = grid.crop((left, top, left + width, top + height))
        _draw_marker(crop, px - left, py - top)

        buf = io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        result = {"thumb_b64": b64, "full_url": maps_url, "source": "osm_map", "date": ""}
        _kartaview_cache[key] = result
        _save_kartaview_cache()
        return result

    except Exception as e:
        print(f"      OSM tile fetch failed ({e}), using placeholder...")

    # Pillow-only fallback
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (width, height), color="#e8edf2")
        draw = ImageDraw.Draw(img)
        for x in range(0, width, 22):
            draw.line([(x, 0), (x, height)], fill="#d0d8e0", width=1)
        for y in range(0, height, 22):
            draw.line([(0, y), (width, y)], fill="#d0d8e0", width=1)
        _draw_marker(img, width // 2, height // 2)
        coord_text = f"{lat:.4f}, {lon:.4f}"
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), coord_text, font=font)
        tw = bbox[2] - bbox[0]
        draw.rectangle([(width//2 - tw//2 - 4, height - 20), (width//2 + tw//2 + 4, height - 4)],
                       fill="#2b6cb0")
        draw.text((width//2 - tw//2, height - 19), coord_text, fill="white", font=font)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return {"thumb_b64": b64, "full_url": maps_url, "source": "osm_map", "date": ""}
    except Exception as e2:
        print(f"      Map placeholder failed: {e2}")
        return None


def embed_photo_url(url):
    """
    Download a photo URL and return a base64 data-URI for self-contained HTML.
    Disk-cached by URL so re-runs don't re-download.
    Returns None on failure.
    """
    if not url:
        return None
    if url in _photo_embed_cache:
        return _photo_embed_cache[url]
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "gpx-handbook-generator/1.0"})
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not ct.startswith("image/"):
            ct = "image/jpeg"
        data_uri = f"data:{ct};base64," + base64.b64encode(resp.content).decode()
        _photo_embed_cache[url] = data_uri
        _save_photo_embed_cache()
        return data_uri
    except Exception as exc:
        print(f"    Photo download warning ({url[:60]}): {exc}")
        _photo_embed_cache[url] = None
        _save_photo_embed_cache()
        return None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine(p1, p2):
    R = 6371.0
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def bearing(p1, p2):
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y))


def bearing_diff(b1, b2):
    diff = abs(b1 - b2)
    return min(diff, 360 - diff)


# ---------------------------------------------------------------------------
# GPX loading
# ---------------------------------------------------------------------------

def load_points(gpx_path):
    with open(gpx_path, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            points.extend(segment.points)
    waypoints = list(gpx.waypoints)
    return points, waypoints


def cumulative_distances(points):
    dists = [0.0]
    for i in range(1, len(points)):
        dists.append(dists[-1] + haversine(points[i - 1], points[i]))
    return dists


# ---------------------------------------------------------------------------
# Reverse geocoding via Nominatim
# ---------------------------------------------------------------------------

def _nominatim(lat, lon, zoom=14):
    """Raw Nominatim call; returns full response dict. Disk-cached."""
    key = f"{round(lat,4)},{round(lon,4)},{zoom}"
    if key in _nominatim_cache:
        return _nominatim_cache[key]
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json",
                    "zoom": zoom, "addressdetails": 1},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        time.sleep(1.1)  # Nominatim rate limit: max 1 req/s
    except Exception as exc:
        print(f"    Geocode warning: {exc}")
        data = {}
    _nominatim_cache[key] = data
    _save_nominatim_cache()
    return data


def reverse_geocode(lat, lon):
    """Return a short place/village name."""
    data = _nominatim(lat, lon, zoom=14)
    addr = data.get("address", {})
    return (
        addr.get("village")
        or addr.get("hamlet")
        or addr.get("town")
        or addr.get("city_district")
        or addr.get("suburb")
        or addr.get("city")
        or addr.get("county")
        or data.get("display_name", "").split(",")[0]
        or f"{lat:.4f},{lon:.4f}"
    )


def reverse_geocode_road(lat, lon):
    """Return the road/path name at this coordinate."""
    data = _nominatim(lat, lon, zoom=17)  # zoom 17 = street level
    addr = data.get("address", {})
    return (
        addr.get("cycleway")
        or addr.get("path")
        or addr.get("road")
        or addr.get("pedestrian")
        or addr.get("footway")
        or ""
    )


# ---------------------------------------------------------------------------
# Overpass POI fetch
# ---------------------------------------------------------------------------

def fetch_pois(sec_points, sec_dists, corridor_m=150):
    """
    Query Overpass for cycling-relevant POIs within `corridor_m` metres of
    the section track. Returns list of dicts sorted by dist_km into section.
    Results are disk-cached by bounding box key.
    """
    # Build bounding box with a small pad
    lats = [p.latitude  for p in sec_points]
    lons = [p.longitude for p in sec_points]
    pad = corridor_m / 111_000  # degrees (~1° ≈ 111 km)
    bbox = f"{min(lats)-pad:.5f},{min(lons)-pad:.5f},{max(lats)+pad:.5f},{max(lons)+pad:.5f}"

    if bbox in _overpass_cache:
        elements = _overpass_cache[bbox]
    else:
        # Build Overpass QL union of all POI filters
        union_parts = "\n  ".join(
            f'node["{k}"="{v}"]({bbox});'
            for k, v in POI_FILTERS
        )
        query = f"[out:json][timeout:25];\n(\n  {union_parts}\n);\nout body;"
        print(f"    Querying Overpass API (bbox {bbox[:30]}...)...")
        try:
            resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=30)
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            print(f"    Overpass returned {len(elements)} raw elements")
        except Exception as exc:
            print(f"    Overpass warning: {exc}")
            elements = []
        _overpass_cache[bbox] = elements
        _save_overpass_cache()

    # For each OSM node, find the closest track point and compute distance
    class _P:
        def __init__(self, lat, lon):
            self.latitude, self.longitude = lat, lon

    pois = []
    for el in elements:
        elat, elon = el.get("lat"), el.get("lon")
        if elat is None:
            continue
        ep = _P(elat, elon)

        # Distance from POI to nearest track point
        closest_i = min(range(len(sec_points)), key=lambda i: haversine(sec_points[i], ep))
        dist_to_track = haversine(sec_points[closest_i], ep) * 1000  # metres
        if dist_to_track > corridor_m:
            continue

        tags = el.get("tags", {})
        # Determine category and icon
        category = None
        for k, v in POI_FILTERS:
            if tags.get(k) == v:
                category = v
                break
        icon = POI_ICON.get(category, "📍")

        name = (tags.get("name")
                or tags.get("brand")
                or category.replace("_", " ").title()
                or "POI")

        pois.append({
            "name":       name,
            "category":   category,
            "icon":       icon,
            "lat":        elat,
            "lon":        elon,
            "dist_km":    round(sec_dists[closest_i], 2),
            "dist_m":     round(dist_to_track),
            "opening_hours": tags.get("opening_hours", ""),
            "website":    tags.get("website", "") or tags.get("contact:website", ""),
        })

    # Deduplicate by name+position, sort by distance into section
    seen = set()
    unique = []
    for p in sorted(pois, key=lambda x: x["dist_km"]):
        key = (p["name"], round(p["lat"], 4), round(p["lon"], 4))
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


# ---------------------------------------------------------------------------
# Section splitting — snapped to named places
# ---------------------------------------------------------------------------

def find_named_boundary(points, dists, target_km, snap_km=3.0):
    """
    Starting from the point closest to target_km, scan ±snap_km along the
    route and return the index of the first point that reverse-geocodes to a
    named place different from the previous section's start name.

    Strategy:
      1. Build a small candidate window around target_km (±snap_km).
      2. Sample every ~200m within that window.
      3. Geocode each candidate; accept the first one that has a non-empty name.
      4. Return its index in `points`.
    """
    # Find index closest to target_km
    center = min(range(len(dists)), key=lambda i: abs(dists[i] - target_km))

    lo = next((i for i in range(len(dists)) if dists[i] >= target_km - snap_km), 0)
    hi = next((i for i in range(len(dists)) if dists[i] >= target_km + snap_km), len(dists) - 1)

    # Sample roughly every 200m inside the window
    step = max(1, (hi - lo) // 30)
    candidates = list(range(center, hi, step)) + list(range(center - step, lo, -step))
    # Deduplicate while preserving proximity order
    seen = set()
    ordered = []
    for c in candidates:
        if c not in seen and 0 <= c < len(points):
            seen.add(c)
            ordered.append(c)

    for idx in ordered:
        name = reverse_geocode(points[idx].latitude, points[idx].longitude)
        if name:
            return idx, name

    return center, reverse_geocode(points[center].latitude, points[center].longitude)


def split_sections_named(points, dists, section_km, max_km=None):
    """
    Split into sections of ~section_km, snapping each boundary to a
    named OSM place. Returns list of (start_idx, end_idx, start_name, end_name).
    """
    if max_km is not None:
        cutoff = next((i for i, d in enumerate(dists) if d >= max_km), len(points) - 1)
        points = points[: cutoff + 1]
        dists = dists[: cutoff + 1]

    total = dists[-1]
    boundaries = [0]  # always start at index 0

    target = section_km
    while target < total - section_km * 0.4:
        idx, name = find_named_boundary(points, dists, target)
        # Avoid duplicates
        if idx > boundaries[-1]:
            boundaries.append(idx)
        target = dists[idx] + section_km

    boundaries.append(len(points) - 1)

    # Geocode boundary names (start + end of each section)
    print("  Geocoding section boundaries...")
    boundary_names = {}
    for b in boundaries:
        if b not in boundary_names:
            boundary_names[b] = reverse_geocode(points[b].latitude, points[b].longitude)

    sections = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        sections.append((s, e, boundary_names[s], boundary_names[e]))

    return sections, points, dists


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------

def get_elevations(points):
    elev_data = srtm.get_data()
    return [elev_data.get_elevation(p.latitude, p.longitude) or 0 for p in points]


def elevation_stats(elevs):
    gain = loss = 0.0
    for i in range(1, len(elevs)):
        diff = elevs[i] - elevs[i - 1]
        if diff > 0:
            gain += diff
        else:
            loss += abs(diff)
    return gain, loss


# ---------------------------------------------------------------------------
# Image generators
# ---------------------------------------------------------------------------

def render_elevation_png(sec_dists, sec_elevs, path, title=""):
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.fill_between(sec_dists, sec_elevs, alpha=0.35, color="steelblue")
    ax.plot(sec_dists, sec_elevs, color="steelblue", linewidth=1.5)
    ax.set_xlabel("Distance (km)")
    ax.set_ylabel("Elevation (m)")
    gain, loss = elevation_stats(sec_elevs)
    ax.set_title(f"{title}  |  +{gain:.0f}m  /  -{loss:.0f}m")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def render_map_png(points, path, width=600, height=400):
    m = StaticMap(width, height)
    coords = [(p.longitude, p.latitude) for p in points]
    m.add_line(Line(coords, "blue", 3))
    m.add_marker(CircleMarker(coords[0], "green", 12))
    m.add_marker(CircleMarker(coords[-1], "red", 12))
    m.render().save(path)


# ---------------------------------------------------------------------------
# Notable turns
# ---------------------------------------------------------------------------

CARDINAL = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

def cardinal(deg):
    """Convert bearing degrees to 8-point cardinal label."""
    idx = round(deg / 45) % 8
    return CARDINAL[idx]

def turn_direction(b_before, b_after):
    """Return 'LEFT' or 'RIGHT' based on signed bearing change."""
    diff = (b_after - b_before + 360) % 360
    return "RIGHT" if diff <= 180 else "LEFT"

def detect_turns(points, sec_dists, sec_elevs, threshold=45):
    """
    Return enriched turn list. Each entry has:
      - dist_km       : distance into section
      - elevation_m   : elevation at turn point
      - bearing_change: degrees of heading change
      - direction     : LEFT / RIGHT
      - heading_after : cardinal direction after turn (e.g. SE)
      - bearing_after : numeric bearing after turn
      - road          : road/path name from Nominatim (best-effort)
      - lat, lon
    """
    turns = []
    step = max(1, len(points) // 50)
    prev_b = None
    candidates = 0
    for i in range(step, len(points) - step, step):
        b_after = bearing(points[i - step], points[i + step])
        if prev_b is not None:
            diff = bearing_diff(prev_b, b_after)
            if diff > threshold:
                candidates += 1
                lat, lon = points[i].latitude, points[i].longitude
                print(f"      Turn {candidates}: {lat:.4f},{lon:.4f} — geocoding road name...")
                road = reverse_geocode_road(lat, lon)
                print(f"      Turn {candidates}: fetching photo...")
                photo = fetch_photo(lat, lon)
                if photo:
                    src = photo.get("source", "kartaview")
                    print(f"      Turn {candidates}: photo found ({src})")
                else:
                    print(f"      Turn {candidates}: no photo available")
                turns.append({
                    "dist_km":        round(sec_dists[i], 2),
                    "elevation_m":    round(sec_elevs[i]),
                    "bearing_change": round(diff, 1),
                    "direction":      turn_direction(prev_b, b_after),
                    "heading_after":  cardinal(b_after),
                    "bearing_after":  round(b_after % 360, 1),
                    "road":           road,
                    "photo":          photo,
                    "lat":            lat,
                    "lon":            lon,
                })
        prev_b = b_after
    print(f"      → {len(turns)} notable turns detected")
    return turns


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(gpx_path, section_km=SECTION_KM, max_km=None, output_dir=OUTPUT_DIR, output_name=None, title=None):
    print(f"Loading GPX: {gpx_path}")
    os.makedirs(output_dir, exist_ok=True)
    init_cache(output_dir)

    points, waypoints = load_points(gpx_path)
    print(f"  Total track points: {len(points)}")

    dists = cumulative_distances(points)
    print(f"  Total distance: {dists[-1]:.1f} km")
    if max_km:
        print(f"  Limiting to first {max_km} km")

    sections, points, dists = split_sections_named(points, dists, section_km, max_km)
    print(f"  Sections: {len(sections)}")

    print("Fetching SRTM elevation data...")
    elevs = get_elevations(points)
    print(f"  Elevation data ready for {len(elevs)} points")

    sections_dir = os.path.join(output_dir, "sections")
    os.makedirs(sections_dir, exist_ok=True)

    handbook = {
        "source_file": os.path.basename(gpx_path),
        "title":       title or os.path.basename(gpx_path),
        "total_km": round(dists[-1], 2),
        "section_target_km": section_km,
        "sections": [],
    }

    for idx, (s, e, start_name, end_name) in enumerate(sections):
        sec_points = points[s: e + 1]
        sec_dists  = [d - dists[s] for d in dists[s: e + 1]]
        sec_elevs  = elevs[s: e + 1]
        sec_km     = round(sec_dists[-1], 2)
        gain, loss = elevation_stats(sec_elevs)
        label   = f"{start_name} → {end_name}"
        print(f"\n  [Section {idx+1:02d}/{len(sections)}] {label} ({sec_km} km)")
        print(f"  [Section {idx+1:02d}] Detecting notable turns...")
        turns = detect_turns(sec_points, sec_dists, sec_elevs)
        sec_dir = os.path.join(sections_dir, f"section_{idx + 1:02d}")
        os.makedirs(sec_dir, exist_ok=True)

        map_path = os.path.join(sec_dir, "map.png")
        if os.path.exists(map_path):
            print(f"  [Section {idx+1:02d}] Map already cached, skipping render")
        else:
            print(f"  [Section {idx+1:02d}] Rendering map...")
            try:
                render_map_png(sec_points, map_path)
            except Exception as exc:
                print(f"    WARNING: map render failed: {exc}")
                map_path = None

        elev_path = os.path.join(sec_dir, "elevation.png")
        if os.path.exists(elev_path):
            print(f"  [Section {idx+1:02d}] Elevation chart already cached, skipping render")
        else:
            print(f"  [Section {idx+1:02d}] Rendering elevation chart...")
            try:
                render_elevation_png(sec_dists, sec_elevs, elev_path, title=label)
            except Exception as exc:
                print(f"    WARNING: elevation render failed: {exc}")
                elev_path = None

        # GPX waypoints: snap to nearest track point, include if in this section
        sec_waypoints = []
        for wp in waypoints:
            class _P:
                def __init__(self, lat, lon):
                    self.latitude, self.longitude = lat, lon
            wp_pt = _P(wp.latitude, wp.longitude)
            closest_idx = min(range(len(points)), key=lambda i: haversine(points[i], wp_pt))
            if s <= closest_idx <= e:
                dist_into = round(dists[closest_idx] - dists[s], 2)
                sec_waypoints.append({
                    "name":        wp.name or "Waypoint",
                    "category":    wp.symbol or "waypoint",
                    "icon":        "📍",
                    "lat":         wp.latitude,
                    "lon":         wp.longitude,
                    "dist_km":     dist_into,
                    "dist_m":      0,
                    "description": wp.description or "",
                    "opening_hours": "",
                    "website":     "",
                    "source":      "gpx",
                })

        # OSM POIs along the section
        print(f"  [Section {idx+1:02d}] Fetching OSM POIs...")
        osm_pois = fetch_pois(sec_points, sec_dists)
        for p in osm_pois:
            p["source"] = "osm"
            p["description"] = ""
        print(f"             → {len(osm_pois)} POIs found")

        # Merge and sort by dist_km
        all_pois = sorted(sec_waypoints + osm_pois, key=lambda x: x["dist_km"])

        handbook["sections"].append({
            "index":             idx + 1,
            "label":             label,
            "start_name":        start_name,
            "end_name":          end_name,
            "start_km":          round(dists[s], 2),
            "end_km":            round(dists[e], 2),
            "distance_km":       sec_km,
            "elevation_gain_m":  round(gain),
            "elevation_loss_m":  round(loss),
            "start_lat":         sec_points[0].latitude,
            "start_lon":         sec_points[0].longitude,
            "end_lat":           sec_points[-1].latitude,
            "end_lon":           sec_points[-1].longitude,
            "waypoints":         all_pois,
            "notable_turns":     turns,
            "map_png":           os.path.relpath(map_path,  output_dir) if map_path  else None,
            "elevation_png":     os.path.relpath(elev_path, output_dir) if elev_path else None,
        })

    json_path = os.path.join(output_dir, "handbook.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(handbook, f, indent=2, ensure_ascii=False)
    print(f"\nHandbook JSON written: {json_path}")

    html_path = os.path.join(output_dir, "handbook_selfcontained.html")
    write_html(handbook, output_dir, html_path)
    print(f"Self-contained HTML written: {html_path}")

    rb_path = write_routebook(handbook, output_dir, output_name)
    print(f"Routebook written:      {rb_path}")

    return handbook


# ---------------------------------------------------------------------------
# .jumroutebook output  (ZIP with manifest + data + assets)
# ---------------------------------------------------------------------------

def write_routebook(handbook, output_dir, output_name=None):
    """
    Pack the handbook into a self-contained .jumroutebook file.

    ZIP layout:
      manifest.json          — format identifier + summary metadata
      handbook.json          — full route data (sections, POIs, turns)
      assets/
        section_01/map.png
        section_01/elevation.png
        section_02/...
    """
    import datetime

    title = handbook.get("title") or handbook.get("source_file", "route").rsplit(".", 1)[0]
    if output_name:
        safe = output_name
    else:
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    rb_path = os.path.join(output_dir, f"{safe}.jumroutebook")

    manifest = {
        "format":    "jumroutebook",
        "version":   "1.0",
        "generator": "gpx-handbook-generator",
        "title":     handbook.get("title") or handbook.get("source_file", ""),
        "total_km":  handbook.get("total_km", 0),
        "sections":  len(handbook.get("sections", [])),
        "created":   datetime.date.today().isoformat(),
    }

    with zipfile.ZipFile(rb_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # manifest
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        # handbook data — strip map_png / elevation_png paths (assets are in ZIP)
        import copy
        hb_export = copy.deepcopy(handbook)
        for sec in hb_export.get("sections", []):
            sec.pop("map_png", None)
            sec.pop("elevation_png", None)
        zf.writestr("handbook.json", json.dumps(hb_export, indent=2, ensure_ascii=False))

        # assets
        for sec in handbook.get("sections", []):
            idx = sec["index"]
            folder = f"assets/section_{idx:02d}"
            for key, arc_name in [("map_png", "map.png"), ("elevation_png", "elevation.png")]:
                rel = sec.get(key)
                if rel:
                    abs_path = os.path.join(output_dir, rel)
                    if os.path.exists(abs_path):
                        zf.write(abs_path, f"{folder}/{arc_name}")

    return rb_path


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

def img_to_base64(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def write_html(handbook, output_dir, html_path):
    n = len(handbook["sections"])
    print(f"Building HTML for {n} sections...")
    sections_html = []
    for sec in handbook["sections"]:
        sec_label = sec.get("label", sec.get("name", ""))
        print(f"  HTML section: {sec_label}...")
        map_b64  = img_to_base64(os.path.join(output_dir, sec["map_png"]))  if sec["map_png"]  else None
        elev_b64 = img_to_base64(os.path.join(output_dir, sec["elevation_png"])) if sec["elevation_png"] else None

        map_tag  = (f'<img src="data:image/png;base64,{map_b64}"  style="max-width:100%;border-radius:8px;">'
                    if map_b64  else "<p><em>Map unavailable</em></p>")
        elev_tag = (f'<img src="data:image/png;base64,{elev_b64}" style="max-width:100%;border-radius:8px;">'
                    if elev_b64 else "<p><em>Elevation unavailable</em></p>")

        if sec["waypoints"]:
            # Group POIs by category
            from collections import defaultdict
            groups = defaultdict(list)
            for wp in sec["waypoints"]:
                groups[wp.get("category", "other")].append(wp)

            # Priority order — high-priority groups start open, rest collapsed
            PRIORITY_OPEN = {
                'restaurant', 'cafe', 'fast_food', 'bar', 'pub',
                'drinking_water', 'spring', 'bicycle', 'bicycle_repair_station',
                'viewpoint', 'hotel', 'guest_house', 'hostel', 'camp_site', 'waypoint',
            }
            CAT_LABEL = {
                'restaurant': '🍽️ Food & Restaurants',
                'cafe': '☕ Cafes',
                'fast_food': '🍔 Fast Food',
                'bar': '🍺 Bars & Pubs',
                'pub': '🍺 Bars & Pubs',
                'drinking_water': '💧 Drinking Water',
                'spring': '💧 Natural Springs',
                'bicycle': '🚲 Bike Shops',
                'bicycle_repair_station': '🔧 Bike Repair',
                'viewpoint': '👁️ Viewpoints',
                'hotel': '🏨 Hotels',
                'guest_house': '🏠 Guest Houses',
                'hostel': '🛏️ Hostels',
                'camp_site': '⛺ Campsites',
                'fuel': '⛽ Fuel Stations',
                'information': 'ℹ️ Information',
                'wayside_cross': '✝️ Wayside Crosses',
                'waypoint': '📍 GPX Waypoints',
            }

            # Merge bar+pub into one group for display
            merged = defaultdict(list)
            for cat, items in groups.items():
                display_key = 'bar' if cat in ('bar', 'pub') else cat
                merged[display_key].extend(items)

            # Sort groups: priority-open first, then rest alphabetically
            def group_sort_key(cat):
                return (0 if cat in PRIORITY_OPEN else 1, cat)

            group_blocks = []
            uid = 0
            for cat in sorted(merged.keys(), key=group_sort_key):
                items = sorted(merged[cat], key=lambda x: x["dist_km"])
                label = CAT_LABEL.get(cat, f"📍 {cat.replace('_',' ').title()}")
                is_open = False
                open_attr = " open" if is_open else ""
                rows = ""
                for wp in items:
                    oh = wp.get('opening_hours', '')
                    website = wp.get('website', '')
                    lat_w, lon_w = wp.get('lat', ''), wp.get('lon', '')
                    gmaps_link = f'<a href="https://www.google.com/maps/search/?api=1&query={lat_w},{lon_w}" target="_blank">📍</a>'
                    if website:
                        link_cell = f'{gmaps_link} <a href="{website}" target="_blank">🔗 website</a>'
                    elif oh:
                        link_cell = f'{gmaps_link} <span class="oh">{oh}</span>'
                    else:
                        link_cell = gmaps_link
                    desc_span = f' <span class="desc">{wp["description"]}</span>' if wp.get('description') else ''
                    rows += (
                        f"<tr>"
                        f"<td style='white-space:nowrap'>{wp['dist_km']} km</td>"
                        f"<td><strong>{wp['name']}</strong>{desc_span}</td>"
                        f"<td style='white-space:nowrap;color:#888'>{wp.get('dist_m',0)}m</td>"
                        f"<td>{link_cell}</td>"
                        f"</tr>"
                    )
                group_blocks.append(f"""
<details{open_attr} class="poi-group">
  <summary>{label} <span class="badge">{len(items)}</span></summary>
  <table class="poi-table">
    <thead><tr><th>At km</th><th>Name</th><th>Off-route</th><th>Hours / Link</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</details>""")
                uid += 1

            total = sum(len(v) for v in merged.values())
            wps_html = f"<h4>Points of Interest <span class='badge' style='background:#718096'>{total}</span></h4>" + "".join(group_blocks)
        else:
            wps_html = "<h4>Points of Interest</h4><p style='font-size:.85rem;color:#666'>No POIs found near this section.</p>"

        if sec["notable_turns"]:
            turn_cards = ""
            for t in sec["notable_turns"]:
                photo = t.get("photo")
                dir_class = "left" if t["direction"] == "LEFT" else "right"
                if photo:
                    if photo.get("source") == "wikimedia":
                        source_badge = '<span class="photo-src">📷 Commons</span>'
                    elif photo.get("source") == "osm_map":
                        source_badge = '<span class="photo-src">🗺️ OSM map</span>'
                    elif photo.get("date"):
                        source_badge = f'<span class="photo-src">📷 KartaView {photo["date"]}</span>'
                    else:
                        source_badge = '<span class="photo-src">📷 KartaView</span>'
                    thumb_b64 = photo.get("thumb_b64")
                    if thumb_b64:
                        img_tag = (f'<a href="{photo["full_url"]}" target="_blank">'
                                   f'<img src="{thumb_b64}" class="turn-photo" '
                                   f'alt="Photo at turn">'
                                   f'</a>{source_badge}')
                    else:
                        img_tag = '<div class="turn-photo-na">No photo</div>'
                else:
                    img_tag = '<div class="turn-photo-na">No photo</div>'
                turn_cards += f"""
<div class="turn-card">
  <div class="turn-photo-col">{img_tag}</div>
  <div class="turn-info">
    <div class="turn-dir {dir_class}">{t['direction']}</div>
    <div class="turn-meta">{t['dist_km']} km &nbsp;·&nbsp; {t['elevation_m']} m</div>
    <div class="turn-meta">{t['bearing_change']}° → {t['heading_after']} ({t['bearing_after']}°)</div>
    <div class="turn-road">{t.get('road') or ''} <a href="https://www.google.com/maps/@{t['lat']},{t['lon']},3a,75y,{t.get('bearing_after',0)}h,90t/data=!3m1!1e1" target="_blank" class="maps-link">📍 Street View</a></div>
  </div>
</div>"""
            turns_html = f"<h4>Notable Turns</h4><div class='turn-cards'>{turn_cards}</div>"
        else:
            turns_html = "<h4>Notable Turns</h4><p style='font-size:.85rem;color:#666'>No sharp turns in this section.</p>"

        sections_html.append(f"""
<div class="section" id="sec{sec['index']}" data-index="{sec['index'] - 1}">
  <div class="section-inner">
    <div class="section-header">
      <span class="num">Section {sec['index']:02d}</span>
      <span class="section-title">{sec['start_name']} → {sec['end_name']}</span>
    </div>
    <table class="meta">
      <tr><td>Distance</td><td>{sec['distance_km']} km &nbsp;(route km {sec['start_km']}–{sec['end_km']})</td></tr>
      <tr><td>Elevation</td><td>+{sec['elevation_gain_m']}m &nbsp;/ &nbsp;-{sec['elevation_loss_m']}m</td></tr>
      <tr><td>Start</td><td>{sec['start_lat']:.5f}, {sec['start_lon']:.5f}</td></tr>
      <tr><td>End</td><td>{sec['end_lat']:.5f}, {sec['end_lon']:.5f}</td></tr>
    </table>
    <div class="imgs">
      <div>{map_tag}</div>
      <div>{elev_tag}</div>
    </div>
    {turns_html}
    {wps_html}
  </div>
</div>
""")

    # Build nav menu items
    nav_items = "".join(
        f'<li><a class="nav-link" data-index="{i}" href="#sec{sec["index"]}">'
        f'<span class="nav-num">{sec["index"]:02d}</span>'
        f'<span class="nav-text">'
        f'<span class="nav-route">{sec["start_name"]} → {sec["end_name"]}</span>'
        f'<span class="nav-km">{sec["start_km"]:.0f}–{sec["end_km"]:.0f} km</span>'
        f'</span>'
        f'</a></li>'
        for i, sec in enumerate(handbook["sections"])
    )

    total_sections = len(handbook["sections"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<title>Route Handbook — {handbook['source_file']}</title>

<!-- PWA / Add to Home Screen -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Route">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#2b6cb0">

<!-- Touch icon (bicycle emoji rendered to canvas, fallback SVG) -->
<link rel="apple-touch-icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 180 180'%3E%3Crect width='180' height='180' rx='40' fill='%232b6cb0'/%3E%3Ctext x='90' y='125' font-size='96' text-anchor='middle'%3E🚴%3C/text%3E%3C/svg%3E">

<!-- Web App Manifest (inline data URI) -->
<link rel="manifest" href="data:application/manifest+json,%7B%22name%22%3A%22Route+Handbook%22%2C%22short_name%22%3A%22Route%22%2C%22display%22%3A%22standalone%22%2C%22orientation%22%3A%22portrait%22%2C%22background_color%22%3A%22%232b6cb0%22%2C%22theme_color%22%3A%22%232b6cb0%22%2C%22start_url%22%3A%22.%2F%22%2C%22icons%22%3A%5B%7B%22src%22%3A%22data%3Aimage%2Fsvg%2Bxml%2C%253Csvg+xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'+viewBox%3D'0+0+512+512'%253E%253Crect+width%3D'512'+height%3D'512'+rx%3D'80'+fill%3D'%25232b6cb0'%2F%253E%253Ctext+x%3D'256'+y%3D'370'+font-size%3D'320'+text-anchor%3D'middle'%253E%25F0%259F%259A%25B4%253C%2Ftext%253E%253C%2Fsvg%253E%22%2C%22sizes%22%3A%22512x512%22%2C%22type%22%3A%22image%2Fsvg%2Bxml%22%7D%5D%7D">
<style>
/* ── Reset & base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
* {{ -webkit-tap-highlight-color: transparent; }}
html, body {{
  height: 100%; overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 16px; line-height: 1.5;
  background: #1a202c; color: #2d3748;
}}

/* ── Top bar ── */
#topbar {{
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  height: calc(52px + env(safe-area-inset-top));
  padding-top: env(safe-area-inset-top);
  background: #2b6cb0;
  display: flex; align-items: center;
  padding-left: max(1rem, env(safe-area-inset-left));
  padding-right: max(1rem, env(safe-area-inset-right));
  gap: .75rem;
  box-shadow: 0 1px 0 rgba(0,0,0,.2), 0 2px 8px rgba(0,0,0,.25);
}}
#burger {{
  background: none; border: none; cursor: pointer;
  min-width: 44px; min-height: 44px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 5px; flex-shrink: 0; border-radius: 8px;
  transition: background .15s;
}}
#burger:active {{ background: rgba(255,255,255,.18); transform: scale(.93); }}
#burger span {{
  display: block; width: 22px; height: 2px; background: white; border-radius: 2px;
  transition: all .25s;
}}
#topbar-title {{
  color: white; font-weight: 700; font-size: 1rem; flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
#section-counter {{
  color: rgba(255,255,255,.75); font-size: .82rem; white-space: nowrap; flex-shrink: 0;
}}
#prev-btn, #next-btn {{
  background: rgba(255,255,255,.18); border: none; color: white; cursor: pointer;
  border-radius: 8px; min-width: 44px; min-height: 36px;
  font-size: 1rem; flex-shrink: 0;
  transition: background .15s, transform .1s;
  display: flex; align-items: center; justify-content: center;
}}
#prev-btn:hover, #next-btn:hover {{ background: rgba(255,255,255,.28); }}
#prev-btn:active, #next-btn:active {{ transform: scale(.9); background: rgba(255,255,255,.35); }}
#prev-btn:disabled, #next-btn:disabled {{ opacity: .3; cursor: default; transform: none; }}

/* ── Overlay (mobile drawer) ── */
#nav-overlay {{
  display: none; position: fixed; inset: 0; z-index: 200;
  background: rgba(0,0,0,.45); backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}}
#nav-overlay.open {{ display: block; }}

/* ── Sidebar / Drawer shared structure ── */
#nav-drawer {{
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 201;
  width: 280px; background: #f8fafc;
  display: flex; flex-direction: column; overflow: hidden;
  border-right: 1px solid #e2e8f0;
}}
/* Mobile: drawer slides in over content */
@media (max-width: 767px) {{
  #nav-drawer {{
    width: min(300px, 88vw);
    transform: translateX(-100%); transition: transform .28s cubic-bezier(.4,0,.2,1);
    box-shadow: 4px 0 32px rgba(0,0,0,.3);
  }}
  #nav-drawer.open {{ transform: translateX(0); }}
}}
/* Tablet+: persistent sidebar, no overlay needed */
#sidebar-toggle {{
  display: none;
  background: rgba(255,255,255,.18); border: none; color: white; cursor: pointer;
  border-radius: 7px; min-width: 32px; min-height: 32px; margin-top: 2px;
  align-items: center; justify-content: center;
  font-size: 1rem; flex-shrink: 0; transition: background .15s;
}}
#sidebar-toggle:active {{ background: rgba(255,255,255,.35); }}
@media (min-width: 768px) {{
  #nav-overlay {{ display: none !important; }}
  #nav-drawer {{
    transform: none; transition: transform .3s cubic-bezier(.4,0,.2,1);
    box-shadow: none;
  }}
  #burger {{ display: none; }}
  #section-counter {{ display: none; }}
  #sidebar-toggle {{ display: flex; }}
}}
/* Sidebar collapsed on tablet+ */
@media (min-width: 768px) {{
  body.sidebar-hidden #nav-drawer {{ transform: translateX(-100%); }}
  body.sidebar-hidden #topbar {{ left: 0; }}
  body.sidebar-hidden #viewport {{ left: 0; }}
  body.sidebar-hidden #dots {{ left: 0; }}
  body.sidebar-hidden #burger {{ display: flex; }}
  body.sidebar-hidden #section-counter {{ display: inline; }}
}}
@media (min-width: 960px) {{
  body.sidebar-hidden {{ padding-left: 0; }}
}}

#nav-header {{
  background: #2b6cb0; color: white;
  padding: max(1rem, calc(.75rem + env(safe-area-inset-top))) 1rem .85rem 1.2rem;
  font-weight: 700; font-size: 1rem; flex-shrink: 0;
  display: flex; align-items: flex-start; gap: .5rem;
}}
#nav-header-text {{ flex: 1; min-width: 0; }}
#nav-header small {{
  display: block; font-weight: 400; font-size: .78rem; opacity: .8; margin-top: 3px;
}}
#nav-list {{
  list-style: none; overflow-y: auto; flex: 1;
  -webkit-overflow-scrolling: touch;
}}
#nav-list li {{ border-bottom: 1px solid #e8edf2; }}
.nav-link {{
  display: flex; align-items: center; gap: .75rem;
  min-height: 56px; padding: 0 1.2rem;
  text-decoration: none; color: #2d3748;
  transition: background .12s;
}}
.nav-link:active {{ background: #dbeafe; }}
.nav-link:hover, .nav-link.active {{ background: #ebf8ff; color: #2b6cb0; }}
.nav-num {{
  background: #2b6cb0; color: white; border-radius: 6px;
  padding: 2px 7px; font-size: .75rem; font-weight: 700; flex-shrink: 0;
  min-width: 28px; text-align: center;
}}
.nav-link.active .nav-num {{ background: #c05621; }}
.nav-text {{ display: flex; flex-direction: column; gap: 1px; min-width: 0; }}
.nav-route {{ font-size: .88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3; }}
.nav-km {{ font-size: .74rem; color: #718096; }}
.nav-link.active .nav-km {{ color: #2b6cb0; opacity: .85; }}

/* ── Slide viewport ── */
#viewport {{
  position: fixed;
  top: calc(52px + env(safe-area-inset-top));
  left: 0; right: 0; bottom: 0;
  overflow: hidden;
}}
#slider {{
  display: flex; height: 100%;
  transition: transform .38s cubic-bezier(.4,0,.2,1);
  will-change: transform;
}}

/* ── On tablet+: shift content right of the persistent sidebar ── */
@media (min-width: 768px) {{
  #topbar {{ left: 280px; }}
  #viewport {{ left: 280px; }}
  #dots {{ left: 280px; }}
}}

/* ── Each section slide ── */
.section {{
  flex: 0 0 100%; width: 100%; height: 100%;
  overflow-y: auto; overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  background: white;
}}
.section-inner {{
  max-width: 860px; margin: 0 auto;
  padding: 1rem 1rem calc(1.5rem + env(safe-area-inset-bottom));
}}

/* ── Section header ── */
.section-header {{
  display: flex; align-items: center; gap: .6rem;
  margin-bottom: 1rem; flex-wrap: wrap;
}}
.num {{
  background: #2b6cb0; color: white; border-radius: 6px;
  padding: 3px 9px; font-size: .85rem; font-weight: 700; flex-shrink: 0;
}}
.section-title {{
  font-size: 1.15rem; font-weight: 700; color: #1a365d;
}}

/* ── Meta table ── */
.meta {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; }}
.meta td {{ padding: .35rem .5rem; border-bottom: 1px solid #e2e8f0; font-size: .9rem; line-height: 1.4; }}
.meta td:first-child {{ font-weight: 600; width: 100px; color: #4a5568; }}

/* ── Map + elevation ── */
.imgs {{
  display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin-bottom: 1rem;
}}
.imgs img {{ max-width: 100%; border-radius: 10px; display: block; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
@media (max-width: 600px) {{ .imgs {{ grid-template-columns: 1fr; }} }}

/* ── Section headings ── */
h4 {{ margin: .75rem 0 .4rem; color: #4a5568; font-size: .9rem; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; }}

/* ── Turn cards ── */
.turn-cards {{ display: flex; flex-wrap: wrap; gap: .6rem; margin-bottom: .5rem; }}
.turn-card {{
  display: flex; gap: .6rem; background: #f7fafc; border: 1px solid #e2e8f0;
  border-radius: 10px; padding: .6rem; min-width: 200px; flex: 1 1 200px;
  align-items: flex-start;
}}
.turn-photo-col {{ flex-shrink: 0; text-align: center; }}
.turn-photo {{ width: 110px; height: 74px; object-fit: cover; border-radius: 7px; display: block; }}
.turn-photo-na {{
  width: 110px; height: 74px; background: #e2e8f0; border-radius: 7px;
  display: flex; align-items: center; justify-content: center;
  font-size: .7rem; color: #a0aec0; text-align: center; padding: .3rem;
}}
.turn-info {{ flex: 1; font-size: .85rem; line-height: 1.4; }}
.turn-dir {{ font-weight: 800; font-size: .95rem; margin-bottom: .2rem; }}
.turn-dir.left  {{ color: #c05621; }}
.turn-dir.right {{ color: #276749; }}
.turn-meta {{ color: #4a5568; margin-bottom: .08rem; }}
.turn-road {{ color: #718096; font-size: .78rem; margin-top: .15rem; }}
.photo-src {{ display: block; font-size: .68rem; color: #a0aec0; margin-top: .12rem; text-align: center; }}
.maps-link {{ font-size: .78rem; color: #3182ce; text-decoration: none; margin-left: .25rem; white-space: nowrap; }}
.maps-link:hover {{ text-decoration: underline; }}

/* ── POI groups ── */
.poi-group {{
  border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: .5rem; overflow: hidden;
}}
.poi-group summary {{
  min-height: 44px; padding: 0 .85rem; cursor: pointer;
  font-weight: 600; font-size: .9rem;
  background: #f7fafc; color: #2d3748; list-style: none;
  display: flex; align-items: center; gap: .5rem; user-select: none;
  transition: background .12s;
}}
.poi-group summary:active {{ background: #dbeafe; }}
.poi-group summary::-webkit-details-marker {{ display: none; }}
.poi-group summary::before {{ content: "▸"; font-size: .72rem; color: #a0aec0; transition: transform .15s; }}
.poi-group[open] summary::before {{ transform: rotate(90deg); }}
.poi-group[open] summary {{ background: #ebf8ff; color: #2b6cb0; }}
.poi-table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
.poi-table th {{ background: #f0f8ff; color: #4a5568; padding: .28rem .65rem; text-align: left; font-weight: 600; border-bottom: 1px solid #e2e8f0; }}
.poi-table td {{ padding: .28rem .65rem; border-bottom: 1px solid #f0f4f8; line-height: 1.4; }}
.poi-table tr:last-child td {{ border-bottom: none; }}
.badge {{ background: #2b6cb0; color: white; border-radius: 99px; padding: 1px 7px; font-size: .72rem; font-weight: 700; margin-left: auto; }}
.oh {{ color: #718096; font-size: .8rem; }}
.desc {{ color: #718096; font-size: .8rem; font-weight: 400; }}

/* ── Swipe hint dots ── */
#dots {{
  position: fixed;
  bottom: max(12px, env(safe-area-inset-bottom));
  left: 0; right: 0; z-index: 50;
  display: flex; justify-content: center; gap: 7px; pointer-events: none;
}}
.dot {{
  width: 7px; height: 7px; border-radius: 50%;
  background: rgba(255,255,255,.35); transition: background .2s, transform .2s;
}}
.dot.active {{ background: white; transform: scale(1.25); }}
/* Hide dots on tablet+ (sidebar replaces them) */
@media (min-width: 768px) {{ #dots {{ display: none; }} }}

/* ── Desktop (laptop): body-scroll layout ── */
@media (min-width: 960px) {{
  html {{ overflow: hidden; height: 100%; }}
  body {{ overflow-y: auto; height: 100%; background: #f0f4f8; padding-left: 280px; }}
  #viewport {{ position: static; overflow: visible; left: auto; right: auto; top: auto; bottom: auto; }}
  #slider {{ display: block; transform: none !important; transition: none; }}
  .section {{
    overflow: visible; height: auto;
    background: white; border-radius: 14px;
    box-shadow: 0 2px 12px rgba(0,0,0,.08);
    margin-bottom: 2rem;
  }}
  #topbar {{ position: sticky; top: 0; left: 280px; right: 0; width: auto; }}
}}
</style>
</head>
<body>

<!-- Top bar -->
<div id="topbar">
  <button id="burger" aria-label="Section list" onclick="toggleNav()">
    <span></span><span></span><span></span>
  </button>
  <div id="topbar-title">Route Handbook</div>
  <span id="section-counter">1 / {total_sections}</span>
  <button id="prev-btn" onclick="goTo(current-1)" aria-label="Previous">&#8592;</button>
  <button id="next-btn" onclick="goTo(current+1)" aria-label="Next">&#8594;</button>
</div>

<!-- Nav drawer -->
<div id="nav-overlay" onclick="closeNav()"></div>
<div id="nav-drawer">
  <div id="nav-header">
    <div id="nav-header-text">
      Route Handbook
      <small>{handbook['source_file']} &nbsp;·&nbsp; {handbook['total_km']} km &nbsp;·&nbsp; {total_sections} sections</small>
    </div>
    <button id="sidebar-toggle" onclick="toggleSidebar()" aria-label="Collapse sidebar">&#8249;</button>
  </div>
  <ul id="nav-list">
    {nav_items}
  </ul>
</div>

<!-- Slide viewport -->
<div id="viewport">
  <div id="slider">
    {''.join(sections_html)}
  </div>
</div>

<!-- Dot indicators -->
<div id="dots">
  {''.join(f'<div class="dot{" active" if i==0 else ""}" data-i="{i}"></div>' for i in range(total_sections))}
</div>

<script>
var current = 0;
var total = {total_sections};
var slider = document.getElementById('slider');
var counter = document.getElementById('section-counter');
var dots = document.querySelectorAll('.dot');
var navLinks = document.querySelectorAll('.nav-link');
var titles = {json.dumps([f"Sec {sec['index']:02d}: {sec['start_name']} → {sec['end_name']}" for sec in handbook['sections']])};
var isSidebar = window.innerWidth >= 768;   // persistent sidebar visible
var isDesktop = window.innerWidth >= 960;   // body-scroll layout
var TOPBAR_H = document.getElementById('topbar').getBoundingClientRect().height || 52;

function goTo(n) {{
  if (n < 0 || n >= total) return;
  current = n;
  if (isDesktop) {{
    // Scroll so section header is fully visible below the topbar
    var el = document.getElementById('sec' + (n + 1));
    if (el) {{
      var y = el.getBoundingClientRect().top + document.body.scrollTop - TOPBAR_H - 8;
      document.body.scrollTo({{ top: y, behavior: 'smooth' }});
    }}
  }} else {{
    slider.style.transform = 'translateX(-' + (current * 100) + '%)';
    var slide = slider.children[current];
    if (slide) slide.scrollTop = 0;
  }}
  updateUI();
}}

function updateUI() {{
  counter.textContent = (current+1) + ' / ' + total;
  document.getElementById('topbar-title').textContent = titles[current] || 'Route Handbook';
  document.getElementById('prev-btn').disabled = current === 0;
  document.getElementById('next-btn').disabled = current === total - 1;
  dots.forEach(function(d, i) {{ d.classList.toggle('active', i === current); }});
  navLinks.forEach(function(l, i) {{ l.classList.toggle('active', i === current); }});
}}

function toggleNav() {{
  if (isSidebar) {{
    toggleSidebar();
  }} else {{
    document.getElementById('nav-overlay').classList.toggle('open');
    document.getElementById('nav-drawer').classList.toggle('open');
  }}
}}
function closeNav() {{
  document.getElementById('nav-overlay').classList.remove('open');
  document.getElementById('nav-drawer').classList.remove('open');
}}
function toggleSidebar() {{
  var hidden = document.body.classList.toggle('sidebar-hidden');
  var btn = document.getElementById('sidebar-toggle');
  // Flip the arrow direction
  btn.innerHTML = hidden ? '&#8250;' : '&#8249;';
  // Recalc topbar height after transition settles (layout shifts)
  setTimeout(function() {{
    TOPBAR_H = document.getElementById('topbar').getBoundingClientRect().height || 52;
  }}, 320);
}}

// Nav link clicks — always prevent default, work on both mobile and desktop
navLinks.forEach(function(link, i) {{
  link.addEventListener('click', function(e) {{
    e.preventDefault();
    closeNav();
    goTo(i);
  }});
}});

// Touch swipe — only on phone (no sidebar)
var tx0, ty0, swiping = false;
document.getElementById('viewport').addEventListener('touchstart', function(e) {{
  if (isSidebar) return;
  tx0 = e.touches[0].clientX;
  ty0 = e.touches[0].clientY;
  swiping = true;
}}, {{passive: true}});
document.getElementById('viewport').addEventListener('touchmove', function(e) {{
  if (!swiping || isSidebar) return;
  var dx = e.touches[0].clientX - tx0;
  var dy = e.touches[0].clientY - ty0;
  if (Math.abs(dx) > Math.abs(dy) + 10) e.preventDefault();
}}, {{passive: false}});
document.getElementById('viewport').addEventListener('touchend', function(e) {{
  if (!swiping || isSidebar) return;
  swiping = false;
  var dx = e.changedTouches[0].clientX - tx0;
  var dy = e.changedTouches[0].clientY - ty0;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {{
    goTo(dx < 0 ? current + 1 : current - 1);
  }}
}}, {{passive: true}});

// Keyboard arrows
document.addEventListener('keydown', function(e) {{
  if (e.key === 'ArrowRight') goTo(current + 1);
  if (e.key === 'ArrowLeft')  goTo(current - 1);
}});

/// Desktop: keep topbar title in sync as user scrolls through sections
if (window.IntersectionObserver) {{
  var observer = new IntersectionObserver(function(entries) {{
    if (!isDesktop) return;
    entries.forEach(function(entry) {{
      if (entry.isIntersecting) {{
        var idx = parseInt(entry.target.getAttribute('data-index'), 10);
        if (!isNaN(idx) && idx !== current) {{
          current = idx;
          updateUI();
        }}
      }}
    }});
  }}, {{ root: document.body, rootMargin: '-' + TOPBAR_H + 'px 0px -55% 0px', threshold: 0 }});
  document.querySelectorAll('.section').forEach(function(s) {{ observer.observe(s); }});
}}

// Responsive: recalc on resize
window.addEventListener('resize', function() {{
  var wasSidebar = isSidebar;
  isSidebar = window.innerWidth >= 768;
  isDesktop = window.innerWidth >= 960;
  TOPBAR_H = document.getElementById('topbar').getBoundingClientRect().height || 52;
  // If shrinking below tablet, clear sidebar-hidden so mobile drawer works normally
  if (!isSidebar) document.body.classList.remove('sidebar-hidden');
  if (!isDesktop) slider.style.transform = 'translateX(-' + (current * 100) + '%)';
  else slider.style.transform = '';
}});

// Init
updateUI();

// ── Service worker for offline / "Add to Home Screen" ──
if ('serviceWorker' in navigator) {{
  var swSrc = [
    'const CACHE = "handbook-v1";',
    'self.addEventListener("install", e => {{',
    '  e.waitUntil(caches.open(CACHE).then(c => c.add(self.location.pathname)));',
    '  self.skipWaiting();',
    '}});',
    'self.addEventListener("activate", e => {{',
    '  e.waitUntil(caches.keys().then(ks => Promise.all(',
    '    ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));',
    '  self.clients.claim();',
    '}});',
    'self.addEventListener("fetch", e => {{',
    '  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));',
    '}});',
  ].join('\\n');
  var blob = new Blob([swSrc], {{type: 'text/javascript'}});
  var swUrl = URL.createObjectURL(blob);
  navigator.serviceWorker.register(swUrl).catch(function() {{}});
}}
</script>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPX Route Handbook Generator")
    parser.add_argument("gpx", nargs="?", help="Path to GPX file")
    parser.add_argument("--section-km", type=float, default=None)
    parser.add_argument("--max-km",     type=float, default=None,
                        help="Only process first N km of the route")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--output-name", default=None,
                        help="Base filename for the .jumroutebook archive (without extension)")
    parser.add_argument("--title", default=None,
                        help="Display title shown in the viewer app (defaults to GPX filename)")
    parser.add_argument("--regen-html", action="store_true",
                        help="Rebuild HTML from existing handbook.json without re-running GPX processing")
    args = parser.parse_args()

    if not args.regen_html:
        # Only prompt interactively when the essential args (gpx + title + output-name) are missing
        interactive = not (args.gpx and args.title and args.output_name)

        if interactive:
            print("=" * 56)
            print("  GPX Route Handbook Generator")
            print("=" * 56)

            # GPX file
            if not args.gpx:
                while True:
                    val = input(f"  GPX file path: ").strip()
                    if val and os.path.exists(val):
                        args.gpx = val
                        break
                    print(f"    ✗ File not found: {val!r}")

            # Title
            default_title = os.path.basename(args.gpx).rsplit(".", 1)[0]
            if not args.title:
                val = input(f"  Display title [{default_title}]: ").strip()
                args.title = val if val else default_title

            # Output name
            if not args.output_name:
                safe_default = "".join(c if c.isalnum() or c in "-_ " else "_" for c in args.title).strip()
                val = input(f"  Archive filename (without .jumroutebook) [{safe_default}]: ").strip()
                args.output_name = val if val else safe_default

            # Section km
            if args.section_km is None:
                val = input(f"  Section length in km [{SECTION_KM}]: ").strip()
                try:
                    args.section_km = float(val) if val else SECTION_KM
                except ValueError:
                    args.section_km = SECTION_KM

            # Max km
            if args.max_km is None:
                val = input(f"  Limit route to first N km (leave blank for full route): ").strip()
                try:
                    args.max_km = float(val) if val else None
                except ValueError:
                    args.max_km = None

            # Output dir
            if not args.output_dir:
                val = input(f"  Output directory [{OUTPUT_DIR}]: ").strip()
                args.output_dir = val if val else OUTPUT_DIR

            print()


    # Apply defaults for anything still unset
    if args.section_km is None:
        args.section_km = SECTION_KM
    if args.output_dir is None:
        args.output_dir = OUTPUT_DIR

    if args.regen_html:
        json_path = os.path.join(args.output_dir, "handbook.json")
        if not os.path.exists(json_path):
            print(f"Error: {json_path} not found. Run without --regen-html first.")
            raise SystemExit(1)
        init_cache(args.output_dir)
        with open(json_path, encoding="utf-8") as f:
            handbook = json.load(f)
        html_path = os.path.join(args.output_dir, "handbook_selfcontained.html")
        write_html(handbook, args.output_dir, html_path)
        print(f"HTML regenerated: {html_path}")
        rb_path = write_routebook(handbook, args.output_dir, args.output_name)
        print(f"Routebook written: {rb_path}")
    else:
        result = generate(args.gpx, args.section_km, args.max_km, args.output_dir, args.output_name, args.title)
        print(f"\nDone. {len(result['sections'])} sections in '{args.output_dir}/'")
