# SNI-Finder Scanner

SNI-Finder جفت‌های SNI+IP را با زنجیره‌ای سه‌مرحله‌ای اسکن می‌کند:

1. **SNISPF core** — در حالت سخت‌گیرانه `wrong_seq` برای هر جفت اجرا می‌شود.
2. **Xray core** — با یک outbound از نوع VLESS که به نمونه SNISPF محلی متصل است راه‌اندازی می‌شود.
3. **HTTP probe** — یک درخواست HTTP از طریق رابط SOCKS در Xray ارسال می‌کند تا سالم بودن هر جفت را تأیید کند.

> راهنمای انگلیسی: [README.md](README.md)

---

## شروع سریع (نسخه‌های Release)

روش پیشنهادی استفاده از بسته‌های آماده Release است.

**۱. دانلود بسته مناسب سیستم‌عامل** از GitHub Releases:

| سیستم‌عامل | فایل |
|------------|------|
| ویندوز | `sni-finder_windows_amd64_bundle.zip` |
| لینوکس | `sni-finder_linux_amd64_bundle.tar.gz` |

**۲. استخراج آرشیو** و باز کردن ترمینال در پوشه استخراج‌شده.

**۳. ویرایش `config/sni-list.txt`** — در هر خط یک SNI قرار دهید.

**۴. اجرای اسکنر:**

*ویندوز (حتماً با Administrator):*
```powershell
cd sni-finder_windows_amd64_bundle
.\start.bat
```

*لینوکس (با دسترسی لازم):*
```bash
cd sni-finder_linux_amd64_bundle
chmod +x ./start.sh
sudo ./start.sh
```

**۵. راه‌اندازی اولیه:**
- لانچر وابستگی‌های Python را بررسی کرده و موارد ناقص را نصب می‌کند.
- اگر `vless_source` پیکربندی نشده باشد، تنظیم تعاملی به صورت خودکار شروع می‌شود.

**۶. شروع اسکن:**
- از منو گزینه **Run Scan** را انتخاب کنید، یا
- مستقیم اجرا کنید: `python3 scanner.py run`

**۷. مشاهده نتایج:**
- `results/latest.json`
- `results/<timestamp>/working_pairs.txt`
- `logs/scanner.log`

---

## اسکرین‌شات‌ها

![صفحه نتایج](resources/SNI-Finder-01.png)
![اجرای اسکن](resources/SNI-Finder-02.png)
![منوی اصلی](resources/SNI-Finder-03.png)

---

## قابلیت‌ها

- خواندن لیست SNI از `config/sni-list.txt` و Resolve کردن به IPv4.
- فیلتر کردن جفت‌ها به ساب‌نت‌های Cloudflare پیش از شروع اسکن.
- اسکن موازی با worker‌های مستقل و پورت‌های ایزوله.
- نمایش داشبورد زنده Rich به همراه گزارش علت خطاها.
- ذخیره کامل خروجی‌های هر اجرا: خلاصه، لیست جفت‌های سالم/ناسالم، و لاگ.

---

## پیش‌نیازها

- **Python 3.10** یا بالاتر
- یک **VLESS** معتبر
- باینری‌های **SNISPF** و **Xray**

**ویندوز:**
- PowerShell را به صورت Administrator اجرا کنید (لازمه `wrong_seq` و WinDivert).
- فایل‌های زیر را در `bin/` قرار دهید:
  - `snispf_windows_amd64.exe`
  - `xray.exe`
  - `WinDivert.dll`
  - `WinDivert64.sys`

**لینوکس:**
- دسترسی raw packet داشته باشید (root یا `CAP_NET_RAW`).
- فایل‌های زیر را در `bin/` قرار دهید:
  - `snispf_linux_amd64` (یا نسخه arm64)
  - `xray`

**متغیرهای اختیاری** برای تعریف مسیر باینری‌ها (مسیر کامل، مسیر نسبی پروژه، یا نام دستور در `PATH`):

| متغیر | کاربرد |
|-------|--------|
| `SNI_FINDER_SNISPF_BIN` | تعریف مسیر باینری SNISPF |
| `SNI_FINDER_XRAY_BIN`   | تعریف مسیر باینری Xray   |

---

## نصب

**ویندوز:**
```powershell
cd SNI-Finder
pip install -r requirements.txt
```

**لینوکس:**
```bash
cd SNI-Finder
python3 -m pip install -r requirements.txt
```

---

## پیکربندی

`vless_source` را با یکی از روش‌های زیر تنظیم کنید:

- لینک کامل `vless://...`
- مسیر یک فایل txt حاوی `vless://...`
- مسیر یک فایل JSON از Xray با outbound نوع VLESS

**تنظیم تعاملی:**
```bash
python3 scanner.py configure
```

فایل تنظیمات: `config/scanner_settings.json`

---

## اجرا

| روش | دستور |
|-----|-------|
| اسکریپت لانچ (ویندوز) | `start.bat` |
| اسکریپت لانچ (لینوکس) | `sudo ./start.sh` |
| حالت منو | `python3 scanner.py` |
| اسکن مستقیم | `python3 scanner.py run` |
| فقط Resolve | `python3 scanner.py resolve` |
| اجرا با VLESS موقت | `python3 scanner.py run --vless "vless://..."` |

**توقف نرم:** در حین اسکن `Ctrl+C` بزنید. Worker‌های فعال پروسه‌ها را پاکسازی کرده و پورت‌ها را آزاد می‌کنند.

---

## ساخت بسته انتشار

اسکریپت انتشار به صورت خودکار آخرین نسخه پایدار این ابزارها را دریافت می‌کند:
- **SNISPF** از `NaxonM/snispf-core`
- **Xray** از `XTLS/Xray-core`

**ویندوز:**
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release_bundles.ps1
```

**لینوکس:**
```bash
bash ./scripts/build_release_bundles.sh
```

**فایل‌های خروجی:**

| فایل | توضیح |
|------|-------|
| `release/sni-finder_windows_amd64_bundle.zip` | بسته ویندوز |
| `release/sni-finder_linux_amd64_bundle.tar.gz` | بسته لینوکس |
| `release/checksums.txt` | چک‌سام فایل‌ها |
| `release/release_manifest.json` | مانیفست انتشار |

---

## انتشار با GitHub Actions (پیشنهادی)

انتشار نهایی را با workflow انجام دهید و فایل‌های generated را مستقیماً commit نکنید.

**فایل workflow:** `.github/workflows/release.yml`

| تریگر | رفتار |
|-------|-------|
| `workflow_dispatch` | ساخت بسته برای تست یا بررسی |
| Push تگ با الگوی `v*` | ساخت بسته و انتشار خودکار روی GitHub Releases |

**نمونه انتشار با تگ:**
```bash
git tag v0.1.0
git push origin v0.1.0
```

---

## خروجی‌های اجرا

| مسیر | توضیح |
|------|-------|
| `results/latest.json` | نتایج آخرین اجرا |
| `results/<timestamp>/summary.json` | خلاصه اجرا |
| `results/<timestamp>/working_pairs.json` | جفت‌های سالم (JSON) |
| `results/<timestamp>/failed_pairs.json` | جفت‌های ناسالم (JSON) |
| `results/<timestamp>/working_pairs.txt` | جفت‌های سالم (متن ساده) |
| `logs/scanner.log` | لاگ کامل اسکنر |

---

## نکته‌ها

- فایل `config/cf_subnets.txt` اجباری است و باید پیش از اجرا وجود داشته باشد.
- جفت‌هایی که خارج از ساب‌نت‌های شناخته‌شده Cloudflare هستند، پیش از شروع اسکن حذف می‌شوند.
