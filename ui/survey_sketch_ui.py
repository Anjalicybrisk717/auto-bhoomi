import streamlit as st
import requests


def render_survey_sketch_ui(api_base, master, districts, headless):
    st.markdown("### Fetch Survey Sketch Document")

    col1, col2 = st.columns(2)

    with col1:
        district = st.selectbox(
            "District",
            ["Select District"] + districts,
            key="Survey_Sketch_district",
        )

        if district != "Select District":
            taluks = list(master[district].keys())
        else:
            taluks = []

        taluk = st.selectbox(
            "Taluk",
            ["Select Taluk"] + taluks,
            disabled=district == "Select District",
            key="Survey_Sketch_taluk",
        )

    with col2:
        if district != "Select District" and taluk != "Select Taluk":
            hoblis = list(master[district][taluk].keys())
        else:
            hoblis = []

        hobli = st.selectbox(
            "Hobli",
            ["Select Hobli"] + hoblis,
            disabled=taluk == "Select Taluk",
            key="Survey_Sketch_hobli",
        )

        if (
            district != "Select District"
            and taluk != "Select Taluk"
            and hobli != "Select Hobli"
        ):
            villages = master[district][taluk][hobli]
        else:
            villages = []

        village = st.selectbox(
            "Village",
            ["Select Village"] + villages,
            disabled=hobli == "Select Hobli",
            key="Survey_Sketch_village",
        )

    col3, col4, col5 = st.columns(3)

    with col3:
        survey_number = st.text_input("Survey No", key="Survey_Sketch_survey")

    with col4:
        surnoc = st.text_input("Surnoc", key="Survey_Sketch_surnoc")

    with col5:
        hissa = st.text_input("Hissa", key="Survey_Sketch_hissa")

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
        "surnoc": surnoc,
        "hissa": hissa,
        "headless": headless,
    }

    if st.button(
        "📐 Download Survey Sketch", disabled=disabled, width="stretch"
    ):
        try:
            with st.spinner("Fetching Survey Sketch..."):
                response = requests.post(
                    f"{api_base}/api/survey-sketch/fetch",
                    json=payload,
                    timeout=600,
                )

            result = response.json()

            if not result:
                st.error("Server returned empty response.")
                st.stop()

            if result.get("success"):
                st.success("Survey Sketch Map loaded successfully.")

                if result.get("image"):
                    st.image(result["image"], caption="Survey Sketch Map")

                if result.get("pdf"):
                    st.info(f"Large printable PDF saved at:\n\n{result['pdf']}")

                if result.get("html"):
                    st.info(f"Map HTML saved at:\n\n{result['html']}")
            else:
                st.error("Survey Sketch failed.")
                st.write(result)

        except Exception as e:
            st.error(str(e))
