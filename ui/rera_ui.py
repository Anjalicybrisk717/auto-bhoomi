import requests
import streamlit as st
import re

# =========================================================
# API HELPERS
# =========================================================

def rera_post(
    api_base,
    route,
    payload,
    timeout=900,
):
    response = requests.post(
        f"{api_base.rstrip('/')}{route}",
        json=payload,
        timeout=timeout,
    )

    try:
        result = response.json()

    except ValueError as error:
        raise RuntimeError(
            response.text
            or
            "Invalid RERA API response"
        ) from error

    if (
        not response.ok
        or not result.get("success")
    ):
        raise RuntimeError(
            result.get("detail")
            or result.get("error")
            or result.get("message")
            or "RERA automation failed"
        )

    return result


def rera_download_pdf(
    api_base,
    download_url,
):
    if not download_url:
        raise RuntimeError(
            "RERA API did not return "
            "a PDF download URL"
        )

    if download_url.startswith("/"):
        full_url = (
            f"{api_base.rstrip('/')}"
            f"{download_url}"
        )

    else:
        full_url = download_url

    response = requests.get(
        full_url,
        timeout=300,
    )

    response.raise_for_status()

    if not response.content.startswith(
        b"%PDF-"
    ):
        raise RuntimeError(
            "Downloaded file is not "
            "a valid PDF"
        )

    return response.content


# =========================================================
# SESSION STATE
# =========================================================

def clear_rera_pdf():
    st.session_state[
        "rera_selected_project"
    ] = None

    st.session_state[
        "rera_pdf_result"
    ] = None

    st.session_state[
        "rera_pdf_bytes"
    ] = None


# =========================================================
# CREATE PROJECT PDF
# =========================================================

def create_rera_pdf(
    api_base,
    project,
    headless,
):
    result = rera_post(
        api_base,
        "/api/rera/project-pdf",
        {
            "registrationNumber":
                project.get(
                    "registration_number",
                    "",
                ),

            "projectName":
                project.get(
                    "project_name",
                    "",
                ),

            "promoterName":
                project.get(
                    "promoter_name",
                    "",
                ),

            "headless":
                bool(headless),
        },
        timeout=900,
    )

    pdf_bytes = (
        rera_download_pdf(
            api_base,
            result.get(
                "downloadUrl",
                "",
            ),
        )
    )

    st.session_state[
        "rera_selected_project"
    ] = project

    st.session_state[
        "rera_pdf_result"
    ] = result

    st.session_state[
        "rera_pdf_bytes"
    ] = pdf_bytes


def project_label(
    project,
    index,
):
    return (
        f"{index + 1}. "
        f"{project.get('project_name') or 'Unnamed project'} | "
        f"{project.get('promoter_name') or 'Unknown promoter'} | "
        f"{project.get('registration_number') or 'No registration number'}"
    )


# =========================================================
# MAIN RERA UI
# =========================================================

def render_rera_ui(
    api_base,
    master=None,
    districts=None,
    headless=False,
):
    del master
    del districts

    defaults = {
        "rera_promoter_query": "",
        "rera_project_query": "",
        "rera_registration_query": "",
        "rera_search_result": None,
        "rera_selected_index": 0,
        "rera_selected_project": None,
        "rera_pdf_result": None,
        "rera_pdf_bytes": None,
        "rera_single_result_processed": False,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[
                key
            ] = value

    # -----------------------------------------------------
    # SEARCH FUNCTION
    # -----------------------------------------------------

    def run_search(
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
            f"{api_base.rstrip('/')}/api/rera",
            json={
                "action": "search",
                "searchType": search_type,
                "query": query,
                "headless": bool(headless),
                "maxPages": 25,
                "maxResults": 500,
            },
            timeout=900,
        )

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if (
            response.ok
            and "application/pdf" in content_type
            and response.content.startswith(b"%PDF-")
        ):
            disposition = response.headers.get(
                "content-disposition",
                "",
            )

            filename_match = re.search(
                r"""filename\*?=(?:UTF-8''|")?([^";]+)""",
                disposition,
                flags=re.IGNORECASE,
            )

            filename = (
                filename_match.group(1).strip('"')
                if filename_match
                else "rera_project.pdf"
            )

            st.session_state[
                "rera_search_result"
            ] = {
                "success": True,
                "searchType": search_type,
                "query": query,
                "count": 1,
                "results": [],
                "auto_pdf": True,
            }

            st.session_state[
                "rera_pdf_result"
            ] = {
                "success": True,
                "filename": filename,
            }

            st.session_state[
                "rera_pdf_bytes"
            ] = response.content

            st.session_state[
                "rera_single_result_processed"
            ] = True

            return

        if not response.ok:
            try:
                error_result = response.json()

                error_message = (
                    error_result.get("detail")
                    or error_result.get("error")
                    or error_result.get("message")
                    or response.text
                )

            except ValueError:
                error_message = response.text

            raise RuntimeError(
                error_message
                or "RERA search failed."
            )

        try:
            result = response.json()
        except ValueError as error:
            raise RuntimeError(
                "The RERA server returned an "
                "invalid response."
            ) from error

        st.session_state[
            "rera_search_result"
        ] = result

        st.session_state[
            "rera_selected_index"
        ] = 0

        st.session_state[
            "rera_single_result_processed"
        ] = False

    # -----------------------------------------------------
    # PAGE HEADING
    # -----------------------------------------------------

    st.markdown(
        "## Karnataka RERA Project Search"
    )

    (
        promoter_tab,
        project_tab,
        registration_tab,
    ) = st.tabs(
        [
            "Promoter Name",
            "Project Name",
            "Registration No.",
        ]
    )

    # -----------------------------------------------------
    # PROMOTER NAME TAB
    # -----------------------------------------------------

    with promoter_tab:
        promoter_query = st.text_input(
            "Enter Promoter Name",
            key="rera_promoter_query",
            placeholder=(
                "Example: Prestige Estates"
            ),
        )

        promoter_search = st.button(
            "Search",
            key="rera_promoter_search",
            type="primary",
            use_container_width=True,
            disabled=(
                not promoter_query.strip()
            ),
        )

        if promoter_search:
            try:
                with st.spinner(
                    "Searching Karnataka "
                    "RERA portal..."
                ):
                    run_search(
                        "promoter",
                        promoter_query,
                    )

            except (
                requests.exceptions
                .ConnectionError
            ):
                st.error(
                    "FastAPI server is not "
                    "running on port 5000"
                )

            except (
                requests.exceptions.Timeout
            ):
                st.error(
                    "Karnataka RERA portal "
                    "took too long to respond"
                )

            except Exception as error:
                st.error(
                    str(error)
                )

    # -----------------------------------------------------
    # PROJECT NAME TAB
    # -----------------------------------------------------

    with project_tab:
        project_query = st.text_input(
            "Enter Project Name",
            key="rera_project_query",
            placeholder=(
                "Example: Sattva"
            ),
        )

        project_search = st.button(
            "Search",
            key="rera_project_search",
            type="primary",
            use_container_width=True,
            disabled=(
                not project_query.strip()
            ),
        )

        if project_search:
            try:
                with st.spinner(
                    "Searching Karnataka "
                    "RERA portal..."
                ):
                    run_search(
                        "project",
                        project_query,
                    )

            except (
                requests.exceptions
                .ConnectionError
            ):
                st.error(
                    "FastAPI server is not "
                    "running on port 5000"
                )

            except (
                requests.exceptions.Timeout
            ):
                st.error(
                    "Karnataka RERA portal "
                    "took too long to respond"
                )

            except Exception as error:
                st.error(
                    str(error)
                )

    # -----------------------------------------------------
    # REGISTRATION NUMBER TAB
    # -----------------------------------------------------

    with registration_tab:
        registration_query = st.text_input(
            "Enter Registration Number",
            key="rera_registration_query",
            placeholder=(
                "Example: PRM/KA/RERA/12345"
            ),
        )

        registration_search = st.button(
            "Search",
            key="rera_registration_search",
            type="primary",
            use_container_width=True,
            disabled=(
                not registration_query.strip()
            ),
        )

        if registration_search:
            try:
                with st.spinner(
                    "Searching Karnataka "
                    "RERA portal..."
                ):
                    run_search(
                        "registration",
                        registration_query,
                    )

            except (
                requests.exceptions
                .ConnectionError
            ):
                st.error(
                    "FastAPI server is not "
                    "running on port 5000"
                )

            except (
                requests.exceptions.Timeout
            ):
                st.error(
                    "Karnataka RERA portal "
                    "took too long to respond"
                )

            except Exception as error:
                st.error(
                    str(error)
                )

    # =====================================================
    # SEARCH RESULTS
    # =====================================================

    result = st.session_state.get(
        "rera_search_result"
    )

    single_result_processed = st.session_state.get(
        "rera_single_result_processed",
        False,
    )

    if single_result_processed:
        pass

    elif result:
        results = result.get(
            "results",
            [],
        )

        if not results:
            st.info(
                "No matching RERA projects were found."
            )

        elif len(results) > 1:
            selected_index = st.selectbox(
                "Select the required project",
                options=range(len(results)),
                format_func=lambda index: project_label(
                    results[index],
                    index,
                ),
                key="rera_selected_index",
                on_change=clear_rera_pdf,
            )

            selected_project = results[
                selected_index
            ]

            create_pdf_button = st.button(
                "Open Details, Click Print and Create PDF",
                key="rera_create_selected_pdf",
                type="primary",
                use_container_width=True,
            )

            if create_pdf_button:
                with st.spinner(
                    "Opening View Project Details, "
                    "clicking Print and creating PDF..."
                ):
                    create_rera_pdf(
                        api_base,
                        selected_project,
                        headless,
                    )

    # =====================================================
    # PDF DOWNLOAD
    # =====================================================

    pdf_result = (
        st.session_state.get(
            "rera_pdf_result"
        )
    )

    pdf_bytes = (
        st.session_state.get(
            "rera_pdf_bytes"
        )
    )

    selected_project = (
        st.session_state.get(
            "rera_selected_project"
        )
        or {}
    )

    if pdf_result and pdf_bytes:
        st.divider()

        if pdf_result.get("page_count"):
            st.info(
                f"Pages saved: {pdf_result['page_count']}"
            )

        st.download_button(
            (
                "Download Complete "
                "RERA Project PDF"
            ),
            data=pdf_bytes,
            file_name=pdf_result.get(
                "filename",
                "rera_project.pdf",
            ),
            mime="application/pdf",
            use_container_width=True,
            type="primary",
            key=(
                "rera_download_pdf"
            ),
        )

