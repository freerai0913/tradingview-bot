# app.py
import os
import json
import logging
from flask import Flask, request, jsonify
from binance.um_futures import UMFutures
from binance.error import ClientError
import requests

# ===== 設定 =====
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# 從環境變數讀取（Render 後台設定）
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# 初始化 Binance 客戶端
client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)

# ===== 工具函數 =====
def send_discord(message: str, embeds: list = None):
    if not DISCORD_WEBHOOK_URL:
        app.logger.warning("Discord Webhook URL 未設定")
        return
    payload = {"content": message}
    if embeds:
        payload["embeds"] = embeds
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        app.logger.error(f"Discord 發送失敗: {e}")

def get_symbol_info(symbol):
    """獲取合約最小單位與價格精度"""
    try:
        info = client.exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                step_size = float(s['filters'][1]['stepSize'])  # LOT_SIZE
                tick_size = float(s['filters'][0]['tickSize'])  # PRICE_FILTER
                return step_size, tick_size
        return 0.001, 0.01  # 預設
    except Exception as e:
        app.logger.error(f"獲取 {symbol} 資訊失敗: {e}")
        return 0.001, 0.01

def round_step(value, step):
    return round(value / step) * step

# ===== 主路由 =====
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "無 JSON 資料"}), 400

        app.logger.info(f"收到警報: {data}")

        # 必要欄位
        symbol = data.get('symbol', 'BTCUSDT').upper()
        side = data.get('side', '').upper()  # BUY / SELL
        entry = float(data.get('entry'))
        sl = float(data.get('sl'))
        tp1 = float(data.get('tp1'))
        tp2 = float(data.get('tp2'))

        if side not in ['BUY', 'SELL']:
            return jsonify({"error": "side 必須是 BUY 或 SELL"}), 400

        # 獲取合約精度
        step_size, tick_size = get_symbol_info(symbol)

        # 計算倉位（先固定 10 USDT 名義價值，可改為動態）
        price = entry
        notional = 10.0  # 10 USDT 名義價值
        quantity = notional / price
        quantity = round_step(quantity, step_size)

        if quantity <= 0:
            return jsonify({"error": "計算出的數量 <= 0"}), 400

        # 下單（市價單）
        order = client.new_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity
        )
        app.logger.info(f"開倉成功: {order}")

        # 備註：Binance Futures 不支援 REST API 直接設 TP/SL
        # 你需手動在交易所 UI 設定，或使用另一個策略機器人管理止盈止損
        # 此處僅通知 Discord

        # Discord 通知
        embed = {
            "title": f"🚀 自動開倉 - {symbol}",
            "description": f"方向: {'多' if side == 'BUY' else '空'}\n"
                           f"數量: {quantity}\n"
                           f"進場: {entry}\n"
                           f"止損: {sl}\n"
                           f"TP1: {tp1}\n"
                           f"TP2: {tp2}",
            "color": 0x00FF00 if side == 'BUY' else 0xFF0000
        }
        send_discord("", embeds=[embed])

        return jsonify({"status": "success", "order": order}), 200

    except ClientError as e:
        error_msg = f"Binance API 錯誤: {e.message} (code: {e.code})"
        app.logger.error(error_msg)
        send_discord(f"❌ 開倉失敗: {error_msg}")
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        error_msg = f"未知錯誤: {str(e)}"
        app.logger.error(error_msg)
        send_discord(f"❌ 系統錯誤: {error_msg}")
        return jsonify({"error": error_msg}), 500

# 健康檢查（Render 需要）
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "OK"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
