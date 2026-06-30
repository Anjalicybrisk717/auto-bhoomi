import streamlit as st
import requests

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
        "📥 Download RTC",
        disabled=disabled,
        use_container_width=True,
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

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        "Surnoc",
                        result.get("selected_surnoc", "-"),
                    )

                with col2:
                    st.metric(
                        "Hissa",
                        result.get("selected_hissa", "-"),
                    )

                with col3:
                    st.metric(
                        "Period",
                        result.get("selected_period", "-"),
                    )

                if result.get("pdf"):
                    st.info(f"Saved PDF:\n\n{result['pdf']}")

            else:
                st.error("RTC download failed.")
                st.write(result)

        except Exception as e:
            st.error(str(e))