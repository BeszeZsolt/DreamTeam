import pandas as pd
import streamlit as st
import json as json_lib
import streamlit.components.v1 as components
import base64
import requests
import time
import uuid
from geopy.geocoders import Nominatim
from deep_translator import GoogleTranslator


st.set_page_config(page_title="Carbon Crane", page_icon="🌿", layout="wide")

# ── Statikus konstansok ───────────────────────────────────────────────────────

CO2_PER_WASH  = 0.236615995
CO2_PER_KM    = 0.215118375
CO2_PER_KWH   = 0.236150771
KWH_PER_HOUSE = 2500
BP_PARIS_KM   = 1485

COL_EM_ALL   = "BE - Carbon Emission - all subpages"
COL_EM_PAGE  = "BE - Carbon Emission - page"
COL_RED_PAGE = "BE - Reduced Carbon Emission"
COL_RED_ALL  = "BE - Reduced Carbon Emission - all subpages"

SUMMARY_KEY = "Összesítő"

REQUIRED_COLUMNS = [
    "industry", "website", "pageType", "have all subpages", "url",
    COL_EM_PAGE, COL_EM_ALL,
    "BE - Reduction % - page", "Reduction % - image",
    COL_RED_PAGE, COL_RED_ALL,
    "BE - Reduction % - all subpages", "Rank Reduction % - page",
    "Rank Reduced Carbon Emission", "Rank Reduction % - all subpages",
    "Rank Reduced Carbon Emission -  all subpages",
]

DEFAULT_CITY_1 = "Budapest"
DEFAULT_CITY_2 = "Paris"

LANGUAGES = {
    "Hungarian":             "hu",
    "English":               "en",
    "German":                "de",
    "French":                "fr",
    "Spanish":               "es",
    "Italian":               "it",
    "Dutch":                 "nl",
    "Portuguese":            "pt",
    "Russian":               "ru",
    "Japanese":              "ja",
    "Chinese (Simplified)":  "zh-CN",
    "Korean":                "ko",
    "Arabic":                "ar",
    "Hindi":                 "hi",
    "Turkish":               "tr",
}

MANUAL_LANG_CODES = {"hu", "en"}

MANUAL_TRANSLATIONS = {
    "hu": {
        "wash":                "NAGYMOSÁS",
        "house":               "HÁZTARTÁS ÉVES ÁRAMFOGYASZTÁSA",
        "red_pct_multi":       "ÁTLAGOS CSÖKKENTÉSI POTENCIÁL",
        "red_pct_single":      "CSÖKKENTÉSI POTENCIÁL",
        "all_website":         SUMMARY_KEY,
        "all_pagetype":        "Összes oldal",
        "subtitle_multi":      "Mekkora a {} vizsgált webshop oldalainak CO\u2082e kibocsátása?",
        "subtitle_single":     "Mekkora a(z) {} oldalainak CO\u2082e kibocsátása?",
        "pv_multi_visit":      "{} látogatás esetén összesen: {} webshop",
        "pv_multi_page":       "{} oldalbetöltés esetén összesen: {} webshop: {}",
        "pv_single_visit":     "{} látogatás esetén – {}",
        "pv_single_page":      "{} oldalbetöltés esetén – {}: {}",
    },
    "en": {
        "wash":                "LAUNDRY LOADS",
        "house":               "ANNUAL HOUSEHOLD ELECTRICITY",
        "red_pct_multi":       "AVERAGE REDUCTION POTENTIAL",
        "red_pct_single":      "REDUCTION POTENTIAL",
        "all_website":         "Summary",
        "all_pagetype":        "All pages",
        "subtitle_multi":      "What is the CO\u2082e footprint of the {} websites examined?",
        "subtitle_single":     "What is the CO\u2082e footprint of {}?",
        "pv_multi_visit":      "For {} visits across {} websites",
        "pv_multi_page":       "For {} page views across {} websites: {}",
        "pv_single_visit":     "For {} visits – {}",
        "pv_single_page":      "For {} page views – {}: {}",
    },
}

# ── Fordítás ──────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def translate_text(text: str, target_lang: str) -> str:
    if not text or target_lang == "en":
        return text
    try:
        return GoogleTranslator(source="en", target=target_lang).translate(text)
    except Exception:
        return text

def get_label(key: str, lang_code: str) -> str:
    if lang_code in MANUAL_LANG_CODES:
        return MANUAL_TRANSLATIONS[lang_code].get(key, "")
    return translate_text(MANUAL_TRANSLATIONS["en"].get(key, ""), lang_code)

# ── Session state inicializálás ───────────────────────────────────────────────

if "ref_km"          not in st.session_state: st.session_state["ref_km"]          = BP_PARIS_KM
if "geocoded_cities" not in st.session_state: st.session_state["geocoded_cities"] = None
if "session_id"      not in st.session_state: st.session_state["session_id"]      = uuid.uuid4().hex

# ── Segédfüggvények ───────────────────────────────────────────────────────────

def img_to_base64(path: str) -> str:
    """PNG képet data URI-vá alakít."""
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

    with open(path, "rb") as f:
        return "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode()

def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "."

def fit_cities(city1: str, city2: str, max_total: int = 22) -> tuple:
    sep_len = 3
    budget  = max_total - sep_len
    if len(city1) + len(city2) <= budget:
        return city1, city2
    half = budget // 2
    if len(city1) > half: city1 = truncate(city1, half - 1)
    if len(city2) > half: city2 = truncate(city2, half - 1)
    return city1, city2

def get_route_cities(lang_code: str) -> tuple:
    if st.session_state["geocoded_cities"] is not None:
        cities = st.session_state["geocoded_cities"]
        g1 = geocode_location(cities["loc1_raw"], lang_code)
        g2 = geocode_location(cities["loc2_raw"], lang_code)
        c1 = extract_city(g1) if g1 else cities["city1_en"]
        c2 = extract_city(g2) if g2 else cities["city2_en"]
        return c1, c2
    return (
        translate_text(DEFAULT_CITY_1, lang_code),
        translate_text(DEFAULT_CITY_2, lang_code),
    )

# ── Számítások ────────────────────────────────────────────────────────────────

def calc_raw(rows: pd.DataFrame, col_em: str, col_red: str) -> dict:
    em_avg     = rows[col_em].mean()
    shop_level = rows.groupby("website")[[col_em, col_red]].first()
    red_pct    = shop_level[col_red].sum() / shop_level[col_em].sum()
    return {"em_avg": em_avg, "red_pct": red_pct}

def build_raw_stats(df: pd.DataFrame) -> dict:
    result = {}
    result[SUMMARY_KEY] = calc_raw(df, COL_EM_ALL, COL_RED_ALL)
    for pagetype, pt_df in df.groupby("pageType"):
        result[f"{SUMMARY_KEY}|{pagetype}"] = calc_raw(pt_df, COL_EM_PAGE, COL_RED_PAGE)
    for website, site_df in df.groupby("website"):
        result[f"{website}|"] = calc_raw(site_df, COL_EM_ALL, COL_RED_ALL)
        for pagetype, pt_df in site_df.groupby("pageType"):
            result[f"{website}|{pagetype}"] = calc_raw(pt_df, COL_EM_PAGE, COL_RED_PAGE)
    return result

def build_pagetypes_map(df: pd.DataFrame) -> dict:
    result = {SUMMARY_KEY: sorted(df["pageType"].unique().tolist())}
    for website, group in df.groupby("website"):
        result[website] = sorted(group["pageType"].unique().tolist())
    return result

# ── Geocoder ──────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def geocode_location(place: str, language: str = "en"):
    geolocator = Nominatim(
        user_agent=f"carbon_crane_{uuid.uuid4().hex[:8]}",
        timeout=10,
    )
    for attempt in range(3):
        try:
            time.sleep(1.1)
            return geolocator.geocode(place, language=language)
        except Exception as e:
            wait = 3 * (attempt + 1) if "rate" in str(e).lower() else 2
            if attempt < 2:
                time.sleep(wait)
    return None

def extract_city(location) -> str:
    raw = location.raw.get("address", {})
    return (
        raw.get("city") or raw.get("town") or raw.get("village")
        or raw.get("municipality") or location.address.split(",")[0]
    )

@st.cache_data(show_spinner=False)
def get_road_distance(lat1, lon1, lat2, lon2):
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}?overview=false"
    )
    try:
        data = requests.get(url, timeout=10).json()
        if data.get("code") == "Ok":
            return data["routes"][0]["distance"] / 1000
    except Exception:
        pass
    return None

# ── UI ────────────────────────────────────────────────────────────────────────

st.title("Carbon Crane Infographic Builder")

uploaded = st.file_uploader("Upload Excel file (.xlsx)", type=["xlsx"])
if not uploaded:
    st.stop()

try:
    xl = pd.ExcelFile(uploaded)
    matching = [s for s in xl.sheet_names if "carbon_scan_output_ecomm" in s]
    if not matching:
        st.error("No sheet named 'carbon_scan_output_ecomm' found in the file.")
        st.stop()
    df = xl.parse(matching[0], header=0)
    df.columns = df.columns.str.strip()
except Exception:
    st.error("File could not be read. Please upload a valid .xlsx file.")
    st.stop()

missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
if missing:
    st.error("File structure is invalid. Missing columns:")
    for col in missing:
        st.write(f"- `{col}`")
    st.stop()

df = df.dropna(subset=["website"])
df["website"] = df["website"].str.strip()
df = df[df["pageType"].notna() & (df["pageType"] != "oldaltípus")]

n_websites = df["website"].nunique()
st.success(f"{len(df)} rows loaded – {n_websites} websites")

st.divider()

with open("drag_drop.html", "r") as f:
    html = f.read()

# ── PNG kártyák + monitor képek base64 injektálása ──────────────────────────────
card_images = [
    "sima_tarolo_bal", "sima_tarolo_jobb",
    "sima_fent_bal", "sima_fent_jobb",
    "sima_kozep_bal", "sima_kozep_jobb",
    "sima_lent_bal", "sima_lent_jobb",
    "sima_lent_egyedul_bal", "sima_lent_egyedul_jobb",
    "ora_egyedul", "ora_bal", "ora_jobb", "ora_mindketto",
    "kg", "washing", "car", "light", "household",
    "empty_template",
    "monitor1", "monitor2", "monitor3", "monitor4",
]
for name in card_images:
    html = html.replace(f"cards/{name}.png", img_to_base64(f"cards/{name}.png"))


# ── Nyelvválasztó ─────────────────────────────────────────────────────────────

col_lang, _ = st.columns([1, 3])
with col_lang:
    lang_names = list(LANGUAGES.keys())
    selected_lang_name = st.selectbox(
        "Infographic language:",
        lang_names,
        index=lang_names.index(st.session_state.get("selected_lang_name", "Hungarian")),
        key="lang_selectbox",
    )
    st.session_state["selected_lang_name"] = selected_lang_name

lang_code = LANGUAGES[selected_lang_name]

# ── Útvonal városnevei ────────────────────────────────────────────────────────

city1_raw, city2_raw = get_route_cities(lang_code)
city1_fit, city2_fit = fit_cities(city1_raw, city2_raw)

# ── Adatok összerakása ────────────────────────────────────────────────────────

raw_stats     = build_raw_stats(df)
pagetypes_map = build_pagetypes_map(df)
websites_list = [SUMMARY_KEY] + sorted(df["website"].unique().tolist())
ref_km        = st.session_state["ref_km"]

# ── Groq API kulcs — secrets.toml-ból olvasva ────────────────────────────────
groq_api_key = st.secrets["GROQ_API_KEY"]

def safe_json(obj) -> str:
    return json_lib.dumps(obj, ensure_ascii=False).replace("`", "\\`")

labels = {
    "kg_co2":            {"description": ""},
    "wash":              {"description": truncate(get_label("wash", lang_code), 32)},
    "bp_paris":          {"description": "", "route": truncate(f"{city1_fit} \u2192 {city2_fit}", 32)},
    "kg_saved":          {"description": ""},
    "kwh":               {"description": ""},
    "house":             {"description": truncate(get_label("house", lang_code), 32)},
    "red_pct_multi":     {"description": truncate(get_label("red_pct_multi", lang_code), 32)},
    "red_pct_single":    {"description": truncate(get_label("red_pct_single", lang_code), 32)},
    "all_website":       {"description": get_label("all_website", lang_code)},
    "all_pagetype":      {"description": get_label("all_pagetype", lang_code)},
    "unit_db":           {"description": ""},
    "subtitle_multi":    {"description": get_label("subtitle_multi", lang_code)},
    "subtitle_single":   {"description": get_label("subtitle_single", lang_code)},
    "pv_multi_visit":    {"description": get_label("pv_multi_visit", lang_code)},
    "pv_multi_page":     {"description": get_label("pv_multi_page", lang_code)},
    "pv_single_visit":   {"description": get_label("pv_single_visit", lang_code)},
    "pv_single_page":    {"description": get_label("pv_single_page", lang_code)},
}

constants = {
    "CO2_PER_WASH":       CO2_PER_WASH,
    "CO2_PER_KM":         CO2_PER_KM,
    "CO2_PER_KWH":        CO2_PER_KWH,
    "KWH_PER_HOUSE":      KWH_PER_HOUSE,
    "ref_km":             ref_km,
    "default_pv":         120_000_000,
    "lang_code":          lang_code,
    "summary_key":        SUMMARY_KEY,
    "session_id":         st.session_state["session_id"],
    "selected_lang_name": selected_lang_name,
    "route_label":        f"{city1_raw} \u2192 {city2_raw}",
}

html = html.replace("{{RAW_STATS}}",    safe_json(raw_stats))
html = html.replace("{{LABELS}}",       safe_json(labels))
html = html.replace("{{WEBSITES}}",     safe_json(websites_list))
html = html.replace("{{PAGETYPES}}",    safe_json(pagetypes_map))
html = html.replace("{{CONSTANTS}}",    safe_json(constants))
html = html.replace("{{GROQ_API_KEY}}", groq_api_key)

components.html(html, height=840, scrolling=True)

# ── Távolságkalkulátor ────────────────────────────────────────────────────────

st.divider()
st.subheader("Route calculator")
st.caption("Choose two locations to see how many times the carbon footprint equals that distance.")

col_a, col_b = st.columns(2)
with col_a:
    loc1 = st.text_input("Starting point", value="Budapest, Hungary")
with col_b:
    loc2 = st.text_input("Destination", value="Paris, France")

if st.button("Calculate distance"):
    with st.spinner("Looking up locations..."):
        g1 = geocode_location(loc1, "en")
        g2 = geocode_location(loc2, "en")
    if not g1:
        st.error(f"Could not find location: {loc1}")
    elif not g2:
        st.error(f"Could not find location: {loc2}")
    else:
        with st.spinner("Calculating route..."):
            dist_km = get_road_distance(g1.latitude, g1.longitude, g2.latitude, g2.longitude)
        if dist_km is None:
            st.error("Could not calculate route. There may be no road connection between the two locations.")
        else:
            st.session_state["ref_km"] = dist_km
            st.session_state["geocoded_cities"] = {
                "loc1_raw": loc1, "loc2_raw": loc2,
                "city1_en": extract_city(g1), "city2_en": extract_city(g2),
                "lat1": g1.latitude,  "lon1": g1.longitude,
                "lat2": g2.latitude,  "lon2": g2.longitude,
            }
            st.rerun()

if st.session_state["geocoded_cities"] is not None:
    dist_km  = st.session_state["ref_km"]
    cities   = st.session_state["geocoded_cities"]
    g1_local = geocode_location(cities["loc1_raw"], lang_code)
    g2_local = geocode_location(cities["loc2_raw"], lang_code)
    addr1    = g1_local.address if g1_local else cities["city1_en"]
    addr2    = g2_local.address if g2_local else cities["city2_en"]
    st.success(f"Road distance: **{dist_km:,.0f} km** – this is now the reference route!")
    st.caption(f"({addr1}  →  {addr2})")