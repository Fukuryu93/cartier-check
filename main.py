import os
import sys
import time
import re
import email
import imaplib
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
import requests

# ======================================================================
# GitHub Secrets（環境変数）から情報を取得
# ======================================================================
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
USER_NAME = os.environ.get("USER_NAME")
USER_PHONE = os.environ.get("USER_PHONE")
USER_EMAIL = os.environ.get("USER_EMAIL")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

required_secrets = {
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
    "USER_NAME": USER_NAME,
    "USER_PHONE": USER_PHONE,
    "USER_EMAIL": USER_EMAIL,
}

missing = [k for k, v in required_secrets.items() if not v]
if missing:
    print(f"【エラー】以下の GitHub Secrets が未設定です: {', '.join(missing)}")
    sys.exit(1)

name_parts = USER_NAME.strip().split()
LAST_NAME = name_parts[0] if len(name_parts) > 0 else ""
FIRST_NAME = name_parts[1] if len(name_parts) > 1 else ""

TARGET_URL = "https://mycartiervisit.jp/cartier/terms-or-services"

STORE_PRIORITY = [
    "カルティエ心斎橋ブティック",
    "カルティエ ブティック 大丸心斎橋店",
    "カルティエブティック髙島屋大阪店",
    "カルティエブティック阪急うめだ本店",
    "カルティエブティック阪急うめだ本店１階",
    "カルティエブティック近鉄あべのハルカス店"
]

USER_INFO = {
    "last_name": LAST_NAME,
    "first_name": FIRST_NAME,
    "phone": USER_PHONE,
    "email": USER_EMAIL,
    "first_visit": "初めて",
    "party_size": "パートナーと"
}

TARGET_DAY = 10
TARGET_TIME = "18:00"

def send_discord_notification(message):
    if not DISCORD_WEBHOOK_URL:
        print(f"  [通知スキップ] DISCORD_WEBHOOK_URL未設定:\n{message}")
        return
    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
        if res.status_code in [200, 204]:
            print("  [Discord通知送信成功]")
        else:
            print(f"  [Discord通知失敗] Status: {res.status_code}")
    except Exception as e:
        print(f"  [Discord通知エラー] {e}")

def click_next_if_exists(page, timeout=3000):
    next_keywords = ["次へ", "進む", "確認画面へ", "次へ進む"]
    for kw in next_keywords:
        try:
            btn = page.get_by_role("button", name=re.compile(kw)).first
            if not btn.is_visible():
                btn = page.get_by_text(kw).first

            if btn.is_visible(timeout=timeout):
                btn.scroll_into_view_if_needed()
                btn.click(force=True)
                print(f"  -> 「{kw}」をクリックしました")
                page.wait_for_load_state("networkidle", timeout=5000)
                return True
        except Exception:
            continue
    return False

def click_confirm_button(page, timeout=3000):
    """説明文を避けて右下の『予約確定』ボタンを狙い撃ちクリックする関数"""
    selectors = [
        page.locator("button, a, [role='button'], input[type='submit']").filter(has_text=re.compile(r"予約確定")),
        page.get_by_role("button", name=re.compile(r"予約確定")),
        page.get_by_text(re.compile(r"予約確定"))
    ]
    
    for loc in selectors:
        try:
            count = loc.count()
            if count > 0:
                # 画面上の「最後」の要素（＝説明文ではなく右下のボタン）を指定
                target = loc.last
                if target.is_visible(timeout=timeout):
                    target.scroll_into_view_if_needed()
                    target.click(force=True)
                    print("  -> 右下の「予約確定」ボタンをクリックしました。")
                    return True
        except Exception:
            continue
    return False

def fetch_verification_code_from_gmail(timeout_sec=60):
    print("  -> Gmailから【カルティエ専用】最新の新着認証コードメールを受信監視中...")
    monitor_start_time = datetime.now(timezone.utc)
    start_time = time.time()
    
    while time.time() - start_time < timeout_sec:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(USER_EMAIL, GMAIL_APP_PASSWORD.replace(" ", ""))
            mail.select("inbox")
            
            status, messages = mail.search(None, '(FROM "noreply@mail.myboutique.pro")')
            if status != "OK" or not messages[0]:
                status, messages = mail.search(None, '(SUBJECT "カルティエ")')
            
            if status == "OK" and messages[0]:
                email_ids = messages[0].split()
                for email_id in reversed(email_ids[-5:]):
                    status, msg_data = mail.fetch(email_id, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
                            date_hdr = msg.get("Date")
                            if date_hdr:
                                try:
                                    msg_date = parsedate_to_datetime(date_hdr)
                                    if msg_date < monitor_start_time - timedelta(seconds=5):
                                        continue
                                except Exception:
                                    pass

                            from_hdr = str(msg.get("From", ""))
                            subject_hdr = str(msg.get("Subject", ""))
                            
                            if "myboutique" not in from_hdr.lower() and "カルティエ" not in subject_hdr and "カルティエ" not in from_hdr:
                                continue

                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() in ["text/plain", "text/html"]:
                                        try:
                                            body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        except Exception:
                                            pass
                            else:
                                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                            
                            clean_text = re.sub(r'<[^>]+>', ' ', body)
                            
                            match = re.search(r"認証コード[は:：\s]*(\d{4})", clean_text)
                            if not match:
                                match = re.search(r"コード[は:：\s]*(\d{4})", clean_text)
                                
                            if match:
                                code = match.group(1)
                                mail.logout()
                                print(f"  -> 【新着メールから抽出成功】認証コード: {code}")
                                return code
            mail.logout()
        except Exception as e:
            print(f"    [受信監視ポーリング中...] {e}")
        
        time.sleep(3)
    
    print("  -> タイムアウト: 監視開始後に送信された認証コードメールを受信できませんでした。")
    return None

def fill_customer_info(page):
    print("  -> お客様情報を自動入力中...")
    page.wait_for_load_state("domcontentloaded")
    time.sleep(1)

    fields = [
        ("姓", USER_INFO["last_name"], ["input[placeholder*='姓']", "input[name*='last']"]),
        ("名", USER_INFO["first_name"], ["input[placeholder*='名']", "input[name*='first']"]),
        ("電話番号", USER_INFO["phone"], ["input[type='tel']", "input[placeholder*='電話']", "input[name*='phone']"]),
        ("メールアドレス", USER_INFO["email"], ["input[type='email']", "input[placeholder*='メール']", "input[name*='email']"])
    ]

    for label, val, selectors in fields:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.clear()
                    el.press_sequentially(val, delay=50)
                    el.dispatch_event("change")
                    el.dispatch_event("blur")
                    break
            except Exception:
                continue

    time.sleep(1)

def handle_verification_code(page):
    print("\n==================================================")
    print("【自動化処理】メールからの認証コード受信待機中...")
    
    code = fetch_verification_code_from_gmail(timeout_sec=60)
    if not code:
        print("  -> 認証コードの取得に失敗しました。")
        return False
    
    time.sleep(1.5)
    
    inputs = page.locator("input:visible").all()
    print(f"  -> 検出されたコード入力欄の数: {len(inputs)}")
    
    if len(inputs) == 0:
        print("  -> 【エラー】認証コードの入力欄が見つかりません。")
        page.screenshot(path="error_no_otp_input.png")
        return False

    try:
        if len(inputs) == len(code):
            for i, digit in enumerate(code):
                inp = inputs[i]
                inp.click()
                inp.focus()
                inp.fill(digit)
                inp.dispatch_event("input")
                inp.dispatch_event("change")
                time.sleep(0.1)
        else:
            first_input = inputs[0]
            first_input.click()
            first_input.focus()
            first_input.clear()
            first_input.press_sequentially(code, delay=100)
            first_input.dispatch_event("input")
            first_input.dispatch_event("change")
            
        print(f"  -> 認証コード [{code}] のタップ入力・イベント発火を完了しました。")
    except Exception as e:
        print(f"  -> 入力処理中にエラー発生: {e}")
        return False

    time.sleep(1.5)

    # 認証画面でのボタンクリック処理
    if not click_confirm_button(page):
        click_next_if_exists(page)

    # --- 認証コード送信後の追加フロー ---
    print("  -> 認証コード送信後の画面遷移を待機中...")
    time.sleep(3)

    # ステップ1: 追加情報画面 (/cartier/additional-info) で「次へ」をクリック
    print("  -> [1/3] 追加情報画面を通過。「次へ」をクリックします...")
    click_next_if_exists(page, timeout=5000)
    time.sleep(2.5)

    # ステップ2: 最終確認画面 (/cartier/confirm) で「予約確定」をクリック
    print("  -> [2/3] 最終確認画面に到達。「予約確定」ボタンをクリックします...")
    if not click_confirm_button(page, timeout=5000):
        print("  -> 予約確定ボタンの判定に失敗したため、「次へ」を試行します...")
        click_next_if_exists(page, timeout=3000)

    # ステップ3: 予約完了画面 (/cartier/thank-you) への遷移を確認
    print("  -> [3/3] 予約完了画面(thank-you)への遷移を確認中...")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    
    for _ in range(12):
        body_text = page.inner_text("body")
        current_url = page.url
        
        if "thank-you" in current_url or any(kw in body_text for kw in ["予約が完了", "ご予約ありがとうございます", "完了いたしました", "受付いたしました"]):
            print("  -> 【確定成功】予約完了画面の表示を確認しました！")
            return True
            
        if "error" in current_url or any(err in body_text for err in ["無効", "正しくありません", "期限切れ", "エラー"]):
            print(f"  -> 【エラー】画面遷移中にエラーが発生しました。（URL: {current_url}）")
            page.screenshot(path="error_otp_failed.png")
            return False
            
        time.sleep(1)

    print(f"  -> 【失敗】予約完了画面への到達を確認できませんでした。（URL: {page.url}）")
    page.screenshot(path="error_not_completed.png")
    return False

def complete_reservation(page, store_name, target_day=None, target_time=None):
    time_pattern = re.compile(r"^\d{1,2}:\d{2}$")
    
    date_elements = page.locator("button, [role='button'], li, div").all()
    target_date_el = None
    selected_day_str = ""

    for el in date_elements:
        try:
            if not el.is_visible():
                continue
            text = el.inner_text().strip()
            if not text.isdigit() or not (1 <= int(text) <= 31):
                continue
            
            class_attr = (el.get_attribute("class") or "").lower()
            if "cursor-pointer" in class_attr or "day" in class_attr or "date" in class_attr:
                day_num = int(text)
                if target_day is None or day_num == target_day:
                    target_date_el = el
                    selected_day_str = f"{day_num}日"
                    break
        except Exception:
            continue

    if not target_date_el:
        print(f"  -> 指定/選択可能な日付が見つかりませんでした (Target: {target_day})")
        return False

    print(f"  -> 日付 [{selected_day_str}] を選択します...")
    target_date_el.click()
    time.sleep(1.5)

    time_trigger = page.get_by_text(time_pattern).first
    if time_trigger.is_visible(timeout=2000):
        time_trigger.click()
        time.sleep(1.5)

    time_options = page.get_by_text(time_pattern).all()
    chosen_time_el = None
    chosen_time_str = ""

    for t_el in time_options:
        try:
            if t_el.is_visible():
                t_text = t_el.inner_text().strip()
                if target_time is None or t_text == target_time:
                    chosen_time_el = t_el
                    chosen_time_str = t_text
                    break
        except Exception:
            continue

    if not chosen_time_el:
        print(f"  -> 指定/選択可能な時間が見つかりませんでした (Target: {target_time})")
        return False

    print(f"  -> 時間 [{chosen_time_str}] を選択します...")
    chosen_time_el.click()
    time.sleep(1)

    click_next_if_exists(page)
    time.sleep(2)

    fill_customer_info(page)
    click_next_if_exists(page)
    time.sleep(2)

    print("  -> ご相談内容画面を通過中...")
    click_next_if_exists(page)
    time.sleep(2)

    print("  -> 予約内容確認画面。「予約確定」ボタンを検索・クリックします...")
    if not click_confirm_button(page):
        print("  -> 【エラー】「予約確定」ボタンが見つかりませんでした。")
        page.screenshot(path="error_no_confirm_btn.png")
        return False

    print("  -> 認証コード入力画面への移行を確認中...")
    for i in range(10):
        body_text = page.inner_text("body")
        inputs = page.locator("input:visible").all()
        
        if ("認証" in body_text or "コード" in body_text) and len(inputs) in [1, 4]:
            print("  -> 【成功】認証コード入力画面に到達しました！")
            success = handle_verification_code(page)
            if success:
                success_msg = (
                    f"🎉 **【カルティエ来店予約 完全完了！】**\n"
                    f"**店舗:** {store_name}\n"
                    f"**日時:** {selected_day_str} {chosen_time_str}\n"
                    f"**お名前:** {USER_INFO['last_name']} {USER_INFO['first_name']} 様\n"
                )
                print("\n" + success_msg + "\n")
                send_discord_notification(success_msg)
                return True
            else:
                return False
            break
        
        error_el = page.locator(".error, .error-message, [class*='error'], [class*='invalid']").first
        if error_el.is_visible(timeout=500):
            print(f"  -> 【画面上エラー検知】: {error_el.inner_text().strip()}")
            
        time.sleep(1)

    print(f"  -> 認証画面へ到達できませんでした。（現在のURL: {page.url}）")
    page.screenshot(path="error_failed_to_auth_page.png")
    return False

def setup_page_to_stores(page):
    page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
    time.sleep(2)
    page.get_by_text("規約に同意する").first.click(force=True)
    time.sleep(2)

    page.get_by_text("ご来店").first.click(force=True)
    time.sleep(2)

    cat_btn = page.get_by_text("婚約＆結婚指輪").first
    if cat_btn.is_visible(timeout=3000):
        cat_btn.click(force=True)
        time.sleep(1)
    click_next_if_exists(page)
    time.sleep(2)

    item_btn = page.get_by_text("婚約指輪").first
    if item_btn.is_visible(timeout=3000):
        item_btn.click(force=True)
        time.sleep(3)

    solitaire_btn = page.get_by_text("ソリテール").first
    if solitaire_btn.is_visible(timeout=3000):
        solitaire_btn.click(force=True)
        time.sleep(1)
    
    love_btn = page.get_by_text("LOVE").first
    if love_btn.is_visible(timeout=3000):
        love_btn.click(force=True)
        time.sleep(1)

    click_next_if_exists(page)
    time.sleep(4)

    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    time.sleep(1)

    for trigger_text in ["都市を選ぶ", "エリアを選ぶ", "お店を選んでください", "都道府県", "エリア"]:
        try:
            trigger_btn = page.get_by_text(trigger_text, exact=False).first
            if trigger_btn.is_visible(timeout=1500):
                trigger_btn.click(force=True)
                time.sleep(1)
                break
        except Exception:
            pass

    osaka_candidates = [
        page.get_by_text("大阪府", exact=False).first,
        page.locator("xpath=//*[contains(text(), '大阪府')]").first
    ]

    for loc in osaka_candidates:
        try:
            loc.scroll_into_view_if_needed(timeout=2000)
            if loc.is_visible(timeout=2000):
                loc.click(force=True)
                time.sleep(3)
                break
        except Exception:
            continue

def main():
    print("監視実行を開始します...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 390, "height": 844}
        )
        page = context.new_page()

        try:
            for store_name in STORE_PRIORITY:
                print(f"\n==================================================")
                print(f"[{store_name}] のチェックを開始します...")
                
                try:
                    setup_page_to_stores(page)
                    
                    store_btn = page.get_by_text(store_name, exact=False).first
                    if not store_btn.is_visible(timeout=3000):
                        print(f"  -> 店舗 [{store_name}] が画面上に見つかりませんでした。スキップします。")
                        continue

                    store_btn.scroll_into_view_if_needed(timeout=2000)
                    store_btn.click(force=True)
                    time.sleep(1)

                    click_next_if_exists(page)
                    time.sleep(2)

                    is_navigated = page.get_by_text("ご予約の日時", exact=False).is_visible(timeout=2500)

                    if is_navigated:
                        print("  -> 画面遷移成功（予約枠あり）！全自動予約処理を開始します...")
                        success = complete_reservation(page, store_name, target_day=TARGET_DAY, target_time=TARGET_TIME)
                        if success:
                            print("  [二重予約防止] 全自動予約が完了したため、スクリプトを終了します。")
                            sys.exit(0)
                    else:
                        print("  -> 遷移不可（満席）のため、次の店舗の確認へ移ります。")

                except Exception as e:
                    print(f"  -> [{store_name}] のチェック中にエラー発生: {e}")

        except Exception as e:
            print(f"実行中にエラーが発生しました: {e}")
            sys.exit(1)
        finally:
            browser.close()

if __name__ == "__main__":
    main()

