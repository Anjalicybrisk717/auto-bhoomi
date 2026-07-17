import json
import os
import streamlit as st
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AKARBAND_MASTER_PATH = os.path.join(BASE_DIR, "akarband-master.json")


def load_akarband_master():
    try:
        with open(AKARBAND_MASTER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"akarband-master.json not found at: {AKARBAND_MASTER_PATH}")
        return {}
    except json.JSONDecodeError:
        st.error(f"akarband-master.json is invalid JSON: {AKARBAND_MASTER_PATH}")
        return {}
    except Exception as error:
        st.error(f"Unable to load akarband-master.json: {error}")
        return {}


def get_dropdown_options(node):
    if isinstance(node, dict):
        return list(node.keys())
    if isinstance(node, list):
        return node
    return []


def rerun_app():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def reset_akarband_portal_options():
    for key in (
        "Akarband_portal_options",
        "Akarband_portal_criteria",
        "Akarband_surnoc",
        "Akarband_hissa",
    ):
        st.session_state.pop(key, None)


def reset_akarband_survey():
    st.session_state.pop("Akarband_survey", None)
    reset_akarband_portal_options()


def reset_akarband_taluk():
    st.session_state.pop("Akarband_taluk", None)
    st.session_state.pop("Akarband_hobli", None)
    st.session_state.pop("Akarband_village", None)
    reset_akarband_survey()


def reset_akarband_hobli():
    st.session_state.pop("Akarband_hobli", None)
    st.session_state.pop("Akarband_village", None)
    reset_akarband_survey()


def reset_akarband_village():
    st.session_state.pop("Akarband_village", None)
    reset_akarband_survey()


def option_labels(options):
    return [option["label"] for option in options if option.get("label")]


def fetch_akarband_options(api_base, payload):
    response = requests.post(
        f"{api_base}/api/akarband/options",
        json=payload,
        timeout=240,
    )
    response.raise_for_status()
    result = response.json()

    if not result.get("success"):
        raise Exception(result)

    return result


def render_akarband_ui(api_base, master, districts, headless):
    akarband_master = load_akarband_master()

    if not akarband_master:
        return

    st.markdown(
        """
        <div style="text-align:center;">
            <h3 style="color:#0037ff;">ಕರ್ನಾಟಕ ಸರ್ಕಾರ</h3>
            <h4 style="color:#0037ff;">
                ಭೂಮಾಪನ, ಕಂದಾಯ ವ್ಯವಸ್ಥೆ ಮತ್ತು ಭೂ ದಾಖಲೆಗಳ ಇಲಾಖೆ
            </h4>
            <div style="
                background:#0500d8;
                color:white;
                padding:10px;
                font-weight:bold;
                border-radius:4px;
            ">
                ಆಕಾರಬಂದ್
            </div>
        </div>
        <br>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        district = st.selectbox(
            "ಜಿಲ್ಲೆ:",
            ["--ಆಯ್ಕೆ--"] + list(akarband_master.keys()),
            key="Akarband_district",
            on_change=reset_akarband_taluk,
        )

    with col2:
        taluks = (
            list(akarband_master[district].keys()) if district != "--ಆಯ್ಕೆ--" else []
        )

        taluk = st.selectbox(
            "ತಾಲೂಕು:",
            ["--ಆಯ್ಕೆ--"] + taluks,
            disabled=district == "--ಆಯ್ಕೆ--",
            key="Akarband_taluk",
            on_change=reset_akarband_hobli,
        )

    with col3:
        hoblis = (
            list(akarband_master[district][taluk].keys())
            if district != "--ಆಯ್ಕೆ--" and taluk != "--ಆಯ್ಕೆ--"
            else []
        )

        hobli = st.selectbox(
            "ಹೋಬಳಿ:",
            ["--ಆಯ್ಕೆ--"] + hoblis,
            disabled=taluk == "--ಆಯ್ಕೆ--",
            key="Akarband_hobli",
            on_change=reset_akarband_village,
        )

    col4, col5, col6 = st.columns(3)

    with col4:
        if district != "--ಆಯ್ಕೆ--" and taluk != "--ಆಯ್ಕೆ--" and hobli != "--ಆಯ್ಕೆ--":
            villages = get_dropdown_options(akarband_master[district][taluk][hobli])
        else:
            villages = []

        village = st.selectbox(
            "ಗ್ರಾಮ:",
            ["--ಆಯ್ಕೆ--"] + villages,
            disabled=hobli == "--ಆಯ್ಕೆ--",
            key="Akarband_village",
            on_change=reset_akarband_survey,
        )

    location_ready = (
        district != "--ಆಯ್ಕೆ--"
        and taluk != "--ಆಯ್ಕೆ--"
        and hobli != "--ಆಯ್ಕೆ--"
        and village != "--ಆಯ್ಕೆ--"
    )

    location_criteria = {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
    }

    if st.session_state.get("Akarband_location_criteria") != location_criteria:
        st.session_state["Akarband_location_criteria"] = location_criteria
        reset_akarband_portal_options()

    with col5:
        survey_number = st.text_input(
            "ಸರ್ವೆ ಸಂಖ್ಯೆ:",
            disabled=not location_ready,
            key="Akarband_survey",
        ).strip()

    if False:
        if surnoc_options:
            surnoc = st.selectbox(
                "ಸನೋರ್ಕ್:",
                surnoc_options,
                key="Akarband_surnoc",
            )
        else:
            surnoc = ""
            st.selectbox(
                "ಸನೋರ್ಕ್:",
                ["--ಆಯ್ಕೆ--"],
                disabled=True,
                key="Akarband_surnoc_disabled",
            )

    with col6:
        surnoc = st.text_input(
            "Surnoc:",
            disabled=not (location_ready and survey_number),
            key="Akarband_surnoc",
        ).strip()

    if False:
        surnoc_criteria = {
            "location": location_criteria,
            "surveyNumber": survey_number,
            "surnoc": surnoc,
        }

        if st.session_state.get("Akarband_portal_criteria") != surnoc_criteria:
            try:
                with st.spinner("Loading Hissa from Akarband portal..."):
                    st.session_state.pop("Akarband_hissa", None)
                    st.session_state["Akarband_portal_options"] = (
                        fetch_akarband_options(
                            api_base,
                            {
                                **location_criteria,
                                "surveyNumber": survey_number,
                                "surnoc": surnoc,
                                "hissa": "",
                                "headless": headless,
                            },
                        )
                    )
                    st.session_state["Akarband_portal_criteria"] = surnoc_criteria
                rerun_app()
            except Exception as error:
                st.error(f"Unable to load Hissa options: {error}")

        options_result = st.session_state.get("Akarband_portal_options") or {}
        hissa_options = option_labels(options_result.get("hissas", []))

    col7, col8, col9 = st.columns(3)

    if False:
        if hissa_options:
            hissa = st.selectbox(
                "ಹಿಸ್ಸಾ:",
                hissa_options,
                key="Akarband_hissa",
            )
        else:
            hissa = ""
            st.selectbox(
                "ಹಿಸ್ಸಾ:",
                ["--ಆಯ್ಕೆ--"],
                disabled=True,
                key="Akarband_hissa_disabled",
            )

    with col7:
        hissa = st.text_input(
            "Hissa:",
            disabled=not (location_ready and survey_number and surnoc),
            key="Akarband_hissa",
        ).strip()

    with col8:
        st.write("")

    with col9:
        st.write("")
        st.write("")

        disabled = not (location_ready and survey_number and surnoc and hissa)

        clicked = st.button(
            "ಆಕಾರಬಂದ್ ಪಡೆಯಿರಿ",
            disabled=disabled,
            width="stretch",
        )

    if clicked:
        payload = {
            "district": district,
            "taluk": taluk,
            "hobli": hobli,
            "village": village,
            "surveyNumber": survey_number,
            "surnoc": surnoc,
            "hissa": hissa,
            "headless": headless,
        }

        try:
            with st.spinner("ಆಕಾರಬಂದ್ ಪಡೆಯಲಾಗುತ್ತಿದೆ..."):
                response = requests.post(
                    f"{api_base}/api/akarband/fetch",
                    json=payload,
                    timeout=600,
                )

            result = response.json()

            if result.get("success"):
                st.success("Akarband downloaded successfully.")
                st.info(f"Saved PDF:\n\n{result.get('pdf')}")
            else:
                st.error("Akarband download failed.")
                st.write(result)

        except Exception as e:
            st.error(str(e))
