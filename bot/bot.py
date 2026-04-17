import discord
from discord.ext import commands
import requests
import os
from dotenv import load_dotenv

# =========================
# 🔐 LOAD ENV VARS
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

API_BASE = "http://127.0.0.1:8000"

# =========================
# ⚙️ INTENTS (IMPORTANT)
# =========================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)


# =========================
# 🌱 HELP COMMAND
# =========================
@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="🌱 Smart Garden Bot",
        description="Available commands:",
        color=0x2ecc71
    )

    embed.add_field(
        name="📊 .status",
        value="Shows moisture levels and valve states.",
        inline=False
    )

    embed.add_field(
        name="🛠️ System",
        value="ESP32 + FastAPI Smart Irrigation System",
        inline=False
    )

    embed.set_footer(text="Smart Garden System")

    await ctx.send(embed=embed)


# =========================
# 🌱 API FUNCTION
# =========================
def get_bed_status():
    try:
        res = requests.get(f"{API_BASE}/api/beds/latest", timeout=5)
        return res.json()
    except:
        return None


# =========================
# 🚀 READY EVENT
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}")


# =========================
# 💬 STATUS COMMAND
# =========================
@bot.command()
async def status(ctx):
    data = get_bed_status()

    if not data:
        await ctx.send("❌ Failed to fetch data from API")
        return

    embed = discord.Embed(
        title="🌱 Smart Garden Status",
        color=0x1abc9c
    )

    active = []

    for bed_id, bed in data.items():
        avg = bed["average"]
        valve = bed["valve_state"]

        if avg > 650:
            state = "Dry"
        elif avg > 450:
            state = "Healthy"
        else:
            state = "Wet"

        embed.add_field(
            name=f"{bed_id}",
            value=f"💧 {avg:.1f} ({state}) | 🚰 {valve}",
            inline=False
        )

        if valve == "ON":
            active.append(bed_id)

    footer_text = "Currently watering: " + ", ".join(active) if active else "No active watering"
    embed.set_footer(text=footer_text)

    await ctx.send(embed=embed)


# =========================
# ▶️ START BOT
# =========================
if not TOKEN:
    print("❌ Missing DISCORD_TOKEN in .env file")
else:
    bot.run(TOKEN)