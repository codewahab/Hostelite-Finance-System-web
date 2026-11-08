"""
Hostelite Finance — Supabase PostgreSQL Migration Script
Migrates users and financial records from JSON files to Supabase PostgreSQL.

Usage:
    py migrate_to_supabase.py --dry-run
    py migrate_to_supabase.py --init-db
    py migrate_to_supabase.py --apply
    py migrate_to_supabase.py --verify
"""

import os
import sys
import json
import argparse
from datetime import datetime, date, timezone
from decimal import Decimal
from flask import Flask
from models import db, User, Allowance, Room, Split, Expense, MoneyOwed

def parse_date(date_val):
    """Safely parse date into a date object."""
    if not date_val or not str(date_val).strip():
        return None
    if isinstance(date_val, (date, datetime)):
        return date_val if isinstance(date_val, date) else date_val.date()
    try:
        return datetime.strptime(str(date_val).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def load_json_file(filepath, default=None):
    """Safely load JSON data from disk."""
    if default is None:
        default = []
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Failed to read {filepath}: {e}")
        return default

def get_database_uri(cli_url=None):
    """Retrieve database URL from CLI argument, .env, or environment."""
    url = None
    if cli_url:
        url = cli_url
    else:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DATABASE_URL="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            url = val
                            break
        if not url:
            url = os.environ.get("DATABASE_URL")
    if url and url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url

def create_app(database_url):
    """Create Flask application context for database operations."""
    app = Flask(__name__)
    if database_url and database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    if database_url and ":6543" in database_url:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"options": "-c search_path=public"},
            "pool_pre_ping": True,
        }
    else:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
        }
        
    db.init_app(app)
    return app

def validate_entity_records(user_id, entity_type, records):
    """
    Validate that each record has a valid integer ID and expected core fields.
    Raises ValueError immediately if invalid structure or data is detected.
    """
    seen_ids = set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError(f"User {user_id}: {entity_type} record is not an object: {item}")
        
        # Check ID
        raw_id = item.get("id")
        if raw_id is None:
            raise ValueError(f"User {user_id}: {entity_type} record missing required 'id': {item}")
        try:
            item_id = int(raw_id)
        except (ValueError, TypeError):
            raise ValueError(f"User {user_id}: {entity_type} record has invalid non-integer 'id': {raw_id}")
            
        if item_id in seen_ids:
            raise ValueError(f"User {user_id}: Duplicate {entity_type} ID {item_id} in source JSON data.")
        seen_ids.add(item_id)
        
        # Entity-specific validation
        if entity_type == "expense":
            try:
                amt = float(item.get("amount", 0.0))
                if amt < 0:
                    raise ValueError(f"Negative expense amount: {amt}")
            except (ValueError, TypeError) as ex:
                raise ValueError(f"User {user_id}: Expense {item_id} has invalid amount: {ex}")
            if not parse_date(item.get("date")):
                raise ValueError(f"User {user_id}: Expense {item_id} has invalid date: {item.get('date')}")
                
        elif entity_type == "money_owed":
            try:
                amt = float(item.get("amount", 0.0))
                if amt < 0:
                    raise ValueError(f"Negative debt amount: {amt}")
            except (ValueError, TypeError) as ex:
                raise ValueError(f"User {user_id}: MoneyOwed {item_id} has invalid amount: {ex}")
            if item.get("type") not in ("they_owe_me", "i_owe_them"):
                raise ValueError(f"User {user_id}: MoneyOwed {item_id} has invalid type: {item.get('type')}")
                
        elif entity_type == "room":
            if not item.get("name"):
                raise ValueError(f"User {user_id}: Room {item_id} missing name")
            if not isinstance(item.get("members", []), list):
                raise ValueError(f"User {user_id}: Room {item_id} 'members' must be a list")
                
        elif entity_type == "split":
            try:
                amt = float(item.get("total_amount", 0.0))
                if amt < 0:
                    raise ValueError(f"Negative split total_amount: {amt}")
            except (ValueError, TypeError) as ex:
                raise ValueError(f"User {user_id}: Split {item_id} has invalid total_amount: {ex}")

def build_migration_plan(target_user_id=None):
    """
    Audit source JSON files, identify real user accounts vs test fixtures,
    and construct a verified migration plan.
    """
    users_file = "users.json"
    user_data_base = "user_data"
    
    users = load_json_file(users_file, [])
    if not users:
        print("No registered users found in users.json. Nothing to migrate.")
        return []
    
    users_to_migrate = []
    test_user_ids = {999999, 888123}
    
    for u in users:
        u_id = int(u.get("id"))
        username = str(u.get("username", "")).lower()
        if u_id in test_user_ids or username.startswith("test") or username.startswith("mock"):
            print(f" -> Skipping known test user fixture: {u.get('username')} (ID: {u_id})")
            continue
        if target_user_id and u_id != int(target_user_id):
            continue
        users_to_migrate.append(u)
        
    plan = []
    for u in users_to_migrate:
        u_id = int(u["id"])
        u_dir = os.path.join(user_data_base, str(u_id))
        
        source_desc = ""
        user_folder_exists = os.path.exists(u_dir)
        
        exp_list = load_json_file(os.path.join(u_dir, "expenses.json"), [])
        allowance_data = load_json_file(os.path.join(u_dir, "allowance.json"), {"allowance": 0.0, "received_date": ""})
        owed_list = load_json_file(os.path.join(u_dir, "money_owed.json"), [])
        rooms_list = load_json_file(os.path.join(u_dir, "rooms.json"), [])
        splits_list = load_json_file(os.path.join(u_dir, "splits.json"), [])
        
        # Determine source selection with explicit reasoning
        if user_folder_exists and (exp_list or owed_list or rooms_list or splits_list):
            source_desc = f"Dedicated user directory '{u_dir}' (active records present). Root JSON files excluded."
        elif len(users_to_migrate) == 1 and os.path.exists("expenses.json"):
            # Sole registered user whose directory was empty, root data confirmed as sole account
            exp_list = load_json_file("expenses.json", [])
            allowance_data = load_json_file("allowance.json", allowance_data)
            owed_list = load_json_file("money_owed.json", [])
            rooms_list = load_json_file("rooms.json", [])
            splits_list = load_json_file("splits.json", [])
            source_desc = "Root JSON files (verified single-user setup; user directory had no independent data)."
        else:
            source_desc = f"Dedicated user directory '{u_dir}' (no finance records found)."
            
        # Validate data integrity before proceeding
        validate_entity_records(u_id, "expense", exp_list)
        validate_entity_records(u_id, "money_owed", owed_list)
        validate_entity_records(u_id, "room", rooms_list)
        validate_entity_records(u_id, "split", splits_list)
        
        total_expense_sum = sum(float(e.get("amount", 0)) for e in exp_list)
        total_owed_to_user = sum(float(m.get("amount", 0)) for m in owed_list if m.get("type") == "they_owe_me")
        total_user_owes = sum(float(m.get("amount", 0)) for m in owed_list if m.get("type") == "i_owe_them")
        
        user_plan = {
            "user": u,
            "source_reason": source_desc,
            "expenses": exp_list,
            "allowance": allowance_data,
            "money_owed": owed_list,
            "rooms": rooms_list,
            "splits": splits_list,
            "stats": {
                "expense_count": len(exp_list),
                "expense_total": total_expense_sum,
                "allowance": float(allowance_data.get("allowance", 0)),
                "debt_count": len(owed_list),
                "they_owe_me": total_owed_to_user,
                "i_owe_them": total_user_owes,
                "room_count": len(rooms_list),
                "split_count": len(splits_list),
            }
        }
        plan.append(user_plan)
        
    return plan

def run_migration(database_url, dry_run=True, target_user_id=None, app_instance=None):
    """
    Execute migration in dry-run or apply mode.
    Returns summary dict.
    """
    print("=" * 70)
    print(" HOSTELITE FINANCE — SUPABASE POSTGRESQL MIGRATION")
    print(f" Mode: {'DRY RUN (Simulated — No database writes)' if dry_run else 'APPLY (Writing to Database)'}")
    print("=" * 70)
    
    plan = build_migration_plan(target_user_id)
    if not plan:
        print("No users in migration plan. Exiting.")
        return {"success": True, "created": 0, "updated": 0, "skipped": 0}
        
    print(f"\nConstructed plan for {len(plan)} user account(s):")
    for item in plan:
        u = item["user"]
        st = item["stats"]
        print(f"\nUser: {u.get('username')} (ID: {u.get('id')})")
        print(f"  Source:      {item['source_reason']}")
        print(f"  Auth:        {u.get('auth_provider')} | Google ID: {bool(u.get('google_id'))} | Email: {u.get('email')}")
        print(f"  Expenses:    {st['expense_count']} records | Total: Rs. {st['expense_total']:,.2f}")
        print(f"  Allowance:   Rs. {st['allowance']:,.2f}")
        print(f"  Money Owed:  {st['debt_count']} records | Others owe: Rs. {st['they_owe_me']:,.2f} | User owes: Rs. {st['i_owe_them']:,.2f}")
        print(f"  Rooms:       {st['room_count']} records")
        print(f"  Splits:      {st['split_count']} records")

    if dry_run:
        print("\n" + "-" * 70)
        print(" DRY RUN COMPLETE — 0 database operations performed.")
        print(" All source records, IDs, and constraints were validated.")
        print(" To execute the migration, run:")
        print("   py migrate_to_supabase.py --apply")
        print("-" * 70)
        return {"success": True, "dry_run": True, "plan": plan}

    # APPLY MODE
    if not database_url and not app_instance:
        print("ERROR: DATABASE_URL is not configured in .env or via --database-url argument!")
        sys.exit(1)

    app = app_instance or create_app(database_url)
    
    with app.app_context():
        # Schema tables must exist
        db.create_all()
        
        counts = {
            "users_created": 0, "users_skipped": 0, "users_updated": 0,
            "allowances_created": 0, "allowances_skipped": 0, "allowances_updated": 0,
            "rooms_created": 0, "rooms_skipped": 0, "rooms_updated": 0,
            "splits_created": 0, "splits_skipped": 0, "splits_updated": 0,
            "expenses_created": 0, "expenses_skipped": 0, "expenses_updated": 0,
            "debts_created": 0, "debts_skipped": 0, "debts_updated": 0,
        }
        
        try:
            for item in plan:
                u_dict = item["user"]
                u_id = int(u_dict["id"])
                
                # 1. User
                existing_user = db.session.get(User, u_id)
                created_dt = parse_date(u_dict.get("created_at"))
                created_datetime = datetime.combine(created_dt, datetime.min.time()) if created_dt else datetime.now(timezone.utc)
                
                if not existing_user:
                    user_obj = User(
                        id=u_id,
                        username=u_dict.get("username"),
                        display_name=u_dict.get("display_name") or u_dict.get("username"),
                        email=u_dict.get("email") or None,
                        email_verified=bool(u_dict.get("email_verified", False)),
                        password_hash=u_dict.get("password_hash"),
                        google_id=u_dict.get("google_id") or None,
                        auth_provider=u_dict.get("auth_provider", "local"),
                        created_at=created_datetime
                    )
                    db.session.add(user_obj)
                    counts["users_created"] += 1
                else:
                    if existing_user.username.lower() != str(u_dict.get("username", "")).lower():
                        raise ValueError(f"Cross-user conflict: User ID {u_id} in DB has username '{existing_user.username}', conflicts with '{u_dict.get('username')}'.")
                    
                    changed = False
                    if u_dict.get("display_name") and existing_user.display_name != u_dict["display_name"]:
                        existing_user.display_name = u_dict["display_name"]
                        changed = True
                    if u_dict.get("google_id") and existing_user.google_id != u_dict["google_id"]:
                        existing_user.google_id = u_dict["google_id"]
                        existing_user.auth_provider = "google"
                        changed = True
                    if changed:
                        counts["users_updated"] += 1
                    else:
                        counts["users_skipped"] += 1
                
                db.session.flush()

                # 2. Allowance
                al_data = item["allowance"]
                al_amt = Decimal(str(al_data.get("allowance", 0.0)))
                al_date = parse_date(al_data.get("received_date"))
                
                al_obj = Allowance.query.filter_by(user_id=u_id).first()
                if not al_obj:
                    al_obj = Allowance(user_id=u_id, amount=al_amt, received_date=al_date)
                    db.session.add(al_obj)
                    counts["allowances_created"] += 1
                else:
                    if al_obj.amount != al_amt or al_obj.received_date != al_date:
                        al_obj.amount = al_amt
                        al_obj.received_date = al_date
                        counts["allowances_updated"] += 1
                    else:
                        counts["allowances_skipped"] += 1

                # 3. Rooms (Strict Ownership & Collision Check)
                for r in item["rooms"]:
                    r_id = int(r.get("id"))
                    global_room = db.session.get(Room, r_id)
                    if global_room:
                        if global_room.user_id != u_id:
                            raise ValueError(f"Cross-user ID collision: Room ID {r_id} already exists under user {global_room.user_id}, cannot assign to user {u_id}. Aborting.")
                        if global_room.name != r.get("name", "Room") or global_room.members != r.get("members", []):
                            global_room.name = r.get("name", "Room")
                            global_room.members = r.get("members", [])
                            counts["rooms_updated"] += 1
                        else:
                            counts["rooms_skipped"] += 1
                    else:
                        room_obj = Room(id=r_id, user_id=u_id, name=r.get("name", "Room"), members=r.get("members", []))
                        db.session.add(room_obj)
                        counts["rooms_created"] += 1

                # 4. Splits (Strict Ownership & Collision Check)
                for s in item["splits"]:
                    s_id = int(s.get("id"))
                    s_total = Decimal(str(s.get("total_amount", 0.0)))
                    s_date = parse_date(s.get("date")) or date.today()
                    
                    global_split = db.session.get(Split, s_id)
                    if global_split:
                        if global_split.user_id != u_id:
                            raise ValueError(f"Cross-user ID collision: Split ID {s_id} already exists under user {global_split.user_id}, cannot assign to user {u_id}. Aborting.")
                        global_split.title = s.get("title", "Split Bill")
                        global_split.total_amount = s_total
                        global_split.payer = s.get("payer", "You")
                        global_split.date = s_date
                        counts["splits_updated"] += 1
                    else:
                        split_obj = Split(
                            id=s_id,
                            user_id=u_id,
                            title=s.get("title", "Split Bill"),
                            total_amount=s_total,
                            payer=s.get("payer", "You"),
                            date=s_date,
                            split_mode=s.get("split_mode", "room"),
                            split_type=s.get("split_type", "equal"),
                            participants=s.get("participants", []),
                            breakdown=s.get("breakdown", []),
                            who_owes_whom=s.get("who_owes_whom", [])
                        )
                        db.session.add(split_obj)
                        counts["splits_created"] += 1

                db.session.flush()

                # 5. Expenses (Strict Ownership & Collision Check)
                for e in item["expenses"]:
                    e_id = int(e.get("id"))
                    e_amt = Decimal(str(e.get("amount", 0.0)))
                    e_date = parse_date(e.get("date")) or date.today()
                    s_id = e.get("split_id")
                    if s_id and not db.session.get(Split, int(s_id)):
                        s_id = None
                        
                    global_exp = db.session.get(Expense, e_id)
                    if global_exp:
                        if global_exp.user_id != u_id:
                            raise ValueError(f"Cross-user ID collision: Expense ID {e_id} already exists under user {global_exp.user_id}, cannot assign to user {u_id}. Aborting.")
                        if (global_exp.name != e.get("name") or global_exp.amount != e_amt or
                            global_exp.category != e.get("category") or global_exp.date != e_date):
                            global_exp.name = e.get("name", "Expense")
                            global_exp.category = e.get("category", "General")
                            global_exp.amount = e_amt
                            global_exp.date = e_date
                            global_exp.note = e.get("note", "") or ""
                            global_exp.payment_method = e.get("payment_method", "Cash") or "Cash"
                            global_exp.split_id = int(s_id) if s_id else None
                            counts["expenses_updated"] += 1
                        else:
                            counts["expenses_skipped"] += 1
                    else:
                        exp_obj = Expense(
                            id=e_id,
                            user_id=u_id,
                            name=e.get("name", "Expense"),
                            category=e.get("category", "General"),
                            amount=e_amt,
                            date=e_date,
                            note=e.get("note", "") or "",
                            payment_method=e.get("payment_method", "Cash") or "Cash",
                            split_id=int(s_id) if s_id else None
                        )
                        db.session.add(exp_obj)
                        counts["expenses_created"] += 1

                # 6. Money Owed (Strict Ownership & Collision Check)
                for m in item["money_owed"]:
                    m_id = int(m.get("id"))
                    m_amt = Decimal(str(m.get("amount", 0.0)))
                    m_date = parse_date(m.get("date")) or date.today()
                    s_id = m.get("split_id")
                    if s_id and not db.session.get(Split, int(s_id)):
                        s_id = None
                        
                    global_debt = db.session.get(MoneyOwed, m_id)
                    if global_debt:
                        if global_debt.user_id != u_id:
                            raise ValueError(f"Cross-user ID collision: MoneyOwed ID {m_id} already exists under user {global_debt.user_id}, cannot assign to user {u_id}. Aborting.")
                        if (global_debt.person != m.get("person") or global_debt.amount != m_amt or
                            global_debt.status != m.get("status") or global_debt.type != m.get("type")):
                            global_debt.person = m.get("person", "Friend")
                            global_debt.type = m.get("type", "they_owe_me")
                            global_debt.amount = m_amt
                            global_debt.date = m_date
                            global_debt.reason = m.get("reason", "") or ""
                            global_debt.note = m.get("note", "") or ""
                            global_debt.status = m.get("status", "Pending") or "Pending"
                            global_debt.split_id = int(s_id) if s_id else None
                            counts["debts_updated"] += 1
                        else:
                            counts["debts_skipped"] += 1
                    else:
                        debt_obj = MoneyOwed(
                            id=m_id,
                            user_id=u_id,
                            person=m.get("person", "Friend"),
                            type=m.get("type", "they_owe_me"),
                            amount=m_amt,
                            date=m_date,
                            reason=m.get("reason", "") or "",
                            note=m.get("note", "") or "",
                            status=m.get("status", "Pending") or "Pending",
                            split_id=int(s_id) if s_id else None
                        )
                        db.session.add(debt_obj)
                        counts["debts_created"] += 1

            db.session.commit()
            print("\n" + "=" * 70)
            print(" MIGRATION COMMITTED SUCCESSFULLY")
            print(f" Users:      {counts['users_created']} created, {counts['users_updated']} updated, {counts['users_skipped']} unchanged")
            print(f" Allowances: {counts['allowances_created']} created, {counts['allowances_updated']} updated, {counts['allowances_skipped']} unchanged")
            print(f" Rooms:      {counts['rooms_created']} created, {counts['rooms_updated']} updated, {counts['rooms_skipped']} unchanged")
            print(f" Splits:     {counts['splits_created']} created, {counts['splits_updated']} updated, {counts['splits_skipped']} unchanged")
            print(f" Expenses:   {counts['expenses_created']} created, {counts['expenses_updated']} updated, {counts['expenses_skipped']} unchanged")
            print(f" Debts:      {counts['debts_created']} created, {counts['debts_updated']} updated, {counts['debts_skipped']} unchanged")
            print("=" * 70)
            return {"success": True, "counts": counts}
            
        except Exception as ex:
            db.session.rollback()
            print(f"\nFATAL: Migration aborted and completely rolled back: {ex}")
            raise ex

def verify_migration(database_url, target_user_id=None, app_instance=None, exit_on_failure=True):
    """
    Audit database against source JSON records.
    Compares record counts, field values, amounts, dates, and ownership.
    Reports all missing, extra, and mismatched records.
    Returns True if pass, False if fail.
    Never modifies the database.
    """
    print("=" * 70)
    print(" POST-MIGRATION VERIFICATION AUDIT (READ-ONLY)")
    print("=" * 70)
    
    if not database_url and not app_instance:
        print("ERROR: DATABASE_URL is not set.")
        if exit_on_failure:
            sys.exit(1)
        return False
        
    plan = build_migration_plan(target_user_id)
    if not plan:
        print("No users found to verify.")
        if exit_on_failure:
            sys.exit(1)
        return False

    app = app_instance or create_app(database_url)
    discrepancies = []
    
    with app.app_context():
        for item in plan:
            u_dict = item["user"]
            u_id = int(u_dict["id"])
            u_name = u_dict.get("username", "Unknown")
            print(f"\nAuditing User: {u_name} (ID: {u_id})")
            
            # 1. User Account Verification
            db_user = db.session.get(User, u_id)
            if not db_user:
                discrepancies.append(f"[MISSING] User {u_id} ({u_name}) not found in database.")
                continue
            if db_user.username.lower() != u_name.lower():
                discrepancies.append(f"[MISMATCH] User {u_id}: username mismatch: JSON '{u_name}' vs DB '{db_user.username}'.")
                
            # 2. Allowance Verification
            expected_al = item["allowance"]
            expected_amt = round(float(expected_al.get("allowance", 0.0)), 2)
            expected_date = parse_date(expected_al.get("received_date"))
            
            db_al = Allowance.query.filter_by(user_id=u_id).first()
            if not db_al and expected_amt > 0:
                discrepancies.append(f"[MISSING] User {u_id}: Allowance record missing in database.")
            elif db_al:
                db_amt = round(float(db_al.amount), 2)
                if db_amt != expected_amt:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Allowance amount mismatch: JSON Rs. {expected_amt} vs DB Rs. {db_amt}")
                if db_al.received_date != expected_date:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Allowance received_date mismatch: JSON '{expected_date}' vs DB '{db_al.received_date}'")
                    
            # 3. Rooms Verification
            json_rooms = {int(r["id"]): r for r in item["rooms"]}
            db_rooms = {r.id: r for r in Room.query.filter_by(user_id=u_id).all()}
            
            missing_rooms = set(json_rooms.keys()) - set(db_rooms.keys())
            extra_rooms = set(db_rooms.keys()) - set(json_rooms.keys())
            for r_id in missing_rooms:
                discrepancies.append(f"[MISSING] User {u_id}: Room ID {r_id} missing in database.")
            for r_id in extra_rooms:
                discrepancies.append(f"[EXTRA] User {u_id}: Room ID {r_id} present in DB but not in JSON source.")
                
            for r_id in set(json_rooms.keys()) & set(db_rooms.keys()):
                jr = json_rooms[r_id]
                dr = db_rooms[r_id]
                if jr.get("name") != dr.name:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Room {r_id} name mismatch: JSON '{jr.get('name')}' vs DB '{dr.name}'")
                if sorted(jr.get("members", [])) != sorted(dr.members or []):
                    discrepancies.append(f"[MISMATCH] User {u_id}: Room {r_id} members mismatch.")

            # 4. Splits Verification
            json_splits = {int(s["id"]): s for s in item["splits"]}
            db_splits = {s.id: s for s in Split.query.filter_by(user_id=u_id).all()}
            
            missing_splits = set(json_splits.keys()) - set(db_splits.keys())
            extra_splits = set(db_splits.keys()) - set(json_splits.keys())
            for s_id in missing_splits:
                discrepancies.append(f"[MISSING] User {u_id}: Split ID {s_id} missing in database.")
            for s_id in extra_splits:
                discrepancies.append(f"[EXTRA] User {u_id}: Split ID {s_id} present in DB but not in JSON source.")
                
            for s_id in set(json_splits.keys()) & set(db_splits.keys()):
                js = json_splits[s_id]
                ds = db_splits[s_id]
                if round(float(js.get("total_amount", 0.0)), 2) != round(float(ds.total_amount), 2):
                    discrepancies.append(f"[MISMATCH] User {u_id}: Split {s_id} total_amount mismatch.")
                if parse_date(js.get("date")) != ds.date:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Split {s_id} date mismatch.")

            # 5. Expenses Verification
            json_exp = {int(e["id"]): e for e in item["expenses"]}
            db_exp = {e.id: e for e in Expense.query.filter_by(user_id=u_id).all()}
            
            missing_exp = set(json_exp.keys()) - set(db_exp.keys())
            extra_exp = set(db_exp.keys()) - set(json_exp.keys())
            for e_id in missing_exp:
                discrepancies.append(f"[MISSING] User {u_id}: Expense ID {e_id} missing in database.")
            for e_id in extra_exp:
                discrepancies.append(f"[EXTRA] User {u_id}: Expense ID {e_id} present in DB but not in JSON source.")
                
            for e_id in set(json_exp.keys()) & set(db_exp.keys()):
                je = json_exp[e_id]
                de = db_exp[e_id]
                je_amt = round(float(je.get("amount", 0.0)), 2)
                de_amt = round(float(de.amount), 2)
                if je_amt != de_amt:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Expense {e_id} amount mismatch: JSON Rs. {je_amt} vs DB Rs. {de_amt}")
                if parse_date(je.get("date")) != de.date:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Expense {e_id} date mismatch: JSON '{je.get('date')}' vs DB '{de.date}'")
                if (je.get("category") or "General") != de.category:
                    discrepancies.append(f"[MISMATCH] User {u_id}: Expense {e_id} category mismatch: JSON '{je.get('category')}' vs DB '{de.category}'")

            # 6. Money Owed Verification
            json_debt = {int(m["id"]): m for m in item["money_owed"]}
            db_debt = {m.id: m for m in MoneyOwed.query.filter_by(user_id=u_id).all()}
            
            missing_debt = set(json_debt.keys()) - set(db_debt.keys())
            extra_debt = set(db_debt.keys()) - set(json_debt.keys())
            for m_id in missing_debt:
                discrepancies.append(f"[MISSING] User {u_id}: MoneyOwed ID {m_id} missing in database.")
            for m_id in extra_debt:
                discrepancies.append(f"[EXTRA] User {u_id}: MoneyOwed ID {m_id} present in DB but not in JSON source.")
                
            for m_id in set(json_debt.keys()) & set(db_debt.keys()):
                jm = json_debt[m_id]
                dm = db_debt[m_id]
                jm_amt = round(float(jm.get("amount", 0.0)), 2)
                dm_amt = round(float(dm.amount), 2)
                if jm_amt != dm_amt:
                    discrepancies.append(f"[MISMATCH] User {u_id}: MoneyOwed {m_id} amount mismatch: JSON Rs. {jm_amt} vs DB Rs. {dm_amt}")
                if parse_date(jm.get("date")) != dm.date:
                    discrepancies.append(f"[MISMATCH] User {u_id}: MoneyOwed {m_id} date mismatch: JSON '{jm.get('date')}' vs DB '{dm.date}'")
                if jm.get("type") != dm.type:
                    discrepancies.append(f"[MISMATCH] User {u_id}: MoneyOwed {m_id} type mismatch: JSON '{jm.get('type')}' vs DB '{dm.type}'")
                if (jm.get("status") or "Pending") != dm.status:
                    discrepancies.append(f"[MISMATCH] User {u_id}: MoneyOwed {m_id} status mismatch: JSON '{jm.get('status')}' vs DB '{dm.status}'")

    print("\n" + "=" * 70)
    if discrepancies:
        print(f" VERIFICATION FAILED: {len(discrepancies)} discrepancy/discrepancies detected:")
        for disc in discrepancies:
            print(f"   {disc}")
        print("=" * 70)
        if exit_on_failure:
            sys.exit(1)
        return False
    else:
        print(" VERIFICATION PASSED: All user records, financial totals, dates, and ownership match perfectly.")
        print("=" * 70)
        if exit_on_failure:
            sys.exit(0)
        return True

def init_database(database_url):
    """Explicitly create schema tables in database without modifying or truncating existing data."""
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)
    print(f"Initializing database schema on target database...")
    app = create_app(database_url)
    with app.app_context():
        db.create_all()
        print("SUCCESS: Database schema initialized safely (tables created if not already existing).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hostelite Finance Supabase PostgreSQL Migration")
    parser.add_argument("--dry-run", action="store_true", help="Simulate migration without modifying database (default)")
    parser.add_argument("--init-db", action="store_true", help="Explicitly create database tables safely without modifying records")
    parser.add_argument("--apply", action="store_true", help="Execute the migration to Supabase")
    parser.add_argument("--verify", action="store_true", help="Audit database against JSON records")
    parser.add_argument("--database-url", type=str, default=None, help="Database connection URI override")
    parser.add_argument("--user-id", type=int, default=None, help="Migrate only a specific user ID")
    
    args = parser.parse_args()
    db_url = get_database_uri(args.database_url)
    
    if args.init_db:
        init_database(db_url)
    elif args.verify:
        verify_migration(db_url, target_user_id=args.user_id)
    elif args.apply:
        run_migration(db_url, dry_run=False, target_user_id=args.user_id)
    else:
        run_migration(db_url, dry_run=True, target_user_id=args.user_id)
