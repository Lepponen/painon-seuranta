import os
from datetime import date
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

# Optional: Supabase for cloud storage
try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

# ----------------------
# Page setup & styling
# ----------------------
st.set_page_config(page_title="Painon seuranta", page_icon="⚖️", layout="centered")

HIDE_STREAMLIT_STYLE = """
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        .block-container {padding-top: 1rem; padding-bottom: 2.5rem;}
        .stButton>button {width: 100%; height: 3rem; font-size: 1.05rem;}
        .stNumberInput input, .stTextInput input, .stDateInput input {font-size: 1.05rem; height: 2.6rem;}
    </style>
"""
st.markdown(HIDE_STREAMLIT_STYLE, unsafe_allow_html=True)

# ----------------------
# Helpers
# ----------------------
@st.cache_resource(show_spinner=False)
def get_supabase() -> Optional[Client]:
    try:
        url = st.secrets.get("supabase_url", None)
        key = st.secrets.get("supabase_key", None)
    except Exception:
        return None
    if not (url and key) or create_client is None:
        return None
    return create_client(url, key)

@st.cache_data(show_spinner=False)
def fetch_weights(user_id: str) -> pd.DataFrame:
    sb = get_supabase()
    if sb is None:
        path = f"weights_{user_id}.csv"
        if os.path.exists(path):
            df = pd.read_csv(path)
        else:
            df = pd.DataFrame(columns=["entry_date", "weight_kg", "note"])
        return df.sort_values("entry_date")

    resp = sb.table("weights").select("entry_date, weight_kg, note").eq("user_id", user_id).order("entry_date", desc=False).execute()
    data = resp.data or []
    df = pd.DataFrame(data)
    return df


def save_weight(user_id: str, d: date, w: float, note: str = "") -> None:
    sb = get_supabase()
    if sb is None:
        path = f"weights_{user_id}.csv"
        fetch_weights.clear()
        df = fetch_weights(user_id)
        new_row = {"entry_date": d.isoformat(), "weight_kg": w, "note": note}
        if df is None or df.empty:
            df = pd.DataFrame([new_row])
        else:
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(path, index=False)
        fetch_weights.clear()
        return

    sb.table("weights").insert({
        "user_id": user_id,
        "entry_date": d.isoformat(),
        "weight_kg": w,
        "note": note,
    }).execute()
    fetch_weights.clear()


def delete_weight(user_id: str, d: date, w: float, note: str = "") -> None:
    sb = get_supabase()
    if sb is None:
        path = f"weights_{user_id}.csv"
        if os.path.exists(path):
            df = pd.read_csv(path)
            df["entry_date_norm"] = pd.to_datetime(df["entry_date"], errors="coerce").dt.date
            df["weight_kg_norm"] = pd.to_numeric(df["weight_kg"], errors="coerce")
            df["note_norm"] = df.get("note", "").astype(str)

            match = (
                (df["entry_date_norm"] == pd.to_datetime(d).date()) &
                (df["weight_kg_norm"].sub(float(w)).abs() < 1e-6) &
                (df["note_norm"] == (note or ""))
            )
            if match.any():
                drop_idx = df[match].index[0]
                df = df.drop(index=drop_idx)
                df = df.drop(columns=["entry_date_norm", "weight_kg_norm", "note_norm"], errors="ignore")
                df.to_csv(path, index=False)
        fetch_weights.clear()
        return

    sb.table("weights").delete().match({
        "user_id": user_id,
        "entry_date": d.isoformat(),
        "weight_kg": w,
        "note": note,
    }).execute()
    fetch_weights.clear()



def compute_metrics(df: pd.DataFrame, height_cm: Optional[float], target_kg: Optional[float]) -> Tuple[dict, pd.DataFrame]:
    metrics = {}
    if df.empty:
        return metrics, df
    df_sorted = df.sort_values("entry_date")
    metrics["latest"] = float(df_sorted["weight_kg"].iloc[-1])
    metrics["start"] = float(df_sorted["weight_kg"].iloc[0])
    metrics["change"] = metrics["latest"] - metrics["start"]

    if height_cm:
        h_m = height_cm / 100.0
        metrics["bmi"] = round(metrics["latest"] / (h_m*h_m), 1)

    if target_kg:
        metrics["to_goal"] = round(metrics["latest"] - target_kg, 1)

    dfe = df_sorted.copy()
    dfe["entry_date"] = pd.to_datetime(dfe["entry_date"], errors="coerce")
    dfe = dfe.dropna(subset=["entry_date"])
    dfe = dfe.groupby("entry_date", as_index=False)[["weight_kg"]].mean()
    dfe = dfe.set_index("entry_date").asfreq("D").interpolate()
    dfe = dfe.reset_index()

    metrics["avg7"] = float(dfe["weight_kg"].rolling(7, min_periods=1).mean().iloc[-1])
    return metrics, dfe

# ----------------------
# UI
# ----------------------
st.title("⚖️ Painon seuranta")

with st.sidebar:
    st.subheader("Asetukset")
    user_id = st.text_input("Käyttäjätunnus (esim. oma nimi tai tunnus)", value=st.session_state.get("user_id", "minä"))
    st.session_state["user_id"] = user_id
    height_cm = st.number_input("Pituus (cm)", min_value=100, max_value=250, value=int(st.session_state.get("height_cm", 180)))
    st.session_state["height_cm"] = height_cm
    target_kg = st.number_input("Tavoitepaino (kg)", min_value=30.0, max_value=300.0, value=float(st.session_state.get("target_kg", 80.0)), step=0.1)
    st.session_state["target_kg"] = target_kg

    st.markdown("---")
    st.caption("Tallennus: Supabase, jos salaisuudet asetettu. Muuten paikallinen CSV (vain tämän instanssin sisällä).")

col1, col2 = st.columns(2)
with col1:
    entry_date = st.date_input("Päivämäärä", value=date.today())
with col2:
    weight = st.number_input("Paino (kg)", min_value=30.0, max_value=300.0, step=0.1, format="%.1f")
note = st.text_input("Huomio (valinnainen)")

if st.button("➕ Lisää merkintä"):
    if not user_id.strip():
        st.error("Aseta käyttäjätunnus sivupalkista.")
    elif weight <= 0:
        st.error("Syötä paino.")
    else:
        save_weight(user_id, entry_date, float(weight), note)
        st.success("Tallennettu!")
        st.rerun()

# Load data
_df = fetch_weights(user_id)

# Summary metrics & chart
if _df.empty:
    st.info("Ei vielä merkintöjä. Lisää ensimmäinen yllä.")
else:
    metrics, dfe = compute_metrics(_df, height_cm, target_kg)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Uusin (kg)", f"{metrics['latest']:.1f}")
    m2.metric("Muutos (kg)", f"{metrics['change']:+.1f}")
    if "bmi" in metrics:
        m3.metric("BMI", f"{metrics['bmi']:.1f}")
    if "to_goal" in metrics:
        m4.metric("Tavoitteeseen (kg)", f"{metrics['to_goal']:+.1f}")
    if "avg7" in metrics:
        m5.metric("7 pv ka (kg)", f"{metrics['avg7']:.1f}")

    st.subheader("Kehitys")
    chart_df = dfe[["entry_date", "weight_kg"]].rename(columns={
        "entry_date": "Päivä",
        "weight_kg": "Paino",
    })
    st.line_chart(chart_df.set_index("Päivä"))

    # Recent entries with delete buttons
    st.subheader("Viimeisimmät merkinnät")
    recent = _df.sort_values("entry_date", ascending=False).head(20)
    for i, r in recent.iterrows():
        cols = st.columns([1, 1, 5, 1])
        cols[0].write(r["entry_date"])
        cols[1].write(f"{r['weight_kg']:.1f} kg")
        cols[2].write(r.get("note", ""))
        if cols[3].button("Poista", key=f"del-{i}"):
            delete_weight(user_id, pd.to_datetime(r["entry_date"]).date(), float(r["weight_kg"]), r.get("note", ""))
            st.rerun()

    # Export / Import
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        csv = _df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Lataa CSV", csv, file_name=f"painot_{user_id}.csv", mime="text/csv")
    with c2:
        up = st.file_uploader("Tuo CSV (entry_date, weight_kg, note)", type=["csv"])
        if up is not None:
            try:
                imp = pd.read_csv(up)
                imp["entry_date"] = pd.to_datetime(imp["entry_date"], errors="coerce").dt.date
                imp = imp.dropna(subset=["entry_date"])
                for _, row in imp.iterrows():
                    save_weight(user_id, row["entry_date"], float(row["weight_kg"]), str(row.get("note", "")))
                st.success("Tuonti valmis!")
                st.rerun()
            except Exception as e:
                st.error(f"Tuonti epäonnistui: {e}")

# ----------------------
# Onboarding tips
# ----------------------
with st.expander("📱 Vinkit iPhonelle"):
    st.markdown(
        """
        **Lisää Koti-valikkoon**: Avaa Safari → jaa-kuvake → *Add to Home Screen*. Sovellus avautuu kuin natiivina täysruudussa.

        **Vinkkejä**
        - Jos et käytä Supabasea, tiedot tallentuvat tämän palvelimen CSV:hen; ota varmuuskopio **Lataa CSV**-napilla.
        - Suosittelemme pilvitallennusta (Supabase) pysyvyyden ja monilaitteisen käytön vuoksi.
        """
    )

# ----------------------
# Footer
# ----------------------
st.caption("© 2025 Painon seuranta – Streamlit")
