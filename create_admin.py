#!/usr/bin/env python3
"""Create an admin user for the VFS admin panel.

Usage (from scraping/ directory):
    python create_admin.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from getpass import getpass
from werkzeug.security import generate_password_hash
from models import init_db, create_admin, get_admin_by_username


def main():
    init_db()
    print("=== VFS Admin Panel — Create Admin User ===")
    username = input("Username: ").strip()
    if not username:
        print("Error: username cannot be empty.")
        sys.exit(1)
    if get_admin_by_username(username):
        print(f"Error: admin '{username}' already exists.")
        sys.exit(1)
    password = getpass("Password: ")
    if len(password) < 6:
        print("Error: password must be at least 6 characters.")
        sys.exit(1)
    confirm = getpass("Confirm password: ")
    if password != confirm:
        print("Error: passwords do not match.")
        sys.exit(1)
    create_admin(username, generate_password_hash(password))
    print(f"\nAdmin '{username}' created successfully.")
    print("Log in at /admin/login")


if __name__ == "__main__":
    main()
