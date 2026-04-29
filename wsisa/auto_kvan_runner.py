"""
auto_kvan_runner.py - K-VAN 링크 생성 직렬 큐 실행기

K-VAN 은 동일 계정으로 동시 로그인이 불가하다.
web_form.py 의 trigger_auto_kvan_async() 가 이 스크립트를 단 한 번만 띄우고,
이 스크립트가 큐를 소진할 때까지 순서대로 auto_kvan.main() 을 호출한다.

실패 시 admin_state.json 의 해당 세션 status 를 '링크생성실패' 로 기록하여
어드민 화면에서 "다시 요청" 버튼을 표시한다.

사용법:
    python auto_kvan_runner.py <queue_file> <lock_file>
"""
from __future__ import annotations

import json
import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

_SELF_DIR = Path(__file__).resolve().parent
_AUTO_KVAN = _SELF_DIR / "auto_kvan.py"
_BASE_DIR = _SELF_DIR.parent
_DATA_DIR = Path(os.environ.get("LUXX_DATA_DIR", "").strip() or str(_BASE_DIR / "data"))
_LOG_PATH = _DATA_DIR / "hq_logs.log"


def _log(msg: str) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().isoformat()} [RUNNER] {msg}\n")
    except Exception:
        pass
    print(f"[RUNNER] {msg}")


def _read_queue(queue_path: Path) -> list:
    try:
        if not queue_path.exists():
            return []
        return json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_queue(queue_path: Path, queue: list) -> None:
    try:
        queue_path.write_text(json.dumps(queue), encoding="utf-8")
    except Exception as e:
        _log(f"[WARN] 큐 파일 쓰기 실패: {e}")


def _pop_session(queue_path: Path):
    queue = _read_queue(queue_path)
    if not queue:
        return None
    sid = queue.pop(0)
    _write_queue(queue_path, queue)
    return sid


def _admin_state_path() -> Path | None:
    """admin_state.json 실제 경로 탐색."""
    candidates = [
        _DATA_DIR / "admin_state.json",
        _BASE_DIR / "data" / "admin_state.json",
        Path("/app/data/admin_state.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _mark_session_failed(session_id: str, reason: str) -> None:
    """admin_state.json 에 링크생성실패 상태를 기록한다."""
    if not session_id:
        return
    try:
        st_path = _admin_state_path()
        if st_path is None or not st_path.exists():
            return
        state = json.loads(st_path.read_text(encoding="utf-8"))
        sessions = state.get("sessions") or []
        updated = False
        for s in sessions:
            if str(s.get("id")) == str(session_id):
                s["status"] = "링크생성실패"
                s["error_reason"] = reason
                s["failed_at"] = datetime.utcnow().isoformat()
                updated = True
                break
        if updated:
            state["sessions"] = sessions
            st_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _log(f"세션 실패 상태 기록 session_id={session_id} reason={reason[:80]}")
    except Exception as e:
        _log(f"[WARN] 세션 실패 상태 기록 오류: {e}")


def _session_has_link(session_id: str) -> bool:
    """세션에 kvan_link 가 이미 저장되었는지 확인한다."""
    try:
        st_path = _admin_state_path()
        if st_path is None or not st_path.exists():
            return False
        state = json.loads(st_path.read_text(encoding="utf-8"))
        for s in state.get("sessions") or []:
            if str(s.get("id")) == str(session_id):
                return bool(s.get("kvan_link"))
        return False
    except Exception:
        return False


def main() -> None:
    if len(sys.argv) < 3:
        print("사용법: auto_kvan_runner.py <queue_file> <lock_file>")
        sys.exit(1)

    queue_path = Path(sys.argv[1])
    lock_path = Path(sys.argv[2])

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        _log(f"[ERROR] 락 파일 생성 실패: {e}")
        sys.exit(1)

    _log(f"runner 시작 pid={os.getpid()}")

    try:
        while True:
            sid = _pop_session(queue_path)
            if sid is None:
                time.sleep(5)
                sid = _pop_session(queue_path)
                if sid is None:
                    _log("큐 비어있음 – runner 종료")
                    break

            _log(f"세션 처리 시작 session_id={sid}")
            success = False
            fail_reason = ""

            try:
                result = subprocess.run(
                    [sys.executable, str(_AUTO_KVAN), sid],
                    timeout=600,
                    capture_output=False,
                )
                if result.returncode == 0:
                    # exit 0 이어도 링크가 실제로 저장됐는지 확인
                    time.sleep(2)
                    if _session_has_link(sid):
                        success = True
                        _log(f"세션 처리 완료 session_id={sid}")
                    else:
                        fail_reason = "링크 생성 프로세스가 종료됐지만 링크가 저장되지 않았습니다."
                        _log(f"[WARN] 링크 미저장 session_id={sid}")
                else:
                    fail_reason = f"auto_kvan 종료코드 {result.returncode}"
                    _log(f"[ERROR] 비정상 종료 session_id={sid} exit={result.returncode}")
            except subprocess.TimeoutExpired:
                fail_reason = "링크 생성 시간 초과(10분)"
                _log(f"[ERROR] 타임아웃 session_id={sid}")
            except Exception as e:
                fail_reason = str(e)
                _log(f"[ERROR] 예외 session_id={sid}: {e}")

            if not success and fail_reason:
                _mark_session_failed(sid, fail_reason)

            # 다음 세션 전 K-VAN 상태 안정화 대기
            time.sleep(3)

    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        _log("runner 종료 – 락 파일 제거 완료")


if __name__ == "__main__":
    main()
