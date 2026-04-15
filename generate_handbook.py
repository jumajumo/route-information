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
import json
import math
import os
import sys
import time

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
    """Try KartaView first, fall back to Wikimedia Commons."""
    photo = fetch_kartaview_photo(lat, lon, radius=500)
    if photo is None:
        print(f"      No KartaView — trying Wikimedia Commons...")
        photo = fetch_wikimedia_photo(lat, lon, radius=1000)
        if photo:
            print(f"      → Found: {photo['title'][:50]}")
    return photo


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

def generate(gpx_path, section_km=SECTION_KM, max_km=None, output_dir=OUTPUT_DIR):
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

    return handbook


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
                    gmaps_link = f'<a href="https://www.google.com/maps?q={lat_w},{lon_w}" target="_blank">📍</a>'
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
                    source_badge = ""
                    if photo.get("source") == "wikimedia":
                        source_badge = '<span class="photo-src">📷 Commons</span>'
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
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Route Handbook — {handbook['source_file']}</title>
<style>
/* ── Reset & base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #1a202c; color: #2d3748; }}

/* ── Top bar ── */
#topbar {{
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  height: 52px; background: #2b6cb0;
  display: flex; align-items: center; padding: 0 1rem; gap: .75rem;
  box-shadow: 0 2px 8px rgba(0,0,0,.3);
}}
#burger {{
  background: none; border: none; cursor: pointer; padding: 4px 6px;
  display: flex; flex-direction: column; gap: 5px; flex-shrink: 0;
}}
#burger span {{
  display: block; width: 22px; height: 2px; background: white; border-radius: 2px;
  transition: all .25s;
}}
#topbar-title {{
  color: white; font-weight: 700; font-size: 1rem; flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
#section-counter {{
  color: rgba(255,255,255,.7); font-size: .82rem; white-space: nowrap; flex-shrink: 0;
}}
#prev-btn, #next-btn {{
  background: rgba(255,255,255,.18); border: none; color: white; cursor: pointer;
  border-radius: 6px; padding: 5px 10px; font-size: 1rem; flex-shrink: 0;
  transition: background .15s;
}}
#prev-btn:hover, #next-btn:hover {{ background: rgba(255,255,255,.32); }}
#prev-btn:disabled, #next-btn:disabled {{ opacity: .3; cursor: default; }}

/* ── Drawer nav ── */
#nav-overlay {{
  display: none; position: fixed; inset: 0; z-index: 200;
  background: rgba(0,0,0,.45); backdrop-filter: blur(2px);
}}
#nav-overlay.open {{ display: block; }}
#nav-drawer {{
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 201;
  width: min(320px, 88vw); background: white;
  transform: translateX(-100%); transition: transform .28s ease;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 4px 0 24px rgba(0,0,0,.25);
}}
#nav-drawer.open {{ transform: translateX(0); }}
#nav-header {{
  background: #2b6cb0; color: white; padding: 1rem 1.2rem;
  font-weight: 700; font-size: 1rem; flex-shrink: 0;
}}
#nav-header small {{
  display: block; font-weight: 400; font-size: .78rem; opacity: .8; margin-top: 2px;
}}
#nav-list {{
  list-style: none; overflow-y: auto; flex: 1;
}}
#nav-list li {{ border-bottom: 1px solid #e2e8f0; }}
.nav-link {{
  display: flex; align-items: center; gap: .7rem;
  padding: .65rem 1.2rem; text-decoration: none; color: #2d3748;
  font-size: .9rem; transition: background .12s;
}}
.nav-link:hover, .nav-link.active {{ background: #ebf8ff; color: #2b6cb0; }}
.nav-num {{
  background: #2b6cb0; color: white; border-radius: 4px;
  padding: 1px 6px; font-size: .75rem; font-weight: 700; flex-shrink: 0;
}}
.nav-link.active .nav-num {{ background: #c05621; }}
.nav-text {{ display: flex; flex-direction: column; gap: 1px; min-width: 0; }}
.nav-route {{ font-size: .88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.nav-km {{ font-size: .74rem; color: #718096; }}
.nav-link.active .nav-km {{ color: #2b6cb0; opacity: .8; }}

/* ── Slide viewport ── */
#viewport {{
  position: fixed; top: 52px; left: 0; right: 0; bottom: 0;
  overflow: hidden;
}}
#slider {{
  display: flex; height: 100%;
  transition: transform .35s cubic-bezier(.4,0,.2,1);
  will-change: transform;
}}

/* ── Each section slide ── */
.section {{
  flex: 0 0 100%; width: 100%; height: 100%;
  overflow-y: auto; overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  scroll-behavior: smooth;
  background: white;
}}
.section-inner {{
  max-width: 900px; margin: 0 auto;
  padding: 1rem 1rem 2rem;
}}

/* ── Section header ── */
.section-header {{
  display: flex; align-items: center; gap: .6rem;
  margin-bottom: .9rem; flex-wrap: wrap;
}}
.num {{
  background: #2b6cb0; color: white; border-radius: 4px;
  padding: 2px 8px; font-size: .85rem; font-weight: 700; flex-shrink: 0;
}}
.section-title {{
  font-size: 1.1rem; font-weight: 700; color: #2b6cb0;
}}

/* ── Meta table ── */
.meta {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; }}
.meta td {{ padding: .28rem .5rem; border-bottom: 1px solid #e2e8f0; font-size: .85rem; }}
.meta td:first-child {{ font-weight: 600; width: 90px; color: #555; }}

/* ── Map + elevation ── */
.imgs {{
  display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin-bottom: 1rem;
}}
.imgs img {{ max-width: 100%; border-radius: 8px; display: block; }}
@media (max-width: 600px) {{ .imgs {{ grid-template-columns: 1fr; }} }}

/* ── Section headings ── */
h4 {{ margin: .6rem 0 .3rem; color: #4a5568; font-size: .88rem; font-weight: 700; }}

/* ── Turn cards ── */
.turn-cards {{ display: flex; flex-wrap: wrap; gap: .6rem; margin-bottom: .5rem; }}
.turn-card {{
  display: flex; gap: .6rem; background: #f7fafc; border: 1px solid #e2e8f0;
  border-radius: 8px; padding: .55rem; min-width: 200px; flex: 1 1 200px;
  align-items: flex-start;
}}
.turn-photo-col {{ flex-shrink: 0; text-align: center; }}
.turn-photo {{ width: 110px; height: 74px; object-fit: cover; border-radius: 6px; display: block; }}
.turn-photo-na {{
  width: 110px; height: 74px; background: #e2e8f0; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  font-size: .7rem; color: #a0aec0; text-align: center; padding: .3rem;
}}
.turn-info {{ flex: 1; font-size: .8rem; }}
.turn-dir {{ font-weight: 800; font-size: .95rem; margin-bottom: .15rem; }}
.turn-dir.left  {{ color: #c05621; }}
.turn-dir.right {{ color: #276749; }}
.turn-meta {{ color: #4a5568; margin-bottom: .08rem; }}
.turn-road {{ color: #718096; font-size: .75rem; margin-top: .15rem; }}
.photo-src {{ display: block; font-size: .68rem; color: #a0aec0; margin-top: .12rem; text-align: center; }}
.maps-link {{ font-size: .72rem; color: #3182ce; text-decoration: none; margin-left: .25rem; white-space: nowrap; }}
.maps-link:hover {{ text-decoration: underline; }}

/* ── POI groups ── */
.poi-group {{
  border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: .4rem; overflow: hidden;
}}
.poi-group summary {{
  padding: .42rem .75rem; cursor: pointer; font-weight: 600; font-size: .85rem;
  background: #f7fafc; color: #2d3748; list-style: none;
  display: flex; align-items: center; gap: .4rem; user-select: none;
}}
.poi-group summary::-webkit-details-marker {{ display: none; }}
.poi-group summary::before {{ content: "▸"; font-size: .72rem; color: #a0aec0; transition: transform .15s; }}
.poi-group[open] summary::before {{ transform: rotate(90deg); }}
.poi-group[open] summary {{ background: #ebf8ff; color: #2b6cb0; }}
.poi-table {{ width: 100%; border-collapse: collapse; font-size: .8rem; }}
.poi-table th {{ background: #f0f8ff; color: #4a5568; padding: .22rem .6rem; text-align: left; font-weight: 600; border-bottom: 1px solid #e2e8f0; }}
.poi-table td {{ padding: .2rem .6rem; border-bottom: 1px solid #f0f4f8; }}
.poi-table tr:last-child td {{ border-bottom: none; }}
.badge {{ background: #2b6cb0; color: white; border-radius: 99px; padding: 1px 7px; font-size: .72rem; font-weight: 700; margin-left: auto; }}
.oh {{ color: #718096; font-size: .76rem; }}
.desc {{ color: #718096; font-size: .76rem; font-weight: 400; }}

/* ── Swipe hint dots ── */
#dots {{
  position: fixed; bottom: 10px; left: 0; right: 0; z-index: 50;
  display: flex; justify-content: center; gap: 6px; pointer-events: none;
}}
.dot {{
  width: 7px; height: 7px; border-radius: 50%;
  background: rgba(255,255,255,.35); transition: background .2s;
}}
.dot.active {{ background: white; }}

/* ── Laptop: wider content, body is the scroll container ── */
@media (min-width: 960px) {{
  html {{ overflow: hidden; height: 100%; }}
  body {{ overflow-y: auto; height: 100%; background: #f0f4f8; }}
  #viewport {{ position: static; overflow: visible; }}
  #slider {{
    display: block; transform: none !important;
    transition: none;
  }}
  .section {{
    overflow: visible; height: auto;
    background: white; border-radius: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,.1);
    margin-bottom: 2rem;
  }}
  #dots {{ display: none; }}
  #topbar {{ position: sticky; top: 0; }}
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
    Route Handbook
    <small>{handbook['source_file']} &nbsp;·&nbsp; {handbook['total_km']} km &nbsp;·&nbsp; {total_sections} sections</small>
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
var isDesktop = window.innerWidth >= 960;
var TOPBAR_H = 52;

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
  document.getElementById('nav-overlay').classList.toggle('open');
  document.getElementById('nav-drawer').classList.toggle('open');
}}
function closeNav() {{
  document.getElementById('nav-overlay').classList.remove('open');
  document.getElementById('nav-drawer').classList.remove('open');
}}

// Nav link clicks — always prevent default, work on both mobile and desktop
navLinks.forEach(function(link, i) {{
  link.addEventListener('click', function(e) {{
    e.preventDefault();
    closeNav();
    goTo(i);
  }});
}});

// Touch swipe
var tx0, ty0, swiping = false;
document.getElementById('viewport').addEventListener('touchstart', function(e) {{
  if (isDesktop) return;
  tx0 = e.touches[0].clientX;
  ty0 = e.touches[0].clientY;
  swiping = true;
}}, {{passive: true}});
document.getElementById('viewport').addEventListener('touchmove', function(e) {{
  if (!swiping || isDesktop) return;
  var dx = e.touches[0].clientX - tx0;
  var dy = e.touches[0].clientY - ty0;
  // only intercept horizontal swipes wider than vertical scroll
  if (Math.abs(dx) > Math.abs(dy) + 10) e.preventDefault();
}}, {{passive: false}});
document.getElementById('viewport').addEventListener('touchend', function(e) {{
  if (!swiping || isDesktop) return;
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
  isDesktop = window.innerWidth >= 960;
  if (!isDesktop) slider.style.transform = 'translateX(-' + (current * 100) + '%)';
  else slider.style.transform = '';
}});

// Init
updateUI();
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
    parser.add_argument("--section-km", type=float, default=SECTION_KM)
    parser.add_argument("--max-km",     type=float, default=None,
                        help="Only process first N km of the route")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--regen-html", action="store_true",
                        help="Rebuild HTML from existing handbook.json without re-running GPX processing")
    args = parser.parse_args()

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
    else:
        if not args.gpx:
            parser.error("gpx argument is required unless --regen-html is used")
        result = generate(args.gpx, args.section_km, args.max_km, args.output_dir)
        print(f"\nDone. {len(result['sections'])} sections in '{args.output_dir}/'")
