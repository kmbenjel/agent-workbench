---
title: التثبيت
description: تثبيت agent-workbench بأمر واحد، وتثبيت إضافة Claude Code.
order: 2
---

# التثبيت

## المتطلبات

- بايثون 3.10 أو أحدث (يُنصح بـ 3.11 فأعلى فلا يحتاج إلى أي حزمة إضافية).
- لا حاجة إلى قاعدة بيانات ولا إلى Docker.
- الوضع البعيد: `ssh` و`rsync` على الجهازين، ويُستحسن حاسوب سحابي فيه بايثون 3.
- أجزاء «صحّة المهمة» (حارس الذاكرة والبطارية والشبكة) تقرأ نظام لينكس.

## بأمر واحد

بلا تثبيت دائم:

```bash
uvx agent-workbench demo --serve
```

أو تثبيتًا دائمًا:

```bash
pipx install agent-workbench
awb --version
```

أو من المصدر:

```bash
git clone https://github.com/kmbenjel/agent-workbench
cd agent-workbench
make install
```

## إضافة Claude Code

أضف المتجر ثم ثبّت الإضافة منه:

```text
/plugin marketplace add kmbenjel/agent-workbench
/plugin install agent-workbench@agent-workbench
```

تضيف الإضافة أربع مهارات (`review-loop` و`remote-review` و`static-review` و`job-hygiene`) وأربعة أوامر شرطة مائلة
(`/agent-workbench:review` و`:watch` و`:news` و`:status`) وخادم MCP فيه الأدوات `post_news` و`read_remarks`
و`make_review_page` و`close_card`، ومراقِبًا يبثّ ملاحظاتك إلى الجلسة.

!!! note "ملاحظة"
    الإضافة مكتفية بنفسها: تحمل الأمر `awb` معها وتضعه في `PATH` الوكيل ما دامت مفعّلة، فلا تحتاج إلا إلى
    `python3` (3.10 أو أحدث). وإن كنت ثبّتّ `awb` بنفسك فنسختك هي التي تُستعمل.

## ملف الإعداد

ملف اختياري اسمه `workbench.toml`. أنشئ نموذجًا مشروحًا بـ:

```bash
awb init
```

كل مفتاح له قيمة افتراضية، وأي مفتاح غير معروف يُعدّ خطأ برسالة واضحة.
