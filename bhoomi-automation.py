import json
import os
import re
from pathlib import Path

import requests
import streamlit as st


# =========================================================
# BASIC CONFIG
# =========================================================

st.set_page_config(
    page_title="Bhoomi Automation",
    page_icon="📄",
    layout="wide",
)

API_BASE = os.getenv(
    "BHOOMI_API_BASE",
    "http://127.0.0.1:5000",
).rstrip("/")

BASE_DIR = Path(__file__).resolve().parent

BHOOMI_MASTER_FILES = [
    BASE_DIR / "bhoomi-master-bilingual.json",
    BASE_DIR / "bhoomi-master.json",
    BASE_DIR / "master.json",
]

AKARBAND_MASTER_FILE = BASE_DIR / "akarband-master.json"

SELECT = "--ಆಯ್ಕೆ--"


# =========================================================
# STYLE
# =========================================================

st.markdown(
    """
    <style>
        .stApp {
            background-color: #0e1117;
            color: white;
        }

        .block-container {
            padding-top: 1.2rem;
            max-width: 1200px;
        }

        label, .stMarkdown, .stTextInput label, .stSelectbox label {
            color: white !important;
            font-weight: 600 !important;
        }

        div.stButton > button {
            min-height: 42px;
            font-weight: 700;
            border-radius: 7px;
        }

        .main-title {
            color: white;
            font-size: 34px;
            font-weight: 800;
            margin-bottom: 20px;
        }

        .blue-heading {
            background: #0500d8;
            color: white;
            padding: 12px;
            border-radius: 5px;
            font-size: 18px;
            text-align: center;
            font-weight: 700;
            margin-bottom: 22px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# COMMON HELPERS
# =========================================================

@st.cache_data(show_spinner=False)
def load_json_file(path_string, modified_time):
    del modified_time

    path = Path(path_string)

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    return data if isinstance(data, dict) else {}


def load_first_existing_json(paths):
    for path in paths:
        if path.exists():
            try:
                return load_json_file(
                    str(path),
                    path.stat().st_mtime,
                )
            except Exception:
                return {}

    return {}


def clean_options(values):
    cleaned = []
    seen = set()

    for value in values:
        if isinstance(value, dict):
            value = (
                value.get("name")
                or value.get("label")
                or value.get("text")
                or value.get("value")
                or ""
            )

        value = str(value).strip()

        if not value:
            continue

        lower_value = value.lower()

        if (
            "ಆಯ್ಕೆ" in value
            or "select" in lower_value
            or "choose" in lower_value
            or "option" in lower_value
        ):
            continue

        if value in seen:
            continue

        seen.add(value)
        cleaned.append(value)

    return cleaned


def get_root_container(data):
    if not isinstance(data, dict):
        return {}

    for key in [
        "districts",
        "Districts",
        "DISTRICTS",
        "ಜಿಲ್ಲೆಗಳು",
    ]:
        if isinstance(data.get(key), dict):
            return data[key]

    return data


def get_child_container(node, wrapper_keys):
    if not isinstance(node, dict):
        return {}

    for key in wrapper_keys:
        if isinstance(node.get(key), dict):
            return node[key]

    return node


def get_options(node):
    if isinstance(node, dict):
        return clean_options(node.keys())

    if isinstance(node, list):
        return clean_options(node)

    return []


def get_village_options(hobli_node):
    if isinstance(hobli_node, list):
        return clean_options(hobli_node)

    if isinstance(hobli_node, dict):
        for key in [
            "villages",
            "village",
            "Villages",
            "VILLAGES",
            "ಗ್ರಾಮಗಳು",
        ]:
            value = hobli_node.get(key)

            if isinstance(value, list):
                return clean_options(value)

            if isinstance(value, dict):
                return clean_options(value.keys())

        return clean_options(hobli_node.keys())

    return []


def filename_from_response(response, default_name):
    disposition = response.headers.get(
        "content-disposition",
        "",
    )

    match = re.search(
        r"""filename\*?=(?:UTF-8''|")?([^";]+)""",
        disposition,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).strip('"')

    return default_name


def download_url_to_bytes(api_base, download_url):
    if not download_url:
        return None

    if download_url.startswith("/"):
        url = f"{api_base.rstrip('/')}{download_url}"
    else:
        url = download_url

    response = requests.get(
        url,
        timeout=300,
    )

    response.raise_for_status()

    return response.content


def handle_backend_response(
    response,
    default_filename,
):
    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    if (
        response.ok
        and "application/pdf" in content_type
        and response.content.startswith(b"%PDF")
    ):
        return {
            "kind": "pdf",
            "bytes": response.content,
            "filename": filename_from_response(
                response,
                default_filename,
            ),
            "json": None,
        }

    try:
        result = response.json()
    except ValueError:
        result = {
            "success": False,
            "detail": response.text,
        }

    if not response.ok:
        raise RuntimeError(
            result.get("detail")
            or result.get("error")
            or result.get("message")
            or response.text
            or "Backend request failed."
        )

    return {
        "kind": "json",
        "bytes": None,
        "filename": default_filename,
        "json": result,
    }


def show_pdf_download(
    label,
    pdf_bytes,
    filename,
    key,
):
    if not pdf_bytes:
        return

    st.download_button(
        label=label,
        data=pdf_bytes,
        file_name=filename,
        mime="application/pdf",
        use_container_width=True,
        type="primary",
        key=key,
    )


def post_to_first_working_endpoint(
    endpoint_list,
    payload,
    timeout=900,
):
    errors = []

    for endpoint in endpoint_list:
        url = f"{API_BASE}{endpoint}"

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=timeout,
            )

            if response.status_code == 404:
                errors.append(
                    f"{endpoint}: 404 Not Found"
                )
                continue

            return response

        except requests.exceptions.RequestException as error:
            errors.append(
                f"{endpoint}: {error}"
            )

    raise RuntimeError(
        "No backend endpoint worked:\n"
        + "\n".join(errors)
    )


def make_payload(
    district,
    taluk,
    hobli,
    village,
    survey_number,
    surnoc,
    hissa,
    headless,
    period=None,
):
    payload = {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,

        "surveyNumber": survey_number,
        "survey_number": survey_number,

        "surnoc": surnoc,
        "hissa": hissa,

        "headless": bool(headless),
    }

    if period is not None:
        payload["period"] = period

    return payload


# =========================================================
# LOCATION DROPDOWNS
# =========================================================

def render_location_dropdowns(
    prefix,
    master_data,
):
    root = get_root_container(master_data)

    district_options = get_options(root)

    col1, col2, col3 = st.columns(3)

    with col1:
        district = st.selectbox(
            "ಜಿಲ್ಲೆ:",
            [SELECT] + district_options,
            key=f"{prefix}_district",
        )

    district_node = (
        root.get(district, {})
        if district != SELECT
        else {}
    )

    taluk_container = get_child_container(
        district_node,
        [
            "taluks",
            "taluk",
            "Taluks",
            "TALUKS",
            "ತಾಲೂಕುಗಳು",
        ],
    )

    taluk_options = get_options(taluk_container)

    with col2:
        taluk = st.selectbox(
            "ತಾಲೂಕು:",
            [SELECT] + taluk_options,
            disabled=district == SELECT,
            key=f"{prefix}_taluk",
        )

    taluk_node = (
        taluk_container.get(taluk, {})
        if taluk != SELECT
        else {}
    )

    hobli_container = get_child_container(
        taluk_node,
        [
            "hoblis",
            "hobli",
            "Hoblis",
            "HOBLIS",
            "ಹೋಬಳಿಗಳು",
        ],
    )

    hobli_options = get_options(hobli_container)

    with col3:
        hobli = st.selectbox(
            "ಹೋಬಳಿ:",
            [SELECT] + hobli_options,
            disabled=taluk == SELECT,
            key=f"{prefix}_hobli",
        )

    hobli_node = (
        hobli_container.get(hobli, {})
        if hobli != SELECT
        else {}
    )

    village_options = get_village_options(
        hobli_node
    )

    col4, col5, col6 = st.columns(3)

    with col4:
        village = st.selectbox(
            "ಗ್ರಾಮ:",
            [SELECT] + village_options,
            disabled=hobli == SELECT,
            key=f"{prefix}_village",
        )

    return {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "col5": col5,
        "col6": col6,
    }


# =========================================================
# RTC / MR / REVENUE MAP / SURVEY SKETCH UI
# =========================================================

def render_land_service(
    service_name,
    prefix,
    endpoint_list,
    needs_period=False,
):
    st.markdown(
        f'<div class="blue-heading">{service_name}</div>',
        unsafe_allow_html=True,
    )

    master_data = load_first_existing_json(
        BHOOMI_MASTER_FILES
    )

    if not master_data:
        st.warning(
            "Bhoomi master JSON not found. "
            "Place bhoomi-master-bilingual.json or bhoomi-master.json "
            "in the same folder."
        )

        col1, col2, col3 = st.columns(3)

        with col1:
            district = st.text_input(
                "District",
                key=f"{prefix}_district_text",
            )

        with col2:
            taluk = st.text_input(
                "Taluk",
                key=f"{prefix}_taluk_text",
            )

        with col3:
            hobli = st.text_input(
                "Hobli",
                key=f"{prefix}_hobli_text",
            )

        col4, col5, col6 = st.columns(3)

        with col4:
            village = st.text_input(
                "Village",
                key=f"{prefix}_village_text",
            )

    else:
        selected = render_location_dropdowns(
            prefix,
            master_data,
        )

        district = selected["district"]
        taluk = selected["taluk"]
        hobli = selected["hobli"]
        village = selected["village"]

        col5 = selected["col5"]
        col6 = selected["col6"]

    if master_data:
        with col5:
            survey_number = st.text_input(
                "ಸರ್ವೆ ಸಂಖ್ಯೆ:",
                key=f"{prefix}_survey_number",
            )

        with col6:
            surnoc = st.text_input(
                "ಸರ್‌ನೋಕ್:",
                key=f"{prefix}_surnoc",
            )
    else:
        col7, col8, col9 = st.columns(3)

        with col7:
            survey_number = st.text_input(
                "Survey Number",
                key=f"{prefix}_survey_number_text",
            )

        with col8:
            surnoc = st.text_input(
                "Surnoc",
                key=f"{prefix}_surnoc_text",
            )

    col10, col11, col12 = st.columns(3)

    with col10:
        hissa = st.text_input(
            "ಹಿಸ್ಸಾ:",
            key=f"{prefix}_hissa",
        )

    period = None

    if needs_period:
        with col11:
            period = st.selectbox(
                "Period:",
                [
                    "Current Year",
                    "Old Year",
                ],
                key=f"{prefix}_period",
            )

    with col12:
        st.write("")
        st.write("")

        clicked = st.button(
            f"{service_name} ಪಡೆಯಿರಿ",
            use_container_width=True,
            type="primary",
            key=f"{prefix}_button",
        )

    if clicked:
        required_values = [
            district,
            taluk,
            hobli,
            village,
            survey_number,
        ]

        if any(
            not str(value).strip()
            or value == SELECT
            for value in required_values
        ):
            st.error(
                "Please select District, Taluk, Hobli, Village "
                "and enter Survey Number."
            )
            return

        payload = make_payload(
            district=district,
            taluk=taluk,
            hobli=hobli,
            village=village,
            survey_number=survey_number.strip(),
            surnoc=surnoc.strip(),
            hissa=hissa.strip(),
            headless=st.session_state.get(
                "headless",
                True,
            ),
            period=period,
        )

        try:
            with st.spinner(
                f"{service_name} fetching..."
            ):
                response = post_to_first_working_endpoint(
                    endpoint_list,
                    payload,
                    timeout=900,
                )

            handled = handle_backend_response(
                response,
                f"{prefix}.pdf",
            )

            if handled["kind"] == "pdf":
                st.success(
                    f"{service_name} downloaded successfully."
                )

                show_pdf_download(
                    f"Download {service_name} PDF",
                    handled["bytes"],
                    handled["filename"],
                    f"{prefix}_download",
                )

            else:
                result = handled["json"]

                if result.get("success") is False:
                    st.error(
                        result.get("detail")
                        or result.get("error")
                        or result.get("message")
                        or f"{service_name} failed."
                    )

                    st.json(result)
                    return

                download_url = (
                    result.get("downloadUrl")
                    or result.get("download_url")
                )

                if download_url:
                    pdf_bytes = download_url_to_bytes(
                        API_BASE,
                        download_url,
                    )

                    st.success(
                        f"{service_name} downloaded successfully."
                    )

                    show_pdf_download(
                        f"Download {service_name} PDF",
                        pdf_bytes,
                        result.get(
                            "filename",
                            f"{prefix}.pdf",
                        ),
                        f"{prefix}_download_url",
                    )

                else:
                    st.success(
                        f"{service_name} request completed."
                    )

                    st.json(result)

        except Exception as error:
            st.error(
                f"{service_name} failed: {error}"
            )


# =========================================================
# AKARBAND UI
# =========================================================

@st.cache_data(ttl=900, show_spinner=False)
def akarband_options(
    api_base,
    district,
    taluk,
    hobli,
    village,
    survey_number,
    surnoc,
    headless,
):
    response = requests.post(
        f"{api_base.rstrip('/')}/api/akarband/options",
        json={
            "district": district,
            "taluk": taluk,
            "hobli": hobli,
            "village": village,
            "surveyNumber": survey_number,
            "surnoc": surnoc,
            "headless": bool(headless),
        },
        timeout=300,
    )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            response.text
            or "Invalid Akarband option response."
        )

    if not response.ok or not result.get("success", True):
        raise RuntimeError(
            result.get("detail")
            or result.get("error")
            or result.get("message")
            or "Unable to load Akarband options."
        )

    return result


def render_akarband_ui():
    st.markdown(
        '<div class="blue-heading">ಆಕಾರಬಂದ್</div>',
        unsafe_allow_html=True,
    )

    if not AKARBAND_MASTER_FILE.exists():
        st.error(
            "akarband-master.json not found. "
            "Keep it in the same folder as bhoomi-automation.py."
        )
        return

    try:
        akarband_master = load_json_file(
            str(AKARBAND_MASTER_FILE),
            AKARBAND_MASTER_FILE.stat().st_mtime,
        )
    except Exception as error:
        st.error(
            f"Unable to read akarband-master.json: {error}"
        )
        return

    selected = render_location_dropdowns(
        "akarband",
        akarband_master,
    )

    district = selected["district"]
    taluk = selected["taluk"]
    hobli = selected["hobli"]
    village = selected["village"]

    col5 = selected["col5"]
    col6 = selected["col6"]

    survey_options = []
    surnoc_options = []
    hissa_options = []
    option_error = None

    if village != SELECT:
        try:
            result = akarband_options(
                API_BASE,
                district,
                taluk,
                hobli,
                village,
                "",
                "",
                st.session_state.get(
                    "headless",
                    True,
                ),
            )

            survey_options = clean_options(
                result.get("surveyNumbers", [])
            )

        except Exception as error:
            option_error = str(error)

    with col5:
        survey_number = st.selectbox(
            "ಸರ್ವೆ ಸಂಖ್ಯೆ:",
            [SELECT] + survey_options,
            disabled=not survey_options,
            key="akarband_survey_number",
        )

    if survey_number != SELECT:
        try:
            result = akarband_options(
                API_BASE,
                district,
                taluk,
                hobli,
                village,
                survey_number,
                "",
                st.session_state.get(
                    "headless",
                    True,
                ),
            )

            surnoc_options = clean_options(
                result.get("surnocs", [])
            )

        except Exception as error:
            option_error = str(error)

    with col6:
        surnoc = st.selectbox(
            "ಸರ್‌ನೋಕ್:",
            [SELECT] + surnoc_options,
            disabled=not surnoc_options,
            key="akarband_surnoc",
        )

    if surnoc != SELECT:
        try:
            result = akarband_options(
                API_BASE,
                district,
                taluk,
                hobli,
                village,
                survey_number,
                surnoc,
                st.session_state.get(
                    "headless",
                    True,
                ),
            )

            hissa_options = clean_options(
                result.get("hissas", [])
            )

        except Exception as error:
            option_error = str(error)

    col7, col8, col9 = st.columns(3)

    with col7:
        hissa = st.selectbox(
            "ಹಿಸ್ಸಾ:",
            [SELECT] + hissa_options,
            disabled=not hissa_options,
            key="akarband_hissa",
        )

    with col8:
        st.write("")

    with col9:
        st.write("")
        st.write("")

        clicked = st.button(
            "ಆಕಾರಬಂದ್ ಪಡೆಯಿರಿ",
            use_container_width=True,
            type="primary",
            key="akarband_fetch_button",
        )

    if option_error:
        st.warning(
            f"Akarband dropdown data issue: {option_error}"
        )

    if clicked:
        required = [
            district,
            taluk,
            hobli,
            village,
            survey_number,
            surnoc,
            hissa,
        ]

        if any(
            not value
            or value == SELECT
            for value in required
        ):
            st.error(
                "Please select all Akarband dropdown values."
            )
            return

        payload = {
            "district": district,
            "taluk": taluk,
            "hobli": hobli,
            "village": village,
            "surveyNumber": survey_number,
            "surnoc": surnoc,
            "hissa": hissa,
            "headless": st.session_state.get(
                "headless",
                True,
            ),
        }

        try:
            with st.spinner(
                "Akarband downloading..."
            ):
                response = requests.post(
                    f"{API_BASE}/api/akarband/fetch",
                    json=payload,
                    timeout=900,
                )

            handled = handle_backend_response(
                response,
                "akarband.pdf",
            )

            if handled["kind"] == "pdf":
                st.success(
                    "Akarband PDF downloaded successfully."
                )

                show_pdf_download(
                    "Download Akarband PDF",
                    handled["bytes"],
                    handled["filename"],
                    "akarband_pdf_download",
                )

            else:
                result = handled["json"]

                if result.get("success") is False:
                    st.error(
                        "Akarband download failed."
                    )
                    st.json(result)
                    return

                download_url = (
                    result.get("downloadUrl")
                    or result.get("download_url")
                )

                if download_url:
                    pdf_bytes = download_url_to_bytes(
                        API_BASE,
                        download_url,
                    )

                    st.success(
                        "Akarband PDF downloaded successfully."
                    )

                    show_pdf_download(
                        "Download Akarband PDF",
                        pdf_bytes,
                        result.get(
                            "filename",
                            "akarband.pdf",
                        ),
                        "akarband_download_url",
                    )

                else:
                    st.success(
                        "Akarband request completed."
                    )
                    st.json(result)

        except Exception as error:
            st.error(
                f"Akarband download failed: {error}"
            )


# =========================================================
# RERA UI
# =========================================================

def rera_project_label(project, index):
    return (
        f"{index + 1}. "
        f"{project.get('project_name') or 'Unnamed project'} | "
        f"{project.get('promoter_name') or 'Unknown promoter'} | "
        f"{project.get('registration_number') or 'No registration number'}"
    )


def clear_rera_pdf():
    st.session_state["rera_pdf_bytes"] = None
    st.session_state["rera_pdf_filename"] = None
    st.session_state["rera_single_result_processed"] = False


def create_rera_pdf(
    selected_project,
):
    response = requests.post(
        f"{API_BASE}/api/rera",
        json={
            "action": "pdf",
            "registrationNumber": selected_project.get(
                "registration_number",
                "",
            ),
            "projectName": selected_project.get(
                "project_name",
                "",
            ),
            "promoterName": selected_project.get(
                "promoter_name",
                "",
            ),
            "headless": st.session_state.get(
                "headless",
                True,
            ),
        },
        timeout=900,
    )

    if not response.ok:
        try:
            error_result = response.json()
            message = (
                error_result.get("detail")
                or error_result.get("error")
                or error_result.get("message")
                or response.text
            )
        except ValueError:
            message = response.text

        raise RuntimeError(
            message or "RERA PDF creation failed."
        )

    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    if (
        "application/pdf" not in content_type
        or not response.content.startswith(b"%PDF")
    ):
        raise RuntimeError(
            "Server did not return a valid RERA PDF."
        )

    st.session_state["rera_pdf_bytes"] = response.content
    st.session_state["rera_pdf_filename"] = filename_from_response(
        response,
        "rera_project.pdf",
    )


def run_rera_search(
    search_type,
    query,
):
    query = query.strip()

    if len(query) < 2:
        st.error(
            "Enter at least two characters."
        )
        return

    clear_rera_pdf()

    response = requests.post(
        f"{API_BASE}/api/rera",
        json={
            "action": "search",
            "searchType": search_type,
            "query": query,
            "headless": st.session_state.get(
                "headless",
                True,
            ),
            "maxPages": 25,
            "maxResults": 500,
        },
        timeout=900,
    )

    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    # One result: backend returns PDF directly
    if (
        response.ok
        and "application/pdf" in content_type
        and response.content.startswith(b"%PDF")
    ):
        st.session_state["rera_search_result"] = {
            "success": True,
            "searchType": search_type,
            "query": query,
            "count": 1,
            "results": [],
            "auto_pdf": True,
        }

        st.session_state["rera_pdf_bytes"] = response.content
        st.session_state["rera_pdf_filename"] = filename_from_response(
            response,
            "rera_project.pdf",
        )
        st.session_state["rera_single_result_processed"] = True
        return

    if not response.ok:
        try:
            error_result = response.json()
            message = (
                error_result.get("detail")
                or error_result.get("error")
                or error_result.get("message")
                or response.text
            )
        except ValueError:
            message = response.text

        raise RuntimeError(
            message or "RERA search failed."
        )

    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError(
            "Invalid RERA search response."
        ) from error

    st.session_state["rera_search_result"] = result
    st.session_state["rera_selected_index"] = 0
    st.session_state["rera_single_result_processed"] = False


def render_rera_ui():
    st.markdown(
        "## Karnataka RERA Project Search"
    )

    defaults = {
        "rera_promoter_query": "",
        "rera_project_query": "",
        "rera_registration_query": "",
        "rera_search_result": None,
        "rera_selected_index": 0,
        "rera_pdf_bytes": None,
        "rera_pdf_filename": None,
        "rera_single_result_processed": False,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    promoter_tab, project_tab, registration_tab = st.tabs(
        [
            "Promoter Name",
            "Project Name",
            "Registration No.",
        ]
    )

    with promoter_tab:
        promoter_query = st.text_input(
            "Enter Promoter Name",
            key="rera_promoter_query",
            placeholder="Example: SRIVARI INFRASTRUCTURES",
        )

        if st.button(
            "Search",
            key="rera_promoter_search",
            use_container_width=True,
            type="primary",
            disabled=not promoter_query.strip(),
        ):
            try:
                with st.spinner(
                    "Searching Karnataka RERA portal..."
                ):
                    run_rera_search(
                        "promoter",
                        promoter_query,
                    )
            except Exception as error:
                st.error(
                    f"RERA search failed: {error}"
                )

    with project_tab:
        project_query = st.text_input(
            "Enter Project Name",
            key="rera_project_query",
            placeholder="Example: SATTVA",
        )

        if st.button(
            "Search",
            key="rera_project_search",
            use_container_width=True,
            type="primary",
            disabled=not project_query.strip(),
        ):
            try:
                with st.spinner(
                    "Searching Karnataka RERA portal..."
                ):
                    run_rera_search(
                        "project",
                        project_query,
                    )
            except Exception as error:
                st.error(
                    f"RERA search failed: {error}"
                )

    with registration_tab:
        registration_query = st.text_input(
            "Enter Registration Number",
            key="rera_registration_query",
            placeholder=(
                "Example: PRM/KA/RERA/1251/446/PR/240323/005820"
            ),
        )

        if st.button(
            "Search",
            key="rera_registration_search",
            use_container_width=True,
            type="primary",
            disabled=not registration_query.strip(),
        ):
            try:
                with st.spinner(
                    "Searching Karnataka RERA portal..."
                ):
                    run_rera_search(
                        "registration",
                        registration_query,
                    )
            except Exception as error:
                st.error(
                    f"RERA search failed: {error}"
                )

    result = st.session_state.get(
        "rera_search_result"
    )

    single_done = st.session_state.get(
        "rera_single_result_processed",
        False,
    )

    if single_done:
        st.success(
            "One matching project was found. "
            "The same RERA session opened View Project Details, "
            "clicked Print and created the complete PDF."
        )

    elif result:
        results = result.get(
            "results",
            [],
        )

        st.divider()
        st.subheader("Search Results")

        st.caption(
            f"Found {len(results)} result(s) for "
            f"{result.get('searchType', '')}: "
            f"{result.get('query', '')}"
        )

        if not results:
            st.info(
                "No matching RERA projects were found."
            )

        elif len(results) > 1:
            st.dataframe(
                [
                    {
                        "Registration No.": row.get(
                            "registration_number",
                            "",
                        ),
                        "Promoter Name": row.get(
                            "promoter_name",
                            "",
                        ),
                        "Project Name": row.get(
                            "project_name",
                            "",
                        ),
                        "Type": row.get(
                            "project_type",
                            "",
                        ),
                    }
                    for row in results
                ],
                use_container_width=True,
                hide_index=True,
            )

            selected_index = st.selectbox(
                "Select the required project",
                options=range(len(results)),
                format_func=lambda index: rera_project_label(
                    results[index],
                    index,
                ),
                key="rera_selected_index",
                on_change=clear_rera_pdf,
            )

            selected_project = results[selected_index]

            c1, c2, c3 = st.columns(3)

            with c1:
                st.caption("Project Name")
                st.write(
                    selected_project.get("project_name")
                    or "Not available"
                )

            with c2:
                st.caption("Promoter Name")
                st.write(
                    selected_project.get("promoter_name")
                    or "Not available"
                )

            with c3:
                st.caption("Registration No.")
                st.write(
                    selected_project.get("registration_number")
                    or "Not available"
                )

            if st.button(
                "Open Details, Click Print and Create PDF",
                key="rera_selected_create_pdf",
                use_container_width=True,
                type="primary",
            ):
                try:
                    with st.spinner(
                        "Opening View Project Details, clicking Print "
                        "and creating PDF..."
                    ):
                        create_rera_pdf(
                            selected_project
                        )

                    st.success(
                        "RERA PDF created successfully."
                    )

                except Exception as error:
                    st.error(
                        f"RERA PDF failed: {error}"
                    )

    pdf_bytes = st.session_state.get(
        "rera_pdf_bytes"
    )

    pdf_filename = st.session_state.get(
        "rera_pdf_filename",
        "rera_project.pdf",
    )

    if pdf_bytes:
        st.divider()

        st.success(
            "Complete RERA Project PDF is ready."
        )

        show_pdf_download(
            "Download Complete RERA Project PDF",
            pdf_bytes,
            pdf_filename,
            "rera_pdf_download_button",
        )


# =========================================================
# MAIN APP
# =========================================================

st.markdown(
    '<div class="main-title">Bhoomi Automation</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.subheader("Settings")

    st.session_state["headless"] = st.checkbox(
        "Run browser headless",
        value=True,
    )

    st.caption(
        f"API Server: {API_BASE}"
    )

service = st.selectbox(
    "Select Bhoomi Service",
    [
        "RTC",
        "MR",
        "Revenue Map",
        "Survey Sketch",
        "Akarband",
        "RERA Search",
    ],
)

if service == "RTC":
    render_land_service(
        service_name="RTC",
        prefix="rtc",
        endpoint_list=[
            "/api/rtc/fetch",
            "/api/fetch-rtc/auto",
            "/api/fetch-rtc/playwright",
        ],
        needs_period=True,
    )

elif service == "MR":
    render_land_service(
        service_name="MR",
        prefix="mr",
        endpoint_list=[
            "/api/mr/fetch",
            "/api/fetch-mr/auto",
            "/api/mutation-register/fetch",
        ],
        needs_period=False,
    )

elif service == "Revenue Map":
    render_land_service(
        service_name="Revenue Map",
        prefix="revenue_map",
        endpoint_list=[
            "/api/revenue-map/fetch",
            "/api/revenue-map",
            "/api/fetch-revenue-map",
        ],
        needs_period=False,
    )

elif service == "Survey Sketch":
    render_land_service(
        service_name="Survey Sketch",
        prefix="survey_sketch",
        endpoint_list=[
            "/api/survey-sketch/fetch",
            "/api/survey-sketch",
            "/api/fetch-survey-sketch",
        ],
        needs_period=False,
    )

elif service == "Akarband":
    render_akarband_ui()

elif service == "RERA Search":
    render_rera_ui()