import json
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BHOOMI_URL = "https://landrecords.karnataka.gov.in/Service2/"
OUTPUT_FILE = "bhoomi-master.json"
PARTIAL_FILE = "bhoomi-master-partial.json"


def delay(ms):
    time.sleep(ms / 1000)


def get_options(page, index):
    options = page.locator("select").nth(index).locator("option").all()

    values = []

    for option in options:
        text = option.inner_text().strip()
        text_lower = text.lower()

        if (
            text
            and "select" not in text_lower
            and "--" not in text_lower
            and text_lower != "0"
        ):
            values.append(text)

    return values


def wait_for_dropdown_to_populate(page, index, timeout=60000):
    page.wait_for_function(
        """
        (idx) => {
            const selects = document.querySelectorAll("select");
            if (!selects[idx]) return false;
            return selects[idx].options.length > 1;
        }
        """,
        arg=index,
        timeout=timeout
    )


def select_by_label_safely(page, index, label):
    dropdown = page.locator("select").nth(index)

    dropdown.wait_for(
        state="visible",
        timeout=60000
    )

    options = dropdown.locator("option").all()

    exact_value = None
    available_options = []

    for option in options:
        text = option.inner_text().strip()
        value = option.get_attribute("value")

        available_options.append(text)

        if text == label:
            exact_value = value

    if exact_value is None:
        print(f"Option not found in dropdown {index}: {label}")
        print("Available options:", available_options)
        return False

    dropdown.select_option(value=exact_value)
    delay(2500)

    return True


def save_master(master, filename=OUTPUT_FILE):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)


def scrape_bhoomi_master():
    master = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=150
        )

        page = browser.new_page()

        try:
            page.goto(
                BHOOMI_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_selector("select", timeout=60000)

            districts = get_options(page, 0)

            print("District count:", len(districts))
            print(districts)

            for district in districts:
                print(f"\nDistrict: {district}")
                master[district] = {}

                district_selected = select_by_label_safely(page, 0, district)

                if not district_selected:
                    continue

                wait_for_dropdown_to_populate(page, 1)
                taluks = get_options(page, 1)

                print(f"Taluk count for {district}:", len(taluks))

                for taluk in taluks:
                    print(f"  Taluk: {taluk}")
                    master[district][taluk] = {}

                    taluk_selected = select_by_label_safely(page, 1, taluk)

                    if not taluk_selected:
                        continue

                    wait_for_dropdown_to_populate(page, 2)
                    hoblis = get_options(page, 2)

                    print(f"  Hobli count for {district} > {taluk}:", len(hoblis))

                    for hobli in hoblis:
                        print(f"    Hobli: {hobli}")
                        master[district][taluk][hobli] = []

                        hobli_selected = select_by_label_safely(page, 2, hobli)

                        if not hobli_selected:
                            continue

                        wait_for_dropdown_to_populate(page, 3)
                        villages = get_options(page, 3)

                        print(
                            f"    Village count for {district} > {taluk} > {hobli}:",
                            len(villages)
                        )

                        master[district][taluk][hobli] = villages

                        save_master(master)

                    save_master(master)

                save_master(master)

            print("\nDONE: bhoomi-master.json created successfully")

        except Exception as error:
            print("\nScraping failed:", str(error))
            save_master(master, PARTIAL_FILE)
            print("Partial data saved to bhoomi-master-partial.json")

        finally:
            browser.close()


if __name__ == "__main__":
    scrape_bhoomi_master()