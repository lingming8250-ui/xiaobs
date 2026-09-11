#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小薄销 · 常驻服务
- 定时主动推送（北京时间，不走 GitHub 排队）
- 网页聊天
- 记忆与聊天记录落盘持久化
"""
import os
import json
import time
import threading
import datetime
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, render_template_string

CN_TZ = datetime.timezone(datetime.timedelta(hours=8))

BARK_KEY = os.environ.get("BARK_KEY", "")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")
MODEL = os.environ.get("MODEL_NAME", "deepseek-chat")

DATA_DIR = os.environ.get("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
MEM_FILE = os.path.join(DATA_DIR, "memory.json")
LOG_FILE = os.path.join(DATA_DIR, "log.json")

# 主动推送时刻（北京时间，逗号分隔）
PUSH_TIMES = [x.strip() for x in os.environ.get("PUSH_TIMES", "08:00,12:30,20:00,23:30").split(",") if x.strip()]

PERSONA = (
    "你是薄销，30岁，在上海做财务的社畜，INFJ。\n"
    "慵懒、爱吐槽，但心是软的。说话短，像随手发微信。\n"
    "凌冥是你认识很久的网友，在哈尔滨念书，你们关系很近。\n"
    "别说教，别肉麻，别问睡没睡、吃没吃。"
)

app = Flask(__name__)


def now_cn():
    return datetime.datetime.now(CN_TZ)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存失败 {path}: {e}")


def call_ai(messages, max_tokens=500, temperature=0.9):
    if not API_KEY:
        return "（还没配 API Key）"
    try:
        r = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"AI 调用失败: {e}")
        return "（脑子卡住了，稍后再说）"


def bark(title, content):
    if not BARK_KEY:
        print("未配置 BARK_KEY，跳过推送")
        return
    try:
        requests.get(
            f"https://api.day.app/{BARK_KEY}/{quote(title)}/{quote(content)}",
            timeout=10,
        )
        print(f"[推送] {content}")
    except Exception as e:
        print(f"推送失败: {e}")


def scheduler():
    """后台线程：到点就主动发消息"""
    sent = set()
    while True:
        try:
            now = now_cn()
            hm = now.strftime("%H:%M")
            key = f"{now.strftime('%Y-%m-%d')} {hm}"
            if hm in PUSH_TIMES and key not in sent:
                sent.add(key)
                if len(sent) > 50:
                    sent = set(list(sent)[-20:])

                logs = load_json(LOG_FILE, [])
                recent = "\n".join(
                    f"- {x.get('content', '')}" for x in logs[-6:] if x.get("content")
                ) or "（还没有发过）"
                mem = json.dumps(load_json(MEM_FILE, {}), ensure_ascii=False)

                prompt = (
                    PERSONA
                    + f"\n\n现在是 {now.strftime('%m月%d日 %H:%M')}。\n"
                    + f"【关于凌冥的记忆】\n{mem}\n\n"
                    + f"【你最近发过的消息】\n{recent}\n\n"
                    + "写一条现在发给他的消息。一到两句，口语，换个新角度，"
                    + "不要重复上面出现过的话题和句式。直接输出内容。"
                )
                msg = call_ai(
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "发一条"},
                    ],
                    max_tokens=150,
                    temperature=1.0,
                )
                bark("薄销", msg)
                logs.append({
                    "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "role": "assistant",
                    "content": msg,
                    "pushed": True,
                })
                save_json(LOG_FILE, logs[-200:])
        except Exception as e:
            print(f"定时器出错: {e}")
        time.sleep(20)


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "time": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "api_ready": bool(API_KEY),
        "push_times": PUSH_TIMES,
    })


@app.route("/history")
def history():
    logs = load_json(LOG_FILE, [])
    return jsonify(logs[-40:])


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "empty"}), 400

    logs = load_json(LOG_FILE, [])
    mem = json.dumps(load_json(MEM_FILE, {}), ensure_ascii=False)

    history_msgs = []
    for x in logs[-12:]:
        role = x.get("role")
        content = x.get("content")
        if role in ("user", "assistant") and content:
            history_msgs.append({"role": role, "content": content})

    messages = (
        [{"role": "system", "content": PERSONA + f"\n\n【关于凌冥的记忆】\n{mem}"}]
        + history_msgs
        + [{"role": "user", "content": user_msg}]
    )
    reply = call_ai(messages)

    ts = now_cn().strftime("%Y-%m-%d %H:%M:%S")
    logs.append({"time": ts, "role": "user", "content": user_msg})
    logs.append({"time": ts, "role": "assistant", "content": reply})
    save_json(LOG_FILE, logs[-200:])

    return jsonify({"reply": reply})


@app.route("/")
def index():
    return render_template_string(PAGE)


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>薄销</title>
<style>
:root{--bg:#111;--fg:#e8e8e8;--me:#3eb575;--other:#242424;}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--fg);height:100dvh;display:flex;flex-direction:column;font-family:-apple-system,"PingFang SC",sans-serif}
header{padding:14px;text-align:center;font-size:15px;color:#888;border-bottom:1px solid #222;flex:none}
#log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:9px}
.m{max-width:76%;padding:9px 13px;border-radius:14px;font-size:15px;line-height:1.55;word-break:break-word;white-space:pre-wrap}
.me{align-self:flex-end;background:var(--me);color:#0b0b0b;border-bottom-right-radius:4px}
.other{align-self:flex-start;background:var(--other);border-bottom-left-radius:4px}
.sys{align-self:center;color:#666;font-size:12px}
footer{padding:10px;display:flex;gap:8px;border-top:1px solid #222;flex:none;padding-bottom:calc(10px + env(safe-area-inset-bottom))}
input{flex:1;padding:12px;border-radius:20px;border:1px solid #333;background:#1a1a1a;color:#eee;font-size:16px;outline:none}
button{padding:0 18px;border-radius:20px;border:0;background:var(--me);color:#0b0b0b;font-size:15px;font-weight:600}
button:active{opacity:.7}
</style>
</head>
<body>
<header>薄销</header>
<div id="log"></div>
<footer>
  <input id="in" placeholder="说点什么…" autocomplete="off">
  <button onclick="send()">发</button>
</footer>
<script>
const log = document.getElementById('log');
const inp = document.getElementById('in');
const NAME = 'bs-chat-draft';
function add(text, cls){
  const d = document.createElement('div');
  d.className = 'm ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
async function loadHistory(){
  try{
    const r = await fetch('history');
    const arr = await r.json();
    arr.forEach(x => add(x.content, x.role === 'user' ? 'me' : 'other'));
  }catch(e){}
}
async function send(){
  const t = inp.value.trim();
  if(!t) return;
  inp.value = '';
  localStorage.removeItem(NAME);
  add(t, 'me');
  try{
    const r = await fetch('chat', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: t})
    });
    const d = await r.json();
    add(d.reply || '（没回）', 'other');
  }catch(e){
    add('（断了）', 'other');
  }
}
inp.addEventListener('keydown', e => { if(e.key === 'Enter') send(); });
inp.value = localStorage.getItem(NAME) || '';
inp.addEventListener('input', () => localStorage.setItem(NAME, inp.value));
loadHistory();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    threading.Thread(target=scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    print(f"启动：http://0.0.0.0:{port}  推送时刻={PUSH_TIMES}")
    app.run(host="0.0.0.0", port=port)
