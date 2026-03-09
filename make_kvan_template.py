from openpyxl import Workbook


EXCEL_PATH = "kvan_input.xlsx"


HEADERS = [
    "login_id",
    "login_password",
    "login_pin",
    "card_number",
    "expiry_mm",
    "expiry_yy",
    "card_password",
    "installment_months",
    "phone_number",
    "customer_name",
    "resident_front",
    "amount",
    "product_name",
]


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "kvan_input"

    # 1행: 헤더
    ws.append(HEADERS)

    # 2행: 예시 데이터 한 줄 (필요 없으면 엑셀에서 지우셔도 됩니다)
    example_row = [
        "m3313",      # login_id
        "k2255",      # login_password
        "2424",       # login_pin
        "1234567812345678",  # card_number (예시)
        "1",          # expiry_mm
        "2026",       # expiry_yy
        "12",         # card_password (앞 2자리)
        "2",          # installment_months
        "12345678",   # phone_number (010 뒤)
        "홍길동",      # customer_name
        "900101",     # resident_front (YYMMDD)
        50000,        # amount
        "잡화",        # product_name
    ]
    ws.append(example_row)

    wb.save(EXCEL_PATH)
    print(f"엑셀 템플릿이 생성되었습니다: {EXCEL_PATH}")


if __name__ == "__main__":
    main()

