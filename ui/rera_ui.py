import re
import io
import json
import zipfile
import requests
import streamlit as st
import uuid
from concurrent.futures import ThreadPoolExecutor


RERA_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def execute_rera_search(api_base, payload):
    response = requests.post(
        f"{api_base.rstrip('/')}/api/rera/search",
        json=payload,
        timeout=600,
    )
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(response.text or "Invalid RERA server response.")
    if not response.ok or not result.get("success"):
        raise RuntimeError(
            result.get("detail") or result.get("error")
            or result.get("message") or "RERA search failed."
        )
    return result


# =========================================================
# HELPERS
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
    st.session_state["rera_selected_project"] = None
    st.session_state["rera_documents"] = []
    st.session_state["rera_selected_documents"] = []
    st.session_state["rera_documents_zip_bytes"] = None
    st.session_state["rera_documents_zip_filename"] = None
    st.session_state["rera_all_documents_zip_bytes"] = None
    st.session_state["rera_all_documents_zip_filename"] = None
    st.session_state["rera_all_documents_summary"] = {}
    st.session_state["rera_all_documents_report"] = {}


def extract_filename_from_response(response):
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

    return "rera_project.pdf"


def download_pdf_from_result(api_base, result):
    download_url = result.get("downloadUrl", "")

    if not download_url:
        raise RuntimeError(
            "RERA server did not return a PDF download URL."
        )

    if download_url.startswith("/"):
        download_url = (
            f"{api_base.rstrip('/')}{download_url}"
        )

    response = requests.get(
        download_url,
        timeout=300,
    )

    response.raise_for_status()

    if not response.content.startswith(b"%PDF-"):
        raise RuntimeError(
            "Downloaded file is not a valid PDF."
        )

    return response.content


def create_rera_pdf(api_base, project, headless):
    response = requests.post(
        f"{api_base.rstrip('/')}/api/rera/project-pdf",
        json={
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
            "headless": bool(headless),
        },
        timeout=900,
    )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            response.text or "Invalid RERA server response."
        )

    if not response.ok or not result.get("success"):
        raise RuntimeError(
            result.get("detail")
            or result.get("error")
            or result.get("message")
            or "RERA PDF generation failed."
        )

    pdf_bytes = download_pdf_from_result(
        api_base,
        result,
    )

    st.session_state["rera_pdf_bytes"] = pdf_bytes
    st.session_state["rera_pdf_filename"] = result.get(
        "filename",
        "rera_project.pdf",
    )
    st.session_state["rera_selected_project"] = project

def extract_rera_download_report(zip_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zip_file:
            if "00_DOWNLOAD_REPORT.json" not in zip_file.namelist():
                return {}

            with zip_file.open("00_DOWNLOAD_REPORT.json") as report_file:
                return json.loads(
                    report_file.read().decode("utf-8")
                )

    except Exception:
        return {}


def download_all_rera_documents(
    api_base,
    selected_project,
    headless,
):
    response = requests.post(
        f"{api_base.rstrip('/')}/api/rera/download-all-documents",
        json={
            "registrationNumber":
                selected_project.get(
                    "registration_number",
                    "",
                ),
            "projectName":
                selected_project.get(
                    "project_name",
                    "",
                ),
            "promoterName":
                selected_project.get(
                    "promoter_name",
                    "",
                ),
            "headless":
                bool(headless),
        },
        timeout=1200,
    )

    if not response.ok:
        try:
            error_data = response.json()

            error_message = (
                error_data.get("detail")
                or error_data.get("error")
                or error_data.get("message")
                or response.text
            )

        except ValueError:
            error_message = response.text

        raise RuntimeError(
            error_message
            or "RERA all-document download failed."
        )

    if (
        "zip" not in response.headers.get("content-type", "").lower()
        and not response.content.startswith(b"PK")
    ):
        raise RuntimeError(
            "Server did not return a valid ZIP file."
        )

    filename = "rera_all_documents.zip"

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
        filename = match.group(1).strip('"')

    report = extract_rera_download_report(
        response.content
    )

    st.session_state[
        "rera_all_documents_zip_bytes"
    ] = response.content

    st.session_state[
        "rera_all_documents_zip_filename"
    ] = filename

    st.session_state[
        "rera_all_documents_summary"
    ] = {
        "found":
            report.get("found_candidates_count")
            or response.headers.get(
                "X-RERA-Found-Documents",
                "",
            ),
        "downloaded":
            report.get("downloaded_count")
            or response.headers.get(
                "X-RERA-Downloaded-Documents",
                "",
            ),
        "failed":
            report.get("failed_count")
            or response.headers.get(
                "X-RERA-Failed-Documents",
                "",
            ),
        "not_applicable":
            report.get("not_applicable_count", 0),
        "not_downloaded":
            report.get("not_downloaded_count", 0),
    }

    st.session_state[
        "rera_all_documents_report"
    ] = report

def rera_document_label(document):
    return (
        f"{document.get('section') or 'Project Details'} | "
        f"{document.get('label') or document.get('filename')}"
    )


def load_rera_documents(
    api_base,
    selected_project,
    headless,
):
    response = requests.post(
        f"{api_base.rstrip('/')}/api/rera/documents",
        json={
            "registrationNumber":
                selected_project.get(
                    "registration_number",
                    "",
                ),
            "projectName":
                selected_project.get(
                    "project_name",
                    "",
                ),
            "promoterName":
                selected_project.get(
                    "promoter_name",
                    "",
                ),
            "headless": bool(headless),
        },
        timeout=900,
    )

    try:
        result = response.json()

    except ValueError:
        raise RuntimeError(
            response.text
            or "Invalid RERA document response."
        )

    if not response.ok or not result.get("success"):
        raise RuntimeError(
            result.get("detail")
            or result.get("error")
            or result.get("message")
            or "Unable to load RERA documents."
        )

    st.session_state[
        "rera_documents"
    ] = result.get(
        "documents",
        [],
    )

    st.session_state[
        "rera_selected_documents"
    ] = []


def download_selected_rera_documents(
    api_base,
    selected_project,
    selected_documents,
    headless,
):
    response = requests.post(
        f"{api_base.rstrip('/')}/api/rera/documents/download",
        json={
            "registrationNumber":
                selected_project.get(
                    "registration_number",
                    "",
                ),
            "projectName":
                selected_project.get(
                    "project_name",
                    "",
                ),
            "promoterName":
                selected_project.get(
                    "promoter_name",
                    "",
                ),
            "headless": bool(headless),
            "documents": selected_documents,
        },
        timeout=900,
    )

    if not response.ok:
        try:
            error_data = response.json()

            error_message = (
                error_data.get("detail")
                or error_data.get("error")
                or error_data.get("message")
                or response.text
            )

        except ValueError:
            error_message = response.text

        raise RuntimeError(
            error_message
            or "RERA document ZIP download failed."
        )

    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    if (
        "zip" not in content_type
        and not response.content.startswith(b"PK")
    ):
        raise RuntimeError(
            "Server did not return a valid ZIP file."
        )

    filename = "rera_documents.zip"

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
        filename = match.group(1).strip('"')

    st.session_state[
        "rera_documents_zip_bytes"
    ] = response.content

    st.session_state[
        "rera_documents_zip_filename"
    ] = filename


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
        "rera_pdf_bytes": None,
        "rera_pdf_filename": None,
        "rera_documents": [],
        "rera_selected_documents": [],
        "rera_all_documents_zip_bytes": None,
        "rera_all_documents_zip_filename": None,
        "rera_all_documents_summary": {},
        "rera_all_documents_report": {},
        "rera_search_future": None,
        "rera_search_request_id": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    st.markdown("## Karnataka RERA Project Search")

    promoter_tab, project_tab, registration_tab = st.tabs(
        [
            "Promoter Name",
            "Project Name",
            "Registration Number",
        ]
    )

    # -----------------------------------------------------
    # SEARCH FUNCTION
    # -----------------------------------------------------

    def run_search(search_type, query):
        query = query.strip()

        if len(query) < 2:
            st.error(
                "Enter at least two characters."
            )
            return

        clear_rera_pdf()
        st.session_state["rera_search_result"] = None

        request_id = uuid.uuid4().hex
        payload = {
                "searchType": search_type,
                "query": query,
                "headless": bool(headless),
                "maxPages": 25,
                "maxResults": 500,
                "requestId": request_id,
        }
        st.session_state["rera_search_request_id"] = request_id
        st.session_state["rera_search_future"] = RERA_EXECUTOR.submit(
            execute_rera_search, api_base, payload
        )

    # -----------------------------------------------------
    # PROMOTER TAB
    # -----------------------------------------------------

    with promoter_tab:
        promoter_query = st.text_input(
            "Enter Promoter Name",
            key="rera_promoter_query",
            placeholder="Example: SRIVARI INFRASTRUCTURES",
        )

        promoter_search = st.button(
            "Search",
            key="rera_promoter_search",
            type="primary",
            width="stretch",
            disabled=not promoter_query.strip(),
        )

        if promoter_search:
            try:
                with st.spinner(
                    "Searching Karnataka RERA portal..."
                ):
                    run_search(
                        "promoter",
                        promoter_query,
                    )

            except requests.exceptions.ConnectionError:
                st.error(
                    "FastAPI server is not running."
                )

            except requests.exceptions.Timeout:
                st.error(
                    "Karnataka RERA portal took too long to respond."
                )

            except Exception as error:
                st.error(
                    f"RERA search failed: {error}"
                )

    # -----------------------------------------------------
    # PROJECT TAB
    # -----------------------------------------------------

    with project_tab:
        project_query = st.text_input(
            "Enter Project Name",
            key="rera_project_query",
            placeholder="Example: SATTVA",
        )

        project_search = st.button(
            "Search",
            key="rera_project_search",
            type="primary",
            width="stretch",
            disabled=not project_query.strip(),
        )

        if project_search:
            try:
                with st.spinner(
                    "Searching Karnataka RERA portal..."
                ):
                    run_search(
                        "project",
                        project_query,
                    )

            except requests.exceptions.ConnectionError:
                st.error(
                    "FastAPI server is not running."
                )

            except requests.exceptions.Timeout:
                st.error(
                    "Karnataka RERA portal took too long to respond."
                )

            except Exception as error:
                st.error(
                    f"RERA search failed: {error}"
                )

    # -----------------------------------------------------
    # REGISTRATION NUMBER TAB
    # -----------------------------------------------------

    with registration_tab:
        registration_query = st.text_input(
            "Enter Registration Number",
            key="rera_registration_query",
            placeholder="Example: PRM/KA/RERA/1251/310/PR/171014/000064",
        )

        registration_search = st.button(
            "Search",
            key="rera_registration_search",
            type="primary",
            width="stretch",
            disabled=not registration_query.strip(),
        )

        if registration_search:
            try:
                run_search(
                    "registration",
                    registration_query,
                )
            except Exception as error:
                st.error(
                    f"RERA search failed: {error}"
                )

    @st.fragment(run_every="1s")
    def render_search_progress():
        future = st.session_state.get("rera_search_future")
        if future is None:
            return

        if future.done():
            st.session_state["rera_search_future"] = None
            st.session_state["rera_search_request_id"] = None
            try:
                st.session_state["rera_search_result"] = future.result()
                st.session_state["rera_selected_index"] = 0
            except Exception as error:
                st.session_state["rera_search_error"] = str(error)
            st.rerun()

        st.info("RERA automation is running.")
        if st.button(
            "Cancel RERA Automation",
            key="rera_cancel_search",
            type="secondary",
            width="stretch",
        ):
            request_id = st.session_state.get("rera_search_request_id")
            if request_id:
                try:
                    requests.post(
                        f"{api_base.rstrip('/')}/api/rera/cancel/{request_id}",
                        timeout=10,
                    )
                except requests.RequestException:
                    pass
            future.cancel()
            st.session_state["rera_search_future"] = None
            st.session_state["rera_search_request_id"] = None
            st.warning("RERA automation was cancelled.")
            st.rerun()

    render_search_progress()

    search_error = st.session_state.pop("rera_search_error", None)
    if search_error:
        st.error(f"RERA search failed: {search_error}")

    # =====================================================
    # SEARCH RESULTS
    # =====================================================

    result = st.session_state.get(
        "rera_search_result"
    )
    selected_project = None

    if result:
        results = result.get(
            "results",
            [],
        )

        if not results:
            st.info(
                "No matching RERA projects were found."
            )

        elif len(results) == 1:
            row = results[0]
            selected_project = row

        else:
            selected_index = st.selectbox(
                "Select the required project",
                options=range(len(results)),
                format_func=lambda index:
                    rera_project_label(
                        results[index],
                        index,
                    ),
                key="rera_selected_index",
                on_change=clear_rera_pdf,
            )

            selected_project = results[selected_index]
    if selected_project is None:
        return

    st.divider()
    st.subheader("Download All RERA Uploaded Documents")
    
    download_all_docs = st.button(
        "Open Details and Download All RERA Documents as ZIP",
        key="rera_download_all_project_documents",
        width="stretch",
        type="primary",
    )
    
    if download_all_docs:
        try:
            with st.spinner(
                "Downloading all available RERA documents..."
            ):
                download_all_rera_documents(
                    api_base,
                    selected_project,
                    headless,
                )
    
        except Exception as error:
            st.error(
                f"RERA document download failed: {error}"
            )
    
    zip_bytes = st.session_state.get(
        "rera_all_documents_zip_bytes"
    )
    
    zip_filename = st.session_state.get(
        "rera_all_documents_zip_filename",
        "rera_all_documents.zip",
    )
    
    if zip_bytes:
        st.success(
            "All available RERA documents were downloaded into one ZIP file."
        )
    
        st.download_button(
            label="Download All RERA Documents ZIP",
            data=zip_bytes,
            file_name=zip_filename,
            mime="application/zip",
            width="stretch",
            type="primary",
            key="rera_all_documents_zip_download",
        )

    report = st.session_state.get(
        "rera_all_documents_report",
        {},
    )

    if report:
        not_applicable = report.get(
            "not_applicable",
            [],
        )

        failed = report.get(
            "failed",
            [],
        )

        if not_applicable:
            st.warning(
                f"{len(not_applicable)} document(s) were marked as NOT APPLICABLE and were not downloaded."
            )

            st.dataframe(
                [
                    {
                        "Status": "NOT APPLICABLE",
                        "Section": item.get("section", ""),
                        "Document": item.get("label", ""),
                        "Reason": item.get("reason", ""),
                    }
                    for item in not_applicable
                ],
                width="stretch",
                hide_index=True,
            )

        if failed:
            st.error(
                f"{len(failed)} document link(s) were found but could not be downloaded."
            )

            st.dataframe(
                [
                    {
                        "Status": "FAILED",
                        "Section": item.get("section", ""),
                        "Document": item.get("label", ""),
                        "File Name": item.get("filename", ""),
                        "Reason": item.get("error", ""),
                    }
                    for item in failed
                ],
                width="stretch",
                hide_index=True,
            )

        if not not_applicable and not failed:
            st.success(
                "No NOT APPLICABLE or failed document links were detected."
            )

    # =====================================================
    # PDF DOWNLOAD
    # =====================================================

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

        st.download_button(
            label="Download Complete RERA Project PDF",
            data=pdf_bytes,
            file_name=pdf_filename,
            mime="application/pdf",
            width="stretch",
            type="primary",
            key="rera_download_pdf",
        )
