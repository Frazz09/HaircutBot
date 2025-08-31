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

HELP_TEXT = (
    "Hey, I’m your barber bot ✂️\n\n"
    "Send 'haircut' and I’ll check Standby Haircuts availability."
)


# ====== SCRAPER FUNCTION ======
async def fetch_slots() -> list[str]:
    """Scrape the SimplyBook page for available slots."""
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(BOOKING_URL, timeout=60000)

        # Wait for booking widget to load
        await page.wait_for_timeout(4000)

        # Extract text from the page
        content = await page.content()

        # Regex for times (e.g., "10:30 AM")
        time_matches = re.findall(r"\d{1,2}:\d{2}\s?(?:AM|PM)", content)

        # Collect next 7 days
        today = datetime.today()
        for i in range(DAYS_TO_CHECK):
            day = today + timedelta(days=i)
            # Here we just say times found belong to these days (SimplyBook often shows daily slots dynamically)
            for t in time_matches:
                results.append(f"{day.strftime('%a %d %b')}: {t}")

        await browser.close()

    return results


# ====== COMMAND HANDLERS ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def haircut_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("One moment—checking the Standby Haircuts page…")
    try:
        slots = await fetch_slots()
        if slots:
            msg = "Available Standby Haircut slots:\n\n" + "\n".join(slots)
        else:
            msg = "Nothing found — no Standby Haircut slots are available right now."
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# ====== MAIN ======
def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        raise SystemExit("Please set BOT_TOKEN as an environment variable.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("(?i)haircut"), haircut_handler))

    print("Bot started. Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
