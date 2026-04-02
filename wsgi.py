"""WSGI entry point for Gunicorn (optional). Use: gunicorn wsgi:application"""
from app import app as application

__all__ = ["application"]
