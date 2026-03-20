from __future__ import annotations

from dataclasses import dataclass

import kvan_crawler as kc


@dataclass
class _FakeState:
    tx_exists: bool = False
    insert_params: tuple | None = None
    update_params: tuple | None = None
    agencies: list[dict] | None = None
    kvan_link_rows: list[dict] | None = None


class _FakeCursor:
    def __init__(self, st: _FakeState):
        self._st = st
        self._last = None

    def execute(self, sql: str, params=None):
        q = " ".join((sql or "").split()).lower()
        self._last = None

        if "select id from agencies" in q:
            self._last = self._st.agencies or []
            return

        if "select agency_id from kvan_links" in q:
            self._last = self._st.kvan_link_rows or []
            return

        if "select id from transactions where kvan_approval_no" in q:
            self._last = [{"id": "TX-EXIST"}] if self._st.tx_exists else None
            return

        if "insert into transactions" in q:
            self._st.insert_params = params
            self._last = None
            return

        if "update transactions set amount" in q:
            self._st.update_params = params
            self._last = None
            return

        if "select id from transactions" in q and "where kvan_approval_no" in q:
            self._last = [{"id": "TX-EXIST"}] if self._st.tx_exists else None
            return

    def fetchall(self):
        return self._last or []

    def fetchone(self):
        if self._last is None:
            return None
        if isinstance(self._last, list):
            return self._last[0] if self._last else None
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, st: _FakeState):
        self._st = st
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._st)

    def commit(self):
        return None

    def close(self):
        self.closed = True


def scenario_popup_upsert_maps_agency_from_kvan_links() -> bool:
    st = _FakeState(
        tx_exists=False,
        agencies=[{"id": "AGY001"}],
        kvan_link_rows=[{"agency_id": "AGY001"}],
    )
    orig_get_db = kc.get_db
    orig_get_aid = kc._get_agency_id_for_session
    orig_notify = kc.append_payment_notification
    try:
        kc.get_db = lambda: _FakeConn(st)
        kc._get_agency_id_for_session = lambda _: ""
        kc.append_payment_notification = lambda *args, **kwargs: None
        kv = kc.KVStore()
        kv.use_json = False
        kv.upsert_popup_transaction(
            session_id="KEYABC123",
            amount=20000,
            approval_no="APR-001",
            card_number="485480******9341",
            registered_at="2026-03-20 14:32:15",
            customer_name="",
        )
        # INSERT 파라미터: (new_tx_id, safe_agency_id, amount, customer_name, prefix4, message, approval_no, registered_at)
        return bool(st.insert_params and st.insert_params[1] == "AGY001")
    finally:
        kc.get_db = orig_get_db
        kc._get_agency_id_for_session = orig_get_aid
        kc.append_payment_notification = orig_notify


def scenario_popup_upsert_drops_invalid_agency_fk() -> bool:
    st = _FakeState(
        tx_exists=False,
        agencies=[{"id": "AGY001"}],  # AGY999 는 FK 유효하지 않음
        kvan_link_rows=[{"agency_id": "AGY999"}],
    )
    orig_get_db = kc.get_db
    orig_get_aid = kc._get_agency_id_for_session
    orig_notify = kc.append_payment_notification
    try:
        kc.get_db = lambda: _FakeConn(st)
        kc._get_agency_id_for_session = lambda _: ""
        kc.append_payment_notification = lambda *args, **kwargs: None
        kv = kc.KVStore()
        kv.use_json = False
        kv.upsert_popup_transaction(
            session_id="KEYXYZ999",
            amount=10000,
            approval_no="APR-002",
            card_number="467309******0000",
            registered_at="2026-03-20 15:01:11",
            customer_name="",
        )
        # 유효하지 않은 agency는 None(NULL)이어야 FK 오류를 피한다.
        return bool(st.insert_params and st.insert_params[1] is None)
    finally:
        kc.get_db = orig_get_db
        kc._get_agency_id_for_session = orig_get_aid
        kc.append_payment_notification = orig_notify


def scenario_session_normalization_key_to_internal() -> bool:
    orig_lookup = kc._lookup_internal_session_id_for_kvan_key
    try:
        kc._lookup_internal_session_id_for_kvan_key = lambda key: "SID-INT-001" if key == "KEYABC123" else ""
        sid, kkey = kc._normalize_session_id_for_admin_state("https://store.k-van.app/p/KEYABC123?sessionId=KEYABC123&type=KEYED")
        return sid == "SID-INT-001" and kkey == "KEYABC123"
    finally:
        kc._lookup_internal_session_id_for_kvan_key = orig_lookup


def run() -> int:
    cases = [
        ("popup_upsert_maps_agency_from_kvan_links", scenario_popup_upsert_maps_agency_from_kvan_links),
        ("popup_upsert_drops_invalid_agency_fk", scenario_popup_upsert_drops_invalid_agency_fk),
        ("session_normalization_key_to_internal", scenario_session_normalization_key_to_internal),
    ]
    passed = 0
    for name, fn in cases:
        ok = False
        try:
            ok = bool(fn())
        except Exception as e:
            print(f"[FAIL] {name} exception={e}")
            ok = False
        if ok:
            passed += 1
            print(f"[PASS] {name}")
        else:
            print(f"[FAIL] {name}")
    print(f"pass={passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(run())

