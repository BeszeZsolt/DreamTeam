import pandas as pd
import streamlit as st

st.set_page_config(page_title="Carbon Crane", page_icon="🌿", layout="wide")

REQUIRED_COLUMNS = [
    "industry",
    "website",
    "pageType",
    "have all subpages",
    "url",
    "BE - Carbon Emission - page",
    "BE_2_Manual",
    "BE - Carbon Emission - all subpages ",
    "BE - Reduction % - page",
    "Reduction % - image",
    "BE - Reduced Carbon Emission",
    "BE - Reduced Carbon Emission - all subpages",
    "BE - Reduction % - all subpages",
    "Rank Reduction % - page",
    "Rank Reduced Carbon Emission",
    "Rank Reduction % - all subpages",
    "Rank Reduced Carbon Emission -  all subpages",
]

st.title("Carbon Crane infografika készítő")

uploaded = st.file_uploader("Excel fájl feltöltése (.xlsx)", type=["xlsx"])

if not uploaded:
    st.stop()

try:
    df = pd.read_excel(uploaded, sheet_name=0, header=0)
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

sel_ceg = st.selectbox("Válassz weboldalt", sorted(df["website"].unique()))

reszletes = st.toggle("Részletes nézet")

if reszletes:
    st.divider()

    scope = st.radio("Megjelenítés", ["Csak a kiválasztott weboldal", "Összes weboldal"], horizontal=True)

    if scope == "Összes weboldal":
        col_ipar, col_oldal = st.columns(2)
        with col_ipar:
            sel_ipar = st.multiselect("Iparág", sorted(df["industry"].unique()), placeholder="Mind")
        with col_oldal:
            sel_oldal = st.multiselect("Oldaltípus", sorted(df["pageType"].unique()), placeholder="Mind")
        filtered = df.copy()
        if sel_ipar:
            filtered = filtered[filtered["industry"].isin(sel_ipar)]
        if sel_oldal:
            filtered = filtered[filtered["pageType"].isin(sel_oldal)]
    else:
        filtered = df[df["website"] == sel_ceg].copy()

    st.dataframe(filtered.reset_index(drop=True), width='stretch')