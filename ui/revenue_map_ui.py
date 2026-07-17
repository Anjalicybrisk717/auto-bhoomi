import json
import streamlit as st
import requests


def load_revenue_master():
    try:
        with open("revenue-map-master.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error("revenue-map-master.json not found. Run your Revenue Map scraper first.")
        return {}
    except json.JSONDecodeError:
        st.error("revenue-map-master.json is not valid JSON.")
        return {}


def get_hobli_node(revenue_master, district, taluk, hobli):
    try:
        node = revenue_master[district][taluk][hobli]
        if isinstance(node, dict):
            return node
        if isinstance(node, list):
            return {
                "_villages": node,
                "_map_types": ["Cadastral Maps", "Geo-referenced Cadastral Maps"],
            }
        return {
            "_villages": [],
            "_map_types": ["Cadastral Maps", "Geo-referenced Cadastral Maps"],
        }
    except Exception:
        return {
            "_villages": [],
            "_map_types": ["Cadastral Maps", "Geo-referenced Cadastral Maps"],
        }


def render_revenue_map_ui(api_base, master, districts, headless):
    st.markdown("### Fetch Revenue Map Document")

    revenue_master = load_revenue_master()
    if not revenue_master:
        return

    col1, col2 = st.columns(2)

    with col1:
        district = st.selectbox(
            "District",
            ["Select District"] + list(revenue_master.keys()),
            key="Revenue_Map_district",
        )

        if district != "Select District":
            taluks = list(revenue_master[district].keys())
        else:
            taluks = []

        taluk = st.selectbox(
            "Taluk",
            ["Select Taluk"] + taluks,
            disabled=district == "Select District",
            key="Revenue_Map_taluk",
        )

    with col2:
        if district != "Select District" and taluk != "Select Taluk":
            hoblis = list(revenue_master[district][taluk].keys())
        else:
            hoblis = []

        hobli = st.selectbox(
            "Hobli",
            ["Select Hobli"] + hoblis,
            disabled=taluk == "Select Taluk",
            key="Revenue_Map_hobli",
        )

        if (
            district != "Select District"
            and taluk != "Select Taluk"
            and hobli != "Select Hobli"
        ):
            node = get_hobli_node(
                revenue_master,
                district,
                taluk,
                hobli,
            )

            villages = node.get("_villages", [])
            map_types = node.get(
                "_map_types",
                ["Cadastral Maps", "Geo-referenced Cadastral Maps"],
            )
        else:
            villages = []
            map_types = ["Cadastral Maps", "Geo-referenced Cadastral Maps"]

        village = st.selectbox(
            "Village",
            ["Select Village"] + villages,
            disabled=hobli == "Select Hobli",
            key="Revenue_Map_village",
        )

    map_type = st.selectbox(
        "Map Type",
        ["All"] + map_types,
        disabled=hobli == "Select Hobli",
        key="Revenue_Map_map_type",
    )

    disabled = not (
        district != "Select District"
        and taluk != "Select Taluk"
        and hobli != "Select Hobli"
        and village != "Select Village"
        and map_type
    )

    payload = {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "mapType": map_type,
        "headless": headless,
    }

    if st.button(
        "🗺️ Download Revenue Map",
        disabled=disabled,
        width="stretch",
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
