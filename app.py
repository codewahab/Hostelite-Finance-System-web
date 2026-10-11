from flask import Flask, render_template, request, redirect, url_for
import json
from datetime import date, datetime, timedelta
import calendar
import time
import urllib.parse

app = Flask(__name__)


# -------------------------------------------------------------
# Jinja Custom Filters
# -------------------------------------------------------------
def format_rupees(amount):
    """Format numbers into clean Pakistani Rupees (e.g. Rs. 30,000 or Rs. 250.50)."""
    try:
        val = float(amount)
        sign = "-" if val < 0 else ""
        val = abs(val)
        if val.is_integer():
            return f"{sign}Rs. {int(val):,}"
        else:
            return f"{sign}Rs. {val:,.2f}"
    except (ValueError, TypeError):
        return f"Rs. {amount}"

def urlencode_filter(s):
    """URL encode text for WhatsApp and web sharing links."""
    return urllib.parse.quote_plus(str(s))

def reminder_text_filter(record):
    """Generate a polite, natural hostel reminder message for debts."""
    person = record.get("person", "friend")
    try:
        amount = int(float(record.get("amount", 0)))
    except (ValueError, TypeError):
        amount = record.get("amount", 0)
    reason = record.get("reason", "").strip()
    reason_phrase = f" for {reason}" if reason and reason.lower() != "general" else ""
    return f"Assalam-o-Alaikum {person}! Hope you are doing well. Just a gentle reminder regarding Rs. {amount:,}{reason_phrase}. Whenever you get a chance, please settle it. Thanks!"

app.jinja_env.filters["rupees"] = format_rupees
app.jinja_env.filters["urlencode"] = urlencode_filter
app.jinja_env.filters["reminder_text"] = reminder_text_filter


# -------------------------------------------------------------
# JSON Storage Helper Functions
# -------------------------------------------------------------
def load_expenses():
    """Load all expenses from expenses.json."""
    try:
        with open("expenses.json", "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_expenses(expenses):
    """Save the list of expenses back to expenses.json."""
    with open("expenses.json", "w") as file:
        json.dump(expenses, file, indent=4)

def load_allowance():
    """Load monthly allowance data from allowance.json."""
    try:
        with open("allowance.json", "r") as file:
            data = json.load(file)
            return {
                "amount": float(data.get("allowance", 0.0)),
                "received_date": data.get("received_date", "")
            }
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {"amount": 0.0, "received_date": ""}

def save_allowance(amount, received_date=""):
    """Save allowance and received date to allowance.json."""
    with open("allowance.json", "w") as file:
        json.dump({"allowance": amount, "received_date": received_date}, file, indent=4)

def load_money_owed():
    """Load money owed records from money_owed.json."""
    try:
        with open("money_owed.json", "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_money_owed(records):
    """Save money owed records to money_owed.json."""
    with open("money_owed.json", "w") as file:
        json.dump(records, file, indent=4)

def load_splits():
    """Load bill splitting history from splits.json."""
    try:
        with open("splits.json", "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_splits(records):
    """Save bill splitting history to splits.json."""
    with open("splits.json", "w") as file:
        json.dump(records, file, indent=4)


# -------------------------------------------------------------
# Budget & Cycle Calculation Helpers
# -------------------------------------------------------------
def get_cycle_info(received_date_str):
    """
    Calculate the 1-month budget cycle based on the allowance received date.
    Does NOT assume cycle begins on the 1st of the month.
    """
    today = date.today()
    if not received_date_str:
        # Default fallback: calendar month
        _, total_days = calendar.monthrange(today.year, today.month)
        cycle_start = date(today.year, today.month, 1)
        cycle_end = date(today.year, today.month, total_days)
        days_rem = max(1, total_days - today.day + 1)
        return cycle_start, cycle_end, days_rem

    try:
        rec_date = datetime.strptime(received_date_str, "%Y-%m-%d").date()
    except ValueError:
        # If date is invalid format, fallback safely
        return get_cycle_info(None)

    # 1 month cycle from received_date
    y = rec_date.year
    m = rec_date.month
    d = rec_date.day

    next_m = m + 1 if m < 12 else 1
    next_y = y if m < 12 else y + 1
    max_d_in_next_m = calendar.monthrange(next_y, next_m)[1]
    next_cycle_start = date(next_y, next_m, min(d, max_d_in_next_m))
    cycle_end = next_cycle_start - timedelta(days=1)

    diff = (cycle_end - today).days + 1
    days_rem = max(1, diff) if diff > 0 else 1
    return rec_date, cycle_end, days_rem


# -------------------------------------------------------------
# 1. DASHBOARD & QUICK ADD ROUTES
# -------------------------------------------------------------
@app.route("/")
def home():
    expenses = load_expenses()
    allowance_info = load_allowance()
    allowance = allowance_info["amount"]
    received_date = allowance_info["received_date"]

    cycle_start, cycle_end, days_remaining = get_cycle_info(received_date)

    # Calculate expenses within the current allowance cycle
    if received_date:
        cycle_expenses = [e for e in expenses if e.get("date", "") >= received_date]
    else:
        cycle_expenses = expenses

    total_expense = sum(float(e.get("amount", 0)) for e in cycle_expenses)
    all_time_total = sum(float(e.get("amount", 0)) for e in expenses)

    # Budget calculations (Allowance stays the fixed baseline)
    remaining = allowance - total_expense
    percentage_spent = round((total_expense / allowance) * 100, 1) if allowance > 0 else 0.0

    if allowance > 0 and remaining > 0:
        daily_limit = round(remaining / days_remaining, 2)
    else:
        daily_limit = 0.0

    is_over_budget = (allowance > 0 and remaining < 0)

    # Money Owed overview
    money_owed_records = load_money_owed()
    others_owe_you = sum(float(r.get("amount", 0)) for r in money_owed_records if r.get("type") == "they_owe_me" and r.get("status") == "Pending")
    you_owe_others = sum(float(r.get("amount", 0)) for r in money_owed_records if r.get("type") == "i_owe_them" and r.get("status") == "Pending")
    pending_records_count = sum(1 for r in money_owed_records if r.get("status") == "Pending")

    # Available Cash calculation (Cash in hand):
    # Allowance Remaining + Cash borrowed from friends - Cash lent to friends
    if allowance > 0:
        available_cash = remaining + you_owe_others - others_owe_you
    else:
        available_cash = you_owe_others - others_owe_you

    # Low Funds Detection
    is_low_funds = False
    low_funds_message = ""
    top_debtors = []

    if allowance > 0:
        if remaining <= 0 or available_cash <= 0:
            is_low_funds = True
            low_funds_message = "Your budget or available cash is in negative balance. Limit spending immediately!"
        elif available_cash < (allowance * 0.15) or (daily_limit < 200 and days_remaining > 2):
            is_low_funds = True
            low_funds_message = f"Available cash is low ({format_rupees(available_cash)}). Safe daily limit is down to {format_rupees(daily_limit)}/day."

    if (is_low_funds or is_over_budget) and others_owe_you > 0:
        debtors_map = {}
        for r in money_owed_records:
            if r.get("type") == "they_owe_me" and r.get("status") == "Pending":
                p = r.get("person", "").strip()
                if p:
                    debtors_map[p] = debtors_map.get(p, 0.0) + float(r.get("amount", 0))
        top_debtors = sorted([{"name": k, "amount": v} for k, v in debtors_map.items()], key=lambda x: x["amount"], reverse=True)[:3]

    # Quick Add Helpers (recent categories & last payment method)
    recent_cats = []
    for e in reversed(expenses):
        c = e.get("category", "").strip().capitalize()
        if c and c not in recent_cats:
            recent_cats.append(c)
    defaults = ["Mess/Food", "Chai/Snacks", "Transport", "Groceries", "Laundry"]
    quick_categories = (recent_cats + [d for d in defaults if d not in recent_cats])[:5]
    last_payment_method = expenses[-1].get("payment_method", "Cash") if expenses else "Cash"

    # Recent expenses (last 5, newest first)
    recent_expenses = list(reversed(expenses))[:5]

    return render_template(
        "index.html",
        expenses=expenses,
        recent_expenses=recent_expenses,
        total_expense=total_expense,
        all_time_total=all_time_total,
        allowance=allowance,
        received_date=received_date,
        cycle_start=cycle_start.strftime("%b %d, %Y"),
        cycle_end=cycle_end.strftime("%b %d, %Y"),
        remaining=remaining,
        available_cash=available_cash,
        percentage_spent=percentage_spent,
        days_remaining=days_remaining,
        daily_limit=daily_limit,
        is_over_budget=is_over_budget,
        is_low_funds=is_low_funds,
        low_funds_message=low_funds_message,
        top_debtors=top_debtors,
        others_owe_you=others_owe_you,
        you_owe_others=you_owe_others,
        pending_records_count=pending_records_count,
        quick_categories=quick_categories,
        last_payment_method=last_payment_method,
        today=date.today().isoformat(),
        active_page="dashboard"
    )


@app.route("/quick-add-expense", methods=["POST"])
def quick_add_expense():
    """Rapid 5-second expense entry directly from Dashboard."""
    amount_raw = request.form.get("amount", "").strip()
    category = request.form.get("category", "").strip() or "General"
    name = request.form.get("name", "").strip() or category
    payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"
    date_val = request.form.get("date", "").strip() or date.today().isoformat()
    note = request.form.get("note", "").strip()

    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        return redirect(url_for("home"))

    expenses = load_expenses()
    new_expense = {
        "name": name,
        "category": category,
        "amount": amount,
        "date": date_val,
        "note": note,
        "payment_method": payment_method
    }
    expenses.append(new_expense)
    save_expenses(expenses)
    return redirect(url_for("home"))


# -------------------------------------------------------------
# 2. EXPENSE LIST, SEARCH & FILTER ROUTE
# -------------------------------------------------------------
@app.route("/expenses")
def list_expenses():
    expenses = load_expenses()

    q = request.args.get("q", "").strip().lower()
    category_filter = request.args.get("category", "").strip().lower()
    payment_filter = request.args.get("payment_method", "").strip()
    date_filter = request.args.get("date", "").strip()

    filtered = []
    # Keep track of original indices for edit/delete
    for idx, exp in enumerate(expenses):
        match = True

        # Text search across name, category, or note
        if q:
            name_match = q in exp.get("name", "").lower()
            cat_match = q in exp.get("category", "").lower()
            note_match = q in exp.get("note", "").lower()
            if not (name_match or cat_match or note_match):
                match = False

        # Category filter (text input)
        if match and category_filter:
            if category_filter not in exp.get("category", "").lower():
                match = False

        # Payment method filter
        if match and payment_filter and payment_filter != "All":
            if exp.get("payment_method", "Cash") != payment_filter:
                match = False

        # Date filter
        if match and date_filter:
            if exp.get("date", "") != date_filter:
                match = False

        if match:
            filtered.append({"index": idx, "data": exp})

    filtered_total = sum(float(item["data"].get("amount", 0)) for item in filtered)

    return render_template(
        "expenses.html",
        expenses=filtered,
        total_count=len(expenses),
        filtered_count=len(filtered),
        filtered_total=filtered_total,
        q=request.args.get("q", ""),
        category_filter=request.args.get("category", ""),
        payment_filter=payment_filter,
        date_filter=date_filter,
        active_page="expenses"
    )


# -------------------------------------------------------------
# 3. ADD EXPENSE ROUTE
# -------------------------------------------------------------
@app.route("/add-expense", methods=["GET", "POST"])
def add_expense():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip()
        note = request.form.get("note", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"

        # Validation: positive amount
        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return render_template(
                "add_expense.html",
                error="Please enter a valid positive amount.",
                today=date.today().isoformat(),
                active_page="expenses"
            )

        if not name or not category or not date_val:
            return render_template(
                "add_expense.html",
                error="Please fill in all required fields (Name, Category, Amount, Date).",
                today=date.today().isoformat(),
                active_page="expenses"
            )

        expenses = load_expenses()
        new_expense = {
            "name": name,
            "category": category,
            "amount": amount,
            "date": date_val,
            "note": note,
            "payment_method": payment_method
        }

        expenses.append(new_expense)
        save_expenses(expenses)

        return redirect(url_for("list_expenses"))

    return render_template(
        "add_expense.html",
        today=date.today().isoformat(),
        active_page="expenses"
    )


# -------------------------------------------------------------
# 4. EDIT EXPENSE ROUTE
# -------------------------------------------------------------
@app.route("/edit-expense/<int:index>", methods=["GET", "POST"])
def edit_expense(index):
    expenses = load_expenses()

    if index < 0 or index >= len(expenses):
        return redirect(url_for("list_expenses"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip()
        note = request.form.get("note", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return render_template(
                "edit_expense.html",
                expense=expenses[index],
                index=index,
                error="Please enter a valid positive amount.",
                active_page="expenses"
            )

        expenses[index] = {
            "name": name,
            "category": category,
            "amount": amount,
            "date": date_val,
            "note": note,
            "payment_method": payment_method
        }

        save_expenses(expenses)
        return redirect(url_for("list_expenses"))

    expense = expenses[index]
    return render_template(
        "edit_expense.html",
        expense=expense,
        index=index,
        active_page="expenses"
    )


# -------------------------------------------------------------
# 5. DELETE EXPENSE ROUTE
# -------------------------------------------------------------
@app.route("/delete-expense/<int:index>", methods=["GET", "POST"])
def delete_expense(index):
    expenses = load_expenses()
    if 0 <= index < len(expenses):
        expenses.pop(index)
        save_expenses(expenses)

    # Redirect back to where the user requested it from
    referrer = request.referrer
    if referrer and "/expenses" in referrer:
        return redirect(url_for("list_expenses"))
    return redirect(url_for("home"))


# -------------------------------------------------------------
# 6. SET ALLOWANCE & RECEIVED DATE ROUTE
# -------------------------------------------------------------
@app.route("/set-allowance", methods=["GET", "POST"])
def set_allowance():
    if request.method == "POST":
        allowance_raw = request.form.get("allowance", "").strip()
        received_date = request.form.get("received_date", "").strip()

        try:
            amount = float(allowance_raw)
            if amount < 0:
                raise ValueError
        except ValueError:
            current_info = load_allowance()
            return render_template(
                "set_allowance.html",
                allowance=current_info["amount"],
                received_date=current_info["received_date"],
                error="Please enter a valid allowance (0 or greater).",
                active_page="dashboard"
            )

        save_allowance(amount, received_date)
        return redirect(url_for("home"))

    current_info = load_allowance()
    return render_template(
        "set_allowance.html",
        allowance=current_info["amount"],
        received_date=current_info["received_date"],
        today=date.today().isoformat(),
        active_page="dashboard"
    )


# -------------------------------------------------------------
# 7. UNIFIED MONEY OWED ROUTE
# -------------------------------------------------------------
def calculate_money_owed_stats(records):
    """Calculate key summary statistics and per-person balances for Money Owed."""
    others_owe_you = sum(float(r.get("amount", 0)) for r in records if r.get("type") == "they_owe_me" and r.get("status") == "Pending")
    you_owe_others = sum(float(r.get("amount", 0)) for r in records if r.get("type") == "i_owe_them" and r.get("status") == "Pending")

    people_who_owe_me = set(r.get("person", "").strip().lower() for r in records if r.get("type") == "they_owe_me" and r.get("status") == "Pending" and r.get("person", "").strip())
    people_who_owe_me_count = len(people_who_owe_me)

    people_you_owe = set(r.get("person", "").strip().lower() for r in records if r.get("type") == "i_owe_them" and r.get("status") == "Pending" and r.get("person", "").strip())
    people_you_owe_count = len(people_you_owe)

    pending_count = sum(1 for r in records if r.get("status") == "Pending")
    settled_count = sum(1 for r in records if r.get("status") == "Settled")
    settled_total = sum(float(r.get("amount", 0)) for r in records if r.get("status") == "Settled")

    # Group records by individual person
    person_groups = {}
    for r in records:
        raw_person = r.get("person", "").strip()
        if not raw_person:
            continue
        key = raw_person.lower()
        if key not in person_groups:
            person_groups[key] = {
                "display_name": raw_person,
                "they_owe": 0.0,
                "you_owe": 0.0,
                "settled_total": 0.0,
                "records": []
            }
        person_groups[key]["records"].append(r)
        amt = float(r.get("amount", 0))
        if r.get("status") == "Pending":
            if r.get("type") == "they_owe_me":
                person_groups[key]["they_owe"] += amt
            else:
                person_groups[key]["you_owe"] += amt
        else:
            person_groups[key]["settled_total"] += amt

    grouped_list = []
    for key, group in person_groups.items():
        net = group["they_owe"] - group["you_owe"]
        group["net"] = net
        if net > 0:
            group["reminder_msg"] = (
                f"Assalam-o-Alaikum {group['display_name']}! Hope you're doing well. "
                f"Just a friendly reminder about the net balance of Rs. {int(net):,} when you get a chance. Thanks brother!"
            )
        else:
            group["reminder_msg"] = ""
        grouped_list.append(group)

    # Sort grouped list: active debts first (highest owed to user at top)
    grouped_list.sort(key=lambda g: (g["they_owe"] > 0, g["net"], abs(g["net"])), reverse=True)

    return {
        "others_owe_you": others_owe_you,
        "you_owe_others": you_owe_others,
        "people_who_owe_me_count": people_who_owe_me_count,
        "people_you_owe_count": people_you_owe_count,
        "pending_count": pending_count,
        "settled_count": settled_count,
        "settled_total": settled_total,
        "grouped_by_person": grouped_list
    }


@app.route("/money-owed", methods=["GET", "POST"])
def money_owed():
    records = load_money_owed()

    if request.method == "POST":
        person = request.form.get("person", "").strip()
        item_type = request.form.get("type", "they_owe_me").strip()  # "they_owe_me" or "i_owe_them"
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip()
        reason = request.form.get("reason", "").strip()
        note = request.form.get("note", "").strip()

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            stats = calculate_money_owed_stats(records)
            return render_template(
                "money_owed.html",
                records=list(reversed(records)),
                error="Please enter a valid positive amount.",
                today=date.today().isoformat(),
                active_page="money_owed",
                **stats
            )

        if not person:
            stats = calculate_money_owed_stats(records)
            return render_template(
                "money_owed.html",
                records=list(reversed(records)),
                error="Please enter a person name.",
                today=date.today().isoformat(),
                active_page="money_owed",
                **stats
            )

        new_entry = {
            "id": int(time.time() * 1000),
            "person": person,
            "type": item_type,
            "amount": amount,
            "date": date_val or date.today().isoformat(),
            "reason": reason or "General",
            "note": note,
            "status": "Pending"
        }

        records.append(new_entry)
        save_money_owed(records)
        return redirect(url_for("money_owed"))

    # Global stats across all records
    stats = calculate_money_owed_stats(records)

    # Optional search / filter params
    q = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "All").strip()
    person_filter = request.args.get("person", "").strip().lower()

    filtered_records = []
    for r in reversed(records):
        match = True
        if q:
            p_match = q in r.get("person", "").lower()
            r_match = q in r.get("reason", "").lower()
            n_match = q in r.get("note", "").lower()
            if not (p_match or r_match or n_match):
                match = False
        if match and status_filter and status_filter != "All":
            if r.get("status") != status_filter:
                match = False
        if match and person_filter:
            if r.get("person", "").strip().lower() != person_filter:
                match = False
        if match:
            filtered_records.append(r)

    return render_template(
        "money_owed.html",
        records=filtered_records,
        total_records_count=len(records),
        q=request.args.get("q", ""),
        status_filter=status_filter,
        person_filter=request.args.get("person", ""),
        today=date.today().isoformat(),
        active_page="money_owed",
        **stats
    )


@app.route("/money-owed/edit/<int:item_id>", methods=["GET", "POST"])
def edit_money_owed(item_id):
    """Edit an existing Money Owed record."""
    records = load_money_owed()
    target_idx = None
    for idx, r in enumerate(records):
        if r.get("id") == item_id:
            target_idx = idx
            break

    if target_idx is None:
        return redirect(url_for("money_owed"))

    target_record = records[target_idx]

    if request.method == "POST":
        person = request.form.get("person", "").strip()
        item_type = request.form.get("type", "they_owe_me").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip() or date.today().isoformat()
        reason = request.form.get("reason", "").strip()
        note = request.form.get("note", "").strip()
        status = request.form.get("status", "Pending").strip()

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return render_template(
                "edit_money_owed.html",
                record=target_record,
                error="Please enter a valid positive amount.",
                today=date.today().isoformat(),
                active_page="money_owed"
            )

        if not person:
            return render_template(
                "edit_money_owed.html",
                record=target_record,
                error="Person name is required.",
                today=date.today().isoformat(),
                active_page="money_owed"
            )

        records[target_idx] = {
            "id": item_id,
            "person": person,
            "type": item_type if item_type in ["they_owe_me", "i_owe_them"] else "they_owe_me",
            "amount": amount,
            "date": date_val,
            "reason": reason or "General",
            "note": note,
            "status": status if status in ["Pending", "Settled"] else "Pending"
        }
        save_money_owed(records)
        return redirect(url_for("money_owed"))

    return render_template(
        "edit_money_owed.html",
        record=target_record,
        today=date.today().isoformat(),
        active_page="money_owed"
    )


@app.route("/money-owed/toggle/<int:item_id>")
def toggle_money_owed(item_id):
    records = load_money_owed()
    for r in records:
        if r.get("id") == item_id:
            r["status"] = "Settled" if r.get("status") == "Pending" else "Pending"
            break
    save_money_owed(records)
    return redirect(url_for("money_owed"))


@app.route("/money-owed/delete/<int:item_id>")
def delete_money_owed(item_id):
    records = load_money_owed()
    records = [r for r in records if r.get("id") != item_id]
    save_money_owed(records)
    return redirect(url_for("money_owed"))


# Backward-compatible redirects for old routes
@app.route("/friends", methods=["GET", "POST"])
@app.route("/borrowed-lent", methods=["GET", "POST"])
def redirect_to_money_owed():
    return redirect(url_for("money_owed"))


# Add Split Bill calculation result to Money Owed
@app.route("/split-expense/add-to-money-owed", methods=["POST"])
def split_add_to_money_owed():
    person = request.form.get("person", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    item_type = request.form.get("type", "they_owe_me").strip()
    reason = request.form.get("reason", "").strip()
    date_val = request.form.get("date", "").strip() or date.today().isoformat()

    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        return redirect(url_for("split_expense"))

    if person:
        records = load_money_owed()
        new_entry = {
            "id": int(time.time() * 1000),
            "person": person,
            "type": item_type,
            "amount": amount,
            "date": date_val,
            "reason": reason or "Split Expense",
            "note": "Added from Split Bill calculator",
            "status": "Pending"
        }
        records.append(new_entry)
        save_money_owed(records)

    return redirect(url_for("money_owed"))


# -------------------------------------------------------------
# 9. GROUP / EXPENSE SPLITTING ROUTE
# -------------------------------------------------------------
@app.route("/split-expense", methods=["GET", "POST"])
def split_expense():
    splits_history = load_splits()
    calculation_result = None
    error = None

    if request.method == "POST":
        title = request.form.get("title", "").strip() or "Group Expense"
        total_raw = request.form.get("total_amount", "").strip()
        payer = request.form.get("payer", "You").strip() or "You"
        split_type = request.form.get("split_type", "equal").strip()
        sync_money_owed = request.form.get("sync_money_owed") == "yes"
        sync_expense = request.form.get("sync_expense") == "yes"
        expense_category = request.form.get("expense_category", "").strip() or "Food"
        expense_payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"
        save_to_history = request.form.get("save_history") == "yes"
        date_val = request.form.get("date", "").strip() or date.today().isoformat()

        try:
            total_amount = float(total_raw)
            if total_amount <= 0:
                raise ValueError
        except ValueError:
            error = "Please enter a valid positive total bill amount."
            return render_template(
                "split_expense.html",
                splits_history=splits_history,
                error=error,
                today=date.today().isoformat(),
                active_page="split"
            )

        breakdown = []
        who_owes_whom = []

        if split_type == "equal":
            people_input = request.form.get("people", "").strip()
            # Split comma separated names
            names = [n.strip() for n in people_input.split(",") if n.strip()]
            if not names:
                names = ["You", "Roommate 1", "Roommate 2"]

            count = len(names)
            per_person = round(total_amount / count, 2)

            for name in names:
                is_payer = (name.lower() == payer.lower())
                breakdown.append({
                    "name": name,
                    "share": per_person,
                    "is_payer": is_payer
                })
                if not is_payer:
                    who_owes_whom.append(f"{name} owes {payer} {format_rupees(per_person)}")

        else:
            # Custom splitting: Name: Amount per line
            custom_input = request.form.get("custom_breakdown", "").strip()
            lines = [l.strip() for l in custom_input.split("\n") if l.strip()]
            custom_total = 0.0

            for line in lines:
                if ":" in line:
                    parts = line.split(":", 1)
                    p_name = parts[0].strip()
                    try:
                        p_share = float(parts[1].strip())
                    except ValueError:
                        p_share = 0.0
                    custom_total += p_share
                    is_payer = (p_name.lower() == payer.lower())
                    breakdown.append({
                        "name": p_name,
                        "share": p_share,
                        "is_payer": is_payer
                    })
                    if not is_payer and p_share > 0:
                        who_owes_whom.append(f"{p_name} owes {payer} {format_rupees(p_share)}")

            if not breakdown:
                error = "Please enter custom shares in 'Name: Amount' format (e.g. Ali: 500)."

        sync_message = None
        if not error:
            calculation_result = {
                "title": title,
                "total_amount": total_amount,
                "payer": payer,
                "date": date_val,
                "split_type": split_type,
                "breakdown": breakdown,
                "who_owes_whom": who_owes_whom
            }

            sync_notices = []
            payer_lower = payer.lower()
            is_user_payer = (payer_lower in ["you", "me", "myself"])

            # 1. Automatic sync to Money Owed
            if sync_money_owed:
                owed_records = load_money_owed()
                debts_added = 0

                if is_user_payer:
                    for item in breakdown:
                        if not item["is_payer"] and item["share"] > 0:
                            owed_records.append({
                                "id": int(time.time() * 1000) + debts_added,
                                "person": item["name"],
                                "type": "they_owe_me",
                                "amount": item["share"],
                                "date": date_val,
                                "reason": f"Split: {title}",
                                "note": f"Share of {format_rupees(total_amount)} group bill",
                                "status": "Pending"
                            })
                            debts_added += 1
                    if debts_added > 0:
                        sync_notices.append(f"Recorded {debts_added} roommate debt(s) in Money Owed")
                else:
                    user_item = next((item for item in breakdown if item["name"].lower() in ["you", "me", "myself"]), None)
                    if user_item and user_item["share"] > 0:
                        owed_records.append({
                            "id": int(time.time() * 1000),
                            "person": payer,
                            "type": "i_owe_them",
                            "amount": user_item["share"],
                            "date": date_val,
                            "reason": f"Split: {title}",
                            "note": f"Your share of {format_rupees(total_amount)} group bill paid by {payer}",
                            "status": "Pending"
                        })
                        sync_notices.append(f"Recorded debt of {format_rupees(user_item['share'])} to {payer} in Money Owed")

                if debts_added > 0 or (not is_user_payer and debts_added == 0 and any("Recorded debt" in n for n in sync_notices)):
                    save_money_owed(owed_records)

            # 2. Automatic sync of personal share to Expenses (No double-counting)
            if sync_expense and is_user_payer:
                user_item = next((item for item in breakdown if item["name"].lower() in ["you", "me", "myself"]), None)
                user_share = user_item["share"] if user_item else (breakdown[0]["share"] if breakdown else 0.0)
                if user_share > 0:
                    exp_list = load_expenses()
                    exp_list.append({
                        "name": f"{title} (My share)",
                        "category": expense_category,
                        "amount": user_share,
                        "date": date_val,
                        "note": f"Personal share of {format_rupees(total_amount)} {title} split",
                        "payment_method": expense_payment_method
                    })
                    save_expenses(exp_list)
                    sync_notices.append(f"Recorded {format_rupees(user_share)} in Expenses as your personal share (No double-counting)")

            if sync_notices:
                sync_message = " & ".join(sync_notices)

            if save_to_history:
                new_split = {
                    "id": int(time.time() * 1000),
                    "title": title,
                    "total_amount": total_amount,
                    "payer": payer,
                    "date": date_val,
                    "split_type": split_type,
                    "who_owes_whom": who_owes_whom
                }
                splits_history.append(new_split)
                save_splits(splits_history)

    return render_template(
        "split_expense.html",
        splits_history=list(reversed(splits_history)),
        result=calculation_result,
        sync_message=sync_message if request.method == "POST" else None,
        error=error,
        today=date.today().isoformat(),
        active_page="split"
    )

@app.route("/split-expense/delete/<int:item_id>")
def delete_split(item_id):
    splits = load_splits()
    splits = [s for s in splits if s.get("id") != item_id]
    save_splits(splits)
    return redirect(url_for("split_expense"))


# -------------------------------------------------------------
# 10. REPORTS & ANALYTICS ROUTE
# -------------------------------------------------------------
@app.route("/reports")
def reports():
    expenses = load_expenses()
    allowance_info = load_allowance()
    allowance = allowance_info["amount"]
    received_date = allowance_info["received_date"]

    cycle_start, cycle_end, days_remaining = get_cycle_info(received_date)

    total_spent = sum(float(e.get("amount", 0)) for e in expenses)

    # 1. Category Breakdown (Doughnut Chart Data)
    category_map = {}
    for e in expenses:
        cat = e.get("category", "Other").strip()
        cat = cat.capitalize() if cat else "Other"
        amt = float(e.get("amount", 0))
        category_map[cat] = category_map.get(cat, 0.0) + amt

    category_stats = []
    category_labels = []
    category_data = []
    for cat, amt in sorted(category_map.items(), key=lambda x: x[1], reverse=True):
        pct = round((amt / total_spent) * 100, 1) if total_spent > 0 else 0.0
        category_stats.append({"category": cat, "amount": amt, "percentage": pct})
        category_labels.append(cat)
        category_data.append(round(amt, 2))

    highest_category = category_stats[0] if category_stats else None

    # 2. Payment Method Breakdown (Doughnut Chart Data)
    payment_map = {}
    for e in expenses:
        pm = e.get("payment_method", "Cash").strip() or "Cash"
        amt = float(e.get("amount", 0))
        payment_map[pm] = payment_map.get(pm, 0.0) + amt

    payment_stats = []
    payment_labels = []
    payment_data = []
    for pm, amt in sorted(payment_map.items(), key=lambda x: x[1], reverse=True):
        pct = round((amt / total_spent) * 100, 1) if total_spent > 0 else 0.0
        payment_stats.append({"method": pm, "amount": amt, "percentage": pct})
        payment_labels.append(pm)
        payment_data.append(round(amt, 2))

    # 3. Timeframe Metrics
    today = date.today()
    today_str = today.isoformat()
    seven_days_ago = today - timedelta(days=6)
    month_prefix = today.strftime("%Y-%m")

    today_spent = sum(float(e.get("amount", 0)) for e in expenses if e.get("date") == today_str)
    week_spent = sum(float(e.get("amount", 0)) for e in expenses if e.get("date", "") >= seven_days_ago.isoformat())
    month_spent = sum(float(e.get("amount", 0)) for e in expenses if e.get("date", "").startswith(month_prefix))

    # 4. Bar Chart: Recent Spending (Last 7 Days)
    last_7_days_labels = []
    last_7_days_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.isoformat()
        last_7_days_labels.append(d.strftime("%b %d"))
        day_total = sum(float(e.get("amount", 0)) for e in expenses if e.get("date") == d_str)
        last_7_days_data.append(round(day_total, 2))

    # 5. Line Chart: Monthly Spending Trend (from cycle start to today)
    cycle_trend_labels = []
    cycle_trend_data = []
    start_d = cycle_start if cycle_start <= today else date(today.year, today.month, 1)
    end_d = today
    curr_d = start_d
    running_total = 0.0
    while curr_d <= end_d:
        d_str = curr_d.isoformat()
        day_amt = sum(float(e.get("amount", 0)) for e in expenses if e.get("date") == d_str)
        running_total += day_amt
        cycle_trend_labels.append(curr_d.strftime("%b %d"))
        cycle_trend_data.append(round(running_total, 2))
        curr_d += timedelta(days=1)

    return render_template(
        "reports.html",
        total_spent=total_spent,
        expense_count=len(expenses),
        today_spent=today_spent,
        week_spent=week_spent,
        month_spent=month_spent,
        category_stats=category_stats,
        highest_category=highest_category,
        payment_stats=payment_stats,
        category_labels_json=json.dumps(category_labels),
        category_data_json=json.dumps(category_data),
        payment_labels_json=json.dumps(payment_labels),
        payment_data_json=json.dumps(payment_data),
        last_7_days_labels_json=json.dumps(last_7_days_labels),
        last_7_days_data_json=json.dumps(last_7_days_data),
        cycle_trend_labels_json=json.dumps(cycle_trend_labels),
        cycle_trend_data_json=json.dumps(cycle_trend_data),
        allowance=allowance,
        active_page="reports"
    )


if __name__ == "__main__":
    app.run(debug=True)