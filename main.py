def handle_verification_code(page):
    print("\n==================================================")
    print("【自動化処理】メールからの認証コード受信待機中...")
    
    code = fetch_verification_code_from_gmail(timeout_sec=60)
    if not code:
        print("  -> 認証コードの取得に失敗しました。")
        return False
    
    time.sleep(1.5)  # 画面と入力欄の安定待ち
    
    inputs = page.locator("input:visible").all()
    print(f"  -> 検出されたコード入力欄の数: {len(inputs)}")
    
    if len(inputs) == 0:
        print("  -> 【エラー】認証コードの入力欄が見つかりません。")
        page.screenshot(path="error_no_otp_input.png")
        return False

    # --- 1. タップ(click)・フォーカスを行ってから入力 ---
    try:
        if len(inputs) == len(code):
            # 1桁ずつ別々のボックスに分かれているタイプ
            for i, digit in enumerate(code):
                inp = inputs[i]
                inp.click()
                inp.focus()
                inp.fill(digit)
                inp.dispatch_event("input")
                inp.dispatch_event("change")
                time.sleep(0.1)
        else:
            # 1つの入力欄にまとめて入力するタイプ
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

    time.sleep(1.5)  # ボタンが活性化するのを待つ

    # --- 2. 認証実行ボタンの特定とクリック ---
    confirm_keywords = ["認証", "確認", "送信", "予約確定", "完了", "予約を確定"]
    clicked = False
    
    for kw in confirm_keywords:
        try:
            # ボタン要素かつ画面上に表示・有効化されているものを検索
            btn = page.locator("button, [role='button'], input[type='submit']").filter(has_text=kw).first
            if btn.is_visible(timeout=1000):
                btn.scroll_into_view_if_needed()
                btn.click()
                print(f"  -> 【認証実行】「{kw}」ボタンをクリックしました。")
                clicked = True
                break
        except Exception:
            continue
            
    if not clicked:
        try:
            btn = page.locator("button:visible, input[type='submit']:visible").first
            btn.click(force=True)
            print("  -> 【認証実行】可視ボタンを強制クリックしました。")
        except Exception as e:
            print(f"  -> 【エラー】送信ボタンのクリックに失敗しました: {e}")
            page.screenshot(path="error_otp_submit_btn.png")
            return False

    # --- 3. 【重要】実際の予約完了画面の表示チェック ---
    print("  -> 予約完了画面への遷移を確認中...")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    
    for _ in range(12):
        body_text = page.inner_text("body")
        
        # 完了メッセージの確認
        if any(kw in body_text for kw in ["予約が完了", "ご予約ありがとうございます", "完了いたしました", "受付いたしました"]):
            print("  -> 【確定成功】予約完了画面の表示を確認しました！")
            return True
            
        # コード間違い等のエラーメッセージ検知
        if any(err in body_text for err in ["無効", "正しくありません", "期限切れ", "エラー"]):
            print(f"  -> 【エラー】認証コードまたは送信処理でエラーが発生しました。")
            page.screenshot(path="error_otp_failed.png")
            return False
            
        time.sleep(1)

    print(f"  -> 【失敗】予約完了画面への到達を確認できませんでした。（URL: {page.url}）")
    page.screenshot(path="error_not_completed.png")
    return False

