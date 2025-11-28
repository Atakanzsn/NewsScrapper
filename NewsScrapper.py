import os
import io
import sys
import time
import re
import random
import datetime
import urllib.parse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# edit names and sites

NAMES_DEFAULT = ["Ahmet D.", "Elif T.", "Mehmet O."]
SITES_DEFAULT = ["diken.com.tr", "sozcu.com.tr"]
KEYWORDS_DEFAULT = [
    "reform", "skandal", "dava", "şirket", "yatırım",
    "kaçakçılık", "taciz", "tecavüz", "tarikat", "ödenek"
]
TIMEOUT_DEFAULT = 30 

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

DATE_SELECTORS = [
    "time[datetime]", "meta[property='article:published_time']", "meta[name='publish-date']",
    ".post-meta-info span", ".entry-date", ".article-date time", "time", "span.date",
    "div.date", ".jeg_post_meta .jeg_meta_date span", ".news-date", "small.date", "small",
    ".td-post-date", ".date", ".meta-date"
]

def log_print(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")

def normalize(text):
    if not text: return ""
    mapping = str.maketrans("IİĞÜÖÇŞ", "iiğüöçş")
    t = text.translate(mapping).lower()
    t = re.sub(r'[^\w\s]', '', t)
    return t

def title_match(text, keywords):
    if not text: return False
    t = normalize(text)
    keyword_ok = any(normalize(k) in t for k in keywords)
    return keyword_ok

def normalize_date_string(date_str):
    if not date_str or "Tarih" in date_str: return date_str
    try:
        months_tr_en = {'ocak': 'january', 'şubat': 'february', 'mart': 'march', 'nisan': 'april', 'mayıs': 'may', 'haziran': 'june', 'temmuz': 'july', 'ağustos': 'august', 'eylül': 'september', 'ekim': 'october', 'kasım': 'november', 'aralık': 'december'}
        normalized = normalize(date_str)
        
        for tr, en in months_tr_en.items(): normalized = normalized.replace(tr, en)
        
        cleaned_date = re.sub(r'[^a-z0-9\s-:]', '', normalized)

        if re.search(r'\d+\s+(gun|saat|dakika)\s+once', cleaned_date):
            return datetime.date.today().strftime("%Y-%m-%d (Tahmini)")

        date_obj = None
        if re.match(r'\d{4}-\d{2}-\d{2}', cleaned_date):
            date_obj = datetime.datetime.fromisoformat(cleaned_date.split('T')[0])
        
        if date_obj: return date_obj.strftime("%Y-%m-%d")
        
    except Exception:
        pass
    return date_str.strip()

def extract_date(article_page, url):
    try:
        article_page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
        article_page.goto(url, timeout=10000, wait_until='domcontentloaded')
    except Exception:
        return "Tarih alınamadı"

    for sel in DATE_SELECTORS:
        try:
            el = article_page.query_selector(sel)
            if not el: continue

            content = el.get_attribute("datetime")
            if not content: content = el.get_attribute("content")
            if content and content.strip(): return normalize_date_string(content)

            txt = el.text_content()
            if txt and txt.strip(): return normalize_date_string(txt)
        except Exception:
            continue
    return "Tarih bulunamadı"

def block_resources(route):
    url = route.request.url
    resource_type = route.request.resource_type

    blocked_domains = [
        "google-analytics", "doubleclick", "adservice", "googlesyndication",
        "criteo", "yandex.ru", "facebook", "twitter", "gemius", "hotjar",
        "taboola", "outbrain", "teads", "onesignal"
    ]

    if any(domain in url for domain in blocked_domains):
        route.abort()
        return

    if resource_type in ["image", "media", "font"]:
        route.abort()
        return

    route.continue_()

def auto_scroll(page):
    try:
        page.evaluate("""
            async () => {
                await new Promise((resolve) => {
                    var totalHeight = 0;
                    var distance = 300;
                    var timer = setInterval(() => {
                        var scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;

                        if(totalHeight >= scrollHeight - window.innerHeight || totalHeight > 15000){
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }
        """)
        time.sleep(1)
    except: pass

# search funcs

def scrape_site_internal_search(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords):
    
    base_url = f"https://{site_url}"
    log_print(f"-> [SİTE İÇİ] {site_name} taranıyor.")
    results = []

    try:
        search_page.goto(base_url, timeout=site_timeout_ms + 10000, wait_until='domcontentloaded')
        time.sleep(2.0)
    except Exception as e:
        log_print(f"⚠ [SİTE İÇİ] {site_name}: Erişim hatası ({str(e)[:50]}...). Bing'e geçiliyor.")
        return None

    search_input = None

    search_triggers = ['.tdb-head-search-btn', '.td-icon-search', 'a[aria-label="Search"]', 'button[aria-label="Ara"]', 'button[aria-label="Search"]', 'a[href*="search"]', 'i[class*="search"]', '.search-icon', '.fa-search', '#search-button']
    input_query = "//input[@type='search' or contains(@name, 'q') or contains(@class, 'search') or contains(@id, 'search')]"

    try:
        search_input = search_page.query_selector(input_query)

        if not search_input or not search_input.is_visible():
            for trigger in search_triggers:
                try:
                    btn = search_page.query_selector(trigger)
                    if btn and btn.is_visible():
                        btn.click(timeout=3000)
                        time.sleep(1.0)
                        break
                except: continue

        FINAL_INPUT_SELECTORS = [
            '.tdb-head-search-form-input', 'input[type="search"]', 'input[name="q"]', 'input[name="query"]',
            'input[name="s"]', 'input[placeholder*="ara"]', 'input[class*="search"]', 'form[role="search"] input'
        ]

        for sel in FINAL_INPUT_SELECTORS:
            try:
                search_input = search_page.wait_for_selector(sel, state="visible", timeout=3000)
                if search_input: break
            except Exception: continue

    except Exception: pass

    if not search_input:
        try:
            search_query_url = f"https://{site_url}/arama?q={urllib.parse.quote(target_name)}"
            if "diken" in site_url:
                 search_query_url = f"https://{site_url}/?s={urllib.parse.quote(target_name)}"
            elif "sozcu" in site_url:
                 search_query_url = f"https://{site_url}/arama/?search={urllib.parse.quote(target_name)}"

            search_page.goto(search_query_url, timeout=site_timeout_ms, wait_until='domcontentloaded')
            time.sleep(3.0)
        except:
            log_print(f"❌ [SİTE İÇİ] {site_name}: Arama yapılamadı. Bing'e geçiliyor.")
            return None
    else:
        try:
            search_input.fill(target_name)
            search_input.press("Enter")

            try:
                search_page.wait_for_load_state("networkidle", timeout=10000)
            except:
                time.sleep(5.0)
        except: pass

    MAX_PAGES = 3 
    LOAD_MORE_SELECTORS = ['.td-load-more-wrap a', 'a.load-more-btn', 'button.load-more', 'a[rel="next"]', '.pagination a.next', 'text="Daha Fazla"', 'text="Load More"']

    for page_num in range(MAX_PAGES):
        log_print(f"   → Sayfa {page_num + 1} taranıyor...")

        auto_scroll(search_page)

        try:
            all_links = search_page.query_selector_all('a[href]')

            for a in all_links:
                try:
                    href = a.get_attribute("href")
                    title = (a.text_content() or "").strip()

                    if not href or not title: continue
                    if href.startswith("/"): href = f"https://{site_url}{href}"
                    if site_url not in href: continue

                    is_news = False
                    link_class = a.get_attribute("class") or ""
                    parent_class = a.evaluate("el => el.parentElement.className") or ""
                    
                    if "td-image-wrap" in link_class or "entry-title" in link_class or "post-title" in link_class or "col-" in parent_class: 
                        is_news = True

                    if any(x in href for x in ["/haber/", "/yazarlar/", "/gundem/", "/dunya/", "/ekonomi/", ".html", "-haberi-", "-son-dakika-"]):
                        is_news = True

                    if not is_news and len(title) < 15: continue
                    if not title_match(title, keywords): continue
                    if any(r['link'] == href for r in results): continue

                    results.append({
                        "site": site_name, "title": title, "link": href,
                        "date": extract_date(article_page, href), "search_name": target_name
                    })
                except Exception: continue
        except Exception: pass

        if page_num < MAX_PAGES - 1:
            loaded_more = False
            for lm_sel in LOAD_MORE_SELECTORS:
                try:
                    load_more_btn = search_page.query_selector(lm_sel)
                    if load_more_btn and load_more_btn.is_visible():
                        log_print(f"   → 'Daha Fazla Göster' / Sonraki Sayfa butonuna tıklandı.")
                        load_more_btn.scroll_into_view_if_needed()
                        load_more_btn.click()
                        time.sleep(4.0) 
                        try: search_page.wait_for_load_state("networkidle", timeout=5000)
                        except: pass
                        loaded_more = True
                        break
                except: continue

            if not loaded_more: break

    log_print(f"   ✅ [SİTE İÇİ] {site_name}: {len(results)} haber bulundu.")
    return results


def scrape_bing_search_fallback(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords):
    
    bing_query = urllib.parse.quote(f'site:{site_url} "{target_name}"')
    search_url = f"https://www.bing.com/search?q={bing_query}"

    log_print(f"-> [BING YEDEK] {site_name} taranıyor...")
    results = []

    try:
        search_page.goto(search_url, timeout=site_timeout_ms, wait_until='domcontentloaded')

        try:
            search_page.wait_for_selector('li.b_algo h2 a', timeout=8000)
        except:
            log_print(f"⚠ [BING] Sonuç yok veya zaman aşımı. Google'a geçiliyor.")
            return scrape_google_search_fallback(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords)

        time.sleep(2.0)

        links = search_page.query_selector_all('li.b_algo h2 a')

        if links:
            log_print(f"   → Bing'de {len(links)} sonuç bulundu.")
            for a in links:
                try:
                    href = a.get_attribute("href")
                    title = a.text_content()

                    if not href or not title: continue
                    if site_url not in href: continue
                    if not title_match(title, keywords): continue

                    results.append({
                        "site": site_name, "title": title, "link": href,
                        "date": extract_date(article_page, href), "search_name": target_name
                    })
                except: continue

            log_print(f"   ✅ [BING YEDEK] {site_name}: {len(results)} haber çekildi.")
            return results
    except Exception as e:
        log_print(f"❌ [BING] Hata: {e}")

    return []


def scrape_google_search_fallback(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords, max_retries=2):
    
    google_query = urllib.parse.quote(f'site:{site_url} "{target_name}"')
    search_url = f"https://www.google.com/search?q={google_query}&hl=tr"

    log_print(f"-> [GOOGLE YEDEK] {site_name} taranıyor (Son Çare)...")
    results = []

    for attempt in range(max_retries):
        try:
            search_page.goto(search_url, timeout=site_timeout_ms, wait_until='domcontentloaded')

            try:
                consent_btn = search_page.query_selector('button:has-text("Kabul et"), button:has-text("Accept all")')
                if consent_btn and consent_btn.is_visible(): consent_btn.click()
            except: pass

            try:
                search_page.wait_for_selector('#search .g', timeout=8000)
            except:
                time.sleep(3)
                continue

            result_blocks = search_page.query_selector_all('#search .g')

            if result_blocks:
                for block in result_blocks:
                    try:
                        link_el = block.query_selector('a')
                        if not link_el: continue
                        href = link_el.get_attribute("href")
                        title = block.query_selector('h3').text_content()

                        if not href or not title: continue
                        if href.startswith("/url?q="):
                            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('q', [''])[0]

                        if site_url not in href: continue
                        if not title_match(title, keywords): continue

                        results.append({
                            "site": site_name, "title": title, "link": href,
                            "date": extract_date(article_page, href), "search_name": target_name
                        })
                    except: continue
                return results

        except Exception:
            time.sleep(2)
            continue

    return []


def create_output_file(results, filename="tarama_raporu.txt"):
    output = io.StringIO()
    output.write(f"TARAMA RAPORU\n")
    output.write(f"TARİH: {datetime.date.today()}\n")
    output.write("=" * 60 + "\n\n")

    total_count = 0

    for target_name, res_list in results.items():
        total_count += len(res_list)
        output.write(f"\n############################################################\n")
        output.write(f"## SONUÇLAR: {target_name} (Toplam {len(res_list)} Haber)\n")
        output.write(f"############################################################\n\n")

        if not res_list:
             output.write("Bu isim ve kriterler için haber bulunamadı.\n\n")

        for r in res_list:
            output.write(f"[{r['site']}]\n")
            output.write(f"Başlık : {r['title']}\n")
            output.write(f"Tarih  : {r['date']}\n")
            output.write(f"Link   : {r['link']}\n")
            output.write("-" * 60 + "\n\n")

    log_print(f"\nToplam {total_count} eşleşen haber bulundu. '{filename}' dosyasına yazılıyor.")

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(output.getvalue())

# run

def run_scraper(names_list, sites_list, site_timeout_ms, keywords):
    
    total_tasks = len(names_list) * len(sites_list)
    completed_tasks = 0
    all_results = {}
    
    log_print(f"Toplam {total_tasks} görev için tarama başlatılıyor...")

    try:
        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-infobars"]
            )
            
            user_agent = random.choice(USER_AGENTS)
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1366, "height": 768},
                locale="tr-TR",
                timezone_id="Europe/Istanbul"
            )

            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            context.route("**/*", block_resources)

            search_page = context.new_page()
            article_page = context.new_page()

            for i, target_name in enumerate(names_list):

                log_print(f"\n{'='*40}")
                log_print(f"🚀 BAŞLATILIYOR ({i+1}/{len(names_list)}): {target_name}")
                log_print(f"{'='*40}")

                current_results = []

                for site_url in sites_list:

                    site_name = site_url.split('.')[0].upper()
                    site_results = None

                    # 1. Adım: Site içi arama
                    site_results = scrape_site_internal_search(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords)

                    # 2. Adım: Başarısız olursa Bing yedek araması
                    if not site_results:
                        site_results = scrape_bing_search_fallback(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords)

                    # 3. Adım: Hala sonuç yoksa Google yedek araması
                    if not site_results:
                        site_results = scrape_google_search_fallback(search_page, article_page, site_name, site_url, target_name, site_timeout_ms, keywords)

                    current_results.extend(site_results or [])

                    completed_tasks += 1
                    progress = int((completed_tasks / total_tasks) * 100)
                    log_print(f"  [GENEL İLERLEME: {progress}%] - Sonuç: {len(site_results or [])} haber.")
                    time.sleep(1) 

                all_results[target_name] = current_results
            
            browser.close()
            log_print("\n✨ TÜM İŞLEMLER BAŞARIYLA TAMAMLANDI.")
            return all_results

    except Exception as e:
        log_print(f"\n❌ KRİTİK HATA: Playwright başlatılamadı veya çalışırken sorun çıktı: {e}")
        return {}

# main

def main():
    print("------------------------------------------")
    print("   NewsScraper BAŞLATILIYOR")
    print("------------------------------------------")

    site_timeout_ms = TIMEOUT_DEFAULT * 1000

    print("\n--- TARAMA BAŞLADI ---\n")

    results = run_scraper(NAMES_DEFAULT, SITES_DEFAULT, site_timeout_ms, KEYWORDS_DEFAULT)

    if results:
        create_output_file(results)
    else:
        print("\nSonuç alınamadı veya kritik hata oluştu.")
        
    print("\n------------------------------------------")
    print("   PROGRAM SONA ERDİ")
    print("------------------------------------------")


if __name__ == "__main__":
    main()