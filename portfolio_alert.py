"""
==========================================================
  퀀트 포트폴리오 급락 알림 시스템
  - 전략 ① 글로벌 멀티팩터 ETF (MTUM, QUAL, VTV)
  - 전략 ② 채권 + 대안자산 (SHY, TLT, GLD, VNQ)
==========================================================
  실행 방법:
    pip install yfinance schedule requests
    python portfolio_alert.py

  자동 스케줄링 (매일 장 마감 후 체크):
    python portfolio_alert.py --schedule
==========================================================
"""

import yfinance as yf
import schedule
import time
import json
import os
import argparse
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─────────────────────────────────────────
# ① 포트폴리오 설정
# ─────────────────────────────────────────
PORTFOLIO = {
    "전략1_글로벌ETF": {
        "MTUM": {"이름": "iShares Momentum Factor", "배분": 1.33, "통화": "USD"},
        "QUAL": {"이름": "iShares Quality Factor",  "배분": 1.33, "통화": "USD"},
        "VTV":  {"이름": "Vanguard Value ETF",       "배분": 1.34, "통화": "USD"},
    },
    "전략2_채권대안": {
        "SHY":  {"이름": "iShares 단기국채",          "배분": 0.75, "통화": "USD"},
        "TLT":  {"이름": "iShares 장기국채",          "배분": 0.75, "통화": "USD"},
        "GLD":  {"이름": "SPDR Gold Shares",         "배분": 0.50, "통화": "USD"},
        "VNQ":  {"이름": "Vanguard Real Estate ETF", "배분": 0.50, "통화": "USD"},
    },
}

# ─────────────────────────────────────────
# ② 알림 임계값 설정
# ─────────────────────────────────────────
ALERT_THRESHOLDS = {
    "일간_경고":    -3.0,   # 하루 -3% → 경고
    "일간_위험":    -5.0,   # 하루 -5% → 위험 (즉시 알림)
    "주간_경고":    -7.0,   # 1주 -7% → 경고
    "주간_위험":   -10.0,   # 1주 -10% → 위험
    "월간_위험":   -15.0,   # 1달 -15% → 비상 (손절 검토)
    "고점대비_위험": -20.0, # 52주 고점 대비 -20% → 하락장 진입
}

# ─────────────────────────────────────────
# ③ 알림 채널 설정 (사용할 채널 True로 변경)
# ─────────────────────────────────────────
ALERT_CONFIG = {
    "콘솔출력":  True,   # 터미널 출력 (항상 켜두기)
    "이메일":    False,  # 이메일 알림 (아래 설정 필요)
    "텔레그램":  False,  # 텔레그램 봇 알림 (아래 설정 필요)
    "슬랙":      False,  # 슬랙 웹훅 알림 (아래 설정 필요)
}

# 이메일 설정 (Gmail 앱 비밀번호 사용)
EMAIL_CONFIG = {
    "발신자":    "your_email@gmail.com",
    "수신자":    "your_email@gmail.com",
    "비밀번호":  "your_app_password",   # Gmail → 앱 비밀번호 생성
    "smtp서버":  "smtp.gmail.com",
    "포트":      587,
}

# 텔레그램 봇 설정
TELEGRAM_CONFIG = {
    "bot_token": "YOUR_BOT_TOKEN",      # @BotFather 에서 발급
    "chat_id":   "YOUR_CHAT_ID",        # @userinfobot 에서 확인
}

# 슬랙 웹훅 설정
SLACK_CONFIG = {
    "webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
}

# ─────────────────────────────────────────
# ④ 가격 데이터 수집
# ─────────────────────────────────────────
def get_price_data(ticker: str) -> dict:
    """티커의 현재가 및 변동률 조회"""
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="3mo")

        if hist.empty:
            return {"오류": f"{ticker} 데이터 없음"}

        current   = hist["Close"].iloc[-1]
        prev_1d   = hist["Close"].iloc[-2]  if len(hist) >= 2  else current
        prev_1w   = hist["Close"].iloc[-6]  if len(hist) >= 6  else current
        prev_1m   = hist["Close"].iloc[-22] if len(hist) >= 22 else current
        high_52w  = hist["Close"].tail(252).max() if len(hist) >= 252 else hist["Close"].max()

        return {
            "현재가":       round(current, 2),
            "1일변동":      round((current - prev_1d) / prev_1d * 100, 2),
            "1주변동":      round((current - prev_1w) / prev_1w * 100, 2),
            "1달변동":      round((current - prev_1m) / prev_1m * 100, 2),
            "52주고점대비": round((current - high_52w) / high_52w * 100, 2),
            "52주고점":     round(high_52w, 2),
        }
    except Exception as e:
        return {"오류": str(e)}


# ─────────────────────────────────────────
# ⑤ 알림 레벨 판단
# ─────────────────────────────────────────
def evaluate_alerts(ticker: str, data: dict) -> list:
    """급락 여부 판단 후 알림 목록 반환"""
    if "오류" in data:
        return []

    alerts = []
    t = ALERT_THRESHOLDS

    checks = [
        ("일간", data["1일변동"],      t["일간_위험"],    t["일간_경고"]),
        ("주간", data["1주변동"],      t["주간_위험"],    t["주간_경고"]),
        ("월간", data["1달변동"],      t["월간_위험"],    None),
        ("52주고점대비", data["52주고점대비"], t["고점대비_위험"], None),
    ]

    for label, val, danger, warn in checks:
        if val <= danger:
            alerts.append({
                "레벨": "🚨 위험",
                "내용": f"{ticker} {label} {val:.2f}% (위험 임계값: {danger}%)"
            })
        elif warn and val <= warn:
            alerts.append({
                "레벨": "⚠️ 경고",
                "내용": f"{ticker} {label} {val:.2f}% (경고 임계값: {warn}%)"
            })

    return alerts


# ─────────────────────────────────────────
# ⑥ 알림 채널별 전송 함수
# ─────────────────────────────────────────
def send_console(report: str):
    print(report)


def send_email(subject: str, body: str):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_CONFIG["발신자"]
        msg["To"]      = EMAIL_CONFIG["수신자"]
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(EMAIL_CONFIG["smtp서버"], EMAIL_CONFIG["포트"]) as server:
            server.starttls()
            server.login(EMAIL_CONFIG["발신자"], EMAIL_CONFIG["비밀번호"])
            server.send_message(msg)
        print("✅ 이메일 전송 완료")
    except Exception as e:
        print(f"❌ 이메일 전송 실패: {e}")


def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_CONFIG['bot_token']}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CONFIG["chat_id"],
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 200:
            print("✅ 텔레그램 전송 완료")
        else:
            print(f"❌ 텔레그램 오류: {resp.text}")
    except Exception as e:
        print(f"❌ 텔레그램 전송 실패: {e}")


def send_slack(message: str):
    try:
        resp = requests.post(
            SLACK_CONFIG["webhook_url"],
            json={"text": message},
            timeout=10
        )
        if resp.status_code == 200:
            print("✅ 슬랙 전송 완료")
        else:
            print(f"❌ 슬랙 오류: {resp.text}")
    except Exception as e:
        print(f"❌ 슬랙 전송 실패: {e}")


# ─────────────────────────────────────────
# ⑦ 전체 모니터링 실행
# ─────────────────────────────────────────
def run_monitor():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator = "=" * 55

    console_lines = [
        "",
        separator,
        f"  📊 퀀트 포트폴리오 모니터링 | {now}",
        separator,
    ]

    all_alerts  = []
    alert_lines = []

    for strategy, tickers in PORTFOLIO.items():
        console_lines.append(f"\n  [{strategy}]")

        for ticker, info in tickers.items():
            data = get_price_data(ticker)

            if "오류" in data:
                console_lines.append(f"    {ticker}: 데이터 오류 - {data['오류']}")
                continue

            # 상태 이모지 결정
            d = data["1일변동"]
            status = "🟢" if d >= 0 else ("🟡" if d > -3 else ("🟠" if d > -5 else "🔴"))

            line = (
                f"    {status} {ticker:<5} "
                f"${data['현재가']:>8.2f}  "
                f"1d: {data['1일변동']:>+6.2f}%  "
                f"1w: {data['1주변동']:>+6.2f}%  "
                f"1m: {data['1달변동']:>+6.2f}%  "
                f"52wH: {data['52주고점대비']:>+6.2f}%"
            )
            console_lines.append(line)

            # 알림 평가
            alerts = evaluate_alerts(ticker, data)
            all_alerts.extend(alerts)
            for a in alerts:
                alert_lines.append(f"    {a['레벨']}: {a['내용']}")

    # 알림 섹션
    if all_alerts:
        console_lines.append(f"\n{separator}")
        console_lines.append("  🔔 발생한 알림:")
        console_lines.extend(alert_lines)
    else:
        console_lines.append(f"\n  ✅ 모든 자산 정상 범위 내")

    console_lines.append(separator)
    report = "\n".join(console_lines)

    # ── 콘솔 출력
    if ALERT_CONFIG["콘솔출력"]:
        send_console(report)

    # ── 알림이 있을 때만 외부 채널 발송
    if all_alerts:
        subject = f"🚨 포트폴리오 급락 경보 | {now}"

        if ALERT_CONFIG["이메일"]:
            send_email(subject, report)

        if ALERT_CONFIG["텔레그램"]:
            tg_msg = (
                f"<b>🚨 포트폴리오 급락 경보</b>\n"
                f"<code>{now}</code>\n\n"
                + "\n".join(f"• {a['레벨']}: {a['내용']}" for a in all_alerts)
            )
            send_telegram(tg_msg)

        if ALERT_CONFIG["슬랙"]:
            send_slack(report)

    return all_alerts


# ─────────────────────────────────────────
# ⑧ 스케줄 실행 (장 마감 후 자동 체크)
# ─────────────────────────────────────────
def run_scheduled():
    print("⏰ 자동 모니터링 시작 (Ctrl+C로 중지)")
    print("   - 평일 미국장 마감 후: 매일 07:00 (KST)")
    print("   - 장중 급락 체크: 매일 23:30, 01:30 (KST)\n")

    # 미국 장 마감 후 (한국시간 기준)
    schedule.every().monday.at("07:00").do(run_monitor)
    schedule.every().tuesday.at("07:00").do(run_monitor)
    schedule.every().wednesday.at("07:00").do(run_monitor)
    schedule.every().thursday.at("07:00").do(run_monitor)
    schedule.every().friday.at("07:00").do(run_monitor)

    # 장중 중간 체크
    schedule.every().day.at("23:30").do(run_monitor)
    schedule.every().day.at("01:30").do(run_monitor)

    # 즉시 한 번 실행
    run_monitor()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ─────────────────────────────────────────
# ⑨ 진입점
# ─────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="퀀트 포트폴리오 급락 모니터")
    parser.add_argument("--schedule", action="store_true", help="스케줄 모드 (자동 반복)")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        run_monitor()
