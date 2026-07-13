import os
import re
import time
import json
import base64
import unicodedata
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
BHOOMI_URL = "https://landrecords.karnataka.gov.in/Service2/"
MR_URL = "https://landrecords.karnataka.gov.in/Service11/MR_MutationExtract.aspx"
REVENUE_MAP_URL = "https://landrecords.karnataka.gov.in/service3/"
SURVEY_SKETCH_URL = "https://rdservices.karnataka.gov.in/service84/"
AKARBAND_URL = "https://bhoomojini.karnataka.gov.in/service39/"
try:
    BASE_DIR
except NameError:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RERA_URL = "https://rera.karnataka.gov.in/viewAllCompletedProjects"
RERA_DOWNLOAD_DIR = os.path.join(BASE_DIR, "rera_downloads")
RERA_PDF_DIR = RERA_DOWNLOAD_DIR
os.makedirs(RERA_DOWNLOAD_DIR, exist_ok=True)
with open("bhoomi-master-bilingual.json", "r", encoding="utf-8") as f:
    bilingual_master = json.load(f)

app = FastAPI(title="Bhoomi Automation API")


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


class ReraRequest(BaseModel):
    action: str = "search"
    searchType: str = ""
    query: str = ""
    registrationNumber: str = ""
    projectName: str = ""
    promoterName: str = ""
    headless: bool = True
    maxPages: int = 25
    maxResults: int = 500


ReraUnifiedRequest = ReraRequest


class ReraSearchRequest(BaseModel):
    searchType: str
    query: str
    headless: bool = True
    maxPages: int = 25
    maxResults: int = 500


class ReraProjectPdfRequest(BaseModel):
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


def find_akarband_fetch_button(page):
    result = page.evaluate(
        """
        () => {
            const clean = value => (value || "")
                .normalize("NFKC")
                .replace(/\\u00a0/g, " ")
                .replace(/\\s+/g, " ")
                .trim()
                .toLowerCase();

            const visible = element => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden";
            };

            const candidates = Array.from(
                document.querySelectorAll(
                    "button, input[type='button'], input[type='submit'], a"
                )
            ).filter(element => visible(element) && !element.disabled);

            const scored = candidates.map((element, index) => {
                const text = clean(
                    element.innerText ||
                    element.textContent ||
                    element.value ||
                    element.getAttribute("aria-label") ||
                    ""
                );
                const classes = clean(element.className || "");
                const type = clean(element.getAttribute("type") || "");
                let score = 0;

                if (text.includes("ಆಕಾರಬಂದ್")) score += 100;
                if (text.includes("ಪಡೆಯಿರಿ") || text.includes("ಪಡೆ")) score += 80;
                if (text.includes("akarband")) score += 100;
                if (text.includes("fetch") || text.includes("submit")) score += 70;
                if (type === "submit" || type === "button") score += 20;
                if (classes.includes("btn") || classes.includes("button")) score += 10;

                return {element, index, score, text};
            }).filter(item => item.score > 0);

            const selected = scored.sort((a, b) => {
                if (b.score !== a.score) return b.score - a.score;
                return b.index - a.index;
            })[0] || candidates[candidates.length - 1];

            if (!selected) {
                return {success: false, reason: "No visible enabled button found."};
            }

            const element = selected.element || selected;
            document.querySelectorAll("[data-akarband-fetch-target]")
                .forEach(item => item.removeAttribute("data-akarband-fetch-target"));
            element.setAttribute("data-akarband-fetch-target", "true");
            element.scrollIntoView({block: "center", inline: "center"});

            return {
                success: true,
                text: clean(
                    element.innerText ||
                    element.textContent ||
                    element.value ||
                    ""
                ),
            };
        }
        """
    )

    if not result.get("success"):
        raise Exception(
            "Akarband Fetch button not found: "
            f"{result.get('reason', 'unknown reason')}"
        )

    return page.locator("[data-akarband-fetch-target='true']").first


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
                        !option.label.includes("à²†à²¯à³à²•à³†") &&
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

            button = find_akarband_fetch_button(page)
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



def rera_clean(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def rera_norm(value):
    return rera_clean(value).lower()


def rera_safe_name(value):
    value = rera_clean(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    return (value or "RERA_PROJECT")[:120]


def create_rera_browser(playwright, *, headless, load_assets):
    browser = playwright.chromium.launch(
        headless=bool(headless),
        slow_mo=80,
        args=[
            "--disable-features=Translate",
            "--disable-translate",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    context = browser.new_context(
        locale="en-IN",
        viewport={"width": 1800, "height": 1100},
        accept_downloads=True,
        ignore_https_errors=True,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    )

    if not load_assets:
        def route_handler(route):
            if route.request.resource_type in {"image", "media", "font"}:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", route_handler)

    context.add_init_script(
        """
        (() => {
            window.__reraPrintRequested = false;
            window.__reraCloseRequested = false;
            try {
                Object.defineProperty(window, "print", {
                    configurable: true,
                    writable: true,
                    value: () => {
                        window.__reraPrintRequested = true;
                    }
                });
            } catch (_) {
                window.print = () => {
                    window.__reraPrintRequested = true;
                };
            }

            try {
                Object.defineProperty(window, "close", {
                    configurable: true,
                    writable: true,
                    value: () => {
                        window.__reraCloseRequested = true;
                    }
                });
            } catch (_) {
                window.close = () => {
                    window.__reraCloseRequested = true;
                };
            }
        })();
        """
    )

    return browser, context


def wait_for_rera_ready(page, timeout_ms=120000):
    deadline = time.time() + (timeout_ms / 1000)

    while time.time() < deadline:
        if page.is_closed():
            raise RuntimeError("RERA page closed before it became ready.")

        try:
            table_count = page.locator("table").count()
            search_count = page.locator(
                ".dataTables_filter input, input[type='search'], "
                "input[placeholder*='Search' i]"
            ).count()

            if table_count > 0:
                page.wait_for_timeout(1200)
                return
        except Exception:
            pass

        page.wait_for_timeout(500)

    raise TimeoutError(
        "The RERA page opened, but the project table "
        "did not become ready within the allowed time."
    )


def open_rera_page(context, attempts=3):
    last_error = None

    for attempt in range(1, attempts + 1):
        page = context.new_page()
        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(45000)

        try:
            page.goto(
                RERA_URL,
                wait_until="commit",
                timeout=45000,
            )
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30000)
            except Exception:
                pass
            wait_for_rera_ready(page, timeout_ms=120000)
            return page

        except Exception as error:
            last_error = error

            try:
                page.close()
            except Exception:
                pass

            if attempt < attempts:
                time.sleep(2 * attempt)

    raise RuntimeError(
        f"Unable to open the Karnataka RERA portal after {attempts} attempts: "
        f"{last_error}"
    )


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

    raise RuntimeError("RERA search field not found.")


def wait_for_rera_filter(page):
    try:
        processing = page.locator(".dataTables_processing")
        if processing.count():
            processing.first.wait_for(state="hidden", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(1200)


def fill_rera_search(page, value):
    try:
        search_input = find_rera_search_input(page)
    except Exception:
        return False

    search_input.scroll_into_view_if_needed()
    search_input.fill("")
    search_input.fill(str(value))
    search_input.evaluate(
        """
        element => {
            element.dispatchEvent(new Event("input", {bubbles: true}));
            element.dispatchEvent(new KeyboardEvent("keyup", {bubbles: true}));
            element.dispatchEvent(new Event("change", {bubbles: true}));
        }
        """
    )
    wait_for_rera_filter(page)
    return True


def mark_project_details_across_pages(
    page,
    registration_number,
    project_name,
    promoter_name,
    max_pages=75,
):
    last_result = {
        "success": False,
        "reason": "Selected project row not found.",
    }

    for _ in range(max_pages):
        last_result = mark_project_details_icon(
            page,
            registration_number,
            project_name,
            promoter_name,
        )

        if last_result.get("success"):
            return last_result

        next_button = find_rera_next_button(page)
        if next_button is None:
            break

        table = read_rera_table(page)
        previous = json.dumps(table.get("rows", [])[:1], ensure_ascii=False)

        next_button.click(force=True)
        wait_for_rera_filter(page)

        current = json.dumps(
            read_rera_table(page).get("rows", [])[:1],
            ensure_ascii=False,
        )

        if current == previous:
            break

    return last_result


def read_rera_table(page):
    return page.evaluate(
        r"""
        () => {
            const clean = value => (value || "")
                .normalize("NFKC")
                .replace(/\u00a0/g, " ")
                .replace(/\s+/g, " ")
                .trim();

            const visible = element => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden";
            };

            const tables = Array.from(document.querySelectorAll("table"))
                .filter(visible);

            const table = tables.find(candidate => {
                const headers = Array.from(
                    candidate.querySelectorAll("thead th, tr:first-child th")
                ).map(header => clean(
                    header.innerText || header.textContent
                ).toLowerCase());

                return headers.some(text => text.includes("promoter")) &&
                    headers.some(text => text.includes("project"));
            });

            if (!table) return {headers: [], rows: []};

            let headers = Array.from(table.querySelectorAll("thead th"))
                .map(header => clean(header.innerText || header.textContent));

            if (!headers.length) {
                headers = Array.from(table.querySelectorAll("tr:first-child th"))
                    .map(header => clean(header.innerText || header.textContent));
            }

            const rows = Array.from(table.querySelectorAll("tbody tr"))
                .filter(visible)
                .map(row => ({
                    values: Array.from(row.querySelectorAll("td"))
                        .map(cell => clean(cell.innerText || cell.textContent))
                }))
                .filter(row => row.values.some(Boolean));

            return {headers, rows};
        }
        """
    )


def header_index(headers, predicate):
    for index, header in enumerate(headers):
        if predicate(rera_norm(header)):
            return index
    return -1


def projects_from_table(table):
    headers = table.get("headers", [])
    rows = table.get("rows", [])

    registration_index = header_index(
        headers,
        lambda value: "registration" in value,
    )
    promoter_index = header_index(
        headers,
        lambda value: "promoter" in value,
    )
    project_index = header_index(
        headers,
        lambda value: (
            "project" in value
            and "view" not in value
            and "detail" not in value
        ),
    )
    type_index = header_index(
        headers,
        lambda value: value == "type" or "project type" in value,
    )
    district_index = header_index(
        headers,
        lambda value: "district" in value,
    )
    taluk_index = header_index(
        headers,
        lambda value: "taluk" in value,
    )

    projects = []

    for row in rows:
        values = row.get("values", [])

        def at(index):
            return values[index] if 0 <= index < len(values) else ""

        registration = at(registration_index)
        if not registration:
            registration = next(
                (
                    value
                    for value in values
                    if "PRM/KA/RERA" in value.upper()
                ),
                "",
            )

        projects.append(
            {
                "registration_number": registration,
                "promoter_name": at(promoter_index),
                "project_name": at(project_index),
                "project_type": at(type_index),
                "district": at(district_index),
                "taluk": at(taluk_index),
            }
        )

    return projects


def find_rera_next_button(page):
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
                classes = (item.get_attribute("class") or "").lower()
                aria_disabled = (item.get_attribute("aria-disabled") or "").lower()
                if (
                    item.is_visible()
                    and "disabled" not in classes
                    and aria_disabled != "true"
                ):
                    return item
            except Exception:
                continue

    return None


rera_read_table = read_rera_table
rera_projects_from_table = projects_from_table
rera_next_button = find_rera_next_button


def search_rera_projects_on_open_page(
    page,
    payload,
):
    """
    Searches using an already-open RERA browser page.

    This function does not open or close a browser.
    Therefore, when one result is found, the same page can
    continue directly to View Project Details and Print.
    """

    query = rerapdf_clean(payload.get("query", ""))
    search_type = rerapdf_norm(payload.get("searchType", ""))

    try:
        max_pages = int(payload.get("maxPages", 25))
    except (TypeError, ValueError):
        max_pages = 25

    try:
        max_results = int(payload.get("maxResults", 500))
    except (TypeError, ValueError):
        max_results = 500

    max_pages = max(1, min(max_pages, 100))
    max_results = max(1, min(max_results, 5000))

    if search_type not in {"promoter", "project", "registration"}:
        raise RuntimeError(
            "searchType must be promoter, project, or registration."
        )

    if len(query) < 2:
        raise RuntimeError("Enter at least two characters.")

    rerapdf_fill_search(page, query)

    results = []
    seen = set()
    normalized_query = rerapdf_norm(query)
    registration_query = re.sub(r"\s+", "", normalized_query)

    for _ in range(max_pages):
        table_data = rera_read_table(page)
        projects = rera_projects_from_table(table_data)

        for project in projects:
            if search_type == "promoter":
                searched_value = project.get("promoter_name", "")
            elif search_type == "project":
                searched_value = project.get("project_name", "")
            else:
                searched_value = project.get("registration_number", "")

            normalized_value = rerapdf_norm(searched_value)

            if search_type == "registration":
                registration_value = re.sub(r"\s+", "", normalized_value)
                if (
                    registration_query not in registration_value
                    and registration_value not in registration_query
                ):
                    continue
            elif normalized_query not in normalized_value:
                continue

            unique_key = "|".join(
                [
                    rerapdf_norm(project.get("registration_number", "")),
                    rerapdf_norm(project.get("project_name", "")),
                    rerapdf_norm(project.get("promoter_name", "")),
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

        current_table = rera_read_table(page)
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

def search_rera_projects(data):
    query = rera_clean(data.get("query"))
    search_type = rera_norm(data.get("searchType"))

    if search_type not in {"promoter", "project"}:
        raise ValueError("searchType must be 'promoter' or 'project'.")
    if len(query) < 2:
        raise ValueError("Enter at least two characters.")

    max_pages = max(1, min(int(data.get("maxPages", 25)), 100))
    max_results = max(1, min(int(data.get("maxResults", 500)), 5000))

    with sync_playwright() as playwright:
        browser = context = page = None
        try:
            browser, context = create_rera_browser(
                playwright,
                headless=data.get("headless", True),
                load_assets=False,
            )
            page = open_rera_page(context)
            fill_rera_search(page, query)

            results = []
            seen = set()

            for _ in range(max_pages):
                table = read_rera_table(page)
                projects = projects_from_table(table)

                for project in projects:
                    searched_value = (
                        project.get("promoter_name", "")
                        if search_type == "promoter"
                        else project.get("project_name", "")
                    )

                    if rera_norm(query) not in rera_norm(searched_value):
                        continue

                    key = "|".join(
                        [
                            rera_norm(project.get("registration_number")),
                            rera_norm(project.get("project_name")),
                            rera_norm(project.get("promoter_name")),
                        ]
                    )

                    if key in seen:
                        continue

                    seen.add(key)
                    results.append(project)

                    if len(results) >= max_results:
                        break

                if len(results) >= max_results:
                    break

                next_button = find_rera_next_button(page)
                if next_button is None:
                    break

                previous = json.dumps(table.get("rows", [])[:1], ensure_ascii=False)
                next_button.click(force=True)
                wait_for_rera_filter(page)
                current = json.dumps(
                    read_rera_table(page).get("rows", [])[:1],
                    ensure_ascii=False,
                )

                if current == previous:
                    break

            return {
                "success": True,
                "type": "RERA_PROJECT_SEARCH",
                "searchType": search_type,
                "query": query,
                "count": len(results),
                "results": results,
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


# =========================================================
# COMMON HELPERS
# =========================================================

def rerapdf_clean(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    text = text.replace(
        "\u00a0",
        " ",
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def rerapdf_norm(value: Any) -> str:
    return rerapdf_clean(
        value
    ).lower()


def rerapdf_safe(value: Any) -> str:
    text = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]+',
        "_",
        rerapdf_clean(value),
    )

    text = re.sub(
        r"\s+",
        "_",
        text,
    ).strip("._")

    return (
        text or "RERA_PROJECT"
    )[:120]


# =========================================================
# BROWSER
# =========================================================

def rerapdf_launch(
    playwright,
    headless: bool,
):
    browser = playwright.chromium.launch(
        headless=bool(headless),
        slow_mo=80,
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
        accept_downloads=True,
        ignore_https_errors=True,
    )

    return browser, context


def rerapdf_open_portal(context):
    last_error = None

    for attempt in range(1, 4):
        page = context.new_page()

        page.set_default_timeout(
            45000
        )

        page.set_default_navigation_timeout(
            60000
        )

        try:
            # Do not wait for domcontentloaded.
            # This portal may keep some resources pending.
            page.goto(
                RERA_URL,
                wait_until="commit",
                timeout=45000,
            )

            page.wait_for_selector(
                (
                    ".dataTables_filter input, "
                    "input[type='search'], "
                    "table"
                ),
                state="attached",
                timeout=120000,
            )

            page.wait_for_timeout(
                2500
            )

            return page

        except Exception as error:
            last_error = error

            try:
                page.close()
            except Exception:
                pass

            if attempt < 3:
                time.sleep(
                    attempt * 2
                )

    raise RuntimeError(
        "Unable to open Karnataka "
        f"RERA portal: {last_error}"
    )


# =========================================================
# SEARCH FIELD
# =========================================================

def rerapdf_find_search(page):
    selectors = [
        ".dataTables_filter input:visible",
        "input[type='search']:visible",
        (
            "input[placeholder*='Search' i]"
            ":visible"
        ),
        (
            "input[aria-label*='Search' i]"
            ":visible"
        ),
    ]

    for selector in selectors:
        items = page.locator(
            selector
        )

        for index in range(
            items.count()
        ):
            item = items.nth(
                index
            )

            try:
                if (
                    item.is_visible()
                    and item.is_enabled()
                ):
                    return item
            except Exception:
                continue

    raise RuntimeError(
        "RERA search field was not found."
    )


def rerapdf_fill_search(
    page,
    value: str,
):
    field = rerapdf_find_search(
        page
    )

    field.scroll_into_view_if_needed()

    field.fill("")
    field.fill(value)

    field.evaluate(
        """
        element => {
            element.dispatchEvent(
                new Event(
                    "input",
                    {bubbles: true}
                )
            );

            element.dispatchEvent(
                new KeyboardEvent(
                    "keyup",
                    {bubbles: true}
                )
            );

            element.dispatchEvent(
                new Event(
                    "change",
                    {bubbles: true}
                )
            );
        }
        """
    )

    page.wait_for_timeout(
        2200
    )


# =========================================================
# FIND VIEW PROJECT DETAILS ICON
# =========================================================

def rerapdf_mark_details(
    page,
    registration: str,
    project: str,
    promoter: str,
):
    return page.evaluate(
        r"""
        ({
            registration,
            project,
            promoter
        }) => {
            const normalize = value =>
                (value || "")
                    .normalize("NFKC")
                    .replace(/\u00a0/g, " ")
                    .replace(/\s+/g, " ")
                    .trim()
                    .toLowerCase();

            const visible = element => {
                if (!element) {
                    return false;
                }

                const rectangle =
                    element.getBoundingClientRect();

                const style =
                    getComputedStyle(element);

                return (
                    rectangle.width > 0 &&
                    rectangle.height > 0 &&
                    style.display !== "none" &&
                    style.visibility !== "hidden"
                );
            };

            const table = Array.from(
                document.querySelectorAll("table")
            )
                .filter(visible)
                .find(candidate => {
                    const headers = Array.from(
                        candidate.querySelectorAll(
                            "thead th, " +
                            "tr:first-child th"
                        )
                    ).map(header =>
                        normalize(
                            header.innerText ||
                            header.textContent
                        )
                    );

                    return (
                        headers.some(
                            value =>
                                value.includes(
                                    "promoter"
                                )
                        ) &&
                        headers.some(
                            value =>
                                value.includes(
                                    "project"
                                )
                        )
                    );
                });

            if (!table) {
                return {
                    success: false,
                    reason:
                        "Project table not found"
                };
            }

            let headers = Array.from(
                table.querySelectorAll(
                    "thead th"
                )
            ).map(header =>
                normalize(
                    header.innerText ||
                    header.textContent
                )
            );

            if (!headers.length) {
                headers = Array.from(
                    table.querySelectorAll(
                        "tr:first-child th"
                    )
                ).map(header =>
                    normalize(
                        header.innerText ||
                        header.textContent
                    )
                );
            }

            const registrationIndex =
                headers.findIndex(
                    value =>
                        value.includes(
                            "registration"
                        )
                );

            const promoterIndex =
                headers.findIndex(
                    value =>
                        value.includes(
                            "promoter"
                        )
                );

            const projectIndex =
                headers.findIndex(
                    value =>
                        value.includes(
                            "project"
                        ) &&
                        !value.includes(
                            "view"
                        ) &&
                        !value.includes(
                            "detail"
                        )
                );

            let detailsIndex =
                headers.findIndex(
                    value =>
                        value.includes(
                            "view project details"
                        ) ||
                        (
                            value.includes("view") &&
                            value.includes("project")
                        )
                );

            const wantedRegistration =
                normalize(registration);

            const wantedProject =
                normalize(project);

            const wantedPromoter =
                normalize(promoter);

            let matchedRow = null;

            const rows = Array.from(
                table.querySelectorAll(
                    "tbody tr"
                )
            ).filter(visible);

            for (const row of rows) {
                const cells = Array.from(
                    row.querySelectorAll("td")
                );

                if (!cells.length) {
                    continue;
                }

                const values = cells.map(
                    cell =>
                        normalize(
                            cell.innerText ||
                            cell.textContent
                        )
                );

                const registrationValue =
                    registrationIndex >= 0
                        ? values[
                            registrationIndex
                        ]
                        : (
                            values.find(
                                value =>
                                    value.includes(
                                        "prm/ka/rera"
                                    )
                            ) || ""
                        );

                const projectValue =
                    projectIndex >= 0
                        ? values[
                            projectIndex
                        ]
                        : "";

                const promoterValue =
                    promoterIndex >= 0
                        ? values[
                            promoterIndex
                        ]
                        : "";

                const registrationMatches =
                    wantedRegistration &&
                    (
                        registrationValue ===
                            wantedRegistration ||
                        registrationValue.includes(
                            wantedRegistration
                        ) ||
                        wantedRegistration.includes(
                            registrationValue
                        )
                    );

                const namesMatch =
                    wantedProject &&
                    projectValue.includes(
                        wantedProject
                    ) &&
                    (
                        !wantedPromoter ||
                        promoterValue.includes(
                            wantedPromoter
                        )
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
                    reason:
                        "Selected project row not found"
                };
            }

            const cells = Array.from(
                matchedRow.querySelectorAll(
                    "td"
                )
            );

            if (
                detailsIndex < 0 &&
                projectIndex >= 0
            ) {
                detailsIndex =
                    projectIndex + 1;
            }

            if (
                detailsIndex < 0 ||
                detailsIndex >= cells.length
            ) {
                detailsIndex =
                    cells.findIndex(
                        cell =>
                            cell.querySelector(
                                "a, button, input, " +
                                "[onclick], " +
                                "[role='button'], " +
                                "img, svg, i"
                            )
                    );
            }

            if (detailsIndex < 0) {
                return {
                    success: false,
                    reason:
                        "View Project Details column not found"
                };
            }

            const detailsCell =
                cells[detailsIndex];

            const icon =
                detailsCell.querySelector(
                    "img, svg, i, span"
                );

            const target =
                detailsCell.querySelector(
                    "a[href], button, " +
                    "input[type='button'], " +
                    "input[type='submit'], " +
                    "[role='button'], " +
                    "[onclick]"
                ) ||
                (
                    icon &&
                    (
                        icon.closest(
                            "a, button, " +
                            "[role='button'], " +
                            "[onclick]"
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
            "registration":
                registration,
            "project":
                project,
            "promoter":
                promoter,
        },
    )


# =========================================================
# WAIT FOR PROJECT DETAILS
# =========================================================

def rerapdf_page_score(page) -> int:
    try:
        text = rerapdf_norm(
            page.locator("body").inner_text(
                timeout=5000
            )
        )
    except Exception:
        return 0

    markers = [
        "project registration details",
        "promoter details",
        "authorized signatory",
        "project details",
        "land details",
        "land survey details",
        "uploaded documents",
        "bank details",
    ]

    return (
        sum(
            100
            for marker in markers
            if marker in text
        )
        +
        min(
            len(text),
            50000,
        ) // 100
    )


def rerapdf_wait_details(
    context,
    list_page,
    pages_before,
    timeout=60,
):
    deadline = (
        time.time() + timeout
    )

    best_page = None
    best_score = 0

    while time.time() < deadline:
        candidates = [
            list_page,
            *[
                page
                for page in context.pages
                if page not in pages_before
            ],
        ]

        for candidate in candidates:
            if candidate.is_closed():
                continue

            score = rerapdf_page_score(
                candidate
            )

            if score > best_score:
                best_page = candidate
                best_score = score

            if score >= 300:
                return candidate

        list_page.wait_for_timeout(
            350
        )

    if (
        best_page is not None
        and best_score >= 250
    ):
        return best_page

    raise RuntimeError(
        "Project Registration Details "
        "did not open."
    )


def rerapdf_wait_stable(
    page,
    timeout=30,
):
    deadline = (
        time.time() + timeout
    )

    previous = None
    stable_count = 0

    while time.time() < deadline:
        try:
            current = page.evaluate(
                """
                () => ({
                    textLength:
                        (
                            document.body.innerText
                            || ""
                        ).length,

                    height:
                        Math.max(
                            document.body.scrollHeight,
                            document.documentElement
                                .scrollHeight
                        ),

                    loadedImages:
                        Array.from(
                            document.images
                        ).filter(
                            image =>
                                image.complete
                        ).length,

                    totalImages:
                        document.images.length
                })
                """
            )

        except Exception:
            page.wait_for_timeout(
                400
            )
            continue

        if (
            current == previous
            and current["textLength"] > 800
        ):
            stable_count += 1

            if stable_count >= 3:
                return
        else:
            stable_count = 0

        previous = current

        page.wait_for_timeout(
            500
        )


# =========================================================
# PRINT BUTTON
# =========================================================

def rerapdf_find_print(page):
    selectors = [
        "button:has-text('Print'):visible",
        "a:has-text('Print'):visible",
        "input[value*='Print' i]:visible",
        "[onclick*='print' i]:visible",
    ]

    for selector in selectors:
        items = page.locator(
            selector
        )

        for index in range(
            items.count()
        ):
            item = items.nth(
                index
            )

            try:
                if item.is_visible():
                    return item
            except Exception:
                continue

    raise RuntimeError(
        "Print button beside Project "
        "Registration Details was not found."
    )


def rerapdf_install_print_interceptor(page):
    page.evaluate(
        """
        () => {
            window.__reraPrintRequested =
                false;

            try {
                Object.defineProperty(
                    window,
                    "print",
                    {
                        configurable: true,
                        writable: true,

                        value: () => {
                            window
                                .__reraPrintRequested =
                                true;
                        }
                    }
                );
            }
            catch (error) {
                window.print = () => {
                    window
                        .__reraPrintRequested =
                        true;
                };
            }
        }
        """
    )


# =========================================================
# EXTRACT ONLY PROJECT DETAILS
# =========================================================

def rerapdf_serialize_details(page):
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

            /*
            Trigger the same layout lifecycle used by
            Chrome Print Preview.
            */
            window.dispatchEvent(
                new Event("beforeprint")
            );

            document.dispatchEvent(
                new Event("beforeprint")
            );

            const heading = Array.from(
                document.querySelectorAll(
                    "h1, h2, h3, h4, h5, " +
                    "div, span"
                )
            ).find(element =>
                normalize(
                    element.innerText ||
                    element.textContent
                ) ===
                "project registration details"
            );

            if (!heading) {
                throw new Error(
                    "Project Registration Details heading not found"
                );
            }

            /*
            Prefer the details modal/dialog. This prevents
            the underlying Project Applications table and
            site footer from appearing in the PDF.
            */
            let root = heading.closest(
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

                    const markerCount = [
                        "project registration details",
                        "promoter details",
                        "project details",
                        "authorized signatory",
                        "land details",
                        "uploaded documents"
                    ].filter(
                        marker =>
                            text.includes(marker)
                    ).length;

                    if (
                        markerCount >= 3 &&
                        text.length > 1000
                    ) {
                        root = current;
                        break;
                    }

                    current =
                        current.parentElement;
                }
            }

            if (!root) {
                root = document.body;
            }

            /*
            Canvas pixels are not included by outerHTML.
            Convert maps/charts to images before cloning.
            */
            root.querySelectorAll(
                "canvas"
            ).forEach(canvas => {
                try {
                    const image =
                        document.createElement(
                            "img"
                        );

                    image.src =
                        canvas.toDataURL(
                            "image/png"
                        );

                    image.style.cssText =
                        canvas.style.cssText;

                    image.width =
                        canvas.width;

                    image.height =
                        canvas.height;

                    canvas.replaceWith(
                        image
                    );
                }
                catch (error) {
                }
            });

            /*
            Convert relative links/images to absolute URLs.
            */
            root.querySelectorAll(
                "[src]"
            ).forEach(element => {
                try {
                    element.setAttribute(
                        "src",
                        element.src
                    );
                }
                catch (error) {
                }
            });

            root.querySelectorAll(
                "a[href]"
            ).forEach(element => {
                try {
                    element.setAttribute(
                        "href",
                        element.href
                    );
                }
                catch (error) {
                }
            });

            /*
            Preserve entered form values.
            */
            root.querySelectorAll(
                "input, textarea, select"
            ).forEach(element => {
                if (
                    element.tagName ===
                    "TEXTAREA"
                ) {
                    element.textContent =
                        element.value;
                }
                else if (
                    element.tagName ===
                    "SELECT"
                ) {
                    Array.from(
                        element.options
                    ).forEach(option =>
                        option.toggleAttribute(
                            "selected",
                            option.selected
                        )
                    );
                }
                else {
                    element.setAttribute(
                        "value",
                        element.value || ""
                    );
                }
            });

            /*
            Reveal every tab/collapsed section so all
            Promoter, Land, Project, Bank, Document and
            other data becomes part of the PDF.
            */
            root.querySelectorAll(
                ".tab-pane, " +
                ".collapse, " +
                ".accordion-collapse, " +
                ".panel-collapse, " +
                "[hidden]"
            ).forEach(element => {
                element.hidden = false;

                element.removeAttribute(
                    "aria-hidden"
                );

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
                    document.querySelectorAll(
                        "link[rel='stylesheet']"
                    )
                ).map(link =>
                    `<link rel="stylesheet" ` +
                    `href="${link.href}" ` +
                    `media="all">`
                ),

                ...Array.from(
                    document.querySelectorAll(
                        "style"
                    )
                ).map(style =>
                    style.outerHTML
                )
            ].join("\n");

            return {
                baseUrl:
                    location.href,

                title:
                    document.title ||
                    "RERA Project Details",

                bodyClass:
                    document.body.className ||
                    "",

                styleAssets:
                    styleAssets,

                rootHtml:
                    root.outerHTML
            };
        }
        """
    )


# =========================================================
# CREATE CLEAN PRINT PAGE
# =========================================================

def rerapdf_create_clean_page(
    context,
    details_page,
):
    data = rerapdf_serialize_details(
        details_page
    )

    export_page = context.new_page()

    export_page.set_default_timeout(
        60000
    )

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
                margin: 10mm 8mm 12mm 8mm;
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
            [data-rera-export-root]
                .modal-dialog,
            [data-rera-export-root]
                .modal-content,
            [data-rera-export-root]
                .modal-body {{
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

            [data-rera-export-root]
                .tab-pane,
            [data-rera-export-root]
                .collapse,
            [data-rera-export-root]
                .accordion-collapse,
            [data-rera-export-root]
                .panel-collapse {{
                display: block !important;
                visibility: visible !important;
                opacity: 1 !important;
                height: auto !important;
                max-height: none !important;
                overflow: visible !important;
            }}

            [data-rera-export-root]
                button,
            [data-rera-export-root]
                input[type="button"],
            [data-rera-export-root]
                input[type="submit"],
            [data-rera-export-root]
                .close,
            [data-rera-export-root]
                .nav-tabs,
            [data-rera-export-root]
                .dataTables_filter,
            [data-rera-export-root]
                .dataTables_paginate,
            [data-rera-export-root]
                .dataTables_info {{
                display: none !important;
            }}

            * {{
                -webkit-print-color-adjust:
                    exact !important;

                print-color-adjust:
                    exact !important;

                box-sizing:
                    border-box;
            }}

            table {{
                border-collapse:
                    collapse !important;

                width:
                    100% !important;
            }}

            thead {{
                display:
                    table-header-group !important;
            }}

            tfoot {{
                display:
                    table-footer-group !important;
            }}

            tr,
            img,
            .panel,
            .card {{
                break-inside:
                    avoid;

                page-break-inside:
                    avoid;
            }}

            img {{
                max-width:
                    100% !important;

                height:
                    auto !important;
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
            if (
                document.fonts &&
                document.fonts.ready
            ) {
                await document.fonts.ready;
            }

            await Promise.all(
                Array.from(
                    document.images
                ).map(image => {
                    if (image.complete) {
                        return Promise.resolve();
                    }

                    return new Promise(
                        resolve => {
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

                            setTimeout(
                                resolve,
                                10000
                            );
                        }
                    );
                })
            );
        }
        """
    )

    export_page.emulate_media(
        media="print"
    )

    export_page.wait_for_timeout(
        1000
    )

    return export_page


# =========================================================
# SAVE EVERY PAGE INTO ONE PDF
# =========================================================

def rerapdf_save(
    page,
    output_path,
):
    page.pdf(
        path=output_path,

        format="A4",
        landscape=False,

        print_background=True,
        prefer_css_page_size=False,

        # Makes wide RERA tables fit clearly.
        scale=0.88,

        margin={
            "top": "10mm",
            "right": "7mm",
            "bottom": "12mm",
            "left": "7mm",
        },

        display_header_footer=True,

        header_template=(
            "<div style='"
            "width:100%;"
            "font-size:8px;"
            "padding:0 8mm;"
            "text-align:center;'>"
            "RERA Project Details"
            "</div>"
        ),

        footer_template=(
            "<div style='"
            "width:100%;"
            "font-size:8px;"
            "padding:0 8mm;'>"

            "<span class='url'></span>"

            "<span style='float:right'>"
            "<span class='pageNumber'></span>"
            "/"
            "<span class='totalPages'></span>"
            "</span>"

            "</div>"
        ),
    )

    if (
        not os.path.isfile(
            output_path
        )
        or os.path.getsize(
            output_path
        ) < 30000
    ):
        raise RuntimeError(
            "Generated RERA PDF is "
            "blank or incomplete."
        )


# =========================================================
# COMPLETE PDF FLOW
# =========================================================

def rerapdf_create(data):
    registration = rerapdf_clean(
        data.get(
            "registrationNumber"
        )
    )

    project = rerapdf_clean(
        data.get(
            "projectName"
        )
    )

    promoter = rerapdf_clean(
        data.get(
            "promoterName"
        )
    )

    if (
        not registration
        and not project
    ):
        raise RuntimeError(
            "registrationNumber or "
            "projectName is required."
        )

    with sync_playwright() as playwright:
        browser = None
        context = None
        active_page = None

        try:
            browser, context = (
                rerapdf_launch(
                    playwright,
                    bool(
                        data.get(
                            "headless",
                            True,
                        )
                    ),
                )
            )

            list_page = (
                rerapdf_open_portal(
                    context
                )
            )

            active_page = list_page

            # ---------------------------------------------
            # SEARCH EXACT PROJECT
            # ---------------------------------------------

            rerapdf_fill_search(
                list_page,
                registration or project,
            )

            marked = rerapdf_mark_details(
                list_page,
                registration,
                project,
                promoter,
            )

            if (
                not marked.get("success")
                and project
            ):
                rerapdf_fill_search(
                    list_page,
                    project,
                )

                marked = (
                    rerapdf_mark_details(
                        list_page,
                        registration,
                        project,
                        promoter,
                    )
                )

            if not marked.get(
                "success"
            ):
                raise RuntimeError(
                    marked.get("reason")
                    or
                    "View Project Details "
                    "icon not found."
                )

            # ---------------------------------------------
            # CLICK VIEW PROJECT DETAILS
            # ---------------------------------------------

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

            details_page = (
                rerapdf_wait_details(
                    context,
                    list_page,
                    pages_before,
                )
            )

            active_page = details_page

            rerapdf_wait_stable(
                details_page
            )

            # ---------------------------------------------
            # CLICK PRINT
            # ---------------------------------------------

            rerapdf_install_print_interceptor(
                details_page
            )

            print_button = (
                rerapdf_find_print(
                    details_page
                )
            )

            print_button.scroll_into_view_if_needed()

            print_button.click(
                force=True,
                no_wait_after=True,
            )

            details_page.wait_for_timeout(
                1000
            )

            # Trigger the same before-print phase
            # used by Chrome Print Preview.
            details_page.evaluate(
                """
                () => {
                    window.dispatchEvent(
                        new Event(
                            "beforeprint"
                        )
                    );

                    document.dispatchEvent(
                        new Event(
                            "beforeprint"
                        )
                    );
                }
                """
            )

            details_page.wait_for_timeout(
                800
            )

            # ---------------------------------------------
            # CREATE CLEAN PAGE FROM DETAILS ONLY
            # ---------------------------------------------

            export_page = (
                rerapdf_create_clean_page(
                    context,
                    details_page,
                )
            )

            active_page = export_page

            # ---------------------------------------------
            # SAVE COMPLETE MULTI-PAGE PDF
            # ---------------------------------------------

            timestamp = time.strftime(
                "%Y%m%d_%H%M%S"
            )

            project_file_part = rerapdf_safe(
                project or registration
            )

            registration_file_part = rerapdf_safe(
                registration or "PROJECT"
            )

            filename = (
                f"RERA_{project_file_part}_"
                f"{registration_file_part}_"
                f"{timestamp}.pdf"
            )

            output_path = os.path.join(
                RERA_PDF_DIR,
                filename,
            )

            rerapdf_save(
                export_page,
                output_path,
            )

            return (
                output_path,
                filename,
            )

        except Exception as error:
            raise RuntimeError(
                "RERA PDF creation failed: "
                f"{error}"
            ) from error

        finally:
            if context is not None:
                context.close()

            if browser is not None:
                browser.close()

def create_rera_pdf(data):
    return rerapdf_create(data)

def create_rera_pdf_from_open_list_page(
    context,
    list_page,
    selected_project,
):
    """
    Continues from the browser page already used for search.

    It does not reopen the Karnataka RERA portal.
    """

    registration = rerapdf_clean(
        selected_project.get(
            "registration_number",
            "",
        )
    )

    project = rerapdf_clean(
        selected_project.get(
            "project_name",
            "",
        )
    )

    promoter = rerapdf_clean(
        selected_project.get(
            "promoter_name",
            "",
        )
    )

    if not registration and not project:
        raise RuntimeError(
            "Selected RERA project does not contain "
            "a registration number or project name."
        )

    # Return to the first filtered page when required.
    first_button_selectors = [
        (
            "a.paginate_button.first"
            ":not(.disabled):visible"
        ),
        (
            "li.first:not(.disabled) "
            "a:visible"
        ),
        "a:has-text('First'):visible",
    ]

    for selector in first_button_selectors:
        first_button = list_page.locator(
            selector
        )

        if first_button.count():
            try:
                candidate = first_button.first

                classes = (
                    candidate.get_attribute(
                        "class"
                    )
                    or ""
                ).lower()

                if (
                    candidate.is_visible()
                    and "disabled" not in classes
                ):
                    candidate.click(
                        force=True
                    )

                    list_page.wait_for_timeout(
                        1000
                    )

                break

            except Exception:
                continue

    # Filter the existing page by the exact project.
    rerapdf_fill_search(
        list_page,
        registration or project,
    )

    marked = rerapdf_mark_details(
        list_page,
        registration,
        project,
        promoter,
    )

    if (
        not marked.get("success")
        and project
    ):
        rerapdf_fill_search(
            list_page,
            project,
        )

        marked = rerapdf_mark_details(
            list_page,
            registration,
            project,
            promoter,
        )

    if not marked.get("success"):
        raise RuntimeError(
            marked.get("reason")
            or "View Project Details icon was not found."
        )

    # -----------------------------------------------------
    # CLICK VIEW PROJECT DETAILS
    # -----------------------------------------------------

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

    details_page = rerapdf_wait_details(
        context,
        list_page,
        pages_before,
    )

    rerapdf_wait_stable(
        details_page
    )

    # -----------------------------------------------------
    # CLICK PRINT
    # -----------------------------------------------------

    rerapdf_install_print_interceptor(
        details_page
    )

    print_button = rerapdf_find_print(
        details_page
    )

    print_button.scroll_into_view_if_needed()

    print_button.click(
        force=True,
        no_wait_after=True,
    )

    details_page.wait_for_timeout(
        1000
    )

    details_page.evaluate(
        """
        () => {
            window.dispatchEvent(
                new Event("beforeprint")
            );

            document.dispatchEvent(
                new Event("beforeprint")
            );
        }
        """
    )

    details_page.wait_for_timeout(
        800
    )

    # -----------------------------------------------------
    # COPY ONLY PROJECT DETAILS TO CLEAN PAGE
    # -----------------------------------------------------

    export_page = rerapdf_create_clean_page(
        context,
        details_page,
    )

    timestamp = time.strftime(
        "%Y%m%d_%H%M%S"
    )

    filename = (
        f"RERA_"
        f"{rerapdf_safe(project or registration)}_"
        f"{rerapdf_safe(registration or 'PROJECT')}_"
        f"{timestamp}.pdf"
    )

    output_path = os.path.join(
        RERA_PDF_DIR,
        filename,
    )

    try:
        rerapdf_save(
            export_page,
            output_path,
        )
    finally:
        try:
            export_page.close()
        except Exception:
            pass

    return output_path, filename

# =========================================================
# FASTAPI ROUTES
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
def rera_search(data: ReraSearchRequest):
    try:
        return search_rera_projects(data.model_dump())
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/rera/project-pdf")
def rera_project_pdf(data: ReraProjectPdfRequest):
    try:
        pdf_path, filename = create_rera_pdf(data.model_dump())

        return {
            "success": True,
            "type": "RERA_COMPLETE_PROJECT_PDF",
            "filename": filename,
            "pdf": pdf_path,
            "downloadUrl": f"/api/rera/download/{quote(filename)}",
            "file_size": os.path.getsize(pdf_path),
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.get("/api/rera/download/{filename}")
def rera_download(filename: str):
    safe_name = os.path.basename(filename)

    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF filename.",
        )

    pdf_path = os.path.join(RERA_DOWNLOAD_DIR, safe_name)

    if not os.path.isfile(pdf_path):
        raise HTTPException(
            status_code=404,
            detail="RERA PDF not found.",
        )

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=safe_name,
    )

@app.post("/api/rera")
def rera_unified_api(
    data: ReraUnifiedRequest,
):
    payload = data.model_dump()

    action = rerapdf_norm(
        payload.get("action", "")
    )

    # =====================================================
    # SEARCH
    # =====================================================

    if action == "search":
        browser = None
        context = None

        try:
            with sync_playwright() as playwright:
                browser, context = rerapdf_launch(
                    playwright,
                    bool(
                        payload.get(
                            "headless",
                            True,
                        )
                    ),
                )

                list_page = rerapdf_open_portal(
                    context
                )

                result = (
                    search_rera_projects_on_open_page(
                        list_page,
                        payload,
                    )
                )

                results = result.get(
                    "results",
                    [],
                )

                # -----------------------------------------
                # NO RESULTS
                # -----------------------------------------

                if not results:
                    return result

                # -----------------------------------------
                # MULTIPLE RESULTS
                #
                # Return JSON. The UI will show the
                # project-selection dropdown.
                # -----------------------------------------

                if len(results) > 1:
                    return result

                # -----------------------------------------
                # EXACTLY ONE RESULT
                #
                # Continue in this same browser and page.
                # Do not reopen the RERA portal.
                # -----------------------------------------

                output_path, filename = (
                    create_rera_pdf_from_open_list_page(
                        context,
                        list_page,
                        results[0],
                    )
                )

                return FileResponse(
                    path=output_path,
                    media_type="application/pdf",
                    filename=filename,
                    headers={
                        "X-RERA-Result-Mode":
                            "single-result-auto-pdf",
                    },
                )

        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "RERA search failed: "
                    f"{error}"
                ),
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

    # =====================================================
    # PDF FOR USER-SELECTED MULTIPLE RESULT
    # =====================================================

    if action == "pdf":
        try:
            output_path, filename = rerapdf_create(
                payload
            )

            return FileResponse(
                path=output_path,
                media_type="application/pdf",
                filename=filename,
                headers={
                    "X-RERA-Result-Mode":
                        "selected-result-pdf",
                },
            )

        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "RERA PDF creation failed: "
                    f"{error}"
                ),
            ) from error

    raise HTTPException(
        status_code=400,
        detail="action must be search or pdf.",
    )

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
    )



