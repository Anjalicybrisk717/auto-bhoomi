import json
import streamlit as st
import requests

from ui.mr_ui import render_mr_ui

API_BASE = "http://localhost:5000"

st.set_page_config(
    page_title="Land Query Automation",
    page_icon="📋",
    layout="wide",
)

try:
    with open("bhoomi-master.json", "r", encoding="utf-8") as f:
        master = json.load(f)
except FileNotFoundError:
    master = {}
    st.error("bhoomi-master.json file not found.")
except json.JSONDecodeError:
    master = {}
    st.error("bhoomi-master.json is not valid JSON.")

districts = list(master.keys())

st.markdown(
    """
<style>
.stApp {
    background-color: #0e1117;
    color: #ffffff;
}
section[data-testid="stSidebar"] {
    background-color: #262730;
    width: 300px !important;
}
.block-container {
    max-width: 820px;
    padding-top: 90px;
    padding-left: 70px;
}
h1 {
    font-size: 46px !important;
    font-weight: 800 !important;
    line-height: 1.15 !important;
    color: white !important;
}
h3 {
    font-size: 28px !important;
    font-weight: 800 !important;
    color: white !important;
}
label, p {
    color: white !important;
    font-weight: 600 !important;
}
.stCaption {
    color: #9ca3af !important;
}
.stTextInput input,
div[data-baseweb="select"] > div {
    background-color: #262730 !important;
    border-radius: 8px !important;
    color: white !important;
    border: none !important;
    height: 42px !important;
}
.stButton > button {
    width: 100%;
    height: 44px;
    background-color: #111827;
    color: white;
    border: 1px solid #4b5563;
    border-radius: 8px;
    font-weight: 700;
}
.stButton > button:hover {
    border-color: #ff4b4b;
    color: #ff4b4b;
}
.download-box {
    background-color: #294461;
    padding: 18px;
    border-radius: 8px;
    color: #4da3ff;
    margin-bottom: 25px;
    line-height: 1.8;
}
</style>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Configuration")

    client_id = st.text_input("Client ID", value="default_client")
    headless = st.checkbox("Headless Mode", value=True)

    st.subheader("Download Location")
    st.markdown(
        f"""
        <div class="download-box">
        📁 &nbsp; Files will be saved to:<br><br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;rtc_downloads\\{client_id}
        </div>
        """,
        unsafe_allow_html=True,
    )

st.title("📋 Land Query Automation")
st.caption("Automated land record and portal-based document fetcher")

tabs = st.tabs(["Land Query"])


def location_inputs(service_name):
    st.markdown(f"### Fetch {service_name} Document")

    col1, col2 = st.columns(2)

    with col1:
        district = st.selectbox(
            "District",
            ["Select District"] + districts,
            key=f"{service_name}_district",
        )

        if district != "Select District" and district in master:
            taluks = list(master[district].keys())
            taluk = st.selectbox(
                "Taluk",
                ["Select Taluk"] + taluks,
                key=f"{service_name}_taluk",
            )
        else:
            taluk = st.selectbox(
                "Taluk",
                ["Select Taluk"],
                disabled=True,
                key=f"{service_name}_taluk_disabled",
            )

    with col2:
        if district != "Select District" and taluk != "Select Taluk":
            hoblis = list(master[district][taluk].keys())
            hobli = st.selectbox(
                "Hobli",
                ["Select Hobli"] + hoblis,
                key=f"{service_name}_hobli",
            )
        else:
            hobli = st.selectbox(
                "Hobli",
                ["Select Hobli"],
                disabled=True,
                key=f"{service_name}_hobli_disabled",
            )

        if (
            district != "Select District"
            and taluk != "Select Taluk"
            and hobli != "Select Hobli"
        ):
            villages = master[district][taluk][hobli]
            village = st.selectbox(
                "Village",
                ["Select Village"] + villages,
                key=f"{service_name}_village",
            )
        else:
            village = st.selectbox(
                "Village",
                ["Select Village"],
                disabled=True,
                key=f"{service_name}_village_disabled",
            )

    survey_number = st.text_input(
        "Survey Number",
        placeholder="Enter Survey Number",
        disabled=not (
            district != "Select District"
            and taluk != "Select Taluk"
            and hobli != "Select Hobli"
            and village != "Select Village"
        ),
        key=f"{service_name}_survey",
    )

    hissa_no = ""

    if service_name == "RTC":
        hissa_no = st.text_input(
            "Hissa Number (Optional)",
            placeholder="Example: 1, 2, A",
            key=f"{service_name}_hissa",
        )

    disabled = not (
        district != "Select District"
        and taluk != "Select Taluk"
        and hobli != "Select Hobli"
        and village != "Select Village"
        and survey_number
    )

    payload = {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "surveyNumber": survey_number,
        "hissa": hissa_no,
        "headless": headless,
    }

    return payload, disabled


with tabs[0]:
    st.markdown("### Land Query")

    portal = st.selectbox(
        "Select Portal / Service",
        [
            "Select Portal",
            "Bhoomi",
            "Kaveri",
            "CERSAI Portal",
            "BBMP",
            "BDA",
            "BIAAPA",
            "BESCOM",
            "BWSSB",
            "Karnataka RERA",
            "Private Facilitation Services",
        ],
    )

    if portal == "Bhoomi":
        bhoomi_service = st.selectbox(
            "Select Bhoomi Service",
            [
                "Select Service",
                "RTC",
                "MR",
                "Mutation Status",
                "Khata Extract",
                "Survey Document",
                "RTC With Sketch",
                "Survey Sketch",
                "Record Room Document",
                "Old Year RTC",
            ],
        )

        if bhoomi_service == "RTC":
            payload, disabled = location_inputs("RTC")

            if st.button("📥 Download RTC", disabled=disabled):
                try:
                    with st.spinner("Opening Bhoomi Portal for RTC..."):
                        response = requests.post(
                            f"{API_BASE}/api/fetch-rtc/auto",
                            json=payload,
                            timeout=600,
                        )

                    result = response.json()

                    if result.get("success"):
                        st.success("RTC downloaded successfully")
                        if result.get("pdf"):
                            st.write("PDF saved at:", result.get("pdf"))
                    else:
                        st.error("RTC download failed")
                        st.write(result)

                except Exception as e:
                    st.error(str(e))

        elif bhoomi_service == "MR":
            render_mr_ui(
                API_BASE,
                master,
                districts,
                headless,
            )

        elif bhoomi_service != "Select Service":
            st.info(f"{bhoomi_service} automation will be added next.")

    elif portal != "Select Portal":
        st.info(f"{portal} automation will be added next.")