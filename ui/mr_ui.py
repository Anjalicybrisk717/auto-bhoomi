import streamlit as st
import requests

from ui.common import location_inputs, prepare_mr_payload


def render_mr_ui(api_base, master, districts, headless):
    payload, disabled = location_inputs(
        service_name="MR",
        master=master,
        districts=districts,
        headless=headless,
        include_survey=True,
        include_hissa=False,
    )

    if st.button(
        "🔍 Fetch Mutation Details",
        disabled=disabled,
        width="stretch",
    ):
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
                    st.success("Mutation records fetched successfully.")
                else:
                    st.warning("No mutation records found.")

            else:
                st.error("MR fetch failed.")
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

            if st.button(
                "📥 Download Selected MR",
                width="stretch",
            ):
                try:
                    selected_payload = st.session_state["mr_payload"].copy()
                    selected_payload["rowIndex"] = selected_index

                    with st.spinner("Opening preview and saving selected MR as PDF..."):
                        response = requests.post(
                            f"{api_base}/api/mr/download-selected",
                            json=selected_payload,
                            timeout=600,
                        )

                    result = response.json()

                    if result.get("success"):
                        st.success("Selected MR downloaded successfully.")

                        if result.get("pdf"):
                            st.info(f"Saved PDF:\n\n{result['pdf']}")

                    else:
                        st.warning(result.get("message", "No data found."))

                except Exception as e:
                    st.error(str(e))
