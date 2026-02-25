"""
api/auth_routes.py — Authentication endpoints
"""

import re

from flask import Blueprint, jsonify, request

from src.core.config import USERS
from src.core.auth import create_token

auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/api/login")
def api_login():
    """Issue an auth token for the supplied credentials."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password or USERS.get(username) != password:
        return jsonify({"error": "invalid_credentials"}), 401
    token = create_token(username)
    return jsonify({"token": token, "username": username})
