"""Project locations for the map view.

`locate` reads a report's parsed pages and returns the first latitude/longitude pair stated in the text
(decimal degrees or degrees-minutes-seconds, with hemisphere letters or signs), with its page and verbatim
quote, provided the point falls inside (or within ~0.5 degrees of) the project's country on the Natural
Earth 1:50m boundaries. Reports that state only UTM coordinates, or none, get no stated location; the
export then places the project at its country's centroid and marks it approximate.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "config" / "geo"

# country names used in extractions -> Natural Earth names (world-atlas)
COUNTRY_ALIASES = {
    "USA": "United States of America", "United States": "United States of America", "US": "United States of America",
    "DRC": "Dem. Rep. Congo", "Democratic Republic of the Congo": "Dem. Rep. Congo", "Dominican Republic": "Dominican Rep.",
    "Côte d’Ivoire": "Côte d'Ivoire", "Cote d'Ivoire": "Côte d'Ivoire", "Ivory Coast": "Côte d'Ivoire",
    "Kyrgyz Republic": "Kyrgyzstan", "Türkiye": "Turkey", "Turkiye": "Turkey", "UK": "United Kingdom",
    "Republic of the Congo": "Congo", "Bosnia and Herzegovina": "Bosnia and Herz.", "Laos": "Laos",
}

CONTINENTS = {
    "North America": ["Canada", "United States of America", "Mexico", "Greenland"],
    "Central America & Caribbean": ["Guatemala", "Honduras", "Nicaragua", "Panama", "Costa Rica", "El Salvador", "Belize",
                                    "Dominican Rep.", "Haiti", "Cuba", "Jamaica"],
    "South America": ["Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Ecuador", "Guyana", "Paraguay", "Peru",
                      "Suriname", "Uruguay", "Venezuela", "French Guiana"],
    "Europe": ["Finland", "Sweden", "Norway", "Spain", "Portugal", "Greece", "Ireland", "United Kingdom", "Serbia",
               "Bulgaria", "Romania", "Poland", "Germany", "France", "Italy", "Austria", "Slovakia", "Bosnia and Herz.",
               "North Macedonia", "Albania", "Montenegro", "Greenland", "Iceland", "Russia", "Ukraine", "Armenia", "Georgia"],
    "Africa": ["Botswana", "Burkina Faso", "Côte d'Ivoire", "Dem. Rep. Congo", "Congo", "Egypt", "Ghana", "Guinea", "Mali",
               "Mauritania", "Morocco", "Namibia", "Niger", "Senegal", "South Africa", "Sudan", "Tanzania", "Zambia",
               "Zimbabwe", "Ethiopia", "Eritrea", "Kenya", "Liberia", "Sierra Leone", "Madagascar", "Mozambique", "Angola",
               "Cameroon", "Gabon", "Uganda", "Malawi", "Algeria", "Tunisia", "Nigeria", "Benin", "Togo"],
    "Asia & Middle East": ["Turkey", "Kyrgyzstan", "Kazakhstan", "Uzbekistan", "Tajikistan", "Mongolia", "China", "Pakistan",
                           "India", "Indonesia", "Philippines", "Malaysia", "Laos", "Vietnam", "Thailand", "Myanmar",
                           "Saudi Arabia", "Oman", "Iran", "Afghanistan", "Japan", "South Korea", "Cambodia"],
    "Oceania": ["Australia", "Papua New Guinea", "New Zealand", "Fiji", "Solomon Is.", "New Caledonia"],
}
CONTINENT_OF = {c: k for k, v in CONTINENTS.items() for c in v}


def country_key(name: str | None) -> str | None:
    if not name:
        return None
    return COUNTRY_ALIASES.get(name.strip(), name.strip())


def continent(name: str | None) -> str | None:
    return CONTINENT_OF.get(country_key(name) or "")


@lru_cache(maxsize=2)
def _countries(scale: str = "50m") -> dict[str, list[list[tuple[float, float]]]]:
    """Natural Earth countries from world-atlas TopoJSON -> {name: [ring (lon, lat), ...]} (outer and inner rings)."""
    topo = json.loads((GEO / f"countries-{scale}.json").read_text())
    sx, sy = topo["transform"]["scale"]; tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0; pts = []
        for dx, dy in arc:
            x += dx; y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        out = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(a if not out else a[1:])
        return out

    res: dict = {}
    for g in topo["objects"]["countries"]["geometries"]:
        polys = g.get("arcs", [])
        if g["type"] == "Polygon":
            polys = [polys]
        rings = [ring(r) for poly in polys for r in poly]
        res[g["properties"]["name"]] = rings
    return res


def _in_rings(lon: float, lat: float, rings) -> bool:
    inside = False
    for r in rings:                      # even-odd rule across outer and hole rings
        j = len(r) - 1
        for i in range(len(r)):
            xi, yi = r[i]; xj, yj = r[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def in_country(lat: float, lon: float, country: str | None, tol: float = 0.5) -> bool | None:
    rings = _countries().get(country_key(country) or "")
    if not rings:
        return None
    return any(_in_rings(lon + dx, lat + dy, rings) for dx in (0, -tol, tol) for dy in (0, -tol, tol))


def centroid(country: str | None) -> tuple[float, float] | None:
    """(lat, lon) of the country's largest ring's vertex mean: a rough placement for approximate markers."""
    rings = _countries("110m").get(country_key(country) or "")
    if not rings:
        return None
    r = max(rings, key=len)
    return sum(p[1] for p in r) / len(r), sum(p[0] for p in r) / len(r)


# --- coordinate parsing -------------------------------------------------------------------------------
# A coordinate: degrees (decimal or with minutes/seconds), the degree mark may be °, º, a stray "o" from PDF
# extraction or the word "degrees"; an optional hemisphere letter or word before or after.
HEMI = r"(?:north|south|east|west|N|S|E|W)\b"
C = (r"(?:(?P<pre>[NSEW])\s?)?(?P<neg>[-−–])?\s?(?P<d>\d{1,3}(?:\.\d+)?)\s*(?:°|º|˚|\bo\b|o(?=\s*\d)|degrees?|deg\.?)\s*"
     r"(?:(?P<m>\d{1,2}(?:\.\d+)?)\s*(?:'|’|′|‘|`|min(?:utes)?\.?)\s*"
     r"(?:(?P<s>\d{1,2}(?:\.\d+)?)\s*(?:\"|”|″|''|’’|′′|“|sec(?:onds)?\.?)?)?)?\s*(?P<h>" + HEMI + r")?")
COORD = re.compile(C, re.I)
DEC = re.compile(r"(?P<neg>[-−–])?(?P<d>\d{1,3}\.\d{2,})\s*(?:°|º|degrees?)?\s*(?P<h>" + HEMI + r")?", re.I)
KEY = re.compile(r"\b(lat(?:itude)?|long?(?:itude)?)\b\.?", re.I)
UTM_E = re.compile(r"(?P<v>\d{6}(?:\.\d+)?)\s*(?:m\s*)?(?:E\b|mE\b|east(?:ing)?\b)", re.I)
UTM_N = re.compile(r"(?P<v>\d{6,7}(?:\.\d+)?)\s*(?:m\s*)?(?:N\b|mN\b|north(?:ing)?\b)", re.I)
UTM_E2 = re.compile(r"east(?:ing)?s?[\s:=]*(?P<v>\d{3},?\d{3}(?:\.\d+)?)", re.I)
UTM_N2 = re.compile(r"north(?:ing)?s?[\s:=]*(?P<v>\d{1},?\d{3},?\d{3}(?:\.\d+)?)", re.I)
ZONE = re.compile(r"zone\s*(?P<z>\d{1,2})\s*(?P<l>[C-X])?\b", re.I)


def _val(m) -> tuple[float, str | None]:
    d = float(m.group("d")); mi = float(m.groupdict().get("m") or 0); s = float(m.groupdict().get("s") or 0)
    if mi >= 60 or s >= 60:
        raise ValueError
    h = (m.groupdict().get("h") or m.groupdict().get("pre") or "")[:1].upper() or None
    v = d + mi / 60 + s / 3600
    if m.groupdict().get("neg") or str(m.group("d")).startswith("-"):
        v = abs(v); h = h or "-"
    return v, h


def _near(text: str, pos: int, end: int, rx, span: int = 45):
    """The coordinate closest to a latitude/longitude keyword (after it, else before it)."""
    after = rx.search(text, end, end + span + 40)      # may run past the window, must start inside it
    if after and after.group("d") and after.start() <= end + span:
        return after
    for m in rx.finditer(text, max(0, pos - span), pos):
        last = m
    return locals().get("last")


def _signed(v, h, axis):
    if h == "-":
        return [-v]
    if h in ("N", "E"):
        return [v]
    if h in ("S", "W"):
        return [-v]
    return [v, -v]


def _resolve(lat_opts, lon_opts, country):
    ok = [(la, lo) for la in lat_opts for lo in lon_opts if abs(la) <= 90 and abs(lo) <= 180
          and in_country(la, lo, country) is not False]
    return ok[0] if len(ok) == 1 else None


def utm_to_latlon(e: float, n: float, zone: int, south: bool) -> tuple[float, float]:
    """WGS84 UTM -> geographic (Snyder 1987). NAD27/NAD83 differences are under 0.01 degree."""
    import math
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    x = e - 500000.0; y = n - (10000000.0 if south else 0.0)
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1 = ep2 * math.cos(p1) ** 2; t1 = math.tan(p1) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(p1) ** 2); r1 = a * (1 - e2) / (1 - e2 * math.sin(p1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = p1 - (n1 * math.tan(p1) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                          + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(p1)
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lon)


def find_geographic(text: str, country):
    """Yield (lat, lon, start, end) from latitude/longitude statements."""
    keys = list(KEY.finditer(text))
    for i, k in enumerate(keys):
        if not k.group(1).lower().startswith("lat"):
            continue
        for k2 in keys[max(0, i - 2): i + 3]:
            if k2 is k or k2.group(1).lower().startswith("lat") or abs(k2.start() - k.start()) > 160:
                continue
            for rx in (DEC, COORD):
                try:
                    a = _near(text, k.start(), k.end(), rx); b = _near(text, k2.start(), k2.end(), rx)
                    if a and b and a.start() == b.start():     # table: "Latitude Longitude ... -5° 46' -50° 32'"
                        b = rx.search(text, a.end(), a.end() + 30)
                    if not a or not b:
                        continue
                    (la, hla), (lo, hlo) = _val(a), _val(b)
                except (ValueError, TypeError):
                    continue
                if la > 90 or lo > 180:
                    continue
                if hla in ("E", "W") or hlo in ("N", "S"):
                    continue
                pt = _resolve(_signed(la, hla, "lat"), _signed(lo, hlo, "lon"), country)
                if pt:
                    yield pt[0], pt[1], min(k.start(), k2.start(), a.start(), b.start()), max(a.end(), b.end(), k.end(), k2.end())
                    break
    # "24°45’N / 101°30’W" without the words latitude/longitude
    for a in COORD.finditer(text):
        if not a.group("h") or a.group("h")[:1].upper() not in "NS" or not (a.group("m") or "." in a.group("d")):
            continue
        b = COORD.search(text, a.end(), a.end() + 25)
        if not b or not b.group("h") or b.group("h")[:1].upper() not in "EW":
            continue
        try:
            (la, hla), (lo, hlo) = _val(a), _val(b)
        except ValueError:
            continue
        pt = _resolve(_signed(la, hla, "lat"), _signed(lo, hlo, "lon"), country)
        if pt:
            yield pt[0], pt[1], a.start(), b.end()


def find_utm(text: str, country):
    for z in ZONE.finditer(text):
        lo, hi = max(0, z.start() - 220), z.end() + 220
        win = text[lo:hi]
        e = UTM_E.search(win) or UTM_E2.search(win); n = UTM_N.search(win) or UTM_N2.search(win)
        if not e or not n:
            continue
        ev, nv = float(e.group("v").replace(",", "")), float(n.group("v").replace(",", ""))
        if not (100000 <= ev <= 900000 and 0 <= nv <= 10000000):
            continue
        zone = int(z.group("z"))
        if not 1 <= zone <= 60:
            continue
        letter = (z.group("l") or "").upper()
        souths = [False] if letter == "N" or (letter and letter >= "N" and letter != "S") else [True] if letter and letter < "N" else [False, True]
        pts = [utm_to_latlon(ev, nv, zone, s) for s in souths]
        pts = [p for p in pts if in_country(p[0], p[1], country)]
        if len(pts) == 1:
            span = sorted([lo + e.start(), lo + e.end(), lo + n.start(), lo + n.end(), z.start(), z.end()])
            yield pts[0][0], pts[0][1], span[0], span[-1]


CONTEXT = ("centre", "center", "centred", "centered", "located", "location", "approximate", "coordinates", "situated", "site", "property", "plant", "mill", "mine is")


def locate(folder: Path, country: str | None, max_pages: int = 150) -> dict | None:
    """First stated latitude/longitude (else UTM) inside the project's country, preferring 'located at' context."""
    pages = sorted((folder / "parsed" / "pages").glob("*.md"))[:max_pages]
    best = {"stated": None, "utm": None}
    for p in pages:
        text = re.sub(r"\s+", " ", p.read_text(errors="ignore"))
        for method, finder in (("stated", find_geographic), ("utm", find_utm)):
            for lat, lon, a, b in finder(text, country):
                hit = {"lat": round(lat, 4), "lon": round(lon, 4), "page": int(p.stem), "quote": text[a:b].strip(),
                       "method": method}
                if method == "stated" and any(k in text[max(0, a - 250):a + 40].lower() for k in CONTEXT):
                    return hit
                best[method] = best[method] or hit
    return best["stated"] or best["utm"]


# Approximate centres of states / provinces / regions that appear in the data, used only when a report states
# no coordinates. Markers placed this way are drawn as approximate on the map.
REGION_CENTRES = {
    "Canada": {"british columbia": (54.5, -125.0), "ontario": (49.5, -85.0), "quebec": (50.0, -73.5), "québec": (50.0, -73.5),
               "manitoba": (55.0, -97.5), "saskatchewan": (55.0, -105.5), "yukon": (63.0, -135.5), "northwest territories": (63.5, -114.0),
               "nunavut": (65.0, -95.0), "newfoundland": (53.5, -63.0), "labrador": (53.5, -63.0), "nova scotia": (45.0, -63.0),
               "new brunswick": (46.5, -66.5)},
    "United States of America": {"nevada": (39.5, -116.8), "alaska": (63.0, -152.0), "arizona": (33.5, -111.0), "idaho": (44.5, -115.0),
               "michigan": (46.5, -88.0), "minnesota": (47.5, -92.5), "oregon": (43.5, -119.0), "south carolina": (34.6, -80.6),
               "south dakota": (44.3, -103.6), "wyoming": (43.0, -107.5), "colorado": (38.8, -105.2), "montana": (46.5, -112.0),
               "utah": (40.0, -112.0), "california": (37.0, -119.5), "washington": (47.5, -120.5), "new mexico": (34.5, -106.0)},
    "Australia": {"western australia": (-29.0, 121.0), "new south wales": (-32.5, 147.0), "northern territory": (-14.0, 132.0),
               "queensland": (-21.0, 145.0), "south australia": (-31.0, 137.5), "victoria": (-37.0, 144.5), "tasmania": (-42.0, 146.5)},
    "Brazil": {"amapá": (1.0, -52.0), "amapa": (1.0, -52.0), "bahia": (-11.5, -40.5), "goiás": (-15.5, -49.5), "goias": (-15.5, -49.5),
               "maranhão": (-2.5, -45.5), "maranhao": (-2.5, -45.5), "mato grosso": (-12.5, -56.0), "minas gerais": (-19.0, -44.5),
               "pará": (-6.0, -50.5), "para": (-6.0, -50.5), "rio grande do norte": (-5.8, -36.6), "tocantins": (-10.0, -48.0)},
    "Chile": {"antofagasta": (-23.5, -69.0), "atacama": (-27.0, -70.0), "tarapacá": (-20.5, -69.0), "tarapaca": (-20.5, -69.0),
              "valparaiso": (-32.5, -70.8), "valparaíso": (-32.5, -70.8), "coquimbo": (-30.5, -71.0), "o'higgins": (-34.3, -70.6)},
    "Peru": {"arequipa": (-15.8, -72.0), "cusco": (-14.0, -72.0), "moquegua": (-17.0, -70.7), "pasco": (-10.6, -76.2), "cajamarca": (-7.1, -78.5), "apurimac": (-14.0, -73.0)},
    "Mexico": {"oaxaca": (-0.0 + 16.8, -96.7), "sinaloa": (25.5, -107.5), "sonora": (29.5, -110.5), "zacatecas": (23.5, -102.5),
               "durango": (24.5, -104.5), "chihuahua": (28.5, -106.5), "guerrero": (17.6, -99.9)},
    "Argentina": {"san juan": (-30.5, -69.5), "salta": (-24.5, -66.0), "santa cruz": (-48.0, -69.5), "catamarca": (-27.0, -67.0)},
    "Colombia": {"antioquia": (6.8, -75.6), "caldas": (5.3, -75.4), "santander": (7.1, -73.0)},
    "South Africa": {"free state": (-27.9, 26.7), "gauteng": (-26.2, 27.9), "north west": (-26.4, 27.2), "limpopo": (-23.9, 29.4)},
    "Zambia": {"copperbelt": (-12.8, 28.2), "north-western": (-12.3, 25.8), "north western": (-12.3, 25.8), "northwest": (-12.3, 25.8), "north west": (-12.3, 25.8)},
    "Tanzania": {"geita": (-2.9, 32.2), "kagera": (-2.6, 30.8), "mara": (-1.8, 34.4), "shinyanga": (-3.6, 33.4)},
    "Zimbabwe": {"matabeleland": (-20.5, 28.5), "mashonaland": (-17.5, 31.0), "midlands": (-19.0, 29.8)},
    "Ghana": {"ahafo": (-0.0 + 7.0, -2.4), "ashanti": (6.7, -1.6), "western": (5.5, -2.1)},
    "Senegal": {"kédougou": (12.7, -12.2), "kedougou": (12.7, -12.2)},
    "Burkina Faso": {"sahel": (14.0, -0.5), "sud-ouest": (10.5, -3.2)},
    "Spain": {"huelva": (37.7, -6.6)}, "Greece": {"chalkidiki": (40.4, 23.6)}, "Sweden": {"västerbotten": (64.7, 18.5)},
    "Finland": {"karelia": (62.7, 29.0), "lapland": (67.5, 26.0)}, "Norway": {"finnmark": (70.0, 25.0)},
    "Turkey": {"artvin": (41.2, 41.8), "izmir": (38.4, 27.1), "i̇zmir": (38.4, 27.1)},
    "Mongolia": {"gobi": (43.0, 106.9)}, "Pakistan": {"balochistan": (29.0, 64.0)},
    "Papua New Guinea": {"new ireland": (-3.2, 152.6), "morobe": (-6.9, 146.8)}, "Indonesia": {"papua": (-4.1, 137.1)},
    "Philippines": {"nueva vizcaya": (16.3, 121.1)}, "New Zealand": {"otago": (-45.3, 170.3), "waikato": (-37.4, 175.8)},
    "Guyana": {"cuyuni": (6.5, -60.0), "potaro": (5.0, -59.0)}, "Guatemala": {"jutiapa": (14.3, -89.9)},
    "Dem. Rep. Congo": {"haut-uélé": (3.0, 29.5), "lualaba": (-10.7, 25.5)}, "Côte d'Ivoire": {"worodougou": (8.2, -6.6)},
    "Egypt": {"eastern desert": (25.0, 34.0)}, "Mali": {"kayes": (13.0, -11.3)}, "Mauritania": {"inchiri": (20.0, -15.0)},
    "Kyrgyzstan": {"issyk": (42.0, 78.0)}, "Botswana": {"kalahari": (-21.5, 22.0), "north east": (-21.0, 27.5), "central": (-22.0, 26.0)},
    "Dominican Rep.": {"sánchez ramírez": (19.0, -70.2), "sanchez ramirez": (19.0, -70.2)},
}


def approximate(country: str | None, region: str | None) -> dict | None:
    """Region centre if the region is known, else the country's rough centre."""
    ck = country_key(country)
    reg = (region or "").lower()
    for key, (lat, lon) in REGION_CENTRES.get(ck or "", {}).items():
        if key in reg:
            return {"lat": lat, "lon": lon, "method": "region"}
    c = centroid(country)
    return {"lat": round(c[0], 2), "lon": round(c[1], 2), "method": "country"} if c else None


def run(report_ids: list[str] | None = None) -> dict:
    """Write reports/<id>/extracted/location.json for every extracted report (stated or UTM coordinates)."""
    import sqlite3
    con = sqlite3.connect(ROOT / "data" / "benchmark.db")
    rows = con.execute("SELECT r.id, p.country FROM reports r JOIN projects p ON p.id = r.project_id").fetchall()
    counts: dict = {}
    for rid, country in rows:
        if report_ids and rid not in report_ids:
            continue
        folder = ROOT / "reports" / rid
        out = folder / "extracted" / "location.json"
        hit = locate(folder, country)
        if hit:
            out.write_text(json.dumps(hit, ensure_ascii=False, indent=1))
        elif out.exists():
            out.unlink()
        counts[hit["method"] if hit else "none"] = counts.get(hit["method"] if hit else "none", 0) + 1
    return counts


def world_geojson(scale: str = "110m") -> dict:
    """Country outlines as compact GeoJSON for the map view (coordinates rounded to 0.01 degree)."""
    feats = []
    for name, rings in _countries(scale).items():
        if name == "Antarctica":
            continue
        polys = []
        for r in rings:
            if len(r) <= 3:
                continue
            if any(abs(r[i][0] - r[i - 1][0]) > 180 for i in range(1, len(r))):   # crosses the antimeridian
                r = [(x + 360 if x < 0 else x, y) for x, y in r]                  # (Russia, Fiji): keep it continuous
            polys.append([[[round(x, 2), round(y, 2)] for x, y in r]])
        feats.append({"type": "Feature", "properties": {"name": name},
                      "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "features": feats}
