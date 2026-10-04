"""
扫码点单系统 - 本机服务器
启动: python3 server.py
"""
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, render_template, g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "orders.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

app = Flask(__name__, template_folder="templates", static_folder="static")


# ============ 配置 ============
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()


# ============ 数据库 ============
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT DEFAULT '',
        image TEXT DEFAULT '',
        available INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT UNIQUE NOT NULL,
        table_no TEXT DEFAULT '堂食',
        items_json TEXT NOT NULL,
        total REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        pay_method TEXT DEFAULT 'online',
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    # 首次初始化时塞入示例商品
    c.execute("SELECT COUNT(*) FROM categories")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO categories(name) VALUES (?)",
                      [("热销推荐",), ("主食",), ("小吃",), ("饮品",)])
    c.execute("SELECT COUNT(*) FROM products")
    if c.fetchone()[0] == 0:
        sample = [
            (1, "招牌牛肉面", 22.0, "汤浓肉香", 1),
            (1, "卤味双拼饭", 18.0, "卤肉+卤蛋", 1),
            (1, "炸鸡薯条套餐", 25.0, "含可乐", 1),
            (2, "扬州炒饭", 16.0, "粒粒分明", 1),
            (2, "番茄鸡蛋面", 14.0, "家常口味", 1),
            (3, "上校鸡块(6块)", 10.0, "现炸", 1),
            (3, "薯条(中)", 8.0, "现炸", 1),
            (4, "可乐(杯)", 5.0, "加冰", 1),
            (4, "柠檬茶", 8.0, "冰爽", 1),
            (4, "矿泉水", 3.0, "", 1),
        ]
        c.executemany(
            "INSERT INTO products(category_id,name,price,description,available) VALUES (?,?,?,?,?)",
            sample
        )
    conn.commit()
    conn.close()


# ============ 工具 ============
def gen_order_no(db):
    # 每天从001开始，格式: 20260930-001
    today = datetime.now().strftime("%Y%m%d")
    prefix = today + "-"
    row = db.execute(
        "SELECT COUNT(*) FROM orders WHERE order_no LIKE ?",
        (prefix + "%",)
    ).fetchone()
    seq = row[0] + 1
    return f"{prefix}{seq:03d}"


# ============ 顾客端 API ============
@app.route("/")
def index():
    return render_template("index.html", shop_name=CONFIG["shop_name"])


@app.route("/api/menu")
def api_menu():
    db = get_db()
    cats = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
    prods = db.execute(
        "SELECT * FROM products WHERE available=1 ORDER BY category_id, sort_order, id"
    ).fetchall()
    result = []
    for cat in cats:
        items = [dict(p) for p in prods if p["category_id"] == cat["id"]]
        if items:
            result.append({"id": cat["id"], "name": cat["name"], "items": items})
    return jsonify({"ok": True, "data": result, "shop_name": CONFIG["shop_name"]})


@app.route("/api/order", methods=["POST"])
def api_create_order():
    data = request.get_json(force=True)
    items = data.get("items", [])
    if not items:
        return jsonify({"ok": False, "msg": "购物车为空"}), 400

    # 校验商品并计算金额
    db = get_db()
    total = 0
    detail_items = []
    for it in items:
        p = db.execute("SELECT * FROM products WHERE id=? AND available=1",
                       (it["product_id"],)).fetchone()
        if not p:
            return jsonify({"ok": False, "msg": f"商品ID {it['product_id']} 不存在或已售罄"}), 400
        qty = max(1, int(it.get("qty", 1)))
        total += p["price"] * qty
        detail_items.append({
            "product_id": p["id"],
            "name": p["name"],
            "price": p["price"],
            "qty": qty
        })

    order_no = gen_order_no(db)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    table_no = data.get("table_no", "堂食")
    note = data.get("note", "")

    db.execute(
        """INSERT INTO orders(order_no,table_no,items_json,total,status,pay_method,note,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (order_no, table_no, json.dumps(detail_items, ensure_ascii=False),
         round(total, 2), "pending_pay", data.get("pay_method", "online"), note, now)
    )
    db.commit()

    return jsonify({
        "ok": True,
        "order_no": order_no,
        "total": round(total, 2),
        "pay_url": f"/pay/{order_no}"
    })


@app.route("/pay/<order_no>")
def pay_page(order_no):
    return render_template("pay.html", order_no=order_no)


@app.route("/api/order/<order_no>")
def api_get_order(order_no):
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404
    order = dict(row)
    order["items"] = json.loads(order["items_json"])
    return jsonify({"ok": True, "data": order})


@app.route("/api/customer-paid/<order_no>", methods=["POST"])
def api_customer_paid(order_no):
    """顾客点了"我已付款"，等商家确认"""
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404
    db.execute("UPDATE orders SET status='waiting_confirm' WHERE order_no=?", (order_no,))
    db.commit()
    return jsonify({"ok": True, "msg": "已通知商家确认"})


@app.route("/api/pay/callback/<order_no>", methods=["POST"])
def api_pay_callback(order_no):
    """商家确认收款后调用，触发打印"""
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404

    db.execute("UPDATE orders SET status='paid', pay_method=? WHERE order_no=?",
               (request.json.get("method", "现金/扫码") if request.is_json else "扫码",
                order_no))
    db.commit()

    order = dict(row)
    order["items"] = json.loads(order["items_json"])
    order["shop_name"] = CONFIG["shop_name"]
    from printer import Printer
    p = Printer(CONFIG)
    result = p.print_receipt(order)
    print(f"[打印] 订单 {order_no} -> {result}")

    return jsonify({"ok": True, "print_result": result})


# ============ 商家后台 ============
@app.route("/admin")
def admin():
    return render_template("admin.html")


@app.route("/api/admin/orders")
def api_admin_orders():
    db = get_db()
    status = request.args.get("status", "")
    date = request.args.get("date", "")
    sql = "SELECT * FROM orders WHERE 1=1"
    params = []
    if status and status != "all":
        if status == "active":
            sql += " AND status IN ('pending_pay','paid')"
        else:
            sql += " AND status = ?"
            params.append(status)
    if date:
        sql += " AND date(created_at) = ?"
        params.append(date)
    sql += " ORDER BY id DESC LIMIT 200"
    rows = db.execute(sql, params).fetchall()
    result = []
    for r in rows:
        o = dict(r)
        o["items"] = json.loads(o["items_json"])
        result.append(o)
    return jsonify({"ok": True, "data": result})


@app.route("/api/admin/orders/<order_no>/status", methods=["POST"])
def api_admin_update_status(order_no):
    new_status = request.json.get("status")
    if new_status not in ("paid", "completed", "cancelled"):
        return jsonify({"ok": False, "msg": "非法状态"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404
    old_status = row["status"]
    db.execute("UPDATE orders SET status=? WHERE order_no=?", (new_status, order_no))
    db.commit()
    # 商家点确认收款（从待确认→已支付）时触发打印
    if old_status == "waiting_confirm" and new_status == "paid":
        order = dict(row)
        order["items"] = json.loads(order["items_json"])
        order["shop_name"] = CONFIG["shop_name"]
        from printer import Printer
        p = Printer(CONFIG)
        result = p.print_receipt(order)
        print(f"[打印] 订单 {order_no} -> {result}")
    return jsonify({"ok": True})


@app.route("/api/admin/products", methods=["GET", "POST"])
def api_admin_products():
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT * FROM products ORDER BY category_id, id").fetchall()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    else:
        d = request.json
        db.execute(
            """INSERT INTO products(category_id,name,price,description,available)
               VALUES (?,?,?,?,?)""",
            (d["category_id"], d["name"], float(d["price"]),
             d.get("description", ""), 1)
        )
        db.commit()
        return jsonify({"ok": True})


@app.route("/api/admin/products/<int:pid>", methods=["PUT", "DELETE"])
def api_admin_product_modify(pid):
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM products WHERE id=?", (pid,))
    else:
        d = request.json
        db.execute(
            """UPDATE products SET name=?, price=?, description=?, available=?
               WHERE id=?""",
            (d["name"], float(d["price"]), d.get("description", ""),
             int(d.get("available", 1)), pid)
        )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/reprint/<order_no>", methods=["POST"])
def api_reprint(order_no):
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if not row:
        return jsonify({"ok": False, "msg": "订单不存在"}), 404
    order = dict(row)
    order["items"] = json.loads(order["items_json"])
    order["shop_name"] = CONFIG["shop_name"]
    from printer import Printer
    p = Printer(CONFIG)
    result = p.print_receipt(order)
    return jsonify({"ok": result.get("ok", False), "msg": result})


if __name__ == "__main__":
    init_db()
    print(f"=" * 50)
    print(f"  {CONFIG['shop_name']} - 扫码点单系统已启动")
    print(f"  顾客端: http://<本机IP>:{CONFIG['port']}/")
    print(f"  商家后台: http://<本机IP>:{CONFIG['port']}/admin")
    print(f"  打印提供商: {CONFIG['printer']['provider']}")
    print(f"=" * 50)
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
