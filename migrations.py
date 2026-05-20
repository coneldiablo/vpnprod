#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from database import Database


def run_migrations(db_path: str = None) -> Database:
    return Database(db_path)


if __name__ == "__main__":
    run_migrations()
    print("Database migrations completed")
