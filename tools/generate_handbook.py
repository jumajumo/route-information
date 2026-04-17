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
OUTPUT_DIR = "output/routebook"
CACHE_BASE = "cache"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {"User-Agent": "gpx-handbook-generator/1.0"}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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
# Surface / road type classification
# ---------------------------------------------------------------------------

SURFACE_MAP = {
    'asphalt': 'asphalt', 'paved': 'asphalt', 'concrete': 'asphalt',
    'concrete:plates': 'asphalt', 'concrete:lanes': 'asphalt',
    'paving_stones': 'asphalt', 'cobblestone': 'gravel', 'sett': 'gravel',
    'gravel': 'gravel', 'fine_gravel': 'gravel', 'pebblestone': 'gravel',
    'compacted': 'gravel', 'dirt': 'unpaved', 'earth': 'unpaved',
    'ground': 'unpaved', 'mud': 'unpaved', 'sand': 'unpaved',
    'grass': 'unpaved', 'wood': 'unpaved',
}

HIGHWAY_ROAD_MAP = {
    'cycleway': 'cycleway', 'path': 'path', 'footway': 'path', 'track': 'path',
    'residential': 'minor_road', 'living_street': 'minor_road',
    'service': 'minor_road', 'unclassified': 'minor_road',
    'tertiary': 'minor_road', 'tertiary_link': 'minor_road',
    'secondary': 'main_road', 'secondary_link': 'main_road',
    'primary': 'main_road', 'primary_link': 'main_road',
    'trunk': 'main_road', 'trunk_link': 'main_road',
}

HIGHWAY_SURFACE_INFER = {
    'cycleway': 'asphalt', 'residential': 'asphalt', 'living_street': 'asphalt',
    'unclassified': 'asphalt', 'tertiary': 'asphalt', 'tertiary_link': 'asphalt',
    'secondary': 'asphalt', 'secondary_link': 'asphalt',
    'primary': 'asphalt', 'trunk': 'asphalt', 'track': 'gravel',
}

SURFACE_COLOR = {
    'asphalt': '#48bb78',   # green
    'gravel':  '#ed8936',   # orange
    'unpaved': '#e53e3e',   # red
    'unknown': '#a0aec0',   # grey
}

def classify_surface(tags):
    if not tags:
        return 'unknown'
    s = tags.get('surface', '').lower()
    if s in SURFACE_MAP:
        return SURFACE_MAP[s]
    hw = tags.get('highway', '').lower()
    return HIGHWAY_SURFACE_INFER.get(hw, 'unknown')

def classify_road_type(tags):
    if not tags:
        return 'unknown'
    hw = tags.get('highway', '').lower()
    return HIGHWAY_ROAD_MAP.get(hw, 'unknown')


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

_cache_dir = None
_nominatim_cache = {}
_overpass_cache = {}
_surface_cache = {}

def init_cache(cache_dir=CACHE_BASE):
    global _cache_dir, _nominatim_cache, _overpass_cache, _surface_cache, _photo_embed_cache
    _cache_dir = cache_dir
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

    surf_path = os.path.join(_cache_dir, "surface.json")
    if os.path.exists(surf_path):
        with open(surf_path, encoding="utf-8") as f:
            _surface_cache = json.load(f)
        print(f"  Loaded {len(_surface_cache)} cached surface entries")

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

def _save_surface_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "surface.json"), "w", encoding="utf-8") as f:
        json.dump(_surface_cache, f, ensure_ascii=False)

_photo_embed_cache = {}  # url -> "data:image/jpeg;base64,..."

def _save_photo_embed_cache():
    if _cache_dir is None:
        return
    with open(os.path.join(_cache_dir, "photo_embed.json"), "w", encoding="utf-8") as f:
        json.dump(_photo_embed_cache, f, ensure_ascii=False)


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
    """Try Wikimedia Commons, fall back to OSM static map thumbnail."""
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
    cached = _wikimedia_cache.get(key)  # reuse wikimedia in-memory cache for OSM tiles
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
        _wikimedia_cache[key] = result
        _save_wikimedia_cache()
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
            _overpass_cache[bbox] = elements
            _save_overpass_cache()
        except Exception as exc:
            print(f"    Overpass warning: {exc}")
            elements = []

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
        if category is None:
            continue  # element doesn't match any POI filter; skip
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
# Surface / road type data — Overpass way query + point snapping
# ---------------------------------------------------------------------------

def _point_to_segment_dist_sq(px, py, ax, ay, bx, by):
    """Squared distance from point (px,py) to segment (ax,ay)-(bx,by). Cartesian approx."""
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2


def snap_points_to_ways(sec_points, ways, max_snap_m=50):
    """
    For each track point, find the nearest OSM way segment and return its tags.
    Returns a list of tag dicts (one per point; empty dict if no way within max_snap_m).
    """
    # Pre-build segment list for speed: list of (tags, ax, ay, bx, by)
    segments = []
    for way in ways:
        geom = way.get("geometry", [])
        tags = way.get("tags", {})
        for i in range(len(geom) - 1):
            segments.append((tags,
                              geom[i]["lon"],  geom[i]["lat"],
                              geom[i+1]["lon"], geom[i+1]["lat"]))

    max_dist_sq = (max_snap_m / 111_000) ** 2  # degrees² threshold

    result = []
    for pt in sec_points:
        px, py = pt.longitude, pt.latitude
        best_sq = float("inf")
        best_tags = {}
        for tags, ax, ay, bx, by in segments:
            d = _point_to_segment_dist_sq(px, py, ax, ay, bx, by)
            if d < best_sq:
                best_sq = d
                best_tags = tags
        result.append(best_tags if best_sq <= max_dist_sq else {})
    return result


def compute_stats(labels):
    """Convert a list of string labels into a percentage dict, sorted by descending %."""
    from collections import Counter
    n = len(labels)
    if n == 0:
        return {}
    counts = Counter(labels)
    return {k: round(v * 100.0 / n, 1) for k, v in sorted(counts.items(), key=lambda x: -x[1])}


def fetch_surface_ways(sec_points, corridor_m=150):
    """
    Query Overpass for highway ways with geometry in the section bbox.
    Returns list of way elements with 'tags' and 'geometry'. Disk-cached.
    """
    lats = [p.latitude  for p in sec_points]
    lons = [p.longitude for p in sec_points]
    pad  = corridor_m / 111_000
    bbox = f"{min(lats)-pad:.5f},{min(lons)-pad:.5f},{max(lats)+pad:.5f},{max(lons)+pad:.5f}"

    if bbox in _surface_cache:
        return _surface_cache[bbox]

    query = (
        f"[out:json][timeout:30];\n"
        f"(\n  way[\"highway\"]({bbox});\n);\n"
        f"out geom;"
    )
    print(f"    Fetching surface ways ({bbox[:35]}...)...")
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=35)
        resp.raise_for_status()
        ways = [el for el in resp.json().get("elements", []) if el.get("type") == "way"]
        print(f"    → {len(ways)} ways")
        _surface_cache[bbox] = ways
        _save_surface_cache()
    except Exception as exc:
        print(f"    Surface Overpass warning: {exc}")
        ways = []

    return ways

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


def render_map_png(points, path, width=600, height=400, point_surfaces=None):
    m = StaticMap(width, height)
    coords = [(p.longitude, p.latitude) for p in points]

    if point_surfaces and len(point_surfaces) == len(points):
        # Draw colored segments — group consecutive points with same surface
        seg_coords = [coords[0]]
        seg_color  = SURFACE_COLOR.get(point_surfaces[0], SURFACE_COLOR['unknown'])
        for i in range(1, len(coords)):
            cur_color = SURFACE_COLOR.get(point_surfaces[i], SURFACE_COLOR['unknown'])
            if cur_color == seg_color:
                seg_coords.append(coords[i])
            else:
                if len(seg_coords) >= 2:
                    m.add_line(Line(seg_coords, seg_color, 3))
                seg_coords = [coords[i - 1], coords[i]]
                seg_color  = cur_color
        if len(seg_coords) >= 2:
            m.add_line(Line(seg_coords, seg_color, 3))
    else:
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

def generate(gpx_path, section_km=SECTION_KM, max_km=None, output_dir=OUTPUT_DIR, cache_base=CACHE_BASE, output_name=None, title=None):
    print(f"Loading GPX: {gpx_path}")
    os.makedirs(output_dir, exist_ok=True)

    # Resolve title early so we can key the cache by route name
    title = title or os.path.basename(gpx_path).rsplit(".", 1)[0]
    cache_dir = os.path.join(cache_base, title)
    init_cache(cache_dir)

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

    sections_dir = os.path.join(cache_dir, "sections")
    os.makedirs(sections_dir, exist_ok=True)

    handbook = {
        "source_file": os.path.basename(gpx_path),
        "title":       title,
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
        stamp_path = os.path.join(sec_dir, "map_surface.stamp")

        # Fetch surface/road data (cached after first run)
        print(f"  [Section {idx+1:02d}] Fetching surface/road type data...")
        surface_ways   = fetch_surface_ways(sec_points)
        point_tags     = snap_points_to_ways(sec_points, surface_ways)
        point_surfaces = [classify_surface(t)   for t in point_tags]
        point_roads    = [classify_road_type(t) for t in point_tags]
        surface_stats  = compute_stats(point_surfaces)
        road_stats     = compute_stats(point_roads)

        # Render colored map; use stamp file to detect if already done
        need_map = not os.path.exists(map_path) or not os.path.exists(stamp_path)
        if need_map:
            print(f"  [Section {idx+1:02d}] Rendering colored map...")
            try:
                render_map_png(sec_points, map_path, point_surfaces=point_surfaces)
                open(stamp_path, "w").close()
            except Exception as exc:
                print(f"    WARNING: map render failed: {exc}")
                map_path = None
        else:
            print(f"  [Section {idx+1:02d}] Colored map already cached, skipping render")

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

        # Sample track points and elevation profile for interactive viewer
        track_step = max(1, len(sec_points) // 300)
        elev_step  = max(1, len(sec_points) // 200)
        track_points_data = [
            {"la": round(sec_points[i].latitude, 5),
             "lo": round(sec_points[i].longitude, 5),
             "s":  point_surfaces[i][0]}   # first char: 'a'=asphalt 'g'=gravel 'u'=unpaved 'k'=unknown
            for i in range(0, len(sec_points), track_step)
        ]
        elevation_profile_data = [
            {"d": round(sec_dists[i], 3), "e": int(sec_elevs[i])}
            for i in range(0, len(sec_points), elev_step)
        ]

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
            "surface_stats":     surface_stats,
            "road_stats":        road_stats,
            "track_points":      track_points_data,
            "elevation_profile": elevation_profile_data,
            "map_png":           os.path.relpath(map_path,  cache_dir) if map_path  else None,
            "elevation_png":     os.path.relpath(elev_path, cache_dir) if elev_path else None,
        })

    json_path = os.path.join(cache_dir, "handbook.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(handbook, f, indent=2, ensure_ascii=False)
    print(f"\nHandbook JSON written: {json_path}")

    rb_path = write_routebook(handbook, output_dir, cache_dir, output_name)
    print(f"Routebook written:      {rb_path}")

    update_routes_index(handbook, rb_path, output_dir)

    return handbook


# ---------------------------------------------------------------------------
# .jumroutebook output  (ZIP with manifest + data + assets)
# ---------------------------------------------------------------------------

def write_routebook(handbook, output_dir, cache_dir=CACHE_BASE, output_name=None):
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
        # handbook data — strip map_png / elevation_png paths (assets are in ZIP)
        import copy, hashlib
        hb_export = copy.deepcopy(handbook)
        for sec in hb_export.get("sections", []):
            sec.pop("map_png", None)
            sec.pop("elevation_png", None)
        hb_bytes = json.dumps(hb_export, indent=2, ensure_ascii=False).encode("utf-8")

        # compute hash of handbook content and embed in manifest
        manifest["content_hash"] = hashlib.sha256(hb_bytes).hexdigest()

        # manifest written after hash is known
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("handbook.json", hb_bytes.decode("utf-8"))

        # assets
        for sec in handbook.get("sections", []):
            idx = sec["index"]
            folder = f"assets/section_{idx:02d}"
            for key, arc_name in [("map_png", "map.png"), ("elevation_png", "elevation.png")]:
                rel = sec.get(key)
                if rel:
                    abs_path = os.path.join(cache_dir, rel)
                    if os.path.exists(abs_path):
                        zf.write(abs_path, f"{folder}/{arc_name}")

    return rb_path


def update_routes_index(handbook, rb_path, output_dir):
    """Upsert this route into output_dir/routes.json (keyed by filename)."""
    index_path = os.path.join(output_dir, "routes.json")
    try:
        with open(index_path, encoding="utf-8") as f:
            entries = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []

    filename = os.path.basename(rb_path)
    n_sections = len(handbook.get("sections", []))
    total_km   = round(handbook.get("total_km", 0), 1)
    title      = handbook.get("title") or filename.rsplit(".", 1)[0]
    entry = {
        "filename": filename,
        "title":    title,
        "meta":     f"{n_sections} sections · {total_km:.0f} km",
    }

    entries = [e for e in entries if e.get("filename") != filename]
    entries.append(entry)
    entries.sort(key=lambda e: e["title"])

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"Routes index updated:   {index_path}")


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

def img_to_base64(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


_STATS_COLORS = {
    'asphalt': '#48bb78', 'gravel': '#ed8936', 'unpaved': '#e53e3e',
    'cycleway': '#3182ce', 'path': '#805ad5',
    'minor_road': '#718096', 'main_road': '#e53e3e',
    'unknown': '#a0aec0',
}

def _render_stats_bar_html(label, stats):
    if not stats:
        return ""
    segs = "".join(
        f'<div class="stats-bar-seg" style="width:{pct}%;background:{_STATS_COLORS.get(cat,"#a0aec0")}" title="{cat} {pct}%"></div>'
        for cat, pct in stats.items() if pct > 0
    )
    legend = "".join(
        f'<span class="stats-legend-item">'
        f'<span class="stats-legend-dot" style="background:{_STATS_COLORS.get(cat,"#a0aec0")}"></span>'
        f'{cat.replace("_"," ")} {pct}%</span>'
        for cat, pct in stats.items() if pct > 0
    )
    return (
        f'<div class="stats-bar-row">'
        f'<div class="stats-bar-label">{label}</div>'
        f'<div class="stats-bar">{segs}</div>'
        f'<div class="stats-legend">{legend}</div>'
        f'</div>'
    )



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
    parser.add_argument("--cache-base", default=None,
                        help="Base cache directory; a per-route subdirectory is created inside (default: cache/)")
    parser.add_argument("--output-name", default=None,
                        help="Base filename for the .jumroutebook archive (without extension)")
    parser.add_argument("--title", default=None,
                        help="Display title shown in the viewer app (defaults to GPX filename)")
    args = parser.parse_args()

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
    if args.cache_base is None:
        args.cache_base = CACHE_BASE

    result = generate(args.gpx, args.section_km, args.max_km, args.output_dir, args.cache_base, args.output_name, args.title)
    print(f"\nDone. {len(result['sections'])} sections in '{args.output_dir}'")
