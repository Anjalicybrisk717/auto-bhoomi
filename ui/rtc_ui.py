import streamlit as st
import requests
from ui.common import location_inputs


def render_rtc_ui(api_base, master, districts, headless):
    payload, disabled = location_inputs(
        "RTC",
        master,
        districts,
        headless
    )

    if st.button("📥 Download RTC", disabled=disabled):
        try:
            with st.spinner("Opening Bhoomi Portal for RTC..."):
                response = requests.post(
                    f"{api_base}/api/fetch-rtc/auto",
                    json=payload,
                    timeout=600
                )

            result = response.json()

            if result.get("success"):
                st.success("RTC downloaded successfully")

                if result.get("pdf"):
                    st.write("PDF saved at:", result.get("pdf"))

                st.write(result)

            else:
                st.error("RTC download failed")
                st.write(result)

        except Exception as e:
            st.error(str(e))