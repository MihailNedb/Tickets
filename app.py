from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "kiosk.db"

app = Flask(__name__)
app.secret_key = "change-this-secret-key-for-real-deployment"
app.config["DATABASE"] = str(DB_PATH)

DENOMINATIONS = (1, 2, 5, 10, 20)
ROUTES = {
    "Северное": ["Центральная", "Лесная", "Озёрная", "Сосновка"],
    "Южное": ["Центральная", "Южная", "Садовая", "Речная"],
    "Западное": ["Центральная", "Западная", "Новая", "Промышленная"],
    "Восточное": ["Центральная", "Восточная", "Аэропорт", "Дачная"],
}
TIMES = ["06:40", "08:15", "10:30", "13:05", "16:20", "18:45", "21:10"]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS cash_inventory (
        denomination INTEGER PRIMARY KEY,
        quantity INTEGER NOT NULL CHECK(quantity >= 0)
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction TEXT NOT NULL,
        departure TEXT NOT NULL,
        arrival TEXT NOT NULL,
        travel_date TEXT NOT NULL,
        travel_time TEXT NOT NULL,
        price INTEGER NOT NULL,
        purchased_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        kind TEXT NOT NULL,
        amount INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(ticket_id) REFERENCES tickets(id)
    );
    """)
    for denomination, quantity in [(1, 18), (2, 12), (5, 8), (10, 6), (20, 4)]:
        db.execute("INSERT OR IGNORE INTO cash_inventory VALUES (?, ?)", (denomination, quantity))
    db.execute("INSERT OR IGNORE INTO settings VALUES ('paper_count', '30')")
    db.execute("INSERT OR IGNORE INTO settings VALUES ('admin_password', 'admin20')")
    db.commit()
    db.close()


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            flash("Для доступа к разделу администратора необходимо войти.", "error")
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper


def get_setting(key, default="0"):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )


def inventory():
    rows = get_db().execute("SELECT denomination, quantity FROM cash_inventory ORDER BY denomination").fetchall()
    return {int(r["denomination"]): int(r["quantity"]) for r in rows}


def cash_total(stock=None):
    stock = stock or inventory()
    return sum(d * q for d, q in stock.items())


def find_change(amount, stock):
    """Подбирает точную сдачу с учётом ограниченного количества каждого номинала."""
    if amount < 0:
        return None
    dp = [None] * (amount + 1)
    dp[0] = {}
    for value in range(1, amount + 1):
        for denomination in sorted(DENOMINATIONS, reverse=True):
            previous = value - denomination
            if previous < 0 or dp[previous] is None:
                continue
            used = dp[previous].get(denomination, 0)
            if used >= stock.get(denomination, 0):
                continue
            candidate = dict(dp[previous])
            candidate[denomination] = used + 1
            if dp[value] is None or sum(candidate.values()) < sum(dp[value].values()):
                dp[value] = candidate
    return dp[amount]


def calculate_price(departure, arrival, direction):
    stations = ROUTES[direction]
    distance = abs(stations.index(arrival) - stations.index(departure))
    return max(5, 20 + distance * 15)


@app.route("/")
def index():
    return render_template("customer.html", routes=ROUTES, times=TIMES,
                           min_date=date.today().isoformat(),
                           max_date=(date.today() + timedelta(days=10)).isoformat())


@app.post("/api/purchase")
def purchase():
    data = request.get_json(silent=True) or {}
    direction = data.get("direction")
    departure = data.get("departure")
    arrival = data.get("arrival")
    travel_date = data.get("travel_date")
    travel_time = data.get("travel_time")
    denominations = data.get("payment", [])

    if direction not in ROUTES or departure not in ROUTES[direction] or arrival not in ROUTES[direction] or arrival == departure:
        return jsonify(ok=False, message="Проверьте направление и пункты маршрута."), 400
    if travel_time not in TIMES:
        return jsonify(ok=False, message="Выберите время отправления из списка."), 400
    try:
        selected_date = date.fromisoformat(travel_date)
    except (TypeError, ValueError):
        return jsonify(ok=False, message="Некорректная дата поездки."), 400
    if selected_date < date.today() or selected_date > date.today() + timedelta(days=10):
        return jsonify(ok=False, message="Дата поездки должна быть в пределах ближайших 10 суток."), 400
    if not isinstance(denominations, list) or not denominations:
        return jsonify(ok=False, message="Внесите деньги для оплаты."), 400
    if any(type(x) is not int or x not in DENOMINATIONS for x in denominations):
        return jsonify(ok=False, message="Автомат принимает только 1, 2, 5, 10 и 20 рублей."), 400

    db = get_db()
    paper = int(get_setting("paper_count"))
    if paper <= 0:
        return jsonify(ok=False, message="Закончилась бумага. Обратитесь к обслуживающему персоналу."), 409

    price = calculate_price(departure, arrival, direction)
    inserted = sum(denominations)
    if inserted < price:
        return jsonify(ok=False, message=f"Введённой суммы недостаточно. Не хватает {price - inserted} ₽.", inserted=inserted, price=price), 400

    stock = inventory()
    # Сначала учитываем внесённые деньги, затем подбираем сдачу.
    available = dict(stock)
    for denomination in denominations:
        available[denomination] += 1
    change_amount = inserted - price
    change = find_change(change_amount, available)
    if change is None:
        return jsonify(ok=False, message="Невозможно выдать сдачу. Внесённые деньги возвращены.", refund=inserted), 409

    purchased_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute("""INSERT INTO tickets
        (direction, departure, arrival, travel_date, travel_time, price, purchased_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (direction, departure, arrival, travel_date, travel_time, price, purchased_at))
    ticket_id = cur.lastrowid

    for denomination in denominations:
        db.execute("UPDATE cash_inventory SET quantity=quantity+1 WHERE denomination=?", (denomination,))
    for denomination, quantity in change.items():
        db.execute("UPDATE cash_inventory SET quantity=quantity-? WHERE denomination=?", (quantity, denomination))

    set_setting("paper_count", paper - 1)
    db.execute("INSERT INTO transactions(ticket_id,kind,amount,created_at) VALUES(?,?,?,?)",
               (ticket_id, "sale", price, purchased_at))
    db.commit()

    return jsonify(
        ok=True, message="Билет успешно напечатан. Заберите билет и сдачу.",
        ticket={"id": ticket_id, "direction": direction, "departure": departure,
                "arrival": arrival, "travel_date": travel_date, "travel_time": travel_time,
                "price": price, "purchased_at": purchased_at},
        change=change_amount, change_items=change, inserted=inserted
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == get_setting("admin_password", "admin20"):
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Неверный пароль.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    today = date.today().isoformat()
    tickets = db.execute("""SELECT * FROM tickets WHERE substr(purchased_at,1,10)=?
                            ORDER BY purchased_at DESC""", (today,)).fetchall()
    total = sum(row["price"] for row in tickets)
    stock = inventory()
    paper = int(get_setting("paper_count"))
    return render_template("admin.html", tickets=tickets, total=total,
                           ticket_count=len(tickets), stock=stock,
                           cash_total=cash_total(stock), paper=paper,
                           denominations=DENOMINATIONS, today=date.today().strftime("%d.%m.%Y"))


@app.post("/admin/cash")
@admin_required
def admin_cash():
    denomination = request.form.get("denomination", type=int)
    quantity = request.form.get("quantity", type=int)
    action = request.form.get("action")
    if denomination not in DENOMINATIONS or not quantity or quantity < 1 or quantity > 500:
        flash("Проверьте номинал и количество (от 1 до 500).", "error")
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    current = inventory()[denomination]
    if action == "remove" and current < quantity:
        flash("В ящике недостаточно денежных знаков выбранного номинала.", "error")
    else:
        delta = quantity if action == "add" else -quantity
        db.execute("UPDATE cash_inventory SET quantity=quantity+? WHERE denomination=?", (delta, denomination))
        db.commit()
        flash(f"Операция выполнена: {quantity} шт. номиналом {denomination} ₽.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/paper")
@admin_required
def admin_paper():
    count = request.form.get("paper_count", type=int)
    if count is None or count < 0 or count > 1000:
        flash("Количество бумаги должно быть от 0 до 1000.", "error")
    else:
        set_setting("paper_count", count)
        get_db().commit()
        flash("Запас бумаги обновлён.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/password")
@admin_required
def admin_password():
    new_password = request.form.get("new_password", "")
    if len(new_password) < 6:
        flash("Новый пароль должен содержать минимум 6 символов.", "error")
    else:
        set_setting("admin_password", new_password)
        get_db().commit()
        flash("Пароль администратора изменён.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/report.csv")
@admin_required
def report_csv():
    import csv
    from io import StringIO
    from flask import Response
    rows = get_db().execute("SELECT * FROM tickets ORDER BY purchased_at DESC").fetchall()
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Направление", "Отправление", "Прибытие", "Дата поездки", "Время", "Стоимость", "Время покупки"])
    for row in rows:
        writer.writerow([row["id"], row["direction"], row["departure"], row["arrival"],
                         row["travel_date"], row["travel_time"], row["price"], row["purchased_at"]])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tickets_report.csv"})


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
