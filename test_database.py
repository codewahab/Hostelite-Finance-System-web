"""
Hostelite Finance — Database Isolation & Schema Verification Tests
Tests multi-user data isolation, split cascades, Google ID uniqueness,
and model defaults using an isolated in-memory SQLite database.
"""

import unittest
from decimal import Decimal
from datetime import date
from flask import Flask

from unittest.mock import patch, MagicMock
from models import db, User, Allowance, Room, Split, Expense, MoneyOwed
from migrate_to_supabase import run_migration, verify_migration, build_migration_plan, validate_entity_records
import app as flask_app


class HosteliteDatabaseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create a separate Flask app exclusively for testing.
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )

        # Initialize SQLAlchemy only once on this test app.
        db.init_app(cls.app)

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()

        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    
    @classmethod
    def tearDownClass(cls):
      with cls.app.app_context():
        db.engine.dispose()


    def test_01_multi_user_data_isolation(self):
        """Verify queries filtered by user_id return the correct user's data."""

        user1 = User(
            id=1001, username="alice",
            display_name="Alice", auth_provider="local"
        )
        user2 = User(
            id=1002, username="bob",
            display_name="Bob", auth_provider="local"
        )
        db.session.add_all([user1, user2])
        db.session.commit()

        exp1 = Expense(
            id=5001, user_id=1001, name="Alice Groceries",
            category="Food", amount=Decimal("1500.00"),
            date=date(2026, 10, 20)
        )
        debt1 = MoneyOwed(
            id=6001, user_id=1001, person="Charlie",
            type="they_owe_me", amount=Decimal("500.00"),
            date=date(2026, 10, 20)
        )
        room1 = Room(
            id=7001, user_id=1001,
            name="Alice Room 101", members=["Alice", "Charlie"]
        )
        allowance1 = Allowance(
            user_id=1001, amount=Decimal("10000.00"),
            received_date=date(2026, 10, 1)
        )

        exp2 = Expense(
            id=5002, user_id=1002, name="Bob Books",
            category="Study", amount=Decimal("800.00"),
            date=date(2026, 10, 21)
        )
        debt2 = MoneyOwed(
            id=6002, user_id=1002, person="Dave",
            type="i_owe_them", amount=Decimal("300.00"),
            date=date(2026, 10, 21)
        )
        room2 = Room(
            id=7002, user_id=1002,
            name="Bob Room 202", members=["Bob", "Dave"]
        )
        allowance2 = Allowance(
            user_id=1002, amount=Decimal("8000.00"),
            received_date=date(2026, 10, 5)
        )

        db.session.add_all([
            exp1, debt1, room1, allowance1,
            exp2, debt2, room2, allowance2
        ])
        db.session.commit()

        alice_expenses = Expense.query.filter_by(user_id=1001).all()
        self.assertEqual(len(alice_expenses), 1)
        self.assertEqual(alice_expenses[0].name, "Alice Groceries")

        alice_debts = MoneyOwed.query.filter_by(user_id=1001).all()
        self.assertEqual(len(alice_debts), 1)
        self.assertEqual(alice_debts[0].person, "Charlie")

        alice_allowance = Allowance.query.filter_by(user_id=1001).first()
        self.assertEqual(float(alice_allowance.amount), 10000.00)

        bob_expenses = Expense.query.filter_by(user_id=1002).all()
        self.assertEqual(len(bob_expenses), 1)
        self.assertEqual(bob_expenses[0].name, "Bob Books")

        bob_debts = MoneyOwed.query.filter_by(user_id=1002).all()
        self.assertEqual(len(bob_debts), 1)
        self.assertEqual(bob_debts[0].person, "Dave")

        bob_allowance = Allowance.query.filter_by(user_id=1002).first()
        self.assertEqual(float(bob_allowance.amount), 8000.00)

        # Confirm Alice's filtered queries do not return Bob's records.
        self.assertNotEqual(alice_expenses[0].user_id, 1002)
        self.assertNotEqual(bob_expenses[0].user_id, 1001)

    def test_02_bill_split_transaction_and_cascade_delete(self):
        """Verify split-linked debts and expenses are deleted with the split."""

        user = User(
            id=2001, username="tariq",
            display_name="Tariq", auth_provider="local"
        )
        db.session.add(user)
        db.session.commit()

        split_id = 9001
        split = Split(
            id=split_id,
            user_id=2001,
            title="Hostel Dinner",
            total_amount=Decimal("3000.00"),
            payer="You",
            date=date(2026, 10, 22),
            split_mode="room",
            split_type="equal",
            participants=["You", "Ali", "Saad"],
            breakdown=[
                {"name": "You", "share": 1000.0, "is_payer": True},
                {"name": "Ali", "share": 1000.0, "is_payer": False},
                {"name": "Saad", "share": 1000.0, "is_payer": False},
            ],
            who_owes_whom=[
                "Ali owes You Rs. 1,000",
                "Saad owes You Rs. 1,000",
            ],
        )
        db.session.add(split)
        db.session.flush()

        debt_ali = MoneyOwed(
            id=9101, user_id=2001, person="Ali",
            type="they_owe_me", amount=Decimal("1000.00"),
            date=date(2026, 10, 22),
            reason="Hostel Dinner (Split)", split_id=split_id
        )
        debt_saad = MoneyOwed(
            id=9102, user_id=2001, person="Saad",
            type="they_owe_me", amount=Decimal("1000.00"),
            date=date(2026, 10, 22),
            reason="Hostel Dinner (Split)", split_id=split_id
        )
        exp_share = Expense(
            id=9201, user_id=2001,
            name="Hostel Dinner (My Share)",
            category="Food", amount=Decimal("1000.00"),
            date=date(2026, 10, 22), split_id=split_id
        )

        db.session.add_all([debt_ali, debt_saad, exp_share])
        db.session.commit()

        self.assertEqual(Split.query.filter_by(user_id=2001).count(), 1)
        self.assertEqual(MoneyOwed.query.filter_by(split_id=split_id).count(), 2)
        self.assertEqual(Expense.query.filter_by(split_id=split_id).count(), 1)

        db.session.delete(split)
        db.session.commit()

        self.assertEqual(Split.query.filter_by(user_id=2001).count(), 0)
        self.assertEqual(MoneyOwed.query.filter_by(split_id=split_id).count(), 0)
        self.assertEqual(Expense.query.filter_by(split_id=split_id).count(), 0)

    def test_03_google_oauth_sub_mapping(self):
        """Verify Google ID lookup and uniqueness."""

        google_sub = "google-sub-xyz-98765"
        user = User(
            id=3001,
            username="google_user",
            display_name="Google Student",
            email="student@google.com",
            email_verified=True,
            google_id=google_sub,
            auth_provider="google",
        )
        db.session.add(user)
        db.session.commit()

        found = User.query.filter_by(google_id=google_sub).first()
        self.assertIsNotNone(found)
        self.assertEqual(found.username, "google_user")
        self.assertEqual(found.email, "student@google.com")
        self.assertEqual(found.auth_provider, "google")

        dup_user = User(
            id=3002,
            username="another_user",
            display_name="Another",
            google_id=google_sub,
            auth_provider="google",
        )
        db.session.add(dup_user)

        with self.assertRaises(Exception):
            db.session.commit()

        db.session.rollback()

    def test_04_missing_optional_fields_and_defaults(self):
        """Verify expense defaults and optional split reference."""

        user = User(
            id=4001, username="tester",
            display_name="Tester", auth_provider="local"
        )
        db.session.add(user)
        db.session.commit()

        exp = Expense(
            id=4101,
            user_id=4001,
            name="Tea",
            category="Beverage",
            amount=Decimal("50.00"),
            date=date(2026, 10, 23),
        )
        db.session.add(exp)
        db.session.commit()

        loaded = db.session.get(Expense, 4101)
        self.assertEqual(loaded.note, "")
        self.assertEqual(loaded.payment_method, "Cash")
        self.assertIsNone(loaded.split_id)

        data = loaded.to_dict()
        self.assertEqual(data["amount"], 50.00)
        self.assertNotIn("split_id", data)

    def test_05_cross_user_id_collision_prevention(self):
        """Verify cross-user ID collision aborts and rolls back to prevent overwriting another user's records."""
        user1 = User(id=5001, username="user_one", display_name="User One", auth_provider="local")
        user2 = User(id=5002, username="user_two", display_name="User Two", auth_provider="local")
        db.session.add_all([user1, user2])
        db.session.commit()

        # Add expense 9001 for User 1
        exp1 = Expense(id=9001, user_id=5001, name="Lunch", category="Food", amount=Decimal("200.00"), date=date(2026, 10, 25))
        db.session.add(exp1)
        db.session.commit()

        # In migration: if User 2 tries to import an expense with ID 9001, it must abort with ValueError and rollback
        colliding_plan = [{
            "user": {"id": 5002, "username": "user_two", "display_name": "User Two"},
            "source_reason": "Test plan",
            "expenses": [{"id": 9001, "name": "Hijacked Expense", "category": "Food", "amount": 500.0, "date": "2026-10-25"}],
            "allowance": {"allowance": 0.0, "received_date": ""},
            "money_owed": [],
            "rooms": [],
            "splits": [],
            "stats": {"expense_count": 1, "expense_total": 500.0, "allowance": 0.0, "debt_count": 0, "they_owe_me": 0, "i_owe_them": 0, "room_count": 0, "split_count": 0}
        }]

        with patch("migrate_to_supabase.build_migration_plan", return_value=colliding_plan):
            with self.assertRaises(ValueError) as ctx:
                run_migration(database_url=None, dry_run=False, app_instance=self.app)
            self.assertIn("Cross-user ID collision", str(ctx.exception))

        # Verify original record owned by user 1 was preserved untouched
        preserved = db.session.get(Expense, 9001)
        self.assertEqual(preserved.user_id, 5001)
        self.assertEqual(preserved.name, "Lunch")
        self.assertEqual(float(preserved.amount), 200.00)

    def test_06_duplicate_migration_handling(self):
        """Verify repeated migration runs handle existing records consistently without creating duplicates."""
        user_plan = [{
            "user": {"id": 6001, "username": "repeat_user", "display_name": "Repeat User", "auth_provider": "local"},
            "source_reason": "Test plan",
            "expenses": [{"id": 9101, "name": "Books", "category": "Study", "amount": 1000.0, "date": "2026-10-25"}],
            "allowance": {"allowance": 5000.0, "received_date": "2026-10-01"},
            "money_owed": [{"id": 9201, "person": "Farhan", "type": "they_owe_me", "amount": 300.0, "date": "2026-10-25", "status": "Pending"}],
            "rooms": [{"id": 9301, "name": "Room 10", "members": ["Farhan"]}],
            "splits": [],
            "stats": {"expense_count": 1, "expense_total": 1000.0, "allowance": 5000.0, "debt_count": 1, "they_owe_me": 300.0, "i_owe_them": 0.0, "room_count": 1, "split_count": 0}
        }]

        # Run 1: initial migration
        with patch("migrate_to_supabase.build_migration_plan", return_value=user_plan):
            res1 = run_migration(database_url=None, dry_run=False, app_instance=self.app)
            self.assertEqual(res1["counts"]["users_created"], 1)
            self.assertEqual(res1["counts"]["expenses_created"], 1)
            self.assertEqual(res1["counts"]["debts_created"], 1)
            self.assertEqual(res1["counts"]["rooms_created"], 1)

        # Total rows check
        self.assertEqual(Expense.query.filter_by(user_id=6001).count(), 1)
        self.assertEqual(MoneyOwed.query.filter_by(user_id=6001).count(), 1)

        # Run 2: duplicate migration attempt with exact same data
        with patch("migrate_to_supabase.build_migration_plan", return_value=user_plan):
            res2 = run_migration(database_url=None, dry_run=False, app_instance=self.app)
            self.assertEqual(res2["counts"]["users_created"], 0)
            self.assertEqual(res2["counts"]["users_skipped"], 1)
            self.assertEqual(res2["counts"]["expenses_created"], 0)
            self.assertEqual(res2["counts"]["expenses_skipped"], 1)
            self.assertEqual(res2["counts"]["debts_created"], 0)
            self.assertEqual(res2["counts"]["debts_skipped"], 1)

        # Total rows check: still exactly 1, no duplicate rows created
        self.assertEqual(Expense.query.filter_by(user_id=6001).count(), 1)
        self.assertEqual(MoneyOwed.query.filter_by(user_id=6001).count(), 1)

    def test_07_verification_detects_missing_records(self):
        """Verify that --verify reports missing records and returns False."""
        user_plan = [{
            "user": {"id": 7001, "username": "missing_test_user", "display_name": "Test User"},
            "source_reason": "Test plan",
            "expenses": [{"id": 9501, "name": "Bus Fare", "category": "Travel", "amount": 100.0, "date": "2026-10-25"}],
            "allowance": {"allowance": 0.0, "received_date": ""},
            "money_owed": [],
            "rooms": [],
            "splits": [],
            "stats": {"expense_count": 1, "expense_total": 100.0, "allowance": 0.0, "debt_count": 0, "they_owe_me": 0, "i_owe_them": 0, "room_count": 0, "split_count": 0}
        }]

        # Insert user into DB, but deliberately omit the expense
        user = User(id=7001, username="missing_test_user", display_name="Test User", auth_provider="local")
        db.session.add(user)
        db.session.commit()

        with patch("migrate_to_supabase.build_migration_plan", return_value=user_plan):
            result = verify_migration(database_url=None, app_instance=self.app, exit_on_failure=False)
            self.assertFalse(result, "Verification should fail when records are missing in the database.")

    def test_08_verification_detects_mismatched_amounts_and_dates(self):
        """Verify that --verify reports discrepancies when amounts or dates mismatch."""
        user_plan = [{
            "user": {"id": 8001, "username": "mismatch_user", "display_name": "Mismatch User"},
            "source_reason": "Test plan",
            "expenses": [{"id": 9601, "name": "Dinner", "category": "Food", "amount": 1500.0, "date": "2026-10-25"}],
            "allowance": {"allowance": 5000.0, "received_date": "2026-10-01"},
            "money_owed": [],
            "rooms": [],
            "splits": [],
            "stats": {"expense_count": 1, "expense_total": 1500.0, "allowance": 5000.0, "debt_count": 0, "they_owe_me": 0, "i_owe_them": 0, "room_count": 0, "split_count": 0}
        }]

        user = User(id=8001, username="mismatch_user", display_name="Mismatch User", auth_provider="local")
        # In DB: amount is 999.00 instead of 1500.00
        exp = Expense(id=9601, user_id=8001, name="Dinner", category="Food", amount=Decimal("999.00"), date=date(2026, 10, 25))
        db.session.add_all([user, exp])
        db.session.commit()

        with patch("migrate_to_supabase.build_migration_plan", return_value=user_plan):
            result = verify_migration(database_url=None, app_instance=self.app, exit_on_failure=False)
            self.assertFalse(result, "Verification should fail when amounts or dates mismatch.")

    def test_09_verification_detects_extra_records(self):
        """Verify that --verify reports unexpected extra records in the database."""
        user_plan = [{
            "user": {"id": 8501, "username": "extra_test_user", "display_name": "Extra Test User"},
            "source_reason": "Test plan",
            "expenses": [],
            "allowance": {"allowance": 0.0, "received_date": ""},
            "money_owed": [],
            "rooms": [],
            "splits": [],
            "stats": {"expense_count": 0, "expense_total": 0.0, "allowance": 0.0, "debt_count": 0, "they_owe_me": 0, "i_owe_them": 0, "room_count": 0, "split_count": 0}
        }]

        user = User(id=8501, username="extra_test_user", display_name="Extra Test User", auth_provider="local")
        # DB has an extra expense not present in the plan
        extra_exp = Expense(id=9701, user_id=8501, name="Ghost Expense", category="Other", amount=Decimal("300.00"), date=date(2026, 10, 25))
        db.session.add_all([user, extra_exp])
        db.session.commit()

        with patch("migrate_to_supabase.build_migration_plan", return_value=user_plan):
            result = verify_migration(database_url=None, app_instance=self.app, exit_on_failure=False)
            self.assertFalse(result, "Verification should fail when extra unexpected records exist in the database.")

    def test_10_dry_run_non_modification(self):
        """Verify that dry-run performs zero writes and modifies no database tables."""
        # Start with empty database
        self.assertEqual(User.query.count(), 0)
        self.assertEqual(Expense.query.count(), 0)

        # Execute dry-run with mock plan
        mock_plan = [{
            "user": {"id": 9901, "username": "dry_run_user", "display_name": "Dry Run User"},
            "source_reason": "Dry run test",
            "expenses": [{"id": 9801, "name": "Stationery", "category": "Study", "amount": 250.0, "date": "2026-10-25"}],
            "allowance": {"allowance": 3000.0, "received_date": "2026-10-01"},
            "money_owed": [],
            "rooms": [],
            "splits": [],
            "stats": {"expense_count": 1, "expense_total": 250.0, "allowance": 3000.0, "debt_count": 0, "they_owe_me": 0, "i_owe_them": 0, "room_count": 0, "split_count": 0}
        }]

        with patch("migrate_to_supabase.build_migration_plan", return_value=mock_plan):
            res = run_migration(database_url=None, dry_run=True, app_instance=self.app)
            self.assertTrue(res["dry_run"])

        # Confirm 0 records were created
        self.assertEqual(User.query.count(), 0)
        self.assertEqual(Expense.query.count(), 0)
        self.assertEqual(Allowance.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
