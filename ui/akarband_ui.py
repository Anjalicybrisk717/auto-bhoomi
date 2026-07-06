import json
from pathlib import Path
from typing import Any

import requests
import streamlit as st


PLACEHOLDER = "--ಆಯ್ಕೆ--"
MASTER_FILE = Path("akarband-master.json")


# -------------------------------------------------------------------
# JSON NORMALIZATION
# -------------------------------------------------------------------

NAME_KEYS = {
    "district": [
        "district",
        "district_name",
        "districtName",
        "DistrictName",
        "name",
        "label",
        "text",
    ],
    "taluk": [
        "taluk",
        "taluk_name",
        "talukName",
        "TalukName",
        "name",
        "label",
        "text",
    ],
    "hobli": [
        "hobli",
        "hobli_name",
        "hobliName",
        "HobliName",
        "name",
        "label",
        "text",
    ],
    "village": [
        "village",
        "village_name",
        "villageName",
        "VillageName",
        "name",
        "label",
        "text",
    ],
}


METADATA_KEYS = {
    "id",
    "code",
    "value",
    "selected",
    "disabled",
    "district_id",
    "districtId",
    "taluk_id",
    "talukId",
    "hobli_id",
    "hobliId",
    "village_id",
    "villageId",
    "districts",
    "taluks",
    "hoblis",
    "villages",
}


def clean_text(value: Any) -> str:
    """Return a clean display value."""

    if value is None:
        return ""

    value = str(value).strip()

    invalid_values = {
        "",
        "none",
        "null",
        "select",
        "--select--",
        "choose",
        "--choose--",
        PLACEHOLDER,
    }

    if value.lower() in invalid_values:
        return ""

    return value


def extract_node_name(node: Any, level: str) -> str:
    """Extract district/taluk/hobli/village name from a JSON object."""

    if isinstance(node, str):
        return clean_text(node)

    if not isinstance(node, dict):
        return ""

    for key in NAME_KEYS[level]:
        value = node.get(key)

        if isinstance(value, (str, int, float)):
            cleaned = clean_text(value)

            if cleaned:
                return cleaned

    return ""


def get_child_container(node: Any, wrapper_key: str) -> Any:
    """
    Get children from wrapper keys such as:
    taluks, hoblis and villages.

    For directly nested JSON, return the node itself.
    """

    if isinstance(node, dict):
        wrapped_value = node.get(wrapper_key)

        if isinstance(wrapped_value, (dict, list)):
            return wrapped_value

        return node

    if isinstance(node, list):
        return node

    return {}


def iter_named_nodes(container: Any, level: str):
    """
    Convert dictionary or list structures into:
    (display_name, child_node)
    """

    if isinstance(container, list):
        for item in container:
            if isinstance(item, str):
                name = clean_text(item)

                if name:
                    yield name, {}

            elif isinstance(item, dict):
                name = extract_node_name(item, level)

                if name:
                    yield name, item

        return

    if not isinstance(container, dict):
        return

    # The container itself may be a single object.
    single_name = extract_node_name(container, level)

    wrapper_names = {
        "district": "districts",
        "taluk": "taluks",
        "hobli": "hoblis",
        "village": "villages",
    }

    wrapper_name = wrapper_names[level]

    if single_name and wrapper_name in container:
        yield single_name, container
        return

    # Otherwise treat dictionary keys as display names.
    for key, value in container.items():
        if key in METADATA_KEYS:
            continue

        explicit_name = extract_node_name(value, level)
        name = explicit_name or clean_text(key)

        if not name:
            continue

        yield name, value


def natural_sort(values):
    """Remove duplicates and sort dropdown values."""

    cleaned_values = {
        clean_text(value)
        for value in values
        if clean_text(value)
    }

    return sorted(cleaned_values, key=lambda item: item.casefold())


def normalize_akarband_master(raw_master: Any) -> dict:
    """
    Convert different scraper JSON formats into:

    {
        "District": {
            "Taluk": {
                "Hobli": [
                    "Village 1",
                    "Village 2"
                ]
            }
        }
    }
    """

    normalized = {}

    if not isinstance(raw_master, (dict, list)):
        return normalized

    if isinstance(raw_master, dict) and "districts" in raw_master:
        district_container = raw_master["districts"]
    else:
        district_container = raw_master

    for district_name, district_node in iter_named_nodes(
        district_container,
        "district",
    ):
        district_name = clean_text(district_name)

        if not district_name:
            continue

        normalized.setdefault(district_name, {})

        taluk_container = get_child_container(
            district_node,
            "taluks",
        )

        for taluk_name, taluk_node in iter_named_nodes(
            taluk_container,
            "taluk",
        ):
            taluk_name = clean_text(taluk_name)

            if not taluk_name:
                continue

            normalized[district_name].setdefault(taluk_name, {})

            hobli_container = get_child_container(
                taluk_node,
                "hoblis",
            )

            for hobli_name, hobli_node in iter_named_nodes(
                hobli_container,
                "hobli",
            ):
                hobli_name = clean_text(hobli_name)

                if not hobli_name:
                    continue

                village_container = get_child_container(
                    hobli_node,
                    "villages",
                )

                villages = []

                for village_name, _ in iter_named_nodes(
                    village_container,
                    "village",
                ):
                    village_name = clean_text(village_name)

                    if village_name:
                        villages.append(village_name)

                normalized[district_name][taluk_name][hobli_name] = (
                    natural_sort(villages)
                )

    return normalized


def load_akarband_master() -> dict:
    """Load and normalize the Akarband master JSON file."""

    if not MASTER_FILE.exists():
        st.error(
            "akarband-master.json ಸಿಗಲಿಲ್ಲ. "
            "ಮೊದಲು scrape_akarband_master.py ಚಲಾಯಿಸಿ."
        )
        return {}

    try:
        with MASTER_FILE.open("r", encoding="utf-8-sig") as file:
            raw_master = json.load(file)

        normalized_master = normalize_akarband_master(raw_master)

        if not normalized_master:
            st.error(
                "akarband-master.json ನಲ್ಲಿ ಸರಿಯಾದ ಜಿಲ್ಲೆ, ತಾಲೂಕು, "
                "ಹೋಬಳಿ ಮತ್ತು ಗ್ರಾಮ ಮಾಹಿತಿ ಸಿಗಲಿಲ್ಲ."
            )

        return normalized_master

    except json.JSONDecodeError as error:
        st.error(
            f"akarband-master.json ಸರಿಯಾದ JSON ಆಗಿಲ್ಲ: {error}"
        )
        return {}

    except Exception as error:
        st.error(
            f"akarband-master.json ಓದಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: {error}"
        )
        return {}


# -------------------------------------------------------------------
# SESSION STATE RESET FUNCTIONS
# -------------------------------------------------------------------

def reset_after_district_change():
    st.session_state.pop("Akarband_taluk", None)
    st.session_state.pop("Akarband_hobli", None)
    st.session_state.pop("Akarband_village", None)


def reset_after_taluk_change():
    st.session_state.pop("Akarband_hobli", None)
    st.session_state.pop("Akarband_village", None)


def reset_after_hobli_change():
    st.session_state.pop("Akarband_village", None)


# -------------------------------------------------------------------
# MASTER FILE STATISTICS
# -------------------------------------------------------------------

def calculate_master_counts(master: dict) -> dict:
    district_count = len(master)

    taluk_count = sum(
        len(taluks)
        for taluks in master.values()
    )

    hobli_count = sum(
        len(hoblis)
        for taluks in master.values()
        for hoblis in taluks.values()
    )

    village_count = sum(
        len(villages)
        for taluks in master.values()
        for hoblis in taluks.values()
        for villages in hoblis.values()
    )

    return {
        "districts": district_count,
        "taluks": taluk_count,
        "hoblis": hobli_count,
        "villages": village_count,
    }


# -------------------------------------------------------------------
# AKARBAND UI
# -------------------------------------------------------------------

def render_akarband_ui(
    api_base,
    master=None,
    districts=None,
    headless=True,
):
    # master and districts are retained for compatibility
    # with the existing main application.
    del master
    del districts

    akarband_master = load_akarband_master()

    if not akarband_master:
        return

    counts = calculate_master_counts(akarband_master)

    st.markdown(
        """
        <div style="text-align:center;">
            <h3 style="color:#0037ff; margin-bottom:5px;">
                ಕರ್ನಾಟಕ ಸರ್ಕಾರ
            </h3>

            <h4 style="color:#0037ff; margin-top:5px;">
                ಭೂಮಾಪನ, ಕಂದಾಯ ವ್ಯವಸ್ಥೆ ಮತ್ತು ಭೂ ದಾಖಲೆಗಳ ಇಲಾಖೆ
            </h4>

            <div style="
                background:#0500d8;
                color:white;
                padding:10px;
                font-weight:bold;
                border-radius:4px;
                margin-top:12px;
            ">
                ಆಕಾರಬಂದ್
            </div>
        </div>
        <br>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        f"ಲೋಡ್ ಆಗಿರುವ ಮಾಹಿತಿ: "
        f"{counts['districts']} ಜಿಲ್ಲೆಗಳು | "
        f"{counts['taluks']} ತಾಲೂಕುಗಳು | "
        f"{counts['hoblis']} ಹೋಬಳಿಗಳು | "
        f"{counts['villages']} ಗ್ರಾಮಗಳು"
    )

    if counts["districts"] < 20:
        st.warning(
            "akarband-master.json ನಲ್ಲಿ ಎಲ್ಲಾ ಜಿಲ್ಲೆಗಳು ಇಲ್ಲದಿರುವ ಸಾಧ್ಯತೆ ಇದೆ. "
            "Master scraper ಅನ್ನು ಮತ್ತೆ ಚಲಾಯಿಸಿ."
        )

    district_options = [
        PLACEHOLDER,
        *natural_sort(akarband_master.keys()),
    ]

    col1, col2, col3 = st.columns(3)

    with col1:
        district = st.selectbox(
            "ಜಿಲ್ಲೆ:",
            district_options,
            key="Akarband_district",
            on_change=reset_after_district_change,
        )

    taluk_options = [PLACEHOLDER]

    if district != PLACEHOLDER:
        taluk_options.extend(
            natural_sort(
                akarband_master
                .get(district, {})
                .keys()
            )
        )

    with col2:
        taluk = st.selectbox(
            "ತಾಲೂಕು:",
            taluk_options,
            disabled=district == PLACEHOLDER,
            key="Akarband_taluk",
            on_change=reset_after_taluk_change,
        )

    hobli_options = [PLACEHOLDER]

    if district != PLACEHOLDER and taluk != PLACEHOLDER:
        hobli_options.extend(
            natural_sort(
                akarband_master
                .get(district, {})
                .get(taluk, {})
                .keys()
            )
        )

    with col3:
        hobli = st.selectbox(
            "ಹೋಬಳಿ:",
            hobli_options,
            disabled=(
                district == PLACEHOLDER
                or taluk == PLACEHOLDER
            ),
            key="Akarband_hobli",
            on_change=reset_after_hobli_change,
        )

    village_options = [PLACEHOLDER]

    if (
        district != PLACEHOLDER
        and taluk != PLACEHOLDER
        and hobli != PLACEHOLDER
    ):
        village_options.extend(
            natural_sort(
                akarband_master
                .get(district, {})
                .get(taluk, {})
                .get(hobli, [])
            )
        )

    col4, col5, col6 = st.columns(3)

    with col4:
        village = st.selectbox(
            "ಗ್ರಾಮ:",
            village_options,
            disabled=(
                district == PLACEHOLDER
                or taluk == PLACEHOLDER
                or hobli == PLACEHOLDER
            ),
            key="Akarband_village",
        )

    with col5:
        survey_number = st.text_input(
            "ಸರ್ವೆ ಸಂಖ್ಯೆ:",
            key="Akarband_survey",
            placeholder="ಸರ್ವೆ ಸಂಖ್ಯೆಯನ್ನು ನಮೂದಿಸಿ",
        )

    with col6:
        surnoc = st.text_input(
            "ಸರ್‌ನಾಕ್:",
            value="*",
            key="Akarband_surnoc",
        )

    col7, col8, col9 = st.columns(3)

    with col7:
        hissa = st.text_input(
            "ಹಿಸ್ಸಾ:",
            value="*",
            key="Akarband_hissa",
        )

    form_disabled = not (
        district != PLACEHOLDER
        and taluk != PLACEHOLDER
        and hobli != PLACEHOLDER
        and village != PLACEHOLDER
        and survey_number.strip()
    )

    with col9:
        st.write("")
        st.write("")

        clicked = st.button(
            "ಆಕಾರಬಂದ್ ಪಡೆಯಿರಿ",
            disabled=form_disabled,
            use_container_width=True,
            key="Akarband_submit",
        )

    # Diagnostic information
    with st.expander("ಆಯ್ಕೆ ಮಾಡಿದ ಮಾಹಿತಿಯನ್ನು ಪರಿಶೀಲಿಸಿ"):
        st.json(
            {
                "district": district,
                "taluk": taluk,
                "hobli": hobli,
                "village": village,
                "available_taluks": max(
                    len(taluk_options) - 1,
                    0,
                ),
                "available_hoblis": max(
                    len(hobli_options) - 1,
                    0,
                ),
                "available_villages": max(
                    len(village_options) - 1,
                    0,
                ),
            }
        )

    if not clicked:
        return

    payload = {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "surveyNumber": survey_number.strip(),
        "surnoc": surnoc.strip() or "*",
        "hissa": hissa.strip() or "*",
        "headless": bool(headless),
    }

    try:
        with st.spinner("ಆಕಾರಬಂದ್ ಪಡೆಯಲಾಗುತ್ತಿದೆ..."):
            response = requests.post(
                f"{api_base.rstrip('/')}/api/akarband/fetch",
                json=payload,
                timeout=600,
            )

        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            st.error(
                "Automation API ಸರಿಯಾದ JSON response ನೀಡಲಿಲ್ಲ."
            )
            st.code(response.text)
            return

        if result.get("success"):
            st.success("ಆಕಾರಬಂದ್ ಯಶಸ್ವಿಯಾಗಿ ಪಡೆಯಲಾಗಿದೆ.")

            pdf_path = (
                result.get("pdf")
                or result.get("downloaded_file")
                or result.get("file_path")
            )

            if pdf_path:
                st.info(f"ಉಳಿಸಿದ PDF:\n\n{pdf_path}")

            with st.expander("Automation ಫಲಿತಾಂಶ"):
                st.json(result)

        else:
            st.error("ಆಕಾರಬಂದ್ ಪಡೆಯಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ.")
            st.json(result)

    except requests.Timeout:
        st.error(
            "Automation request ಸಮಯ ಮೀರಿದೆ. "
            "Portal response ಪರಿಶೀಲಿಸಿ."
        )

    except requests.ConnectionError:
        st.error(
            f"Automation API ಸಂಪರ್ಕ ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: {api_base}"
        )

    except requests.HTTPError as error:
        st.error(
            f"Automation API HTTP error: {error}"
        )

        if error.response is not None:
            st.code(error.response.text)

    except Exception as error:
        st.error(f"ಅನಿರೀಕ್ಷಿತ ದೋಷ: {error}")