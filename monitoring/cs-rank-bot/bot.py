import os
import sys
import asyncio
import requests
from bs4 import BeautifulSoup
from discord.ext import commands
import discord

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN_CS")
STEAM_ID = os.getenv("STEAM_ID")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", 300))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def extract_rating(span_tag):
    if not span_tag:
        return None
    base = ""
    extra = ""
    for child in span_tag.children:
        if isinstance(child, str):
            base += child.strip()
        elif getattr(child, "name", None) == "small":
            extra += child.text.strip()
    return f"{base}{extra}".replace(",", "").replace(" ", "")

def fetch_html_via_flaresolverr(url):
    api_url = "http://localhost:8191/v1"
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000
    }
    resp = requests.post(api_url, json=payload)
    data = resp.json()
    if resp.status_code == 200 and "solution" in data and "response" in data["solution"]:
        return data["solution"]["response"]
    else:
        print("FlareSolverr failed:", data)
        return None

def get_premier_ranks_selenium(steam_id):
    url = f"https://csstats.gg/player/{steam_id}"
    html = fetch_html_via_flaresolverr(url)
    soup = BeautifulSoup(html, "html.parser")
    all_seasons = []
    for div in soup.select("#player-ranks .ranks"):
        season_info = div.select_one(".icon[style*='flex-basis']")
        if not season_info:
            continue
        season = season_info.text.strip()
        if not season.startswith("S"):
            continue
        rank_div = div.select_one(".rank .cs2rating span")
        rating = extract_rating(rank_div)
        best_div = div.select_one(".best .cs2rating span")
        best = extract_rating(best_div)
        wins_div = div.select_one(".wins b")
        wins = wins_div.text.strip() if wins_div else None
        all_seasons.append({
            "season": season,
            "rating": rating,
            "best": best,
            "wins": wins,
        })
    return all_seasons

def get_latest_season_rating(steam_id):
    seasons = get_premier_ranks_selenium(steam_id)
    if not seasons:
        return None, None
    latest = seasons[0]  # S3 is listed before S2, S1, etc.
    return latest["season"], latest["rating"]

async def update_status_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        season, rating = get_latest_season_rating(STEAM_ID)
        if season and rating:
            status = f"CS2 {season}: {rating}"
        else:
            status = "Premier rating: unavailable"
        activity = discord.CustomActivity(
            name=status
        )
        try:
            await bot.change_presence(activity=activity)
            print(f"Updated bot status: {status}")
        except Exception as e:
            print(f"Error updating status: {e}")

        await asyncio.sleep(UPDATE_INTERVAL)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def premier(ctx):
    """Replies with all Premier ranks (current, best, wins) from all seasons."""
    seasons = get_premier_ranks_selenium(STEAM_ID)
    if not seasons:
        await ctx.send("Could not fetch Premier ranks.")
        return
    msg = []
    for s in seasons:
        msg.append(
            f"**{s['season']}**: {s['rating']} (Best: {s['best']}, Wins: {s['wins']})"
        )
    await ctx.send("\n".join(msg))

@bot.event
async def on_connect():
    bot.loop.create_task(update_status_task())

if __name__ == "__main__":
    if not DISCORD_TOKEN or not STEAM_ID:
        print("DISCORD_TOKEN or STEAM_ID missing in .env.")
        sys.exit(1)
    bot.run(DISCORD_TOKEN)
