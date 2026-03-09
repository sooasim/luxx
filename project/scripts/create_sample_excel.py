from pathlib import Path
from openpyxl import Workbook

base_dir = Path(__file__).resolve().parent.parent
out_file = base_dir / 'data' / 'sample_input.xlsx'
out_file.parent.mkdir(parents=True, exist_ok=True)

wb = Workbook()
ws = wb.active
ws.title = 'input'
ws.append([
    'username', 'password', 'pin', 'card_type', 'card_number', 'exp_month', 'exp_year',
    'card_password_two_digits', 'installment', 'phone', 'customer_name', 'birth6'
])
ws.append([
    'demo_user', 'demo_pass', '2424', '개인카드', '1234123412341234', '01', '29',
    '12', '일시불', '01012345678', '홍길동', '900101'
])

wb.save(out_file)
print(f'Created: {out_file}')
