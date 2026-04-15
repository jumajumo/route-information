# JumRouteBook — Requirements & Functional Specification

## Overview

JumRouteBook is a two-part tool for cyclists:

1. **`generate_handbook.py`** — converts a GPX route into a self-contained route handbook (maps, elevation profiles, POIs, turn guidance)
2. **`build_viewer.py`** — builds an interactive web viewer (`viewer.html`) that loads and displays `.jumroutebook` files as a PWA

---

## 1. Route Handbook Generator (`generate_handbook.py`)

### 1.1 Inputs

| Input | Description |
|-------|-------------|
| GPX file | Track points + optional waypoints |
| `--section-km` | Target section length in km (default: 10) |
| `--max-km` | Truncate route to first N km (optional) |
| `--output-dir` | Output directory (default: `handbook/`) |
| `--output-name` | Base name for output files |
| `--title` | Human-readable route title |

### 1.2 Outputs

| File | Description |
|------|-------------|
| `handbook/handbook.json` | Full route metadata and section data |
| `handbook/handbook_selfcontained.html` | Single-file HTML with all images embedded as base64 |
| `handbook/{name}.jumroutebook` | ZIP archive for use in the viewer |
| `handbook/sections/section_NN/map.png` | Colored surface-type map (600×400 px) per section |
| `handbook/sections/section_NN/elevation.png` | Elevation profile chart per section |

### 1.3 Section Splitting

- Route is split into sections of approximately `--section-km` (default 10 km)
- Boundaries are snapped to named villages/towns via OSM Nominatim reverse geocoding
- Snapping samples the track every ~200 m within a ±3 km window around the raw split point, taking the first point with a resolvable place name
- Result: every section runs from one named place to another

### 1.4 Elevation Data

- SRTM elevation fetched per track point via `srtm.py` library (disk-cached)
- Per-section: cumulative elevation gain and loss computed (only deltas > 0 / < 0 counted)
- Elevation profile sampled at ~200 evenly-spaced points per section for JSON output

### 1.5 Surface & Road Type Classification

- Overpass API queried for all highway ways in each section's bounding box (with geometry)
- Each track point snapped to the nearest OSM way segment within 50 m
- Surface type derived from OSM `surface` tag: `asphalt`, `gravel`, `unpaved`, `unknown`
- Road type derived from OSM `highway` tag: `cycleway`, `path`, `minor_road`, `main_road`, etc.
- Per-section percentage breakdown stored in `surface_stats` and `road_stats`
- Surface type also encoded per track point as single character (`a`/`g`/`u`/`k`) for map coloring

### 1.6 Points of Interest (POIs)

- Overpass API queried for 14 cycling-relevant categories within a 150 m corridor around the route:
  - Food & drink: restaurant, café, fast food, bar, pub
  - Cycling: bicycle repair station, bicycle shop, fuel, drinking water
  - Accommodation: hotel, guest house, hostel, campsite
  - Other: viewpoint, tourist information, natural spring, wayside cross
- GPX waypoints embedded in the GPX file are merged into POI list with `source: "gpx"`
- POIs within a section boundary are included in that section's `waypoints` array
- Deduplication by name + rounded coordinates; sorted by distance into section

### 1.7 Notable Turns

- Turn detection: sample ~50 points along section, compute bearing between distant neighbors
- Turns with bearing change > 45° are flagged as notable
- Per turn: geocode road name (Nominatim), compute heading, bearing change, cardinal direction
- Photo acquired for each turn (see §1.8)

### 1.8 Photo Acquisition for Turns

Priority order (first success wins):

1. **Wikimedia Commons** — geosearch for geotagged files within 1 km; download and base64-embed thumbnail
2. **OSM Tile Thumbnail** — fetch 3×3 grid of OSM tiles (zoom 15), composite, add red pin marker, crop to 220×148 px
3. **Pillow placeholder** — generated locally if tile fetch fails

All images validated for correct image magic bytes (JPEG/PNG/GIF/WebP) before accepting.

### 1.9 Map Rendering

- Per-section colored track map rendered using `staticmap` library
- Track segments colored by surface type: green (asphalt), orange (gravel), red (unpaved), grey (unknown)
- Green circle at section start, red circle at section end
- Output: 600×400 px PNG

### 1.10 Elevation Chart Rendering

- Per-section elevation profile rendered with matplotlib
- Filled area chart with grid lines, axis labels, elevation gain/loss in title
- Output: 8×3 inch PNG at 120 DPI

### 1.11 handbook.json Schema

```json
{
  "source_file": "route.gpx",
  "title": "Route Title",
  "total_km": 89.5,
  "section_target_km": 10.0,
  "sections": [
    {
      "index": 1,
      "label": "Town A → Town B",
      "start_name": "Town A",
      "end_name": "Town B",
      "start_km": 0.0,
      "end_km": 10.2,
      "distance_km": 10.2,
      "elevation_gain_m": 245,
      "elevation_loss_m": 180,
      "start_lat": 46.123,
      "start_lon": 11.567,
      "end_lat": 46.234,
      "end_lon": 11.678,
      "surface_stats": { "asphalt": 75.3, "gravel": 20.1, "unpaved": 4.6 },
      "road_stats": { "minor_road": 60, "main_road": 40 },
      "track_points": [
        { "la": 46.123, "lo": 11.567, "s": "a" }
      ],
      "elevation_profile": [
        { "d": 0.0, "e": 450 },
        { "d": 0.5, "e": 480 }
      ],
      "waypoints": [
        {
          "name": "Café Rossi",
          "category": "cafe",
          "lat": 46.124, "lon": 11.568,
          "dist_km": 3.5,
          "dist_m": 80,
          "opening_hours": "08:00-18:00",
          "website": "https://example.com",
          "source": "osm"
        }
      ],
      "notable_turns": [
        {
          "dist_km": 5.2,
          "elevation_m": 520,
          "bearing_change": 67.3,
          "direction": "LEFT",
          "heading_after": "SE",
          "bearing_after": 135.6,
          "road": "Via Garibaldi",
          "lat": 46.145, "lon": 11.589,
          "photo": {
            "thumb_b64": "data:image/jpeg;base64,...",
            "full_url": "https://...",
            "source": "wikimedia",
            "title": "Photo title",
            "date": "2021-06-12"
          }
        }
      ],
      "map_png": "sections/section_01/map.png",
      "elevation_png": "sections/section_01/elevation.png"
    }
  ]
}
```

### 1.12 .jumroutebook Format

ZIP archive (deflate compression) containing:

| Path | Content |
|------|---------|
| `manifest.json` | Format identifier + summary metadata |
| `handbook.json` | Full route data (map_png/elevation_png paths stripped) |
| `assets/section_NN/map.png` | Section map image |
| `assets/section_NN/elevation.png` | Section elevation image |

### 1.13 Caching

All external API responses are disk-cached in `handbook/.cache/`:

| Cache file | Content |
|------------|---------|
| `nominatim.json` | Reverse geocoding results (place names, road names) |
| `overpass.json` | POI query results |
| `surface.json` | OSM way geometry + surface tags |
| `wikimedia.json` | Photo metadata + base64 thumbnails; also stores OSM tile thumbnails |
| `photo_embed.json` | Embedded photo URLs |
| `tiles/{z}/{x}/{y}.png` | OSM tile images |

Rate limiting: Nominatim requires 1 request/second (enforced with `time.sleep`).

---

## 2. Viewer Build System (`build_viewer.py`)

### 2.1 Inputs

Libraries must be downloaded to `/tmp/` before running:

```bash
curl -sL https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js -o /tmp/jszip.min.js
curl -sL https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js -o /tmp/leaflet.js
curl -sL https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css -o /tmp/leaflet.css
```

### 2.2 Outputs

| File | Description |
|------|-------------|
| `viewer.html` | ~300 KB self-contained interactive viewer |
| `sw.js` | Service worker with embedded build timestamp |

### 2.3 Build Process

- Reads JSZip, Leaflet JS, and Leaflet CSS from `/tmp/`
- Generates a UTC build timestamp (`YYYYMMDDHHmmss`)
- Inlines all assets into a single HTML file (no external dependencies)
- Generates `sw.js` with the timestamp baked in as the cache version

---

## 3. Interactive Viewer (`viewer.html`)

### 3.1 Library Screen

- Lists all routes stored in IndexedDB
- Per-route: title, section count, total km, stored date, delete button
- File input: open one or more `.jumroutebook` files from disk
- URL input: load a `.jumroutebook` from a remote URL
- Clicking a route opens the Route Screen

### 3.2 Route Screen

- Top bar: back button, section title, prev/next navigation, section counter (e.g. "2 / 5")
- Navigation drawer/sidebar with full section list:
  - Mobile (< 960 px): slide-in drawer, closes automatically on section selection
  - Desktop (≥ 960 px): persistent sidebar, collapsible with toggle button
- Section slides: horizontal slider (mobile swipe) or vertical scroll (desktop)
- Keyboard: left/right arrow keys navigate between sections; Escape returns to library

### 3.3 Section Content

Each section displays:

| Element | Description |
|---------|-------------|
| Header | Section number + "Start → End" label |
| Meta table | Distance, route km range, elevation gain/loss |
| Surface stats bar | Color-coded percentage breakdown (asphalt/gravel/unpaved) |
| Road type stats bar | Color-coded percentage breakdown (minor road/path/main road) |
| Embedded map | Interactive Leaflet map (left column) |
| Elevation chart | Canvas-drawn elevation profile (right column) |
| Notable turns | Photo cards with direction, bearing, road name, Maps link |
| POIs | Collapsible groups by category with icons, links, opening hours |

### 3.4 Embedded Map

- One Leaflet map instance per section, mounted on page load
- OSM tile layer (`tile.openstreetmap.org`)
- Colored track line by surface type: green (asphalt), orange (gravel), red (unpaved/unknown)
- Green start marker and red end marker with popups
- Blue turn point markers with bearing popup
- Custom zoom in/out buttons (top-left, outside Leaflet's stacking context — required for iOS visibility)
- Fullscreen expand button (bottom-right)
- `invalidateSize()` called at 400 ms and 800 ms after slide transition (required for iOS repaint)

### 3.5 Fullscreen Map Modal

- Triggered by expand button on any section map
- Covers viewport with 32 px inset on all sides (app visible behind)
- Semi-transparent blurred backdrop (`rgba(0,0,0,.35)` + `backdrop-filter: blur(3px)`)
- Full Leaflet map with scroll-wheel zoom enabled
- Same track, markers, and turn markers as embedded map
- Close via: ✕ button, backdrop click, or Escape key
- Previous modal map instance destroyed before opening new one

### 3.6 Elevation Profile

- Canvas-rendered chart (not a static image)
- Fills full height of the column (same as embedded map, min 220 px)
- **Y-axis range is global** — shared across all sections using the tour-wide min/max elevation, enabling direct visual comparison between sections
- X-axis: distance in km (0 to section length)
- Hover/touch interaction:
  - Vertical dashed crosshair line
  - Filled dot at current elevation
  - Floating tooltip: distance (km) + elevation (m)
  - Synced blue marker on the embedded Leaflet map moves to corresponding track position

### 3.7 File Loading & Storage

- `.jumroutebook` ZIP extracted client-side using JSZip
- `manifest.json` validated for expected format
- All assets (PNG images) converted to base64 data URIs
- Route stored in IndexedDB (`jumroutebook-store`, object store `routes`)
- Unique ID: `"{title}::{creation_date}"`
- Multiple routes can be stored simultaneously

### 3.8 PWA Features

- `manifest.json` (inline data URI): standalone display mode, theme color `#2b6cb0`, bicycle emoji icon
- Apple-specific meta tags: `apple-mobile-web-app-capable`, status bar style, home screen title, touch icon
- Service worker registered on startup
- Update toast: displays "App updated to YYYY-MM-DD HH:MM ✓" when a new version activates in background

---

## 4. Service Worker (`sw.js`)

### 4.1 Versioning

- `VERSION` constant baked in at build time: `YYYYMMDDHHmmss` UTC timestamp
- Cache named `routebook-{VERSION}`

### 4.2 Install

- Caches `viewer.html` and `sw.js`
- Calls `skipWaiting()` to activate immediately without waiting for page reload

### 4.3 Activate

- Deletes all caches except the current version
- Claims all open clients
- Sends `{type: 'UPDATE_AVAILABLE', version: VERSION}` postMessage to all open tabs

### 4.4 Fetch Strategy

| Request | Strategy | Rationale |
|---------|----------|-----------|
| `viewer.html`, `sw.js` | Network-first, cache fallback | Always pick up new versions when online |
| Everything else | Cache-first, network fallback | Offline access to loaded routes |

---

## 5. External Dependencies

### Python (generate_handbook.py)

| Library | Purpose |
|---------|---------|
| `gpxpy` | GPX file parsing |
| `srtm` | SRTM elevation lookup |
| `staticmap` | OSM tile-based map rendering |
| `matplotlib` | Elevation chart rendering |
| `Pillow` (PIL) | Image compositing for OSM thumbnails |
| `requests` | HTTP calls to Nominatim, Overpass, Wikimedia, tile servers |

### JavaScript (viewer.html)

| Library | Version | Purpose |
|---------|---------|---------|
| Leaflet | 1.9.4 | Interactive maps |
| JSZip | 3.10.1 | ZIP file parsing |

### External Services

| Service | Used by | Purpose |
|---------|---------|---------|
| OSM Nominatim | generate_handbook.py | Reverse geocoding (place names, road names) |
| OSM Overpass | generate_handbook.py | POI queries, way geometry + tags |
| OSM Tile Server | generate_handbook.py, viewer.html | Map tiles |
| Wikimedia Commons | generate_handbook.py | Geotagged turn photos |
| SRTM (via srtm.py) | generate_handbook.py | Elevation data |

---

## 6. Deployment

The viewer is deployed to GitHub Pages. `viewer.html` and `sw.js` are both committed to the repository root. On each build:

1. Run `python3 build_viewer.py` → produces `viewer.html` + `sw.js` with new timestamp
2. Commit and push both files
3. Installed PWA users receive the update silently on next launch (service worker detects version change)
