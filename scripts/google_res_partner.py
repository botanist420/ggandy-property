#!/usr/bin/env python3
"""
Import or update GGAndy contacts into Odoo res.partner via XML-RPC.

Expected CSV columns:
partner_key,name,phone,mobile,email,street,city,zip,is_owner,is_tenant,is_vendor

Usage:
    python scripts/google_res_partner.py /path/to/contacts.csv
    python scripts/google_res_partner.py "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0"
    python scripts/google_res_partner.py "https://docs.google.com/spreadsheets/d/.../edit" --gid 0
"""

from __future__ import annotations

import argparse
import xmlrpc.client
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

DEFAULT_URL = "http://localhost:1025"
DEFAULT_USER = "admin"

BOOLEAN_COLUMNS = ("is_owner", "is_tenant", "is_vendor")
COLUMN_MAP = {
    "partner_key": "ref",
    "name": "name",
    "phone": "phone",
    "mobile": "mobile",
    "email": "email",
    "street": "street",
    "city": "city",
    "zip": "zip",
    "is_owner": "is_ggandy_owner",
    "is_tenant": "is_ggandy_tenant",
    "is_vendor": "is_ggandy_vendor",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import or update GGAndy contacts into Odoo res.partner via XML-RPC."
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Local CSV path or Google Sheets URL.",
    )
    parser.add_argument(
        "--gid",
        help="Google Sheets gid. Optional when gid exists in the URL.",
    )
    return parser.parse_args()


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def to_bool(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {
        "1",
        "true",
        "t",
        "yes",
        "y",
        "是",
        "房東",
        "房客",
        "廠商",
    }


def clean_value(value):
    if pd.isna(value):
        return False
    if isinstance(value, str):
        value = value.strip()
        return value or False
    return value


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def google_sheet_csv_url(source: str, gid: str | None = None) -> str:
    parsed = urlparse(source)
    if "docs.google.com" not in parsed.netloc or "/spreadsheets/d/" not in parsed.path:
        return source

    parts = parsed.path.split("/")
    try:
        sheet_id = parts[parts.index("d") + 1]
    except (ValueError, IndexError) as error:
        raise ValueError("無法從 Google Sheets URL 解析 spreadsheet id。") from error

    query_gid = parse_qs(parsed.query).get("gid", [None])[0]
    fragment_gid = parse_qs(parsed.fragment).get("gid", [None])[0]
    resolved_gid = gid or query_gid or fragment_gid or ask("Google Sheets gid", "0")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={resolved_gid}"


def resolve_csv_source(source: str, gid: str | None = None) -> str:
    if is_url(source):
        return google_sheet_csv_url(source, gid)

    csv_path = Path(source).expanduser()
    if not csv_path.exists():
        raise FileNotFoundError(f"找不到 CSV：{csv_path}")
    return str(csv_path)


def read_contacts_csv(source: str, gid: str | None = None) -> pd.DataFrame:
    csv_source = resolve_csv_source(source, gid)
    print(f"\n讀取 CSV：{csv_source}")
    df = pd.read_csv(csv_source, dtype=str).fillna("")
    df.columns = [column.strip() for column in df.columns]

    missing = {"name"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少必要欄位：{', '.join(sorted(missing))}")

    for column in BOOLEAN_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    return df


def print_preview(df: pd.DataFrame) -> None:
    print("\n=== CSV 預覽 ===")
    print(f"筆數：{len(df)}")
    print(f"欄位：{', '.join(df.columns)}")
    print("\n前 10 筆：")
    print(df.head(10).to_string(index=False))
    print("\n角色統計：")
    for column in BOOLEAN_COLUMNS:
        count = df[column].map(to_bool).sum() if column in df.columns else 0
        print(f"- {column}: {count}")


def execute_kw(models, db: str, uid: int, password: str, model: str, method: str, args, kwargs=None):
    try:
        return models.execute_kw(db, uid, password, model, method, args, kwargs or {})
    except xmlrpc.client.Fault as error:
        print("\n=== Odoo XML-RPC 錯誤 ===")
        print(f"model: {model}")
        print(f"method: {method}")
        print(f"args: {args}")
        print(f"kwargs: {kwargs or {}}")
        print(f"faultCode: {error.faultCode}")
        print(f"faultString:\n{error.faultString}")
        raise


def get_model_fields(models, db: str, uid: int, password: str, model: str) -> set[str]:
    fields_info = execute_kw(models, db, uid, password, model, "fields_get", [[]])
    return set(fields_info)


def build_partner_values(row: pd.Series, available_fields: set[str]) -> dict:
    values = {}
    for csv_column, odoo_field in COLUMN_MAP.items():
        if csv_column not in row.index:
            continue
        if odoo_field not in available_fields:
            continue
        if csv_column in BOOLEAN_COLUMNS:
            values[odoo_field] = to_bool(row[csv_column])
        else:
            value = clean_value(row[csv_column])
            if value:
                values[odoo_field] = value
    return values


def find_existing_partner(
    models,
    db: str,
    uid: int,
    password: str,
    row: pd.Series,
    available_fields: set[str],
):
    partner_key = clean_value(row.get("partner_key", ""))
    email = clean_value(row.get("email", ""))
    name = clean_value(row.get("name", ""))
    phone = clean_value(row.get("phone", ""))
    mobile = clean_value(row.get("mobile", ""))

    search_domains = []
    if partner_key and "ref" in available_fields:
        search_domains.append([("ref", "=", partner_key)])
    if email and "email" in available_fields:
        search_domains.append([("email", "=", email)])
    if mobile and "mobile" in available_fields:
        search_domains.append([("mobile", "=", mobile)])
    if phone and "phone" in available_fields:
        search_domains.append([("phone", "=", phone)])
    if name and "name" in available_fields:
        search_domains.append([("name", "=", name)])

    for domain in search_domains:
        ids = execute_kw(
            models,
            db,
            uid,
            password,
            "res.partner",
            "search",
            [domain],
            {"limit": 1},
        )
        if ids:
            return ids[0]
    return None


def main() -> int:
    args = parse_args()
    source = args.source or ask("CSV 路徑或 Google Sheets URL")

    url = ask("Odoo URL", DEFAULT_URL).rstrip("/")
    db = ask("Odoo database")
    username = ask("Odoo 帳號", DEFAULT_USER)
    password = input("Odoo 密碼: ")

    try:
        df = read_contacts_csv(source, args.gid)
    except Exception as error:
        print(f"讀取 CSV 失敗：{error}")
        return 1
    print_preview(df)

    confirm = ask("\n確認匯入/更新到 Odoo？輸入 YES 繼續", "NO")
    if confirm != "YES":
        print("已取消，沒有寫入 Odoo。")
        return 0

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, password, {})
    if not uid:
        print("登入失敗，請確認 database、帳號或密碼。")
        return 1

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    partner_fields = get_model_fields(models, db, uid, password, "res.partner")
    skipped_fields = sorted(set(COLUMN_MAP.values()) - partner_fields)
    if skipped_fields:
        print("\n以下 Odoo res.partner 欄位不存在，會自動略過：")
        for field_name in skipped_fields:
            print(f"- {field_name}")

    created = 0
    updated = 0
    skipped = 0

    for index, row in df.iterrows():
        values = build_partner_values(row, partner_fields)
        if not values.get("name"):
            print(f"[SKIP] 第 {index + 2} 列缺少 name")
            skipped += 1
            continue

        try:
            partner_id = find_existing_partner(models, db, uid, password, row, partner_fields)
            if partner_id:
                execute_kw(
                    models,
                    db,
                    uid,
                    password,
                    "res.partner",
                    "write",
                    [[partner_id], values],
                )
                updated += 1
                print(f"[UPDATE] res.partner({partner_id}) {values['name']}")
            else:
                partner_id = execute_kw(
                    models,
                    db,
                    uid,
                    password,
                    "res.partner",
                    "create",
                    [values],
                )
                created += 1
                print(f"[CREATE] res.partner({partner_id}) {values['name']}")
        except xmlrpc.client.Fault:
            print("\n=== 發生錯誤的 CSV 列 ===")
            print(f"列號：{index + 2}")
            print(row.to_dict())
            return 1

    print("\n=== 匯入完成 ===")
    print(f"新增：{created}")
    print(f"更新：{updated}")
    print(f"跳過：{skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
