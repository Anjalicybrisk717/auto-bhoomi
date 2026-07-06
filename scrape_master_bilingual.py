import json
import time
from playwright.sync_api import sync_playwright

RTC_URL = "https://landrecords.karnataka.gov.in/Service2/"
AKARBAND_URL = "https://bhoomojini.karnataka.gov.in/service39/"

OUTPUT_FILE = "bhoomi-master-bilingual.json"


def clean(text):
    return " ".join(str(text).strip().split())


def get_options(page, index):
    return page.locator("select").nth(index).locator("option").evaluate_all(
        """
        opts => opts
            .map(o => o.textContent.trim())
            .filter(t =>
                t &&
                !t.toLowerCase().includes("select") &&
                !t.includes("--")
            )
        """
    )


def scrape_portal(url, language_name):
    data = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,
            args=["--disable-features=Translate"],
        )

        context = browser.new_context(locale="kn-IN")
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("select", timeout=30000)

        districts = get_options(page, 0)

        for district in districts:
            district = clean(district)
            print(language_name, "District:", district)

            page.locator("select").nth(0).select_option(label=district)
            page.wait_for_timeout(2000)

            taluks = get_options(page, 1)
            data[district] = {}

            for taluk in taluks:
                taluk = clean(taluk)
                print("  Taluk:", taluk)

                page.locator("select").nth(1).select_option(label=taluk)
                page.wait_for_timeout(2000)

                hoblis = get_options(page, 2)
                data[district][taluk] = {}

                for hobli in hoblis:
                    hobli = clean(hobli)
                    print("    Hobli:", hobli)

                    page.locator("select").nth(2).select_option(label=hobli)
                    page.wait_for_timeout(2000)

                    villages = get_options(page, 3)
                    villages = [clean(v) for v in villages]

                    data[district][taluk][hobli] = villages

        browser.close()

    return data


def merge_english_kannada(english_data, kannada_data):
    master = {}

    english_districts = list(english_data.keys())
    kannada_districts = list(kannada_data.keys())

    for d_index, eng_district in enumerate(english_districts):
        kan_district = kannada_districts[d_index] if d_index < len(kannada_districts) else ""

        master[eng_district] = {
            "kn": kan_district,
            "taluks": {}
        }

        eng_taluks = list(english_data[eng_district].keys())
        kan_taluks = list(kannada_data.get(kan_district, {}).keys())

        for t_index, eng_taluk in enumerate(eng_taluks):
            kan_taluk = kan_taluks[t_index] if t_index < len(kan_taluks) else ""

            master[eng_district]["taluks"][eng_taluk] = {
                "kn": kan_taluk,
                "hoblis": {}
            }

            eng_hoblis = list(english_data[eng_district][eng_taluk].keys())
            kan_hoblis = list(
                kannada_data
                .get(kan_district, {})
                .get(kan_taluk, {})
                .keys()
            )

            for h_index, eng_hobli in enumerate(eng_hoblis):
                kan_hobli = kan_hoblis[h_index] if h_index < len(kan_hoblis) else ""

                eng_villages = english_data[eng_district][eng_taluk][eng_hobli]
                kan_villages = (
                    kannada_data
                    .get(kan_district, {})
                    .get(kan_taluk, {})
                    .get(kan_hobli, [])
                )

                villages = {}

                for v_index, eng_village in enumerate(eng_villages):
                    kan_village = kan_villages[v_index] if v_index < len(kan_villages) else ""

                    villages[eng_village] = {
                        "kn": kan_village
                    }

                master[eng_district]["taluks"][eng_taluk]["hoblis"][eng_hobli] = {
                    "kn": kan_hobli,
                    "villages": villages
                }

    return master


if __name__ == "__main__":
    print("Scraping English portal...")
    english_data = scrape_portal(RTC_URL, "EN")

    print("Scraping Kannada portal...")
    kannada_data = scrape_portal(AKARBAND_URL, "KN")

    print("Merging...")
    master = merge_english_kannada(english_data, kannada_data)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    print("Saved:", OUTPUT_FILE)