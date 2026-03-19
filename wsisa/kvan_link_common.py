# -*- coding: utf-8 -*-
"""
K-VAN 결제링크 DB 공통: URL에서 KEY 추출, 금액 파싱, 링크 최초 생성 시 kvan_links 시드.
auto_kvan.py / kvan_crawler.py / web_form.py 가 동일 규칙을 쓰도록 분리.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse

import pymysql
from pymysql.cursors import DictCursor

DB_HOST = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST") or "localhost"
DB_PORT = int(os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT") or "3306")
DB_USER = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER") or "root"
DB_PASSWORD = os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD") or ""
DB_NAME = (
    os.environ.get("MYSQL_DATABASE")
    or os.environ.get("MYSQLDATABASE")
    or os.environ.get("MYSQL_DB")
    or "railway"
)
DB_CONNECT_TIMEOUT = int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "5"))
DB_READ_TIMEOUT = int(os.environ.get("MYSQL_READ_TIMEOUT", "10"))
DB_WRITE_TIMEOUT = int(os.environ.get("MYSQL_WRITE_TIMEOUT", "10"))


def kvan_db_connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=DB_CONNECT_TIMEOUT,
        read_timeout=DB_READ_TIMEOUT,
        write_timeout=DB_WRITE_TIMEOUT,
    )


def extract_kvan_session_key_from_url(link: str) -> str:
    """K-VAN 결제 URL에서 KEY… 세션 토큰 추출."""
    u = (link or "").strip()
    if not u:
        return ""
    try:
        q = parse_qs(urlparse(u).query)
        for key in ("sessionId", "sessionid"):
            for v in q.get(key) or []:
                vv = (v or "").strip()
                if vv.startswith("KEY"):
                    return vv
    except Exception:
        pass
    m = re.search(r"/p/(KEY[0-9A-Za-z]+)", u, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"(KEY[0-9A-Za-z]+)", u)
    return m.group(1) if m else ""


def parse_amount_won(text: str) -> int:
    """
    '결제금액 1,234,567원' 등에서 금액 추출.
    비정상적으로 큰 값(파싱 오류)은 제외하고 합리적 후보만 사용.
    """
    raw = text or ""
    # 라벨 뒤 금액 우선
    for pat in (
        r"결제\s*금액\s*[:：]?\s*([\d,，\s]+)\s*원",
        r"판매\s*가격\s*[:：]?\s*([\d,，\s]+)\s*원",
        r"금액\s*[:：]?\s*([\d,，\s]+)\s*원",
    ):
        m = re.search(pat, raw)
        if m:
            try:
                v = int(re.sub(r"[^\d]", "", m.group(1)))
                if 0 < v <= 1_000_000_000:
                    return v
            except ValueError:
                continue
    candidates: list[int] = []
    for m in re.finditer(r"([\d,，\s]{1,18})\s*원", raw):
        try:
            v = int(re.sub(r"[^\d]", "", m.group(1)))
            if 0 < v <= 1_000_000_000:
                candidates.append(v)
        except ValueError:
            continue
    if not candidates:
        stripped = raw.replace("원", "").replace(",", "").replace("，", "").strip()
        try:
            v = int(re.sub(r"[^\d]", "", stripped))
            return v if 0 < v <= 1_000_000_000 else 0
        except ValueError:
            return 0
    # 여러 개면 '결제 금액'에 가까운 보통가(너무 큰 단일 오탐 제거)
    reasonable = [c for c in candidates if c <= 100_000_000]
    return max(reasonable) if reasonable else max(candidates)


def fetch_agency_company_name(agency_id: str) -> str:
    """agencies.company_name 조회. 없거나 본사면 본사."""
    aid = (agency_id or "").strip()
    if not aid:
        return "본사"
    try:
        conn = kvan_db_connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT company_name FROM agencies WHERE id = %s LIMIT 1",
                (aid,),
            )
            row = cur.fetchone()
        conn.close()
        if row and (row.get("company_name") or "").strip():
            return str(row["company_name"]).strip()
    except Exception:
        pass
    return aid


def ensure_kvan_links_internal_session_column(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'kvan_links'
                  AND COLUMN_NAME = 'internal_session_id'
                """
            )
            if not (cur.fetchall() or []):
                cur.execute(
                    "ALTER TABLE kvan_links ADD COLUMN internal_session_id VARCHAR(64) DEFAULT ''"
                )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def upsert_kvan_link_creation_seed(
    link: str,
    internal_session_id: str,
    session_blob: dict | None,
    *,
    skip_db: bool = False,
) -> None:
    """
    매크로가 링크를 만든 직후: KEY·내부 세션·agency_id·금액을 kvan_links 에 시드.
    이후 크롤러가 동일 kvan_link 로 스크랩하면 agency_id / internal_session_id 를 병합 유지한다.
    """
    if skip_db:
        return
    link = (link or "").strip()
    internal_session_id = (internal_session_id or "").strip()
    session_blob = session_blob or {}
    if not link or "store.k-van.app" not in link:
        return
    kkey = extract_kvan_session_key_from_url(link)
    if not kkey:
        return
    agency_id = str(session_blob.get("agency_id") or "").strip()
    owner = fetch_agency_company_name(agency_id)
    amt_raw = str(session_blob.get("amount") or "").strip()
    amount = parse_amount_won(amt_raw + ("원" if amt_raw and "원" not in amt_raw else ""))
    if amount <= 0:
        try:
            amount = int(re.sub(r"[^\d]", "", amt_raw) or "0")
        except ValueError:
            amount = 0
    title = f"{owner} · 내부세션 {internal_session_id}"
    conn = None
    try:
        conn = kvan_db_connect()
        ensure_kvan_links_internal_session_column(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, agency_id, internal_session_id FROM kvan_links WHERE kvan_link = %s LIMIT 1",
                (link,),
            )
            prev = cur.fetchone()
            if prev:
                cur.execute(
                    """
                    UPDATE kvan_links SET
                      kvan_session_id = %s,
                      agency_id = COALESCE(NULLIF(TRIM(%s), ''), agency_id),
                      internal_session_id = COALESCE(NULLIF(TRIM(%s), ''), internal_session_id),
                      title = CASE
                        WHEN title IS NULL OR TRIM(title) = '' THEN %s
                        ELSE title END,
                      amount = CASE WHEN amount IS NULL OR amount = 0 THEN %s ELSE amount END,
                      raw_text = %s
                    WHERE kvan_link = %s
                    """,
                    (
                        kkey,
                        agency_id,
                        internal_session_id,
                        title,
                        amount,
                        f"seed:internal={internal_session_id}",
                        link,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO kvan_links (
                      captured_at, title, amount, ttl_label, status,
                      kvan_link, mid, kvan_session_id, agency_id, internal_session_id, raw_text
                    )
                    VALUES (NOW(), %s, %s, '', %s, %s, '', %s, %s, %s, %s)
                    """,
                    (
                        title,
                        amount,
                        "링크생성됨",
                        link,
                        kkey,
                        agency_id,
                        internal_session_id,
                        f"seed:internal={internal_session_id}",
                    ),
                )
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def load_kvan_link_preserved_by_url(urls: list[str]) -> dict[str, dict]:
    """병합용: 기존 행의 agency_id, internal_session_id, title(선택)."""
    out: dict[str, dict] = {}
    urls = [u.strip() for u in urls if (u or "").strip()]
    if not urls:
        return out
    try:
        conn = kvan_db_connect()
        ensure_kvan_links_internal_session_column(conn)
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(urls))
            cur.execute(
                f"""
                SELECT kvan_link, agency_id, internal_session_id, title
                FROM kvan_links WHERE kvan_link IN ({ph})
                """,
                tuple(urls),
            )
            for row in cur.fetchall() or []:
                k = (row.get("kvan_link") or "").strip()
                if k:
                    out[k] = dict(row)
        conn.close()
    except Exception:
        pass
    return out
