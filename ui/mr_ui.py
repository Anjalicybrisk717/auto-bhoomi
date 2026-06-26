import streamlit as st
import requests
import pandas as pd


def location_inputs(service_name, master, districts, headless):
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
        "headless": headless,
    }

    return payload, disabled


def prepare_mr_payload(payload):
    mr_payload = payload.copy()

    if (
        mr_payload.get("district") == "BENGALURU"
        and mr_payload.get("taluk") == "YALAHANKA"
    ):
        mr_payload["taluk"] = "Bangalore North(Additional)"

    return mr_payload


def render_mr_ui(api_base, master, districts, headless):
    payload, disabled = location_inputs(
        "MR",
        master,
        districts,
        headless,
    )

    if st.button("🔍 Fetch Mutation Details", disabled=disabled):
        try:
            mr_payload = prepare_mr_payload(payload)

            with st.spinner("Fetching mutation records from Bhoomi..."):
                response = requests.post(
                    f"{api_base}/api/mr/search",
                    json=mr_payload,
                    timeout=600,
                )

            result = response.json()

            if result.get("success"):
                rows = result.get("rows", [])

                if rows:
                    st.session_state["mr_rows"] = rows
                    st.session_state["mr_payload"] = mr_payload
                    st.success("Mutation records fetched successfully")
                else:
                    st.warning("No mutation records found")
            else:
                st.error("MR fetch failed")
                st.write(result)

        except Exception as e:
            st.error(str(e))

    if "mr_rows" in st.session_state and "mr_payload" in st.session_state:
        rows = st.session_state["mr_rows"]

        if rows:

            selected_index = st.selectbox(
                "Select Mutation Entry",
                options=list(range(len(rows))),
                format_func=lambda i: (
                    f"MR No: {rows[i].get('mr_number', '')} | "
                    f"Survey: {rows[i].get('survey_no', '')} | "
                    f"Year: {rows[i].get('transaction_year', '')} | "
                    f"Date: {rows[i].get('approved_date', '')}"
                ),
            )

            if st.button("📥 Download Selected MR"):
                try:
                    selected_payload = st.session_state["mr_payload"].copy()
                    selected_payload["rowIndex"] = selected_index

                    with st.spinner("Downloading selected MR as PDF..."):
                        response = requests.post(
                            f"{api_base}/api/mr/download-selected",
                            json=selected_payload,
                            timeout=600,
                        )

                    result = response.json()

                    if result.get("success"):
                        st.success("Selected MR downloaded successfully")

                        if result.get("pdf"):
                            st.write("PDF saved at:", result.get("pdf"))
                    else:
                        st.warning(result.get("message", "No data found"))

                except Exception as e:
                    st.error(str(e))