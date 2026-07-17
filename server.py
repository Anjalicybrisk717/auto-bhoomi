import os
import re
import time
import json
import base64
import mimetypes
import uvicorn
import unicodedata
import zipfile
import threading
from typing import Any, Dict, List
from urllib.parse import unquote, urljoin
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BHOOMI_URL = "https://landrecords.karnataka.gov.in/Service2/"
MR_URL = "https://landrecords.karnataka.gov.in/Service11/MR_MutationExtract.aspx"
REVENUE_MAP_URL = "https://landrecords.karnataka.gov.in/service3/"
SURVEY_SKETCH_URL = "https://rdservices.karnataka.gov.in/service84/"
AKARBAND_URL = "https://bhoomojini.karnataka.gov.in/service39/"
RERA_URL = "https://rera.karnataka.gov.in/viewAllCompletedProjects"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RERA_DOWNLOAD_DIR = os.path.join(BASE_DIR, "rera_downloads")
RERA_DEBUG_DIR = os.path.join(BASE_DIR, "rera_debug")
RERA_DOCUMENT_ZIP_DIR = os.path.join(BASE_DIR, "rera_document_zips")

os.makedirs(RERA_DOWNLOAD_DIR, exist_ok=True)
os.makedirs(RERA_DEBUG_DIR, exist_ok=True)
os.makedirs(RERA_DOCUMENT_ZIP_DIR, exist_ok=True)

with open(
    os.path.join(BASE_DIR, "bhoomi-master-bilingual.json"),
    "r",
    encoding="utf-8",
) as f:
    bilingual_master = json.load(f)

app = FastAPI(title="Bhoomi Automation API")


@app.get("/health")
def health_check():
    return {"status": "ok"}


class BhoomiRequest(BaseModel):
    district: str
    taluk: str
    hobli: str
    village: str
    surveyNumber: str = ""
    hissa: str = ""
    headless: bool = False


class MRDownloadRequest(BhoomiRequest):
    rowIndex: int


class RevenueMapRequest(BaseModel):
    district: str
    taluk: str
    hobli: str
    village: str
    mapType: str = "All"
    headless: bool = False


class SurveySketchRequest(BaseModel):
    district: str
    taluk: str
    hobli: str
    village: str
    surveyNumber: str
    surnoc: str = ""
    hissa: str = ""
    headless: bool = False


class AkarbandRequest(BaseModel):
    district: str
    taluk: str
    hobli: str
    village: str
    surveyNumber: str
    surnoc: str = ""
    hissa: str = ""
    headless: bool = False


class AkarbandOptionsRequest(AkarbandRequest):
    surveyNumber: str = ""

class ReraSearchRequest(BaseModel):
    searchType: str
    query: str
    headless: bool = True
    maxPages: int = 25
    maxResults: int = 500
    requestId: str = ""


RERA_CANCEL_EVENTS = {}
RERA_CANCEL_LOCK = threading.Lock()


def rera_cancel_event(request_id):
    with RERA_CANCEL_LOCK:
        return RERA_CANCEL_EVENTS.get(request_id)


def rera_raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("RERA automation was cancelled by the user.")


class ReraProjectPdfRequest(BaseModel):
    registrationNumber: str = ""
    projectName: str = ""
    promoterName: str = ""
    headless: bool = True


class ReraDocumentsRequest(BaseModel):
    registrationNumber: str = ""
    projectName: str = ""
    promoterName: str = ""
    headless: bool = True


class ReraDocumentsZipRequest(ReraDocumentsRequest):
    documents: List[Dict[str, Any]] = Field(default_factory=list)


class ReraAllDocumentsZipRequest(BaseModel):
    registrationNumber: str = ""
    projectName: str = ""
    promoterName: str = ""
    headless: bool = True


def safe_filename(value):
    return (
        str(value)
        .replace("/", "-")
        .replace("\\", "-")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )


def normalize_text(text: str) -> str:
    if text is None:
        return ""

    text = unicodedata.normalize("NFKC", str(text))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip().lower()


def select_dropdown_by_text_contains(page, index, wanted_text, allow_partial=True):
    wanted = normalize_text(wanted_text)

    result = page.evaluate(
        """
        ({index, wanted, allowPartial}) => {

            function normalize(s){
                if(!s) return "";

                return s
                    .normalize("NFKC")
                    .replace(/\\u00a0/g," ")
                    .replace(/\\s+/g," ")
                    .trim()
                    .toLowerCase();
            }

            const selects = Array.from(document.querySelectorAll("select"))
                .filter(s=>{
                    const r=s.getBoundingClientRect();

                    return (
                        r.width>0 &&
                        r.height>0 &&
                        !s.disabled
                    );
                });

            if(index>=selects.length){
                return {
                    success:false,
                    message:"Dropdown not found",
                    count:selects.length
                };
            }

            const ddl=selects[index];

            const options=Array.from(ddl.options);

            for(const option of options){
                const txt=normalize(option.textContent);

                if(txt===wanted){

                    ddl.value=option.value;

                    ddl.dispatchEvent(new Event("input",{bubbles:true}));
                    ddl.dispatchEvent(new Event("change",{bubbles:true}));

                    return{
                        success:true,
                        selected:option.textContent.trim()
                    };
                }
            }

            if(allowPartial && !/^\\d+$/.test(wanted)){
                for(const option of options){
                    const txt=normalize(option.textContent);

                    if(!txt || /^\\d+$/.test(txt)){
                        continue;
                    }

                    if(txt.includes(wanted) || wanted.includes(txt)){

                        ddl.value=option.value;

                        ddl.dispatchEvent(new Event("input",{bubbles:true}));
                        ddl.dispatchEvent(new Event("change",{bubbles:true}));

                        return{
                            success:true,
                            selected:option.textContent.trim()
                        };
                    }
                }
            }

            return{
                success:false,
                wanted:wanted,
                options:options.map(o=>o.textContent.trim())
            };

        }
        """,
        {
            "index": index,
            "wanted": wanted,
            "allowPartial": allow_partial,
        },
    )

    if not result["success"]:
        raise Exception(result)

    page.wait_for_timeout(2000)
    return result.get("selected")


def click_button_by_text(page, button_text):
    clicked = page.evaluate(
        """
        (buttonText) => {
            const wanted = buttonText.trim().toLowerCase();

            const items = Array.from(
                document.querySelectorAll("input, button, a")
            );

            const btn = items.find(el => {
                const text = (el.innerText || el.value || "")
                    .trim()
                    .toLowerCase();

                return text === wanted || text.includes(wanted);
            });

            if (!btn) return false;

            btn.removeAttribute("disabled");
            btn.disabled = false;
            btn.click();
            return true;
        }
        """,
        button_text,
    )

    if not clicked:
        raise Exception(f"Button not found: {button_text}")

    page.wait_for_timeout(8000)


def click_fetch_details(page):
    click_button_by_text(page, "Fetch Details")


def require_filled_fields(data, fields, context):
    missing = [
        field
        for field in fields
        if not str(data.get(field, "")).strip()
    ]

    if missing:
        raise Exception(f"{context} requires: {', '.join(missing)}")


def wait_for_visible_select_ready(page, index, min_options=1):
    page.wait_for_function(
        """
        ({index, minOptions}) => {
            const selects = Array.from(document.querySelectorAll("select"))
                .filter(s => {
                    const r = s.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && !s.disabled;
                });

            const ddl = selects[index];
            return !!ddl && ddl.options.length >= minOptions;
        }
        """,
        arg={"index": index, "minOptions": min_options},
        timeout=30000,
    )


def wait_for_visible_select_filled(page, index, field_name):
    filled = page.wait_for_function(
        """
        (index) => {
            const selects = Array.from(document.querySelectorAll("select"))
                .filter(s => {
                    const r = s.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && !s.disabled;
                });

            const ddl = selects[index];
            if (!ddl) return false;

            const selected = ddl.options[ddl.selectedIndex];
            const text = (selected ? selected.textContent : "")
                .normalize("NFKC")
                .replace(/\\u00a0/g, " ")
                .replace(/\\s+/g, " ")
                .trim()
                .toLowerCase();

            if (!ddl.value && !text) return false;

            return (
                text &&
                !text.includes("select") &&
                !text.startsWith("--")
            );
        }
        """,
        arg=index,
        timeout=30000,
    )

    if not filled:
        raise Exception(f"{field_name} was not filled")


def select_cascading_dropdown(
    page,
    index,
    value,
    field_name,
    next_index=None,
    allow_partial=True,
):
    wait_for_visible_select_ready(page, index, min_options=2)
    selected = select_dropdown_by_text_contains(
        page,
        index,
        value,
        allow_partial=allow_partial,
    )
    wait_for_visible_select_filled(page, index, field_name)

    if next_index is not None:
        wait_for_visible_select_ready(page, next_index, min_options=2)

    return selected


def click_akarband_fetch_button(page):
    clicked = page.evaluate(
        """
        () => {
            function visible(el) {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }

            const items = Array.from(document.querySelectorAll("button, input, a"))
                .filter(el => visible(el) && !el.disabled);

            const btn = items.find(el => {
                const text = (el.innerText || el.value || "")
                    .normalize("NFKC")
                    .replace(/\\u00a0/g, " ")
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLowerCase();

                return (
                    text === "fetch" ||
                    text === "fetch details" ||
                    text.includes("fetch details") ||
                    text.includes("fetch")
                );
            });

            if (!btn) return false;

            btn.click();
            return true;
        }
        """
    )

    if not clicked:
        raise Exception("Akarband Fetch button not found")

    page.wait_for_timeout(8000)


def fill_rtc_fields(page, data):
    page.wait_for_selector("select", timeout=30000)

    select_dropdown_by_text_contains(page, 0, data["district"])
    page.wait_for_timeout(2000)

    select_dropdown_by_text_contains(page, 1, data["taluk"])
    page.wait_for_timeout(2000)

    select_dropdown_by_text_contains(page, 2, data["hobli"])
    page.wait_for_timeout(2000)

    select_dropdown_by_text_contains(page, 3, data["village"])
    page.wait_for_timeout(2000)

    survey_input = page.locator(
        'input[placeholder="Survey Number"], input[type="text"]'
    ).first

    survey_input.wait_for(state="visible", timeout=30000)
    survey_input.fill(str(data["surveyNumber"]))

    survey_input.evaluate("""
        (el) => {
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            el.dispatchEvent(new Event("blur", { bubbles: true }));
        }
        """)

    page.wait_for_timeout(1000)

    go_btn = page.locator(
        'input[value="Go"], button:has-text("Go"), a:has-text("Go")'
    ).first

    go_btn.wait_for(state="visible", timeout=30000)
    go_btn.click(force=True)

    page.wait_for_timeout(8000)


def fill_mr_fields(page, data):
    page.wait_for_selector("select", timeout=30000)

    select_dropdown_by_text_contains(page, 0, data["district"])
    select_dropdown_by_text_contains(page, 1, data["taluk"])
    select_dropdown_by_text_contains(page, 2, data["hobli"])
    select_dropdown_by_text_contains(page, 3, data["village"])

    survey_input = page.locator(
        'input[placeholder="Survey Number"], '
        'input[placeholder="Survey No."], '
        'input[type="text"]'
    ).first

    survey_input.wait_for(state="visible", timeout=30000)
    survey_input.fill(str(data["surveyNumber"]))

    survey_input.evaluate("""
        (el) => {
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            el.dispatchEvent(new Event("blur", { bubbles: true }));
        }
        """)

    page.wait_for_timeout(3000)


def select_first_option_by_index(page, index):
    dropdown = page.locator("select").nth(index)
    dropdown.wait_for(state="visible", timeout=30000)

    is_disabled = dropdown.evaluate("(ddl) => ddl.disabled")

    if is_disabled:
        return dropdown.evaluate("""
            (ddl) => {
                const selected = ddl.options[ddl.selectedIndex];
                return selected ? selected.textContent.trim() : null;
            }
            """)

    options = dropdown.locator("option").all()

    for option in options:
        text = option.inner_text().strip()

        if text and "select" not in text.lower():
            dropdown.select_option(label=text)
            page.wait_for_timeout(2500)
            return text

    return None


def get_visible_select_options(page, index, include_placeholder=False):
    result = page.evaluate(
        """
        ({index, includePlaceholder}) => {
            const selects = Array.from(document.querySelectorAll("select"))
                .filter(s => {
                    const r = s.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && !s.disabled;
                });

            const ddl = selects[index];

            if (!ddl) {
                return {
                    success: false,
                    reason: "Dropdown not found",
                    count: selects.length
                };
            }

            const options = Array.from(ddl.options)
                .map(option => ({
                    label: (option.textContent || "").trim(),
                    value: option.value
                }))
                .filter(option => {
                    if (!option.label) return false;
                    if (includePlaceholder) return true;
                    const label = option.label.toLowerCase();
                    return (
                        !label.includes("select") &&
                        !option.label.includes("ಆಯ್ಕೆ") &&
                        !option.label.startsWith("--")
                    );
                });

            return {
                success: true,
                options
            };
        }
        """,
        {"index": index, "includePlaceholder": include_placeholder},
    )

    if not result.get("success"):
        raise Exception(result)

    return result.get("options", [])


def save_page_screenshot_as_pdf(browser, source_page, pdf_path):
    png_bytes = source_page.screenshot(full_page=True)
    encoded = base64.b64encode(png_bytes).decode("utf-8")

    pdf_page = browser.new_page()

    pdf_page.set_content(
        f"""
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{
                    size: A4 landscape;
                    margin: 5mm;
                }}
                body {{
                    margin: 0;
                    padding: 0;
                    background: white;
                }}
                img {{
                    width: 100%;
                    height: auto;
                    display: block;
                }}
            </style>
        </head>
        <body>
            <img src="data:image/png;base64,{encoded}">
        </body>
        </html>
        """,
        wait_until="domcontentloaded",
    )

    pdf_page.pdf(
        path=pdf_path,
        format="A4",
        landscape=True,
        print_background=True,
        margin={
            "top": "5mm",
            "right": "5mm",
            "bottom": "5mm",
            "left": "5mm",
        },
    )

    pdf_page.close()


def save_rtc_pdf(page, data):
    os.makedirs("rtc_downloads", exist_ok=True)

    pdf_path = os.path.join(
        "rtc_downloads",
        f"RTC_{safe_filename(data['district'])}_"
        f"{safe_filename(data['taluk'])}_"
        f"{safe_filename(data['village'])}_"
        f"{safe_filename(data['surveyNumber'])}.pdf",
    )

    page.wait_for_function(
        """
        () => {
            const text = document.body.innerText.toLowerCase();
            return (
                text.includes("owner") ||
                text.includes("extent") ||
                text.includes("rtc documents") ||
                text.includes("land id")
            );
        }
        """,
        timeout=60000,
    )

    page.wait_for_timeout(3000)

    view_clicked = page.evaluate("""
        () => {
            const items = Array.from(document.querySelectorAll("input, button, a"));

            const btn = items.find(el => {
                const text = (el.innerText || el.value || "")
                    .trim()
                    .toLowerCase();

                return text === "view" || text.includes("view");
            });

            if (!btn) return false;

            btn.removeAttribute("disabled");
            btn.disabled = false;
            btn.click();
            return true;
        }
        """)

    if not view_clicked:
        page.screenshot(path="rtc-view-button-not-found.png", full_page=True)
        raise Exception("RTC View button not found after Fetch Details")

    page.wait_for_timeout(7000)

    pages = page.context.pages
    preview_page = pages[-1] if len(pages) > 1 else page

    preview_page.wait_for_timeout(5000)

    try:
        preview_page.pdf(
            path=pdf_path,
            format="A4",
            landscape=True,
            print_background=True,
        )
    except Exception:
        save_page_screenshot_as_pdf(
            page.context.browser,
            preview_page,
            pdf_path,
        )

    return pdf_path


def fetch_rtc_with_playwright(data):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
        )

        page = browser.new_page()

        try:
            page.goto(BHOOMI_URL, wait_until="domcontentloaded", timeout=60000)

            fill_rtc_fields(page, data)

            selected_surnoc = select_first_option_by_index(page, 4)
            selected_hissa = select_first_option_by_index(page, 5)
            selected_period = select_first_option_by_index(page, 6)

            click_fetch_details(page)

            page.wait_for_function(
                """
                () => {
                    const text = document.body.innerText.toLowerCase();
                    return (
                        text.includes("owner") ||
                        text.includes("extent") ||
                        text.includes("rtc documents") ||
                        text.includes("land id")
                    );
                }
                """,
                timeout=60000,
            )

            pdf_path = save_rtc_pdf(page, data)

            return {
                "success": True,
                "type": "RTC",
                "engine": "playwright",
                "selected_surnoc": selected_surnoc,
                "selected_hissa": selected_hissa,
                "selected_period": selected_period,
                "pdf": pdf_path,
            }

        except Exception as e:
            page.screenshot(path="rtc-error.png", full_page=True)
            raise Exception(f"RTC failed: {str(e)}")

        finally:
            browser.close()


def extract_mutation_rows(page):
    return page.evaluate("""
        () => {
            const rows = [];
            const links = Array.from(document.querySelectorAll("a"))
                .filter(a => a.innerText.trim().toLowerCase() === "select");

            for (const link of links) {
                const tr = link.closest("tr");
                if (!tr) continue;

                const cells = Array.from(tr.querySelectorAll("td"))
                    .map(td => td.innerText.trim());

                if (cells.length >= 8) {
                    rows.push({
                        index: rows.length,
                        survey_no: cells[1] || "",
                        transaction_year: cells[2] || "",
                        transaction_no: cells[3] || "",
                        mr_number: cells[4] || "",
                        mutation_type: cells[5] || "",
                        acquisition_type: cells[6] || "",
                        approved_date: cells[7] || ""
                    });
                }
            }

            return rows;
        }
        """)


def fetch_mr_rows(data):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
        )

        page = browser.new_page()

        try:
            page.goto(MR_URL, wait_until="domcontentloaded", timeout=60000)

            fill_mr_fields(page, data)
            click_fetch_details(page)

            page.screenshot(path="mr-after-fetch.png", full_page=True)

            mutation_rows = extract_mutation_rows(page)

            return {
                "success": True,
                "type": "MR_TABLE",
                "rows": mutation_rows,
            }

        except Exception as e:
            page.screenshot(path="mr-search-error.png", full_page=True)
            raise Exception(f"MR search failed: {str(e)}")

        finally:
            browser.close()


def download_selected_mr(data):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
        )

        page = browser.new_page()

        try:
            page.goto(MR_URL, wait_until="domcontentloaded", timeout=60000)

            fill_mr_fields(page, data)
            click_fetch_details(page)

            page.wait_for_timeout(5000)

            row_index = int(data["rowIndex"])

            clicked = page.evaluate(
                """
                (rowIndex) => {
                    const links = Array.from(document.querySelectorAll("a"))
                        .filter(a => a.innerText.trim().toLowerCase() === "select");

                    if (rowIndex < 0 || rowIndex >= links.length) {
                        return false;
                    }

                    links[rowIndex].click();
                    return true;
                }
                """,
                row_index,
            )

            if not clicked:
                raise Exception("Could not click selected mutation row")

            page.wait_for_timeout(5000)

            preview_btn = page.locator(
                'input[value="Preview"], '
                'button:has-text("Preview"), '
                'a:has-text("Preview")'
            ).first

            preview_btn.wait_for(state="visible", timeout=30000)

            os.makedirs("mr_downloads", exist_ok=True)

            pdf_path = os.path.join(
                "mr_downloads",
                f"MR_{safe_filename(data['district'])}_"
                f"{safe_filename(data['taluk'])}_"
                f"{safe_filename(data['village'])}_"
                f"{safe_filename(data['surveyNumber'])}_"
                f"ROW_{row_index + 1}.pdf",
            )

            try:
                with page.context.expect_page(timeout=15000) as new_page_info:
                    preview_btn.click(force=True)

                preview_page = new_page_info.value
                preview_page.wait_for_load_state("domcontentloaded", timeout=60000)
                preview_page.wait_for_timeout(12000)

            except Exception:
                preview_btn.click(force=True)
                page.wait_for_timeout(12000)
                preview_page = page

            preview_page.screenshot(path="mr-preview-debug.png", full_page=True)

            preview_text = preview_page.locator("body").inner_text().strip()

            if preview_text.lower() == "view only" or not preview_text:
                return {
                    "success": False,
                    "message": "No data found. Preview shows only View Only.",
                    "pdf": None,
                }

            save_page_screenshot_as_pdf(browser, preview_page, pdf_path)

            return {
                "success": True,
                "type": "MR_SELECTED",
                "pdf": pdf_path,
            }

        except Exception as e:
            page.screenshot(path="mr-download-error.png", full_page=True)
            raise Exception(f"MR selected download failed: {str(e)}")

        finally:
            browser.close()


def fill_revenue_map_fields(page, data):
    page.wait_for_selector("select", timeout=30000)

    select_dropdown_by_text_contains(page, 0, data["district"])
    page.wait_for_timeout(1500)

    select_dropdown_by_text_contains(page, 1, data["taluk"])
    page.wait_for_timeout(1500)

    select_dropdown_by_text_contains(page, 2, data["hobli"])
    page.wait_for_timeout(1500)

    if data.get("mapType"):
        select_dropdown_by_text_contains(page, 3, data["mapType"])
        page.wait_for_timeout(1500)


def get_revenue_map_file_element(page, village_name, cell_index):
    handle = page.evaluate_handle(
        """
        ({villageName, cellIndex}) => {
            const normalize = (value) =>
                (value || "").trim().toLowerCase().replace(/\\s+/g, " ");

            const wanted = normalize(villageName);
            const rows = Array.from(document.querySelectorAll("tr"));

            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll("td"));

                if (cells.length <= cellIndex) continue;

                const village = normalize(cells[3].innerText);

                if (village !== wanted) continue;

                const cell = cells[cellIndex];
                return (
                    cell.querySelector("a") ||
                    cell.querySelector("input[type='image']") ||
                    cell.querySelector("input[type='button']") ||
                    cell.querySelector("button") ||
                    cell.querySelector("img") ||
                    cell
                );
            }

            return null;
        }
        """,
        {
            "villageName": village_name,
            "cellIndex": cell_index,
        },
    )

    return handle.as_element()


def save_revenue_map_result_page(context, result_page, download_path):
    result_page.wait_for_load_state("domcontentloaded", timeout=30000)
    result_page.wait_for_timeout(2000)

    result_url = result_page.url

    if result_url and result_url.startswith("http"):
        response = context.request.get(result_url)
        body = response.body()
        content_type = response.headers.get("content-type", "").lower()

        if response.ok and ("pdf" in content_type or body.startswith(b"%PDF")):
            with open(download_path, "wb") as f:
                f.write(body)
            return result_url

    result_page.pdf(
        path=download_path,
        format="A4",
        print_background=True,
    )
    return result_url


def click_and_save_revenue_map_pdf(context, page, pdf_element, download_path):
    pages_before_click = list(context.pages)

    try:
        with page.expect_download(timeout=10000) as download_info:
            pdf_element.click(force=True)

        download = download_info.value
        download.save_as(download_path)
        return download.url

    except PlaywrightTimeoutError:
        page.wait_for_timeout(3000)

        new_pages = [
            open_page
            for open_page in context.pages
            if open_page not in pages_before_click
        ]

        if new_pages:
            return save_revenue_map_result_page(context, new_pages[-1], download_path)

        if page.url != REVENUE_MAP_URL:
            return save_revenue_map_result_page(context, page, download_path)

        embedded_pdf_count = page.locator("embed, iframe, object").count()

        if embedded_pdf_count:
            return save_revenue_map_result_page(context, page, download_path)

        with context.expect_page(timeout=10000) as new_page_info:
            pdf_element.click(force=True)

        result_page = new_page_info.value
        return save_revenue_map_result_page(context, result_page, download_path)


def fetch_revenue_map(data):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            page.goto(REVENUE_MAP_URL, wait_until="domcontentloaded", timeout=60000)

            fill_revenue_map_fields(page, data)

            village_input = page.locator(
                'input[placeholder*="Village"], input[type="text"]'
            ).first

            village_input.wait_for(state="visible", timeout=30000)
            village_input.fill(str(data["village"]))

            page.wait_for_timeout(1500)

            search_btn = page.locator(
                'input[value="Search"], button:has-text("Search"), a:has-text("Search")'
            ).first

            search_btn.wait_for(state="visible", timeout=30000)
            search_btn.click(force=True)

            page.wait_for_timeout(5000)

            os.makedirs("revenue_maps", exist_ok=True)

            base_name = (
                f"REVENUE_MAP_{safe_filename(data['district'])}_"
                f"{safe_filename(data['taluk'])}_"
                f"{safe_filename(data['hobli'])}_"
                f"{safe_filename(data['village'])}"
            )

            pdf_element = get_revenue_map_file_element(
                page,
                data["village"],
                4,
            )

            if not pdf_element:
                page.screenshot(
                    path="revenue-map-no-pdf-icon.png",
                    full_page=True,
                )
                raise Exception("No PDF file icon available for selected village.")

            download_path = os.path.join(
                "revenue_maps",
                f"{base_name}.pdf",
            )

            source_url = click_and_save_revenue_map_pdf(
                context,
                page,
                pdf_element,
                download_path,
            )

            return {
                "success": True,
                "type": "REVENUE_MAP",
                "file_type": "PDF",
                "file": download_path,
                "pdf": download_path,
                "kmz": None,
                "source_url": source_url,
            }

        except Exception as e:
            page.screenshot(path="revenue-map-error.png", full_page=True)
            raise Exception(f"Revenue Map fetch failed: {str(e)}")

        finally:
            browser.close()


def select_visible_dropdown(page, index, value):
    result = page.evaluate(
        """
        ({index, value}) => {
            const normalize = (s) =>
                (s || "").trim().toLowerCase().replace(/\\s+/g, " ");

            const wanted = normalize(value);

            const selects = Array.from(document.querySelectorAll("select"))
                .filter(s => {
                    const r = s.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && !s.disabled;
                });

            const ddl = selects[index];

            if (!ddl) {
                return {
                    success: false,
                    reason: "Dropdown not found",
                    count: selects.length
                };
            }

            const options = Array.from(ddl.options);

            for (const option of options) {
                const text = normalize(option.textContent);

                if (
                    text &&
                    !text.includes("select") &&
                    (
                        text === wanted ||
                        text.includes(wanted) ||
                        wanted.includes(text)
                    )
                ) {
                    ddl.value = option.value;
                    ddl.dispatchEvent(new Event("input", { bubbles: true }));
                    ddl.dispatchEvent(new Event("change", { bubbles: true }));

                    return {
                        success: true,
                        selected: option.textContent.trim()
                    };
                }
            }

            return {
                success: false,
                reason: "Option not found",
                options: options.map(o => o.textContent.trim())
            };
        }
        """,
        {"index": index, "value": value},
    )

    if not result.get("success"):
        raise Exception(
            f"Could not select '{value}' in visible dropdown {index}. Details: {result}"
        )

    print(f"Selected visible dropdown {index}: {result.get('selected')}")
    page.wait_for_timeout(2500)


def fetch_survey_sketch(data):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
        )

        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1400},
        )
        page = context.new_page()

        try:
            page.goto(
                SURVEY_SKETCH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(4000)

            checkbox = page.locator('input[type="checkbox"]').first
            checkbox.wait_for(state="visible", timeout=30000)

            if not checkbox.is_checked():
                checkbox.check(force=True)

            page.wait_for_timeout(3000)

            def select_dropdown(index, value):
                result = page.evaluate(
                    """
                    ({index, value}) => {
                        const normalize = s =>
                            (s || "").trim().toLowerCase().replace(/\\s+/g, " ");

                        const wanted = normalize(value);

                        const selects = Array.from(document.querySelectorAll("select"))
                            .filter(s => {
                                const r = s.getBoundingClientRect();
                                return r.width > 0 && r.height > 0 && !s.disabled;
                            });

                        const ddl = selects[index];
                        if (!ddl) return {success:false, reason:"Dropdown not found", count:selects.length};

                        const options = Array.from(ddl.options);

                        const option = options.find(o => {
                            const text = normalize(o.textContent);
                            return text &&
                                   !text.includes("select") &&
                                   (
                                       text === wanted ||
                                       text.includes(wanted) ||
                                       wanted.includes(text)
                                   );
                        });

                        if (!option) {
                            return {
                                success:false,
                                reason:"Option not found",
                                wanted:value,
                                options: options.map(o => o.textContent.trim())
                            };
                        }

                        ddl.value = option.value;
                        ddl.dispatchEvent(new Event("input", {bubbles:true}));
                        ddl.dispatchEvent(new Event("change", {bubbles:true}));
                        ddl.dispatchEvent(new Event("blur", {bubbles:true}));

                        return {success:true, selected:option.textContent.trim()};
                    }
                    """,
                    {"index": index, "value": value},
                )

                if not result.get("success"):
                    raise Exception(result)

                page.wait_for_timeout(3000)

            select_dropdown(0, data["district"])
            select_dropdown(1, data["taluk"])
            select_dropdown(2, data["hobli"])
            select_dropdown(3, data["village"])

            survey_filled = page.evaluate(
                """
                (surveyNumber) => {
                    const inputs = Array.from(document.querySelectorAll("input"))
                        .filter(input => {
                            const r = input.getBoundingClientRect();
                            const type = (input.type || "").toLowerCase();
                            return r.width > 0 &&
                                   r.height > 0 &&
                                   !input.disabled &&
                                   type !== "checkbox" &&
                                   type !== "button" &&
                                   type !== "submit";
                        });

                    if (inputs.length < 2) return false;

                    const surveyInput = inputs[1];
                    surveyInput.focus();
                    surveyInput.value = "";
                    surveyInput.dispatchEvent(new Event("input", {bubbles:true}));

                    surveyInput.value = surveyNumber;
                    surveyInput.dispatchEvent(new Event("input", {bubbles:true}));
                    surveyInput.dispatchEvent(new Event("change", {bubbles:true}));
                    surveyInput.dispatchEvent(new KeyboardEvent("keyup", {bubbles:true}));
                    surveyInput.dispatchEvent(new Event("blur", {bubbles:true}));

                    return true;
                }
                """,
                str(data["surveyNumber"]),
            )

            if not survey_filled:
                raise Exception("Survey No field not found")

            page.wait_for_timeout(5000)

            page.wait_for_function(
                """
                () => {
                    const selects = Array.from(document.querySelectorAll("select"))
                        .filter(s => {
                            const r = s.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && !s.disabled;
                        });

                    return selects.length >= 5 && selects[4].options.length > 1;
                }
                """,
                timeout=30000,
            )

            select_dropdown(4, data.get("surnoc", "*"))

            page.wait_for_function(
                """
                () => {
                    const selects = Array.from(document.querySelectorAll("select"))
                        .filter(s => {
                            const r = s.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && !s.disabled;
                        });

                    return selects.length >= 6 && selects[5].options.length > 1;
                }
                """,
                timeout=30000,
            )

            select_dropdown(5, data.get("hissa", "*"))

            search_clicked = page.evaluate("""
                () => {
                    const items = Array.from(document.querySelectorAll("button, input, a"));

                    const btn = items.find(el => {
                        const text = (el.innerText || el.value || "")
                            .trim()
                            .toLowerCase();

                        return text.includes("search");
                    });

                    if (!btn) return false;
                    btn.click();
                    return true;
                }
                """)

            if not search_clicked:
                raise Exception("Search button not found")

            page.wait_for_timeout(10000)

            view_clicked = page.evaluate("""
                () => {
                    const items = Array.from(document.querySelectorAll("button, input, a, span, div"));

                    const btn = items.find(el => {
                        const text = (el.innerText || el.value || "")
                            .trim()
                            .toLowerCase();

                        return text.includes("view sketch on map");
                    });

                    if (!btn) return false;
                    btn.click();
                    return true;
                }
                """)

            if not view_clicked:
                raise Exception("View Sketch On Map button not found")

            page.wait_for_timeout(12000)

            os.makedirs("survey_sketch_downloads", exist_ok=True)

            base_name = (
                f"SURVEY_SKETCH_{safe_filename(data['district'])}_"
                f"{safe_filename(data['taluk'])}_"
                f"{safe_filename(data['village'])}_"
                f"{safe_filename(data['surveyNumber'])}"
            )

            image_path = os.path.join(
                "survey_sketch_downloads",
                f"{base_name}_MAP.png",
            )

            pdf_path = os.path.join(
                "survey_sketch_downloads",
                f"{base_name}_MAP.pdf",
            )

            page.screenshot(
                path=image_path,
                full_page=True,
            )

            page.pdf(
                path=pdf_path,
                width="48in",
                height="36in",
                print_background=True,
                margin={
                    "top": "0.25in",
                    "right": "0.25in",
                    "bottom": "0.25in",
                    "left": "0.25in",
                },
            )

            return {
                "success": True,
                "type": "SURVEY_SKETCH_MAP",
                "image": image_path,
                "pdf": pdf_path,
            }

        except Exception as e:
            page.screenshot(path="survey-sketch-error.png", full_page=True)
            raise Exception(f"Survey Sketch fetch failed: {str(e)}")

        finally:
            browser.close()


def fetch_akarband(data):
    require_filled_fields(
        data,
        [
            "district",
            "taluk",
            "hobli",
            "village",
            "surveyNumber",
            "surnoc",
            "hissa",
        ],
        "Akarband fetch",
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
            args=["--lang=kn-IN", "--disable-features=Translate"],
        )

        context = browser.new_context(
            accept_downloads=True,
            locale="kn-IN",
        )

        page = context.new_page()

        try:
            page.goto(
                AKARBAND_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_selector("select", timeout=30000)

            selected_district = select_cascading_dropdown(
                page,
                0,
                data["district"],
                "District",
                next_index=1,
            )
            selected_taluk = select_cascading_dropdown(
                page,
                1,
                data["taluk"],
                "Taluk",
                next_index=2,
            )
            selected_hobli = select_cascading_dropdown(
                page,
                2,
                data["hobli"],
                "Hobli",
                next_index=3,
            )
            selected_village = select_cascading_dropdown(
                page,
                3,
                data["village"],
                "Village",
                next_index=4,
            )
            selected_survey = select_cascading_dropdown(
                page,
                4,
                data["surveyNumber"],
                "Survey No",
                next_index=5,
                allow_partial=False,
            )
            selected_surnoc = select_cascading_dropdown(
                page,
                5,
                data["surnoc"],
                "Surnoc",
                next_index=6,
                allow_partial=False,
            )
            selected_hissa = select_cascading_dropdown(
                page,
                6,
                data["hissa"],
                "Hissa",
                allow_partial=False,
            )

            os.makedirs("akarband_downloads", exist_ok=True)

            pdf_path = os.path.join(
                "akarband_downloads",
                f"AKARBAND_{safe_filename(data['district'])}_"
                f"{safe_filename(data['taluk'])}_"
                f"{safe_filename(data['village'])}_"
                f"{safe_filename(data['surveyNumber'])}.pdf",
            )

            button = page.locator(
                'button:has-text("ಆಕಾರಬಂದ್"), '
                'input[value*="ಆಕಾರಬಂದ್"], '
                'a:has-text("ಆಕಾರಬಂದ್")'
            ).first

            button.wait_for(state="visible", timeout=30000)

            try:
                with page.context.expect_page(timeout=15000) as popup_info:
                    button.click(force=True)

                result_page = popup_info.value

            except PlaywrightTimeoutError:
                button.click(force=True)
                result_page = page

            save_akarband_pdf_from_viewer(context, result_page, pdf_path)

            return {
                "success": True,
                "type": "AKARBAND",
                "selected_district": selected_district,
                "selected_taluk": selected_taluk,
                "selected_hobli": selected_hobli,
                "selected_village": selected_village,
                "selected_survey": selected_survey,
                "selected_surnoc": selected_surnoc,
                "selected_hissa": selected_hissa,
                "pdf": pdf_path,
            }

        except Exception as e:
            page.screenshot(path="akarband-error.png", full_page=True)
            raise Exception(f"Akarband fetch failed: {str(e)}")

        finally:
            browser.close()


def click_pdf_viewer_download_button(page):
    return page.evaluate("""
        () => {
            function visible(el) {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }

            function clickIfPresent(root, selectors) {
                if (!root) return false;

                for (const selector of selectors) {
                    const el = root.querySelector(selector);

                    if (visible(el)) {
                        el.click();
                        return true;
                    }
                }

                return false;
            }

            function allRoots(root) {
                const roots = [root];
                const walker = document.createTreeWalker(
                    root,
                    NodeFilter.SHOW_ELEMENT
                );

                while (walker.nextNode()) {
                    const el = walker.currentNode;

                    if (el.shadowRoot) {
                        roots.push(...allRoots(el.shadowRoot));
                    }
                }

                return roots;
            }

            function textOf(el) {
                return [
                    el.id,
                    el.className,
                    el.getAttribute("title"),
                    el.getAttribute("aria-label"),
                    el.getAttribute("data-l10n-id"),
                    el.textContent,
                    el.innerText,
                    el.innerHTML
                ]
                    .filter(Boolean)
                    .join(" ")
                    .toLowerCase();
            }

            function clickSemanticDownload(root) {
                const elements = Array.from(
                    root.querySelectorAll("button, a, input, cr-icon-button, mwc-icon-button, [role='button']")
                );

                for (const el of elements) {
                    if (!visible(el)) continue;

                    const text = textOf(el);

                    if (
                        text.includes("download") ||
                        text.includes("file_download") ||
                        text.includes("save")
                    ) {
                        el.click();
                        return true;
                    }
                }

                return false;
            }

            const selectors = [
                '#download',
                '#downloadButton',
                '#download-button',
                '#save',
                'cr-icon-button#download',
                'cr-icon-button#save',
                'button#download',
                'button#downloadButton',
                'button#download-button',
                'button[data-l10n-id="download"]',
                '[data-l10n-id="download"]',
                'button[aria-label*="Save"]',
                'button[aria-label*="Download"]',
                'button[title*="Save"]',
                'button[title*="Download"]',
                '[title*="Save"]',
                '[title*="Download"]',
                '[aria-label*="Save"]',
                '[aria-label*="Download"]'
            ];

            for (const root of allRoots(document)) {
                if (clickIfPresent(root, selectors)) return true;
                if (clickSemanticDownload(root)) return true;
            }

            const visibleButtons = Array.from(
                document.querySelectorAll("button, a, [role='button']")
            ).filter(visible);

            visibleButtons.sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return br.right - ar.right || ar.top - br.top;
            });

            for (const button of visibleButtons) {
                const rect = button.getBoundingClientRect();

                if (rect.top < 120 && rect.right > window.innerWidth - 180) {
                    const text = textOf(button);

                    if (!text.includes("print") && !text.includes("settings")) {
                        button.click();
                        return true;
                    }
                }
            }

            return false;
        }
        """)


def is_pdf_file(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def save_pdf_bytes_from_viewer_source(context, result_page, pdf_path):
    source = result_page.evaluate("""
        async () => {
            function allRoots(root) {
                const roots = [root];
                const walker = document.createTreeWalker(
                    root,
                    NodeFilter.SHOW_ELEMENT
                );

                while (walker.nextNode()) {
                    const el = walker.currentNode;

                    if (el.shadowRoot) {
                        roots.push(...allRoots(el.shadowRoot));
                    }
                }

                return roots;
            }

            const urls = new Set([window.location.href]);

            for (const root of allRoots(document)) {
                for (const el of root.querySelectorAll("embed, iframe, object")) {
                    const url = el.src || el.data;

                    if (url) {
                        urls.add(url);
                    }
                }

                for (const el of root.querySelectorAll("[src], [href], [data]")) {
                    const url = el.src || el.href || el.data;

                    if (url && /pdf|blob:|data:application\\/pdf/i.test(url)) {
                        urls.add(url);
                    }
                }
            }

            for (const url of urls) {
                if (!url || url === "about:blank") continue;

                if (url.startsWith("data:application/pdf")) {
                    return {
                        kind: "data",
                        url
                    };
                }

                if (url.startsWith("blob:")) {
                    const response = await fetch(url);
                    const buffer = await response.arrayBuffer();
                    let binary = "";
                    const bytes = new Uint8Array(buffer);

                    for (let i = 0; i < bytes.length; i += 1) {
                        binary += String.fromCharCode(bytes[i]);
                    }

                    return {
                        kind: "base64",
                        body: btoa(binary),
                        url
                    };
                }

                if (/pdf/i.test(url)) {
                    return {
                        kind: "url",
                        url
                    };
                }
            }

            return null;
        }
        """)

    if not source:
        return False

    if source["kind"] == "base64":
        with open(pdf_path, "wb") as f:
            f.write(base64.b64decode(source["body"]))
        return is_pdf_file(pdf_path)

    if source["kind"] == "data":
        header, encoded = source["url"].split(",", 1)
        data = base64.b64decode(encoded) if ";base64" in header else encoded.encode()
        with open(pdf_path, "wb") as f:
            f.write(data)
        return is_pdf_file(pdf_path)

    response = context.request.get(source["url"])

    if response.ok:
        body = response.body()

        if body.startswith(b"%PDF"):
            with open(pdf_path, "wb") as f:
                f.write(body)
            return True

    return False


def save_akarband_pdf_from_viewer(context, result_page, pdf_path):
    result_page.wait_for_load_state("domcontentloaded", timeout=60000)
    result_page.wait_for_timeout(7000)

    try:
        with result_page.expect_download(timeout=15000) as download_info:
            clicked = click_pdf_viewer_download_button(result_page)

            if not clicked:
                viewport = result_page.viewport_size or {"width": 1280, "height": 720}
                result_page.mouse.click(viewport["width"] - 105, 70)

        download = download_info.value
        download.save_as(pdf_path)
        if is_pdf_file(pdf_path):
            return

    except Exception:
        pass

    if save_pdf_bytes_from_viewer_source(context, result_page, pdf_path):
        return

    raise Exception("Could not save the original Akarband PDF from the viewer")


def get_node_portal_value(node, fallback):
    if isinstance(node, dict):
        return (
            node.get("portal")
            or node.get("label")
            or node.get("kn")
            or node.get("en")
            or fallback
        )

    if isinstance(node, str):
        return node

    return fallback


def get_portal_location_values(data):
    district = data["district"]
    taluk = data["taluk"]
    hobli = data["hobli"]
    village = data["village"]

    district_node = bilingual_master[district]
    district_value = get_node_portal_value(district_node, district)

    taluk_node = district_node["taluks"][taluk]
    taluk_value = get_node_portal_value(taluk_node, taluk)

    hobli_node = taluk_node["hoblis"][hobli]
    hobli_value = get_node_portal_value(hobli_node, hobli)

    village_node = hobli_node["villages"][village]
    village_value = get_node_portal_value(village_node, village)

    return district_value, taluk_value, hobli_value, village_value


def fetch_akarband_options(data):
    require_filled_fields(
        data,
        ["district", "taluk", "hobli", "village"],
        "Akarband options",
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300,
            args=["--lang=kn-IN", "--disable-features=Translate"],
        )

        context = browser.new_context(locale="kn-IN")
        page = context.new_page()

        try:
            page.goto(AKARBAND_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("select", timeout=30000)

            select_cascading_dropdown(
                page,
                0,
                data["district"],
                "District",
                next_index=1,
            )
            select_cascading_dropdown(
                page,
                1,
                data["taluk"],
                "Taluk",
                next_index=2,
            )
            select_cascading_dropdown(
                page,
                2,
                data["hobli"],
                "Hobli",
                next_index=3,
            )
            select_cascading_dropdown(
                page,
                3,
                data["village"],
                "Village",
                next_index=4,
            )

            surveys = get_visible_select_options(page, 4)
            surnocs = []
            hissas = []
            selected_survey = ""
            selected_surnoc = ""

            if data.get("surveyNumber", "").strip():
                selected_survey = select_cascading_dropdown(
                    page,
                    4,
                    data["surveyNumber"],
                    "Survey No",
                    next_index=5,
                    allow_partial=False,
                )
                surnocs = get_visible_select_options(page, 5)

            if data.get("surnoc", "").strip() and surnocs:
                selected_surnoc = select_cascading_dropdown(
                    page,
                    5,
                    data["surnoc"],
                    "Surnoc",
                    next_index=6,
                    allow_partial=False,
                )
                hissas = get_visible_select_options(page, 6)

            return {
                "success": True,
                "surveys": surveys,
                "surnocs": surnocs,
                "hissas": hissas,
                "selected_survey": selected_survey,
                "selected_surnoc": selected_surnoc,
            }

        except Exception as error:
            page.screenshot(path="akarband-options-error.png", full_page=True)
            raise Exception(f"Akarband options failed: {str(error)}")

        finally:
            browser.close()

# =========================================================
# TEXT HELPERS
# =========================================================

def rera_clean_text(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def rera_normalize(value):
    return rera_clean_text(value).lower()


def rera_safe_filename(value):
    value = rera_clean_text(value)

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1F]+',
        "_",
        value,
    )

    value = re.sub(r"\s+", "_", value).strip("._")

    return (value or "RERA_PROJECT")[:120]


# =========================================================
# BROWSER
# =========================================================

def create_rera_browser(headless=True):
    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        headless=bool(headless),
        slow_mo=100,
        args=[
            "--disable-features=Translate",
            "--disable-translate",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--start-maximized",
        ],
    )

    context = browser.new_context(
        locale="en-IN",
        viewport={
            "width": 1800,
            "height": 1100,
        },
        ignore_https_errors=True,
        accept_downloads=True,
    )

    return playwright, browser, context


def open_rera_portal(context, cancel_event=None):
    last_error = None

    for attempt in range(1, 4):
        rera_raise_if_cancelled(cancel_event)
        page = context.new_page()

        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(60000)

        try:
            page.goto(
                RERA_URL,
                wait_until="commit",
                timeout=45000,
            )

            selector = ".dataTables_filter input, input[type='search'], table"
            for _ in range(24):
                rera_raise_if_cancelled(cancel_event)
                try:
                    page.wait_for_selector(selector, timeout=5000)
                    break
                except PlaywrightTimeoutError:
                    continue
            else:
                raise RuntimeError("RERA portal search controls did not load.")

            page.wait_for_timeout(2500)

            return page

        except Exception as error:
            last_error = error

            try:
                page.close()
            except Exception:
                pass

            time.sleep(attempt * 2)

    raise RuntimeError(
        f"Unable to open Karnataka RERA portal: {last_error}"
    )


# =========================================================
# SEARCH FIELD
# =========================================================

def find_rera_search_input(page):
    selectors = [
        ".dataTables_filter input:visible",
        "input[type='search']:visible",
        "input[placeholder*='Search' i]:visible",
        "input[aria-label*='Search' i]:visible",
    ]

    for selector in selectors:
        locator = page.locator(selector)

        for index in range(locator.count()):
            item = locator.nth(index)

            try:
                if item.is_visible() and item.is_enabled():
                    return item

            except Exception:
                continue

    raise RuntimeError("RERA search field was not found.")


def fill_rera_search(page, value):
    search_input = find_rera_search_input(page)

    search_input.scroll_into_view_if_needed()

    search_input.fill("")
    search_input.fill(value)

    search_input.evaluate(
        """
        element => {
            element.dispatchEvent(
                new Event("input", {bubbles: true})
            );

            element.dispatchEvent(
                new KeyboardEvent("keyup", {bubbles: true})
            );

            element.dispatchEvent(
                new Event("change", {bubbles: true})
            );
        }
        """
    )

    page.wait_for_timeout(2500)


# =========================================================
# READ PROJECT TABLE
# =========================================================

def read_rera_table(page):
    return page.evaluate(
        r"""
        () => {
            const clean = value =>
                (value || "")
                    .normalize("NFKC")
                    .replace(/\u00a0/g, " ")
                    .replace(/\s+/g, " ")
                    .trim();

            const visible = element => {
                if (!element) return false;

                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);

                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden"
                );
            };

            const tables = Array.from(
                document.querySelectorAll("table")
            ).filter(visible);

            const table = tables.find(candidate => {
                const headers = Array.from(
                    candidate.querySelectorAll(
                        "thead th, tr:first-child th"
                    )
                ).map(th =>
                    clean(th.innerText || th.textContent).toLowerCase()
                );

                return (
                    headers.some(h => h.includes("promoter")) &&
                    headers.some(h => h.includes("project"))
                );
            });

            if (!table) {
                return {
                    headers: [],
                    rows: []
                };
            }

            let headers = Array.from(
                table.querySelectorAll("thead th")
            ).map(th => clean(th.innerText || th.textContent));

            if (!headers.length) {
                headers = Array.from(
                    table.querySelectorAll("tr:first-child th")
                ).map(th => clean(th.innerText || th.textContent));
            }

            const rows = Array.from(
                table.querySelectorAll("tbody tr")
            )
                .filter(visible)
                .map((row, index) => ({
                    index,
                    values: Array.from(
                        row.querySelectorAll("td")
                    ).map(td =>
                        clean(td.innerText || td.textContent)
                    )
                }))
                .filter(row => row.values.some(Boolean));

            return {
                headers,
                rows
            };
        }
        """
    )


def header_index(headers, condition):
    for index, header in enumerate(headers):
        if condition(rera_normalize(header)):
            return index

    return -1


def table_to_projects(table_data):
    headers = table_data.get("headers", [])
    rows = table_data.get("rows", [])

    registration_index = header_index(
        headers,
        lambda h: "registration" in h,
    )

    promoter_index = header_index(
        headers,
        lambda h: "promoter" in h,
    )

    project_index = header_index(
        headers,
        lambda h: (
            "project" in h
            and "view" not in h
            and "detail" not in h
        ),
    )

    type_index = header_index(
        headers,
        lambda h: h == "type" or "project type" in h,
    )

    district_index = header_index(
        headers,
        lambda h: "district" in h,
    )

    taluk_index = header_index(
        headers,
        lambda h: "taluk" in h,
    )

    completion_index = header_index(
        headers,
        lambda h: "completion" in h,
    )

    projects = []

    for row in rows:
        values = row.get("values", [])

        def value_at(index):
            if index >= 0 and index < len(values):
                return values[index]
            return ""

        registration_number = value_at(registration_index)

        if not registration_number:
            registration_number = next(
                (
                    value
                    for value in values
                    if "PRM/KA/RERA" in value.upper()
                ),
                "",
            )

        projects.append(
            {
                "registration_number": registration_number,
                "promoter_name": value_at(promoter_index),
                "project_name": value_at(project_index),
                "project_type": value_at(type_index),
                "district": value_at(district_index),
                "taluk": value_at(taluk_index),
                "proposed_completion_date": value_at(completion_index),
            }
        )

    return projects


def rera_next_button(page):
    selectors = [
        "a.paginate_button.next:not(.disabled):visible",
        "li.next:not(.disabled) a:visible",
        "button:has-text('Next'):not([disabled]):visible",
        "a:has-text('Next'):visible",
    ]

    for selector in selectors:
        locator = page.locator(selector)

        for index in range(locator.count()):
            item = locator.nth(index)

            try:
                classes = (
                    item.get_attribute("class") or ""
                ).lower()

                aria_disabled = (
                    item.get_attribute("aria-disabled") or ""
                ).lower()

                if (
                    item.is_visible()
                    and "disabled" not in classes
                    and aria_disabled != "true"
                ):
                    return item

            except Exception:
                continue

    return None


# =========================================================
# SEARCH RERA PROJECTS
# =========================================================

def search_rera_projects(data):
    search_type = rera_normalize(data.get("searchType", ""))
    query = rera_clean_text(data.get("query", ""))

    try:
        max_pages = int(data.get("maxPages", 25))
    except Exception:
        max_pages = 25

    try:
        max_results = int(data.get("maxResults", 500))
    except Exception:
        max_results = 500

    max_pages = max(1, min(max_pages, 100))
    max_results = max(1, min(max_results, 5000))

    if search_type not in {"promoter", "project", "registration"}:
        raise RuntimeError(
            "searchType must be promoter, project, or registration."
        )

    if len(query) < 2:
        raise RuntimeError(
            "Enter at least two characters."
        )

    playwright = None
    browser = None
    context = None
    request_id = data.get("requestId", "")
    cancel_event = rera_cancel_event(request_id)

    try:
        playwright, browser, context = create_rera_browser(
            headless=data.get("headless", True)
        )

        page = open_rera_portal(context, cancel_event)

        fill_rera_search(page, query)

        results = []
        seen = set()

        normalized_query = rera_normalize(query)

        for _ in range(max_pages):
            rera_raise_if_cancelled(cancel_event)
            table_data = read_rera_table(page)
            projects = table_to_projects(table_data)

            for project in projects:
                if search_type == "promoter":
                    searched_value = project.get(
                        "promoter_name",
                        "",
                    )
                elif search_type == "project":
                    searched_value = project.get(
                        "project_name",
                        "",
                    )
                else:
                    searched_value = project.get(
                        "registration_number",
                        "",
                    )

                if normalized_query not in rera_normalize(searched_value):
                    continue

                unique_key = "|".join(
                    [
                        rera_normalize(
                            project.get("registration_number", "")
                        ),
                        rera_normalize(
                            project.get("project_name", "")
                        ),
                        rera_normalize(
                            project.get("promoter_name", "")
                        ),
                    ]
                )

                if unique_key in seen:
                    continue

                seen.add(unique_key)
                results.append(project)

                if len(results) >= max_results:
                    break

            if len(results) >= max_results:
                break

            next_button = rera_next_button(page)

            if next_button is None:
                break

            previous_first_row = json.dumps(
                table_data.get("rows", [])[:1],
                ensure_ascii=False,
            )

            next_button.click(force=True)
            page.wait_for_timeout(1200)

            current_table = read_rera_table(page)

            current_first_row = json.dumps(
                current_table.get("rows", [])[:1],
                ensure_ascii=False,
            )

            if current_first_row == previous_first_row:
                break

        return {
            "success": True,
            "type": "RERA_PROJECT_SEARCH",
            "searchType": search_type,
            "query": query,
            "count": len(results),
            "results": results,
        }

    except Exception as error:
        raise RuntimeError(
            f"RERA search failed: {error}"
        ) from error

    finally:
        if request_id:
            with RERA_CANCEL_LOCK:
                RERA_CANCEL_EVENTS.pop(request_id, None)
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


# =========================================================
# MARK VIEW PROJECT DETAILS ICON
# =========================================================

def mark_rera_details_icon(
    page,
    registration_number,
    project_name,
    promoter_name,
):
    return page.evaluate(
        r"""
        ({
            registrationNumber,
            projectName,
            promoterName
        }) => {
            const normalize = value =>
                (value || "")
                    .normalize("NFKC")
                    .replace(/\u00a0/g, " ")
                    .replace(/\s+/g, " ")
                    .trim()
                    .toLowerCase();

            const visible = element => {
                if (!element) return false;

                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);

                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden"
                );
            };

            const tables = Array.from(
                document.querySelectorAll("table")
            ).filter(visible);

            const table = tables.find(candidate => {
                const headers = Array.from(
                    candidate.querySelectorAll(
                        "thead th, tr:first-child th"
                    )
                ).map(header =>
                    normalize(header.innerText || header.textContent)
                );

                return (
                    headers.some(h => h.includes("promoter")) &&
                    headers.some(h => h.includes("project"))
                );
            });

            if (!table) {
                return {
                    success: false,
                    reason: "RERA project table not found."
                };
            }

            let headers = Array.from(
                table.querySelectorAll("thead th")
            ).map(header =>
                normalize(header.innerText || header.textContent)
            );

            if (!headers.length) {
                headers = Array.from(
                    table.querySelectorAll("tr:first-child th")
                ).map(header =>
                    normalize(header.innerText || header.textContent)
                );
            }

            const registrationIndex = headers.findIndex(
                h => h.includes("registration")
            );

            const promoterIndex = headers.findIndex(
                h => h.includes("promoter")
            );

            const projectIndex = headers.findIndex(
                h =>
                    h.includes("project") &&
                    !h.includes("view") &&
                    !h.includes("detail")
            );

            let detailsIndex = headers.findIndex(
                h =>
                    h.includes("view project details") ||
                    (
                        h.includes("view") &&
                        h.includes("project")
                    )
            );

            const wantedRegistration = normalize(registrationNumber);
            const wantedProject = normalize(projectName);
            const wantedPromoter = normalize(promoterName);

            const rows = Array.from(
                table.querySelectorAll("tbody tr")
            ).filter(visible);

            let matchedRow = null;

            for (const row of rows) {
                const cells = Array.from(
                    row.querySelectorAll("td")
                );

                if (!cells.length) {
                    continue;
                }

                const values = cells.map(cell =>
                    normalize(cell.innerText || cell.textContent)
                );

                const registrationValue =
                    registrationIndex >= 0
                        ? values[registrationIndex]
                        : (
                            values.find(
                                value => value.includes("prm/ka/rera")
                            ) || ""
                        );

                const projectValue =
                    projectIndex >= 0
                        ? values[projectIndex]
                        : "";

                const promoterValue =
                    promoterIndex >= 0
                        ? values[promoterIndex]
                        : "";

                const registrationMatches =
                    wantedRegistration &&
                    (
                        registrationValue === wantedRegistration ||
                        registrationValue.includes(wantedRegistration) ||
                        wantedRegistration.includes(registrationValue)
                    );

                const namesMatch =
                    wantedProject &&
                    projectValue.includes(wantedProject) &&
                    (
                        !wantedPromoter ||
                        promoterValue.includes(wantedPromoter)
                    );

                if (
                    registrationMatches ||
                    (
                        !wantedRegistration &&
                        namesMatch
                    )
                ) {
                    matchedRow = row;
                    break;
                }
            }

            if (!matchedRow) {
                return {
                    success: false,
                    reason: "Selected project row was not found."
                };
            }

            const cells = Array.from(
                matchedRow.querySelectorAll("td")
            );

            if (
                detailsIndex < 0 &&
                projectIndex >= 0
            ) {
                detailsIndex = projectIndex + 1;
            }

            if (
                detailsIndex < 0 ||
                detailsIndex >= cells.length
            ) {
                detailsIndex = cells.findIndex(
                    cell =>
                        Boolean(
                            cell.querySelector(
                                "a, button, input, [onclick], " +
                                "[role='button'], img, svg, i"
                            )
                        )
                );
            }

            if (
                detailsIndex < 0 ||
                detailsIndex >= cells.length
            ) {
                return {
                    success: false,
                    reason: "View Project Details column not found."
                };
            }

            const detailsCell = cells[detailsIndex];

            const icon = detailsCell.querySelector(
                "img, svg, i, span"
            );

            const target =
                detailsCell.querySelector(
                    "a[href], button, input[type='button'], " +
                    "input[type='submit'], [role='button'], [onclick]"
                ) ||
                (
                    icon &&
                    (
                        icon.closest(
                            "a, button, [role='button'], [onclick]"
                        ) ||
                        icon
                    )
                ) ||
                detailsCell;

            document.querySelectorAll(
                "[data-rera-details-target]"
            ).forEach(element =>
                element.removeAttribute(
                    "data-rera-details-target"
                )
            );

            target.setAttribute(
                "data-rera-details-target",
                "true"
            );

            target.scrollIntoView({
                block: "center",
                inline: "center"
            });

            return {
                success: true
            };
        }
        """,
        {
            "registrationNumber": registration_number,
            "projectName": project_name,
            "promoterName": promoter_name,
        },
    )


# =========================================================
# WAIT FOR DETAILS PAGE
# =========================================================

def rera_page_score(page):
    if page is None or page.is_closed():
        return 0

    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return 0

    text = rera_normalize(text)

    markers = [
        "project registration details",
        "promoter details",
        "authorized signatory",
        "project details",
        "land details",
        "land survey details",
        "bank details",
        "uploaded documents",
    ]

    return (
        sum(100 for marker in markers if marker in text)
        + min(len(text), 50000) // 100
    )


def wait_for_rera_details_page(
    context,
    source_page,
    pages_before,
    timeout=60,
):
    deadline = time.time() + timeout

    best_page = None
    best_score = 0

    while time.time() < deadline:
        candidates = [
            source_page,
            *[
                page
                for page in context.pages
                if page not in pages_before
            ],
        ]

        for candidate in candidates:
            if candidate.is_closed():
                continue

            score = rera_page_score(candidate)

            if score > best_score:
                best_score = score
                best_page = candidate

            if score >= 300:
                return candidate

        source_page.wait_for_timeout(350)

    if best_page is not None and best_score >= 250:
        return best_page

    raise RuntimeError(
        "Project Registration Details did not open."
    )


def wait_for_page_stable(page, timeout=30):
    deadline = time.time() + timeout

    previous = None
    stable_count = 0

    while time.time() < deadline:
        try:
            current = page.evaluate(
                """
                () => ({
                    textLength:
                        (document.body.innerText || "").length,

                    height:
                        Math.max(
                            document.body.scrollHeight,
                            document.documentElement.scrollHeight
                        ),

                    images:
                        Array.from(document.images)
                            .filter(img => img.complete)
                            .length,

                    totalImages:
                        document.images.length
                })
                """
            )
        except Exception:
            page.wait_for_timeout(500)
            continue

        if current == previous and current["textLength"] > 800:
            stable_count += 1

            if stable_count >= 3:
                return

        else:
            stable_count = 0

        previous = current

        page.wait_for_timeout(500)


# =========================================================
# PRINT BUTTON
# =========================================================

def install_print_interceptor(page):
    page.evaluate(
        """
        () => {
            window.__reraPrintRequested = false;

            try {
                Object.defineProperty(
                    window,
                    "print",
                    {
                        configurable: true,
                        writable: true,
                        value: () => {
                            window.__reraPrintRequested = true;
                        }
                    }
                );
            } catch (error) {
                window.print = () => {
                    window.__reraPrintRequested = true;
                };
            }
        }
        """
    )


def find_rera_print_button(page):
    selectors = [
        "button:has-text('Print'):visible",
        "a:has-text('Print'):visible",
        "input[value*='Print' i]:visible",
        "[onclick*='print' i]:visible",
    ]

    for selector in selectors:
        locator = page.locator(selector)

        for index in range(locator.count()):
            item = locator.nth(index)

            try:
                if item.is_visible():
                    return item
            except Exception:
                continue

    raise RuntimeError(
        "Print button beside Project Registration Details was not found."
    )


# =========================================================
# CLONE ONLY PROJECT DETAILS
# =========================================================

def serialize_project_details(page):
    return page.evaluate(
        r"""
        async () => {
            const normalize = value =>
                (value || "")
                    .normalize("NFKC")
                    .replace(/\u00a0/g, " ")
                    .replace(/\s+/g, " ")
                    .trim()
                    .toLowerCase();

            window.dispatchEvent(new Event("beforeprint"));
            document.dispatchEvent(new Event("beforeprint"));

            const heading = Array.from(
                document.querySelectorAll(
                    "h1, h2, h3, h4, h5, div, span"
                )
            ).find(element =>
                normalize(
                    element.innerText ||
                    element.textContent
                ) === "project registration details"
            );

            if (!heading) {
                throw new Error(
                    "Project Registration Details heading not found."
                );
            }

            let root = heading.closest(
                ".modal, [role='dialog']"
            );

            if (!root) {
                let current = heading.parentElement;

                while (
                    current &&
                    current !== document.body
                ) {
                    const text = normalize(
                        current.innerText ||
                        current.textContent
                    );

                    const count = [
                        "project registration details",
                        "promoter details",
                        "project details",
                        "authorized signatory",
                        "land details",
                        "uploaded documents"
                    ].filter(marker =>
                        text.includes(marker)
                    ).length;

                    if (count >= 3 && text.length > 1000) {
                        root = current;
                        break;
                    }

                    current = current.parentElement;
                }
            }

            if (!root) {
                root = document.body;
            }

            root.querySelectorAll("canvas").forEach(canvas => {
                try {
                    const img = document.createElement("img");
                    img.src = canvas.toDataURL("image/png");
                    img.style.cssText = canvas.style.cssText;
                    img.width = canvas.width;
                    img.height = canvas.height;
                    canvas.replaceWith(img);
                } catch (error) {}
            });

            root.querySelectorAll("[src]").forEach(element => {
                try {
                    element.setAttribute("src", element.src);
                } catch (error) {}
            });

            root.querySelectorAll("a[href]").forEach(element => {
                try {
                    element.setAttribute("href", element.href);
                } catch (error) {}
            });

            root.querySelectorAll(
                ".tab-pane, .collapse, .accordion-collapse, " +
                ".panel-collapse, [hidden]"
            ).forEach(element => {
                element.hidden = false;
                element.removeAttribute("aria-hidden");

                element.style.setProperty(
                    "display",
                    "block",
                    "important"
                );

                element.style.setProperty(
                    "visibility",
                    "visible",
                    "important"
                );

                element.style.setProperty(
                    "opacity",
                    "1",
                    "important"
                );

                element.style.setProperty(
                    "height",
                    "auto",
                    "important"
                );

                element.style.setProperty(
                    "max-height",
                    "none",
                    "important"
                );

                element.style.setProperty(
                    "overflow",
                    "visible",
                    "important"
                );
            });

            root.setAttribute(
                "data-rera-export-root",
                "true"
            );

            const styleAssets = [
                ...Array.from(
                    document.querySelectorAll("link[rel='stylesheet']")
                ).map(link =>
                    `<link rel="stylesheet" href="${link.href}" media="all">`
                ),

                ...Array.from(
                    document.querySelectorAll("style")
                ).map(style => style.outerHTML)
            ].join("\\n");

            return {
                baseUrl: location.href,
                title: document.title || "RERA Project Details",
                bodyClass: document.body.className || "",
                styleAssets,
                rootHtml: root.outerHTML
            };
        }
        """
    )


def create_clean_rera_pdf_page(context, details_page):
    data = serialize_project_details(details_page)

    export_page = context.new_page()
    export_page.set_default_timeout(60000)

    html = f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <base href="{data['baseUrl']}">
        <title>{data['title']}</title>

        {data['styleAssets']}

        <style>
            @page {{
                size: A4 portrait;
                margin: 10mm 7mm 12mm 7mm;
            }}

            html,
            body {{
                margin: 0 !important;
                padding: 0 !important;
                background: white !important;
                width: auto !important;
                height: auto !important;
                overflow: visible !important;
            }}

            [data-rera-export-root],
            [data-rera-export-root] .modal-dialog,
            [data-rera-export-root] .modal-content,
            [data-rera-export-root] .modal-body {{
                display: block !important;
                position: static !important;
                inset: auto !important;
                float: none !important;
                transform: none !important;
                opacity: 1 !important;
                visibility: visible !important;
                width: 100% !important;
                max-width: none !important;
                height: auto !important;
                max-height: none !important;
                overflow: visible !important;
                margin: 0 !important;
                box-shadow: none !important;
                background: white !important;
            }}

            [data-rera-export-root] .tab-pane,
            [data-rera-export-root] .collapse,
            [data-rera-export-root] .accordion-collapse,
            [data-rera-export-root] .panel-collapse {{
                display: block !important;
                visibility: visible !important;
                opacity: 1 !important;
                height: auto !important;
                max-height: none !important;
                overflow: visible !important;
            }}

            [data-rera-export-root] button,
            [data-rera-export-root] input[type="button"],
            [data-rera-export-root] input[type="submit"],
            [data-rera-export-root] .close,
            [data-rera-export-root] .nav-tabs,
            [data-rera-export-root] .dataTables_filter,
            [data-rera-export-root] .dataTables_paginate,
            [data-rera-export-root] .dataTables_info {{
                display: none !important;
            }}

            * {{
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
                box-sizing: border-box;
            }}

            table {{
                border-collapse: collapse !important;
                width: 100% !important;
            }}

            thead {{
                display: table-header-group !important;
            }}

            tfoot {{
                display: table-footer-group !important;
            }}

            tr,
            img,
            .panel,
            .card {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}

            img {{
                max-width: 100% !important;
                height: auto !important;
            }}
        </style>
    </head>
    <body class="{data['bodyClass']}">
        {data['rootHtml']}
    </body>
    </html>
    """

    export_page.set_content(
        html,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    try:
        export_page.wait_for_load_state(
            "networkidle",
            timeout=30000,
        )
    except Exception:
        pass

    export_page.evaluate(
        """
        async () => {
            if (document.fonts && document.fonts.ready) {
                await document.fonts.ready;
            }

            await Promise.all(
                Array.from(document.images).map(image => {
                    if (image.complete) {
                        return Promise.resolve();
                    }

                    return new Promise(resolve => {
                        image.addEventListener(
                            "load",
                            resolve,
                            {once: true}
                        );

                        image.addEventListener(
                            "error",
                            resolve,
                            {once: true}
                        );

                        setTimeout(resolve, 10000);
                    });
                })
            );
        }
        """
    )

    export_page.emulate_media(media="print")
    export_page.wait_for_timeout(1000)

    return export_page


def save_rera_pdf(export_page, output_path):
    export_page.pdf(
        path=output_path,
        format="A4",
        landscape=False,
        print_background=True,
        prefer_css_page_size=False,
        scale=0.88,
        margin={
            "top": "10mm",
            "right": "7mm",
            "bottom": "12mm",
            "left": "7mm",
        },
        display_header_footer=True,
        header_template=(
            "<div style='width:100%;font-size:8px;"
            "padding:0 8mm;text-align:center;'>"
            "RERA Project Details"
            "</div>"
        ),
        footer_template=(
            "<div style='width:100%;font-size:8px;padding:0 8mm;'>"
            "<span class='url'></span>"
            "<span style='float:right'>"
            "<span class='pageNumber'></span>/"
            "<span class='totalPages'></span>"
            "</span>"
            "</div>"
        ),
    )

    if (
        not os.path.isfile(output_path)
        or os.path.getsize(output_path) < 30000
    ):
        raise RuntimeError(
            "Generated RERA PDF is blank or incomplete."
        )


# =========================================================
# CREATE PDF
# =========================================================

def create_rera_project_pdf(data):
    registration_number = rera_clean_text(
        data.get("registrationNumber", "")
    )

    project_name = rera_clean_text(
        data.get("projectName", "")
    )

    promoter_name = rera_clean_text(
        data.get("promoterName", "")
    )

    if not registration_number and not project_name:
        raise RuntimeError(
            "registrationNumber or projectName is required."
        )

    playwright = None
    browser = None
    context = None
    active_page = None

    try:
        playwright, browser, context = create_rera_browser(
            headless=data.get("headless", True)
        )

        list_page = open_rera_portal(context)
        active_page = list_page

        fill_rera_search(
            list_page,
            registration_number or project_name,
        )

        marked = mark_rera_details_icon(
            list_page,
            registration_number,
            project_name,
            promoter_name,
        )

        if not marked.get("success") and project_name:
            fill_rera_search(list_page, project_name)

            marked = mark_rera_details_icon(
                list_page,
                registration_number,
                project_name,
                promoter_name,
            )

        if not marked.get("success"):
            raise RuntimeError(
                marked.get("reason")
                or "View Project Details icon not found."
            )

        pages_before = list(context.pages)

        target = list_page.locator(
            "[data-rera-details-target='true']"
        ).first

        target.wait_for(
            state="visible",
            timeout=30000,
        )

        target.click(
            force=True,
            no_wait_after=True,
        )

        details_page = wait_for_rera_details_page(
            context,
            list_page,
            pages_before,
        )

        active_page = details_page

        wait_for_page_stable(details_page)

        install_print_interceptor(details_page)

        print_button = find_rera_print_button(details_page)

        print_button.scroll_into_view_if_needed()

        print_button.click(
            force=True,
            no_wait_after=True,
        )

        details_page.wait_for_timeout(1000)

        details_page.evaluate(
            """
            () => {
                window.dispatchEvent(new Event("beforeprint"));
                document.dispatchEvent(new Event("beforeprint"));
            }
            """
        )

        details_page.wait_for_timeout(800)

        export_page = create_clean_rera_pdf_page(
            context,
            details_page,
        )

        active_page = export_page

        timestamp = time.strftime("%Y%m%d_%H%M%S")

        filename = (
            f"RERA_"
            f"{rera_safe_filename(project_name or registration_number)}_"
            f"{rera_safe_filename(registration_number or 'PROJECT')}_"
            f"{timestamp}.pdf"
        )

        output_path = os.path.join(
            RERA_DOWNLOAD_DIR,
            filename,
        )

        save_rera_pdf(
            export_page,
            output_path,
        )

        return {
            "success": True,
            "filename": filename,
            "pdf": output_path,
            "downloadUrl": f"/api/rera/download/{filename}",
        }

    except Exception as error:
        debug_id = int(time.time())

        try:
            if active_page is not None and not active_page.is_closed():
                screenshot_path = os.path.join(
                    RERA_DEBUG_DIR,
                    f"rera-{debug_id}.png",
                )

                html_path = os.path.join(
                    RERA_DEBUG_DIR,
                    f"rera-{debug_id}.html",
                )

                active_page.screenshot(
                    path=screenshot_path,
                    full_page=True,
                )

                with open(html_path, "w", encoding="utf-8") as file:
                    file.write(active_page.content())

        except Exception:
            pass

        raise RuntimeError(
            f"RERA PDF creation failed: {error}"
        ) from error

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


RERA_DOCUMENT_KEYWORDS = [
    "registration certificate",
    "promoter",
    "company",
    "partnership",
    "pan",
    "gst",
    "balance sheet",
    "profit",
    "loss",
    "audit",
    "title deed",
    "title",
    "ownership",
    "land owner",
    "landowner",
    "sale deed",
    "rtc",
    "encumbrance",
    "ec",
    "khata",
    "conversion",
    "section 95",
    "development agreement",
    "jda",
    "joint development",
    "affidavit",
    "declaration",
    "approved plan",
    "building plan",
    "plotting plan",
    "layout plan",
    "existing layout",
    "commencement certificate",
    "floor plan",
    "section",
    "elevation",
    "drawing",
    "infrastructure",
    "area development",
    "ktcp",
    "section 14",
    "utilisation",
    "utilization",
    "tdr",
    "transferable development",
    "relinquishment",
    "agreement for sale",
    "allotment",
    "conveyance",
    "brochure",
    "specification",
    "fire noc",
    "airport",
    "bescom",
    "bwssb",
    "kspcb",
    "seiaa",
    "environment",
    "noc",
    "approval",
    "photograph",
    "photo",
    "gallery",
    "quarterly",
    "completion",
    "certificate",
    "licence",
    "license",
    "download",
    "annexure",
]


def rera_doc_safe_name(value):
    value = rera_clean_text(value)

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1F]+',
        "_",
        value,
    )

    value = re.sub(
        r"\s+",
        "_",
        value,
    ).strip("._")

    return (value or "document")[:140]


def rera_doc_is_not_applicable(value):
    text = rera_normalize(value)

    return (
        "not applicable" in text
        or "not_applicable" in text
        or "notapplicable" in text
    )


def rera_doc_extension_from_content_type(content_type):
    content_type = (content_type or "").lower()

    if "application/pdf" in content_type:
        return ".pdf"

    if "image/jpeg" in content_type:
        return ".jpg"

    if "image/png" in content_type:
        return ".png"

    if "image/webp" in content_type:
        return ".webp"

    if "wordprocessingml" in content_type:
        return ".docx"

    if "spreadsheet" in content_type:
        return ".xlsx"

    guessed = mimetypes.guess_extension(
        content_type.split(";")[0].strip()
    )

    return guessed or ".bin"


def rera_doc_guess_filename(label, url, content_type=""):
    label = rera_clean_text(label)

    if label:
        filename = label

    else:
        filename = ""

        if url:
            try:
                filename = os.path.basename(
                    unquote(
                        url.split("?")[0]
                    )
                )
            except Exception:
                filename = ""

    filename = filename or "document"
    filename = rera_doc_safe_name(filename)

    root, ext = os.path.splitext(filename)

    if not ext:
        ext = rera_doc_extension_from_content_type(
            content_type
        )

        filename = f"{root}{ext}"

    return filename


def rera_doc_body_looks_valid(body, content_type):
    if not body or len(body) < 100:
        return False

    content_type = (content_type or "").lower()

    if body.startswith(b"%PDF-"):
        return True

    if body.startswith(b"\x89PNG"):
        return True

    if body.startswith(b"\xff\xd8\xff"):
        return True

    if body.startswith(b"PK"):
        return True

    if (
        "application/pdf" in content_type
        or "image/" in content_type
        or "wordprocessingml" in content_type
        or "spreadsheet" in content_type
        or "application/octet-stream" in content_type
    ):
        return True

    if b"<html" in body[:500].lower():
        return False

    return True


def rera_open_selected_project_details_for_documents(payload):
    """
    Opens completed projects page, searches selected project,
    clicks View Project Details, and returns the open details page.
    """

    registration_number = rera_clean_text(
        payload.get("registrationNumber", "")
    )

    project_name = rera_clean_text(
        payload.get("projectName", "")
    )

    promoter_name = rera_clean_text(
        payload.get("promoterName", "")
    )

    if not registration_number and not project_name:
        raise RuntimeError(
            "registrationNumber or projectName is required."
        )

    playwright = None
    browser = None
    context = None

    try:
        playwright, browser, context = create_rera_browser(
            headless=payload.get("headless", True)
        )

        list_page = open_rera_portal(context)

        fill_rera_search(
            list_page,
            registration_number or project_name,
        )

        marked = mark_rera_details_icon(
            list_page,
            registration_number,
            project_name,
            promoter_name,
        )

        if not marked.get("success") and project_name:
            fill_rera_search(
                list_page,
                project_name,
            )

            marked = mark_rera_details_icon(
                list_page,
                registration_number,
                project_name,
                promoter_name,
            )

        if not marked.get("success"):
            raise RuntimeError(
                marked.get("reason")
                or "View Project Details icon not found."
            )

        pages_before = list(context.pages)

        target = list_page.locator(
            "[data-rera-details-target='true']"
        ).first

        target.wait_for(
            state="visible",
            timeout=30000,
        )

        target.click(
            force=True,
            no_wait_after=True,
        )

        details_page = wait_for_rera_details_page(
            context,
            list_page,
            pages_before,
        )

        wait_for_page_stable(details_page)

        return playwright, browser, context, details_page

    except Exception:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

        raise


def rera_open_all_project_tabs(page):
    """
    Clicks every tab in Project Registration Details so all links load.
    """

    tab_selectors = [
        ".nav-tabs a",
        "a[data-toggle='tab']",
        "a[data-bs-toggle='tab']",
        "button[data-bs-toggle='tab']",
        "button[data-toggle='tab']",
    ]

    for selector in tab_selectors:
        tabs = page.locator(selector)

        for index in range(tabs.count()):
            tab = tabs.nth(index)

            try:
                if tab.is_visible():
                    tab.click(
                        force=True,
                        no_wait_after=True,
                    )

                    page.wait_for_timeout(350)

            except Exception:
                continue

    page.evaluate(
        """
        () => {
            // The ZIP workflow must never open Chromium's native print dialog,
            // because that blocks Playwright until a user closes it.
            window.print = () => {};

            document.querySelectorAll(
                ".tab-pane, .collapse, .accordion-collapse, " +
                ".panel-collapse, [hidden]"
            ).forEach(element => {
                element.hidden = false;
                element.removeAttribute("aria-hidden");

                element.style.setProperty(
                    "display",
                    "block",
                    "important"
                );

                element.style.setProperty(
                    "visibility",
                    "visible",
                    "important"
                );

                element.style.setProperty(
                    "opacity",
                    "1",
                    "important"
                );

                element.style.setProperty(
                    "height",
                    "auto",
                    "important"
                );

                element.style.setProperty(
                    "max-height",
                    "none",
                    "important"
                );

                element.style.setProperty(
                    "overflow",
                    "visible",
                    "important"
                );
            });
        }
        """
    )

    page.wait_for_timeout(500)


def rera_extract_all_download_candidates(page):
    """
    Extracts all possible downloadable files from all tabs.
    Returns:
    {
        "candidates": downloadable documents,
        "not_applicable": documents marked NOT APPLICABLE
    }
    """

    rera_open_all_project_tabs(page)

    result = page.evaluate(
        r"""
        () => {
            const clean = value =>
                (value || "")
                    .normalize("NFKC")
                    .replace(/\u00a0/g, " ")
                    .replace(/\s+/g, " ")
                    .trim();

            const normalize = value =>
                clean(value).toLowerCase();

            const sectionNameFor = element => {
                const tabPane =
                    element.closest(".tab-pane");

                if (tabPane && tabPane.id) {
                    return tabPane.id;
                }

                let current = element;

                while (current && current !== document.body) {
                    const heading = current.querySelector(
                        "h1, h2, h3, h4, h5, .panel-title, legend"
                    );

                    if (heading) {
                        return clean(
                            heading.innerText ||
                            heading.textContent
                        );
                    }

                    current = current.parentElement;
                }

                return "Project Details";
            };

            const rowLabelFor = element => {
                const row =
                    element.closest("tr") ||
                    element.closest(".row") ||
                    element.parentElement;

                if (row) {
                    const rowText = clean(
                        row.innerText ||
                        row.textContent
                    );

                    if (rowText) {
                        return rowText;
                    }
                }

                return clean(
                    element.innerText ||
                    element.textContent ||
                    element.value ||
                    element.title ||
                    element.alt ||
                    element.getAttribute("aria-label") ||
                    ""
                );
            };

            const isVisibleOrUseful = element => {
                if (!element) {
                    return false;
                }

                const style =
                    window.getComputedStyle(element);

                const rect =
                    element.getBoundingClientRect();

                const html =
                    element.outerHTML || "";

                return (
                    (
                        style.display !== "none" &&
                        style.visibility !== "hidden"
                    ) ||
                    html.toLowerCase().includes(".pdf") ||
                    html.toLowerCase().includes("download") ||
                    html.toLowerCase().includes("annexure") ||
                    html.toLowerCase().includes("not applicable")
                );
            };

            const heading = Array.from(
                document.querySelectorAll(
                    "h1, h2, h3, h4, h5, div, span"
                )
            ).find(element =>
                normalize(
                    element.innerText ||
                    element.textContent
                ) === "project registration details"
            );

            let root = null;

            if (heading) {
                root = heading.closest(
                    ".modal, [role='dialog']"
                );

                if (!root) {
                    let current =
                        heading.parentElement;

                    while (
                        current &&
                        current !== document.body
                    ) {
                        const text = normalize(
                            current.innerText ||
                            current.textContent
                        );

                        const count = [
                            "project registration details",
                            "promoter details",
                            "land details",
                            "project details",
                            "bank details",
                            "uploaded documents",
                            "quarterly updates",
                            "completion details"
                        ].filter(marker =>
                            text.includes(marker)
                        ).length;

                        if (
                            count >= 3 &&
                            text.length > 1000
                        ) {
                            root = current;
                            break;
                        }

                        current =
                            current.parentElement;
                    }
                }
            }

            if (!root) {
                root = document.body;
            }

            const elements = Array.from(
                root.querySelectorAll(
                    "a[href], img[src], button, input[type='button'], " +
                    "input[type='submit'], [onclick], embed[src], " +
                    "iframe[src], object[data]"
                )
            ).filter(isVisibleOrUseful);

            const candidates = [];
            const notApplicable = [];

            let counter = 0;

            for (const element of elements) {
                const tagName =
                    element.tagName.toLowerCase();

                const text = clean(
                    element.innerText ||
                    element.textContent ||
                    element.value ||
                    element.title ||
                    element.alt ||
                    element.getAttribute("aria-label") ||
                    ""
                );

                const href =
                    element.href ||
                    element.src ||
                    element.getAttribute("href") ||
                    element.getAttribute("src") ||
                    element.getAttribute("data") ||
                    "";

                const onclick =
                    element.getAttribute("onclick") ||
                    "";

                const html =
                    element.outerHTML || "";

                const rowLabel =
                    rowLabelFor(element);

                const section =
                    sectionNameFor(element);

                const combined = normalize(
                    [
                        rowLabel,
                        text,
                        href,
                        onclick,
                        html
                    ].join(" ")
                );

                const controlText = normalize(text);
                const controlAction = normalize(
                    [text, href, onclick].join(" ")
                );

                const isNotApplicable =
                    combined.includes("not applicable") ||
                    combined.includes("not_applicable") ||
                    combined.includes("notapplicable");

                if (isNotApplicable) {
                    notApplicable.push({
                        label:
                            rowLabel || text || "NOT APPLICABLE",
                        filename:
                            text || "NOT APPLICABLE.pdf",
                        section:
                            section,
                        reason:
                            "Marked as NOT APPLICABLE in portal"
                    });

                    continue;
                }

                const hasFileExtension =
                    /\.(pdf|jpg|jpeg|png|webp|doc|docx|xls|xlsx|zip)(\?|#|$)/i
                        .test(
                            href || text || onclick || html
                        );

                const looksLikeDocument =
                    hasFileExtension ||
                    combined.includes(".pdf") ||
                    combined.includes("annexure") ||
                    combined.includes("certificate") ||
                    combined.includes("pancard") ||
                    combined.includes("pan card") ||
                    combined.includes("title") ||
                    combined.includes("sale deed") ||
                    combined.includes("rtc") ||
                    combined.includes("encumbrance") ||
                    combined.includes("khata") ||
                    combined.includes("conversion") ||
                    combined.includes("agreement") ||
                    combined.includes("affidavit") ||
                    combined.includes("plan") ||
                    combined.includes("drawing") ||
                    combined.includes("section") ||
                    combined.includes("elevation") ||
                    combined.includes("commencement") ||
                    combined.includes("noc") ||
                    combined.includes("approval") ||
                    combined.includes("licence") ||
                    combined.includes("license") ||
                    combined.includes("utilisation") ||
                    combined.includes("brochure") ||
                    combined.includes("specification") ||
                    combined.includes("photograph") ||
                    combined.includes("gallery") ||
                    combined.includes("quarterly") ||
                    combined.includes("completion") ||
                    combined.includes("download");

                const ignoreUi =
                    controlText === "print" ||
                    controlText.startsWith("print ") ||
                    controlText === "close" ||
                    controlText === "next" ||
                    controlText === "previous" ||
                    controlAction.includes("window.print") ||
                    controlAction.includes("print()") ||
                    controlAction.includes("printpage") ||
                    controlAction.includes("print page") ||
                    (
                        controlAction.includes("javascript:void(0)") &&
                        !looksLikeDocument
                    );

                if (
                    !looksLikeDocument ||
                    ignoreUi
                ) {
                    continue;
                }

                const absoluteUrl = href
                    ? new URL(href, location.href).href
                    : "";

                let filename = text;

                if (!filename && absoluteUrl) {
                    try {
                        filename = decodeURIComponent(
                            absoluteUrl
                                .split("?")[0]
                                .split("/")
                                .pop()
                        );
                    } catch (error) {
                        filename =
                            absoluteUrl
                                .split("?")[0]
                                .split("/")
                                .pop();
                    }
                }

                filename =
                    filename || text || "document";

                const id =
                    "rera_doc_" + counter;

                element.setAttribute(
                    "data-rera-download-id",
                    id
                );

                candidates.push({
                    id,
                    tag: tagName,
                    label: rowLabel || text || filename,
                    filename,
                    section,
                    url: absoluteUrl,
                    hasDirectUrl:
                        Boolean(
                            absoluteUrl &&
                            !absoluteUrl
                                .toLowerCase()
                                .startsWith("javascript:")
                        ),
                    hasOnClick:
                        Boolean(onclick)
                });

                counter += 1;
            }

            return {
                candidates,
                not_applicable: notApplicable
            };
        }
        """
    )

    candidates = result.get("candidates", [])
    not_applicable = result.get("not_applicable", [])

    final_candidates = []
    final_not_applicable = []

    seen_candidates = set()
    seen_not_applicable = set()

    for index, item in enumerate(candidates):
        label = rera_clean_text(item.get("label", ""))
        filename = rera_clean_text(item.get("filename", ""))
        section = rera_clean_text(item.get("section", "Project Details"))
        url = rera_clean_text(item.get("url", ""))

        key = "|".join(
            [
                rera_normalize(section),
                rera_normalize(label),
                rera_normalize(filename),
                rera_normalize(url),
            ]
        )

        if key in seen_candidates:
            continue

        seen_candidates.add(key)

        final_candidates.append(
            {
                "id": item.get("id") or f"rera_doc_{index}",
                "label": label or filename or f"Document {index + 1}",
                "filename": filename or label or f"Document {index + 1}",
                "section": section,
                "url": url,
                "hasDirectUrl": bool(item.get("hasDirectUrl")),
                "hasOnClick": bool(item.get("hasOnClick")),
            }
        )

    for index, item in enumerate(not_applicable):
        label = rera_clean_text(item.get("label", ""))
        filename = rera_clean_text(item.get("filename", "NOT APPLICABLE.pdf"))
        section = rera_clean_text(item.get("section", "Project Details"))

        key = "|".join(
            [
                rera_normalize(section),
                rera_normalize(label),
                rera_normalize(filename),
            ]
        )

        if key in seen_not_applicable:
            continue

        seen_not_applicable.add(key)

        final_not_applicable.append(
            {
                "label": label or f"Not Applicable Document {index + 1}",
                "filename": filename or "NOT APPLICABLE.pdf",
                "section": section,
                "reason": "Marked as NOT APPLICABLE in RERA portal",
            }
        )

    return {
        "candidates": final_candidates,
        "not_applicable": final_not_applicable,
    }

def rera_download_one_document(context, page, document):
    """
    Downloads document by direct URL first.
    Falls back to clicking the original element.
    """

    url = document.get("url", "")
    doc_id = document.get("id", "")
    control_text = rera_normalize(
        " ".join(
            [
                str(document.get("filename", "")),
                str(document.get("url", "")),
            ]
        )
    )

    if (
        control_text == "print"
        or control_text.startswith("print ")
        or "window.print" in control_text
        or "print()" in control_text
        or "printpage" in control_text
    ):
        raise RuntimeError("Skipped the RERA page Print control.")

    if (
        url
        and not url.lower().startswith("javascript:")
        and not url.endswith("#")
    ):
        try:
            response = context.request.get(
                url,
                headers={
                    "Referer": page.url,
                },
                timeout=20000,
            )

            body = response.body()
            content_type = response.headers.get(
                "content-type",
                "",
            )

            if (
                response.ok
                and rera_doc_body_looks_valid(
                    body,
                    content_type,
                )
            ):
                return body, content_type

        except Exception:
            pass

    if doc_id:
        locator = page.locator(
            f"[data-rera-download-id='{doc_id}']"
        ).first

        try:
            if locator.count() == 0 or not locator.is_visible():
                raise RuntimeError("Document link is no longer available.")
        except Exception as error:
            raise RuntimeError(
                "Document link is no longer available."
            ) from error

        try:
            with page.expect_download(
                timeout=10000
            ) as download_info:
                locator.click(
                    force=True,
                    no_wait_after=True,
                )

            download = download_info.value
            download_path = download.path()

            with open(
                download_path,
                "rb",
            ) as file:
                body = file.read()

            content_type = mimetypes.guess_type(
                download.suggested_filename or ""
            )[0] or ""

            return body, content_type

        except Exception:
            pass

        try:
            pages_before = list(page.context.pages)

            locator.click(
                force=True,
                no_wait_after=True,
            )

            page.wait_for_timeout(750)

            new_pages = [
                candidate
                for candidate in page.context.pages
                if candidate not in pages_before
            ]

            if new_pages:
                popup = new_pages[-1]

                try:
                    popup.wait_for_load_state(
                        "domcontentloaded",
                        timeout=5000,
                    )
                except Exception:
                    pass

                popup_url = popup.url

                if popup_url:
                    response = context.request.get(
                        popup_url,
                        headers={
                            "Referer": page.url,
                        },
                        timeout=20000,
                    )

                    body = response.body()
                    content_type = response.headers.get(
                        "content-type",
                        "",
                    )

                    if (
                        response.ok
                        and rera_doc_body_looks_valid(
                            body,
                            content_type,
                        )
                    ):
                        try:
                            popup.close()
                        except Exception:
                            pass

                        return body, content_type

                try:
                    popup.close()
                except Exception:
                    pass

        except Exception:
            pass

    raise RuntimeError(
        "Could not download document: "
        f"{document.get('label') or document.get('filename')}"
    )


def rera_create_all_documents_zip(payload):
    playwright = None
    browser = None
    context = None

    try:
        playwright, browser, context, details_page = (
            rera_open_selected_project_details_for_documents(
                payload
            )
        )

        extract_result = rera_extract_all_download_candidates(
            details_page
        )

        if isinstance(extract_result, dict):
            candidates = extract_result.get("candidates", [])
            not_applicable_documents = extract_result.get("not_applicable", [])
        else:
            candidates = extract_result
            not_applicable_documents = []

        documents = candidates

        timestamp = time.strftime("%Y%m%d_%H%M%S")

        project_name = (
            payload.get("projectName")
            or payload.get("registrationNumber")
            or "RERA_PROJECT"
        )

        zip_filename = (
            f"RERA_ALL_DOCUMENTS_"
            f"{rera_doc_safe_name(project_name)}_"
            f"{timestamp}.zip"
        )

        zip_path = os.path.join(
            RERA_DOCUMENT_ZIP_DIR,
            zip_filename,
        )

        used_names = set()
        downloaded_count = 0
        downloaded = []
        failed = []

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_file:

            for index, document in enumerate(documents, start=1):
                try:
                    body, content_type = rera_download_one_document(
                        context,
                        details_page,
                        document,
                    )

                    filename = rera_doc_guess_filename(
                        document.get("filename")
                        or document.get("label"),
                        document.get("url"),
                        content_type,
                    )

                    section = rera_doc_safe_name(
                        document.get("section", "Project Details")
                    )

                    archive_name = (
                        f"{index:03d}_{section}_{filename}"
                    )

                    while archive_name in used_names:
                        root, ext = os.path.splitext(archive_name)
                        archive_name = f"{root}_{index}{ext}"

                    used_names.add(archive_name)

                    zip_file.writestr(
                        archive_name,
                        body,
                    )

                    downloaded_count += 1
                    downloaded.append(
                        {
                            "label": document.get("label", ""),
                            "section": document.get("section", ""),
                            "filename": archive_name,
                            "url": document.get("url", ""),
                        }
                    )

                except Exception as error:
                    failed.append(
                        {
                            "label": document.get("label"),
                            "filename": document.get("filename"),
                            "section": document.get("section"),
                            "url": document.get("url", ""),
                            "error": str(error),
                        }
                    )

            not_downloaded = []

            for item in not_applicable_documents:
                not_downloaded.append(
                    {
                        "status": "NOT APPLICABLE",
                        "label": item.get("label", ""),
                        "section": item.get("section", ""),
                        "filename": item.get("filename", ""),
                        "reason": "Marked as NOT APPLICABLE in RERA portal",
                    }
                )

            for item in failed:
                not_downloaded.append(
                    {
                        "status": "FAILED",
                        "label": item.get("label", ""),
                        "section": item.get("section", ""),
                        "filename": item.get("filename", ""),
                        "url": item.get("url", ""),
                        "reason": item.get("error", ""),
                    }
                )

            report = {
                "project": {
                    "registrationNumber":
                        payload.get("registrationNumber"),
                    "projectName":
                        payload.get("projectName"),
                    "promoterName":
                        payload.get("promoterName"),
                },
                "expected_document_types":
                    globals().get("RERA_EXPECTED_DOCUMENT_TYPES", []),
                "found_candidates_count":
                    len(candidates),
                "downloaded_count":
                    len(downloaded),
                "failed_count":
                    len(failed),
                "not_applicable_count":
                    len(not_applicable_documents),
                "not_downloaded_count":
                    len(not_downloaded),
                "downloaded":
                    downloaded,
                "failed":
                    failed,
                "not_applicable":
                    not_applicable_documents,
                "not_downloaded":
                    not_downloaded,
            }

            zip_file.writestr(
                "00_DOWNLOAD_REPORT.json",
                json.dumps(report, indent=2, ensure_ascii=False),
            )

            summary_text = [
                "Karnataka RERA Document Download Summary",
                "",
                f"Project Name: {payload.get('projectName', '')}",
                f"Promoter Name: {payload.get('promoterName', '')}",
                f"Registration Number: {payload.get('registrationNumber', '')}",
                "",
                f"Detected documents: {len(documents)}",
                f"Downloaded documents: {downloaded_count}",
                f"Failed documents: {len(failed)}",
                f"Not applicable documents: {len(not_applicable_documents)}",
                "",
                "Note: NOT APPLICABLE files were skipped.",
                "",
            ]

            if failed:
                summary_text.append("Failed Items:")
                for item in failed:
                    summary_text.append(
                        f"- {item.get('section')} | "
                        f"{item.get('label')} | "
                        f"{item.get('error')}"
                    )

            zip_file.writestr(
                "DOWNLOAD_SUMMARY.txt",
                "\n".join(summary_text),
            )

        return zip_path, zip_filename

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


@app.post("/api/rera/download-all-documents")
def rera_download_all_documents_endpoint(
    data: ReraAllDocumentsZipRequest,
):
    try:
        zip_path, zip_filename = rera_create_all_documents_zip(
            data.model_dump()
        )

        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=zip_filename,
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


def rera_doc_safe_name(value):
    value = rera_clean_text(value)

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1F]+',
        "_",
        value,
    )

    value = re.sub(
        r"\s+",
        "_",
        value,
    ).strip("._")

    return (
        value or "document"
    )[:120]


def rera_doc_is_not_applicable(value):
    text = rera_normalize(value)

    return (
        "not applicable" in text
        or "not_applicable" in text
        or "notapplicable" in text
    )


def rera_doc_extension_from_content_type(content_type):
    content_type = (
        content_type or ""
    ).lower()

    if "application/pdf" in content_type:
        return ".pdf"

    if "image/jpeg" in content_type:
        return ".jpg"

    if "image/png" in content_type:
        return ".png"

    if "image/webp" in content_type:
        return ".webp"

    if (
        "application/vnd.ms-excel"
        in content_type
        or
        "spreadsheet"
        in content_type
    ):
        return ".xlsx"

    if "wordprocessingml" in content_type:
        return ".docx"

    guessed = mimetypes.guess_extension(
        content_type.split(";")[0].strip()
    )

    return guessed or ".bin"


def rera_doc_guess_filename(label, url, content_type=""):
    label = rera_clean_text(label)

    if label:
        filename = label

    else:
        filename = ""

        if url:
            try:
                filename = os.path.basename(
                    unquote(
                        url.split("?")[0]
                    )
                )
            except Exception:
                filename = ""

    filename = filename or "document"

    filename = rera_doc_safe_name(filename)

    root, ext = os.path.splitext(filename)

    if not ext:
        ext = rera_doc_extension_from_content_type(
            content_type
        )

        filename = f"{root}{ext}"

    return filename


def rera_open_selected_project_details(
    payload,
):
    """
    Opens RERA completed projects page, searches selected
    project and opens its Project Registration Details page.
    """

    registration_number = rera_clean_text(
        payload.get(
            "registrationNumber",
            "",
        )
    )

    project_name = rera_clean_text(
        payload.get(
            "projectName",
            "",
        )
    )

    promoter_name = rera_clean_text(
        payload.get(
            "promoterName",
            "",
        )
    )

    if not registration_number and not project_name:
        raise RuntimeError(
            "registrationNumber or projectName is required."
        )

    playwright = None
    browser = None
    context = None

    try:
        playwright, browser, context = create_rera_browser(
            headless=payload.get(
                "headless",
                True,
            )
        )

        list_page = open_rera_portal(
            context
        )

        fill_rera_search(
            list_page,
            registration_number or project_name,
        )

        marked = mark_rera_details_icon(
            list_page,
            registration_number,
            project_name,
            promoter_name,
        )

        if not marked.get("success") and project_name:
            fill_rera_search(
                list_page,
                project_name,
            )

            marked = mark_rera_details_icon(
                list_page,
                registration_number,
                project_name,
                promoter_name,
            )

        if not marked.get("success"):
            raise RuntimeError(
                marked.get("reason")
                or
                "View Project Details icon not found."
            )

        pages_before = list(
            context.pages
        )

        target = list_page.locator(
            "[data-rera-details-target='true']"
        ).first

        target.wait_for(
            state="visible",
            timeout=30000,
        )

        target.click(
            force=True,
            no_wait_after=True,
        )

        details_page = wait_for_rera_details_page(
            context,
            list_page,
            pages_before,
        )

        wait_for_page_stable(
            details_page
        )

        return playwright, browser, context, details_page

    except Exception:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

        raise


def rera_click_all_detail_tabs(page):
    """
    Loads all RERA project-detail tabs before extracting links.
    """

    tab_selectors = [
        ".nav-tabs a",
        "a[data-toggle='tab']",
        "a[data-bs-toggle='tab']",
        "button[data-bs-toggle='tab']",
        "button[data-toggle='tab']",
    ]

    for selector in tab_selectors:
        tabs = page.locator(
            selector
        )

        count = tabs.count()

        for index in range(count):
            tab = tabs.nth(index)

            try:
                if tab.is_visible():
                    tab.click(
                        force=True,
                        no_wait_after=True,
                    )

                    page.wait_for_timeout(
                        900
                    )

            except Exception:
                continue

    page.evaluate(
        """
        () => {
            document.querySelectorAll(
                ".tab-pane, .collapse, .accordion-collapse, " +
                ".panel-collapse, [hidden]"
            ).forEach(element => {
                element.hidden = false;

                element.removeAttribute("aria-hidden");

                element.style.setProperty(
                    "display",
                    "block",
                    "important"
                );

                element.style.setProperty(
                    "visibility",
                    "visible",
                    "important"
                );

                element.style.setProperty(
                    "opacity",
                    "1",
                    "important"
                );

                element.style.setProperty(
                    "height",
                    "auto",
                    "important"
                );

                element.style.setProperty(
                    "max-height",
                    "none",
                    "important"
                );

                element.style.setProperty(
                    "overflow",
                    "visible",
                    "important"
                );
            });
        }
        """
    )

    page.wait_for_timeout(
        1200
    )


def rera_extract_downloadable_links(page):
    """
    Finds all downloadable PDF/image/document links from
    the selected RERA Project Registration Details page.
    """

    rera_click_all_detail_tabs(
        page
    )

    documents = page.evaluate(
        r"""
        () => {
            const clean = value =>
                (value || "")
                    .normalize("NFKC")
                    .replace(/\u00a0/g, " ")
                    .replace(/\s+/g, " ")
                    .trim();

            const normalize = value =>
                clean(value).toLowerCase();

            const heading = Array.from(
                document.querySelectorAll(
                    "h1, h2, h3, h4, h5, div, span"
                )
            ).find(element =>
                normalize(
                    element.innerText ||
                    element.textContent
                ) === "project registration details"
            );

            let root = null;

            if (heading) {
                root = heading.closest(
                    ".modal, [role='dialog']"
                );

                if (!root) {
                    let current = heading.parentElement;

                    while (
                        current &&
                        current !== document.body
                    ) {
                        const text = normalize(
                            current.innerText ||
                            current.textContent
                        );

                        const count = [
                            "project registration details",
                            "promoter details",
                            "project details",
                            "land details",
                            "uploaded documents",
                            "bank details"
                        ].filter(marker =>
                            text.includes(marker)
                        ).length;

                        if (count >= 3) {
                            root = current;
                            break;
                        }

                        current = current.parentElement;
                    }
                }
            }

            if (!root) {
                root = document.body;
            }

            const elements = Array.from(
                root.querySelectorAll(
                    "a[href], button, input[type='button'], " +
                    "input[type='submit'], [onclick], img[src]"
                )
            );

            const output = [];
            let counter = 0;

            for (const element of elements) {
                const tagName =
                    element.tagName.toLowerCase();

                const text = clean(
                    element.innerText ||
                    element.textContent ||
                    element.value ||
                    element.title ||
                    element.alt ||
                    element.getAttribute("aria-label") ||
                    ""
                );

                const href =
                    element.href ||
                    element.src ||
                    element.getAttribute("href") ||
                    element.getAttribute("src") ||
                    element.getAttribute("data") ||
                    "";

                const onclick =
                    element.getAttribute("onclick") ||
                    "";

                const combined = normalize(
                    [
                        text,
                        href,
                        onclick,
                        element.outerHTML || ""
                    ].join(" ")
                );

                if (
                    combined.includes("not applicable") ||
                    combined.includes("not_applicable") ||
                    combined.includes("notapplicable")
                ) {
                    continue;
                }

                const hasFileExtension =
                    /\.(pdf|jpg|jpeg|png|webp|doc|docx|xls|xlsx|zip)(\?|$)/i
                        .test(
                            href || text || onclick
                        );

                const looksLikeDocument =
                    hasFileExtension ||
                    combined.includes("annexure") ||
                    combined.includes("certificate") ||
                    combined.includes("licence") ||
                    combined.includes("license") ||
                    combined.includes("approval") ||
                    combined.includes("noc") ||
                    combined.includes("plan") ||
                    combined.includes("drawing") ||
                    combined.includes("uploaded document") ||
                    combined.includes("download");

                const ignoredUi =
                    combined === "print" ||
                    combined === "close" ||
                    combined.includes("promoter details land details") ||
                    combined.includes("project details bank details") ||
                    combined.includes("quarterly updates") ||
                    combined.includes("completion details");

                if (
                    !looksLikeDocument ||
                    ignoredUi
                ) {
                    continue;
                }

                const tabPane =
                    element.closest(".tab-pane");

                let section = "";

                if (tabPane && tabPane.id) {
                    section = tabPane.id;
                }

                if (!section) {
                    let current = element;

                    while (
                        current &&
                        current !== root
                    ) {
                        const previous =
                            current.previousElementSibling;

                        if (previous) {
                            const heading = previous.querySelector(
                                "h1, h2, h3, h4, h5, .panel-title"
                            );

                            if (heading) {
                                section = clean(
                                    heading.innerText ||
                                    heading.textContent
                                );
                                break;
                            }
                        }

                        current = current.parentElement;
                    }
                }

                section = section || "Project Details";

                const url = href
                    ? new URL(href, location.href).href
                    : "";

                let filename = text;

                if (!filename && url) {
                    try {
                        filename = decodeURIComponent(
                            url.split("?")[0].split("/").pop()
                        );
                    } catch (error) {
                        filename = url.split("?")[0].split("/").pop();
                    }
                }

                filename = filename || "document";

                const id =
                    "rera_doc_" + counter;

                element.setAttribute(
                    "data-rera-doc-id",
                    id
                );

                output.push({
                    id,
                    label: text || filename,
                    filename,
                    section,
                    url,
                    tag: tagName,
                    hasDirectUrl:
                        Boolean(
                            url &&
                            !url.toLowerCase().startsWith("javascript:")
                        ),
                    onclick:
                        Boolean(onclick)
                });

                counter += 1;
            }

            return output;
        }
        """
    )

    final_documents = []
    seen = set()

    for index, item in enumerate(documents):
        label = rera_clean_text(
            item.get("label", "")
        )

        filename = rera_clean_text(
            item.get("filename", "")
        )

        section = rera_clean_text(
            item.get("section", "Project Details")
        )

        url = rera_clean_text(
            item.get("url", "")
        )

        if (
            rera_doc_is_not_applicable(label)
            or rera_doc_is_not_applicable(filename)
            or rera_doc_is_not_applicable(url)
        ):
            continue

        key = "|".join(
            [
                rera_normalize(section),
                rera_normalize(label),
                rera_normalize(filename),
                rera_normalize(url),
            ]
        )

        if key in seen:
            continue

        seen.add(key)

        final_documents.append(
            {
                "id": item.get("id") or f"rera_doc_{index}",
                "label": label or filename or f"Document {index + 1}",
                "filename": filename or label or f"Document {index + 1}",
                "section": section,
                "url": url,
                "hasDirectUrl": bool(item.get("hasDirectUrl")),
                "onclick": bool(item.get("onclick")),
            }
        )

    return final_documents


def rera_download_document_bytes(
    context,
    page,
    document,
):
    """
    Downloads one document either by direct URL or by clicking
    the link in the project-details page.
    """

    url = document.get("url", "")
    doc_id = document.get("id", "")

    # Direct URL download
    if (
        url
        and not url.lower().startswith("javascript:")
        and not url.endswith("#")
    ):
        try:
            response = context.request.get(
                url,
                headers={
                    "Referer": page.url,
                },
                timeout=60000,
            )

            if response.ok:
                body = response.body()

                content_type = (
                    response.headers.get("content-type")
                    or ""
                )

                if body and len(body) > 100:
                    return body, content_type

        except Exception:
            pass

    # Click-download fallback
    if doc_id:
        locator = page.locator(
            f"[data-rera-doc-id='{doc_id}']"
        ).first

        try:
            with page.expect_download(
                timeout=20000
            ) as download_info:
                locator.click(
                    force=True,
                    no_wait_after=True,
                )

            download = download_info.value

            download_path = download.path()

            with open(
                download_path,
                "rb",
            ) as file:
                body = file.read()

            suggested_name = (
                download.suggested_filename
                or document.get("filename")
                or "document"
            )

            return body, mimetypes.guess_type(
                suggested_name
            )[0] or ""

        except Exception:
            pass

    raise RuntimeError(
        f"Unable to download document: "
        f"{document.get('label') or document.get('filename')}"
    )


def rera_find_documents_for_project(payload):
    playwright = None
    browser = None
    context = None

    try:
        playwright, browser, context, details_page = (
            rera_open_selected_project_details(
                payload
            )
        )

        documents = rera_extract_downloadable_links(
            details_page
        )

        return {
            "success": True,
            "count": len(documents),
            "documents": documents,
        }

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


def rera_create_documents_zip(payload):
    requested_documents = payload.get(
        "documents",
        [],
    )

    if not requested_documents:
        raise RuntimeError(
            "No RERA documents selected."
        )

    requested_ids = {
        str(item.get("id", ""))
        for item in requested_documents
        if item.get("id")
    }

    requested_urls = {
        str(item.get("url", ""))
        for item in requested_documents
        if item.get("url")
    }

    requested_labels = {
        rera_normalize(
            item.get("label", "")
        )
        for item in requested_documents
        if item.get("label")
    }

    playwright = None
    browser = None
    context = None

    try:
        playwright, browser, context, details_page = (
            rera_open_selected_project_details(
                payload
            )
        )

        all_documents = rera_extract_downloadable_links(
            details_page
        )

        selected_documents = []

        for doc in all_documents:
            if (
                doc.get("id") in requested_ids
                or doc.get("url") in requested_urls
                or rera_normalize(doc.get("label", "")) in requested_labels
            ):
                selected_documents.append(doc)

        if not selected_documents:
            raise RuntimeError(
                "Selected document links were not found "
                "after reopening the RERA details page."
            )

        timestamp = time.strftime(
            "%Y%m%d_%H%M%S"
        )

        project_name = payload.get(
            "projectName",
            "RERA_PROJECT",
        )

        zip_filename = (
            f"RERA_DOCUMENTS_"
            f"{rera_doc_safe_name(project_name)}_"
            f"{timestamp}.zip"
        )

        zip_path = os.path.join(
            RERA_DOCUMENT_ZIP_DIR,
            zip_filename,
        )

        used_names = set()

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zip_file:

            for index, doc in enumerate(
                selected_documents,
                start=1,
            ):
                body, content_type = (
                    rera_download_document_bytes(
                        context,
                        details_page,
                        doc,
                    )
                )

                filename = rera_doc_guess_filename(
                    doc.get("filename")
                    or doc.get("label"),
                    doc.get("url"),
                    content_type,
                )

                section = rera_doc_safe_name(
                    doc.get("section", "Project Details")
                )

                archive_name = (
                    f"{index:03d}_{section}_{filename}"
                )

                while archive_name in used_names:
                    root, ext = os.path.splitext(
                        archive_name
                    )

                    archive_name = (
                        f"{root}_{index}{ext}"
                    )

                used_names.add(
                    archive_name
                )

                zip_file.writestr(
                    archive_name,
                    body,
                )

        return zip_path, zip_filename

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


# =========================================================
# API ROUTES
# =========================================================

@app.post("/api/fetch-rtc/auto")
def fetch_rtc_auto(data: BhoomiRequest):
    try:
        return fetch_rtc_with_playwright(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/mr/search")
def mr_search(data: BhoomiRequest):
    try:
        return fetch_mr_rows(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/mr/download-selected")
def mr_download_selected(data: MRDownloadRequest):
    try:
        return download_selected_mr(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/revenue-map/fetch")
def revenue_map_fetch(data: RevenueMapRequest):
    try:
        return fetch_revenue_map(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/survey-sketch/fetch")
def survey_sketch_fetch(data: SurveySketchRequest):
    try:
        return fetch_survey_sketch(data.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/akarband/fetch")
def akarband_fetch(data: AkarbandRequest):
    try:
        return fetch_akarband(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/akarband/options")
def akarband_options(data: AkarbandOptionsRequest):
    try:
        return fetch_akarband_options(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))

@app.post("/api/rera/search")
def rera_search_endpoint(data: ReraSearchRequest):
    request_id = data.requestId
    if request_id:
        with RERA_CANCEL_LOCK:
            RERA_CANCEL_EVENTS.setdefault(request_id, threading.Event())
    try:
        return search_rera_projects(
            data.model_dump()
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


@app.post("/api/rera/cancel/{request_id}")
def rera_cancel_endpoint(request_id: str):
    with RERA_CANCEL_LOCK:
        event = RERA_CANCEL_EVENTS.setdefault(
            request_id, threading.Event()
        )
    if event is not None:
        event.set()
    return {"success": True, "cancelled": True}


@app.post("/api/rera/project-pdf")
def rera_project_pdf_endpoint(data: ReraProjectPdfRequest):
    try:
        return create_rera_project_pdf(
            data.model_dump()
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


@app.post("/api/rera/documents")
def rera_documents_endpoint(
    data: ReraDocumentsRequest,
):
    try:
        return rera_find_documents_for_project(
            data.model_dump()
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


@app.post("/api/rera/documents/download")
def rera_documents_download_endpoint(
    data: ReraDocumentsZipRequest,
):
    try:
        zip_path, zip_filename = (
            rera_create_documents_zip(
                data.model_dump()
            )
        )

        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=zip_filename,
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
    )
