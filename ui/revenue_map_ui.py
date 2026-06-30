import json
import streamlit as st
import requests


def load_revenue_map_master():
    try:
        with open("revenue-map-master.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error("revenue-map-master.json not found. Run your Revenue Map scraper first.")
        return {}
    except json.JSONDecodeError:
        st.error("revenue-map-master.json is not valid JSON.")
        return {}


def render_revenue_map_ui(api_base, master, districts, headless):
    st.markdown("### Fetch Revenue Map Document")

    revenue_master = load_revenue_map_master()

    if not revenue_master:
        return

    revenue_districts = list(revenue_master.keys())

    col1, col2 = st.columns(2)

    with col1:
        district = st.selectbox(
            "District",
            ["Select District"] + revenue_districts,
            key="Revenue_Map_district",
        )

        if district != "Select District":
            taluks = list(revenue_master[district].keys())
            taluk = st.selectbox(
                "Taluk",
                ["Select Taluk"] + taluks,
                key="Revenue_Map_taluk",
            )
        else:
            taluk = st.selectbox(
                "Taluk",
                ["Select Taluk"],
                disabled=True,
                key="Revenue_Map_taluk_disabled",
            )

    with col2:
        if district != "Select District" and taluk != "Select Taluk":
            hoblis = list(revenue_master[district][taluk].keys())
            hobli = st.selectbox(
                "Hobli",
                ["Select Hobli"] + hoblis,
                key="Revenue_Map_hobli",
            )
        else:
            hobli = st.selectbox(
                "Hobli",
                ["Select Hobli"],
                disabled=True,
                key="Revenue_Map_hobli_disabled",
            )

        if (
            district != "Select District"
            and taluk != "Select Taluk"
            and hobli != "Select Hobli"
        ):
            villages = revenue_master[district][taluk][hobli]

            if isinstance(villages, list) and villages:
                village = st.selectbox(
                    "Village",
                    ["Select Village"] + villages,
                    key="Revenue_Map_village",
                )
            else:
                village = st.text_input(
                    "Village",
                    placeholder="Enter village name exactly as in portal",
                    key="Revenue_Map_village_text",
                )
        else:
            village = st.selectbox(
                "Village",
                ["Select Village"],
                disabled=True,
                key="Revenue_Map_village_disabled",
            )

    disabled = not (
        district != "Select District"
        and taluk != "Select Taluk"
        and hobli != "Select Hobli"
        and village
        and village != "Select Village"
    )

    payload = {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "headless": headless,
    }

    if st.button(
        "🗺️ Download Revenue Map",
        disabled=disabled,
        use_container_width=True,
    ):
        try:
            with st.spinner("Fetching Revenue Map from Bhoomi..."):
                response = requests.post(
                    f"{api_base}/api/revenue-map/fetch",
                    json=payload,
                    timeout=600,
                )

            result = response.json()

            if result.get("success"):
                st.success("Revenue Map downloaded successfully.")

                if result.get("pdf"):
                    st.info(f"Saved PDF:\n\n{result.get('pdf')}")
            else:
                st.error("Revenue Map download failed.")
                st.write(result)

        except Exception as e:
            st.error(str(e))