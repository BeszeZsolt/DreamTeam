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

CO2_PER_WASH  = 0.236615995   # 1 mosás CO2e [kg]
CO2_PER_KM    = 0.215118375   # 1 km autóút CO2e [kg]
CO2_PER_KWH   = 0.236150771   # 1 kWh áram CO2e [kg]
KWH_PER_HOUSE = 2500          # 1 háztartás éves áramfogyasztása [kWh]
BP_PARIS_KM   = 1485          # Budapest → Párizs alapértelmezett távolság [km]

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
if "ai_summary"      not in st.session_state: st.session_state["ai_summary"]      = None
if "ai_summary_meta" not in st.session_state: st.session_state["ai_summary_meta"] = {}

# ── Segédfüggvények ───────────────────────────────────────────────────────────

def img_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

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

# ── AI összefoglaló (Groq) ────────────────────────────────────────────────────

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_API_KEY = "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"

def calc_full_stats(em_avg: float, red_pct: float, ref_km: float,
                    total_pv: int = 120_000_000) -> dict:
    kg_co2   = em_avg * total_pv / 1000
    kg_saved = kg_co2 * red_pct
    kwh      = kg_saved / CO2_PER_KWH
    return {
        "kg_co2":         kg_co2,
        "wash":           kg_co2   / CO2_PER_WASH,
        "bp_paris_trips": kg_co2   / CO2_PER_KM / ref_km,
        "red_pct":        red_pct,
        "kg_saved":       kg_saved,
        "kwh":            kwh,
        "house":          kwh / KWH_PER_HOUSE,
    }

def generate_ai_summary(em_avg: float, red_pct: float, ref_km: float,
                         scope_label: str, num_sites: int, num_rows: int,
                         industries: list, route_label: str,
                         out_lang: str) -> str:
    s = calc_full_stats(em_avg, red_pct, ref_km)
    industry_str = ", ".join(sorted(set(str(i) for i in industries if i))[:8])

    prompt = f"""You are a sustainability analyst writing a concise infographic report for Carbon Crane.
Write a professional summary in {out_lang}. Use 3-4 short paragraphs of flowing prose.
No markdown headers, no bullet points, no bold text. Just clean readable paragraphs.
Highlight the most striking numbers, put the carbon footprint in human-scale context using the analogies,
mention the industries covered, and end on a forward-looking note about the reduction potential.

DATA SNAPSHOT:
- Scope: {scope_label}
- Websites analysed: {num_sites}
- Data rows: {num_rows}
- Industries: {industry_str}
- Reference route: {route_label}
- Total page views modelled: 120,000,000

CARBON EMISSION (120M page views):
- Total CO2: {s['kg_co2']:,.0f} kg
- Equivalent laundry loads: {s['wash']:,.0f}
- Equivalent {route_label} road trips: {s['bp_paris_trips']:,.0f}

REDUCTION POTENTIAL:
- Reduction rate: {s['red_pct']*100:.1f}%
- CO2 saved: {s['kg_saved']:,.0f} kg
- Energy saved: {s['kwh']:,.0f} kWh
- Equivalent households powered: {s['house']:,.0f}

Write only the summary paragraphs, nothing else."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":       GROQ_MODEL,
        "max_tokens":  700,
        "temperature": 0.65,
        "messages":    [{"role": "user", "content": prompt}],
    }
    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        if code == 401: return "Invalid API key."
        if code == 429: return "Rate limit reached. Please wait a minute and try again."
        return f"API error ({code}): {e}"
    except Exception as e:
        return f"Error during generation: {e}"

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

card_images = [
    "sima_tarolo_bal", "sima_tarolo_jobb",
    "sima_fent_bal", "sima_fent_jobb",
    "sima_kozep_bal", "sima_kozep_jobb",
    "sima_lent_bal", "sima_lent_jobb",
    "sima_lent_egyedul_bal", "sima_lent_egyedul_jobb",
    "ora_egyedul", "ora_bal", "ora_jobb", "ora_mindketto",
    "kg", "washing", "car", "light", "household",
    "empty_template",
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
    "CO2_PER_WASH":  CO2_PER_WASH,
    "CO2_PER_KM":    CO2_PER_KM,
    "CO2_PER_KWH":   CO2_PER_KWH,
    "KWH_PER_HOUSE": KWH_PER_HOUSE,
    "ref_km":        ref_km,
    "default_pv":    120_000_000,
    "lang_code":     lang_code,
    "summary_key":   SUMMARY_KEY,
    "session_id":    st.session_state["session_id"],
}

html = html.replace("{{RAW_STATS}}",       safe_json(raw_stats))
html = html.replace("{{LABELS}}",          safe_json(labels))
html = html.replace("{{WEBSITES}}",        safe_json(websites_list))
html = html.replace("{{PAGETYPES}}",       safe_json(pagetypes_map))
html = html.replace("{{CONSTANTS}}",       safe_json(constants))
ai_summary_text = st.session_state.get("ai_summary") or ""
html = html.replace("{{AI_SUMMARY_JSON}}", json_lib.dumps(ai_summary_text, ensure_ascii=False))

components.html(html, height=660, scrolling=False)

# ── AI összefoglaló ───────────────────────────────────────────────────────────

st.divider()
st.subheader("AI Summary Generator")
st.caption("Powered by Groq. Select the view you want to summarize, then click Generate.")

col_ws, col_pt = st.columns(2)
with col_ws:
    ai_website = st.selectbox(
        "Website:",
        websites_list,
        index=0,
        key="ai_website_sel",
        format_func=lambda w: get_label("all_website", lang_code) if w == SUMMARY_KEY else w,
    )
with col_pt:
    pt_options = [""] + (pagetypes_map.get(ai_website) or [])
    ai_pagetype = st.selectbox(
        "Page type:",
        pt_options,
        index=0,
        key="ai_pagetype_sel",
        format_func=lambda p: get_label("all_pagetype", lang_code) if p == "" else p,
    )

if st.button("Generate AI Summary", use_container_width=True):
    if ai_website == SUMMARY_KEY:
        stat_key = SUMMARY_KEY if ai_pagetype == "" else f"{SUMMARY_KEY}|{ai_pagetype}"
    else:
        stat_key = f"{ai_website}|" if ai_pagetype == "" else f"{ai_website}|{ai_pagetype}"

    raw = raw_stats.get(stat_key)
    if not raw:
        st.error(f"No data found for this selection ({stat_key}).")
    else:
        scope_lbl  = ai_website if ai_website != SUMMARY_KEY else "All websites"
        pt_display = ai_pagetype if ai_pagetype else get_label("all_pagetype", lang_code)
        route_lbl  = f"{city1_raw} \u2192 {city2_raw}"
        scope_df   = df if ai_website == SUMMARY_KEY else df[df["website"] == ai_website]
        if ai_pagetype:
            scope_df = scope_df[scope_df["pageType"] == ai_pagetype]

        with st.spinner("Generating summary..."):
            summary = generate_ai_summary(
                em_avg      = raw["em_avg"],
                red_pct     = raw["red_pct"],
                ref_km      = st.session_state["ref_km"],
                scope_label = f"{scope_lbl} – {pt_display}",
                num_sites   = scope_df["website"].nunique(),
                num_rows    = len(scope_df),
                industries  = scope_df["industry"].dropna().tolist(),
                route_label = route_lbl,
                out_lang    = selected_lang_name,
            )
        st.session_state["ai_summary"]      = summary
        st.session_state["ai_summary_meta"] = {
            "website":  ai_website,
            "pagetype": pt_display,
            "lang":     selected_lang_name,
        }
        st.rerun()

if st.session_state.get("ai_summary"):
    meta = st.session_state["ai_summary_meta"]
    st.success(f"**{meta.get('website','?')}** · {meta.get('pagetype','?')} · {meta.get('lang','?')}")
    st.write(st.session_state["ai_summary"])
    st.info("The summary will automatically be included in the PDF export.")

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
    st.info(
        f"The selected route is **{dist_km / BP_PARIS_KM:.2f}×** the Budapest–Paris "
        f"distance ({BP_PARIS_KM} km). "
        f"The infographic values have been updated to use this as the new reference route."
    )
