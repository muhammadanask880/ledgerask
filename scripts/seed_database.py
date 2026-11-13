"""
Generates a realistic ~2-year sample company database for LedgerAsk.

Run from the project root:
    python scripts/seed_database.py

It is fully reproducible (fixed random seed) and safe to re-run: it deletes
any existing business DB file first and rebuilds it from scratch.

One deliberate, documented anomaly is baked in for the demo video: the
"Europe" region's revenue drops sharply in the most recently completed
month compared to the month before, so a question like "which region's
revenue dropped last month, and by how much?" has a real, correct answer.
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "ledgerask_business.db"

# Anchor "today" for the generated data. Data runs from 2 years before this
# date up to this date.
ANCHOR_DATE = date(2026, 11, 10)
START_DATE = ANCHOR_DATE.replace(year=ANCHOR_DATE.year - 2)

REGIONS = [
    "North America",
    "Europe",
    "Asia Pacific",
    "Latin America",
    "Middle East & Africa",
]

PRODUCT_CATALOG = {
    "Electronics": ["Wireless Mouse", "4K Monitor", "Mechanical Keyboard", "USB-C Hub", "Webcam Pro", "Noise-Cancelling Headset"],
    "Office Supplies": ["Standing Desk", "Ergonomic Chair", "Desk Lamp", "Whiteboard", "Notebook Pack", "Pen Set"],
    "Furniture": ["Bookshelf", "Filing Cabinet", "Office Sofa", "Meeting Table", "Storage Locker"],
    "Software Licenses": ["Project Tracker License", "Design Suite License", "Security Suite License", "Analytics Dashboard License"],
    "Accessories": ["Laptop Sleeve", "Cable Organizer", "Phone Stand", "Travel Adapter", "Desk Mat"],
    "Networking": ["Wi-Fi Router", "Network Switch", "Ethernet Cable Pack", "Firewall Appliance"],
}

# Relative monthly order-volume multiplier per region, to give each region a
# distinct, plausible growth story over the 2 years. 1.0 = baseline.
REGION_GROWTH = {
    "North America": 1.15,
    "Europe": 1.00,
    "Asia Pacific": 1.30,
    "Latin America": 1.10,
    "Middle East & Africa": 0.90,
}


def month_key(d: date) -> tuple:
    return (d.year, d.month)


def last_complete_month(anchor: date) -> tuple:
    first_of_this_month = anchor.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    return month_key(last_day_prev_month)


def month_before(mk: tuple) -> tuple:
    y, m = mk
    return (y - 1, 12) if m == 1 else (y, m - 1)


def build_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS customers;
        DROP TABLE IF EXISTS sales_reps;
        DROP TABLE IF EXISTS regions;

        CREATE TABLE regions (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE sales_reps (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            email     TEXT NOT NULL,
            region_id INTEGER NOT NULL REFERENCES regions(id)
        );

        CREATE TABLE customers (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL,
            region_id  INTEGER NOT NULL REFERENCES regions(id),
            created_at TEXT NOT NULL
        );

        CREATE TABLE products (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            category   TEXT NOT NULL,
            unit_price REAL NOT NULL,
            cost       REAL NOT NULL
        );

        CREATE TABLE orders (
            id            INTEGER PRIMARY KEY,
            customer_id   INTEGER NOT NULL REFERENCES customers(id),
            sales_rep_id  INTEGER NOT NULL REFERENCES sales_reps(id),
            region_id     INTEGER NOT NULL REFERENCES regions(id),
            order_date    TEXT NOT NULL,
            status        TEXT NOT NULL CHECK (status IN ('completed', 'refunded', 'cancelled'))
        );

        CREATE TABLE order_items (
            id          INTEGER PRIMARY KEY,
            order_id    INTEGER NOT NULL REFERENCES orders(id),
            product_id  INTEGER NOT NULL REFERENCES products(id),
            quantity    INTEGER NOT NULL,
            unit_price  REAL NOT NULL
        );

        CREATE INDEX idx_orders_date ON orders(order_date);
        CREATE INDEX idx_orders_region ON orders(region_id);
        CREATE INDEX idx_order_items_order ON order_items(order_id);
        """
    )


def seed_regions(conn: sqlite3.Connection) -> dict:
    ids = {}
    for name in REGIONS:
        cur = conn.execute("INSERT INTO regions (name) VALUES (?)", (name,))
        ids[name] = cur.lastrowid
    return ids


def seed_sales_reps(conn: sqlite3.Connection, region_ids: dict) -> dict:
    """2 reps per region. Returns {region_name: [rep_id, ...]}."""
    reps_by_region = {name: [] for name in REGIONS}
    for name in REGIONS:
        for _ in range(2):
            rep_name = fake.name()
            email = f"{rep_name.lower().replace(' ', '.')}@ledgerask-demo.test"
            cur = conn.execute(
                "INSERT INTO sales_reps (name, email, region_id) VALUES (?, ?, ?)",
                (rep_name, email, region_ids[name]),
            )
            reps_by_region[name].append(cur.lastrowid)
    return reps_by_region


def seed_customers(conn: sqlite3.Connection, region_ids: dict, count: int = 150) -> dict:
    """Returns {region_name: [customer_id, ...]}."""
    customers_by_region = {name: [] for name in REGIONS}
    for _ in range(count):
        region_name = random.choice(REGIONS)
        company = fake.company()
        email = f"contact@{company.lower().replace(' ', '').replace(',', '').replace('.', '')[:20]}.test"
        created = fake.date_between(start_date=START_DATE, end_date=ANCHOR_DATE)
        cur = conn.execute(
            "INSERT INTO customers (name, email, region_id, created_at) VALUES (?, ?, ?, ?)",
            (company, email, region_ids[region_name], created.isoformat()),
        )
        customers_by_region[region_name].append(cur.lastrowid)
    return customers_by_region


def seed_products(conn: sqlite3.Connection) -> list:
    product_ids = []
    for category, names in PRODUCT_CATALOG.items():
        for name in names:
            unit_price = round(random.uniform(15, 1200), 2)
            cost = round(unit_price * random.uniform(0.35, 0.65), 2)
            cur = conn.execute(
                "INSERT INTO products (name, category, unit_price, cost) VALUES (?, ?, ?, ?)",
                (name, category, unit_price, cost),
            )
            product_ids.append(cur.lastrowid)
    return product_ids


def daily_order_count(d: date, region_name: str, dip_month: tuple, dip_before_month: tuple) -> float:
    """Expected number of orders for one region on one day, with growth,
    weekday effect, a holiday bump, and the deliberate Europe dip."""
    days_elapsed = (d - START_DATE).days
    total_days = max((ANCHOR_DATE - START_DATE).days, 1)
    growth_factor = REGION_GROWTH[region_name] * (0.6 + 1.0 * (days_elapsed / total_days))

    # Weekends are quieter for B2B-style ordering.
    weekday_factor = 0.5 if d.weekday() >= 5 else 1.0

    # Mild holiday-season bump in November/December.
    holiday_factor = 1.35 if d.month in (11, 12) else 1.0

    base = 0.9 * growth_factor * weekday_factor * holiday_factor

    mk = month_key(d)
    if region_name == "Europe":
        if mk == dip_month:
            base *= 0.35  # the deliberate drop
        elif mk == dip_before_month:
            base *= 1.1  # slightly healthy month right before the drop, for contrast

    return base


def seed_orders_and_items(
    conn: sqlite3.Connection,
    region_ids: dict,
    reps_by_region: dict,
    customers_by_region: dict,
    product_ids: list,
) -> None:
    dip_month = last_complete_month(ANCHOR_DATE)
    dip_before_month = month_before(dip_month)

    cur_day = START_DATE
    order_rows = []
    item_rows = []

    while cur_day <= ANCHOR_DATE:
        for region_name in REGIONS:
            expected = daily_order_count(cur_day, region_name, dip_month, dip_before_month)
            num_orders = max(0, round(random.gauss(expected, expected * 0.3)))
            for _ in range(num_orders):
                customer_id = random.choice(customers_by_region[region_name])
                sales_rep_id = random.choice(reps_by_region[region_name])
                status = random.choices(
                    ["completed", "refunded", "cancelled"], weights=[0.92, 0.05, 0.03]
                )[0]
                order_rows.append(
                    (customer_id, sales_rep_id, region_ids[region_name], cur_day.isoformat(), status)
                )
        cur_day += timedelta(days=1)

    conn.executemany(
        "INSERT INTO orders (customer_id, sales_rep_id, region_id, order_date, status) VALUES (?, ?, ?, ?, ?)",
        order_rows,
    )

    # Now attach 1-5 line items to every order.
    order_ids = [row[0] for row in conn.execute("SELECT id FROM orders")]
    price_by_product = dict(conn.execute("SELECT id, unit_price FROM products"))

    for order_id in order_ids:
        for _ in range(random.randint(1, 5)):
            product_id = random.choice(product_ids)
            quantity = random.randint(1, 8)
            item_rows.append((order_id, product_id, quantity, price_by_product[product_id]))

    conn.executemany(
        "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
        item_rows,
    )

    print(f"  Dip engineered for Europe in {dip_month[0]}-{dip_month[1]:02d} "
          f"(vs {dip_before_month[0]}-{dip_before_month[1]:02d}).")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        print("Building schema...")
        build_schema(conn)

        print("Seeding regions...")
        region_ids = seed_regions(conn)

        print("Seeding sales reps...")
        reps_by_region = seed_sales_reps(conn, region_ids)

        print("Seeding customers...")
        customers_by_region = seed_customers(conn, region_ids)

        print("Seeding products...")
        product_ids = seed_products(conn)

        print("Seeding orders and order items (this takes a few seconds)...")
        seed_orders_and_items(conn, region_ids, reps_by_region, customers_by_region, product_ids)

        conn.commit()

        orders_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        items_count = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
        print(f"Done. {orders_count} orders, {items_count} order items written to {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
