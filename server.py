import datetime
import os
import random
import threading
import time
import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# የቴሌግራም ቦት እና የአድሚን መረጃ
BOT_TOKEN = "9045023313:AAE9MJMwGMxcAGaxyPSHrmc333V-oetua_g"
ADMIN_CHAT_ID = "429206833"
APP_URL = "https://my-bingo-bot-yeer.onrender.com"

# 600 የቢንጎ ካርዶች ማመንጫ (በትክክለኛው የ B-I-N-G-O ክልል)
ALL_CARDS = {}

def generate_all_cards(total=600):
  global ALL_CARDS
  ranges = [
      range(1, 16),   # B: 1 - 15
      range(16, 31),  # I: 16 - 30
      range(31, 46),  # N: 31 - 45
      range(46, 61),  # G: 46 - 60
      range(61, 76),  # O: 61 - 75
  ]
  for c in range(1, total + 1):
    card = []
    for r in ranges:
      card.append(random.sample(list(r), 5))
    card[2][2] = "FREE"
    ALL_CARDS[c] = card

generate_all_cards(600)

USERS_DB = {}
TRANSACTIONS_DB = []

GAME_STATE = {
    "round_id": 1,
    "status": "WAITING",  # WAITING, PLAYING, WON
    "timer": 60,
    "called_numbers": [],
    "current_number": "--",
    "current_letter": "",
    "taken_cards": {},  # card_id -> {id, name}
    "stake": 10,
    "prize_pool": 0.0,
    "winner": None,
    "win_countdown": 5,
}

state_lock = threading.Lock()

def get_letter(num):
  if num <= 15:
    return "B"
  if num <= 30:
    return "I"
  if num <= 45:
    return "N"
  if num <= 60:
    return "G"
  return "O"

def check_bingo_server(card, called_nums):
  grid = []
  for r in range(5):
    row = []
    for c in range(5):
      val = card[c][r]
      row.append(val == "FREE" or val in called_nums)
    grid.append(row)

  for r in range(5):
    if all(grid[r][c] for c in range(5)):
      return [f"{r}-{c}" for c in range(5)]
  for c in range(5):
    if all(grid[r][c] for r in range(5)):
      return [f"{r}-{c}" for r in range(5)]
  if all(grid[i][i] for i in range(5)):
    return [f"{i}-{i}" for i in range(5)]
  if all(grid[i][4 - i] for i in range(5)):
    return [f"{i}-{4-i}" for i in range(5)]
  return []

def game_engine_loop():
  last_draw_time = 0
  while True:
    time.sleep(1)
    with state_lock:
      if GAME_STATE["status"] == "WAITING":
        if GAME_STATE["timer"] > 0:
          if len(GAME_STATE["taken_cards"]) > 0:
            GAME_STATE["timer"] -= 1
        else:
          if len(GAME_STATE["taken_cards"]) > 0:
            GAME_STATE["status"] = "PLAYING"
            GAME_STATE["called_numbers"] = []
            GAME_STATE["current_number"] = "--"
            last_draw_time = time.time()
          else:
            GAME_STATE["timer"] = 60

      elif GAME_STATE["status"] == "PLAYING":
        if time.time() - last_draw_time >= 3.0:
          last_draw_time = time.time()
          available = [
              i for i in range(1, 76) if i not in GAME_STATE["called_numbers"]
          ]
          if not available:
            GAME_STATE["status"] = "WAITING"
            GAME_STATE["timer"] = 60
            GAME_STATE["taken_cards"] = {}
            GAME_STATE["prize_pool"] = 0.0
            continue

          num = random.choice(available)
          GAME_STATE["called_numbers"].append(num)
          letter = get_letter(num)
          GAME_STATE["current_letter"] = letter
          GAME_STATE["current_number"] = f"{letter}-{num}"

          for card_id, owner in GAME_STATE["taken_cards"].items():
            win_cells = check_bingo_server(
                ALL_CARDS[card_id], GAME_STATE["called_numbers"]
            )
            if win_cells:
              prize = GAME_STATE["prize_pool"]
              GAME_STATE["status"] = "WON"
              GAME_STATE["win_countdown"] = 6
              GAME_STATE["winner"] = {
                  "name": owner["name"],
                  "id": owner["id"],
                  "card_id": card_id,
                  "prize": prize,
                  "winning_cells": win_cells,
              }
              if owner["id"] in USERS_DB:
                USERS_DB[owner["id"]]["wallet"] += prize
                USERS_DB[owner["id"]]["games_won"] += 1
                date_str = datetime.datetime.now().strftime("%m/%d/%Y %H:%M")
                USERS_DB[owner["id"]]["history"].insert(
                    0,
                    {
                        "id": int(time.time() * 1000),
                        "type": "WIN PRIZE",
                        "method": f"Cartela #{card_id}",
                        "date": date_str,
                        "amount": prize,
                        "ref": f"Round #{GAME_STATE['round_id']}",
                        "status": "APPROVED",
                    },
                )
              break

      elif GAME_STATE["status"] == "WON":
        if GAME_STATE["win_countdown"] > 0:
          GAME_STATE["win_countdown"] -= 1
        else:
          GAME_STATE["round_id"] += 1
          GAME_STATE["status"] = "WAITING"
          GAME_STATE["timer"] = 60
          GAME_STATE["called_numbers"] = []
          GAME_STATE["current_number"] = "--"
          GAME_STATE["taken_cards"] = {}
          GAME_STATE["prize_pool"] = 0.0
          GAME_STATE["winner"] = None

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
  data = request.json or {}
  if "message" in data:
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    user_info = msg.get("from", {})
    tg_id = str(user_info.get("id", chat_id))
    first_name = user_info.get("first_name", "Player")

    # ተጠቃሚው ስልክ ቁጥሩን (Contact) ሲያጋራ
    if "contact" in msg:
      phone = msg["contact"].get("phone_number", "")
      with state_lock:
        if tg_id not in USERS_DB:
          USERS_DB[tg_id] = {
              "name": first_name,
              "id": tg_id,
              "phone": phone,
              "wallet": 50.0,
              "games_won": 0,
              "history": [],
          }
        else:
          USERS_DB[tg_id]["phone"] = phone
      remove_keyboard_and_send_success(chat_id)
      return jsonify({"status": "ok"})

    text = msg.get("text", "")
    is_registered = tg_id in USERS_DB and USERS_DB[tg_id].get("phone")

    if not is_registered:
      send_phone_request(chat_id)
      return jsonify({"status": "ok"})

    if text.startswith("/start") or text.startswith("/play") or text.startswith("/register"):
      send_welcome_button(chat_id, first_name)
    elif text.startswith("/balance"):
      send_message(chat_id, f"💳 የርስዎ አካውንት ሒሳብ: *{USERS_DB[tg_id]['wallet']} ETB*")
    elif text.startswith("/deposit"):
      send_message(chat_id, "💰 ገንዘብ ለማስገባት (Deposit) ሚኒ አፑን ከፍተው 'ቴሌብር' የሚለውን ታብ ይጠቀሙ።\n• Telebirr: 0920183489\n• CBE: 1000516685817")
    elif text.startswith("/withdraw"):
      send_message(chat_id, "📤 ገንዘብ ለማውጣት አድሚንን ያነጋግሩ።")
    elif text.startswith("/history"):
      send_message(chat_id, "📊 የግብይት ታሪክዎን ሚኒ አፑ ውስጥ 'ታሪክ' በሚለው ማየት ይችላሉ።")
    elif text.startswith("/instructions") or text.startswith("/instruction"):
      send_message(chat_id, "📖 መመሪያ:\n1. ካርድ ይምረጡ (እስከ 3)\n2. ጨዋታው እስኪጀምር ይጠብቁ\n3. ቁጥሮች ሲጠሩ ይፈትሹ\n4. ቢንጎ ሲሉ ያሸንፉ!")
    else:
      send_welcome_button(chat_id, first_name)

  return jsonify({"status": "ok"})

def setup_webhook_and_commands():
  try:
    wh_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={APP_URL}/webhook/{BOT_TOKEN}"
    requests.get(wh_url)

    cmd_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands"
    commands = [
        {"command": "play", "description": "Play 🎮"},
        {"command": "balance", "description": "My balance"},
        {"command": "deposit", "description": "Deposit"},
        {"command": "withdraw", "description": "Withdraw"},
        {"command": "history", "description": "Transaction history"},
        {"command": "instructions", "description": "Instruction"},
        {"command": "register", "description": "Register"}
    ]
    requests.post(cmd_url, json={"commands": commands})
  except Exception as e:
    print("Setup error:", e)

def send_phone_request(chat_id):
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  text = "⚠️ *ምዝገባ ያስፈልጋል (Registration Required)*\n\nBest Bingoን ለመጠቀም ከታች Share Phone የሚለውን ይጫኑ። ከዚያም Share የሚለውን ይጫኑ"
  payload = {
      "chat_id": chat_id,
      "text": text,
      "parse_mode": "Markdown",
      "reply_markup": {
          "keyboard": [
              [{"text": "📱 Share Phone", "request_contact": True}]
          ],
          "resize_keyboard": True,
          "one_time_keyboard": True
      }
  }
  try:
    requests.post(url, json=payload)
  except Exception as e:
    print("Phone request error:", e)

def remove_keyboard_and_send_success(chat_id):
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  payload = {
      "chat_id": chat_id,
      "text": "✅ Registered! Balance: 50.00 ETB",
      "reply_markup": {"remove_keyboard": True}
  }
  try:
    requests.post(url, json=payload)
    send_welcome_button(chat_id, "Player")
  except Exception as e:
    print("Success msg error:", e)

def send_message(chat_id, text):
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
  try:
    requests.post(url, json=payload)
  except Exception as e:
    print("Send message error:", e)

def send_welcome_button(chat_id, name):
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  text = f"እንኳን ወደ *Best Bingo* በደህና መጡ, {name}! 🎮\n\nጨዋታውን ለመጀመር ከታች ያለውን ቁልፍ ይጫኑ፦"
  payload = {
      "chat_id": chat_id,
      "text": text,
      "parse_mode": "Markdown",
      "reply_markup": {
          "inline_keyboard": [
              [{
                  "text": "🎲 ቢንጎ ይጫወቱ (Play Bingo)",
                  "web_app": {"url": APP_URL}
              }]
          ]
      }
  }
  try:
    requests.post(url, json=payload)
  except Exception as e:
    print("Webhook error:", e)

@app.route("/api/state", methods=["GET"])
def api_state():
  tg_id = request.args.get("tg_id", "12345")
  name = request.args.get("name", "Player")

  with state_lock:
    if tg_id and tg_id not in USERS_DB:
      USERS_DB[tg_id] = {
          "name": name,
          "id": tg_id,
          "phone": "",
          "wallet": 50.0,
          "games_won": 0,
          "history": [],
      }

    user = USERS_DB.get(
        tg_id,
        {"name": name, "id": tg_id, "phone": "", "wallet": 50.0, "games_won": 0, "history": []},
    )
    my_cards = [
        cid for cid, owner in GAME_STATE["taken_cards"].items() if owner["id"] == tg_id
    ]
    taken_list = list(GAME_STATE["taken_cards"].keys())
    unique_players = len(set(owner["id"] for owner in GAME_STATE["taken_cards"].values()))

    return jsonify({
        "user": user,
        "my_cards": my_cards,
        "taken_cards": taken_list,
        "active_players": max(unique_players, len(USERS_DB)),
        "game": {
            "round_id": GAME_STATE["round_id"],
            "status": GAME_STATE["status"],
            "timer": GAME_STATE["timer"],
            "called_numbers": GAME_STATE["called_numbers"],
            "current_number": GAME_STATE["current_number"],
            "prize_pool": GAME_STATE["prize_pool"],
            "winner": GAME_STATE["winner"],
            "win_countdown": GAME_STATE["win_countdown"],
        },
    })

@app.route("/api/select_card", methods=["POST"])
def api_select_card():
  data = request.json or {}
  tg_id = data.get("tg_id")
  card_id = int(data.get("card_id"))

  with state_lock:
    if GAME_STATE["status"] != "WAITING":
      return jsonify({"success": False, "message": "ጨዋታው ተጀምሯል!"})
    if tg_id not in USERS_DB:
      USERS_DB[tg_id] = {
          "name": "Player",
          "id": tg_id,
          "phone": "",
          "wallet": 50.0,
          "games_won": 0,
          "history": [],
      }

    user = USERS_DB[tg_id]
    my_cards = [
        cid for cid, owner in GAME_STATE["taken_cards"].items() if owner["id"] == tg_id
    ]

    if card_id in my_cards:
      del GAME_STATE["taken_cards"][card_id]
      user["wallet"] += GAME_STATE["stake"]
      GAME_STATE["prize_pool"] = round(
          len(GAME_STATE["taken_cards"]) * GAME_STATE["stake"] * 0.8, 2
      )
      return jsonify({"success": True, "action": "removed"})

    if card_id in GAME_STATE["taken_cards"]:
      return jsonify({"success": False, "message": "ይህ ካርቴላ በሌላ ተጫዋች ተይዟል!"})

    if len(my_cards) >= 3:
      return jsonify({"success": False, "message": "ከ 3 ካርድ በላይ መምረጥ አይቻልም!"})

    if user["wallet"] < GAME_STATE["stake"]:
      return jsonify({"success": False, "message": "በቂ ሒሳብ የለዎትም!"})

    user["wallet"] -= GAME_STATE["stake"]
    GAME_STATE["taken_cards"][card_id] = {
        "id": tg_id,
        "name": user["name"],
    }
    GAME_STATE["prize_pool"] = round(
        len(GAME_STATE["taken_cards"]) * GAME_STATE["stake"] * 0.8, 2
    )

    return jsonify({"success": True, "action": "added"})

@app.route("/api/deposit", methods=["POST"])
def api_deposit():
  data = request.json or {}
  tg_id = data.get("tg_id")
  method = data.get("method")
  amount = float(data.get("amount", 0))
  ref = data.get("ref", "").strip()

  with state_lock:
    if tg_id not in USERS_DB:
      USERS_DB[tg_id] = {
          "name": "Player",
          "id": tg_id,
          "phone": "",
          "wallet": 50.0,
          "games_won": 0,
          "history": [],
      }
    tx_id = int(time.time() * 1000)
    date_str = datetime.datetime.now().strftime("%m/%d/%Y %H:%M")
    user_name = USERS_DB[tg_id]["name"]
    tx = {
        "id": tx_id,
        "tg_id": tg_id,
        "user_name": user_name,
        "type": "DEPOSIT",
        "method": method,
        "date": date_str,
        "amount": amount,
        "ref": ref,
        "status": "PENDING",
    }
    TRANSACTIONS_DB.insert(0, tx)
    USERS_DB[tg_id]["history"].insert(0, tx)

    msg = (
        f"💰 *አዲስ የዲፖዚት ጥያቄ ደርሷል!*\n\n"
        f"👤 ተጠቃሚ: `{user_name}` (ID: `{tg_id}`)\n"
        f"💵 የገንዘብ መጠን: `{amount} ETB`\n"
        f"🏦 ዘዴ: `{method}`\n"
        f"🔖 ራጅ/Ref No: `{ref}`"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("Telegram error:", e)

    return jsonify({"success": True, "message": "የዲፖዚት ጥያቄዎ ተልኳል!"})

@app.route("/api/admin/transactions", methods=["GET"])
def api_admin_tx():
  with state_lock:
    pending = [t for t in TRANSACTIONS_DB if t["status"] == "PENDING"]
    return jsonify({"transactions": pending})

@app.route("/api/admin/action", methods=["POST"])
def api_admin_action():
  data = request.json or {}
  tx_id = int(data.get("tx_id"))
  action = data.get("action")

  with state_lock:
    target_tx = next((t for t in TRANSACTIONS_DB if t["id"] == tx_id), None)
    if not target_tx:
      return jsonify({"success": False, "message": "አልተገኘም!"})
    user = USERS_DB.get(target_tx["tg_id"])
    if target_tx["type"] == "DEPOSIT":
      if action == "APPROVE":
        target_tx["status"] = "APPROVED"
        if user:
          user["wallet"] += target_tx["amount"]
      else:
        target_tx["status"] = "REJECTED"
    return jsonify({"success": True})

TEMPLATE = """
<!DOCTYPE html>
<html lang="am">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Best Bingo - Commercial</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { box-sizing: border-box; }
        body { font-family: sans-serif; background: #0b0f1d; color: #fff; margin: 0; padding: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
        .header { background: #161b33; padding: 8px 10px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.1); flex-shrink: 0; }
        .header h3 { margin: 0; color: #00ffcc; font-size: 13px; cursor: pointer; }
        .content-area { flex: 1; overflow-y: auto; padding: 8px; padding-bottom: 65px; display: flex; flex-direction: column; align-items: center; width: 100%; }
        
        .tab-content { display: none; flex: 1; overflow-y: auto; padding: 2px; width: 100%; }
        .tab-content.active { display: flex; flex-direction: column; align-items: center; }

        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; height: 55px; background: #161b33; display: flex; justify-content: space-around; align-items: center; border-top: 1px solid rgba(255,255,255,0.1); z-index: 100; flex-shrink: 0; }
        .nav-item { display: flex; flex-direction: column; align-items: center; color: #8a99ad; font-size: 10px; cursor: pointer; width: 25%; }
        .nav-item.active { color: #38ef7d; font-weight: bold; }
        .nav-icon { font-size: 15px; margin-bottom: 2px; }

        .top-bar { display: flex; justify-content: space-around; background: rgba(255,255,255,0.08); padding: 5px; border-radius: 6px; font-size: 11px; margin-bottom: 4px; width: 100%; max-width: 400px; flex-shrink: 0; }
        .commercial-bar { display: flex; justify-content: space-between; background: rgba(0,255,204,0.1); border: 1px solid #00ffcc; padding: 4px 8px; border-radius: 6px; font-size: 11px; margin-bottom: 4px; color: #00ffcc; width: 100%; max-width: 400px; flex-shrink: 0; }
        .timer-box-main { color: #ffeb3b; font-weight: bold; font-size: 11px; background: rgba(0,0,0,0.4); padding: 4px; border-radius: 6px; margin-bottom: 4px; border: 1px solid #ffeb3b; text-align: center; width: 100%; max-width: 400px; flex-shrink: 0; }

        .main-container { display: flex; gap: 6px; justify-content: center; width: 100%; max-width: 400px; margin: 0 auto; }
        .left-section { width: 48%; display: flex; flex-direction: column; gap: 4px; }
        .right-section { width: 50%; display: flex; flex-direction: column; gap: 4px; }

        .grid-container-600 { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; height: 280px; overflow-y: auto; background: rgba(255,255,255,0.05); padding: 4px; border-radius: 6px; }
        .card-btn { background: #1e2640; color: #fff; border: 1px solid rgba(255,255,255,0.1); padding: 5px 0; font-size: 10px; font-weight: bold; border-radius: 3px; cursor: pointer; }
        .card-btn.selected { background: #38ef7d; color: #000; }
        .card-btn.taken { background: #ff4757; color: #fff; opacity: 0.5; }

        .board-container { display: none; grid-template-columns: repeat(5, 1fr); gap: 2px; background: rgba(255,255,255,0.05); padding: 4px; border-radius: 6px; height: 280px; overflow-y: auto; }
        .board-cell { background: rgba(255,255,255,0.1); color: #fff; font-size: 9px; padding: 4px 0; border-radius: 2px; text-align: center; font-weight: bold; }
        .board-cell.called { background: #ff9800; color: #000; }

        .draw-display { display: none; background: rgba(255,255,255,0.1); border: 2px solid #ffd700; border-radius: 6px; padding: 3px; text-align: center; margin-bottom: 4px; flex-shrink: 0; }
        .current-num { font-size: 18px; font-weight: bold; color: #ffd700; }

        .selected-cards-area { display: flex; flex-direction: column; gap: 5px; width: 100%; height: 280px; overflow-y: auto; }
        .bingo-card { background: #161b33; border-radius: 6px; padding: 4px; border: 1px solid #00ffcc; flex-shrink: 0; }
        .bingo-card h4 { margin: 0 0 2px 0; color: #ffd700; font-size: 9px; text-align: center; }
        .b-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1px; }
        .cell { background: #222a45; font-size: 8px; padding: 3px 0; border-radius: 2px; font-weight: bold; text-align: center; color: #fff; }
        .cell.header-cell { background: #11162b; color: #00ffcc; }
        .cell.free { background: #db2777; }
        .cell.marked { background: #ff9800; color: #000; }
        .cell.winning-line { background: #ff5722 !important; }

        .card-box { background: #161b33; border-radius: 8px; padding: 10px; margin-bottom: 8px; border: 1px solid rgba(255,255,255,0.05); width: 100%; max-width: 400px; margin-left: auto; margin-right: auto; }
        .card-title { font-size: 10px; color: #8a99ad; margin-bottom: 2px; }
        .card-val { font-size: 16px; font-weight: bold; color: #fff; }
        
        .tx-card { background: #161b33; border-radius: 8px; padding: 10px; margin-bottom: 8px; border: 1px solid rgba(255,255,255,0.08); width: 100%; max-width: 400px; margin-left: auto; margin-right: auto; display: flex; justify-content: space-between; align-items: center; }
        .tx-left { display: flex; flex-direction: column; gap: 2px; }
        .tx-type { font-weight: bold; font-size: 11px; }
        .tx-date { font-size: 9px; color: #8a99ad; }
        .tx-amount { font-weight: bold; font-size: 13px; }
        .tx-amount.deposit { color: #38ef7d; }
        .tx-amount.withdraw { color: #ff4757; }
        .status-badge { font-size: 8px; font-weight: bold; padding: 2px 6px; border-radius: 3px; text-transform: uppercase; }
        .status-badge.approved { background: rgba(56,239,125,0.15); color: #38ef7d; }
        .status-badge.pending { background: rgba(255,193,7,0.15); color: #ffc107; }

        .modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.85); z-index: 999999; align-items: center; justify-content: center; padding: 10px; }
        .modal-card { background: #161b33; border: 2px solid #ffd700; border-radius: 12px; padding: 14px; width: 100%; max-width: 270px; text-align: center; }
        .crown-circle { width: 45px; height: 45px; background: rgba(255,215,0,0.15); border: 2px solid #ffd700; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 5px; font-size: 22px; }
        .auth-btn { width: 100%; background: #38ef7d; color: #000; border: none; padding: 8px; border-radius: 5px; font-weight: bold; font-size: 12px; cursor: pointer; }
        .input-group { margin-bottom: 8px; text-align: left; }
        .input-group label { display: block; font-size: 9px; color: #8a99ad; margin-bottom: 2px; }
        .input-group input, .input-group select { width: 100%; padding: 7px; background: #0b0f1d; border: 1px solid rgba(255,255,255,0.2); border-radius: 4px; color: #fff; font-size: 11px; }
    </style>
</head>
<body>

    <div class="header">
        <h3 onclick="triggerSecretAdmin()">Best Bingo (Multiplayer)</h3>
        <div style="font-size: 11px; color: #ffd700;">
            <span>🔊</span> <input type="checkbox" id="soundSwitch" checked onchange="voiceEnabled = this.checked">
        </div>
    </div>

    <!-- WIN MODAL -->
    <div class="modal-overlay" id="winModal">
        <div class="modal-card">
            <div class="crown-circle">👑</div>
            <h2 style="color: #ffd700; margin: 0 0 5px 0; font-size: 18px;">BINGO!</h2>
            <div id="modalWinnerName" style="font-size: 12px; margin-bottom: 4px;">Player WON!</div>
            <div id="modalWinnerPrize" style="color: #38ef7d; font-weight: bold; font-size: 14px; margin-bottom: 8px;">+0.00 ETB</div>
            <div style="background: #0b0f1d; padding: 6px; border-radius: 6px; margin-bottom: 8px;">
                <div id="modalCartelaNo" style="color: #ffd700; font-size: 10px; font-weight: bold; margin-bottom: 4px;">Cartela</div>
                <div id="modalCartelaContent"></div>
            </div>
            <div style="font-size: 10px; color: #ffd700;">ቀጣይ ጨዋታ በ <b id="winCountdown">5</b>ሰ</div>
        </div>
    </div>

    <div class="content-area">
        <!-- MAIN GAME TAB -->
        <div id="gameTab" class="tab-content active">
            <div class="top-bar">
                <div>Wallet: <b style="color:#38ef7d;" id="uiWallet">0.00 ETB</b></div>
                <div>ID: <b style="color:#ffd700;" id="uiPhone">--</b></div>
                <div>Stake: <b style="color:#ffd700;">10 ETB</b></div>
            </div>

            <div class="commercial-bar">
                <span>👥 Online Players: <b id="uiPlayersCount" style="color:#fff;">1</b></span>
                <span>💰 Prize Pool: <b id="uiPrizePool" style="color:#ffd700;">0.00 ETB</b></span>
            </div>

            <div class="timer-box-main" id="timerBox">⏱ ጨዋታው ሊጀምር: <span id="timer">60</span> ሰከንድ</div>

            <div class="main-container">
                <div class="left-section">
                    <div class="draw-display" id="drawDisplay">
                        <div style="font-size: 8px; color: #8a99ad;">Called Number</div>
                        <div class="current-num" id="currentNum">--</div>
                    </div>
                    <div class="grid-container-600" id="cardsGrid"></div>
                    
                    <!-- B I N G O Header with Main Board -->
                    <div style="display:none; grid-template-columns: repeat(5, 1fr); gap: 2px; background: rgba(255,255,255,0.05); padding: 4px; border-radius: 6px; text-align: center;" id="mainBoardWrapper">
                        <div style="color: #00ffcc; font-size: 9px; font-weight: bold;">B</div>
                        <div style="color: #00ffcc; font-size: 9px; font-weight: bold;">I</div>
                        <div style="color: #00ffcc; font-size: 9px; font-weight: bold;">N</div>
                        <div style="color: #00ffcc; font-size: 9px; font-weight: bold;">G</div>
                        <div style="color: #00ffcc; font-size: 9px; font-weight: bold;">O</div>
                    </div>
                    <div class="board-container" id="mainBoard"></div>
                </div>

                <div class="right-section">
                    <div style="font-size: 10px; color: #ffd700; font-weight: bold; margin-bottom: 3px;">Your Cards: (<b id="count" style="color:#38ef7d;">0</b>/3)</div>
                    <div class="selected-cards-area" id="selectedCards">
                        <div style="color: #8a99ad; font-size: 10px; text-align: center; margin-top: 15px;">ካርድ አልመረጡም</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- HISTORY -->
        <div id="historyTab" class="tab-content">
            <h3 style="margin: 0 0 8px 0; font-size: 14px; text-align:center;">History</h3>
            <div id="historyList"></div>
        </div>

        <!-- WALLET -->
        <div id="walletTab" class="tab-content">
            <div class="card-box">
                <div class="card-title">Main Wallet</div>
                <div class="card-val" style="color: #38ef7d;" id="uiMainWallet">0.00 ETB</div>
            </div>
            <div class="card-box">
                <h4 style="margin:0 0 6px 0; color:#ffd700; font-size:11px;">ገንዘብ ማስገቢያ (Deposit)</h4>
                <div style="font-size: 9px; color: #ffd700; margin-bottom: 6px;">• Telebirr: 0920183489<br>• CBE: 1000516685817</div>
                <div class="input-group">
                    <label>Method</label>
                    <select id="payMethod"><option value="Telebirr">Telebirr</option><option value="CBE">CBE</option></select>
                </div>
                <div class="input-group">
                    <label>Amount</label>
                    <input type="number" id="depositAmount" value="50">
                </div>
                <div class="input-group">
                    <label>Ref No</label>
                    <input type="text" id="payRef" placeholder="Ref No">
                </div>
                <button onclick="submitDeposit()" class="auth-btn" style="padding: 8px; font-size: 11px;">አስገባ</button>
            </div>
        </div>

        <!-- ADMIN -->
        <div id="adminTab" class="tab-content">
            <h3 style="margin: 0 0 8px 0; font-size: 14px; color: #00ffcc; text-align:center;">Admin Panel</h3>
            <button onclick="switchTab('gameTab', this)" style="background:#1e2640; color:#00ffcc; border:1px solid #00ffcc; padding:4px 8px; border-radius:4px; font-size:10px; margin-bottom:8px;">⬅️ ተመለስ</button>
            <div id="adminTransactionsList"></div>
        </div>

        <!-- PROFILE -->
        <div id="profileTab" class="tab-content">
            <div style="text-align: center; padding: 10px;">
                <div id="uiAvatar" style="width: 50px; height: 50px; background: #2ed573; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: bold; color: #000; margin: 0 auto 5px;">K</div>
                <h3 id="uiName" style="margin: 0; font-size: 14px;">Player</h3>
                <div id="uiProfilePhone" style="font-size: 10px; color: #8a99ad;">--</div>
            </div>
            <button onclick="logoutUser()" style="width: 100%; background: #ff4757; color: #fff; border: none; padding: 10px; border-radius: 5px; font-weight: bold; margin-top: 15px; cursor: pointer;">ውጣ (Logout)</button>
        </div>

        <!-- BOTTOM NAV -->
        <div class="bottom-nav">
            <div class="nav-item active" onclick="switchTab('gameTab', this)"><span class="nav-icon">🏠</span><span>መነሻ</span></div>
            <div class="nav-item" onclick="switchTab('historyTab', this)"><span class="nav-icon">📊</span><span>ታሪክ</span></div>
            <div class="nav-item" onclick="switchTab('walletTab', this)"><span class="nav-icon">💳</span><span>ቴሌብር</span></div>
            <div class="nav-item" onclick="switchTab('profileTab', this)"><span class="nav-icon">👤</span><span>ፕሮፋይል</span></div>
        </div>
    </div>

    <script>
        const cardsData = {{ cards | tojson }};
        
        let tgId = "";
        let tgName = "Player";

        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe?.user) {
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
            const tgUser = window.Telegram.WebApp.initDataUnsafe.user;
            tgId = tgUser.id.toString();
            tgName = tgUser.first_name + (tgUser.last_name ? " " + tgUser.last_name : "");
        }
        
        if (!tgId) {
            let storedId = localStorage.getItem('bingo_tg_id');
            if (!storedId) {
                storedId = 'user_' + Math.floor(Math.random() * 900000 + 100000);
                localStorage.setItem('bingo_tg_id', storedId);
            }
            tgId = storedId;
            tgName = "ተጫዋች_" + tgId.slice(-4);
        }
        
        let voiceEnabled = true;
        let lastSpokenNumber = "";
        let syncInterval = null;

        // Unlock audio on first user click/interaction to satisfy autoplay policies
        document.addEventListener('click', () => {
            if (voiceEnabled) {
                speakText("");
            }
        }, { once: true });

        window.onload = function() {
            init600CardsGrid();
            initMainBoard();
            syncWithServer();
            syncInterval = setInterval(syncWithServer, 1000);
        };

        function logoutUser() {
            alert("የቴሌግራም ሚኒ አፕ ተዘግቷል።");
        }

        function speakText(text) {
            if (!voiceEnabled) return;
            try {
                const audioUrl = `https://translate.google.com/translate_tts?ie=UTF-8&q=${encodeURIComponent(text)}&tl=am&client=tw-ob`;
                const audio = new Audio(audioUrl);
                audio.play().catch(e => console.log("Audio play error:", e));
            } catch (err) {
                console.log("TTS error:", err);
            }
        }

        function syncWithServer() {
            fetch(`/api/state?tg_id=${encodeURIComponent(tgId)}&name=${encodeURIComponent(tgName)}`)
            .then(r => r.json())
            .then(data => {
                if (!data.user) return;
                const u = data.user;
                const g = data.game;

                document.getElementById('uiWallet').innerText = u.wallet.toFixed(2) + " ETB";
                document.getElementById('uiMainWallet').innerText = u.wallet.toFixed(2) + " ETB";
                document.getElementById('uiPhone').innerText = "ID: " + u.id.slice(-4);
                document.getElementById('uiProfilePhone').innerText = "Telegram ID: " + u.id;
                document.getElementById('uiName').innerText = u.name;
                document.getElementById('uiAvatar').innerText = u.name.charAt(0).toUpperCase();

                document.getElementById('uiPlayersCount').innerText = data.active_players;
                document.getElementById('uiPrizePool').innerText = g.prize_pool.toFixed(2) + " ETB";
                document.getElementById('count').innerText = data.my_cards.length;

                updateHistoryList(u.history);

                if (g.status === "WAITING") {
                    document.getElementById('winModal').style.display = 'none';
                    document.getElementById('cardsGrid').style.display = 'grid';
                    document.getElementById('mainBoardWrapper').style.display = 'none';
                    document.getElementById('mainBoard').style.display = 'none';
                    document.getElementById('drawDisplay').style.display = 'none';
                    document.getElementById('timerBox').style.display = 'block';
                    document.getElementById('timer').innerText = g.timer;
                    lastSpokenNumber = "";

                    updateGridButtons(data.my_cards, data.taken_cards);
                    renderSelectedCards(data.my_cards, g.called_numbers);

                } else if (g.status === "PLAYING") {
                    document.getElementById('winModal').style.display = 'none';
                    document.getElementById('cardsGrid').style.display = 'none';
                    document.getElementById('mainBoardWrapper').style.display = 'grid';
                    document.getElementById('mainBoard').style.display = 'grid';
                    document.getElementById('drawDisplay').style.display = 'block';
                    document.getElementById('timerBox').style.display = 'none';

                    document.getElementById('currentNum').innerText = g.current_number;
                    if (g.current_number !== "--" && g.current_number !== lastSpokenNumber) {
                        lastSpokenNumber = g.current_number;
                        speakText(g.current_number);
                    }

                    for (let i = 1; i <= 75; i++) {
                        const cell = document.getElementById('board-cell-' + i);
                        if (cell) {
                            if (g.called_numbers.includes(i)) cell.classList.add('called');
                            else cell.classList.remove('called');
                        }
                    }
                    renderSelectedCards(data.my_cards, g.called_numbers);

                } else if (g.status === "WON" && g.winner) {
                    const w = g.winner;
                    document.getElementById('modalWinnerName').innerText = `${w.name} WON!`;
                    document.getElementById('modalWinnerPrize').innerText = `+${w.prize.toFixed(2)} ETB`;
                    document.getElementById('modalCartelaNo').innerText = `Cartela #${w.card_id}`;
                    document.getElementById('winCountdown').innerText = g.win_countdown;

                    if (lastSpokenNumber !== "WON") {
                        lastSpokenNumber = "WON";
                        speakText("ቢንጎ! አሸናፊ " + w.name);
                    }

                    const matrix = cardsData[w.card_id];
                    let cardHtml = `<div class="b-grid">`;
                    ['B', 'I', 'N', 'G', 'O'].forEach(h => cardHtml += `<div class="cell header-cell">${h}</div>`);
                    for (let r = 0; r < 5; r++) {
                        for (let c = 0; c < 5; c++) {
                            let val = matrix[c][r];
                            let isFree = (val === "FREE") ? "free" : "";
                            let isMarked = (val !== "FREE" && g.called_numbers.includes(parseInt(val))) ? "marked" : "";
                            let isWin = w.winning_cells.includes(`${r}-${c}`) ? "winning-line" : "";
                            cardHtml += `<div class="cell ${isFree} ${isMarked} ${isWin}">${val}</div>`;
                        }
                    }
                    cardHtml += `</div>`;
                    document.getElementById('modalCartelaContent').innerHTML = cardHtml;
                    document.getElementById('winModal').style.display = 'flex';
                }
            });
        }

        function init600CardsGrid() {
            const grid = document.getElementById('cardsGrid');
            grid.innerHTML = '';
            for (let i = 1; i <= 600; i++) {
                const btn = document.createElement('button');
                btn.className = 'card-btn';
                btn.id = 'grid-btn-' + i;
                btn.innerText = i;
                btn.onclick = () => selectCard(i);
                grid.appendChild(btn);
            }
        }

        function updateGridButtons(myCards, takenCards) {
            for (let i = 1; i <= 600; i++) {
                const btn = document.getElementById('grid-btn-' + i);
                if (!btn) continue;
                btn.classList.remove('selected', 'taken');
                if (myCards.includes(i)) btn.classList.add('selected');
                else if (takenCards.includes(i)) btn.classList.add('taken');
            }
        }

        function initMainBoard() {
            const mainBoard = document.getElementById('mainBoard');
            mainBoard.innerHTML = '';
            for (let i = 1; i <= 75; i++) {
                const cell = document.createElement('div');
                cell.className = 'board-cell';
                cell.id = 'board-cell-' + i;
                cell.innerText = i;
                mainBoard.appendChild(cell);
            }
        }

        function selectCard(cardId) {
            fetch('/api/select_card', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({tg_id: tgId, card_id: cardId})
            }).then(r => r.json()).then(res => {
                if (!res.success) alert(res.message);
                else syncWithServer();
            });
        }

        function renderSelectedCards(myCards, calledNumbers) {
            const container = document.getElementById('selectedCards');
            if (!myCards || myCards.length === 0) {
                container.innerHTML = `<div style="color: #8a99ad; font-size: 10px; text-align: center; margin-top: 15px;">ካርድ አልመረጡም</div>`;
                return;
            }
            let html = '';
            myCards.forEach(id => {
                const card = cardsData[id];
                html += `<div class="bingo-card"><h4>Cartela #${id}</h4><div class="b-grid">`;
                ['B', 'I', 'N', 'G', 'O'].forEach(h => html += `<div class="cell header-cell">${h}</div>`);
                for (let r = 0; r < 5; r++) {
                    for (let c = 0; c < 5; c++) {
                        let val = card[c][r];
                        let isFree = (val === "FREE") ? "free" : "";
                        let isMarked = (val !== "FREE" && calledNumbers && calledNumbers.includes(parseInt(val))) ? "marked" : "";
                        html += `<div class="cell ${isFree} ${isMarked}">${val}</div>`;
                    }
                }
                html += `</div></div>`;
            });
            container.innerHTML = html;
        }

        function submitDeposit() {
            const method = document.getElementById('payMethod').value;
            const amount = document.getElementById('depositAmount').value;
            const ref = document.getElementById('payRef').value.trim();
            fetch('/api/deposit', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({tg_id: tgId, method, amount, ref})
            }).then(r => r.json()).then(res => {
                alert(res.message);
                if (res.success) { document.getElementById('payRef').value = ''; syncWithServer(); switchTab('historyTab', document.querySelectorAll('.nav-item')[1]); }
            });
        }

        function updateHistoryList(history) {
            const container = document.getElementById('historyList');
            if (!history || history.length === 0) {
                container.innerHTML = `<div style="color: #8a99ad; font-size: 10px; text-align: center; margin-top: 15px;">ምንም ግብይት የለም</div>`;
                return;
            }
            let html = '';
            history.forEach(h => {
                let isPlus = (h.type === 'DEPOSIT' || h.type === 'WIN PRIZE');
                html += `<div class="tx-card"><div class="tx-left"><div class="tx-type">${h.type}</div><div class="tx-date">${h.date}</div></div><div style="text-align:right;"><div class="tx-amount ${isPlus?'deposit':'withdraw'}">${isPlus?'+':'-'}${h.amount.toFixed(2)}</div><span class="status-badge ${h.status.toLowerCase()}">${h.status}</span></div></div>`;
            });
            container.innerHTML = html;
        }

        let clickCount = 0;
        function triggerSecretAdmin() {
            clickCount++;
            if (clickCount >= 3) {
                clickCount = 0;
                let pin = prompt("Admin PIN:");
                if (pin === "1234") { switchTab('adminTab', null); loadAdmin(); }
                else if (pin !== null) alert("የተሳሳተ ፒን!");
            }
            setTimeout(() => clickCount = 0, 3000);
        }

        function loadAdmin() {
            fetch('/api/admin/transactions').then(r => r.json()).then(data => {
                const container = document.getElementById('adminTransactionsList');
                if (!data.transactions || data.transactions.length === 0) {
                    container.innerHTML = `<div style="color: #8a99ad; font-size: 10px; text-align: center; margin-top: 15px;">ምንም ፔንዲንግ የለም</div>`;
                    return;
                }
                let html = '';
                data.transactions.forEach(h => {
                    html += `<div class="tx-card" style="flex-direction: column; align-items: stretch; gap: 5px;"><div style="display:flex; justify-content:space-between;"><div><b>${h.type}</b> (${h.user_name})<br><span style="font-size:9px; color:#ffd700;">Ref: ${h.ref}</span></div><div class="tx-amount deposit">${h.amount} ETB</div></div><div style="display:flex; gap:5px;"><button onclick="adminAction(${h.id}, 'APPROVE')" style="flex:1; background:#38ef7d; border:none; padding:4px; font-weight:bold; border-radius:3px; cursor:pointer;">✔ Approve</button><button onclick="adminAction(${h.id}, 'REJECT')" style="flex:1; background:#ff4757; color:#fff; border:none; padding:4px; font-weight:bold; border-radius:3px; cursor:pointer;">✖ Reject</button></div></div>`;
                });
                container.innerHTML = html;
            });
        }

        function adminAction(txId, action) {
            fetch('/api/admin/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({tx_id: txId, action})
            }).then(r => r.json()).then(res => { if (res.success) { loadAdmin(); syncWithServer(); } });
        }

        function switchTab(tabId, el) {
            document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(e => e.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            if (el) el.classList.add('active');
            else document.querySelectorAll('.nav-item')[0].classList.add('active');
            if (tabId === 'adminTab') loadAdmin();
        }
    </script>
</body>
</html>
"""

@app.route("/")
def home():
  return render_template_string(TEMPLATE, cards=ALL_CARDS)

if __name__ == "__main__":
  threading.Thread(target=game_engine_loop, daemon=True).start()
  setup_webhook_and_commands()
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port, debug=False)
