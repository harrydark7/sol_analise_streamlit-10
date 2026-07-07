from __future__ import annotations

from io import BytesIO

import pandas as pd


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for sheet_name, df in sheets.items():
            safe = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe, index=False)
            workbook = writer.book
            worksheet = writer.sheets[safe]
            header_fmt = workbook.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "#FFFFFF", "border": 1})
            money_fmt = workbook.add_format({"num_format": "R$ #,##0.00"})
            percent_fmt = workbook.add_format({"num_format": "0.00%"})
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_fmt)
                width = min(max(len(str(value)) + 2, 12), 40)
                worksheet.set_column(col_num, col_num, width)
                if any(k in str(value).upper() for k in ["VALOR", "VPL", "SALDO", "HO", "META"]):
                    worksheet.set_column(col_num, col_num, width, money_fmt)
                if "%" in str(value).upper() or "CONVERSAO" in str(value).upper():
                    worksheet.set_column(col_num, col_num, width, percent_fmt)
            worksheet.freeze_panes(1, 0)
            if not df.empty:
                worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
    output.seek(0)
    return output.getvalue()
