import os
import sys
import asyncio
from discord.ext import commands
from plexapi.server import PlexServer
import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PLEX_URL = os.getenv("PLEX_URL")
PLEX_TOKEN = os.getenv("PLEX_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents)

def plex_connect():
    try:
        return PlexServer(PLEX_URL, PLEX_TOKEN)
    except Exception:
        return None

async def update_status_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        plex = plex_connect()
        if plex is None:
            print("Plex server offline. Exiting bot.")
            await bot.close()
            sys.exit(1)
        try:
            sessions = plex.sessions()
            # Count unique users
            user_names = set()
            for session in sessions:
                try:
                    user_names.add(session.usernames[0])
                except Exception:
                    pass
            user_count = len(user_names)
            # Update bot status
            activity = discord.CustomActivity(
                name=f"{user_count} user{'s' if user_count != 1 else ''} on Plex"
            )
            await bot.change_presence(activity=activity)
        except Exception as e:
            print(f"Error updating status: {e}")
        await asyncio.sleep(5)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def plexstatus(ctx):
    plex = plex_connect()
    if plex is None:
        await ctx.send("❌ Plex server is **offline**.")
        await bot.close()
        sys.exit(1)
    sessions = plex.sessions()
    user_names = set()
    for session in sessions:
        try:
            user_names.add(session.usernames[0])
        except Exception:
            pass
    user_count = len(user_names)
    now_playing = len(sessions)
    await ctx.send(f"✅ Plex is **online**.\n🎬 Currently **{now_playing}** stream(s) playing by **{user_count}** user(s).")

@bot.command()
async def viewers(ctx):
    plex = plex_connect()
    if plex is None:
        await ctx.send("❌ Plex server is offline.")
        await bot.close()
        sys.exit(1)
    sessions = plex.sessions()
    users = set()
    for session in sessions:
        try:
            users.add(session.usernames[0])
        except Exception:
            pass
    await ctx.send(f"👀 Users currently watching: {', '.join(users) if users else 'No one'}")

async def health_check():
    while True:
        plex = plex_connect()
        if plex is None:
            print("Plex server went offline. Exiting bot.")
            await bot.close()
            sys.exit(1)
        await asyncio.sleep(30)

@bot.event
async def on_connect():
    bot.loop.create_task(health_check())
    bot.loop.create_task(update_status_task())

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
