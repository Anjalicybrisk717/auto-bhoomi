import json
from playwright.sync_api import sync_playwright

URL = "https://landrecords.karnataka.gov.in/service3/"
OUTPUT = "revenue-map-master.json"


def get_select_options(page, index):
    return page.locator("select").nth(index).locator("option").evaluate_all("""
        opts => opts
            .map(o => o.textContent.trim())
            .filter(t => t && t.toLowerCase() !== "all" && !t.toLowerCase().includes("select"))
    """)


def get_villages_from_table(page):
    return page.evaluate("""
        () => {
            const villages = [];
            const rows = Array.from(document.querySelectorAll("tr"));

            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll("td"))
                    .map(td => td.innerText.trim());

                // table columns: District, Taluk, Hobli, Village, Pdf File, KMZ File
                if (cells.length >= 4) {
                    const village = cells[3];
                    if (
                        village &&
                        village.toLowerCase() !== "village" &&
                        !villages.includes(village)
                    ) {
                        villages.push(village);
                    }
                }
            }

            return villages;
        }
    """)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=200)
    page = browser.new_page()

    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("select", timeout=30000)

    master = {}

    districts = get_select_options(page, 0)

    for district in districts:
        print("District:", district)

        page.locator("select").nth(0).select_option(label=district)
        page.wait_for_timeout(1500)

        taluks = get_select_options(page, 1)
        master[district] = {}

        for taluk in taluks:
            print("  Taluk:", taluk)

            page.locator("select").nth(1).select_option(label=taluk)
            page.wait_for_timeout(1500)

            hoblis = get_select_options(page, 2)
            master[district][taluk] = {}

            for hobli in hoblis:
                print("    Hobli:", hobli)

                page.locator("select").nth(2).select_option(label=hobli)
                page.wait_for_timeout(1000)

                # Map Type dropdown
                try:
                    page.locator("select").nth(3).select_option(label="All")
                except Exception:
                    pass

                page.wait_for_timeout(500)

                # Leave village search blank and click Search
                inputs = page.locator('input[type="text"]')
                if inputs.count() > 0:
                    inputs.first.fill("")

                page.locator('input[value="Search"], button:has-text("Search")').first.click()
                page.wait_for_timeout(2500)

                villages = get_villages_from_table(page)

                master[district][taluk][hobli] = {
                    "_villages": villages,
                    "_map_types": get_select_options(page, 3),
                }

                print("      Villages:", len(villages))

    browser.close()

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False, indent=2)

print("Saved:", OUTPUT)