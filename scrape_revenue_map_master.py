import json
from playwright.sync_api import sync_playwright

REVENUE_MAP_URL = "https://landrecords.karnataka.gov.in/service3/"


def get_options(page, index):
    return page.locator("select").nth(index).locator("option").evaluate_all("""
        opts => opts
            .map(o => o.textContent.trim())
            .filter(t => t && t.toLowerCase() !== "all" && !t.toLowerCase().includes("select"))
    """)


master = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=200)
    page = browser.new_page()

    page.goto(REVENUE_MAP_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("select", timeout=30000)

    districts = get_options(page, 0)

    for district in districts:
        print("District:", district)

        page.locator("select").nth(0).select_option(label=district)
        page.wait_for_timeout(1500)

        taluks = get_options(page, 1)
        master[district] = {}

        for taluk in taluks:
            print("  Taluk:", taluk)

            page.locator("select").nth(1).select_option(label=taluk)
            page.wait_for_timeout(1500)

            hoblis = get_options(page, 2)
            master[district][taluk] = {}

            for hobli in hoblis:
                print("    Hobli:", hobli)

                page.locator("select").nth(2).select_option(label=hobli)
                page.wait_for_timeout(1500)

                # Revenue Map portal usually shows village in table/search,
                # not always as dropdown. Keep empty list if no village dropdown exists.
                try:
                    villages = get_options(page, 3)
                except Exception:
                    villages = []

                master[district][taluk][hobli] = villages

    browser.close()

with open("revenue-map-master.json", "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False, indent=2)

print("Saved revenue-map-master.json")