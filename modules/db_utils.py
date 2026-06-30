import psycopg2
from typing import List, Tuple

class PostgresDB:
    def __init__(self, dbname, user, password, host, port):
        self.conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
        self.cur = self.conn.cursor()

    def fetch_codes_insee(self, query: str = None) -> List[str]:
        sql = query or "SELECT code_insee FROM signalement_ban"
        if not isinstance(sql, str) or not sql.strip().lower().startswith("select"):
            raise ValueError("La requete SQL des codes INSEE doit etre un SELECT.")
        self.cur.execute(sql)
        return [str(row[0]).strip() for row in self.cur.fetchall() if row and row[0] is not None and str(row[0]).strip()]

    def clear_pending(self, table_name: str = "signalement_ban", pending_status: str = "PENDING"):
        self.cur.execute(f"DELETE FROM {table_name} WHERE status = %s;", (pending_status,))
        self.conn.commit()

    def insert_signalements(self, sql: str, all_rows: List[Tuple]):
        self.cur.executemany(sql, all_rows)
        self.conn.commit()

    def close(self):
        self.cur.close()
        self.conn.close()
