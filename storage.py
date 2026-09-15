"""
Pluggable price storage. User chooses at bot setup: sqlite (default), csv, or postgres.
"""
import os
import csv
import time
import sqlite3
import threading


class SQLiteStorage:
    kind = "sqlite"

    def __init__(self, path="data/khr_usd.db"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS rates ("
                "ts REAL PRIMARY KEY, buy REAL, sell REAL, mid REAL, source TEXT)"
            )
            self.conn.commit()

    def insert(self, ts, buy, sell, source):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO rates VALUES (?,?,?,?,?)",
                (ts, buy, sell, (buy + sell) / 2, source))
            self.conn.commit()

    def since(self, t0):
        with self.lock:
            return self.conn.execute(
                "SELECT ts, buy, sell, mid, source FROM rates WHERE ts>=? ORDER BY ts", (t0,)).fetchall()

    def latest(self):
        with self.lock:
            return self.conn.execute(
                "SELECT ts, buy, sell, mid, source FROM rates ORDER BY ts DESC LIMIT 1").fetchone()


class CSVStorage:
    kind = "csv"

    def __init__(self, path="data/khr_usd.csv"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(["ts", "buy", "sell", "mid", "source"])

    def insert(self, ts, buy, sell, source):
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow([ts, buy, sell, (buy + sell) / 2, source])

    def _rows(self):
        with open(self.path) as f:
            return list(csv.reader(f))[1:]

    def since(self, t0):
        return [tuple([float(r[0]), float(r[1]), float(r[2]), float(r[3]), r[4]])
                for r in self._rows() if float(r[0]) >= t0]

    def latest(self):
        rows = self._rows()
        if not rows:
            return None
        r = rows[-1]
        return (float(r[0]), float(r[1]), float(r[2]), float(r[3]), r[4])


class PostgresStorage:
    kind = "postgres"

    def __init__(self, url):
        import psycopg2  # pip install psycopg2-binary
        self.conn = psycopg2.connect(url)
        self.lock = threading.Lock()
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS rates ("
                        "ts DOUBLE PRECISION PRIMARY KEY, buy REAL, sell REAL, mid REAL, source TEXT)")
            self.conn.commit()

    def insert(self, ts, buy, sell, source):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("INSERT INTO rates VALUES (%s,%s,%s,%s,%s) ON CONFLICT (ts) DO NOTHING",
                        (ts, buy, sell, (buy + sell) / 2, source))
            self.conn.commit()

    def since(self, t0):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT ts,buy,sell,mid,source FROM rates WHERE ts>=%s ORDER BY ts", (t0,))
            return cur.fetchall()

    def latest(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT ts,buy,sell,mid,source FROM rates ORDER BY ts DESC LIMIT 1")
            return cur.fetchone()


def create_storage(kind, location):
    kind = kind.lower()
    if kind == "sqlite":
        return SQLiteStorage(location or "data/khr_usd.db")
    if kind == "csv":
        return CSVStorage(location or "data/khr_usd.csv")
    if kind == "postgres":
        return PostgresStorage(location)
    raise ValueError(f"Unknown storage kind: {kind}")
