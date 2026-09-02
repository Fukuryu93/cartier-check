import email
import imaplib
import os
import re
import sys
import time
from playwright.sync_api import sync_playwright
import requests

# ----------------------------------------------------------------------
# GitHub Secrets から環境変数を安全に取得
# ----------------------------------------------------------------------
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
USER_NAME = os.environ.get("USER_NAME")
USER_PHONE = os.environ.get("USER_PHONE")
USER_EMAIL = os.environ.get("USER_EMAIL")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 必須項目のチェック（設定されていない場合は安全に停止）
required_secrets = {
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
    "USER_NAME": USER_NAME,
    "USER_PHONE": USER_PHONE,
    "USER_EMAIL": USER_EMAIL,
}

missing_secrets = [key for key, val in required_secrets.items() if not val]
if missing_secrets:
  print(
      f"【エラー】以下の GitHub Secrets が未設定です: {', '.join(missing_secrets)}"
  )
  print("リポジトリの Settings > Secrets and variables > Actions で登録してください。")
  sys.exit(1)

# 姓・名に分解（空白区切り想定）
name_parts = USER_NAME.strip().split()
LAST_NAME = name_parts[0] if len(name_parts) > 0 else ""
FIRST_NAME = name_parts[1] if len(name_parts) > 1 else ""

TARGET_URL = "https://mycartiervisit.jp/cartier/terms-or-services"

STORE_PRIORITY = [
    "カルティエ心斎橋ブティック",
    "カルティエ ブティック 大丸心斎橋店",
    "カルティエブティック髙島屋大阪店",
    "カルティエブティック阪急うめだ本店",
    "カルティエブティック近鉄あべのハルカス店",
    "カルティエブティック阪急うめだ本店１階",
]

USER_INFO = {
    "last_name": LAST_NAME,
    "first_name": FIRST_NAME,
    "phone": USER_PHONE,
    "email": USER_EMAIL,
    "first_visit": "初めて",
    "party_size": "パートナーと",
}

TARGET_DAY = 10
TARGET_TIME = "16:00"


def send_discord_notification(message):
  """Discord通知の送信"""
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


def click_next_if_exists(page):
  """「次へ」ボタンクリック"""
  try:
    next_btn = page.get_by_text("次へ").first
    if next_btn.is_visible(timeout=3000):
      next_btn.click()
      print("  -> 「次へ」をクリック")
      return True
  except Exception:
    pass
  return False


def fetch_verification_code_from_gmail(timeout_sec=60):
  """Gmailを受信監視し、カルティエからの最新メールから4桁コードを抽出"""
  print("  -> Gmailから認証コードメールを受信監視中...")
  start_time = time.time()

  while time.time() - start_time < timeout_sec:
    try:
      mail = imaplib.IMAP4_SSL("imap.gmail.com")
      mail.login(USER_EMAIL, GMAIL_APP_PASSWORD.replace(" ", ""))
      mail.select("inbox")

      status, messages = mail.search(
          None, '(FROM "noreply@mail.myboutique.pro")'
      )

      if status == "OK" and messages[0]:
        email_ids = messages[0].split()
        latest_id = email_ids[-1]

        status, msg_data = mail.fetch(latest_id, "(RFC822)")
        for response_part in msg_data:
          if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])

            body = ""
            if msg.is_multipart():
              for part in msg.walk():
                if part.get_content_type() in ["text/plain", "text/html"]:
                  try:
                    body += part.get_payload(decode=True).decode(
                        "utf-8", errors="ignore"
                    )
                  except Exception:
                    pass
            else:
              body = msg.get_payload(decode=True).decode(
                  "utf-8", errors="ignore"
              )

            match = re.search(r"認証コードは\s*(\d{4})\s*になります", body)
            if not match:
              match = re.search(r"\b(\d{4})\b", body)

            if match:
              code = match.group(1)
              mail.logout()
              print(f"  -> 【自動取得成功】認証コード: {code}")
              return code
      mail.logout()
    except Exception as e:
      print(f"    [受信監視ポーリング中...] {e}")

    time.sleep(3)

  print("  -> タイムアウト: 認証コードメールを受信できませんでした。")
  return None


def fill_customer_info(page):
  """お客様情報の自動入力"""
  print("  -> お客様情報を自動入力中...")
  time.sleep(1)

  inputs = page.locator("input").all()
  if len(inputs) >= 4:
    inputs[0].fill(USER_INFO["last_name"])
    inputs[1].fill(USER_INFO["first_name"])
    inputs[2].fill(USER_INFO["phone"])
    inputs[3].fill(USER_INFO["email"])
  else:
    page.locator(
        "input[name*='last'], input[placeholder*='姓']"
    ).first.fill(USER_INFO["last_name"])
    page.locator(
        "input[name*='first'], input[placeholder*='名']"
    ).first.fill(USER_INFO["first_name"])
    page.locator("input[type='tel']").first.fill(USER_INFO["phone"])
    page.locator("input[type='email']").first.fill(USER_INFO["email"])

  try:
    selects = page.locator("select").all()
    if len(selects) >= 2:
      selects[0].select_option(label=USER_INFO["first_visit"])
      selects[1].select_option(label=USER_INFO["party_size"])
    else:
      for target_text in [USER_INFO["first_visit"], USER_INFO["party_size"]]:
        opt = page.get_by_text(target_text, exact=True).first
        if opt.is_visible(timeout=1000):
          opt.click()
  except Exception as e:
    print(f"    [ドロップダウン選択スキップ]: {e}")


def handle_verification_code(page):
  """認証コード画面の完全自動処理」"""
  print("\n==================================================")
  print("【自動化処理】メールからの認証コード受信待機中...")

  code = fetch_verification_code_from_gmail(timeout_sec=60)
  if not code:
    return False

  inputs = page.locator("input").all()
  if len(inputs) == 4:
    for i in range(4):
      inputs[i].fill(code[i])
      time.sleep(0.2)
  elif len(inputs) >= 1:
    inputs[0].fill(code)

  time.sleep(1)

  final_confirm_btn = page.get_by_text("予約確定").first
  if final_confirm_btn.is_visible(timeout=3000):
    final_confirm_btn.click()
    print("  -> 【最終処理】「予約確定」ボタンを自動クリックしました！")
    time.sleep(5)
    return True

  return False


def complete_reservation(page, store_name, target_day=None, target_time=None):
  """日付・時間選択から完全自動予約完了まで"""
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
      if "cursor-pointer" in class_attr:
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
  target_date_el.click(force=True)
  time.sleep(1.5)

  time_trigger = page.get_by_text(time_pattern).first
  if time_trigger.is_visible(timeout=2000):
    time_trigger.click(force=True)
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
  chosen_time_el.click(force=True)
  time.sleep(1)

  click_next_if_exists(page)
  time.sleep(2)

  fill_customer_info(page)
  click_next_if_exists(page)
  time.sleep(2)

  print("  -> ご相談内容画面を通過中...")
  click_next_if_exists(page)
  time.sleep(2)

  print(
      "  -> 予約内容確認画面。認証コード送信のため「予約確定」をクリックします..."
  )
  confirm_btn = page.get_by_text("予約確定").first
  if confirm_btn.is_visible(timeout=3000):
    confirm_btn.click()
    time.sleep(3)

  if "confirmation-code" in page.url or page.get_by_text(
      "認証コード", exact=False
  ).is_visible(timeout=5000):
    success = handle_verification_code(page)
    if success:
      success_msg = (
          f"🎉 **【カルティエ来店予約 完全完了！】**\n"
          f"**店舗:** {store_name}\n"
          f"**日時:** 9月{selected_day_str} {chosen_time_str}\n"
          f"**お名前:** {USER_INFO['last_name']} {USER_INFO['first_name']} 様\n"
          f"※全自動予約が確定したため、監視プログラムを正常終了しました。"
      )
      print("\n" + success_msg + "\n")
      send_discord_notification(success_msg)
      return True

  return False


def main():
  print("監視実行を開始します...")

  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 390, "height": 844},
    )
    page = context.new_page()

    try:
      print("1/6 利用規約ページを開きます...")
      page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
      time.sleep(2)
      page.get_by_text("規約に同意する").first.click()
      time.sleep(2)

      print("2/6 ご予約の種類を選択...")
      page.get_by_text("ご来店").first.click()
      time.sleep(2)

      print("3/6 カテゴリーを選択...")
      cat_btn = page.get_by_text("婚約＆結婚指輪").first
      if cat_btn.is_visible(timeout=3000):
        cat_btn.click()
        time.sleep(1)
      click_next_if_exists(page)
      time.sleep(2)

      print("4/6 アイテムを選択...")
      item_btn = page.get_by_text("婚約指輪").first
      if item_btn.is_visible(timeout=3000):
        item_btn.click()
        time.sleep(3)

      print("5/6 コレクションを選ぶ...")
      solitaire_btn = page.get_by_text("ソリテール").first
      if solitaire_btn.is_visible(timeout=3000):
        solitaire_btn.click()
        time.sleep(1)

      love_btn = page.get_by_text("LOVE").first
      if love_btn.is_visible(timeout=3000):
        love_btn.click()
        time.sleep(1)

      click_next_if_exists(page)
      time.sleep(4)

      print("6/6 都道府県を選択 (大阪府)...")
      try:
        page.wait_for_load_state("networkidle", timeout=5000)
      except Exception:
        pass
      page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
      time.sleep(1)

      for trigger_text in [
          "都市を選ぶ",
          "エリアを選ぶ",
          "お店を選んでください",
          "都道府県",
          "エリア",
      ]:
        try:
          trigger_btn = page.get_by_text(trigger_text, exact=False).first
          if trigger_btn.is_visible(timeout=1500):
            trigger_btn.click()
            time.sleep(1)
            break
        except Exception:
          pass

      osaka_candidates = [
          page.get_by_text("大阪府", exact=False).first,
          page.locator("xpath=//*[contains(text(), '大阪府')]").first,
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

      print("店舗一覧を取得して順次判定します...\n")

      store_names = []
      candidates = page.locator("button, a, div, li").all()

      for cand in candidates:
        try:
          if not cand.is_visible():
            continue
          text = cand.inner_text().strip()
          if any(
              no_word in text
              for no_word in ["取り扱い", "ございません", "ご注意", "お知らせ"]
          ):
            continue

          if (
              len(text) < 200
              and ("ブティック" in text or "店" in text)
              and text.count("カルティエ") <= 1
          ):
            lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
            for l in lines:
              if (
                  "カルティエ" in l or "ブティック" in l or "店" in l
              ) and "予約は満席です" not in l:
                if l not in store_names:
                  store_names.append(l)
                break
        except Exception:
          continue

      def get_priority(name):
        for idx, prio in enumerate(STORE_PRIORITY):
          if prio in name or name in prio:
            return idx
        return 999

      store_names.sort(key=get_priority)

      print(f"チェック対象店舗数 (優先度順): {len(store_names)} 件")
      for s in store_names:
        print(f"  ・{s}")
      print()

      for store_name in store_names:
        print(f"[{store_name}] をチェック中...")
        try:
          store_btn = page.get_by_text(store_name, exact=False).first
          store_btn.scroll_into_view_if_needed(timeout=2000)
          store_btn.click(force=True)
          time.sleep(1)

          click_next_if_exists(page)
          time.sleep(2)

          is_navigated = page.get_by_text(
              "ご予約の日時", exact=False
          ).is_visible(timeout=2500)

          if is_navigated:
            print(
                "  -> 画面遷移成功（予約枠あり）！全自動予約処理を開始します..."
            )
            success = complete_reservation(
                page,
                store_name,
                target_day=TARGET_DAY,
                target_time=TARGET_TIME,
            )
            if success:
              print(
                  "  [二重予約防止] 全自動予約が完了したため、スクリプトを終了します。"
              )
              sys.exit(0)

            back_btn = page.get_by_text("戻る", exact=False).first
            if back_btn.is_visible(timeout=3000):
              back_btn.click()
              time.sleep(2)
            try:
              store_btn.click(force=True)
              time.sleep(1)
            except Exception:
              pass
          else:
            print(
                "  -> 遷移不可（満席）のため、そのまま次の店舗を確認します"
            )
            try:
              store_btn.click(force=True)
              time.sleep(0.5)
            except Exception:
              pass

        except Exception as e:
          print(f"  -> 確認エラー: {e}")

    except Exception as e:
      print(f"実行中にエラーが発生しました: {e}")
      sys.exit(1)
    finally:
      browser.close()


if __name__ == "__main__":
  main()
