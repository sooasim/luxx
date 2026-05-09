"""upaynara.com (U-PAY) 통합 HTTP 클라이언트.

기존 U-PAY(store.k-van.app) 자동화는 무거운 Selenium 매크로를 썼지만,
U-PAY 사이트는 일반적인 PHP/HTML 페이지이므로 requests 만으로
- 로그인 (`/member/login_chk.php`)
- 결제내역 조회 (`/app/my_charge_history.php`)
가 가능하다.

PAY 결제 폼(`/app/pay_charge.php`)은 사이트 운영시간(09:00~22:30 KST)
밖에는 alert + history.back() 으로 막혀 있어 폼 DOM 을 수집할 수 없다.
운영시간 내에 fill_pay_charge_form() 을 호출하면 폼 매크로를 동작시킬 수
있도록 자리만 마련해 두었다 (TODO 표시).

환경변수:
    UPAY_ID       (기본: K_VAN_ID 와 동일하게 fallback)
    UPAY_PW       (기본: K_VAN_PW 와 동일하게 fallback)
    UPAY_BASE_URL (기본: https://upaynara.com)
"""

from __future__ import annotations

import os
import re
import time
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UPAY_BASE_URL = os.environ.get("UPAY_BASE_URL", "https://upaynara.com").rstrip("/")
UPAY_LOGIN_URL = f"{UPAY_BASE_URL}/member/login.php"
UPAY_LOGIN_POST_URL = f"{UPAY_BASE_URL}/member/login_chk.php"
UPAY_APP_URL = f"{UPAY_BASE_URL}/app/"
UPAY_PAY_CHARGE_URL = f"{UPAY_BASE_URL}/app/pay_charge.php"
UPAY_HISTORY_URL = f"{UPAY_BASE_URL}/app/my_charge_history.php"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _env(name: str, fallback_names: List[str] = (), default: str = "") -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    for fb in fallback_names:
        v = os.environ.get(fb, "").strip()
        if v:
            return v
    return default


def get_credentials() -> tuple[str, str]:
    """U-PAY 로그인 ID/PW 를 환경변수에서 읽는다.

    UPAY_ID/UPAY_PW 가 1순위, 없으면 기존 K_VAN_ID/K_VAN_PW 를 fallback.
    """
    uid = _env("UPAY_ID", ["K_VAN_ID"], "")
    upw = _env("UPAY_PW", ["K_VAN_PW"], "")
    return uid, upw


@dataclass
class UpayHistoryRow:
    raw_date: str = ""        # 날짜
    approval_no: str = ""     # 승인번호
    card_company: str = ""    # 카드사
    card_number: str = ""     # 카드번호
    holder_name: str = ""     # 카드주
    holder_phone: str = ""    # 카드주 연락처
    amount_paid: str = ""     # 결제금액
    fee: str = ""             # 수수료
    amount_settled: str = ""  # 지급금액

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UpayClient:
    """upaynara.com 세션 클라이언트.

    Selenium 없이 requests.Session 으로 로그인/페이지 fetch 만 처리.
    HTML 파싱은 BeautifulSoup 사용.
    """

    def __init__(self, login_id: str = "", login_pw: str = "", timeout: int = 15) -> None:
        self.login_id = login_id or get_credentials()[0]
        self.login_pw = login_pw or get_credentials()[1]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_UA,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        self._logged_in = False

    # ------------------------------------------------------------------ login
    def login(self, force: bool = False) -> bool:
        """U-PAY 로그인. 성공 시 True."""
        if self._logged_in and not force:
            return True
        if not (self.login_id and self.login_pw):
            logger.warning("UpayClient.login: 자격 증명이 없습니다 (UPAY_ID/PW 미설정)")
            return False

        # 1) 로그인 페이지 GET (쿠키 셋업)
        try:
            self.session.get(UPAY_LOGIN_URL, timeout=self.timeout)
        except requests.RequestException as e:
            logger.warning("UPAY 로그인 페이지 GET 실패: %s", e)
            return False

        # 2) 로그인 폼 submit
        try:
            resp = self.session.post(
                UPAY_LOGIN_POST_URL,
                data={"mb_id": self.login_id, "mb_password": self.login_pw},
                headers={"Referer": UPAY_LOGIN_URL},
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as e:
            logger.warning("UPAY 로그인 POST 실패: %s", e)
            return False

        # 3) 응답 검사: 성공 시 {"link":"https://upaynara.com..."} JSON 반환
        text = (resp.text or "").strip()
        ok = False
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("link"):
                ok = True
        except (json.JSONDecodeError, ValueError):
            # 일부 환경에서는 직접 리다이렉트만 일어날 수 있음 → 세션 쿠키 확인
            if "mysession" in self.session.cookies.get_dict():
                ok = True

        if not ok:
            logger.warning("UPAY 로그인 실패. 응답 일부: %s", text[:200])
            return False

        # 4) 메인 GET 으로 세션 활성화 보장
        try:
            self.session.get(UPAY_APP_URL, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException:
            pass

        self._logged_in = True
        logger.info("UPAY 로그인 성공: %s", self.login_id)
        return True

    def ensure_login(self) -> bool:
        return self.login(force=False)

    # ------------------------------------------------- pay charge (영식 매크로)
    def fill_pay_charge_form(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """PAY 결제(`pay_charge.php`) 폼 매크로.

        ⚠️ 운영시간(09:00~22:30 KST) 외에는 사이트가 alert("결제 불가능") 후
        history.back() 으로 폼을 막아두므로 이 함수는 그 외 시간에는 빈 폼 반환.

        실제 폼 필드 셀렉터/POST 엔드포인트는 운영시간 내 DOM 분석이 필요하다.
        분석 후 아래 TODO 부분을 채우면 된다.

        payload 예시:
            {
              "amount": 100000,
              "card_no": "4111-1111-1111-1111",
              "holder_name": "홍길동",
              "holder_phone": "01012345678",
              "expiry": "12/30",
              "cvc": "123",
              "card_pw2": "12",
              "ssn_birth": "900101",
              "product_name": "결제 상품",
            }
        """
        if not self.ensure_login():
            return {"ok": False, "reason": "login_failed"}

        try:
            r = self.session.get(UPAY_PAY_CHARGE_URL, timeout=self.timeout)
        except requests.RequestException as e:
            return {"ok": False, "reason": f"fetch_failed: {e}"}

        html = r.text or ""
        # 운영시간 외 점검 메시지 감지
        if "오후 10시30분부터" in html or "결제 불가능합니다" in html:
            return {
                "ok": False,
                "reason": "pay_charge_closed",
                "message": "U-PAY 결제는 09:00 ~ 22:30 KST 만 가능합니다 (사이트 정책).",
            }

        # TODO(운영시간 내 DOM 확보 후 폼 필드 매핑):
        # 1) BeautifulSoup 으로 폼 추출
        # 2) name 별로 payload 값 매핑
        # 3) self.session.post(<form_action>, data=...) 후 응답 검증
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        action = (form.get("action") if form else None) or UPAY_PAY_CHARGE_URL
        action_url = urljoin(UPAY_PAY_CHARGE_URL, action)
        # 폼 필드를 추출 (운영시간 내 전체 DOM 확보 후 정확한 매핑 필요)
        fields = {}
        if form:
            for inp in form.find_all(["input", "select", "textarea"]):
                name = inp.get("name")
                if not name:
                    continue
                fields[name] = inp.get("value", "")
        return {
            "ok": True,
            "action_url": action_url,
            "form_fields": fields,
            "payload_seen": payload,
            "note": (
                "폼 필드 매핑이 아직 미정. 운영시간 내 pay_charge.php 의 실제 폼 DOM 을 "
                "수집한 뒤, 카드/금액/카드주 등 각 필드 name 을 payload 키와 매핑하라."
            ),
        }

    # ------------------------------------------------------ history scraping
    def fetch_history(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_pages: int = 5,
    ) -> List[UpayHistoryRow]:
        """`my_charge_history.php` 결제내역 테이블 크롤링.

        start_date/end_date 는 'YYYY-MM-DD' 형식. 기본은 최근 30일.
        """
        if not self.ensure_login():
            return []
        if not end_date:
            end_date = date.today().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

        rows: List[UpayHistoryRow] = []
        page = 1
        seen_keys: set[tuple[str, str]] = set()
        while page <= max_pages:
            params = {
                "sStartDate": start_date,
                "sEndDate": end_date,
                "page": page,
            }
            try:
                r = self.session.get(UPAY_HISTORY_URL, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                logger.warning("UPAY history fetch 실패 (page=%s): %s", page, e)
                break

            page_rows = parse_history_html(r.text or "")
            if not page_rows:
                break
            new = 0
            for row in page_rows:
                key = (row.raw_date, row.approval_no)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append(row)
                new += 1
            if new == 0:
                break
            page += 1
            time.sleep(0.2)

        logger.info(
            "UPAY history: %d rows (range=%s ~ %s)", len(rows), start_date, end_date
        )
        return rows


def parse_history_html(html: str) -> List[UpayHistoryRow]:
    """결제내역 페이지 HTML 에서 거래 row 추출.

    컬럼 순서: 날짜 | 승인번호 | 카드사 | 카드번호 | 카드주 | 카드주 연락처 |
              결제금액 | 수수료 | 지급금액
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows: List[UpayHistoryRow] = []
    table = soup.find(class_="table_history")
    if table:
        tbody = table.find("tbody")
    else:
        # fallback: page 내 첫 table 의 tbody
        first_table = soup.find("table")
        tbody = first_table.find("tbody") if first_table else None
    if not tbody:
        return rows
    for tr in tbody.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not tds or all(not c for c in tds):
            continue
        # 일부 행은 컬럼 수가 다를 수 있어 안전하게 채운다
        cells = tds + [""] * (9 - len(tds))
        row = UpayHistoryRow(
            raw_date=cells[0],
            approval_no=cells[1],
            card_company=cells[2],
            card_number=cells[3],
            holder_name=cells[4],
            holder_phone=cells[5],
            amount_paid=cells[6],
            fee=cells[7],
            amount_settled=cells[8],
        )
        rows.append(row)
    return rows


def parse_amount(s: str) -> int:
    """'12,345원' → 12345. 빈 값은 0."""
    if not s:
        return 0
    digits = re.sub(r"[^0-9-]", "", s)
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


# ---------------------------------------------------------- module-level helper
def quick_login_test(login_id: str = "", login_pw: str = "") -> Dict[str, Any]:
    """수동 점검용. UPAY 로그인 + 결제내역 1페이지 fetch 결과를 dict 로 반환."""
    cli = UpayClient(login_id=login_id, login_pw=login_pw)
    ok = cli.login()
    if not ok:
        return {"ok": False, "reason": "login_failed"}
    rows = cli.fetch_history(max_pages=1)
    return {
        "ok": True,
        "rows": [r.to_dict() for r in rows],
        "row_count": len(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    out = quick_login_test()
    print(json.dumps(out, ensure_ascii=False, indent=2))
