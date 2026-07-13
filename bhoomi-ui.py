import json
import streamlit as st
from ui.survey_sketch_ui import render_survey_sketch_ui
from ui.rtc_ui import render_rtc_ui
from ui.mr_ui import render_mr_ui
from ui.revenue_map_ui import render_revenue_map_ui
from ui.akarband_ui import render_akarband_ui
from ui.rera_ui import render_rera_ui


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
    max-width: 900px;
    padding-top: 70px;
    padding-left: 70px;
}

h1 {
    font-size: 44px !important;
    font-weight: 800 !important;
    line-height: 1.15 !important;
    color: white !important;
}

h3 {
    font-size: 26px !important;
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
    headless = st.checkbox(
        "Headless Mode",
        value=True,
    )

    st.subheader("Download Location")
    st.markdown(
        f"""
        <div class="download-box">
        📁 &nbsp; Files will be saved locally under:<br><br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;rtc_downloads<br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;mr_downloads<br>
        &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;revenue_maps
        </div>
        """,
        unsafe_allow_html=True,
    )


st.title("📋 Land Query Automation")
st.caption("Automated land record and portal-based document fetcher")


tabs = st.tabs(["Land Query"])


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
                "Revenue Map",
                "Mutation Status",
                "Akarband",
                "Khata Extract",
                "Survey Document",
                "RTC With Sketch",
                "Survey Sketch",
                "Record Room Document",
                "Old Year RTC",
            ],
        )

        if bhoomi_service == "RTC":
            render_rtc_ui(
                API_BASE,
                master,
                districts,
                headless,
            )

        elif bhoomi_service == "MR":
            render_mr_ui(
                API_BASE,
                master,
                districts,
                headless,
            )

        elif bhoomi_service == "Revenue Map":
            render_revenue_map_ui(
                API_BASE,
                master,
                districts,
                headless,
            )

        elif bhoomi_service == "Survey Sketch":
            render_survey_sketch_ui(
                API_BASE,
                master,
                districts,
                headless,
          )
        elif bhoomi_service == "Akarband":
            render_akarband_ui(
                API_BASE,
                master,
                districts,
                headless,
        )

        elif bhoomi_service != "Select Service":
            st.info(f"{bhoomi_service} automation will be added next.")

    elif portal == "Karnataka RERA":
        render_rera_ui(
            API_BASE,
            master,
            districts,
            headless,
        )

    elif portal != "Select Portal":
        st.info(f"{portal} automation will be added next.")
