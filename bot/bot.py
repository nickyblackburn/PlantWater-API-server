import discord
from discord.ext import commands
import requests

# =========================
# ⚙️ CONFIG
# =========================
TOKEN = "PUT_YOUR_NEW_TOKEN_HERE"
API_BASE = "http://127.0.0.1:8000"

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
        description="Here are all available commands for your garden system:",
        color=0x2ecc71
    )

    embed.add_field(
        name="📊 .status",
        value="Shows moisture levels and valve states for all beds.",
        inline=False
    )

    embed.add_field(
        name="💧 Example Output",
        value="Bed1 → 512 (Healthy) | ON/OFF",
        inline=False
    )

    embed.add_field(
        name="🛠️ More Commands",
        value="More features like watering controls and alerts coming soon 🌿",
        inline=False
    )

    embed.set_footer(text="Smart Garden System • ESP32 + FastAPI")

    await ctx.send(embed=embed)


# =========================
# 🌱 HELPER FUNCTION
# =========================
def get_bed_status():
    try:
        res = requests.get(f"{API_BASE}/api/beds/latest")
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

    message = "🌱 **Smart Garden Status**\n\n"
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