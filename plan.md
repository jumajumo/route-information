# GPX Route Handbook Generator

## Context

The user has a 500km cycling GPX file and wants an automated tool that generates a section-by-section route handbook — eventually compilable into an iPhone app. Each section (~10km, or split at significant route changes) should contain a map image, elevation profile, and key metadata. No paid APIs are required for the MVP.

---

## Architecture

```
GPX file
  → Python script
      → gpxpy (parse + split)
      → srtm.py + matplotlib (elevation profiles as PNG)
      → staticmap library (free OSM tiles, no API key)
      → handbook.json + section PNGs
  → React web app (reads JSON, displays sections)
      → Capacitor (wraps React app into native iOS app)
```

---

## Phase 1: Python Generator Script

### Dependencies
```
gpxpy       # GPX parsing
srtm        # Elevation data (downloads SRTM NASA tiles, caches locally)
matplotlib  # Elevation profile charts → PNG
staticmap   # OSM tile map rendering — no API key needed
requests    # Optional HTTP calls
```

### Section splitting logic
- Primary split: every 10km of cumulative distance (configurable)
- Secondary splits (detect significant changes):
  - Bearing change > 45° over a short segment → flag as turn
  - GPX waypoints → automatic section breaks + labels
  - Surface change tags in GPX (if present)
- Output: list of `Section` objects with start/end indices into the point array

### Per section, generate:
1. **Elevation profile PNG** via `srtm.get_data()` + `matplotlib`
   - x-axis: distance (km), y-axis: elevation (m)
   - Show gain/loss in subtitle
2. **Static map PNG** via `staticmap` Python library (fetches OSM tiles, composites locally)
   - Draw the section path as a blue polyline
   - Mark start (green) and end (red) pins
   - Zero API calls, no account needed
3. **Metadata JSON** per section:
   - start/end coordinates, distance, elevation gain/loss
   - waypoints (name, lat/lng, type)
   - notable turns list
   - Mapillary photo URL (best-effort, skip if no coverage)

### Output structure
```
handbook/
  handbook.json          # Master index with all section metadata
  sections/
    section_01/
      map.png
      elevation.png
  handbook_selfcontained.html  # Optional: single-file HTML with base64 images
```

---

## Phase 2: React Viewer App (for iPhone)

### Stack
- **React** (Vite) — handbook viewer UI
- **Capacitor** — iOS/Android native wrapper (free, no native code needed)
- **Leaflet** (react-leaflet) — optional interactive map per section

### Key screens
1. **Overview screen**: route stats, list of sections with thumbnail maps
2. **Section screen**: full map image, elevation chart, waypoint list, coordinates
3. **Offline support**: bundle all JSON + PNG assets into the app build

### Deployment path
1. `npm run build` → static HTML/JS bundle
2. `npx cap add ios` → creates Xcode project
3. Open in Xcode → side-load to personal iPhone (free) or submit to App Store ($99/year)

---

## Critical Files to Create

| File | Purpose |
|------|---------|
| `generate_handbook.py` | Main Python script: parse GPX → generate handbook |
| `requirements.txt` | Python deps |
| `handbook-app/` | React + Capacitor app directory |
| `handbook-app/src/App.jsx` | Main viewer UI |
| `handbook-app/capacitor.config.json` | Capacitor config |

---

## Key Implementation Details

### Bearing change detection (significant turns)
```python
import math

def bearing(p1, p2):
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    return math.degrees(math.atan2(x, y))

def bearing_change(b1, b2):
    diff = abs(b1 - b2)
    return min(diff, 360 - diff)
```

### Zero-API map rendering (staticmap library)
```python
pip install staticmap
from staticmap import StaticMap, Line, CircleMarker

m = StaticMap(600, 400)
m.add_line(Line([(p.longitude, p.latitude) for p in points], 'blue', 3))
m.add_marker(CircleMarker((points[0].longitude, points[0].latitude), 'green', 12))
m.add_marker(CircleMarker((points[-1].longitude, points[-1].latitude), 'red', 12))
image = m.render()
image.save('map.png')
```
Fetches tiles from `tile.openstreetmap.org` — free, no key, within OSM usage policy.

### Elevation chart
```python
import srtm
import matplotlib.pyplot as plt

elevation_data = srtm.get_data()
elevations = [elevation_data.get_elevation(p.latitude, p.longitude) for p in points]
distances = [cumulative_distance_at(i) for i in range(len(points))]

plt.figure(figsize=(8, 3))
plt.fill_between(distances, elevations, alpha=0.4)
plt.plot(distances, elevations)
plt.xlabel('Distance (km)')
plt.ylabel('Elevation (m)')
plt.title(f'Gain: +{gain}m / Loss: -{loss}m')
plt.savefig('elevation.png', bbox_inches='tight')
```

---

## Verification Plan

1. Run `generate_handbook.py` against the real GPX file
2. Check `handbook.json` — verify section count (~50 sections for 500km), distances, waypoints
3. Open `handbook_selfcontained.html` in browser — verify maps and elevation charts render
4. Run React app locally (`npm run dev`) — navigate through sections
5. Build with Capacitor and open in iOS Simulator or real device

---

## Notes

- **Street View**: Skipped for MVP. Coverage unreliable without Google API key. Mapillary URLs can be included as clickable links if desired.
- **Surface changes**: Detected from GPX waypoint tags if present; otherwise user annotates manually.
- **iPhone**: Capacitor path is proven and free. Requires Xcode on Mac. Side-loading to personal device is free; App Store requires $99/year Apple Developer account.
