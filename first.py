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

CITY_NAMES = {
    "hu": ("Budapest", "Párizs"),
    "en": ("Budapest", "Paris"),
    "de": ("Budapest", "Paris"),
    "fr": ("Budapest", "Paris"),
    "es": ("Budapest", "París"),
    "it": ("Budapest", "Parigi"),
    "nl": ("Budapest", "Parijs"),
    "pt": ("Budapest", "Paris"),
    "ru": ("Будапешт", "Париж"),
    "ja": ("ブダペスト", "パリ"),
    "zh-CN": ("布达佩斯", "巴黎"),
    "ko": ("부다페스트", "파리"),
    "ar": ("بودابست", "باريس"),
    "hi": ("बुडापेस्ट", "पेरिस"),
    "tr": ("Budapeşte", "Paris"),
}

LANGUAGES = {
    "Hungarian": "hu",
    "English":   "en",
    "German":    "de",
    "French":    "fr",
    "Spanish":   "es",
    "Italian":   "it",
    "Dutch":     "nl",
    "Portuguese":"pt",
    "Russian":   "ru",
    "Japanese":  "ja",
    "Chinese (Simplified)": "zh-CN",
    "Korean":    "ko",
    "Arabic":    "ar",
    "Hindi":     "hi",
    "Turkish":   "tr",
}

@st.cache_data(show_spinner=False)
def translate_text(text: str, target_lang: str) -> str:
    if target_lang == "en" or not text:
        return text
    try:
        translator = GoogleTranslator(source='en', target=target_lang)
        return translator.translate(text)
    except Exception:
        return text

# ── Session state inicializálás ───────────────────────────────────────────────

if "ref_km" not in st.session_state:
    st.session_state["ref_km"] = BP_PARIS_KM
if "ref_label" not in st.session_state:
    st.session_state["ref_label"] = "Budapest → Paris"
if "groq_api_key" not in st.session_state:
    st.session_state["groq_api_key"] = ""
if "ai_summary" not in st.session_state:
    st.session_state["ai_summary"] = None
if "ai_summary_meta" not in st.session_state:
    st.session_state["ai_summary_meta"] = {}

# ── Segédfüggvények ───────────────────────────────────────────────────────────

def img_to_base64(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

def shorten_label(text: str, max_chars: int = 32) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "."

def fit_cities(city1: str, city2: str, max_total: int = 22) -> tuple:
    """
    JAVÍTVA: az eredeti logika mindkét nevet egyszerre csonkíthatta feleslegesen.
    Most a hosszabb nevet rövidítjük le arányosan, egymástól függetlenül.
    """
    if len(city1) + len(city2) <= max_total:
        return city1, city2
    # A hosszabb nevet rövidítjük le, hogy a másik változatlan maradjon
    budget = max_total - 1  # 1 karakter a "." jelnek
    if len(city1) > len(city2):
        city1 = city1[:budget - len(city2)].rstrip() + "."
    else:
        city2 = city2[:budget - len(city1)].rstrip() + "."
    # Ha még mindig túl hosszú, mindkettőt 9 karakterre vágjuk
    if len(city1) + len(city2) > max_total:
        city1 = city1[:9].rstrip() + "."
        city2 = city2[:9].rstrip() + "."
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
def geocode_location(place: str, language: str = "en"):
    geolocator = Nominatim(
        user_agent=f"carbon_crane_{uuid.uuid4().hex[:8]}",
        timeout=10
    )
    for attempt in range(3):
        try:
            time.sleep(1.1)
            result = geolocator.geocode(place, language=language)
            return result
        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "limit" in err or "429" in err:
                time.sleep(3 * (attempt + 1))
            elif attempt < 2:
                time.sleep(2)
            else:
                return None
    return None

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

# ── AI összefoglaló (Groq – ingyenes, hitelkártya nélkül) ─────────────────────

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

def calc_full_stats(em_avg: float, red_pct: float, ref_km: float, total_pv: int = 120_000_000) -> dict:
    """em_avg + red_pct alapján kiszámolja a teljes statisztikát az AI prompthoz."""
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
                         out_lang: str, api_key: str) -> str:
    """Groq Llama 3.3 70B-vel összefoglalót generál a jelenlegi adatokra."""
    s = calc_full_stats(em_avg, red_pct, ref_km)
    industry_str = ", ".join(sorted(set(str(i) for i in industries if i))[:8])

    prompt = f"""You are a sustainability analyst writing a concise infographic report for Carbon Crane.
Write a professional summary in {out_lang}. Use 3–4 short paragraphs of flowing prose.
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
- Total CO₂: {s['kg_co2']:,.0f} kg
- Equivalent laundry loads: {s['wash']:,.0f}
- Equivalent {route_label} road trips: {s['bp_paris_trips']:,.0f}

REDUCTION POTENTIAL:
- Reduction rate: {s['red_pct']*100:.1f}%
- CO₂ saved: {s['kg_saved']:,.0f} kg
- Energy saved: {s['kwh']:,.0f} kWh
- Equivalent households powered: {s['house']:,.0f}

Write only the summary paragraphs, nothing else."""

    headers = {
        "Authorization": f"Bearer {api_key}",
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
        if code == 401:
            return "❌ Invalid API key. Check your key at console.groq.com → API Keys."
        elif code == 429:
            return "❌ Rate limit reached. Please wait a minute and try again."
        else:
            return f"❌ API error ({code}): {e}"
    except Exception as e:
        return f"❌ Error during generation: {e}"

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

# Panel állapot visszaolvasása query param-ból
if "cc_state" in st.query_params:
    try:
        import base64 as _b64
        raw = st.query_params["cc_state"]
        decoded = json_lib.loads(_b64.b64decode(raw.encode()).decode())
        st.session_state["panel_state"] = decoded
    except Exception:
        pass

n_websites = df['website'].nunique()
st.success(f"{len(df)} rows loaded – {n_websites} websites")

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

def safe_json(obj) -> str:
    """JSON sorosítás – backtick escape-elve hogy a JS template literál ne törjön el."""
    return json_lib.dumps(obj, ensure_ascii=False).replace("`", "\\`")

# ── JAVÍTVA: nyelvválasztó session state-ben tárolja az értéket,
#    így rerun (pl. távolságkalkulátor) után sem ugrik vissza az alapértelmezettre ──
col_lang, _ = st.columns([1, 3])
with col_lang:
    lang_names = list(LANGUAGES.keys())
    default_lang_idx = lang_names.index(
        st.session_state.get("selected_lang_name", "English")
    )
    selected_lang_name = st.selectbox(
        "Infographic language:",
        lang_names,
        index=default_lang_idx,
        key="lang_selectbox"
    )
    st.session_state["selected_lang_name"] = selected_lang_name

lang_code = LANGUAGES[selected_lang_name]

# Ha még nem volt geocoder számítás, az alapértelmezett városneveket
# a kiválasztott nyelv szerint adjuk meg
default_city1, default_city2 = CITY_NAMES.get(lang_code, ("Budapest", "Paris"))
ref_label = st.session_state["ref_label"]
# Ha az alapértelmezett BP→Paris valamelyik változata van eltárolva, frissítjük a nyelvre
if st.session_state["ref_km"] == BP_PARIS_KM:
    ref_label = f"{default_city1} → {default_city2}"

city1_raw, city2_raw = ref_label.split(" → ")
city1_fit, city2_fit = fit_cities(city1_raw, city2_raw)

def t(text: str) -> str:
    """Rövid alias a fordításhoz."""
    return translate_text(text, lang_code)

HU_LABELS = {
    "wash":           "NAGYMOSÁS",
    "house":          "HÁZTARTÁS ÉVES ÁRAMFOGYASZTÁSA",
    "red_pct":        "ÁTLAGOS CSÖKKENTÉSI POTENCIÁL",
    "all_website":    "Összesítő",
    "all_pagetype":   "Összes oldal",
    "subtitle_multi": "Mekkora a {} vizsgált webshop oldalainak CO\u2082e kibocsátása?",
    "subtitle_single":"Mekkora a(z) {} oldalainak CO\u2082e kibocsátása?",
    "pv_multi":       "{} megtekintés esetén összesen – {} webshop{}",
    "pv_single":      "{} megtekintés esetén – {}{}",
}

EN_LABELS = {
    "wash":           "LAUNDRY LOADS",
    "house":          "ANNUAL HOUSEHOLD ELECTRICITY",
    "red_pct":        "AVERAGE REDUCTION POTENTIAL",
    "all_website":    "Summary",
    "all_pagetype":   "All pages",
    "subtitle_multi": "What is the CO\u2082e footprint of the {} websites examined?",
    "subtitle_single":"What is the CO\u2082e footprint of {}?",
    "pv_multi":       "For {} page views across {} websites{}",
    "pv_single":      "For {} page views – {}{}",
}

def get_label(key: str) -> str:
    if lang_code == "hu":
        return HU_LABELS.get(key, "")
    return t(EN_LABELS.get(key, ""))

labels = {
    "kg_co2":         {"description": ""},
    "wash":           {"description": shorten_label(get_label("wash"))},
    "bp_paris":       {"description": "", "route": shorten_label(f"{city1_fit} → {city2_fit}")},
    "kg_saved":       {"description": ""},
    "kwh":            {"description": ""},
    "house":          {"description": shorten_label(get_label("house"))},
    "red_pct":        {"description": shorten_label(get_label("red_pct"))},
    "all_website":    {"description": get_label("all_website")},
    "all_pagetype":   {"description": get_label("all_pagetype")},
    "unit_db":        {"description": ""},
    "subtitle_multi": {"description": get_label("subtitle_multi")},
    "subtitle_single":{"description": get_label("subtitle_single")},
    "pv_multi":       {"description": get_label("pv_multi")},
    "pv_single":      {"description": get_label("pv_single")},
}

constants = {
    "CO2_PER_WASH":  CO2_PER_WASH,
    "CO2_PER_KM":    CO2_PER_KM,
    "CO2_PER_KWH":   CO2_PER_KWH,
    "KWH_PER_HOUSE": KWH_PER_HOUSE,
    "ref_km":        ref_km,
    "default_pv":    120_000_000,
    "lang_code":     lang_code,   # JAVÍTVA: fájlnév generáláshoz átadjuk a JS-nek
}

html = html.replace("{{RAW_STATS}}",    safe_json(raw_stats))
html = html.replace("{{LABELS}}",       safe_json(labels))
html = html.replace("{{WEBSITES}}",     safe_json(websites_list))
html = html.replace("{{PAGETYPES}}",    safe_json(pagetypes_map))
html = html.replace("{{CONSTANTS}}",    safe_json(constants))
html = html.replace("{{PANEL_STATE}}", safe_json(st.session_state.get("panel_state", None)))

components.html(html, height=660, scrolling=False)

# ── AI összefoglaló ───────────────────────────────────────────────────────────

st.divider()
st.subheader("🤖 AI Summary Generator")
st.caption(
    "Powered by **Groq** (free, no credit card needed) · "
    "Get your free API key at [console.groq.com](https://console.groq.com) → API Keys → Create API Key"
)

# API kulcs megadása – csak akkor nyitva, ha még nincs kulcs
with st.expander("🔑 API Key", expanded=(st.session_state["groq_api_key"] == "")):
    key_input = st.text_input(
        "Groq API key",
        value=st.session_state["groq_api_key"],
        type="password",
        placeholder="gsk_...",
        help="Stored only for this session – never sent anywhere except Groq's API.",
    )
    if key_input != st.session_state["groq_api_key"]:
        st.session_state["groq_api_key"] = key_input
        st.session_state["ai_summary"]   = None

# Szűrők – website és pagetype a már meglévő listákból
ai_col1, ai_col2, ai_col3 = st.columns([2, 2, 1])

with ai_col1:
    ai_website = st.selectbox(
        "Website / scope",
        websites_list,
        key="ai_website_sel",
    )

with ai_col2:
    available_pts = ["All pages"] + pagetypes_map.get(
        ai_website if ai_website != "Összesítő" else "Összesítő", []
    )
    ai_pagetype = st.selectbox(
        "Page type",
        available_pts,
        key="ai_pagetype_sel",
    )

with ai_col3:
    st.write("")
    st.write("")
    gen_btn = st.button(
        "✨ Generate",
        use_container_width=True,
        disabled=(st.session_state["groq_api_key"] == ""),
    )

if st.session_state["groq_api_key"] == "":
    st.info("👆 Add your free Groq API key above to enable AI summaries.")

if gen_btn:
    # Raw stats kulcs összerakása (egyezik a build_raw_stats logikájával)
    if ai_website == "Összesítő":
        if ai_pagetype == "All pages":
            stat_key = "Összesítő"
        else:
            stat_key = f"Összesítő|{ai_pagetype}"
    else:
        if ai_pagetype == "All pages":
            stat_key = f"{ai_website}|"
        else:
            stat_key = f"{ai_website}|{ai_pagetype}"

    raw = raw_stats.get(stat_key)
    if not raw:
        st.error(f"No data found for this selection ({stat_key}).")
    else:
        scope_lbl  = ai_website if ai_website != "Összesítő" else "All websites"
        route_lbl  = st.session_state["ref_label"]

        # Iparágak szűrése a kiválasztott scope-ra
        if ai_website == "Összesítő":
            scope_df = df
        else:
            scope_df = df[df["website"] == ai_website]
        if ai_pagetype != "All pages":
            scope_df = scope_df[scope_df["pageType"] == ai_pagetype]

        with st.spinner("🧠 AI is generating the summary…"):
            summary = generate_ai_summary(
                em_avg      = raw["em_avg"],
                red_pct     = raw["red_pct"],
                ref_km      = st.session_state["ref_km"],
                scope_label = f"{scope_lbl} – {ai_pagetype}",
                num_sites   = scope_df["website"].nunique(),
                num_rows    = len(scope_df),
                industries  = scope_df["industry"].dropna().tolist(),
                route_label = route_lbl,
                out_lang    = selected_lang_name,
                api_key     = st.session_state["groq_api_key"],
            )
        st.session_state["ai_summary"] = summary
        st.session_state["ai_summary_meta"] = {
            "website":  ai_website,
            "pagetype": ai_pagetype,
            "lang":     selected_lang_name,
        }

if st.session_state.get("ai_summary"):
    meta = st.session_state["ai_summary_meta"]
    st.success(
        f"**{meta.get('website','?')}** · {meta.get('pagetype','?')} · {meta.get('lang','?')}"
    )
    st.write(st.session_state["ai_summary"])
    st.download_button(
        label     = "⬇️ Download (.txt)",
        data      = st.session_state["ai_summary"],
        file_name = f"carbon_summary_{meta.get('website','all')}_{meta.get('pagetype','all')}.txt",
        mime      = "text/plain",
    )

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
        g1 = geocode_location(loc1, lang_code)
        g2 = geocode_location(loc2, lang_code)

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
            city1 = extract_city(g1)
            city2 = extract_city(g2)
            st.session_state["ref_km"]        = dist_km
            st.session_state["ref_label"]     = f"{city1} → {city2}"
            st.session_state["calc_dist_km"]  = dist_km
            st.session_state["calc_address1"] = g1.address
            st.session_state["calc_address2"] = g2.address
            st.session_state["calc_lat1"]     = g1.latitude
            st.session_state["calc_lon1"]     = g1.longitude
            st.session_state["calc_lat2"]     = g2.latitude
            st.session_state["calc_lon2"]     = g2.longitude
            st.session_state["calc_loc1_raw"] = loc1
            st.session_state["calc_loc2_raw"] = loc2
            st.rerun()

# ── Kalkulátor eredménye (rerun után is megmarad) ─────────────────────────────

if "calc_dist_km" in st.session_state:
    dist_km = st.session_state["calc_dist_km"]

    # Városneveket újra lekérdezzük a kiválasztott nyelven
    if "calc_loc1_raw" in st.session_state:
        g1_local = geocode_location(st.session_state["calc_loc1_raw"], lang_code)
        g2_local = geocode_location(st.session_state["calc_loc2_raw"], lang_code)
        if g1_local and g2_local:
            city1_local = extract_city(g1_local)
            city2_local = extract_city(g2_local)
            addr1 = g1_local.address
            addr2 = g2_local.address
            st.session_state["ref_label"] = f"{city1_local} → {city2_local}"
        else:
            addr1 = st.session_state.get("calc_address1", "")
            addr2 = st.session_state.get("calc_address2", "")
    else:
        addr1 = st.session_state.get("calc_address1", "")
        addr2 = st.session_state.get("calc_address2", "")

    st.success(f"Road distance: **{dist_km:,.0f} km** – this is now the reference route!")
    st.caption(f"({addr1}  →  {addr2})")

    bp_paris_equiv = dist_km / BP_PARIS_KM
    st.info(
        f"The selected route is **{bp_paris_equiv:.2f}×** the Budapest–Paris "
        f"distance ({BP_PARIS_KM} km). "
        f"The infographic values have been updated to use this as the new reference route."
    )
