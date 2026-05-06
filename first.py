import pandas as pd
import streamlit as st
import json as json_lib
import streamlit.components.v1 as components
import base64
import requests
import time

from geopy.geocoders import Nominatim

# Nyelvek miatt lett belerakva
from deep_translator import GoogleTranslator

st.set_page_config(page_title="Carbon Crane", page_icon="🌿", layout="wide")

# ── Statikus konstansok ───────────────────────────────────────────────────────

CO2_PER_WASH  = 0.236615995  # 1 mosás CO2e-je [kg]
CO2_PER_KM    = 0.215118375  # 1 km autózás CO2e-je [kg]
CO2_PER_KWH   = 0.236150771  # 1 kWh áram CO2-je [kg]
KWH_PER_HOUSE = 2500         # 1 háztartás éves energiafogyasztása [kWh]
BP_PARIS_KM   = 1485         # Budapest → Párizs távolság [km] (alapértelmezett)

COL_EM_ALL   = "BE - Carbon Emission - all subpages"
COL_EM_PAGE  = "BE - Carbon Emission - page"
COL_RED_PAGE = "BE - Reduced Carbon Emission"
COL_RED_ALL  = "BE - Reduced Carbon Emission - all subpages"

REQUIRED_COLUMNS = [
    "industry", "website", "pageType", "have all subpages", "url",
    COL_EM_PAGE, COL_EM_ALL,
    "BE - Reduction % - page", "Reduction % - image",
    COL_RED_PAGE, COL_RED_ALL,
    "BE - Reduction % - all subpages", "Rank Reduction % - page",
    "Rank Reduced Carbon Emission", "Rank Reduction % - all subpages",
    "Rank Reduced Carbon Emission -  all subpages",
]

# ── Nyelvek megadása ───────────────────────────────────────────────────────
LANGUAGES = {
    "Angol": "en",
    "Spanyol": "es",
    "Francia": "fr",
    "Német": "de",
    "Kínai (egyszerűsített)": "zh-CN",
    "Hindi": "hi",
    "Arab": "ar",
    "Orosz": "ru",
    "Portugál": "pt",
    "Japán": "ja",
    "Olasz": "it",
    "Koreai": "ko",
    "Török": "tr",
    "Holland": "nl",
    "Magyar": "hu"
}
# ── Session state inicializálás ───────────────────────────────────────────────

if "ref_km" not in st.session_state:
    st.session_state["ref_km"] = BP_PARIS_KM
if "ref_label" not in st.session_state:
    st.session_state["ref_label"] = "Budapest → Párizs"

# ── Segédfüggvények ───────────────────────────────────────────────────────────

def img_to_base64(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

def hu(val: float) -> str:
    """Szám formázása szóközös ezres elválasztóval."""
    return f"{round(val):,}".replace(",", " ")

def shorten_label(text: str, max_chars: int = 32) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "."

def fit_cities(city1: str, city2: str, max_total: int = 22) -> tuple:
    if len(city1) + len(city2) <= max_total:
        return city1, city2
    if len(city1) >= len(city2):
        city1 = city1[:10].rstrip() + "."
    else:
        city2 = city2[:10].rstrip() + "."
    if len(city1) + len(city2) > max_total:
        if not city1.endswith("."):
            city1 = city1[:10].rstrip() + "."
        if not city2.endswith("."):
            city2 = city2[:10].rstrip() + "."
    return city1, city2

# ── Számítások ────────────────────────────────────────────────────────────────

def calc_raw(rows: pd.DataFrame, col_em: str, col_red: str) -> dict:
    """
    Nyers (PV-független) statisztikák egy adott szűrésre.
    Csak em_avg és red_pct kerül vissza – a JS számolja a többi értéket.

    em_avg:  sor-szintű mean() – egyezik az Excel/minta logikájával
    red_pct: shop-szintű sum(red)/sum(em) – súlyozott, nagy emittálók
             nagyobb hatással bírnak a végeredményre
    """
    em_avg     = rows[col_em].mean()
    shop_level = rows.groupby("website")[[col_em, col_red]].first()
    red_pct    = shop_level[col_red].sum() / shop_level[col_em].sum()
    return {
        "em_avg":  em_avg,
        "red_pct": red_pct,
    }


def build_raw_stats(df: pd.DataFrame) -> dict:
    """
    Nyers statisztikák minden szűrési kombinációra.
    A JS fogja ezekből a végleges értékeket kiszámolni a PV alapján.
    Kulcsok:
      - "Összesítő"          → összes sor, COL_EM_ALL
      - "Összesítő|pagetype" → összes webshop, adott oldaltípus
      - "website|"           → adott webshop, összes oldaltípus
      - "website|pagetype"   → adott webshop, adott oldaltípus
    """
    result = {}

    result["Összesítő"] = calc_raw(df, COL_EM_ALL, COL_RED_ALL)

    for pagetype, pt_df in df.groupby("pageType"):
        result[f"Összesítő|{pagetype}"] = calc_raw(pt_df, COL_EM_PAGE, COL_RED_PAGE)

    for website, site_df in df.groupby("website"):
        result[f"{website}|"] = calc_raw(site_df, COL_EM_ALL, COL_RED_ALL)
        for pagetype, pt_df in site_df.groupby("pageType"):
            result[f"{website}|{pagetype}"] = calc_raw(pt_df, COL_EM_PAGE, COL_RED_PAGE)

    return result


def build_pagetypes_map(df: pd.DataFrame) -> dict:
    """
    Visszaadja, melyik webshophoz milyen oldaltípusok tartoznak.
    { "Összesítő": ["főoldal", "kosár", ...], "eMAG": ["főoldal", "kosár", ...], ... }
    Az Összesítőnél az összes létező oldaltípus szerepel.
    """
    all_pagetypes = sorted(df["pageType"].unique().tolist())
    result = {"Összesítő": all_pagetypes}
    for website, group in df.groupby("website"):
        result[website] = sorted(group["pageType"].unique().tolist())
    return result

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
    raw = location.raw.get("address", {})
    return (
        raw.get("city") or
        raw.get("town") or
        raw.get("village") or
        raw.get("municipality") or
        location.address.split(",")[0]
    )

@st.cache_data(show_spinner=False)
def get_road_distance(lat1, lon1, lat2, lon2):
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


# ── Nyelve fv. ─────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def translate_text(text: str, target_lang: str) -> str:
    """Szöveg fordítása a megadott célnyelvre."""
    if target_lang == "hu" or not text:
        return text
    try:
        translator = GoogleTranslator(source='hu', target=target_lang)
        return translator.translate(text)
    except Exception:
        return text
        
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
    df.columns = df.columns.str.strip()  # trailing/leading szóközök eltávolítása
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

st.success(f"{len(df)} sor betöltve – {df['website'].nunique()} weboldal")

# ── Több nyelvhez kell ─────────────────────────────────────────────────────

st.divider()
st.subheader("🌐 Nyelvbeállítások")
selected_lang_name = st.selectbox("Válaszd ki az infografika nyelvét:", list(LANGUAGES.keys()))
lang_code = LANGUAGES[selected_lang_name]

# ── Drag&drop összerakása ─────────────────────────────────────────────────────

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

ref_km = st.session_state["ref_km"]

raw_stats     = build_raw_stats(df)
pagetypes_map = build_pagetypes_map(df)
websites_list = ["Összesítő"] + sorted(df["website"].unique().tolist())

city1_raw, city2_raw = st.session_state["ref_label"].split(" → ")
city1_fit, city2_fit = fit_cities(city1_raw, city2_raw)

# ── Nyelvek miatt labelek cseréje ──────────────────────────────────────────────

labels = {
   "kg_co2":   {"description": shorten_label(translate_text("SZÉN-DIOXID KIBOCSÁTÁS", lang_code))},
    "wash":     {"description": shorten_label(translate_text("NAGYMOSÁS", lang_code))},
    "bp_paris": {"description": "", "route": shorten_label(translate_text(f"{city1_fit} → {city2_fit}", lang_code))},
    "kg_saved": {"description": shorten_label(translate_text("CSÖKKENTETT SZÉN-DIOXID", lang_code))},
    "kwh":      {"description": shorten_label(translate_text("ÉVES ÁRAMFOGYASZTÁS", lang_code))},
    "house":    {"description": shorten_label(translate_text("HÁZTARTÁS ÉVES ÁRAMFOGYASZTÁSA", lang_code))},
    "red_pct":  {"description": shorten_label(translate_text("ÁTLAGOS CSÖKKENTÉSI POTENCIÁL", lang_code))},
    
    # "kg_co2":   {"description": ""},
    # "wash":     {"description": shorten_label("NAGYMOSÁS")},
    # "bp_paris": {"description": "", "route": shorten_label(f"{city1_fit} → {city2_fit}")},
    # "kg_saved": {"description": ""},
    # "kwh":      {"description": ""},
    # "house":    {"description": shorten_label("HÁZTARTÁS ÉVES ÁRAMFOGYASZTÁSA")},
    # "red_pct":  {"description": shorten_label("ÁTLAGOS CSÖKKENTÉSI POTENCIÁL")},
}

# Konstansok átadása a JS-nek (Python-ban definiáltak, JS számolja a végeredményt)
constants = {
    "CO2_PER_WASH":  CO2_PER_WASH,
    "CO2_PER_KM":    CO2_PER_KM,
    "CO2_PER_KWH":   CO2_PER_KWH,
    "KWH_PER_HOUSE": KWH_PER_HOUSE,
    "ref_km":        ref_km,
    "default_pv":    120_000_000,
}

html = html.replace("{{RAW_STATS}}",  json_lib.dumps(raw_stats,     ensure_ascii=False))
html = html.replace("{{LABELS}}",     json_lib.dumps(labels,        ensure_ascii=False))
html = html.replace("{{WEBSITES}}",   json_lib.dumps(websites_list, ensure_ascii=False))
html = html.replace("{{PAGETYPES}}",  json_lib.dumps(pagetypes_map, ensure_ascii=False))
html = html.replace("{{CONSTANTS}}",  json_lib.dumps(constants,     ensure_ascii=False))

components.html(html, height=660, scrolling=False)

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
    dist_km  = st.session_state["calc_dist_km"]
    # A kalkulátornál csak az útvonal-info jelenik meg, PV-független adatok
    em_avg   = df[COL_EM_ALL].mean()
    red_raw  = build_raw_stats(df)["Összesítő"]

    st.success(f"📏 Közúti távolság: **{dist_km:,.0f} km** – ez lett az új referencia útvonal!")
    st.caption(f"({st.session_state['calc_address1']}  →  {st.session_state['calc_address2']})")

    bp_paris_equiv = dist_km / BP_PARIS_KM
    st.info(
        f"A választott útvonal **{bp_paris_equiv:.2f}×** a Budapest–Párizs "
        f"távolságnak ({BP_PARIS_KM} km). "
        f"Az infografika értékei automatikusan frissültek az új referencia alapján."
    )

