import os
import re
import asyncio
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

from playwright.async_api import async_playwright

# ====== SETTINGS YOU CAN TWEAK ======
BOOKING_URL = "https://liverpoolstreetbarber.simplybook.it/v2/#book/category/7/count/1/provider/any/"
DAYS_TO_CHECK = 7   # how many calendar days ahead to scan
PAGE_LOAD_TIMEOUT_MS = 60000
# ====================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Please set BOT_TOKEN as an environment variable.")

TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

async def scrape_standby_times() -> str:
    """
    Opens the Standby Haircuts page (Category 7), iterates services on that page,
    and gathers available times for the next few days.
    Returns a formatted text message for Telegram.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 1800})

        try:
            # Go straight to the Standby category
            await page.goto(BOOKING_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)

            # Dismiss any cookie banners if present
            for label in ["Accept", "I agree", "Got it", "Allow all", "Accept all"]:
                try:
                    await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=2000)
                    break
                except Exception:
                    pass

            # Wait for any service cards to appear (look for "Book" buttons)
            await page.wait_for_timeout(1200)
            await page.wait_for_selector("button:has-text('Book')", timeout=30000)

            # Some pages lazy-load more items; try scrolling
            for _ in range(3):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(500)

            # Gather the "Book" buttons (each corresponds to a service card)
            book_buttons = page.locator("button:has-text('Book')")
            count = await book_buttons.count()
            if count == 0:
                return "I couldn’t find any services to check in the Standby Haircuts category right now."

            results = []  # list of (service_title, {date_str: [times...]})

            for i in range(count):
                # Re-get the button each loop to avoid stale handles
                btn = page.locator("button:has-text('Book')").nth(i)

                # Try to read the service title
                card = btn.locator("xpath=ancestor::*[self::div or self::section][.//button[contains(., 'Book')]][1]")
                title_loc = card.locator("xpath=.//h1|.//h2|.//h3|.//h4|.//div[contains(@class,'title')]|.//div[contains(@class,'name')]")
                service_title = None
                try:
                    service_title = (await title_loc.first.inner_text()).strip()
                except Exception:
                    service_title = f"Service {i+1}"

                # Open this service's booking times
                try:
                    await btn.click()
                except Exception:
                    continue

                # Wait for calendar/times
                await page.wait_for_timeout(1000)

                # Try bypassing "Any employee" or "Continue"
                for label in ["Continue", "Next", "Any employee", "Any"]:
                    try:
                        await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=1500)
                        await page.wait_for_timeout(500)
                    except Exception:
                        pass

                # Collect available dates & times
                per_service = {}
                seen_dates = set()
                attempts = 0
                while len(seen_dates) < DAYS_TO_CHECK and attempts < DAYS_TO_CHECK * 2:
                    attempts += 1

                    # Detect current date label
                    date_text = None
                    candidates = [
                        "xpath=//div[contains(@class,'selected-date') or contains(@class,'date')][not(@hidden)]",
                        "xpath=//button[@aria-pressed='true' or @aria-selected='true']",
                        "xpath=//h2[contains(.,'20') or contains(.,'19')]",
                    ]
                    for sel in candidates:
                        try:
                            txt = ((await page.locator(sel).first.inner_text(timeout=1000)).strip())
                            if txt:
                                date_text = txt
                                break
                        except Exception:
                            pass

                    if not date_text:
                        date_str = (datetime.now().date() + timedelta(days=len(seen_dates))).strftime("%Y-%m-%d")
                    else:
                        date_str = date_text.replace("\n", " ").strip()

                    # Read visible time buttons like "09:15"
                    times = []
                    try:
                        time_buttons = page.locator("button").filter(has_text=TIME_RE)
                        tcount = await time_buttons.count()
                        for ti in range(tcount):
                            t_txt = (await time_buttons.nth(ti).inner_text()).strip()
                            if TIME_RE.match(t_txt):
                                times.append(t_txt)
                    except Exception:
                        pass

                    if times:
                        per_service[date_str] = sorted(set(times))
                        seen_dates.add(date_str)

                    # Move to next date
                    moved = False
                    for nxt in ["Next day", "Next", "›", "»", "→"]:
                        try:
                            await page.get_by_role("button", name=re.compile(nxt)).click(timeout=800)
                            moved = True
                            break
                        except Exception:
                            pass
                    if not moved:
                        try:
                            cell = page.locator("xpath=//button[not(@disabled) and (contains(@aria-label,'Choose') or contains(@aria-label,'Select') or @role='gridcell')][not(@aria-pressed='true') and not(@aria-selected='true')]").first
                            await cell.click(timeout=800)
                            moved = True
                        except Exception:
                            pass
                    if not moved:
                        break

                results.append((service_title, per_service))

                # Go back to service list
                try:
                    await page.go_back()
                    await page.wait_for_selector("button:has-text('Book')", timeout=20000)
                except Exception:
                    try:
                        await page.goto(BOOKING_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
                        await page.wait_for_selector("button:has-text('Book')", timeout=20000)
                    except Exception:
                        pass

            await browser.close()

            # Format message
            lines = []
            any_slots = False
            for title, days in results:
                if days:
                    any_slots = True
                    lines.append(f"• {title}")
                    for d, tlist in list(days.items())[:DAYS_TO_CHECK]:
                        times_str = ", ".join(tlist)
                        lines.append(f"   - {d}: {times_str}")
            if not any_slots:
                return "No available times found right now in Standby Haircuts."

            header = "✅ Standby Haircuts availability:"
            return header + "\n" + "\n".join(lines)

        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
            return "Sorry—something went wrong while checking the page. Please try again in a minute."


# ===== Telegram bot bits =====

HELP_TEXT = (
    "Hi! Send me:  \n"
    "• haircut — I’ll fetch available Standby Haircuts days & times.\n\n"
    "Tip: If I don’t reply, make sure my service (Railway or Replit) is running."
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, disable_web_page_preview=True)

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    if text == "haircut":
        await update.message.reply_text("One moment—checking the Standby Haircuts page…")
        msg = await scrape_standby_times()
        await update.message.reply_text(msg, disable_web_page_preview=True)
    else:
        await update.message.reply_text("Send haircut and I’ll check Standby Haircuts availability for you.", disable_web_page_preview=True)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_router))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
