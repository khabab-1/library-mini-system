import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, g

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-key"),
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "library.db")),
    )
    if test_config:
        app.config.update(test_config)

    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(exception=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                isbn TEXT NOT NULL UNIQUE,
                year INTEGER,
                total_copies INTEGER NOT NULL DEFAULT 1 CHECK(total_copies >= 0),
                available_copies INTEGER NOT NULL DEFAULT 1 CHECK(available_copies >= 0),
                CHECK(available_copies <= total_copies)
            );
            CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                borrower TEXT NOT NULL,
                borrowed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                returned INTEGER NOT NULL DEFAULT 0
            );
        """)
        db.commit()

    with app.app_context():
        init_db()

    @app.route("/")
    def index():
        db = get_db()
        q = request.args.get("q", "").strip()
        if q:
            books = db.execute(
                "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? OR isbn LIKE ? ORDER BY id DESC",
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            books = db.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
        stats = db.execute("""
            SELECT COUNT(*) AS titles,
                   COALESCE(SUM(total_copies), 0) AS copies,
                   COALESCE(SUM(available_copies), 0) AS available
            FROM books
        """).fetchone()
        loans = db.execute("""
            SELECT loans.id, loans.borrower, loans.borrowed_at, books.title
            FROM loans JOIN books ON books.id = loans.book_id
            WHERE loans.returned = 0 ORDER BY loans.id DESC
        """).fetchall()
        return render_template("index.html", books=books, q=q, stats=stats, loans=loans)

    @app.post("/books/add")
    def add_book():
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        isbn = request.form.get("isbn", "").strip()
        year_raw = request.form.get("year", "").strip()
        copies_raw = request.form.get("copies", "1").strip()

        if not title or not author or not isbn:
            flash("العنوان واسم المؤلف وISBN حقول مطلوبة.", "error")
            return redirect(url_for("index"))
        try:
            year = int(year_raw) if year_raw else None
            if year is not None and (year < 1000 or year > 2100):
                raise ValueError
        except ValueError:
            flash("سنة النشر يجب أن تكون رقماً بين 1000 و2100.", "error")
            return redirect(url_for("index"))
        try:
            copies = int(copies_raw)
            if copies < 0 or copies > 10000:
                raise ValueError
        except ValueError:
            flash("عدد النسخ يجب أن يكون رقماً صحيحاً من 0 إلى 10000.", "error")
            return redirect(url_for("index"))

        db = get_db()
        try:
            db.execute(
                "INSERT INTO books (title, author, isbn, year, total_copies, available_copies) VALUES (?, ?, ?, ?, ?, ?)",
                (title, author, isbn, year, copies, copies)
            )
            db.commit()
            flash("تمت إضافة الكتاب بنجاح.", "success")
        except sqlite3.IntegrityError:
            flash("رقم ISBN مستخدم من قبل. أدخل رقماً مختلفاً.", "error")
        return redirect(url_for("index"))

    @app.post("/books/<int:book_id>/delete")
    def delete_book(book_id):
        db = get_db()
        book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            flash("الكتاب غير موجود.", "error")
        else:
            active = db.execute("SELECT COUNT(*) FROM loans WHERE book_id = ? AND returned = 0", (book_id,)).fetchone()[0]
            if active:
                flash("لا يمكن حذف كتاب توجد له إعارات غير مُعادة.", "error")
            else:
                db.execute("DELETE FROM books WHERE id = ?", (book_id,))
                db.commit()
                flash("تم حذف الكتاب.", "success")
        return redirect(url_for("index"))

    @app.post("/books/<int:book_id>/borrow")
    def borrow_book(book_id):
        borrower = request.form.get("borrower", "").strip()
        db = get_db()
        book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not borrower:
            flash("أدخل اسم المستعير.", "error")
        elif not book:
            flash("الكتاب غير موجود.", "error")
        elif book["available_copies"] < 1:
            flash("لا توجد نسخ متاحة للاستعارة.", "error")
        else:
            db.execute("INSERT INTO loans (book_id, borrower) VALUES (?, ?)", (book_id, borrower))
            db.execute("UPDATE books SET available_copies = available_copies - 1 WHERE id = ?", (book_id,))
            db.commit()
            flash(f'تمت إعارة "{book["title"]}" بنجاح.', "success")
        return redirect(url_for("index"))

    @app.post("/loans/<int:loan_id>/return")
    def return_book(loan_id):
        db = get_db()
        loan = db.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            flash("سجل الإعارة غير موجود.", "error")
        elif loan["returned"]:
            flash("تم إرجاع هذا الكتاب مسبقاً.", "error")
        else:
            db.execute("UPDATE loans SET returned = 1 WHERE id = ?", (loan_id,))
            db.execute("UPDATE books SET available_copies = available_copies + 1 WHERE id = ?", (loan["book_id"],))
            db.commit()
            flash("تم تسجيل إرجاع الكتاب.", "success")
        return redirect(url_for("index"))

    @app.cli.command("seed")
    def seed():
        """Insert a few demo books if the database is empty."""
        db = get_db()
        count = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        if count == 0:
            db.executemany(
                "INSERT INTO books (title, author, isbn, year, total_copies, available_copies) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("مقدمة في بايثون", "أحمد علي", "DEMO-1001", 2022, 3, 3),
                    ("أساسيات الشبكات", "سارة محمد", "DEMO-1002", 2021, 2, 2),
                    ("قواعد البيانات ببساطة", "محمد عثمان", "DEMO-1003", 2020, 1, 1),
                ],
            )
            db.commit()
            print("تمت إضافة 3 كتب تجريبية.")
        else:
            print("قاعدة البيانات تحتوي على كتب بالفعل؛ لم تتم إضافة بيانات مكررة.")

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
