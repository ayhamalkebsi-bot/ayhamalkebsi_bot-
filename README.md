# DigitalStoreBot

بوت تيليجرام جاهز لبيع الاشتراكات الرقمية عبر ShopDigital API.

## المزايا

- جلب المنتجات تلقائيًا من ShopDigital.
- عرض الأسعار بالدولار.
- إضافة هامش ربح 50% افتراضيًا.
- نظام رصيد داخلي للمستخدمين.
- تنفيذ الشراء تلقائيًا عبر API.
- حفظ الطلبات في SQLite.
- إعادة رصيد العميل تلقائيًا عند فشل الطلب.
- استخدام `external_order_id` فريد لمنع الخصم المكرر.
- لوحة إدارة لإضافة وخصم الأرصدة.
- إحصائيات للمستخدمين والمبيعات.
- عرض رصيد حساب ShopDigital للمدير.

## مهم قبل التشغيل

1. أنشئ بوتًا عبر BotFather.
2. من ShopDigital ألغِ مفتاح API القديم الظاهر في الصور عبر:
   `Revoke & Regenerate`
3. استخدم المفتاح الجديد فقط.
4. لا ترسل التوكن أو مفتاح API لأي شخص.
5. اشحن رصيد ShopDigital قبل تجربة الشراء.

## التشغيل على Termux

```bash
pkg update -y
pkg install python unzip -y
unzip DigitalStoreBot.zip
cd DigitalStoreBot
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

لمنع الهاتف من إيقاف Termux:

```bash
termux-wake-lock
```

## إعداد ملف .env

```env
BOT_TOKEN=توكن_البوت
SHOPDIGITAL_API_KEY=مفتاح_API_الجديد
ADMIN_ID=8188389318
PROFIT_MARGIN=0.50
DATABASE_PATH=bot.db
SHOPDIGITAL_BASE_URL=https://api.shopdigital.app
```

هامش الربح `0.50` يعني 50%.

مثال:

- سعر المزود: $10
- سعر العميل: $15

## أوامر الاستخدام

المستخدم يضغط `/start` ثم يمكنه:

- عرض المنتجات.
- مشاهدة رصيده.
- مشاهدة آخر طلباته.
- تأكيد الشراء.

المدير يرى زر لوحة الإدارة ويمكنه:

- إضافة رصيد.
- خصم رصيد.
- مشاهدة الإحصائيات.
- مشاهدة رصيد ShopDigital.

لإضافة رصيد، اضغط زر **إضافة رصيد** ثم أرسل:

```text
USER_ID AMOUNT
```

مثال:

```text
123456789 25
```

يجب أن يضغط المستخدم `/start` مرة واحدة قبل إضافة رصيد له.

## الرفع على Render

1. ارفع المشروع إلى GitHub.
2. أنشئ Background Worker جديدًا على Render.
3. استخدم:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`
4. أضف متغيرات البيئة الموجودة في `.env`.
5. لا ترفع ملف `.env` إلى GitHub.

ملاحظة: SQLite قد لا تكون مناسبة على بعض الخطط التي لا توفر قرصًا دائمًا. للتجربة تكفي، وللاستخدام التجاري الدائم يُفضّل PostgreSQL أو قرص دائم.

## الرفع على Railway

1. ارفع المشروع إلى GitHub.
2. أنشئ مشروعًا جديدًا في Railway.
3. اربط المستودع.
4. أضف متغيرات البيئة.
5. أمر التشغيل موجود في `railway.json`.

## ملفات المشروع

```text
DigitalStoreBot/
├── bot.py
├── config.py
├── database.py
├── shopdigital.py
├── keyboards.py
├── handlers/
│   ├── __init__.py
│   ├── common.py
│   ├── start.py
│   ├── user.py
│   ├── products.py
│   ├── purchase.py
│   └── admin.py
├── requirements.txt
├── .env.example
├── .gitignore
├── Procfile
├── render.yaml
├── railway.json
└── README.md
```

## تنبيه أمني

- لا تستخدم مفتاح API الذي ظهر في الصور السابقة.
- لا تضع الأسرار داخل ملفات الكود.
- لا ترفع `.env` إلى GitHub.
- احتفظ بنسخة احتياطية من قاعدة البيانات.
