import discord
from discord.ext import commands
import requests

# =========================
# ⚙️ CONFIG
# =========================
TOKEN = "MTQ5NDgyMjUyMTEwOTg3MjcyMA.GyaswG.RBmzsCf4N4sFIw3Gz25N-KCu42kGjx6Nb6WD4c"
API_BASE = "http://127.0.0.1:8000"  # your FastAPI server

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=".", intents=intents)


# =========================
# 🌱 HELPER FUNCTION
# =========================
def get_bed_status():
    try:
        res = requests.get(f"{API_BASE}/api/beds/latest")
        return res.json()
    except Exception as e:
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

    message = "🌱 **Smart Garden Status**\n\n"
    active = []

    for bed_id, bed in data.items():
        avg = bed["average"]
        valve = bed["valve_state"]

        # classify moisture (optional but nice)
        if avg > 650:
            state = "Dry"
        elif avg > 450:
            state = "Healthy"
        else:
            state = "Wset"

        message += f"**{bed_id}** → 💧 {avg:.1f} ({state}) | 🚰 {valve}\n"

        if valve == "ON":
            active.append(bed_id)

    message += "\n"

    if active:
        message += "🚰 **Currently watering:** " + ", ".join(active)
    else:
        message += "💤 No active watering"

    await ctx.send(message)


# =========================
# ▶️ START BOT
# =========================
bot.run(TOKEN)