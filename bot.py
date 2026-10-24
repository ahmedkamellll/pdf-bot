import os
import io
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import fitz  # PyMuPDF
import google.generativeai as genai
from telegram import Update, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv

load_dotenv()

# ==================== الإعدادات ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("لازم تحط TELEGRAM_TOKEN و GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== Health Check لـ Render ====================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        return  # عشان ميعملش لوج كتير

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health server running on port {port}")
    server.serve_forever()

# ==================== أوامر البوت ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بيك 👋\n\n"
        "ابعتلي أي PDF محاضرة وأنا هحولها لأسئلة كويز (اختيار من متعدد).\n\n"
        "جرب دلوقتي 📄"
    )

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document

    if not document or document.mime_type != "application/pdf":
        await update.message.reply_text("ابعت ملف PDF بس من فضلك 📄")
        return

    status_msg = await update.message.reply_text("جاري قراءة المحاضرة... ⏳")

    try:
        # تنزيل الملف
        file = await context.bot.get_file(document.file_id)
        pdf_bytes = await file.download_as_bytearray()

        # استخراج النص
        text = ""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                text += page.get_text()

        text = text.strip()
        if len(text) < 150:
            await status_msg.edit_text("مفيش نص كافي في الملف ده.")
            return

        # قص النص عشان ميتعداش الحدود
        text = text[:15000]

        await status_msg.edit_text("جاري توليد الأسئلة باستخدام Gemini... 🧠")

        prompt = f"""
أنت مساعد تعليمي ممتاز. اقرأ النص التالي من محاضرة جامعية واعمل **8 أسئلة** اختيار من متعدد.

القواعد المهمة:
- كل سؤال يكون واضح ومباشر.
- 4 اختيارات فقط لكل سؤال.
- إجابة واحدة صحيحة فقط.
- الأسئلة تكون مفيدة للطالب.
- استخدم لغة عربية فصحى سهلة.

ارجع النتيجة بالشكل ده بالظبط (مهم جدًا للتنسيق):

السؤال 1: [نص السؤال هنا]
أ) [الاختيار الأول]
ب) [الاختيار الثاني]
ج) [الاختيار الثالث]
د) [الاختيار الرابع]
الإجابة الصحيحة: أ

السؤال 2: ...
(وهكذا لحد السؤال 8)

النص:
{text}
"""

        response = model.generate_content(prompt)
        questions_text = response.text

        questions = parse_questions(questions_text)

        if not questions:
            await status_msg.edit_text("مش قدرت أستخرج الأسئلة بشكل صحيح. جرب ملف تاني أو حاول مرة كمان.")
            return

        await status_msg.edit_text(f"تم توليد {len(questions)} أسئلة. هبعتهم دلوقتي 👇")

        for q in questions:
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=q["question"][:300],
                options=[opt[:100] for opt in q["options"]],
                type=Poll.QUIZ,
                correct_option_id=q["correct"],
                is_anonymous=False,
            )

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"حصل خطأ: {str(e)[:200]}")

def parse_questions(text: str):
    """تحليل النص الراجع من Gemini"""
    questions = []
    blocks = text.strip().split("السؤال")

    for block in blocks[1:]:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        if len(lines) < 5:
            continue

        try:
            question = lines[0].split(":", 1)[-1].strip()
            options = []
            correct = 0

            for line in lines[1:]:
                clean = line.strip()
                if clean.startswith(("أ)", "ا)", "أ ", "ا ")):
                    options.append(clean.split(")", 1)[-1].strip() if ")" in clean else clean[2:].strip())
                elif clean.startswith(("ب)", "ب ")):
                    options.append(clean.split(")", 1)[-1].strip() if ")" in clean else clean[2:].strip())
                elif clean.startswith(("ج)", "ج ")):
                    options.append(clean.split(")", 1)[-1].strip() if ")" in clean else clean[2:].strip())
                elif clean.startswith(("د)", "د ")):
                    options.append(clean.split(")", 1)[-1].strip() if ")" in clean else clean[2:].strip())
                elif "الإجابة الصحيحة" in clean or "الاجابة الصحيحة" in clean:
                    ans = clean.split(":")[-1].strip()
                    if any(x in ans for x in ["أ", "ا", "A", "a"]):
                        correct = 0
                    elif any(x in ans for x in ["ب", "B", "b"]):
                        correct = 1
                    elif any(x in ans for x in ["ج", "C", "c"]):
                        correct = 2
                    elif any(x in ans for x in ["د", "D", "d"]):
                        correct = 3

            if len(options) >= 4 and question:
                questions.append({
                    "question": question,
                    "options": options[:4],
                    "correct": correct
                })
        except Exception:
            continue

    return questions

# ==================== التشغيل ====================
def main():
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    logger.info("البوت بدأ الشغل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
  
