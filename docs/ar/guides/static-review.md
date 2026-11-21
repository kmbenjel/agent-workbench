---
title: المراجعة من موقع ثابت
description: نشر صفحة المراجعة على أي استضافة ثابتة مع خدمة صغيرة للملاحظات، فتُفتح برابط عادي من الهاتف ولا يبقى عندك شيء يعمل.
order: 12
---

# المراجعة من موقع ثابت

في [المراجعة من حاسوب بعيد](remote-review.md) يبقى حاسوب يعمل ما دامت المراجعة قائمة. هنا لا يبقى شيء: تُرفع الصفحة
مرة واحدة إلى **استضافة ثابتة** (GitHub Pages أو Cloudflare Pages أو حاوية تخزين أو مجلد على خادم ويب)، وتذهب علاماتك
وملاحظاتك إلى **خدمة صغيرة للملاحظات**، ثم يسحبها الوكيل إلى ملفه المحلي. فتفتح الصفحة برابط `https` عادي من هاتفك
أو من أي حاسوب، ولو كان حاسوب الوكيل مطفأً.

## ما تحتاج إليه

1. **خدمة الملاحظات.** في المستودع مثال جاهز لـ Cloudflare في المجلد `examples/cloudflare-worker`: عامل (Worker) واحد
   وحاوية R2 واحدة، يُنشران بثلاثة أوامر. وتصلح أي خدمة تجيب عن الطلبات الأربعة الموصوفة في ملف البروتوكول.
2. **استضافة ثابتة** لمجلد الموقع.
3. **الإعداد** في `workbench.toml`:

```toml
[static]
api = "https://awb-comments.example.workers.dev"
page_url = "https://reviews.example.org"
publish_cmd = "rsync -a {site}/ host:/var/www/reviews/"
```

والمفتاحان يؤخذان من متغيرات البيئة لا من الملف، فلا يدخلان مستودعًا: `AWB_REVIEW_KEY` للمراجع و`AWB_STATIC_TOKEN`
للوكيل.

## نشر خدمة الملاحظات على Cloudflare

```bash
cd examples/cloudflare-worker
npx wrangler r2 bucket create awb-comments
npx wrangler deploy
npx wrangler secret put REVIEW_KEY
npx wrangler secret put AGENT_TOKEN
```

قبل `deploy` ضع في `wrangler.toml` عنوان موقع صفحاتك في `ALLOWED_ORIGINS`. والمفتاحان سلسلتان عشوائيتان تولّدهما أنت، مثلًا:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
```

## الاستعمال

```bash
awb page build ocr --cards cards.jsonl
awb static publish
awb static url ocr
awb static loop &
awb watch
```

- `awb static publish` يشغّل `publish_cmd` على مجلد الموقع.
- `awb static url ocr` يطبع رابط المراجع، وآخره `#k=` ثم المفتاح.
- `awb static loop` يسحب الملاحظات الجديدة كل 20 ثانية ويدفع لوحة الأخبار، و`awb static sync` يفعل ذلك مرة واحدة.
- ما يُسحب يُضاف إلى `remarks.jsonl`، فيعمل `awb watch` و`awb remarks` و`awb close` وإخفاء البطاقات الموافَق عليها كما في
  الخادم المحلي تمامًا.

بعد كل إعادة بناء للصفحة أعد `awb static publish`. أما الأخبار فلا تحتاج إلى نشر: تدفعها المزامنة.

## ما يراه المراجع

- يحمل الرابط المفتاح بعد العلامة `#`، وهذا الجزء من الرابط لا يُرسَل إلى أي خادم. ويحفظه المتصفح لذلك الموقع، فتُفتح
  الصفحات الأخرى بعد ذلك دونه.
- إن فُتح الرابط بلا مفتاح صحيح قالت الصفحة ذلك وأبقت علاماتك في المتصفح، وكذلك إن لم تُجب الخدمة. والحفظ المرفوض يظهر
  «لم يُحفظ»، ولا يظهر محفوظًا أبدًا.

!!! warning "الصفحات نفسها علنية"
    المفتاح يحمي الملاحظات والأخبار، لا الصفحات: الصفحة علنية بقدر ما تجعلها استضافتها. فإن كانت المادة خاصة فانشرها
    على استضافة تتحكم في الدخول، أو استعمل [المراجعة من حاسوب بعيد](remote-review.md) حيث لا يُنشر شيء.

!!! note "الرابط كلمة سر"
    من يملك الرابط يستطيع أن يضع العلامات ويكتب الملاحظات، فأرسله كما ترسل كلمة سر. ولإبطاله ضع مفتاح مراجع جديدًا
    بـ `npx wrangler secret put REVIEW_KEY`.

## التجربة بلا حساب

الملف `dev-server.mjs` في المجلد نفسه يشغّل خدمة الملاحظات نفسها على حاسوبك بذاكرة مؤقتة (Node 18 أو أحدث):

```bash
REVIEW_KEY=r AGENT_TOKEN=a ALLOWED_ORIGINS=http://127.0.0.1:8000 node dev-server.mjs --port 8787
```
