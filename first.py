import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
import json

st.set_page_config(page_title="Carbon Crane", page_icon="🌿", layout="wide")

# ── Statikus konstansok ───────────────────────────────────────────────────────

CO2_PER_WASH  = 0.236615995  # 1 mosás CO2e-je [kg]
CO2_PER_KM    = 0.215118375  # 1 km autózás CO2e-je [kg]
CO2_PER_KWH   = 0.236150771  # 1 kWh áram CO2-je [kg]
KWH_PER_HOUSE = 2500         # 1 háztartás éves energiafogyasztása [kWh]
TOTAL_PV      = 120_000_000  # összes page visit (fixált)
BP_PARIS_KM   = 1485         # Budapest → Párizs távolság [km]

COL_EM_ALL   = "BE - Carbon Emission - all subpages "
COL_EM_PAGE  = "BE - Carbon Emission - page"
COL_RED_PAGE = "BE - Reduced Carbon Emission"
COL_RED      = COL_RED_PAGE  # alias
COL_RED_ALL  = "BE - Reduced Carbon Emission - all subpages"

REQUIRED_COLUMNS = [
    "industry", "website", "pageType", "have all subpages", "url",
    COL_EM_PAGE, COL_EM_ALL,
    "BE - Reduction % - page", "Reduction % - image",
    COL_RED, "BE - Reduced Carbon Emission - all subpages",
    "BE - Reduction % - all subpages", "Rank Reduction % - page",
    "Rank Reduced Carbon Emission", "Rank Reduction % - all subpages",
    "Rank Reduced Carbon Emission -  all subpages",
]
# ── Számítások ────────────────────────────────────────────────────────────────

def calc_stats(rows: pd.DataFrame, col_em: str, col_red: str) -> dict:
    """Emission statisztikák + csökkentési potenciál egy adott oszloppárra."""
    em_avg   = rows[col_em].mean()
    kg_co2   = em_avg * TOTAL_PV / 1000
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
        "bp_paris_trips": kg_co2 / CO2_PER_KM / BP_PARIS_KM,
        "red_pct":        red_pct,
        "kg_saved":       kg_saved,
        "kwh":            kwh,
        "house":          kwh / KWH_PER_HOUSE,
    }


def calc_all(df: pd.DataFrame) -> dict:
    """Összesítő + oldaltípusonkénti max/avg/min számítások."""
    return {
        "summary":     calc_stats(df, COL_EM_ALL, COL_RED_ALL),
        "by_pagetype": {pt: calc_stats(g, COL_EM_PAGE, COL_RED_PAGE) for pt, g in df.groupby("pageType")},
    }

# ── Infografika generálás ─────────────────────────────────────────────────────

def generate_infographic(stats: dict, template_path: str = "Carbon.Crane_infografika_template.png", layout_path: str = "layout.json") -> Image.Image:
    with open(layout_path) as f:
        layout = json.load(f)

    img  = Image.open(template_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    fields = {
        "em_max":         f"{stats['em_max']:.2f}",
        "em_avg":         f"{stats['em_avg']:.2f}",
        "em_min":         f"{stats['em_min']:.2f}",
        "kg_co2":         f"{stats['kg_co2']:,.0f}",
        "wash":           f"{stats['wash']:,.0f}",
        "bp_paris_trips": f"{stats['bp_paris_trips']:,.0f}",
        "red_pct":        f"{stats['red_pct']*100:.1f}%",
        "kg_saved":       f"{stats['kg_saved']:,.0f}",
        "kwh":            f"{stats['kwh']:,.0f}",
        "house":          f"{stats['house']:,.0f}",
    }

    font = ImageFont.load_default(size=36)

    for key, text in fields.items():
        box = layout[key]
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        th   = bbox[3] - bbox[1]
        tx   = x + (w - tw) // 2
        ty   = y + (h - th) // 2
        draw.text((tx, ty), text, font=font, fill="white")

    return img

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

data = calc_all(df)

st.success(f"{len(df)} sor betöltve – {df['website'].nunique()} weboldal")

# ── Számítások megjelenítése ──────────────────────────────────────────────────

st.divider()

page_options = ["Összesítő"] + sorted(data["by_pagetype"].keys())
sel_oldal = st.radio("Oldal", page_options, horizontal=True)

if sel_oldal == "Összesítő":
    st.json(data["summary"])
else:
    st.json(data["by_pagetype"][sel_oldal])

# ── Infografika ───────────────────────────────────────────────────────────────

st.divider()

if st.button("Infografika generálása"):
    if sel_oldal == "Összesítő":
        stats = data["summary"]
    else:
        stats = data["by_pagetype"][sel_oldal]
    img = generate_infographic(stats)
    st.image(img)

# ── Részletes nézet ───────────────────────────────────────────────────────────

reszletes = st.toggle("Részletes nézet")

if reszletes:
    st.divider()

    scope = st.radio("Megjelenítés", ["Összes weboldal", "Egy weboldal"], horizontal=True)

    col1, col2 = st.columns(2)

    if scope == "Összes weboldal":
        with col1:
            sel_ipar = st.multiselect("Iparág", sorted(df["industry"].unique()), placeholder="Mind")
        with col2:
            sel_oldaltipus = st.multiselect("Oldaltípus", sorted(df["pageType"].unique()), placeholder="Mind")
        filtered = df.copy()
        if sel_ipar:
            filtered = filtered[filtered["industry"].isin(sel_ipar)]
        if sel_oldaltipus:
            filtered = filtered[filtered["pageType"].isin(sel_oldaltipus)]
    else:
        with col1:
            sel_ceg = st.selectbox("Válassz weboldalt", sorted(df["website"].unique()))
        filtered = df[df["website"] == sel_ceg].copy()

    st.dataframe(filtered.reset_index(drop=True), width='stretch')
