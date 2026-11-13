---
title: البدء السريع
description: تجربة حلقة المراجعة كاملة في أقل من دقيقة على نصّ من الملك العام.
order: 3
---

# البدء السريع

نجرّب حلقة المراجعة على نصّ من الملك العام: وكيل صحّح التعرّف الضوئي لرواية «كبرياء وتحامل» لجين أوستن (1813،
مشروع غوتنبرغ رقم 1342) وبقيت له سبعة أسطر مشكوك فيها.

## 1. شغّل العرض

```bash
uvx agent-workbench demo --serve
```

يُنشئ الأمر مجلد `awb-demo` فيه البطاقات والصفحة، ثم يشغّل الخادم على `127.0.0.1:8765` (أي على حاسوبك وحده).

## 2. راجع في المتصفح

افتح `http://127.0.0.1:8765/ocr-fixes.html`. ترى في الأعلى سؤال الوكيل، وتحته سبع بطاقات. اضغط «Approve» على
ما تراه صوابًا، و«Reject» على الأخير، واكتب سببك في الخانة. كل ما تفعله يُحفظ أثناء الكتابة.

## 3. شاهد ما يصل إلى الوكيل

في طرفية ثانية:

```bash
uvx agent-workbench --root awb-demo watch --from-start --settle 3
```

يظهر سطر لكل ملاحظة:

```text
REMARK ocr-fixes line-7 verdict=reject «Keep the British spelling of the printed edition.»
REPLY _news n0ab092 verdict=none «British: follow the printed edition.»
```

## 4. أغلق البطاقة

```bash
uvx agent-workbench --root awb-demo close ocr-fixes line-7 'أبقيت الإملاء البريطاني: «Keep the British spelling»'
```

لاحظ أن الأمر يرفض أي ملاحظة إغلاق لا تقتبس كلامك الفعلي من السجل. هذا مقصود: لا يُسمح للوكيل بأن يدّعي موافقة
لم تقع.

## 5. أعد بناء الصفحة

```bash
uvx agent-workbench --root awb-demo page build ocr-fixes
```

تختفي البطاقات المُنجزة وتبقى أخبار الوكيل. وإن غيّر الوكيل محتوى بطاقة وافقتَ عليها عادت إلى الصفحة وعليها
علامة «تغيّرت منذ آخر حكم لك».

!!! tip "بلا تثبيت"
    نسخة ثابتة من الصفحة في `docs/demo/index.html` تعمل في أي متصفح، وتبقى علاماتك فيه وحده.

الخطوة التالية: [صفحة مراجعة لمهمتك](guides/review-page.md).
