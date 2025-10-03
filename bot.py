import logging
from datetime import datetime, date, time
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# === CONFIG ===
import os
TOKEN = os.getenv("BOT_TOKEN")  # set BOT_TOKEN in Render Dashboard → Environment
GROUP_ID = -1003164790829
WORK_START_LIMIT = time(9, 0, 0)
WORK_OVERTIME_LIMIT = time(22, 0, 0)
ACTIVITY_LIMIT = 15 * 60

FINES = {
    "late": 50,
    "night": 60,
    "overtime": 10
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_records = {}

reply_keyboard = [
    ["上班", "下班"],
    ["吃饭", "上厕所", "抽烟"],
    ["会议", "回座"]
]
markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)


def reset_all_records(context: ContextTypes.DEFAULT_TYPE):
    global user_records
    user_records = {}
    logger.info("Daily reset done.")


def reset_if_new_day(uid):
    today = date.today()
    if uid not in user_records or user_records[uid]["last_reset"] != today:
        user_records[uid] = {
            "counts": {}, "times": {}, "active": None,
            "work_start": None, "work_total": 0,
            "penalties": [], "last_reset": today
        }


def format_duration(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h} 小时 {m} 分钟 {s} 秒"
    if m: return f"{m} 分钟 {s} 秒"
    return f"{s} 秒"


async def send_penalty(context, user, action, status, fine, overtime=None):
    mention = f"[{user.first_name}](tg://user?id={user.id})"
    msg = (
        f"群组：{context.bot_data.get('group_name','本群')}\n"
        f"用户：{mention}\n"
        f"打卡活动：{action}\n"
        f"状态：{status}\n"
    )
    if overtime:
        msg += f"超时时长：{format_duration(overtime)}\n"
    msg += f"本次惩罚：{fine}￥"

    await context.bot.send_message(chat_id=GROUP_ID, text=msg, parse_mode="Markdown")


async def broadcast_penalties(context: ContextTypes.DEFAULT_TYPE):
    if not user_records:
        return
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"📊 今日罚款总结（{today}）"]

    for uid, record in user_records.items():
        total_penalty = sum(
            [int(p.split()[-1].replace("￥", "")) for p in record["penalties"]]
        ) if record["penalties"] else 0

        mention = f"[用户 {uid}](tg://user?id={uid})"
        if total_penalty == 0:
            lines.append(f"\n{mention}\n总罚款：0￥ ✅")
        else:
            details = "\n".join(record["penalties"])
            lines.append(f"\n{mention}\n总罚款：{total_penalty}￥\n明细：\n{details}")

    msg = "\n".join(lines)
    await context.bot.send_message(chat_id=GROUP_ID, text=msg, parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.bot_data["group_name"] = update.message.chat.title if update.message.chat else "群组"
    await update.message.reply_text("请选择打卡操作:", reply_markup=markup)


async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    action = update.message.text.strip()
    now = datetime.now()
    reset_if_new_day(user.id)
    record = user_records[user.id]

    # === 上班 ===
    if action == "上班":
        if record["work_start"] is not None:
            await update.message.reply_text(
                f"❌ 您已经上班过了！ {now.strftime('%m/%d %H:%M:%S')}",
                reply_markup=markup
            )
            return

        record["work_start"] = now
        msg = (
            f"✅ 打卡成功：上班 - {now.strftime('%m/%d %H:%M:%S')}\n"
            f"提示：请记得下班时打卡下班"
        )
        await update.message.reply_text(msg, reply_markup=markup)

        if now.time() > WORK_START_LIMIT and now.time() < WORK_OVERTIME_LIMIT:
            fine = FINES["late"]
            record["penalties"].append(f"迟到罚款 {fine}￥")
            await send_penalty(context, user, "上班", "迟到", fine)

        if now.time() >= WORK_OVERTIME_LIMIT:
            fine = FINES["night"]
            record["penalties"].append(f"晚班罚款 {fine}￥")
            await send_penalty(context, user, "上班", "晚间打卡超过 22:00", fine)
        return

    # === 下班 ===
    if action == "下班":
        if record["work_start"] is None:
            await update.message.reply_text(
                f"❌ 您还没有上班，无法下班！", reply_markup=markup
            )
            return
        duration = int((now - record["work_start"]).total_seconds())
        record["work_total"] += duration
        record["work_start"] = None
        penalty_text = "\n".join(record["penalties"]) if record["penalties"] else "无"
        msg = (
            f"✅ 打卡成功：下班 - {now.strftime('%m/%d %H:%M:%S')}\n"
            f"今日工作总计：{format_duration(record['work_total'])}\n"
            f"今日累计活动时间：{format_duration(record['times'].get('total',0))}\n"
            f"今日罚款记录：\n{penalty_text}"
        )
        await update.message.reply_text(msg, reply_markup=markup)
        return

    # === 回座 ===
    if action == "回座":
        if not record["active"]:
            await update.message.reply_text("❌ 您没有正在进行的活动", reply_markup=markup)
            return
        last_action, start_time = record["active"]
        duration = int((now - start_time).total_seconds())
        record["times"][last_action] = record["times"].get(last_action, 0) + duration
        record["times"]["total"] = record["times"].get("total", 0) + duration
        record["active"] = None

        if duration > ACTIVITY_LIMIT and last_action in ["上厕所", "抽烟", "吃饭"]:
            overtime = duration - ACTIVITY_LIMIT
            fine = FINES["overtime"]
            record["penalties"].append(f"{last_action}超时罚款 {fine}￥")
            await send_penalty(context, user, last_action, "超时", fine, overtime=overtime)

        msg = (
            f"✅ {now.strftime('%m/%d %H:%M:%S')} 回座：{last_action}\n"
            f"本次耗时：{format_duration(duration)}\n"
            f"今日累计{last_action}：{format_duration(record['times'][last_action])}\n"
            f"今日累计活动：{format_duration(record['times']['total'])}"
        )
        await update.message.reply_text(msg, reply_markup=markup)
        return

    # === 普通活动 ===
    record["counts"][action] = record["counts"].get(action, 0) + 1
    record["active"] = (action, now)
    await update.message.reply_text(
        f"✅ 打卡成功：{action} - {now.strftime('%m/%d %H:%M:%S')}\n"
        f"第 {record['counts'][action]} 次{action}\n"
        f"活动时间限制：15 分钟\n"
        f"请完成后打卡回座",
        reply_markup=markup
    )


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        reset_if_new_day(member.id)
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text="状态：已开启便捷回复键盘",
            reply_markup=markup
        )


def main():
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_daily(reset_all_records, time(hour=15, minute=0))
    app.job_queue.run_daily(broadcast_penalties, time(hour=13, minute=0))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, checkin))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.run_polling()


if __name__ == "__main__":
    main()
