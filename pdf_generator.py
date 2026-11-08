"""
Pure-Python PDF generator for Hostelite Finance.
Generates clean, compliant PDF 1.4 documents without any external C or pip dependencies.
"""
import io
from datetime import datetime

def escape_pdf_text(text):
    """Escape parentheses and backslashes for PDF string literal and sanitize unicode."""
    if text is None:
        return ""
    text = str(text)
    # Transliterate common unicode characters that fall outside latin1
    replacements = {
        "\u2014": "-",  # em-dash
        "\u2013": "-",  # en-dash
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2022": "*",  # bullet
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Ensure latin1 compatibility
    text = text.encode("latin1", errors="replace").decode("latin1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

def _build_pdf_stream(cmds):
    """Compile PDF drawing commands into standard PDF 1.4 binary bytes."""
    stream_str = "\n".join(cmds)
    stream_bytes = stream_str.encode("latin1")
    stream_len = len(stream_bytes)

    font_obj = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    font_bold_obj = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"

    obj1 = "<< /Type /Catalog /Pages 2 0 R >>"
    obj2 = "<< /Type /Pages /Kids [5 0 R] /Count 1 >>"
    obj3 = font_obj
    obj4 = font_bold_obj
    obj5 = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents 6 0 R >>"
    obj6 = f"<< /Length {stream_len} >>\nstream\n{stream_str}\nendstream"

    objs = [obj1, obj2, obj3, obj4, obj5, obj6]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin1"))

    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode("latin1"))
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin1"))

    out.write(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin1"))
    return out.getvalue()


# -------------------------------------------------------------
# 1. Monthly Report PDF
# -------------------------------------------------------------
def generate_monthly_report_pdf(report_data):
    """
    Generate a monthly finance report in PDF format.
    report_data dictionary contains:
      - month_label, generated_date, total_spent, daily_average, expense_count
      - allowance, remaining, category_stats, payment_stats, others_owe_you, you_owe_others
    """
    month_label = report_data.get("month_label", "Monthly Report")
    generated_date = report_data.get("generated_date", datetime.today().strftime("%b %d, %Y"))
    total_spent = float(report_data.get("total_spent", 0.0))
    daily_average = float(report_data.get("daily_average", 0.0))
    expense_count = int(report_data.get("expense_count", 0))
    allowance = float(report_data.get("allowance", 0.0))
    remaining = float(report_data.get("remaining", allowance - total_spent))
    category_stats = report_data.get("category_stats", [])
    payment_stats = report_data.get("payment_stats", [])
    others_owe_you = float(report_data.get("others_owe_you", 0.0))
    you_owe_others = float(report_data.get("you_owe_others", 0.0))

    cmds = []

    def set_fill_color(r, g, b):
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")

    def set_stroke_color(r, g, b):
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} RG")

    def draw_rect(x, y, w, h, fill=True, stroke=False):
        cmds.append(f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re")
        if fill and stroke:
            cmds.append("b")
        elif fill:
            cmds.append("f")
        elif stroke:
            cmds.append("s")

    def draw_line(x1, y1, x2, y2, width=1.0):
        cmds.append(f"{width:.1f} w")
        cmds.append(f"{x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l s")

    def draw_text(x, y, text, font="F1", size=10, r=0.1, g=0.1, b=0.1):
        clean = escape_pdf_text(text)
        cmds.append("BT")
        cmds.append(f"/{font} {size} Tf")
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        cmds.append(f"{x:.1f} {y:.1f} Td")
        cmds.append(f"({clean}) Tj")
        cmds.append("ET")

    def draw_text_right(right_x, y, text, font="F1", size=10, r=0.1, g=0.1, b=0.1):
        clean = escape_pdf_text(text)
        approx_w = len(clean) * (size * 0.52)
        x = max(50.0, right_x - approx_w)
        draw_text(x, y, clean, font, size, r, g, b)

    # 1. Top Header Banner
    set_fill_color(0.09, 0.13, 0.20) # Slate 900
    draw_rect(0, 770, 595, 72, fill=True, stroke=False)

    # Header brand and title
    draw_text(45, 805, "HOSTELITE FINANCE", font="F2", size=15, r=1.0, g=1.0, b=1.0)
    draw_text(45, 785, f"Monthly Financial Report - {month_label}", font="F1", size=11, r=0.85, g=0.90, b=0.95)
    draw_text_right(550, 795, f"Generated: {generated_date}", font="F1", size=9, r=0.75, g=0.80, b=0.88)

    # 2. Executive Summary Box
    y_box = 675
    set_fill_color(0.96, 0.98, 1.0) # Light slate tint
    set_stroke_color(0.85, 0.90, 0.95)
    draw_rect(45, y_box, 505, 75, fill=True, stroke=True)

    # Metric 1: Total Spent
    draw_text(60, y_box + 50, "TOTAL SPENT", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(60, y_box + 30, f"Rs. {int(total_spent):,}", font="F2", size=14, r=0.08, g=0.12, b=0.20)
    draw_text(60, y_box + 14, f"{expense_count} total expenses", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Metric 2: Daily Average
    draw_text(185, y_box + 50, "DAILY AVERAGE", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(185, y_box + 30, f"Rs. {daily_average:,.1f}", font="F2", size=14, r=0.08, g=0.12, b=0.20)
    draw_text(185, y_box + 14, "Avg spend per day", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Metric 3: Allowance
    draw_text(310, y_box + 50, "MONTHLY ALLOWANCE", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(310, y_box + 30, f"Rs. {int(allowance):,}" if allowance > 0 else "Not set", font="F2", size=14, r=0.08, g=0.12, b=0.20)
    draw_text(310, y_box + 14, "Fixed baseline budget", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Metric 4: Budget Remaining
    rem_color = (0.05, 0.5, 0.25) if remaining >= 0 else (0.75, 0.1, 0.1)
    draw_text(435, y_box + 50, "BUDGET REMAINING", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(435, y_box + 30, f"Rs. {int(remaining):,}", font="F2", size=14, r=rem_color[0], g=rem_color[1], b=rem_color[2])
    draw_text(435, y_box + 14, "Within budget" if remaining >= 0 else "Over budget", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Dividers between metrics
    set_stroke_color(0.85, 0.90, 0.95)
    draw_line(170, y_box + 10, 170, y_box + 65, width=0.8)
    draw_line(295, y_box + 10, 295, y_box + 65, width=0.8)
    draw_line(420, y_box + 10, 420, y_box + 65, width=0.8)

    # 3. Category Breakdown Table
    curr_y = 635
    draw_text(45, curr_y, "Spending by Category", font="F2", size=12, r=0.08, g=0.12, b=0.20)
    curr_y -= 18

    # Table Header
    set_fill_color(0.93, 0.95, 0.97)
    set_stroke_color(0.80, 0.85, 0.90)
    draw_rect(45, curr_y - 5, 505, 20, fill=True, stroke=True)
    draw_text(55, curr_y, "Category", font="F2", size=9, r=0.2, g=0.25, b=0.35)
    draw_text(260, curr_y, "Share of Spend", font="F2", size=9, r=0.2, g=0.25, b=0.35)
    draw_text_right(535, curr_y, "Amount (Rs.)", font="F2", size=9, r=0.2, g=0.25, b=0.35)
    curr_y -= 20

    if not category_stats:
        draw_text(55, curr_y, "No expense records found for this period.", font="F1", size=9, r=0.5, g=0.5, b=0.5)
        curr_y -= 25
    else:
        for cat_item in category_stats[:8]:
            cat_name = cat_item.get("category", "General")
            cat_amt = float(cat_item.get("amount", 0.0))
            cat_pct = float(cat_item.get("percentage", 0.0))

            set_stroke_color(0.90, 0.92, 0.95)
            draw_line(45, curr_y - 4, 550, curr_y - 4, width=0.5)

            draw_text(55, curr_y, cat_name, font="F1", size=9, r=0.1, g=0.15, b=0.25)
            draw_text(260, curr_y, f"{cat_pct:.1f}%", font="F1", size=9, r=0.3, g=0.35, b=0.45)
            draw_text_right(535, curr_y, f"Rs. {int(cat_amt):,}", font="F2", size=9, r=0.08, g=0.12, b=0.20)
            curr_y -= 18

    curr_y -= 15

    # 4. Payment Method & Money Owed (Two Side-by-Side Cards)
    card_w = 245
    card_h = 100
    y_dual = curr_y - card_h

    # Card Left: Payment Methods
    set_fill_color(0.98, 0.99, 1.0)
    set_stroke_color(0.85, 0.90, 0.95)
    draw_rect(45, y_dual, card_w, card_h, fill=True, stroke=True)
    draw_text(55, y_dual + 82, "Payment Methods", font="F2", size=10, r=0.08, g=0.12, b=0.20)

    pm_y = y_dual + 62
    if not payment_stats:
        draw_text(55, pm_y, "None recorded", font="F1", size=8, r=0.5, g=0.5, b=0.5)
    else:
        for pm in payment_stats[:3]:
            m_name = pm.get("method", "Cash")
            m_amt = float(pm.get("amount", 0.0))
            m_pct = float(pm.get("percentage", 0.0))
            draw_text(55, pm_y, f"{m_name}:", font="F1", size=8, r=0.2, g=0.25, b=0.35)
            draw_text_right(45 + card_w - 15, pm_y, f"Rs. {int(m_amt):,} ({m_pct:.0f}%)", font="F2", size=8, r=0.1, g=0.15, b=0.25)
            pm_y -= 15

    # Card Right: Outstanding Money Owed (Refined Muted Slate & Subtle Teal Palette)
    x_right_card = 305
    # Muted slate background with clean border
    set_fill_color(0.972, 0.980, 0.988) # Slate 50
    set_stroke_color(0.796, 0.835, 0.882) # Slate 300
    draw_rect(x_right_card, y_dual, card_w, card_h, fill=True, stroke=True)

    # Subtle top teal accent line
    set_fill_color(0.059, 0.463, 0.431) # Teal 700
    draw_rect(x_right_card, y_dual + card_h - 2.5, card_w, 2.5, fill=True, stroke=False)

    draw_text(x_right_card + 12, y_dual + 82, "Outstanding Balances (Money Owed)", font="F2", size=9.5, r=0.06, g=0.09, b=0.16)

    mo_y = y_dual + 58
    draw_text(x_right_card + 12, mo_y, "Friends Owe You:", font="F1", size=9, r=0.28, g=0.33, b=0.41)
    # Professional dark teal amount (high contrast, restrained)
    draw_text_right(x_right_card + card_w - 12, mo_y, f"Rs. {int(others_owe_you):,}", font="F2", size=9, r=0.059, g=0.463, b=0.431)

    mo_y -= 20
    set_stroke_color(0.886, 0.910, 0.941)
    draw_line(x_right_card + 10, mo_y + 12, x_right_card + card_w - 10, mo_y + 12, width=0.5)

    draw_text(x_right_card + 12, mo_y, "You Owe Friends:", font="F1", size=9, r=0.28, g=0.33, b=0.41)
    # Balanced muted warm slate for payable (clean, restrained, non-jarring)
    draw_text_right(x_right_card + card_w - 12, mo_y, f"Rs. {int(you_owe_others):,}", font="F2", size=9, r=0.450, g=0.200, b=0.150)

    mo_y -= 20
    draw_line(x_right_card + 10, mo_y + 12, x_right_card + card_w - 10, mo_y + 12, width=0.5)

    net_mo = others_owe_you - you_owe_others
    draw_text(x_right_card + 12, mo_y, "Net Receivable Position:", font="F1", size=8, r=0.35, g=0.40, b=0.48)
    net_str = f"+Rs. {int(net_mo):,}" if net_mo >= 0 else f"-Rs. {int(abs(net_mo)):,}"
    draw_text_right(x_right_card + card_w - 12, mo_y, net_str, font="F2", size=8.5, r=0.06, g=0.09, b=0.16)

    # 5. Footer Notice
    draw_line(45, 50, 550, 50, width=0.5)
    draw_text(45, 36, "Hostelite Finance - Built for Hostel Students", font="F1", size=8, r=0.5, g=0.55, b=0.6)
    draw_text_right(550, 36, "Page 1 of 1", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    return _build_pdf_stream(cmds)


# -------------------------------------------------------------
# 2. Split Bill Record PDF
# -------------------------------------------------------------
def generate_split_bill_pdf(split_data):
    """
    Generate an individual Split Bill receipt/summary document in PDF format.
    split_data dictionary:
      - title, total_amount, payer, date, split_type, participants, breakdown, who_owes_whom
    """
    title = split_data.get("title", "Group Bill Split")
    total_amount = float(split_data.get("total_amount", 0.0))
    payer = split_data.get("payer", "You")
    bill_date = split_data.get("date", datetime.today().strftime("%Y-%m-%d"))
    split_type = split_data.get("split_type", "equal").title()
    breakdown = split_data.get("breakdown", [])
    who_owes_whom = split_data.get("who_owes_whom", [])
    generated_date = datetime.today().strftime("%b %d, %Y")

    cmds = []

    def set_fill_color(r, g, b):
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")

    def set_stroke_color(r, g, b):
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} RG")

    def draw_rect(x, y, w, h, fill=True, stroke=False):
        cmds.append(f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re")
        if fill and stroke:
            cmds.append("b")
        elif fill:
            cmds.append("f")
        elif stroke:
            cmds.append("s")

    def draw_line(x1, y1, x2, y2, width=1.0):
        cmds.append(f"{width:.1f} w")
        cmds.append(f"{x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l s")

    def draw_text(x, y, text, font="F1", size=10, r=0.1, g=0.1, b=0.1):
        clean = escape_pdf_text(text)
        cmds.append("BT")
        cmds.append(f"/{font} {size} Tf")
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        cmds.append(f"{x:.1f} {y:.1f} Td")
        cmds.append(f"({clean}) Tj")
        cmds.append("ET")

    def draw_text_right(right_x, y, text, font="F1", size=10, r=0.1, g=0.1, b=0.1):
        clean = escape_pdf_text(text)
        approx_w = len(clean) * (size * 0.52)
        x = max(50.0, right_x - approx_w)
        draw_text(x, y, clean, font, size, r, g, b)

    # 1. Header Banner
    set_fill_color(0.09, 0.13, 0.20)
    draw_rect(0, 770, 595, 72, fill=True, stroke=False)

    draw_text(45, 805, "HOSTELITE FINANCE", font="F2", size=15, r=1.0, g=1.0, b=1.0)
    draw_text(45, 785, f"Split Bill Summary - {title}", font="F1", size=11, r=0.85, g=0.90, b=0.95)
    draw_text_right(550, 795, f"Date: {bill_date}", font="F1", size=9, r=0.75, g=0.80, b=0.88)

    # 2. Executive Details Card
    y_box = 675
    set_fill_color(0.972, 0.980, 0.988)
    set_stroke_color(0.80, 0.84, 0.88)
    draw_rect(45, y_box, 505, 75, fill=True, stroke=True)

    # Metric 1: Total Bill
    draw_text(60, y_box + 50, "TOTAL BILL", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(60, y_box + 30, f"Rs. {int(total_amount):,}", font="F2", size=14, r=0.08, g=0.12, b=0.20)
    draw_text(60, y_box + 14, f"{split_type} Split", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Metric 2: Paid by
    draw_text(185, y_box + 50, "PAID BY", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(185, y_box + 30, str(payer), font="F2", size=14, r=0.08, g=0.12, b=0.20)
    draw_text(185, y_box + 14, "Settled full amount", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Metric 3: Participants Count
    draw_text(310, y_box + 50, "PARTICIPANTS", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(310, y_box + 30, f"{len(breakdown)} People", font="F2", size=14, r=0.08, g=0.12, b=0.20)
    draw_text(310, y_box + 14, "Included in split", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Metric 4: Generated Date
    draw_text(435, y_box + 50, "GENERATED ON", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(435, y_box + 30, generated_date, font="F2", size=12, r=0.08, g=0.12, b=0.20)
    draw_text(435, y_box + 14, "Official Receipt", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    # Dividers
    draw_line(170, y_box + 10, 170, y_box + 65, width=0.8)
    draw_line(295, y_box + 10, 295, y_box + 65, width=0.8)
    draw_line(420, y_box + 10, 420, y_box + 65, width=0.8)

    # 3. Individual Shares Table
    curr_y = 635
    draw_text(45, curr_y, "Individual Share Breakdown", font="F2", size=12, r=0.08, g=0.12, b=0.20)
    curr_y -= 18

    # Table Header
    set_fill_color(0.93, 0.95, 0.97)
    set_stroke_color(0.80, 0.85, 0.90)
    draw_rect(45, curr_y - 5, 505, 20, fill=True, stroke=True)
    draw_text(55, curr_y, "Participant", font="F2", size=9, r=0.2, g=0.25, b=0.35)
    draw_text(250, curr_y, "Role / Participation", font="F2", size=9, r=0.2, g=0.25, b=0.35)
    draw_text_right(535, curr_y, "Share (Rs.)", font="F2", size=9, r=0.2, g=0.25, b=0.35)
    curr_y -= 20

    for item in breakdown:
        p_name = item.get("name", "Unknown")
        p_share = float(item.get("share", 0.0))
        is_payer = item.get("is_payer", False)

        set_stroke_color(0.90, 0.92, 0.95)
        draw_line(45, curr_y - 4, 550, curr_y - 4, width=0.5)

        draw_text(55, curr_y, p_name, font="F1", size=9.5, r=0.1, g=0.15, b=0.25)
        role_label = "Payer (Paid full bill)" if is_payer else "Participant"
        draw_text(250, curr_y, role_label, font="F1", size=9, r=0.35, g=0.40, b=0.50)
        draw_text_right(535, curr_y, f"Rs. {int(p_share):,}", font="F2", size=9.5, r=0.08, g=0.12, b=0.20)
        curr_y -= 18

    curr_y -= 15

    # 4. Settlement & Who Owes Whom Box
    draw_text(45, curr_y, "Settlement & Balances", font="F2", size=12, r=0.08, g=0.12, b=0.20)
    curr_y -= 16

    box_h = max(55, 24 + len(who_owes_whom) * 16)
    set_fill_color(0.972, 0.980, 0.988)
    set_stroke_color(0.80, 0.84, 0.88)
    draw_rect(45, curr_y - box_h + 10, 505, box_h, fill=True, stroke=True)

    # Subtle Teal Top Accent
    set_fill_color(0.059, 0.463, 0.431)
    draw_rect(45, curr_y + 8, 505, 2.5, fill=True, stroke=False)

    w_y = curr_y - 8
    if not who_owes_whom:
        draw_text(60, w_y, "All shares were settled or paid by a single individual.", font="F1", size=9, r=0.4, g=0.45, b=0.55)
    else:
        for line in who_owes_whom:
            draw_text(60, w_y, f"*  {line}", font="F2", size=9, r=0.10, g=0.25, b=0.35)
            w_y -= 16

    # Footer
    draw_line(45, 50, 550, 50, width=0.5)
    draw_text(45, 36, "Hostelite Finance - Built for Hostel Students", font="F1", size=8, r=0.5, g=0.55, b=0.6)
    draw_text_right(550, 36, "Page 1 of 1", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    return _build_pdf_stream(cmds)


# -------------------------------------------------------------
# 3. Individual Expense Receipt PDF
# -------------------------------------------------------------
def generate_expense_pdf(expense_data, allowance_info=None):
    """
    Generate an individual expense receipt document in PDF format.
    expense_data: dict with id, name, category, amount, date, payment_method, note
    allowance_info: dict with amount, remaining
    """
    exp_name = expense_data.get("name", "Expense")
    category = expense_data.get("category", "General")
    amount = float(expense_data.get("amount", 0.0))
    exp_date = expense_data.get("date", datetime.today().strftime("%Y-%m-%d"))
    payment_method = expense_data.get("payment_method", "Cash")
    note = expense_data.get("note", "").strip()
    generated_date = datetime.today().strftime("%b %d, %Y")

    cmds = []

    def set_fill_color(r, g, b):
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")

    def set_stroke_color(r, g, b):
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} RG")

    def draw_rect(x, y, w, h, fill=True, stroke=False):
        cmds.append(f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re")
        if fill and stroke:
            cmds.append("b")
        elif fill:
            cmds.append("f")
        elif stroke:
            cmds.append("s")

    def draw_line(x1, y1, x2, y2, width=1.0):
        cmds.append(f"{width:.1f} w")
        cmds.append(f"{x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l s")

    def draw_text(x, y, text, font="F1", size=10, r=0.1, g=0.1, b=0.1):
        clean = escape_pdf_text(text)
        cmds.append("BT")
        cmds.append(f"/{font} {size} Tf")
        cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        cmds.append(f"{x:.1f} {y:.1f} Td")
        cmds.append(f"({clean}) Tj")
        cmds.append("ET")

    def draw_text_right(right_x, y, text, font="F1", size=10, r=0.1, g=0.1, b=0.1):
        clean = escape_pdf_text(text)
        approx_w = len(clean) * (size * 0.52)
        x = max(50.0, right_x - approx_w)
        draw_text(x, y, clean, font, size, r, g, b)

    # 1. Header Banner
    set_fill_color(0.09, 0.13, 0.20)
    draw_rect(0, 770, 595, 72, fill=True, stroke=False)

    draw_text(45, 805, "HOSTELITE FINANCE", font="F2", size=15, r=1.0, g=1.0, b=1.0)
    draw_text(45, 785, f"Expense Receipt - {exp_name}", font="F1", size=11, r=0.85, g=0.90, b=0.95)
    draw_text_right(550, 795, f"Date: {exp_date}", font="F1", size=9, r=0.75, g=0.80, b=0.88)

    # 2. Main Expense Card
    y_box = 650
    set_fill_color(0.972, 0.980, 0.988)
    set_stroke_color(0.80, 0.84, 0.88)
    draw_rect(45, y_box, 505, 100, fill=True, stroke=True)

    # Accent top border
    set_fill_color(0.059, 0.463, 0.431)
    draw_rect(45, y_box + 97.5, 505, 2.5, fill=True, stroke=False)

    draw_text(65, y_box + 70, "EXPENSE NAME", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text(65, y_box + 50, exp_name, font="F2", size=16, r=0.08, g=0.12, b=0.20)
    draw_text(65, y_box + 30, f"Category: {category}", font="F1", size=9.5, r=0.3, g=0.35, b=0.45)
    draw_text(65, y_box + 14, f"Payment Method: {payment_method}", font="F1", size=9, r=0.4, g=0.45, b=0.55)

    draw_text_right(530, y_box + 68, "AMOUNT PAID", font="F2", size=8, r=0.4, g=0.45, b=0.55)
    draw_text_right(530, y_box + 44, f"Rs. {int(amount):,}", font="F2", size=18, r=0.06, g=0.09, b=0.16)
    draw_text_right(530, y_box + 22, f"Recorded on {exp_date}", font="F1", size=8.5, r=0.5, g=0.55, b=0.6)

    # 3. Details / Notes Section
    curr_y = 610
    if note:
        draw_text(45, curr_y, "Notes & Details", font="F2", size=11, r=0.08, g=0.12, b=0.20)
        curr_y -= 14
        set_fill_color(0.99, 1.0, 1.0)
        set_stroke_color(0.88, 0.90, 0.93)
        draw_rect(45, curr_y - 30, 505, 34, fill=True, stroke=True)
        draw_text(55, curr_y - 18, note, font="F1", size=9, r=0.2, g=0.25, b=0.35)
        curr_y -= 45

    # 4. Budget Context Box (if allowance configured)
    if allowance_info and allowance_info.get("amount", 0) > 0:
        al_amt = float(allowance_info.get("amount", 0))
        rem = float(allowance_info.get("remaining", 0))
        draw_text(45, curr_y, "Monthly Budget Context", font="F2", size=11, r=0.08, g=0.12, b=0.20)
        curr_y -= 14
        set_fill_color(0.96, 0.98, 1.0)
        set_stroke_color(0.85, 0.90, 0.95)
        draw_rect(45, curr_y - 40, 505, 45, fill=True, stroke=True)

        draw_text(60, curr_y - 18, "Monthly Allowance:", font="F1", size=9, r=0.3, g=0.35, b=0.45)
        draw_text(180, curr_y - 18, f"Rs. {int(al_amt):,}", font="F2", size=9, r=0.08, g=0.12, b=0.20)

        draw_text(300, curr_y - 18, "Budget Remaining:", font="F1", size=9, r=0.3, g=0.35, b=0.45)
        draw_text(420, curr_y - 18, f"Rs. {int(rem):,}", font="F2", size=9, r=0.059, g=0.463, b=0.431 if rem >= 0 else 0.75)

    # Footer
    draw_line(45, 50, 550, 50, width=0.5)
    draw_text(45, 36, "Hostelite Finance - Built for Hostel Students", font="F1", size=8, r=0.5, g=0.55, b=0.6)
    draw_text_right(550, 36, "Page 1 of 1", font="F1", size=8, r=0.5, g=0.55, b=0.6)

    return _build_pdf_stream(cmds)
