import streamlit as st


def location_inputs(service_name, master, districts, headless):
    st.markdown(f"### Fetch {service_name} Document")

    col1, col2 = st.columns(2)

    with col1:
        district = st.selectbox(
            "District",
            ["Select District"] + districts,
            key=f"{service_name}_district"
        )

        if district != "Select District" and district in master:
            taluks = list(master[district].keys())
            taluk = st.selectbox(
                "Taluk",
                ["Select Taluk"] + taluks,
                key=f"{service_name}_taluk"
            )
        else:
            taluk = st.selectbox(
                "Taluk",
                ["Select Taluk"],
                disabled=True,
                key=f"{service_name}_taluk_disabled"
            )

    with col2:
        if district != "Select District" and taluk != "Select Taluk":
            hoblis = list(master[district][taluk].keys())
            hobli = st.selectbox(
                "Hobli",
                ["Select Hobli"] + hoblis,
                key=f"{service_name}_hobli"
            )
        else:
            hobli = st.selectbox(
                "Hobli",
                ["Select Hobli"],
                disabled=True,
                key=f"{service_name}_hobli_disabled"
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
                key=f"{service_name}_village"
            )
        else:
            village = st.selectbox(
                "Village",
                ["Select Village"],
                disabled=True,
                key=f"{service_name}_village_disabled"
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
        key=f"{service_name}_survey"
    )

    hissa_no = ""

    if service_name == "RTC":
        hissa_no = st.text_input(
            "Hissa Number (Optional)",
            placeholder="Example: 1, 2, A",
            key=f"{service_name}_hissa"
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
        "headless": headless
    }

    return payload, disabled