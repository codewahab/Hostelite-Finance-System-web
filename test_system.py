"""
Comprehensive Test Suite for Hostelite Finance System.
Tests all requirements across Phase 1 to Phase 6.
"""
import os
import json
import unittest
from datetime import date
from app import (
    app,
    load_expenses,
    save_expenses,
    load_money_owed,
    save_money_owed,
    load_splits,
    save_splits,
    load_rooms,
    save_rooms,
    load_allowance,
    save_allowance,
    load_users,
    save_users,
    init_user_storage,
    normalize_person_name,
    calculate_money_owed_stats,
    get_monthly_report_data
)
from pdf_generator import (
    generate_monthly_report_pdf,
    generate_expense_pdf,
    generate_split_bill_pdf
)
from unittest.mock import patch, MagicMock
from app import oauth, find_user_by_google_id, find_user_by_email

class HosteliteSystemTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["TEST_ACTIVE_USER_ID"] = 999999
        self.client = app.test_client()

        # Create temporary isolated backup copies for testing
        self.orig_users = load_users()
        self.orig_expenses = load_expenses()
        self.orig_money_owed = load_money_owed()
        self.orig_splits = load_splits()
        self.orig_rooms = load_rooms()
        self.orig_allowance = load_allowance()

        # Initialize mock authenticated user for regression suite
        init_user_storage(999999)
        with self.client.session_transaction() as sess:
            sess["user_id"] = 999999
            sess["username"] = "testuser"
            sess["display_name"] = "Test User"

    def tearDown(self):
        # Restore original state
        app.config["TEST_ACTIVE_USER_ID"] = 999999
        save_users(self.orig_users)
        save_expenses(self.orig_expenses)
        save_money_owed(self.orig_money_owed)
        save_splits(self.orig_splits)
        save_rooms(self.orig_rooms)
        save_allowance(self.orig_allowance["amount"], self.orig_allowance["received_date"])

    # ---------------------------------------------------------
    # TEST 1: Expense creation with persistent ID
    # ---------------------------------------------------------
    def test_01_expense_creation_with_id(self):
        initial_expenses = [
            {"id": 1001, "name": "Breakfast", "category": "Food", "amount": 150.0, "date": "2026-10-15", "payment_method": "Cash"}
        ]
        save_expenses(initial_expenses)

        # Quick add
        res = self.client.post("/quick-add-expense", data={
            "amount": "250",
            "category": "Mess",
            "name": "Mess Dinner",
            "payment_method": "Cash"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        expenses = load_expenses()
        self.assertEqual(len(expenses), 2)
        new_exp = expenses[-1]
        self.assertIn("id", new_exp)
        self.assertIsInstance(new_exp["id"], int)
        self.assertEqual(new_exp["name"], "Mess Dinner")
        self.assertEqual(new_exp["amount"], 250.0)

    # ---------------------------------------------------------
    # TEST 2: Expense deletion by ID from Dashboard (Bug Fix Verification)
    # ---------------------------------------------------------
    def test_02_expense_deletion_by_id_dashboard(self):
        # 3 expenses: Oldest (ID 101), Middle (ID 102), Newest (ID 103)
        sample = [
            {"id": 101, "name": "Oldest Expense", "category": "General", "amount": 10.0, "date": "2026-10-01", "payment_method": "Cash"},
            {"id": 102, "name": "Middle Expense", "category": "General", "amount": 20.0, "date": "2026-10-05", "payment_method": "Cash"},
            {"id": 103, "name": "Newest Expense", "category": "General", "amount": 30.0, "date": "2026-10-10", "payment_method": "Cash"}
        ]
        save_expenses(sample)

        # In old buggy code, deleting recent[0] deleted index 0 (Oldest Expense 101).
        # In fixed code, deleting ID 103 must delete Newest Expense 103 only!
        res = self.client.get("/delete-expense/103", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        updated = load_expenses()
        self.assertEqual(len(updated), 2)
        remaining_ids = [e["id"] for e in updated]
        self.assertIn(101, remaining_ids)
        self.assertIn(102, remaining_ids)
        self.assertNotIn(103, remaining_ids)
        self.assertEqual(updated[0]["name"], "Oldest Expense")  # Still intact!

    # ---------------------------------------------------------
    # TEST 3: Expense deletion from Expense List by ID
    # ---------------------------------------------------------
    def test_03_expense_deletion_by_id_expenses_list(self):
        sample = [
            {"id": 201, "name": "Chai", "category": "Tea", "amount": 60.0, "date": "2026-10-12", "payment_method": "Cash"},
            {"id": 202, "name": "Bus Fare", "category": "Transport", "amount": 100.0, "date": "2026-10-12", "payment_method": "Cash"}
        ]
        save_expenses(sample)

        res = self.client.get("/delete-expense/201", headers={"Referer": "/expenses"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        updated = load_expenses()
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["id"], 202)
        self.assertEqual(updated[0]["name"], "Bus Fare")

    # ---------------------------------------------------------
    # TEST 4: Expense editing by ID
    # ---------------------------------------------------------
    def test_04_expense_editing_by_id(self):
        sample = [
            {"id": 301, "name": "Initial Lunch", "category": "Food", "amount": 300.0, "date": "2026-10-10", "note": "old", "payment_method": "Cash"}
        ]
        save_expenses(sample)

        res = self.client.post("/edit-expense/301", data={
            "name": "Updated Dinner",
            "category": "Mess",
            "amount": "450.50",
            "date": "2026-10-10",
            "note": "updated note",
            "payment_method": "Easypaisa"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        updated = load_expenses()
        self.assertEqual(len(updated), 1)
        exp = updated[0]
        self.assertEqual(exp["id"], 301)
        self.assertEqual(exp["name"], "Updated Dinner")
        self.assertEqual(exp["amount"], 450.50)
        self.assertEqual(exp["payment_method"], "Easypaisa")

    # ---------------------------------------------------------
    # TEST 5: Name normalization (Case-insensitivity & whitespace deduplication)
    # ---------------------------------------------------------
    def test_05_name_normalization(self):
        records = [
            {"id": 1, "person": "Ali", "type": "they_owe_me", "amount": 500.0, "status": "Pending"}
        ]
        rooms = [
            {"id": 1, "name": "Room 204", "members": ["Saad", "Hamza"]}
        ]

        # Case variations of existing Ali should resolve to "Ali"
        self.assertEqual(normalize_person_name("ali", records, rooms), "Ali")
        self.assertEqual(normalize_person_name("  ALI  ", records, rooms), "Ali")
        self.assertEqual(normalize_person_name("aLi", records, rooms), "Ali")

        # Case variations of room member should resolve to canonical casing
        self.assertEqual(normalize_person_name("saad", records, rooms), "Saad")
        self.assertEqual(normalize_person_name("  HAMZA  ", records, rooms), "Hamza")

        # New person should be cleanly title-cased and whitespace-trimmed
        self.assertEqual(normalize_person_name("  bilal   tariq  ", records, rooms), "Bilal Tariq")

    # ---------------------------------------------------------
    # TEST 6: Money Owed statistics & Separation of both sides
    # ---------------------------------------------------------
    def test_06_money_owed_calculations(self):
        records = [
            {"id": 1, "person": "Ali", "type": "they_owe_me", "amount": 1500.0, "status": "Pending"},
            {"id": 2, "person": "ali", "type": "they_owe_me", "amount": 500.0, "status": "Pending"},
            {"id": 3, "person": "Ahmed", "type": "i_owe_them", "amount": 800.0, "status": "Pending"},
            {"id": 4, "person": "Saad", "type": "they_owe_me", "amount": 200.0, "status": "Settled"}
        ]

        stats = calculate_money_owed_stats(records)
        # They Owe You = 1500 + 500 = 2000
        self.assertEqual(stats["others_owe_you"], 2000.0)
        # You Owe Them = 800
        self.assertEqual(stats["you_owe_others"], 800.0)
        # People counts
        self.assertEqual(stats["people_who_owe_me_count"], 1)  # "Ali" and "ali" count as 1 person!
        self.assertEqual(stats["people_you_owe_count"], 1)
        self.assertEqual(stats["pending_count"], 3)
        self.assertEqual(stats["settled_count"], 1)
        self.assertEqual(stats["settled_total"], 200.0)

    # ---------------------------------------------------------
    # TEST 7: Amount validation
    # ---------------------------------------------------------
    def test_07_amount_validation(self):
        # Negative expense
        res = self.client.post("/add-expense", data={
            "name": "Invalid", "category": "Food", "amount": "-50", "date": "2026-10-15"
        })
        self.assertIn(b"valid positive amount", res.data)

        # Zero expense
        res = self.client.post("/add-expense", data={
            "name": "Invalid", "category": "Food", "amount": "0", "date": "2026-10-15"
        })
        self.assertIn(b"valid positive amount", res.data)

        # Non-numeric expense
        res = self.client.post("/add-expense", data={
            "name": "Invalid", "category": "Food", "amount": "abc", "date": "2026-10-15"
        })
        self.assertIn(b"valid positive amount", res.data)

    # ---------------------------------------------------------
    # TEST 8: Room creation and management in rooms.json
    # ---------------------------------------------------------
    def test_08_room_management(self):
        save_rooms([])

        res = self.client.post("/rooms/add", data={
            "room_name": "Room 305",
            "members": " ali, saad , HAMZA , ali "  # has whitespace, mixed cases, duplicates
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        rooms = load_rooms()
        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0]["name"], "Room 305")
        # Should deduplicate and normalize casing
        self.assertEqual(rooms[0]["members"], ["Ali", "Saad", "Hamza"])

    # ---------------------------------------------------------
    # TEST 9: Split Bill Mode A (Equal split with room)
    # ---------------------------------------------------------
    def test_09_split_bill_mode_a_room_equal(self):
        save_rooms([{"id": 1, "name": "Room 204", "members": ["Ali", "Saad"]}])
        save_money_owed([])
        save_expenses([])
        save_splits([])

        # "You" paid Rs. 3,000 for You, Ali, Saad (Rs. 1,000 each)
        res = self.client.post("/split-expense", data={
            "title": "Mess Dinner",
            "total_amount": "3000",
            "payer": "You",
            "split_mode": "room",
            "split_type": "equal",
            "date": "2026-10-17",
            "room_participants": ["You", "Ali", "Saad"],
            "sync_money_owed": "yes",
            "sync_expense": "yes",
            "expense_category": "Food",
            "save_history": "yes"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Debts in money_owed: Ali owes Rs. 1,000, Saad owes Rs. 1,000
        owed = load_money_owed()
        self.assertEqual(len(owed), 2)
        ali_debt = next((d for d in owed if d["person"] == "Ali"), None)
        saad_debt = next((d for d in owed if d["person"] == "Saad"), None)
        self.assertIsNotNone(ali_debt)
        self.assertEqual(ali_debt["amount"], 1000.0)
        self.assertEqual(ali_debt["type"], "they_owe_me")
        self.assertIsNotNone(saad_debt)
        self.assertEqual(saad_debt["amount"], 1000.0)

        # Expenses: only personal share (Rs. 1,000) recorded!
        exp = load_expenses()
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["amount"], 1000.0)
        self.assertEqual(exp[0]["name"], "Mess Dinner (My share)")

        # Splits history saved
        splits = load_splits()
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["total_amount"], 3000.0)

    # ---------------------------------------------------------
    # TEST 10: Split Bill Mode B (Custom split with other people)
    # ---------------------------------------------------------
    def test_10_split_bill_mode_b_custom(self):
        save_money_owed([])
        save_expenses([])
        save_splits([])

        # Total bill Rs. 2,000. Paid by You. Custom shares: You: 800, Ali: 1200
        res = self.client.post("/split-expense", data={
            "title": "Grocery Run",
            "total_amount": "2000",
            "payer": "You",
            "split_mode": "people",
            "split_type": "custom",
            "custom_people": "You, Ali",
            "share_You": "800",
            "share_Ali": "1200",
            "sync_money_owed": "yes",
            "sync_expense": "yes",
            "save_history": "yes"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        owed = load_money_owed()
        self.assertEqual(len(owed), 1)
        self.assertEqual(owed[0]["person"], "Ali")
        self.assertEqual(owed[0]["amount"], 1200.0)

        exp = load_expenses()
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["amount"], 800.0)

    # ---------------------------------------------------------
    # TEST 11: Split Bill where payer is EXCLUDED from participants
    # ---------------------------------------------------------
    def test_11_split_bill_payer_excluded(self):
        save_money_owed([])
        save_expenses([])
        save_splits([])

        # You paid Rs. 1,000 for Ali and Ahmed, but You did NOT participate!
        res = self.client.post("/split-expense", data={
            "title": "Medicines for Roommates",
            "total_amount": "1000",
            "payer": "You",
            "split_mode": "people",
            "split_type": "equal",
            "custom_people": "Ali, Ahmed",  # "You" is NOT in participants!
            "sync_money_owed": "yes",
            "sync_expense": "yes",
            "save_history": "yes"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Ali owes 500, Ahmed owes 500
        owed = load_money_owed()
        self.assertEqual(len(owed), 2)
        total_owed = sum(d["amount"] for d in owed)
        self.assertEqual(total_owed, 1000.0)

        # Personal expense MUST BE ZERO since user was excluded!
        exp = load_expenses()
        self.assertEqual(len(exp), 0)

    # ---------------------------------------------------------
    # TEST 12: Split cascade deletion (Preserving manual debts)
    # ---------------------------------------------------------
    def test_12_split_cascade_deletion(self):
        # Set up manual debt
        save_money_owed([
            {"id": 9999, "person": "Hamza", "type": "they_owe_me", "amount": 400.0, "status": "Pending"}
        ])
        save_expenses([])
        save_splits([])

        # Create split
        self.client.post("/split-expense", data={
            "title": "WiFi",
            "total_amount": "1200",
            "payer": "You",
            "split_mode": "people",
            "split_type": "equal",
            "custom_people": "You, Ali",
            "sync_money_owed": "yes",
            "sync_expense": "yes",
            "save_history": "yes"
        }, follow_redirects=True)

        splits = load_splits()
        self.assertEqual(len(splits), 1)
        split_id = splits[0]["id"]

        # Deleting split bill
        res = self.client.get(f"/split-expense/delete/{split_id}", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Split history is deleted
        self.assertEqual(len(load_splits()), 0)
        # Generated debt to Ali is removed, but Hamza's manual debt (ID 9999) is completely intact!
        owed = load_money_owed()
        self.assertEqual(len(owed), 1)
        self.assertEqual(owed[0]["id"], 9999)
        self.assertEqual(owed[0]["person"], "Hamza")
        # Personal expense share from WiFi is also cleanly removed
        self.assertEqual(len(load_expenses()), 0)

    # ---------------------------------------------------------
    # TEST 13: Monthly Reports, WhatsApp Text & Pure-Python PDF
    # ---------------------------------------------------------
    def test_13_reports_whatsapp_and_pdf(self):
        save_expenses([
            {"id": 1, "name": "Mess", "category": "Food", "amount": 10000.0, "date": "2026-10-05", "payment_method": "Cash"},
            {"id": 2, "name": "Snacks", "category": "Food", "amount": 2000.0, "date": "2026-10-10", "payment_method": "Easypaisa"}
        ])

        report_data = get_monthly_report_data("2026-10")
        self.assertEqual(report_data["total_spent"], 12000.0)
        self.assertEqual(report_data["expense_count"], 2)
        self.assertEqual(len(report_data["category_stats"]), 1)
        self.assertEqual(report_data["category_stats"][0]["category"], "Food")

        # Test PDF generation
        pdf_bytes = generate_monthly_report_pdf(report_data)
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-1.4"))
        self.assertTrue(b"%%EOF" in pdf_bytes)

        # Test PDF download endpoint
        res = self.client.get("/reports/download-pdf?month=2026-10")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF-1.4"))

    # ---------------------------------------------------------
    # TEST 14: Split Bill Validation Experience & Form Preservation
    # ---------------------------------------------------------
    def test_14_split_bill_validation_form_preservation(self):
        save_money_owed([])
        save_expenses([])
        save_splits([])

        # Submit custom split where sum of shares (1000 + 1500 = 2500) < total bill (3000)
        res = self.client.post("/split-expense", data={
            "title": "Room WiFi Router",
            "total_amount": "3000",
            "payer": "Ali",
            "split_mode": "people",
            "split_type": "custom",
            "date": "2026-10-18",
            "custom_people": "You, Ali",
            "share_You": "1000",
            "share_Ali": "1500",
            "sync_money_owed": "yes",
            "sync_expense": "yes",
            "save_history": "yes"
        })
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        # Validation error message with exact difference
        self.assertIn("does not equal total bill", html)
        self.assertIn("Rs. 500.00 remains unallocated", html)
        # Form values must be preserved
        self.assertIn('value="Room WiFi Router"', html)
        self.assertIn('value="3000"', html)
        self.assertIn('value="2026-10-18"', html)
        self.assertIn('value="You, Ali"', html)

        # Test over-allocated case (1500 + 2000 = 3500 > 3000)
        res_over = self.client.post("/split-expense", data={
            "title": "Room WiFi Router",
            "total_amount": "3000",
            "payer": "Ali",
            "split_mode": "people",
            "split_type": "custom",
            "date": "2026-10-18",
            "custom_people": "You, Ali",
            "share_You": "1500",
            "share_Ali": "2000"
        })
        self.assertEqual(res_over.status_code, 200)
        html_over = res_over.data.decode("utf-8")
        self.assertIn("shares exceed total bill by Rs. 500.00", html_over)

    # ---------------------------------------------------------
    # TEST 15: Low Balance Warnings based on Daily Safe Spending Limit
    # ---------------------------------------------------------
    def test_15_daily_limit_thresholds(self):
        # 1. Unconfigured allowance: no false low-balance warning
        save_allowance(0.0, "")
        save_expenses([])
        save_money_owed([])
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertNotIn("Low daily limit", html)
        self.assertNotIn("Very low daily limit", html)

        today_str = date.today().isoformat()

        # 2. Daily limit >= 300: Normal, no warning
        save_allowance(30000.0, today_str)
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertNotIn("Low daily limit", html)
        self.assertNotIn("Very low daily limit", html)

        # 3. Daily limit between 200 and 300 (e.g. 250/day): Amber low warning
        from app import get_cycle_info
        _, _, days_rem = get_cycle_info(today_str)
        target_remaining = days_rem * 250.0
        allowance_val = target_remaining + 1000.0
        save_allowance(allowance_val, today_str)
        save_expenses([
            {"id": 1, "name": "Initial Spend", "category": "Food", "amount": 1000.0, "date": today_str, "payment_method": "Cash"}
        ])
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Low daily limit", html)
        self.assertIn("available per day", html)

        # 4. Daily limit < 200: Critical warning + debtor reminder if others_owe_you > 0
        target_critical_remaining = days_rem * 100.0  # 100/day < 200
        save_allowance(target_critical_remaining + 5000.0, today_str)
        save_expenses([
            {"id": 1, "name": "Major Expense", "category": "Rent", "amount": 5000.0, "date": today_str, "payment_method": "Cash"}
        ])
        # Add a debt where Ali owes user Rs. 1500
        save_money_owed([
            {"id": 501, "person": "Ali", "type": "they_owe_me", "amount": 1500.0, "date": today_str, "status": "Pending"}
        ])
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Very low daily limit", html)
        self.assertIn("Outstanding Money to Collect", html)
        self.assertIn("Ali", html)

    # ---------------------------------------------------------
    # TEST 16: Split & Expense PDF Receipts Generation & Endpoints
    # ---------------------------------------------------------
    def test_16_split_and_expense_pdf_endpoints(self):
        # 1. Test pure-python expense PDF generator
        exp_sample = {
            "id": 8801,
            "name": "Hostel Mess Advance",
            "category": "Food",
            "amount": 4500.0,
            "date": "2026-10-19",
            "payment_method": "Cash",
            "note": "Semester mess fees"
        }
        exp_pdf = generate_expense_pdf(exp_sample, {"amount": 25000.0})
        self.assertIsInstance(exp_pdf, bytes)
        self.assertTrue(exp_pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"HOSTELITE FINANCE", exp_pdf)
        self.assertIn(b"Hostel Mess Advance", exp_pdf)

        # 2. Test expense PDF endpoints
        save_expenses([exp_sample])
        # Inline PDF
        res_view = self.client.get("/expense/pdf/8801")
        self.assertEqual(res_view.status_code, 200)
        self.assertEqual(res_view.mimetype, "application/pdf")
        self.assertIn("inline", res_view.headers.get("Content-Disposition", ""))
        # Attachment download
        res_dl = self.client.get("/expense/download-pdf/8801")
        self.assertEqual(res_dl.status_code, 200)
        self.assertEqual(res_dl.mimetype, "application/pdf")
        self.assertIn("attachment", res_dl.headers.get("Content-Disposition", ""))

        # 3. Test pure-python split bill PDF generator
        split_sample = {
            "id": 9901,
            "title": "Flat Electricity & Gas Bill",
            "total_amount": 6000.0,
            "payer": "You",
            "date": "2026-10-19",
            "participants": ["You", "Ali", "Ahmed"],
            "breakdown": [
                {"name": "You", "share": 2000.0, "is_payer": True},
                {"name": "Ali", "share": 2000.0, "is_payer": False},
                {"name": "Ahmed", "share": 2000.0, "is_payer": False}
            ],
            "who_owes_whom": [
                "Ali owes You Rs. 2,000",
                "Ahmed owes You Rs. 2,000"
            ]
        }
        split_pdf = generate_split_bill_pdf(split_sample)
        self.assertIsInstance(split_pdf, bytes)
        self.assertTrue(split_pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"HOSTELITE FINANCE", split_pdf)
        self.assertIn(b"Flat Electricity & Gas Bill", split_pdf)

        # 4. Test split PDF endpoints
        save_splits([split_sample])
        # Inline PDF
        res_split_view = self.client.get("/split-expense/pdf/9901")
        self.assertEqual(res_split_view.status_code, 200)
        self.assertEqual(res_split_view.mimetype, "application/pdf")
        self.assertIn("inline", res_split_view.headers.get("Content-Disposition", ""))
        # Attachment download
        res_split_dl = self.client.get("/split-expense/download-pdf/9901")
        self.assertEqual(res_split_dl.status_code, 200)
        self.assertEqual(res_split_dl.mimetype, "application/pdf")
        self.assertIn("attachment", res_split_dl.headers.get("Content-Disposition", ""))

    # ---------------------------------------------------------
    # TEST 17: Application Branding Standardized to Hostelite Finance
    # ---------------------------------------------------------
    def test_17_app_branding_hostelite_finance(self):
        endpoints = ["/", "/expenses", "/split-expense", "/money-owed", "/reports", "/set-allowance", "/add-expense"]
        for ep in endpoints:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 200)
            html = res.data.decode("utf-8")
            self.assertIn("Hostelite Finance", html)
            # Ensure no remnants of incorrect or old spelling
            self.assertNotIn("Hostile Finance", html)
            self.assertNotIn("Hostilite", html)

    # ---------------------------------------------------------
    # TEST 18: User Registration, Validation & Login
    # ---------------------------------------------------------
    def test_18_user_registration_and_login(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()
        # 1. Reject too short username or password
        res = c.post("/register", data={
            "username": "al",
            "display_name": "Ali",
            "password": "123",
            "confirm_password": "123"
        })
        self.assertIn(b"at least 3 characters", res.data)

        # 2. Reject mismatch passwords
        res = c.post("/register", data={
            "username": "student_ali",
            "display_name": "Ali",
            "password": "password123",
            "confirm_password": "mismatch_password"
        })
        self.assertIn(b"Passwords do not match", res.data)

        # 3. Successful registration
        res = c.post("/register", data={
            "username": "student_ali",
            "display_name": "Ali Khan",
            "password": "password123",
            "confirm_password": "password123"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Welcome, Ali Khan", res.data)

        # 4. Logout before attempting duplicate registration
        res_logout = c.get("/logout", follow_redirects=True)
        self.assertEqual(res_logout.status_code, 200)
        self.assertIn(b"logged out", res_logout.data)

        # 5. Reject duplicate username
        res_dup = c.post("/register", data={
            "username": "STUDENT_ALI",
            "display_name": "Ali Duplicate",
            "password": "password123",
            "confirm_password": "password123"
        })
        self.assertIn(b"already taken", res_dup.data)

        # 6. Login with correct credentials
        res_login = c.post("/login", data={
            "username": "student_ali",
            "password": "password123"
        }, follow_redirects=True)
        self.assertEqual(res_login.status_code, 200)
        self.assertIn(b"Welcome back", res_login.data)

        # 7. Login with invalid password
        c.get("/logout")
        res_bad = c.post("/login", data={
            "username": "student_ali",
            "password": "wrong_password"
        })
        self.assertIn(b"Invalid username or password", res_bad.data)

    # ---------------------------------------------------------
    # TEST 19: Multi-User Data Isolation
    # ---------------------------------------------------------
    def test_19_multi_user_data_isolation(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        client_a = app.test_client()
        client_b = app.test_client()

        # Register User A
        client_a.post("/register", data={
            "username": "user_a",
            "display_name": "User Alpha",
            "password": "password123",
            "confirm_password": "password123"
        }, follow_redirects=True)

        # Register User B
        client_b.post("/register", data={
            "username": "user_b",
            "display_name": "User Beta",
            "password": "password123",
            "confirm_password": "password123"
        }, follow_redirects=True)

        # User A sets allowance 20,000 and adds an expense Rs. 500
        client_a.post("/set-allowance", data={"allowance": "20000", "received_date": "2026-10-01"}, follow_redirects=True)
        client_a.post("/quick-add-expense", data={"amount": "500", "category": "Mess Alpha", "name": "Alpha Mess"}, follow_redirects=True)

        # User B sets allowance 50,000 and adds an expense Rs. 3,500
        client_b.post("/set-allowance", data={"allowance": "50000", "received_date": "2026-10-01"}, follow_redirects=True)
        client_b.post("/quick-add-expense", data={"amount": "3500", "category": "Book Beta", "name": "Beta Books"}, follow_redirects=True)

        # Check User A's expenses: has Alpha Mess, does NOT have Beta Books
        res_a = client_a.get("/expenses")
        html_a = res_a.data.decode("utf-8")
        self.assertIn("Alpha Mess", html_a)
        self.assertNotIn("Beta Books", html_a)

        # Check User B's expenses: has Beta Books, does NOT have Alpha Mess
        res_b = client_b.get("/expenses")
        html_b = res_b.data.decode("utf-8")
        self.assertIn("Beta Books", html_b)
        self.assertNotIn("Alpha Mess", html_b)

    # ---------------------------------------------------------
    # TEST 20: Unauthenticated access redirects to login
    # ---------------------------------------------------------
    def test_20_unauthenticated_access_redirects(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()  # No session
        protected_endpoints = ["/", "/expenses", "/money-owed", "/split-expense", "/reports", "/set-allowance", "/backup-data"]
        for ep in protected_endpoints:
            res = c.get(ep)
            self.assertEqual(res.status_code, 302)
            self.assertIn("/login", res.headers.get("Location", ""))

    # ---------------------------------------------------------
    # TEST 21: Data backup download
    # ---------------------------------------------------------
    def test_21_backup_data_download(self):
        app.config["TEST_ACTIVE_USER_ID"] = 999999
        init_user_storage(999999)
        with self.client.session_transaction() as sess:
            sess["user_id"] = 999999
            sess["username"] = "testuser"
            sess["display_name"] = "Test User"
        res = self.client.get("/backup-data")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/json")
        data = json.loads(res.data.decode("utf-8"))
        self.assertEqual(data["app"], "Hostelite Finance")
        self.assertEqual(data["username"], "testuser")
        self.assertIn("expenses", data)
        self.assertIn("money_owed", data)
        self.assertIn("splits", data)
        self.assertIn("rooms", data)

    # ---------------------------------------------------------
    # TEST 22: Google OAuth UI Buttons Present on Login & Register
    # ---------------------------------------------------------
    def test_22_google_oauth_buttons_rendered(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()
        res_login = c.get("/login")
        self.assertEqual(res_login.status_code, 200)
        html_login = res_login.data.decode("utf-8")
        self.assertIn('id="google-login-btn"', html_login)
        self.assertIn("Continue with Google", html_login)

        res_reg = c.get("/register")
        self.assertEqual(res_reg.status_code, 200)
        html_reg = res_reg.data.decode("utf-8")
        self.assertIn('id="google-register-btn"', html_reg)
        self.assertIn("Sign up with Google", html_reg)

    # ---------------------------------------------------------
    # TEST 23: Google OAuth Missing Configuration Handling
    # ---------------------------------------------------------
    def test_23_google_oauth_missing_config(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()

        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
            res = c.get("/login/google", follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            html = res.data.decode("utf-8")
            self.assertIn("Google Sign-In is not configured yet", html)

    # ---------------------------------------------------------
    # TEST 24: Google OAuth Cancellation and Error Handling
    # ---------------------------------------------------------
    def test_24_google_oauth_cancellation(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()

        res = c.get("/login/google/callback?error=access_denied", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Google sign-in was cancelled.", html)

    # ---------------------------------------------------------
    # TEST 25: Google OAuth New User Registration & Data Isolation
    # ---------------------------------------------------------
    def test_25_google_oauth_new_user_registration(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()

        # Seed an existing user so new user is a subsequent user
        save_users([{"id": 100, "username": "admin_student", "display_name": "Admin Student"}])

        mock_token = {
            "userinfo": {
                "sub": "mock-google-sub-777",
                "email": "student777@hostel.edu",
                "name": "Hamza Tariq",
                "email_verified": True
            }
        }

        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "dummy-id", "GOOGLE_CLIENT_SECRET": "dummy-sec"}):
            with patch.object(oauth.google, "authorize_access_token", return_value=mock_token):
                res = c.get("/login/google/callback", follow_redirects=True)
                self.assertEqual(res.status_code, 200)
                html = res.data.decode("utf-8")
                self.assertIn("Welcome to Hostelite Finance, Hamza Tariq!", html)

        # Verify user was saved to users.json
        user = find_user_by_google_id("mock-google-sub-777")
        self.assertIsNotNone(user)
        self.assertEqual(user["email"], "student777@hostel.edu")
        self.assertEqual(user["auth_provider"], "google")

        # Verify their private storage files exist and are isolated
        u_exp = load_expenses(user_id=user["id"])
        self.assertEqual(u_exp, [])

    # ---------------------------------------------------------
    # TEST 26: Google OAuth Existing User Re-login (No Data Overwrite)
    # ---------------------------------------------------------
    def test_26_google_oauth_existing_user_no_data_overwrite(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()

        # Pre-seed an existing Google user with custom financial data
        existing_google_id = "mock-google-sub-888"
        user_id = 888123
        users = load_users()
        users.append({
            "id": user_id,
            "username": "hamza_existing",
            "display_name": "Hamza Existing",
            "email": "hamza.existing@gmail.com",
            "google_id": existing_google_id,
            "auth_provider": "google",
            "created_at": "2026-10-01"
        })
        save_users(users)
        init_user_storage(user_id)

        # Add private data for this user
        save_allowance(35000.0, "2026-10-01", user_id=user_id)
        save_expenses([
            {"id": 991, "name": "Hostel Rent", "category": "Rent", "amount": 12000.0, "date": "2026-10-02", "payment_method": "Cash"}
        ], user_id=user_id)

        # Simulate Google callback re-login
        mock_token = {
            "userinfo": {
                "sub": existing_google_id,
                "email": "hamza.existing@gmail.com",
                "name": "Hamza Existing",
                "email_verified": True
            }
        }

        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "dummy-id", "GOOGLE_CLIENT_SECRET": "dummy-sec"}):
            with patch.object(oauth.google, "authorize_access_token", return_value=mock_token):
                res = c.get("/login/google/callback", follow_redirects=True)
                self.assertEqual(res.status_code, 200)
                html = res.data.decode("utf-8")
                self.assertIn("Welcome back, Hamza Existing!", html)

        # Verify existing data was strictly preserved (NOT overwritten)
        saved_allowance = load_allowance(user_id=user_id)
        self.assertEqual(saved_allowance["amount"], 35000.0)
        saved_expenses = load_expenses(user_id=user_id)
        self.assertEqual(len(saved_expenses), 1)
        self.assertEqual(saved_expenses[0]["name"], "Hostel Rent")

    # ---------------------------------------------------------
    # TEST 27: Google OAuth Email Collision (Strict No Automatic Merge)
    # ---------------------------------------------------------
    def test_27_google_oauth_email_collision_no_auto_merge(self):
        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()

        # Seed local password user with this email
        users = load_users()
        users.append({
            "id": 777999,
            "username": "victim_student",
            "display_name": "Victim Student",
            "email": "victim@hostel.edu",
            "password_hash": "hash123",
            "created_at": "2026-10-01"
        })
        save_users(users)

        # Google returns an account with the same email but a new sub
        mock_token = {
            "userinfo": {
                "sub": "attacker-or-separate-sub-555",
                "email": "victim@hostel.edu",
                "name": "Different Profile",
                "email_verified": True
            }
        }

        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "dummy-id", "GOOGLE_CLIENT_SECRET": "dummy-sec"}):
            with patch.object(oauth.google, "authorize_access_token", return_value=mock_token):
                res = c.get("/login/google/callback", follow_redirects=True)
                self.assertEqual(res.status_code, 200)
                html = res.data.decode("utf-8")
                # Automatic merging must be blocked
                self.assertIn("automatic merging is disabled", html)
                self.assertIn("Please sign in with your username and password", html)

        # Verify original user was not altered or linked
        orig_user = find_user_by_email("victim@hostel.edu")
        self.assertNotIn("google_id", orig_user)

    # ---------------------------------------------------------
    # TEST 28: Brand Logo Static Assets and Render
    # ---------------------------------------------------------
    def test_28_brand_icon_and_logo_assets(self):
        # Verify SVG logo asset exists on disk
        self.assertTrue(os.path.exists("static/images/hostelite-logo.svg"))
        self.assertTrue(os.path.exists("static/favicon.svg"))

        # Check logo image markup in navbar and login/register
        app.config["TEST_ACTIVE_USER_ID"] = 999999
        res_dash = self.client.get("/")
        html_dash = res_dash.data.decode("utf-8")
        self.assertIn("hostelite-logo.png", html_dash)
        self.assertIn("hostelite-logo.svg", html_dash)
        self.assertIn("brand-logo-img", html_dash)

        app.config["TEST_ACTIVE_USER_ID"] = None
        c = app.test_client()
        res_login = c.get("/login")
        html_login = res_login.data.decode("utf-8")
        self.assertIn("auth-logo-badge", html_login)
        self.assertIn("auth-logo-img", html_login)

        res_reg = c.get("/register")
        html_reg = res_reg.data.decode("utf-8")
        self.assertIn("auth-logo-badge", html_reg)
        self.assertIn("auth-logo-img", html_reg)

    # ---------------------------------------------------------
    # TEST 29: Mobile Navigation Markup & Hamburger Accessibility
    # ---------------------------------------------------------
    def test_29_mobile_navigation_markup(self):
        app.config["TEST_ACTIVE_USER_ID"] = 999999
        res = self.client.get("/")
        html = res.data.decode("utf-8")
        self.assertIn('id="mobile-nav-toggle"', html)
        self.assertIn('aria-label="Toggle navigation menu"', html)
        self.assertIn('id="nav-collapsible"', html)
        self.assertIn("hamburger-bar", html)

    # ---------------------------------------------------------
    # TEST 30: Apple Touch Icon & Favicon Markup in Head
    # ---------------------------------------------------------
    def test_30_apple_touch_icon_links(self):
        app.config["TEST_ACTIVE_USER_ID"] = 999999
        endpoints = ["/", "/expenses", "/money-owed", "/split-expense", "/reports", "/set-allowance"]
        for ep in endpoints:
            res = self.client.get(ep)
            html = res.data.decode("utf-8")
            self.assertIn('rel="icon"', html)
            self.assertIn('rel="apple-touch-icon"', html)



if __name__ == "__main__":
    unittest.main()
