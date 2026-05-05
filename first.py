import pandas as pd
import streamlit as st
import json as json_lib
import streamlit.components.v1 as components
import base64
import requests
import time

from geopy.geocoders import Nominatim


st.set_page_config(page_title="Carbon Crane", page_icon="🌿", layout="wide")

# ── Statikus konstansok ───────────────────────────────────────────────────────

CO2_PER_WASH  = 0.236615995  # 1 mosás CO2e-je [kg]
CO2_PER_KM    = 0.215118375  # 1 km autózás CO2e-je [kg]
CO2_PER_KWH   = 0.236150771  # 1 kWh áram CO2-je [kg]
KWH_PER_HOUSE = 2500         # 1 háztartás éves energiafogyasztása [kWh]
BP_PARIS_KM   = 1485         # Budapest → Párizs távolság [km] (alapértelmezett)

COL_EM_ALL   = "BE - Carbon Emission - all subpages "
COL_EM_PAGE  = "BE - Carbon Emission - page"
COL_RED_PAGE = "BE - Reduced Carbon Emission"
COL_RED_ALL  = "BE - Reduced Carbon Emission - all subpages"

REQUIRED_COLUMNS = [
    "industry", "website", "pageType", "have all subpages", "url",
    COL_EM_PAGE, COL_EM_ALL,
    "BE - Reduction % - page", "Reduction % - image",
    COL_RED_PAGE, "BE - Reduced Carbon Emission - all subpages",
    "BE - Reduction % - all subpages", "Rank Reduction % - page",
    "Rank Reduced Carbon Emission", "Rank Reduction % - all subpages",
    "Rank Reduced Carbon Emission -  all subpages",
]

# ── Session state inicializálás ───────────────────────────────────────────────

if "ref_km" not in st.session_state:
    st.session_state["ref_km"] = BP_PARIS_KM
if "ref_label" not in st.session_state:
    st.session_state["ref_label"] = "Budapest → Paris"

# ── Drag&drop ─────────────────────────────────────────────────────────────────

def img_to_base64(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

# ── Számítások ────────────────────────────────────────────────────────────────

def calc_stats(rows: pd.DataFrame, col_em: str, col_red: str, ref_km: float, total_pv: int) -> dict:
    """Emission statisztikák + csökkentési potenciál egy adott oszloppárra."""
    em_avg   = rows[col_em].mean()
    kg_co2   = em_avg * total_pv / 1000
    per_site = rows.groupby("website").agg(max_em=(col_em, "max"), max_red=(col_red, "max"))
    red_pct  = per_site["max_red"].sum() / per_site["max_em"].sum()
    kg_saved = kg_co2 * red_pct
    kwh      = kg_saved / CO2_PER_KWH
    return {
        "em_max":         rows[col_em].max(),
        "em_avg":         em_avg,
        "em_min":         rows[col_em].min(),
        "kg_co2":         kg_co2,
        "wash":           kg_co2 / CO2_PER_WASH,
        "bp_paris_km_raw": kg_co2 / CO2_PER_KM,
        "red_pct":        red_pct,
        "kg_saved":       kg_saved,
        "kwh":            kwh,
        "house":          kwh / KWH_PER_HOUSE,
    }


def calc_all(df: pd.DataFrame, ref_km: float, total_pv: int) -> dict:
    """Összesítő + oldaltípusonkénti max/avg/min számítások."""
    return {
        "summary":     calc_stats(df, COL_EM_ALL, COL_RED_ALL, ref_km, total_pv),
        "by_pagetype": {pt: calc_stats(g, COL_EM_PAGE, COL_RED_PAGE, ref_km, total_pv) for pt, g in df.groupby("pageType")},
    }

# ── Geocoder segédfüggvények ──────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def geocode_location(place: str):
    geolocator = Nominatim(user_agent="carbon_crane_app", timeout=10)
    for attempt in range(3):
        try:
            time.sleep(1)
            result = geolocator.geocode(place)
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise e

def extract_city(location) -> str:
    """Városnév kinyerése a Nominatim geocoder eredményéből."""
    raw = location.raw.get("address", {})
    return (
        raw.get("city") or
        raw.get("town") or
        raw.get("village") or
        raw.get("municipality") or
        location.address.split(",")[0]
    )

def shorten_label(text: str, max_chars: int = 32) -> str:
    """Feliratot rövidít ha meghaladja a max karakterszámot."""
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "."

def fit_cities(city1: str, city2: str, max_total: int = 22) -> tuple:
    """Városneveket rövidít ha együtt nem férnének el. Először a hosszabbat rövidíti."""
    if len(city1) + len(city2) <= max_total:
        return city1, city2
    # Hosszabbat rövidítjük először
    if len(city1) >= len(city2):
        city1 = city1[:10].rstrip() + "."
    else:
        city2 = city2[:10].rstrip() + "."
    # Ha még mindig nem fér, a másikat is rövidítjük
    if len(city1) + len(city2) > max_total:
        if not city1.endswith("."):
            city1 = city1[:10].rstrip() + "."
        if not city2.endswith("."):
            city2 = city2[:10].rstrip() + "."
    return city1, city2

@st.cache_data(show_spinner=False)
def get_road_distance(lat1, lon1, lat2, lon2):
    """OSRM közúti távolság (ingyenes, nem kell API kulcs)."""
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}"
        f"?overview=false"
    )
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("code") == "Ok":
            return data["routes"][0]["distance"] / 1000
        return None
    except Exception:
        return None

# ── UI ────────────────────────────────────────────────────────────────────────

st.title("Carbon Crane infografika készítő")

uploaded = st.file_uploader("Excel fájl feltöltése (.xlsx)", type=["xlsx"])

if not uploaded:
    st.stop()

try:
    xl = pd.ExcelFile(uploaded)
    matching = [s for s in xl.sheet_names if "carbon_scan_output_ecomm" in s]
    if not matching:
        st.error("A fájlban nem található 'carbon_scan_output_ecomm' nevű sheet.")
        st.stop()
    df = xl.parse(matching[0], header=0)
except Exception:
    st.error("A fájl nem olvasható. Ellenőrizd, hogy érvényes .xlsx fájlt töltöttél-e fel.")
    st.stop()

missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
if missing:
    st.error("A fájl struktúrája nem megfelelő. Hiányzó oszlopok:")
    for col in missing:
        st.write(f"- `{col}`")
    st.stop()

df = df.dropna(subset=["website"])
df["website"] = df["website"].str.strip()

# ── Page visit szám beállítása ────────────────────────────────────────────────

DEFAULT_PV = 120_000_000
col_pv, _ = st.columns([1, 3])
with col_pv:
    pv_input = st.text_input("Összes page visit", value="120,000,000")
try:
    total_pv = int(pv_input.replace(",", "").replace(" ", "").replace(".", ""))
    if total_pv <= 0:
        raise ValueError
except ValueError:
    st.warning("Érvénytelen page visit szám, az alapértelmezett értéket használjuk (120 000 000).")
    total_pv = DEFAULT_PV

# Mindig az aktuális ref_km-mel és total_pv-vel számolunk
data = calc_all(df, st.session_state["ref_km"], total_pv)

st.success(f"{len(df)} sor betöltve – {df['website'].nunique()} weboldal")

# ── Drag&drop ─────────────────────────────────────────────────────────────────

st.divider()

with open("drag_drop.html", "r") as f:
    html = f.read()

card_images = [
    "sima_tarolo_bal", "sima_tarolo_jobb",
    "sima_fent_bal", "sima_fent_jobb",
    "sima_kozep_bal", "sima_kozep_jobb",
    "sima_lent_bal", "sima_lent_jobb",
    "sima_lent_egyedul_bal", "sima_lent_egyedul_jobb",
    "ora_egyedul", "ora_bal", "ora_jobb", "ora_mindketto",
    "kg", "washing", "car", "light", "household",
    "empty_template"
]

for name in card_images:
    html = html.replace(f"cards/{name}.png", img_to_base64(f"cards/{name}.png"))

def fmt(stats: dict, ref_km: float) -> dict:
    """Formázott értékek összerakása — szám + unit együtt."""
    bp_km    = stats['bp_paris_km_raw']
    bp_trips = round(bp_km / ref_km)
    return {
        "kg_co2":         f"{stats['kg_co2']:,.0f} KG CO2E",
        "wash":           f"{stats['wash']:,.0f} DB",
        "bp_paris_trips": f"{bp_km:,.0f} KM",
        "bp_paris_trips_count": bp_trips,
        "kg_saved":       f"{stats['kg_saved']:,.0f} KG CO2E",
        "kwh":            f"{stats['kwh']:,.0f} KWH",
        "house":          f"{stats['house']:,.0f}",
        "red_pct":        f"{stats['red_pct']*100:.1f}%",
    }

ref_km = st.session_state["ref_km"]
all_stats = {"Összesítő": fmt(data["summary"], ref_km)}
for pt, stats in data["by_pagetype"].items():
    all_stats[pt] = fmt(stats, ref_km)

city1_raw, city2_raw = st.session_state["ref_label"].split(" → ")
city1_fit, city2_fit = fit_cities(city1_raw, city2_raw)

labels = {
    "kg_co2":   {"description": ""},
    "wash":     {"description": shorten_label("NAGYMOSÁS")},
    "bp_paris": {"description": "", "route": shorten_label(f"{city1_fit} → {city2_fit}")},
    "kg_saved": {"description": ""},
    "kwh":      {"description": ""},
    "house":    {"description": shorten_label("HÁZTARTÁS ÉVES ÁRAMFOGYASZTÁSA")},
    "red_pct":  {"description": shorten_label("ÁTLAGOS CSÖKKENTÉSI POTENCIÁL")},
}

html = html.replace("{{ALL_STATS}}", json_lib.dumps(all_stats, ensure_ascii=False))
html = html.replace("{{LABELS}}", json_lib.dumps(labels, ensure_ascii=False))
components.html(html, height=600, scrolling=False)

# ── Számítások megjelenítése ──────────────────────────────────────────────────

# st.divider()

# page_options = ["Összesítő"] + sorted(data["by_pagetype"].keys())
# sel_oldal = st.radio("Oldal", page_options, horizontal=True)

# # Aktuális referencia megjelenítése
# st.caption(f"📍 Jelenlegi referencia útvonal: **{st.session_state['ref_label']}** ({st.session_state['ref_km']:,.0f} km)")

# if sel_oldal == "Összesítő":
#     st.json(data["summary"])
# else:
#     st.json(data["by_pagetype"][sel_oldal])

# ── Részletes nézet ───────────────────────────────────────────────────────────

# st.divider()

# reszletes = st.toggle("Részletes nézet")

# if reszletes:
#     st.divider()

#     scope = st.radio("Megjelenítés", ["Összes weboldal", "Egy weboldal"], horizontal=True)

#     col1, col2 = st.columns(2)

#     if scope == "Összes weboldal":
#         with col1:
#             sel_ipar = st.multiselect("Iparág", sorted(df["industry"].unique()), placeholder="Mind")
#         with col2:
#             sel_oldaltipus = st.multiselect("Oldaltípus", sorted(df["pageType"].unique()), placeholder="Mind")
#         filtered = df.copy()
#         if sel_ipar:
#             filtered = filtered[filtered["industry"].isin(sel_ipar)]
#         if sel_oldaltipus:
#             filtered = filtered[filtered["pageType"].isin(sel_oldaltipus)]
#     else:
#         with col1:
#             sel_ceg = st.selectbox("Válassz weboldalt", sorted(df["website"].unique()))
#         filtered = df[df["website"] == sel_ceg].copy()

#     st.dataframe(filtered.reset_index(drop=True), width='stretch')

# ── Távolságkalkulátor ────────────────────────────────────────────────────────

st.divider()
st.subheader("🚗 Útvonal-kalkulátor")
st.caption("Válassz két helyszínt, és megmutatjuk, hányszor felel meg a carbon kibocsátás annak az útnak.")

col_a, col_b = st.columns(2)
with col_a:
    loc1 = st.text_input("📍 Kiindulópont", value="Budapest, Hungary")
with col_b:
    loc2 = st.text_input("🏁 Célpont", value="Paris, France")

if st.button("Távolság kiszámítása"):
    with st.spinner("Helyszínek keresése..."):
        g1 = geocode_location(loc1)
        g2 = geocode_location(loc2)

    if not g1:
        st.error(f"Nem találtam meg ezt a helyet: {loc1}")
    elif not g2:
        st.error(f"Nem találtam meg ezt a helyet: {loc2}")
    else:
        with st.spinner("Útvonal kiszámítása..."):
            dist_km = get_road_distance(g1.latitude, g1.longitude, g2.latitude, g2.longitude)

        if dist_km is None:
            st.error("Nem sikerült az útvonalat kiszámítani. Lehet, hogy nincs közúti összeköttetés (pl. különböző kontinensek)?")
        else:
            city1 = extract_city(g1)
            city2 = extract_city(g2)
            st.session_state["ref_km"]        = dist_km
            st.session_state["ref_label"]     = f"{city1} → {city2}"
            st.session_state["calc_dist_km"]  = dist_km
            st.session_state["calc_address1"] = g1.address
            st.session_state["calc_address2"] = g2.address
            st.rerun()

# ── Kalkulátor eredménye (rerun után is megmarad) ─────────────────────────────

if "calc_dist_km" in st.session_state:
    dist_km = st.session_state["calc_dist_km"]
    new_data = calc_all(df, dist_km, total_pv)
    kg_co2_total = new_data["summary"]["kg_co2"]
    trips        = new_data["summary"]["bp_paris_km_raw"] / dist_km

    st.success(f"📏 Közúti távolság: **{dist_km:,.0f} km** – ez lett az új referencia útvonal!")
    st.caption(f"({st.session_state['calc_address1']}  →  {st.session_state['calc_address2']})")

    col1, col2, col3 = st.columns(3)
    col1.metric("Választott útvonal", f"{dist_km:,.0f} km")
    col2.metric("Összesített CO₂ kibocsátás", f"{kg_co2_total:,.0f} kg")
    col3.metric("Hányszor teszi meg ezt az utat?", f"{trips:,.0f}×")

    bp_paris_equiv = dist_km / BP_PARIS_KM
    st.info(
        f"A választott útvonal **{bp_paris_equiv:.2f}×** a Budapest–Párizs "
        f"távolságnak ({BP_PARIS_KM} km). "
        f"Az összes vizsgált weboldal carbon kibocsátása összesen "
        f"**{trips:,.0f}×** megtételének felel meg."
    )