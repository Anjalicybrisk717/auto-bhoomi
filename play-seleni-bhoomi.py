import os
import json
import time
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

BHOOMI_URL = os.getenv(
    "BHOOMI_URL",
    "https://landrecords.karnataka.gov.in/Service2/"
)

app = FastAPI(title="Bhoomi RTC Automation API")


class RTCRequest(BaseModel):
    district: str
    taluk: str
    hobli: str
    village: str
    surveyNumber: str
    surnoc: str = "*"
    hissa: str = ""
    period: str = "Current Year"
    clientId: str = "default_client"
    headless: bool = False


class DropdownRequest(BaseModel):
    district: str | None = None
    taluk: str | None = None
    hobli: str | None = None
def clean_options(options):
    values = []
    for option in options:
        text = option.inner_text().strip()
        if text and "select" not in text.lower():
            values.append(text)
    return values


def fetch_rtc_with_playwright(data):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=data.get("headless", False),
            slow_mo=300
        )

        page = browser.new_page()

        try:
            page.goto(BHOOMI_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("select", timeout=30000)

            selects = page.locator("select")

            selects.nth(0).select_option(label=data["district"])
            time.sleep(1.2)

            selects.nth(1).select_option(label=data["taluk"])
            time.sleep(1.2)

            selects.nth(2).select_option(label=data["hobli"])
            time.sleep(1.2)

            selects.nth(3).select_option(label=data["village"])
            time.sleep(1.2)

            page.locator('input[placeholder="Survey Number"]').fill(
                data["surveyNumber"]
            )
            
           page.get_by_role("button", name="Go").click()
time.sleep(5)

# SURNOC
surnoc = page.locator("#ctl00_MainContent_ddlCSurnoc")
surnoc.wait_for(state="visible", timeout=30000)

options = surnoc.locator("option").all()

selected_surnoc = None

for option in options:
    text = option.inner_text().strip()
    value = option.get_attribute("value")

    if value and value != "0" and "select" not in text.lower():
        surnoc.select_option(value=value)
        selected_surnoc = text
        break

time.sleep(2)

# HISSA
hissa = page.locator("#ctl00_MainContent_ddlCHissa")
hissa.wait_for(state="visible", timeout=30000)

options = hissa.locator("option").all()

selected_hissa = None

for option in options:
    text = option.inner_text().strip()
    value = option.get_attribute("value")

    if value and value != "0" and "select" not in text.lower():
        hissa.select_option(value=value)
        selected_hissa = text
        break

time.sleep(2)

# PERIOD
period = page.locator("#ctl00_MainContent_ddlCPeriod")
period.wait_for(state="visible", timeout=30000)

options = period.locator("option").all()

selected_period = None

for option in options:
    text = option.inner_text().strip()
    value = option.get_attribute("value")

    if value and value != "0" and "select" not in text.lower():
        period.select_option(value=value)
        selected_period = text
        break

time.sleep(2)

# FETCH DETAILS
page.locator(
    'input[value="Fetch details"], button:has-text("Fetch details")'
).first.click()

print("Fetch Details clicked")

page.wait_for_timeout(8000)

# Wait for View button
page.wait_for_selector(
    'input[value="View"], button:has-text("View")',
    timeout=30000
)

# Click View
page.locator(
    'input[value="View"], button:has-text("View")'
).first.click()

print("View clicked")

page.wait_for_timeout(10000)
with page.context.expect_page() as new_page_info:

    page.locator(
        'input[value="View"], button:has-text("View")'
    ).first.click()

rtc_page = new_page_info.value

rtc_page.wait_for_load_state()

pdf_path = os.path.join(
    download_dir,
    f"RTC_{data['district']}_{data['surveyNumber']}.pdf"
)

rtc_page.pdf(
    path=pdf_path,
    format="A4",
    print_background=True
)

print("RTC PDF saved")
# page.screenshot(path="rtc-playwright-result.png", full_page=True)

return {
    "success": True,
    "engine": "playwright",
    "selected_surnoc": selected_surnoc,
    "selected_hissa": selected_hissa,
    "selected_period": selected_period,
    "pdf": pdf_path
}

    finally:
        driver.quit()


@app.post("/api/fetch-rtc/playwright")
def fetch_rtc_playwright(data: RTCRequest):
    try:
        return fetch_rtc_with_playwright(data.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/fetch-rtc/selenium")
def fetch_rtc_selenium(data: RTCRequest):
    try:
        return fetch_rtc_with_selenium(data.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/fetch-rtc/auto")
def fetch_rtc_auto(data: RTCRequest):
    try:
        return fetch_rtc_with_playwright(data.model_dump())
    except Exception as playwright_error:
        try:
            return fetch_rtc_with_selenium(data.model_dump())
        except Exception as selenium_error:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Both Playwright and Selenium failed",
                    "playwrightError": str(playwright_error),
                    "seleniumError": str(selenium_error)
                }
            )


@app.get("/api/bhoomi/dropdowns")
def get_district_dropdowns():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page()

        try:
            page.goto(BHOOMI_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("select", timeout=60000)

            options = page.locator("select").nth(0).locator("option").all()
            districts = clean_options(options)

            return {
                "success": True,
                "districts": districts
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        finally:
            browser.close()


@app.post("/api/bhoomi/dropdowns/full")
def get_full_dropdowns(data: DropdownRequest):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page()

        try:
            page.goto(BHOOMI_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("select", timeout=60000)

            selects = page.locator("select")

            def get_options(index):
                opts = selects.nth(index).locator("option").all()
                return clean_options(opts)

            districts = get_options(0)
            taluks = []
            hoblis = []
            villages = []

            if data.district:
                selects.nth(0).select_option(label=data.district)
                time.sleep(2)
                taluks = get_options(1)

            if data.district and data.taluk:
                selects.nth(1).select_option(label=data.taluk)
                time.sleep(2)
                hoblis = get_options(2)

            if data.district and data.taluk and data.hobli:
                selects.nth(2).select_option(label=data.hobli)
                time.sleep(2)
                villages = get_options(3)

            return {
                "success": True,
                "districts": districts,
                "taluks": taluks,
                "hoblis": hoblis,
                "villages": villages
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        finally:
            browser.close()



if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=5000,
        reload=True
    )   