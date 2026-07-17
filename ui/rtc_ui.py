import requests
import streamlit as st

from ui.common import location_inputs


def render_rtc_ui(api_base, master, districts, headless):
    payload, disabled = location_inputs(
        service_name="RTC",
        master=master,
        districts=districts,
        headless=headless,
        include_survey=True,
        include_hissa=True,
    )

    if st.button(
        "Download RTC",
        disabled=disabled,
        width="stretch",
    ):
        try:
            with st.spinner("Opening Bhoomi Portal and downloading RTC..."):
                response = requests.post(
                    f"{api_base}/api/fetch-rtc/auto",
                    json=payload,
                    timeout=600,
                )

            result = response.json()

            if result.get("success"):
                st.success("RTC downloaded successfully.")

                if result.get("pdf"):
                    st.info(f"Saved PDF:\n\n{result['pdf']}")
            else:
                st.warning(result.get("message", "No records or data exist."))

                if result.get("portal_message"):
                    st.write(result.get("portal_message"))

                st.write(result)

        except Exception as e:
            st.error(str(e))
