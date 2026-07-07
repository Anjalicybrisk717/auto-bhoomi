import json
import os
from playwright.sync_api import sync_playwright

AKARBAND_URL = "https://bhoomojini.karnataka.gov.in/service39/"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "akarband-master.json")


def clean_options(options):
    final = []
    for opt in options:
        opt = opt.strip()
        if not opt:
            continue
        if "ಆಯ್ಕೆ" in opt or "select" in opt.lower() or "option" in opt.lower():
            continue
        final.append(opt)
    return final


def get_options(page, index):
    return clean_options(
        page.locator("select").nth(index).locator("option").evaluate_all(
            "opts => opts.map(o => o.textContent.trim())"
        )
    )


def select_option(page, index, label):
    page.locator("select").nth(index).select_option(label=label)
    page.wait_for_timeout(2500)


def main():
    master = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,
            args=["--lang=kn-IN", "--disable-features=Translate"],
        )

        context = browser.new_context(locale="kn-IN")
        page = context.new_page()

        page.goto(AKARBAND_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("select", timeout=30000)

        districts = get_options(page, 0)

        for district in districts:
            print("District:", district)
            master[district] = {}

            select_option(page, 0, district)

            taluks = get_options(page, 1)

            for taluk in taluks:
                print("  Taluk:", taluk)
                master[district][taluk] = {}

                select_option(page, 1, taluk)

                hoblis = get_options(page, 2)

                for hobli in hoblis:
                    print("    Hobli:", hobli)
                    master[district][taluk][hobli] = {}

                    select_option(page, 2, hobli)

                    villages = get_options(page, 3)

                    for village in villages:
                        print("      Village:", village)
                        master[district][taluk][hobli][village] = {}

        browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    print("Saved:", OUTPUT_FILE)


if __name__ == "__main__":
    main()
