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
import jpholiday

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

# 監視対象店舗のキーワード
TARGET_STORES = [
    {"name": "カルティエ 銀座4丁目ブティック", "keyword": "銀座4丁目"},
    {"name": "カルティエ 銀座並木通りブティック", "keyword": "並木通り"},
    {"name": "カルティエ ブティック GINZA SIX店", "keyword": "GINZA SIX"},
    {"name": "カルティエ ブティック 六本木ヒルズ店", "keyword": "六本木ヒルズ"},
    {"name": "カルティエ ブティック 西武池袋本店", "keyword": "池袋"},
    {"name": "カルティエ ブティック 髙島屋新宿店", "keyword": "高島屋"},
    {"name": "カルティエ ブティック 伊勢丹新宿本店", "keyword": "伊勢丹"},
    {"name": "カルティエ ブティック 三越日本橋本店", "keyword": "日本橋"}
]

USER_INFO = {
    "last_name": LAST_NAME,
    "first_name": FIRST_NAME,
    "phone": USER_PHONE,
    "email": USER_EMAIL,
    "first_visit": "初めて",
    "party_size": "パートナーと"
}

# 希望の時間帯の範囲（土日祝の自動予約用）
TARGET_START_HOUR = 11
TARGET_END_HOUR = 18

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

def is_weekend_or_holiday(day_int: int) -> bool:
    """日付（数値）から土曜日・日曜日・日本の祝日であるかを判定する"""
    try:
        current_year = datetime.now().year
        current_month = datetime.now().month
        dt = datetime(current_year, current_month, day_int)
        if dt.weekday() in [5, 6]:
            return True
        if jpholiday.is_holiday(dt):
            return True
    except Exception:
        pass
    return False

def click_next_if_exists(page, timeout=3000):
    next_keywords = ["次へ", "進む", "確認画面へ", "次へ進む", "→"]
    for kw in next_keywords:
        try:
            btn = page.get_by_role("button", name=re.compile(kw)).last
            if not btn.is_visible():
                btn = page.get_by_text(kw).last

            if btn.is_visible(timeout=timeout):
                btn.scroll_into_view_if_needed()
                btn.click(force=True)
                print(f"  -> 「{kw}」ボタンをクリックしました")
                page.wait_for_load_state("networkidle", timeout=5000)
                return True
        except Exception:
            continue
    return False

def click_back_button(page):
    """左下の「← 戻る」ボタンを押して店舗一覧に戻る"""
    back_keywords = ["戻る", "←"]
    for kw in back_keywords:
        try:
            btn = page.get_by_text(kw, exact=False).first
            if btn.is_visible(timeout=2000):
                btn.scroll_into_view_if_needed()
                btn.click(force=True)
                print(f"  -> 「{kw}」ボタン（戻る）をクリックしました")
                time.sleep(2)
                return True
        except Exception:
            continue
    # 万が一見つからない場合はブラウザバック
    try:
        page.go_back()
        time.sleep(2)
        return True
    except:
        pass
    return False

def click_confirm_button(page, timeout=3000):
    selectors = [
        page.locator("button, a, [role='button'], input[type='submit']").filter(has_text=re.compile(r"予約確定")),
        page.get_by_role("button", name=re.compile(r"予約確定")),
        page.get_by_text(re.compile(r"予約確定"))
    ]
    
    for loc in selectors:
        try:
            count = loc.count()
            if count > 0:
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

    if not click_confirm_button(page):
        click_next_if_exists(page)

    print("  -> 予約完了画面(thank-you)への遷移を確認中...")
    
    for _ in range(15):
        try:
            body_text = page.inner_text("body")
            current_url = page.url
            
            if "thank-you" in current_url or any(kw in body_text for kw in ["予約が完了", "ご予約ありがとうございます", "完了いたしました", "受付いたしました"]):
                print("  -> 【確定成功】予約完了画面の表示を確認しました！")
                return True
                
            if "error" in current_url or any(err in body_text for err in ["無効", "正しくありません", "期限切れ"]):
                print(f"  -> 【エラー】画面遷移中にエラーが発生しました。（URL: {current_url}）")
                page.screenshot(path="error_otp_failed.png")
                return False
        except Exception:
            pass
            
        time.sleep(1)

    print(f"  -> 【失敗】予約完了画面への到達を確認できませんでした。（URL: {page.url}）")
    page.screenshot(path="error_not_completed.png")
    return False

def execute_booking(page, store_keyword, day_num, time_str):
    """見つかった特定の店舗・日付・時間を指定して自動予約を完遂する関数"""
    try:
        print(f"  -> 【自動予約実行】キーワード [{store_keyword}] ({day_num}日 {time_str}) の予約手続きを進めます...")
        
        # 1. 日付の再選択
        date_elements = page.locator("button, [role='button'], li, div").all()
        for el in date_elements:
            if el.is_visible() and el.inner_text().strip() == str(day_num):
                class_attr = (el.get_attribute("class") or "").lower()
                if "cursor-pointer" in class_attr or "day" in class_attr or "date" in class_attr:
                    el.click()
                    time.sleep(1.5)
                    break

        # 2. 時間の選択
        time_pattern = re.compile(r"^\d{1,2}:\d{2}$")
        time_trigger = page.get_by_text(time_pattern).first
        if time_trigger.is_visible(timeout=2000):
            time_trigger.click()
            time.sleep(1.0)

        time_options = page.get_by_text(time_pattern).all()
        for t_el in time_options:
            if t_el.is_visible() and t_el.inner_text().strip() == time_str:
                t_el.click()
                time.sleep(1.0)
                break

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
                        f"**日時:** 10月{day_num}日 {time_str}\n"
                        f"**お名前:** {USER_INFO['last_name']} {USER_INFO['first_name']} 様\n"
                    )
                    print("\n" + success_msg + "\n")
                    send_discord_notification(success_msg)
                    
                    try:
                        with open("reservation_success.txt", "w") as f:
                            f.write("SUCCESS")
                    except Exception as e:
                        print(f"  [フラグファイル作成エラー] {e}")

                    return True
                else:
                    return False
            time.sleep(1)

    except Exception as e:
        print(f"  -> 予約実行中にエラー発生: {e}")
    
    return False

def scan_store_slots(page):
    """各店舗のページで、すべての空き枠をスキャンして収集する"""
    time_pattern = re.compile(r"^\d{1,2}:\d{2}$")
    date_elements = page.locator("button, [role='button'], li, div").all()
    slots_data = {} # {day_num: [times]}

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
                
                el.click()
                time.sleep(1.0)

                time_trigger = page.get_by_text(time_pattern).first
                if time_trigger.is_visible(timeout=1000):
                    time_trigger.click()
                    time.sleep(1.0)

                time_options = page.get_by_text(time_pattern).all()
                day_times = []
                for t_el in time_options:
                    try:
                        if t_el.is_visible():
                            t_text = t_el.inner_text().strip()
                            if t_text not in day_times:
                                day_times.append(t_text)
                    except Exception:
                        continue
                
                if day_times:
                    slots_data[day_num] = day_times
        except Exception:
            continue

    return slots_data

def navigate_to_store_selection(page):
    """規約同意から進めて、東京都の店舗一覧画面に到達させる"""
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
        time.sleep(2)

    solitaire_btn = page.get_by_text("ソリテール").first
    if solitaire_btn.is_visible(timeout=3000):
        solitaire_btn.click(force=True)
        time.sleep(1)
    
    love_btn = page.get_by_text("LOVE").first
    if love_btn.is_visible(timeout=3000):
        love_btn.click(force=True)
        time.sleep(1)

    click_next_if_exists(page)
    time.sleep(3)

    for trigger_text in ["都市を選ぶ", "エリアを選ぶ", "お店を選んでください", "都道府県", "エリア"]:
        try:
            trigger_btn = page.get_by_text(trigger_text, exact=False).first
            if trigger_btn.is_visible(timeout=1500):
                trigger_btn.click(force=True)
                time.sleep(1)
                break
        except Exception:
            pass

    tokyo_candidates = [
        page.get_by_text("東京都", exact=False).first,
        page.locator("xpath=//*[contains(text(), '東京都')]").first
    ]

    for loc in tokyo_candidates:
        try:
            loc.scroll_into_view_if_needed(timeout=2000)
            if loc.is_visible(timeout=2000):
                loc.click(force=True)
                time.sleep(3)
                break
        except Exception:
            continue
    time.sleep(2)

def main():
    print("監視実行を開始します（対象：東京都 8店舗 / 平日通知 ＆ 土日祝日11:00〜18:00早い時間優先自動予約）...")
    
    all_store_availability = {}
    best_target = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 390, "height": 844}
        )
        page = context.new_page()

        try:
            navigate_to_store_selection(page)

            for store in TARGET_STORES:
                store_name = store["name"]
                keyword = store["keyword"]
                print(f"\n==================================================")
                print(f"[{store_name}] ({keyword}) のチェックを開始します...")
                
                try:
                    store_text_el = page.get_by_text(keyword, exact=False).first
                    if not store_text_el.is_visible(timeout=3000):
                        print(f"  -> 店舗 [{store_name}] は画面内に見つかりません。")
                        continue

                    # 店舗カードや画像をクリックして選択状態にする
                    clicked = False
                    try:
                        parent = store_text_el.locator("xpath=ancestor::*[contains(@class, 'store') or contains(@class, 'card') or contains(@class, 'item') or contains(@class, 'box') or self::button or self::a or self::label][1]")
                        img_el = parent.locator("img").first
                        if img_el.is_visible(timeout=1500):
                            img_el.scroll_into_view_if_needed()
                            img_el.click(force=True)
                            print(f"  -> [{store_name}] の店舗写真画像をクリックしました。")
                            clicked = True
                        else:
                            parent.scroll_into_view_if_needed()
                            parent.click(force=True)
                            print(f"  -> [{store_name}] の店舗カード要素をクリックしました。")
                            clicked = True
                    except Exception:
                        pass

                    if not clicked:
                        store_text_el.scroll_into_view_if_needed()
                        store_text_el.click(force=True)
                        print(f"  -> [{store_name}] のテキスト部分をクリックしました。")

                    time.sleep(1.5)

                    # 右下の「次へ」ボタンを押して次画面へ進む
                    if not click_next_if_exists(page, timeout=3000):
                        print(f"  -> [{store_name}] は満席（または選択不可）のため次へ進めませんでした。スキップします。")
                        continue

                    time.sleep(2)

                    is_navigated = page.get_by_text("ご予約の日時", exact=False).is_visible(timeout=2500)

                    if is_navigated:
                        print("  -> 画面遷移成功（予約枠あり）！空きスロットをスキャンします...")
                        slots = scan_store_slots(page)
                        
                        if slots:
                            all_store_availability[store_name] = slots
                            
                            for day_num, times in slots.items():
                                if is_weekend_or_holiday(day_num):
                                    for t_str in times:
                                        hour = int(t_str.split(":")[0])
                                        if TARGET_START_HOUR <= hour <= TARGET_END_HOUR:
                                            candidate = {"store_name": store_name, "keyword": keyword, "day": day_num, "time": t_str}
                                            if not best_target:
                                                best_target = candidate
                                            else:
                                                is_earlier_day = candidate["day"] < best_target["day"]
                                                is_same_day_earlier_time = (
                                                    candidate["day"] == best_target["day"] 
                                                    and candidate["time"] < best_target["time"]
                                                )
                                                if is_earlier_day or is_same_day_earlier_time:
                                                    best_target = candidate
                    else:
                        print("  -> 遷移不可（満席）のため、次の店舗の確認へ移ります。")

                    # 【重要修正】「ご予約の日時」画面から左下の「← 戻る」ボタンを押して店舗一覧に戻る
                    click_back_button(page)
                    time.sleep(2)

                except Exception as e:
                    print(f"  -> [{store_name}] のチェック中にエラー発生: {e}")
                    # エラー時も安全に店舗一覧に戻る試行
                    try:
                        click_back_button(page)
                        time.sleep(2)
                    except:
                        pass

            if all_store_availability:
                report_lines = ["📋 **【カルティエ 空き状況通知レポート】**\n"]
                for s_name, slots in all_store_availability.items():
                    report_lines.append(f"🏪 **{s_name}**")
                    for day_num, times in slots.items():
                        day_type = "土日祝" if is_weekend_or_holiday(day_num) else "平日"
                        report_lines.append(f"　- 10月{day_num}日 ({day_type}): {', '.join(times)}")
                    report_lines.append("")
                
                send_discord_notification("\n".join(report_lines))
                print("空き状況をDiscordに通知しました。")
            else:
                print("ℹ️ 【定期チェック】対象8店舗に空き枠はありませんでした。")

            if best_target:
                print(f"\n🎉 土日祝の条件合致！最速の枠で自動予約を実行します: {best_target['store_name']} 10月{best_target['day']}日 {best_target['time']}")
                
                navigate_to_store_selection(page)
                
                store_text_el = page.get_by_text(best_target["keyword"], exact=False).first
                try:
                    parent = store_text_el.locator("xpath=ancestor::*[contains(@class, 'store') or contains(@class, 'card') or contains(@class, 'item') or contains(@class, 'box') or self::button or self::a or self::label][1]")
                    img_el = parent.locator("img").first
                    if img_el.is_visible(timeout=1500):
                        img_el.click(force=True)
                    else:
                        parent.click(force=True)
                except:
                    store_text_el.click(force=True)

                time.sleep(1)
                click_next_if_exists(page)
                time.sleep(2)

                success = execute_booking(page, best_target["keyword"], best_target["day"], best_target["time"])
                if success:
                    print("自動予約が完了しました。")
                    sys.exit(0)

        except Exception as e:
            print(f"実行中にエラーが発生しました: {e}")
            sys.exit(1)
        finally:
            browser.close()

if __name__ == "__main__":
    main()
