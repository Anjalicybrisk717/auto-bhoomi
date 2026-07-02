import os
import time
import base64
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

BHOOMI_URL = "https://landrecords.karnataka.gov.in/Service2/"
MR_URL = "https://landrecords.karnataka.gov.in/Service11/MR_MutationExtract.aspx"
REVENUE_MAP_URL = "https://landrecords.karnataka.gov.in/service3/"
SURVEY_SKETCH_URL = "https://rdservices.karnataka.gov.in/service84/"

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


def safe_filename(value):
    return (
        str(value)
        .replace("/", "-")
        .replace("\\", "-")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )


def select_dropdown_by_text_contains(page, index, wanted_text):
    dropdown = page.locator("select").nth(index)
    dropdown.wait_for(state="visible", timeout=30000)

    selected = dropdown.evaluate(
        """
        (ddl, wantedText) => {
            const normalize = (s) =>
                (s || "").trim().toLowerCase().replace(/\\s+/g, " ");

            const wanted = normalize(wantedText);
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
                    return option.textContent.trim();
                }
            }

            if (wanted.includes("yalahanka") || wanted.includes("yelahanka")) {
                for (const option of options) {
                    const text = normalize(option.textContent);

                    if (
                        text.includes("bangalore north") ||
                        text.includes("bengaluru north") ||
                        text.includes("additional")
                    ) {
                        ddl.value = option.value;
                        ddl.dispatchEvent(new Event("input", { bubbles: true }));
                        ddl.dispatchEvent(new Event("change", { bubbles: true }));
                        return option.textContent.trim();
                    }
                }
            }

            return null;
        }
        """,
        wanted_text,
    )

    page.wait_for_timeout(2500)

    if not selected:
        options = dropdown.locator("option").evaluate_all(
            "opts => opts.map(o => o.textContent.trim())"
        )
        raise Exception(
            f"Could not select '{wanted_text}' in dropdown {index}. Options: {options}"
        )

    print(f"Selected dropdown {index}: {selected}")
    return selected


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


def fill_rtc_fields(page, data):
    page.wait_for_selector("select", timeout=30000)

    selects = page.locator("select")

    selects.nth(0).select_option(label=data["district"])
    time.sleep(2)

    selects.nth(1).select_option(label=data["taluk"])
    time.sleep(2)

    selects.nth(2).select_option(label=data["hobli"])
    time.sleep(2)

    selects.nth(3).select_option(label=data["village"])
    time.sleep(2)

    page.locator('input[placeholder="Survey Number"]').fill(str(data["surveyNumber"]))

    time.sleep(1)

    go_btn = page.locator('input[value="Go"], button:has-text("Go")').first

    try:
        with page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=15000,
        ):
            go_btn.click()
    except Exception:
        go_btn.click()

    page.wait_for_timeout(10000)


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
    view_btn = page.locator(
        'input[value="View"], button:has-text("View"), a:has-text("View")'
    ).first

    view_btn.wait_for(state="visible", timeout=30000)

    os.makedirs("rtc_downloads", exist_ok=True)

    pdf_path = os.path.join(
        "rtc_downloads",
        f"RTC_{safe_filename(data['district'])}_"
        f"{safe_filename(data['taluk'])}_"
        f"{safe_filename(data['village'])}_"
        f"{safe_filename(data['surveyNumber'])}.pdf",
    )

    try:
        with page.context.expect_page(timeout=15000) as new_page:
            view_btn.click(force=True)

        preview_page = new_page.value
        preview_page.wait_for_load_state("domcontentloaded")
        preview_page.wait_for_timeout(7000)

        preview_page.pdf(
            path=pdf_path,
            format="A4",
            landscape=True,
            print_background=True,
        )

    except Exception:
        view_btn.click(force=True)
        page.wait_for_timeout(7000)

        page.pdf(
            path=pdf_path,
            format="A4",
            landscape=True,
            print_background=True,
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

            file_info = page.evaluate(
                """
                (villageName) => {
                    const wanted = villageName.trim().toLowerCase();
                    const rows = Array.from(document.querySelectorAll("tr"));

                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll("td"));

                        if (cells.length < 6) continue;

                        const village = cells[3].innerText.trim().toLowerCase();

                        if (village !== wanted) continue;

                        function extract(cell) {
                            const link = cell.querySelector("a");

                            if (link && link.href) {
                                return link.href;
                            }

                            const img = cell.querySelector("img");

                            if (img) {
                                const parent = img.closest("a");

                                if (parent && parent.href) {
                                    return parent.href;
                                }
                            }

                            return null;
                        }

                        const pdfUrl = extract(cells[4]);

                        if (pdfUrl) {
                            return {
                                fileType: "pdf",
                                url: pdfUrl
                            };
                        }

                        const kmzUrl = extract(cells[5]);

                        if (kmzUrl) {
                            return {
                                fileType: "kmz",
                                url: kmzUrl
                            };
                        }

                        return null;
                    }

                    return null;
                }
                """,
                data["village"],
            )

            if not file_info:
                page.screenshot(
                    path="revenue-map-no-download-file.png",
                    full_page=True,
                )
                raise Exception("No PDF or KMZ file available for selected village.")

            file_type = file_info["fileType"]
            file_url = file_info["url"]

            download_path = os.path.join(
                "revenue_maps",
                f"{base_name}.{file_type}",
            )

            response = context.request.get(file_url)

            if not response.ok:
                raise Exception(f"Failed to download {file_type.upper()} file")

            with open(download_path, "wb") as f:
                f.write(response.body())

            return {
                "success": True,
                "type": "REVENUE_MAP",
                "file_type": file_type.upper(),
                "file": download_path,
                "pdf": download_path if file_type == "pdf" else None,
                "kmz": download_path if file_type == "kmz" else None,
                "source_url": file_url,
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

            search_clicked = page.evaluate(
                """
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
                """
            )

            if not search_clicked:
                raise Exception("Search button not found")

            page.wait_for_timeout(10000)

            view_clicked = page.evaluate(
                """
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
                """
            )

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
    
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
    )
