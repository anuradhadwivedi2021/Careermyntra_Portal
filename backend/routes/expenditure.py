"""
routes/expenditure.py
----------------------
Flask Blueprint for the Expenditure Module.

Covers: Persons, Expense Types, Expenses (incl. recurring auto-split
and shared-expense split), Settlements, Dashboard cards, and
balance / outstanding ("who owes whom") reports.

Matches the project's existing blueprint pattern (see main.py):
url_prefix="/api" is applied at registration time, not inside the
blueprint itself.

Register in main.py:
    from routes.expenditure import expenditure_bp
    app.register_blueprint(expenditure_bp, url_prefix="/api")
"""

import uuid
from datetime import date
from calendar import monthrange

from flask import Blueprint, request, jsonify
from psycopg2.extras import RealDictCursor

from db import get_connection

expenditure_bp = Blueprint("expenditure_bp", __name__)

# period_months = length of one generated row's period (used for labeling/stepping)
# divide = whether the entered amount is the TOTAL to be split across periods
#          (True) or the full amount repeated every period (False)
RECURRING_CONFIG = {
    "Monthly":    {"period_months": 1, "divide": False},  # amount repeats every month
    "Quarterly":  {"period_months": 3, "divide": True},   # amount is annual total -> /4 quarters
    "Six Months": {"period_months": 6, "divide": True},   # amount is annual total -> /2 halves
    "Yearly":     {"period_months": 1, "divide": True},   # amount is annual total -> /12 months
}


# =====================================================================
# Helpers
# =====================================================================

def _first_of_month(d):
    return date(d.year, d.month, 1)


def _add_months(d, n):
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def _month_range(start, end):
    """Yield first-of-month dates from start to end inclusive."""
    cur = _first_of_month(start)
    end = _first_of_month(end)
    while cur <= end:
        yield cur
        cur = _add_months(cur, 1)


def _generate_recurring_periods(recurring_type, start_month, end_month, total_amount):
    """
    Split total_amount into periods based on recurring_type.
    Returns list of (period_start_month_date, amount) tuples.

    If end_month is not given, defaults to a 1-year span from start_month.

    Per spec example: Yearly total of 12000 -> 12 monthly rows of 1000 each
    (amount divided across months). Monthly recurring instead repeats the
    FULL amount every month (e.g. rent of 5000/month for N months) — it is
    not divided, since the entered amount already represents one month.
    """
    config = RECURRING_CONFIG.get(recurring_type, {"period_months": 1, "divide": False})
    period_len = config["period_months"]
    divide = config["divide"]

    if not end_month:
        end_month = _add_months(start_month, 11)  # default: 1 year span

    all_months = list(_month_range(start_month, end_month))
    period_starts = all_months[::period_len]
    num_periods = max(len(period_starts), 1)

    if not divide:
        # Monthly: full amount repeats every period
        return [(p_start, round(float(total_amount), 2)) for p_start in period_starts]

    base_amount = round(float(total_amount) / num_periods, 2)
    periods = []
    running_total = 0
    for i, p_start in enumerate(period_starts):
        if i == len(period_starts) - 1:
            # last period absorbs rounding remainder
            amt = round(float(total_amount) - running_total, 2)
        else:
            amt = base_amount
            running_total += amt
        periods.append((p_start, amt))
    return periods


def _compute_splits(split_type, amount, persons_payload):
    """
    persons_payload:
      Equal  -> {"person_ids": [1,2,3]}
      Custom -> {"splits": [{"person_id":1,"amount":2000}, ...]}
    Returns list of (person_id, share_amount), raises ValueError on mismatch.
    """
    if split_type == "Equal":
        person_ids = persons_payload.get("person_ids", [])
        if not person_ids:
            raise ValueError("At least one person required for equal split")
        n = len(person_ids)
        base = round(float(amount) / n, 2)
        splits = []
        running = 0
        for i, pid in enumerate(person_ids):
            if i == n - 1:
                share = round(float(amount) - running, 2)
            else:
                share = base
                running += share
            splits.append((pid, share))
        return splits

    elif split_type == "Custom":
        rows = persons_payload.get("splits", [])
        if not rows:
            raise ValueError("Custom split rows required")
        total = round(sum(float(r["amount"]) for r in rows), 2)
        if abs(total - round(float(amount), 2)) > 0.01:
            raise ValueError(
                f"Custom split total ({total}) does not match expense amount ({amount})"
            )
        return [(r["person_id"], round(float(r["amount"]), 2)) for r in rows]

    raise ValueError("split_type must be 'Equal' or 'Custom'")


def _simplify_debts(net_balances):
    """
    Greedy min-transaction debt simplification.
    net_balances: {person_id: net_amount}  (+ve = should receive, -ve = should pay)
    Returns list of {"from": pid, "to": pid, "amount": x}
    """
    creditors = [[pid, amt] for pid, amt in net_balances.items() if amt > 0.01]
    debtors = [[pid, -amt] for pid, amt in net_balances.items() if amt < -0.01]
    creditors.sort(key=lambda x: -x[1])
    debtors.sort(key=lambda x: -x[1])

    txns = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        d_pid, d_amt = debtors[i]
        c_pid, c_amt = creditors[j]
        settle = round(min(d_amt, c_amt), 2)
        if settle > 0.01:
            txns.append({"from_person_id": d_pid, "to_person_id": c_pid, "amount": settle})
        debtors[i][1] -= settle
        creditors[j][1] -= settle
        if debtors[i][1] <= 0.01:
            i += 1
        if creditors[j][1] <= 0.01:
            j += 1
    return txns


# =====================================================================
# 1. Persons
# =====================================================================

@expenditure_bp.route("/expenditure/persons", methods=["GET"])
def get_persons():
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM expenditure_persons ORDER BY name")
        return jsonify({"success": True, "persons": cur.fetchall()})
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/persons", methods=["POST"])
def add_person():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """INSERT INTO expenditure_persons (name, mobile_number, status)
               VALUES (%s, %s, %s) RETURNING *""",
            (name, data.get("mobile_number"), data.get("status", "Active")),
        )
        conn.commit()
        return jsonify({"success": True, "person": cur.fetchone()})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/persons/<int:person_id>", methods=["PUT"])
def update_person(person_id):
    data = request.get_json(force=True)
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE expenditure_persons
               SET name = %s, mobile_number = %s, status = %s, updated_at = NOW()
               WHERE person_id = %s RETURNING *""",
            (data.get("name"), data.get("mobile_number"), data.get("status"), person_id),
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Person not found"}), 404
        return jsonify({"success": True, "person": row})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/persons/<int:person_id>", methods=["DELETE"])
def delete_person(person_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM expenditure_persons WHERE person_id = %s", (person_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# =====================================================================
# 2. Expense Types
# =====================================================================

@expenditure_bp.route("/expenditure/expense-types", methods=["GET"])
def get_expense_types():
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM expenditure_expense_types ORDER BY expense_type_name")
        return jsonify({"success": True, "expense_types": cur.fetchall()})
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/expense-types", methods=["POST"])
def add_expense_type():
    data = request.get_json(force=True)
    name = (data.get("expense_type_name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "expense_type_name is required"}), 400
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """INSERT INTO expenditure_expense_types (expense_type_name)
               VALUES (%s) ON CONFLICT (expense_type_name) DO NOTHING RETURNING *""",
            (name,),
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Expense type already exists"}), 409
        return jsonify({"success": True, "expense_type": row})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/expense-types/<int:type_id>", methods=["PUT"])
def update_expense_type(type_id):
    data = request.get_json(force=True)
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE expenditure_expense_types SET expense_type_name = %s
               WHERE expense_type_id = %s RETURNING *""",
            (data.get("expense_type_name"), type_id),
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Not found"}), 404
        return jsonify({"success": True, "expense_type": row})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/expense-types/<int:type_id>", methods=["DELETE"])
def delete_expense_type(type_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM expenditure_expense_types WHERE expense_type_id = %s", (type_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# =====================================================================
# 3. Expenses  (create handles recurring explosion + split creation)
# =====================================================================

@expenditure_bp.route("/expenditure/expenses", methods=["GET"])
def get_expenses():
    """Filters: category, expense_type_id, person_id, month(YYYY-MM), year,
    quarter(1-4), six_month(H1/H2), recurring_type, search"""
    args = request.args
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        where, params = [], []

        if args.get("category"):
            where.append("e.expense_category = %s")
            params.append(args["category"])
        if args.get("expense_type_id"):
            where.append("e.expense_type_id = %s")
            params.append(args["expense_type_id"])
        if args.get("person_id"):
            where.append("e.paid_by_person_id = %s")
            params.append(args["person_id"])
        if args.get("recurring_type"):
            where.append("e.recurring_type = %s")
            params.append(args["recurring_type"])
        if args.get("month"):  # YYYY-MM
            where.append("to_char(e.expense_month, 'YYYY-MM') = %s")
            params.append(args["month"])
        if args.get("year"):
            where.append("EXTRACT(YEAR FROM e.expense_month) = %s")
            params.append(args["year"])
        if args.get("quarter"):
            q = int(args["quarter"])
            where.append("EXTRACT(QUARTER FROM e.expense_month) = %s")
            params.append(q)
        if args.get("six_month"):
            if args["six_month"] == "H1":
                where.append("EXTRACT(MONTH FROM e.expense_month) BETWEEN 1 AND 6")
            else:
                where.append("EXTRACT(MONTH FROM e.expense_month) BETWEEN 7 AND 12")
        if args.get("search"):
            where.append("e.expense_name ILIKE %s")
            params.append(f"%{args['search']}%")

        sql = """
            SELECT e.*, et.expense_type_name, p.name AS paid_by_name
            FROM expenditure_expenses e
            LEFT JOIN expenditure_expense_types et ON et.expense_type_id = e.expense_type_id
            LEFT JOIN expenditure_persons p ON p.person_id = e.paid_by_person_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.expense_month DESC, e.expense_id DESC"

        cur.execute(sql, params)
        expenses = cur.fetchall()

        # attach splits
        if expenses:
            ids = [e["expense_id"] for e in expenses]
            cur.execute(
                """SELECT s.*, p.name AS person_name FROM expenditure_expense_splits s
                   JOIN expenditure_persons p ON p.person_id = s.person_id
                   WHERE s.expense_id = ANY(%s)""",
                (ids,),
            )
            splits_by_expense = {}
            for s in cur.fetchall():
                splits_by_expense.setdefault(s["expense_id"], []).append(s)
            for e in expenses:
                e["splits"] = splits_by_expense.get(e["expense_id"], [])

        return jsonify({"success": True, "expenses": expenses})
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/expenses", methods=["POST"])
def add_expense():
    """
    Body:
    {
      "expense_name", "expense_type_id", "expense_category" (Office/Individual),
      "amount", "paid_by_person_id", "paid_date" (YYYY-MM-DD),
      "expense_month" (YYYY-MM-DD, any day of target month),
      "description", "attachment_path",
      "is_recurring": bool,
      "recurring_type": "Monthly|Quarterly|Six Months|Yearly",
      "recurring_start_month": "YYYY-MM-DD",
      "recurring_end_month": "YYYY-MM-DD" (optional),
      "is_split_expense": bool,
      "split_type": "Equal|Custom",
      "split_payload": { "person_ids":[...] }  or  { "splits":[{"person_id":x,"amount":y}] }
    }
    """
    data = request.get_json(force=True)
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        is_recurring = bool(data.get("is_recurring"))
        is_split = bool(data.get("is_split_expense"))
        amount = float(data["amount"])
        created_expenses = []

        if is_recurring:
            recurring_type = data["recurring_type"]
            start_month = date.fromisoformat(data["recurring_start_month"])
            end_month = (
                date.fromisoformat(data["recurring_end_month"])
                if data.get("recurring_end_month")
                else None
            )
            batch_id = str(uuid.uuid4())
            periods = _generate_recurring_periods(recurring_type, start_month, end_month, amount)

            for period_month, period_amount in periods:
                cur.execute(
                    """INSERT INTO expenditure_expenses
                       (expense_name, expense_category, expense_type_id, amount,
                        paid_by_person_id, paid_date, expense_month, is_recurring,
                        recurring_type, recurring_batch_id, recurring_start_month,
                        recurring_end_month, is_split_expense, split_type,
                        description, attachment_path)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING *""",
                    (
                        data["expense_name"], data["expense_category"], data.get("expense_type_id"),
                        period_amount, data["paid_by_person_id"], data.get("paid_date", str(period_month)),
                        period_month, recurring_type, batch_id, start_month, end_month,
                        is_split, data.get("split_type"), data.get("description"),
                        data.get("attachment_path"),
                    ),
                )
                new_expense = cur.fetchone()

                if is_split:
                    splits = _compute_splits(data["split_type"], period_amount, data["split_payload"])
                    for pid, share in splits:
                        cur.execute(
                            """INSERT INTO expenditure_expense_splits
                               (expense_id, person_id, share_amount) VALUES (%s,%s,%s)""",
                            (new_expense["expense_id"], pid, share),
                        )
                created_expenses.append(new_expense)

        else:
            expense_month = date.fromisoformat(data["expense_month"])
            cur.execute(
                """INSERT INTO expenditure_expenses
                   (expense_name, expense_category, expense_type_id, amount,
                    paid_by_person_id, paid_date, expense_month, is_recurring,
                    is_split_expense, split_type, description, attachment_path)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    data["expense_name"], data["expense_category"], data.get("expense_type_id"),
                    amount, data["paid_by_person_id"], data["paid_date"], expense_month,
                    is_split, data.get("split_type"), data.get("description"),
                    data.get("attachment_path"),
                ),
            )
            new_expense = cur.fetchone()

            if is_split:
                splits = _compute_splits(data["split_type"], amount, data["split_payload"])
                for pid, share in splits:
                    cur.execute(
                        """INSERT INTO expenditure_expense_splits
                           (expense_id, person_id, share_amount) VALUES (%s,%s,%s)""",
                        (new_expense["expense_id"], pid, share),
                    )
            created_expenses.append(new_expense)

        conn.commit()
        return jsonify({"success": True, "expenses": created_expenses})

    except ValueError as ve:
        conn.rollback()
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/expenses/<int:expense_id>", methods=["PUT"])
def update_expense(expense_id):
    """Edits a single expense row (does not re-explode recurring periods)."""
    data = request.get_json(force=True)
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE expenditure_expenses SET
               expense_name = %s, expense_category = %s, expense_type_id = %s,
               amount = %s, paid_by_person_id = %s, paid_date = %s,
               description = %s, updated_at = NOW()
               WHERE expense_id = %s RETURNING *""",
            (
                data.get("expense_name"), data.get("expense_category"), data.get("expense_type_id"),
                data.get("amount"), data.get("paid_by_person_id"), data.get("paid_date"),
                data.get("description"), expense_id,
            ),
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Expense not found"}), 404

        # optional: replace splits if provided
        if "split_payload" in data and data.get("is_split_expense"):
            cur.execute("DELETE FROM expenditure_expense_splits WHERE expense_id = %s", (expense_id,))
            splits = _compute_splits(data["split_type"], row["amount"], data["split_payload"])
            for pid, share in splits:
                cur.execute(
                    """INSERT INTO expenditure_expense_splits
                       (expense_id, person_id, share_amount) VALUES (%s,%s,%s)""",
                    (expense_id, pid, share),
                )
            conn.commit()

        return jsonify({"success": True, "expense": row})
    except ValueError as ve:
        conn.rollback()
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    """Pass ?scope=batch to delete every period of the same recurring entry."""
    scope = request.args.get("scope", "single")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if scope == "batch":
            cur.execute(
                "SELECT recurring_batch_id FROM expenditure_expenses WHERE expense_id = %s",
                (expense_id,),
            )
            row = cur.fetchone()
            if row and row["recurring_batch_id"]:
                cur.execute(
                    "DELETE FROM expenditure_expenses WHERE recurring_batch_id = %s",
                    (row["recurring_batch_id"],),
                )
            else:
                cur.execute("DELETE FROM expenditure_expenses WHERE expense_id = %s", (expense_id,))
        else:
            cur.execute("DELETE FROM expenditure_expenses WHERE expense_id = %s", (expense_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# =====================================================================
# 4. Settlements
# =====================================================================

@expenditure_bp.route("/expenditure/settlements", methods=["GET"])
def get_settlements():
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT s.*, fp.name AS from_name, tp.name AS to_name
               FROM expenditure_settlements s
               JOIN expenditure_persons fp ON fp.person_id = s.from_person_id
               JOIN expenditure_persons tp ON tp.person_id = s.to_person_id
               ORDER BY s.payment_date DESC, s.settlement_id DESC"""
        )
        return jsonify({"success": True, "settlements": cur.fetchall()})
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/settlements", methods=["POST"])
def add_settlement():
    data = request.get_json(force=True)
    if data.get("from_person_id") == data.get("to_person_id"):
        return jsonify({"success": False, "error": "From and To person cannot be the same"}), 400
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """INSERT INTO expenditure_settlements
               (from_person_id, to_person_id, amount, payment_date, payment_mode,
                reference_number, remarks)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                data["from_person_id"], data["to_person_id"], data["amount"],
                data["payment_date"], data["payment_mode"],
                data.get("reference_number"), data.get("remarks"),
            ),
        )
        conn.commit()
        return jsonify({"success": True, "settlement": cur.fetchone()})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/settlements/<int:settlement_id>", methods=["PUT"])
def update_settlement(settlement_id):
    data = request.get_json(force=True)
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE expenditure_settlements SET
               from_person_id=%s, to_person_id=%s, amount=%s, payment_date=%s,
               payment_mode=%s, reference_number=%s, remarks=%s
               WHERE settlement_id = %s RETURNING *""",
            (
                data.get("from_person_id"), data.get("to_person_id"), data.get("amount"),
                data.get("payment_date"), data.get("payment_mode"),
                data.get("reference_number"), data.get("remarks"), settlement_id,
            ),
        )
        conn.commit()
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Not found"}), 404
        return jsonify({"success": True, "settlement": row})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/settlements/<int:settlement_id>", methods=["DELETE"])
def delete_settlement(settlement_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM expenditure_settlements WHERE settlement_id = %s", (settlement_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# =====================================================================
# 5. Dashboard
# =====================================================================

@expenditure_bp.route("/expenditure/dashboard", methods=["GET"])
def dashboard():
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenditure_expenses")
        total_expenses = cur.fetchone()["total"]

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenditure_expenses WHERE expense_category='Office'"
        )
        office_expenses = cur.fetchone()["total"]

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenditure_expenses WHERE expense_category='Individual'"
        )
        individual_expenses = cur.fetchone()["total"]

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenditure_expenses WHERE is_split_expense = TRUE"
        )
        shared_expenses = cur.fetchone()["total"]

        cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenditure_settlements")
        total_settled = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS c FROM expenditure_persons WHERE status='Active'")
        active_members = cur.fetchone()["c"]

        balances = _get_net_balances(cur)
        outstanding_total = round(sum(b for b in balances.values() if b < 0) * -1, 2)
        pending_settlements = len(_simplify_debts(balances))

        return jsonify({
            "success": True,
            "cards": {
                "total_expenses": total_expenses,
                "office_expenses": office_expenses,
                "individual_expenses": individual_expenses,
                "shared_expenses": shared_expenses,
                "total_paid_settlements": total_settled,
                "outstanding_amount": outstanding_total,
                "pending_settlements": pending_settlements,
                "active_members": active_members,
            },
        })
    finally:
        conn.close()


# =====================================================================
# 6. Reports
# =====================================================================

def _get_net_balances(cur):
    """
    Net Balance = Total Paid − Total Shared Expense (own share)
                  − Total Settlements Paid + Total Settlements Received
    Returns {person_id: net_balance}
    """
    cur.execute("SELECT person_id FROM expenditure_persons")
    person_ids = [r["person_id"] for r in cur.fetchall()]
    balances = {pid: 0.0 for pid in person_ids}

    # Total paid (only for split/shared expenses — payer fronted the money)
    cur.execute(
        """SELECT paid_by_person_id AS pid, COALESCE(SUM(amount),0) AS total
           FROM expenditure_expenses WHERE is_split_expense = TRUE
           GROUP BY paid_by_person_id"""
    )
    for r in cur.fetchall():
        balances[r["pid"]] = balances.get(r["pid"], 0) + float(r["total"])

    # Total share owed (from splits, for shared expenses only)
    cur.execute(
        """SELECT s.person_id AS pid, COALESCE(SUM(s.share_amount),0) AS total
           FROM expenditure_expense_splits s
           JOIN expenditure_expenses e ON e.expense_id = s.expense_id
           WHERE e.is_split_expense = TRUE
           GROUP BY s.person_id"""
    )
    for r in cur.fetchall():
        balances[r["pid"]] = balances.get(r["pid"], 0) - float(r["total"])

    # Settlements paid (reduces balance)
    cur.execute(
        """SELECT from_person_id AS pid, COALESCE(SUM(amount),0) AS total
           FROM expenditure_settlements GROUP BY from_person_id"""
    )
    for r in cur.fetchall():
        balances[r["pid"]] = balances.get(r["pid"], 0) - float(r["total"])

    # Settlements received (increases balance)
    cur.execute(
        """SELECT to_person_id AS pid, COALESCE(SUM(amount),0) AS total
           FROM expenditure_settlements GROUP BY to_person_id"""
    )
    for r in cur.fetchall():
        balances[r["pid"]] = balances.get(r["pid"], 0) + float(r["total"])

    return {pid: round(bal, 2) for pid, bal in balances.items()}


@expenditure_bp.route("/expenditure/reports/person-wise", methods=["GET"])
def person_wise_report():
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT person_id, name FROM expenditure_persons ORDER BY name")
        persons = cur.fetchall()

        cur.execute(
            """SELECT paid_by_person_id AS pid, COALESCE(SUM(amount),0) AS total
               FROM expenditure_expenses GROUP BY paid_by_person_id"""
        )
        paid_map = {r["pid"]: float(r["total"]) for r in cur.fetchall()}

        cur.execute(
            """SELECT paid_by_person_id AS pid, COALESCE(SUM(amount),0) AS total
               FROM expenditure_expenses WHERE is_split_expense = FALSE
               GROUP BY paid_by_person_id"""
        )
        personal_map = {r["pid"]: float(r["total"]) for r in cur.fetchall()}

        cur.execute(
            """SELECT s.person_id AS pid, COALESCE(SUM(s.share_amount),0) AS total
               FROM expenditure_expense_splits s
               JOIN expenditure_expenses e ON e.expense_id = s.expense_id
               WHERE e.is_split_expense = TRUE GROUP BY s.person_id"""
        )
        shared_map = {r["pid"]: float(r["total"]) for r in cur.fetchall()}

        balances = _get_net_balances(cur)

        report = []
        for p in persons:
            pid = p["person_id"]
            report.append({
                "person_id": pid,
                "name": p["name"],
                "paid": round(paid_map.get(pid, 0), 2),
                "personal_expense": round(personal_map.get(pid, 0), 2),
                "shared_expense": round(shared_map.get(pid, 0), 2),
                "balance": balances.get(pid, 0),
            })
        return jsonify({"success": True, "report": report})
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/reports/outstanding", methods=["GET"])
def outstanding_report():
    """Who Owes Whom — minimum transactions via debt simplification."""
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        balances = _get_net_balances(cur)

        cur.execute("SELECT person_id, name FROM expenditure_persons")
        names = {r["person_id"]: r["name"] for r in cur.fetchall()}

        txns = _simplify_debts(balances)
        for t in txns:
            t["from_name"] = names.get(t["from_person_id"])
            t["to_name"] = names.get(t["to_person_id"])

        return jsonify({"success": True, "outstanding": txns})
    finally:
        conn.close()


@expenditure_bp.route("/expenditure/reports/summary", methods=["GET"])
def summary_report():
    """
    Category-wise expense totals.
    Query params: period = monthly|quarterly|six_monthly|yearly
                  month=YYYY-MM | quarter=1-4&year=YYYY | half=H1/H2&year=YYYY | year=YYYY
                  category = Office|Individual (optional)
    """
    args = request.args
    period = args.get("period", "monthly")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        where, params = [], []

        if args.get("category"):
            where.append("e.expense_category = %s")
            params.append(args["category"])

        if period == "monthly" and args.get("month"):
            where.append("to_char(e.expense_month, 'YYYY-MM') = %s")
            params.append(args["month"])
        elif period == "quarterly" and args.get("quarter") and args.get("year"):
            where.append("EXTRACT(QUARTER FROM e.expense_month) = %s AND EXTRACT(YEAR FROM e.expense_month) = %s")
            params.extend([args["quarter"], args["year"]])
        elif period == "six_monthly" and args.get("half") and args.get("year"):
            if args["half"] == "H1":
                where.append("EXTRACT(MONTH FROM e.expense_month) BETWEEN 1 AND 6 AND EXTRACT(YEAR FROM e.expense_month) = %s")
            else:
                where.append("EXTRACT(MONTH FROM e.expense_month) BETWEEN 7 AND 12 AND EXTRACT(YEAR FROM e.expense_month) = %s")
            params.append(args["year"])
        elif period == "yearly" and args.get("year"):
            where.append("EXTRACT(YEAR FROM e.expense_month) = %s")
            params.append(args["year"])

        sql = """
            SELECT et.expense_type_name, COALESCE(SUM(e.amount),0) AS total
            FROM expenditure_expenses e
            LEFT JOIN expenditure_expense_types et ON et.expense_type_id = e.expense_type_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY et.expense_type_name ORDER BY total DESC"

        cur.execute(sql, params)
        rows = cur.fetchall()
        grand_total = round(sum(float(r["total"]) for r in rows), 2)

        return jsonify({"success": True, "summary": rows, "grand_total": grand_total})
    finally:
        conn.close()